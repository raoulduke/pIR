# Pi Home Control

A DIY IR remote control and home automation device built on a Raspberry Pi Zero 2 W. Runs headless, battery-powered, and handles IR transmit/receive, an LCD status display, physical button/keypad input, and Home Assistant integration (including voice commands).

## Hardware

- **Board**: Raspberry Pi Zero 2 W
- **OS**: Kali Linux ARM (headless, armhf)
- **Power**: Adafruit PowerBoost 500C + 3.7V Li-ion 1800mAh battery
- **Display**: 1602 IIC 16×2 HD44780 LCD (PCF8574 I²C backpack)
- **IR receiver**: VS1838
- **IR transmitter**: IRF3708 MOSFET, 3-emitter array
- **Shutdown button**: Philmore 30-146 momentary switch
- **Input**: 12-wire pre-built keypad matrix

### Pin Wiring Table

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

## Project Structure

```
app/          # application entry point / main loop
hardware/     # GPIO, IR, LCD, I2C drivers
screens/      # UI screens
  widgets/    # reusable screen components
services/     # Home Assistant integration, voice commands, etc.
config/       # configuration
```

Boot launches directly into the `send_screen` (the most frequently used screen).

## Setup

### 1. System configuration

Ensure `/boot/firmware/config.txt` includes:
```
dtparam=i2c_arm=off
gpu_mem=16
hdmi_ignore_hotplug=1
dtoverlay=dwc2
```

The full reference config lives at [`boot/config.txt`](boot/config.txt) — copy it to `/boot/firmware/config.txt` on the Pi and keep it in sync with any changes made there.

For USB recovery access (SSH over the data-capable micro-USB port when Wi-Fi is unavailable), also add `modules-load=dwc2,g_ether` to `cmdline.txt` (after `rootwait`). Micro-HDMI is not a usable fallback — the display driver overlays are intentionally not loaded on this fully headless device.

### 2. Python environment

The virtual environment lives inside the project:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> `requirements.txt` must be generated **on the Pi** (the authoritative hardware environment), not from WSL2 or another dev machine — hardware-dependent libraries like `smbus2` won't build correctly elsewhere.

### 3. Running

```bash
sudo .venv/bin/python3 main.py
```

Do **not** run `sudo python3 main.py` — that bypasses the venv entirely.

## Development

- Developed locally on WSL2, synced to the Pi for testing (hardware-dependent libraries like `smbus2` do not function on WSL2)
- `.venv/` is excluded from sync — Windows-built venvs are not compatible with ARM Linux

## Notes

- Battery monitoring is not yet implemented. The LBO pin approach (binary low-battery signal) has been chosen over an ADC; SPI0 is reserved in case an MCP3008-based percentage readout is added later
- Wi-Fi power management is disabled to avoid wake-latency interference with IR timing and LCD responsiveness