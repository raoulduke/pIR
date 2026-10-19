"""GPIO keypad matrix.

The pre-built 12-wire keypad's row/column grouping is unknown, so nothing
here assumes which wires are rows or columns. A reading is an id for what's
pressed:

  - "14"     — wire 14 reads LOW on its own (wired straight to ground)
  - "14+15"  — driving wire 14 LOW pulls wire 15 LOW (a matrix key)
  - None     — nothing pressed

scripts/keypad_wizard.py maps readings to labels (config/keypad_map.json);
KeypadScanner looks them up at runtime and queues key events, signalled
through a pipe that hardware/input.py's select() waits on.
"""

import json
import logging
import os
import queue
import select
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
    KEYPAD_REPEAT_DELAY_SECONDS,
    KEYPAD_REPEAT_INTERVAL_SECONDS,
    KEYPAD_HOLD_SECONDS,
    KEYPAD_WAKE_IDLE_SECONDS,
)
from app.exceptions import HardwareError
from hardware.input import (
    KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER, KEY_BACK,
    KEY_DEVICE_PREV, KEY_DEVICE_NEXT, KEY_MENU,
    KEY_DEVICE_1_UP, KEY_DEVICE_1_DOWN, KEY_DEVICE_2_UP, KEY_DEVICE_2_DOWN,
    KEY_DEVICE_3_UP, KEY_DEVICE_3_DOWN, KEY_DEVICE_4_UP, KEY_DEVICE_4_DOWN,
    KEY_PRESET_1, KEY_PRESET_2, KEY_PRESET_3, KEY_PRESET_4,
)

log = logging.getLogger(__name__)

_MAP_PATH = Path(__file__).parent.parent / KEYPAD_MAP_FILE
_GPIO_CHIP = "/dev/gpiochip0"

# Keypad map labels -> the app's key vocabulary (hardware/input.py). A label
# not listed here comes through as the label text itself, which no screen
# acts on.
_LABEL_ALIASES = {
    "UP": KEY_UP, "DOWN": KEY_DOWN, "LEFT": KEY_LEFT, "RIGHT": KEY_RIGHT,
    "ENTER": KEY_ENTER, "OK": KEY_ENTER,
    "BACK": KEY_BACK, "*": KEY_BACK,
    "MENU": KEY_MENU,
    "< DEVICE": KEY_DEVICE_PREV, "DEVICE >": KEY_DEVICE_NEXT,
    "PRESET DEVICE 1 - UP": KEY_DEVICE_1_UP, "PRESET DEVICE 1 - DOWN": KEY_DEVICE_1_DOWN,
    "PRESET DEVICE 2 - UP": KEY_DEVICE_2_UP, "PRESET DEVICE 2 - DOWN": KEY_DEVICE_2_DOWN,
    "PRESET DEVICE 3 - UP": KEY_DEVICE_3_UP, "PRESET DEVICE 3 - DOWN": KEY_DEVICE_3_DOWN,
    "PRESET DEVICE 4 - UP": KEY_DEVICE_4_UP, "PRESET DEVICE 4 - DOWN": KEY_DEVICE_4_DOWN,
    "PRESET FUNCTION 1": KEY_PRESET_1, "PRESET FUNCTION 2": KEY_PRESET_2,
    "PRESET FUNCTION 3": KEY_PRESET_3, "PRESET FUNCTION 4": KEY_PRESET_4,
}

# Keys that auto-repeat while held by default: Up/Down only, since repeating
# them just moves a cursor, while a repeated Enter or quick action would
# re-fire it. Screens add more with set_repeatable_keys().
_REPEATABLE_KEYS = {KEY_UP, KEY_DOWN}


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
    """One full pass over the matrix: an id for whatever is pressed, or
    None. The general scan (the wizard, and maps that aren't a clean
    two-sided matrix); assumes setup_pins() was called."""
    # A wire pulled to ground on its own (no driving needed).
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


