"""Receive screen.

Collects device / location / button from the user, then delegates IR capture
to ``hardware.ir_receiver`` and persistence to ``services.signal_service``.

Text entry uses screens/widgets/char_input.py's D-pad-only composer, same
as network_screen.py's Wi-Fi password prompt and command_screen.py's
Command entry — the bare physical keypad has no letter keys (see that
widget's docstring), so screens/widgets/text_input.py's read_text() (raw
characters from a USB keyboard or SSH session only) just sat there
unresponsive to every physical keypress here.
"""

import time
from hardware.lcd import LCD
from hardware import ir_receiver
from hardware.input import KEY_QUIT, KEY_BACK
from services import home_service, signal_service
from services.signal_service import signal_exists, save_signal
from screens.widgets.selector import select_from_list
from screens.widgets.char_input import read_dpad_text
from screens.widgets.format import format_name, slugify
from app.constants import (
    PROMPT_DEVICE, PROMPT_LOCATION, PROMPT_BUTTON,
    STATEMENT_NO_SIGNAL, STATEMENT_RECEIVING,
    STATEMENT_FILE_EXISTS, STATEMENT_INVALID_INPUT, STATEMENT_TRY_AGAIN,
    STATEMENT_SAVED, STATEMENT_SAVE_FAILED,
    LABEL_ENTER_LOCATION, LABEL_SINGLE_DEVICE, LABEL_TRY_AGAIN, LABEL_CANCEL,
    LABEL_OVERWRITE, LABEL_NEW_DEVICE, LABEL_NEW_LOCATION, MIN_SIGNAL_LENGTH,
)
from app.exceptions import HardwareError
from app.state import AppState


def show(lcd: LCD, state: AppState) -> str | None:
    home = home_service.get_active_home()

    # --- Collect device / location / button via back-navigable steps ---
    # Device and location are picked from what already exists whenever
    # possible (see LABEL_NEW_DEVICE/LABEL_NEW_LOCATION below) — adding
    # another button to a device you've already learned signals for is
    # the common case, and re-typing "tv"/"living_room" character by
    # character on the D-pad composer every single time was needless
    # friction for something the app already knows (2026-09-1x). Button
    # is always typed — that's the one genuinely new piece of information
    # each time.
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
                    result = _prompt_text(lcd, PROMPT_DEVICE, format_name(home)[:16])
                else:
                    result = choice  # an existing device, or KEY_QUIT/KEY_BACK — handled below either way
            else:
                result = _prompt_text(lcd, PROMPT_DEVICE, format_name(home)[:16])

        elif i == 1:
            device = values[0]
            locations = signal_service.list_locations(home, device)
            if locations:
                choice = select_from_list(lcd, context, [LABEL_NEW_LOCATION] + locations)
                if choice == LABEL_NEW_LOCATION:
                    result = _prompt_text(lcd, PROMPT_LOCATION, context)
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
                    result = _prompt_text(lcd, PROMPT_LOCATION, context)
                else:
                    result = choice  # KEY_QUIT or KEY_BACK

        elif i == 2:
            result = _prompt_text(lcd, PROMPT_BUTTON, context)

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

    device, location, button = values

    lcd.write(PROMPT_BUTTON, format_name(button)[:16])
    time.sleep(0.5)

    # --- Check for existing file ---
    if signal_exists(home, device, button, location):
        answer = select_from_list(lcd, STATEMENT_FILE_EXISTS, [LABEL_OVERWRITE, LABEL_CANCEL])
        if answer in (KEY_BACK, KEY_QUIT, LABEL_CANCEL):
            return None

    # --- Capture ---
    lcd.write(STATEMENT_RECEIVING, "Press remote now")
    time.sleep(1)

    data = _capture(lcd)
    time.sleep(1)

    while not data or len(data) < MIN_SIGNAL_LENGTH:
        answer = select_from_list(lcd, STATEMENT_NO_SIGNAL, [LABEL_TRY_AGAIN, LABEL_CANCEL])
        if answer in (KEY_BACK, KEY_QUIT, LABEL_CANCEL):
            return None
        lcd.write(STATEMENT_RECEIVING, "Press remote now")
        time.sleep(1)
        data = _capture(lcd)
        time.sleep(1)

    # --- Save ---
    try:
        save_signal(data, home, device, button, location)
        lcd.write(STATEMENT_SAVED, format_name(button)[:16])
        time.sleep(2)
        # Make the just-learned device active so it can be tested right
        # away with Up/Down/Enter on Active Device Control.
        state.active_device = device
        state.active_location = location
    except Exception as e:
        lcd.write(STATEMENT_SAVE_FAILED, str(e)[:16])
        time.sleep(2)

    return None


def _capture(lcd: LCD) -> str:
    try:
        return ir_receiver.capture(lcd)
    except HardwareError as e:
        lcd.write("RX Error", str(e)[:16])
        time.sleep(2)
        return ""


def _prompt_text(lcd: LCD, prompt: str, context_line: str = "") -> str:
    """Show an optional context line then collect text, slugified
    (screens.widgets.format.slugify()) into a filesystem-safe snake_case
    name — same as home_screen.py's device/home-name entry, so typing the
    natural "Living Room" gets you the "living_room" directory name this
    becomes part of the path for, without needing to know that convention
    or type an underscore yourself. Loops on empty input (including text
    that's *only* punctuation/whitespace, which slugify() reduces to "")."""
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
