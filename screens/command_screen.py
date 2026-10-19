"""Voice command screen: pick a template (_TEMPLATES — the phrasings Google
Home reliably acts on), fill its one blank with the D-pad composer, and it's
sent through Home Assistant. Loops back to the template list after each
send; Left from the blank returns to the list. An empty entry reprompts."""

import time
from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK, press_count
from services import preset_service, voice_service
from screens.widgets.selector import select_from_list
from screens.widgets.char_input import read_dpad_text
from screens.widgets.format import crumbs
from app.constants import (
    MENU_TITLE, MENU_COMMAND, STATEMENT_SENDING, STATEMENT_FAILED, ASYNC_SEND_FLASH_SECONDS,
)

# (list label, template with one "{}" blank, fill-in prompt). Only phrasings
# Google Home is confirmed to act on; check before rewording.
_TEMPLATES = [
    ("Turn On", "Turn on {}"),
    ("Turn Off", "Turn off {}"),
    ("Dim", "Dim {}"),
    ("Brighten", "Brighten {}"),
    ("Play on YouTube", "Play {} on YouTube"),
    ("Play on Spotify", "Play {} on Spotify"),
]
_LABELS = [label for label, _ in _TEMPLATES]


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
        choice = select_from_list(lcd, crumbs(MENU_TITLE, MENU_COMMAND), _LABELS, start_index=index)

        if choice == KEY_QUIT:
            return KEY_QUIT
        if choice == KEY_BACK:
            return None

        index = _LABELS.index(choice)
        label, template = _TEMPLATES[index]

        value = _fill_blank(lcd, crumbs(MENU_TITLE, MENU_COMMAND, label))
        if value == KEY_QUIT:
            return KEY_QUIT
        if value == KEY_BACK:
            continue  # back to the template list, not out of Command

        command = template.format(value)
        # Sent in the background (up to 15s); only a failure shows afterwards.
        lcd.write(STATEMENT_SENDING, command[:16])
        pressed = press_count()

        def _on_result(sent, total, failed_step, command=command, pressed=pressed):
            # Only over this screen: a key since means it's moved on (logged anyway).
            if failed_step is not None and press_count() == pressed:
                lcd.write(STATEMENT_FAILED, command[:16])

        preset_service.run_steps_background([{"type": "voice", "command": command}], _on_result)
        time.sleep(ASYNC_SEND_FLASH_SECONDS)
