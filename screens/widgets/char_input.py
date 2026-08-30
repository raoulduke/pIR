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
"""

from hardware.lcd import LCD
from hardware.input import read_key, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER, KEY_QUIT, KEY_BACK

# Ordered so the common case (a lowercase letter) is reachable with the
# fewest Up presses from the default starting character. Excludes `"` —
# services/wifi_service.py wraps values in literal double quotes for
# wpa_cli, so keeping them out of the charset sidesteps escaping entirely.
_CHARSET = (
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    " !#$%&'()*+,-./:;<=>?@[]^_`{|}~"
)


def read_dpad_text(lcd: LCD, prompt: str) -> str:
    """Collect a line of text using only Up/Down/Left/Right/Enter.

    Returns the entered string, KEY_BACK, or KEY_QUIT. Enter submits (even
    an empty string, unlike read_text — an empty Wi-Fi password is valid
    for an open network). Left deletes the last committed character, or
    backs out of the screen entirely once there's nothing left to delete.
    """
    text = ""
    candidate_index = 0

    while True:
        display = (text + _CHARSET[candidate_index])[-16:]
        lcd.write(prompt[:16], display)

        key = read_key()

        if key == KEY_QUIT:
            return KEY_QUIT
        if key == KEY_ENTER:
            return text
        if key == KEY_DOWN:
            candidate_index = (candidate_index + 1) % len(_CHARSET)
        elif key == KEY_UP:
            candidate_index = (candidate_index - 1) % len(_CHARSET)
        elif key == KEY_RIGHT:
            text += _CHARSET[candidate_index]
            candidate_index = 0
        elif key == KEY_LEFT:
            if not text:
                return KEY_BACK
            text = text[:-1]
            candidate_index = 0
