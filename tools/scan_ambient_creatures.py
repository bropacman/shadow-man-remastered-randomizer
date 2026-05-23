"""
scan_ambient_creatures.py
─────────────────────────
Scans every enemies.rsc / enemys.rsc in an extracted levels directory and
reports all RSC_ names found, grouped by level — with special attention to
names that look like ambient creatures but are NOT yet in enemy_locations.csv.

Usage (run from the repo root):
    python tools/scan_ambient_creatures.py --levels-dir "C:/path/to/levels"

The "levels dir" is the extracted levels folder the randomizer works from
(e.g. the work_dir/levels path, or the game's levels/ folder directly).

Output sections:
  1. Per-level table of every distinct RSC_ name in the source files, tagged:
       [ambient]  — already in enemy_locations.csv as category=ambient
       [enemy]    — already in enemy_locations.csv as category=enemy/boss
       [NEW AMB?] — looks like an ambient creature but NOT in the CSV
       [NEW?]     — not in CSV at all, unknown category
  2. Summary: new-looking ambient names across all levels, sorted by frequency.
"""

import sys
import os
import argparse
import csv
import struct
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── RSC constants (mirrors rsc_utils.py) ─────────────────────────────────────
HEADER_SIZE = 8
RECORD_SIZE = 72
NAME_OFF    = 0x22
NAME_MAXLEN = 30

