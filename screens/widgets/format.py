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
