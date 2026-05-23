"""
compare_barrel_bytes.py — Byte-by-byte diff of cadeaux vs non-cadeaux barrel records.

Walks all RSC files, collects every barrel record (BARREL / CRATE / PACKBOX),
splits them into:
  • cadeaux barrels  (track_type == 0x0002)
  • plain barrels    (everything else)

Then for every byte offset in the 72-byte record, prints:
  - the set of values seen in each group
  - a DIFFERS flag when the groups are distinct

Special focus on the unknown regions:
  0x00–0x03  (before XYZ)
  0x10       (between XYZ end and zone)
  0x12–0x1B  (between zone and track_type)
  0x40–0x47  (after name)

Usage:
    python tools/compare_barrel_bytes.py --levels-dir "C:/path/to/levels"
    python tools/compare_barrel_bytes.py --levels-dir "..." --dump-records   # show every raw record
    python tools/compare_barrel_bytes.py --levels-dir "..." --level asylum   # only one level
"""

import argparse
import struct
import sys
from collections import defaultdict
from pathlib import Path

# ── RSC format constants (matches rsc_utils.py) ───────────────────────────────
HEADER_SIZE    = 8
RECORD_SIZE    = 72
XYZ_OFF        = 0x04   # 12 bytes  (3× float32 LE)
ZONE_OFF       = 0x11   #  1 byte
TRACK_TYPE_OFF = 0x1C   #  2 bytes  big-endian
SAVE_IDX_OFF   = 0x1E   #  4 bytes  big-endian
NAME_OFF       = 0x22   # 30 bytes  ASCII null-terminated
NAME_MAXLEN    = 30

TRACK_PERSISTENT = 0x0002

BARREL_KEYWORDS = ("BARREL", "CRATE", "PACKBOX")

# Known field spans — everything outside these is "unknown"
KNOWN_SPANS = [
    (XYZ_OFF,        XYZ_OFF + 12,        "XYZ"),
    (ZONE_OFF,       ZONE_OFF + 1,         "zone"),
    (TRACK_TYPE_OFF, TRACK_TYPE_OFF + 2,   "track_type"),
    (SAVE_IDX_OFF,   SAVE_IDX_OFF + 4,     "save_idx"),
    (NAME_OFF,       NAME_OFF + NAME_MAXLEN,"name"),
]

UNKNOWN_SPANS = []
prev = 0
for start, end, label in sorted(KNOWN_SPANS):
    if start > prev:
        UNKNOWN_SPANS.append((prev, start))
    prev = end
if prev < RECORD_SIZE:
    UNKNOWN_SPANS.append((prev, RECORD_SIZE))


def field_label(off: int) -> str:
    for start, end, label in KNOWN_SPANS:
        if start <= off < end:
            return f"{label}+{off - start}"
    return "??"


def extract_name(rec: bytes) -> str:
    field = rec[NAME_OFF : NAME_OFF + NAME_MAXLEN]
    null  = field.find(b"\x00")
    raw   = field[:null] if null >= 0 else field
    try:
        return raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return raw.decode("latin-1").strip()


def scan_barrels(levels_dir: Path, only_level: str | None) -> tuple[list, list]:
    """Return (cadeaux_records, plain_records) as list of (path, slot, raw_bytes, name, track, save_idx)."""
    cadeaux = []
    plain   = []

    level_dirs = (
        [levels_dir / only_level] if only_level else sorted(levels_dir.iterdir())
    )

    for level_dir in level_dirs:
        if not level_dir.is_dir():
            continue
        for rsc_path in sorted(level_dir.glob("*.rsc")):
            try:
                data = rsc_path.read_bytes()
            except OSError:
                continue
            if len(data) < HEADER_SIZE + RECORD_SIZE:
                continue

            n_slots = (len(data) - HEADER_SIZE) // RECORD_SIZE
            for i in range(n_slots):
                off = HEADER_SIZE + i * RECORD_SIZE
                rec = data[off : off + RECORD_SIZE]
                if bytes(rec) == bytes(RECORD_SIZE):
                    continue

                name  = extract_name(rec)
                if not any(k in name for k in BARREL_KEYWORDS):
                    continue

                track    = struct.unpack(">H", rec[TRACK_TYPE_OFF : TRACK_TYPE_OFF + 2])[0]
                save_idx = struct.unpack(">I", rec[SAVE_IDX_OFF   : SAVE_IDX_OFF   + 4])[0]
                entry    = (rsc_path, i, rec, name, track, save_idx)

                if track == TRACK_PERSISTENT:
                    cadeaux.append(entry)
                else:
                    plain.append(entry)

    return cadeaux, plain


