"""Wi-Fi scan/connect through NetworkManager (nmcli), which owns wlan0.

A failed switch is undone twice over: connect() reverts to the previous
network itself, and scripts/wifi_watchdog.sh, started detached beforehand,
recovers connectivity if the app crashes or hangs mid-attempt."""

import re
import subprocess
import time
from pathlib import Path

from app.constants import WIFI_INTERFACE, WIFI_CONNECT_TIMEOUT_SECONDS
from hardware import network

_NMCLI_TIMEOUT_SECONDS = 15
# Absolute, from this checkout: systemd-run starts it outside the app's
# working directory.
_WATCHDOG_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "wifi_watchdog.sh"
_WATCHDOG_DURATION_SECONDS = 90
# Longer than connect()'s worst case (~80s), so the watchdog never acts
# during a legitimate attempt.
_WATCHDOG_START_DELAY_SECONDS = 90


def _nmcli(*args: str, timeout: float = _NMCLI_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)


def _split_terse(line: str) -> list[str]:
    """nmcli -t output is colon-separated with `\\:` escaping a literal
    colon inside a value — a plain str.split(":") would mis-parse an SSID
    or connection name containing one."""
    return re.split(r"(?<!\\):", line)


def is_ready() -> bool:
    """True once NetworkManager is answering. The app starts before it, so
    right after boot an empty scan can mean "not ready" rather than "nothing
    in range"."""
    try:
        result = _nmcli("-t", "-f", "RUNNING", "general", "status", timeout=_NMCLI_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return result.returncode == 0 and result.stdout.strip() == "running"


def scan_networks() -> list[dict]:
    """Networks in range, strongest first: [{"ssid", "secure"}]. Hidden
    networks are dropped; an SSID seen on several APs keeps its strongest."""
    try:
        _nmcli("device", "wifi", "rescan")
        time.sleep(2)  # give the rescan a moment before listing
        result = _nmcli("-t", "-f", "SSID,SECURITY,SIGNAL", "device", "wifi", "list", "ifname", WIFI_INTERFACE)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    best: dict[str, dict] = {}
    for line in result.stdout.strip().splitlines():
        parts = _split_terse(line)
        if len(parts) < 3:
            continue
        ssid, security, signal = parts[0], parts[1], parts[2]
        if not ssid:
            continue
        secure = security not in ("", "--")
        try:
            signal_level = int(signal)
        except ValueError:
            signal_level = -1

        existing = best.get(ssid)
        if existing is None or signal_level > existing["_signal"]:
            best[ssid] = {"ssid": ssid, "secure": secure, "_signal": signal_level}

    return sorted(best.values(), key=lambda n: n["_signal"], reverse=True)


def _current_connection() -> str | None:
    try:
        result = _nmcli("-t", "-f", "GENERAL.CONNECTION", "device", "show", WIFI_INTERFACE)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    parts = _split_terse(result.stdout.strip())
    name = parts[1] if len(parts) == 2 else ""
    return name if name and name != "--" else None


def _start_watchdog() -> None:
    """Independent backstop in case the in-process revert below never
    gets to run at all (this process crashes or hangs). Best-effort —
    a broken watchdog launch must never block a real connect attempt."""
    try:
        subprocess.run(
            ["systemd-run", f"--unit=wifi-watchdog-{int(time.time())}",
             str(_WATCHDOG_SCRIPT), str(_WATCHDOG_DURATION_SECONDS),
             str(_WATCHDOG_START_DELAY_SECONDS)],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


# Name for a throwaway profile that checks a typed password against an SSID
# that already has a saved profile (see connect()).
_TRIAL_PREFIX = "pihome-trial-"


def _wifi_profiles() -> dict[str, str]:
    """Saved Wi-Fi profiles: SSID -> profile name."""
    try:
        listing = _nmcli("-t", "-f", "NAME,TYPE", "connection", "show")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {}
    profiles = {}
    for line in listing.stdout.splitlines():
        parts = _split_terse(line)
        if len(parts) != 2 or parts[1] != "802-11-wireless":
            continue
        name = parts[0].replace("\\:", ":")
        if name.startswith(_TRIAL_PREFIX):
            continue
        try:
            ssid = _nmcli("-g", "802-11-wireless.ssid", "connection", "show", name).stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
        if ssid:
            profiles.setdefault(ssid, name)
    return profiles


def _delete_profile(name: str) -> None:
    try:
        _nmcli("connection", "delete", name)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def _online_on(profile: str, deadline: float) -> bool:
    """Poll until wlan0 is on *profile* with connectivity, or *deadline*
    (time.monotonic()) passes. Both matter: is_connected() alone stays true
    on the previous network after a failed attempt."""
    while True:
        if _current_connection() == profile and network.is_connected():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(1)


def connect(ssid: str, password: str = "") -> tuple[bool, str]:
    """Switch wlan0 to *ssid*; if it doesn't come up, revert to the previous
    network. Returns (success, message).

    A saved profile is never deleted: a typed password for an SSID that
    already has one is tried on a throwaway trial profile, copied into the
    saved profile only once it connects. A failed attempt removes only
    profiles it created."""
    previous = _current_connection()
    saved = _wifi_profiles().get(ssid)
    trial = f"{_TRIAL_PREFIX}{ssid}" if saved else None
    _start_watchdog()

    args = ["device", "wifi", "connect", ssid, "ifname", WIFI_INTERFACE]
    if password:
        args += ["password", password]
    if trial:
        if previous == trial:
            trial += "-2"  # an earlier trial is what's connected; leave it be
        _delete_profile(trial)  # a leftover from an interrupted attempt
        args += ["name", trial]
    attempt = trial or ssid  # nmcli names a new profile after the SSID

    result = None
    try:
        result = _nmcli(*args, timeout=WIFI_CONNECT_TIMEOUT_SECONDS + 10)
    except Exception:
        pass  # fall through to the connectivity check/revert below regardless

    # A nonzero exit means nmcli already knows it failed (e.g. a bad password
    # fails the handshake almost at once) — check once rather than burn the
    # poll window; only poll when it reported success but DHCP may lag.
    polling = result is not None and result.returncode == 0
    deadline = time.monotonic() + (WIFI_CONNECT_TIMEOUT_SECONDS if polling else 0)
    if _online_on(attempt, deadline):
        if not trial:
            return True, "Connected"
        # The typed password works: store it in the saved profile and use that.
        try:
            if password:
                _nmcli("connection", "modify", saved, "wifi-sec.psk", password)
            _nmcli("connection", "up", saved, timeout=WIFI_CONNECT_TIMEOUT_SECONDS + 10)
        except Exception:
            pass
        if _online_on(saved, time.monotonic() + WIFI_CONNECT_TIMEOUT_SECONDS):
            _delete_profile(trial)
        # Otherwise keep the trial: it's the profile that's working.
        return True, "Connected"

    # Didn't come up for real — remove only a profile this attempt created,
    # then revert to whatever was working before.
    if trial:
        _delete_profile(trial)
    else:
        # No profile existed for this SSID before, so any there now is this
        # attempt's and never connected.
        created = _wifi_profiles().get(ssid)
        if created:
            _delete_profile(created)
    if previous:
        try:
            _nmcli("connection", "up", previous, timeout=WIFI_CONNECT_TIMEOUT_SECONDS)
        except Exception:
            pass
        time.sleep(3)
        if network.is_connected():
            return False, "Failed, reverted"
        return False, "Failed, no fallback"

    return False, "Failed"
