#!/usr/bin/env python3
"""Standalone IR signal capture helper.

Captures one or more named buttons for a device (and optional location)
without going through the app's LCD Menu → Learn flow each time — run it
directly from the terminal, repeatedly, for troubleshooting bad captures.

Run this ON THE PI, with pihome.service STOPPED first (it also uses the IR
receiver and LCD — running both at once will fight over the hardware):

    sudo systemctl stop pihome.service
    sudo .venv/bin/python3 scripts/capture_signals.py google_tv up down left right enter
    sudo .venv/bin/python3 scripts/capture_signals.py light brighten_up brighten_down -l hall
    sudo systemctl start pihome.service

For each button: prompts on the terminal, then listens on the IR receiver
the same way screens/receive_screen.py does (hardware.ir_receiver.capture),
mirroring progress on the physical LCD too. Shows a preview and a rough
line/pulse count before saving, and flags captures that look like they may
have picked up noise (unusually many lines) — you can retry or keep it
anyway. An existing signal for the same button is backed up to
<button>.txt.bak, not silently overwritten.
"""

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# A single clean button press (main frame + a few repeat frames, per the
# NEC-style signals already in signals/) is typically well under this many
# lines. More than that is a hint the capture caught noise or an accidental
# double-press — not a hard failure, just a prompt to look twice.
_SUSPICIOUS_LINE_COUNT = 8


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture one or more IR signals for a device without "
                     "the app's LCD menu.",
    )
    parser.add_argument("device", help="device folder name, e.g. google_tv")
    parser.add_argument("buttons", nargs="+", help="button names to capture, e.g. up down left")
    parser.add_argument("-l", "--location", default="", help="location subfolder, e.g. hall (omit if the device has none)")
    parser.add_argument("--force", action="store_true", help="proceed even if pihome.service looks like it's running")
    return parser.parse_args()


def _service_is_running() -> bool:
    result = subprocess.run(["systemctl", "is-active", "--quiet", "pihome.service"])
    return result.returncode == 0


# Handle -h/--help and argument errors before any hardware-touching imports
# below run (importing hardware.lcd, for example, immediately opens the I2C
# bus) — same reasoning as main.py.
_args = _parse_args()

if _service_is_running() and not _args.force:
    print("pihome.service is currently running — stop it first "
          "(it uses the same IR receiver/LCD) or pass --force:")
    print("  sudo systemctl stop pihome.service")
    sys.exit(1)

from hardware import ir_receiver
from hardware.lcd import lcd
from services import signal_service, home_service
from app.constants import MIN_SIGNAL_LENGTH


def _capture_one(home: str, device: str, location: str, button: str) -> None:
    path = signal_service.signal_path(home, device, button, location)

    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        backup.write_text(path.read_text())
        print(f"  existing {path.name} backed up to {backup.name}")

    while True:
        input(f"\n>>> Aim the remote at the receiver, then press ENTER here "
              f"and immediately press '{button}' on the remote...")
        lcd.write("Receiving...", "Press button now")
        data = ir_receiver.capture(lcd)

        if not data or len(data) < MIN_SIGNAL_LENGTH:
            print(f"  no signal captured — retry '{button}'")
            continue

        lines = data.splitlines()
        pulse_count = sum(len(l.split()) for l in lines)
        print(f"  captured {len(lines)} line(s), {pulse_count} pulses total")
        print(f"  preview: {lines[0][:80]}")

        if len(lines) > _SUSPICIOUS_LINE_COUNT:
            print(f"  ! more lines than a clean single press usually has "
                  f"({_SUSPICIOUS_LINE_COUNT}+) — may be noise or a double-press")

        answer = input("  keep this capture? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            signal_service.save_signal(data, home, device, button, location)
            print(f"  saved {path}")
            return
        print(f"  discarded — retry '{button}'")


def main() -> None:
    home = home_service.get_active_home()
    print(f"Home: {home}  Device: {_args.device}  Location: {_args.location or '(none)'}")
    print(f"Buttons to capture: {', '.join(_args.buttons)}")

    try:
        for button in _args.buttons:
            _capture_one(home, _args.device, _args.location, button)
    except KeyboardInterrupt:
        print("\nInterrupted — anything already saved is kept.")
        sys.exit(1)

    print("\nDone. Restart the service when ready:")
    print("  sudo systemctl start pihome.service")


if __name__ == "__main__":
    main()
