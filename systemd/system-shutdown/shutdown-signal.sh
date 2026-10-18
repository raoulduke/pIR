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
# Two signals: the ACT LED goes dark, and the LCD lights up with "Shutdown
# done / OK to power off" (main.py turns the backlight off when the app
# exits, so the screen sits dark through the rest of teardown, then lights
# here). Neither loses power on its own — the Pi's 5V header pins (which
# feed the LCD) are hard-wired to the PowerBoost's PWR IN feed, nothing
# software can gate — so both hold until the battery switch is flipped.
#
# The LCD write is raw i2ctransfer to the PCF8574 backpack on the
# i2c-gpio bus (bus 3, 0x27), not Python: no interpreter startup on a Pi
# Zero 2 W this late, and no venv on a read-only root. The app already
# put the HD44780 in 4-bit mode, so this only sets each line's address
# and writes its characters. PCF8574 bits: D7-D4 | backlight | EN | RW | RS.

LCD_BUS=3
LCD_ADDR=0x27
BACKLIGHT=0x08
EN=0x04

# lcd_line <DDRAM address command> <text> — one i2ctransfer per line.
lcd_line() {
    local bytes=() i c
    nibble() {  # $1 = high nibble in bits 7-4, $2 = RS
        local b=$(( $1 | BACKLIGHT | $2 ))
        bytes+=( $b $(( b | EN )) $b )
    }
    send() {  # $1 = byte, $2 = RS
        nibble $(( $1 & 0xF0 )) "$2"
        nibble $(( ($1 << 4) & 0xF0 )) "$2"
    }
    send "$1" 0
    local text
    text=$(printf '%-16.16s' "$2")
    for (( i = 0; i < 16; i++ )); do
        printf -v c '%d' "'${text:i:1}"
        send "$c" 1
    done
    i2ctransfer -y "$LCD_BUS" "w${#bytes[@]}@$LCD_ADDR" "${bytes[@]}"
}

if [ "$1" = "poweroff" ] || [ "$1" = "halt" ]; then
    echo none > /sys/class/leds/ACT/trigger 2>/dev/null
    echo 0 > /sys/class/leds/ACT/brightness 2>/dev/null

    # Best-effort — a failed LCD write must never hold up the shutdown.
    lcd_line 0x80 "Shutdown done" 2>/dev/null
    lcd_line 0xC0 "OK to power off" 2>/dev/null
fi
exit 0
