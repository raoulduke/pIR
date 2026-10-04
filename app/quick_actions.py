"""Global keypad quick actions.

Wired into hardware.input via set_quick_action_handler at startup (see
main.py), so device/preset/menu shortcuts on the physical keypad fire from
anywhere in the app — read_key() intercepts them before returning to
whichever screen is currently blocked waiting for a key.

init(state) must be called once at startup (see main.py) before any of
these touch AppState — handle() itself stays a plain function of just the
key, matching hardware.input.set_quick_action_handler's calling convention.
"""

import time

from hardware.lcd import lcd
from hardware import ir_transmitter
from hardware.input import (
    KEY_DEVICE_1_UP, KEY_DEVICE_1_DOWN, KEY_DEVICE_2_UP, KEY_DEVICE_2_DOWN,
    KEY_DEVICE_3_UP, KEY_DEVICE_3_DOWN, KEY_DEVICE_4_UP, KEY_DEVICE_4_DOWN,
    KEY_PRESET_1, KEY_PRESET_2, KEY_PRESET_3, KEY_PRESET_4,
    KEY_DEVICE_PREV, KEY_DEVICE_NEXT, KEY_MENU, KEY_QUIT,
    DeviceCycledAway,
)
from services import (
    device_slot_service, preset_service, signal_service, config_service, home_service,
)
from app.constants import ASYNC_SEND_FLASH_SECONDS
from app.exceptions import HardwareError
from app.state import AppState

_DEVICE_KEYS = {
    KEY_DEVICE_1_UP: (0, "up"), KEY_DEVICE_1_DOWN: (0, "down"),
    KEY_DEVICE_2_UP: (1, "up"), KEY_DEVICE_2_DOWN: (1, "down"),
    KEY_DEVICE_3_UP: (2, "up"), KEY_DEVICE_3_DOWN: (2, "down"),
    KEY_DEVICE_4_UP: (3, "up"), KEY_DEVICE_4_DOWN: (3, "down"),
}
_PRESET_KEYS = {KEY_PRESET_1: 1, KEY_PRESET_2: 2, KEY_PRESET_3: 3, KEY_PRESET_4: 4}
_CYCLE_DIRECTIONS = {KEY_DEVICE_PREV: -1, KEY_DEVICE_NEXT: 1}

_state: AppState | None = None

# Set for the duration of _handle_menu() (and everything it calls) so
# _handle_device_cycle can tell it's currently nested under a Menu screen —
# see hardware.input.DeviceCycledAway.
_in_menu = False


def init(state: AppState) -> None:
    """Give this module access to AppState. Call once from main.py."""
    global _state
    _state = state


def handle(key: str) -> bool:
    """Returns True if hardware.input.read_key() should return None to its
    caller right away instead of continuing to block for another key —
    used by the "Preset Device N" slot buttons, Preset Function, and
    < Device/Device > so whatever they've already drawn doesn't just sit
    there with the interrupted screen's own read_key() call left blocked
    indefinitely behind it, silently, until some unrelated keypress
    happens to wake it back up. Returning True makes read_key() return
    None to its caller right away instead — every screen's own read loop
    already redraws unconditionally on a None key, except a few of
    screens/active_device_screen.py's own (see its handling of a None key
    there), which is also exactly what lets that screen notice a leaf
    change and start its Preset-name reveal (_reveal_presets()) the
    instant a device switch actually happens, not one extra keypress
    later. Menu keeps the previous behavior (False/None) — it already
    redraws synchronously as part of replacing the screen entirely, so
    there's nothing stale left to clear."""
    if key in _DEVICE_KEYS:
        _handle_device(key)
        return True
    elif key in _PRESET_KEYS:
        _handle_preset(_PRESET_KEYS[key])
        return True
    elif key in _CYCLE_DIRECTIONS:
        _handle_device_cycle(key)
        return True
    elif key == KEY_MENU:
        _handle_menu()
    return False


