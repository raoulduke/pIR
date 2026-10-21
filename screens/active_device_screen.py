"""Active Device Control — the boot screen and the app's main loop (runs
until state.running is False). Other screens are reached through the Menu
key; < Device/Device > switches the active device (app/quick_actions.py).

One active leaf (state.active_device, state.active_location), drawn by its
shape:
  - Google TV (DEFAULT_ACTIVE_DEVICE): Up/Down/Left/Right/Enter fire the
    matching D-pad signal.
  - A collapsed group stop (MENU_DEVICE_SENTINELS: Lights, TVs): shows
    "Press Enter"; Enter drills into the group's first leaf.
  - A group leaf (light, tv): Enter fires Power, Up/Down the paired action
    (_menu_power_paired()); Left/Right move between the group's leaves.
  - A fixed-mapping device (at most one Power and one paired function,
    nothing else — detected by shape, e.g. receiver, ac): Enter fires
    Power, Up/Down the paired action, no Left/Right.
  - Anything else: Left/Right cycle functions, Up/Down fire a paired one,
    Enter a standalone one; line 2 shows the function in < >.
Functions come from IR signal files, or config/step_devices.json for a
step device (_functions_for()). Presets run from the Preset Function keys
(app/quick_actions.py), not this screen."""

import logging
import time
from hardware.lcd import (
    LCD, GLYPH_HOME, GLYPH_CC_LEFT, GLYPH_CC_RIGHT, GLYPH_BACK_ARROW,
    GLYPH_BACK_SHAFT, GLYPH_PLAY_PAUSE,
)
from hardware import ir_transmitter
from hardware.lcd import lcd as lcd_hw
from hardware import input as input_hw
from hardware.input import (
    read_key, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER, KEY_QUIT,
    DeviceCycledAway,
)
from services import (
    home_service, signal_service, step_device_service, preset_service, androidtv_service, hue_service,
    cast_service,
)
from screens.widgets.format import format_name, frame
from app.constants import (
    DEFAULT_ACTIVE_DEVICE, MENU_DEVICE_LABELS, MENU_DEVICE_SENTINELS, MENU_SENTINEL_TO_DEVICE,
    ASYNC_SEND_FLASH_SECONDS, STATEMENT_LIGHT_OFF, YOUTUBE_PACKAGE,
)
from app.state import AppState
from app.exceptions import HardwareError

log = logging.getLogger(__name__)

_DPAD_BUTTONS = {
    KEY_UP: "up", KEY_DOWN: "down", KEY_LEFT: "left",
    KEY_RIGHT: "right", KEY_ENTER: "enter",
}

# Google TV's D-pad also repeats Left/Right while held (only on that screen:
# elsewhere a repeated Left would back out several levels).
_GOOGLE_TV_REPEAT_KEYS = {KEY_LEFT, KEY_RIGHT}

# Slower than the default 100ms repeat: each repeat is an IR send (~100ms),
# and faster repeats piled up and kept firing after release.
_GOOGLE_TV_REPEAT_INTERVAL_SECONDS = 0.15

# How long each preset frame shows in the reveal after switching to a leaf.
# The reveal runs once, then the screen blocks on read_key() with no timeout:
# a continuous cycle would wake the CPU every second or two for as long as
# the screen is up.
_PRESET_REVEAL_FRAME_SECONDS = 1.3

# Line 2 on every leaf of a group: one caption per group.
_MENU_CONTROL_LABEL = {"light": "Light Control", "tv": "TV Control"}


def menu_leaves(home: str, device: str) -> list[tuple[str, str]]:
    """Every leaf under group *device* ("light", "tv"), in list order: what
    Left/Right cycles through, and what the group stop's Enter opens (the
    first)."""
    return [leaf for leaf in signal_service.list_leaf_devices(home) if leaf[0] == device]


