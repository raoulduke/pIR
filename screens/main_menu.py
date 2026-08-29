"""Main menu screen.

Returns one of: MENU_SEND, MENU_COMMAND, MENU_HOME, KEY_QUIT, or None (when
the outer loop should keep running). Selecting the "Quit" option also
returns KEY_QUIT. The last option, IP Address, is display-only — a fallback
for finding the device's IP when nothing else (router, HA, etc.) is
available — and Enter on it is a no-op.

Note: there's no "Receive"/Learn option here — the physical Learn button
(app/quick_actions.py) already opens screens/receive_screen.py from
anywhere, so a menu entry for it would be redundant.
"""

from app.constants import MENU_SEND, MENU_COMMAND, MENU_HOME, MENU_QUIT, MENU_IP
from app.state import AppState
from hardware.lcd import LCD
from hardware.input import read_key, KEY_UP, KEY_DOWN, KEY_ENTER, KEY_QUIT
from hardware.network import get_ip_address
from screens.widgets.format import format_name


_OPTIONS = [MENU_SEND, MENU_COMMAND, MENU_HOME, MENU_QUIT, MENU_IP]


def _line2(option: str, state: AppState) -> str:
    if option == MENU_HOME:
        return format_name(state.active_home or "")[:16]
    if option == MENU_IP:
        return get_ip_address() or "No network"
    return option


def show(lcd: LCD, state: AppState) -> str | None:
    index = 0

    while state.running:
        lcd.write("Select Mode", _line2(_OPTIONS[index], state))
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
            option = _OPTIONS[index]
            if option == MENU_QUIT:
                lcd.clear()
                return KEY_QUIT
            if option == MENU_IP:
                continue
            return option

    return None
