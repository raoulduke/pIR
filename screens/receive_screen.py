"""Receive screen.

Collects device / location / button from the user, then delegates IR capture
to ``hardware.ir_receiver`` and persistence to ``services.signal_service``.
"""

import time
from hardware.lcd import LCD
from hardware import ir_receiver
from hardware.input import KEY_QUIT, KEY_BACK
from services import home_service
from services.signal_service import signal_exists, save_signal
from screens.widgets.selector import select_from_list
from screens.widgets.text_input import read_text
from screens.widgets.format import format_name
from app.constants import (
    PROMPT_DEVICE, PROMPT_LOCATION, PROMPT_BUTTON,
    STATEMENT_NO_SIGNAL, STATEMENT_RECEIVING,
    STATEMENT_FILE_EXISTS, STATEMENT_INVALID_INPUT, STATEMENT_TRY_AGAIN,
    STATEMENT_SAVED, STATEMENT_SAVE_FAILED,
    LABEL_ENTER_LOCATION, LABEL_SINGLE_DEVICE, LABEL_TRY_AGAIN, LABEL_CANCEL,
    LABEL_OVERWRITE, MIN_SIGNAL_LENGTH,
)
from app.exceptions import HardwareError
from app.state import AppState


def show(lcd: LCD, state: AppState) -> str | None:
    home = home_service.get_active_home()

    # --- Collect device / location / button via back-navigable steps ---
    values: list[str] = []
    i = 0

    while i < 3:
        context = format_name(values[i - 1])[:16] if i > 0 else ""
        result = None

        if i == 0:
            result = _prompt_text(lcd, PROMPT_DEVICE, format_name(home)[:16])

        elif i == 1:
            choice = select_from_list(lcd, context, [LABEL_ENTER_LOCATION, LABEL_SINGLE_DEVICE])
            if choice == KEY_QUIT:
                return KEY_QUIT
            if choice == KEY_BACK:
                values.pop()
                i -= 1
                continue
            if choice == LABEL_SINGLE_DEVICE:
                values.append("")
                i += 1
                continue
            result = _prompt_text(lcd, PROMPT_LOCATION, context)

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
    """Show an optional context line then collect text.  Loops on empty input."""
    if context_line:
        lcd.write(prompt, context_line[:16])
        time.sleep(0.5)

    while True:
        value = read_text(lcd, prompt)

        if value in (KEY_QUIT, KEY_BACK):
            return value

        if not value:
            lcd.write(STATEMENT_INVALID_INPUT, STATEMENT_TRY_AGAIN)
            time.sleep(1)
            continue

        return value
