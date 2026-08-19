"""Home switcher screen."""

import time
from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK
from services import home_service
from screens.widgets.selector import select_from_list
from app.state import AppState


def show(lcd: LCD, state: AppState) -> str | None:
    homes = home_service.list_homes()

    if not homes:
        lcd.write("No homes found", "")
        time.sleep(2)
        return None

    choice = select_from_list(lcd, "Select Home", homes)

    if choice == KEY_QUIT:
        return KEY_QUIT
    if choice == KEY_BACK:
        return None

    home_service.set_active_home(choice)
    state.active_home = choice

    lcd.write("Active Home:", choice[:16])
    time.sleep(1.5)

    return None
