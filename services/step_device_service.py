"""Step devices for Active Device Control: devices whose functions are
step sequences (preset_service's vocabulary — voice, Google TV, Hue, IR)
instead of learned IR files.

config/step_devices.json maps "device" or "device/location" to a list of
functions: {"name", "steps"} fired by Enter, or {"name", "up_steps",
"down_steps"} fired by Up/Down. Left/Right cycles them like IR functions.
The device still needs a (possibly empty) directory under
signals/<home>/<device>[/<location>]/ to appear as a leaf."""

from pathlib import Path

from app.files import read_json_cached

_PATH = Path(__file__).parent.parent / "config" / "step_devices.json"


def _load() -> dict:
    return read_json_cached(_PATH, {})


def _device_key(device: str, location: str) -> str:
    return f"{device}/{location}" if location else device


def is_step_device(device: str, location: str = "") -> bool:
    return _device_key(device, location) in _load()


def list_functions(device: str, location: str = "") -> list[dict]:
    """Functions in signal_service.list_functions()' shape: "step" (Enter
    runs "steps") or "step_paired" (Up/Down run "up_steps"/"down_steps")."""
    functions = []
    for entry in _load().get(_device_key(device, location), []):
        if "up_steps" in entry or "down_steps" in entry:
            functions.append({
                "name": entry["name"], "type": "step_paired",
                "up_steps": entry.get("up_steps", []),
                "down_steps": entry.get("down_steps", []),
            })
        else:
            functions.append({"name": entry["name"], "type": "step", "steps": entry["steps"]})
    return functions
