"""Input: the GPIO keypad (hardware/keypad.py), USB keyboards/remotes
(evdev) and an SSH terminal, merged behind read_key(). The rest of the app
only calls read_key() and the key-behaviour setters below.
"""

import os
import sys
import termios
import tty
from select import select

# noinspection PyUnresolvedReferences
from evdev import InputDevice, list_devices, categorize, ecodes

# ---------------------------------------------------------------------------
# Key constants — the only key vocabulary the rest of the app uses.
# ---------------------------------------------------------------------------
KEY_UP        = "UP"
KEY_DOWN      = "DOWN"
KEY_LEFT      = "LEFT"
KEY_RIGHT     = "RIGHT"
KEY_ENTER     = "ENTER"
KEY_QUIT      = "QUIT"
KEY_BACK      = "BACK"
KEY_ENTER_HOLD = "ENTER_HOLD"  # Enter held — only on screens that opt in, see set_hold_keys()


# ---------------------------------------------------------------------------
# Keypad quick-action keys (only the keypad sends these). read_key() hands
# them to the quick-action handler, so they work from any screen.
# ---------------------------------------------------------------------------
KEY_DEVICE_1_UP   = "DEVICE_1_UP"
KEY_DEVICE_1_DOWN = "DEVICE_1_DOWN"
KEY_DEVICE_2_UP   = "DEVICE_2_UP"
KEY_DEVICE_2_DOWN = "DEVICE_2_DOWN"
KEY_DEVICE_3_UP   = "DEVICE_3_UP"
KEY_DEVICE_3_DOWN = "DEVICE_3_DOWN"
KEY_DEVICE_4_UP   = "DEVICE_4_UP"
KEY_DEVICE_4_DOWN = "DEVICE_4_DOWN"
KEY_DEVICE_NEXT   = "DEVICE_NEXT"
KEY_DEVICE_PREV   = "DEVICE_PREV"
KEY_PRESET_1      = "PRESET_1"
KEY_PRESET_2      = "PRESET_2"
KEY_PRESET_3      = "PRESET_3"
KEY_PRESET_4      = "PRESET_4"
KEY_MENU          = "MENU"

class DeviceCycledAway(Exception):
    """Raised by the < Device/Device > quick action while a Menu screen is
    up. Not caught here: it unwinds out of the Menu to Active Device
    Control's own read_key() call, which catches it and reads afresh for
    the new device."""


QUICK_ACTION_KEYS = frozenset({
    KEY_DEVICE_1_UP, KEY_DEVICE_1_DOWN, KEY_DEVICE_2_UP, KEY_DEVICE_2_DOWN,
    KEY_DEVICE_3_UP, KEY_DEVICE_3_DOWN, KEY_DEVICE_4_UP, KEY_DEVICE_4_DOWN,
    KEY_PRESET_1, KEY_PRESET_2, KEY_PRESET_3, KEY_PRESET_4,
    KEY_DEVICE_PREV, KEY_DEVICE_NEXT, KEY_MENU,
})


