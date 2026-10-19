"""Input hardware abstraction.

Reads key events from:
  - USB HID keyboards (via evdev)
  - SSH terminal (via stdin / termios)

The rest of the application only ever calls ``read_key()``.
Adding a GPIO navigation module in the future means only this file changes.
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
KEY_MENU          = "MENU"

class DeviceCycledAway(Exception):
    """Raised by app/quick_actions.py's device-cycle handler when
    < Device/Device > fires while a Menu screen is up, to unwind out of
    the Menu screen (and whichever widget under it currently has
    read_key() blocked) all the way back through *this module's own*
    read_key() call — the one a screen originally called and is still
    sitting inside, from before the Menu key was ever pressed — so that
    screen's own next call to read_key() is a genuinely fresh one, for the
    newly-active device, instead of silently continuing to block inside
    the old call.

    Deliberately NOT caught anywhere in this module — read_key() has no
    try/except around invoking the quick-action handler, so this
    propagates straight through to whichever screen is blocked on the
    outermost read_key() call, which is the only place with enough
    context to safely retry (see screens/active_device_screen.py's own
    catch, wrapping its whole read loop)."""


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

    def _read_stdin_key(self) -> str | None:
        """One key from the SSH terminal (raw mode). Reads whatever bytes are
        waiting straight from the fd: going through sys.stdin's buffer left
        bytes select() couldn't see (so arrow keys lagged), and an escape
        sequence's later bytes arrive in the same read, so a lone Esc no
        longer blocks waiting for more."""
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
            # Ctrl+C, for SSH development sessions only. Raw mode
            # (tty.setraw above) clears ISIG, so the tty driver never turns
            # this into SIGINT — it arrives here as a plain byte instead.
            # 'q'/Esc are intentionally NOT quit keys — use the physical
            # shutdown button.
            return KEY_QUIT
        return None

    # USB keyboard / remote keys -> app keys. Only navigation: nothing in the
    # app takes typed text (the D-pad composer, screens/widgets/char_input.py,
    # is how names are entered).
    _USB_KEYS = {
        "KEY_UP": KEY_UP, "KEY_DOWN": KEY_DOWN, "KEY_LEFT": KEY_LEFT, "KEY_RIGHT": KEY_RIGHT,
        "KEY_ENTER": KEY_ENTER, "KEY_KPENTER": KEY_ENTER, "BTN_RIGHT": KEY_ENTER,
    }

    def _read_usb_key(self, fd: int) -> str | None:
        for event in self.devices[fd].read():
            if event.type == ecodes.EV_KEY and event.value == 1:
                keycode = categorize(event).keycode
                if isinstance(keycode, (list, tuple)):
                    keycode = keycode[0]
                if keycode in self._USB_KEYS:
                    return self._USB_KEYS[keycode]
        return None

    def read_key(self, timeout: float | None = None) -> str | None:
        """Block for a key, or up to *timeout* seconds and return None if
        none arrives (e.g. screens/network_screen.py's periodic rescan).
        Default (None) blocks forever, unchanged from before this param
        existed."""
        import time
        from hardware.lcd import lcd

        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            if not self.devices and self.keypad is None and self.stdin_fd is None:
                # This is the one true "no input at all" check now (see
                # app/startup.py — a separate proactive check here used to
                # gate boot on the same condition, but only ever counted
                # evdev devices, not whether the keypad itself actually
                # came up; this one's the real thing either way, so it was
                # dead weight worth cutting). Show the IP so there's still
                # a way to reach the device (SSH) with zero physical input.
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
    """Forward to the physical keypad's scanner (see hardware/keypad.py's
    KeypadScanner.set_repeatable_keys), if one is actually present — a
    no-op otherwise (dev on WSL2, SSH-only input, or a keypad-map error at
    startup), same fallback style as everything else that touches
    self.keypad. Up/Down repeat by default; *extra* adds more keys on top
    and *exclude* removes baseline ones, for as long as the caller keeps
    re-asserting it — see screens/active_device_screen.py for the Google
    TV D-pad use, and use_default_keys() below for list screens."""
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
    """Forward to the physical keypad's scanner (KeypadScanner.
    set_hold_keys) — tap vs. hold for the keys in *holds*, e.g.
    {KEY_ENTER: KEY_ENTER_HOLD}. Pass {} to turn it off. A no-op without a
    keypad (USB/SSH input has no hold, so a hold event never arrives)."""
    reader = _get_reader()
    if reader.keypad is not None:
        reader.keypad.set_hold_keys(holds)


def is_key_held(key: str) -> bool:
    """True while *key* is still physically held on the keypad. Always
    False for USB/SSH input, which reports presses only — a hold-to-
    stream consumer then just sends once, like a tap."""
    reader = _get_reader()
    return reader.keypad is not None and reader.keypad.is_held(key)


def set_repeat_interval(seconds: float | None) -> None:
    """Forward to the physical keypad's scanner (see hardware/keypad.py's
    KeypadScanner.set_repeat_interval), if one is present — a no-op
    otherwise. Pass None to restore the default pace."""
    reader = _get_reader()
    if reader.keypad is not None:
        reader.keypad.set_repeat_interval(seconds)


def set_quick_action_handler(handler) -> None:
    """Register a callback for QUICK_ACTION_KEYS.

    Called from app/quick_actions.py at startup. When set, read_key() runs
    the handler itself and keeps waiting instead of returning the key to its
    caller — that's what makes device/preset/learn shortcuts work no matter
    which screen currently has read_key() blocked.

    *handler* returns a bool: True means read_key() should return None to
    its own caller right away, instead of continuing to wait for a real
    key — see app/quick_actions.py's handle() docstring for which quick
    actions want this (currently just Preset Function) and why.
    """
    global _quick_handler
    _quick_handler = handler


def set_key_observer(observer) -> None:
    """Register a callback fired on every real key this module returns —
    quick-action keys included, USB/SSH input included, before either the
    quick-action dispatch above or the caller ever sees it. Unlike
    set_quick_action_handler(), *observer* doesn't affect what read_key()
    returns or does — it's a side-effect-only notification, called from
    this same thread, so it must not block or raise. This module (like
    set_quick_action_handler above) stays agnostic of what a registered
    observer actually does — see main.py for what's wired up (currently
    services.wifi_wake_service.wake(), a speculative Wi-Fi radio nudge on
    every press so a Wi-Fi-dependent action a press or two later doesn't
    pay however long the radio takes to wake from power-save; see that
    module's docstring)."""
    global _key_observer
    _key_observer = observer


