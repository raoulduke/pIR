"""Fun menu sweep — steps through every library code for one brand's signal
(TV-B-Gone style, but one brand at a time and slow enough to see which code
worked), reached from screens/fun_screen.py.

Running: one code every SWEEP_GAP_SECONDS, line 1 showing the code just
sent. Enter pauses on it, Left exits.

Paused ("||" in front of the code):
  Up / Down  send the previous / next code and stay paused on it (no
             wrapping — they stop at the first / last code)
  Enter      save the code under a typed name (fun_service.save())
  Right      resume the sweep from the next code
  Left       exit
Running off the end pauses on the last code, so Up can still step back.

Runs on its own loop with ir_transmitter called directly, not
preset_service's background worker — a pause has to land between two
codes, not after whatever's already queued.
"""

import logging
import time

from hardware.lcd import LCD
from hardware import ir_transmitter
from hardware.input import (
    read_key, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER, KEY_QUIT, KEY_BACK,
)
from services import fun_service, ir_encode
from screens.widgets.selector import select_from_list
from screens.widgets.signal_entry import prompt_text
from screens.widgets.format import format_name
from app.constants import (
    SWEEP_GAP_SECONDS, PROMPT_NAME, STATEMENT_NO_SIGNALS,
    STATEMENT_FILE_EXISTS, STATEMENT_SAVED, STATEMENT_SAVE_FAILED,
    LABEL_OVERWRITE, LABEL_CANCEL,
)
from app.exceptions import HardwareError

log = logging.getLogger(__name__)


def show(lcd: LCD, device_type: str, brand: str, signal: str) -> str | None:
    """Returns KEY_QUIT, or None when Left exits the sweep."""
    codes = fun_service.get_codes(device_type, brand, signal)
    if not codes:
        lcd.write(brand[:16], STATEMENT_NO_SIGNALS)
        time.sleep(1)
        return None

    last = len(codes) - 1
    index = 0
    paused = not _send(lcd, brand, codes, index)
    if not paused:
        _draw_running(lcd, codes, index)

    while True:
        if paused:
            _draw_paused(lcd, codes, index, last)
            key = read_key()
            if key == KEY_QUIT:
                return KEY_QUIT
            if key == KEY_LEFT:
                return None
            if key == KEY_UP and index > 0:
                index -= 1
                _send(lcd, brand, codes, index)
            elif key == KEY_DOWN and index < last:
                index += 1
                _send(lcd, brand, codes, index)
            elif key == KEY_ENTER:
                if _save(lcd, device_type, brand, signal, codes[index], index + 1) == KEY_QUIT:
                    return KEY_QUIT
            elif key == KEY_RIGHT and index < last:
                index += 1
                paused = not _send(lcd, brand, codes, index)
                if not paused:
                    _draw_running(lcd, codes, index)
            continue

        key = _wait(lcd, codes, index)
        if key == KEY_QUIT:
            return KEY_QUIT
        if key == KEY_LEFT:
            return None
        if key == KEY_ENTER or index == last:
            paused = True
            continue
        index += 1
        paused = not _send(lcd, brand, codes, index)
        if not paused:
            _draw_running(lcd, codes, index)


def _wait(lcd: LCD, codes: list[dict], index: int) -> str | None:
    """Wait out the gap after a code. Returns Enter/Left/Quit as soon as
    pressed, or None once the full gap has passed. Other keys are ignored
    without cutting the gap short."""
    deadline = time.monotonic() + SWEEP_GAP_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        key = read_key(timeout=remaining)
        if key in (KEY_ENTER, KEY_LEFT, KEY_QUIT):
            return key
        if key is None and time.monotonic() < deadline:
            # A quick action (e.g. a Preset key) drew over the screen.
            _draw_running(lcd, codes, index)


def _draw_running(lcd: LCD, codes: list[dict], index: int) -> None:
    lcd.write(ir_encode.label(codes[index], index + 1), "Enter=Pause")


def _draw_paused(lcd: LCD, codes: list[dict], index: int, last: int) -> None:
    line1 = "||" + ir_encode.label(codes[index], index + 1, compact=True)
    lcd.write(line1[:16], "Done Enter=Save" if index == last else "Enter=Save")


def _send(lcd: LCD, brand: str, codes: list[dict], index: int) -> bool:
    """Transmit codes[index]. On failure, logs it, shows the error and
    returns False — the caller pauses rather than sweeping on through
    codes that aren't actually going out."""
    entry = codes[index]
    try:
        ir_transmitter.transmit_pulses(*ir_encode.encode(entry))
        return True
    except (HardwareError, ValueError) as e:
        log.error("Fun sweep transmit failed (%s #%d %s): %s", brand, index + 1, entry, e)
        lcd.write("TX Error", str(e)[:16])
        time.sleep(1.5)
        return False


def _save(lcd: LCD, device_type: str, brand: str, signal: str,
          entry: dict, number: int) -> str | None:
    """Name and save *entry*. Returns KEY_QUIT, or None to go back to the
    paused sweep (saved, cancelled, or failed)."""
    lcd.write(brand[:16], ir_encode.label(entry, number))
    time.sleep(1)

    name = prompt_text(lcd, PROMPT_NAME)
    if name in (KEY_QUIT, KEY_BACK):
        return name if name == KEY_QUIT else None

    if fun_service.saved_exists(device_type, brand, signal, name):
        answer = select_from_list(lcd, STATEMENT_FILE_EXISTS, [LABEL_OVERWRITE, LABEL_CANCEL])
        if answer == KEY_QUIT:
            return KEY_QUIT
        if answer != LABEL_OVERWRITE:
            return None

    try:
        text = ir_encode.to_signal_text(*ir_encode.encode(entry))
        fun_service.save(device_type, brand, signal, name, text)
        lcd.write(STATEMENT_SAVED, format_name(name)[:16])
        time.sleep(1)
    except (OSError, ValueError) as e:
        log.error("Fun save failed (%s/%s/%s/%s): %s", device_type, brand, signal, name, e)
        lcd.write(STATEMENT_SAVE_FAILED, str(e)[:16])
        time.sleep(2)
    return None
