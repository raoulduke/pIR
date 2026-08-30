"""LCD hardware abstraction.

Wraps the 1602 IIC 16×2 HD44780 display (PCF8574 I²C backpack) over
software I²C.  All other layers call only ``lcd.write(line1, line2)``
and ``lcd.clear()``.
"""

from RPLCD.i2c import CharLCD

# Custom CGRAM characters for a sub-cell-resolution progress bar. Each 5px-
# wide cell gets 1-4 columns filled from the left (slots 1-4); a fully
# filled cell just uses the ROM's built-in solid block (0xFF), so together
# they give 5 fill levels per cell — 80 discrete positions across 16 cells.
_BAR_PARTIAL_BITS = {
    1: 0b10000,
    2: 0b11000,
    3: 0b11100,
    4: 0b11110,
}
_BAR_FULL_CELL = chr(0xFF)


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
