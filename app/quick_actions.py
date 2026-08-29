"""Global keypad quick actions.

Wired into hardware.input via set_quick_action_handler at startup (see
main.py), so device/preset/nav shortcuts on the physical keypad fire from
anywhere in the app — read_key() intercepts them before returning to
whichever screen is currently blocked waiting for a key.

Known limitation: KEY_LEARN drops straight into receive_screen from inside
read_key(). If the user quits from there (KEY_QUIT), that signal is
swallowed rather than propagated up to main.py's loop — acceptable for now
since the shutdown button remains the primary exit path.
"""

import time

from hardware.lcd import lcd
from hardware import ir_transmitter
from hardware.input import (
    KEY_DEVICE_1_UP, KEY_DEVICE_1_DOWN, KEY_DEVICE_2_UP, KEY_DEVICE_2_DOWN,
    KEY_DEVICE_3_UP, KEY_DEVICE_3_DOWN, KEY_DEVICE_4_UP, KEY_DEVICE_4_DOWN,
    KEY_PRESET_1, KEY_PRESET_2, KEY_PRESET_3, KEY_PRESET_4,
    KEY_LEARN, KEY_TV_MUTE,
)
from services import (
    device_slot_service, preset_service, signal_service, config_service, voice_service, home_service,
)
from app.exceptions import HardwareError

_DEVICE_KEYS = {
    KEY_DEVICE_1_UP: (0, "up"), KEY_DEVICE_1_DOWN: (0, "down"),
    KEY_DEVICE_2_UP: (1, "up"), KEY_DEVICE_2_DOWN: (1, "down"),
    KEY_DEVICE_3_UP: (2, "up"), KEY_DEVICE_3_DOWN: (2, "down"),
    KEY_DEVICE_4_UP: (3, "up"), KEY_DEVICE_4_DOWN: (3, "down"),
}
_PRESET_KEYS = {KEY_PRESET_1: 1, KEY_PRESET_2: 2, KEY_PRESET_3: 3, KEY_PRESET_4: 4}

# "Device >" — mute for the Google TV remote. Not a device-slot entry since
# it's a single fixed button, not a configurable up/down pair.
_TV_MUTE_DEVICE = "google_tv"
_TV_MUTE_BUTTON = "mute"


def handle(key: str) -> None:
    if key in _DEVICE_KEYS:
        _handle_device(key)
    elif key in _PRESET_KEYS:
        _handle_preset(_PRESET_KEYS[key])
    elif key == KEY_TV_MUTE:
        _handle_tv_mute()
    elif key == KEY_LEARN:
        _handle_learn()


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


def _handle_tv_mute() -> None:
    home = home_service.get_active_home()
    path = signal_service.signal_path(home, _TV_MUTE_DEVICE, _TV_MUTE_BUTTON)

    if not path.exists():
        lcd.write("TV Mute", "Signal missing")
        time.sleep(1)
        return

    try:
        ir_transmitter.transmit(path)
        lcd.write("TV", "Mute")
    except HardwareError as e:
        lcd.write("TX Error", str(e)[:16])
    time.sleep(0.6)


def _handle_preset(number: int) -> None:
    lcd.write(f"Preset {number}", "Running...")
    sent = preset_service.run_preset(number)
    lcd.write(f"Preset {number}", f"{sent} sent" if sent else "Not configured")
    time.sleep(1)


def _handle_learn() -> None:
    import screens.receive_screen as receive_screen
    receive_screen.show(lcd)
