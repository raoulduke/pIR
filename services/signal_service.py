"""Signal filesystem service.

All reads and writes to the signals/ directory live here.
Hardware and screens never touch the filesystem directly.
"""

from pathlib import Path
import os
import pwd

from app.constants import SIGNALS_DIR, FILE_OWNER

_BASE = Path(__file__).parent.parent / SIGNALS_DIR


def home_dir(home: str) -> Path:
    return _BASE / home


def home_exists(home: str) -> bool:
    return home_dir(home).is_dir()


def list_homes() -> list[str]:
    if not _BASE.is_dir():
        return []
    return sorted(d.name for d in _BASE.iterdir() if d.is_dir())


def list_devices(home: str) -> list[str]:
    d = home_dir(home)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def list_locations(home: str, device: str) -> list[str]:
    d = home_dir(home) / device
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def list_buttons(home: str, device: str, location: str = "") -> list[str]:
    d = home_dir(home) / device
    if location:
        d = d / location
    if not d.is_dir():
        return []
    return sorted(f.stem for f in d.iterdir() if f.is_file())


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
