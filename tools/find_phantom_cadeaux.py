"""
find_phantom_cadeaux.py — Find CSV cadeaux rows not backed by an RSC record.

Scans specified level directories from the RSC binary (same logic as
count_cadeaux.py), then cross-references against locations.csv.  Reports:

  PHANTOM   — CSV row claims to be a cadeaux but the RSC doesn't confirm it
  MISSING   — RSC has a cadeaux record that has no CSV row

Usage:
    python tools/find_phantom_cadeaux.py --levels-dir "C:/path/to/levels" --check wastland t3swmgad
    python tools/find_phantom_cadeaux.py --levels-dir "..." --check wastland t3swmgad --verbose
"""

import argparse
import csv
import struct
import sys
from pathlib import Path

# ── RSC constants (same as count_cadeaux / backfill_cadeaux) ──────────────────
HEADER_SIZE    = 8
RECORD_SIZE    = 72
NAME_OFF       = 0x22
NAME_MAXLEN    = 30
TRACK_TYPE_OFF = 0x1C
SAVE_IDX_OFF   = 0x1E
ZONE_OFF       = 0x11
XYZ_OFF        = 0x04
TRACK_PERSISTENT = 0x0002

CADEAUX_RSC     = {"RSC_CADEAUX", "RSC_X_CADEAUX", "RSC_PICKUP_CADEAUX"}
BARREL_KEYWORDS = ("BARREL", "CRATE", "PACKBOX")
NON_ITEM_RSC_FILES = {
    "enemies.rsc", "enemy.rsc", "enemys.rsc",
    "audio.rsc", "snd.rsc", "sound.rsc",
    "events.rsc", "aclmlogo.rsc", "day.rsc", "night.rsc",
}


def _read_name(rec: bytes) -> str:
    field = rec[NAME_OFF : NAME_OFF + NAME_MAXLEN]
    null  = field.find(b"\x00")
    raw   = field[:null] if null >= 0 else field
    try:
        return raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return raw.decode("latin-1").strip()


def scan_rsc(path: Path) -> dict[int, dict]:
    """Return {name_offset: record_info} for every cadeaux in the file."""
    try:
        data = path.read_bytes()
    except OSError:
        return {}
    if len(data) < HEADER_SIZE + RECORD_SIZE:
        return {}

    n_slots          = (len(data) - HEADER_SIZE) // RECORD_SIZE
    check_persistent = path.name not in NON_ITEM_RSC_FILES
    results          = {}

    for i in range(n_slots):
        off = HEADER_SIZE + i * RECORD_SIZE
        rec = data[off : off + RECORD_SIZE]
        if len(rec) < RECORD_SIZE or bytes(rec) == bytes(RECORD_SIZE):
            continue

        track    = struct.unpack(">H", rec[TRACK_TYPE_OFF : TRACK_TYPE_OFF + 2])[0]
        save_idx = struct.unpack(">I", rec[SAVE_IDX_OFF   : SAVE_IDX_OFF   + 4])[0]
        name     = _read_name(rec)

        is_barrel           = any(k in name for k in BARREL_KEYWORDS)
        is_explicit_cadeaux = name in CADEAUX_RSC
        is_persistent_barrel = check_persistent and track == TRACK_PERSISTENT and is_barrel

        if is_explicit_cadeaux or is_persistent_barrel:
            name_offset = off + NAME_OFF
            results[name_offset] = {
                "source_file": path.name,
                "offset":      name_offset,
                "object":      name,
                "track_type":  track,
                "save_idx":    save_idx,
            }

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    ALL_LEVEL_IDS = [
        "swampday", "swampnit", "tenement", "ntenemnt", "prison", "nprison",
        "uground", "nuground", "florida", "nflorida", "salvage", "nsalvage",
        "deadside", "wastland", "asylum", "as2exper", "as3schis", "as4dkeng",
        "t1tchgad", "ah1cagew", "ah2playr", "t2wlkgad", "ah3lavad", "t3swmgad",
        "ah4fogom", "t4ndgad",
    ]

    parser.add_argument("--levels-dir", required=True)
    parser.add_argument("--csv", default="data/locations.csv")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", nargs="+",
                        help="Level dir names to check (e.g. wastland t3swmgad)")
    group.add_argument("--check-all", action="store_true",
                        help="Check every known level")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    levels_dir   = Path(args.levels_dir)
    csv_path     = Path(args.csv)
    check_levels = ALL_LEVEL_IDS if args.check_all else args.check

    # ── Scan RSC binary for each requested level ─────────────────────────────
    rsc_found: dict[tuple, dict] = {}   # (level_id, source_file, name_offset) → info
    for level_id in check_levels:
        level_dir = levels_dir / level_id
        if not level_dir.is_dir():
            print(f"WARNING: {level_dir} not found, skipping", file=sys.stderr)
            continue
        for rsc_path in sorted(level_dir.glob("*.rsc")):
            for name_offset, info in scan_rsc(rsc_path).items():
                key = (level_id, rsc_path.name, name_offset)
                rsc_found[key] = info

    # ── Load CSV cadeaux rows for those levels ───────────────────────────────
    csv_rows: list[dict] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("category", "").strip().lower() != "cadeaux":
                continue
            if row["level_id"] not in check_levels:
                continue
            try:
                raw = row["offset"].strip()
                off = int(raw, 16) if raw.startswith("0x") else int(raw)
            except (ValueError, KeyError):
                off = -1
            csv_rows.append({**row, "_offset_int": off})

    # ── Cross-reference ──────────────────────────────────────────────────────
    csv_keys = set()
    phantoms = []
    for row in csv_rows:
        key = (row["level_id"], row.get("source_file", ""), row["_offset_int"])
        csv_keys.add(key)
        if key not in rsc_found:
            phantoms.append(row)

    missing = [info for key, info in rsc_found.items() if key not in csv_keys]

    # ── Report ───────────────────────────────────────────────────────────────
    print(f"Levels checked: {check_levels}")
    print(f"RSC cadeaux records found:  {len(rsc_found)}")
    print(f"CSV cadeaux rows:           {len(csv_rows)}")
    print()

    if phantoms:
        print(f"PHANTOM CSV rows ({len(phantoms)}) — in CSV but not in RSC scan:")
        for row in phantoms:
            print(f"  {row['level_id']:<12} {row.get('source_file',''):<20} "
                  f"{row['offset']:<10} {row.get('object',''):<25} "
                  f"track={row.get('track_type','')}  save_idx={row.get('save_idx','')}")
    else:
        print("No phantom CSV rows found.")

    print()

    if missing:
        print(f"MISSING from CSV ({len(missing)}) — in RSC but not in CSV:")
        for info in sorted(missing, key=lambda x: (x["source_file"], x["offset"])):
            print(f"  {info['source_file']:<20} 0x{info['offset']:04X}  "
                  f"{info['object']:<25} track=0x{info['track_type']:04X}  "
                  f"save_idx={info['save_idx']}")
    else:
        print("No missing RSC records.")

    if args.verbose:
        print("\nAll RSC records found:")
        for (lid, sf, off), info in sorted(rsc_found.items()):
            matched = "✓" if (lid, sf, off) in csv_keys else "✗ MISSING"
            print(f"  {lid:<12} {sf:<20} 0x{off:04X}  {info['object']:<25} {matched}")


if __name__ == "__main__":
    main()
