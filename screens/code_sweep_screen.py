"""Sweep — steps through every library code for one brand's signal
(TV-B-Gone style, but one brand at a time and slow enough to see which code
worked), reached from screens/fun_screen.py. All Brands sweeps every
brand's codes in one go; a save goes under the brand of the code on
screen.

Line 1 is that brand and the position in the sweep ("Hisense    15/38"),
the count ending in the last column; "38/38" marks the end.

Running: one code per gap (Menu > Settings > Sweep Speed: 1s, 0.5s or
0.25s), line 1 naming the code just sent. Enter pauses on it, Left goes
back a level (the signal list).

Paused ("||" on line 2):
  Up / Down     move to the previous / next code without sending it (no
                wrapping — they stop at the first / last code, which
                the count shows). They auto-repeat when held, since
                nothing fires.
  Right         send the code on screen, once per press ("|| Sending..."
                on line 2 while it goes out)
  Enter (tap)   resume from the code on screen: sent first unless it
                already went out since landing on it (paused on it, or
                fired with Right), so a code never goes out twice in a row
  Enter (hold)  save the code under a typed name (fun_service.save())
  Left          back a level
Running off the end pauses on the last code, so Up can still step back.

Tap vs. hold on Enter is this screen's alone (hardware.input.
set_hold_keys()): a tap acts on release here, ~0.1s later than a press
would, so everywhere else Enter still acts the instant it's pressed.
Without a keypad (USB/SSH) there's no hold, so no saving from there.

Runs on its own loop with ir_transmitter called directly, not
preset_service's background worker — a pause has to land between two
codes, not after whatever's already queued.
"""

import logging
import time

from hardware.lcd import LCD
from hardware import ir_transmitter
from hardware.input import (
    read_key, set_repeatable_keys, set_repeat_interval, set_hold_keys,
    KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER, KEY_ENTER_HOLD, KEY_QUIT, KEY_BACK,
)
from services import config_service, fun_service, ir_encode
from screens.widgets.selector import select_from_list
from screens.widgets.signal_entry import prompt_text
from screens.widgets.format import format_name, with_arrows, fit_with_suffix
from app.constants import (
    PROMPT_NAME, STATEMENT_NO_SIGNALS, STATEMENT_SENDING, ASYNC_SEND_FLASH_SECONDS,
    STATEMENT_FILE_EXISTS, STATEMENT_SAVED, STATEMENT_SAVE_FAILED,
    LABEL_OVERWRITE, LABEL_CANCEL,
)
from app.exceptions import HardwareError

log = logging.getLogger(__name__)


def show(lcd: LCD, device_type: str, brand: str, signal: str) -> str | None:
    """Returns KEY_QUIT, or None when Left goes back a level."""
    codes = fun_service.get_codes(device_type, brand, signal)
    if not codes:
        lcd.write(brand[:16], STATEMENT_NO_SIGNALS)
        time.sleep(1)
        return None
    try:
        return _sweep(lcd, device_type, signal, codes, config_service.get_sweep_gap_seconds())
    finally:
        set_hold_keys({})


def _use_sweep_keys() -> None:
    """Enter: tap/hold. Up/Down repeat (they only move, never send);
    Right doesn't, so a hold can't rapid-fire codes. Re-asserted every
    pass, since the save prompt's text entry and list switch to their own."""
    set_hold_keys({KEY_ENTER: KEY_ENTER_HOLD})
    set_repeatable_keys(set())
    set_repeat_interval(None)


def _sweep(lcd: LCD, device_type: str, signal: str, codes: list[tuple[str, dict]],
           gap: float) -> str | None:
    """*codes* are (brand, code) pairs; *gap* is seconds between codes."""
    last = len(codes) - 1
    index = 0
    sent = None  # the code that last went out, while still on it

    def fire() -> bool:
        nonlocal sent
        ok = _send(lcd, codes, index)
        sent = index if ok else None
        return ok

    _use_sweep_keys()
    paused = not fire()
    if not paused:
        _draw_running(lcd, codes, index)

    while True:
        _use_sweep_keys()
        if paused:
            _draw_paused(lcd, codes, index, last)
            key = read_key()
            if key == KEY_QUIT:
                return KEY_QUIT
            if key == KEY_LEFT:
                return None
            if key == KEY_UP and index > 0:
                index -= 1
            elif key == KEY_DOWN and index < last:
                index += 1
            elif key == KEY_RIGHT:
                lcd.write(*with_arrows(_line1(codes, index), f"|| {STATEMENT_SENDING}", last > 0))
                started = time.monotonic()
                fire()
                # Long enough to see even when the send itself is quick.
                time.sleep(max(0.0, ASYNC_SEND_FLASH_SECONDS - (time.monotonic() - started)))
            elif key == KEY_ENTER_HOLD:
                set_hold_keys({})  # plain Enter while naming it
                brand, entry = codes[index]
                if _save(lcd, device_type, brand, signal, entry, index + 1) == KEY_QUIT:
                    return KEY_QUIT
            elif key == KEY_ENTER:
                paused = sent != index and not fire()
                if not paused:
                    _draw_running(lcd, codes, index)
            continue

        key = _wait(lcd, codes, index, gap)
        if key == KEY_QUIT:
            return KEY_QUIT
        if key == KEY_LEFT:
            return None
        if key in (KEY_ENTER, KEY_ENTER_HOLD) or index == last:
            paused = True
            continue
        index += 1
        paused = not fire()
        if not paused:
            _draw_running(lcd, codes, index)


def _wait(lcd: LCD, codes: list[tuple[str, dict]], index: int, gap: float) -> str | None:
    """Wait out the gap after a code. Returns Enter/Left/Quit as soon as
    pressed, or None once the full gap has passed. Other keys are ignored
    without cutting the gap short."""
    deadline = time.monotonic() + gap
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        key = read_key(timeout=remaining)
        if key in (KEY_ENTER, KEY_ENTER_HOLD, KEY_LEFT, KEY_QUIT):
            return key
        if key is None and time.monotonic() < deadline:
            # A quick action (e.g. a Preset key) drew over the screen.
            _draw_running(lcd, codes, index)


def _draw_running(lcd: LCD, codes: list[tuple[str, dict]], index: int) -> None:
    # No key presses while it runs, but the code on screen is the whole
    # point — keep the backlight's idle timer from darkening it.
    lcd.wake()
    lcd.write(_line1(codes, index), "Enter=Pause")


def _draw_paused(lcd: LCD, codes: list[tuple[str, dict]], index: int, last: int) -> None:
    lcd.write(*with_arrows(_line1(codes, index), "|| Hold=Save", last > 0))


def _line1(codes: list[tuple[str, dict]], index: int) -> str:
    """The code's brand, then "15/38" ending in the last column."""
    return fit_with_suffix(codes[index][0], f"{index + 1}/{len(codes)}", 16)


def _send(lcd: LCD, codes: list[tuple[str, dict]], index: int) -> bool:
    """Transmit codes[index]. On failure, logs it, shows the error and
    returns False — the caller pauses rather than sweeping on through
    codes that aren't actually going out."""
    brand, entry = codes[index]
    try:
        ir_transmitter.transmit_pulses(*ir_encode.encode(entry))
        return True
    except (HardwareError, ValueError) as e:
        log.error("Sweep transmit failed (%s #%d %s): %s", brand, index + 1, entry, e)
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
