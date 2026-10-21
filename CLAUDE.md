# Pi Home Control — Project Context

DIY IR remote control and home automation device. Runs headless on a Raspberry Pi Zero 2 W (Kali Linux ARM, armhf), battery-powered. Handles IR transmit/receive, LCD display output, GPIO button/keypad input, and Home Assistant integration (including voice commands). Battery life and boot speed are explicit optimization priorities.

## Hardware

- **Board**: Raspberry Pi Zero 2 W
- **Power**: Adafruit PowerBoost 500C + 3.7V Li-ion 1800mAh cell
- **OS**: Kali Linux ARM, headless, armhf
- **Display**: 1602 IIC 16×2 HD44780 LCD, PCF8574 I²C backpack (not the DM2021 variant)
- **IR receiver**: VS1838
- **IR transmitter**: IRF3708 MOSFET, 3-emitter array
- **Shutdown button**: Philmore 30-146 momentary switch
- **Input**: 12-wire pre-built keypad matrix (pre-built layout preserved as-is, not renumbered)

## Pin Wiring Table

| Physical Pin       | BCM    | Use                                   |
|--------------------|--------|---------------------------------------|
| 1                  | 3V3    | IR RX power                           |
| 2                  | 5V     | IR TX power                           |
| 3                  | GPIO2  | IR RX signal                          |
| 4                  | 5V     | LCD power                             |
| 5                  | GPIO3  | Shutdown/wake signal                  |
| 6                  | GND    | Shutdown/wake ground                  |
| 7                  | GPIO4  | IR TX signal                          |
| 8                  | GPIO14 | Keypad wire 1                         |
| 9                  | GND    | IR TX ground                          |
| 10                 | GPIO15 | Keypad wire 2                         |
| 11                 | GPIO17 | Keypad wire 3                         |
| 12                 | GPIO18 | Keypad wire 4                         |
| 13                 | GPIO27 | Keypad wire 5                         |
| 14                 | GND    | IR RX ground                          |
| 15                 | GPIO22 | Spare                                 |
| 16                 | GPIO23 | LCD SDA                               |
| 18                 | GPIO24 | LCD SCL                               |
| 20                 | GND    | LCD ground                            |
| 22                 | GPIO25 | Spare                                 |
| 29                 | GPIO5  | Keypad wire 6                         |
| 31                 | GPIO6  | Keypad wire 7                         |
| 32                 | GPIO12 | LCD backlight PWM                     |
| 33                 | GPIO13 | Keypad wire 8                         |
| 35                 | GPIO19 | Keypad wire 9                         |
| 36                 | GPIO16 | Keypad wire 10                        |
| 37                 | GPIO26 | Keypad wire 11                        |
| 38                 | GPIO20 | Keypad wire 12                        |
| 40                 | GPIO21 | Spare                                 |
| 19, 21, 23, 24, 26 | SPI0   | Reserved — MCP3008 battery ADC option |

Notes:
- LCD uses **software I²C (bit-banged)** on GPIO23 (SDA) / GPIO24 (SCL), not hardware I²C.
- Shutdown button (Philmore 30-146) requires hardware I²C1 **disabled** (`dtparam=i2c_arm=off` in `config.txt`) to free GPIO3 for the `gpio-shutdown` overlay.
- **Shutdown / power-off signaling** (2026-08-30, LCD message added 2026-08-31, removed 2026-08-31, re-added 2026-10-18): the shutdown button triggers a normal OS shutdown via `gpio-shutdown`, but that alone gives no way to tell *when* it's actually finished — cutting power (the PowerBoost 500C's physical battery switch) too early risks filesystem corruption. The Pi's own 5V header pins (which feed the LCD) aren't gated by anything software can control — no HAT/PMIC, just a hard-wired rail from the PowerBoost's micro-USB PWR IN feed — so `poweroff`/`halt` alone never makes anything visibly change; a human still has to flip that switch, and the only thing to fix was making that moment obvious. The signal is the **ACT LED**, forced off as the literal last action before poweroff/halt completes, via `systemd/system-shutdown/shutdown-signal.sh` (installed to `/lib/systemd/system-shutdown/shutdown-signal.sh`, chmod +x — systemd runs everything in that directory synchronously, after every other unit has already stopped, right before the kernel actually powers off; see `systemd-shutdown(8)`), so it's trustworthy independent of whether `pihome.service` itself is even still running. Previously disabled (`dtparam=act_led_trigger=none`) for battery life: now the `heartbeat` trigger (`boot/config.txt`) while the OS is up — its double-flash pulse, negligible current either way — forced off by the shutdown script. (A single-flash `timer` trigger with custom `delay_on`/`delay_off`, first every 3s then every 1s, was tried in between via `systemd/act-led-timer.service`; reverted back to plain `heartbeat`, preferred over both — that service and its install step no longer exist.) Since the LED can't lose power on its own, it stays dark indefinitely until the battery switch is actually flipped.

  **LCD signal** (re-added 2026-10-18, reworked the same day): when the app exits as part of a power-off/halt (`main.py`'s `_powering_off()` looks for `poweroff.target`/`halt.target` in `systemctl list-jobs`), it writes **"Shutting down..."** and leaves the backlight **on**; any other exit (restart, reboot, error) shows "Program stopped" as before, also lit. Then `shutdown-signal.sh`, in the same step that darkens the LED, **clears the LCD and switches its backlight off** with one raw `i2ctransfer` to the PCF8574 on bus 3 (HD44780 already in 4-bit mode from the app) — not Python: no interpreter startup this late, no venv dependency on a read-only root. Nothing in userspace runs later than this script, so the screen going dark is as close to the actual halt as software can get. Tried first the other way round (backlight off on exit, "OK to power off" lit at the end) and dropped: the backlight then stays lit after the halt, since nothing can turn it off later. Seen by eye 2026-10-18: after the script, the ACT LED blinked a few more times before going off for good (cause not checked; likely the firmware's own halt blink).

  Rule: **screen dark, then ACT LED off for good after its last blinks = safe to flip the battery switch.** The screen going dark is not quite the end: the LED still blinks for a few seconds after (seen 2026-10-18). "Shutting down..." lit means teardown is still running.
- **LCD backlight is switched by the PCF8574's P3 bit over I²C** (confirmed by eye 2026-10-18: `i2cset -y 3 0x27 0x00` turned it off, `0x08` back on). On/off only, no dimming. **Idle-off** (2026-10-18): `hardware/lcd.py`'s `wake()` turns it off after a set time with no key press, using RPLCD's `backlight_enabled`. The time is set in **Menu > Settings > Backlight** (`screens/settings_screen.py`): a preset list of Never, then 5s to 30s in 5s steps, opening on the current value marked "(current)". Enter saves to `config/settings.json`'s `backlight_timeout_seconds` (`null` = Never) and applies right away; Left cancels. With no saved value it defaults to `app/constants.py`'s `BACKLIGHT_IDLE_SECONDS` (30s). Any key turns it back on, and that press also does its normal job (2026-10-20; it used to only light the screen, so every command took two presses): `hardware/input.py`'s `set_wake_handler()`, wired in `main.py`. On Google TV the relit screen starts the held connection and the key waits on it (`_connect_lock`), then sends. The running Sweep calls `wake()` on every code so its display stays lit, and `main.py` turns the backlight on for good on exit so its exit message stays readable (a power-off then darkens it — see "Shutdown / power-off signaling" above). GPIO12 (verified 2026-08-29) has no effect: the transistor switch it was meant to drive was never wired, and is only worth building now if PWM dimming is wanted. The PCF8574 backpack is connected via its standard 4 wires (VCC/GND/SDA/SCL) as-is, and no jumper has been removed or otherwise modified on it.
- Battery monitoring is not yet implemented. LBO pin (binary OK/LOW at ~10% remaining) has been chosen over an ADC approach; debounce recommended since IR transmitter current spikes can cause false triggers. LBO GPIO pin not yet assigned.

