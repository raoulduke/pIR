"""Wi-Fi network picker (Menu > WiFi); a secured network's password is typed
with the D-pad composer, so no keyboard is ever needed.

Its own list loop rather than select_from_list, because it re-scans every
WIFI_RESCAN_INTERVAL_SECONDS while open, updating only line 1 so scrolling
isn't interrupted."""

import time
from hardware.lcd import LCD
from hardware.input import (
    read_key, use_default_keys, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER, KEY_QUIT, KEY_BACK,
)
from services import wifi_service
from screens.widgets.char_input import read_dpad_text
from screens.widgets.format import format_name, with_arrows, crumbs
from app.constants import (
    WIFI_RESCAN_INTERVAL_SECONDS, WIFI_SCAN_STOP_AFTER_UNCHANGED, MENU_TITLE, MENU_NETWORK,
)


def _sorted_ssids(by_ssid: dict) -> list[str]:
    return sorted(by_ssid, key=lambda s: by_ssid[s]["_signal"], reverse=True)


def _title(ssids: list[str]) -> str:
    """Line-1 breadcrumb — appends a running total once there's anything
    to count, e.g. "Menu > WiFi (7)"."""
    return crumbs(MENU_TITLE, f"{MENU_NETWORK} ({len(ssids)})" if ssids else MENU_NETWORK)


def _merge_scan(by_ssid: dict) -> int:
    """Run one scan, merging results into *by_ssid* — never dropping a
    previously-seen network (scans aren't perfectly exhaustive every time,
    see _pick_network's docstring). Returns how many new SSIDs this pass
    actually added, so the caller can report it."""
    before = len(by_ssid)
    for n in wifi_service.scan_networks():
        by_ssid[n["ssid"]] = n
    return len(by_ssid) - before


def _pick_network(lcd: LCD) -> dict | str | None:
    """Scan, then browse (re-scanning periodically) until a pick or Left.
    Returns {"ssid", "secure"}, KEY_BACK or KEY_QUIT. Networks accumulate
    across scans (one scan can miss a network); re-scanning stops after
    WIFI_SCAN_STOP_AFTER_UNCHANGED scans find nothing new, so a screen left up
    doesn't keep scanning."""
    lcd.write(_title([]), "Scanning...")
    by_ssid: dict = {}
    _merge_scan(by_ssid)
    ssids = _sorted_ssids(by_ssid)
    index = 0
    last_scan = time.monotonic()
    unchanged_scans = 0
    scanning = True

    while True:
        use_default_keys()
        if not ssids:
            # Right after boot NetworkManager may not be answering yet.
            lcd.write(_title(ssids), "No networks" if wifi_service.is_ready() else "Wi-Fi starting")
        else:
            lcd.write(*with_arrows(_title(ssids), format_name(ssids[index]), len(ssids) > 1))

        # Scanning has stopped: block for a key with no timeout.
        timeout = None if not scanning else max(WIFI_RESCAN_INTERVAL_SECONDS - (time.monotonic() - last_scan), 0)
        key = read_key(timeout=timeout)

        if key is None:  # rescan on timeout — only line 1 changes here.
            # Background rescans touch only line 1, so scrolling isn't interrupted.
            current = ssids[index] if ssids else None
            lcd.write_line1("Scanning...")
            added = _merge_scan(by_ssid)
            ssids = _sorted_ssids(by_ssid)
            index = ssids.index(current) if current in ssids else 0
            last_scan = time.monotonic()

            if added:
                # Pause only when something new turned up.
                unchanged_scans = 0
                lcd.write_line1(f"+{added} found ({len(ssids)})")
                time.sleep(1.5)
            else:
                unchanged_scans += 1
                if unchanged_scans >= WIFI_SCAN_STOP_AFTER_UNCHANGED:
                    scanning = False
                    lcd.write_line1("Scan paused")
                    time.sleep(1.5)
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
        elif key in (KEY_ENTER, KEY_RIGHT):
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
            password = read_dpad_text(lcd, crumbs(MENU_TITLE, MENU_NETWORK, format_name(ssid)))
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
