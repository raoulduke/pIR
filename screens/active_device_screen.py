"""Active Device Control — the default/boot screen.

Owns the app's main loop (see main.py): boots straight into this instead of
a menu detour, and runs until state.running goes False. Everything else
(Learn, Command, Home) lives behind the Menu key and is reached via
app/quick_actions.py, which also owns switching the active device
(< Device/Device >, Learn) — see docs/active-device-control-plan.md for
the full design.

Tracks one active leaf device (state.active_device, state.active_location):
  - DEFAULT_ACTIVE_DEVICE ("google_tv") is raw D-pad passthrough — Up/Down/
    Left/Right/Enter each fire the matching signal immediately, no function
    list, no exceptions.
  - Every other device shows one function at a time — Left/Right cycle
    functions, Up/Down fire a paired function's two signals, Enter fires a
    standalone one. Line 2 frames the name in < > whenever there's more
    than one function to cycle to, so it's obvious Left/Right does
    something. Functions come from services.signal_service.list_functions
    (IR signal files) normally, or services.voice_device_service (Home
    Assistant voice commands, all standalone) for a device configured in
    config/voice_devices.json — see _functions_for().
"""

import time
from hardware.lcd import LCD
from hardware import ir_transmitter
from hardware import input as input_hw
from hardware.input import (
    read_key, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER, KEY_QUIT,
)
from services import home_service, signal_service, voice_device_service, preset_service
from screens.widgets.format import format_name
from app.constants import DEFAULT_ACTIVE_DEVICE
from app.state import AppState
from app.exceptions import HardwareError

_DPAD_BUTTONS = {
    KEY_UP: "up", KEY_DOWN: "down", KEY_LEFT: "left",
    KEY_RIGHT: "right", KEY_ENTER: "enter",
}

# google_tv's D-pad already gets Up/Down hold-to-scroll for free (the
# keypad's global baseline — see hardware/keypad.py). Left/Right don't
# repeat by default anywhere else in the app (holding Back or a char-picker
# commit repeatedly would misbehave — see _REPEATABLE_KEYS's comment), so
# this layers them on only while google_tv is actually the active device,
# via hardware.input.set_repeatable_keys(), and drops them again the moment
# it isn't.
_GOOGLE_TV_REPEAT_KEYS = {KEY_LEFT, KEY_RIGHT}

# The keypad's default 100ms repeat pace (KEYPAD_REPEAT_INTERVAL_SECONDS)
# assumes a cheap action per event. An IR transmit isn't: measured ~103ms
# per ir-ctl send alone on this hardware, already past 100ms before any of
# the consumer's own overhead — so held-key events piled up in the keypad's
# event queue and kept draining, stale, after release (laggy/bursty, not a
# smooth scroll). 150ms leaves real margin above the measured floor.
_GOOGLE_TV_REPEAT_INTERVAL_SECONDS = 0.15


def _functions_for(home: str, device: str, location: str) -> list[dict]:
    """Functions to cycle for this leaf — voice commands
    (services.voice_device_service) if it's configured as a voice device,
    else the usual IR-signal-derived functions (services.signal_service)."""
    if voice_device_service.is_voice_device(device):
        return voice_device_service.list_functions(device)
    return signal_service.list_functions(home, device, location)


def _display_name(function: dict) -> str:
    """Line-2 function name — voice commands are shown exactly as authored
    (they're already human-readable phrases, not snake_case IR button
    stems), skipping format_name(): str.title() mangles an apostrophe
    (e.g. "it's" -> "It'S"), which would otherwise show up as a visible
    glitch on a name typed correctly in config/voice_devices.json."""
    return function["name"] if function["type"] == "voice" else format_name(function["name"])


def _device_label(device: str, location: str) -> str:
    """Line-1 title for a leaf device — location first, like a person
    would actually say it ("Hall Light", "Bedroom TV"), not "Light / Hall"
    — so two leaves under the same device (light/hall vs. light/kitchen)
    don't display identically as just "Light" with no way to tell which
    one is active."""
    name = format_name(device)
    return f"{format_name(location)} {name}" if location else name


def render_after_cycle(lcd: LCD, state: AppState) -> None:
    """Draw index-0 content for whatever state.active_device/location was
    just switched to, and arm the matching keypad repeat behavior — used
    by app/quick_actions.py's device-cycle handler (< Device/Device >) so
    switching devices shows the real, already-interactive screen right
    away instead of a separate "Active device" confirmation banner that
    only delayed the next real button press with a fixed sleep.

    Always index 0 because a leaf switch always resets show()'s own
    function_index to 0 too (see its leaf_changed handling below) — by the
    time show()'s loop actually runs again, it'll agree with what's drawn
    here rather than redraw something different out from under it."""
    home = home_service.get_active_home()
    device, location = state.active_device, state.active_location

    if not device:
        input_hw.set_repeatable_keys(set())
        input_hw.set_repeat_interval(None)
        lcd.write("No signals", format_name(home)[:16])
        return

    if device == DEFAULT_ACTIVE_DEVICE:
        input_hw.set_repeatable_keys(_GOOGLE_TV_REPEAT_KEYS)
        input_hw.set_repeat_interval(_GOOGLE_TV_REPEAT_INTERVAL_SECONDS)
        lcd.write(_device_label(device, location), "Remote Control")
        return

    input_hw.set_repeatable_keys(set())
    input_hw.set_repeat_interval(None)
    functions = _functions_for(home, device, location)
    if not functions:
        lcd.write(_device_label(device, location), "No signals")
        return

    name = _display_name(functions[0])
    if len(functions) > 1 and len(name) <= 14:
        line2 = f"<{name.center(14)}>"
    else:
        line2 = name
    lcd.write(_device_label(device, location), line2[:16])


