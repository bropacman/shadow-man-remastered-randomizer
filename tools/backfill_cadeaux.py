"""
backfill_cadeaux.py — Populate track_type in locations.csv and add missing cadeaux rows.

Does two things in one pass:

  1. BACKFILL track_type
     For every existing CSV row whose track_type cell is empty, reads the
     actual TrackType value from the RSC binary at the row's (level_id,
     source_file, offset) and writes it as a hex string ("0x0002", etc.).

  2. ADD MISSING CADEAUX
     Finds every RSC record that is a cadeaux (explicit type OR barrel with
     TrackType=0x0002) but has no matching row in the CSV, and appends new
     rows.  XYZ coordinates and zone are read directly from the binary.
     New rows are marked is_tracked=TRUE, is_verified=FALSE.

Usage:
    python tools/backfill_cadeaux.py --levels-dir "C:/path/to/levels"
    python tools/backfill_cadeaux.py --levels-dir "..." --apply   # write CSV
"""

import argparse
import csv
import struct
import sys
from pathlib import Path
from collections import defaultdict

# ── RSC format constants ──────────────────────────────────────────────────────
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

# ── Level directory → display name (for level_region in new rows) ─────────────
LEVEL_REGION_MAP = {
    "swampday":  "Louisiana Swampland",
    "swampnit":  "Louisiana Swampland",
    "tenement":  "New York Tenement",
    "prison":    "Texas Prison",
    "uground":   "London Underground",
    "florida":   "Florida Summer Camp",
    "salvage":   "Mojave Desert Salvage Yard",
    "t4ndgad":   "Mojave Desert Salvage Yard",   # sub-zone of salvage
    "deadside":  "Deadside Marrow Gates",
    "wastland":  "Deadside Wasteland",
    "asylum":    "Asylum Station 1",
    "as2exper":  "Asylum Station 2",
    "as3schis":  "Asylum Station 3",
    "as4dkeng":  "Asylum Station 4",
    "t1tchgad":  "Gad Temple 1",
    "ah1cagew":  "Asylum Hub 1",
    "ah2playr":  "Asylum Hub 2",
    "t2wlkgad":  "Gad Temple 2",
    "ah3lavad":  "Asylum Hub 3",
    "t3swmgad":  "Gad Temple 3",
    "ah4fogom":  "Asylum Hub 4",
}

CSV_COLUMNS = [
    "level_id", "source_file", "friendly_name", "offset", "object",
    "track_type", "category", "save_idx",
    "is_tracked", "is_verified", "zone",
    "level_region", "sub_region",
    "x", "y", "z", "notes",
]


# ── RSC binary helpers ────────────────────────────────────────────────────────

def _read_name(rec: bytes) -> str:
    field = rec[NAME_OFF : NAME_OFF + NAME_MAXLEN]
    null  = field.find(b"\x00")
    raw   = field[:null] if null >= 0 else field
    try:
        return raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return raw.decode("latin-1").strip()


def scan_rsc(path: Path) -> list[dict]:
    """Return one dict per cadeaux record in the file."""
    try:
        data = path.read_bytes()
    except OSError:
        return []

    if len(data) < HEADER_SIZE + RECORD_SIZE:
        return []

    n_slots          = (len(data) - HEADER_SIZE) // RECORD_SIZE
    check_persistent = path.name not in NON_ITEM_RSC_FILES
    results          = []

    for i in range(n_slots):
        off = HEADER_SIZE + i * RECORD_SIZE
        rec = data[off : off + RECORD_SIZE]
        if len(rec) < RECORD_SIZE or bytes(rec) == bytes(RECORD_SIZE):
            continue

        track    = struct.unpack(">H", rec[TRACK_TYPE_OFF : TRACK_TYPE_OFF + 2])[0]
        save_idx = struct.unpack(">I", rec[SAVE_IDX_OFF   : SAVE_IDX_OFF   + 4])[0]
        name     = _read_name(rec)
        x, y, z  = struct.unpack("<fff", rec[XYZ_OFF : XYZ_OFF + 12])
        zone     = rec[ZONE_OFF]

        is_barrel           = any(k in name for k in BARREL_KEYWORDS)
        is_explicit_cadeaux = name in CADEAUX_RSC
        is_persistent_barrel = check_persistent and track == TRACK_PERSISTENT and is_barrel

        if is_explicit_cadeaux or is_persistent_barrel:
            results.append({
                "level_id":   path.parent.name,
                "source_file": path.name,
                "offset":     off + NAME_OFF,   # file offset of the name field
                "object":     name,
                "track_type": track,
                "save_idx":   save_idx,
                "zone":       zone,
                "x": x, "y": y, "z": z,
            })

    return results


