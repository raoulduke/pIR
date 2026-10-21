"""Startup verification sequence.

Runs each hardware/config check in order, updating *state* as it goes.
Returns the populated AppState on success, or None on unrecoverable failure.
"""

import logging
import time
from app.state import AppState
from app.exceptions import ConfigError, HardwareError
from hardware.lcd import lcd
from screens.widgets.format import fit_or
from hardware import ir_transmitter, ir_receiver
from services import home_service

log = logging.getLogger(__name__)


def initialize() -> AppState | None:
    """Run the quick checks worth failing fast on before the first screen.
    Input devices aren't checked here: read_key() reports having none the
    first time it's called."""
    state = AppState()

    def _check_config() -> None:
        state.active_home = home_service.verify_active_home()

    def _check_ir_receive() -> None:
        # Only Learn needs the receiver, so a failure here doesn't stop the
        # app; Learn reports it when used.
        try:
            ir_receiver.verify()
        except HardwareError as e:
            log.warning("IR receiver unavailable: %s", e)

    def _check_ir_transmit() -> None:
        ir_transmitter.verify()

    checks = [_check_config, _check_ir_receive, _check_ir_transmit]

    try:
        for i, check in enumerate(checks):
            lcd.write_progress("Initializing...", (i + 1) / len(checks))
            check()

        lcd.load_preset_glyphs()
        return state

    except (ConfigError, HardwareError) as e:
        log.error("Startup failed: %s", e)
        lcd.write("Startup Failed", fit_or(str(e), ""))
        time.sleep(5)
        return None

    except Exception as e:
        log.exception("Startup failed")
        lcd.write("Startup Failed", fit_or(str(e), ""))
        time.sleep(5)
        return None
