"""Sweep menu: the power-code sweep and codes saved from it.

  Sweep ─┬─ Saved → TV → Power → <saved name, any brand>  (Enter sends it)
         └─ TV → <All Brands|brand> → Power → sweep (screens/code_sweep_screen.py)

Every level is a list with a breadcrumb title; one with a single option
(TV, Power) is skipped, except Saved's name list. Saved lists only what has a
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

# Level index of the sweep path's brand list (device type, brand, signal)
# and of Saved's name list (device type, signal, name).
_BRAND_LEVEL = 1
_SWEEP_SIGNAL_LEVEL = 2
_SAVED_NAME_LEVEL = 2


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
                 fun_service.list_saved_signals,
                 fun_service.list_saved],
                _send_saved,
                formatter=lambda level: format_name,
                always_list={_SAVED_NAME_LEVEL},  # never send a lone save unasked
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
                formatter=_sweep_formatter,
                picks=[choice],
            )
        if result == KEY_QUIT:
            return KEY_QUIT


def _browse(lcd: LCD, path: list[str], sources: list, finish, formatter,
            picks: list[str] | None = None, always_list: set[int] = frozenset()) -> str | None:
    """Walk one list per entry in *sources* (each called with the picks so
    far), then call finish(lcd, *picks), which returns KEY_QUIT or the level to
    go back to. formatter(level) gives each level's display function. *picks*
    already made skip their lists; each list reopens on its last pick. A list
    with one option is picked without showing it, going forward, and passed
    over going back, except at the levels in *always_list*. Returns KEY_QUIT,
    or None when going back passes the first list."""
    picks = list(picks or [])
    floor = len(picks)
    starts = [0] * len(sources)
    backing = False

    while True:
        level = len(picks)
        if level == len(sources):
            result = finish(lcd, *picks)
            if result == KEY_QUIT:
                return KEY_QUIT
            del picks[result:]
            backing = True
            continue

        options = sources[level](*picks)
        if len(options) == 1 and level not in always_list and not backing:
            picks.append(options[0])
            continue
        if not options or (len(options) == 1 and level not in always_list):
            if level == floor:
                return None
            picks.pop()
            backing = True
            continue

        heading = crumbs(*path, *(formatter(i)(p) for i, p in enumerate(picks)))
        choice = select_from_list(
            lcd, heading, options, start_index=starts[level], formatter=formatter(level),
        )

        if choice == KEY_QUIT:
            return KEY_QUIT
        if choice == KEY_BACK:
            if level == floor:
                return None
            picks.pop()
            backing = True
            continue

        starts[level] = options.index(choice)
        picks.append(choice)
        backing = False


def _sweep_formatter(level: int):
    """Brands are already display names ("LG", "onn."); everything else is
    a snake_case name ("tv", "power", a typed save name)."""
    return str if level == _BRAND_LEVEL else format_name


def _sweep(lcd: LCD, device_type: str, brand: str, signal: str) -> str | int:
    result = code_sweep_screen.show(lcd, device_type, brand, signal)
    return KEY_QUIT if result == KEY_QUIT else _SWEEP_SIGNAL_LEVEL


def _send_saved(lcd: LCD, device_type: str, signal: str, name: str) -> int:
    path = fun_service.save_path(device_type, signal, name)
    try:
        ir_transmitter.transmit(path)
        lcd.write(crumbs(format_name(device_type), format_name(signal)), format_name(name)[:16])
    except HardwareError as e:
        log.error("IR transmit failed (%s): %s", path, e)
        lcd.write("TX Error", fit_or(str(e), ""))
    time.sleep(0.6)
    return _SAVED_NAME_LEVEL  # back to the name list, on the name just sent
