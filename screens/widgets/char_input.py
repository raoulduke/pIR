"""D-pad text entry — the app's only text entry, since the keypad has no
letter keys. Like a feature phone: Up/Down pick the character, Right
commits it and moves on, Left deletes (or backs out once empty), Enter
submits."""

from hardware.lcd import LCD
from hardware.input import read_key, use_default_keys, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER, KEY_QUIT, KEY_BACK

# Ordered for the fewest presses: capitals first for a word's first
# character (the start, or after a space), lowercase otherwise, so names
# come out title case. Space is last, one Up from the first character.
# No `"` or `~` (the A00 ROM has no tilde; RPLCD draws it blank, so it
# passed for a space), so a password containing one can't be typed.
_LOWER = "abcdefghijklmnopqrstuvwxyz"
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_DIGITS = "0123456789"
_SYMBOLS = "!#$%&'()*+,-./:;<=>?@[]^_`{|} "
_FIRST_CHARSET = _UPPER + _LOWER + _DIGITS + _SYMBOLS
_CHARSET = _LOWER + _DIGITS + _UPPER + _SYMBOLS


def _charset(text: str) -> str:
    return _FIRST_CHARSET if not text or text[-1] == " " else _CHARSET


def read_dpad_text(lcd: LCD, prompt: str) -> str:
    """Collect a line of text with Up/Down/Left/Right/Enter. Returns the text,
    KEY_BACK or KEY_QUIT.

    Enter submits what's on screen, including the character under the cursor
    without a Right first — except an untouched slot's default preview, so
    Right-then-Enter adds nothing and an untouched entry submits "" (a valid
    open-network password)."""
    text = ""
    candidate_index = 0
    candidate_touched = False  # Up/Down pressed since the last commit/start?

    while True:
        use_default_keys()  # Up/Down repeat to scroll characters; Left doesn't
        chars = _charset(text)
        display = (text + chars[candidate_index])[-16:]
        lcd.write(prompt[:16], display)

        key = read_key()

        if key == KEY_QUIT:
            return KEY_QUIT
        if key == KEY_ENTER:
            return text + chars[candidate_index] if candidate_touched else text
        if key == KEY_DOWN:
            candidate_index = (candidate_index + 1) % len(chars)
            candidate_touched = True
        elif key == KEY_UP:
            candidate_index = (candidate_index - 1) % len(chars)
            candidate_touched = True
        elif key == KEY_RIGHT:
            text += chars[candidate_index]
            candidate_index = 0
            candidate_touched = False
        elif key == KEY_LEFT:
            if not text:
                return KEY_BACK
            text = text[:-1]
            candidate_index = 0
            candidate_touched = False
