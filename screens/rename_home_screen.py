"""Rename Home screen — reached via Menu > Rename > Home, for
fixing a home's name without re-creating it and re-learning every signal
under it.

Renaming a home touches more than its signals/<home> directory —
config/settings.json's active_home (if this is the active home),
config/device_order.json's home-prefixed ordering keys, and the "home"
field on any keypad-device slot or preset step in config/keypad_devices
.json, config/keypad_presets.json, or config/step_devices.json all
reference a home by its current name. services.home_service.rename_home()
updates every one of them in one call.
"""

import time
from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK
from services import home_service
from screens.widgets.selector import select_from_list
from screens.widgets.char_input import read_dpad_text
from screens.widgets.format import format_name, slugify
from app.constants import PROMPT_HOME_NAME, STATEMENT_RENAMED, STATEMENT_SAVE_FAILED
from app.state import AppState


def show(lcd: LCD, state: AppState) -> str | None:
    homes = home_service.list_homes()
    if not homes:
        lcd.write("No homes", "to rename")
        time.sleep(1.5)
        return None

    old = select_from_list(lcd, "Rename Home:", homes)
    if old == KEY_QUIT:
        return KEY_QUIT
    if old == KEY_BACK:
        return None

    lcd.write("New name for:", format_name(old)[:16])
    time.sleep(1)

    while True:
        name = read_dpad_text(lcd, PROMPT_HOME_NAME)
        if name == KEY_QUIT:
            return KEY_QUIT
        if name == KEY_BACK:
            return None

        new = slugify(name)
        if not new or new == old:
            lcd.write("Invalid name", "")
            time.sleep(1.5)
            continue
        if new in home_service.list_homes():
            lcd.write("Already exists", format_name(new)[:16])
            time.sleep(1.5)
            continue
        break

    try:
        home_service.rename_home(old, new)
        lcd.write(STATEMENT_RENAMED, format_name(new)[:16])
        time.sleep(1.5)
        if state.active_home == old:
            state.active_home = new
            # Active Device Control re-resolves to the (renamed) home's
            # default next time it runs — same as home_screen.py's
            # _activate() after switching homes.
            state.active_device = None
            state.active_location = None
    except Exception as e:
        lcd.write(STATEMENT_SAVE_FAILED, str(e)[:16])
        time.sleep(2)

    return None
