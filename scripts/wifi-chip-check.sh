#!/bin/bash
# Boot-time check that the Wi-Fi chip came up. About 1 boot in 90, brcmfmac's
# firmware download over SDIO fails ("Downloaded RAM image is corrupted",
# "dongle image file download failed"), so wlan0 never appears and the Pi
# has no network until the next boot. Run by systemd/wifi-chip-check.service.
#
# Exits as soon as wlan0 exists (every normal boot, a few seconds in).
# Otherwise, in order, checking for wlan0 after each step:
#   1. reload the brcmfmac module (retries the firmware download)
#   2. unbind and rebind the SDIO host the chip sits on (resets the bus;
#      the SD card is on a different host, checked below)
#   3. reboot, unless this script already rebooted within the last
#      REBOOT_GUARD_SECONDS, so a chip that never comes up can't loop.

set -u

IFACE="wlan0"
WAIT_FOR_BOOT_SECONDS=30   # normal boots have wlan0 within ~5s of the kernel
STEP_WAIT_SECONDS=20
REBOOT_STAMP="/var/lib/pihome/wifi-chip-check-reboot"
REBOOT_GUARD_SECONDS=600
SDIO_DRIVER="/sys/bus/platform/drivers/mmc-bcm2835"
SDIO_HOST="3f300000.mmcnr"

log() { echo "$*"; }   # stdout goes to the journal

have_iface() { [ -e "/sys/class/net/$IFACE" ]; }

wait_for_iface() {
    local deadline=$(( $(cut -d. -f1 /proc/uptime) + $1 ))
    while [ "$(cut -d. -f1 /proc/uptime)" -lt "$deadline" ]; do
        have_iface && return 0
        sleep 1
    done
    have_iface
}

# Wait until WAIT_FOR_BOOT_SECONDS of uptime, not from this script's start.
uptime_s=$(cut -d. -f1 /proc/uptime)
if wait_for_iface $(( WAIT_FOR_BOOT_SECONDS > uptime_s ? WAIT_FOR_BOOT_SECONDS - uptime_s : 0 )); then
    exit 0
fi

log "$IFACE missing ${WAIT_FOR_BOOT_SECONDS}s after boot; recent brcmfmac messages:"
journalctl -k -b --no-pager | grep -iE 'brcmf|mmc1' | tail -5

log "Step 1: reloading brcmfmac"
modprobe -r brcmfmac 2>&1
sleep 1
modprobe brcmfmac 2>&1
if wait_for_iface "$STEP_WAIT_SECONDS"; then
    log "Recovered: $IFACE is back after reloading brcmfmac"
    exit 0
fi

# The chip's host must not be the SD card's (the root filesystem).
root_host=$(basename "$(readlink -f /sys/class/mmc_host/mmc0/device)")
if [ -e "$SDIO_DRIVER/$SDIO_HOST" ] && [ "$root_host" != "$SDIO_HOST" ]; then
    log "Step 2: rebinding SDIO host $SDIO_HOST"
    modprobe -r brcmfmac 2>&1
    echo "$SDIO_HOST" > "$SDIO_DRIVER/unbind"
    sleep 2
    echo "$SDIO_HOST" > "$SDIO_DRIVER/bind"
    sleep 2
    modprobe brcmfmac 2>&1
    if wait_for_iface "$STEP_WAIT_SECONDS"; then
        log "Recovered: $IFACE is back after rebinding the SDIO host"
        exit 0
    fi
else
    log "Step 2 skipped: $SDIO_HOST not bound to mmc-bcm2835, or it's the SD card's host ($root_host)"
fi

now=$(date +%s)
last=$(cat "$REBOOT_STAMP" 2>/dev/null || echo 0)
if [ $(( now - last )) -lt "$REBOOT_GUARD_SECONDS" ]; then
    log "Still no $IFACE, and this script already rebooted $(( now - last ))s ago; not rebooting again"
    exit 1
fi
log "Step 3: still no $IFACE, rebooting"
mkdir -p "$(dirname "$REBOOT_STAMP")"
echo "$now" > "$REBOOT_STAMP"
sync
systemctl reboot
