"""Learn: pick or type a device / location, type the button name, capture
the signal (hardware.ir_receiver) and save it (services.signal_service)."""

import logging
import subprocess
import time
from hardware.lcd import LCD
from hardware import ir_receiver
from hardware.input import KEY_QUIT, KEY_BACK
from services import home_service
from services.signal_service import signal_exists, save_signal
from screens.widgets.selector import select_from_list
from screens.widgets.format import fit_or, format_name
from screens.widgets.signal_entry import collect_device_location_button
from app.constants import (
    PROMPT_BUTTON, MENU_TITLE, MENU_LEARN,
    STATEMENT_NO_SIGNAL, STATEMENT_RECEIVING,
    STATEMENT_FILE_EXISTS, STATEMENT_SAVED, STATEMENT_SAVE_FAILED,
    LABEL_TRY_AGAIN, LABEL_CANCEL, LABEL_OVERWRITE, MIN_SIGNAL_LENGTH,
)
from app.exceptions import HardwareError
from app.state import AppState

log = logging.getLogger(__name__)


def show(lcd: LCD, state: AppState) -> str | None:
    home = home_service.get_active_home()

    # --- Collect device / location / button (back-navigable) ---
    values = collect_device_location_button(lcd, home, [MENU_TITLE, MENU_LEARN])
    if values is None:
        return None
    if values == KEY_QUIT:
        return KEY_QUIT

    device, location, button = values

    lcd.write(PROMPT_BUTTON, format_name(button)[:16])
    time.sleep(0.5)

    # --- Check for existing file ---
    if signal_exists(home, device, button, location):
        answer = select_from_list(lcd, STATEMENT_FILE_EXISTS, [LABEL_OVERWRITE, LABEL_CANCEL])
        if answer in (KEY_BACK, KEY_QUIT, LABEL_CANCEL):
            return None

    # --- Capture ---
    # capture() prompts once it's listening, so no press is lost.
    data = _capture(lcd)
    time.sleep(1)

    while not data or len(data) < MIN_SIGNAL_LENGTH:
        answer = select_from_list(lcd, STATEMENT_NO_SIGNAL, [LABEL_TRY_AGAIN, LABEL_CANCEL])
        if answer in (KEY_BACK, KEY_QUIT, LABEL_CANCEL):
            return None
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
        lcd.write(STATEMENT_SAVE_FAILED, fit_or(str(e), ""))
        time.sleep(2)

    return None


def _capture(lcd: LCD) -> str:
    try:
        return ir_receiver.capture(lcd)
    except (HardwareError, OSError, subprocess.SubprocessError) as e:
        log.error("IR capture failed: %s", e)
        lcd.write("RX Error", fit_or(str(e), ""))
        time.sleep(2)
        return ""
