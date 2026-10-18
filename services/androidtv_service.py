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

Held open on the Google TV screen (2026-10-18): while that screen is the
active device *and* the LCD backlight is on, the connection is opened
right away and kept open with no idle timeout, reconnecting with backoff
if the TV drops it — so a Home/YouTube press there never waits for a
connect. Switching away or the backlight turning off closes it at once
(set_on_tv_screen()/set_screen_lit()); the battery cost is bounded to
time spent actually looking at that screen.

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
# Gaps between reconnect attempts while held, the last one repeating —
# bounded anyway, since the hold ends with the backlight timeout.
_HOLD_RETRY_SECONDS = (0, 5, 10, 30, 60)

_loop: asyncio.AbstractEventLoop | None = None
_remote: AndroidTVRemote | None = None
_connected = False
_idle_handle: asyncio.TimerHandle | None = None
_status = "closed"  # "closed" | "connecting" | "open" — see set_status_listener()
_status_listener = None
# Serializes connecting, so the hold's own connect and a real send
# arriving mid-connect don't both call async_connect() on the same
# AndroidTVRemote.
_connect_lock = asyncio.Lock()
# Held open while both are true — see the module docstring. Only touched
# on the background loop.
_on_tv_screen = False
_screen_lit = True
_hold_task: asyncio.Task | None = None


def set_status_listener(listener) -> None:
    """Register *listener(status)*, called with "connecting", "open" or
    "closed" whenever the connection changes — main.py wires it to
    hardware/lcd.py's set_status_char(), the Google TV screen's status
    character. Called on the background loop's thread, so it must be quick and
    must not raise."""
    global _status_listener
    _status_listener = listener


def _set_status(status: str) -> None:
    global _status
    if status == _status:
        return
    _status = status
    if _status_listener is not None:
        try:
            _status_listener(status)
        except Exception:
            log.exception("Status listener failed (%s)", status)


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
    if _held():
        return  # no idle timeout while held
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
    _set_status("closed")


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


def set_on_tv_screen(on: bool) -> None:
    """Whether the Google TV screen is the active device — screens/
    active_device_screen.py calls this on every pass of its loop.
    Non-blocking."""
    _ensure_loop().call_soon_threadsafe(_update_hold, on, None)


def set_screen_lit(lit: bool) -> None:
    """Whether the LCD backlight is on — wired to hardware/lcd.py's
    set_backlight_listener() in main.py. Off also closes a connection
    opened on demand from another screen. Non-blocking."""
    _ensure_loop().call_soon_threadsafe(_update_hold, None, lit)


def _held() -> bool:
    return _on_tv_screen and _screen_lit


def _update_hold(on_tv_screen: bool | None, screen_lit: bool | None) -> None:
    global _on_tv_screen, _screen_lit, _hold_task
    was_held = _held()
    if on_tv_screen is not None:
        _on_tv_screen = on_tv_screen
    if screen_lit is not None:
        _screen_lit = screen_lit
    if _held() == was_held:
        if screen_lit is False:
            _reset_connection()
        return
    if _held():
        _cancel_idle_timer()
        _start_hold_connect()
    else:
        if _hold_task is not None:
            _hold_task.cancel()
            _hold_task = None
        _reset_connection()


def _start_hold_connect() -> None:
    global _hold_task
    if _hold_task is None or _hold_task.done():
        _hold_task = _ensure_loop().create_task(_hold_connect())


async def _hold_connect() -> None:
    """Connect, retrying at _HOLD_RETRY_SECONDS gaps, for as long as the
    hold lasts and the connection isn't open. Connecting is just the TLS
    handshake and the protocol's own configuration exchange — no key
    press — so it doesn't touch what's playing."""
    attempt = 0
    while _held() and not _connected:
        delay = _HOLD_RETRY_SECONDS[min(attempt, len(_HOLD_RETRY_SECONDS) - 1)]
        if delay:
            await asyncio.sleep(delay)
            if not _held() or _connected:
                return
        attempt += 1
        try:
            async with asyncio.timeout(_CALL_TIMEOUT_SECONDS):
                await _ensure_connected()
        except Exception as e:
            _reset_connection()
            log.warning("Hold connect failed (attempt %d): %s", attempt, _describe_chain(e))


async def _watch_connection(protocol) -> None:
    """Notices the TV dropping the connection (standby, Wi-Fi, or the
    library closing it after missed pings) instead of leaving "open" up
    until the next send fails — and reconnects if held. androidtvremote2
    only exposes the drop through its protocol's on_con_lost future, so
    this reads its private _remote_message_protocol (androidtvremote2
    0.3.2): a mismatch means we closed or replaced it ourselves."""
    await protocol.on_con_lost
    if _remote is None or _remote._remote_message_protocol is not protocol:
        return
    log.info("Google TV connection lost")
    _reset_connection()
    if _held():
        _start_hold_connect()


async def _ensure_connected() -> AndroidTVRemote:
    """Returns an open, ready-to-send AndroidTVRemote, connecting first if
    idle-disconnected or this is the first call. Runs entirely on the
    background loop (called only from coroutines passed to _call())."""
    global _remote, _connected

    async with _connect_lock:
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
            _set_status("connecting")
            await _remote.async_connect()
            _connected = True
            _set_status("open")
            log.info("Connected to Google TV at %s in %.1fs", _remote.host, time.monotonic() - started)
            asyncio.get_running_loop().create_task(
                _watch_connection(_remote._remote_message_protocol)
            )

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
