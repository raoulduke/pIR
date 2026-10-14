"""Android TV / Google TV remote control via the Android TV Remote protocol
v2 (androidtvremote2) — the same protocol the Google Home app and physical
Android TV remotes use. Replaced the earlier adb/Wireless-debugging-based
implementation (2026-09-09): adb is a debugging protocol repurposed for
remote control, and had already caused two separate outages in one week —
a lost pairing trust after a TV update, and the connect port drifting on
every Wireless-debugging toggle/TV reboot. This protocol needs neither
Developer Options nor adb; pairing is a one-time self-signed-cert exchange
this module's async_start_pairing()/finish_pairing() flow handles (see
scripts/pair_androidtv.py), independent of the TV's own debugging settings.

It's also meaningfully faster: adb paid two subprocess spawns (connect +
shell) per keypress, real overhead on a Pi Zero 2 W. Here, a keypress on an
already-open connection is a single non-blocking socket write — measured
~2-3ms of call overhead against the actual TV during PoC testing, versus
adb's measured hundreds of ms to multiple seconds.

Bypasses IR entirely for keys the TV's own IR remote can't emulate (Home,
true Back distinct from Menu — see docs/active-device-control-plan.md and
the session notes on the Insignia remote's Home button opening Menu
instead).

Connect-on-demand, not held open 24/7 — see CLAUDE.md's battery note: the
device pings an open connection every ~5s to keep it alive, so holding one
open at all times would mean a permanent wake-every-5s tax. Instead, a
connection opens on first use after being idle and closes after
_IDLE_DISCONNECT_SECONDS of no further activity, so the cost is bounded to
actual use (and a short tail after it) rather than running all day
regardless of whether anyone's touching the remote. "Activity" isn't just
this module's own calls (send_keyevent/launch_app) — keepalive() lets
screens/active_device_screen.py push the idle timer back out on every
D-pad/Back press too (2026-09-09), even though those are plain IR and
never call into this module otherwise, so navigating the TV by any input
keeps an open connection warm instead of it dropping mid-session.

The library is asyncio-only; the rest of this app is fully synchronous
(hardware.input's blocking read_key() loop). Bridged here with a single
dedicated event loop run forever in a background thread, started lazily on
first use — every function below is a plain blocking call from the
caller's point of view, matching the old adb-based module's contract
(send_keyevent/launch_app, same (success, message) return shape) so
preset_service.py and active_device_screen.py needed no changes at all.
"""

import asyncio
import logging
import os
import threading
import time

from androidtvremote2 import AndroidTVRemote, CannotConnect, ConnectionClosed, InvalidAuth

from services import config_service

log = logging.getLogger(__name__)

# The whole call — connect (if needed), send, and the one reconnect-retry —
# must finish within this, enforced inside _send() on the background loop so
# a timeout cancels the work itself rather than abandoning it mid-flight.
_CALL_TIMEOUT_SECONDS = 5
# Backstop for the calling thread only, in case the loop itself is wedged
# and _send()'s own timeout can never fire.
_CALL_BACKSTOP_SECONDS = _CALL_TIMEOUT_SECONDS + 2
_IDLE_DISCONNECT_SECONDS = 30

_loop: asyncio.AbstractEventLoop | None = None
_remote: AndroidTVRemote | None = None
_connected = False
_idle_handle: asyncio.TimerHandle | None = None


def _ensure_loop() -> asyncio.AbstractEventLoop:
    """The single background event loop, started on first use and kept
    running for the life of the process — everything in this module after
    startup runs as a coroutine submitted to it via _call()."""
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        threading.Thread(target=_loop.run_forever, name="androidtv-loop", daemon=True).start()
    return _loop


def _call(coro):
    """Run *coro* on the background loop and block the calling (sync)
    thread for its result — the bridge every public function in this
    module goes through. Cancels *coro* if the backstop expires, so it
    never keeps running on the loop after its caller has given up."""
    future = asyncio.run_coroutine_threadsafe(coro, _ensure_loop())
    try:
        return future.result(timeout=_CALL_BACKSTOP_SECONDS)
    except TimeoutError:
        future.cancel()
        raise


def _cancel_idle_timer() -> None:
    global _idle_handle
    if _idle_handle is not None:
        _idle_handle.cancel()
        _idle_handle = None


def _arm_idle_timer() -> None:
    """(Re)starts the idle-disconnect countdown from _IDLE_DISCONNECT_SECONDS.
    Must run on the background loop — called both right after connecting
    and by keepalive() below, so any activity pushes the disconnect back
    out rather than just the moment of connecting."""
    global _idle_handle
    _cancel_idle_timer()
    _idle_handle = _ensure_loop().call_later(_IDLE_DISCONNECT_SECONDS, _disconnect_idle)


def _disconnect_idle() -> None:
    """Runs on the background loop via call_later — closes the connection
    after _IDLE_DISCONNECT_SECONDS of no activity, so the TV's keepalive
    ping tax only applies during and briefly after actual use."""
    global _idle_handle
    _idle_handle = None
    _reset_connection()


def _reset_connection() -> None:
    """Closes whatever connection state exists, including a half-open one
    — androidtvremote2 leaves its transport/protocol set when async_connect()
    fails or is cancelled partway, so without this a failed or timed-out
    attempt would leak a socket and the next connect would stack on top."""
    global _connected
    _cancel_idle_timer()
    if _remote is not None:
        _remote.disconnect()
    _connected = False


