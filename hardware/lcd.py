"""LCD hardware abstraction.

Wraps the DM2021 16×2 display over I²C.  All other layers call only
``lcd.write(line1, line2)`` and ``lcd.clear()``.
"""

from RPLCD.i2c import CharLCD


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
        self.clear()

    def clear(self) -> None:
        self._lcd.clear()

    def write(self, line1: str = "", line2: str = "") -> None:
        self.clear()
        self._lcd.cursor_pos = (0, 0)
        self._lcd.write_string(line1[:16].ljust(16))
        self._lcd.cursor_pos = (1, 0)
        self._lcd.write_string(line2[:16].ljust(16))


# Module-level singleton — imported by screens and startup alike.
lcd = LCD()
