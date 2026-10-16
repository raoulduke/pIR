"""IR transmitter hardware wrapper.

Wraps ``ir-ctl -s`` (lirc0).  Only this file knows about the TX device path.

Signal files are ir-ctl ``+pulse -space`` text, sent at the gpio-ir-tx
default 38 kHz carrier. A file may start with a ``carrier N`` line instead
(the Fun menu's library codes — Sony, Philips and Panasonic need their own
carrier; see services/ir_encode.py): ir-ctl rejects that line mixed with
+/- values, so it's stripped here and passed as ``-c N``, with the rest
sent from a copy in /dev/shm (RAM, not the SD card).
"""

import subprocess
import tempfile
from pathlib import Path

from app.constants import LIRC_TX_DEVICE
from app.exceptions import HardwareError

_TEMP_DIR = "/dev/shm" if Path("/dev/shm").is_dir() else None


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
