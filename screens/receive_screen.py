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

import logging
import time
from hardware.lcd import LCD
from hardware import ir_receiver
from hardware.input import KEY_QUIT, KEY_BACK
from services import home_service
from services.signal_service import signal_exists, save_signal
from screens.widgets.selector import select_from_list
from screens.widgets.format import format_name
from screens.widgets.signal_entry import collect_device_location_button
from app.constants import (
    PROMPT_BUTTON,
    STATEMENT_NO_SIGNAL, STATEMENT_RECEIVING,
    STATEMENT_FILE_EXISTS, STATEMENT_SAVED, STATEMENT_SAVE_FAILED,
    LABEL_TRY_AGAIN, LABEL_CANCEL, LABEL_OVERWRITE, MIN_SIGNAL_LENGTH,
)
from app.exceptions import HardwareError
from app.state import AppState

log = logging.getLogger(__name__)


def show(lcd: LCD, state: AppState) -> str | None:
    home = home_service.get_active_home()

    # --- Collect device / location / button via back-navigable steps ---
    # Device and location are picked from what already exists whenever
    # possible — adding another button to a device you've already learned
    # signals for is the common case, and re-typing "tv"/"living_room"
    # character by character on the D-pad composer every single time was
    # needless friction for something the app already knows (2026-09-1x).
    # Button is always typed — that's the one genuinely new piece of
    # information each time. Shared with screens.rename_screen, which
    # collects a mistyped signal's new identity the same way.
    values = collect_device_location_button(lcd, home)
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
        log.error("IR capture failed: %s", e)
        lcd.write("RX Error", str(e)[:16])
        time.sleep(2)
        return ""
