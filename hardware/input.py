"""Input hardware abstraction.

Reads key events from:
  - USB HID keyboards (via evdev)
  - SSH terminal (via stdin / termios)

The rest of the application only ever calls ``read_key()``.
Adding a GPIO navigation module in the future means only this file changes.
"""

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
KEY_BACKSPACE = "BACKSPACE"

_COMMANDS = {KEY_UP, KEY_DOWN, KEY_RIGHT, KEY_LEFT, KEY_ENTER, KEY_QUIT, KEY_BACKSPACE}

# ---------------------------------------------------------------------------
# Keypad quick-action keys — physical shortcuts with no on-screen equivalent.
# These never come from a USB keyboard or SSH; only hardware/keypad.py emits
# them. They're intercepted globally by read_key() below (see
# set_quick_action_handler) so they work from any screen, not just whichever
# one currently has read_key() blocked.
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
KEY_LEARN         = "LEARN"
KEY_TV_MUTE       = "TV_MUTE"
KEY_MENU          = "MENU"

QUICK_ACTION_KEYS = frozenset({
    KEY_DEVICE_1_UP, KEY_DEVICE_1_DOWN, KEY_DEVICE_2_UP, KEY_DEVICE_2_DOWN,
    KEY_DEVICE_3_UP, KEY_DEVICE_3_DOWN, KEY_DEVICE_4_UP, KEY_DEVICE_4_DOWN,
    KEY_PRESET_1, KEY_PRESET_2, KEY_PRESET_3, KEY_PRESET_4,
    KEY_DEVICE_PREV, KEY_DEVICE_NEXT, KEY_MENU,
})
# KEY_DEVICE_PREV/KEY_DEVICE_NEXT ("< Device" / "Device >") cycle
# screens.active_device_screen's active device — global, like everything
# else above, per docs/active-device-control-plan.md. KEY_MENU opens the
# escape-hatch menu (screens/main_menu.py) from anywhere.
#
# KEY_LEARN and KEY_TV_MUTE are no longer bound to physical keys (Learn
# moved into the Menu; Mute is deferred — see the plan doc) and so are no
# longer quick actions, but the constants stay defined since
# hardware/keypad.py's label aliases still recognize them.


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

        # SSH/terminal input is only available when stdin is an actual TTY —
        # under systemd (no controlling terminal) stdin is /dev/null, and
        # termios.tcgetattr on it raises. Skip that path there; USB/GPIO
        # keypad input still works either way.
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

    @staticmethod
    def _read_stdin_key() -> str | None:
        ch = sys.stdin.read(1)

        if ch == "\x1b":
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                return {"A": KEY_UP, "B": KEY_DOWN, "C": KEY_RIGHT, "D": KEY_LEFT}.get(ch3)
            return None

        if ch in ("\r", "\n"):
            return KEY_ENTER
        if ch == "\x7f":
            return KEY_BACKSPACE
        if ch == "\x03":
            # Ctrl+C, for SSH development sessions only. Raw mode
            # (tty.setraw above) clears ISIG, so the tty driver never turns
            # this into SIGINT — it arrives here as a plain byte instead.
            # 'q'/Esc are intentionally NOT quit keys — use the menu's Quit
            # option or the physical shutdown button.
            return KEY_QUIT
        if ch.isprintable():
            return ch

        return None

    def _read_usb_key(self, fd: int) -> str | None:
        device = self.devices[fd]

        for event in device.read():
            if event.type == ecodes.EV_KEY and event.value == 1:
                key_event = categorize(event)
                keycode = key_event.keycode

                if isinstance(keycode, (list, tuple)):
                    keycode = keycode[0]

                _direct = {
                    "KEY_UP":        KEY_UP,
                    "KEY_DOWN":      KEY_DOWN,
                    "KEY_LEFT":      KEY_LEFT,
                    "KEY_RIGHT":     KEY_RIGHT,
                    "KEY_ENTER":     KEY_ENTER,
                    "KEY_KPENTER":   KEY_ENTER,
                    "BTN_RIGHT":     KEY_ENTER,
                    "KEY_BACKSPACE": KEY_BACKSPACE,
                    "KEY_DELETE":    KEY_BACKSPACE,
                }
                if keycode in _direct:
                    return _direct[keycode]

                if isinstance(keycode, str) and keycode.startswith("KEY_"):
                    key_char = keycode[4:]

                    if len(key_char) == 1 and key_char.isalpha():
                        return key_char.lower()
                    if len(key_char) == 1 and key_char.isdigit():
                        return key_char
                    if key_char.startswith("KP") and len(key_char) == 3:
                        return key_char[2]

                    _special = {
                        "SPACE": " ", "MINUS": "-", "EQUAL": "=",
                        "LEFTBRACE": "[", "RIGHTBRACE": "]", "SEMICOLON": ";",
                        "APOSTROPHE": "'", "GRAVE": "`", "BACKSLASH": "\\",
                        "COMMA": ",", "DOT": ".", "SLASH": "/",
                        "KPASTERISK": "*", "KPMINUS": "-", "KPPLUS": "+",
                        "KPDOT": ".", "KPSLASH": "/",
                    }
                    if key_char in _special:
                        return _special[key_char]

        return None

    def read_key(self) -> str:
        import time
        from hardware.lcd import lcd

        while True:
            if not self.devices and self.keypad is None and self.stdin_fd is None:
                lcd.write("Input unavailable", "Exiting.")
                time.sleep(5)
                raise SystemExit(0)

            read_fds = list(self.devices.keys())
            if self.stdin_fd is not None:
                read_fds.append(self.stdin_fd)
            if self.keypad is not None:
                read_fds.append(self.keypad.wake_fd)

            r, _, _ = select(read_fds, [], [])

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
_reader: InputReader | None = None
_quick_handler = None


def _get_reader() -> InputReader:
    global _reader
    if _reader is None:
        _reader = InputReader()
    return _reader


def set_quick_action_handler(handler) -> None:
    """Register a callback for QUICK_ACTION_KEYS.

    Called from app/quick_actions.py at startup. When set, read_key() runs
    the handler itself and keeps waiting instead of returning the key to its
    caller — that's what makes device/preset/learn shortcuts work no matter
    which screen currently has read_key() blocked.
    """
    global _quick_handler
    _quick_handler = handler


def read_key() -> str:
    while True:
        key = _get_reader().read_key()
        if _quick_handler is not None and key in QUICK_ACTION_KEYS:
            _quick_handler(key)
            continue
        return key


def close() -> None:
    global _reader
    if _reader is not None:
        try:
            _reader.close()
        except Exception:
            pass
        _reader = None
