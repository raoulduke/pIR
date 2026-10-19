#!/bin/bash
# Disables stock Kali services that are dead weight on this headless,
# battery-powered, single-purpose box — confirmed inactive/unused here, but
# enabled by default and either poll on a timer, sit socket-activated
# waiting for a trigger that never comes, or (runonce.service) re-check
# the same nine already-applied provisioning scripts on every single boot.
#
# sysstat-collect.timer is the one that matters most for battery — it
# wakes the CPU every 10 minutes around the clock to log stats nothing
# reads on this device, defeating idle power states all day for no
# benefit. runonce.service is the one that matters most for boot speed —
# see CLAUDE.md's "Pi OS boot time" section: measured 453ms of pure
# overhead confirming nothing needs to run, every boot, forever.
#
# rsyslog.service copies every journal message into /var/log/syslog, so
# each log line hit the SD card twice; nothing here reads those text logs
# (journalctl is the log), and the journal itself is size-capped by
# systemd/journald.conf.d/zz-pihome.conf.
#
# The tty1 console auto-login (a getty@tty1 drop-in, added by hand in Jan
# 2026 for an HDMI monitor that's no longer used) is switched off too: it
# kept the user's systemd session up all day, which started PipeWire,
# WirePlumber and a never-configured per-user Mosquitto broker (pulled in
# by OpenVAS's notus-scanner) waking the CPU ~10x a second. SSH logins are
# unaffected — each gets its own session, and those services then run only
# while someone's logged in. The drop-in is renamed, not deleted (systemd
# only reads *.conf), so renaming it back and rebooting restores it.
#
# Idempotent — safe to re-run after a re-flash/rebuild. Run once during
# setup (see CLAUDE.md "Setup (on the Pi)"):
#
#   sudo scripts/disable-unused-services.sh

set -eu

UNITS=(
    sysstat-collect.timer
    sysstat-rotate.timer
    sysstat-summary.timer
    plocate-updatedb.timer
    phpsessionclean.timer
    cups.path
    pcscd.socket
    accounts-daemon.service
    runonce.service
    rsyslog.service
)

systemctl disable --now "${UNITS[@]}"

AUTOLOGIN=/etc/systemd/system/getty@tty1.service.d/override.conf
if [ -f "$AUTOLOGIN" ] && grep -q -- --autologin "$AUTOLOGIN"; then
    mv "$AUTOLOGIN" "$AUTOLOGIN.disabled"
    systemctl daemon-reload
fi
