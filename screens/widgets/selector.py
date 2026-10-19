"""List selector: Up/Down scroll (wrapping), the up/down glyph in line 2's
last column when there's more than one option, Enter/Right select, Left
returns KEY_BACK."""

from hardware.lcd import LCD
from hardware.input import (
    read_key, use_default_keys, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER, KEY_QUIT, KEY_BACK,
)
from screens.widgets.format import format_name, with_arrows


def select_from_list(
        lcd: LCD, title: str, options: list[str], start_index: int = 0,
        formatter=format_name,
) -> str:
    """Show *options* and return the chosen one, KEY_BACK or KEY_QUIT.
    *start_index* opens on that entry; *formatter* gives the display text
    (format_name by default; str for names already formatted)."""
    index = start_index if 0 <= start_index < len(options) else 0
    count = len(options)

    while True:
        # Re-asserted every pass: a quick action (Menu) or another screen
        # may have changed it while this list was waiting.
        use_default_keys()
        lcd.write(*with_arrows(title, formatter(options[index]), count > 1))
        key = read_key()

        if key == KEY_QUIT:
            return KEY_QUIT
        if key == KEY_LEFT:
            return KEY_BACK
        if key == KEY_UP:
            index = (index - 1) % count
        if key == KEY_DOWN:
            index = (index + 1) % count
        if key in (KEY_ENTER, KEY_RIGHT):
            return options[index]
