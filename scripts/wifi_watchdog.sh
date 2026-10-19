#!/bin/bash
# Wi-Fi safety net for testing anything that can disrupt wlan0 (monitor
# mode, injection tests, deauth) — watches real connectivity and escalates
# through increasingly aggressive fixes, up to a reboot, if it drops and
# doesn't recover on its own.
#
# Run this DETACHED FROM ANY SSH SESSION, on the Pi itself, before starting
# a risky test — the whole point is surviving that session's own
# connection dying (as happened testing aireplay-ng against this exact
# Wi-Fi chip: iw reported concurrent managed+monitor support, but a
# multi-channel probe test retuned the shared radio anyway and dropped
# wlan0 with nothing watching to recover it):
#
#   sudo systemd-run --unit=wifi-watchdog "$PWD/scripts/wifi_watchdog.sh" [duration_seconds] [start_delay_seconds]
#
# (from the project root — systemd-run needs the absolute path)
#
# Check progress or stop it early:
#   journalctl -u wifi-watchdog -f
#   sudo systemctl stop wifi-watchdog
#
# Runs for duration_seconds total (default 600 = 10 min), checking every
# CHECK_INTERVAL_SECONDS. Exits clean if connectivity is fine throughout —
# not meant to run as a permanent daemon (battery life is a project
# priority), only kicked off deliberately right before risky Wi-Fi testing.

set -u

WATCH_DURATION_SECONDS="${1:-600}"
# Wait this long before watching at all. services/wifi_service.py's
# connect() passes its own worst-case duration, so a legitimate (slow) Wi-Fi
# switch or revert finishes before any remediation could interrupt it; the
# watchdog is only there for the app crashing or hanging mid-attempt.
START_DELAY_SECONDS="${2:-0}"
CHECK_INTERVAL_SECONDS=5
FAIL_THRESHOLD=3       # consecutive failed checks before declaring it down (~15s)
GRACE_SECONDS=15       # wait after each remediation step before rechecking
LOG_FILE="/var/log/wifi-watchdog.log"
IFACE="wlan0"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG_FILE"
}

is_connected() {
    local gw
    gw="$(ip route | awk '/^default/ {print $3; exit}')"
    if [ -n "$gw" ] && ping -c1 -W2 "$gw" >/dev/null 2>&1; then
        return 0
    fi
    ping -c1 -W2 1.1.1.1 >/dev/null 2>&1
}

remove_monitor_interfaces() {
    local mon found=0
    for mon in $(iw dev | awk '/Interface/{iface=$2} /type monitor/{print iface}'); do
        found=1
        log "Removing leftover monitor interface: $mon"
        ip link set "$mon" down 2>/dev/null
        iw dev "$mon" del 2>/dev/null
    done
    [ "$found" -eq 0 ] && log "No leftover monitor interfaces found"
}

reassociate() {
    log "Attempting nmcli reassociate on $IFACE"
    nmcli device disconnect "$IFACE" >/dev/null 2>&1
    sleep 2
    nmcli device connect "$IFACE" >/dev/null 2>&1
}

restart_network_stack() {
    log "Restarting wpa_supplicant and NetworkManager"
    systemctl restart wpa_supplicant.service 2>/dev/null
    systemctl restart NetworkManager.service 2>/dev/null
}

log "wifi-watchdog started, watching for ${WATCH_DURATION_SECONDS}s after a ${START_DELAY_SECONDS}s delay"
sleep "$START_DELAY_SECONDS"

fail_streak=0
elapsed=0

while [ "$elapsed" -lt "$WATCH_DURATION_SECONDS" ]; do
    if is_connected; then
        if [ "$fail_streak" -gt 0 ]; then
            log "Connectivity OK again after $fail_streak failed check(s)"
        fi
        fail_streak=0
    else
        fail_streak=$((fail_streak + 1))
        log "Connectivity check failed ($fail_streak/$FAIL_THRESHOLD)"
    fi

    if [ "$fail_streak" -ge "$FAIL_THRESHOLD" ]; then
        log "Connectivity lost — beginning escalation"

        remove_monitor_interfaces
        sleep "$GRACE_SECONDS"
        if is_connected; then
            log "Recovered after removing monitor interface(s)"
            fail_streak=0
            elapsed=$((elapsed + CHECK_INTERVAL_SECONDS + GRACE_SECONDS))
            continue
        fi

        reassociate
        sleep "$GRACE_SECONDS"
        if is_connected; then
            log "Recovered after nmcli reassociate"
            fail_streak=0
            elapsed=$((elapsed + CHECK_INTERVAL_SECONDS + GRACE_SECONDS))
            continue
        fi

        restart_network_stack
        sleep "$GRACE_SECONDS"
        if is_connected; then
            log "Recovered after restarting wpa_supplicant/NetworkManager"
            fail_streak=0
            elapsed=$((elapsed + CHECK_INTERVAL_SECONDS + GRACE_SECONDS))
            continue
        fi

        log "Still unreachable after all soft remediation — rebooting"
        reboot
        exit 0
    fi

    sleep "$CHECK_INTERVAL_SECONDS"
    elapsed=$((elapsed + CHECK_INTERVAL_SECONDS))
done

log "wifi-watchdog finished its watch window with no unrecovered failure"