def cycle_menu_leaf(home: str, device: str, current: tuple[str, str], direction: int) -> tuple[str, str]:
    leaves = menu_leaves(home, device)
    if not leaves:
        return current
    try:
        index = leaves.index(current)
    except ValueError:
        return leaves[0]
    return leaves[(index + direction) % len(leaves)]


def cycle_top_level(home: str, current: tuple[str, str], direction: int) -> tuple[str, str]:
    """The next stop for < Device/Device >: each ungrouped leaf is a stop, and
    each group is one (its sentinel). From inside a group, it moves to the
    next stop outside it — moving within a group is Left/Right's job."""
    top_level: list[tuple[str, str]] = []
    seen_devices: set[str] = set()
    for leaf in signal_service.list_leaf_devices(home):
        device = leaf[0]
        if device in MENU_DEVICE_LABELS:
            if device not in seen_devices:
                top_level.append((MENU_DEVICE_SENTINELS[device], ""))
                seen_devices.add(device)
        else:
            top_level.append(leaf)
    if not top_level:
        return current

    current_device = current[0]
    if current_device in MENU_DEVICE_LABELS:
        effective_current = (MENU_DEVICE_SENTINELS[current_device], "")
    elif current_device in MENU_SENTINEL_TO_DEVICE:
        effective_current = (current_device, "")
    else:
        effective_current = current
    try:
        index = top_level.index(effective_current)
    except ValueError:
        return top_level[0]
    return top_level[(index + direction) % len(top_level)]


def _fixed_mapping_functions(functions: list[dict]) -> tuple[dict | None, dict | None] | None:
    """(power, paired) if *functions* is at most one Power-named function and
    at most one paired one, nothing else — Enter and Up/Down then cover it,
    with no cycling. None otherwise."""
    paired_types = ("paired", "step_paired")
    power = [f for f in functions if f["type"] not in paired_types and f["name"].lower() == "power"]
    paired = [f for f in functions if f["type"] in paired_types]
    accounted = power + paired
    other = [f for f in functions if f not in accounted]
    if other or len(power) > 1 or len(paired) > 1 or not accounted:
        return None
    return (power[0] if power else None, paired[0] if paired else None)


def _menu_power_paired(functions: list[dict]) -> tuple[dict | None, dict | None]:
    """(power, paired) for a group leaf, where Left/Right is taken by group
    cycling: Power-named function for Enter (or the leaf's only function), its
    one paired function for Up/Down. Any other functions are left to Preset
    Function shortcuts."""
    paired_types = ("paired", "step_paired")
    power_matches = [f for f in functions if f["type"] not in paired_types and f["name"].lower() == "power"]
    paired_matches = [f for f in functions if f["type"] in paired_types]
    power = power_matches[0] if power_matches else None
    paired = paired_matches[0] if len(paired_matches) == 1 else None
    if power is None and paired is None and len(functions) == 1:
        power = functions[0]
    return power, paired


def _persistent_presets_line2(power: dict | None, paired: dict | None, frames: list[str]) -> str | None:
    """Line 2 for a fixed-mapping leaf with a single action whose presets fit
    one frame: the presets, shown permanently instead of the one-shot reveal.
    None otherwise (several frames would need a repaint timer, costing
    battery)."""
    if (power is None) == (paired is None) or len(frames) != 1:
        return None
    return frames[0]


def _fire_power(lcd: LCD, home: str, device: str, location: str, function: dict) -> None:
    if function["type"] == "standalone":
        _transmit(lcd, home, device, location, function["button"])
    elif function["type"] == "step":
        _run_step_function(lcd, device, location, function["steps"])


def _fire_paired(lcd: LCD, home: str, device: str, location: str, function: dict, direction: str) -> None:
    """*direction*: "up" or "down"."""
    if function["type"] == "paired":
        _transmit(lcd, home, device, location, function[direction])
    elif function["type"] == "step_paired":
        _run_step_function(lcd, device, location, function[f"{direction}_steps"])


