"""Keypad presets — run multiple IR commands in sequence from one button.

config/keypad_presets.json maps preset number ("1".."4") to an ordered list
of steps:
  {"home": "home", "device": "tv", "location": "bedroom", "button": "power",
   "delay_after": 0.3}

"location" and "delay_after" are optional (default "" and
PRESET_DEFAULT_DELAY_SECONDS). Presets start out empty — assign steps by
editing the file directly. Missing signal files are skipped rather than
aborting the rest of the sequence.
"""

import json
import time
from pathlib import Path

from app.constants import KEYPAD_PRESETS_FILE, PRESET_DEFAULT_DELAY_SECONDS
from app.exceptions import HardwareError
from hardware import ir_transmitter
from services import signal_service

_PATH = Path(__file__).parent.parent / KEYPAD_PRESETS_FILE


def _load() -> dict:
    if _PATH.exists():
        try:
            return json.loads(_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def get_preset(number: int) -> list[dict]:
    return _load().get(str(number), [])


def run_preset(number: int) -> int:
    """Fire every step of preset *number* in order. Returns steps actually sent."""
    steps = get_preset(number)
    sent = 0

    for step in steps:
        path = signal_service.signal_path(
            step["home"], step["device"], step["button"], step.get("location", "")
        )
        if path.exists():
            try:
                ir_transmitter.transmit(path)
                sent += 1
            except HardwareError:
                pass

        time.sleep(step.get("delay_after", PRESET_DEFAULT_DELAY_SECONDS))

    return sent
