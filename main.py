#!/usr/bin/env python3
"""Pi Home Control — entry point.

Startup → main menu loop → dispatch to screens → shutdown.
"""

import os
import signal
import sys

print(f"[BOOT] PID={os.getpid()}")

from app.startup import initialize
from app.constants import MENU_SEND, MENU_RECEIVE, MENU_COMMAND, MENU_HOME
from hardware.lcd import lcd
from hardware.input import KEY_QUIT, close as close_input
import screens.main_menu as main_menu
import screens.send_screen as send_screen
import screens.receive_screen as receive_screen
import screens.command_screen as command_screen
import screens.home_screen as home_screen
import screens.shutdown_screen as shutdown_screen


def main() -> None:
    print("[BOOT] starting")

    state = initialize()
    if state is None:
        sys.exit(0)

    def _handle_signal(signum, frame):
        print(f"[BOOT] signal {signum}")
        state.running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        while state.running:
            choice = main_menu.show(lcd, state)

            if choice is None:
                continue

            if choice == KEY_QUIT:
                break

            if choice == MENU_SEND:
                result = send_screen.show(lcd)

            elif choice == MENU_RECEIVE:
                result = receive_screen.show(lcd)

            elif choice == MENU_COMMAND:
                result = command_screen.show(lcd)

            elif choice == MENU_HOME:
                result = home_screen.show(lcd, state)

            else:
                result = None

            if result == KEY_QUIT:
                break

        shutdown_screen.show(lcd, state)

    finally:
        try:
            lcd.write("Init failed")
        except Exception:
            pass
        close_input()
        sys.exit(0)


if __name__ == "__main__":
    main()
