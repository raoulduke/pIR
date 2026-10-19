"""Settings (Menu > Settings). Each setting opens a list of choices that
opens on the current one, marked "(current)"; Enter saves and applies it,
Left cancels.

  Backlight     idle timeout (backlight_timeout_seconds; null = Never)
  Sweep Speed   seconds between sweep codes (sweep_gap_seconds)"""

import time

from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK
from services import config_service
from screens.widgets.selector import select_from_list
from screens.widgets.format import crumbs
from app.constants import (
    MENU_TITLE, MENU_SETTINGS, SETTINGS_BACKLIGHT, BACKLIGHT_TIMEOUT_CHOICES,
    SETTINGS_SWEEP_SPEED, SWEEP_GAP_CHOICES, STATEMENT_SAVED,
)

_CURRENT_SUFFIX = " (current)"


def show(lcd: LCD) -> str | None:
    while True:
        choice = select_from_list(
            lcd, crumbs(MENU_TITLE, MENU_SETTINGS), [SETTINGS_BACKLIGHT, SETTINGS_SWEEP_SPEED],
            formatter=str,
        )
        if choice == KEY_QUIT:
            return KEY_QUIT
        if choice == KEY_BACK:
            return None
        if choice == SETTINGS_BACKLIGHT:
            if _backlight(lcd) == KEY_QUIT:
                return KEY_QUIT
        if choice == SETTINGS_SWEEP_SPEED:
            if _sweep_speed(lcd) == KEY_QUIT:
                return KEY_QUIT


def _label(seconds: int | None) -> str:
    if seconds is None:
        return "Never"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _backlight(lcd: LCD) -> str | None:
    current = config_service.get_backlight_timeout_seconds()
    choices = list(BACKLIGHT_TIMEOUT_CHOICES)
    if current not in choices:
        # A saved value that isn't a choice (hand-edited, or from older choices):
        # listed in order so it can be kept.
        choices.insert(1 + sum(1 for c in choices if c is not None and c < current), current)

    labels = [_label(c) + (_CURRENT_SUFFIX if c == current else "") for c in choices]
    choice = select_from_list(
        lcd, crumbs(MENU_TITLE, MENU_SETTINGS, SETTINGS_BACKLIGHT), labels,
        start_index=choices.index(current), formatter=str,
    )
    if choice == KEY_QUIT:
        return KEY_QUIT
    if choice == KEY_BACK:
        return None

    seconds = choices[labels.index(choice)]
    config_service.set_backlight_timeout_seconds(seconds)
    lcd.set_idle_seconds(seconds)
    lcd.write(STATEMENT_SAVED, f"Backlight: {_label(seconds)}")
    time.sleep(1)
    return None


def _seconds_label(seconds: float) -> str:
    return f"{seconds:g}s"


def _sweep_speed(lcd: LCD) -> str | None:
    current = config_service.get_sweep_gap_seconds()
    choices = list(SWEEP_GAP_CHOICES)
    if current not in choices:
        # Hand-edited into settings.json: keep it choosable, slowest first.
        choices = sorted(choices + [current], reverse=True)

    labels = [_seconds_label(c) + (_CURRENT_SUFFIX if c == current else "") for c in choices]
    choice = select_from_list(
        lcd, crumbs(MENU_TITLE, MENU_SETTINGS, SETTINGS_SWEEP_SPEED), labels,
        start_index=choices.index(current), formatter=str,
    )
    if choice == KEY_QUIT:
        return KEY_QUIT
    if choice == KEY_BACK:
        return None

    seconds = choices[labels.index(choice)]
    config_service.set_sweep_gap_seconds(seconds)
    lcd.write(STATEMENT_SAVED, f"Sweep: {_seconds_label(seconds)}")
    time.sleep(1)
    return None
