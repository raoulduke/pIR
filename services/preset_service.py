"""Keypad presets — run multiple IR commands in sequence from one button.

config/keypad_presets.json maps preset number ("1".."4") to an ordered list
of steps. An IR step:
  {"home": "home", "device": "tv", "location": "bedroom", "button": "power",
   "delay_after": 0.3}

"location" and "delay_after" are optional (default "" and
PRESET_DEFAULT_DELAY_SECONDS). Presets start out empty — assign steps by
editing the file directly. Missing signal files are skipped rather than
aborting the rest of the sequence.

A voice step sends free-text through Home Assistant
(services/voice_service.py) instead of an IR signal:
  {"type": "voice", "command": "Rest", "delay_after": 0.3}

An androidtv step sends an Android keyevent over adb
(services/androidtv_service.py) — for Google TV functions IR can't reach
(Home, true Back distinct from Menu):
  {"type": "androidtv", "keyevent": "KEYCODE_HOME", "delay_after": 0.3}
"""

import json
import time
from pathlib import Path

from app.constants import KEYPAD_PRESETS_FILE, PRESET_DEFAULT_DELAY_SECONDS
from app.exceptions import HardwareError
from hardware import ir_transmitter
from services import signal_service, voice_service, androidtv_service

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

    for i, step in enumerate(steps):
        if step.get("type") == "voice":
            success, _ = voice_service.send_command(step["command"])
            if success:
                sent += 1
        elif step.get("type") == "androidtv":
            success, _ = androidtv_service.send_keyevent(step["keyevent"])
            if success:
                sent += 1
        else:
            path = signal_service.signal_path(
                step["home"], step["device"], step["button"], step.get("location", "")
            )
            if path.exists():
                try:
                    ir_transmitter.transmit(path)
                    sent += 1
                except HardwareError:
                    pass

        # No delay needed after the last step — nothing follows it that
        # could be disrupted by firing signals too close together.
        if i < len(steps) - 1:
            time.sleep(step.get("delay_after", PRESET_DEFAULT_DELAY_SECONDS))

    return sent
