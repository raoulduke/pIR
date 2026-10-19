"""IR transmitter: sends signals with `ir-ctl -s` on the TX device.

Signal files are `+pulse -space` text at 38 kHz, optionally starting with a
`carrier N` line (Sweep codes for brands that need another carrier). ir-ctl
rejects that line mixed with timings, so signals go out from a temp copy in
/dev/shm (RAM) with `-c N`. The kernel refuses any send over 500ms, so a
longer signal loses trailing repeat frames until it fits (_fit_tx_limit()).
"""

import fcntl
import logging
import os
import struct
import subprocess
import tempfile
import time
from array import array
from pathlib import Path

from app.constants import LIRC_TX_DEVICE
from hardware import lirc
from app.exceptions import HardwareError

log = logging.getLogger(__name__)

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
# lirc_dev's IR_MAX_DURATION: a send longer than this in total fails with
# EINVAL ("Invalid argument").
_TX_MAX_US = 500000
# Two frames match when every duration agrees within this or 25%,
# whichever is larger: capture jitter, or a capture against the encoder's
# nominal timing.
_FRAME_MATCH_US = 250
# A send is at most 500ms of IR plus ~0.1s to start ir-ctl; past this it's
# hung, and it runs on the main thread, so it would freeze the app.
_IR_CTL_TIMEOUT_SECONDS = 5


def transmit(signal_file: Path) -> None:
    """Send the IR signal stored in *signal_file*.

    Raises ``HardwareError`` on failure.
    """
    carrier, pulses = _read_pulses(signal_file)
    transmit_pulses(pulses, carrier)


def stream(signal_file: Path, still_held) -> None:
    """Send *signal_file*'s first frame, then repeat it at the capture's own
    frame period while *still_held()* — like a held remote button, so a
    receiver ramps volume smoothly. Blocks until released; a tap is one
    frame. Writes the LIRC device directly: an ir-ctl per frame takes
    ~100ms, longer than the frame period. Raises HardwareError on failure."""
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


def split_frames(pulses: list[int]) -> tuple[list[list[int]], list[int]]:
    """Frames (each starting and ending on a mark) and the gaps between
    them. A trailing space is dropped."""
    frames, gaps, start = [], [], 0
    for i in range(1, len(pulses), 2):  # odd indexes are spaces
        if pulses[i] >= _FRAME_GAP_MIN_US:
            frames.append(pulses[start:i])
            gaps.append(pulses[i])
            start = i + 1
    tail = pulses[start:]
    if len(tail) % 2 == 0:
        tail = tail[:-1]
    if tail:
        frames.append(tail)
    elif gaps:
        gaps.pop()
    return frames, gaps


def same_frame(a: list[int], b: list[int]) -> bool:
    """True if *a* and *b* are the same frame, allowing for timing jitter."""
    return len(a) == len(b) and all(
        abs(x - y) <= max(_FRAME_MATCH_US, y // 4) for x, y in zip(a, b))


def _fit_tx_limit(pulses: list[int]) -> list[int]:
    """*pulses*, cut to the kernel's 500ms send limit by dropping trailing
    frames that repeat an earlier one (a held button's repeats). The
    first copy of every distinct frame stays, so the data and anything
    the protocol needs once (an NEC repeat code, Sharp's inverted frame)
    is never cut. Raises HardwareError if it still doesn't fit."""
    if sum(pulses) <= _TX_MAX_US:
        return pulses
    frames, gaps = split_frames(pulses)
    total = sum(map(sum, frames)) + sum(gaps)
    dropped = 0
    while (total > _TX_MAX_US and len(frames) > 1
           and any(same_frame(frames[-1], f) for f in frames[:-1])):
        total -= sum(frames.pop()) + gaps.pop()
        dropped += 1
    if total > _TX_MAX_US:
        raise HardwareError(f"Signal too long: {total // 1000}ms")
    log.info("Dropped %d repeat frame(s) to fit the 500ms send limit", dropped)
    out = list(frames[0])
    for gap, frame in zip(gaps, frames[1:]):
        out += [gap, *frame]
    return out


def transmit_pulses(pulses: list[int], carrier: int) -> None:
    """Send raw mark/space timings (µs, starting with a mark) at *carrier* Hz."""
    pulses = _fit_tx_limit(pulses)
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
            timeout=_IR_CTL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise HardwareError("ir-ctl timed out") from None
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip()
        raise HardwareError(f"ir-ctl TX failed: {detail or e}") from e
    except FileNotFoundError:
        raise HardwareError("ir-ctl not found") from None


def verify() -> bool:
    """Return True if the TX device exists and can send; raises
    HardwareError otherwise."""
    lirc.require(LIRC_TX_DEVICE, lirc.CAN_SEND_PULSE, "send")
    return True
