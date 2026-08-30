# Active Device Control — Finalized Plan

Replaces the current menu-first flow (Main Menu → Send → Device → Location →
Button) with a default "Active Device Control" screen, restores a working
device-cycle key pair, and moves the escape hatch onto its own physical key
instead of a long-press gesture.

This document is the finalized design, reached by working through the
original implementation plan issue by issue against the actual codebase.
It supersedes that plan wherever the two disagree.

## Goals

- Boot straight into control of whichever device is "active," instead of a
  Device → Location → Button picker every time.
- The most-used device (`google_tv`, a D-pad remote) gets raw, immediate
  passthrough — no list, no selection step.
- Every other device gets a lightweight function cycle (Left/Right to pick
  a function, Up/Down/Enter to fire it) derived from signals you've already
  learned — no new per-device config to author.
- Keep the 4 Preset Device pairs exactly as they are today — untouched,
  still global, still simple.
- Preset Function 1-4 buttons stay global hotkeys, but now read the active
  device: each slot fires that device's override if one is configured
  (services/preset_service.py's `by_device`), else its usual global macro.
  Superseded 2026-08-30 — see below.
- Move the long-tail (full device browsing, Learn, future Settings) behind
  a dedicated `Menu` key instead of a long-press gesture, which turned out
  to be fundamentally incompatible with instant D-pad passthrough (see
  "Rejected: long-press Enter" below).

## Final physical button mapping

| Physical key (today) | New role |
|---|---|
| Up / Down / Left / Right | Context-sensitive — see Active Device Control below |
| Enter | Context-sensitive — see Active Device Control below |
| `< Device` | Unchanged — cycle active device backward |
| `Mute` | **Becomes `Device ⟩`** — cycle active device forward (restores the original working pair) |
| Preset Device 1–4 Up/Down (8 keys) | **Unchanged** — same global hotkeys as today, do not affect the active device |
| Preset Function 1–4 (4 keys) | Global hotkeys as today, but each now reads the active device — see "Superseded 2026-08-30" below |
| `Learn` | **Becomes `Menu`** — opens the escape-hatch menu (Learn moves inside it) |

Net: no new physical keys needed, no keys lost. `Mute` and `Learn` are
repurposed; everything else is untouched.

## Active Device Control (new boot/default screen)

Tracks one **active device**, always a *leaf* (see "Device tree" below).

### Special case: `google_tv`

Raw passthrough. Up/Down/Left/Right/Enter each send `google_tv`'s
corresponding D-pad signal (`up`/`down`/`left`/`right`/`enter`)
**immediately on press**. No function list, no cycling, no exceptions —
this is what makes it a true TV-remote feel.

> ⚠️ Action item: `signals/home/google_tv/` currently only has `mute.txt`.
> The `up`/`down`/`left`/`right`/`enter` signals need to be learned before
> this is usable.

### Every other device

Has an ordered list of **functions**, derived automatically from the
signal files already saved for that device/location — no new config file.

- A file ending `_up` paired with one ending `_down` (matching stem, e.g.
  `temp_up.txt`/`temp_down.txt`) becomes one function: Left/Right selects
  it, **Up fires the `_up` file, Down fires the `_down` file** (normal
  remote semantics — increase/brighten/raise on Up, decrease/dim/lower on
  Down).
- Any other file (no `_up`/`_down` partner, e.g. `power.txt`) becomes a
  standalone function: Left/Right selects it, **Enter fires it**, Up/Down
  do nothing for it.
- Function order: alphabetical (matches the existing sort in
  `signal_service.list_buttons()` — no new ordering config).
- No multi-value "step" functions (e.g. a 3-way mode cycle) — dropped,
  nothing in current data needs it. Revisit only if a real device requires
  it later.
- LCD: line 1 = device name, line 2 = currently selected function name.

> ⚠️ Action item: `signals/home/light/hall/brighten.txt` and `dim.txt`
> don't share a naming pattern, so they won't auto-pair. Rename to
> `brightness_up.txt` / `brightness_down.txt` before/while implementing.

## Device tree (what `⟨Device/Device⟩` cycles through)

Walks every device under the **active home** (`signals/<home>/`), not just
top-level folders:

- A top-level folder with subdirectories (locations) — each subdirectory
  is its own cyclable leaf (e.g. `light/hall`; future `light/bedroom` would
  be a separate leaf too).
- A top-level folder with no subdirectories (buttons directly inside) is
  itself one leaf (e.g. `ac`, `receiver`, `google_tv`).

`receiver` stays its own leaf even after its volume signals are copied
into `google_tv` (for use with the physical Google TV remote) — the two
are intentionally not merged into one entry, since `receiver` has its own
full remote worth learning separately.

This needs a small new helper in `services/signal_service.py` — today's
`list_devices()`/`cycle_device()` only look at top-level folders; a
flattened-leaf equivalent doesn't exist yet.

## Active device state rules

- **Default at boot**: `google_tv`. If the active home has no `google_tv`
  leaf (e.g. `cottage`, which currently only has `tv`), fall back to the
  alphabetically-first leaf instead.
- **Switching homes** (via the existing Home screen) applies the exact
  same reset rule, live — not just at boot.
- **Selecting a leaf from Full Device List** (in the escape-hatch menu)
  makes it active immediately once the leaf is fully resolved — i.e. once
  you've picked the location too, for a device that has one (picking just
  `light` isn't enough; picking `hall` is what activates `light/hall`).
- **Completing a Learn capture** makes the just-learned device active, so
  it can be tested immediately with Up/Down.
- **Preset Device and Preset Function presses never change the active
  device** — they stay pure fire-and-forget, exactly like today.

This needs one new field on `app/state.py`'s `AppState` (no `active_device`
exists there today), most likely persisted the same way `active_home`
already is not — per the boot rule above, it deliberately does **not**
persist across reboots (always resets), unlike `active_home` which does.

