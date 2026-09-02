"""Philips Hue Bridge control via its local (LAN) API.

Local, not cloud: the bridge sits on the same network as the Pi
(`hue_bridge_ip` in config/settings.json) and talks plain HTTP with no
internet dependency — see docs discussion favoring this over routing
light control through Google Home for a battery-powered, latency-sensitive
device. The username/API key was minted once via the bridge's link-button
flow (`POST /api` with `devicetype`) and is read from `hue_username_file`,
mirroring how voice_service.py keeps the HA token out of the repo rather
than in settings.json.
"""

import json
from pathlib import Path

from services import config_service

_TIMEOUT_SECONDS = 5


def _username_path() -> Path | None:
    value = config_service.get_hue_username_file()
    return Path(value) if value else None


def username_exists() -> bool:
    path = _username_path()
    return path is not None and path.exists()


def _load_username() -> str:
    path = _username_path()
    if path is None:
        raise FileNotFoundError("hue_username_file not set in config")
    with open(path, "r") as f:
        return f.read().strip()


def _base_url() -> str:
    bridge_ip = config_service.get_hue_bridge_ip()
    if not bridge_ip:
        raise FileNotFoundError("hue_bridge_ip not set in config")
    username = _load_username()
    return f"http://{bridge_ip}/api/{username}"


def _request(method: str, path: str, body: dict | None = None) -> dict | list:
    # Deferred for the same reason voice_service.py defers it: real
    # import-time cost, worth paying only when a Hue command actually runs.
    import urllib.request
    import urllib.error

    url = f"{_base_url()}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None

    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def list_lights() -> dict:
    """Return the bridge's raw {light_id: {...}} mapping."""
    return _request("GET", "/lights")


def get_light(light_id: str) -> dict:
    return _request("GET", f"/lights/{light_id}")


def set_light_state(light_id: str, **state) -> list:
    """Set arbitrary state fields on a light, e.g. on=True, bri=200."""
    return _request("PUT", f"/lights/{light_id}/state", state)


def turn_on(light_id: str) -> list:
    return set_light_state(light_id, on=True)


def turn_off(light_id: str) -> list:
    return set_light_state(light_id, on=False)


def identify(light_id: str) -> list:
    """Briefly flash a light (Hue's built-in "select" alert) without
    otherwise changing its on/off state — for matching a bridge light_id
    to the physical bulb in a room."""
    return set_light_state(light_id, alert="select")


def rename_light(light_id: str, name: str) -> list:
    return _request("PUT", f"/lights/{light_id}", {"name": name})


def is_reachable(light_id: str) -> bool:
    """True if the bridge can currently talk to the bulb over Zigbee.

    Use this, not state["on"], to tell if a bulb is actually powered.
    "on" only reflects the last command the bridge sent — if a bulb is
    cut at a wall switch, the bridge can't detect that and "on" keeps
    reporting whatever it last told the bulb, stuck true, even though the
    bulb is dark. "reachable" flips false as soon as the bridge loses
    contact (confirmed 2026-09-01: wall switch off -> reachable false
    within one poll, "on" stayed true)."""
    return bool(get_light(light_id).get("state", {}).get("reachable"))