def _wait_for_press(stop_event: threading.Event, scan_fn=scan_once,
                    give_up_after: float | None = None) -> str | None:
    """Return a reading once it's stable for KEYPAD_DEBOUNCE_SCANS scans
    (without waiting for release). None if stop_event is set first, or once
    nothing has read as pressed for *give_up_after* seconds."""
    last = None
    streak = 0
    last_seen = time.monotonic()
    while not stop_event.is_set():
        reading = scan_fn()
        if reading == last and reading is not None:
            streak += 1
        else:
            streak = 1 if reading is not None else 0
        last = reading

        if streak >= KEYPAD_DEBOUNCE_SCANS:
            return reading
        if reading is not None:
            last_seen = time.monotonic()
        elif give_up_after is not None and time.monotonic() - last_seen >= give_up_after:
            return None

        time.sleep(KEYPAD_POLL_INTERVAL_SECONDS if reading is None else KEYPAD_SCAN_DELAY_SECONDS)
    return None


def _wait_for_release(stop_event: threading.Event, scan_fn=scan_once) -> None:
    """Block until nothing reads as pressed."""
    while scan_fn() is not None and not stop_event.is_set():
        time.sleep(KEYPAD_SCAN_DELAY_SECONDS)


def _read_debounced(stop_event: threading.Event, scan_fn=scan_once) -> str | None:
    """One whole press (stable, then released): its id, or None if
    stop_event was set first. For scripts/keypad_wizard.py."""
    reading = _wait_for_press(stop_event, scan_fn)
    if reading is not None:
        _wait_for_release(stop_event, scan_fn)
    return reading


