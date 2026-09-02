"""Step-sequence devices for Active Device Control.

config/step_devices.json maps a device name to an ordered list of
{"name", "steps"} entries — "name" is what's shown on the LCD, "steps" is
one or more steps fired in sequence when it's selected, via
services/preset_service.py's run_steps() — the exact same step-execution
engine and vocabulary keypad presets use (voice/androidtv/androidtv_app/
IR/hue — see that module's docstring). Despite the name's history (this
file was "voice_device_service.py" until renamed — a device here isn't
limited to voice at all: "Rust" fires a voice command *and* a follow-up
IR signal, and e.g. "bedroom_light" fires a hue step with no voice
involved). "name" doesn't have to match what any step actually does —
see config/step_devices.json's "Soften kitchen" entry, whose name never
appears in either of its two real (voice) steps.

screens/active_device_screen.py cycles through a device's entries with
Left/Right exactly like an IR device's functions (see
services/signal_service.py's list_functions). An entry with "steps" is
standalone (fired by Enter only); an entry with "up_steps"/"down_steps"
instead of "steps" is paired (fired by Up/Down, one step sequence each) —
see config/step_devices.json's kitchen light "Brightness" entries for a
hue bri_inc pair.

A step device still needs an actual (if empty) directory under
signals/<home>/<device>/ — a device is just a directory name to
services/signal_service.py's list_leaf_devices()/cycle_leaf_device(), so
that's what makes it show up as a cyclable leaf via < Device/Device >
alongside every IR device, with no changes needed there.
"""

import json
from pathlib import Path

_PATH = Path(__file__).parent.parent / "config" / "step_devices.json"


def _load() -> dict:
    if _PATH.exists():
        try:
            return json.loads(_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def is_step_device(device: str) -> bool:
    return device in _load()


def list_functions(device: str) -> list[dict]:
    """Same shape as signal_service.list_functions() — a list of {"name",
    "type", ...} dicts. A config entry with "steps" becomes "type": "step"
    (fired by Enter, via services/preset_service.py's run_steps()); one
    with "up_steps"/"down_steps" becomes "type": "step_paired" (Up fires
    up_steps, Down fires down_steps, same run_steps() engine for each) —
    the step-sequence equivalent of an IR device's paired up/down
    functions (see services/signal_service.py's list_functions)."""
    functions = []
    for entry in _load().get(device, []):
        if "up_steps" in entry or "down_steps" in entry:
            functions.append({
                "name": entry["name"], "type": "step_paired",
                "up_steps": entry.get("up_steps", []),
                "down_steps": entry.get("down_steps", []),
            })
        else:
            functions.append({"name": entry["name"], "type": "step", "steps": entry["steps"]})
    return functions
