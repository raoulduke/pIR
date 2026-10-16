"""Fun menu — TV-B-Gone style power-code sweep, and codes saved from it.

  Fun ─┬─ Sweep → TV → <brand> → Power → sweep (screens/code_sweep_screen.py)
       └─ Saved → TV → <brand> → Power → <saved name>  (Enter sends it)

Every level is a select_from_list; Left goes back one level, except out of
a sweep, which goes back to the brand list (picking the next brand to try
is the usual next step). Saved mirrors Sweep's path but only lists what has
at least one save, and stays on the name just sent so Enter resends it.

Codes come from data/fun_codes.json, saves from fun_signals/ — see
services/fun_service.py. Neither is ever visible to Learn, Rename or
Active Device Control.
"""

import logging
import time

from hardware.lcd import LCD
from hardware import ir_transmitter
from hardware.input import KEY_QUIT, KEY_BACK
from services import fun_service
from screens import code_sweep_screen
from screens.widgets.selector import select_from_list
from screens.widgets.format import format_name
from app.constants import MENU_FUN, FUN_SWEEP, FUN_SAVED, STATEMENT_NO_SIGNALS
from app.exceptions import HardwareError

log = logging.getLogger(__name__)

_MODES = [FUN_SWEEP, FUN_SAVED]

# Level index each path's lists sit at: device type, brand, signal[, name].
_BRAND_LEVEL = 1


def show(lcd: LCD) -> str | None:
    index = 0
    while True:
        choice = select_from_list(lcd, MENU_FUN, _MODES, start_index=index)
        if choice == KEY_QUIT:
            return KEY_QUIT
        if choice == KEY_BACK:
            return None
        index = _MODES.index(choice)

        if choice == FUN_SWEEP:
            result = _browse(
                lcd, choice,
                [fun_service.list_device_types,
                 fun_service.list_brands,
                 fun_service.list_signals],
                _sweep,
            )
        else:
            if not fun_service.list_saved_device_types():
                lcd.write(FUN_SAVED, STATEMENT_NO_SIGNALS)
                time.sleep(1)
                continue
            result = _browse(
                lcd, choice,
                [fun_service.list_saved_device_types,
                 fun_service.list_saved_brands,
                 fun_service.list_saved_signals,
                 fun_service.list_saved],
                _send_saved,
            )
        if result == KEY_QUIT:
            return KEY_QUIT


def _browse(lcd: LCD, title: str, sources: list, finish) -> str | None:
    """Walk one list per entry in *sources* (each called with the picks so
    far), then call finish(lcd, *picks), which returns KEY_QUIT or the level to
    go back to. Each list re-opens on its last pick. Returns KEY_QUIT, or
    None when Left backs out of the first list."""
    picks: list[str] = []
    starts = [0] * len(sources)

    while True:
        level = len(picks)
        if level == len(sources):
            result = finish(lcd, *picks)
            if result == KEY_QUIT:
                return KEY_QUIT
            del picks[result:]
            continue

        options = sources[level](*picks)
        if not options:
            if level == 0:
                return None
            picks.pop()
            continue

        heading = title if level == 0 else _formatter(level - 1)(picks[-1])
        choice = select_from_list(
            lcd, heading, options, start_index=starts[level], formatter=_formatter(level),
        )

        if choice == KEY_QUIT:
            return KEY_QUIT
        if choice == KEY_BACK:
            if level == 0:
                return None
            picks.pop()
            continue

        starts[level] = options.index(choice)
        picks.append(choice)


def _formatter(level: int):
    """Brands are already display names ("LG", "onn."); everything else is
    a snake_case name ("tv", "power", a typed save name)."""
    return str if level == _BRAND_LEVEL else format_name


def _sweep(lcd: LCD, device_type: str, brand: str, signal: str) -> str | int:
    result = code_sweep_screen.show(lcd, device_type, brand, signal)
    return KEY_QUIT if result == KEY_QUIT else _BRAND_LEVEL


def _send_saved(lcd: LCD, device_type: str, brand: str, signal: str, name: str) -> int:
    path = fun_service.save_path(device_type, brand, signal, name)
    try:
        ir_transmitter.transmit(path)
        lcd.write(brand[:16], format_name(name)[:16])
    except HardwareError as e:
        log.error("IR transmit failed (%s): %s", path, e)
        lcd.write("TX Error", str(e)[:16])
    time.sleep(0.6)
    return 3  # back to the name list, on the name just sent
