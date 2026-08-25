"""Keypad device-slot config.

config/keypad_devices.json is a flat list of configured devices. The keypad
only has 4 physical up/down pairs (device 1-4), so those buttons control a
sliding window over this list — KEY_DEVICE_NEXT/KEY_DEVICE_PREV (the
"device >" / "< device" keys) shift the window by 4 and wrap around. With
4 or fewer devices configured, the window never moves.

Each entry:
  {
    "label": "Bedroom TV",       # shown on the LCD
    "home": "home",
    "device": "tv",
    "location": "bedroom",       # "" if buttons live directly under device/
    "up_button": "volume_up",    # signal stem fired by DEVICE_n_UP
    "down_button": "volume_down" # signal stem fired by DEVICE_n_DOWN
  }
"""

import json
from pathlib import Path

from app.constants import KEYPAD_DEVICES_FILE, KEYPAD_DEVICE_SLOTS

_PATH = Path(__file__).parent.parent / KEYPAD_DEVICES_FILE


def _load() -> list[dict]:
    if _PATH.exists():
        try:
            return json.loads(_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return []


def slot_count() -> int:
    return len(_load())


def get_window(offset: int, size: int = KEYPAD_DEVICE_SLOTS) -> list[dict | None]:
    """Return *size* device entries starting at *offset*, wrapping around."""
    devices = _load()
    if not devices:
        return [None] * size
    return [devices[(offset + i) % len(devices)] for i in range(size)]


def get_slot(offset: int, index: int) -> dict | None:
    """The device currently occupying window position *index* (0-based)."""
    devices = _load()
    if not devices:
        return None
    return devices[(offset + index) % len(devices)]
