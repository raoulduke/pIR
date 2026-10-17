"""Rename Signal screen — reached via Menu > Rename > Signal, for fixing a
device/location/button name mistyped during Learn without re-learning the
IR signal itself.

Picks the existing signal to rename from lists first (three back-navigable
steps, all picked from what's already on disk — nothing typed, since
every value here must already exist), then collects its new identity the
exact same way screens.receive_screen collects a brand-new signal's
identity (screens.widgets.signal_entry.collect_device_location_button) —
existing entries pickable, New Device/New Location to type instead,
button always typed.
"""

import time
from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK
from services import home_service, signal_service
from screens.widgets.selector import select_from_list
from screens.widgets.signal_entry import collect_device_location_button
from screens.widgets.format import format_name, crumbs
from app.constants import (
    MENU_TITLE, MENU_RENAME, RENAME_SIGNAL, STEP_DEVICE, STATEMENT_FILE_EXISTS, STATEMENT_RENAMED,
    STATEMENT_SAVE_FAILED, STATEMENT_NO_SIGNALS,
    LABEL_OVERWRITE, LABEL_CANCEL,
)
from app.state import AppState

_PATH = [MENU_TITLE, MENU_RENAME, RENAME_SIGNAL]


def show(lcd: LCD, state: AppState) -> str | None:
    home = home_service.get_active_home()

    old = _pick_existing_signal(lcd, home)
    if old is None:
        return None
    if old == KEY_QUIT:
        return KEY_QUIT
    device, location, button = old

    lcd.write("New name for:", format_name(button)[:16])
    time.sleep(1)

    picked = [format_name(v) for v in old]
    new_values = collect_device_location_button(lcd, home, [*_PATH, STEP_DEVICE, *picked])
    if new_values is None:
        return None
    if new_values == KEY_QUIT:
        return KEY_QUIT
    new_device, new_location, new_button = new_values

    if (new_device, new_location, new_button) == (device, location, button):
        return None  # nothing actually changed

    if signal_service.signal_exists(home, new_device, new_button, new_location):
        answer = select_from_list(lcd, STATEMENT_FILE_EXISTS, [LABEL_OVERWRITE, LABEL_CANCEL])
        if answer in (KEY_BACK, KEY_QUIT, LABEL_CANCEL):
            return None

    try:
        signal_service.rename_signal(home, device, button, location, new_device, new_button, new_location)
        lcd.write(STATEMENT_RENAMED, format_name(new_button)[:16])
        time.sleep(2)
        # Make the renamed signal active so it can be tested right away —
        # also fixes up state.active_device/location if they pointed at
        # the old identity, which no longer exists on disk.
        state.active_device = new_device
        state.active_location = new_location
    except Exception as e:
        lcd.write(STATEMENT_SAVE_FAILED, str(e)[:16])
        time.sleep(2)

    return None


def _pick_existing_signal(lcd: LCD, home: str) -> list[str] | str | None:
    """Back-navigable device -> location -> button selection, picking only
    from what already exists — the signal being renamed must already be
    on disk, so there's nothing to type here. Returns [device, location,
    button], KEY_QUIT, or None (backed out of step one)."""
    if not signal_service.list_devices(home):
        lcd.write(STATEMENT_NO_SIGNALS, format_name(home)[:16])
        time.sleep(1.5)
        return None

    values: list[str] = []
    i = 0

    while i < 3:
        prompt = crumbs(*_PATH, STEP_DEVICE, *(format_name(v) for v in values))

        if i == 0:
            options = signal_service.list_devices(home)
        elif i == 1:
            options = signal_service.list_locations(home, values[0])
            if not options:
                # No location sub-dirs — buttons live directly under the
                # device (e.g. "ac") — nothing to pick.
                values.append("")
                i += 1
                continue
        else:
            options = signal_service.list_buttons(home, values[0], values[1])

        if not options:
            # Shouldn't happen — a listed device/location always has
            # something under it — but bail cleanly rather than crashing
            # select_from_list() on an empty list.
            return None

        choice = select_from_list(lcd, prompt, options)
        if choice == KEY_QUIT:
            return KEY_QUIT
        if choice == KEY_BACK:
            if i == 0:
                return None
            values.pop()
            i -= 1
            continue

        values.append(choice)
        i += 1

    return values
