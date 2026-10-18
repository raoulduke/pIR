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
- **Shutdown / power-off signaling** (2026-08-30, LCD message added 2026-08-31, LCD message removed 2026-08-31): the shutdown button triggers a normal OS shutdown via `gpio-shutdown`, but that alone gives no way to tell *when* it's actually finished — cutting power (the PowerBoost 500C's physical battery switch) too early risks filesystem corruption. The Pi's own 5V header pins (which feed the LCD) aren't gated by anything software can control — no HAT/PMIC, just a hard-wired rail from the PowerBoost's micro-USB PWR IN feed — so `poweroff`/`halt` alone never makes anything visibly change; a human still has to flip that switch, and the only thing to fix was making that moment obvious. The signal is the **ACT LED**, forced off as the literal last action before poweroff/halt completes, via `systemd/system-shutdown/shutdown-signal.sh` (installed to `/lib/systemd/system-shutdown/shutdown-signal.sh`, chmod +x — systemd runs everything in that directory synchronously, after every other unit has already stopped, right before the kernel actually powers off; see `systemd-shutdown(8)`), so it's trustworthy independent of whether `pihome.service` itself is even still running. Previously disabled (`dtparam=act_led_trigger=none`) for battery life: now the `heartbeat` trigger (`boot/config.txt`) while the OS is up — its double-flash pulse, negligible current either way — forced off by the shutdown script. (A single-flash `timer` trigger with custom `delay_on`/`delay_off`, first every 3s then every 1s, was tried in between via `systemd/act-led-timer.service`; reverted back to plain `heartbeat`, preferred over both — that service and its install step no longer exist.) Since the LED can't lose power on its own, it stays dark indefinitely until the battery switch is actually flipped.

  An LCD message ("Shutdown done" / "OK to power off") was added alongside the LED on 2026-08-31, then removed the same day: confirmed by eye that the LED alone (flashes while the OS is up, goes dark at this final step) is a sufficient, already-trusted signal on its own — the LCD write was redundant with it, so `shutdown-signal.sh` no longer touches the LCD at all.

  Rule: **LED flashing = running, LED off = safe to flip the battery switch.** `pihome.service`'s own LCD "Program stopped" on exit (see Software Architecture below) is a useful *early* sign shutdown has started, but not sufficient on its own — the OS can still be mid-teardown for a few more seconds after the app exits, and the LED going dark is the one to actually trust.
