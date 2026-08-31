"""LCD hardware abstraction.

Wraps the 1602 IIC 16×2 HD44780 display (PCF8574 I²C backpack) over
software I²C.  All other layers call only ``lcd.write(line1, line2)``
and ``lcd.clear()``.
"""

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


class LCD:
    def __init__(self) -> None:
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
        self._lcd.clear()

    def write(self, line1: str = "", line2: str = "") -> None:
        self.clear()
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
        self._lcd.cursor_pos = (0, 0)
        self._lcd.write_string(text[:16].ljust(16))

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

        self._lcd.cursor_pos = (0, 0)
        self._lcd.write_string(line1[:16].ljust(16))
        self._lcd.cursor_pos = (1, 0)
        self._lcd.write_string(bar)


# Module-level singleton — imported by screens and startup alike.
lcd = LCD()
