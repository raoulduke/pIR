"""Keypad device-slot config.

config/keypad_devices.json is a flat list of configured devices. The keypad
has 4 physical up/down pairs (device 1-4), each bound by position to one
entry in this list — device_slot_offset (see services/config_service.py)
is the index of the first entry currently occupying slot 1. It's fixed at
0 today; "< Device" is Send-screen-scoped now (see screens/send_screen.py),
not a window shift over this list.

Each entry is an IR slot by default:
  {
    "label": "Bedroom TV",       # shown on the LCD
    "home": "home",
    "device": "tv",
    "location": "bedroom",       # "" if buttons live directly under device/
    "up_button": "volume_up",    # signal stem fired by DEVICE_n_UP
    "down_button": "volume_down" # signal stem fired by DEVICE_n_DOWN
  }

Setting "type": "voice" sends free-text Home Assistant voice commands
(services/voice_service.py) instead of an IR signal:
  {
    "label": "Google Home",
    "type": "voice",
    "up_command": "Turn living room lights up",
    "down_command": "Turn living room lights down"
  }
"""

import json
from pathlib import Path

from app.constants import KEYPAD_DEVICES_FILE

_PATH = Path(__file__).parent.parent / KEYPAD_DEVICES_FILE


def _load() -> list[dict]:
    if _PATH.exists():
        try:
            return json.loads(_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return []


def get_slot(offset: int, index: int) -> dict | None:
    """The device currently occupying window position *index* (0-based)."""
    devices = _load()
    if not devices:
        return None
    return devices[(offset + index) % len(devices)]
