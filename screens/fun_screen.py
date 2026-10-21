"""Sweep menu: the power-code sweep and codes saved from it.

  Sweep ─┬─ Saved → TV → <brand> → Power → <saved name>  (Enter sends it)
         └─ TV → <All Brands|brand> → Power → sweep (screens/code_sweep_screen.py)

Every level is a list with a breadcrumb title. Saved lists only what has a
save and stays on the name just sent, so Enter resends it."""

import logging
import time

from hardware.lcd import LCD
from hardware import ir_transmitter
from hardware.input import KEY_QUIT, KEY_BACK
from services import fun_service
from screens import code_sweep_screen
from screens.widgets.selector import select_from_list
from screens.widgets.format import fit_or, format_name, crumbs
from app.constants import MENU_TITLE, MENU_SWEEP, FUN_SAVED, FUN_ALL_BRANDS, STATEMENT_NO_SIGNALS
from app.exceptions import HardwareError

log = logging.getLogger(__name__)

_PATH = [MENU_TITLE, MENU_SWEEP]

# Level index each path's lists sit at: device type, brand, signal[, name].
_BRAND_LEVEL = 1
_SIGNAL_LEVEL = 2
_NAME_LEVEL = 3


def show(lcd: LCD) -> str | None:
    index = 0
    while True:
        options = [FUN_SAVED] + fun_service.list_device_types()
        choice = select_from_list(lcd, crumbs(*_PATH), options, start_index=index)
        if choice == KEY_QUIT:
            return KEY_QUIT
        if choice == KEY_BACK:
            return None
        index = options.index(choice)

        if choice == FUN_SAVED:
            path = [*_PATH, FUN_SAVED]
            if not fun_service.list_saved_device_types():
                lcd.write(crumbs(*path), STATEMENT_NO_SIGNALS)
                time.sleep(1)
                continue
            result = _browse(
                lcd, path,
                [fun_service.list_saved_device_types,
                 fun_service.list_saved_brands,
                 fun_service.list_saved_signals,
                 fun_service.list_saved],
                _send_saved,
            )
        else:
            # The device type was picked right here, so browsing starts
            # one level in, on its brand list.
            result = _browse(
                lcd, _PATH,
                [fun_service.list_device_types,
                 lambda device_type: [FUN_ALL_BRANDS, *fun_service.list_brands(device_type)],
                 fun_service.list_signals],
                _sweep,
                picks=[choice],
            )
        if result == KEY_QUIT:
            return KEY_QUIT


def _browse(lcd: LCD, path: list[str], sources: list, finish,
            picks: list[str] | None = None) -> str | None:
    """Walk one list per entry in *sources* (each called with the picks so
    far), then call finish(lcd, *picks), which returns KEY_QUIT or the level to
    go back to. *picks* already made skip their lists; each list reopens on its
    last pick. Returns KEY_QUIT, or None when Left backs out of the first list."""
    picks = list(picks or [])
    floor = len(picks)
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
            if level == floor:
                return None
            picks.pop()
            continue

        heading = crumbs(*path, *(_formatter(i)(p) for i, p in enumerate(picks)))
        choice = select_from_list(
            lcd, heading, options, start_index=starts[level], formatter=_formatter(level),
        )

        if choice == KEY_QUIT:
            return KEY_QUIT
        if choice == KEY_BACK:
            if level == floor:
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
    return KEY_QUIT if result == KEY_QUIT else _SIGNAL_LEVEL


def _send_saved(lcd: LCD, device_type: str, brand: str, signal: str, name: str) -> int:
    path = fun_service.save_path(device_type, brand, signal, name)
    try:
        ir_transmitter.transmit(path)
        lcd.write(brand[:16], format_name(name)[:16])
    except HardwareError as e:
        log.error("IR transmit failed (%s): %s", path, e)
        lcd.write("TX Error", fit_or(str(e), ""))
    time.sleep(0.6)
    return _NAME_LEVEL  # back to the name list, on the name just sent
