"""Main menu (the Menu key). Up/Down scroll, Enter/Right select, Left closes
it; < Device/Device > also leaves it (DeviceCycledAway).

Returns MENU_LEARN, MENU_SWEEP, MENU_RENAME, MENU_COMMAND, MENU_HOME,
MENU_NETWORK, MENU_SETTINGS, KEY_QUIT (a keyboard's quit key), or None
(closed). IP Address is display-only. WiFi is disabled, showing
"WiFi loading", until NetworkManager is ready. There's no Quit entry: the
app runs as a service, and nothing on the device could start it again."""

from app.constants import (
    MENU_LEARN, MENU_SWEEP, MENU_RENAME, MENU_COMMAND, MENU_HOME, MENU_NETWORK,
    MENU_SETTINGS, MENU_IP, MENU_TITLE, MENU_WIFI_POLL_INTERVAL_SECONDS,
)
from app.state import AppState
from hardware.lcd import LCD
from hardware.input import (
    read_key, use_default_keys, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER, KEY_QUIT,
)
from hardware.network import get_ip_address
from services import wifi_service
from screens.widgets.format import with_arrows


_OPTIONS = [
    MENU_LEARN, MENU_SWEEP, MENU_RENAME, MENU_COMMAND, MENU_HOME, MENU_NETWORK, MENU_SETTINGS,
    MENU_IP,
]


def _line2(option: str) -> str:
    if option == MENU_IP:
        return get_ip_address() or "No network"  # at most 15 characters
    if option == MENU_NETWORK and not wifi_service.is_ready():
        return "WiFi loading"
    return option


def show(lcd: LCD, state: AppState) -> str | None:
    index = 0

    while state.running:
        option = _OPTIONS[index]
        use_default_keys()
        lcd.write(*with_arrows(MENU_TITLE, _line2(option), True))

        # Poll only while WiFi is highlighted, so "WiFi loading" clears on its own.
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
        if key in (KEY_ENTER, KEY_RIGHT):
            if option == MENU_IP:
                continue
            if option == MENU_NETWORK and not wifi_service.is_ready():
                continue  # disabled — WiFi isn't up yet
            return option

    return None
