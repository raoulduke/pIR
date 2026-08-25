#!/usr/bin/env python3
"""Interactive keypad mapping wizard.

Run this ON THE PI, with the keypad wired per CLAUDE.md's Pin Wiring Table:

    sudo .venv/bin/python3 scripts/keypad_wizard.py

For each button you press it detects which wire(s) reacted and asks you what
label is printed on that key (e.g. "1", "*", "A", "UP"). Recognized nav
labels (UP/DOWN/LEFT/RIGHT/ENTER/BACK/BACKSPACE/QUIT — see
hardware/keypad.py's _LABEL_ALIASES) get translated to the app's existing
key vocabulary at runtime; anything else passes through as a raw character.

Result is written to config/keypad_map.json, consumed by hardware/keypad.py.
Safe to re-run — it starts from any existing map and lets you overwrite or
add entries.
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from hardware import keypad
from app.constants import KEYPAD_MAP_FILE

_MAP_PATH = Path(__file__).parent.parent / KEYPAD_MAP_FILE


def main() -> None:
    existing = {}
    if _MAP_PATH.exists():
        try:
            existing = json.loads(_MAP_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            pass

    if existing:
        print(f"Loaded {len(existing)} existing mapping(s) from {_MAP_PATH}")

    keypad.setup_pins()
    result = dict(existing)

    print()
    print("Press each button on the keypad one at a time.")
    print("Type 'done' instead of a label at any point to stop and save.")
    print("Type 'skip' to discard a detected press without saving it.")
    print()

    try:
        while True:
            print("Waiting for a key press... ", end="", flush=True)
            wire_id = None
            while wire_id is None:
                wire_id = keypad._read_debounced(_NeverStop())
            print(f"detected wire(s): {wire_id}")

            if wire_id in result:
                print(f"  (already mapped to {result[wire_id]!r} — this will overwrite it)")

            label = input("  Label printed on this key (or 'done'/'skip'): ").strip()

            if label.lower() == "done":
                break
            if label.lower() == "skip":
                print("  skipped.\n")
                continue
            if not label:
                print("  empty label, skipped.\n")
                continue

            result[wire_id] = label
            print(f"  mapped {wire_id} -> {label!r}\n")

    except KeyboardInterrupt:
        print("\nInterrupted.")

    finally:
        keypad.teardown_pins()

    if result != existing:
        _MAP_PATH.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(f"\nSaved {len(result)} mapping(s) to {_MAP_PATH}")
    else:
        print("\nNo changes made.")


class _NeverStop:
    """Minimal stand-in for threading.Event — this script runs single-threaded."""

    @staticmethod
    def is_set() -> bool:
        return False


if __name__ == "__main__":
    main()
