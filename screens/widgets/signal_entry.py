"""Device / location / button collection shared by Learn and Rename Signal:
pick an existing device or location (or New Device/New Location to type
one), then type the button. Line 1 is the breadcrumb ("Learn > Device",
"Device > TV", "TV > Living Room")."""

import time
from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK
from services import signal_service, step_device_service
from screens.widgets.selector import select_from_list
from screens.widgets.char_input import read_dpad_text
from screens.widgets.format import format_name, slugify, crumbs
from app.constants import (
    STEP_DEVICE, STATEMENT_INVALID_INPUT, STATEMENT_TRY_AGAIN,
    LABEL_NEW_DEVICE, LABEL_NEW_LOCATION,
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
        typed = False  # a new name typed, rather than picked from a list

        if i == 0:
            devices = [d for d in signal_service.list_devices(home) if _ir_locations(home, d) is not None]
            if devices:
                choice = select_from_list(lcd, title, [LABEL_NEW_DEVICE] + devices)
                if choice == LABEL_NEW_DEVICE:
                    result, typed = prompt_text(lcd, crumbs(*trail, LABEL_NEW_DEVICE)), True
                else:
                    result = choice  # an existing device, or KEY_QUIT/KEY_BACK — handled below either way
            else:
                result, typed = prompt_text(lcd, crumbs(*trail, LABEL_NEW_DEVICE)), True

        elif i == 1:
            device = values[0]
            locations = _ir_locations(home, device) or []
            if locations:
                choice = select_from_list(lcd, title, [LABEL_NEW_LOCATION] + locations)
                if choice == LABEL_NEW_LOCATION:
                    result, typed = prompt_text(lcd, crumbs(*trail, LABEL_NEW_LOCATION)), True
                else:
                    result = choice  # an existing location, or KEY_QUIT/KEY_BACK — handled below either way
            else:
                # Every device has a location: a new device gets its first one here.
                result, typed = prompt_text(lcd, crumbs(*trail, LABEL_NEW_LOCATION)), True

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

        if typed and (_ir_locations(home, result) is None if i == 0
                      else i == 1 and not _takes_ir(home, values[0], result)):
            # A step device: its buttons come from steps, not IR signals.
            lcd.write("Step device:", "no IR signals")
            time.sleep(1.5)
            continue

        if typed and result in (signal_service.list_devices(home) if i == 0
                                else signal_service.list_locations(home, values[0])):
            # New means new: an existing one is picked from the list.
            lcd.write("Already exists", format_name(result)[:16])
            time.sleep(1.5)
            continue

        values.append(result)
        i += 1

    return values


def _takes_ir(home: str, device: str, location: str) -> bool:
    """Whether signals learned into this location get used: any IR device,
    or a step device whose steps send IR from its own location (Hall). Other
    step devices (Hue, voice) run their steps and would ignore a signal."""
    return (not step_device_service.is_step_device(device, location)
            or step_device_service.sends_own_ir(home, device, location))


def _ir_locations(home: str, device: str) -> list[str] | None:
    """*device*'s locations that take IR signals (_takes_ir()). None if it
    has locations but none take IR; [] if it has none yet."""
    locations = signal_service.list_locations(home, device)
    ir = [loc for loc in locations if _takes_ir(home, device, loc)]
    return None if locations and not ir else ir


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