def _fire_mapped(lcd: LCD, home: str, device: str, location: str, key: str,
                 power: dict | None, paired: dict | None) -> bool:
    """Enter fires *power*, Up/Down fire *paired* — the fixed key mapping
    shared by fixed-mapping devices and Lights leaves. Returns True if it
    fired (the screen then needs redrawing)."""
    if key == KEY_ENTER and power:
        _fire_power(lcd, home, device, location, power)
    elif key in (KEY_UP, KEY_DOWN) and paired:
        _fire_paired(lcd, home, device, location, paired, "up" if key == KEY_UP else "down")
    else:
        return False
    return True


def _cycle_line2(functions: list[dict], function: dict) -> str:
    """Line 2 for the cycling branch: the function's name, in < > when there's
    more than one."""
    name = _display_name(function)
    return frame(name) if len(functions) > 1 else name


def _functions_for(home: str, device: str, location: str) -> list[dict]:
    """The leaf's functions: from config/step_devices.json for a step device,
    else from its IR signal files."""
    if step_device_service.is_step_device(device, location):
        return step_device_service.list_functions(device, location)
    return signal_service.list_functions(home, device, location)


def _display_name(function: dict) -> str:
    """Line-2 function name. Step functions show as written (format_name()'s
    title-casing mangles apostrophes); IR stems are formatted."""
    step_types = ("step", "step_paired")
    return function["name"] if function["type"] in step_types else format_name(function["name"])


def _device_label(device: str, location: str) -> str:
    """Line-1 title, location first ("Bedroom AC"). A group leaf (light, tv)
    shows just the location — the group is clear from context. Falls back to
    the device name alone if it won't fit 16 columns ("Receiver")."""
    if device in MENU_DEVICE_LABELS:
        return format_name(location) if location else MENU_DEVICE_LABELS[device]
    name = format_name(device)
    label = f"{format_name(location)} {name}" if location else name
    return label if len(label) <= 16 else name


def _menu_leaf_label(home: str, device: str, location: str) -> str:
    """_device_label() for a group leaf, in < > when the group has more than
    one leaf to move to."""
    label = _device_label(device, location)
    return frame(label) if len(menu_leaves(home, device)) > 1 else label


# Hand-picked short forms for preset names (keyed lowercase). A name with no
# entry makes _preset_entries() fall back to full names.
_PRESET_ABBREVIATIONS = {
    "back": GLYPH_BACK_ARROW + GLYPH_BACK_SHAFT,
    "rust": "Rst",
    "rest": "Rst",
    "home": GLYPH_HOME,
    "mute": "Ssh",
    "power": "Pwr",
    "youtube": "YT",  # the plause glyph while YouTube is in front (update_play_glyph())
    "captions": GLYPH_CC_LEFT + GLYPH_CC_RIGHT,
    "candlelight": "Cdl",
    "warm white": "Wrm",
    "daylight": "Day",
    "picture": "Pic",
}


def update_play_glyph(*_) -> None:
    """With YouTube in front, the plause glyph replaces "YT" on the preset
    line, as the action Preset 3 would take: play when paused, pause when
    playing (Cast's state). Wired to the TV's app and Cast's state in
    main.py; any thread."""
    if androidtv_service.current_app() != YOUTUBE_PACKAGE:
        lcd_hw.set_status_line2_swap(None)
        return
    playing = cast_service.player_state() in ("PLAYING", "BUFFERING")
    lcd_hw.set_play_glyph("pause" if playing else "play")
    # The space keeps the other presets where they were.
    lcd_hw.set_status_line2_swap((_PRESET_ABBREVIATIONS["youtube"], GLYPH_PLAY_PAUSE + " "))