def _infer_partition(
        pins: list[int], edges: set[tuple[int, int]]
) -> tuple[set[int], set[int]] | None:
    """Split the wires into two sides so every key connects one wire from
    each: (drive side, sense side), or None if that's impossible. Lets the
    scanner set pin directions once and only toggle output values — changing
    directions on every sweep caused enough GPIO traffic to garble the
    LCD's bit-banged I2C."""
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
    """Background thread that scans the matrix and queues key events for
    hardware/input.py."""

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
        self._stop_r, self._stop_w = os.pipe()
        self._edges = self._request_edges() if self._fast_path else None
        self._repeatable_keys = set(_REPEATABLE_KEYS)
        self._repeat_interval = KEYPAD_REPEAT_INTERVAL_SECONDS
        self._hold_keys: dict[str, str] = {}
        # The key physically held right now, or None — see is_held().
        self._held_key: str | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _request_edges(self):
        """Falling-edge interrupts (gpiod) on every sense pin, so idle waits
        sleep instead of scanning 100 times a second. None — poll instead —
        if gpiod is missing or the request fails."""
        try:
            import gpiod
            from gpiod.line import Bias, Direction, Edge
            settings = gpiod.LineSettings(
                direction=Direction.INPUT, bias=Bias.PULL_UP, edge_detection=Edge.FALLING)
            pins = tuple(sorted(self._side_b | set(self._direct_pins)))
            self._edge_pins = pins
            return gpiod.request_lines(_GPIO_CHIP, consumer="pihome-keypad", config={pins: settings})
        except Exception as e:
            log.warning("keypad edge detection unavailable, polling instead: %s", e)
            return None

    def _sleep_until_edge(self) -> None:
        """Idle wait: drive every side-A pin low so any key pulls its
        sense pin low, then sleep until an edge (or close()). Side A goes
        back high before returning, ready for _scan()."""
        for pin in self._side_a:
            GPIO.output(pin, GPIO.LOW)
        try:
            time.sleep(KEYPAD_SCAN_DELAY_SECONDS)
            # Edges from the last press's scanning are stale.
            while self._edges.wait_edge_events(0):
                self._edges.read_edge_events()
            # A press since then may have left no edge after the drain.
            if all(GPIO.input(pin) == GPIO.HIGH for pin in self._edge_pins):
                select.select([self._edges.fd, self._stop_r], [], [])
        finally:
            for pin in self._side_a:
                GPIO.output(pin, GPIO.HIGH)

    @property
    def wake_fd(self) -> int:
        """Readable fd — becomes ready whenever an event is queued."""
        return self._wake_r

    def set_repeatable_keys(self, extra: set[str], exclude: set[str] = frozenset()) -> None:
        """Keys that auto-repeat: the Up/Down baseline plus *extra*, minus
        *exclude*. Read at the next press, so no lock is needed."""
        self._repeatable_keys = (set(_REPEATABLE_KEYS) | set(extra)) - set(exclude)

    def set_hold_keys(self, holds: dict[str, str]) -> None:
        """Tap vs. hold for the keys in *holds* (key -> event sent on a
        hold): a tap sends the key on release; holding past
        KEYPAD_HOLD_SECONDS sends the hold event instead. {} restores
        on-press. Only the sweep uses it — elsewhere Enter must act on
        press."""
        self._hold_keys = dict(holds)

    def is_held(self, key: str) -> bool:
        """True while *key* is physically held (set before its press is
        queued, cleared on release). For ir_transmitter.stream(), which
        needs the release directly rather than through the queue."""
        return self._held_key == key

    def set_repeat_interval(self, seconds: float | None) -> None:
        """Repeat pace once a held key starts repeating; None restores the
        default. Google TV's D-pad widens it: each repeat is an IR send
        (~100ms), and repeats faster than that piled up and kept firing
        after release."""
        self._repeat_interval = KEYPAD_REPEAT_INTERVAL_SECONDS if seconds is None else seconds

    def _scan(self) -> str | None:
        """One sweep. Pin directions are set once in __init__, so this only
        toggles output values; falls back to scan_once() for a map that
        isn't a clean two-sided matrix."""
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

    def _emit(self, key: str) -> None:
        self.events.put(key)
        os.write(self._wake_w, b"\x00")

    def _repeat_while_held(self, reading: str, key: str) -> None:
        """Repeat *key* at the repeat interval once held past
        KEYPAD_REPEAT_DELAY_SECONDS, until released."""
        held_since = time.monotonic()
        repeating = False
        while not self._stop.is_set():
            if self._scan() != reading:
                return  # released (or a different combo now reads as pressed)
            interval = self._repeat_interval if repeating else KEYPAD_REPEAT_DELAY_SECONDS
            if time.monotonic() - held_since >= interval:
                self._emit(key)
                held_since = time.monotonic()
                repeating = True
            time.sleep(KEYPAD_SCAN_DELAY_SECONDS)

    def _tap_or_hold(self, reading: str, key: str, hold_event: str) -> None:
        """Emit *key* on release, or *hold_event* once held past
        KEYPAD_HOLD_SECONDS (see set_hold_keys())."""
        pressed_at = time.monotonic()
        while not self._stop.is_set():
            if self._scan() != reading:
                self._emit(key)
                return
            if time.monotonic() - pressed_at >= KEYPAD_HOLD_SECONDS:
                self._emit(hold_event)
                _wait_for_release(self._stop, self._scan)
                return
            time.sleep(KEYPAD_SCAN_DELAY_SECONDS)

    def _run(self) -> None:
        """Scan forever. An unexpected error (GPIO, gpiod) is logged once and
        scanning retries every second — uncaught it would end the thread and
        leave the keypad dead."""
        failing = False
        while not self._stop.is_set():
            try:
                self._scan_once_through()
                failing = False
            except Exception:
                if not failing:
                    log.exception("Keypad scanner error; retrying every second")
                failing = True
                self._held_key = None
                self._stop.wait(1)

    def _scan_once_through(self) -> None:
        """Wait for one press and handle it through to release."""
        if self._edges is not None:
            self._sleep_until_edge()
            reading = _wait_for_press(self._stop, self._scan, KEYPAD_WAKE_IDLE_SECONDS)
        else:
            reading = _wait_for_press(self._stop, self._scan)
        if reading is None:
            return
        label = self._map.get(reading)
        if label is None:
            _wait_for_release(self._stop, self._scan)
            return

        key = resolve_label(label)
        self._held_key = key
        try:
            hold_event = self._hold_keys.get(key)
            if hold_event is not None:
                self._tap_or_hold(reading, key, hold_event)
                return

            self._emit(key)

            if key in self._repeatable_keys:
                self._repeat_while_held(reading, key)
            else:
                _wait_for_release(self._stop, self._scan)
        finally:
            self._held_key = None

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
        os.write(self._stop_w, b"\x00")
        self._thread.join(timeout=1)
        if self._edges is not None:
            self._edges.release()
        for fd in (self._wake_r, self._wake_w, self._stop_r, self._stop_w):
            try:
                os.close(fd)
            except OSError:
                pass
        teardown_pins()

