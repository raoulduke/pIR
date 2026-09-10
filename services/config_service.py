from pathlib import Path
import json

_CONFIG_FILE = Path(__file__).parent.parent / "config" / "settings.json"
_DEFAULT_HOME = "home"


def _load() -> dict:
    if _CONFIG_FILE.exists():
        try:
            return json.loads(_CONFIG_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def _save(data: dict) -> None:
    _CONFIG_FILE.write_text(json.dumps(data, indent=2))


def get_active_home() -> str:
    return _load().get("active_home", _DEFAULT_HOME)


def set_active_home(name: str) -> None:
    data = _load()
    data["active_home"] = name
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
    """"every_key" (default) fires services.wifi_wake_service.wake() on
    every keypress; "throttled" limits it to at most once per
    get_wifi_wake_interval_seconds(). See that module's docstring for why
    "every_key" is the current default — untuned, deliberately generous."""
    return _load().get("wifi_wake_mode", "every_key")


def get_wifi_wake_interval_seconds() -> float:
    """Only consulted when get_wifi_wake_mode() == "throttled"."""
    return _load().get("wifi_wake_interval_seconds", 3.0)


def get_wifi_wake_target() -> str | None:
    """Ping target for services.wifi_wake_service.wake() — None (the
    default) auto-detects the LAN default gateway at runtime instead."""
    return _load().get("wifi_wake_target")
