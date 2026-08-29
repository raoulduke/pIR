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
- LCD backlight control is **not built** — GPIO12 was intended to drive an NPN transistor / N-channel MOSFET low-side switch (GPIO can't supply the 80–120mA the backlight draws directly), but that switch circuit isn't actually wired: toggling GPIO12 HIGH/LOW (verified 2026-08-29) has no effect on the backlight, which stays on. `hardware/lcd.py` has no backlight code, and no `lcd.backlight_enabled` exists in the codebase. Building this needs the transistor switch wired between GPIO12 and the backlight supply first. The J1 jumper removal from the PCF8574 backpack (disconnecting the backpack's own I²C-controlled backlight switch) has been done, per prior notes.
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

- Boot launches directly to `send_screen` (the most-used screen) — no menu detour on startup.
- **Exit behavior**: on error, LCD line 1 shows the error message, line 2 shows "Program stopped". On clean exit, line 1 shows "Program stopped". LCD is **never** cleared on exit.

## Python Environment

- venv lives inside the project at `ir/.venv`
- Correct invocation: `sudo .venv/bin/python3 main.py` — **never** `sudo python3 main.py` (bypasses the venv)
- Confirmed third-party dependencies: `evdev`, `RPi.GPIO`, `RPLCD`, `smbus2`, `pigpio`
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

## Key Engineering Decisions

- **LBO over MCP3008 ADC** for battery monitoring (decided, not yet implemented) — binary OK/LOW at ~10% is sufficient for this use case; ADC adds complexity without meaningful benefit. SPI0 pins stay reserved in case this is revisited.
- **Software I²C for the LCD** — bit-banged on GPIO23/24, not the Pi's hardware I²C bus.
- **Hardware I²C1 disabled** (`dtparam=i2c_arm=off`) — required to free GPIO3 for `gpio-shutdown`; otherwise a 3-pin workaround via GPIO17 would be needed.
- **Transistor/MOSFET low-side switch for backlight control** (decided, not yet built) — GPIO pins cap at ~16mA; backlight draws 80–120mA, so GPIO12 needs to switch a transistor rather than drive the backlight directly. I²C cannot carry this. Confirmed 2026-08-29: this switch isn't wired yet — GPIO12 currently has no effect on the backlight.
- **Wi-Fi power management disabled** — introduces 100–500ms wake latency that disrupts IR timing and LCD responsiveness. Disable via systemd service (most reliable method on Kali).
- **`pigpio`, `asyncio`, and `gpu_mem=16`** are the highest-impact performance changes for this build.

## Development Workflow

- Local development happens on WSL2 at `/mnt/c/Users/mattk/code/pi/ir/`, synced to the Pi at `/home/matt/dev/ir/`
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
3. Run:
   ```bash
   sudo .venv/bin/python3 main.py
   ```

## IR Tools & Integrations

- **IR tooling**: `ir-ctl`, `lirc`
- **Home automation**: Home Assistant API integration, including voice command handling
- These are used alongside the custom IR transmit/receive code in `hardware/` — check for existing wrappers before introducing a new IR library or tool.

## Conventions for Assistance

- Give direct, complete answers with all necessary steps included upfront — avoid answers that require follow-up prompting to fill gaps
- When correcting a previous answer: state the correction plainly and briefly, no framing or justification
- Address only what's asked, using existing project context rather than introducing unrelated assumptions or variables