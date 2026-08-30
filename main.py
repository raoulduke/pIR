#!/usr/bin/env python3
"""Pi Home Control — entry point.

Startup → Active Device Control (main loop) → shutdown. Everything else
(full device list, Learn, voice command, Home switcher, Settings) is
reached via the physical Menu key from anywhere — see
docs/active-device-control-plan.md.
"""

import argparse
import os
import signal
import sys
import traceback

HELP_DESCRIPTION = """\
Pi Home Control — DIY IR remote control and home automation device.

Runs headless on a Raspberry Pi Zero 2 W, battery-powered. Boots directly
into Active Device Control (no menu detour) — D-pad passthrough for the
active device (google_tv by default) or a cycled function list for any
other device — and handles IR transmit/receive, LCD status display,
physical keypad/button input, and Home Assistant integration (including
voice commands).
"""

HELP_EPILOG = """\
what it does:
  - Controls the active device directly: raw D-pad passthrough for
    google_tv, or Left/Right-cycled functions (Up/Down/Enter to fire) for
    everything else — derived from saved signals (per-"home", e.g.
    TV/AC/light/receiver), no separate config to author
  - Full device list, Learn (IR capture), voice command, and Home switcher
    all live behind the physical Menu key, reachable from any screen
  - Receives and learns new IR signals via the VS1838 IR receiver
  - Shows status/menus on a 16x2 I2C LCD
  - Reads input from a 12-wire keypad matrix (evdev)
  - Sends free-text voice/command strings to Home Assistant's Google
    Assistant SDK endpoint for smart-home voice control
  - Cleanly hands off to a physical shutdown button (GPIO3, gpio-shutdown)

services it connects to:
  - Home Assistant (ha_url / google_sdk_path in config/settings.json),
    authenticated with a bearer token read from ha_token_file. Only used
    when a voice/HA command is issued; IR send/receive works fully offline.

what needs to be set up / running for it to work properly:
  - Root privileges (GPIO/I2C access) — run via the venv, never bare python3:
        sudo .venv/bin/python3 main.py
  - A populated venv at ./.venv, built ON THE PI (armhf) — see requirements.txt
  - config/settings.json with an active home set, and (for voice/HA control)
    ha_url, google_sdk_path, and ha_token_file pointing to a valid token file
  - At least one evdev input device available (keypad); startup aborts to
    the LCD with an error if none is found
  - IR receiver/transmitter and LCD wired per the pin table in README.md
  - /boot/firmware/config.txt configured per boot/config.txt (software I2C
    on GPIO23/24, i2c_arm=off to free GPIO3 for gpio-shutdown, gpu_mem=16)
  - Wi-Fi power management disabled (needed for HA calls / IR timing)

exit behavior:
  - Clean exit or SIGTERM/SIGINT: LCD line 1 shows "Program stopped"
  - Unhandled error: LCD line 1 shows the error, line 2 "Program stopped"
  - The LCD is never cleared on exit, so the last status stays visible

See CLAUDE.md / README.md in the project root for full hardware/software
details.
"""


def _parse_args() -> None:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description=HELP_DESCRIPTION,
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.parse_args()


# Handle -h/--help before any hardware-touching imports below run (importing
# hardware.lcd, for example, immediately opens the I2C bus).
_parse_args()

print(f"[BOOT] PID={os.getpid()}")

from app.startup import initialize
from app import quick_actions
from hardware.lcd import lcd
from hardware.input import set_quick_action_handler, close as close_input
import screens.active_device_screen as active_device_screen


def main() -> None:
    print("[BOOT] starting")

    state = initialize()
    if state is None:
        sys.exit(0)

    quick_actions.init(state)
    set_quick_action_handler(quick_actions.handle)

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
        # state.running goes False; everything else (Full Device List,
        # Learn, Command, Home, Settings) is reached via the Menu key
        # (app/quick_actions.py), not through this loop.
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
