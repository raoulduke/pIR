"""Wi-Fi network picker — reached via Menu > Network.

Scan and pick an SSID work from the bare physical keypad already (Up/Down/
Enter/Left, same as the rest of the app). Password entry, when the chosen
network is secured, uses screens/widgets/char_input.py's D-pad-only
composer so the whole flow needs no USB keyboard or SSH session, even at a
brand-new location.

The list re-scans on its own every WIFI_RESCAN_INTERVAL_SECONDS while
you're sitting on it (not just once on entry) — a custom loop instead of
screens/widgets/selector.py's select_from_list, since that widget blocks
on read_key() with no timeout and is shared by every other screen; adding
timeout support to read_key() (hardware/input.py) was the safer, additive
change, done once there instead of forking behavior per-caller.
"""

import time
from hardware.lcd import LCD
from hardware.input import read_key, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_ENTER, KEY_QUIT, KEY_BACK
from services import wifi_service
from screens.widgets.char_input import read_dpad_text
from screens.widgets.format import format_name
from app.constants import WIFI_RESCAN_INTERVAL_SECONDS


def _sorted_ssids(by_ssid: dict) -> list[str]:
    return sorted(by_ssid, key=lambda s: by_ssid[s]["_signal"], reverse=True)


def _pick_network(lcd: LCD) -> dict | str | None:
    """Scan, then let the user browse (re-scanning periodically) until
    they pick a network or back out. Returns a {"ssid", "secure"} dict,
    KEY_BACK, or KEY_QUIT.

    Networks accumulate across rescans for the life of this call rather
    than being replaced each time — a network that drops out of one scan
    sweep (scans aren't perfectly exhaustive every time) stays selectable
    instead of vanishing, as long as you're still sitting on this screen.
    """
    lcd.write("Wi-Fi", "Scanning...")
    by_ssid: dict = {}
    for n in wifi_service.scan_networks():
        by_ssid[n["ssid"]] = n
    ssids = _sorted_ssids(by_ssid)
    index = 0
    last_scan = time.monotonic()

    while True:
        if not ssids:
            lcd.write("Wi-Fi", "No networks")
        else:
            lcd.write("Wi-Fi", format_name(ssids[index])[:16])

        remaining = WIFI_RESCAN_INTERVAL_SECONDS - (time.monotonic() - last_scan)
        key = read_key(timeout=max(remaining, 0))

        if key is None:  # rescan on timeout
            current = ssids[index] if ssids else None
            lcd.write("Wi-Fi", "Scanning...")
            for n in wifi_service.scan_networks():
                by_ssid[n["ssid"]] = n  # merge — never drop a previously-seen network here
            ssids = _sorted_ssids(by_ssid)
            index = ssids.index(current) if current in ssids else 0
            last_scan = time.monotonic()
            continue

        if key == KEY_QUIT:
            return KEY_QUIT
        if key == KEY_LEFT:
            return KEY_BACK
        if not ssids:
            continue  # nothing to select yet
        if key == KEY_UP:
            index = (index - 1) % len(ssids)
        elif key == KEY_DOWN:
            index = (index + 1) % len(ssids)
        elif key == KEY_ENTER:
            return by_ssid[ssids[index]]


def show(lcd: LCD) -> str | None:
    while True:
        network = _pick_network(lcd)

        if network == KEY_QUIT:
            return KEY_QUIT
        if network == KEY_BACK:
            return None

        ssid = network["ssid"]
        password = ""
        if network["secure"]:
            password = read_dpad_text(lcd, "Password:")
            if password == KEY_QUIT:
                return KEY_QUIT
            if password == KEY_BACK:
                continue  # back to the network list, not out of the screen

        lcd.write(ssid[:16], "Connecting...")
        success, message = wifi_service.connect(ssid, password)
        lcd.write(ssid[:16], message[:16])
        time.sleep(2)

        if success:
            return None
        # Failed — loop back to the network list rather than dropping to
        # the outer Menu, so a mistyped password is a quick retry.
