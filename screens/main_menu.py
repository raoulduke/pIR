"""Main menu screen.

Returns one of: MENU_SEND, MENU_RECEIVE, MENU_COMMAND, MENU_HOME, KEY_QUIT,
or None (when the outer loop should keep running).
"""

from app.constants import MENU_SEND, MENU_RECEIVE, MENU_COMMAND, MENU_HOME
from app.state import AppState
from hardware.lcd import LCD
from hardware.input import read_key, KEY_UP, KEY_DOWN, KEY_ENTER, KEY_QUIT
from screens.widgets.format import format_name


_OPTIONS = [MENU_SEND, MENU_RECEIVE, MENU_COMMAND, MENU_HOME]


def show(lcd: LCD, state: AppState) -> str | None:
    index = 0

    while state.running:
        active_home = state.active_home or ""
        line2 = format_name(active_home)[:16] if _OPTIONS[index] == MENU_HOME else _OPTIONS[index]

        lcd.write("Select Mode", line2)
        key = read_key()

        if key is None:
            continue
        if key == KEY_QUIT:
            lcd.clear()
            return KEY_QUIT
        if key == KEY_UP:
            index = (index - 1) % len(_OPTIONS)
        if key == KEY_DOWN:
            index = (index + 1) % len(_OPTIONS)
        if key == KEY_ENTER:
            return _OPTIONS[index]

    return None
