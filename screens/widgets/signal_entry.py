"""Shared device / location / button collection for screens that need a
signal *identity* — screens.receive_screen (Learn, for a brand-new signal)
and screens.rename_screen (Rename Signal, for a mistyped one's new name) —
both ask for exactly the same three values the same way: existing entries
pickable from a list first (+ New Device/+ New Location to type instead),
button always typed. Extracted out of receive_screen.py once rename_screen
needed the identical flow (2026-09-19).
"""

import time
from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK
from services import signal_service
from screens.widgets.selector import select_from_list
from screens.widgets.char_input import read_dpad_text
from screens.widgets.format import format_name, slugify
from app.constants import (
    PROMPT_DEVICE, PROMPT_LOCATION, PROMPT_BUTTON,
    STATEMENT_INVALID_INPUT, STATEMENT_TRY_AGAIN,
    LABEL_ENTER_LOCATION, LABEL_SINGLE_DEVICE, LABEL_NEW_DEVICE, LABEL_NEW_LOCATION,
)


def collect_device_location_button(lcd: LCD, home: str) -> list[str] | str | None:
    """Back-navigable device -> location -> button collection. Picks from
    existing entries whenever any exist (+ New Device/+ New Location to
    type instead); button is always typed — the one genuinely new piece
    of information every time. Returns [device, location, button] on
    success, KEY_QUIT, or None (backed all the way out of step one)."""
    values: list[str] = []
    i = 0

    while i < 3:
        context = format_name(values[i - 1])[:16] if i > 0 else ""
        result = None

        if i == 0:
            devices = signal_service.list_devices(home)
            if devices:
                choice = select_from_list(lcd, PROMPT_DEVICE, [LABEL_NEW_DEVICE] + devices)
                if choice == LABEL_NEW_DEVICE:
                    result = prompt_text(lcd, PROMPT_DEVICE, format_name(home)[:16])
                else:
                    result = choice  # an existing device, or KEY_QUIT/KEY_BACK — handled below either way
            else:
                result = prompt_text(lcd, PROMPT_DEVICE, format_name(home)[:16])

        elif i == 1:
            device = values[0]
            locations = signal_service.list_locations(home, device)
            if locations:
                choice = select_from_list(lcd, context, [LABEL_NEW_LOCATION] + locations)
                if choice == LABEL_NEW_LOCATION:
                    result = prompt_text(lcd, PROMPT_LOCATION, context)
                else:
                    result = choice  # an existing location, or KEY_QUIT/KEY_BACK — handled below either way
            elif signal_service.buttons_at_device_level(home, device):
                # An existing device that already stores buttons directly
                # (no location sub-dirs, e.g. "ac") — nothing to pick and
                # nothing to type either, same as choosing Single Device
                # below for a brand-new device.
                result = ""
            else:
                choice = select_from_list(lcd, context, [LABEL_ENTER_LOCATION, LABEL_SINGLE_DEVICE])
                if choice == LABEL_SINGLE_DEVICE:
                    result = ""
                elif choice == LABEL_ENTER_LOCATION:
                    result = prompt_text(lcd, PROMPT_LOCATION, context)
                else:
                    result = choice  # KEY_QUIT or KEY_BACK

        elif i == 2:
            result = prompt_text(lcd, PROMPT_BUTTON, context)

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


def prompt_text(lcd: LCD, prompt: str, context_line: str = "") -> str:
    """Show an optional context line then collect text, slugified
    (screens.widgets.format.slugify()) into a filesystem-safe snake_case
    name. Loops on empty input (including text that's *only* punctuation/
    whitespace, which slugify() reduces to "")."""
    if context_line:
        lcd.write(prompt, context_line[:16])
        time.sleep(0.5)

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
