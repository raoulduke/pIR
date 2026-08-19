"""Voice / Home Assistant command screen."""

import time
from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK
from services import voice_service
from screens.widgets.text_input import read_text
from app.constants import PROMPT_COMMAND, STATEMENT_SENDING, STATEMENT_SENT, STATEMENT_FAILED


def show(lcd: LCD) -> str | None:
    if not voice_service.token_exists():
        lcd.write("HA token missing", "Add token file")
        time.sleep(3)
        return None

    while True:
        command = read_text(lcd, PROMPT_COMMAND)

        if command == KEY_QUIT:
            return KEY_QUIT
        if command == KEY_BACK:
            return None

        lcd.write(STATEMENT_SENDING, command[:16])
        success, message = voice_service.send_command(command)

        if success:
            lcd.write(STATEMENT_SENT, message[:16])
            time.sleep(2)
        else:
            lcd.write(STATEMENT_FAILED, message[:16])
            time.sleep(3)
