import json
from pathlib import Path

from app.exceptions import ConfigError
from app.files import write_text_atomic
from services import config_service, signal_service

_CONFIG_DIR = Path(__file__).parent.parent / "config"

# Every config file (besides device_order.json, handled separately by
# signal_service.rename_order_keys() since it's keyed *by* home name
# rather than referencing one from inside a step) whose entries can carry
# a {"home": "<name>", ...} field naming which home a step/slot belongs
# to — keypad device slots, Preset Function steps (including nested
# "by_device" overrides), and voice/Hue/IR preset steps respectively.
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
    """Rename a home: its signals/ directory, plus every config file that
    references it by name. A home is referenced in more places than just
    signals/ — config/settings.json's active_home (if this is the active
    home), config/device_order.json's home-prefixed ordering keys, and
    the "home" field on any keypad-device slot or preset step in
    _STEP_REFERENCING_FILES. Raises if *new* already exists (see
    signal_service.rename_home()) — the caller is expected to have
    checked home_exists(new) first for a friendlier message, this is the
    last-resort guard against silently merging two homes together.
    Missing/unparsable config files are skipped rather than raising — a
    fresh home with none of these configured yet is normal, not an
    error."""
    if signal_service.home_exists(new):
        raise ConfigError(f"'{new}' already exists")

    signal_service.rename_home(old, new)
    signal_service.rename_order_keys(old, new)
    config_service.rename_active_home_if_matches(old, new)
    for filename in _STEP_REFERENCING_FILES:
        _rewrite_home_refs_in_file(_CONFIG_DIR / filename, old, new)


def _rewrite_home_refs_in_file(path: Path, old: str, new: str) -> None:
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if _rename_home_in_place(data, old, new):
        write_text_atomic(path, json.dumps(data, indent=2))


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


# Config dicts keyed by "device" or "device/location" (not by home) —
# config/keypad_presets.json's "by_device" overrides, and
# config/step_devices.json's top level. (filename, path to the dict).
_DEVICE_KEYED_DICTS = [("keypad_presets.json", ["by_device"]), ("step_devices.json", [])]


def rename_device(home: str, old: str, new: str) -> None:
    """Rename a device within *home*: its signals/<home>/<device>
    directory, its config/device_order.json entries, the "device" field
    on any step/slot in _STEP_REFERENCING_FILES that also names *home*,
    and any _DEVICE_KEYED_DICTS key for it ("old" or "old/<location>").

    Those device-keyed dicts aren't scoped by home, so if another home
    still has a device named *old* its keys are copied to *new* rather
    than moved — renaming one home's device shouldn't strip the other's
    presets. An existing key for *new* is never overwritten. Raises if
    *new* already exists in *home* (same last-resort guard as
    rename_home())."""
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
    write it back if so. Missing/unparsable files are skipped, same as
    _rewrite_home_refs_in_file()."""
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
