from hardware.lcd import GLYPH_ARROW_UPDOWN, GLYPH_ELLIPSIS

# Words shown in capitals ("google_tv" -> "Google TV").
_ACRONYMS = {"ac", "tv", "dvd"}

# Single-letter suffixes spelled out ("kitchen_n" -> "Kitchen North").
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
    """Free text -> a filesystem-safe snake_case name (the inverse of
    format_name()): characters outside [a-z0-9_] are dropped ("Mom's House!"
    -> "moms_house"), and runs of spaces/hyphens become one underscore."""
    text = "_".join(text.strip().lower().replace("-", " ").split())
    return "".join(c for c in text if c.isalnum() or c == "_")


def fit_or(text: str, fallback: str, width: int = 16) -> str:
    """*text* if it fits *width* columns, else *fallback*: for messages built
    at run time (exception text, step names), which would otherwise be cut
    mid-word."""
    return text if len(text) <= width else fallback


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
    """Line-1 breadcrumb: the names that led here, joined with " > ". Names
    are dropped from the left until it fits 16 columns (the last always stays);
    empty names are skipped."""
    parts = [n for n in names if n]
    while len(parts) > 1 and len(" > ".join(parts)) > 16:
        parts.pop(0)
    return " > ".join(parts)[:16]
