#!/bin/bash
# Install to /lib/systemd/system-shutdown/shutdown-signal.sh (chmod +x) —
# systemd runs every executable in that directory synchronously, very late
# in shutdown/reboot (see systemd-shutdown(8)): after every other unit has
# already stopped and filesystems are read-only, right before the kernel
# actually reboots/powers off. Nothing in userspace runs later, which makes
# this the reliable "safe to cut power" signal for the shutdown button
# (GPIO3, gpio-shutdown overlay) — it runs even if Pi Home Control's own
# process already exited, hung, or crashed.
#
# systemd passes the shutdown type as $1: "reboot", "halt", "poweroff", or
# "kexec". Only signal "safe to power off" for an actual power-off/halt —
# on a reboot the LED keeps pulsing (boot/config.txt's
# act_led_trigger=heartbeat) and the LCD shouldn't claim it's safe to cut
# power, since the machine is coming straight back up.
#
# Two signals: the ACT LED goes dark, and the LCD is cleared with its
# backlight switched off. main.py leaves the backlight on with "Shutting
# down... / Off when dark" when the app exits during a power-off, so the
# screen going dark here is the cue. Neither comes back on its own — the
# Pi's 5V header pins (which feed the LCD) are hard-wired to the
# PowerBoost's PWR IN feed, nothing software can gate — so both stay dark
# until the battery switch is flipped.
#
# The LCD write is raw i2ctransfer to the PCF8574 backpack on the
# i2c-gpio bus (bus 3, 0x27), not Python: no interpreter startup on a Pi
# Zero 2 W this late, and no venv on a read-only root. The app already
# put the HD44780 in 4-bit mode. PCF8574 bits: D7-D4 | backlight | EN |
# RW | RS; every byte here leaves the backlight bit clear.

LCD_BUS=3
LCD_ADDR=0x27
EN=0x04

# Clear display (HD44780 command 0x01) as two nibbles, each latched by an
# EN pulse, backlight off throughout.
lcd_clear_dark() {
    local bytes=() n
    for n in 0x00 0x10; do
        bytes+=( $n $(( n | EN )) $n )
    done
    i2ctransfer -y "$LCD_BUS" "w${#bytes[@]}@$LCD_ADDR" "${bytes[@]}"
}

if [ "$1" = "poweroff" ] || [ "$1" = "halt" ]; then
    echo none > /sys/class/leds/ACT/trigger 2>/dev/null
    echo 0 > /sys/class/leds/ACT/brightness 2>/dev/null

    # Best-effort — a failed LCD write must never hold up the shutdown.
    lcd_clear_dark 2>/dev/null
fi
exit 0