class InputReader:
    def __init__(self) -> None:
        self.devices: dict[int, InputDevice] = {}

        for path in list_devices():
            try:
                device = InputDevice(path)
                if ecodes.EV_KEY in device.capabilities():
                    self.devices[device.fd] = device
            except Exception:
                continue

        # Terminal input only when stdin is a TTY (under systemd it's
        # /dev/null).
        self.stdin_fd: int | None = None
        self._old_settings = None
        if sys.stdin.isatty():
            self.stdin_fd = sys.stdin.fileno()
            self._old_settings = termios.tcgetattr(self.stdin_fd)
            tty.setraw(self.stdin_fd)

        self.keypad = None
        try:
            from hardware import keypad as keypad_module
            self.keypad = keypad_module.KeypadScanner()
        except Exception as e:
            print(f"Keypad unavailable: {e}\r")

        if self.devices:
            print("Monitoring USB input devices:\r")
            for device in self.devices.values():
                print(f"  - {device.name}\r")
        if self.keypad:
            print("Monitoring GPIO keypad\r")
        if self.stdin_fd is not None:
            print("Also monitoring SSH keyboard input\r")

    def close(self) -> None:
        if self.keypad is not None:
            try:
                self.keypad.close()
            except Exception:
                pass
            self.keypad = None
        try:
            if self.stdin_fd is not None:
                termios.tcsetattr(self.stdin_fd, termios.TCSADRAIN, self._old_settings)
        except Exception:
            pass

    def _read_stdin_key(self) -> str | None:
        """One key from the SSH terminal (raw mode). Reads the fd directly, not
        through sys.stdin's buffer, which can hold bytes select() doesn't
        see; an escape sequence arrives in one read."""
        data = os.read(self.stdin_fd, 16).decode(errors="ignore")
        if not data:
            return None

        if data.startswith("\x1b"):
            if data.startswith("\x1b[") and len(data) >= 3:
                return {"A": KEY_UP, "B": KEY_DOWN, "C": KEY_RIGHT, "D": KEY_LEFT}.get(data[2])
            return None

        ch = data[0]
        if ch in ("\r", "\n"):
            return KEY_ENTER
        if ch == "\x03":
            # Ctrl+C (raw mode delivers it as a byte, not SIGINT). Only for SSH
            # sessions; there's deliberately no quit key on the device.
            return KEY_QUIT
        return None

    # USB keyboard/remote keys -> app keys. Navigation only: no screen takes
    # typed text.
    _USB_KEYS = {
        "KEY_UP": KEY_UP, "KEY_DOWN": KEY_DOWN, "KEY_LEFT": KEY_LEFT, "KEY_RIGHT": KEY_RIGHT,
        "KEY_ENTER": KEY_ENTER, "KEY_KPENTER": KEY_ENTER, "BTN_RIGHT": KEY_ENTER,
    }

    def _read_usb_key(self, fd: int) -> str | None:
        device = self.devices[fd]
        try:
            events = list(device.read())
        except BlockingIOError:
            return None  # nothing to read after all
        except OSError as e:
            # Unplugged (or otherwise gone): stop listening to it rather than
            # let the error end the app.
            print(f"Input device removed: {device.name} ({e})\r")
            del self.devices[fd]
            try:
                device.close()
            except OSError:
                pass
            return None
        for event in events:
            if event.type == ecodes.EV_KEY and event.value == 1:
                keycode = categorize(event).keycode
                if isinstance(keycode, (list, tuple)):
                    keycode = keycode[0]
                if keycode in self._USB_KEYS:
                    return self._USB_KEYS[keycode]
        return None

    def read_key(self, timeout: float | None = None) -> str | None:
        """Block for a key; with *timeout* (seconds), return None if none
        arrives in time."""
        import time
        from hardware.lcd import lcd

        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            if not self.devices and self.keypad is None and self.stdin_fd is None:
                # No input at all: show the IP so it can still be reached
                # over SSH.
                from hardware import network
                ip = network.get_ip_address()
                lcd.write("No input device", ip[:16] if ip else "WiFi not conn.")
                time.sleep(5)
                raise SystemExit(1)  # nonzero: systemd retries, in case it was transient

            read_fds = list(self.devices.keys())
            read_fds.append(_quit_r)
            if self.stdin_fd is not None:
                read_fds.append(self.stdin_fd)
            if self.keypad is not None:
                read_fds.append(self.keypad.wake_fd)

            select_timeout = None if deadline is None else max(deadline - time.monotonic(), 0)
            r, _, _ = select(read_fds, [], [], select_timeout)

            if not r:
                return None  # only reachable with a timeout set

            if _quit_r in r:
                return KEY_QUIT
            for fd in r:
                if fd == self.stdin_fd:
                    result = self._read_stdin_key()
                elif self.keypad is not None and fd == self.keypad.wake_fd:
                    result = self.keypad.pop()
                else:
                    result = self._read_usb_key(fd)
                if result:
                    return result


# ---------------------------------------------------------------------------
# Module-level singleton and convenience function
# ---------------------------------------------------------------------------
# request_quit(): once set, every read_key() returns KEY_QUIT, so whichever
# screens are waiting unwind through their normal quit path. The pipe wakes a
# read already blocked in select().
_quit_requested = False
_quit_r, _quit_w = os.pipe()

