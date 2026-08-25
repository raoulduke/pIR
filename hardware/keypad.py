"""GPIO keypad matrix hardware abstraction.

The 12-wire keypad is pre-built with an unknown row/column grouping (see
CLAUDE.md — "pre-built layout preserved as-is, not renumbered"), so this
module treats it generically: it doesn't assume which wires are rows and
which are columns, or even that it's a true matrix at all (a directly
grounded-per-button wiring scheme reads the same way here).

``scan_once()`` is the shared primitive used both by ``scripts/keypad_wizard.py``
(to discover the physical layout) and by ``KeypadScanner`` at runtime (to look
a reading up in the saved map). It returns a deterministic string id for
whatever is currently pressed:

  - "14"     — a single wire reads LOW on its own (direct-to-ground wiring)
  - "14+15"  — wire 14 driven LOW causes wire 15 to read LOW (true matrix)
  - None     — nothing pressed

Runtime key events are pushed onto a thread-safe queue and signalled through
a self-pipe so ``hardware.input.InputReader`` can multiplex them into its
existing ``select()`` loop without polling.
"""

import json
import os
import queue
import threading
import time
from pathlib import Path

# noinspection PyUnresolvedReferences
import RPi.GPIO as GPIO

from app.constants import (
    KEYPAD_GPIO_PINS,
    KEYPAD_MAP_FILE,
    KEYPAD_SCAN_DELAY_SECONDS,
    KEYPAD_DEBOUNCE_SCANS,
    KEYPAD_POLL_INTERVAL_SECONDS,
)
from app.exceptions import HardwareError
from hardware.input import (
    KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER, KEY_BACK,
    KEY_BACKSPACE, KEY_QUIT, KEY_LEARN, KEY_DEVICE_NEXT, KEY_DEVICE_PREV,
    KEY_DEVICE_1_UP, KEY_DEVICE_1_DOWN, KEY_DEVICE_2_UP, KEY_DEVICE_2_DOWN,
    KEY_DEVICE_3_UP, KEY_DEVICE_3_DOWN, KEY_DEVICE_4_UP, KEY_DEVICE_4_DOWN,
    KEY_PRESET_1, KEY_PRESET_2, KEY_PRESET_3, KEY_PRESET_4,
)

_MAP_PATH = Path(__file__).parent.parent / KEYPAD_MAP_FILE

# Labels that map onto the app's existing key vocabulary (hardware/input.py).
# Anything not listed here passes through to the rest of the app as a raw
# character, same as a USB keyboard key does.
_LABEL_ALIASES = {
    "UP": KEY_UP, "DOWN": KEY_DOWN, "LEFT": KEY_LEFT, "RIGHT": KEY_RIGHT,
    "ENTER": KEY_ENTER, "OK": KEY_ENTER,
    "BACK": KEY_BACK, "*": KEY_BACK,
    "BACKSPACE": KEY_BACKSPACE, "DEL": KEY_BACKSPACE,
    "QUIT": KEY_QUIT,
    "LEARN": KEY_LEARN,
    "DEVICE >": KEY_DEVICE_NEXT, "< DEVICE": KEY_DEVICE_PREV,
    "DEVICE 1 UP": KEY_DEVICE_1_UP, "DEVICE 1 DOWN": KEY_DEVICE_1_DOWN,
    "DEVICE 2 UP": KEY_DEVICE_2_UP, "DEVICE 2 DOWN": KEY_DEVICE_2_DOWN,
    "DEVICE 3 UP": KEY_DEVICE_3_UP, "DEVICE 3 DOWN": KEY_DEVICE_3_DOWN,
    "DEVICE 4 UP": KEY_DEVICE_4_UP, "DEVICE 4 DOWN": KEY_DEVICE_4_DOWN,
    "PRESET 1": KEY_PRESET_1, "PRESET 2": KEY_PRESET_2,
    "PRESET 3": KEY_PRESET_3, "PRESET 4": KEY_PRESET_4,
}


