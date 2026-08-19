"""Shutdown confirmation screen."""

from hardware.lcd import LCD
from hardware.input import read_key, KEY_ENTER, KEY_LEFT, KEY_QUIT
from services import system_service
from app.state import AppState


def show(lcd: LCD, state: AppState) -> None:
    lcd.write("Shutdown Pi?", "Enter=Yes  <-=No")

    while state.running:
        key = read_key()

        if key == KEY_ENTER:
            lcd.write("Shutting down...", "")
            system_service.shutdown()
            return

        if key in (KEY_LEFT, KEY_QUIT):
            lcd.clear()
            return
