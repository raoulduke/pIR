"""The 16x2 HD44780 LCD (PCF8574 I2C backpack, bit-banged I2C on bus 3).
Everything else draws through the shared `lcd` below.
"""

import threading

from app.constants import BACKLIGHT_IDLE_SECONDS

# Startup progress bar: CGRAM slots 1-5 fill 1-5 of a cell's 5 pixel
# columns, for 80 steps across 16 cells. The full cell is custom too: the
# ROM's solid block isn't at the same code point on every HD44780 clone.
_BAR_PARTIAL_BITS = {
    1: 0b10000,
    2: 0b11000,
    3: 0b11100,
    4: 0b11110,
    5: 0b11111,
}
_BAR_FULL_CELL = chr(5)

# Preset-line glyphs. They reuse the progress bar's slots 1-5 once startup
# is done (load_preset_glyphs()).
GLYPH_HOME = chr(1)
GLYPH_CC_LEFT = chr(2)
GLYPH_CC_RIGHT = chr(3)
GLYPH_BACK_ARROW = chr(4)
GLYPH_BACK_SHAFT = chr(5)

# List scroll indicator (format.with_arrows()): solid up and down triangles
# in one cell. Slot 0, loaded once at startup.
GLYPH_ARROW_UPDOWN = chr(0)
_ARROW_UPDOWN_ROWS = [
    0b00100,
    0b01110,
    0b11111,
    0b00000,
    0b00000,
    0b11111,
    0b01110,
    0b00100,
]
# Ellipsis for a name cut short (format.fit_with_suffix()): three dots in one
# cell; the ROM has none. Slot 6, loaded once at startup.
GLYPH_ELLIPSIS = chr(6)
_ELLIPSIS_ROWS = [0, 0, 0, 0, 0, 0, 0b10101, 0]
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
        # Background threads write too (late step failures, the status
        # character, the idle timer); interleaved cursor moves and writes
        # would garble the screen.
        self._lock = threading.Lock()
        from RPLCD.i2c import CharLCD  # here, not at import: see _LazyLCD
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
        self._lcd.create_char(0, _ARROW_UPDOWN_ROWS)
        self._lcd.create_char(6, _ELLIPSIS_ROWS)
        # Status character in line 1's last column (write_with_status()).
        # _status_shown is cleared by every other line-1 write, so a
        # background set_status_char() never draws over another screen.
        self._status_char = " "
        self._status_shown = False
        # Backlight idle-off (the backpack's P3 bit; RPLCD re-sends it with
        # every write, so writes while it's off don't relight it).
        # _idle_gen invalidates a timer that fired but is still waiting on
        # _lock when wake() re-arms.
        self._idle_timer: threading.Timer | None = None
        self._idle_gen = 0
        self._backlight_listener = None
        self._idle_seconds: float | None = BACKLIGHT_IDLE_SECONDS
        self.clear()
        self.wake()

    def set_backlight_listener(self, listener) -> None:
        """Register *listener(lit)*, called when the backlight goes off
        (False) or comes back on (True). Called outside the lock, from the
        timer thread or wake()'s caller: must be quick and not raise."""
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
        """Backlight on for good — on exit, so the exit message stays
        readable. On a power-off, systemd/system-shutdown/shutdown-signal.sh
        turns it off at the last moment."""
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
        """Both lines, each padded to 16 columns so every cell is rewritten
        — no clear() first: on this bit-banged bus it's a slow extra command
        and makes the screen flicker, for nothing the padding doesn't do."""
        with self._lock:
            self._status_shown = False
            self._lcd.cursor_pos = (0, 0)
            self._lcd.write_string(line1[:16].ljust(16))
            self._lcd.cursor_pos = (1, 0)
            self._lcd.write_string(line2[:16].ljust(16))

    def write_line1(self, text: str = "") -> None:
        """Rewrite only the top row, leaving line 2 as it is."""
        with self._lock:
            self._status_shown = False
            self._lcd.cursor_pos = (0, 0)
            self._lcd.write_string(text[:16].ljust(16))

    def write_line2(self, text: str = "") -> None:
        """Rewrite only the bottom row, leaving line 1 as it is."""
        with self._lock:
            self._lcd.cursor_pos = (1, 0)
            self._lcd.write_string(text[:16].ljust(16))

    def write_with_status(self, line1: str = "", line2: str = "") -> None:
        """write(), with *line1* cut to 15 columns and the current status
        character (set_status_char()) in the 16th, kept up to date there
        until something else redraws line 1. Used by the Google TV screen
        (screens/active_device_screen.py) for its connection status."""
        with self._lock:
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
        """*line1* on top; a bar filled to *fraction* (0.0-1.0) below, at 5
        steps per cell."""
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


class _LazyLCD:
    """Stands in for the shared LCD and opens the hardware on first use, so
    importing this module (every screen does, for the LCD type) has no side
    effects — no I2C traffic, and it works where RPLCD isn't installed (the
    build scripts, WSL2). First use is startup's progress bar, on the main
    thread before any other thread exists."""

    _real: LCD | None = None

    def __getattr__(self, name):
        if _LazyLCD._real is None:
            _LazyLCD._real = LCD()
        return getattr(_LazyLCD._real, name)


# Module-level singleton — imported by screens and startup alike.
lcd = _LazyLCD()
