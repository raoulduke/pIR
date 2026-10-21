"""Keypad presets and the shared step runner.

config/keypad_presets.json's "by_device" maps "device" or "device/location"
(whatever is active when Preset Function N is pressed) to that device's
slots "1".."4", each {"name": ..., "steps": [...]}. There are no global
defaults: a slot the device doesn't define is "Not configured".

  "by_device": {"light/hall": {"1": {"name": "On", "steps": [...]}}}

Steps ("delay_after" is optional, default PRESET_DEFAULT_DELAY_SECONDS):

  IR signal       {"home": "home", "device": "tv", "location": "bedroom",
                   "button": "power"}                  (location optional)
  Voice (HA)      {"type": "voice", "command": "Rest"}
  Google TV key   {"type": "androidtv", "keyevent": "KEYCODE_HOME"}
  Google TV app   {"type": "androidtv_app", "app_link": "https://www.youtube.com/tv",
                   "package": "com.google.android.youtube.tv"}
                  (a deep link: a bare package name may not leave the
                  current app; "package" is optional, and skips the
                  launch when that app is already in front, sending
                  "if_open_keyevent" instead if given)
  Hue             {"type": "hue", "action": "toggle", "light_id": "3"}
                  "action" is on/off/toggle; "bri_inc"/"ct_inc" (relative)
                  or "bri"/"ct" (absolute) take priority over it.
  IR on/off pair  {"type": "ir_toggle", "home": "home", "device": "light",
                   "location": "hall", "on_button": "on", "off_button": "off"}
  Voice on/off    {"type": "voice_toggle", "key": "lamp",
                   "on_command": "Turn on lamp", "off_command": "Turn off lamp"}

The two toggles track an assumed on/off state in memory, for devices with no
state readback (a dumb IR light; Google Assistant has no reliable "toggle"
phrase). It starts "off" at every restart and drifts if the device is
changed another way. "key" only needs to be unique among voice_toggle steps.

run_steps() is also used by step devices (services/step_device_service.py).
run_steps_background() queues steps on one worker thread, so a voice or Hue
call (up to 15s) never blocks the keypad, and two quick presses run in order
instead of racing through the toggle state. Its on_result(sent, total,
failed_step) runs on that thread when the steps finish.
"""

import json
import logging
import queue
import threading
import time
from pathlib import Path

from app.files import read_json_cached
from app.constants import KEYPAD_PRESETS_FILE, PRESET_DEFAULT_DELAY_SECONDS
from app.exceptions import HardwareError
from hardware import ir_transmitter
from services import signal_service, voice_service, androidtv_service, hue_service

log = logging.getLogger(__name__)

_PATH = Path(__file__).parent.parent / KEYPAD_PRESETS_FILE

# Assumed on/off state for ir_toggle steps, keyed by (home, device,
# location), and voice_toggle steps, keyed by "key" (see module docstring).
_ir_toggle_state: dict[tuple[str, str, str], bool] = {}
_voice_toggle_state: dict[str, bool] = {}


def _load() -> dict:
    return read_json_cached(_PATH, {})


def _device_key(device: str, location: str) -> str:
    return f"{device}/{location}" if location else device


def get_preset(number: int, device: str = "", location: str = "") -> tuple[str, list[dict]]:
    """(name, steps) of the device's preset *number*; ("", []) if it has
    none."""
    entry = _load().get("by_device", {}).get(_device_key(device, location), {}).get(str(number), {}) if device else {}
    return entry.get("name", ""), entry.get("steps", [])


def describe_step(step: dict) -> str:
    """A short label for a step, for the LCD ("Failed: <label>")."""
    step_type = step.get("type")
    if step_type == "voice":
        return step.get("command", "Voice")
    if step_type == "androidtv":
        return step.get("keyevent", "").removeprefix("KEYCODE_").replace("_", " ").title()
    if step_type == "androidtv_app":
        return "App"
    if step_type == "hue":
        return "Light"
    if step_type == "ir_toggle":
        return step.get("device", "IR").replace("_", " ").title()
    if step_type == "voice_toggle":
        return step.get("key", "Voice").replace("_", " ").title()
    return step.get("button", "Signal").replace("_", " ").title()


def hue_light_ids(steps: list[dict]) -> list[str]:
    return [step["light_id"] for step in steps if step.get("type") == "hue" and "light_id" in step]


def all_unreachable(steps: list[dict]) -> bool:
    """True if every step is a Hue step on a light last found unreachable
    (off at the wall), so none of them could do anything."""
    return bool(steps) and all(
        step.get("type") == "hue" and hue_service.is_known_unreachable(step.get("light_id", ""))
        for step in steps
    )


def _send_ir(step: dict, button: str) -> str:
    """Transmit *button* of *step*'s home/device/location. Returns "" on
    success, else what went wrong."""
    path = signal_service.signal_path(step["home"], step["device"], button, step.get("location", ""))
    if not path.exists():
        return f"signal missing: {path}"
    try:
        ir_transmitter.transmit(path)
        return ""
    except HardwareError as e:
        return str(e)