def byte_sets(records: list) -> list[set]:
    """For each byte offset, the set of values seen across all records."""
    sets = [set() for _ in range(RECORD_SIZE)]
    for _, _, rec, *_ in records:
        for b in range(RECORD_SIZE):
            sets[b].add(rec[b])
    return sets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--levels-dir", required=True)
    parser.add_argument("--level",       default=None, metavar="LEVEL_ID",
                        help="Restrict to a single level directory")
    parser.add_argument("--dump-records", action="store_true",
                        help="Print every raw record (hex) for both groups")
    parser.add_argument("--unknown-only", action="store_true",
                        help="Only print bytes in unknown regions")
    args = parser.parse_args()

    levels_dir = Path(args.levels_dir)
    if not levels_dir.is_dir():
        print(f"ERROR: levels dir not found: {levels_dir}", file=sys.stderr)
        sys.exit(1)

    cadeaux, plain = scan_barrels(levels_dir, args.level)

    print(f"Cadeaux barrels (track=0x0002): {len(cadeaux)}")
    print(f"Plain  barrels  (other track):  {len(plain)}")

    if not cadeaux or not plain:
        print("\nNeed at least one record in each group to compare — aborting.")
        sys.exit(0)

    # ── Optional: dump every raw record ──────────────────────────────────────
    if args.dump_records:
        for label, group in [("CADEAUX", cadeaux), ("PLAIN", plain)]:
            print(f"\n{'='*70}")
            print(f"{label} BARREL RECORDS ({len(group)}):")
            for rsc_path, slot, rec, name, track, save_idx in group:
                print(f"\n  {rsc_path.parent.name}/{rsc_path.name}  slot={slot}"
                      f"  name={name!r}  track=0x{track:04X}  save_idx={save_idx}")
                # Print hex in rows of 16
                for row in range(0, RECORD_SIZE, 16):
                    chunk = rec[row:row+16]
                    hex_part = " ".join(f"{b:02X}" for b in chunk)
                    print(f"    {row:02X}: {hex_part}")

    # ── Per-byte comparison ───────────────────────────────────────────────────
    cad_sets   = byte_sets(cadeaux)
    plain_sets = byte_sets(plain)

    print(f"\n{'='*70}")
    print("BYTE-BY-BYTE COMPARISON (cadeaux vs plain barrels)")
    print(f"{'Off':>4}  {'Field':<18}  {'Cadeaux values':<28}  {'Plain values':<28}  Note")
    print("-" * 100)

    differs_unknown = []

    for b in range(RECORD_SIZE):
        # Optionally skip known fields
        in_unknown = any(s <= b < e for s, e in UNKNOWN_SPANS)
        if args.unknown_only and not in_unknown:
            continue

        cv = sorted(cad_sets[b])
        pv = sorted(plain_sets[b])

        cv_str = ", ".join(f"0x{v:02X}" for v in cv)
        pv_str = ", ".join(f"0x{v:02X}" for v in pv)

        differs  = set(cv) != set(pv)
        note     = ""
        if differs:
            note = "<<< DIFFERS"
            if in_unknown:
                note += " (UNKNOWN FIELD)"
                differs_unknown.append(b)

        print(f"  {b:02X}  {field_label(b):<18}  {cv_str:<28}  {pv_str:<28}  {note}")

    # ── Summary of interesting unknowns ──────────────────────────────────────
    print(f"\n{'='*70}")
    print("UNKNOWN REGIONS SUMMARY:")
    for start, end in UNKNOWN_SPANS:
        span_bytes = list(range(start, end))
        diffs = [b for b in span_bytes if sorted(cad_sets[b]) != sorted(plain_sets[b])]
        all_zero_cad   = all(cad_sets[b] == {0} for b in span_bytes)
        all_zero_plain = all(plain_sets[b] == {0} for b in span_bytes)
        print(f"\n  0x{start:02X}–0x{end-1:02X}  ({end-start} bytes)")
        if diffs:
            print(f"    DIFFERS at: {[f'0x{b:02X}' for b in diffs]}")
            for b in diffs:
                cv = sorted(cad_sets[b])
                pv = sorted(plain_sets[b])
                print(f"      0x{b:02X}  cadeaux={[f'0x{v:02X}' for v in cv]}  "
                      f"plain={[f'0x{v:02X}' for v in pv]}")
        else:
            note = ""
            if all_zero_cad and all_zero_plain:
                note = "  (all zeros in both groups)"
            print(f"    No differences{note}")

    # ── Unique track_type values in plain group ───────────────────────────────
    plain_tracks = sorted(set(track for _, _, _, _, track, _ in plain))
    print(f"\n{'='*70}")
    print(f"track_type values in plain barrels: "
          f"{[f'0x{t:04X}' for t in plain_tracks]}")
    print(f"track_type in cadeaux barrels:      ['0x0002']")

    # ── Breakdown of plain barrels by track_type ─────────────────────────────
    by_track: dict[int, list] = defaultdict(list)
    for entry in plain:
        _, _, _, name, track, _ = entry
        by_track[track].append(name)

    print(f"\nPlain barrel track_type breakdown:")
    for track in sorted(by_track):
        names = sorted(set(by_track[track]))
        print(f"  0x{track:04X}  ({len(by_track[track])} records)  "
              f"RSC names: {names[:8]}{'...' if len(names)>8 else ''}")


if __name__ == "__main__":
    main()
