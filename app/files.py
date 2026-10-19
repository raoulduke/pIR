"""Crash-safe file writes."""

import os
from pathlib import Path


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
