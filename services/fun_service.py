"""Fun menu data: the TV power-code library (data/fun_codes.json, built by
scripts/build_fun_codes.py) and codes saved from a sweep.

Saves live in fun_signals/<device_type>/<brand>/<signal>/<name>.txt — a
top-level folder, not under signals/, because everything under signals/ is
a home to signal_service (Learn, Rename, Active Device Control) and these
must never show up there. Brand folders are slugified ("onn." -> "onn");
everything else here takes the library's display names ("onn.", "LG").
Tracked in git like signals/: saves made on the Pi get committed and pushed
from the Pi.
"""

import json
import os
import pwd
from functools import lru_cache
from pathlib import Path

from app.constants import FUN_CODES_FILE, FUN_SIGNALS_DIR, FILE_OWNER
from screens.widgets.format import slugify

_ROOT = Path(__file__).parent.parent
_BASE = _ROOT / FUN_SIGNALS_DIR


@lru_cache(maxsize=1)
def _library() -> dict:
    data = json.loads((_ROOT / FUN_CODES_FILE).read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def list_device_types() -> list[str]:
    return list(_library())


def list_brands(device_type: str) -> list[str]:
    return list(_library().get(device_type, {}))


def list_signals(device_type: str, brand: str) -> list[str]:
    return list(_library().get(device_type, {}).get(brand, {}))


def get_codes(device_type: str, brand: str, signal: str) -> list[dict]:
    return _library().get(device_type, {}).get(brand, {}).get(signal, [])


# --- Saves ---------------------------------------------------------------

def _brand_dir(device_type: str, brand: str) -> Path:
    return _BASE / device_type / slugify(brand)


def save_path(device_type: str, brand: str, signal: str, name: str) -> Path:
    return _brand_dir(device_type, brand) / signal / f"{name}.txt"


def _brand_name(device_type: str, folder: str) -> str:
    """Folder slug back to the library's display name ("onn" -> "onn.")."""
    for brand in list_brands(device_type):
        if slugify(brand) == folder:
            return brand
    return folder


def list_saved_device_types() -> list[str]:
    return [d for d in list_device_types() if list_saved_brands(d)]


def list_saved_brands(device_type: str) -> list[str]:
    """Brands with at least one save, in library order (unknown ones last)."""
    root = _BASE / device_type
    if not root.is_dir():
        return []
    folders = [d.name for d in root.iterdir() if d.is_dir() and any(d.rglob("*.txt"))]
    names = [_brand_name(device_type, f) for f in folders]
    order = list_brands(device_type)
    return sorted(names, key=lambda n: (order.index(n) if n in order else len(order), n))


def list_saved_signals(device_type: str, brand: str) -> list[str]:
    root = _brand_dir(device_type, brand)
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir() and any(d.glob("*.txt")))


def list_saved(device_type: str, brand: str, signal: str) -> list[str]:
    root = _brand_dir(device_type, brand) / signal
    if not root.is_dir():
        return []
    return sorted(p.stem for p in root.glob("*.txt"))


def saved_exists(device_type: str, brand: str, signal: str, name: str) -> bool:
    return save_path(device_type, brand, signal, name).exists()


def save(device_type: str, brand: str, signal: str, name: str, text: str) -> Path:
    """Write a save and chown it (and new parent folders) to FILE_OWNER, like
    signal_service.save_signal() — the app runs as root."""
    path = save_path(device_type, brand, signal, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    try:
        pw = pwd.getpwnam(FILE_OWNER)
        for p in [path, *path.parents]:
            if p == _ROOT:
                break
            os.chown(p, pw.pw_uid, pw.pw_gid)
    except (KeyError, PermissionError, OSError):
        pass
    return path
