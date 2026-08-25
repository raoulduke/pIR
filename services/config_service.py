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


def get_bridge_ip() -> str | None:
    return _load().get("bridge_ip")


def get_device_slot_offset() -> int:
    return _load().get("device_slot_offset", 0)


def set_device_slot_offset(offset: int) -> None:
    data = _load()
    data["device_slot_offset"] = offset
    _save(data)
