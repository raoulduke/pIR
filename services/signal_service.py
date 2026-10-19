"""Signal files: everything under signals/<home>/<device>[/<location>]/<button>.txt."""

import json
from pathlib import Path
import os
import pwd

from app.constants import SIGNALS_DIR, FILE_OWNER, DEFAULT_ACTIVE_DEVICE, DEVICE_ORDER_FILE
from app.files import write_text_atomic

_BASE = Path(__file__).parent.parent / SIGNALS_DIR
_ORDER_PATH = Path(__file__).parent.parent / DEVICE_ORDER_FILE


def home_dir(home: str) -> Path:
    return _BASE / home


def home_exists(home: str) -> bool:
    return home_dir(home).is_dir()


def list_homes() -> list[str]:
    if not _BASE.is_dir():
        return []
    return sorted(d.name for d in _BASE.iterdir() if d.is_dir())


def create_home(home: str) -> None:
    """Create an empty home directory, owned by matt (the app runs as root)."""
    d = home_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    _chown_to_file_owner(d)


def rename_home(old: str, new: str) -> None:
    """Rename a home's directory. The caller checks *new* doesn't exist
    (a rename would merge into it, or fail)."""
    old_dir = home_dir(old)
    new_dir = home_dir(new)
    old_dir.rename(new_dir)
    _chown_to_file_owner(new_dir)


