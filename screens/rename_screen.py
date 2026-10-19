"""Rename Signal (Menu > Rename > Signal): pick an existing signal from
lists, then give it a new device / location / button the same way Learn
does (screens.widgets.signal_entry), without re-capturing it."""

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
        # Make the renamed signal active (testable at once; the old identity no
        # longer exists).
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
        else:
            options = signal_service.list_buttons(home, values[0], values[1])

        if not options:
            # Nothing to pick here (e.g. a device with no locations yet): say
            # so and go back a step.
            lcd.write(STATEMENT_NO_SIGNALS, prompt[-16:])
            time.sleep(1.5)
            if i == 0:
                return None
            values.pop()
            i -= 1
            continue

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
