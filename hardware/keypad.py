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
    KEYPAD_PRESS_HITS,
    KEYPAD_RELEASE_SECONDS,
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
        # Readings already handled and still held (-> when last read):
        # ignored until released, so a key held through another press, or
        # flickering as it's let go, isn't read twice.
        self._handled: dict[str, float] = {}
        # When anything last read as pressed, for _wait_for_new_press()'s log.
        self._last_contact = 0.0
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
        back high before returning, ready for _pressed()."""
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

    def _pressed(self) -> set[str]:
        """Every reading pressed right now, so overlapping presses are each
        seen. Pin directions are set once in __init__, so this only toggles
        output values. Falls back to scan_once()'s single reading for a map
        that isn't a clean two-sided matrix."""
        if not self._fast_path:
            reading = scan_once()
            return {reading} if reading is not None else set()

        pressed = {str(pin) for pin in self._direct_pins if GPIO.input(pin) == GPIO.LOW}
        for drive_pin in self._side_a:
            GPIO.output(drive_pin, GPIO.LOW)
            time.sleep(KEYPAD_SCAN_DELAY_SECONDS)
            for sense_pin in self._side_b:
                if GPIO.input(sense_pin) == GPIO.LOW:
                    pressed.add(f"{min(drive_pin, sense_pin)}+{max(drive_pin, sense_pin)}")
            GPIO.output(drive_pin, GPIO.HIGH)
        return pressed

    def _emit(self, key: str) -> None:
        self.events.put(key)
        os.write(self._wake_w, b"\x00")

    def _new_press(self, pressed: set[str], hits: dict[str, list[float]],
                   now: float) -> str | None:
        """A reading newly pressed (not one already handled and still held)
        once it's been read in KEYPAD_PRESS_HITS sweeps of one contact: a
        gap over KEYPAD_RELEASE_SECONDS starts its count over. *hits*
        (reading -> [count, last read]) carries counts between sweeps. Also
        forgets handled keys unread for KEYPAD_RELEASE_SECONDS, and logs a
        reading that stopped before reaching the count, as evidence for
        missed presses."""
        if pressed:
            self._last_contact = now
        for reading in pressed & self._handled.keys():
            self._handled[reading] = now
        for reading, seen in list(self._handled.items()):
            if now - seen >= KEYPAD_RELEASE_SECONDS:
                del self._handled[reading]
        for reading, (count, seen) in list(hits.items()):
            if now - seen >= KEYPAD_RELEASE_SECONDS:
                del hits[reading]
                log.info("keypad: press not registered: %s (%s) read in %d of %d sweeps needed",
                         reading, self._map.get(reading, "unmapped"), count, KEYPAD_PRESS_HITS)
        for reading in pressed - self._handled.keys():
            count = hits.get(reading, [0, now])[0] + 1
            hits[reading] = [count, now]
            if count >= KEYPAD_PRESS_HITS:
                return reading
        return None

    def _wait_for_new_press(self, give_up_after: float | None) -> str | None:
        """Wait for _new_press(). None once nothing new has read as pressed
        for *give_up_after* seconds (back to the edge sleep), or on close().
        After an edge wake (*give_up_after* set) the sweeps run back to back.
        A wake that reads nothing at all is logged, as evidence for missed
        presses (a reading seen too few times is logged by _new_press())."""
        hits: dict[str, list[float]] = {}
        started = last_seen = time.monotonic()
        quiet_before = started - self._last_contact
        while not self._stop.is_set():
            pressed = self._pressed()
            now = time.monotonic()
            reading = self._new_press(pressed, hits, now)
            if reading is not None:
                return reading
            if hits:
                last_seen = now
            elif give_up_after is not None and now - last_seen >= give_up_after:
                if self._last_contact < started and not self._handled and quiet_before >= 0.2:
                    # Wakes right after a release are its bounce, not a press.
                    log.info("keypad: woke on an edge but no key read")
                return None
            if give_up_after is None and not hits:
                time.sleep(KEYPAD_POLL_INTERVAL_SECONDS)
            else:
                time.sleep(KEYPAD_SCAN_DELAY_SECONDS)
        return None

    def _until_released(self, reading: str, repeat_key: str | None = None) -> str | None:
        """Wait for *reading*'s release (unread for KEYPAD_RELEASE_SECONDS),
        repeating *repeat_key* while it's held. Returns a key pressed
        meanwhile (handled next, so a press that overlaps the last one
        isn't lost), or None once released."""
        hits: dict[str, list[float]] = {}
        held_since = time.monotonic()
        repeating = False
        while not self._stop.is_set():
            now = time.monotonic()
            new = self._new_press(self._pressed(), hits, now)
            if new is not None or reading not in self._handled:
                return new
            if repeat_key is not None:
                interval = self._repeat_interval if repeating else KEYPAD_REPEAT_DELAY_SECONDS
                if now - held_since >= interval:
                    self._emit(repeat_key)
                    held_since = now
                    repeating = True
            time.sleep(KEYPAD_SCAN_DELAY_SECONDS)
        return None

    def _tap_or_hold(self, reading: str, key: str, hold_event: str) -> str | None:
        """Emit *key* on release (or when another key is pressed), or
        *hold_event* once held past KEYPAD_HOLD_SECONDS (see
        set_hold_keys()). Returns a key pressed meanwhile, like
        _until_released()."""
        hits: dict[str, list[float]] = {}
        pressed_at = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            new = self._new_press(self._pressed(), hits, now)
            if new is not None or reading not in self._handled:
                self._emit(key)
                return new
            if now - pressed_at >= KEYPAD_HOLD_SECONDS:
                self._emit(hold_event)
                return self._until_released(reading)
            time.sleep(KEYPAD_SCAN_DELAY_SECONDS)
        return None

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
                self._handled = {}
                self._stop.wait(1)

    def _scan_once_through(self) -> None:
        """Wait for a press and handle it, and any pressed before it was
        released, through to release. With a handled key still held, the
        edge sleep returns at once (its sense pin is low), so this polls
        until it's let go."""
        if self._edges is not None:
            self._sleep_until_edge()
            reading = self._wait_for_new_press(KEYPAD_WAKE_IDLE_SECONDS)
        else:
            reading = self._wait_for_new_press(None)
        while reading is not None and not self._stop.is_set():
            reading = self._handle(reading)

    def _handle(self, reading: str) -> str | None:
        """Emit *reading*'s key (with repeat or tap/hold) until it's
        released or another key is pressed; returns that key, if any."""
        self._handled[reading] = time.monotonic()
        label = self._map.get(reading)
        if label is None:
            log.info("keypad: unmapped reading %s", reading)
            return self._until_released(reading)

        key = resolve_label(label)
        log.info("keypad: %s (%s)", key, reading)
        self._held_key = key
        try:
            hold_event = self._hold_keys.get(key)
            if hold_event is not None:
                return self._tap_or_hold(reading, key, hold_event)
            self._emit(key)
            return self._until_released(
                reading, key if key in self._repeatable_keys else None)
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

