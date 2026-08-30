"""Android TV / Google TV remote control over adb (Wireless debugging).

Bypasses IR entirely for keys the TV's own IR remote can't emulate (Home,
true Back distinct from Menu — see docs/active-device-control-plan.md and
the session notes on the Insignia remote's Home button opening Menu
instead). Requires the TV to be paired once via Settings > System >
Developer options > Wireless debugging > Pair device with pairing code:

    adb pair <tv_ip>:<pairing_port> <pairing_code>

Pairing trust persists (keyed off the adb keypair in ~/.android/adbkey),
but the TV's connect port can change across TV reboots, so this module
always re-runs ``adb connect`` before sending a keyevent rather than
assuming a prior connection is still live — that call is a fast no-op if
already connected.
"""

import subprocess

from services import config_service

_ADB_TIMEOUT_SECONDS = 5


def _target() -> str | None:
    ip = config_service.get_androidtv_ip()
    port = config_service.get_androidtv_port()
    if not ip or not port:
        return None
    return f"{ip}:{port}"


def send_keyevent(keycode: str) -> tuple[bool, str]:
    """Send an Android keyevent (e.g. "KEYCODE_HOME", "KEYCODE_BACK").

    Returns (success, message), matching services.voice_service.send_command
    so both slot into preset_service's step handling the same way.
    """
    target = _target()
    if target is None:
        return False, "TV not configured"

    try:
        connect = subprocess.run(
            ["adb", "connect", target],
            capture_output=True, text=True, timeout=_ADB_TIMEOUT_SECONDS,
        )
        if "connected" not in connect.stdout and "already" not in connect.stdout:
            return False, "TV unreachable"

        result = subprocess.run(
            ["adb", "-s", target, "shell", "input", "keyevent", keycode],
            capture_output=True, text=True, timeout=_ADB_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return False, (result.stderr.strip()[:16] or "adb error")
        return True, "Sent"

    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except FileNotFoundError:
        return False, "adb missing"
    except Exception as e:
        return False, str(e)[:16]
