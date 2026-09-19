"""Signal filesystem service.

All reads and writes to the signals/ directory live here.
Hardware and screens never touch the filesystem directly.
"""

import json
from pathlib import Path
import os
import pwd

from app.constants import SIGNALS_DIR, FILE_OWNER, DEFAULT_ACTIVE_DEVICE, DEVICE_ORDER_FILE

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
    """Create an empty home directory and fix ownership so it's accessible
    as *matt* — same pattern as save_signal for files written while running
    as root."""
    d = home_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    _chown_to_file_owner(d)


def rename_home(old: str, new: str) -> None:
    """Rename a home's signals/ directory. Caller (services.home_service)
    is responsible for checking home_exists(new) first if it wants to
    refuse an overwrite — Path.rename() would otherwise silently merge
    *old*'s contents into an existing *new* directory on most platforms,
    or fail outright, depending on whether *new* is empty."""
    old_dir = home_dir(old)
    new_dir = home_dir(new)
    old_dir.rename(new_dir)
    _chown_to_file_owner(new_dir)


def rename_order_keys(old: str, new: str) -> None:
    """Rewrite config/device_order.json's home-prefixed keys (the home's
    own top-level key, e.g. "home", plus every "<home>/<device>" key
    under it, e.g. "home/light") from *old* to *new* — for
    services.home_service.rename_home(). A silently stale key would just
    fall back to alphabetical ordering (see _explicit_order()), not
    break anything outright, but there's no reason to let a rename orphan
    it when fixing it up is this cheap."""
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
        _ORDER_PATH.write_text(json.dumps(renamed, indent=2))


def _explicit_order(key: str) -> list[str]:
    """config/device_order.json's explicit order for *key*, or [] if the
    file/key entry is missing — callers fall back to pure alphabetical
    either way. *key* is a home name for list_devices()'s top-level
    ordering (e.g. "home"), or "<home>/<device>" for list_locations()'s
    per-device ordering (e.g. "home/light" — see there)."""
    if not _ORDER_PATH.exists():
        return []
    try:
        data = json.loads(_ORDER_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    return data.get(key, [])


def list_devices(home: str) -> list[str]:
    """Device directory names under *home*, in < Device/Device >'s cycle
    order — config/device_order.json's explicit order for this home
    first, then any device not listed there (a new one added since, or a
    home with no config entry at all), alphabetically, appended after."""
    d = home_dir(home)
    if not d.is_dir():
        return []
    names = {p.name for p in d.iterdir() if p.is_dir()}

    order = [name for name in _explicit_order(home) if name in names]
    remaining = sorted(names - set(order))
    return order + remaining


def cycle_device(home: str, current_device: str, direction: int) -> str:
    """Return the device before (-1) or after (+1) *current_device* in
    list_devices() order, wrapping around at the ends."""
    devices = list_devices(home)
    if not devices:
        return current_device
    try:
        index = devices.index(current_device)
    except ValueError:
        index = 0
    return devices[(index + direction) % len(devices)]


def list_leaf_devices(home: str) -> list[tuple[str, str]]:
    """Every cyclable "leaf" device under *home*, as (device, location)
    pairs — used by screens.active_device_screen and the < Device/Device >
    quick action (see docs/active-device-control-plan.md). A device with
    location sub-dirs contributes one entry per location (e.g. ("light",
    "hall")); a device with buttons directly under it contributes one
    entry with location "" instead (e.g. ("ac", "")).
    """
    leaves: list[tuple[str, str]] = []
    for device in list_devices(home):
        locations = list_locations(home, device)
        if locations:
            leaves.extend((device, location) for location in locations)
        else:
            leaves.append((device, ""))
    return leaves


def cycle_leaf_device(
        home: str, current: tuple[str, str], direction: int
) -> tuple[str, str]:
    """Return the leaf before (-1) or after (+1) *current* in
    list_leaf_devices() order, wrapping around. Falls back to the first
    leaf if *current* isn't found (or the home has none at all, in which
    case *current* is returned unchanged)."""
    leaves = list_leaf_devices(home)
    if not leaves:
        return current
    try:
        index = leaves.index(current)
    except ValueError:
        return leaves[0]
    return leaves[(index + direction) % len(leaves)]


def default_leaf_device(home: str) -> tuple[str, str]:
    """The leaf Active Device Control resets to on boot or a home switch:
    DEFAULT_ACTIVE_DEVICE if this home has it, else the alphabetically
    first leaf. ("", "") if the home has no leaves at all."""
    leaves = list_leaf_devices(home)
    if not leaves:
        return "", ""
    for device, location in leaves:
        if device == DEFAULT_ACTIVE_DEVICE:
            return device, location
    return leaves[0]


def list_locations(home: str, device: str) -> list[str]:
    """Location directory names under *device*, in Left/Right cycling
    order for that device (e.g. screens.active_device_screen's light
    leaves) — config/device_order.json's explicit order for "<home>/
    <device>" first (see _explicit_order()), then any location not listed
    there, alphabetically, appended after."""
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
    return sorted(f.stem for f in d.iterdir() if f.is_file())


def list_functions(home: str, device: str, location: str = "") -> list[dict]:
    """Derive screens.active_device_screen's function list for a device
    from its existing signal files — no separate config to author.

    A pair of files sharing a stem with matching "_up"/"_down" suffixes
    (e.g. "temp_up.txt" / "temp_down.txt") becomes one "paired" function:
    Up fires the "_up" file, Down fires the "_down" file. Any other file
    (e.g. "power.txt") becomes a "standalone" function fired by Enter.
    Order is alphabetical by function name.

    Returns a list of dicts: {"name", "type" ("paired"/"standalone"),
    "up", "down", "button"} — "up"/"down" are set for paired functions,
    "button" for standalone ones, the rest None.
    """
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


def buttons_at_device_level(home: str, device: str) -> bool:
    """True when buttons live directly under the device dir (no location sub-dir)."""
    d = home_dir(home) / device
    if not d.is_dir():
        return False
    return any(f.is_file() for f in d.iterdir())


def signal_path(home: str, device: str, button: str, location: str = "") -> Path:
    d = home_dir(home) / device
    if location:
        d = d / location
    return d / f"{button}.txt"


def signal_exists(home: str, device: str, button: str, location: str = "") -> bool:
    return signal_path(home, device, button, location).exists()


def load_signal(home: str, device: str, button: str, location: str = "") -> Path:
    """Return the Path to the signal file (callers pass it to ir-ctl)."""
    return signal_path(home, device, button, location)


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
    path.write_text(data)
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
    """Move a signal file to a new device/location/button identity — for
    screens.rename_screen, fixing a name mistyped during Learn without
    re-learning the IR signal itself. Caller is responsible for checking
    signal_exists() at the destination first if an overwrite prompt is
    wanted; this always overwrites silently.

    Any now-empty directories left behind at the old location (the old
    location dir, then the old device dir) are removed, so a renamed-away
    leaf doesn't linger as an empty stop in list_locations()/list_devices()
    — stops as soon as a directory still has something else in it."""
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
