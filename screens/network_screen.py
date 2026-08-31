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

A background rescan only ever touches line 1 (hardware/lcd.py's
write_line1()) — briefly "Scanning...", then "+N found (total)" if it
actually found something new — so cycling through the list on line 2 is
never interrupted by one, even mid-scan. Rescanning gives up for good
after WIFI_SCAN_STOP_AFTER_UNCHANGED consecutive scans in a row find
nothing new, so leaving this screen up unattended doesn't keep spending
battery on nmcli rescans forever.
"""

import time
from hardware.lcd import LCD
from hardware.input import read_key, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_ENTER, KEY_QUIT, KEY_BACK
from services import wifi_service
from screens.widgets.char_input import read_dpad_text
from screens.widgets.format import format_name
from app.constants import WIFI_RESCAN_INTERVAL_SECONDS, WIFI_SCAN_STOP_AFTER_UNCHANGED


def _sorted_ssids(by_ssid: dict) -> list[str]:
    return sorted(by_ssid, key=lambda s: by_ssid[s]["_signal"], reverse=True)


def _title(ssids: list[str]) -> str:
    """Line-1 title — appends a running total once there's anything to
    count, e.g. "Wi-Fi (7)"."""
    return f"Wi-Fi ({len(ssids)})" if ssids else "Wi-Fi"


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
    """Scan, then let the user browse (re-scanning periodically) until
    they pick a network or back out. Returns a {"ssid", "secure"} dict,
    KEY_BACK, or KEY_QUIT.

    Networks accumulate across rescans for the life of this call rather
    than being replaced each time — a network that drops out of one scan
    sweep (scans aren't perfectly exhaustive every time) stays selectable
    instead of vanishing, as long as you're still sitting on this screen.

    Auto-rescanning stops for good after WIFI_SCAN_STOP_AFTER_UNCHANGED
    consecutive scans add nothing new — someone walking away and leaving
    this screen up (nmcli rescans aren't free) shouldn't quietly drain the
    battery forever. Backing out and back in starts fresh.
    """
    lcd.write("Wi-Fi", "Scanning...")
    by_ssid: dict = {}
    _merge_scan(by_ssid)
    ssids = _sorted_ssids(by_ssid)
    index = 0
    last_scan = time.monotonic()
    unchanged_scans = 0
    scanning = True

    while True:
        if not ssids:
            # Distinguish "genuinely nothing in range" from "NetworkManager
            # itself isn't answering yet" (see wifi_service.is_ready()) —
            # a real possibility right after boot, not just a theoretical
            # one, since this app deliberately starts before NetworkManager
            # is guaranteed to be up.
            lcd.write(_title(ssids), "No networks" if wifi_service.is_ready() else "Wi-Fi starting")
        else:
            lcd.write(_title(ssids), format_name(ssids[index])[:16])

        # No more timeout once scanning has given up — just block for a
        # real key, same as any other screen, instead of waking up every
        # WIFI_RESCAN_INTERVAL_SECONDS forever with nothing left to do.
        timeout = None if not scanning else max(WIFI_RESCAN_INTERVAL_SECONDS - (time.monotonic() - last_scan), 0)
        key = read_key(timeout=timeout)

        if key is None:  # rescan on timeout — only line 1 changes here.
            # Whatever's on line 2 (the network currently highlighted)
            # keeps showing the whole time a background rescan runs,
            # instead of being blanked by a full "Scanning..." screen —
            # cycling through the list is never interrupted by one.
            current = ssids[index] if ssids else None
            lcd.write_line1("Scanning...")
            added = _merge_scan(by_ssid)
            ssids = _sorted_ssids(by_ssid)
            index = ssids.index(current) if current in ssids else 0
            last_scan = time.monotonic()

            if added:
                # Still only line 1 — worth a moment's pause since this is
                # actual news (found something new), unlike the common
                # case (nothing new) which reports nothing and goes
                # straight back to browsing.
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
