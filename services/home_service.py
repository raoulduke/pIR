import json
from pathlib import Path

from app.exceptions import ConfigError
from app.files import write_text_atomic
from services import config_service, signal_service

_CONFIG_DIR = Path(__file__).parent.parent / "config"

# Config files whose steps/slots name a home in a "home" field.
_STEP_REFERENCING_FILES = ["keypad_devices.json", "keypad_presets.json", "step_devices.json"]


def get_active_home() -> str:
    return config_service.get_active_home()


def set_active_home(name: str) -> None:
    config_service.set_active_home(name)


def verify_active_home() -> str:
    """Return the active home name or raise ConfigError."""
    home = get_active_home()
    if not home:
        raise ConfigError("No active home configured")
    if not signal_service.home_exists(home):
        raise ConfigError(f"Home '{home}' directory not found")
    return home


def list_homes() -> list[str]:
    return signal_service.list_homes()


def create_home(name: str) -> None:
    signal_service.create_home(name)


def rename_home(old: str, new: str) -> None:
    """Rename a home everywhere it's named: its signals directory, active_home,
    device_order.json keys, and the "home" field of steps/slots in
    _STEP_REFERENCING_FILES. Raises if *new* exists (the caller checks first
    for a friendlier message). Missing or unreadable config files are skipped."""
    if signal_service.home_exists(new):
        raise ConfigError(f"'{new}' already exists")

    signal_service.rename_home(old, new)
    signal_service.rename_order_keys(old, new)
    config_service.rename_active_home_if_matches(old, new)
    for filename in _STEP_REFERENCING_FILES:
        _rewrite_json_file(
            _CONFIG_DIR / filename, lambda data: _rename_home_in_place(data, old, new),
        )


def _rename_home_in_place(node, old: str, new: str) -> bool:
    """Recursively walk *node* (nested dicts/lists parsed from one of
    _STEP_REFERENCING_FILES) and rename any {"home": old, ...} step's
    "home" field to *new* in place. Returns True if anything changed."""
    changed = False
    if isinstance(node, dict):
        if node.get("home") == old:
            node["home"] = new
            changed = True
        for value in node.values():
            if _rename_home_in_place(value, old, new):
                changed = True
    elif isinstance(node, list):
        for item in node:
            if _rename_home_in_place(item, old, new):
                changed = True
    return changed


# Config dicts keyed by "device" or "device/location": (filename, key path).
_DEVICE_KEYED_DICTS = [("keypad_presets.json", ["by_device"]), ("step_devices.json", [])]


def rename_device(home: str, old: str, new: str) -> None:
    """Rename a device in *home*: its directory, device_order.json entries,
    the "device" field of steps/slots naming *home*, and _DEVICE_KEYED_DICTS
    keys ("old", "old/<location>"). Those keys aren't per home, so if another
    home still has a device called *old* they're copied rather than moved. An
    existing key for *new* is never overwritten. Raises if *new* exists."""
    if new in signal_service.list_devices(home):
        raise ConfigError(f"'{new}' already exists")

    signal_service.rename_device(home, old, new)
    signal_service.rename_device_order(home, old, new)
    for filename in _STEP_REFERENCING_FILES:
        _rewrite_json_file(
            _CONFIG_DIR / filename,
            lambda data: _rename_device_in_place(data, home, old, new),
        )

    keep_old = any(
        old in signal_service.list_devices(h) for h in list_homes() if h != home
    )
    for filename, key_path in _DEVICE_KEYED_DICTS:
        _rewrite_json_file(
            _CONFIG_DIR / filename,
            lambda data: _rename_device_keys(data, key_path, old, new, keep_old),
        )


def _rewrite_json_file(path: Path, mutate) -> None:
    """Load *path*, apply *mutate* (returns True if it changed anything),
    write it back if so. Missing/unparsable files are skipped."""
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if mutate(data):
        write_text_atomic(path, json.dumps(data, indent=2))


def _rename_device_in_place(node, home: str, old: str, new: str) -> bool:
    """Like _rename_home_in_place(), but renames the "device" field of
    any {"home": home, "device": old, ...} step/slot."""
    changed = False
    if isinstance(node, dict):
        if node.get("home") == home and node.get("device") == old:
            node["device"] = new
            changed = True
        for value in node.values():
            if _rename_device_in_place(value, home, old, new):
                changed = True
    elif isinstance(node, list):
        for item in node:
            if _rename_device_in_place(item, home, old, new):
                changed = True
    return changed


def _rename_device_keys(data, key_path: list[str], old: str, new: str, keep_old: bool) -> bool:
    target = data
    for key in key_path:
        if not isinstance(target, dict):
            return False
        target = target.get(key)
    if not isinstance(target, dict):
        return False

    changed = False
    for key in list(target):
        if key == old:
            new_key = new
        elif key.startswith(f"{old}/"):
            new_key = new + key[len(old):]
        else:
            continue
        if new_key in target:
            continue
        target[new_key] = target[key] if not keep_old else json.loads(json.dumps(target[key]))
        if not keep_old:
            del target[key]
        changed = True
    return changed
