"""Keypad device slots: config/keypad_devices.json is a list of devices,
and the keypad's 4 up/down pairs map to entries device_slot_offset onward
(the offset is 0).

An IR slot:
  {
    "label": "Bedroom TV",       # shown on the LCD
    "home": "home",
    "device": "tv",
    "location": "bedroom",       # "" if signals are directly under the device
    "up_button": "volume_up",    # signal fired by DEVICE_n_UP
    "down_button": "volume_down" # signal fired by DEVICE_n_DOWN
  }
"smooth": true repeats the signal while held (quick_actions._stream_device()).
A voice slot sends Home Assistant commands instead:
  {
    "label": "Google Home",
    "type": "voice",
    "up_command": "Turn living room lights up",
    "down_command": "Turn living room lights down"
  }"""

from pathlib import Path

from app.files import read_json_cached

from app.constants import KEYPAD_DEVICES_FILE

_PATH = Path(__file__).parent.parent / KEYPAD_DEVICES_FILE


def _load() -> list[dict]:
    return read_json_cached(_PATH, [])


def get_slot(offset: int, index: int) -> dict | None:
    """The device currently occupying window position *index* (0-based)."""
    devices = _load()
    if not devices:
        return None
    return devices[(offset + index) % len(devices)]
