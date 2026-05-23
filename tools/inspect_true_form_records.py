"""
inspect_true_form_records.py — Dump raw field values for specific RSC records.

Reads the exact bytes for track_type and save_idx out of the listed
(level_id, source_file, name_offset) entries so we can confirm what is
actually stored in the binary vs. what the CSV says.

Usage:
    python tools/inspect_true_form_records.py --levels-dir "C:/path/to/levels"
"""

import argparse
import struct
from pathlib import Path

HEADER_SIZE    = 8
RECORD_SIZE    = 72
NAME_OFF       = 0x22   # name field starts here within each record
TRACK_TYPE_OFF = 0x1C
SAVE_IDX_OFF   = 0x1E
NAME_MAXLEN    = 30

# Targets: (level_id, source_file, name_offset_hex, csv_save_idx)
# name_offset is the value stored in the CSV — i.e. record_start + NAME_OFF
TARGETS = [
    ("as4dkeng", "enemies.rsc", 0x1998, 38),
    ("as4dkeng", "enemies.rsc", 0x04AA, 40),
    ("as4dkeng", "enemies.rsc", 0x14B0, 52),
    ("ah4fogom", "enemies.rsc", 0x08DC, 120),
]


def _read_name(rec: bytes) -> str:
    raw = rec[NAME_OFF : NAME_OFF + NAME_MAXLEN]
    null = raw.find(b"\x00")
    raw = raw[:null] if null >= 0 else raw
    try:
        return raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return raw.decode("latin-1").strip()


def inspect(levels_dir: Path) -> None:
    print(f"{'level':<12} {'file':<14} {'name_off':>10}  {'name':<30} "
          f"{'track_type':>12}  {'save_idx_bin':>13}  {'save_idx_csv':>13}  match?")
    print("-" * 110)

    for level_id, src_file, name_off, csv_save_idx in TARGETS:
        path = levels_dir / level_id / src_file
        try:
            data = path.read_bytes()
        except OSError:
            print(f"  !! Could not read {path}")
            continue

        # name_off is record_start + NAME_OFF  →  record_start = name_off - NAME_OFF
        rec_start = name_off - NAME_OFF
        if rec_start < HEADER_SIZE or rec_start + RECORD_SIZE > len(data):
            print(f"  !! Offset 0x{name_off:04X} out of range in {path.name} "
                  f"(file size {len(data)})")
            continue

        rec = data[rec_start : rec_start + RECORD_SIZE]
        name       = _read_name(rec)
        track_type = struct.unpack(">H", rec[TRACK_TYPE_OFF : TRACK_TYPE_OFF + 2])[0]
        save_idx   = struct.unpack(">I", rec[SAVE_IDX_OFF   : SAVE_IDX_OFF   + 4])[0]
        match      = "✓" if save_idx == csv_save_idx else f"✗ (csv={csv_save_idx})"

        print(f"  {level_id:<12} {src_file:<14} 0x{name_off:04X}      "
              f"{name:<30} 0x{track_type:04X}        {save_idx:>13}  {csv_save_idx:>13}  {match}")

    print()
    print("Note: save_idx_bin == 0 means the binary doesn't store a drop ID for this")
    print("      entry — the CSV value came from an external source (e.g. levels.txt).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--levels-dir", required=True,
                        help="Path to the unpacked levels directory")
    args = parser.parse_args()
    inspect(Path(args.levels_dir))


if __name__ == "__main__":
    main()
