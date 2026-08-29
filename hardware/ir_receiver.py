"""IR receiver hardware wrapper.

Wraps ``ir-ctl -r`` (lirc1).  Only this file knows about the RX device path
and the capture state machine.
"""

import subprocess
import signal
import select
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from app.constants import (
    LIRC_RX_DEVICE,
    SIGNAL_TAIL_CAPTURE_SECONDS,
    STATEMENT_RECEIVING,
    STATEMENT_SIGNAL_DETECTED,
    STATEMENT_CAPTURED,
)
from app.exceptions import HardwareError

if TYPE_CHECKING:
    from hardware.lcd import LCD


def capture(lcd: "LCD") -> str:
    """Capture one IR signal burst.

    Displays progress on *lcd*.  Returns the raw signal text, or ``""`` on
    failure.  Raises ``HardwareError`` if ``ir-ctl`` is not available.
    """
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="ir_signal_")
    os.close(tmp_fd)

    lcd.write(STATEMENT_RECEIVING, "Press button now")

    try:
        with open(tmp_path, "w") as tmp:
            try:
                proc = subprocess.Popen(
                    ["ir-ctl", "-r", "-d", LIRC_RX_DEVICE],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
            except FileNotFoundError:
                raise HardwareError("ir-ctl not found") from None

            captured = False
            last_data_time: float | None = None
            pulse_count = 0

            try:
                while True:
                    ready, _, _ = select.select([proc.stdout], [], [], 0.1)

                    if ready:
                        line = proc.stdout.readline()
                        if not line:
                            break

                        tmp.write(line)
                        tmp.flush()

                        # ir-ctl -r emits one line per burst, e.g.
                        # "+8559 -4195 +659 -1520 ...", not lines literally
                        # starting with "pulse"/"space" — count the +/-
                        # tokens themselves instead.
                        stripped = line.strip()
                        if stripped:
                            new_pulses = len(stripped.split())
                            was_first = pulse_count == 0
                            pulse_count += new_pulses
                            if was_first:
                                lcd.write(STATEMENT_RECEIVING, STATEMENT_SIGNAL_DETECTED)
                            elif pulse_count // 20 > (pulse_count - new_pulses) // 20:
                                lcd.write(STATEMENT_RECEIVING, f"{pulse_count} pulses")

                        captured = True
                        last_data_time = time.monotonic()

                    if captured and (time.monotonic() - last_data_time) > SIGNAL_TAIL_CAPTURE_SECONDS:
                        lcd.write(STATEMENT_CAPTURED, f"{pulse_count} pulses")
                        break

            finally:
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=1)

        if os.path.getsize(tmp_path) > 0:
            with open(tmp_path, "r") as f:
                return f.read()
        return ""

    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def verify() -> bool:
    """Return True if the GPIO subsystem is present (basic sanity check)."""
    if not Path("/sys/class/gpio").exists():
        raise HardwareError("GPIO subsystem unavailable")
    return True
