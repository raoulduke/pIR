"""Voice / Home Assistant command service."""

import json
import urllib.request
import urllib.error
from pathlib import Path

from services import config_service


def _token_path() -> Path | None:
    value = config_service.get_ha_token_file()
    return Path(value) if value else None


def token_exists() -> bool:
    path = _token_path()
    return path is not None and path.exists()


def _load_token() -> str:
    path = _token_path()
    if path is None:
        raise FileNotFoundError("ha_token_file not set in config")
    with open(path, "r") as f:
        return f.read().strip()


def send_command(text: str) -> tuple[bool, str]:
    """Send *text* to Google Assistant via Home Assistant.

    Returns ``(success, message)``.
    """
    ha_url = config_service.get_ha_url()
    sdk_path = config_service.get_google_sdk_path()
    endpoint = f"{ha_url}{sdk_path}"

    try:
        token = _load_token()
        payload = json.dumps({"command": text}).encode("utf-8")

        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
            if isinstance(result, list) and result:
                return True, "Sent"
            return True, "OK"

    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except urllib.error.URLError:
        return False, "Network error"
    except FileNotFoundError:
        return False, "Token missing"
    except Exception as e:
        return False, str(e)[:16]
