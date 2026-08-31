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
)

systemctl disable --now "${UNITS[@]}"