def _handle_device(key: str) -> None:
    slot_index, direction = _DEVICE_KEYS[key]
    offset = config_service.get_device_slot_offset()
    slot = device_slot_service.get_slot(offset, slot_index)

    if slot is None:
        lcd.write(f"Device {slot_index + 1}", "Not configured")
        time.sleep(1)
        return

    label = slot.get("label", "")[:16]

    if slot.get("type") == "voice":
        _handle_voice_device(label, slot, direction)
        return

    button = slot.get(f"{direction}_button")
    if not button:
        lcd.write(label, "No action set")
        time.sleep(1)
        return

    path = signal_service.signal_path(
        slot["home"], slot["device"], button, slot.get("location", "")
    )
    if not path.exists():
        lcd.write(label, "Signal missing")
        time.sleep(1)
        return

    try:
        ir_transmitter.transmit(path)
        lcd.write(label, button.replace("_", " ").title())
    except HardwareError as e:
        lcd.write("TX Error", str(e)[:16])
    time.sleep(0.6)


def _handle_voice_device(label: str, slot: dict, direction: str) -> None:
    command = slot.get(f"{direction}_command")
    if not command:
        lcd.write(label, "No action set")
        time.sleep(1)
        return

    # Non-blocking (see services/preset_service.py's run_steps_background)
    # — voice_service.send_command() is a real HTTP round-trip (up to 15s
    # timeout), so blocking here used to leave the keypad unresponsive for
    # however long that took. Shows "Sending..." for a brief fixed flash
    # (ASYNC_SEND_FLASH_SECONDS — the command is already sending in the
    # background during it, this is only so the message is actually seen
    # before the caller's own next redraw would otherwise overwrite it)
    # then returns; a failure overwrites the label whenever it's actually
    # known, a success needs no follow-up message.
    lcd.write(label, "Sending...")

    def _on_result(sent, total, failed_step):
        if failed_step is not None:
            lcd.write(label, f"Err: {preset_service.describe_step(failed_step)}"[:16])

    preset_service.run_steps_background([{"type": "voice", "command": command}], _on_result)
    time.sleep(ASYNC_SEND_FLASH_SECONDS)


def _handle_preset(number: int) -> None:
    """Preset Function N — runs the active device's override for slot N if
    it has one (see services/preset_service.py), else the global default.
    Active device is read-only here; presses never change it.

    Shows the preset's configured name (e.g. "Candlelight", "Rest")
    instead of the generic "Preset N" — falls back to "Preset N" if the
    entry has no name or isn't configured at all, so there's still
    something on line 1 either way."""
    device = _state.active_device or ""
    location = _state.active_location or ""

    name, steps = preset_service.get_preset(number, device, location)
    label = (name or f"Preset {number}")[:16]

    if not steps:
        lcd.write(label, "Not configured")
        time.sleep(1)
        return

    # Non-blocking (see services/preset_service.py's run_steps_background)
    # — a voice/hue step in here is a real network round-trip, so blocking
    # used to leave "Running..." on screen and the keypad unresponsive for
    # however long that took. Shows "Running..." for a brief fixed flash
    # (ASYNC_SEND_FLASH_SECONDS — steps are already running in the
    # background during it, this is only so the message is actually seen
    # before the caller's own next redraw would otherwise overwrite it)
    # then returns; a failure overwrites the label whenever it's actually
    # known — which step failed (preset_service.describe_step()), same for
    # a single-step preset (e.g. Preset 1's default "Back" IR signal) and a
    # multi-step one (e.g. "Rust"'s voice command + IR signal) — a full
    # success needs no follow-up message.
    lcd.write(label, "Running...")

    def _on_result(sent, total, failed_step):
        if failed_step is not None:
            lcd.write(label, f"Failed: {preset_service.describe_step(failed_step)}"[:16])

    preset_service.run_steps_background(steps, _on_result)
    time.sleep(ASYNC_SEND_FLASH_SECONDS)