## Software Architecture

Layered structure:
```
app/
hardware/
screens/
  widgets/
services/
config/
```

- Boot launches directly to `active_device_screen` (the most-used screen) — no menu detour on startup. `send_screen`/"Full Device List" was the boot screen in an earlier design; it was superseded by Active Device Control and later removed outright as redundant with `< Device/Device >` (see `docs/active-device-control-plan.md`'s "Superseded" notes) — it no longer exists.
- **Navigation convention** (2026-10-15, keys changed 2026-10-17): every
  screen that scrolls through options uses the same keys — **Up/Down**
  scroll (wrapping), with one up/down glyph in line 2's last column
  (`screens/widgets/format.py`'s `with_arrows()`) whenever there's more than
  one option; **Enter** or **Right** selects; **Left** goes back one level
  (the main Menu's Left closes it, back to Active Device Control). Line 1
  gets all 16 columns; line 2's option text gets 15. The glyph is solid up
  and down triangles stacked in one custom CGRAM cell (slot 0,
  `hardware/lcd.py`'s `GLYPH_ARROW_UPDOWN`). It replaced a ROM `^` at the
  end of line 1 plus a custom `v` at the end of line 2 (2026-10-18), freeing
  line 1's last column. `screens/widgets/selector.py`'s `select_from_list`
  implements it for every list (Sweep, Learn, Rename, Change Home, Command,
  Overwrite?/Try again prompts); the main Menu, Wi-Fi network list and
  Sweep's paused screen (Up/Down move to the previous/next code, no
  wrapping; its line-1 count shows the ends) follow it in their
  own loops. Up/Down auto-repeat when held and Left doesn't
  (`hardware.input.use_default_keys()`), so a held Left can't back out several
  levels. The D-pad text entry (`screens/widgets/char_input.py`) matches
  it: Up/Down pick the character, Left deletes / backs out when empty,
  Right commits the character, Enter submits. Text entry's first character
  starts on capitals, later ones on lowercase. The one deliberate
  exception is Active Device Control, where Up/Down are device controls
  (volume, brightness, Google TV's D-pad) and Left/Right cycle
  devices/functions with line 2 framed `<   name   >` (`frame()`). Changed
  from Left/Right scrolling + Up = back on 2026-10-17 to match the text
  entry and read naturally with breadcrumbs (Left steps back along them).
  New screens must follow this convention rather than invent their own.
- **Breadcrumbs** (2026-10-17): every sub-menu's line 1 — lists and the
  D-pad text entry alike — is the path that led there, ending with the
  screen you're on, joined with ` > `: `Menu > Rename`, `Learn > Device`
  (Learn's first list is the "Device" step), `Device > TV` (TV's location
  list), `TV > Living Room` (typing the button). `screens/widgets/format.py`'s
  `crumbs()` drops names from the left until it fits 16 characters
  (`Menu > Sweep > TV > Hisense` → `TV > Hisense`); the last name always stays. The
  main Menu's own line 1 stays plain `Menu`. Transient status/confirmation
  screens (Saved!, Overwrite?, No signal!, the running sweep's code
  display, the sweep's save-name entry) keep their own line 1.
- **Exit behavior**: on error, LCD line 1 shows the error message, line 2 shows "Program stopped". On clean exit, line 1 shows "Program stopped". LCD is **never** cleared on exit. The process exits **nonzero** after an error or a failed startup, so `pihome.service`'s `Restart=on-failure` brings it back 2s later; 0 only for a requested stop (2026-10-18 — it used to exit 0 always, leaving the app down until a reboot).
- **Stopping** (2026-10-18): SIGTERM/Ctrl+C no longer raise wherever the main thread is (which could land mid-LCD-write). `main.py`'s handler calls `hardware/input.py`'s `request_quit()`: every `read_key()` returns `KEY_QUIT` and the screens unwind through their normal quit path (measured 0.74s on a service restart). If that takes over 3s (`_QUIT_GRACE_SECONDS`, e.g. stuck in `nmcli`), or on a second signal, it falls back to raising `KeyboardInterrupt`.
- **Startup checks** (2026-10-18): `ir_transmitter.verify()`/`ir_receiver.verify()` ask each LIRC device for its features (`hardware/lirc.py`) — TX must be able to send, RX to record — instead of only checking `/sys/class/gpio`. A TX failure stops startup; an RX failure is only logged (just Learn needs it).
- **Late background failures** (2026-10-18): a voice/Hue/IR step's "Failed: ..." message is drawn only if no key was pressed since it started (`hardware/input.py`'s `press_count()`), and never from a quick action fired inside a Menu screen — otherwise it's only logged, instead of landing on whatever screen is up by then. Steps that wait over 10s in the background queue (`preset_service._STALE_SECONDS`) are skipped as stale.
- **Cached config reads** (2026-10-19): settings, presets, step devices, device slots and device order are read through `app/files.py`'s `read_json_cached()`, which re-parses a file only when its mtime or size changes (they were re-read several times per key press). Hand edits, `git pull` and the app's own saves are picked up at once; an unreadable file is logged once and treated as empty. The cached object is shared: `config_service` setters deep-copy before changing it.
- **USB input removal** (2026-10-19): a USB keyboard/remote that errors (unplugged) is dropped from `hardware/input.py`'s reader with a log line, instead of the read error ending the app.
- **No learning onto step devices** (2026-10-19): a step device's buttons run the steps in `config/step_devices.json` (Hue, voice, Google TV), so a signal learned into it would never be used. Learn and Rename Signal's new-name lists (`screens/widgets/signal_entry.py`) leave those locations out, a device with only such locations disappears (Google Home), and typing one is refused ("Not an IR device"). Otherwise a typed New Device/New Location that already exists shows "Already exists" and returns to the list — an existing one is picked, not retyped. The step-device check comes first, so a step device is never reported as merely existing (a device whose locations are all step devices, like `google_home`, gets the step-device message too). Exception: a step device whose steps send IR from its own location stays learnable (`step_device_service.sends_own_ir()` — Hall's ir_toggle Power and IR Brightness), since those signals may need re-learning.
- **LCD opens on first use** (2026-10-18): `hardware/lcd.py`'s `lcd` is a lazy stand-in and RPLCD is imported in `LCD.__init__`, so importing the module (every screen does, for the type) touches no hardware and works without RPLCD. `write()` no longer clears first: both lines are padded to 16 columns, so the clear only cost time on the bit-banged bus and caused flicker.
- **Crash-safe writes** (2026-10-18): every config/data file the app writes (`config/settings.json`, device order, home renames, signals, Sweep saves) goes through `app/files.py`'s `write_text_atomic()` — temp file, fsync, rename — so a power cut mid-write can't leave a half-written file. Plain `write_text()` used to, and `config_service` then read the damaged file as empty and the next save erased every other setting.
- **Wi-Fi menu never deletes a saved profile** (2026-10-18): `services/wifi_service.py`'s `connect()` tries a typed password for an already-saved SSID on a throwaway `pihome-trial-<ssid>` profile, copies it into the saved profile only once it connects, and removes only profiles it created that never connected. It used to delete the saved profile first, so a typo on the Pi's own network left it with no working profile (no Wi-Fi until USB or the SD card).
- **Menu-device grouping** (2026-09-09): `< Device/Device >` collapses a device's per-location leaves into a single top-level stop instead of listing each one — originally light-only, generalized to `app/constants.py`'s `MENU_DEVICE_LABELS = {"light": "Lights", "tv": "TVs"}` when Living Room TV/Bedroom TV joined the light devices under this scheme. Enter drills into the group's first leaf; Left/Right (otherwise unused on a fixed-mapping-shaped leaf) cycles between the rest, since they never appear as separate top-level stops. `screens/active_device_screen.py`'s `_device_label()` drops the device-name suffix for any grouped leaf ("Lamp", "Living Room", not "Lamp Light"/"Living Room TV") and frames the label in `<>` on line 1 whenever the group has more than one leaf to cycle to — same convention the plain multi-function cycling branch already used on line 2. See `docs/active-device-control-plan.md`'s "Extended 2026-09-09" notes for the full mechanics, including why a grouped leaf that isn't itself fixed-mapping-shaped (a lone function not named "power", e.g. Living Room TV's "Picture" before it also got a "Power" signal) still needs its own resolution logic distinct from `_fixed_mapping_functions()`.
- **Every device has a location** (2026-10-19): signals always live at `signals/<home>/<device>/<location>/<button>.txt`. Learn asks for a location for every new device (the "Single device" option is gone), so the location step never fills itself in — that auto-filled step is what used to trap Back on the button step. Moved the same day: `ac`, `google_tv`, `google_home` → `living_room` (home); cottage's `dvd` and `tv` → `living_room`; michael's `bathroom` device → `light/bathroom` (its `brighten`/`dim` renamed `brightness_up`/`brightness_down` for Up/Down, and Red/Green/Blue added as its Preset Function 1-3). `list_leaf_devices()` still reads a location-less device as `(device, "")`, but none exist.
- **Learn picks from existing devices/locations instead of typing** (2026-09-09): `screens/receive_screen.py`'s device and location steps show a list (`services/signal_service.py`'s `list_devices()`/`list_locations()`), `New Device`/`New Location` first, when anything already exists — adding another button to an already-learned device no longer means re-typing e.g. "tv"/"living_room" on the D-pad composer every time.  Typing is still how you name the button itself (the one genuinely new piece of information) and how you add a brand-new device/location.
- **Preset slots fill from 1, no gaps** (2026-10-18): each device's presets in `config/keypad_presets.json`'s `by_device` start at slot `"1"` and run left to right with none skipped. The preset line packs configured slots left to right, so a device with only `"2"`/`"4"` would show them in display positions that don't match the physical Preset buttons.
- **Non-blocking voice/hue/IR step execution** (2026-09-09): `services/preset_service.py`'s `run_steps_background()` queues steps onto a single dedicated background worker thread (mirrors `androidtv_service.py`'s event-loop-thread bridge) instead of the caller blocking on `run_steps()` directly — a voice or Hue step is a real network round-trip (`voice_service.send_command()`'s timeout is up to 15s), and blocking used to leave the keypad unresponsive for however long that took. Every call site (Active Device Control's per-leaf functions, Preset Function 1-4, the voice Command screen) shows "Sending..."/"Running..." for a brief fixed flash (`app/constants.py`'s `ASYNC_SEND_FLASH_SECONDS`) — long enough to actually be seen before the next screen redraw would otherwise overwrite it — then returns; a failure overwrites the message once the background call actually finishes, which may be after the screen has already moved on. One worker thread, not one per call, so two quick presses run in order rather than racing each other through the in-memory assumed-state dicts (`_ir_toggle_state`/`_voice_toggle_state`) `ir_toggle`/`voice_toggle` steps use. `hardware/lcd.py` gained a lock around every write for this reason — a background thread reporting a late failure can now race the main loop's own writes.

## Python Environment

- venv lives inside the project at `ir/.venv`
- Correct invocation: `sudo .venv/bin/python3 main.py` — **never** `sudo python3 main.py` (bypasses the venv)
- Confirmed third-party dependencies: `evdev`, `RPi.GPIO`, `gpiod` (keypad edge interrupts; builds from source on the Pi, ~35s), `RPLCD`, `smbus2`, `pigpio`, `androidtvremote2` (pulls in `cryptography`, `protobuf`, `aiofiles`, `cffi`, `pycparser`)
- `requirements.txt` must be generated from the Pi (the authoritative environment), not from WSL2
- Windows-built venvs are unusable on ARM Linux — never sync `.venv/` to the Pi; it's excluded from sync

## `/boot/firmware/config.txt` Key Settings

```
dtparam=i2c_arm=off      # frees GPIO3 for gpio-shutdown overlay
gpu_mem=16                # headless, no display output needed
hdmi_ignore_hotplug=1
dtoverlay=dwc2             # USB OTG gadget — Ethernet-over-USB recovery access
```

The `vc4-fkms-v3d`/`vc4-kms-v3d` display driver overlays are **not** loaded — the device never drives a display, and `vc4-fkms-v3d` needs `gpu_mem >= 64`, which would conflict with `gpu_mem=16`. Micro-HDMI is not usable as a recovery path as a result.

USB gadget recovery access also requires:
- `modules-load=dwc2,g_ether` appended to `cmdline.txt` after `rootwait`
- Connect via the Pi's data-capable micro-USB port (separate from the PWR IN port the PowerBoost 500C feeds) to get an Ethernet-over-USB interface and SSH in when Wi-Fi is unavailable.
- `systemd/usb0-gadget.service` (enabled) gives the Pi's `usb0` a static `10.55.0.1/24` the moment the interface exists — even with nothing plugged in — so it's always ready, not dependent on DHCP negotiating anything. Confirmed 2026-08-30: `dwc2` correctly binds `g_ether` as the gadget driver on this hardware (the `dtoverlay=dwc2,dr_mode=host` line in `boot/config.txt` is under `[cm5]` and never applies to this Pi Zero 2 W — inert, not a conflict).
- No DHCP server runs on `usb0` (deliberately — a persistent dnsmasq daemon isn't worth the always-on battery/boot-speed cost for a path used only during rare Wi-Fi outages). Set a matching static IP on the connecting computer's end when using this path, e.g. `10.55.0.2/24`, then `ssh matt@10.55.0.1`.

## Pi OS boot time (2026-08-31)

`pihome.service` starts before the OS has necessarily finished settling —
deliberately. The app needs a human to do anything at all (press a button),
which takes far longer than most OS services still finishing up in the
background, so there's no reason to make boot-to-screen wait on them.
Reviewed with `systemd-analyze time` / `blame` / `critical-chain
pihome.service` (a Pi Zero 2 W boot is ~5.3s kernel + ~13.7s userspace to
`multi-user.target`, but nothing needs to wait for that whole figure —
only for whatever's actually in `pihome.service`'s own dependency chain):

- **`runonce.service` disabled** — a stock Kali one-time-provisioning
  runner (`/etc/runonce.d/*`: user creation, locale, SSH keys, iptables,
  etc.), still ordered `Before=basic.target` (blocking everything,
  `pihome.service` included) on every single boot even though its own
  hash-check logic already skips all nine scripts as unchanged. Measured
  453ms of pure overhead — forking `sha1sum` nine times just to reconfirm
  nothing has changed — for a device that's done being provisioned.
- **`pihome.service`'s `After=` no longer includes `ir-keymap.service`/
  `ir-nec.service`** (measured ~248ms + ~199ms serialized in front of it).
  Those only configure the kernel's rc-core NEC decoder/keymap for
  `gpio_ir_recv`'s scancode-decoded evdev path — `hardware/ir_receiver.py`'s
  actual Learn/capture feature reads raw LIRC pulse timing via `ir-ctl -r`
  and does its own noise-filtering, entirely independent of that decoder,
  so this ordering was never protecting anything Learn needs. They still
  run, just now in parallel with `pihome.service` instead of serialized in
  front of it.
- Confirmed NOT worth touching: `systemd-fsck@...` on `boot-firmware`
  (~879ms) — this project's whole shutdown-signaling effort exists
  specifically to avoid the filesystem corruption an unclean power cut
  risks, so skipping the check that catches it would cut directly against
  that. `NetworkManager`/`NetworkManager-wait-online`/`networking.service`
  (multiple seconds combined) — confirmed via `critical-chain` these were
  never actually in `pihome.service`'s dependency path to begin with; nothing
  needed to change there.
- **The flip side of starting early**: something `pihome.service` doesn't
  wait for can genuinely still be initializing the first time a human
  reaches a screen that needs it — Wi-Fi/NetworkManager is the real case
  (can take several actual seconds, unlike the sub-second IR services
  above). `services/wifi_service.py`'s `is_ready()` checks NetworkManager
  itself rather than assuming a scan's empty result means no networks are
  in range — see `screens/network_screen.py`, which shows "Wi-Fi starting"
  instead of a misleading "No networks" when that's actually the cause.
- **`boot/cmdline.txt` kernel params** (2026-09-01): added `quiet` (suppresses
  verbose kernel/console log output — no cost on a headless box with no
  display) and `raid=noautodetect` (skips MD RAID autodetection scanning —
  no RAID is used anywhere in this setup). Both are pure removed overhead,
  no functional tradeoff. `fastboot` (skips forced fsck) was considered and
  rejected — it directly conflicts with the `systemd-fsck@...` decision
  above.

**Console auto-login removed** (2026-10-18): a hand-added
`/etc/systemd/system/getty@tty1.service.d/override.conf`
(`agetty --autologin matt`, Jan 2026, from when an HDMI monitor and
keyboard were attached) logged `matt` in on tty1 at every boot. With HDMI
now off (`hdmi_ignore_hotplug=1`) and the USB data port in gadget mode,
nothing could see or type into that console, but the login kept the
user's systemd manager up all day, starting PipeWire, WirePlumber,
mpris-proxy and a per-user Mosquitto broker (enabled globally by the
package, a dependency of OpenVAS's notus-scanner; empty config, localhost
only, nothing connects; Home Assistant is reached over HTTPS, not MQTT).
Mosquitto alone measured ~10 wakeups/s idle; the set used ~53MB RAM. The
drop-in is now `override.conf.disabled` (`scripts/disable-unused-services.sh`
does the rename), so tty1 shows a normal password login prompt. Verified
after a reboot: SSH key login works, `pihome.service` runs, and with no one
logged in neither Mosquitto nor PipeWire runs (they start with an SSH
session and stop ~10s after it ends). Undo: rename it back and reboot.

Applying the `pihome.service` ordering change (or any future edit to it) on
the Pi: `/etc/systemd/system/pihome.service` is a symlink to the repo's
file (see "Installed system files" under Setup), so after `git pull` it's
`sudo systemctl daemon-reload && sudo systemctl restart pihome.service`.
`scripts/disable-unused-services.sh` (Setup step 3) now also disables
`runonce.service`.

## Key Engineering Decisions

- **LBO over MCP3008 ADC** for battery monitoring (decided, not yet implemented) — binary OK/LOW at ~10% is sufficient for this use case; ADC adds complexity without meaningful benefit. SPI0 pins stay reserved in case this is revisited.
- **Software I²C for the LCD** — bit-banged on GPIO23/24, not the Pi's hardware I²C bus.
- **Hardware I²C1 disabled** (`dtparam=i2c_arm=off`) — required to free GPIO3 for `gpio-shutdown`; otherwise a 3-pin workaround via GPIO17 would be needed.
- **Backlight on/off via the PCF8574's P3 bit** (2026-10-18), replacing the planned GPIO12 transistor/MOSFET low-side switch, which was never built. The backpack's own switch turned out to work, which covers idle-off with no wiring. The GPIO12 switch only matters if PWM dimming is ever wanted (GPIO pins cap at ~16mA vs. the backlight's 80–120mA, so it would still need the transistor).
- **Wi-Fi power management stays enabled** (`wifi.powersave = 3` in NetworkManager's `/etc/NetworkManager/conf.d/wifi-powersave.conf`, not tracked in this repo) — reversed 2026-09-09 from the earlier "disable it outright" decision; see "Wi-Fi Power Save + Speculative Wake" below for why and what replaced it.
- **`pigpio`, `asyncio`, and `gpu_mem=16`** are the highest-impact performance changes for this build.
- **Android TV Remote protocol v2 over adb** for Google TV control (decided 2026-09-09) — see "Google TV / Android TV Remote Control" below.

## Development Workflow

- Local development happens on WSL2 at `/mnt/c/Users/mattk/code/pi/ir/`, pushed to GitHub (`git@github.com:raoulduke/pIR.git`) and pulled onto the Pi at `/home/matt/dev/ir/` — both are checkouts of the same repo/remote, not independent clones. The Pi's checkout must always be a clean `git pull` away from `origin/main`; never hand-edit files there outside of `signals/` Learn captures (see below), or the two will silently diverge the way rsync-based deploy used to.
- SSH to the Pi (normal Wi-Fi path, not the USB recovery path above): `ssh 10.0.0.56` (bash alias `pi`, defined in WSL2's `~/.bash_aliases`). The connection can be flaky on the first attempt — retry a few times before assuming it's down. Note: a *slow*/failed-then-succeeds first attempt is the "flaky" case worth retrying past; "No route to host" on repeated attempts means the Pi is actually unreachable (off, Wi-Fi down, etc.) — don't keep retrying that past a few tries, surface it.
- **Deploy (2026-09-09): `git pull` on the Pi, not rsync.** Push from WSL2, then pull + restart on the Pi:
  ```bash
  git push
  ```
  ```bash
  ssh 10.0.0.56 "cd /home/matt/dev/ir && git pull && sudo systemctl restart pihome.service; sleep 2; sudo systemctl is-active pihome.service"
  ```
  Replaces the earlier rsync-based deploy: rsync wrote files to disk but never touched the Pi's `.git`, so its repo silently diverged from `origin/main` on every deploy (confirmed 2026-09-09 — Pi's repo was a commit behind with the last deploy's changes sitting as uncommitted local diffs instead of a pulled commit). `git pull` keeps the Pi's repo meaning something (real history, rollback via `git checkout <sha>`) and is safer for `signals/` — it's tracked in git, and pull refuses to overwrite uncommitted local changes there rather than silently deleting them the way rsync `--delete` once did (see `[[scope-rsync-delete-to-code]]`). `.venv/` needs no exclude — it's gitignored, never tracked, per the "never sync `.venv/` to the Pi" rule below. System files are symlinked into the checkout, so `git pull` updates them too; a changed unit file still needs `sudo systemctl daemon-reload` and a restart of that service (see "Installed system files" under Setup). `boot/config.txt`/`cmdline.txt` are the only copies left — copy them to `/boot/firmware/` by hand when they change.
  - **Learn captures made directly on the Pi** land in `signals/` as uncommitted local changes on the Pi's checkout (Learn writes to disk, not to git) — they aren't backed up anywhere until pushed. Periodically run `git add signals/ && git commit && git push` from the Pi, then `git pull` on WSL2, to bring them back into the shared history instead of letting them sit as permanent local drift.
- Hardware-dependent libraries (e.g. `smbus2`) will not function on WSL2 — expect import/runtime errors there for hardware-touching code paths
- IDE: CLion, with `ir/` marked as Sources Root; `pyproject.toml` added to the project to resolve import resolution issues in CLion

## Setup (on the Pi)

1. Ensure `/boot/firmware/config.txt` has the settings listed above. The full reference config is checked in at `boot/config.txt` — copy it to `/boot/firmware/config.txt` and keep both in sync whenever one changes.
2. Create the venv inside the project and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
   `requirements.txt` must be generated **on the Pi**, not WSL2.
3. Disable stock Kali services that are dead weight on a headless, battery-powered, single-purpose box — confirmed inactive/unused on this device, but enabled by default and either poll on a timer or sit socket-activated waiting for a trigger that never comes:
   ```bash
   sudo scripts/disable-unused-services.sh
   ```
   It also disables `cron.service` (2026-10-18: every cron job here is a no-op under systemd or disabled, but the daemon woke every minute; a crontab entry added later needs `sudo systemctl enable --now cron`) and switches off the tty1 console auto-login (see "Console auto-login removed" under "Pi OS boot time"). `sysstat-collect.timer` is the one that matters most — it wakes the CPU every 10 minutes around the clock to log stats nothing reads here, defeating idle power states all day for no benefit.
4. Link the journal size/age caps (see "Log retention" below):
   ```bash
   sudo mkdir -p /etc/systemd/journald.conf.d
   ```
   ```bash
   sudo ln -sfn /home/matt/dev/ir/systemd/journald.conf.d/zz-pihome.conf /etc/systemd/journald.conf.d/zz-pihome.conf
   ```
   ```bash
   sudo systemctl restart systemd-journald
   ```
5. Link the shutdown LED signal (see "Shutdown / power-off signaling" above).
   The script is executable in git (mode 755), which systemd requires:
   ```bash
   sudo ln -sfn /home/matt/dev/ir/systemd/system-shutdown/shutdown-signal.sh /lib/systemd/system-shutdown/shutdown-signal.sh
   ```
   No enable step needed — systemd runs everything in that directory automatically on every shutdown/reboot. The `heartbeat` LED trigger itself (step 1) needs a reboot to take effect.
6. Run:
   ```bash
   sudo .venv/bin/python3 main.py
   ```

### Installed system files (2026-10-18)

The OS only reads its config from fixed paths, so repo files there are
**symlinks back into the checkout**, not copies — `git pull` updates what
the system reads, and nothing drifts (a copied shutdown script had fallen
behind the repo before this change):

| Repo file | Linked from |
|---|---|
| `systemd/{pihome,usb0-gadget,ir-keymap,ir-nec}.service` | `/etc/systemd/system/` |
| `systemd/journald.conf.d/zz-pihome.conf` | `/etc/systemd/journald.conf.d/` |
| `systemd/system-shutdown/shutdown-signal.sh` | `/lib/systemd/system-shutdown/` |
| `etc/rc_maps.cfg`, `etc/rc_keymaps/shutdown.toml` | `/etc/` |
| `etc/NetworkManager/conf.d/brcmfmac-no-scan-rand-mac.conf` | `/etc/NetworkManager/conf.d/` |
| `systemd/wifi-chip-check.service` | `/etc/systemd/system/` (enabled) |
| `home/zshenv` | `~/.zshenv` (user `matt`) |

Safe because `/home` is on the root partition, readable from early boot to
the final shutdown step. systemd still caches unit files and journald its
config: after a pull that changes one, run `sudo systemctl daemon-reload`
and restart that service (or `systemd-journald`). The shutdown script is
read fresh at each shutdown. Because root runs these, the checkout must not
be group/world-writable (fixed 2026-10-18; the Pi's git has
`core.fileMode=false`, so modes there are set by hand, not by pulls).
Pulls wrote new files with the default umask 002, which undid that fix, so
`home/zshenv`, linked as the Pi's `~/.zshenv` (read by every zsh, `ssh pi
"git pull"` included), sets `umask 022` (2026-10-20).

`boot/config.txt` and `boot/cmdline.txt` stay **copies**:
`/boot/firmware` is FAT (no symlinks) and the firmware reads it before
Linux starts. Each deploy checks them with `cmp`.

## Log retention (2026-10-13)

Device failures (IR TX/RX, missing signals, voice/Hue/Android TV steps,
startup) log to the journal as well as the LCD — the LCD message is gone
at the next redraw. Read them with `journalctl -u pihome.service`.

Logs are bounded by **size/age caps, not a scheduled cleanup**:
`systemd/journald.conf.d/zz-pihome.conf` (`SystemMaxUse=200M`,
`SystemKeepFree=2G`, `MaxRetentionSec=3month`) — journald enforces these
itself on every write, so there's no timer adding a CPU wakeup. Measured
~2MB/day (565M accumulated Jan–Oct 2026 under stock settings, whose cap
was ~4G), so 200M is roughly 3 months of history.

`rsyslog.service` is disabled (`scripts/disable-unused-services.sh`) and
`ForwardToSyslog=no` is set — it duplicated every journal line into
`/var/log/syslog`, writing each one to the SD card twice for a text log
nothing reads. `journalctl` is the only log now. The remaining
`/var/log/*` text logs (dpkg, apt, etc.) are tiny and already rotated
weekly by stock logrotate — left alone.

## Google TV / Android TV Remote Control (2026-09-09)

`services/androidtv_service.py` controls Google TV functions IR can't
reach (Home, true Back distinct from Menu, app launching) over the
**Android TV Remote protocol v2** (`androidtvremote2` library) — the same
protocol the Google Home app and physical Android TV remotes use.
Replaced an earlier adb/Wireless-debugging implementation, which had
caused two separate outages in one week: the TV's pairing trust being
reset (needing `adb pair` redone from a fresh code) and the connect port
drifting on every Wireless-debugging toggle/TV reboot (needing a live
port rescan to find). Neither failure mode is inherent to remote
control — they're specific to adb being a debugging protocol repurposed
for this job. The new protocol needs no Developer Options/Wireless
debugging at all; pairing is a one-time self-signed-cert exchange
(`scripts/pair_androidtv.py`), independent of any TV debugging setting.

It's also meaningfully faster: adb paid two subprocess spawns (connect +
shell) per keypress, real overhead on a Pi Zero 2 W. A keypress on an
already-open Android TV Remote connection is a single non-blocking socket
write — measured ~2-3ms of call overhead against the actual TV in PoC
testing, versus adb's measured hundreds of ms to multiple seconds.

**Connect-on-demand, not held open 24/7** — the device pings an open
connection every ~5s to keep it alive, so holding one open at all times
would mean a permanent wake-every-5s tax, in tension with this project's
battery priorities. Instead a connection opens on first use after being
idle and closes after 30s of no further activity
(`_IDLE_DISCONNECT_SECONDS` in `androidtv_service.py`), bounding the cost
to actual use (plus a short tail) rather than running all day regardless
of whether anyone's touching the remote. "Activity" includes plain D-pad/
Back presses on the Google TV screen, not just this module's own calls —
`keepalive()` lets `screens/active_device_screen.py` push the idle timer
back out on any key press while that screen is active, deliberately
scoped to that screen only (not any keypress app-wide) so browsing
Wi-Fi/Menu/Lights elsewhere still lets the TV connection drop normally.

**Held open on the Google TV screen** (2026-10-18): while Google TV is
Active Device Control's active device **and** the backlight is on, the
connection opens right away and stays open with no idle timeout
(`androidtv_service`'s `set_on_tv_screen()`, called from
`active_device_screen.py`'s loop, and `set_screen_lit()`, wired to
`hardware/lcd.py`'s `set_backlight_listener()` in `main.py`). Switching
away or the backlight turning off closes it at once; backlight-off also
closes an on-demand connection from another screen. The wake press relights
the backlight, which re-holds if you're on Google TV. Connecting sends no
key, so playback isn't touched. A dropped connection (TV standby, Wi-Fi,
missed pings) is noticed via the library protocol's `on_con_lost`
(`_watch_connection()`, reading androidtvremote2 0.3.2's private
`_remote_message_protocol`) and retried at 0/5/10/30/60s gaps while held.
`_connect_lock` keeps that connect and a real send from both connecting at
once. Elsewhere it's still connect-on-demand with the 30s idle timeout.
Battery: the TV's ~5s keepalive ping now runs whenever you're looking at
the Google TV screen, bounded by the backlight timeout.

**Connection status** (2026-10-18): the Google TV screen's line 1 ends in
`-` when the connection is closed, `+` while connecting and `*` when open
(`app/constants.py`'s `ANDROIDTV_STATUS_CHARS`, plain ROM characters).
The screen draws it with `hardware/lcd.py`'s `write_with_status()`; on
each change `androidtv_service`'s `set_status_listener()` (wired in
`main.py`) calls `lcd.set_status_char()`, which redraws that one cell only
while that screen is still up — every other write touching line 1 clears
the flag, so a background connection change never draws over another
screen. YouTube's preset shows as `YT` text, replaced by the plause pair (CGRAM slots 4-5) while YouTube is in front (see "YouTube play/pause" below). Back is plain `<-` (2026-10-20; it was a two-cell custom arrow in slots 4-5).

The library is asyncio-only; the rest of the app is fully synchronous.
Bridged via a single dedicated event loop run in a background thread,
started lazily on first use — every public function in the module is a
plain blocking call from the caller's point of view, so
`preset_service.py`/`active_device_screen.py` needed no changes.

`config/settings.json` needs `androidtv_ip`, `androidtv_cert_file`,
`androidtv_key_file` (no port — the protocol uses fixed ports 6466/6467).
Run `scripts/pair_androidtv.py` once per TV (or after a TV factory reset)
to generate the cert/key and complete pairing; the TV shows a code on
screen to enter.

`launch_app()` needs a real deep link (e.g. `https://www.youtube.com/tv`),
not a bare package name — confirmed 2026-09-09: the bare-package path
(`market://launch?id=<package>`) reported success but didn't actually
switch away from whatever app had foreground focus (seen with Plex open).
`config/keypad_presets.json`'s `androidtv_app` steps use `"app_link"`
(not `"package"`) accordingly.

**YouTube play/pause** (2026-10-20): the YouTube preset's
`androidtv_app` step carries `"package"` (`com.google.android.youtube.tv`)
and `"if_open_steps"`. `preset_service` asks `androidtv_service.foreground_app()`
(connects if needed, then waits up to 3s for the TV's app report, so a press
from a dark screen doesn't relaunch over what's playing); with YouTube in
front it runs those steps instead of launching: `KEYCODE_MEDIA_PLAY_PAUSE`,
then the IR Back (the same signal as the Back preset) 1s later to hide the
controls the toggle leaves up (the user's call). Tested down from 0.5s:
0.5, 0.25 and 0.1s hid them after a pause, and no delay was too quick;
at 0.1s a resume exited the video, so it went back up to 1s. At 1s, one press in a fast run exited the video: it sent its
play/pause ~0.8s after the previous press's Back (that press had waited
its turn in the step queue), and the guess is YouTube was still closing
the controls, left them hidden, and the Back exited. So presses are spaced:
`"if_open_gap_seconds": 1.5` holds a plause run back until 1.5s after the
last one finished (`preset_service._wait_if_open_gap()`, logged when it
waits). An Up was tried first
(network at 0.3s, IR at 0.5/1.5/2.5s) and never hid them: after a pause,
YouTube highlights the play/pause control, then the timeline after ~1s,
and Up hid the controls from neither. Unknown app still launches. A hold-Enter Enter, Enter, Back version was built
and removed the same day in favour of this.

The **plause pair** (the user's name for it): while YouTube is in front, a
play triangle and pause bars side by side (CGRAM slots 4-5, `GLYPH_PLAY`,
`GLYPH_PAUSE`) replace `YT` on the preset line. Static: they don't follow
whether the video is playing (the user's call). `active_device_screen.py`'s
`update_play_glyph()`, wired in `main.py` to the TV's app reports
(`androidtv_service.set_app_listener()`), uses `hardware/lcd.py`'s
`set_status_line2_swap()`, which redraws line 2 only while
`write_with_status()`'s screen is still up (like the status character).
Tried and removed the same day: a single glyph showing the playing/paused
state from Google Cast (`pychromecast`, port 8009). Cast does report it for
native YouTube (`PLAYING`/`PAUSED`), if that's ever wanted again.

## Wi-Fi Power Save + Speculative Wake (2026-09-09)

Reverses the earlier "disable Wi-Fi power management outright" decision.
`wifi.powersave = 3` (NetworkManager's "force enable", set in
`/etc/NetworkManager/conf.d/wifi-powersave.conf` — not tracked in this
repo) stays in effect for the battery savings; a `wifi-power-off.service`
systemd unit that used to run `iw dev wlan0 set power_save off` once at
boot was removed — confirmed via `journalctl` that it only ever "won" for
the first ~15 minutes of uptime, since wlan0 fully disconnects/re-
associates roughly every 15 minutes on this network (cause not yet
investigated) and NetworkManager reapplies its powersave policy on every
one of those re-associations; the oneshot service never re-ran, so it
was providing no real benefit and had already been quietly overridden
whenever anyone actually experienced the resulting latency.

The tradeoff being kept: measured up to ~1.6s latency for the first
packet after an idle period (surfaced as `ssh`/`rsync` from WSL2
intermittently failing with "No route to host" — the wake takes longer
than the OS's ARP/route resolution will wait for a fresh connection
attempt, vs. a `ping`'s more tolerant timeout), dropping to single-digit
ms once the radio's awake. The Pi's onboard chip (Broadcom BCM43430/1,
`brcmfmac` driver, confirmed via `readlink /sys/class/net/wlan0/device/
driver`) doesn't expose the firmware's own "how long awake after
activity" timing anywhere in sysfs (unlike e.g. `iwlwifi`'s
`power_save_timeout`) — that timing is opaque, inside Broadcom's closed
firmware.

Middle ground instead of disabling power save: `services/
wifi_wake_service.py`'s `wake()` fires a small, fire-and-forget
background `ping` (target: the LAN default gateway, auto-detected via
`ip route`, or `wifi_wake_target` in `config/settings.json` to override)
on every real keypress, globally — wired via a new generic
`hardware/input.py` hook, `set_key_observer()` (mirrors the existing
`set_quick_action_handler()` pattern; called from `main.py`), which
fires for every key `read_key()` returns, not just quick-action keys. A
Wi-Fi station controls its own transmit timing (unlike receiving, which
waits on the AP's next DTIM beacon), so an outbound packet tends to wake
the radio faster than an inbound one — and a Wi-Fi-dependent action
(voice/Hue/Android TV) is often not the very first press of a session,
so nudging the radio on earlier presses gives it a head start.

Not throttled by default (`wifi_wake_mode` = `"every_key"` in
`config/settings.json`, not `"throttled"`) — deliberately, because the
brcmfmac re-doze timing couldn't be reliably measured (a controlled test
ran back-to-back with a lot of other SSH activity already keeping the
radio warm, so it couldn't isolate a genuinely cold re-doze interval; see
git history around 2026-09-09 for the actual measurements). Rather than
guess an interval and have it silently stop helping partway through a
session, every keypress fires the (cheap, non-blocking) nudge, at most
once a second (`_MIN_GAP_SECONDS`, 2026-10-18: a held Up/Down repeats ~10
times a second and used to start a thread and a ping for each); the real
battery savings are in the long idle stretches between button-pressing
sessions, not during them. The gateway target is cached only once found —
it used to cache "none" if the first press came before Wi-Fi was up, which
disabled the wake for the whole session. `config/settings.json`'s `wifi_wake_enabled`
(bool), `wifi_wake_mode` (`"every_key"`/`"throttled"`),
`wifi_wake_interval_seconds` (float, only used by `"throttled"`), and
`wifi_wake_target` (string IP, `null` = auto-detect) exist specifically
so this can be tuned without a code change once more is learned.

## Slow Wi-Fi at boot (2026-10-18)

A boot took 52s to get on Wi-Fi (normal: 16-23s). The router (Rogers
Xfinity gateway made by Vantiva, one SSID on 2.4 and 5 GHz, channel 11 on
2.4) ignored six association requests in a row (`ASSOC-REJECT
status_code=16` with a blank BSSID: the Pi's driver timed out waiting),
and wpa_supplicant's own 10s/20s back-off after repeated failures added
~25s of that. Signal was -40 dBm, so not range. The same runs of timeouts
showed on 3 earlier boots of the last 25; a single timeout followed by
success a second later is common and harmless. The router also drops the
Pi often with `reason=6` (105 times in the journal), likely the ~15-minute
re-association noted under "Wi-Fi Power Save + Speculative Wake".

Fix tried: NetworkManager scanned with a random MAC and switched back to the
real one right before associating, on every boot and reconnect; Kali's
`no-mac-addr-change.conf` turns that off only for the `eagle_sdio`/`wl`
drivers, not this Pi's `brcmfmac`.
`etc/NetworkManager/conf.d/brcmfmac-no-scan-rand-mac.conf` (symlinked into
`/etc/NetworkManager/conf.d/`) covers brcmfmac. Tested the same evening:
5 forced reconnects before (5 random-MAC scans, 0 timeouts) and after
(0 random-MAC scans, 0 timeouts, all first try), then a reboot: connected
first try at 14.6s. The problem is intermittent, so this shows the change is
safe and in effect, not yet that it cures the timeouts — check later boots
with `journalctl -b <n> -u wpa_supplicant | grep -c ASSOC-REJECT`.

Not changed (router side, user's call): the Pi's radio is 2.4 GHz only
(`iw phy` lists Band 1, channels 1-13), so it always joins 2.4 GHz and
band steering, which targets clients seen on both bands, probably isn't
acting on it. A separate 2.4 GHz-only SSID would only be an experiment, to
take the Pi out of the gateway's client management; the router's own logs
are the better next step if the timeouts continue.

## Wi-Fi chip fails to load at boot (2026-10-20)

On the 2026-10-20 03:08 boot the Wi-Fi chip never came up: brcmfmac's
firmware download over SDIO failed (`Downloaded RAM image is corrupted`,
`dongle image file download failed`, `mmc1: Controller never released
inhibit bit(s)`), so `wlan0` didn't exist and NetworkManager never saw it.
Every network step failed for that whole boot: voice lights (Lamp,
Ceiling: "Network error"), Google TV (`Network is unreachable`, status
`-`). A reboot loaded it fine. 1 of the 91 boots in the journal. Lights
don't depend on the Google TV connection; both just needed Wi-Fi.

What the error means (from the driver source in
`/usr/src/brcmfmac-nexmon-6.12.2/sdio.c`): at probe the driver writes the
firmware (`cypress/cyfmac43430-sdio.bin`, 374,608 bytes) into the chip's
RAM over SDIO (50 MHz, 4-bit), then reads it back in 2048-byte blocks and
compares it with its in-memory copy of the file. The first 182 blocks
matched; the last, partial one (offset 372736, 1872 bytes) didn't, so a
transfer over the bus went wrong. The file is fine: the same one loads on
every other boot, and it's the reference for the compare. Right after,
the SDIO host controller hung (`Controller never released inhibit bit(s)`).
On failure the driver releases the device and nothing retries it until a
module reload, host rebind or reboot (what wifi-chip-check does). The
read-back check exists only because Kali's nexmon build (`brcmfmac-nexmon`
DKMS, for monitor mode) compiles with `-DDEBUG`; the stock module skips it
and would start the firmware anyway (the original module is kept in
`/var/lib/dkms/brcmfmac-nexmon/original_module/`). No under-voltage was
logged on any boot, but that boot's `vcgencmd get_throttled` is lost, so
power isn't ruled out. Not changed: lowering the SDIO clock
(`dtparam=sdio_overclock=<MHz>`) might make the transfer more robust, but
for a 1-in-91 event it's a guess; the recovery script covers it.

`scripts/wifi-chip-check.sh`, run at every boot by
`systemd/wifi-chip-check.service` (`Type=exec`, so boot doesn't wait on
it), exits as soon as `wlan0` exists. If it's still missing 30s after
boot: reload brcmfmac, then unbind/rebind the chip's SDIO host
(`3f300000.mmcnr`; the SD card is on `3f202000.mmc`, checked), then reboot,
at most once per 10 minutes (`/var/lib/pihome/wifi-chip-check-reboot`).
Steps 1-2 are untested on a real failure (it's rare and can't be caused on
purpose); the log says which step worked: `journalctl -u
wifi-chip-check`. Battery: one short script per boot.

## Sweep menu: TV power-code sweep (2026-10-15)

TV-B-Gone style, but one brand at a time and slow enough to see which code
worked. `Menu → Sweep → TV → <brand> → Power` sends each of that
brand's library codes one gap apart: **Menu > Settings > Sweep Speed**
sets it to 1s (default, `SWEEP_GAP_SECONDS`), 0.5s or 0.25s, saved as
`config/settings.json`'s `sweep_gap_seconds`. 1s keeps the code on screen
when the TV reacts (0.3-1s after a power code); faster means pausing a
code or more past it and stepping back. Line 1 is the
code's brand and its position in the sweep (`Hisense      4/5`), the count
ending in the last column (`38/38` marks the end); a brand too long for
that is cut with an ellipsis glyph (`Westinghou…38/38`, CGRAM slot 6, `format.py`'s
`fit_with_suffix()`). The code itself (protocol/address) isn't shown —
changed 2026-10-18, it meant nothing at a glance. Tap Enter to pause/resume. While paused,
Up/Down only move to the previous/next code, sending nothing (no wrapping,
they stop at the first/last code; they auto-repeat when held),
**Right** sends the code on screen once per press (line 2 shows `|| Sending...` for at least `ASYNC_SEND_FLASH_SECONDS`), and Enter resumes from
the code on screen, sending it first unless it already went out since
landing on it, so no code goes out twice in a row (2026-10-18; Up/Down
used to send as they stepped). **Holding Enter** (`KEYPAD_HOLD_SECONDS`, 0.7s)
saves it under a typed name, Left goes back a level. Tap-vs-hold is opted
into by this screen only (`hardware.input.set_hold_keys()`) — a tap acts
on release there; Enter acts on press everywhere else (Google TV's D-pad
needs that). Sweep with the TV **on** — turning off shows immediately,
turning on can take seconds. `Sweep → Saved` mirrors the same path and
sends a saved code on Enter.

- **Code library**: `data/fun_codes.json`, built once on WSL2 by
  `scripts/build_fun_codes.py` from Flipper-IRDB (+ probonopd/irdb for
  ProScan). 20 brands: the Statista Canada 2022 brand-share core, then the
  user's additions A–Z; Skyworth, Sansui and Xiaomi are left out (no power
  code in either database). `services/ir_encode.py` encodes each entry to
  raw timings + carrier.
- **All Brands** (2026-10-18): first in the sweep's brand list. Sweeps
  every brand's codes for the chosen signal in library brand order, each
  shared code once (`fun_service.get_codes()`, using the same first-frame +
  carrier match as the build's dedupe): 38 of the 60 power codes, ~45-50s
  end to end. A hold-Enter save goes under the brand of the code on
  screen. A shared code is listed under the first brand that has it: the
  Insignia power code (NECext 0586:F00F) shows as Hisense 15/38.
- **Same-signal dedupe** (2026-10-18): the build drops any code whose first
  frame and carrier match one earlier in that brand's list (frames compared
  with timing tolerance, `hardware/ir_transmitter.py`'s `same_frame()`).
  Matching by stored form missed raw Flipper captures of a code also listed
  by protocol, and captures of one button held for different lengths. It
  removed 9 (69 → 60): Samsung #5, Hisense #5, RCA #7, Sharp #7–10, Vizio
  #2–3. ProScan's two RCA codes differ only in carrier (56/38 kHz) and both
  stay. Codes shared across brands stay: each brand's list is meant to be
  complete on its own (e.g. Hisense #4 = Insignia #1 = NECext 0586:F00F,
  the power code of the user's own Insignia TVs).
- **500ms send limit**: the kernel (lirc_dev's `IR_MAX_DURATION`) refuses
  any send over 500ms with "Invalid argument". Every send goes through
  `ir_transmitter._fit_tx_limit()`, which drops trailing frames that repeat
  an earlier one until it fits. The first copy of every distinct frame stays
  (the data frame, an NEC repeat code, Sharp's inverted check frame), so the
  code itself is never cut; a signal that still doesn't fit fails with
  "Signal too long". Found 2026-10-18: Hisense #5, a raw Flipper capture of
  a button held ~1s (data frame + 9 NEC repeats, 1083ms), failed this way.
- **Carrier**: Learn captures go out at the 38 kHz `gpio-ir-tx` default,
  but Sony (40k), Philips RC5/RC6 (36k), Panasonic (37k) and some RCA (56k)
  codes need their own. A signal file may start with a `carrier N` line;
  `hardware/ir_transmitter.py` strips it and passes `ir-ctl -c N` (ir-ctl
  rejects the line mixed with `+/-` values). Files without it behave
  exactly as before.
- **Saves**: `fun_signals/<device_type>/<brand>/<signal>/<name>.txt`
  (`services/fun_service.py`) — top-level, deliberately not under
  `signals/`, which `signal_service` reads as homes; Fun saves never show
  in Learn, Rename or Active Device Control. Tracked in git like
  `signals/`: commit and push saves made on the Pi from the Pi, then pull
  on WSL2.

## Smooth hold for Device keys (2026-10-18)

A Device slot with `"smooth": true` in `config/keypad_devices.json`
(currently just Device 1, the Receiver's volume) behaves like its own
remote while held: `hardware/ir_transmitter.py`'s `stream()` sends one
frame of the signal, then repeats it at the capture's own frame period
(~85ms for the receiver) until release, so the receiver ramps volume
smoothly; a tap is exactly one frame. It writes straight to `/dev/lirc0`
(carrier via the `LIRC_SET_SEND_CARRIER` ioctl) rather than spawning
`ir-ctl` per frame, which takes ~103ms on its own. Release comes from the
keypad scanner directly (`hardware.input.is_key_held()`), not the event
queue, so it stops on time even if the main loop is busy. USB/SSH input
has no release, so it sends one frame per press. Other slots (AC
temperature, light brightness) keep the one-step-per-press `ir-ctl` path.

The Receiver's signals live under `signals/home/receiver/living_room/`
(moved 2026-10-18); its line-1 label falls back to just `Receiver`, since
"Living Room Receiver" doesn't fit 16 columns.

## Hue bulbs off at the wall (2026-10-18)

The Hue bridge's `on` value can't tell a bulb cut at the wall switch from
one that's on, but `reachable` can (`services/hue_service.py`'s
`is_reachable()`). Landing on a Hue light in Lights (Bedroom, Kitchen
South, Kitchen North, Kitchen) checks each of its bulbs once in the
background (`check_reachable_background()`), so cycling never waits on
the bridge. If every bulb on that leaf is unreachable, line 2 shows
**`* Not connected`** (`STATEMENT_LIGHT_OFF`) in place of the presets,
and nothing is sent: Enter/Up/Down only re-check (in case it's back on),
and its presets flash the same message. Until a check answers, the last
known state shows. Kitchen with one bulb off still sends to the other
(that bulb's step fails as "Failed: Light"). A failed check (bridge or
Wi-Fi down) records unknown, not off, so sending isn't blocked and the
real error surfaces. A `-`/`+`/`*` line-1 indicator like Google TV's was
tried and dropped the same day: line 2 is where the presets that stop
working are. Battery: one LAN request per arrival on a Hue light, no
polling.

## Keypad idle on edge interrupts (2026-10-18)

The keypad scanner used to sweep the matrix every 10ms around the clock,
about 3% of a core with nothing pressed, enough to keep the CPU out of
deep idle. Idle now sleeps in `select()` on gpiod falling-edge interrupts
(`hardware/keypad.py`'s `_sleep_until_edge()`): every drive-side pin is
held low so any key pulls its sense pin low, which fires the interrupt.
Waking, it puts the drive side back high and scans exactly as before
(debounce, repeat, tap/hold all unchanged), then goes back to sleep once
nothing has read as pressed for `KEYPAD_WAKE_IDLE_SECONDS` (50ms).
RPi.GPIO still does the scanning; gpiod only holds the sense pins as
pulled-up inputs with edge detection (consumer `pihome-keypad` in
`gpioinfo`). RPi.GPIO's own `add_event_detect` fails on this kernel
(sysfs GPIO is gone), hence gpiod's character-device API. If gpiod is
missing or its request fails, it logs a warning and polls as before.
Side benefit: a press too short for the old 10ms gap still fires the
edge.
Verified by hand 2026-10-18: taps, held Up/Down, the smooth volume hold
and hold-Enter in a paused Sweep all work.

**Light and overlapping presses** (2026-10-20). Presses that clicked but
didn't register: a key counted only after 3 sweeps in a row read it, and
one sweep missing it (a light press's contact flickers) restarted the count
after a 10ms nap. Now (`hardware/keypad.py`'s `_new_press()`), after a wake
the sweeps run back to back (~4.2ms each: 7 drive pins x 0.5ms settle), and
a key registers once read in `KEYPAD_PRESS_HITS` (2) sweeps, not
necessarily in a row; it's released only once unread for
`KEYPAD_RELEASE_SECONDS` (25ms), so a flicker can't read as release and a
second press. Sweeps read every key down (`_pressed()`), and a key's
handling ends on its own release: a key pressed before the previous one
was let go used to be dropped (except after Up/Down). Simulated against
the old scanner (fake GPIO, sweep time matched to the Pi's): contact
flickering 40-60% of a 25-60ms press went from 2-7 of 20 to 17-19
of 20, with no doubled presses; a clean 8ms contact is still mostly
missed (needs faster sweeps; `app/constants.py`'s keypad timing comment
warns more GPIO traffic once garbled the LCD). Sweep speed and GPIO traffic per sweep are unchanged. Every registered
press logs `keypad: <KEY> (<reading>)`, and a contact read too few times
`keypad: press not registered: ...` (also while another key is held), or
an edge wake with no read at all `keypad: woke on an edge but no key read`,
so a reported miss can be placed: no line = the switch never closed;
a press line = the keypad saw it and the miss is downstream (IR, network).

## IR Tools & Integrations

- **IR tooling**: `ir-ctl`, `lirc`
- **Home automation**: Home Assistant API integration, including voice command handling
- These are used alongside the custom IR transmit/receive code in `hardware/` — check for existing wrappers before introducing a new IR library or tool.

## Conventions for Assistance

- Give direct, complete answers with all necessary steps included upfront — avoid answers that require follow-up prompting to fill gaps
- When correcting a previous answer: state the correction plainly and briefly, no framing or justification
- Address only what's asked, using existing project context rather than introducing unrelated assumptions or variables