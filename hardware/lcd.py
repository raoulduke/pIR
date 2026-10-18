"""LCD hardware abstraction.

Wraps the 1602 IIC 16×2 HD44780 display (PCF8574 I²C backpack) over
software I²C.  All other layers call only ``lcd.write(line1, line2)``
and ``lcd.clear()``.
"""

import threading

from RPLCD.i2c import CharLCD

from app.constants import BACKLIGHT_IDLE_SECONDS

# Custom CGRAM characters for a sub-cell-resolution progress bar: each 5px-
# wide cell gets 1-5 columns filled from the left (slots 1-5) — 5 fill
# levels per cell, 80 discrete positions across 16 cells. The fully-filled
# cell (slot 5) is defined here too rather than reusing the ROM's built-in
# solid block (0xFF) — that glyph isn't at the same code point on every
# HD44780 clone (the "A00" vs "A02" character ROM tables disagree past
# 0x7F), so relying on it looked like a single block moving across an
# otherwise-blank row instead of a bar filling up: only the custom-defined
# leading edge was guaranteed to render the same way on this hardware.
_BAR_PARTIAL_BITS = {
    1: 0b10000,
    2: 0b11000,
    3: 0b11100,
    4: 0b11110,
    5: 0b11111,
}
_BAR_FULL_CELL = chr(5)

# Preset-line glyphs (screens/active_device_screen.py's
# _PRESET_ABBREVIATIONS). The progress bar only runs during startup, so
# once it's done load_preset_glyphs() reuses its CGRAM slots 1-5 for these
# — never loaded alongside the bar, so no slot conflict.
GLYPH_HOME = chr(1)
GLYPH_CC_LEFT = chr(2)
GLYPH_CC_RIGHT = chr(3)
GLYPH_BACK_ARROW = chr(4)
GLYPH_BACK_SHAFT = chr(5)

# Down arrow for list screens (screens/widgets/format.py's with_arrows()):
# the ROM's "^" (A00 0x5E: rows 0-2) mirrored top to bottom within the
# glyph's 7 rows, since the ROM has no down arrow. Slot 0 — the one slot
# neither the progress bar nor the preset glyphs use — so it's loaded once
# at startup and never replaced.
GLYPH_ARROW_DOWN = chr(0)
_ARROW_DOWN_ROWS = [
    0b00000,
    0b00000,
    0b00000,
    0b00000,
    0b10001,
    0b01010,
    0b00100,
    0b00000,
]
_PRESET_GLYPHS = {
    # Solid house with a door: a 1px gap 2px up the bottom center.
    1: [
        0b00000,
        0b00100,
        0b01110,
        0b11111,
        0b11111,
        0b11011,
        0b11011,
        0b00000,
    ],
    # Closed captions: a box split across two cells, a small "c" in each
    # (single-pixel ends).
    2: [
        0b11111,
        0b10000,
        0b10010,
        0b10100,
        0b10100,
        0b10010,
        0b10000,
        0b11111,
    ],
    3: [
        0b11111,
        0b00001,
        0b00101,
        0b01001,
        0b01001,
        0b00101,
        0b00001,
        0b11111,
    ],
    # Back arrow, split across two cells: a "<" head whose tip runs right
    # to the cell edge on row 3, then the shaft continuing on row 3 in the
    # next cell, stopping two pixels short of its right edge.
    4: [
        0b00000,
        0b00100,
        0b01000,
        0b11111,
        0b01000,
        0b00100,
        0b00000,
        0b00000,
    ],
    5: [
        0b00000,
        0b00000,
        0b00000,
        0b11100,
        0b00000,
        0b00000,
        0b00000,
        0b00000,
    ],
}


