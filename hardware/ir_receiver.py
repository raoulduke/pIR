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
    SIGNAL_WAIT_TIMEOUT_SECONDS,
    STATEMENT_RECEIVING,
    STATEMENT_SIGNAL_DETECTED,
    STATEMENT_CAPTURED,
)
from app.exceptions import HardwareError

if TYPE_CHECKING:
    from hardware.lcd import LCD

# The receiver is locked to NEC-only decoding (see ir-nec.service), and
# every NEC frame — the main burst and every repeat — starts with a lead-in
# pulse around 8500-9000us, dramatically longer than any real data pulse
# (NEC's own unit pulses top out around 600us). Electrical interference
# observed on this circuit tops out well under 1ms per pulse and never
# produces anything close to a real lead-in. Requiring a line's first pulse
# to clear this threshold before treating it as a signal — rather than
# noise — filters that interference out without needing to know its exact
# source. If a non-NEC protocol is ever supported, this needs revisiting.
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
    """Capture one IR signal burst.

    Displays progress on *lcd*.  Returns the raw signal text, or ``""`` on
    failure.  Raises ``HardwareError`` if ``ir-ctl`` is not available.
    """
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
                            # Interference, not a real button press — ignore
                            # it entirely: don't save it, don't count it,
                            # and don't let it reset the tail timer below
                            # (which would otherwise stop a real capture
                            # from ever reaching "quiet long enough to end").
                            # The overall timeout just below is what keeps
                            # this from waiting forever if it's noise the
                            # whole way through with no real press at all.
                            continue

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

                    # Nothing real yet after a while — likely just noise the
                    # whole time with no actual button press. Give up rather
                    # than wait forever; the caller's existing "no signal,
                    # try again" retry handles this the same as a too-short
                    # capture.
                    if not captured and (time.monotonic() - start_time) > SIGNAL_WAIT_TIMEOUT_SECONDS:
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
