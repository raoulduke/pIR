"""Home switcher screen — also where a new Home gets created."""

import time
from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK
from services import home_service
from screens.widgets.selector import select_from_list
from screens.widgets.char_input import read_dpad_text
from screens.widgets.format import format_name, slugify, crumbs
from app.constants import MENU_TITLE, MENU_HOME, LABEL_NEW_HOME
from app.state import AppState


def show(lcd: LCD, state: AppState) -> str | None:
    while True:
        options = [LABEL_NEW_HOME] + home_service.list_homes()

        choice = select_from_list(lcd, crumbs(MENU_TITLE, MENU_HOME), options)

        if choice == KEY_QUIT:
            return KEY_QUIT
        if choice == KEY_BACK:
            return None

        if choice == LABEL_NEW_HOME:
            result = _create_home(lcd, state)
            if result == KEY_QUIT:
                return KEY_QUIT
            continue  # back to the (now updated) home list either way

        _activate(state, choice)
        lcd.write("Active Home:", format_name(choice)[:16])
        time.sleep(1.5)
        return None


def _create_home(lcd: LCD, state: AppState) -> str | None:
    """Prompt for a new home's name, create it, and make it active.
    Returns KEY_QUIT, or None to fall back to the home list (cancelled,
    rejected, or created — the caller re-lists either way)."""
    name = read_dpad_text(lcd, crumbs(MENU_TITLE, MENU_HOME, LABEL_NEW_HOME))
    if name == KEY_QUIT:
        return KEY_QUIT
    if name == KEY_BACK:
        return None

    slug = slugify(name)
    if not slug:
        lcd.write("Invalid name", "")
        time.sleep(1.5)
        return None
    if slug in home_service.list_homes():
        lcd.write("Already exists", format_name(slug)[:16])
        time.sleep(1.5)
        return None

    home_service.create_home(slug)
    _activate(state, slug)
    lcd.write("Home created:", format_name(slug)[:16])
    time.sleep(1.5)
    return None


def _activate(state: AppState, home: str) -> None:
    home_service.set_active_home(home)
    state.active_home = home

    # Active Device Control picks the new home's default device next.
    state.active_device = None
    state.active_location = None
