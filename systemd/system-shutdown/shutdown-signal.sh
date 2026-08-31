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
# act_led_trigger=heartbeat) and the LCD shouldn't claim it's safe to cut
# power, since the machine is coming straight back up.
#
# Two signals, both set at this exact final moment so they're trustworthy
# even if the app itself already died: the ACT LED goes dark, and the LCD
# gets one last overwrite. Neither loses power on its own here — the Pi's
# 5V header pins (which feed the LCD) are hard-wired straight to the
# PowerBoost's micro-USB PWR IN feed with no switch of any kind in between,
# nothing software can gate — so both stay showing this exact state
# indefinitely until the physical battery switch is actually flipped.

if [ "$1" = "poweroff" ] || [ "$1" = "halt" ]; then
    echo none > /sys/class/leds/ACT/trigger 2>/dev/null
    echo 0 > /sys/class/leds/ACT/brightness 2>/dev/null

    # Best-effort — a broken LCD write here must never hold up the actual
    # shutdown. The venv's own RPLCD/smbus2 are needed (not whatever
    # python3/site-packages happens to be on PATH at this late stage).
    /home/matt/dev/ir/.venv/bin/python3 -c "
import sys
sys.path.insert(0, '/home/matt/dev/ir')
try:
    from hardware.lcd import lcd
    lcd.write('Shutdown done', 'OK to power off')
except Exception:
    pass
" 2>/dev/null
fi
