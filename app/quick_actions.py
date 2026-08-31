"""Global keypad quick actions.

Wired into hardware.input via set_quick_action_handler at startup (see
main.py), so device/preset/menu shortcuts on the physical keypad fire from
anywhere in the app — read_key() intercepts them before returning to
whichever screen is currently blocked waiting for a key.

init(state) must be called once at startup (see main.py) before any of
these touch AppState — handle() itself stays a plain function of just the
key, matching hardware.input.set_quick_action_handler's calling convention.
"""

import time

from hardware.lcd import lcd
from hardware import ir_transmitter
from hardware.input import (
    KEY_DEVICE_1_UP, KEY_DEVICE_1_DOWN, KEY_DEVICE_2_UP, KEY_DEVICE_2_DOWN,
    KEY_DEVICE_3_UP, KEY_DEVICE_3_DOWN, KEY_DEVICE_4_UP, KEY_DEVICE_4_DOWN,
    KEY_PRESET_1, KEY_PRESET_2, KEY_PRESET_3, KEY_PRESET_4,
    KEY_DEVICE_PREV, KEY_DEVICE_NEXT, KEY_MENU, KEY_QUIT,
)
from services import (
    device_slot_service, preset_service, signal_service, config_service, voice_service, home_service,
)
from app.exceptions import HardwareError
from app.state import AppState

_DEVICE_KEYS = {
    KEY_DEVICE_1_UP: (0, "up"), KEY_DEVICE_1_DOWN: (0, "down"),
    KEY_DEVICE_2_UP: (1, "up"), KEY_DEVICE_2_DOWN: (1, "down"),
    KEY_DEVICE_3_UP: (2, "up"), KEY_DEVICE_3_DOWN: (2, "down"),
    KEY_DEVICE_4_UP: (3, "up"), KEY_DEVICE_4_DOWN: (3, "down"),
}
_PRESET_KEYS = {KEY_PRESET_1: 1, KEY_PRESET_2: 2, KEY_PRESET_3: 3, KEY_PRESET_4: 4}
_CYCLE_DIRECTIONS = {KEY_DEVICE_PREV: -1, KEY_DEVICE_NEXT: 1}

_state: AppState | None = None


def init(state: AppState) -> None:
    """Give this module access to AppState. Call once from main.py."""
    global _state
    _state = state


def handle(key: str) -> None:
    if key in _DEVICE_KEYS:
        _handle_device(key)
    elif key in _PRESET_KEYS:
        _handle_preset(_PRESET_KEYS[key])
    elif key in _CYCLE_DIRECTIONS:
        _handle_device_cycle(key)
    elif key == KEY_MENU:
        _handle_menu()


def _handle_device(key: str) -> None:
    slot_index, direction = _DEVICE_KEYS[key]
    offset = config_service.get_device_slot_offset()
    slot = device_slot_service.get_slot(offset, slot_index)

    if slot is None:
        lcd.write(f"Device {slot_index + 1}", "Not configured")
        time.sleep(1)
        return

    label = slot.get("label", "")[:16]

    if slot.get("type") == "voice":
        _handle_voice_device(label, slot, direction)
        return

    button = slot.get(f"{direction}_button")
    if not button:
        lcd.write(label, "No action set")
        time.sleep(1)
        return

    path = signal_service.signal_path(
        slot["home"], slot["device"], button, slot.get("location", "")
    )
    if not path.exists():
        lcd.write(label, "Signal missing")
        time.sleep(1)
        return

    try:
        ir_transmitter.transmit(path)
        lcd.write(label, button.replace("_", " ").title())
    except HardwareError as e:
        lcd.write("TX Error", str(e)[:16])
    time.sleep(0.6)


def _handle_voice_device(label: str, slot: dict, direction: str) -> None:
    command = slot.get(f"{direction}_command")
    if not command:
        lcd.write(label, "No action set")
        time.sleep(1)
        return

    lcd.write(label, "Sending...")
    success, message = voice_service.send_command(command)
    lcd.write(label, message[:16] if success else f"Err: {message}"[:16])
    time.sleep(1)


def _handle_preset(number: int) -> None:
    """Preset Function N — runs the active device's override for slot N if
    it has one (see services/preset_service.py), else the global default.
    Active device is read-only here; presses never change it."""
    device = _state.active_device or ""
    location = _state.active_location or ""

    lcd.write(f"Preset {number}", "Running...")
    sent, total = preset_service.run_preset(number, device, location)
    if total == 0:
        message = "Not configured"
    elif sent == total:
        message = f"{sent} sent"
    else:
        message = f"{sent}/{total} sent"
    lcd.write(f"Preset {number}", message)
    time.sleep(1)


def _handle_device_cycle(key: str) -> None:
    """< Device / Device > — cycle screens.active_device_screen's active
    device. Global (works from any screen), replacing the old Send-screen
    -scoped cycle."""
    direction = _CYCLE_DIRECTIONS[key]
    home = home_service.get_active_home()
    current = (_state.active_device, _state.active_location)
    if current[0] is None:
        current = signal_service.default_leaf_device(home)

    device, location = signal_service.cycle_leaf_device(home, current, direction)
    _state.active_device, _state.active_location = device, location

    # Show the real, already-interactive screen for the new device right
    # away — no separate "Active device" confirmation banner, which used a
    # fixed sleep that only delayed the next real button press without
    # adding anything the screen itself doesn't already make obvious.
    import screens.active_device_screen as active_device_screen
    active_device_screen.render_after_cycle(lcd, _state)


def _handle_menu() -> None:
    """Menu key — the escape hatch. Opens screens.main_menu and dispatches
    to whichever screen is chosen directly, since a quick action doesn't
    feed back into an outer dispatch loop (there isn't one anymore — see
    screens/active_device_screen.py, which owns the app's main loop)."""
    # This can fire while active_device_screen.py has google_tv's D-pad
    # Left/Right hold-to-scroll turned on (it re-asserts that per loop
    # iteration, but this quick action runs from inside its blocked
    # read_key() call, mid-iteration) — clear it (and its widened repeat
    # interval) so e.g. holding Left in a menu screen's list doesn't
    # rapid-fire "Back" or, worse, rapid-delete characters in the D-pad
    # text entry widget at the wrong pace. active_device_screen.py
    # reasserts the right state again on its own next loop pass regardless.
    from hardware import input as input_hw
    input_hw.set_repeatable_keys(set())
    input_hw.set_repeat_interval(None)

    import screens.main_menu as main_menu
    import screens.command_screen as command_screen
    import screens.home_screen as home_screen
    import screens.receive_screen as receive_screen
    import screens.network_screen as network_screen
    from app.constants import MENU_LEARN, MENU_COMMAND, MENU_HOME, MENU_NETWORK

    while _state.running:
        choice = main_menu.show(lcd, _state)

        if choice is None:
            return
        if choice == KEY_QUIT:
            _state.running = False
            return

        if choice == MENU_LEARN:
            result = receive_screen.show(lcd, _state)
        elif choice == MENU_COMMAND:
            result = command_screen.show(lcd)
        elif choice == MENU_HOME:
            result = home_screen.show(lcd, _state)
        elif choice == MENU_NETWORK:
            result = network_screen.show(lcd)
        else:
            result = None

        if result == KEY_QUIT:
            _state.running = False
            return
