"""
dump_ambient_csv.py
───────────────────
Reads the binary RSC files for every level, finds all slots matching
WANTED_NAMES, skips entries already in enemy_locations.csv, and prints
ready-to-paste CSV rows for the new ones.

Usage (run from the repo root):
    python tools/dump_ambient_csv.py --levels-dir "C:/path/to/levels"

    # Dump to a file you can review before pasting:
    python tools/dump_ambient_csv.py --levels-dir "C:/path/to/levels" > new_ambients.csv

Columns output match enemy_locations.csv exactly.  Review and fill in:
  - level_region / sub_region  (human-readable area name)
  - context_group / movement_type  (pre-filled with best guess, verify)
  - zone  (read from binary — should be correct)
  - x / y / z  (read from binary — should be correct)

After reviewing, append the rows to data/enemy_locations.csv and re-run:
    python tools/generate_enemies.py
"""

import sys
import os
import csv
import struct
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── RSC constants ─────────────────────────────────────────────────────────────
HEADER_SIZE = 8
RECORD_SIZE = 72
NAME_OFF    = 0x22
NAME_MAXLEN = 30
ZONE_OFF    = 0x11
SAVE_IDX_OFF = 0x1E
XYZ_OFF     = 0x04

# ── RSC names to extract ──────────────────────────────────────────────────────
# Add / remove as needed.  Comparison is case-sensitive (game names vary).
WANTED_NAMES: set[str] = {
    "RSC_FLIES",
    "RSC_flies",
    "RSC_RAT",
    "RSC_BUTTERFLY0",
    "RSC_BUTTERFLY1",
    "RSC_BUTTERFLY2",
    "RSC_EGRET",
    "RSC_egret",
    "RSC_DRAGONFLY",
    "RSC_FISH",
    "RSC_fish",
    "RSC_AP_FISHY",
    "RSC_A2_MUTANT1",
    "RSC_A2_MUTANT2",
    "RSC_A2_MUTANT1_NC",
    "RSC_GATOR",
    "RSC_GATOR_WATER",
    "RSC_gator_water",
    "RSC_D_DEADFISH",
}

# ── Inferred metadata ─────────────────────────────────────────────────────────
# Best-guess context_group per level_id.  Fill in "" for unknowns.
LEVEL_CONTEXT: dict[str, str] = {
    "swampday":  "liveside",
    "swampnit":  "liveside_night",
    "florida":   "liveside",
    "nflorida":  "liveside_night",
    "sacclaim":  "liveside",
    "uground":   "liveside_night",
    "intro":     "liveside",
    "tenement":  "liveside_night",
    "t4ndgad":   "deadside",
    "wastland":  "deadside",
    "deadside":  "deadside",
    "prison":    "liveside_night_interior",
    "salvage":   "liveside",
    "queens":    "liveside_night",
    "ah1cagew":  "asylum",
    "ah2playr":  "asylum",
    "ah3lavad":  "asylum",
    "ah4fogom":  "asylum",
    "as2exper":  "asylum",
    "as3schis":  "asylum",
    "as4dkeng":  "asylum",
    "proflev":   "",   # profiling/debug level — verify before adding
}

