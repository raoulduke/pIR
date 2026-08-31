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

Confirmed 2026-08-30: a "TV unreachable" report doesn't necessarily mean
the port drifted or the TV is unpaired — the Pi's own local adb daemon can
get stuck (mDNS still showed the TV correctly advertising the configured
port, `adb connect` still failed, `adb kill-server` fixed it instantly).
_connect() below retries once through a server restart before giving up,
so this self-heals without a manual `adb kill-server` on the Pi.

Confirmed 2026-08-30: main.py always runs as root (sudo, for GPIO), but
the one-time `adb pair` above is normally run by hand as `matt` — adb's
identity is a keypair under `$HOME/.android/adbkey`, so root ends up
auto-generating and presenting its own never-paired key, and every
androidtv step fails as "TV unreachable" regardless of the TV actually
being reachable (confirmed via mismatched adbkey.pub fingerprints between
/root/.android and /home/matt/.android — only the latter had
adb_known_hosts.pb from the original pairing). Forcing HOME to matt's for
these subprocess calls makes the trusted identity independent of which
Unix user actually runs the app.
"""

import os
import subprocess

from services import config_service

_ADB_TIMEOUT_SECONDS = 5
_ADB_HOME = "/home/matt"  # see note above — keeps adb's identity stable regardless of which user runs main.py


def _target() -> str | None:
    ip = config_service.get_androidtv_ip()
    port = config_service.get_androidtv_port()
    if not ip or not port:
        return None
    return f"{ip}:{port}"


def _adb(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": _ADB_HOME}
    return subprocess.run(
        ["adb", *args], capture_output=True, text=True, timeout=_ADB_TIMEOUT_SECONDS, env=env,
    )


def _connect(target: str) -> bool:
    """True once *target* is connected, self-healing a stuck local adb
    daemon by restarting it and retrying once before giving up."""
    connect = _adb("connect", target)
    if "connected" in connect.stdout or "already" in connect.stdout:
        return True

    _adb("kill-server")
    connect = _adb("connect", target)
    return "connected" in connect.stdout or "already" in connect.stdout


def _run_shell(*shell_args: str) -> tuple[bool, str]:
    """Connect to the configured TV, self-healing as needed, then run
    *shell_args* via ``adb shell``. Returns (success, message), matching
    services.voice_service.send_command so all three slot into
    preset_service's step handling the same way."""
    target = _target()
    if target is None:
        return False, "TV not configured"

    try:
        if not _connect(target):
            return False, "TV unreachable"

        result = _adb("-s", target, "shell", *shell_args)
        if result.returncode != 0:
            return False, (result.stderr.strip()[:16] or "adb error")
        return True, "Sent"

    except subprocess.TimeoutExpired:
        return False, "Timeout"
    except FileNotFoundError:
        return False, "adb missing"
    except Exception as e:
        return False, str(e)[:16]


def send_keyevent(keycode: str) -> tuple[bool, str]:
    """Send an Android keyevent (e.g. "KEYCODE_HOME", "KEYCODE_BACK")."""
    return _run_shell("input", "keyevent", keycode)


def launch_app(package: str) -> tuple[bool, str]:
    """Launch an app by package name (e.g. "com.google.android.youtube.tv")
    via ``monkey``'s launcher-intent trick — there's no keyevent for
    "open app X", so this goes through the same _run_shell as a keyevent."""
    return _run_shell("monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1")
