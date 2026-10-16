"""Scrollable list selector widget.

``select_from_list(lcd, title, options)`` displays a navigable list on the
LCD and returns the chosen option string, KEY_BACK, or KEY_QUIT.
"""

from hardware.lcd import LCD
from hardware.input import read_key, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_ENTER, KEY_QUIT, KEY_BACK
from screens.widgets.format import format_name


def select_from_list(
        lcd: LCD, title: str, options: list[str], start_index: int = 0,
        formatter=format_name,
) -> str:
    """Navigate *options* and return the selected entry or a key constant.

    *start_index* opens the list on that entry instead of the first one —
    e.g. re-showing a button list on the button just transmitted, so
    repeating a signal is just pressing ENTER again.

    *formatter* turns an option into its display text — format_name by
    default, for snake_case names; pass ``str`` for options that are
    already display names (the Fun menu's TV brands: "LG", "onn.").
    """
    index = start_index if 0 <= start_index < len(options) else 0
    count = len(options)

    while True:
        lcd.write(title[:16], formatter(options[index])[:16])
        key = read_key()

        if key == KEY_QUIT:
            return KEY_QUIT
        if key == KEY_LEFT:
            return KEY_BACK
        if key == KEY_UP:
            index = (index - 1) % count
        if key == KEY_DOWN:
            index = (index + 1) % count
        if key == KEY_ENTER:
            return options[index]