class LCD:
    def __init__(self) -> None:
        # Guards every write below against interleaving with a background
        # thread's write (services/preset_service.py's run_steps_background
        # reports a failed voice/hue step from its own worker thread, after
        # the main input loop has already moved on) — without this, two
        # threads racing to write cursor_pos + write_string in an
        # interleaved order could corrupt what's on screen, not just which
        # message wins.
        self._lock = threading.Lock()
        self._lcd = CharLCD(
            i2c_expander="PCF8574",
            address=0x27,
            port=3,
            cols=16,
            rows=2,
            charmap="A00",
            auto_linebreaks=False,
        )
        for location, bits in _BAR_PARTIAL_BITS.items():
            self._lcd.create_char(location, [bits] * 8)
        self._lcd.create_char(0, _ARROW_DOWN_ROWS)
        # Status character in line 1's last column (write_with_status()).
        # _status_shown is True only while the screen that drew it is
        # still up: every other write that touches line 1 clears it, so
        # set_status_char() from a background thread never draws over
        # another screen.
        self._status_char = " "
        self._status_shown = False
        # Backlight idle-off. The backpack's PCF8574 P3 switches the
        # backlight (confirmed by eye 2026-10-18), which RPLCD drives as
        # backlight_enabled and re-sends with every write, so writes made
        # while it's off don't turn it back on. _idle_gen invalidates a
        # timer that already fired but is still waiting on _lock when
        # wake() re-arms.
        self._idle_timer: threading.Timer | None = None
        self._idle_gen = 0
        self._backlight_listener = None
        self._idle_seconds: float | None = BACKLIGHT_IDLE_SECONDS
        self.clear()
        self.wake()

    def set_backlight_listener(self, listener) -> None:
        """Register *listener(lit)*, called when the idle timer turns the
        backlight off (False) and when wake() turns it back on (True) —
        main.py wires it to services/androidtv_service.py's
        set_screen_lit(). Called outside the lock, from the timer's thread
        or wake()'s caller, so it must be quick and must not raise."""
        self._backlight_listener = listener

    def set_idle_seconds(self, seconds: float | None) -> None:
        """Change the backlight timeout (None = never off) and restart the
        countdown with it. main.py applies config/settings.json's value at
        startup; screens/settings_screen.py on save."""
        with self._lock:
            self._idle_seconds = seconds
        self.wake()

    def wake(self) -> bool:
        """Backlight on and restart the idle countdown (set_idle_seconds()).
        Returns True if it was off — hardware/input.py's wake handler
        swallows that key, so a press in the dark only lights the screen.
        Also called by screens that change without key presses and need
        to stay readable (the running Sweep)."""
        with self._lock:
            was_off = not self._lcd.backlight_enabled
            if was_off:
                self._lcd.backlight_enabled = True
            self._idle_gen += 1
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            if self._idle_seconds is not None:
                self._idle_timer = threading.Timer(
                    self._idle_seconds, self._idle_off, args=(self._idle_gen,),
                )
                self._idle_timer.daemon = True
                self._idle_timer.start()
        if was_off and self._backlight_listener is not None:
            self._backlight_listener(True)
        return was_off

    def _idle_off(self, gen: int) -> None:
        with self._lock:
            if gen != self._idle_gen:
                return
            self._lcd.backlight_enabled = False
        if self._backlight_listener is not None:
            self._backlight_listener(False)

    def stop_idle_timer(self) -> None:
        """Backlight on for good — on exit, so "Program stopped" stays
        readable after the app (and its timer) is gone."""
        with self._lock:
            self._idle_gen += 1
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            self._lcd.backlight_enabled = True

    def clear(self) -> None:
        with self._lock:
            self._status_shown = False
            self._lcd.clear()

    def write(self, line1: str = "", line2: str = "") -> None:
        with self._lock:
            self._status_shown = False
            self._lcd.clear()
            self._lcd.cursor_pos = (0, 0)
            self._lcd.write_string(line1[:16].ljust(16))
            self._lcd.cursor_pos = (1, 0)
            self._lcd.write_string(line2[:16].ljust(16))

    def write_line1(self, text: str = "") -> None:
        """Overwrite just the top row — no clear() (that would blank both
        rows), so line 2 survives untouched. For a status update that
        shouldn't interrupt something already showing on line 2, e.g.
        screens/network_screen.py's background rescan not disturbing the
        network currently highlighted."""
        with self._lock:
            self._status_shown = False
            self._lcd.cursor_pos = (0, 0)
            self._lcd.write_string(text[:16].ljust(16))

    def write_line2(self, text: str = "") -> None:
        """Overwrite just the bottom row — no clear() (that would blank
        both rows), so line 1 survives untouched. Mirrors write_line1();
        see there. Used by screens/active_device_screen.py's Preset
        Function name reveal after a device switch, so the device label
        already on line 1 doesn't need rewriting each frame."""
        with self._lock:
            self._lcd.cursor_pos = (1, 0)
            self._lcd.write_string(text[:16].ljust(16))

    def write_with_status(self, line1: str = "", line2: str = "") -> None:
        """write(), with *line1* cut to 15 columns and the current status
        character (set_status_char()) in the 16th, kept up to date there
        until something else redraws line 1. Used by the Google TV screen
        (screens/active_device_screen.py) for its connection status."""
        with self._lock:
            self._lcd.clear()
            self._lcd.cursor_pos = (0, 0)
            self._lcd.write_string(line1[:15].ljust(15) + self._status_char)
            self._lcd.cursor_pos = (1, 0)
            self._lcd.write_string(line2[:16].ljust(16))
            self._status_shown = True

    def set_status_char(self, char: str) -> None:
        """Change the status character, redrawing it in place only if
        write_with_status()'s screen is still up. Wired to services/
        androidtv_service.py's set_status_listener() in main.py."""
        with self._lock:
            self._status_char = char
            if self._status_shown:
                self._lcd.cursor_pos = (0, 15)
                self._lcd.write_string(char)

    def load_preset_glyphs(self) -> None:
        """Replace the progress bar's CGRAM slots with the preset-line
        glyphs. Call once startup (the only user of write_progress) is
        done."""
        with self._lock:
            for location, rows in _PRESET_GLYPHS.items():
                self._lcd.create_char(location, rows)

    def write_progress(self, line1: str, fraction: float) -> None:
        """Top row is *line1*; bottom row is a fill bar for *fraction*
        (0.0-1.0), left to right, at 5 sub-positions per cell.

        Skips clear() (unlike write()) so repeated calls don't flicker —
        every cell in both rows is always overwritten with padding, so no
        stale characters can survive between frames.
        """
        fraction = max(0.0, min(1.0, fraction))
        units = round(fraction * 16 * 5)
        full_cells, partial = divmod(units, 5)
        bar = _BAR_FULL_CELL * full_cells
        if partial:
            bar += chr(partial)
        bar = bar.ljust(16)

        with self._lock:
            self._status_shown = False
            self._lcd.cursor_pos = (0, 0)
            self._lcd.write_string(line1[:16].ljust(16))
            self._lcd.cursor_pos = (1, 0)
            self._lcd.write_string(bar)


# Module-level singleton — imported by screens and startup alike.
lcd = LCD()
