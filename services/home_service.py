import json
from pathlib import Path

from app.exceptions import ConfigError
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
        path.write_text(json.dumps(data, indent=2))


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