def set_wake_handler(handler) -> None:
    """Register a callback fired on every real key, after the key
    observer and before quick-action dispatch. *handler(key)* returns a
    bool: True swallows the key — read_key() keeps waiting as if it never
    arrived. See main.py: the LCD backlight's idle-off (hardware/lcd.py's
    wake()), so the press that lights a dark screen doesn't also act on
    whatever the user couldn't see."""
    global _wake_handler
    _wake_handler = handler


def read_key(timeout: float | None = None) -> str | None:
    """*timeout* (seconds): return None if no key arrives in time, instead
    of blocking forever. A quick action key doesn't consume the caller's
    timeout budget — it's handled here and waiting resumes for what's left
    of the original timeout."""
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
    blocked — main.py's signal handler, for a stop (SIGTERM/Ctrl+C) that
    unwinds the screens normally instead of raising mid-operation.
    Signal-safe: a flag and a one-byte pipe write."""
    global _quit_requested
    _quit_requested = True
    try:
        os.write(_quit_w, b"\x00")
    except OSError:
        pass


def press_count() -> int:
    """How many keys have been pressed so far. A background step's late
    failure message compares it with the count when the step started, and
    only draws if nothing was pressed in between — the screen it would
    overwrite is still the one the step was started from."""
    return _press_count


def close() -> None:
    global _reader
    if _reader is not None:
        try:
            _reader.close()
        except Exception:
            pass
        _reader = None
