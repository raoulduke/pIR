"""Escape-hatch menu — reached via the physical Menu key from anywhere
(see app/quick_actions.py._handle_menu), not part of the normal boot flow
(that's screens/active_device_screen.py — see
docs/active-device-control-plan.md).

Returns one of: MENU_LEARN, MENU_COMMAND, MENU_HOME, KEY_QUIT, or None
(pressing Left backs out to Active Device Control without choosing
anything). Selecting "Quit" also returns KEY_QUIT. MENU_IP is handled
inline here rather than returned — it's display-only. MENU_NETWORK
(labeled "WiFi") is disabled — Enter is a no-op — while
wifi_service.is_ready() is false, showing "WiFi loading..." instead; see
CLAUDE.md's "Pi OS boot time" section for why this can genuinely still be
initializing the first time a human reaches this menu.

No "Full Device List" entry — removed as redundant with < Device/Device >
(app/quick_actions.py), which reaches the exact same leaf devices; see
docs/active-device-control-plan.md's "Superseded" notes.
"""

from app.constants import (
    MENU_LEARN, MENU_COMMAND, MENU_HOME, MENU_NETWORK,
    MENU_QUIT, MENU_IP, MENU_WIFI_POLL_INTERVAL_SECONDS,
)
from app.state import AppState
from hardware.lcd import LCD
from hardware.input import read_key, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_ENTER, KEY_QUIT
from hardware.network import get_ip_address
from services import wifi_service


_OPTIONS = [MENU_LEARN, MENU_COMMAND, MENU_HOME, MENU_NETWORK, MENU_QUIT, MENU_IP]


def _line2(option: str) -> str:
    if option == MENU_IP:
        return get_ip_address() or "No network"
    if option == MENU_NETWORK and not wifi_service.is_ready():
        return "WiFi loading..."
    return option


def show(lcd: LCD, state: AppState) -> str | None:
    index = 0

    while state.running:
        option = _OPTIONS[index]
        lcd.write("Menu", _line2(option))

        # Only poll while WiFi is the highlighted entry — a plain block
        # everywhere else, same as before. Without this, "WiFi loading..."
        # would never clear back to "WiFi" (and re-enable) until some
        # unrelated keypress happened to force a redraw.
        timeout = MENU_WIFI_POLL_INTERVAL_SECONDS if option == MENU_NETWORK else None
        key = read_key(timeout=timeout)

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
            if option == MENU_QUIT:
                lcd.clear()
                return KEY_QUIT
            if option == MENU_IP:
                continue
            if option == MENU_NETWORK and not wifi_service.is_ready():
                continue  # disabled — WiFi isn't up yet
            return option

    return None
