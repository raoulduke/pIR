"""Google TV control over the Android TV Remote protocol v2
(androidtvremote2), for keys IR can't send (Home, a real Back, app launch).
Pairing is a one-time certificate exchange (scripts/pair_androidtv.py).

Connections cost battery (the TV pings every ~5s), so:
- Elsewhere, one opens on first use and closes after
  _IDLE_DISCONNECT_SECONDS without activity; keepalive() lets any key on the
  Google TV screen count as activity.
- On the Google TV screen with the backlight on, it's held open with no
  idle timeout and reconnected if dropped, so a Home/YouTube press never
  waits for a connect (set_on_tv_screen()/set_screen_lit()).

The library is asyncio-only and the app is synchronous: everything runs on
one background event loop, and each public function is a plain blocking
call returning (success, message).
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
# The TV had reported its foreground app within 1.5s of a connect (one probe).
_APP_REPORT_WAIT_SECONDS = 3
# Gaps between reconnect attempts while held, the last one repeating —
# bounded anyway, since the hold ends with the backlight timeout.
_HOLD_RETRY_SECONDS = (0, 5, 10, 30, 60)

_loop: asyncio.AbstractEventLoop | None = None
_remote: AndroidTVRemote | None = None
_connected = False
_idle_handle: asyncio.TimerHandle | None = None
_status = "closed"  # "closed" | "connecting" | "open" — see set_status_listener()
_status_listener = None
# One connect at a time (the hold's and a send's).
_connect_lock = asyncio.Lock()
# Held open while both are true. Only touched on the loop.
_on_tv_screen = False
_screen_lit = True
_hold_task: asyncio.Task | None = None
# True once the TV has reported its foreground app on this connection and
# nothing since may have changed it unreported (our own Home press), so
# remote.current_app can be trusted (launch_app()'s skip).
_app_known = False
# Set whenever _app_known is (wait_for_app()).
_app_event = threading.Event()


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
    """The background event loop, started on first use."""
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        threading.Thread(target=_loop.run_forever, name="androidtv-loop", daemon=True).start()
    return _loop


def _call(coro):
    """Run *coro* on the background loop and wait for its result; cancel it
    if the backstop expires."""
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
    """Restart the idle-disconnect countdown (none while held). On the loop."""
    global _idle_handle
    _cancel_idle_timer()
    if _held():
        return  # no idle timeout while held
    _idle_handle = _ensure_loop().call_later(_IDLE_DISCONNECT_SECONDS, _disconnect_idle)


def _disconnect_idle() -> None:
    """Idle timeout reached: close the connection."""
    global _idle_handle
    _idle_handle = None
    _reset_connection()


def _reset_connection() -> None:
    """Close the connection, including a half-open one: androidtvremote2
    keeps its transport after a failed or cancelled connect, which would
    otherwise leak a socket."""
    global _connected
    _cancel_idle_timer()
    if _remote is not None:
        _remote.disconnect()
    _connected = False
    _app_event.clear()
    _set_status("closed")


def keepalive() -> None:
    """Push the idle-disconnect timer back out without connecting — for any
    key on the Google TV screen, IR ones included. Non-blocking; a no-op
    when not connected."""
    if _connected and _loop is not None:
        _loop.call_soon_threadsafe(_arm_idle_timer)


def set_on_tv_screen(on: bool) -> None:
    """Whether Google TV is Active Device Control's device (called on every
    pass of its loop). Non-blocking."""
    _ensure_loop().call_soon_threadsafe(_update_hold, on, None)


def set_screen_lit(lit: bool) -> None:
    """Whether the backlight is on. Off also closes an on-demand connection
    from another screen. Non-blocking."""
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
    """Connect, retrying at _HOLD_RETRY_SECONDS gaps, while held and not
    connected. Connecting sends no key, so playback isn't touched."""
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
    """Notice the TV dropping the connection (standby, Wi-Fi, missed pings)
    and reconnect if held. Uses androidtvremote2 0.3.2's private
    _remote_message_protocol, the only way it exposes the drop; a mismatch
    means we closed or replaced it ourselves."""
    await protocol.on_con_lost
    if _remote is None or _remote._remote_message_protocol is not protocol:
        return
    log.info("Google TV connection lost")
    _reset_connection()
    if _held():
        _start_hold_connect()


