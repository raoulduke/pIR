"""Google TV playback state (playing/paused) over Google Cast, which the
Android TV Remote protocol (androidtv_service.py) doesn't report. Read only:
nothing here controls the TV.

Connected only while androidtv_service holds its connection (the Google TV
screen, backlight on; set_held() is wired to its hold listener in main.py):
the Cast connection pings the TV every few seconds like the remote one, so
it isn't kept open all day. pychromecast is imported on first use, in the
background: it's a slow import.
"""

import logging
import threading

from services import config_service

log = logging.getLogger(__name__)

_CAST_PORT = 8009

_lock = threading.Lock()
_cast = None
_held = False
_player_state: str | None = None  # "PLAYING", "PAUSED", "BUFFERING", "IDLE" or None
_listener = None


def set_state_listener(listener) -> None:
    """Register *listener(player_state)*, called with player_state() on every
    change, from pychromecast's thread or set_held()'s: quick, no raising."""
    global _listener
    _listener = listener


def player_state() -> str | None:
    """The Cast media player's state, None when not connected or nothing
    reported yet."""
    return _player_state


def _set_state(state: str | None) -> None:
    global _player_state
    if state == _player_state:
        return
    _player_state = state
    if _listener is not None:
        _listener(state)


def set_held(held: bool) -> None:
    """Connect while *held*, disconnect otherwise. Non-blocking."""
    global _held
    with _lock:
        if held == _held:
            return
        _held = held
    threading.Thread(target=_connect if held else _disconnect, name="cast", daemon=True).start()


class _MediaListener:
    def new_media_status(self, status) -> None:
        _set_state(status.player_state)

    def load_media_failed(self, queue_item_id, error_code) -> None:
        pass


def _connect() -> None:
    global _cast
    ip = config_service.get_androidtv_ip()
    if not ip:
        return
    try:
        import pychromecast
        cast = pychromecast.get_chromecast_from_host((ip, _CAST_PORT, None, None, None))
        cast.media_controller.register_status_listener(_MediaListener())
        with _lock:
            if not _held or _cast is not None:
                return
            _cast = cast
        # Starts pychromecast's socket thread, which reconnects by itself.
        cast.wait(timeout=10)
        log.info("Cast connected to %s", ip)
        # Asks for the current state (a read); changes are pushed after.
        try:
            cast.media_controller.update_status()
        except Exception as e:
            log.info("Cast media status not available yet: %r", e)
    except Exception as e:
        log.warning("Cast connect to %s failed: %r", ip, e)


def _disconnect() -> None:
    global _cast
    with _lock:
        if _held:
            return
        cast, _cast = _cast, None
    _set_state(None)
    if cast is not None:
        try:
            cast.disconnect(timeout=2)
        except Exception as e:
            log.warning("Cast disconnect failed: %r", e)
