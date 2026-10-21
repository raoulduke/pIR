#!/usr/bin/env python3
"""Pi Home Control — entry point.

Startup, then Active Device Control (the main loop) until stopped. Every
other screen is reached through the Menu key (app/quick_actions.py).
"""

import _thread
import logging
import os
import signal
import sys
import threading

# Failures also go to the journal (journalctl -u pihome.service): the LCD
# message is gone at the next redraw.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

print(f"[BOOT] PID={os.getpid()}")

from app.startup import initialize
from app import quick_actions
from app.constants import ANDROIDTV_STATUS_CHARS, STATEMENT_SHUTTING_DOWN
from hardware.lcd import lcd
from hardware.input import (
    set_quick_action_handler, set_key_observer, set_wake_handler, request_quit,
    close as close_input,
)
from services import androidtv_service, config_service, wifi_wake_service
import screens.active_device_screen as active_device_screen


def _powering_off() -> bool:
    """True if this exit is part of an OS power-off/halt rather than a
    restart, reboot or plain stop — the only case where
    systemd/system-shutdown/shutdown-signal.sh darkens the screen."""
    import subprocess
    try:
        jobs = subprocess.run(
            ["systemctl", "list-jobs", "--no-legend", "--plain"],
            capture_output=True, text=True, timeout=2,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "poweroff.target" in jobs or "halt.target" in jobs


# Seconds a requested stop may take to unwind through the screens before
# main.py falls back to raising KeyboardInterrupt (pihome.service's
# TimeoutStopSec is 5).
_QUIT_GRACE_SECONDS = 3


def _write_exit_message() -> None:
    """The exit screen: "Shutting down..." during a power-off (the system
    shutdown hook darkens it at the last moment), else "Program stopped"."""
    try:
        lcd.write(STATEMENT_SHUTTING_DOWN if _powering_off() else "Program stopped")
    except Exception:
        pass


def main() -> None:
    print("[BOOT] starting")

    state = initialize()
    if state is None:
        sys.exit(1)  # nonzero, so systemd's Restart=on-failure retries

    quick_actions.init(state)
    set_quick_action_handler(quick_actions.handle)
    set_key_observer(wifi_wake_service.wake)  # wake the Wi-Fi radio early
    # Any key relights a dark backlight; the press still does its usual job.
    set_wake_handler(lambda key: lcd.wake())
    # Google TV's connection is held open on its screen only while lit.
    lcd.set_backlight_listener(androidtv_service.set_screen_lit)
    lcd.set_idle_seconds(config_service.get_backlight_timeout_seconds())
    # Google TV screen's connection status, line 1's last column.
    lcd.set_status_char(ANDROIDTV_STATUS_CHARS["closed"])
    androidtv_service.set_status_listener(
        lambda status: lcd.set_status_char(ANDROIDTV_STATUS_CHARS[status])
    )
    # The plause pair replaces YT on Google TV's preset line in YouTube.
    androidtv_service.set_app_listener(active_device_screen.update_play_glyph)

    stopping = False
    quit_timer: threading.Timer | None = None

    def _handle_signal(signum, frame):
        # First signal: stop politely — every read_key() returns KEY_QUIT, so
        # the screens unwind normally rather than an exception landing in the
        # middle of an LCD write. If that hasn't finished in
        # _QUIT_GRACE_SECONDS (e.g. stuck in a long nmcli call), or on a
        # second signal, raise KeyboardInterrupt as before.
        nonlocal stopping, quit_timer
        print(f"[BOOT] signal {signum}")
        state.running = False
        if stopping:
            raise KeyboardInterrupt
        stopping = True
        request_quit()
        quit_timer = threading.Timer(_QUIT_GRACE_SECONDS, _thread.interrupt_main)
        quit_timer.daemon = True
        quit_timer.start()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    # Nonzero after an error, so pihome.service's Restart=on-failure brings
    # the app back; 0 for a requested stop (signal, quit), which shouldn't.
    exit_code = 0
    try:
        active_device_screen.show(lcd, state)  # runs until state.running is False
        _write_exit_message()

    except KeyboardInterrupt:
        print("[BOOT] interrupted")
        _write_exit_message()

    except Exception as e:
        exit_code = 1
        print(f"[BOOT] fatal error: {e!r}")
        import traceback
        traceback.print_exc()
        try:
            lcd.write(str(e)[:16], "Program stopped")
        except Exception:
            pass

    finally:
        if quit_timer is not None:
            quit_timer.cancel()  # unwound in time; don't interrupt cleanup
        try:
            lcd.stop_idle_timer()  # keep the exit message readable
        except Exception:
            pass
        close_input()
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
