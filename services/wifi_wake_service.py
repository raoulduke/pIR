"""Speculative Wi-Fi radio wake-up.

Background: Wi-Fi power save (802.11 PS mode) is enabled on this device
(NetworkManager's `wifi.powersave = 3` in
/etc/NetworkManager/conf.d/wifi-powersave.conf — not tracked in this repo)
for the battery savings, which puts the radio in a low-power doze state
between activity. The tradeoff is wake latency: measured 2026-09 as up to
~1.6s for the first packet after an idle period, dropping to single-digit
ms once the radio's awake (see git history around this module's
introduction for the actual ping measurements and the "No route to host"
symptom that led to investigating this at all). CLAUDE.md previously
called for disabling power save outright to avoid this; this module is
the middle ground instead — keep the battery savings, hide the latency.

Mechanism: a Wi-Fi station's own radio controls when *it* transmits
(unlike receiving, which has to wait for the AP's next DTIM beacon to
learn there's buffered traffic waiting) — so a small outbound packet
tends to wake the radio faster than waiting on an inbound one. wake() is
called from hardware/input.py's read_key() (via set_key_observer(), wired
in main.py) on every real keypress, globally, across every screen — a
Wi-Fi-dependent action (voice_service/hue_service/androidtv_service) is
often not the very first press of a session (cycling devices, opening a
menu, etc. usually comes first), so nudging the radio awake on those
earlier presses gives it a head start before the press that actually
needs the network.

What's NOT known (see the diagnostic conversation this module came out
of): the Pi's onboard chip (Broadcom BCM43430/1, brcmfmac driver) doesn't
expose the firmware's own "how long until it re-dozes after activity"
timing anywhere in sysfs — measured Pi-initiated pings stayed fast for at
least 45s after one wake, but that test ran back-to-back with a lot of
other SSH activity on the same radio and could not cleanly isolate a
genuinely cold, multi-minute-idle re-doze timing, nor whether a full TCP
handshake/HTTP response (what the real Wi-Fi actions actually need)
behaves the same as a bare ICMP reply. Given that uncertainty, the
default (get_wifi_wake_mode() == "every_key") does not throttle at all —
better to fire on every press than to guess an interval and have it
silently stop helping partway through a session. config/settings.json's
wifi_wake_* keys (services/config_service.py) exist so this can be tuned
without a code change once more is learned:
  "wifi_wake_enabled": true,             # master on/off
  "wifi_wake_mode": "every_key",         # or "throttled"
  "wifi_wake_interval_seconds": 3.0,     # only used by "throttled"
  "wifi_wake_target": null               # ping target; null = auto-detect gateway
"""

import subprocess
import threading
import time

from services import config_service

_PING_TIMEOUT_SECONDS = 1

_lock = threading.Lock()
_last_fire = 0.0
_gateway: str | None = None
_gateway_checked = False


def _default_gateway() -> str | None:
    """The LAN default gateway, parsed from `ip route` once and cached for
    the life of the process — a reliable always-present ping target with
    no config needed. Re-checked never, not just per-call: if the gateway
    genuinely changes (new network), a restart already happens for every
    other reason a network change would matter to this app."""
    global _gateway, _gateway_checked
    if _gateway_checked:
        return _gateway
    _gateway_checked = True
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
    """Runs on its own thread (see wake()) — a detached, fire-and-forget
    ping. The reply doesn't matter and is never read; only the act of
    transmitting does. subprocess.Popen (not .run) so this thread doesn't
    block waiting for the ping to actually finish either."""
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
    """Call on every real keypress — see hardware/input.py's
    set_key_observer()/read_key(). *key* is accepted (and ignored) only to
    match that observer contract; nothing here cares which key it was.

    Never blocks: a no-op check (config read + a lock for the throttled
    case) on the calling thread, then the actual ping runs detached on its
    own thread. No-op entirely if disabled in config/settings.json."""
    if not config_service.get_wifi_wake_enabled():
        return

    if config_service.get_wifi_wake_mode() == "throttled":
        interval = config_service.get_wifi_wake_interval_seconds()
        global _last_fire
        with _lock:
            now = time.monotonic()
            if now - _last_fire < interval:
                return
            _last_fire = now

    threading.Thread(target=_fire, daemon=True).start()
