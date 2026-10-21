#!/bin/bash
# Turn the LCD backlight on early in boot, so a wake from halt (shutdown
# button) shows a lit screen in a dark room well before the app draws its
# startup bar. Run by systemd/lcd-boot-backlight.service.
#
# After a halt the LCD stays powered (hard-wired 5V) with the backlight off,
# left that way by shutdown-signal.sh, so nothing lights it until the app
# opens the LCD (~14s after the kernel starts; this bus is up at ~7.4s).
# After the battery switch the PCF8574 powers up with every pin high, so the
# backlight is already on and this changes nothing.
#
# One raw byte to the PCF8574 (bus 3, 0x27): only the backlight bit set,
# EN low, so the HD44780 sees no command. Best-effort: waits up to
# BUS_WAIT_SECONDS for the bus, then gives up quietly.

LCD_BUS=3
LCD_ADDR=0x27
BACKLIGHT=0x08
BUS_WAIT_SECONDS=3

for _ in $(seq $(( BUS_WAIT_SECONDS * 10 ))); do
    [ -e "/dev/i2c-$LCD_BUS" ] && break
    sleep 0.1
done
i2ctransfer -y "$LCD_BUS" "w1@$LCD_ADDR" "$BACKLIGHT" 2>/dev/null
exit 0