def run_steps(steps: list[dict]) -> tuple[int, int, dict | None]:
    """Run *steps* in order. Returns (steps sent, steps configured, first
    step that failed or None). A failing or malformed step doesn't stop the
    rest."""
    sent = 0
    failed_step = None

    for i, step in enumerate(steps):
        sent_before = sent
        error = ""
        # A malformed step (e.g. a missing "command") fails on its own and
        # is reported like any other failure, rather than aborting the run.
        try:
            if step.get("type") == "voice":
                success, error = voice_service.send_command(step["command"])
                if success:
                    sent += 1
            elif step.get("type") == "androidtv":
                success, error = androidtv_service.send_keyevent(step["keyevent"])
                if success:
                    sent += 1
            elif step.get("type") == "androidtv_app":
                success, error = androidtv_service.launch_app(
                    step["app_link"], step.get("package"), step.get("if_open_keyevent"))
                if success:
                    sent += 1
            elif step.get("type") == "hue" and hue_service.is_known_unreachable(step.get("light_id", "")):
                error = f"light {step.get('light_id')} unreachable"
            elif step.get("type") == "hue":
                try:
                    light_id = step["light_id"]
                    # bri_inc/ct_inc are relative (the bridge clamps); bri/ct absolute.
                    # "on" goes with them: Hue ignores a brightness/colour change to an
                    # off light unless the same request turns it on.
                    light_state = {k: step[k] for k in ("bri_inc", "ct_inc", "bri", "ct") if k in step}
                    if light_state:
                        light_state["on"] = True
                        hue_service.set_light_state(light_id, **light_state)
                    else:
                        action = step.get("action", "toggle")
                        if action == "on":
                            hue_service.turn_on(light_id)
                        elif action == "off":
                            hue_service.turn_off(light_id)
                        else:
                            current = hue_service.get_light(light_id).get("state", {}).get("on", False)
                            hue_service.set_light_state(light_id, on=not current)
                    sent += 1
                except (OSError, ValueError, KeyError) as e:
                    error = repr(e)
            elif step.get("type") == "ir_toggle":
                key = (step["home"], step["device"], step.get("location", ""))
                now_on = not _ir_toggle_state.get(key, False)
                button = step["on_button"] if now_on else step["off_button"]
                error = _send_ir(step, button)
                if not error:
                    _ir_toggle_state[key] = now_on  # only once it actually went out
                    sent += 1
            elif step.get("type") == "voice_toggle":
                key = step["key"]
                now_on = not _voice_toggle_state.get(key, False)
                command = step["on_command"] if now_on else step["off_command"]
                success, error = voice_service.send_command(command)
                if success:
                    _voice_toggle_state[key] = now_on  # only once it actually went out
                    sent += 1
            else:
                error = _send_ir(step, step["button"])
                if not error:
                    sent += 1

        except Exception as e:
            error = f"bad step: {e!r}"

        if sent == sent_before:
            log.warning("Step failed (%s): %s", describe_step(step), error or "unknown error")
            if failed_step is None:
                failed_step = step

        if i < len(steps) - 1:
            time.sleep(step.get("delay_after", PRESET_DEFAULT_DELAY_SECONDS))

    return sent, len(steps), failed_step


# (steps, on_result, queued at) waiting to run — see run_steps_background()
# below. Steps that couldn't start within _STALE_SECONDS of being queued are
# skipped: a press that long ago (stuck behind voice commands timing out at
# up to 15s each) is no longer what anyone wants to happen now.
_STALE_SECONDS = 10
_task_queue: "queue.Queue[tuple[list[dict], object, float]]" = queue.Queue()
_worker_started = False
_worker_start_lock = threading.Lock()


def _worker_loop() -> None:
    while True:
        steps, on_result, queued_at = _task_queue.get()
        waited = time.monotonic() - queued_at
        if waited > _STALE_SECONDS:
            log.warning("Skipped steps queued %.1fs ago: %r", waited, steps)
            result = (0, len(steps), steps[0] if steps else None)
        else:
            # Uncaught, this would end the worker and strand every later task.
            try:
                result = run_steps(steps)
            except Exception:
                log.exception("Background steps crashed: %r", steps)
                result = (0, len(steps), steps[0] if steps else None)
        if on_result:
            try:
                on_result(*result)
            except Exception:
                log.exception("Step result callback failed")


def _ensure_worker() -> None:
    global _worker_started
    with _worker_start_lock:
        if not _worker_started:
            threading.Thread(target=_worker_loop, name="preset-steps", daemon=True).start()
            _worker_started = True


def run_steps_background(steps: list[dict], on_result=None) -> None:
    """Queue *steps* for the worker thread and return at once. on_result, if
    given, gets run_steps()'s result on that thread when they finish (or
    are skipped as stale, reported as failed)."""
    _ensure_worker()
    _task_queue.put((steps, on_result, time.monotonic()))


