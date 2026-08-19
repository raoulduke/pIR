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

        self.stdin_fd = sys.stdin.fileno()
        self._old_settings = termios.tcgetattr(self.stdin_fd)
        tty.setraw(self.stdin_fd)

        if self.devices:
            print("Monitoring USB input devices:\r")
            for device in self.devices.values():
                print(f"  - {device.name}\r")
        print("Also monitoring SSH keyboard input\r")

    def close(self) -> None:
        try:
            if hasattr(self, "stdin_fd") and hasattr(self, "_old_settings"):
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
            return KEY_QUIT

        if ch in ("\r", "\n"):
            return KEY_ENTER
        if ch == "\x7f":
            return KEY_BACKSPACE
        if ch.lower() == "q":
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
                    "KEY_Q":         KEY_QUIT,
                    "KEY_ESC":       KEY_QUIT,
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
            if not self.devices:
                lcd.write("Input unavailable", "Exiting.")
                time.sleep(5)
                raise SystemExit(0)

            read_fds = list(self.devices.keys()) + [self.stdin_fd]

            r, _, _ = select(read_fds, [], [])

            for fd in r:
                if fd == self.stdin_fd:
                    result = self._read_stdin_key()
                else:
                    result = self._read_usb_key(fd)
                if result:
                    return result


# ---------------------------------------------------------------------------
# Module-level singleton and convenience function
# ---------------------------------------------------------------------------
_reader: InputReader | None = None


def _get_reader() -> InputReader:
    global _reader
    if _reader is None:
        _reader = InputReader()
    return _reader


def read_key() -> str:
    return _get_reader().read_key()


def close() -> None:
    global _reader
    if _reader is not None:
        try:
            _reader.close()
        except Exception:
            pass
        _reader = None
