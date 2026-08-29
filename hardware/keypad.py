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
from collections import deque
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
    KEY_BACKSPACE, KEY_LEARN, KEY_DEVICE_PREV, KEY_TV_MUTE,
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
    "LEARN": KEY_LEARN,
    "MUTE": KEY_TV_MUTE, "< DEVICE": KEY_DEVICE_PREV,
    "PRESET DEVICE 1 - UP": KEY_DEVICE_1_UP, "PRESET DEVICE 1 - DOWN": KEY_DEVICE_1_DOWN,
    "PRESET DEVICE 2 - UP": KEY_DEVICE_2_UP, "PRESET DEVICE 2 - DOWN": KEY_DEVICE_2_DOWN,
    "PRESET DEVICE 3 - UP": KEY_DEVICE_3_UP, "PRESET DEVICE 3 - DOWN": KEY_DEVICE_3_DOWN,
    "PRESET DEVICE 4 - UP": KEY_DEVICE_4_UP, "PRESET DEVICE 4 - DOWN": KEY_DEVICE_4_DOWN,
    "PRESET FUNCTION 1": KEY_PRESET_1, "PRESET FUNCTION 2": KEY_PRESET_2,
    "PRESET FUNCTION 3": KEY_PRESET_3, "PRESET FUNCTION 4": KEY_PRESET_4,
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


def _read_debounced(stop_event: threading.Event, scan_fn=scan_once) -> str | None:
    """Block until a press is stable for KEYPAD_DEBOUNCE_SCANS scans in a
    row, then block again until release. Returns the id, or None if
    stop_event was set first. *scan_fn* defaults to the general-purpose
    scan_once(); KeypadScanner passes its own faster scan instead.
    """
    last = None
    streak = 0
    while not stop_event.is_set():
        reading = scan_fn()
        if reading == last and reading is not None:
            streak += 1
        else:
            streak = 1 if reading is not None else 0
        last = reading

        if streak >= KEYPAD_DEBOUNCE_SCANS:
            # wait for release so one press doesn't repeat-fire
            while scan_fn() is not None and not stop_event.is_set():
                time.sleep(KEYPAD_SCAN_DELAY_SECONDS)
            return reading

        time.sleep(KEYPAD_POLL_INTERVAL_SECONDS if reading is None else KEYPAD_SCAN_DELAY_SECONDS)
    return None


def _infer_partition(
        pins: list[int], edges: set[tuple[int, int]]
) -> tuple[set[int], set[int]] | None:
    """2-color the matrix graph so every known key-press edge crosses sides.

    Lets KeypadScanner drive one side's pins as fixed OUTPUTs (idle HIGH)
    and read the other side as fixed INPUTs, so a sweep only toggles output
    *values* instead of calling GPIO.setup() on every pin every sweep — a
    direction change, which is a slow sysfs write and, done continuously in
    a background thread, generates enough traffic to disrupt the LCD's
    bit-banged software I2C (seen as garbled output / I2C timeouts).

    Returns (side_a, side_b), or None if the graph isn't bipartite — caller
    falls back to the slower, fully general scan_once().
    """
    adjacency: dict[int, set[int]] = {p: set() for p in pins}
    for a, b in edges:
        adjacency.setdefault(a, set()).add(b)
        adjacency.setdefault(b, set()).add(a)

    color: dict[int, int] = {}
    for start in adjacency:
        if start in color:
            continue
        color[start] = 0
        pending = deque([start])
        while pending:
            node = pending.popleft()
            for neighbor in adjacency[node]:
                if neighbor not in color:
                    color[neighbor] = 1 - color[node]
                    pending.append(neighbor)
                elif color[neighbor] == color[node]:
                    return None  # odd cycle — not bipartite

    side_a = {p for p, c in color.items() if c == 0}
    side_b = {p for p, c in color.items() if c == 1}
    return side_a, side_b


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

        self._direct_pins: list[int] = []
        edges: set[tuple[int, int]] = set()
        matrix_pins: set[int] = set()

        for wire_id in self._map:
            parts = wire_id.split("+")
            if len(parts) == 1:
                self._direct_pins.append(int(parts[0]))
            else:
                a, b = int(parts[0]), int(parts[1])
                edges.add((a, b))
                matrix_pins.update((a, b))

        partition = _infer_partition(sorted(matrix_pins), edges) if edges else None

        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)

        if partition is not None:
            self._fast_path = True
            self._side_a, self._side_b = partition
            configured = self._side_a | self._side_b | set(self._direct_pins)
            for pin in self._side_a:
                GPIO.setup(pin, GPIO.OUT, initial=GPIO.HIGH)
            for pin in (self._side_b | set(self._direct_pins)):
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
            # Any wired-but-unmapped pin still needs a defined, harmless state.
            for pin in set(KEYPAD_GPIO_PINS) - configured:
                GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        else:
            self._fast_path = False
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

    def _scan(self) -> str | None:
        """Fast path: pins are pre-configured once in __init__, so a sweep
        only toggles output *values* — no GPIO.setup() calls. Falls back to
        the general scan_once() when the map isn't a clean bipartite matrix.
        """
        if not self._fast_path:
            return scan_once()

        for pin in self._direct_pins:
            if GPIO.input(pin) == GPIO.LOW:
                return str(pin)

        for drive_pin in self._side_a:
            GPIO.output(drive_pin, GPIO.LOW)
            time.sleep(KEYPAD_SCAN_DELAY_SECONDS)

            hit = None
            for sense_pin in self._side_b:
                if GPIO.input(sense_pin) == GPIO.LOW:
                    hit = sense_pin
                    break

            GPIO.output(drive_pin, GPIO.HIGH)

            if hit is not None:
                return f"{min(drive_pin, hit)}+{max(drive_pin, hit)}"

        return None

    def _run(self) -> None:
        while not self._stop.is_set():
            reading = _read_debounced(self._stop, self._scan)
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