def _justify(words: list[str], width: int = 16) -> str:
    """Spread *words* across *width* columns — first flush left, last flush
    right, the gaps between as equal as possible (any leftover spaces go to
    the middle gaps first, so it stays visually symmetric). Falls back to
    single-space packing if the words don't leave room for one space per gap."""
    gaps = len(words) - 1
    spaces = width - sum(len(w) for w in words)
    if gaps < 1 or spaces < gaps:
        return " ".join(words)[:width]
    base, extra = divmod(spaces, gaps)
    order = sorted(range(gaps), key=lambda i: abs(i - (gaps - 1) / 2))
    widths = [base + (1 if i in order[:extra] else 0) for i in range(gaps)]
    return "".join(w + " " * n for w, n in zip(words, widths)) + words[-1]


def _preset_entries(device: str, location: str) -> list[str]:
    """The leaf's Preset Function names as display frames for the reveal ([]
    if none). If every name has an abbreviation they all share one frame;
    otherwise full names, packed as many to a frame as fit. Computed once per
    leaf: get_preset() reads the file each time."""
    slots = []
    for number in range(1, 5):
        name, _ = preset_service.get_preset(number, device, location)
        if name:
            slots.append((number, name))

    if not slots:
        return []

    abbreviations = [_PRESET_ABBREVIATIONS.get(name.lower()) for _, name in slots]
    if all(abbreviations):
        if len(abbreviations) == 4:
            return [_justify(abbreviations)]
        return [" ".join(abbreviations)[:16]]

    parts = [name[:16] for _, name in slots]
    frames = []
    current = ""
    for part in parts:
        candidate = f"{current} {part}" if current else part
        if len(candidate) <= 16:
            current = candidate
        else:
            frames.append(current)
            current = part
    if current:
        frames.append(current)
    return frames


def _reveal_presets(lcd: LCD, state: AppState, device: str, location: str, normal_line2: str, frames: list[str],
                    override=None) -> str | None:
    """After switching to a leaf, show its preset frames on line 2, one every
    _PRESET_REVEAL_FRAME_SECONDS, then restore *normal_line2* and wait for a
    key with no timeout. Returns the key that cut it short, or the final
    read_key()'s result.

    Returns None if the active leaf changes, or when a quick action fires
    mid-reveal (told apart from a frame timeout by how soon read_key()
    returned) — that action may have drawn over line 1, and None makes the
    caller redraw. *override*, called before each frame, can replace the rest
    of the reveal (a Hue light found off at the wall)."""
    for frame in frames:
        if (state.active_device, state.active_location) != (device, location):
            return None
        if override is not None and (text := override()) is not None:
            lcd.write_line2(text)
            return read_key()
        lcd.write_line2(frame)
        frame_start = time.monotonic()
        key = read_key(timeout=_PRESET_REVEAL_FRAME_SECONDS)
        if key is not None:
            return key
        if time.monotonic() - frame_start < _PRESET_REVEAL_FRAME_SECONDS * 0.9:
            return None  # a quick action, not a timeout

    if (state.active_device, state.active_location) != (device, location):
        return None
    lcd.write_line2(normal_line2[:16])
    return read_key()


def _hue_ids(functions: list[dict]) -> list[str]:
    """Hue light IDs this leaf's functions control, in order, no repeats."""
    ids: list[str] = []
    for function in functions:
        for key in ("steps", "up_steps", "down_steps"):
            for light_id in preset_service.hue_light_ids(function.get(key, [])):
                if light_id not in ids:
                    ids.append(light_id)
    return ids


def _light_off_label(hue_ids: list[str]) -> str | None:
    """STATEMENT_LIGHT_OFF when every Hue light on this leaf was last found
    unreachable — Enter/Up/Down and its presets send nothing then. A leaf
    with only some lights off (Kitchen) still sends to the rest."""
    if hue_ids and all(hue_service.is_known_unreachable(i) for i in hue_ids):
        return STATEMENT_LIGHT_OFF
    return None


def _check_hue(lcd: LCD, state: AppState, device: str, location: str, hue_ids: list[str], normal_line2: str) -> None:
    """Ask the bridge once whether this leaf's lights are reachable, in the
    background so moving between lights never waits on it. Redraws line 2
    if the answer changes what it should show and the leaf is still up."""
    was_off = _light_off_label(hue_ids)

    def _on_done():
        off = _light_off_label(hue_ids)
        if off != was_off and (state.active_device, state.active_location) == (device, location):
            lcd.write_line2((off or normal_line2)[:16])

    hue_service.check_reachable_background(hue_ids, _on_done)


