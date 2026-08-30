"""Startup verification sequence.

Runs each hardware/config check in order, updating *state* as it goes.
Returns the populated AppState on success, or None on unrecoverable failure.
"""

import time
from app.state import AppState
from app.exceptions import ConfigError, HardwareError
from hardware.lcd import lcd
from hardware import ir_transmitter, ir_receiver, network
# noinspection PyUnresolvedReferences
from evdev import InputDevice, list_devices, ecodes
from services import home_service
from screens.widgets.format import format_name


_STEPS = ["Config", "Input", "IR Receive", "IR Transmit", "Network"]
_TICK_SECONDS = 0.03


def _animate_step(index: int, duration: float) -> None:
    """Fill the progress bar smoothly from step *index*'s start to its end
    fraction over *duration* seconds, in ~30ms ticks, so it reads as real-
    time progress rather than a jump cut between steps."""
    label = _STEPS[index]
    start = index / len(_STEPS)
    end = (index + 1) / len(_STEPS)
    ticks = max(1, round(duration / _TICK_SECONDS))
    for i in range(1, ticks + 1):
        lcd.write_progress(label, start + (end - start) * i / ticks)
        time.sleep(duration / ticks)


def _detect_input_devices() -> list:
    """Return a list of evdev paths that have EV_KEY capability.

    Opens and immediately closes each device so no file descriptors leak
    before InputReader opens them properly later.
    """
    found = []
    for path in list_devices():
        device = None
        try:
            device = InputDevice(path)
            if ecodes.EV_KEY in device.capabilities():
                found.append(path)
        except Exception:
            pass
        finally:
            if device is not None:
                try:
                    device.close()
                except Exception:
                    pass
    return found


def initialize() -> AppState | None:
    state = AppState()

    try:
        _animate_step(0, 0.25)
        active_home = home_service.verify_active_home()
        state.active_home = active_home

        _animate_step(1, 0.25)
        devices = _detect_input_devices()
        state.input_available = bool(devices)

        if not state.input_available:
            ip = network.get_ip_address()
            lcd.write("Input unavailable", ip[:16] if ip else "WiFi not conn.")
            return None

        _animate_step(2, 0.25)
        ir_receiver.verify()
        state.ir_rx_available = True

        _animate_step(3, 0.25)
        ir_transmitter.verify()
        state.ir_tx_available = True

        _animate_step(4, 0.25)
        state.network_connected = network.is_connected()
        state.ip_address = network.get_ip_address()

        lcd.write("Ready", format_name(active_home)[:16])
        time.sleep(0.5)

        return state

    except (ConfigError, HardwareError) as e:
        lcd.write("Startup Failed", str(e)[:16])
        time.sleep(5)
        return None

    except Exception as e:
        lcd.write("Startup Failed", str(e)[:16])
        time.sleep(5)
        return None
