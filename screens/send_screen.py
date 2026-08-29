"""Send screen.

Navigates Device → [Location] → Button, then delegates the actual
transmission to ``hardware.ir_transmitter`` via ``services.signal_service``.
"""

import time
from hardware.lcd import LCD
from hardware.input import KEY_QUIT, KEY_BACK, KEY_DEVICE_PREV, KEY_DEVICE_NEXT
from hardware import ir_transmitter
from services import signal_service, home_service
from screens.widgets.selector import select_from_list
from screens.widgets.format import format_name
from app.exceptions import HardwareError

# "< Device" cycles to the prev/next device from the Location or Button
# level, without backing out to the top Device list first. Maps each key
# straight to the direction signal_service.cycle_device() expects.
# KEY_DEVICE_NEXT has no physical key bound to it currently, but the
# cycle-forward behavior stays wired here for whenever a key is assigned.
_CYCLE_KEYS = {KEY_DEVICE_PREV: -1, KEY_DEVICE_NEXT: 1}


def show(lcd: LCD) -> str | None:
    home = home_service.get_active_home()
    home_dir = signal_service.home_dir(home)

    if not home_dir.is_dir():
        lcd.write(f"Home: {home[:10]}", "No signals")
        time.sleep(2)
        return None

    # --- Device selection ---
    while True:
        devices = signal_service.list_devices(home)
        if not devices:
            lcd.write("No signals", "")
            time.sleep(2)
            return None

        device = select_from_list(lcd, "Device", devices)
        if device == KEY_BACK:
            return None
        if device == KEY_QUIT:
            return KEY_QUIT

        # --- Location or direct-button selection ---
        while True:
            has_direct_buttons = signal_service.buttons_at_device_level(home, device)

            if has_direct_buttons:
                buttons_location = ""
                used_location_prompt = False
            else:
                locations = signal_service.list_locations(home, device)
                location = select_from_list(
                    lcd, format_name(device), locations, extra_keys=_CYCLE_KEYS
                )
                if location == KEY_BACK:
                    break
                if location == KEY_QUIT:
                    return KEY_QUIT
                if location in _CYCLE_KEYS:
                    device = signal_service.cycle_device(home, device, _CYCLE_KEYS[location])
                    continue  # re-run device-entry resolution for the new device
                buttons_location = location
                used_location_prompt = True

            # --- Button selection ---
            switched_device = False
            button_index = 0
            while True:
                buttons = signal_service.list_buttons(home, device, buttons_location)
                if not buttons:
                    lcd.write("No buttons", "")
                    time.sleep(1)
                    break

                button = select_from_list(
                    lcd,
                    format_name(f"{device} / {buttons_location}" if buttons_location else device),
                    buttons,
                    extra_keys=_CYCLE_KEYS,
                    start_index=button_index,
                )
                if button == KEY_BACK:
                    break
                if button == KEY_QUIT:
                    return KEY_QUIT
                if button in _CYCLE_KEYS:
                    device = signal_service.cycle_device(home, device, _CYCLE_KEYS[button])
                    switched_device = True
                    break

                # Reopen the list on this same button next time around, so
                # repeating a signal is just pressing ENTER again.
                button_index = buttons.index(button)

                signal_file = signal_service.load_signal(home, device, button, buttons_location)
                _transmit(lcd, signal_file)
                # Loop back to the button list — pressing the same button
                # again is how the user repeats the signal.

            if switched_device:
                continue  # re-run device-entry resolution for the new device
            if not used_location_prompt:
                break


def _transmit(lcd: LCD, signal_file) -> None:
    try:
        ir_transmitter.transmit(signal_file)
    except HardwareError as e:
        lcd.write("TX Error", str(e)[:16])
        time.sleep(2)