def show(lcd: LCD, state: AppState) -> None:
    function_index = 0
    previous_leaf = (None, None)
    dpad_needs_redraw = True  # forces the first draw on entry, same as leaf_changed would

    while state.running:
        home = home_service.get_active_home()

        if state.active_device is None:
            state.active_device, state.active_location = signal_service.default_leaf_device(home)

        device, location = state.active_device, state.active_location
        leaf_changed = (device, location) != previous_leaf
        if leaf_changed:
            function_index = 0
            previous_leaf = (device, location)

        if not device:
            input_hw.set_repeatable_keys(set())
            input_hw.set_repeat_interval(None)
            lcd.write("No signals", format_name(home)[:16])
            if read_key() == KEY_QUIT:
                state.running = False
                return
            continue

        if device == DEFAULT_ACTIVE_DEVICE:
            input_hw.set_repeatable_keys(_GOOGLE_TV_REPEAT_KEYS)
            input_hw.set_repeat_interval(_GOOGLE_TV_REPEAT_INTERVAL_SECONDS)
            # This screen's text never changes on a button press (success
            # is silent — see _transmit), so only draw it when it actually
            # needs to change, not on every held-key repeat: a redundant
            # identical write here measured ~81ms on this LCD (bit-banged
            # software I2C), which was eating into the repeat interval
            # budget above just as much as the IR transmit itself.
            if leaf_changed or dpad_needs_redraw:
                lcd.write(_device_label(device, location), "Remote Control")
                dpad_needs_redraw = False
            key = read_key()
            if key == KEY_QUIT:
                state.running = False
                return
            button = _DPAD_BUTTONS.get(key)
            if button and not _transmit(lcd, home, device, location, button):
                dpad_needs_redraw = True  # _transmit overwrote the screen with an error
            continue

        input_hw.set_repeatable_keys(set())
        input_hw.set_repeat_interval(None)
        functions = _functions_for(home, device, location)
        if not functions:
            lcd.write(_device_label(device, location), "No signals")
            if read_key() == KEY_QUIT:
                state.running = False
                return
            continue

        function_index %= len(functions)
        function = functions[function_index]
        # Frame the name with < > when there's more than one function to
        # make it obvious Left/Right cycles to something else — nothing to
        # cycle to (and so no arrows) when a device only has the one.
        name = _display_name(function)
        if len(functions) > 1 and len(name) <= 14:
            line2 = f"<{name.center(14)}>"
        else:
            line2 = name
        lcd.write(_device_label(device, location), line2[:16])

        key = read_key()
        if key == KEY_QUIT:
            state.running = False
            return
        if key == KEY_LEFT:
            function_index = (function_index - 1) % len(functions)
        elif key == KEY_RIGHT:
            function_index = (function_index + 1) % len(functions)
        elif key == KEY_UP and function["type"] == "paired":
            _transmit(lcd, home, device, location, function["up"])
        elif key == KEY_DOWN and function["type"] == "paired":
            _transmit(lcd, home, device, location, function["down"])
        elif key == KEY_ENTER and function["type"] == "standalone":
            _transmit(lcd, home, device, location, function["button"])
        elif key == KEY_ENTER and function["type"] == "voice":
            _run_voice_function(lcd, device, location, function["steps"])


def _transmit(lcd: LCD, home: str, device: str, location: str, button: str) -> bool:
    """Returns False if it wrote an error message to the LCD (so a caller
    tracking whether the screen needs a redraw, e.g. google_tv's D-pad
    branch, knows to restore its normal display next), True on success."""
    path = signal_service.signal_path(home, device, button, location)
    if not path.exists():
        lcd.write(_device_label(device, location), "Signal missing")
        time.sleep(1)
        return False
    try:
        ir_transmitter.transmit(path)
        return True
    except HardwareError as e:
        lcd.write("TX Error", str(e)[:16])
        time.sleep(1)
        return False


def _run_voice_function(lcd: LCD, device: str, location: str, steps: list[dict]) -> bool:
    """Fires a voice-device function's step sequence — the same
    voice/androidtv/androidtv_app/IR step engine keypad presets use
    (services.preset_service.run_steps()), not a separate one, so e.g. a
    voice command can be followed by an IR signal (see config/
    voice_devices.json's "Rust" entry). Same True/False-on-error
    convention as _transmit; True only if every step succeeded."""
    lcd.write(_device_label(device, location), "Sending...")
    sent, total = preset_service.run_steps(steps)
    if sent < total:
        lcd.write(_device_label(device, location), f"{sent}/{total} sent")
        time.sleep(1)
        return False
    return True
