"""Rename Device screen — reached via Menu > Rename > Device, for fixing a
device's name in the active home without re-learning every signal under
it.

Like a home, a device is referenced beyond its signals/<home>/<device>
directory — config/device_order.json, the "device" field on keypad-device
slots and preset steps, and the "device"/"device/location" keys of
keypad_presets.json's "by_device" overrides and step_devices.json.
services.home_service.rename_device() updates every one of them in one
call.

DEFAULT_ACTIVE_DEVICE and MENU_DEVICE_LABELS' devices can't be renamed
here — their special Active Device Control behavior (D-pad passthrough,
grouped < Device/Device > stop) is keyed by name in app/constants.py, so a
rename would silently lose it.
"""

import time
from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK
from services import home_service, signal_service
from screens.widgets.selector import select_from_list
from screens.widgets.char_input import read_dpad_text
from screens.widgets.format import format_name, slugify
from app.constants import (
    PROMPT_DEVICE, STATEMENT_RENAMED, STATEMENT_SAVE_FAILED,
    DEFAULT_ACTIVE_DEVICE, MENU_DEVICE_LABELS,
)
from app.state import AppState

_BUILT_IN_DEVICES = {DEFAULT_ACTIVE_DEVICE, *MENU_DEVICE_LABELS}


def show(lcd: LCD, state: AppState) -> str | None:
    home = home_service.get_active_home()

    while True:
        devices = signal_service.list_devices(home)
        if not devices:
            lcd.write("No devices", format_name(home)[:16])
            time.sleep(1.5)
            return None

        old = select_from_list(lcd, "Rename Device:", devices)
        if old == KEY_QUIT:
            return KEY_QUIT
        if old == KEY_BACK:
            return None
        if old in _BUILT_IN_DEVICES:
            lcd.write("Built-in device", "can't rename")
            time.sleep(1.5)
            continue
        break

    lcd.write("New name for:", format_name(old)[:16])
    time.sleep(1)

    while True:
        name = read_dpad_text(lcd, PROMPT_DEVICE)
        if name == KEY_QUIT:
            return KEY_QUIT
        if name == KEY_BACK:
            return None

        new = slugify(name)
        if not new or new == old:
            lcd.write("Invalid name", "")
            time.sleep(1.5)
            continue
        if new in signal_service.list_devices(home):
            lcd.write("Already exists", format_name(new)[:16])
            time.sleep(1.5)
            continue
        break

    try:
        home_service.rename_device(home, old, new)
        lcd.write(STATEMENT_RENAMED, format_name(new)[:16])
        time.sleep(1.5)
        if state.active_home == home and state.active_device == old:
            state.active_device = new
    except Exception as e:
        lcd.write(STATEMENT_SAVE_FAILED, str(e)[:16])
        time.sleep(2)

    return None
