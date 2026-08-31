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

An androidtv_app step launches an app by package name over adb — for
functions with no keyevent equivalent (e.g. a YouTube shortcut button):
  {"type": "androidtv_app", "package": "com.google.android.youtube.tv",
   "delay_after": 0.3}

Top-level "1".."4" are the global defaults — what fires when the active
device (screens/active_device_screen.py's state.active_device/location) has
no override for that slot. A top-level "by_device" key adds per-device
overrides, keyed "device" or "device/location" (matching whatever's active
when the button is pressed):
  "by_device": {
    "light/hall": {"1": [{"home": "home", "device": "light",
                           "location": "hall", "button": "on"}]}
  }
A device's override block only needs the slots it wants to change — any
slot it doesn't define still falls through to the matching global default,
so e.g. a device can override Preset Function 1 while 2-4 keep firing
their usual global macros.

run_steps() below is the actual step-execution engine, shared with
services/voice_device_service.py — a voice-command device's Active Device
Control functions (screens/active_device_screen.py) use this exact same
step vocabulary and step-runner, not a separate one, so e.g. a step can
mix a voice command with a follow-up IR signal.
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


def _device_key(device: str, location: str) -> str:
    return f"{device}/{location}" if location else device


def get_preset(number: int, device: str = "", location: str = "") -> list[dict]:
    data = _load()
    slot = str(number)

    if device:
        overrides = data.get("by_device", {}).get(_device_key(device, location), {})
        if slot in overrides:
            return overrides[slot]

    return data.get(slot, [])


def run_steps(steps: list[dict]) -> tuple[int, int]:
    """Fire every step in *steps* in order — the shared voice/androidtv/
    androidtv_app/IR step-execution engine. Returns (steps actually sent,
    steps configured) — the caller needs the second number too, to tell
    "nothing configured" apart from "configured but every step failed"
    (0 sent either way).

    Used by run_preset() below for keypad presets, and by
    services/voice_device_service.py-backed Active Device Control
    functions (screens/active_device_screen.py) — both draw from the
    exact same step vocabulary rather than each having their own."""
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
        elif step.get("type") == "androidtv_app":
            success, _ = androidtv_service.launch_app(step["package"])
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

    return sent, len(steps)


def run_preset(number: int, device: str = "", location: str = "") -> tuple[int, int]:
    """Fire every step of preset *number* in order — the active device's
    override if it has one for this slot, else the global default.
    Returns (steps actually sent, steps configured)."""
    return run_steps(get_preset(number, device, location))