# RSC_ name fragments that suggest ambient/wildlife (case-insensitive)
AMBIENT_HINTS = {
    "butterfly", "egret", "dragonfly", "flies", "fish", "rat",
    "bird", "crow", "pigeon", "seagull", "gull", "gator", "croc",
    "frog", "snake", "rabbit", "cat", "bat", "moth", "firefly",
    "mosquito", "bee", "wasp", "ant", "beetle", "locust",
    "worm",   # could go either way — check manually
    "bug",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scan ambient creature RSC records.")
    p.add_argument("--levels-dir", required=True,
                   help="Path to extracted levels directory.")
    p.add_argument("--all", action="store_true",
                   help="Show ALL RSC names, not just ambient/unknown ones.")
    return p.parse_args()


def _read_name(rec: bytes) -> str | None:
    raw = rec[NAME_OFF : NAME_OFF + NAME_MAXLEN]
    null = raw.find(b"\x00")
    raw  = raw[:null] if null >= 0 else raw
    if not raw:
        return None
    try:
        name = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    return name if name.startswith("RSC_") else None


def read_rsc_names(path: Path) -> list[tuple[int, str]]:
    """Return [(name_offset, rsc_name), ...] for every named record in the file.

    name_offset = record_start + NAME_OFF — this matches the offset convention
    used in enemy_locations.csv, so lookups against the CSV will be correct.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return []
    results = []
    n = (len(data) - HEADER_SIZE) // RECORD_SIZE
    for i in range(n):
        rec_start = HEADER_SIZE + i * RECORD_SIZE
        rec = data[rec_start : rec_start + RECORD_SIZE]
        name = _read_name(rec)
        if name:
            results.append((rec_start + NAME_OFF, name))
    return results


def load_csv(csv_path: Path) -> dict[str, dict[str, str]]:
    """
    Returns {  "level_id:source_file:0xOFFSET": row_dict  }
    Also builds a secondary set of (level_id, rsc_name) pairs for quick lookup.
    """
    rows = {}
    if not csv_path.exists():
        return rows
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            lid = row["level_id"].strip()
            sf  = row["source_file"].strip()
            try:
                off = int(row["offset"].strip(), 16)
            except ValueError:
                continue
            key = f"{lid}:{sf}:0x{off:04X}"
            rows[key] = row
    return rows


def looks_like_ambient(name: str) -> bool:
    low = name.lower()
    return any(hint in low for hint in AMBIENT_HINTS)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = _parse_args()
    levels_dir = Path(args.levels_dir)
    if not levels_dir.is_dir():
        print(f"ERROR: levels dir not found: {levels_dir}")
        sys.exit(1)

    csv_path = Path(__file__).parent.parent / "data" / "enemy_locations.csv"
    csv_rows = load_csv(csv_path)

    # Build lookup: (level_id, source_file, offset) → category
    known: dict[tuple[str, str, int], str] = {}
    for key, row in csv_rows.items():
        parts = key.split(":")
        if len(parts) == 3:
            lid, sf, off_str = parts
            try:
                known[(lid, sf, int(off_str, 16))] = row["category"].strip()
            except ValueError:
                pass

    # Also: set of all known (level_id, rsc_name) → categories for that name
    known_names: dict[str, set[str]] = defaultdict(set)
    for key, row in csv_rows.items():
        known_names[row["object"].strip()].add(row["category"].strip())

    level_dirs = sorted(d for d in levels_dir.iterdir() if d.is_dir())

    # new_ambients: name → list of (level_id, source_file, offset)
    new_ambients: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    new_unknown:  dict[str, list[tuple[str, str, int]]] = defaultdict(list)

    print()
    print("=" * 80)
    print(f"  AMBIENT CREATURE SCAN  —  {levels_dir}")
    print("=" * 80)

    for level_dir in level_dirs:
        level_id = level_dir.name
        level_records: list[tuple[str, int, str, str]] = []  # (source_file, offset, name, tag)

        for rsc_path in sorted(level_dir.glob("*.rsc")):
            sf = rsc_path.name
            for off, name in read_rsc_names(rsc_path):
                cat = known.get((level_id, sf, off))
                if cat:
                    tag = f"[{cat}]"
                elif name in known_names and known_names[name]:
                    cats = known_names[name]
                    if any("ambient" in c for c in cats):
                        tag = "[NEW AMB?]"  # name known as ambient elsewhere, new location
                        new_ambients[name].append((level_id, sf, off))
                    else:
                        tag = "[NEW?]"
                        if looks_like_ambient(name):
                            new_ambients[name].append((level_id, sf, off))
                elif looks_like_ambient(name):
                    tag = "[NEW AMB?]"
                    new_ambients[name].append((level_id, sf, off))
                else:
                    tag = "[NEW?]"
                    new_unknown[name].append((level_id, sf, off))
                level_records.append((sf, off, name, tag))

        if not level_records:
            continue

        # Filter display unless --all
        display = level_records if args.all else [
            r for r in level_records
            if "NEW" in r[3] or "ambient" in r[3]
        ]
        if not display:
            continue

        print(f"\n  ── {level_id} {'─' * max(0, 60 - len(level_id))}")
        print(f"    {'FILE':<14} {'OFFSET':>8}  {'NAME':<38} TAG")
        print(f"    {'-'*14} {'-'*8}  {'-'*38} {'-'*12}")
        for sf, off, name, tag in sorted(display, key=lambda r: (r[0], r[1])):
            print(f"    {sf:<14} 0x{off:04X}    {name:<38} {tag}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 80)
    print("  NEW AMBIENT CANDIDATES  (not yet in enemy_locations.csv)")
    print("=" * 80)
    if new_ambients:
        sorted_amb = sorted(new_ambients.items(), key=lambda kv: -len(kv[1]))
        for name, locs in sorted_amb:
            print(f"\n  {name}  ({len(locs)} slot(s))")
            for lid, sf, off in sorted(locs):
                print(f"    {lid:<16} {sf:<14} 0x{off:04X}")
    else:
        print("  (none found — all ambient-looking names are already in the CSV)")

    print()
    print("=" * 80)
    print("  OTHER UNKNOWN NAMES  (not in CSV, not obviously ambient)")
    print("  — review manually for anything that could be wildlife")
    print("=" * 80)
    if new_unknown:
        # Only show names that appear in files we'd expect ambients in
        shown = new_unknown
        if shown:
            for name, locs in sorted(shown.items(), key=lambda kv: -len(kv[1])):
                levels_hit = sorted({lid for lid, _, _ in locs})
                print(f"  {name:<40}  {len(locs):>3}x  in: {', '.join(levels_hit)}")
        else:
            print("  (none in enemies.rsc / enemys.rsc)")
    print()


if __name__ == "__main__":
    main()
