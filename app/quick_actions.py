"""Global keypad quick actions: device slots, Preset Function 1-4,
< Device/Device > and Menu. hardware.input's read_key() runs handle() for
these keys from whichever screen is waiting, so they work everywhere.
Call init(state) once at startup.
"""

import logging
import time

from hardware.lcd import lcd
from hardware import ir_transmitter
from hardware.input import (
    KEY_DEVICE_1_UP, KEY_DEVICE_1_DOWN, KEY_DEVICE_2_UP, KEY_DEVICE_2_DOWN,
    KEY_DEVICE_3_UP, KEY_DEVICE_3_DOWN, KEY_DEVICE_4_UP, KEY_DEVICE_4_DOWN,
    KEY_PRESET_1, KEY_PRESET_2, KEY_PRESET_3, KEY_PRESET_4,
    KEY_DEVICE_PREV, KEY_DEVICE_NEXT, KEY_MENU, KEY_QUIT,
    DeviceCycledAway, is_key_held, press_count,
)
from services import (
    device_slot_service, preset_service, signal_service, config_service, home_service,
)
from app.constants import ASYNC_SEND_FLASH_SECONDS, STATEMENT_LIGHT_OFF
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

# True while a Menu screen is up (_handle_menu() and everything it calls).
_in_menu = False


log = logging.getLogger(__name__)


def init(state: AppState) -> None:
    """Give this module access to AppState. Call once from main.py."""
    global _state
    _state = state


def handle(key: str) -> bool:
    """Run the quick action for *key*. Returns True when read_key() should
    return None to its caller at once, so the interrupted screen redraws
    over what the action drew (and Active Device Control notices a device
    switch straight away). Menu returns True when closed with Left, or when
    a quit stopped the app."""
    if key in _DEVICE_KEYS:
        _handle_device(key)
        return True
    elif key in _PRESET_KEYS:
        _handle_preset(_PRESET_KEYS[key])
        return True
    elif key in _CYCLE_DIRECTIONS:
        _handle_device_cycle(key)
        return True
    elif key == KEY_MENU:
        return _handle_menu()
    return False


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
        log.warning("IR signal missing: %s", path)
        lcd.write(label, "Signal missing")
        time.sleep(1)
        return

    if slot.get("smooth"):
        _stream_device(key, label, button, path)
        return

    try:
        ir_transmitter.transmit(path)
        lcd.write(label, button.replace("_", " ").title())
    except HardwareError as e:
        log.error("IR transmit failed (%s): %s", path, e)
        lcd.write("TX Error", str(e)[:16])
    time.sleep(0.6)


def _stream_device(key: str, label: str, button: str, path) -> None:
    """A "smooth" slot: repeat the signal at the remote's own pace until the
    key is released (ir_transmitter.stream()), so e.g. receiver volume ramps
    smoothly. A tap is one frame, shown for at least ASYNC_SEND_FLASH_SECONDS."""
    lcd.write(label, button.replace("_", " ").title())
    started = time.monotonic()
    try:
        ir_transmitter.stream(path, lambda: is_key_held(key))
    except HardwareError as e:
        log.error("IR stream failed (%s): %s", path, e)
        lcd.write("TX Error", str(e)[:16])
        time.sleep(1)
        return
    lcd.wake()  # a long hold mustn't leave the backlight timed out
    time.sleep(max(0.0, ASYNC_SEND_FLASH_SECONDS - (time.monotonic() - started)))


def _still_on_screen():
    """A check for a late background failure message: True while no key has
    been pressed since this call and no Menu screen is up (then or now) —
    a Menu screen behind a quick action isn't the screen the message is
    about. Otherwise the failure is only logged."""
    pressed = press_count()
    in_menu = _in_menu
    return lambda: not in_menu and not _in_menu and press_count() == pressed


def _handle_voice_device(label: str, slot: dict, direction: str) -> None:
    command = slot.get(f"{direction}_command")
    if not command:
        lcd.write(label, "No action set")
        time.sleep(1)
        return

    # Sent in the background (an HTTP call of up to 15s); only a failure
    # shows afterwards.
    lcd.write(label, "Sending...")
    still_here = _still_on_screen()

    def _on_result(sent, total, failed_step):
        if failed_step is not None and still_here():
            lcd.write(label, f"Err: {preset_service.describe_step(failed_step)}"[:16])

    preset_service.run_steps_background([{"type": "voice", "command": command}], _on_result)
    time.sleep(ASYNC_SEND_FLASH_SECONDS)