def _handle_device_cycle(key: str) -> None:
    """< Device / Device > — cycle screens.active_device_screen's active
    device. Global (works from any screen), replacing the old Send-screen
    -scoped cycle. Every light device collapses into one "Lights" stop,
    and every tv device into one "TV" stop, here (screens.
    active_device_screen.cycle_top_level()) — Left/Right, handled locally
    by active_device_screen.show() itself once inside one of those groups,
    is what actually moves between its individual leaves (Google TV, its
    own top-level stop, isn't part of the TV group — see app.constants.
    MENU_DEVICE_LABELS)."""
    direction = _CYCLE_DIRECTIONS[key]
    home = home_service.get_active_home()
    current = (_state.active_device, _state.active_location)
    if current[0] is None:
        current = signal_service.default_leaf_device(home)

    import screens.active_device_screen as active_device_screen
    device, location = active_device_screen.cycle_top_level(home, current, direction)
    _state.active_device, _state.active_location = device, location

    # Show the real, already-interactive screen for the new device right
    # away — no separate "Active device" confirmation banner, which used a
    # fixed sleep that only delayed the next real button press without
    # adding anything the screen itself doesn't already make obvious.
    active_device_screen.render_after_cycle(lcd, _state)

    if _in_menu:
        # Cycling devices backs out of any Menu screen immediately, on
        # this very keypress — not just a redraw that a Menu screen sits
        # on top of, invisible, until some unrelated key is next pressed.
        # Deliberately left uncaught here (see DeviceCycledAway) so it
        # unwinds all the way back to active_device_screen.show()'s own
        # blocked read_key() call, from before the Menu key was pressed —
        # not just out of _handle_menu — so *that* screen's very next
        # read_key() call is a fresh one against the already-switched
        # device, instead of the stale one it was mid-call on when Menu
        # interrupted it (which would otherwise still need its usual
        # "this key isn't safe to apply to it sight unseen" discard-and-
        # redraw, costing an extra throwaway keypress on top of this one).
        raise DeviceCycledAway()


def _handle_menu() -> None:
    """Menu key — the escape hatch. Opens screens.main_menu and dispatches
    to whichever screen is chosen directly, since a quick action doesn't
    feed back into an outer dispatch loop (there isn't one anymore — see
    screens/active_device_screen.py, which owns the app's main loop)."""
    # This can fire while active_device_screen.py has google_tv's D-pad
    # Left/Right hold-to-scroll turned on (it re-asserts that per loop
    # iteration, but this quick action runs from inside its blocked
    # read_key() call, mid-iteration) — clear it (and its widened repeat
    # interval) so e.g. holding Left in a menu screen's list doesn't
    # rapid-fire "Back" or, worse, rapid-delete characters in the D-pad
    # text entry widget at the wrong pace. active_device_screen.py
    # reasserts the right state again on its own next loop pass regardless.
    from hardware import input as input_hw
    input_hw.set_repeatable_keys(set())
    input_hw.set_repeat_interval(None)

    import screens.main_menu as main_menu
    import screens.command_screen as command_screen
    import screens.home_screen as home_screen
    import screens.receive_screen as receive_screen
    import screens.rename_menu_screen as rename_menu_screen
    import screens.network_screen as network_screen
    from app.constants import (
        MENU_LEARN, MENU_RENAME, MENU_COMMAND, MENU_HOME, MENU_NETWORK,
    )

    global _in_menu
    _in_menu = True
    try:
        while _state.running:
            choice = main_menu.show(lcd, _state)

            if choice is None:
                return
            if choice == KEY_QUIT:
                _state.running = False
                return

            if choice == MENU_LEARN:
                result = receive_screen.show(lcd, _state)
            elif choice == MENU_RENAME:
                result = rename_menu_screen.show(lcd, _state)
            elif choice == MENU_COMMAND:
                result = command_screen.show(lcd)
            elif choice == MENU_HOME:
                result = home_screen.show(lcd, _state)
            elif choice == MENU_NETWORK:
                result = network_screen.show(lcd)
            else:
                result = None

            if result == KEY_QUIT:
                _state.running = False
                return
    finally:
        # Not catching DeviceCycledAway here — it's meant to propagate
        # further up, past this function entirely, to
        # active_device_screen.show()'s own blocked read_key() call (see
        # DeviceCycledAway and _handle_device_cycle). This finally only
        # resets the "are we nested under a Menu screen" flag on the way
        # out, however _handle_menu is exiting.
        _in_menu = False
