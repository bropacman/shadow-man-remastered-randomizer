"""
find_phantom_locations.py — Find CSV item rows not backed by an actual RSC record.

Like find_phantom_cadeaux.py but covers ALL item categories (barrel, cadeaux,
progression, weapon, etc.), not just cadeaux.  Useful for catching bad offsets
introduced during extraction or editing.

Scans the RSC binary files for a given set of levels, builds an index of every
non-empty record offset, then cross-references every CSV row for those levels.

Reports:
  PHANTOM  — CSV row has an offset that doesn't exist in the RSC binary
  (MISSING is not reported here since not every RSC record is meant to be in the CSV)

Usage:
    python tools/find_phantom_locations.py --levels-dir "C:/path/to/levels" --check prison
    python tools/find_phantom_locations.py --levels-dir "..." --check prison wastland --category barrel
    python tools/find_phantom_locations.py --levels-dir "..." --check-all
"""

import argparse
import csv
import struct
import sys
from pathlib import Path

# ── RSC constants ─────────────────────────────────────────────────────────────
HEADER_SIZE    = 8
RECORD_SIZE    = 72
NAME_OFF       = 0x22
NAME_MAXLEN    = 30

NON_ITEM_RSC_FILES = {
    "enemies.rsc", "enemy.rsc", "enemys.rsc",
    "audio.rsc", "snd.rsc", "sound.rsc",
    "events.rsc", "aclmlogo.rsc", "day.rsc", "night.rsc",
}

# All level_id values known from locations.csv
ALL_LEVEL_IDS = [
    "swampday", "swampnit", "tenement", "ntenemnt", "prison", "nprison",
    "uground", "nuground", "florida", "nflorida", "salvage", "nsalvage",
    "deadside", "wastland", "asylum", "as2exper", "as3schis", "as4dkeng",
    "t1tchgad", "ah1cagew", "ah2playr", "t2wlkgad", "ah3lavad", "t3swmgad",
    "ah4fogom", "t4ndgad",
]


def scan_rsc_offsets(path: Path) -> set[int]:
    """Return the set of record start offsets that contain a non-empty record."""
    try:
        data = path.read_bytes()
    except OSError:
        return set()
    if len(data) < HEADER_SIZE + RECORD_SIZE:
        return set()

    offsets: set[int] = set()
    n_slots = (len(data) - HEADER_SIZE) // RECORD_SIZE
    for i in range(n_slots):
        off = HEADER_SIZE + i * RECORD_SIZE
        rec = data[off : off + RECORD_SIZE]
        if len(rec) < RECORD_SIZE:
            continue
        if bytes(rec) == bytes(RECORD_SIZE):
            continue  # all-zero = empty slot
        offsets.add(off + NAME_OFF)   # CSV stores name-field offset, not record start
    return offsets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--levels-dir", required=True,
                        help="Path to the unpacked levels directory")
    parser.add_argument("--csv", default="data/locations.csv")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", nargs="+", metavar="LEVEL_ID",
                       help="Level dir name(s) to check (e.g. prison wastland)")
    group.add_argument("--check-all", action="store_true",
                       help="Check every known level")
    parser.add_argument("--category", nargs="*", default=None,
                        help="Filter CSV rows by category (e.g. barrel cadeaux). "
                             "Omit to check all categories.")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    levels_dir  = Path(args.levels_dir)
    csv_path    = Path(args.csv)
    check_levels = ALL_LEVEL_IDS if args.check_all else args.check
    cat_filter   = {c.lower() for c in args.category} if args.category else None

    # ── Build offset index from RSC binaries ──────────────────────────────────
    # (level_id, source_file) → set of valid record offsets
    rsc_index: dict[tuple[str, str], set[int]] = {}
    for level_id in check_levels:
        level_dir = levels_dir / level_id
        if not level_dir.is_dir():
            print(f"WARNING: {level_dir} not found, skipping", file=sys.stderr)
            continue
        for rsc_path in sorted(level_dir.glob("*.rsc")):
            if rsc_path.name in NON_ITEM_RSC_FILES:
                continue
            offsets = scan_rsc_offsets(rsc_path)
            if offsets:
                rsc_index[(level_id, rsc_path.name)] = offsets

    # ── Load CSV rows for checked levels ──────────────────────────────────────
    csv_rows: list[dict] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["level_id"] not in check_levels:
                continue
            cat = row.get("category", "").strip().lower()
            if cat_filter and cat not in cat_filter:
                continue
            try:
                raw = row["offset"].strip()
                off = int(raw, 16) if raw.startswith("0x") else int(raw)
            except (ValueError, KeyError):
                off = -1
            csv_rows.append({**row, "_offset_int": off})

    # ── Cross-reference ───────────────────────────────────────────────────────
    phantoms: list[dict] = []
    for row in csv_rows:
        key = (row["level_id"], row.get("source_file", ""))
        valid_offsets = rsc_index.get(key, set())
        if row["_offset_int"] not in valid_offsets:
            phantoms.append(row)

    # ── Report ────────────────────────────────────────────────────────────────
    cat_label = f"category={args.category}" if cat_filter else "all categories"
    print(f"Levels checked : {check_levels}")
    print(f"Category filter: {cat_label}")
    print(f"CSV rows checked: {len(csv_rows)}")
    print()

    if phantoms:
        print(f"PHANTOM rows ({len(phantoms)}) — offset not found in RSC binary:")
        for row in phantoms:
            print(f"  {row['level_id']:<12} {row.get('source_file',''):<20} "
                  f"{row['offset']:<10} {row.get('object',''):<25} "
                  f"cat={row.get('category',''):<12} "
                  f"verified={row.get('is_verified','')}")
    else:
        print("No phantom rows found.")

    if args.verbose and phantoms:
        print()
        print("Tip: remove these rows from data/locations.csv and extracted_locations.py,")
        print("     or set is_verified=FALSE if you want to keep them as placeholders.")


if __name__ == "__main__":
    main()
