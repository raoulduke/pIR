"""D-pad-only text entry widget.

The physical 12-wire keypad has no letter keys at all — every button is a
pre-assigned label (D-pad, presets, Menu; see hardware/keypad.py). Free-text
entry from it (screens/widgets/text_input.py's read_text) only works from a
USB keyboard or SSH session, since it just passes raw characters through.

``read_dpad_text`` fills that gap the way a feature phone's keypad does:
compose one character at a time by cycling a candidate with Up/Down, commit
it and move to the next slot with Right. Only Up/Down/Left/Right/Enter are
used, since those are the only raw keys the bare keypad can ever produce
(everything else — Menu, Device cycle, Presets — is a global quick action
intercepted before it reaches read_key() here; see hardware/input.py).

Matches the app-wide navigation convention (CLAUDE.md's "Navigation
convention") where it can: Up/Down scroll (the character, here) and Left
goes back (deleting first, then out once empty). Right commits the
character instead of selecting, and Enter submits.
"""

from hardware.lcd import LCD
from hardware.input import read_key, use_default_keys, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER, KEY_QUIT, KEY_BACK

# Ordered so the common case is reachable with the fewest presses from the
# default starting character: a capital for the first character (labels
# start capitalized), lowercase after that. Excludes `"` —
# services/wifi_service.py wraps values in literal double quotes for
# wpa_cli, so keeping them out of the charset sidesteps escaping entirely.
_LOWER = "abcdefghijklmnopqrstuvwxyz"
_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_DIGITS = "0123456789"
_SYMBOLS = " !#$%&'()*+,-./:;<=>?@[]^_`{|}~"
_FIRST_CHARSET = _UPPER + _LOWER + _DIGITS + _SYMBOLS
_CHARSET = _LOWER + _DIGITS + _UPPER + _SYMBOLS


def _charset(text: str) -> str:
    return _FIRST_CHARSET if not text else _CHARSET


def read_dpad_text(lcd: LCD, prompt: str) -> str:
    """Collect a line of text using only Up/Down/Left/Right/Enter.

    Returns the entered string, KEY_BACK, or KEY_QUIT. Left deletes the
    last committed character, or backs out of the screen entirely once
    there's nothing left to delete.

    Enter submits whatever's actually showing on screen, character the
    cursor is sitting on included — not just the characters already
    locked in with Right. Cycling Up/Down to a letter and pressing Enter
    right away (no Right first) works, matching what's visibly displayed;
    no separate, invisible "confirm" step to remember (confirmed
    2026-09-03: without this, "tv" typed as t-Right-<cycle to v>-Enter
    silently submitted just "t" — the display showed "tv" the whole time
    with no visual difference between the committed "t" and the
    never-locked-in "v").

    The one exception is a slot you haven't touched Up/Down on since the
    last commit (or since the start) — its default preview character
    doesn't count, so pressing Right then Enter immediately doesn't
    silently tack on an unintended "a"/"A" (the charset's first character), and
    a fresh, completely untouched call still submits "" on Enter, same as
    before — an empty Wi-Fi password is valid for an open network.
    """
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
