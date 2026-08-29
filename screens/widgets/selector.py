"""Scrollable list selector widget.

``select_from_list(lcd, title, options)`` displays a navigable list on the
LCD and returns the chosen option string, KEY_BACK, or KEY_QUIT.
"""

from hardware.lcd import LCD
from hardware.input import read_key, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_ENTER, KEY_QUIT, KEY_BACK
from screens.widgets.format import format_name


def select_from_list(
        lcd: LCD, title: str, options: list[str], extra_keys: dict | None = None,
        start_index: int = 0,
) -> str:
    """Navigate *options* and return the selected entry or a key constant.

    *extra_keys*, if given, maps additional key constants to anything
    truthy — a matching key is returned immediately, same as KEY_BACK/
    KEY_QUIT, letting a caller (e.g. send_screen's device-cycle shortcut)
    intercept keys beyond the standard nav set without this widget needing
    to know what they mean.

    *start_index* opens the list on that entry instead of the first one —
    e.g. re-showing a button list on the button just transmitted, so
    repeating a signal is just pressing ENTER again.
    """
    index = start_index if 0 <= start_index < len(options) else 0
    count = len(options)
    extra_keys = extra_keys or {}

    while True:
        lcd.write(title[:16], format_name(options[index])[:16])
        key = read_key()

        if key == KEY_QUIT:
            return KEY_QUIT
        if key == KEY_LEFT:
            return KEY_BACK
        if key in extra_keys:
            return key
        if key == KEY_UP:
            index = (index - 1) % count
        if key == KEY_DOWN:
            index = (index + 1) % count
        if key == KEY_ENTER:
            return options[index]
