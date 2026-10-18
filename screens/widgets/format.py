from hardware.lcd import GLYPH_ARROW_DOWN

# Known acronyms, uppercased in full rather than title-cased — checked
# per word, not on the whole name, so "google_tv" -> "Google TV" the same
# way the standalone "tv" device -> "TV". Extend as new devices need it.
_ACRONYMS = {"ac", "tv"}

# Known single-letter directional suffixes, spelled out in full rather
# than title-cased — checked per word, same as _ACRONYMS, so
# "kitchen_n"/"kitchen_s" -> "Kitchen North"/"Kitchen South" (2026-09-09)
# without renaming the actual signals/ directories or any config that
# references them by their short "kitchen_n"/"kitchen_s" names.
_DIRECTIONS = {"n": "North", "s": "South", "e": "East", "w": "West"}


def format_name(name: str) -> str:
    """Convert snake_case / kebab-case names to a readable display form."""
    words = name.replace("_", " ").replace("-", " ").strip().split()
    return " ".join(
        w.upper() if w.lower() in _ACRONYMS
        else _DIRECTIONS[w.lower()] if w.lower() in _DIRECTIONS
        else w.title()
        for w in words
    )


def slugify(text: str) -> str:
    """Convert free text typed on the D-pad (screens/widgets/char_input.py)
    into a filesystem-safe snake_case name — the inverse of format_name.
    Anything outside [a-z0-9_] is dropped rather than substituted, so e.g.
    "Mom's House!" becomes "moms_house", not "mom_s_house_". Runs of
    whitespace/hyphens collapse to a single underscore, so "Lake  Cabin"
    doesn't come out "lake__cabin"."""
    text = "_".join(text.strip().lower().replace("-", " ").split())
    return "".join(c for c in text if c.isalnum() or c == "_")


def frame(text: str, width: int = 16) -> str:
    """Line-2 text for an Active Device Control function/leaf you can cycle
    away from with Left/Right — "<   name   >", *width* columns with the
    name cut to fit so the arrows always show (15 leaves line 1's last
    column for a status character). Menus and lists use with_arrows()
    instead."""
    inner = width - 2
    return f"<{text[:inner].center(inner)}>"


# Scroll indicators in the last column of each line — "^" is in the
# HD44780's ROM; it has no down arrow, so hardware/lcd.py defines a custom
# glyph mirroring it.
ARROW_UP = "^"
ARROW_DOWN = GLYPH_ARROW_DOWN


def with_arrows(line1: str, line2: str, up: bool, down: bool) -> tuple[str, str]:
    """Both lines of a scrolling screen (the app-wide navigation
    convention — see CLAUDE.md's "Navigation convention"): each cut to 15
    characters, with "^" in line 1's last column when Up has somewhere to
    go and "v" in line 2's when Down does."""
    return (
        line1[:15].ljust(15) + (ARROW_UP if up else " "),
        line2[:15].ljust(15) + (ARROW_DOWN if down else " "),
    )


def crumbs(*names: str) -> str:
    """Line-1 breadcrumb for a sub-menu — the path of names that led here,
    ending with the screen you're on: "Menu > Rename". When it's longer
    than 15 characters (the 16th column holds a list's "^"), names are
    dropped from the left until it fits ("Menu > Sweep > TV" ->
    "Sweep > TV"); the last one always stays. Empty names (a device with
    no location) are skipped."""
    parts = [n for n in names if n]
    while len(parts) > 1 and len(" > ".join(parts)) > 15:
        parts.pop(0)
    return " > ".join(parts)[:15]