# Best-guess movement_type per RSC name (normalized to uppercase for lookup).
NAME_MOVEMENT: dict[str, str] = {
    "RSC_FLIES":        "flying",
    "RSC_flies":        "flying",
    "RSC_BUTTERFLY0":   "flying",
    "RSC_BUTTERFLY1":   "flying",
    "RSC_BUTTERFLY2":   "flying",
    "RSC_EGRET":        "flying",
    "RSC_egret":        "flying",
    "RSC_DRAGONFLY":    "flying",
    "RSC_RAT":          "ground",
    "RSC_FISH":         "swimming",
    "RSC_fish":         "swimming",
    "RSC_AP_FISHY":     "swimming",
    "RSC_D_DEADFISH":   "swimming",
    "RSC_GATOR":        "ground",
    "RSC_GATOR_WATER":  "swimming",
    "RSC_gator_water":  "swimming",
    "RSC_A2_MUTANT1":   "ground",
    "RSC_A2_MUTANT2":   "ground",
    "RSC_A2_MUTANT1_NC":"ground",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Dump new ambient CSV rows from RSC binaries.")
    p.add_argument("--levels-dir", required=True,
                   help="Path to extracted levels directory.")
    p.add_argument("--include-known", action="store_true",
                   help="Include entries already in enemy_locations.csv (for auditing).")
    return p.parse_args()


def _read_name(data: bytes, rec_start: int) -> str | None:
    raw = data[rec_start + NAME_OFF : rec_start + NAME_OFF + NAME_MAXLEN]
    null = raw.find(b"\x00")
    raw  = raw[:null] if null >= 0 else raw
    if not raw:
        return None
    try:
        return raw.decode("ascii").strip() or None
    except UnicodeDecodeError:
        return None


def _read_record_fields(data: bytes, rec_start: int) -> dict:
    x, y, z = struct.unpack_from("<fff", data, rec_start + XYZ_OFF)
    zone     = data[rec_start + ZONE_OFF]
    save_idx = struct.unpack_from(">I", data, rec_start + SAVE_IDX_OFF)[0]
    return {"x": x, "y": y, "z": z, "zone": zone, "save_idx": save_idx}


def load_existing_csv(csv_path: Path) -> set[str]:
    """Return set of 'level_id:source_file:0xNAMEOFF' keys already in the CSV."""
    keys = set()
    if not csv_path.exists():
        return keys
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lid = row["level_id"].strip()
            sf  = row["source_file"].strip()
            try:
                off = int(row["offset"].strip(), 16)
            except ValueError:
                continue
            keys.add(f"{lid}:{sf}:0x{off:04X}")
    return keys


def scan_level(level_dir: Path) -> list[tuple[str, int, str, dict]]:
    """Return [(source_file, name_offset, rsc_name, fields), ...] for wanted names."""
    results = []
    for rsc_path in sorted(level_dir.glob("*.rsc")):
        sf = rsc_path.name
        try:
            data = rsc_path.read_bytes()
        except OSError:
            continue
        n = (len(data) - HEADER_SIZE) // RECORD_SIZE
        for i in range(n):
            rec_start = HEADER_SIZE + i * RECORD_SIZE
            name = _read_name(data, rec_start)
            if name and name in WANTED_NAMES:
                name_off = rec_start + NAME_OFF
                fields   = _read_record_fields(data, rec_start)
                results.append((sf, name_off, name, fields))
    return results


# ── Main ──────────────────────────────────────────────────────────────────────

CSV_COLUMNS = [
    "level_id", "source_file", "offset", "object", "category",
    "level_region", "sub_region", "context_group", "movement_type",
    "save_idx", "is_tracked", "is_verified", "zone", "x", "y", "z", "notes",
]

def main():
    args     = _parse_args()
    levels_dir = Path(args.levels_dir)
    if not levels_dir.is_dir():
        print(f"ERROR: levels dir not found: {levels_dir}", file=sys.stderr)
        sys.exit(1)

    csv_path = Path(__file__).parent.parent / "data" / "enemy_locations.csv"
    existing = load_existing_csv(csv_path)

    writer = csv.DictWriter(sys.stdout, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()

    total_new = 0
    total_skip = 0
    warnings = []

    for level_dir in sorted(d for d in levels_dir.iterdir() if d.is_dir()):
        level_id = level_dir.name
        hits = scan_level(level_dir)
        if not hits:
            continue

        context_group = LEVEL_CONTEXT.get(level_id, "")
        if level_id not in LEVEL_CONTEXT:
            warnings.append(f"  WARNING: unknown level '{level_id}' — context_group left blank")

        for sf, name_off, rsc_name, fields in hits:
            key = f"{level_id}:{sf}:0x{name_off:04X}"
            if key in existing and not args.include_known:
                total_skip += 1
                continue

            movement = NAME_MOVEMENT.get(rsc_name, "")
            row = {
                "level_id":      level_id,
                "source_file":   sf,
                "offset":        f"0x{name_off:04X}",
                "object":        rsc_name,
                "category":      "ambient",
                "level_region":  "",   # fill in manually
                "sub_region":    "",
                "context_group": context_group,
                "movement_type": movement,
                "save_idx":      fields["save_idx"],
                "is_tracked":    "FALSE",
                "is_verified":   "FALSE",
                "zone":          fields["zone"],
                "x":             f"{fields['x']:.4f}",
                "y":             f"{fields['y']:.4f}",
                "z":             f"{fields['z']:.4f}",
                "notes":         "REVIEW" if context_group == "" else "",
            }
            writer.writerow(row)
            total_new += 1

    for w in warnings:
        print(w, file=sys.stderr)
    print(f"\n# New rows: {total_new}   Already in CSV (skipped): {total_skip}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