def render_after_cycle(lcd: LCD, state: AppState) -> None:
    """Draw the newly switched-to device's screen and set its key repeat —
    for the < Device/Device > quick action, so the switch shows at once.
    Function index 0, matching show()'s reset on a leaf change."""
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
        frames = _preset_entries(device, location)
        lcd.write_with_status(_device_label(device, location), frames[0] if len(frames) == 1 else "Remote Control")
        return

    if device in MENU_SENTINEL_TO_DEVICE:
        input_hw.set_repeatable_keys(set())
        input_hw.set_repeat_interval(None)
        lcd.write(MENU_DEVICE_LABELS[MENU_SENTINEL_TO_DEVICE[device]], "Press Enter")
        return

    if device in MENU_DEVICE_LABELS:
        input_hw.set_repeatable_keys(set())
        input_hw.set_repeat_interval(None)
        frames = _preset_entries(device, location)
        off_label = _light_off_label(_hue_ids(_functions_for(home, device, location)))
        lcd.write(_menu_leaf_label(home, device, location),
                  off_label or (frames[0] if len(frames) == 1 else "" if not frames else _MENU_CONTROL_LABEL[device]))
        return

    input_hw.set_repeatable_keys(set())
    input_hw.set_repeat_interval(None)
    functions = _functions_for(home, device, location)
    if not functions:
        lcd.write(_device_label(device, location), "No signals")
        return

    fixed = _fixed_mapping_functions(functions)
    if fixed is not None:
        power, paired = fixed
        frames = _preset_entries(device, location)
        line2 = _persistent_presets_line2(power, paired, frames) \
            or ("" if not frames else f"{format_name(paired['name'])} Control" if paired else "Power")
        lcd.write(_device_label(device, location), line2[:16])
        return

    line2 = _cycle_line2(functions, functions[0])
    lcd.write(_device_label(device, location), line2[:16])


