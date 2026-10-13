"""Startup verification sequence.

Runs each hardware/config check in order, updating *state* as it goes.
Returns the populated AppState on success, or None on unrecoverable failure.
"""

import time
from app.state import AppState
from app.exceptions import ConfigError, HardwareError
from hardware.lcd import lcd
from hardware import ir_transmitter, ir_receiver
from services import home_service


def initialize() -> AppState | None:
    """Runs the checks that are cheap and worth failing fast on before the
    real screen appears. Deliberately does NOT check input-device
    availability first — the app needs human interaction to do anything at
    all, and that's inherently slower than any of this, so there's no
    reason to gate showing the real screen on it. hardware/input.py's
    read_key() already has the real version of that same check (and a more
    accurate one — it knows whether the keypad itself actually came up,
    not just whether some evdev device exists), so a broken zero-input
    device still fails exactly as loudly, just the moment read_key() is
    first called instead of before the screen ever gets a chance to draw."""
    state = AppState()

    def _check_config() -> None:
        state.active_home = home_service.verify_active_home()

    def _check_ir_receive() -> None:
        ir_receiver.verify()
        state.ir_rx_available = True

    def _check_ir_transmit() -> None:
        ir_transmitter.verify()
        state.ir_tx_available = True

    # The progress bar's denominator comes from this list's own length, not
    # a separately-maintained count — add or remove a check here and the
    # bar automatically reflects real progress against the new total,
    # instead of a number that has to be remembered and kept in sync.
    checks = [_check_config, _check_ir_receive, _check_ir_transmit]

    try:
        for i, check in enumerate(checks):
            lcd.write_progress("Initializing...", (i + 1) / len(checks))
            check()

        lcd.load_preset_glyphs()

        # No "Ready" screen here — active_device_screen.show() draws the
        # real first screen immediately, and a network-connectivity check
        # used to run right before this too: state.network_connected/
        # ip_address were set but never read anywhere else in the app, so
        # that step was pure dead work — up to a full second of stalling
        # on network.is_connected()'s socket timeout if Wi-Fi wasn't
        # associated yet, for a result nothing ever consulted.
        return state

    except (ConfigError, HardwareError) as e:
        lcd.write("Startup Failed", str(e)[:16])
        time.sleep(5)
        return None

    except Exception as e:
        lcd.write("Startup Failed", str(e)[:16])
        time.sleep(5)
        return None
