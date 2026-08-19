"""Free-text entry widget.

``read_text(lcd, prompt)`` echoes characters typed via the input reader and
returns the final string, KEY_BACK, or KEY_QUIT.
"""

from hardware.lcd import LCD
from hardware.input import (
    read_key,
    KEY_QUIT, KEY_LEFT, KEY_ENTER, KEY_BACKSPACE, KEY_BACK,
)
from hardware.input import _COMMANDS


def read_text(lcd: LCD, prompt: str) -> str:
    """Collect a line of text.  Returns the entered string, KEY_BACK, or KEY_QUIT."""
    text = ""

    while True:
        display_text = (text + "_")[:16] if len(text) <= 15 else text[-16:]
        lcd.write(prompt[:16], display_text)

        key = read_key()

        if key == KEY_QUIT:
            return KEY_QUIT
        if key == KEY_LEFT:
            return KEY_BACK
        if key == KEY_ENTER:
            if text.strip():
                return text.strip()
            continue
        if key == KEY_BACKSPACE:
            text = text[:-1]
            continue
        if key and key not in _COMMANDS and len(key) == 1:
            text += key