def rename_order_keys(old: str, new: str) -> None:
    """Rename *old*'s keys in config/device_order.json ("<old>" and every
    "<old>/<device>") to *new*."""
    if not _ORDER_PATH.exists():
        return
    try:
        data = json.loads(_ORDER_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return

    renamed = {}
    changed = False
    for key, value in data.items():
        if key == old:
            renamed[new] = value
            changed = True
        elif key.startswith(f"{old}/"):
            renamed[new + key[len(old):]] = value
            changed = True
        else:
            renamed[key] = value

    if changed:
        write_text_atomic(_ORDER_PATH, json.dumps(renamed, indent=2))


def rename_device(home: str, old: str, new: str) -> None:
    """Rename a device's directory. The caller checks *new* doesn't exist."""
    old_dir = home_dir(home) / old
    new_dir = home_dir(home) / new
    old_dir.rename(new_dir)
    _chown_to_file_owner(new_dir)


def rename_device_order(home: str, old: str, new: str) -> None:
    """Rename a device in config/device_order.json: its entry in the home's
    list (keeping its place) and its "<home>/<old>" key."""
    if not _ORDER_PATH.exists():
        return
    try:
        data = json.loads(_ORDER_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return

    changed = False
    devices = data.get(home)
    if isinstance(devices, list) and old in devices:
        data[home] = [new if d == old else d for d in devices]
        changed = True

    old_key, new_key = f"{home}/{old}", f"{home}/{new}"
    if old_key in data:
        data = {(new_key if k == old_key else k): v for k, v in data.items()}
        changed = True

    if changed:
        write_text_atomic(_ORDER_PATH, json.dumps(data, indent=2))


def _explicit_order(key: str) -> list[str]:
    """config/device_order.json's order for *key* (a home, or
    "<home>/<device>"), or [] if none."""
    if not _ORDER_PATH.exists():
        return []
    try:
        data = json.loads(_ORDER_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return data.get(key, [])


def list_devices(home: str) -> list[str]:
    """Device directories under *home*: config/device_order.json's order
    first, then the rest alphabetically."""
    d = home_dir(home)
    if not d.is_dir():
        return []
    names = {p.name for p in d.iterdir() if p.is_dir()}

    order = [name for name in _explicit_order(home) if name in names]
    remaining = sorted(names - set(order))
    return order + remaining


def list_leaf_devices(home: str) -> list[tuple[str, str]]:
    """Every (device, location) leaf under *home*: one per location, or
    (device, "") for a device with signals directly under it."""
    leaves: list[tuple[str, str]] = []
    for device in list_devices(home):
        locations = list_locations(home, device)
        if locations:
            leaves.extend((device, location) for location in locations)
        else:
            leaves.append((device, ""))
    return leaves


def default_leaf_device(home: str) -> tuple[str, str]:
    """The leaf Active Device Control starts on: DEFAULT_ACTIVE_DEVICE if
    this home has it, else the first leaf; ("", "") if there are none."""
    leaves = list_leaf_devices(home)
    if not leaves:
        return "", ""
    for device, location in leaves:
        if device == DEFAULT_ACTIVE_DEVICE:
            return device, location
    return leaves[0]


def list_locations(home: str, device: str) -> list[str]:
    """Location directories under *device*: config/device_order.json's
    "<home>/<device>" order first, then the rest alphabetically."""
    d = home_dir(home) / device
    if not d.is_dir():
        return []
    names = {p.name for p in d.iterdir() if p.is_dir()}

    order = [name for name in _explicit_order(f"{home}/{device}") if name in names]
    remaining = sorted(names - set(order))
    return order + remaining


def list_buttons(home: str, device: str, location: str = "") -> list[str]:
    d = home_dir(home) / device
    if location:
        d = d / location
    if not d.is_dir():
        return []
    # Only visible .txt files: not .keep, or the hidden temp file an
    # interrupted atomic write can leave behind.
    return sorted(f.stem for f in d.iterdir()
                  if f.is_file() and f.suffix == ".txt" and not f.name.startswith("."))


def list_functions(home: str, device: str, location: str = "") -> list[dict]:
    """A device's functions, derived from its signal files: "x_up"/"x_down"
    become one "paired" function (Up/Down), anything else a "standalone"
    one (Enter). Alphabetical. Dicts of {"name", "type", "up", "down",
    "button"}, with the fields that don't apply set to None."""
    buttons = list_buttons(home, device, location)
    paired: dict[str, dict[str, str]] = {}
    standalone: list[str] = []

    for name in buttons:
        if name.endswith("_up"):
            paired.setdefault(name[:-3], {})["up"] = name
        elif name.endswith("_down"):
            paired.setdefault(name[:-5], {})["down"] = name
        else:
            standalone.append(name)

    functions = []
    for stem, sides in paired.items():
        if "up" in sides and "down" in sides:
            functions.append({
                "name": stem, "type": "paired",
                "up": sides["up"], "down": sides["down"], "button": None,
            })
        else:
            # Only one side of the pair exists — nothing to pair with, so
            # treat it as its own standalone button instead of dropping it.
            standalone.append(sides.get("up") or sides.get("down"))

    for name in standalone:
        functions.append({
            "name": name, "type": "standalone",
            "up": None, "down": None, "button": name,
        })

    functions.sort(key=lambda f: f["name"])
    return functions


def signal_path(home: str, device: str, button: str, location: str = "") -> Path:
    d = home_dir(home) / device
    if location:
        d = d / location
    return d / f"{button}.txt"


def signal_exists(home: str, device: str, button: str, location: str = "") -> bool:
    return signal_path(home, device, button, location).exists()


def save_signal(
        data: str,
        home: str,
        device: str,
        button: str,
        location: str = "",
) -> None:
    """Write signal data and fix ownership so the file is accessible as *matt*."""
    path = signal_path(home, device, button, location)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, data)
    _chown_to_file_owner(path)


def rename_signal(
        home: str,
        device: str,
        button: str,
        location: str,
        new_device: str,
        new_button: str,
        new_location: str,
) -> None:
    """Move a signal file to a new device/location/button (fixing a name
    mistyped in Learn), overwriting any file there, and remove directories
    left empty."""
    old_path = signal_path(home, device, button, location)
    new_path = signal_path(home, new_device, new_button, new_location)

    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.rename(new_path)
    _chown_to_file_owner(new_path)

    d = old_path.parent
    while d != _BASE:
        try:
            d.rmdir()
        except OSError:
            break
        d = d.parent


def _chown_to_file_owner(path: Path) -> None:
    """Recursively set ownership up to SIGNALS_DIR to FILE_OWNER."""
    try:
        pw = pwd.getpwnam(FILE_OWNER)
        uid, gid = pw.pw_uid, pw.pw_gid
        for p in [path, *path.parents]:
            if p == _BASE.parent:
                break
            os.chown(p, uid, gid)
    except (KeyError, PermissionError, OSError):
        pass
