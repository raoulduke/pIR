"""Sweep menu — TV-B-Gone style power-code sweep, and codes saved from it.

  Sweep ─┬─ Saved → TV → <brand> → Power → <saved name>  (Enter sends it)
         └─ TV → <brand> → Power → sweep (screens/code_sweep_screen.py)

Every level is a select_from_list (Up/Down scroll, Enter or Right picks,
Left goes back one level — the app-wide convention), line 1 a breadcrumb of the path
so far ("Sweep > TV", "TV > Samsung"). Saved mirrors the sweep's path but
only lists what has at least one save, and stays on the name just sent so
Enter resends it.

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
from screens.widgets.format import format_name, crumbs
from app.constants import MENU_TITLE, MENU_SWEEP, FUN_SAVED, STATEMENT_NO_SIGNALS
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
                 fun_service.list_brands,
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
    go back to. *picks* already made by the caller skip their lists. Each
    list re-opens on its last pick, headed by the breadcrumb *path* + picks.
    Returns KEY_QUIT, or None when Up backs out of the first list shown."""
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
        lcd.write("TX Error", str(e)[:16])
    time.sleep(0.6)
    return _NAME_LEVEL  # back to the name list, on the name just sent
