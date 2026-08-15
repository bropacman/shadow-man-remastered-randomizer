"""
tools/find_lore_case_handler.py
================================
Step 2 of the Book of Shadows pickup-animation investigation (see CLAUDE.md
"Next Up" and tools/lookup_lore_type_ids.py for step 1).

Step 1 found RSC_X_BOOK_OF_SHADOWS -> type_id 0x4. This script reuses
gad_pickup_patch.py's own switch-table walker (_find_case_handler) to
resolve that type_id to an actual code address -- the VA of the case
handler in the pickup-dispatch switch statement.

This is still read-only (no writes). The output is what you take into
Ghidra next: open thoth_x64.exe there, jump to the printed VA (Ghidra:
"G" for Go To, paste the hex address), and read the disassembly to find:
  (a) whatever call triggers the read-book animation/cutscene, and
  (b) whatever instruction(s) actually set the pickup/quest flag the AP
      location check depends on.
Those may be two separate things in the same case block, which is the
whole point of checking -- see CLAUDE.md's approach A vs B.

Usage:
    python tools/find_lore_case_handler.py "C:\\path\\to\\thoth_x64.exe" 0x4
    python tools/find_lore_case_handler.py "C:\\path\\to\\thoth_x64.exe" 0x4 0x16 0x18
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gad_pickup_patch import _find_case_handler, IMAGE_BASE  # noqa: E402

DUMP_BYTES = 96  # how many raw bytes to hex-dump from the case start, for a quick sanity look


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python find_lore_case_handler.py <path-to-thoth_x64.exe> <type_id_hex> [more type_ids...]")
        print("Example: python find_lore_case_handler.py thoth_x64.exe 0x4")
        sys.exit(1)

    exe_path = Path(sys.argv[1])
    if not exe_path.exists():
        print(f"ERROR: {exe_path} not found")
        sys.exit(1)

    type_ids = [int(x, 16) for x in sys.argv[2:]]
    data = exe_path.read_bytes()

    for type_id in type_ids:
        print(f"\n=== type_id 0x{type_id:X} ===")
        case_foff, case_va, vanilla12 = _find_case_handler(data, type_id)
        if case_va is None:
            print("  Could not resolve -- type_id out of switch range (1..0x1E) or bad table read.")
            continue

        print(f"  Case handler VA:          0x{case_va:X}")
        print(f"  Case handler file offset: 0x{case_foff:X}")
        print(f"  First 12 bytes (vanilla): {vanilla12.hex(' ')}")

        dump = data[case_foff: case_foff + DUMP_BYTES]
        print(f"  First {DUMP_BYTES} bytes hex dump:")
        for i in range(0, len(dump), 16):
            chunk = dump[i:i + 16]
            addr = case_va + i
            hexstr = ' '.join(f'{b:02x}' for b in chunk)
            print(f"    0x{addr:X}: {hexstr}")

    print("\nNext: open the exe in Ghidra, Go To the VA printed above for "
          "type_id 0x4, and read what the disassembly actually does before "
          "we write any patch.")


if __name__ == "__main__":
    main()