def _handle_preset(number: int) -> None:
    """Preset Function N: run the active device's preset N, shown under its
    name ("Preset N" if it has none). There's no global default — a slot the
    device doesn't define is "Not configured"."""
    device = _state.active_device or ""
    location = _state.active_location or ""

    name, steps = preset_service.get_preset(number, device, location)
    label = (name or f"Preset {number}")[:16]

    if not steps:
        lcd.write(label, "Not configured")
        time.sleep(1)
        return

    if preset_service.all_unreachable(steps):
        lcd.write(label, STATEMENT_LIGHT_OFF)
        time.sleep(1)
        return

    # Steps run in the background (voice/Hue are network calls); only a
    # failure shows afterwards, naming the step that failed.
    lcd.write(label, "Running...")
    still_here = _still_on_screen()

    def _on_result(sent, total, failed_step):
        if failed_step is not None and still_here():
            lcd.write(label, f"Failed: {preset_service.describe_step(failed_step)}"[:16])

    preset_service.run_steps_background(steps, _on_result)
    time.sleep(ASYNC_SEND_FLASH_SECONDS)


def _handle_device_cycle(key: str) -> None:
    """< Device / Device >: switch Active Device Control's device, from any
    screen. Lights and TVs are one stop each (MENU_DEVICE_LABELS); Left/Right
    on that screen moves between their leaves."""
    direction = _CYCLE_DIRECTIONS[key]
    home = home_service.get_active_home()
    current = (_state.active_device, _state.active_location)
    if current[0] is None:
        current = signal_service.default_leaf_device(home)

    import screens.active_device_screen as active_device_screen
    device, location = active_device_screen.cycle_top_level(home, current, direction)
    _state.active_device, _state.active_location = device, location

    active_device_screen.render_after_cycle(lcd, _state)

    if _in_menu:
        # Leave the Menu on this press: unwinds past _handle_menu() to
        # Active Device Control's own read_key(), so its next read is a
        # fresh one for the new device.
        raise DeviceCycledAway()


def _handle_menu() -> bool:
    """Menu key: show the main menu and run the chosen screen. Returns True
    when Left closed the menu or a quit stopped the app (see handle())."""
    # Reset whatever key behaviour the interrupted screen set (Google TV's
    # Left/Right repeat, the sweep's tap/hold Enter), so it doesn't carry
    # into the menu.
    from hardware import input as input_hw
    input_hw.use_default_keys()
    input_hw.set_hold_keys({})

    import screens.main_menu as main_menu
    import screens.command_screen as command_screen
    import screens.home_screen as home_screen
    import screens.receive_screen as receive_screen
    import screens.rename_menu_screen as rename_menu_screen
    import screens.network_screen as network_screen
    import screens.fun_screen as fun_screen
    import screens.settings_screen as settings_screen
    from app.constants import (
        MENU_LEARN, MENU_SWEEP, MENU_RENAME, MENU_COMMAND, MENU_HOME, MENU_NETWORK,
        MENU_SETTINGS,
    )

    global _in_menu
    _in_menu = True
    try:
        while _state.running:
            choice = main_menu.show(lcd, _state)

            if choice is None:
                return _state.running
            if choice == KEY_QUIT:
                # True so read_key() returns at once and the main loop sees
                # running is off, instead of waiting for another key.
                _state.running = False
                return True

            if choice == MENU_LEARN:
                result = receive_screen.show(lcd, _state)
            elif choice == MENU_SWEEP:
                result = fun_screen.show(lcd)
            elif choice == MENU_RENAME:
                result = rename_menu_screen.show(lcd, _state)
            elif choice == MENU_COMMAND:
                result = command_screen.show(lcd)
            elif choice == MENU_HOME:
                result = home_screen.show(lcd, _state)
            elif choice == MENU_NETWORK:
                result = network_screen.show(lcd)
            elif choice == MENU_SETTINGS:
                result = settings_screen.show(lcd)
            else:
                result = None

            if result == KEY_QUIT:
                _state.running = False
                return True
        return True  # stopped while in the menu
    finally:
        # DeviceCycledAway passes through on purpose (see
        # _handle_device_cycle()).
        _in_menu = False