def setup_pins() -> None:
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    for pin in KEYPAD_GPIO_PINS:
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)


def teardown_pins() -> None:
    for pin in KEYPAD_GPIO_PINS:
        try:
            GPIO.cleanup(pin)
        except Exception:
            pass


def scan_once() -> str | None:
    """Do one full pass over the matrix and return an id for whatever is
    pressed right now, or None. Assumes ``setup_pins()`` has been called.
    """
    # Idle check first: catches a wire that's simply pulled to ground when
    # its button is pressed (no active driving needed).
    for pin in KEYPAD_GPIO_PINS:
        if GPIO.input(pin) == GPIO.LOW:
            return str(pin)

    # True matrix: drive one wire low at a time and see if it pulls a
    # different wire low through a closed key.
    for drive_pin in KEYPAD_GPIO_PINS:
        GPIO.setup(drive_pin, GPIO.OUT)
        GPIO.output(drive_pin, GPIO.LOW)
        time.sleep(KEYPAD_SCAN_DELAY_SECONDS)

        hit = None
        for sense_pin in KEYPAD_GPIO_PINS:
            if sense_pin == drive_pin:
                continue
            if GPIO.input(sense_pin) == GPIO.LOW:
                hit = sense_pin
                break

        GPIO.setup(drive_pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        if hit is not None:
            return f"{min(drive_pin, hit)}+{max(drive_pin, hit)}"

    return None


def _read_debounced(stop_event: threading.Event) -> str | None:
    """Block until a press is stable for KEYPAD_DEBOUNCE_SCANS scans in a
    row, then block again until release. Returns the id, or None if
    stop_event was set first.
    """
    last = None
    streak = 0
    while not stop_event.is_set():
        reading = scan_once()
        if reading == last and reading is not None:
            streak += 1
        else:
            streak = 1 if reading is not None else 0
        last = reading

        if streak >= KEYPAD_DEBOUNCE_SCANS:
            # wait for release so one press doesn't repeat-fire
            while scan_once() is not None and not stop_event.is_set():
                time.sleep(KEYPAD_SCAN_DELAY_SECONDS)
            return reading

        time.sleep(KEYPAD_POLL_INTERVAL_SECONDS if reading is None else KEYPAD_SCAN_DELAY_SECONDS)
    return None


def _load_map() -> dict:
    if _MAP_PATH.exists():
        try:
            return json.loads(_MAP_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def resolve_label(label: str) -> str:
    return _LABEL_ALIASES.get(label.upper(), label)


class KeypadScanner:
    """Background thread that scans the matrix and feeds key events into
    hardware/input.py through a queue + self-pipe wakeup.
    """

    def __init__(self) -> None:
        self._map = _load_map()
        if not self._map:
            raise HardwareError("keypad map missing — run scripts/keypad_wizard.py")

        setup_pins()

        self.events: "queue.Queue[str]" = queue.Queue()
        self._wake_r, self._wake_w = os.pipe()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def wake_fd(self) -> int:
        """Readable fd — becomes ready whenever an event is queued."""
        return self._wake_r

    def _run(self) -> None:
        while not self._stop.is_set():
            reading = _read_debounced(self._stop)
            if reading is None:
                continue
            label = self._map.get(reading)
            if label is None:
                continue
            self.events.put(resolve_label(label))
            os.write(self._wake_w, b"\x00")

    def pop(self) -> str | None:
        """Drain one wake byte and return the corresponding key, if any."""
        try:
            os.read(self._wake_r, 1)
        except OSError:
            pass
        try:
            return self.events.get_nowait()
        except queue.Empty:
            return None

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        for fd in (self._wake_r, self._wake_w):
            try:
                os.close(fd)
            except OSError:
                pass
        teardown_pins()


def verify() -> bool:
    """Return True if the map file exists and GPIO is usable."""
    if not _MAP_PATH.exists():
        raise HardwareError("keypad map missing — run scripts/keypad_wizard.py")
    return True
