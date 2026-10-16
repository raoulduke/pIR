"""Voice / Home Assistant command screen.

Fill-in-the-blank templates, not freeform text — Google Home only reliably
acts on a handful of phrasings, so letting someone type an arbitrary
sentence on a D-pad-only composer just to have it fail was worse than
picking from what's known to work. Two steps, looping back to the
template list after every send (repeating the same kind of command, e.g.
several "Turn on X"s in a row, is the common case):

  1. Pick a template from _TEMPLATES — screens/widgets/selector.py's
     select_from_list, same widget receive_screen.py and the Wi-Fi network
     picker already use. Up backs out of Command entirely.
  2. Fill its one blank — screens/widgets/char_input.py's D-pad-only
     read_dpad_text, same as network_screen.py's Wi-Fi password prompt
     (see command_screen's prior version, which used this for the whole
     command before templates replaced it). Left here backs out to the
     template list instead, not out of Command — same layered
     back-navigation receive_screen.py uses for its device/location/button
     steps. An empty entry reprompts rather than sending a blank.
"""

import time
from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK
from services import preset_service, voice_service
from screens.widgets.selector import select_from_list
from screens.widgets.char_input import read_dpad_text
from app.constants import (
    PROMPT_DEVICE, PROMPT_SEARCH, STATEMENT_SENDING, STATEMENT_FAILED, ASYNC_SEND_FLASH_SECONDS,
)

# (list label, command template with one "{}" blank, fill-in prompt).
# Phrasing matches exactly what Google Home is confirmed to act on — don't
# reword these without confirming the new phrasing still works.
_TEMPLATES = [
    ("Turn On", "Turn on {}", PROMPT_DEVICE),
    ("Turn Off", "Turn off {}", PROMPT_DEVICE),
    ("Dim", "Dim {}", PROMPT_DEVICE),
    ("Brighten", "Brighten {}", PROMPT_DEVICE),
    ("Play on YouTube", "Play {} on YouTube", PROMPT_SEARCH),
    ("Play on Spotify", "Play {} on Spotify", PROMPT_SEARCH),
]
_LABELS = [label for label, _, _ in _TEMPLATES]


def _fill_blank(lcd: LCD, prompt: str) -> str:
    """Loops on empty input — nothing to send with an unfilled blank.
    Returns the entered value, KEY_BACK, or KEY_QUIT."""
    while True:
        value = read_dpad_text(lcd, prompt)
        if value in (KEY_QUIT, KEY_BACK) or value:
            return value


def show(lcd: LCD) -> str | None:
    if not voice_service.token_exists():
        lcd.write("HA token missing", "Add token file")
        time.sleep(3)
        return None

    index = 0
    while True:
        # start_index re-opens on the template just used, so repeating the
        # same kind of command is just Enter, fill, Enter again.
        choice = select_from_list(lcd, "Command", _LABELS, start_index=index)

        if choice == KEY_QUIT:
            return KEY_QUIT
        if choice == KEY_BACK:
            return None

        index = _LABELS.index(choice)
        _, template, prompt = _TEMPLATES[index]

        value = _fill_blank(lcd, prompt)
        if value == KEY_QUIT:
            return KEY_QUIT
        if value == KEY_BACK:
            continue  # back to the template list, not out of Command

        command = template.format(value)
        # Non-blocking (see services/preset_service.py's
        # run_steps_background) — send_command() is a real HTTP round-trip
        # (up to 15s timeout), so blocking here used to leave "Sending..."
        # up and the keypad unresponsive for however long that took. Shows
        # "Sending..." for a brief fixed flash (ASYNC_SEND_FLASH_SECONDS —
        # the command is already sending in the background during it, this
        # is only so the message is actually seen before looping back to
        # the template list would otherwise overwrite it) then loops back;
        # a failure overwrites whatever's on screen by then, a success
        # needs no follow-up message.
        lcd.write(STATEMENT_SENDING, command[:16])

        def _on_result(sent, total, failed_step, command=command):
            if failed_step is not None:
                lcd.write(STATEMENT_FAILED, command[:16])

        preset_service.run_steps_background([{"type": "voice", "command": command}], _on_result)
        time.sleep(ASYNC_SEND_FLASH_SECONDS)