def show(lcd: LCD, state: AppState) -> None:
    function_index = 0
    previous_leaf = (None, None)
    dpad_needs_redraw = True  # draw on entry
    preset_entries: list[str] = []  # recomputed on a leaf change

    while state.running:
        try:
            home = home_service.get_active_home()

            if state.active_device is None:
                state.active_device, state.active_location = signal_service.default_leaf_device(home)

            device, location = state.active_device, state.active_location
            androidtv_service.set_on_tv_screen(device == DEFAULT_ACTIVE_DEVICE)
            leaf_changed = (device, location) != previous_leaf
            if leaf_changed:
                function_index = 0
                previous_leaf = (device, location)
                # A group stop has no presets of its own.
                preset_entries = _preset_entries(device, location) if device and device not in MENU_SENTINEL_TO_DEVICE else []

            reveal_presets = leaf_changed and bool(device) and device not in MENU_SENTINEL_TO_DEVICE

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
                # Drawn only when it changes, not on every held-key repeat: a
                # redundant write costs ~80ms on this bus. Presets stay up
                # permanently when they fit one frame.
                persistent = len(preset_entries) == 1
                remote_label = preset_entries[0] if persistent else "Remote Control"
                if leaf_changed or dpad_needs_redraw:
                    # Line 1 ends in the TV connection's status character.
                    lcd.write_with_status(_device_label(device, location), remote_label)
                    dpad_needs_redraw = False
                key = _reveal_presets(lcd, state, device, location, remote_label, preset_entries) \
                    if reveal_presets and not persistent else read_key()
                if key is None:
                    # None: a quick action drew over the screen; redraw it.
                    dpad_needs_redraw = True
                    continue
                if key == KEY_QUIT:
                    state.running = False
                    return
                # D-pad keys are IR, but they still count as activity that keeps an
                # open TV connection from idling out.
                androidtv_service.keepalive()
                # < Device/Device > can switch devices inside read_key(); never fire a key
                # at a device that's no longer active.
                if (state.active_device, state.active_location) != (device, location):
                    continue
                button = _DPAD_BUTTONS.get(key)
                if button and not _transmit(lcd, home, device, location, button):
                    dpad_needs_redraw = True  # _transmit overwrote the screen with an error
                continue

            if device in MENU_SENTINEL_TO_DEVICE:
                underlying = MENU_SENTINEL_TO_DEVICE[device]
                input_hw.set_repeatable_keys(set())
                input_hw.set_repeat_interval(None)
                if leaf_changed or dpad_needs_redraw:
                    lcd.write(MENU_DEVICE_LABELS[underlying], "Press Enter")
                    dpad_needs_redraw = False
                key = read_key()
                if key is None:
                    # None: a quick action drew over the screen; redraw it.
                    dpad_needs_redraw = True
                    continue
                if key == KEY_QUIT:
                    state.running = False
                    return
                if (state.active_device, state.active_location) != (device, location):
                    continue
                if key == KEY_ENTER:
                    leaves = menu_leaves(home, underlying)
                    if leaves:
                        state.active_device, state.active_location = leaves[0]
                continue

            if device in MENU_DEVICE_LABELS:
                # Presets stay up permanently when they fit one frame.
                persistent = len(preset_entries) == 1
                control_label = preset_entries[0] if persistent else "" if not preset_entries else _MENU_CONTROL_LABEL[device]
                functions = _functions_for(home, device, location)
                hue_ids = _hue_ids(functions)
                # Checked once on arrival; the last known state shows meanwhile.
                if leaf_changed and hue_ids:
                    _check_hue(lcd, state, device, location, hue_ids, control_label)
                off_label = _light_off_label(hue_ids)
                input_hw.set_repeatable_keys(set())
                input_hw.set_repeat_interval(None)
                if leaf_changed or dpad_needs_redraw:
                    lcd.write(_menu_leaf_label(home, device, location), off_label or control_label)
                    dpad_needs_redraw = False
                key = _reveal_presets(lcd, state, device, location, control_label, preset_entries,
                                      override=lambda: _light_off_label(hue_ids)) \
                    if reveal_presets and not persistent and not off_label else read_key()
                if key is None:
                    # See the google_tv branch above's identical handling.
                    dpad_needs_redraw = True
                    continue
                if key == KEY_QUIT:
                    state.running = False
                    return
                # Device may have switched inside read_key() (see above).
                if (state.active_device, state.active_location) != (device, location):
                    continue
                if key == KEY_LEFT:
                    state.active_device, state.active_location = cycle_menu_leaf(home, device, (device, location), -1)
                    continue
                if key == KEY_RIGHT:
                    state.active_device, state.active_location = cycle_menu_leaf(home, device, (device, location), 1)
                    continue
                if _light_off_label(hue_ids) and key in (KEY_ENTER, KEY_UP, KEY_DOWN):
                    # Off at the wall: send nothing, but check again.
                    _check_hue(lcd, state, device, location, hue_ids, control_label)
                    continue
                power, paired = _menu_power_paired(functions)
                if _fire_mapped(lcd, home, device, location, key, power, paired):
                    dpad_needs_redraw = True
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

            fixed = _fixed_mapping_functions(functions)
            if fixed is not None:
                power, paired = fixed
                persistent = _persistent_presets_line2(power, paired, preset_entries)
                line2 = persistent or ("" if not preset_entries else f"{format_name(paired['name'])} Control" if paired else "Power")
                if leaf_changed or dpad_needs_redraw:
                    lcd.write(_device_label(device, location), line2[:16])
                    dpad_needs_redraw = False
                key = _reveal_presets(lcd, state, device, location, line2, preset_entries) \
                    if reveal_presets and not persistent else read_key()
                if key is None:
                    # See the google_tv branch above's identical handling.
                    dpad_needs_redraw = True
                    continue
                if key == KEY_QUIT:
                    state.running = False
                    return
                if (state.active_device, state.active_location) != (device, location):
                    continue
                if _fire_mapped(lcd, home, device, location, key, power, paired):
                    dpad_needs_redraw = True
                # Left/Right intentionally not handled — nothing to cycle to.
                continue

            function_index %= len(functions)
            function = functions[function_index]
            line2 = _cycle_line2(functions, function)
            lcd.write(_device_label(device, location), line2[:16])

            key = _reveal_presets(lcd, state, device, location, line2, preset_entries) \
                if reveal_presets else read_key()
            if key is None:
                # None: a quick action ran; back to the top, which notices a device
                # switch (the re-check below would update previous_leaf and hide it).
                continue
            if key == KEY_QUIT:
                state.running = False
                return

            # < Device/Device > can switch devices inside read_key(): apply the key to
            # the device now active, or let the top of the loop handle a different
            # shape of screen.
            new_device, new_location = state.active_device, state.active_location
            if (new_device, new_location) != (device, location):
                device, location = new_device, new_location
                previous_leaf = (device, location)
                if not device or device == DEFAULT_ACTIVE_DEVICE or device in MENU_SENTINEL_TO_DEVICE or device in MENU_DEVICE_LABELS:
                    continue
                functions = _functions_for(home, device, location)
                if not functions or _fixed_mapping_functions(functions) is not None:
                    continue
                function_index = 0
                function = functions[0]

            if key == KEY_LEFT:
                function_index = (function_index - 1) % len(functions)
            elif key == KEY_RIGHT:
                function_index = (function_index + 1) % len(functions)
            elif key == KEY_UP and function["type"] == "paired":
                _transmit(lcd, home, device, location, function["up"])
            elif key == KEY_DOWN and function["type"] == "paired":
                _transmit(lcd, home, device, location, function["down"])
            elif key == KEY_UP and function["type"] == "step_paired":
                _run_step_function(lcd, device, location, function["up_steps"])
            elif key == KEY_DOWN and function["type"] == "step_paired":
                _run_step_function(lcd, device, location, function["down_steps"])
            elif key == KEY_ENTER and function["type"] == "standalone":
                _transmit(lcd, home, device, location, function["button"])
            elif key == KEY_ENTER and function["type"] == "step":
                _run_step_function(lcd, device, location, function["steps"])
        except DeviceCycledAway:
            # < Device/Device > left a Menu and switched device (already drawn):
            # read afresh for it.
            continue