async def _ensure_connected() -> AndroidTVRemote:
    """An open AndroidTVRemote, connecting first if needed. On the loop."""
    global _remote, _connected, _app_known

    async with _connect_lock:
        if _remote is None:
            ip = config_service.get_androidtv_ip()
            certfile = config_service.get_androidtv_certfile()
            keyfile = config_service.get_androidtv_keyfile()
            if not ip or not certfile or not keyfile:
                raise CannotConnect("androidtv not configured")
            _remote = AndroidTVRemote(client_name="pi-ir", certfile=certfile, keyfile=keyfile, host=ip)
            _remote.add_current_app_updated_callback(_on_app_updated)
            await _remote.async_generate_cert_if_missing()

        if not _connected:
            _app_known = False  # the TV reports it again right after connecting
            _app_event.clear()
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


def current_app() -> str | None:
    """The TV's foreground app package (e.g. "com.google.android.youtube.tv"),
    or None if not connected or not known yet. Never connects; any thread."""
    remote = _remote
    if not _connected or not _app_known or remote is None:
        return None
    return remote.current_app or None


def wait_for_app(timeout: float) -> str | None:
    """current_app(), waiting up to *timeout* seconds for it to become known
    (a connect in progress, e.g. the held one after the screen wakes).
    Never connects itself. Blocks: not for the main loop."""
    _app_event.wait(timeout)
    return current_app()


def _on_app_updated(app: str) -> None:
    global _app_known
    _app_known = True
    _app_event.set()


async def _send(action) -> None:
    """Connect if needed and run *action(remote)*, retrying once on a fresh
    connection if the open one turns out stale. Any failure (timeout or
    cancellation included) tears the connection down."""
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
    """*exc* and every exception chained behind it, outermost first: the
    real cause (an OSError's errno) is wrapped inside CannotConnect."""
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
    A failure is also logged — the whole exception chain on one line and
    how long the call ran — since the LCD message alone can't tell one
    failure mode from another. The traceback is added only for an
    unexpected exception: the expected ones (TV off or unreachable, a
    dropped connection, a timeout, not paired) are fully described by the
    chain, and a traceback each time (~40 lines) flooded the journal
    whenever the TV was off."""
    started = time.monotonic()
    try:
        _call(_send(action))
        return True, "Sent"
    except Exception as e:
        expected = isinstance(e, (InvalidAuth, CannotConnect, ConnectionClosed, TimeoutError))
        log.warning(
            "%s failed after %.1fs: %s",
            name, time.monotonic() - started, _describe_chain(e),
            exc_info=None if expected else e,
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
    def action(remote):
        global _app_known
        remote.send_key_command(keycode)
        if keycode.removeprefix("KEYCODE_") == "HOME":
            # Not sure the TV reports the launcher, so don't trust the old app.
            _app_known = False
            _app_event.clear()
    return _run(f"send_keyevent({keycode})", action)


def launch_app(app_link: str, package: str | None = None,
               if_open_keyevent: str | None = None) -> tuple[bool, str]:
    """Launch an app by deep link (e.g. "https://www.youtube.com/tv"). A
    bare package name reports success but may not leave the current app.
    With *package* (e.g. "com.google.android.youtube.tv"), nothing is sent
    if the TV reports that app already in front, so pressing it again
    doesn't interrupt what's playing; *if_open_keyevent* (e.g.
    "KEYCODE_MEDIA_PLAY_PAUSE") is sent instead, if given. On a fresh
    connection it first waits up to _APP_REPORT_WAIT_SECONDS for the TV to
    report its app; still unknown, it launches."""
    if package and current_app() is None:
        ok, message = _run("connect", lambda remote: None)
        if not ok:
            return ok, message
        wait_for_app(_APP_REPORT_WAIT_SECONDS)

    def action(remote):
        if package and _app_known and remote.current_app == package:
            log.info("launch_app(%s): %s already open, not relaunching%s", app_link, package,
                     f"; sending {if_open_keyevent}" if if_open_keyevent else "")
            if if_open_keyevent:
                remote.send_key_command(if_open_keyevent)
            return
        remote.send_launch_app_command(app_link)
    return _run(f"launch_app({app_link})", action)
