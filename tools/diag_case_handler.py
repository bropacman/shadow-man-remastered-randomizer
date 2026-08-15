"""
tools/diag_case_handler.py
==========================
find_lore_case_handler.py came back empty for type_id 0x4 (Book of Shadows).
gad_pickup_patch.py's _find_case_handler() only silently returns None on a
bad result -- it doesn't show its work. This script recomputes the same
switch-table walk by hand and prints every intermediate value, for BOTH
type_id 0x4 (failing) and type_id 0x16 (Prophecy -- known-good, since
gad_pickup_patch.py already ships a verified CASE16_VA/CASE16_VANILLA for
it). Comparing the two side by side should show where the math breaks for
0x4: bad idx byte, out-of-range table read, or a case_foff that comes out
negative/oversized.

Usage:
    python tools/diag_case_handler.py "C:\\path\\to\\thoth_x64.exe"
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gad_pickup_patch import IMAGE_BASE, CASE16_VA, CASE16_VANILLA  # noqa: E402

TARGET_TABLE_VA = 0x14064A368
SWITCH_DATA_VA  = 0x14064A398
TARGET_TABLE_FOFF = TARGET_TABLE_VA - IMAGE_BASE - 0xC00
SWITCH_DATA_FOFF  = SWITCH_DATA_VA - IMAGE_BASE - 0xC00


def walk(data: bytes, type_id: int) -> None:
    print(f"\n--- type_id 0x{type_id:X} ---")
    if not (1 <= type_id <= 0x1E):
        print("  OUT OF RANGE (switch only covers 1..0x1E)")
        return

    switch_off = SWITCH_DATA_FOFF + (type_id - 1)
    print(f"  SWITCH_DATA_FOFF + (type_id-1) = 0x{switch_off:X}  "
          f"(file len 0x{len(data):X})")
    if switch_off < 0 or switch_off >= len(data):
        print("  -> switch_off itself is out of file bounds!")
        return
    idx = data[switch_off]
    print(f"  idx byte read: 0x{idx:X} ({idx})")

    target_off = TARGET_TABLE_FOFF + idx * 4
    print(f"  TARGET_TABLE_FOFF + idx*4 = 0x{target_off:X}")
    if target_off < 0 or target_off + 4 > len(data):
        print("  -> target_off out of file bounds, can't read target_rel")
        return
    target_rel = struct.unpack_from('<I', data, target_off)[0]
    print(f"  target_rel (raw u32): 0x{target_rel:X}")

    case_va = IMAGE_BASE + target_rel
    case_foff = case_va - IMAGE_BASE - 0xC00
    print(f"  case_va   = 0x{case_va:X}")
    print(f"  case_foff = 0x{case_foff:X}  (valid range: 0..0x{len(data):X})")

    if case_foff < 0 or case_foff + 12 > len(data):
        print("  -> INVALID: case_foff out of bounds. This is why it returned None.")
        return

    vanilla = bytes(data[case_foff:case_foff + 12])
    print(f"  First 12 bytes at case_foff: {vanilla.hex(' ')}")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python diag_case_handler.py <path-to-thoth_x64.exe>")
        sys.exit(1)
    exe_path = Path(sys.argv[1])
    data = exe_path.read_bytes()

    print(f"IMAGE_BASE       = 0x{IMAGE_BASE:X}")
    print(f"TARGET_TABLE_VA  = 0x{TARGET_TABLE_VA:X}  -> file off 0x{TARGET_TABLE_FOFF:X}")
    print(f"SWITCH_DATA_VA   = 0x{SWITCH_DATA_VA:X}  -> file off 0x{SWITCH_DATA_FOFF:X}")
    print(f"Known-good CASE16_VA from gad_pickup_patch.py = 0x{CASE16_VA:X}")
    print(f"Known-good CASE16_VANILLA bytes                = {CASE16_VANILLA.hex(' ')}")

    # Known-good comparison case first
    walk(data, 0x16)
    # The one that's failing
    walk(data, 0x4)


if __name__ == "__main__":
    main()
