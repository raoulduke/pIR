import copy
import json
from pathlib import Path

from app.constants import BACKLIGHT_IDLE_SECONDS, SWEEP_GAP_SECONDS
from app.files import read_json_cached, write_text_atomic

_CONFIG_FILE = Path(__file__).parent.parent / "config" / "settings.json"
_DEFAULT_HOME = "home"


def _load() -> dict:
    """settings.json (cached; don't modify — see _load_for_update())."""
    return read_json_cached(_CONFIG_FILE, {})


def _load_for_update() -> dict:
    return copy.deepcopy(_load())


def _save(data: dict) -> None:
    write_text_atomic(_CONFIG_FILE, json.dumps(data, indent=2))


def get_active_home() -> str:
    return _load().get("active_home", _DEFAULT_HOME)


def set_active_home(name: str) -> None:
    data = _load_for_update()
    data["active_home"] = name
    _save(data)


def rename_active_home_if_matches(old: str, new: str) -> None:
    """Point active_home at *new* if it's *old* — written explicitly even when
    it was only the default, which would otherwise name a missing home."""
    data = _load_for_update()
    if data.get("active_home", _DEFAULT_HOME) == old:
        data["active_home"] = new
        _save(data)


def get_ha_url() -> str | None:
    return _load().get("ha_url")


def get_ha_token_file() -> str | None:
    return _load().get("ha_token_file")


def get_google_sdk_path() -> str | None:
    return _load().get("google_sdk_path")


def get_device_slot_offset() -> int:
    return _load().get("device_slot_offset", 0)


def get_androidtv_ip() -> str | None:
    return _load().get("androidtv_ip")


def get_androidtv_certfile() -> str | None:
    return _load().get("androidtv_cert_file")


def get_androidtv_keyfile() -> str | None:
    return _load().get("androidtv_key_file")


def get_hue_bridge_ip() -> str | None:
    return _load().get("hue_bridge_ip")


def get_hue_username_file() -> str | None:
    return _load().get("hue_username_file")


def get_wifi_wake_enabled() -> bool:
    return _load().get("wifi_wake_enabled", True)


def get_wifi_wake_mode() -> str:
    """"every_key" (default): a wake ping per press, at most one a second;
    "throttled": at most one per get_wifi_wake_interval_seconds()."""
    return _load().get("wifi_wake_mode", "every_key")


def get_wifi_wake_interval_seconds() -> float:
    """Only consulted when get_wifi_wake_mode() == "throttled"."""
    return _load().get("wifi_wake_interval_seconds", 3.0)


def get_wifi_wake_target() -> str | None:
    """Ping target for services.wifi_wake_service.wake() — None (the
    default) auto-detects the LAN default gateway at runtime instead."""
    return _load().get("wifi_wake_target")


def get_backlight_timeout_seconds() -> int | None:
    """Menu > Settings > Backlight. None means Never (always on); an
    absent key falls back to BACKLIGHT_IDLE_SECONDS."""
    return _load().get("backlight_timeout_seconds", BACKLIGHT_IDLE_SECONDS)


def set_backlight_timeout_seconds(seconds: int | None) -> None:
    data = _load_for_update()
    data["backlight_timeout_seconds"] = seconds
    _save(data)


def get_sweep_gap_seconds() -> float:
    """Menu > Settings > Sweep Speed: seconds between codes in a sweep."""
    return _load().get("sweep_gap_seconds", SWEEP_GAP_SECONDS)


def set_sweep_gap_seconds(seconds: float) -> None:
    data = _load_for_update()
    data["sweep_gap_seconds"] = seconds
    _save(data)
