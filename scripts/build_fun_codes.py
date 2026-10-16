"""Build data/fun_codes.json — the TV power-code library behind the Fun
menu's sweep (screens/fun_screen.py, screens/code_sweep_screen.py).

Runs once on WSL2, never on the Pi. Reads local checkouts of:
  - Flipper-IRDB (https://github.com/Lucaslhm/Flipper-IRDB), TVs/<Brand>/*.ir
  - probonopd/irdb (https://github.com/probonopd/irdb), codes/<Brand>/TV/*.csv
    — only for brands Flipper-IRDB lacks (ProScan)

    python3 scripts/build_fun_codes.py <flipper-irdb dir> <probonopd-irdb dir>

Each brand's power codes are de-duplicated and ordered by how many model
files share them (most common first), so a sweep usually hits the right one
early. Brands with no power code at all are left out (Skyworth, Sansui,
Xiaomi as of 2026-10-15) — rerun once a source has one.

Brand list and order (decided 2026-10-15): the Statista Canada 2022 brand
-share core in share order, then the user's additions A-Z. See CLAUDE.md's
"Fun menu" section.
"""

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT = PROJECT_ROOT / "data" / "fun_codes.json"

# (display name, Flipper-IRDB folder, probonopd/irdb folder)
BRANDS = [
    ("Samsung", "Samsung", None),
    ("LG", "LG", None),
    ("Sony", "Sony", None),
    ("TCL", "TCL", None),
    ("Hisense", "Hisense", None),
    ("Panasonic", "Panasonic", None),
    ("Xiaomi", "Xiaomi", None),
    ("Amazon", "Amazon", None),
    ("Dynex", "Dynex", None),
    ("Element", "Element", None),
    ("Insignia", "Insignia", None),
    ("JVC", "JVC", None),
    ("onn.", "Onn", None),
    ("Philips", "Philips", None),
    ("ProScan", None, "Proscan"),
    ("RCA", "RCA", None),
    ("Sansui", "Sansui", "Sansui"),
    ("Sanyo", "Sanyo", None),
    ("Sharp", "Sharp", None),
    ("Skyworth", "Skyworth", "Skyworth"),
    ("Toshiba", "Toshiba", None),
    ("Vizio", "Vizio", None),
    ("Westinghouse", "Westinghouse", None),
]

# Brands that sell Roku TV models — the Roku TV power code (taken from
# Flipper-IRDB's onn. Roku TV file) goes second in their list, right after
# the brand's own most common code, so a Roku-based set is found early.
ROKU_TV_BRANDS = {
    "TCL", "Hisense", "Element", "Insignia", "JVC", "onn.", "Philips",
    "RCA", "Sanyo", "Sharp", "Westinghouse",
}
ROKU_TV_POWER = {"protocol": "NECext", "address": 0xC7EA, "command": 0xE817}

# Power toggles and power-off buttons. Power-on-only buttons are skipped:
# a sweep runs with the TV on, so they'd never show a reaction.
_POWER_NAME = re.compile(r"^(tv[ _])?power([ _]?(off|off_only|2|alt|toggle))?$", re.I)

# probonopd/irdb protocol names -> (encoder protocol, carrier Hz).
# irdb's RCA is the original 56 kHz; RCA-38 is the 38 kHz variant.
_IRDB_PROTOCOLS = {"RCA": ("RCA", 56000), "RCA-38": ("RCA", 38000)}


def _flipper_int(field: str) -> int:
    """Flipper's "address: 86 05 00 00" -> 0x0586 (little-endian bytes)."""
    return int.from_bytes(bytes.fromhex(field.replace(" ", "")), "little")


def _parse_flipper_file(path: Path) -> list[dict]:
    blocks, block = [], {}
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("#"):
            if block:
                blocks.append(block)
            block = {}
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            block[key.strip().lower()] = value.strip()
    if block:
        blocks.append(block)
    return [b for b in blocks if "name" in b and "type" in b]


def _flipper_entry(block: dict) -> dict | None:
    if block["type"] == "parsed":
        return {
            "protocol": block["protocol"],
            "address": _flipper_int(block["address"]),
            "command": _flipper_int(block["command"]),
        }
    if block["type"] == "raw":
        return {
            "raw": [int(v) for v in block["data"].split()],
            "carrier": int(block.get("frequency", 38000)),
        }
    return None


def _reverse_bits(value: int, width: int) -> int:
    return int(f"{value:0{width}b}"[::-1], 2)


def _irdb_entries(brand_dir: Path) -> list[dict]:
    """probonopd/irdb TV CSVs. Its RCA is MSB-first (IRP {msb}); Flipper's
    RCA, which ir_encode follows, packs bits LSB-first — so device and
    function are bit-reversed into Flipper's representation."""
    entries = []
    for csv in sorted((brand_dir / "TV").glob("*.csv")):
        for row in csv.read_text().splitlines()[1:]:
            name, protocol, device, _subdevice, function = row.split(",")
            if not _POWER_NAME.match(name.strip().replace(" ", "_")):
                continue
            if protocol not in _IRDB_PROTOCOLS:
                continue
            proto, carrier = _IRDB_PROTOCOLS[protocol]
            entries.append({
                "protocol": proto,
                "address": _reverse_bits(int(device), 4),
                "command": _reverse_bits(int(function), 8),
                "carrier": carrier,
            })
    return entries


def _key(entry: dict) -> str:
    return json.dumps(entry, sort_keys=True)


def _brand_codes(display: str, flipper_dir: Path | None, irdb_dir: Path | None) -> list[dict]:
    counts: Counter = Counter()
    first_seen: dict[str, int] = {}
    entries: dict[str, dict] = {}

    found = []
    if flipper_dir and flipper_dir.is_dir():
        for path in sorted(flipper_dir.rglob("*.ir")):
            # One vote per model file, even if it lists the same code twice.
            per_file = {}
            for block in _parse_flipper_file(path):
                if _POWER_NAME.match(block["name"]):
                    entry = _flipper_entry(block)
                    if entry:
                        per_file[_key(entry)] = entry
            found.extend(per_file.values())
    if irdb_dir and irdb_dir.is_dir():
        found.extend(_irdb_entries(irdb_dir))

    for entry in found:
        k = _key(entry)
        counts[k] += 1
        first_seen.setdefault(k, len(first_seen))
        entries[k] = entry

    ordered = sorted(entries, key=lambda k: (-counts[k], "raw" in entries[k], first_seen[k]))
    codes = [entries[k] for k in ordered]

    if display in ROKU_TV_BRANDS:
        roku = _key(ROKU_TV_POWER)
        codes = [c for c in codes if _key(c) != roku]
        codes.insert(min(1, len(codes)), dict(ROKU_TV_POWER))
    return codes


def _git_head(path: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    flipper_root, irdb_root = Path(sys.argv[1]), Path(sys.argv[2])

    tv: dict[str, dict] = {}
    for display, flipper_name, irdb_name in BRANDS:
        codes = _brand_codes(
            display,
            flipper_root / "TVs" / flipper_name if flipper_name else None,
            irdb_root / "codes" / irdb_name if irdb_name else None,
        )
        if codes:
            tv[display] = {"power": codes}
        print(f"{display:13} {len(codes):3} codes" + ("" if codes else "  (left out)"))

    out = {
        "_source": {
            "flipper_irdb": _git_head(flipper_root),
            "probonopd_irdb": _git_head(irdb_root),
        },
        "tv": tv,
    }
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(out, indent=1) + "\n")
    print(f"{len(tv)} brands -> {OUTPUT.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