def keepalive() -> None:
    """Pushes the idle-disconnect timer back out, without opening a new
    connection — call this for every key press on the Google TV screen
    (screens/active_device_screen.py), even ones that don't go through
    this module at all (D-pad/Back are plain IR — see _transmit there),
    so actively driving the remote via any input keeps the connection
    warm for an imminent Home/YouTube press instead of it dropping out
    from under you mid-session. A no-op if not currently connected — this
    never forces a fresh connect on its own, matching the connect-on-
    demand design (see module docstring's battery note).

    Non-blocking: schedules the reset on the background loop and returns
    immediately, so this is cheap enough to call on every repeat tick
    while a D-pad key is held (see _GOOGLE_TV_REPEAT_INTERVAL_SECONDS)
    without adding to that loop's own timing budget."""
    if _connected and _loop is not None:
        _loop.call_soon_threadsafe(_arm_idle_timer)


async def _ensure_connected() -> AndroidTVRemote:
    """Returns an open, ready-to-send AndroidTVRemote, connecting first if
    idle-disconnected or this is the first call. Runs entirely on the
    background loop (called only from coroutines passed to _call())."""
    global _remote, _connected

    if _remote is None:
        ip = config_service.get_androidtv_ip()
        certfile = config_service.get_androidtv_certfile()
        keyfile = config_service.get_androidtv_keyfile()
        if not ip or not certfile or not keyfile:
            raise CannotConnect("androidtv not configured")
        _remote = AndroidTVRemote(client_name="pi-ir", certfile=certfile, keyfile=keyfile, host=ip)
        await _remote.async_generate_cert_if_missing()

    if not _connected:
        started = time.monotonic()
        await _remote.async_connect()
        _connected = True
        log.info("Connected to Google TV at %s in %.1fs", _remote.host, time.monotonic() - started)

    _arm_idle_timer()
    return _remote


async def _send(action) -> None:
    """Connect if needed, run *action* (a sync function taking the
    connected AndroidTVRemote), retrying once through a fresh connect if
    the existing one turned out to be stale — mirrors the old adb module's
    "self-heal a stuck connection, don't just fail" behavior.

    Any failure — including hitting _CALL_TIMEOUT_SECONDS or being
    cancelled by _call()'s backstop — tears the connection down, so nothing
    from a failed call lingers on the loop or carries into the next one."""
    try:
        async with asyncio.timeout(_CALL_TIMEOUT_SECONDS):
            remote = await _ensure_connected()
            try:
                action(remote)
            except ConnectionClosed:
                _reset_connection()
                remote = await _ensure_connected()
                action(remote)
    except BaseException:
        _reset_connection()
        raise


def _describe_chain(exc: BaseException) -> str:
    """*exc* and every exception chained behind it, outermost first —
    androidtvremote2 wraps the OSError that actually says what went wrong
    (errno, e.g. 113 "No route to host" vs 111 "Connection refused") inside
    CannotConnect, so the outer exception alone hides the cause."""
    parts = []
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        part = repr(exc)
        if isinstance(exc, OSError) and exc.errno:
            part += f" [{os.strerror(exc.errno)}]"
        parts.append(part)
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return " <- ".join(parts)


def _run(name: str, action) -> tuple[bool, str]:
    """Runs *action* via _send(), returning (success, LCD-sized message).
    A failure is also logged in full — the whole exception chain on one
    line, how long the call ran, and the traceback (which for a timeout
    shows the await it was stuck in) — since the LCD message alone can't
    tell one failure mode from another."""
    started = time.monotonic()
    try:
        _call(_send(action))
        return True, "Sent"
    except Exception as e:
        log.warning(
            "%s failed after %.1fs: %s",
            name, time.monotonic() - started, _describe_chain(e), exc_info=e,
        )
        if isinstance(e, InvalidAuth):
            return False, "Not paired"
        if isinstance(e, (CannotConnect, ConnectionClosed)):
            return False, "TV unreachable"
        if isinstance(e, TimeoutError):
            return False, "Timeout"
        return False, str(e)[:16]


def send_keyevent(keycode: str) -> tuple[bool, str]:
    """Send an Android keyevent (e.g. "KEYCODE_HOME", "KEYCODE_BACK" — the
    "KEYCODE_" prefix is optional, androidtvremote2 adds it if missing)."""
    return _run(f"send_keyevent({keycode})", lambda remote: remote.send_key_command(keycode))


def launch_app(app_link: str) -> tuple[bool, str]:
    """Launch an app via a deep link (e.g. "https://www.youtube.com/tv").
    A bare package name (e.g. "com.google.android.youtube.tv") technically
    works too — androidtvremote2 sends it as a market://launch?id=<package>
    request — but confirmed 2026-09-09 that this doesn't reliably switch
    away from whatever app currently has foreground focus (seen with Plex
    open: reported success, did nothing), while an explicit deep link
    does. Use a real deep link; see config/keypad_presets.json's
    androidtv_app steps and preset_service.py's docstring."""
    return _run(f"launch_app({app_link})", lambda remote: remote.send_launch_app_command(app_link))
