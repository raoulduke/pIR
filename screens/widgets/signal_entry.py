"""Device / location / button collection shared by Learn and Rename Signal:
pick an existing device or location (or New Device/New Location to type
one), then type the button. Line 1 is the breadcrumb ("Learn > Device",
"Device > TV", "TV > Living Room")."""

import time
from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK
from services import signal_service
from screens.widgets.selector import select_from_list
from screens.widgets.char_input import read_dpad_text
from screens.widgets.format import format_name, slugify, crumbs
from app.constants import (
    STEP_DEVICE, STATEMENT_INVALID_INPUT, STATEMENT_TRY_AGAIN,
    LABEL_ENTER_LOCATION, LABEL_SINGLE_DEVICE, LABEL_NEW_DEVICE, LABEL_NEW_LOCATION,
)


def collect_device_location_button(lcd: LCD, home: str, path: list[str]) -> list[str] | str | None:
    """Back-navigable device -> location -> button collection; *path* is the
    breadcrumb so far (e.g. ["Menu", "Learn"]). Returns [device, location,
    button], KEY_QUIT, or None (backed out of the first step)."""
    values: list[str] = []
    i = 0

    while i < 3:
        trail = [*path, STEP_DEVICE, *(format_name(v) for v in values)]
        title = crumbs(*trail)
        result = None

        if i == 0:
            devices = signal_service.list_devices(home)
            if devices:
                choice = select_from_list(lcd, title, [LABEL_NEW_DEVICE] + devices)
                if choice == LABEL_NEW_DEVICE:
                    result = prompt_text(lcd, crumbs(*trail, LABEL_NEW_DEVICE))
                else:
                    result = choice  # an existing device, or KEY_QUIT/KEY_BACK — handled below either way
            else:
                result = prompt_text(lcd, crumbs(*trail, LABEL_NEW_DEVICE))

        elif i == 1:
            device = values[0]
            locations = signal_service.list_locations(home, device)
            if locations:
                choice = select_from_list(lcd, title, [LABEL_NEW_LOCATION] + locations)
                if choice == LABEL_NEW_LOCATION:
                    result = prompt_text(lcd, crumbs(*trail, LABEL_NEW_LOCATION))
                else:
                    result = choice  # an existing location, or KEY_QUIT/KEY_BACK — handled below either way
            elif signal_service.buttons_at_device_level(home, device):
                # A device with signals directly under it (e.g. "ac"): no location.
                result = ""
            else:
                choice = select_from_list(lcd, title, [LABEL_ENTER_LOCATION, LABEL_SINGLE_DEVICE])
                if choice == LABEL_SINGLE_DEVICE:
                    result = ""
                elif choice == LABEL_ENTER_LOCATION:
                    result = prompt_text(lcd, crumbs(*trail, LABEL_NEW_LOCATION))
                else:
                    result = choice  # KEY_QUIT or KEY_BACK

        elif i == 2:
            result = prompt_text(lcd, title)

        if result == KEY_QUIT:
            return KEY_QUIT
        if result == KEY_BACK:
            if i == 0:
                return None
            values.pop()
            i -= 1
            continue

        values.append(result)
        i += 1

    return values


def prompt_text(lcd: LCD, prompt: str) -> str:
    """Collect text, slugified (screens.widgets.format.slugify()) into a
    filesystem-safe snake_case name. Loops on empty input (including text
    that's *only* punctuation/whitespace, which slugify() reduces to "")."""
    while True:
        value = read_dpad_text(lcd, prompt)

        if value in (KEY_QUIT, KEY_BACK):
            return value

        value = slugify(value)
        if not value:
            lcd.write(STATEMENT_INVALID_INPUT, STATEMENT_TRY_AGAIN)
            time.sleep(1)
            continue

        return value
