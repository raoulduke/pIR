"""IR transmitter hardware wrapper.

Wraps ``ir-ctl -s`` (lirc0).  Only this file knows about the TX device path.
"""

import subprocess
from pathlib import Path

from app.constants import LIRC_TX_DEVICE
from app.exceptions import HardwareError


def transmit(signal_file: Path) -> None:
    """Send the IR signal stored in *signal_file*.

    Raises ``HardwareError`` on failure.
    """
    try:
        subprocess.run(
            ["ir-ctl", "-d", LIRC_TX_DEVICE, "-s", str(signal_file)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError as e:
        raise HardwareError(f"ir-ctl TX failed: {e}") from e
    except FileNotFoundError:
        raise HardwareError("ir-ctl not found") from None


def verify() -> bool:
    """Return True if the GPIO subsystem is present (basic sanity check)."""
    if not Path("/sys/class/gpio").exists():
        raise HardwareError("GPIO subsystem unavailable")
    return True
