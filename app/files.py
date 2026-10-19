"""Crash-safe file writes, and cached JSON reads."""

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# path -> ((mtime_ns, size), parsed data, or _UNREADABLE)
_json_cache: dict[Path, tuple[tuple[int, int], object]] = {}
_json_lock = threading.Lock()
_UNREADABLE = object()


def read_json_cached(path: Path, default):
    """Parsed JSON at *path*, re-read only when its modification time or size
    changes (config is read on every key press). *default* if the file is
    missing or unreadable; an unreadable version is logged once. The result
    is shared between callers: copy it before modifying."""
    path = Path(path)
    try:
        st = path.stat()
    except FileNotFoundError:
        return default
    version = (st.st_mtime_ns, st.st_size)
    with _json_lock:
        cached = _json_cache.get(path)
    if cached is None or cached[0] != version:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            log.warning("Can't read %s: %s", path, e)
            data = _UNREADABLE
        cached = (version, data)
        with _json_lock:
            _json_cache[path] = cached
    return default if cached[1] is _UNREADABLE else cached[1]


def write_text_atomic(path: Path, text: str) -> None:
    """Replace *path* with *text* so a power cut mid-write leaves either the
    old file or the new one, never a half-written mix: write a temp file in
    the same directory, flush it to the SD card, then rename it over the
    original (a rename within one filesystem is atomic). Keeps an existing
    file's owner and mode — the app runs as root, and files like
    config/settings.json are edited by hand as matt."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    try:
        st = path.stat()
        os.chown(tmp, st.st_uid, st.st_gid)
        os.chmod(tmp, st.st_mode & 0o7777)
    except FileNotFoundError:
        pass
    except PermissionError:
        pass  # not root (e.g. testing on WSL2): the file keeps our own owner
    os.replace(tmp, path)
    # Make the rename itself durable, not just the data.
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
