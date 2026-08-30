"""Wi-Fi scan/connect via wpa_cli.

Wraps the wpa_supplicant + dhcpcd stack already managing Wi-Fi on the Pi
(confirmed: no NetworkManager present) — this doesn't introduce a new
network manager, just drives the existing one from the app.
"""

import subprocess
import time

from app.constants import (
    WIFI_INTERFACE, WIFI_SCAN_WAIT_SECONDS, WIFI_CONNECT_TIMEOUT_SECONDS,
)
from hardware import network

_WPA_CLI_TIMEOUT_SECONDS = 5


def _wpa_cli(*args: str) -> str:
    result = subprocess.run(
        ["wpa_cli", "-i", WIFI_INTERFACE, *args],
        capture_output=True, text=True, timeout=_WPA_CLI_TIMEOUT_SECONDS,
    )
    return result.stdout


def scan_networks() -> list[dict]:
    """Scan and return known networks, strongest signal first.

    Each entry: {"ssid": str, "secure": bool}. Hidden (blank-SSID) results
    are dropped, and duplicate SSIDs (seen on multiple APs/bands) keep only
    their strongest reading.
    """
    _wpa_cli("scan")
    time.sleep(WIFI_SCAN_WAIT_SECONDS)
    raw = _wpa_cli("scan_results")

    best: dict[str, dict] = {}
    lines = raw.strip().splitlines()[1:]  # drop the header row
    for line in lines:
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        _bssid, _freq, signal, flags, ssid = parts[0], parts[1], parts[2], parts[3], parts[4]
        if not ssid:
            continue
        secure = "WPA" in flags or "WEP" in flags
        try:
            signal_level = int(signal)
        except ValueError:
            signal_level = -999

        existing = best.get(ssid)
        if existing is None or signal_level > existing["_signal"]:
            best[ssid] = {"ssid": ssid, "secure": secure, "_signal": signal_level}

    return sorted(best.values(), key=lambda n: n["_signal"], reverse=True)


def connect(ssid: str, password: str = "") -> tuple[bool, str]:
    """Add/enable a network and wait for a real connection.

    Returns (success, message). *password* is ignored (open network) when
    empty.
    """
    try:
        network_id = _wpa_cli("add_network").strip()
        if not network_id.isdigit():
            return False, "wpa_cli error"

        _wpa_cli("set_network", network_id, "ssid", f'"{ssid}"')
        if password:
            _wpa_cli("set_network", network_id, "psk", f'"{password}"')
        else:
            _wpa_cli("set_network", network_id, "key_mgmt", "NONE")

        _wpa_cli("enable_network", network_id)
        _wpa_cli("select_network", network_id)
        _wpa_cli("save_config")

        deadline = time.monotonic() + WIFI_CONNECT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if network.is_connected():
                return True, "Connected"
            time.sleep(1)

        return False, "Timed out"

    except subprocess.TimeoutExpired:
        return False, "wpa_cli timeout"
    except FileNotFoundError:
        return False, "wpa_cli missing"
    except Exception as e:
        return False, str(e)[:16]