def _transmit(lcd: LCD, home: str, device: str, location: str, button: str) -> bool:
    """Send a learned signal. False if it showed an error (the caller then
    redraws)."""
    path = signal_service.signal_path(home, device, button, location)
    if not path.exists():
        log.warning("IR signal missing: %s", path)
        lcd.write(_device_label(device, location), "Signal missing")
        time.sleep(1)
        return False
    try:
        ir_transmitter.transmit(path)
        return True
    except HardwareError as e:
        log.error("IR transmit failed (%s): %s", path, e)
        lcd.write("TX Error", str(e)[:16])
        time.sleep(1)
        return False


def _run_step_function(lcd: LCD, device: str, location: str, steps: list[dict]) -> None:
    """Run a step device's function in the background (voice/Hue are network
    calls), flashing "Sending..."; a failure shows afterwards only if this
    screen is still up."""
    label = _device_label(device, location)
    lcd.write(label, "Sending...")
    pressed = input_hw.press_count()

    def _on_result(sent, total, failed_step):
        # Only over this screen: a key since means it's moved on (logged anyway).
        if failed_step is not None and input_hw.press_count() == pressed:
            lcd.write(label, f"Failed: {preset_service.describe_step(failed_step)}"[:16])

    preset_service.run_steps_background(steps, _on_result)
    time.sleep(ASYNC_SEND_FLASH_SECONDS)
