"""LIRC device capability check, for the startup checks in
hardware/ir_transmitter.py and hardware/ir_receiver.py."""

import fcntl
import os
import struct

from app.exceptions import HardwareError

# linux/lirc.h
_LIRC_GET_FEATURES = 0x80046900  # _IOR('i', 0x00, __u32)
CAN_SEND_PULSE = 0x00000002      # LIRC_CAN_SEND_PULSE
CAN_REC_MODE2 = 0x00040000       # LIRC_CAN_REC_MODE2


def require(device: str, feature: int, what: str) -> None:
    """Raise HardwareError unless *device* exists and reports *feature* —
    catches a missing device, and the two devices' numbering swapping."""
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        raise HardwareError(f"{device} missing: {e.strerror}") from e
    try:
        features = struct.unpack("I", fcntl.ioctl(fd, _LIRC_GET_FEATURES, bytes(4)))[0]
    except OSError as e:
        raise HardwareError(f"{device} not LIRC: {e.strerror}") from e
    finally:
        os.close(fd)
    if not features & feature:
        raise HardwareError(f"{device} can't {what}")
