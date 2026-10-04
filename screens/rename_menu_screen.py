"""Rename submenu — reached via Menu > Rename. Asks "Rename what?" and
hands off to the Home/Device/Signal rename screen picked with Up/Down.
Backing out of a rename screen returns here; Left here returns to the
main Menu.
"""

from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK
from screens.widgets.selector import select_from_list
import screens.rename_home_screen as rename_home_screen
import screens.rename_device_screen as rename_device_screen
import screens.rename_screen as rename_screen
from app.constants import RENAME_HOME, RENAME_DEVICE, RENAME_SIGNAL
from app.state import AppState

_SCREENS = {
    RENAME_HOME: rename_home_screen,
    RENAME_DEVICE: rename_device_screen,
    RENAME_SIGNAL: rename_screen,
}


def show(lcd: LCD, state: AppState) -> str | None:
    while state.running:
        choice = select_from_list(lcd, "Rename what?", list(_SCREENS))
        if choice == KEY_QUIT:
            return KEY_QUIT
        if choice == KEY_BACK:
            return None
        if _SCREENS[choice].show(lcd, state) == KEY_QUIT:
            return KEY_QUIT
    return None
