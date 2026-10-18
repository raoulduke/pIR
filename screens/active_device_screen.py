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
  - Each MENU_DEVICE_SENTINELS entry (app/constants.py's MENU_DEVICE_LABELS
    — currently "light" -> "Lights", "tv" -> "TV") is a synthetic
    top-level stop, not a real leaf — < Device/Device > lands here (see
    cycle_top_level()) instead of stepping through every leaf of that
    device individually. Shows the group's label / "Press Enter"; Enter
    drills into the first real leaf (menu_leaves() order) and every key
    besides Enter/Quit is a no-op here.
  - A "fixed-mapping" device — one Power-like function plus at most one
    paired action, nothing else (see _fixed_mapping_functions()) — skips
    Left/Right cycling entirely: Enter always fires Power, Up/Down always
    fire the paired action directly. This is detected generically by
    function *shape*, not a device allowlist, so it covers every light
    device (Power+Brightness) and, incidentally, any plain IR device
    shaped the same way — receiver (just a "volume" pair, no power
    button), tv/bedroom and ac (a "power" button plus one paired action)
    all get this treatment with no per-device code (2026-09-02).
    A device in MENU_DEVICE_LABELS specifically (light or tv) additionally
    repurposes Left/Right, otherwise unused here, to cycle to the
    next/previous leaf in its group (cycle_menu_leaf()) — the only way to
    move between individual leaves, since that group's sentinel (above)
    collapses them into one stop at the top level; a non-grouped
    fixed-mapping device (receiver, ac) just leaves Left/Right doing
    nothing, since there's nothing else to move to. A grouped leaf that
    isn't itself fixed-mapping-shaped (e.g. tv/living_room's lone
    "Picture" button, not named "power") still gets this treatment —
    Left/Right is already spoken for by group cycling, so there's nothing
    lost in also letting Enter fire it directly (see the MENU_DEVICE_LABELS
    branch in show()).
    Colour/temp presets live on the physical Preset Function 1-4 keys
    instead (app/quick_actions.py's _handle_preset ->
    services.preset_service.get_preset()/run_steps(), config/
    keypad_presets.json's "by_device" overrides keyed by device name) —
    not this screen at all, so they work the same way regardless of
    which screen is showing.
  - Every other device shows one function at a time — Left/Right cycle
    functions, Up/Down fire a paired function's two signals, Enter fires a
    standalone one. Line 2 frames the name in < > whenever there's more
    than one function to cycle to, so it's obvious Left/Right does
    something. Functions come from services.signal_service.list_functions
    (IR signal files) normally, or services.step_device_service (a step
    sequence — voice/androidtv/androidtv_app/IR/hue steps, all standalone)
    for a device configured in config/step_devices.json — see
    _functions_for().
"""

import logging
import time
from hardware.lcd import (
    LCD, GLYPH_HOME, GLYPH_CC_LEFT, GLYPH_CC_RIGHT, GLYPH_BACK_ARROW,
    GLYPH_BACK_SHAFT,
)
from hardware import ir_transmitter
from hardware import input as input_hw
from hardware.input import (
    read_key, KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT, KEY_ENTER, KEY_QUIT,
    DeviceCycledAway,
)
from services import home_service, signal_service, step_device_service, preset_service, androidtv_service
from screens.widgets.format import format_name, frame
from app.constants import (
    DEFAULT_ACTIVE_DEVICE, MENU_DEVICE_LABELS, MENU_DEVICE_SENTINELS, MENU_SENTINEL_TO_DEVICE,
    ASYNC_SEND_FLASH_SECONDS,
)
from app.state import AppState
from app.exceptions import HardwareError

log = logging.getLogger(__name__)

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

# How long each Preset Function display frame shows during the one-shot
# reveal right after switching to a leaf — see _reveal_presets().
#
# BATTERY NOTE: this used to be a continuously-scrolling marquee, then a
# continuous frame-by-frame cycle (2026-09-02) — both were reverted back
# to a bounded one-shot reveal specifically for battery reasons, not
# taste. Either continuous version replaced the screen's single,
# no-timeout, indefinitely-blocking read_key() with a perpetual
# read_key(timeout=1-3s) loop — the CPU never gets to idle while sitting
# on a device doing nothing, waking every 1-3s just to repaint the LCD,
# for as long as you're on that screen. CLAUDE.md's own
# sysstat-collect.timer finding flagged a wake every 10 minutes as bad
# for exactly this reason ("defeats idle power states all day"); this
# would have been ~200-400x more frequent than that. A bounded reveal has
# no ongoing cost at all — it only runs right after a device switch, then
# falls back to the original indefinite blocking wait.
_PRESET_REVEAL_FRAME_SECONDS = 1.3

# Line-2 caption shown on every individual leaf of a MENU_DEVICE_LABELS
# group — deliberately generic rather than naming the leaf's actual paired
# function (e.g. "Light Control" on every light regardless of whether its
# paired action is Brightness; "TV Control" on both Living Room TV's lone
# Picture button and Bedroom TV's Volume pair) — one predictable caption
# per group instead of one that varies leaf to leaf within it.
_MENU_CONTROL_LABEL = {"light": "Light Control", "tv": "TV Control"}


def menu_leaves(home: str, device: str) -> list[tuple[str, str]]:
    """Every leaf under *device* (a MENU_DEVICE_LABELS key — "light" or
    "tv"), in signal_service.list_leaf_devices() order — what Left/Right
    cycles between once inside that device's collapsed top-level stop, and
    what its MENU_DEVICE_SENTINELS entry's Enter drills into (the
    first one). Every Hue, Globe, or IR light (bedroom/ceiling/corner/
    kitchen_n/kitchen_s/lamp/window/hall) lives under the single "light"
    device this way, one location per room — see signals/home/light/,
    config/step_devices.json's "light/<location>" keys, and config/
    keypad_presets.json's "by_device" overrides, same "light/<location>"
    scheme. "tv" (Living Room TV/Bedroom TV) works the same way
    (2026-09-1x)."""
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
    """< Device/Device >'s actual stepping order: every leaf whose device
    isn't in MENU_DEVICE_LABELS is its own stop, and every leaf whose
    device is (light, tv) collapses into a single stop — that device's
    MENU_DEVICE_SENTINELS entry (see menu_leaves()/cycle_menu_leaf() for
    moving between individual leaves once inside one, via Left/Right).

    Being on any leaf of a grouped device already counts as "at" that
    group's collapsed stop for this purpose — so pressing < Device/Device >
    while on a specific leaf (already drilled in) jumps straight to the
    next/prev stop *outside that group*, the same as pressing it from the
    group's own undrilled menu screen itself, never stepping through the
    rest of the group one at a time (that's Left/Right's job, not this
    key's)."""
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
    """If *functions* is simple enough for fixed-mapping treatment (Enter
    fires the one Power-like function, Up/Down fire the one paired
    function, no Left/Right cycling needed) — at most one Power-like
    standalone/step function (matched by name, case-insensitively) and at
    most one paired/step_paired function, nothing else — return
    (power_or_None, paired_or_None). None otherwise (too complex, needs
    normal Left/Right cycling — e.g. google_home's several standalone step
    functions, none of them a paired action).

    Covers every light device (Power+Brightness) and also, generically,
    any plain IR device shaped the same way regardless of naming —
    receiver (just a "volume" pair, no power button at all), tv/bedroom
    and ac (a "power" button plus one paired action) all match this
    without needing to be named or listed specially (2026-09-02)."""
    paired_types = ("paired", "step_paired")
    power = [f for f in functions if f["type"] not in paired_types and f["name"].lower() == "power"]
    paired = [f for f in functions if f["type"] in paired_types]
    accounted = power + paired
    other = [f for f in functions if f not in accounted]
    if other or len(power) > 1 or len(paired) > 1 or not accounted:
        return None
    return (power[0] if power else None, paired[0] if paired else None)


def _menu_power_paired(functions: list[dict]) -> tuple[dict | None, dict | None]:
    """Like _fixed_mapping_functions() above, but for a MENU_DEVICE_LABELS
    leaf (light/tv) specifically — Left/Right is already claimed by
    cross-leaf group cycling there (see cycle_menu_leaf()), never
    per-function cycling, so unlike _fixed_mapping_functions() there's no
    "reject if anything else is left over" rule: a Power-named function
    (or, failing that, the leaf's one paired action) fires on Enter/Up/
    Down regardless of what other functions this leaf might also have —
    those extra ones are only reachable via a Preset Function shortcut
    instead (e.g. tv/living_room's "Picture" on Preset Function 1 — see
    config/keypad_presets.json's "by_device" overrides; confirmed
    2026-09-1x, after tv/living_room grew a second function alongside
    "Picture", that _fixed_mapping_functions() itself would otherwise
    reject the whole leaf down to no Enter action at all, since "Picture"
    counts as an unaccounted-for "other" function to it).

    Falls back to the leaf's one and only function if it has neither a
    Power-named nor a paired function, same single-function relaxation
    used elsewhere in this file (e.g. tv/living_room's "Picture" alone,
    before "Power" existed)."""
    paired_types = ("paired", "step_paired")
    power_matches = [f for f in functions if f["type"] not in paired_types and f["name"].lower() == "power"]
    paired_matches = [f for f in functions if f["type"] in paired_types]
    power = power_matches[0] if power_matches else None
    paired = paired_matches[0] if len(paired_matches) == 1 else None
    if power is None and paired is None and len(functions) == 1:
        power = functions[0]
    return power, paired


def _persistent_presets_line2(power: dict | None, paired: dict | None, frames: list[str]) -> str | None:
    """Line 2 for a fixed-mapping leaf with exactly one action (Power
    alone, or one paired action alone) and presets that fit one frame:
    the presets themselves, shown permanently instead of the one-shot
    reveal. With a single action there's nothing to disambiguate, so the
    "Power"/"X Control" hint is expendable; with both, or with presets
    that need several frames (a timer repaint would cost battery), None —
    caller keeps the normal label + one-shot reveal."""
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


def _cycle_line2(functions: list[dict], function: dict) -> str:
    """Line-2 text for the plain multi-function Left/Right-cycling
    branch — the selected function's name, framed in <> when there's more
    than one to cycle to (nothing to cycle to, so no arrows, when there's
    just the one) — screens.widgets.format.frame(), shared with every
    other scrolling screen."""
    name = _display_name(function)
    return frame(name) if len(functions) > 1 else name


def _functions_for(home: str, device: str, location: str) -> list[dict]:
    """Functions to cycle for this leaf — a step sequence
    (services.step_device_service) if it's configured as a step device,
    else the usual IR-signal-derived functions (services.signal_service).
    location matters to step_device_service too now — every light shares
    the "light" device name, distinguished only by location."""
    if step_device_service.is_step_device(device, location):
        return step_device_service.list_functions(device, location)
    return signal_service.list_functions(home, device, location)


def _display_name(function: dict) -> str:
    """Line-2 function name — a step device's functions are shown exactly
    as authored (they're already human-readable phrases, not snake_case IR
    button stems), skipping format_name(): str.title() mangles an
    apostrophe (e.g. "it's" -> "It'S"), which would otherwise show up as a
    visible glitch on a name typed correctly in config/step_devices.json."""
    step_types = ("step", "step_paired")
    return function["name"] if function["type"] in step_types else format_name(function["name"])


def _device_label(device: str, location: str) -> str:
    """Line-1 title for a leaf device — location first, like a person
    would actually say it ("Bedroom AC"), not "AC / Bedroom" — so two
    leaves under the same device don't display identically with no way to
    tell which one is active.

    A MENU_DEVICE_LABELS device (light, tv) drops the device-name suffix
    entirely (2026-09-09, extended to tv 2026-09-1x) — just "Lamp"/
    "Ceiling"/"Hall", not "Lamp Light"/"Hall Light"; "Living Room"/
    "Bedroom", not "Living Room TV"/"Bedroom TV": once you're inside that
    device's collapsed menu (see MENU_DEVICE_LABELS/menu_leaves()) the
    device type is already established by context, so repeating it on
    every individual leaf's label is redundant rather than disambiguating
    — unlike a non-grouped device (e.g. "Bedroom AC" if there were ever
    more than one AC) where dropping the suffix would lose real
    information."""
    if device in MENU_DEVICE_LABELS:
        return format_name(location) if location else MENU_DEVICE_LABELS[device]
    name = format_name(device)
    return f"{format_name(location)} {name}" if location else name


def _menu_leaf_label(home: str, device: str, location: str) -> str:
    """Line-1 title for a MENU_DEVICE_LABELS leaf (light/tv) — same as
    _device_label(), but framed in <> when there's more than one leaf in
    its group to Left/Right cycle to (nothing to cycle to, so no arrows,
    when the group only has the one leaf) — same <> convention
    _cycle_line2() already uses on line 2 for a plain multi-function
    device's selected function name, so it reads the same way here."""
    label = _device_label(device, location)
    return frame(label) if len(menu_leaves(home, device)) > 1 else label


# Hand-picked short forms for Preset Function names (2026-09-1x) — chosen
# by hand rather than derived automatically (e.g. just the first 3
# letters), so an odd or ambiguous truncation never sneaks in silently.
# Keyed lowercase; _preset_entries() falls back to the older pack-and-
# cycle-frames behavior for any name with no entry here, so adding a new
# preset without an abbreviation degrades gracefully instead of breaking.
_PRESET_ABBREVIATIONS = {
    "back": GLYPH_BACK_ARROW + GLYPH_BACK_SHAFT,
    "rust": "Rst",
    "rest": "Rst",
    "home": GLYPH_HOME,
    "mute": "Ssh",
    "power": "Pwr",
    "youtube": "YT",
    "captions": GLYPH_CC_LEFT + GLYPH_CC_RIGHT,
    "candlelight": "Cdl",
    "warm white": "Wrm",
    "daylight": "Day",
    "picture": "Pic",
}


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
    """Preset Function display frames for this leaf, ready for
    _reveal_presets() to show one at a time. [] if none are configured.
    Computed once per leaf (see show()'s leaf_changed handling), not once
    per _reveal_presets() call — services.preset_service.get_preset()
    re-reads config/keypad_presets.json from disk every time, which would
    otherwise mean 4 fresh file reads on every single repeated keystroke
    while e.g. holding a D-pad direction.

    When every configured slot's name has a _PRESET_ABBREVIATIONS entry,
    all of them are packed onto one single frame as space-separated
    abbreviations (e.g. "Bak Rst Hme Ssh") — no slot number, since the
    digit-prefixed form read as noise rather than a helpful label. Falls
    back to plain names, space-separated and packed as many to a frame as
    fit (e.g. "Back Rest" together when they both fit), cycling to a next
    frame for whatever doesn't, if *any* configured slot's name has no
    abbreviation — a mix of abbreviated and full-name slots on the same
    line would read worse than all of one style or the other."""
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


def _reveal_presets(lcd: LCD, state: AppState, device: str, location: str, normal_line2: str, frames: list[str]) -> str | None:
    """Line 2, showing this leaf's Preset Function display frames
    (*frames* — see _preset_entries(), which packs more than one preset
    onto a frame when they fit together) one at a time, right after
    switching to this leaf — presets are configured per-device
    (services.preset_service's "by_device" overrides) and change out from
    under you silently as you cycle devices, so this is the one moment
    it's actually relevant to see them, with no extra keypress or
    separate lookup screen needed. Line 1 (the device label, already
    drawn by the caller) is left alone.

    Deliberately a one-shot, not a loop — see _PRESET_REVEAL_FRAME_SECONDS'
    battery note: this only runs once, right after a device switch, then
    falls back to a plain, indefinitely-blocking read_key() with no
    ongoing cost, rather than repainting the LCD forever on a timer.

    Each frame holds for _PRESET_REVEAL_FRAME_SECONDS. Once every frame
    has shown (or *frames* is empty — nothing configured), restores
    *normal_line2* and does one final, ordinary blocking read_key().

    Any real key cuts a frame's hold short and *is* this call's return
    value — same as a plain read_key() would have returned, just a little
    later; nothing is wasted by interrupting it. Bails out (returning
    None without touching the LCD further) the moment it notices
    *device*/*location* is no longer what's actually active — the same
    quick-action-inside-read_key() race every branch below already
    re-checks for on its own read_key() call (e.g. < Device/Device >
    firing mid-reveal).

    A quick action (Preset Function, a device-slot button) also returns
    None here, same as a genuine per-frame timeout — but unlike a timeout,
    it may already have overwritten line 1 with its own content (e.g. a
    Preset's own "Sending..."/"Sent" — see app/quick_actions.py's
    _handle_preset), which this function has no way to detect since it
    only ever touches line 2 itself. Left unhandled, that stale line 1
    would sit there indefinitely: the very next thing after this function
    returns is a plain, indefinitely-blocking read_key(), so nothing
    would repaint it until some unrelated future key happened to trigger
    a full redraw (confirmed 2026-09-09 — a Preset fired mid-reveal left
    the LCD showing the preset's own name until an unrelated keypress
    much later). Telling the two apart by elapsed time (a quick action
    returns near-instantly; a real timeout takes the full duration) and
    bailing out immediately on the quick-action case — via the same
    None-means-redraw-everything contract the caller (show()'s
    DEFAULT_ACTIVE_DEVICE branch) already honors for every other quick
    action — fixes it without needing frame timing changes for the
    ordinary case."""
    for frame in frames:
        if (state.active_device, state.active_location) != (device, location):
            return None
        lcd.write_line2(frame)
        frame_start = time.monotonic()
        key = read_key(timeout=_PRESET_REVEAL_FRAME_SECONDS)
        if key is not None:
            return key
        if time.monotonic() - frame_start < _PRESET_REVEAL_FRAME_SECONDS * 0.9:
            return None  # a quick action fired, not a genuine timeout — see above

    if (state.active_device, state.active_location) != (device, location):
        return None
    lcd.write_line2(normal_line2[:16])
    return read_key()


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
        lcd.write(_menu_leaf_label(home, device, location), frames[0] if len(frames) == 1 else "" if not frames else _MENU_CONTROL_LABEL[device])
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
    dpad_needs_redraw = True  # forces the first draw on entry, same as leaf_changed would
    preset_entries: list[str] = []  # recomputed only on a leaf change — see _preset_entries()

    while state.running:
        try:
            home = home_service.get_active_home()

            if state.active_device is None:
                state.active_device, state.active_location = signal_service.default_leaf_device(home)

            device, location = state.active_device, state.active_location
            leaf_changed = (device, location) != previous_leaf
            if leaf_changed:
                function_index = 0
                previous_leaf = (device, location)
                # Only for a real leaf — a MENU_DEVICE_SENTINELS entry is a
                # synthetic stop with no presets of its own (see
                # _reveal_presets()).
                preset_entries = _preset_entries(device, location) if device and device not in MENU_SENTINEL_TO_DEVICE else []

            # Only right after landing on a leaf — see _reveal_presets()'s
            # battery note for why this is a one-shot, not continuous.
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
                # This screen's text never changes on a button press (success
                # is silent — see _transmit), so only draw it when it actually
                # needs to change, not on every held-key repeat: a redundant
                # identical write here measured ~81ms on this LCD (bit-banged
                # software I2C), which was eating into the repeat interval
                # budget above just as much as the IR transmit itself.
                # Presets stay up permanently when they fit one frame.
                persistent = len(preset_entries) == 1
                remote_label = preset_entries[0] if persistent else "Remote Control"
                if leaf_changed or dpad_needs_redraw:
                    # Line 1 ends in the Android TV Remote connection's
                    # status — see hardware/lcd.py's write_with_status().
                    lcd.write_with_status(_device_label(device, location), remote_label)
                    dpad_needs_redraw = False
                key = _reveal_presets(lcd, state, device, location, remote_label, preset_entries) \
                    if reveal_presets and not persistent else read_key()
                if key is None:
                    # A quick action wants an immediate redraw (currently
                    # just Preset Function — see hardware.input.read_key's
                    # None-on-quick-action contract) rather than the usual
                    # "keep waiting silently" — this screen's own redraw is
                    # conditional (dpad_needs_redraw), so force it instead
                    # of leaving the quick action's LCD output stuck up
                    # until some unrelated real key happens to arrive.
                    dpad_needs_redraw = True
                    continue
                if key == KEY_QUIT:
                    state.running = False
                    return
                # Any real key here is D-pad/Back (see _DPAD_BUTTONS below) —
                # plain IR, so it never touches androidtv_service on its own
                # (only Preset Function's Home/YouTube do, via send_keyevent/
                # launch_app). Poking it anyway keeps an already-open TV
                # connection warm while you're actively driving the remote
                # by any input, not just the ones that happen to go over
                # this protocol — see androidtv_service.keepalive()'s own
                # docstring; a no-op if nothing's connected right now.
                androidtv_service.keepalive()
                # < Device/Device > runs entirely inside the blocking read_key()
                # call above (see hardware.input.read_key's quick-action
                # interception) — it redraws the screen for the new device but
                # doesn't return here, so the very next real key comes back
                # through this same call, against the *pre*-switch device/
                # location this iteration started with. Re-check against
                # state before firing anything (confirmed 2026-09-01: without
                # this, switching device then immediately pressing a button
                # could fire against the device active before the switch).
                if (state.active_device, state.active_location) != (device, location):
                    continue  # let the top of the loop redraw/handle the new device; this key isn't safe to apply to it sight unseen
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
                    # A quick action (Preset, a device-slot button,
                    # < Device/Device >) wants an immediate redraw rather
                    # than the usual "keep waiting silently" — see
                    # hardware.input.read_key's None-on-quick-action
                    # contract. This screen's own redraw is conditional
                    # (dpad_needs_redraw), so force it instead of leaving
                    # the quick action's LCD output stuck up until some
                    # unrelated real key happens to arrive.
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
                # Presets stay up permanently when they fit one frame — the
                # generic "Light Control"/"TV Control" label says nothing
                # the device name doesn't already.
                persistent = len(preset_entries) == 1
                control_label = preset_entries[0] if persistent else "" if not preset_entries else _MENU_CONTROL_LABEL[device]
                input_hw.set_repeatable_keys(set())
                input_hw.set_repeat_interval(None)
                if leaf_changed or dpad_needs_redraw:
                    lcd.write(_menu_leaf_label(home, device, location), control_label)
                    dpad_needs_redraw = False
                key = _reveal_presets(lcd, state, device, location, control_label, preset_entries) \
                    if reveal_presets and not persistent else read_key()
                if key is None:
                    # See the google_tv branch above's identical handling.
                    dpad_needs_redraw = True
                    continue
                if key == KEY_QUIT:
                    state.running = False
                    return
                # Same quick-action-inside-read_key() staleness as the
                # google_tv branch above — re-check before firing anything.
                if (state.active_device, state.active_location) != (device, location):
                    continue
                if key == KEY_LEFT:
                    state.active_device, state.active_location = cycle_menu_leaf(home, device, (device, location), -1)
                    continue
                if key == KEY_RIGHT:
                    state.active_device, state.active_location = cycle_menu_leaf(home, device, (device, location), 1)
                    continue
                functions = _functions_for(home, device, location)
                power, paired = _menu_power_paired(functions)
                if key == KEY_ENTER and power:
                    _fire_power(lcd, home, device, location, power)
                    dpad_needs_redraw = True
                elif key == KEY_UP and paired:
                    _fire_paired(lcd, home, device, location, paired, "up")
                    dpad_needs_redraw = True
                elif key == KEY_DOWN and paired:
                    _fire_paired(lcd, home, device, location, paired, "down")
                    dpad_needs_redraw = True
                # Preset Function 1-4 are intentionally not handled here —
                # colour/temp presets are handled globally by
                # app/quick_actions.py's Preset Function dispatch regardless
                # of which screen is up.
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
                if key == KEY_ENTER and power:
                    _fire_power(lcd, home, device, location, power)
                    dpad_needs_redraw = True
                elif key == KEY_UP and paired:
                    _fire_paired(lcd, home, device, location, paired, "up")
                    dpad_needs_redraw = True
                elif key == KEY_DOWN and paired:
                    _fire_paired(lcd, home, device, location, paired, "down")
                    dpad_needs_redraw = True
                # Left/Right intentionally not handled — nothing to cycle to.
                continue

            function_index %= len(functions)
            function = functions[function_index]
            line2 = _cycle_line2(functions, function)
            lcd.write(_device_label(device, location), line2[:16])

            # Same Preset Function reveal every other screen shape gets on
            # entry (2026-09-1x: originally skipped here on the theory that
            # a several-functions device already shows something useful on
            # line 2 — the selected function's own name — but that just
            # meant a device like google_home's presets were invisible
            # until you happened to already know their names; showing them
            # once on arrival doesn't cost anything the normal Left/Right
            # cycling below doesn't already do afterward).
            key = _reveal_presets(lcd, state, device, location, line2, preset_entries) \
                if reveal_presets else read_key()
            if key is None:
                # A quick action (Preset, a device-slot button,
                # < Device/Device >) wants an immediate re-check rather
                # than the usual "keep waiting silently" — see
                # hardware.input.read_key's None-on-quick-action contract.
                # Bouncing straight to the top of the loop here, rather
                # than falling into the staleness re-derivation below,
                # matters specifically for a switch to another
                # general-shaped device: that re-derivation updates
                # previous_leaf in place, which would make the top's own
                # leaf_changed check miss the switch.
                continue
            if key == KEY_QUIT:
                state.running = False
                return

            # < Device/Device > runs entirely inside the blocking read_key()
            # call above (see hardware.input.read_key's quick-action
            # interception) — it redraws the screen for the new device but
            # doesn't return here, so the very next real key comes back
            # through this same call, against the *pre*-switch device/
            # location/function this iteration started with. Re-derive
            # against state before interpreting the key, so a device switch
            # immediately followed by Up/Down/Enter fires on the device now
            # on screen, not the one before the switch (confirmed 2026-09-01:
            # without this, turning one Hue light off, switching to a second,
            # and pressing its Power function turned the *first* light back
            # on instead of firing the second's).
            new_device, new_location = state.active_device, state.active_location
            if (new_device, new_location) != (device, location):
                device, location = new_device, new_location
                previous_leaf = (device, location)
                if not device or device == DEFAULT_ACTIVE_DEVICE or device in MENU_SENTINEL_TO_DEVICE or device in MENU_DEVICE_LABELS:
                    continue  # different-shaped screen; let the top of the loop handle it, this key isn't safe to apply to it here
                functions = _functions_for(home, device, location)
                if not functions or _fixed_mapping_functions(functions) is not None:
                    continue  # ditto — a fixed-mapping device also needs its own branch, not this one's Left/Right-cycling dispatch below
                function_index = 0
                function = functions[0]

            # The currently selected function's own Up/Down/Enter — see the
            # fixed-mapping branch above for devices simple enough to skip
            # Left/Right cycling entirely; this is only reached once that's
            # been ruled out.
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
            # < Device/Device > backed out of a Menu screen and switched
            # the active device while this loop's own read_key() call was
            # blocked (see hardware.input.DeviceCycledAway) — the LCD is
            # already showing the new device (app/quick_actions.py's
            # render_after_cycle already drew it); just loop back to the
            # top for a genuinely fresh read_key() call against it, rather
            # than the discard-and-redraw dance the branches below use for
            # a device switch that happens *inside* their own read_key()
            # call (this is the same race, but caught here instead, so the
            # very next real keypress after backing out of a Menu actually
            # lands on the new device instead of being thrown away too).
            continue


def _transmit(lcd: LCD, home: str, device: str, location: str, button: str) -> bool:
    """Returns False if it wrote an error message to the LCD (so a caller
    tracking whether the screen needs a redraw, e.g. google_tv's D-pad
    branch, knows to restore its normal display next), True on success."""
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
    """Fires a step device's function — the same
    voice/androidtv/androidtv_app/IR/hue step engine keypad presets use
    (services.preset_service.run_steps()), not a separate one, so e.g. a
    voice command can be followed by an IR signal (see config/
    step_devices.json's "Rust" entry).

    Non-blocking (services.preset_service.run_steps_background()): a
    voice/hue step is a real network round-trip, so blocking here used to
    leave "Sending..." on screen — and the keypad unresponsive — for
    however long that took. Shows "Sending..." for a brief fixed flash
    (ASYNC_SEND_FLASH_SECONDS — the steps themselves are already running
    in the background during it, this is only so the message is visible
    at all before the caller's own next redraw would otherwise overwrite
    it) then returns; on failure, the label is overwritten with which
    step failed (services.preset_service.describe_step()) whenever the
    background call actually finishes, which may be after the screen has
    already moved on."""
    label = _device_label(device, location)
    lcd.write(label, "Sending...")

    def _on_result(sent, total, failed_step):
        if failed_step is not None:
            lcd.write(label, f"Failed: {preset_service.describe_step(failed_step)}"[:16])

    preset_service.run_steps_background(steps, _on_result)
    time.sleep(ASYNC_SEND_FLASH_SECONDS)
