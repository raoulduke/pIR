"""Wakes the Wi-Fi radio early on key presses.

Wi-Fi power save is on for battery (NetworkManager wifi.powersave = 3), so
the first packet after idle can take up to ~1.6s. An outbound packet wakes
the radio sooner than waiting for inbound traffic, and a Wi-Fi action is
rarely the first press of a session, so each press sends a fire-and-forget
ping to give the radio a head start. The radio's re-doze timing couldn't
be measured, so by default every press counts (at most one ping a second).
Tunable in config/settings.json:
  "wifi_wake_enabled": true,             # master on/off
  "wifi_wake_mode": "every_key",         # or "throttled"
  "wifi_wake_interval_seconds": 3.0,     # only used by "throttled"
  "wifi_wake_target": null               # ping target; null = the gateway"""

import subprocess
import threading
import time

from services import config_service

_PING_TIMEOUT_SECONDS = 1
# Pings closer together than this are skipped even in "every_key" mode — a
# held Up/Down repeats ~10 times a second, and the radio is awake anyway
# while keys are being pressed.
_MIN_GAP_SECONDS = 1.0

_lock = threading.Lock()
_last_fire = 0.0
_gateway: str | None = None


def _default_gateway() -> str | None:
    """The default gateway from `ip route`, cached once found (a network
    change restarts the app anyway)."""
    global _gateway
    if _gateway is not None:
        return _gateway
    # Not found yet (the app starts before Wi-Fi is up): look again next
    # time rather than remember "none" for the whole session.
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=2,
        )
        fields = result.stdout.split()
        if "via" in fields:
            _gateway = fields[fields.index("via") + 1]
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return _gateway


def _target() -> str | None:
    return config_service.get_wifi_wake_target() or _default_gateway()


def _fire() -> None:
    """A detached, fire-and-forget ping. The reply doesn't matter and is
    never read; only the act of transmitting does. subprocess.Popen (not
    .run) returns as soon as ping starts, so the caller never waits on it."""
    target = _target()
    if not target:
        return
    try:
        subprocess.Popen(
            ["ping", "-c", "1", "-W", str(_PING_TIMEOUT_SECONDS), target],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def wake(key: str = "") -> None:
    """Key observer (hardware.input.set_key_observer()); *key* is unused.
    Pings without waiting, at most once per _MIN_GAP_SECONDS ("every_key") or
    wifi_wake_interval_seconds ("throttled"). No-op when disabled."""
    if not config_service.get_wifi_wake_enabled():
        return

    interval = _MIN_GAP_SECONDS
    if config_service.get_wifi_wake_mode() == "throttled":
        interval = max(interval, config_service.get_wifi_wake_interval_seconds())
    global _last_fire
    with _lock:
        now = time.monotonic()
        if now - _last_fire < interval:
            return
        _last_fire = now

    _fire()
