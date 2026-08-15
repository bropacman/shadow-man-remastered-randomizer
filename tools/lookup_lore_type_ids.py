"""
tools/lookup_lore_type_ids.py
==============================
Step 1 of the Book of Shadows pickup-animation investigation (see CLAUDE.md
"Next Up"). This is a read-only scan, not a Ghidra task -- it reuses the RSC
dispatch-table scanner already written for gad_pickup_patch.py to answer one
question: what type_id does RSC_X_BOOK_OF_SHADOWS (and its LORE_TYPES
siblings, RSC_X_PROPHECY / RSC_X_JACKS_SCHEMATIC) resolve to?

That type_id is what you'll feed into Ghidra in step 2, to find the actual
switch-case handler and disassemble it.

Usage:
    python tools/lookup_lore_type_ids.py "C:\\path\\to\\thoth_x64.exe"

Safe to run directly against your live game install -- it only reads bytes,
never writes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gad_pickup_patch import lookup_type_id  # noqa: E402  (reuses the existing scanner)

LORE_NAMES = [
    "RSC_X_BOOK_OF_SHADOWS",
    "RSC_X_PROPHECY",
    "RSC_X_JACKS_SCHEMATIC",
]


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python lookup_lore_type_ids.py <path-to-thoth_x64.exe>")
        sys.exit(1)

    exe_path = Path(sys.argv[1])
    if not exe_path.exists():
        print(f"ERROR: {exe_path} not found")
        sys.exit(1)

    data = exe_path.read_bytes()
    print(f"Scanning dispatch table in {exe_path.name} ({len(data)} bytes)...\n")

    results = {}
    for name in LORE_NAMES:
        type_id = lookup_type_id(data, name)
        results[name] = type_id
        if type_id is None:
            print(f"  {name:28s} -> NOT FOUND")
        else:
            print(f"  {name:28s} -> type_id 0x{type_id:X}")

    print()
    ids = [v for v in results.values() if v is not None]
    if len(ids) == len(LORE_NAMES) and len(set(ids)) == 1:
        print(f"All three lore items share type_id 0x{ids[0]:X} -- "
              f"one shared case handler. Ghidra step (2) only needs to "
              f"look up this one type_id.")
    elif ids:
        print("Lore items do NOT all share a type_id -- each will need its "
              "own case handler check in Ghidra step (2).")
    else:
        print("None of the names resolved -- double check this is the right "
              "exe version/file (vanilla vs. already-patched).")


if __name__ == "__main__":
    main()
