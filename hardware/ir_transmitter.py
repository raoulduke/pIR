"""IR transmitter hardware wrapper.

Wraps ``ir-ctl -s`` (lirc0).  Only this file knows about the TX device path.

Signal files are ir-ctl ``+pulse -space`` text, sent at the gpio-ir-tx
default 38 kHz carrier. A file may start with a ``carrier N`` line instead
(the Fun menu's library codes — Sony, Philips and Panasonic need their own
carrier; see services/ir_encode.py): ir-ctl rejects that line mixed with
+/- values, so it's stripped here and passed as ``-c N``, with the rest
sent from a copy in /dev/shm (RAM, not the SD card).
"""

import fcntl
import os
import struct
import subprocess
import tempfile
import time
from array import array
from pathlib import Path

from app.constants import LIRC_TX_DEVICE
from app.exceptions import HardwareError

_TEMP_DIR = "/dev/shm" if Path("/dev/shm").is_dir() else None

_DEFAULT_CARRIER = 38000
# linux/lirc.h: LIRC_SET_SEND_CARRIER = _IOW('i', 0x13, __u32).
_LIRC_SET_SEND_CARRIER = 0x40046913
# A space this long ends a frame — NEC's inter-frame gap is ~40ms, while
# the longest space inside a frame is the ~4.5ms leader.
_FRAME_GAP_MIN_US = 10000
# Frame period when a capture holds just one frame, so there's no gap to
# measure: NEC's nominal repeat period.
_DEFAULT_FRAME_PERIOD_US = 108000


def transmit(signal_file: Path) -> None:
    """Send the IR signal stored in *signal_file*.

    Raises ``HardwareError`` on failure.
    """
    try:
        text = Path(signal_file).read_text()
    except OSError as e:
        raise HardwareError(f"Can't read signal: {e}") from e

    first, _, rest = text.partition("\n")
    if not first.startswith("carrier "):
        _run_ir_ctl(["-s", str(signal_file)])
        return

    try:
        carrier = int(first.split()[1])
    except (IndexError, ValueError):
        raise HardwareError(f"Bad carrier line: {first[:20]}") from None
    _send_text(rest, carrier)


def stream(signal_file: Path, still_held) -> None:
    """Send one frame of *signal_file*, then repeat it at the remote's own
    pace for as long as *still_held()* — the way a physical remote behaves
    while a button is held, which a receiver ramps volume on smoothly.
    Blocks until released. A tap sends exactly one frame.

    Writes straight to the LIRC device instead of spawning ir-ctl per
    frame (~103ms each on this hardware, longer than the ~90ms frame
    period itself). The frame is the capture's first; the period is the
    capture's own average frame-plus-gap, so it matches whatever pace the
    original remote used.

    Raises ``HardwareError`` on failure.
    """
    carrier, pulses = _read_pulses(signal_file)
    frame, period = _first_frame(pulses)
    data = array("I", frame).tobytes()
    try:
        fd = os.open(LIRC_TX_DEVICE, os.O_WRONLY)
    except OSError as e:
        raise HardwareError(f"Can't open {LIRC_TX_DEVICE}: {e}") from e
    try:
        fcntl.ioctl(fd, _LIRC_SET_SEND_CARRIER, struct.pack("I", carrier))
        next_at = time.monotonic()
        while True:
            os.write(fd, data)  # returns once the frame has gone out
            next_at += period
            time.sleep(max(0.0, next_at - time.monotonic()))
            if not still_held():
                return
    except OSError as e:
        raise HardwareError(f"IR stream failed: {e}") from e
    finally:
        os.close(fd)


def _read_pulses(signal_file: Path) -> tuple[int, list[int]]:
    """(carrier Hz, mark/space µs starting with a mark) from a signal file."""
    try:
        text = Path(signal_file).read_text()
    except OSError as e:
        raise HardwareError(f"Can't read signal: {e}") from e
    carrier = _DEFAULT_CARRIER
    first, _, rest = text.partition("\n")
    if first.startswith("carrier "):
        try:
            carrier = int(first.split()[1])
        except (IndexError, ValueError):
            raise HardwareError(f"Bad carrier line: {first[:20]}") from None
        text = rest
    try:
        return carrier, [abs(int(v)) for v in text.split()]
    except ValueError:
        raise HardwareError("Bad signal file") from None


def _first_frame(pulses: list[int]) -> tuple[list[int], float]:
    """The first frame (marks/spaces up to the first frame gap, ending on a
    mark) and the frame period in seconds."""
    frames, start = [], 0
    for i in range(1, len(pulses), 2):  # odd indexes are spaces
        if pulses[i] >= _FRAME_GAP_MIN_US:
            frames.append(pulses[start:i + 1])
            start = i + 1
    if not frames:
        frame = pulses if len(pulses) % 2 else pulses[:-1]
        return frame, max(_DEFAULT_FRAME_PERIOD_US, sum(frame) + 40000) / 1e6
    period_us = sum(sum(f) for f in frames) / len(frames)
    return frames[0][:-1], period_us / 1e6


def transmit_pulses(pulses: list[int], carrier: int) -> None:
    """Send raw mark/space timings (µs, starting with a mark) at *carrier* Hz."""
    text = " ".join(f"{'+' if i % 2 == 0 else '-'}{v}" for i, v in enumerate(pulses))
    _send_text(text + "\n", carrier)


def _send_text(text: str, carrier: int) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", dir=_TEMP_DIR) as f:
        f.write(text)
        f.flush()
        _run_ir_ctl(["-c", str(carrier), "-s", f.name])


def _run_ir_ctl(args: list[str]) -> None:
    try:
        subprocess.run(
            ["ir-ctl", "-d", LIRC_TX_DEVICE, *args],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip()
        raise HardwareError(f"ir-ctl TX failed: {detail or e}") from e
    except FileNotFoundError:
        raise HardwareError("ir-ctl not found") from None


def verify() -> bool:
    """Return True if the GPIO subsystem is present (basic sanity check)."""
    if not Path("/sys/class/gpio").exists():
        raise HardwareError("GPIO subsystem unavailable")
    return True
