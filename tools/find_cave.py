"""
tools/find_cave.py
===================
Scans thoth_x64(_patched).exe's .text section for contiguous runs of a
repeated filler byte -- 0xCC is the inter-function alignment padding MSVC
leaves between functions (capped at 15 bytes under 16-byte function
alignment, confirmed empirically), 0x00 is what shows up at the tail of
.text past the last real function (the gad_pickup_patch.py /
death_penalty_patch.py cave region is in exactly that spot: VA
0x14064A354, right before .text ends at 0x14064A3FF).

Since 16-byte function alignment means no *inter-function* CC gap can ever
be much bigger than 15 bytes, finding room for a larger cave (a few dozen
bytes, for something like a Shift-sprint speed patch that needs register
save/restore around a CALL) means either:
  - hunting for a rarer, larger gap (sometimes left at object-file/module
    boundaries by the linker), or
  - chaining multiple small gaps together with connecting JMPs, or
  - as a last resort, extending .text / adding a new section (not done
    anywhere in this codebase yet -- every existing patch reuses
    pre-existing bytes, never resizes the file).

This script surfaces every candidate run >= --min-size so that decision
can be made with real data instead of guessing. Reuses the IMAGE_BASE /
SECTION_DELTA convention from death_penalty_patch.py so results can be
dropped straight into a new patch module's *_CAVE_VA constant.

Usage:
    python tools/find_cave.py "C:\\path\\to\\thoth_x64_patched.exe"
    python tools/find_cave.py "C:\\path\\to\\thoth_x64_patched.exe" --min-size 16
    python tools/find_cave.py "C:\\path\\to\\thoth_x64_patched.exe" --byte 0x00
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from death_penalty_patch import IMAGE_BASE, SECTION_DELTA  # noqa: E402

# .text section bounds, from the PE section table (Ghidra Memory Map):
#   .text   140001000 - 14064a3ff   (VA, inclusive)
# If this build's section layout ever changes, re-check via Ghidra's
# Window > Memory Map before trusting these bounds.
TEXT_VA_START = 0x140001000
TEXT_VA_END   = 0x14064A3FF   # inclusive, last byte of .text

# Every PE section can have its own independent VA-to-file-offset delta
# (RawSize and VirtualSize commonly differ per section), so .rdata/.data
# each need their own constant here, not the .text-derived SECTION_DELTA.
# Pulled from Ghidra's Memory Map 2026-07-31 (Jon):
#   .rdata  14064b000 - 1407ebdff   file 0x649800, length 0x1a0e00
#   .data   1407ec000 - 140fc4dc7   file 0x7ea600, length 0x4fee00 (file-
#           backed) + init[0x2d9fc8] (zero-fill-at-load tail, NOT in the
#           file at all -- not scannable/patchable, excluded below)
RDATA_VA_START = 0x14064B000
RDATA_VA_END   = 0x1407EBDFF
RDATA_DELTA    = 0x1800

DATA_VA_START = 0x1407EC000
DATA_VA_END   = 0x140CEAE00 - 1   # end of the file-backed portion only
DATA_DELTA    = 0x1A00

# name -> (va_start, va_end, delta)
SECTIONS = {
    "text":  (TEXT_VA_START, TEXT_VA_END, SECTION_DELTA),
    "rdata": (RDATA_VA_START, RDATA_VA_END, RDATA_DELTA),
    "data":  (DATA_VA_START, DATA_VA_END, DATA_DELTA),
}


def _va_to_file(va: int, delta: int = SECTION_DELTA) -> int:
    return va - IMAGE_BASE - delta


def _file_to_va(file_off: int, delta: int = SECTION_DELTA) -> int:
    return file_off + IMAGE_BASE + delta


def find_runs(data: bytes, fill_byte: int, min_size: int, start: int, end: int):
    """Return [(file_offset, length), ...] for every run of `fill_byte`
    of at least `min_size` bytes within data[start:end)."""
    runs = []
    i = start
    while i < end:
        if data[i] == fill_byte:
            j = i
            while j < end and data[j] == fill_byte:
                j += 1
            length = j - i
            if length >= min_size:
                runs.append((i, length))
            i = j
        else:
            i += 1
    return runs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find code-cave / free-data candidates (runs of filler bytes) "
                    "in .text, .rdata, or .data"
    )
    parser.add_argument("exe", help="Path to thoth_x64.exe or thoth_x64_patched.exe")
    parser.add_argument("--section", choices=sorted(SECTIONS.keys()), default="text",
                         help="Which PE section to scan (default text). "
                             "rdata/data are for CONTIGUOUS DATA (tables, etc), "
                             "not executable caves -- .data needs --byte 0x00, "
                             "not 0xCC, which is a .text-only alignment convention.")
    parser.add_argument("--min-size", type=int, default=32,
                         help="Minimum run length in bytes (default 32)")
    parser.add_argument("--byte", type=lambda x: int(x, 0), default=None,
                         help="Filler byte to search for (default: 0xCC for "
                             "--section text, 0x00 for rdata/data)")
    parser.add_argument("--top", type=int, default=25,
                         help="Show only the N largest runs (default 25)")
    args = parser.parse_args()

    fill_byte = args.byte if args.byte is not None else (0xCC if args.section == "text" else 0x00)

    path = Path(args.exe)
    if not path.exists():
        print(f"ERROR: file not found: {args.exe}")
        sys.exit(1)

    data = path.read_bytes()

    va_start, va_end, delta = SECTIONS[args.section]
    file_start = _va_to_file(va_start, delta)
    file_end = _va_to_file(va_end, delta) + 1  # exclusive
    if file_end > len(data):
        file_end = len(data)

    runs = find_runs(data, fill_byte, args.min_size, file_start, file_end)
    runs.sort(key=lambda r: r[1], reverse=True)

    print(f"Scanned .{args.section}  file 0x{file_start:X}-0x{file_end:X}  "
          f"VA 0x{va_start:X}-0x{va_end:X}")
    print(f"Filler byte: 0x{fill_byte:02X}   Minimum run size: {args.min_size} bytes")
    print(f"Found {len(runs)} run(s) >= {args.min_size} bytes\n")

    if not runs:
        print("  (none found at this size -- try a smaller --min-size, "
              "or a different --byte)")
        return

    for file_off, length in runs[:args.top]:
        va = _file_to_va(file_off, delta)
        print(f"  VA 0x{va:X}   file 0x{file_off:X}   length {length} bytes")

    if len(runs) > args.top:
        print(f"\n  ... ({len(runs) - args.top} more, use --top to see more)")

    # Quick histogram so it's obvious at a glance whether anything unusually
    # large exists, or whether every hit is clustered at the 15-byte ceiling.
    sizes = sorted(set(length for _, length in runs), reverse=True)
    print(f"\nDistinct run sizes found: {sizes[:15]}"
          f"{' ...' if len(sizes) > 15 else ''}")


if __name__ == "__main__":
    main()