def read_track_for_offset(path: Path, name_offset: int) -> int | None:
    """Read the 2-byte TrackType from a record whose name is at name_offset."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    rec_start = name_offset - NAME_OFF
    if rec_start < 0 or rec_start + RECORD_SIZE > len(data):
        return None
    rec = data[rec_start : rec_start + RECORD_SIZE]
    return struct.unpack(">H", rec[TRACK_TYPE_OFF : TRACK_TYPE_OFF + 2])[0]


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--levels-dir", required=True,
                        help="Path to the extracted levels directory")
    parser.add_argument("--csv",  default="data/locations.csv",
                        help="Path to locations.csv (default: data/locations.csv)")
    parser.add_argument("--apply", action="store_true",
                        help="Write the updated CSV (dry-run by default)")
    args = parser.parse_args()

    levels_dir = Path(args.levels_dir)
    csv_path   = Path(args.csv)

    if not levels_dir.is_dir():
        print(f"ERROR: levels dir not found: {levels_dir}", file=sys.stderr)
        sys.exit(1)
    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        sys.exit(1)

    # ── Load existing CSV ────────────────────────────────────────────────────
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader   = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows     = list(reader)

    # Ensure track_type column exists in fieldnames
    if "track_type" not in fieldnames:
        # Insert after "object"
        try:
            idx = fieldnames.index("object") + 1
        except ValueError:
            idx = len(fieldnames)
        fieldnames.insert(idx, "track_type")

    # Build lookup: (level_id, source_file, offset_int) → row index
    existing: dict[tuple, int] = {}
    for i, row in enumerate(rows):
        try:
            off = int(row["offset"], 16) if row["offset"].startswith("0x") else int(row["offset"])
        except (ValueError, KeyError):
            continue
        key = (row.get("level_id", ""), row.get("source_file", ""), off)
        existing[key] = i

    # ── Scan all RSC files ───────────────────────────────────────────────────
    all_found: list[dict] = []
    for level_dir in sorted(levels_dir.iterdir()):
        if not level_dir.is_dir():
            continue
        for rsc_path in sorted(level_dir.glob("*.rsc")):
            all_found.extend(scan_rsc(rsc_path))

    # ── Step 1: backfill track_type for existing rows ────────────────────────
    backfill_count = 0
    rsc_cache: dict[tuple, bytes] = {}

    for i, row in enumerate(rows):
        if row.get("track_type", "").strip():
            continue   # already has a value

        level_id    = row.get("level_id", "")
        source_file = row.get("source_file", "")
        try:
            off = int(row["offset"], 16) if row["offset"].startswith("0x") else int(row["offset"])
        except (ValueError, KeyError):
            continue

        rsc_path = levels_dir / level_id / source_file
        track    = read_track_for_offset(rsc_path, off)
        if track is not None:
            rows[i]["track_type"] = f"0x{track:04X}"
            backfill_count += 1

    # ── Step 2: find missing cadeaux entries ─────────────────────────────────
    new_rows: list[dict] = []
    for rec in all_found:
        key = (rec["level_id"], rec["source_file"], rec["offset"])
        if key in existing:
            continue   # already in table

        level_region = LEVEL_REGION_MAP.get(rec["level_id"], rec["level_id"])

        new_row = {col: "" for col in fieldnames}
        new_row.update({
            "level_id":    rec["level_id"],
            "source_file": rec["source_file"],
            "friendly_name": "Cadeaux",
            "offset":      f"0x{rec['offset']:04X}",
            "object":      rec["object"],
            "track_type":  f"0x{rec['track_type']:04X}",
            "category":    "cadeaux",
            "save_idx":    str(rec["save_idx"]) if rec["save_idx"] else "0",
            "is_tracked":  "TRUE",
            "is_verified": "FALSE",
            "zone":        str(rec["zone"]),
            "level_region": level_region,
            "sub_region":  "N",
            "x":           f"{rec['x']:.2f}",
            "y":           f"{rec['y']:.2f}",
            "z":           f"{rec['z']:.2f}",
            "notes":       "",
        })
        new_rows.append(new_row)

    # ── Report ───────────────────────────────────────────────────────────────
    print(f"Existing rows:          {len(rows)}")
    print(f"Track_type backfilled:  {backfill_count}")
    print(f"Missing cadeaux found:  {len(new_rows)}")

    if new_rows:
        by_level: dict[str, list] = defaultdict(list)
        for r in new_rows:
            by_level[r["level_id"]].append(r)
        print("\nMissing by level:")
        for lvl in sorted(by_level):
            print(f"  {lvl}: {len(by_level[lvl])}")
            for r in by_level[lvl]:
                print(f"    {r['source_file']} {r['offset']}  "
                      f"track={r['track_type']}  save_idx={r['save_idx']}  {r['object']}")

    if not args.apply:
        print("\n(Dry run — pass --apply to write changes)")
        return

    # ── Write CSV ────────────────────────────────────────────────────────────
    all_rows = rows + new_rows
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n✓ Wrote {len(all_rows)} rows → {csv_path}")


if __name__ == "__main__":
    main()
