from hardware.lcd import GLYPH_ARROW_UPDOWN, GLYPH_ELLIPSIS

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


def frame(text: str) -> str:
    """Line-2 text for an Active Device Control function/leaf you can cycle
    away from with Left/Right — "<   name   >", cut to 14 characters so the
    arrows always show. Menus and lists use with_arrows() instead."""
    return f"<{text[:14].center(14)}>"


def fit_with_suffix(name: str, suffix: str, width: int) -> str:
    """*name* left, *suffix* right-aligned, *width* characters in all, at
    least one space between. A name too long for that is cut and ends in
    an ellipsis glyph, straight against the suffix: "Westingh…38/38"."""
    room = width - len(suffix)
    if len(name) < room:
        return name.ljust(room) + suffix
    return name[:room - 1] + GLYPH_ELLIPSIS + suffix


def with_arrows(line1: str, line2: str, scrollable: bool) -> tuple[str, str]:
    """Both lines of a scrolling screen (the app-wide navigation
    convention — see CLAUDE.md's "Navigation convention"): line 1 gets all
    16 columns; line 2 is cut to 15, with the up/down glyph in its last
    column when Up/Down have somewhere to go."""
    return (
        line1[:16],
        line2[:15].ljust(15) + (GLYPH_ARROW_UPDOWN if scrollable else " "),
    )


def crumbs(*names: str) -> str:
    """Line-1 breadcrumb for a sub-menu — the path of names that led here,
    ending with the screen you're on: "Menu > Rename". When it's longer
    than 16 characters, names are dropped from the left until it fits
    ("Menu > Sweep > TV > Hisense" -> "TV > Hisense"); the last one
    always stays. Empty names (a device with no location) are skipped."""
    parts = [n for n in names if n]
    while len(parts) > 1 and len(" > ".join(parts)) > 16:
        parts.pop(0)
    return " > ".join(parts)[:16]
