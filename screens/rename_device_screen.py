"""Rename Device (Menu > Rename > Device): renames a device in the active
home everywhere it's referenced (services.home_service.rename_device()).
Google TV and the grouped devices (light, tv) can't be renamed: their
behaviour is keyed by name in app/constants.py."""

import time
from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK
from services import home_service, signal_service
from screens.widgets.selector import select_from_list
from screens.widgets.char_input import read_dpad_text
from screens.widgets.format import format_name, slugify, crumbs
from app.constants import (
    MENU_TITLE, MENU_RENAME, RENAME_DEVICE, STATEMENT_RENAMED, STATEMENT_SAVE_FAILED,
    DEFAULT_ACTIVE_DEVICE, MENU_DEVICE_LABELS,
)
from app.state import AppState

_PATH = [MENU_TITLE, MENU_RENAME, RENAME_DEVICE]
_BUILT_IN_DEVICES = {DEFAULT_ACTIVE_DEVICE, *MENU_DEVICE_LABELS}


def show(lcd: LCD, state: AppState) -> str | None:
    home = home_service.get_active_home()

    while True:
        devices = signal_service.list_devices(home)
        if not devices:
            lcd.write("No devices", format_name(home)[:16])
            time.sleep(1.5)
            return None

        old = select_from_list(lcd, crumbs(*_PATH), devices)
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
        name = read_dpad_text(lcd, crumbs(*_PATH, format_name(old)))
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