## Escape-hatch menu (`Menu` key)

Opens instantly, no long-press, no timing detection needed at all — this
is what makes it strictly simpler than the original plan's Enter-hold
gesture.

Effectively an extension of the existing `screens/main_menu.py` (which is
otherwise unreachable once Send stops being the boot screen — see
"Rejected" note below), with two additions:

- **Full Device List** — the current Device → Location → Button picker
  (today's `send_screen.py`), just relabeled and reached from `Menu`
  instead of being the boot screen.
- **Learn** — moved here from its own physical key; calls the existing
  `screens/receive_screen.py` capture flow unchanged (see "Deferred"
  below — its device/location/button prompts are not pre-filled).
- **Settings** — placeholder only ("Coming soon" or similar). No real UI
  in this pass.
- Existing entries (`Command`/voice, `Home` switcher, `Quit`, `IP`) carry
  over unchanged.

There is no Home Assistant Browser — that was a drafting error in the
original plan and doesn't reflect anything actually wanted; dropped
entirely.

## Explicitly deferred / out of scope for this pass

- **`Mute`** — no longer a dedicated key. `signals/home/google_tv/mute.txt`
  already exists; wire it to one of the 4 Preset Function buttons whenever
  convenient. Not required for this implementation to ship.
- **Multi-value "step" functions** (e.g. AC Mode heat/cool/fan) — dropped,
  nothing in current data needs them.
- **Home Assistant Browser** — never existed, not being built.
- **Settings screen real UI** (e.g. reassigning presets from the device) —
  placeholder entry only.
- **Learn pre-filling device/location from the active device** — considered
  and rejected; Learn keeps asking from scratch every time, same as today.

## Rejected approach: long-press on Enter

The original plan proposed holding Enter to reach the escape hatch. This
doesn't work: `google_tv`'s Enter needs to fire its D-pad signal
**immediately on press** (true remote feel), but long-press detection can
only be resolved at release or after a hold threshold — you can't have an
instant press-fire and a hold-detection on the same key. Either Enter gets
a perceptible delay on the one button where it matters most (breaking
"immediate"), or the escape hatch becomes unreachable while `google_tv` is
active. The dedicated `Menu` key sidesteps this entirely, and doubles as
the fix for a related gap: once Left ceases to always mean "Back" inside
Active Device Control (it's D-pad-left or function-cycle-left instead),
there is no other way back to a menu at all.

## Implementation notes for whoever builds this

Two things the original plan got wrong about the existing codebase, worth
correcting before writing code:

1. **Global hotkeys mostly already exist.** `hardware/input.py`'s
   `QUICK_ACTION_KEYS` + `set_quick_action_handler()`, dispatching through
   `app/quick_actions.py`, already makes Preset Device, Preset Function,
   and (today) Learn fire from any screen. The new work is smaller than it
   looks: add device-cycle (`< Device`/`Device ⟩`) and `Menu` as quick
   actions too, retargeting device-cycle from "cycle Send screen's device
   list" (its current, narrower behavior) to "switch Active Device
   Control's active device" using the new leaf-based cycling. Don't
   rebuild the routing layer from scratch.
2. **The LCD "toast" is already the existing behavior.** Every quick
   action handler already does `lcd.write(...); time.sleep(...)` and
   returns; every screen loop (`select_from_list`, `main_menu.show`, etc.)
   redraws on every iteration before blocking on `read_key()` — so
   "overlay briefly, then restore" already happens for free. At most, add
   a tiny shared `toast()` helper to de-duplicate the write+sleep pattern
   already copy-pasted across `quick_actions.py`; don't build a save/restore
   subsystem.

One real wiring seam to solve, not discover mid-implementation: quick
actions are fire-and-forget (write, maybe sleep, return) and don't feed
into `main.py`'s outer dispatch loop. `main_menu.show()` returns a choice
code meant for that outer loop to interpret. When `Menu` opens the menu as
a quick action, whatever handles it needs to call the chosen screen
directly right there (`send_screen.show(lcd)`, `command_screen.show(lcd)`,
etc.) — the same way `_handle_learn()` already directly calls
`receive_screen.show(lcd)` today — rather than trying to hand the choice
back up to `main.py`.

## Action items before/during implementation

- [ ] Learn `google_tv`'s `up`, `down`, `left`, `right`, `enter` D-pad
      signals (only `mute` exists today).
- [ ] Rename `signals/home/light/hall/brighten.txt` →
      `brightness_up.txt`, `dim.txt` → `brightness_down.txt`.
- [ ] Relabel the `Learn` key to `Menu` in `config/keypad_map.json`.
- [ ] Relabel the `Mute` key to `Device ⟩` (or its `KEY_DEVICE_NEXT`
      equivalent) in `config/keypad_map.json`.
- [ ] Assign `google_tv`'s `mute` signal to a Preset Function slot
      whenever convenient (not blocking).

## Superseded 2026-08-30: Preset Function 1-4 now device-aware

This plan's "Preset Function 1-4 stay global, untouched, device-independent"
decision (Goals, and the button-mapping table above) no longer holds.

`app/quick_actions.py`'s `_handle_preset()` now passes the active device
(`state.active_device`/`state.active_location`) into
`services/preset_service.py`, which checks a `by_device` block in
`config/keypad_presets.json` for a per-device override of that slot before
falling back to the slot's global default. The keys still fire from any
screen (unaffected by this change), and pressing one still never changes
the active device — only the currently-active device's own override can
ever be *read*, per the existing "Preset Device and Preset Function
presses never change the active device" rule below, which stands
unchanged.
