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
- LCD backlight control is **not built** — GPIO12 was intended to drive an NPN transistor / N-channel MOSFET low-side switch (GPIO can't supply the 80–120mA the backlight draws directly), but that switch circuit isn't actually wired: toggling GPIO12 HIGH/LOW (verified 2026-08-29) has no effect on the backlight, which stays on. `hardware/lcd.py` has no backlight code, and no `lcd.backlight_enabled` exists in the codebase. Building this needs the transistor switch wired between GPIO12 and the backlight supply first. The PCF8574 backpack is connected via its standard 4 wires (VCC/GND/SDA/SCL) as-is — no jumper has been removed or otherwise modified on it; unverified claims to that effect in earlier notes were wrong and are retracted. Whether the backpack's own backlight jumper (present on most of these boards, usually labeled near the contrast pot) is populated or not has not been checked and should not be assumed either way.
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
- **Exit behavior**: on error, LCD line 1 shows the error message, line 2 shows "Program stopped". On clean exit, line 1 shows "Program stopped". LCD is **never** cleared on exit.

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
- **Transistor/MOSFET low-side switch for backlight control** (decided, not yet built) — GPIO pins cap at ~16mA; backlight draws 80–120mA, so GPIO12 needs to switch a transistor rather than drive the backlight directly. I²C cannot carry this. Confirmed 2026-08-29: this switch isn't wired yet — GPIO12 currently has no effect on the backlight.
- The backpack's own PCF8574-driven backlight switch has not been ruled out as a cheaper alternative to the GPIO12 transistor — needs physically inspecting the board for a backlight jumper before deciding.
- **Wi-Fi power management disabled** — introduces 100–500ms wake latency that disrupts IR timing and LCD responsiveness. Disable via systemd service (most reliable method on Kali).
- **`pigpio`, `asyncio`, and `gpu_mem=16`** are the highest-impact performance changes for this build.
- **Android TV Remote protocol v2 over adb** for Google TV control (decided 2026-09-09) — see "Google TV / Android TV Remote Control" below.

## Development Workflow

- Local development happens on WSL2 at `/mnt/c/Users/mattk/code/pi/ir/`, synced to the Pi at `/home/matt/dev/ir/`
- SSH to the Pi (normal Wi-Fi path, not the USB recovery path above): `ssh 10.0.0.56` (bash alias `pi`, defined in WSL2's `~/.bash_aliases`). The connection can be flaky on the first attempt — retry a few times before assuming it's down.
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
4. Install the shutdown LED signal (see "Shutdown / power-off signaling" above):
   ```bash
   sudo cp systemd/system-shutdown/shutdown-signal.sh /lib/systemd/system-shutdown/shutdown-signal.sh
   sudo chmod +x /lib/systemd/system-shutdown/shutdown-signal.sh
   ```
   No enable step needed — systemd runs everything in that directory automatically on every shutdown/reboot. The `heartbeat` LED trigger itself (step 1) needs a reboot to take effect.
5. Run:
   ```bash
   sudo .venv/bin/python3 main.py
   ```

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

## IR Tools & Integrations

- **IR tooling**: `ir-ctl`, `lirc`
- **Home automation**: Home Assistant API integration, including voice command handling
- These are used alongside the custom IR transmit/receive code in `hardware/` — check for existing wrappers before introducing a new IR library or tool.

## Conventions for Assistance

- Give direct, complete answers with all necessary steps included upfront — avoid answers that require follow-up prompting to fill gaps
- When correcting a previous answer: state the correction plainly and briefly, no framing or justification
- Address only what's asked, using existing project context rather than introducing unrelated assumptions or variables