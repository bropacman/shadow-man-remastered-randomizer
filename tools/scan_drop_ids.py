"""
scan_drop_ids.py — Find RSC records with non-zero save_idx outside cadeaux.

Scans every RSC file across all levels and reports records that have a
save_idx != 0 but are NOT persistent cadeaux (track_type != 0x0002).
These are the "drop ID" carriers — govi, true forms, and any other entity
whose collection is tracked in the save profile.

Cross-references against data/locations.csv and data/enemy_locations.csv
so you can see which are already mapped and which are new.

Usage:
    python tools/scan_drop_ids.py --levels-dir "C:/path/to/levels"
    python tools/scan_drop_ids.py --levels-dir "..." --show-mapped
    python tools/scan_drop_ids.py --levels-dir "..." --rsc-filter enemies.rsc instance.rsc
"""

import argparse
import csv
import struct
from collections import defaultdict
from pathlib import Path

HEADER_SIZE    = 8
RECORD_SIZE    = 72
NAME_OFF       = 0x22
NAME_MAXLEN    = 30
TRACK_TYPE_OFF = 0x1C
SAVE_IDX_OFF   = 0x1E
TRACK_PERSISTENT = 0x0002


def _read_name(rec: bytes) -> str:
    raw = rec[NAME_OFF : NAME_OFF + NAME_MAXLEN]
    null = raw.find(b"\x00")
    raw = raw[:null] if null >= 0 else raw
    try:
        return raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return raw.decode("latin-1").strip()


def scan_all(levels_dir: Path, rsc_filter: set[str] | None) -> list[dict]:
    results = []
    for level_dir in sorted(levels_dir.iterdir()):
        if not level_dir.is_dir():
            continue
        for rsc_path in sorted(level_dir.glob("*.rsc")):
            if rsc_filter and rsc_path.name not in rsc_filter:
                continue
            try:
                data = rsc_path.read_bytes()
            except OSError:
                continue
            if len(data) < HEADER_SIZE + RECORD_SIZE:
                continue
            # Scan all record slots. The RSC_ name filter below discards non-entity
            # records (physics objects, triggers, etc.) whose float bytes would
            # otherwise read as garbage names/track_types/save_idx values.
            n = (len(data) - HEADER_SIZE) // RECORD_SIZE
            for i in range(n):
                off = HEADER_SIZE + i * RECORD_SIZE
                rec = data[off : off + RECORD_SIZE]
                if len(rec) < RECORD_SIZE or bytes(rec) == bytes(RECORD_SIZE):
                    continue
                name     = _read_name(rec)
                # Skip non-RSC records (physics triggers, spawn points, etc.)
                # whose float bytes land in the name/track/save_idx fields as garbage.
                if not name.startswith("RSC_"):
                    continue
                track    = struct.unpack(">H", rec[TRACK_TYPE_OFF : TRACK_TYPE_OFF + 2])[0]
                save_idx = struct.unpack(">I", rec[SAVE_IDX_OFF   : SAVE_IDX_OFF   + 4])[0]
                if save_idx == 0 or track == TRACK_PERSISTENT:
                    continue
                name_off = off + NAME_OFF
                results.append({
                    "level_id":    level_dir.name,
                    "source_file": rsc_path.name,
                    "offset":      name_off,
                    "object":      name,
                    "track_type":  track,
                    "save_idx":    save_idx,
                })
    return results


def load_mapped(csv_paths: list[Path]) -> set[tuple]:
    """Return set of (level_id, source_file, offset_int) already in our CSVs."""
    mapped = set()
    for p in csv_paths:
        if not p.exists():
            continue
        with p.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    raw = row["offset"].strip()
                    off = int(raw, 16) if raw.startswith("0x") else int(raw)
                except (ValueError, KeyError):
                    continue
                mapped.add((row["level_id"], row.get("source_file", ""), off))
    return mapped


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--levels-dir", required=True)
    parser.add_argument("--csv",        default="data/locations.csv")
    parser.add_argument("--enemy-csv",  default="data/enemy_locations.csv")
    parser.add_argument("--show-mapped", action="store_true",
                        help="Also show records already in the CSV (default: only NEW)")
    parser.add_argument("--rsc-filter", nargs="*", default=None,
                        metavar="FILE",
                        help="Only scan these RSC filenames (e.g. enemies.rsc instance.rsc). "
                             "Default: scan all RSC files.")
    args = parser.parse_args()

    levels_dir  = Path(args.levels_dir)
    rsc_filter  = set(args.rsc_filter) if args.rsc_filter else None

    print("Scanning RSC files for tracked non-cadeaux records...")
    records = scan_all(levels_dir, rsc_filter)
    print(f"Found {len(records)} record(s) with save_idx != 0 and track_type != 0x0002\n")

    mapped = load_mapped([Path(args.csv), Path(args.enemy_csv)])

    new_records  = [r for r in records if (r["level_id"], r["source_file"], r["offset"]) not in mapped]
    old_records  = [r for r in records if (r["level_id"], r["source_file"], r["offset"])     in mapped]

    # ── Summary by object name ────────────────────────────────────────────────
    by_name: dict[str, list] = defaultdict(list)
    for r in records:
        by_name[r["object"]].append(r)

    print("=== By RSC object name ===")
    for name, recs in sorted(by_name.items(), key=lambda x: -len(x[1])):
        n_new    = sum(1 for r in recs if (r["level_id"], r["source_file"], r["offset"]) not in mapped)
        tracks   = sorted(set(f"0x{r['track_type']:04X}" for r in recs))
        print(f"  {name:<30} count={len(recs):>4}  new={n_new:>4}  "
              f"track_types={tracks}")

    # ── New records detail ────────────────────────────────────────────────────
    if new_records:
        print(f"\n=== NEW (not in CSV): {len(new_records)} records ===")
        for r in sorted(new_records, key=lambda x: (x["level_id"], x["source_file"], x["offset"])):
            print(f"  {r['level_id']:<12} {r['source_file']:<20} "
                  f"0x{r['offset']:04X}  {r['object']:<30} "
                  f"track=0x{r['track_type']:04X}  save_idx={r['save_idx']}")
    else:
        print("\nNo new records — everything with save_idx != 0 is already mapped.")

    # ── Already-mapped detail (optional) ─────────────────────────────────────
    if args.show_mapped and old_records:
        print(f"\n=== ALREADY MAPPED: {len(old_records)} records ===")
        for r in sorted(old_records, key=lambda x: (x["level_id"], x["source_file"], x["offset"])):
            print(f"  {r['level_id']:<12} {r['source_file']:<20} "
                  f"0x{r['offset']:04X}  {r['object']:<30} "
                  f"track=0x{r['track_type']:04X}  save_idx={r['save_idx']}")


if __name__ == "__main__":
    main()
