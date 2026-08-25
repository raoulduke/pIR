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
    KEY_DEVICE_NEXT, KEY_DEVICE_PREV, KEY_LEARN,
)
from services import device_slot_service, preset_service, signal_service, config_service
from app.exceptions import HardwareError

_DEVICE_KEYS = {
    KEY_DEVICE_1_UP: (0, "up_button"), KEY_DEVICE_1_DOWN: (0, "down_button"),
    KEY_DEVICE_2_UP: (1, "up_button"), KEY_DEVICE_2_DOWN: (1, "down_button"),
    KEY_DEVICE_3_UP: (2, "up_button"), KEY_DEVICE_3_DOWN: (2, "down_button"),
    KEY_DEVICE_4_UP: (3, "up_button"), KEY_DEVICE_4_DOWN: (3, "down_button"),
}
_PRESET_KEYS = {KEY_PRESET_1: 1, KEY_PRESET_2: 2, KEY_PRESET_3: 3, KEY_PRESET_4: 4}


def handle(key: str) -> None:
    if key in _DEVICE_KEYS:
        _handle_device(key)
    elif key in _PRESET_KEYS:
        _handle_preset(_PRESET_KEYS[key])
    elif key == KEY_DEVICE_NEXT:
        _shift_window(1)
    elif key == KEY_DEVICE_PREV:
        _shift_window(-1)
    elif key == KEY_LEARN:
        _handle_learn()


def _handle_device(key: str) -> None:
    slot_index, action_field = _DEVICE_KEYS[key]
    offset = config_service.get_device_slot_offset()
    slot = device_slot_service.get_slot(offset, slot_index)

    if slot is None:
        lcd.write(f"Device {slot_index + 1}", "Not configured")
        time.sleep(1)
        return

    label = slot.get("label", "")[:16]
    button = slot.get(action_field)
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


def _handle_preset(number: int) -> None:
    lcd.write(f"Preset {number}", "Running...")
    sent = preset_service.run_preset(number)
    lcd.write(f"Preset {number}", f"{sent} sent" if sent else "Not configured")
    time.sleep(1)


def _shift_window(direction: int) -> None:
    count = device_slot_service.slot_count()
    if count <= 4:
        lcd.write("Devices", "No more slots")
        time.sleep(1)
        return

    offset = (config_service.get_device_slot_offset() + direction * 4) % count
    config_service.set_device_slot_offset(offset)

    labels = [d["label"] for d in device_slot_service.get_window(offset) if d]
    lcd.write("Devices", " / ".join(labels)[:16])
    time.sleep(1)


def _handle_learn() -> None:
    import screens.receive_screen as receive_screen
    receive_screen.show(lcd)
