"""IR receiver: captures one signal with `ir-ctl -r` on the RX device."""

import subprocess
import signal
import select
import os
import tempfile
import time
from typing import TYPE_CHECKING

from app.constants import (
    LIRC_RX_DEVICE,
    SIGNAL_TAIL_CAPTURE_SECONDS,
    SIGNAL_WAIT_TIMEOUT_SECONDS,
    STATEMENT_RECEIVING,
    STATEMENT_SIGNAL_DETECTED,
    STATEMENT_CAPTURED,
)
from app.exceptions import HardwareError
from hardware import lirc

if TYPE_CHECKING:
    from hardware.lcd import LCD

# Noise filter: every NEC frame starts with a ~9ms lead-in pulse, while the
# interference seen on this circuit stays well under 1ms per pulse. A burst
# whose first pulse is shorter than this is ignored. (Assumes NEC remotes.)
_MIN_LEAD_PULSE_MICROSECONDS = 2000


def _looks_like_real_frame(line: str) -> bool:
    token = line.split(maxsplit=1)[:1]
    if not token or not token[0].startswith("+"):
        return False
    try:
        return int(token[0][1:]) >= _MIN_LEAD_PULSE_MICROSECONDS
    except ValueError:
        return False


def capture(lcd: "LCD") -> str:
    """Capture one IR signal, showing progress on *lcd*. Returns the raw
    `+pulse -space` text, or "" if nothing real arrived. Raises
    HardwareError if ir-ctl is missing."""
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".txt", prefix="ir_signal_")
    os.close(tmp_fd)

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
            # Only once ir-ctl is running, so the prompt never shows while
            # nothing is listening.
            lcd.write(STATEMENT_RECEIVING, "Press button now")

            captured = False
            last_data_time: float | None = None
            pulse_count = 0
            start_time = time.monotonic()

            try:
                while True:
                    ready, _, _ = select.select([proc.stdout], [], [], 0.1)

                    if ready:
                        line = proc.stdout.readline()
                        if not line:
                            break

                        if not _looks_like_real_frame(line):
                            # Interference: not saved, and it mustn't reset
                            # the tail timer below or a capture never ends.
                            continue

                        tmp.write(line)
                        tmp.flush()

                        # One line per burst: "+8559 -4195 +659 ...".
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

                    # Nothing real at all: give up (the caller offers a retry).
                    if not captured and (time.monotonic() - start_time) > SIGNAL_WAIT_TIMEOUT_SECONDS:
                        break

            finally:
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    # Left running, it would keep /dev/lirc1 busy for the
                    # next capture.
                    proc.kill()
                    proc.wait()

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
    """Return True if the RX device exists and can record; raises
    HardwareError otherwise."""
    lirc.require(LIRC_RX_DEVICE, lirc.CAN_REC_MODE2, "receive")
    return True