- **LCD backlight is switched by the PCF8574's P3 bit over I²C** (confirmed by eye 2026-10-18: `i2cset -y 3 0x27 0x00` turned it off, `0x08` back on). On/off only, no dimming. **Idle-off** (2026-10-18): `hardware/lcd.py`'s `wake()` turns it off after a set time with no key press, using RPLCD's `backlight_enabled`. The time is set in **Menu > Settings > Backlight** (`screens/settings_screen.py`): a preset list of Never, then 5s to 30s in 5s steps, opening on the current value marked "(current)". Enter saves to `config/settings.json`'s `backlight_timeout_seconds` (`null` = Never) and applies right away; Left cancels. With no saved value it defaults to `app/constants.py`'s `BACKLIGHT_IDLE_SECONDS` (30s). Any key turns it back on, and the press that wakes a dark screen does nothing else (`hardware/input.py`'s `set_wake_handler()`, wired in `main.py`). The running Sweep calls `wake()` on every code so its display stays lit, and `main.py` turns the backlight on for good on exit so "Program stopped" stays readable. GPIO12 (verified 2026-08-29) has no effect: the transistor switch it was meant to drive was never wired, and is only worth building now if PWM dimming is wanted. The PCF8574 backpack is connected via its standard 4 wires (VCC/GND/SDA/SCL) as-is, and no jumper has been removed or otherwise modified on it.
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
  scroll (wrapping), with `^` in line 1's last column and `v` in line 2's
  (`screens/widgets/format.py`'s `with_arrows()`) whenever there's more than
  one option; **Enter** or **Right** selects; **Left** goes back one level
  (the main Menu's Left closes it, back to Active Device Control). Line 2's
  option text gets 15 characters. The `v` is a custom CGRAM glyph (slot 0,
  `hardware/lcd.py`'s `GLYPH_ARROW_DOWN`) mirroring the ROM's `^` — the
  HD44780 ROM has no down arrow. `screens/widgets/selector.py`'s `select_from_list`
  implements it for every list (Sweep, Learn, Rename, Change Home, Command,
  Overwrite?/Try again prompts); the main Menu, Wi-Fi network list and
  Sweep's paused screen (Up/Down step to the previous/next code, no
  wrapping, arrows only where there's one to step to) follow it in their
  own loops. Up/Down auto-repeat when held and Left doesn't
  (`hardware.input.use_list_keys()`), so a held Left can't back out several
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
  `crumbs()` drops names from the left until it fits 15 characters (the
  16th column holds the `^`)
  (`Menu > Sweep > TV` → `Sweep > TV`); the last name always stays. The
  main Menu's own line 1 stays plain `Menu`. Transient status/confirmation
  screens (Saved!, Overwrite?, No signal!, the running sweep's code
  display, the sweep's save-name entry) keep their own line 1.
- **Exit behavior**: on error, LCD line 1 shows the error message, line 2 shows "Program stopped". On clean exit, line 1 shows "Program stopped". LCD is **never** cleared on exit.
- **Menu-device grouping** (2026-09-09): `< Device/Device >` collapses a device's per-location leaves into a single top-level stop instead of listing each one — originally light-only, generalized to `app/constants.py`'s `MENU_DEVICE_LABELS = {"light": "Lights", "tv": "TVs"}` when Living Room TV/Bedroom TV joined the light devices under this scheme. Enter drills into the group's first leaf; Left/Right (otherwise unused on a fixed-mapping-shaped leaf) cycles between the rest, since they never appear as separate top-level stops. `screens/active_device_screen.py`'s `_device_label()` drops the device-name suffix for any grouped leaf ("Lamp", "Living Room", not "Lamp Light"/"Living Room TV") and frames the label in `<>` on line 1 whenever the group has more than one leaf to cycle to — same convention the plain multi-function cycling branch already used on line 2. See `docs/active-device-control-plan.md`'s "Extended 2026-09-09" notes for the full mechanics, including why a grouped leaf that isn't itself fixed-mapping-shaped (a lone function not named "power", e.g. Living Room TV's "Picture" before it also got a "Power" signal) still needs its own resolution logic distinct from `_fixed_mapping_functions()`.
- **Learn picks from existing devices/locations instead of typing** (2026-09-09): `screens/receive_screen.py`'s device and location steps show a list (`services/signal_service.py`'s `list_devices()`/`list_locations()`), `New Device`/`New Location` first, when anything already exists — adding another button to an already-learned device no longer means re-typing e.g. "tv"/"living_room" on the D-pad composer every time. A device with no locations at all (buttons directly under it, e.g. `ac`) skips the location step entirely, same as always. Typing is still how you name the button itself (the one genuinely new piece of information) and how you add a brand-new device/location.
- **Preset slots fill from 1, no gaps** (2026-10-18): each device's presets in `config/keypad_presets.json`'s `by_device` start at slot `"1"` and run left to right with none skipped. The preset line packs configured slots left to right, so a device with only `"2"`/`"4"` would show them in display positions that don't match the physical Preset buttons.
- **Non-blocking voice/hue/IR step execution** (2026-09-09): `services/preset_service.py`'s `run_steps_background()` queues steps onto a single dedicated background worker thread (mirrors `androidtv_service.py`'s event-loop-thread bridge) instead of the caller blocking on `run_steps()` directly — a voice or Hue step is a real network round-trip (`voice_service.send_command()`'s timeout is up to 15s), and blocking used to leave the keypad unresponsive for however long that took. Every call site (Active Device Control's per-leaf functions, Preset Function 1-4, the voice Command screen) shows "Sending..."/"Running..." for a brief fixed flash (`app/constants.py`'s `ASYNC_SEND_FLASH_SECONDS`) — long enough to actually be seen before the next screen redraw would otherwise overwrite it — then returns; a failure overwrites the message once the background call actually finishes, which may be after the screen has already moved on. One worker thread, not one per call, so two quick presses run in order rather than racing each other through the in-memory assumed-state dicts (`_ir_toggle_state`/`_voice_toggle_state`) `ir_toggle`/`voice_toggle` steps use. `hardware/lcd.py` gained a lock around every write for this reason — a background thread reporting a late failure can now race the main loop's own writes.

## Python Environment

- venv lives inside the project at `ir/.venv`
- Correct invocation: `sudo .venv/bin/python3 main.py` — **never** `sudo python3 main.py` (bypasses the venv)
- Confirmed third-party dependencies: `evdev`, `RPi.GPIO`, `RPLCD`, `smbus2`, `pigpio`, `androidtvremote2` (pulls in `cryptography`, `protobuf`, `aiofiles`, `cffi`, `pycparser`)
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

Applying the `pihome.service` ordering change (or any future edit to it) on
the Pi: `sudo cp systemd/pihome.service /etc/systemd/system/pihome.service
&& sudo systemctl daemon-reload && sudo systemctl restart pihome.service`.
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
  Replaces the earlier rsync-based deploy: rsync wrote files to disk but never touched the Pi's `.git`, so its repo silently diverged from `origin/main` on every deploy (confirmed 2026-09-09 — Pi's repo was a commit behind with the last deploy's changes sitting as uncommitted local diffs instead of a pulled commit). `git pull` keeps the Pi's repo meaning something (real history, rollback via `git checkout <sha>`) and is safer for `signals/` — it's tracked in git, and pull refuses to overwrite uncommitted local changes there rather than silently deleting them the way rsync `--delete` once did (see `[[scope-rsync-delete-to-code]]`). `.venv/` needs no exclude — it's gitignored, never tracked, per the "never sync `.venv/` to the Pi" rule below. `sudo cp systemd/pihome.service /etc/systemd/system/pihome.service && sudo systemctl daemon-reload` is still a separate manual step, only needed when `systemd/pihome.service` itself changed (see "Pi OS boot time" above) — `git pull` does not install that file.
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
   `sysstat-collect.timer` is the one that matters most — it wakes the CPU every 10 minutes around the clock to log stats nothing reads here, defeating idle power states all day for no benefit.
4. Install the journal size/age caps (see "Log retention" below):
   ```bash
   sudo mkdir -p /etc/systemd/journald.conf.d
   ```
   ```bash
   sudo cp systemd/journald.conf.d/zz-pihome.conf /etc/systemd/journald.conf.d/zz-pihome.conf
   ```
   ```bash
   sudo systemctl restart systemd-journald
   ```
5. Install the shutdown LED signal (see "Shutdown / power-off signaling" above):
   ```bash
   sudo cp systemd/system-shutdown/shutdown-signal.sh /lib/systemd/system-shutdown/shutdown-signal.sh
   sudo chmod +x /lib/systemd/system-shutdown/shutdown-signal.sh
   ```
   No enable step needed — systemd runs everything in that directory automatically on every shutdown/reboot. The `heartbeat` LED trigger itself (step 1) needs a reboot to take effect.
6. Run:
   ```bash
   sudo .venv/bin/python3 main.py
   ```

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
screen. YouTube's preset shows as plain `YT` text (freeing CGRAM slots 6-7).

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
session, every keypress fires the (cheap, non-blocking) nudge; the real
battery savings are in the long idle stretches between button-pressing
sessions, not during them. `config/settings.json`'s `wifi_wake_enabled`
(bool), `wifi_wake_mode` (`"every_key"`/`"throttled"`),
`wifi_wake_interval_seconds` (float, only used by `"throttled"`), and
`wifi_wake_target` (string IP, `null` = auto-detect) exist specifically
so this can be tuned without a code change once more is learned.

## Sweep menu: TV power-code sweep (2026-10-15)

TV-B-Gone style, but one brand at a time and slow enough to see which code
worked. `Menu → Sweep → TV → <brand> → Power` sends each of that
brand's library codes `SWEEP_GAP_SECONDS` (1s) apart, showing the code on
line 1 (`#3 NECx 0586:0F`). Tap Enter to pause/resume; while paused
Up/Down send the previous/next code (no wrapping, `^`/`v` only where
there's one to step to), **holding Enter** (`KEYPAD_HOLD_SECONDS`, 0.7s)
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

## IR Tools & Integrations

- **IR tooling**: `ir-ctl`, `lirc`
- **Home automation**: Home Assistant API integration, including voice command handling
- These are used alongside the custom IR transmit/receive code in `hardware/` — check for existing wrappers before introducing a new IR library or tool.

## Conventions for Assistance

- Give direct, complete answers with all necessary steps included upfront — avoid answers that require follow-up prompting to fill gaps
- When correcting a previous answer: state the correction plainly and briefly, no framing or justification
- Address only what's asked, using existing project context rather than introducing unrelated assumptions or variables