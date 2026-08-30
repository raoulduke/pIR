"""Escape-hatch menu — reached via the physical Menu key from anywhere
(see app/quick_actions.py._handle_menu), not part of the normal boot flow
(that's screens/active_device_screen.py — see
docs/active-device-control-plan.md).

Returns one of: MENU_DEVICES, MENU_LEARN, MENU_COMMAND, MENU_HOME,
KEY_QUIT, or None (pressing Left backs out to Active Device Control
without choosing anything). Selecting "Quit" also returns KEY_QUIT.
MENU_SETTINGS and MENU_IP are handled inline here rather than returned —
Settings has no real screen yet (placeholder message), and IP is
display-only.
"""

import time
from app.constants import (
    MENU_DEVICES, MENU_LEARN, MENU_COMMAND, MENU_HOME, MENU_SETTINGS,
    MENU_QUIT, MENU_IP,
)
from app.state import AppState
from hardware.lcd import LCD
from hardware.input import read_key, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_ENTER, KEY_QUIT
from hardware.network import get_ip_address
from screens.widgets.format import format_name


_OPTIONS = [MENU_DEVICES, MENU_LEARN, MENU_COMMAND, MENU_HOME, MENU_SETTINGS, MENU_QUIT, MENU_IP]


def _line2(option: str, state: AppState) -> str:
    if option == MENU_HOME:
        return format_name(state.active_home or "")[:16]
    if option == MENU_IP:
        return get_ip_address() or "No network"
    return option


def show(lcd: LCD, state: AppState) -> str | None:
    index = 0

    while state.running:
        lcd.write("Menu", _line2(_OPTIONS[index], state))
        key = read_key()

        if key is None:
            continue
        if key == KEY_QUIT:
            lcd.clear()
            return KEY_QUIT
        if key == KEY_LEFT:
            return None
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
            if option == MENU_SETTINGS:
                lcd.write("Settings", "Coming soon")
                time.sleep(1)
                continue
            return option

    return None
