#!/bin/bash
# Install to /lib/systemd/system-shutdown/shutdown-signal.sh (chmod +x) —
# systemd runs every executable in that directory synchronously, very late
# in shutdown/reboot (see systemd-shutdown(8)): after every other unit has
# already stopped, right before the kernel actually reboots/powers off.
# That's what makes this reliable as a physical "safe to cut power" signal
# for the shutdown button (GPIO3, gpio-shutdown overlay) — it runs even if
# Pi Home Control's own process already exited, hung, or crashed, since
# it's not that process's responsibility at all by this point.
#
# systemd passes the shutdown type as $1: "reboot", "halt", "poweroff", or
# "kexec". Only signal "safe to power off" for an actual power-off/halt —
# on a reboot the LED should just keep pulsing (see boot/config.txt's
# act_led_trigger=heartbeat), since the machine is coming straight back up.
#
# LCD is deliberately NOT touched here (2026-08-31 change) — confirmed by
# eye that the ACT LED alone (flashes while the OS is up, goes dark here)
# is a sufficient, already-trusted signal on its own; the LCD's final
# overwrite was redundant with it. The LED going dark doesn't lose power on
# its own — the Pi's 5V header pins (which feed the LCD) are hard-wired
# straight to the PowerBoost's micro-USB PWR IN feed with no switch of any
# kind in between, nothing software can gate — so it stays dark
# indefinitely until the physical battery switch is actually flipped.

if [ "$1" = "poweroff" ] || [ "$1" = "halt" ]; then
    echo none > /sys/class/leds/ACT/trigger 2>/dev/null
    echo 0 > /sys/class/leds/ACT/brightness 2>/dev/null
fi
