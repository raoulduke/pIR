#!/usr/bin/env python3
"""Pi Home Control — entry point.

Startup → Active Device Control (main loop) → shutdown. Everything else
(Learn, voice command, Home switcher, WiFi) is reached via the physical
Menu key from anywhere — see docs/active-device-control-plan.md.
"""

import logging
import os
import signal
import sys

# Device failures (IR TX/RX, voice, Hue, Android TV) log here as well as
# the LCD — the LCD message is gone the moment the screen redraws, the
# journal (`journalctl -u pihome.service`) keeps it.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

print(f"[BOOT] PID={os.getpid()}")

from app.startup import initialize
from app import quick_actions
from hardware.lcd import lcd
from hardware.input import set_quick_action_handler, set_key_observer, close as close_input
from services import wifi_wake_service
import screens.active_device_screen as active_device_screen


def main() -> None:
    print("[BOOT] starting")

    state = initialize()
    if state is None:
        sys.exit(0)

    quick_actions.init(state)
    set_quick_action_handler(quick_actions.handle)
    # Speculative Wi-Fi radio wake on every keypress — see
    # services/wifi_wake_service.py's docstring for why.
    set_key_observer(wifi_wake_service.wake)

    def _handle_signal(signum, frame):
        print(f"[BOOT] signal {signum}")
        state.running = False
        # read_key() blocks in a plain select() with no timeout (see
        # hardware/input.py). Per PEP 475, a handler that returns normally
        # just causes that interrupted select() to be retried, so Ctrl+C
        # would otherwise be swallowed until the next keypress. Raising here
        # actually breaks out of the blocked call.
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    try:
        # Boot straight into Active Device Control (the most-used screen)
        # — no menu detour on startup. It owns the main loop until
        # state.running goes False; everything else (Learn, Command, Home)
        # is reached via the Menu key (app/quick_actions.py), not through
        # this loop.
        active_device_screen.show(lcd, state)

        lcd.write("Program stopped")

    except KeyboardInterrupt:
        print("[BOOT] interrupted")
        try:
            lcd.write("Program stopped")
        except Exception:
            pass

    except Exception as e:
        print(f"[BOOT] fatal error: {e!r}")
        import traceback
        traceback.print_exc()
        try:
            lcd.write(str(e)[:16], "Program stopped")
        except Exception:
            pass

    finally:
        close_input()
        sys.exit(0)


if __name__ == "__main__":
    main()
