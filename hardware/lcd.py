"""LCD hardware abstraction.

Wraps the 1602 IIC 16×2 HD44780 display (PCF8574 I²C backpack) over
software I²C.  All other layers call only ``lcd.write(line1, line2)``
and ``lcd.clear()``.
"""

import threading

from RPLCD.i2c import CharLCD

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
# once it's done load_preset_glyphs() reuses its CGRAM slots 1-3 for these
# — never loaded alongside the bar, so no slot conflict.
GLYPH_HOME = chr(1)
GLYPH_CC_LEFT = chr(2)
GLYPH_CC_RIGHT = chr(3)
_PRESET_GLYPHS = {
    # Solid house.
    1: [
        0b00100,
        0b01110,
        0b11111,
        0b11111,
        0b11111,
        0b11111,
        0b11111,
        0b11111,
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
        self.clear()

    def clear(self) -> None:
        with self._lock:
            self._lcd.clear()

    def write(self, line1: str = "", line2: str = "") -> None:
        with self._lock:
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
            self._lcd.cursor_pos = (0, 0)
            self._lcd.write_string(line1[:16].ljust(16))
            self._lcd.cursor_pos = (1, 0)
            self._lcd.write_string(bar)


# Module-level singleton — imported by screens and startup alike.
lcd = LCD()
