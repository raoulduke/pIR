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
        lcd.write("Initializing", "Config")
        time.sleep(0.25)

        active_home = home_service.verify_active_home()
        state.active_home = active_home

        lcd.write("Initializing", "Input")
        time.sleep(0.25)

        devices = _detect_input_devices()
        state.input_available = bool(devices)

        if not state.input_available:
            ip = network.get_ip_address()
            lcd.write("Input unavailable", ip[:16] if ip else "WiFi not conn.")
            return None

        lcd.write("Initializing", "IR Receive")
        time.sleep(0.25)
        ir_receiver.verify()
        state.ir_rx_available = True

        lcd.write("Initializing", "IR Transmit")
        time.sleep(0.25)
        ir_transmitter.verify()
        state.ir_tx_available = True

        lcd.write("Initializing", "Network")
        time.sleep(0.25)
        state.network_connected = network.is_connected()
        state.ip_address = network.get_ip_address()

        lcd.write("Ready", active_home[:16])
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
