"""Active Device Control — the default/boot screen.

Owns the app's main loop (see main.py): boots straight into this instead of
a menu detour, and runs until state.running goes False. Everything else
(Full Device List, Learn, Command, Home, Settings) lives behind the Menu
key and is reached via app/quick_actions.py, which also owns switching the
active device (< Device/Device >, Full Device List selection, Learn) — see
docs/active-device-control-plan.md for the full design.

Tracks one active leaf device (state.active_device, state.active_location):
  - DEFAULT_ACTIVE_DEVICE ("google_tv") is raw D-pad passthrough — Up/Down/
    Left/Right/Enter each fire the matching signal immediately, no function
    list, no exceptions.
  - Every other device shows one function at a time (see
    services.signal_service.list_functions) — Left/Right cycle functions,
    Up/Down fire a paired function's two signals, Enter fires a standalone
    function.
"""

import time
from hardware.lcd import LCD
from hardware import ir_transmitter
from hardware.input import (
    read_key, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER, KEY_QUIT,
)
from services import home_service, signal_service
from screens.widgets.format import format_name
from app.constants import DEFAULT_ACTIVE_DEVICE
from app.state import AppState
from app.exceptions import HardwareError

_DPAD_BUTTONS = {
    KEY_UP: "up", KEY_DOWN: "down", KEY_LEFT: "left",
    KEY_RIGHT: "right", KEY_ENTER: "enter",
}


def show(lcd: LCD, state: AppState) -> None:
    function_index = 0
    previous_leaf = (None, None)

    while state.running:
        home = home_service.get_active_home()

        if state.active_device is None:
            state.active_device, state.active_location = signal_service.default_leaf_device(home)

        device, location = state.active_device, state.active_location
        if (device, location) != previous_leaf:
            function_index = 0
            previous_leaf = (device, location)

        if not device:
            lcd.write("No signals", format_name(home)[:16])
            if read_key() == KEY_QUIT:
                state.running = False
                return
            continue

        if device == DEFAULT_ACTIVE_DEVICE:
            lcd.write(format_name(device), "D-pad Control")
            key = read_key()
            if key == KEY_QUIT:
                state.running = False
                return
            button = _DPAD_BUTTONS.get(key)
            if button:
                _transmit(lcd, home, device, location, button)
            continue

        functions = signal_service.list_functions(home, device, location)
        if not functions:
            lcd.write(format_name(device), "No signals")
            if read_key() == KEY_QUIT:
                state.running = False
                return
            continue

        function_index %= len(functions)
        function = functions[function_index]
        lcd.write(format_name(device), format_name(function["name"]))

        key = read_key()
        if key == KEY_QUIT:
            state.running = False
            return
        if key == KEY_LEFT:
            function_index = (function_index - 1) % len(functions)
        elif key == KEY_RIGHT:
            function_index = (function_index + 1) % len(functions)
        elif key == KEY_UP and function["type"] == "paired":
            _transmit(lcd, home, device, location, function["up"])
        elif key == KEY_DOWN and function["type"] == "paired":
            _transmit(lcd, home, device, location, function["down"])
        elif key == KEY_ENTER and function["type"] == "standalone":
            _transmit(lcd, home, device, location, function["button"])


def _transmit(lcd: LCD, home: str, device: str, location: str, button: str) -> None:
    path = signal_service.signal_path(home, device, button, location)
    if not path.exists():
        lcd.write(format_name(device), "Signal missing")
        time.sleep(1)
        return
    try:
        ir_transmitter.transmit(path)
    except HardwareError as e:
        lcd.write("TX Error", str(e)[:16])
        time.sleep(1)
