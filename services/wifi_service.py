"""Wi-Fi scan/connect via NetworkManager (nmcli).

wlan0 is actively managed by NetworkManager on this Pi (confirmed via
`nmcli device status` — an earlier version of this module assumed only
wpa_supplicant/wpa_cli were involved, checked against the wrong machine;
see project memory). Driving wlan0 with raw wpa_cli commands underneath
NetworkManager risks being overridden or ignored, so this goes through
nmcli instead, which is what actually owns the interface.

Two independent layers of backup if a new network doesn't actually work:
  1. In-process: connect() remembers whatever wlan0 was connected to
     before, and reverts to it if the new network doesn't reach real
     connectivity within WIFI_CONNECT_TIMEOUT_SECONDS.
  2. Out-of-process: scripts/wifi_watchdog.sh is launched detached (via
     systemd-run) before the attempt, independent of this app's own
     process — the backstop for the case layer 1 never gets to run at all
     (this process crashes/hangs mid-connect). Its escalation ladder
     (reassociate, restart the network stack, reboot as a last resort)
     was validated end-to-end with simulated outages — see
     docs/active-device-control-plan.md session notes.
"""

import re
import subprocess
import time

from app.constants import WIFI_INTERFACE, WIFI_CONNECT_TIMEOUT_SECONDS
from hardware import network

_NMCLI_TIMEOUT_SECONDS = 15
_WATCHDOG_SCRIPT = "/home/matt/dev/ir/scripts/wifi_watchdog.sh"
_WATCHDOG_DURATION_SECONDS = 90


def _nmcli(*args: str, timeout: float = _NMCLI_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    return subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)


def _split_terse(line: str) -> list[str]:
    """nmcli -t output is colon-separated with `\\:` escaping a literal
    colon inside a value — a plain str.split(":") would mis-parse an SSID
    or connection name containing one."""
    return re.split(r"(?<!\\):", line)


def scan_networks() -> list[dict]:
    """Scan and return known networks, strongest signal first.

    Each entry: {"ssid": str, "secure": bool}. Hidden (blank-SSID) results
    are dropped, and duplicate SSIDs (seen on multiple APs/bands) keep only
    their strongest reading.
    """
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
             _WATCHDOG_SCRIPT, str(_WATCHDOG_DURATION_SECONDS)],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def connect(ssid: str, password: str = "") -> tuple[bool, str]:
    """Switch wlan0 to *ssid*, reverting to whatever it was connected to
    before if the new network doesn't reach real connectivity in time.
    Returns (success, message).
    """
    previous = _current_connection()
    _start_watchdog()

    args = ["device", "wifi", "connect", ssid, "ifname", WIFI_INTERFACE]
    if password:
        args += ["password", password]

    try:
        _nmcli(*args, timeout=WIFI_CONNECT_TIMEOUT_SECONDS + 10)
    except Exception:
        pass  # fall through to the connectivity check/revert below regardless

    deadline = time.monotonic() + WIFI_CONNECT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if network.is_connected():
            return True, "Connected"
        time.sleep(1)

    # Didn't come up for real — revert to whatever was working before.
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
