"""Voice-command devices for Active Device Control.

config/voice_devices.json maps a device name to an ordered list of
{"name", "steps"} entries — "name" is what's shown on the LCD, "steps" is
one or more steps fired in sequence when it's selected, via
services/preset_service.py's run_steps() — the exact same step-execution
engine and vocabulary keypad presets use (voice/androidtv/androidtv_app/
IR — see that module's docstring), so a "voice device" function isn't
actually limited to voice: "Rust" fires a voice command *and* a follow-up
IR signal. "name" doesn't have to match what any step actually does —
see config/voice_devices.json's "Soften kitchen" entry, whose name never
appears in either of its two real (voice) steps.

screens/active_device_screen.py cycles through a device's entries with
Left/Right exactly like an IR device's functions (see
services/signal_service.py's list_functions) — every entry standalone
(fired by Enter only; no up/down pairing concept for a step sequence).

A voice device still needs an actual (if empty) directory under
signals/<home>/<device>/ — a device is just a directory name to
services/signal_service.py's list_leaf_devices()/cycle_leaf_device(), so
that's what makes it show up as a cyclable leaf via < Device/Device >
alongside every IR device, with no changes needed there.
"""

import json
from pathlib import Path

_PATH = Path(__file__).parent.parent / "config" / "voice_devices.json"


def _load() -> dict:
    if _PATH.exists():
        try:
            return json.loads(_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def is_voice_device(device: str) -> bool:
    return device in _load()


def list_functions(device: str) -> list[dict]:
    """Same shape as signal_service.list_functions() — a list of {"name",
    "type", ...} dicts — but "type": "voice" with a "steps" key (a list,
    fired in order via services/preset_service.py's run_steps()) instead
    of IR button fields. Every entry is standalone (fired by Enter): a
    step sequence has no up/down pairing concept."""
    return [
        {"name": entry["name"], "type": "voice", "steps": entry["steps"]}
        for entry in _load().get(device, [])
    ]