_reader: InputReader | None = None
# Keys read_key() has received, so a late background result can tell
# whether anything was pressed since it started (see press_count()).
_press_count = 0
_quick_handler = None
_key_observer = None
_wake_handler = None


def _get_reader() -> InputReader:
    global _reader
    if _reader is None:
        _reader = InputReader()
    return _reader


def set_repeatable_keys(extra: set[str], exclude: set[str] = frozenset()) -> None:
    """Which keys auto-repeat while held on the keypad (a no-op without
    one): Up/Down by default, plus *extra*, minus *exclude*."""
    reader = _get_reader()
    if reader.keypad is not None:
        reader.keypad.set_repeatable_keys(extra, exclude)


def use_default_keys() -> None:
    """The baseline hold behaviour, for list screens and anything resetting
    a previous screen's (e.g. Google TV's D-pad repeat): Up/Down repeat so a
    long list scrolls while held; Left (Back) doesn't, so a hold can't back
    out several levels."""
    set_repeatable_keys(set())
    set_repeat_interval(None)


def set_hold_keys(holds: dict[str, str]) -> None:
    """Tap vs. hold for the keys in *holds* (key -> event sent on a hold,
    e.g. {KEY_ENTER: KEY_ENTER_HOLD}); {} turns it off. Keypad only."""
    reader = _get_reader()
    if reader.keypad is not None:
        reader.keypad.set_hold_keys(holds)


def is_key_held(key: str) -> bool:
    """True while *key* is still held on the keypad (always False for
    USB/SSH input, which reports presses only)."""
    reader = _get_reader()
    return reader.keypad is not None and reader.keypad.is_held(key)


def set_repeat_interval(seconds: float | None) -> None:
    """Auto-repeat pace on the keypad; None restores the default."""
    reader = _get_reader()
    if reader.keypad is not None:
        reader.keypad.set_repeat_interval(seconds)


def set_quick_action_handler(handler) -> None:
    """Register *handler(key)* for QUICK_ACTION_KEYS: read_key() runs it
    instead of returning the key, then keeps waiting — or returns None at
    once if it returns True (see app/quick_actions.py's handle())."""
    global _quick_handler
    _quick_handler = handler


def set_key_observer(observer) -> None:
    """Register *observer(key)*, called for every key before anything else
    sees it. Notification only, on the reading thread: it must not block or
    raise."""
    global _key_observer
    _key_observer = observer


def set_wake_handler(handler) -> None:
    """Register *handler(key)*, called after the observer; True swallows the
    key (main.py: the press that relights a dark screen does nothing else)."""
    global _wake_handler
    _wake_handler = handler


def read_key(timeout: float | None = None) -> str | None:
    """Wait for a key; with *timeout* (seconds), return None if none arrives
    in time. Quick-action keys are handled here and the wait resumes."""
    import time
    deadline = None if timeout is None else time.monotonic() + timeout

    while True:
        if _quit_requested:
            return KEY_QUIT
        remaining = None if deadline is None else max(deadline - time.monotonic(), 0)
        key = _get_reader().read_key(remaining)
        if key is None:
            return None
        global _press_count
        _press_count += 1
        if _key_observer is not None:
            _key_observer(key)
        if _wake_handler is not None and _wake_handler(key):
            continue
        if _quick_handler is not None and key in QUICK_ACTION_KEYS:
            if _quick_handler(key):
                return None
            continue
        return key


def request_quit() -> None:
    """Make every read_key() return KEY_QUIT from now on, waking one already
    blocked, so the screens unwind normally (main.py's signal handler).
    Signal-safe: a flag and a one-byte pipe write."""
    global _quit_requested
    _quit_requested = True
    try:
        os.write(_quit_w, b"\x00")
    except OSError:
        pass


def press_count() -> int:
    """Keys pressed so far. A late background failure message draws only if
    this hasn't changed since its step started (the same screen is up)."""
    return _press_count


def close() -> None:
    global _reader
    if _reader is not None:
        try:
            _reader.close()
        except Exception:
            pass
        _reader = None
