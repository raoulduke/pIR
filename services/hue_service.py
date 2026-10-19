"""Philips Hue bridge control over its local HTTP API (`hue_bridge_ip`;
the API key is read from `hue_username_file`, kept out of the repo)."""

import json
import logging
import threading
from pathlib import Path

from services import config_service

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 5

# Last reachability check per light ID: True/False, or None if the check
# itself failed (bridge or Wi-Fi down).
_reachable: dict[str, bool | None] = {}


def _username_path() -> Path | None:
    value = config_service.get_hue_username_file()
    return Path(value) if value else None


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
    # Imported here: urllib.request is slow to import (~125ms) and rarely needed.
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


def get_light(light_id: str) -> dict:
    return _request("GET", f"/lights/{light_id}")


def set_light_state(light_id: str, **state) -> list:
    """Set arbitrary state fields on a light, e.g. on=True, bri=200."""
    return _request("PUT", f"/lights/{light_id}/state", state)


def turn_on(light_id: str) -> list:
    return set_light_state(light_id, on=True)


def turn_off(light_id: str) -> list:
    return set_light_state(light_id, on=False)


def is_reachable(light_id: str) -> bool:
    """True if the bridge can reach the bulb. Use this, not state["on"], to
    tell whether a bulb has power: "on" only reflects the last command, so a
    bulb cut at the wall switch still reports on."""
    return bool(get_light(light_id).get("state", {}).get("reachable"))


def is_known_unreachable(light_id: str) -> bool:
    return _reachable.get(light_id) is False


def check_reachable_background(light_ids: list[str], on_done) -> None:
    """Check each light on a background thread, record the result for
    is_known_unreachable(), then call *on_done()*. A failed request records
    None (unknown), not False, so sends still go out and report the real
    error."""
    def _run():
        for light_id in light_ids:
            try:
                _reachable[light_id] = is_reachable(light_id)
            except (OSError, ValueError) as e:
                log.warning("Hue reachability check failed (light %s): %r", light_id, e)
                _reachable[light_id] = None
        on_done()

    threading.Thread(target=_run, name="hue-reachable", daemon=True).start()
