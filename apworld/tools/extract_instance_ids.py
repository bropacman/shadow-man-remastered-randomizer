"""
extract_instance_ids.py
=======================
One-time tool: reads the Shadow Man Remastered KPF archives from the game
directory, parses every key RSC file, and fills in the `save_idx` column
for all locations that currently have `save_idx=0`.

NOTE (2026-07-18): locations.csv was migrated from an `instance_id` column
to `save_idx` (see __init__.py / fill.py / patcher.py, all updated to match).
This tool's CSV read/write logic was NOT updated at the time — it still
matched against a column that no longer exists, so --update-csv silently
did nothing useful post-migration even though the RSC parsing itself was
correct. Fixed below to match against `save_idx`.

Usage:
    python extract_instance_ids.py --game-dir "C:/Steam/steamapps/common/Shadow Man Remastered"

Output:
    Prints a mapping of  level:source_file:offset -> save_idx
    and optionally updates  data/locations.csv  in the randomizer repo.

Writes:
    worlds/shadowman/data/instance_ids.json   (level -> source_file -> offset -> id)
    (Use --update-csv to also patch locations.csv directly.)
"""

from __future__ import annotations

import argparse
import csv
import json
import struct
import zipfile
from pathlib import Path
from typing import Dict, Optional

# ── RSC format constants (must match patcher.py) ──────────────────────────────

HEADER_SIZE  = 8      # "Erscv002" file magic
RECORD_SIZE  = 72     # every record is exactly 72 bytes
NAME_OFF     = 0x22   # byte offset within record where RSC_ string begins
SAVE_IDX_OFF = 0x21   # BUG (fixed below): this was treated as a 1-byte field.
                       # It is actually the LAST byte of a 4-byte big-endian
                       # SaveIdx that starts at 0x1E (see patcher.py's
                       # SAVE_IDX_OFF / TECHNICAL.md §10.4). Reading only the
                       # last byte silently truncates any SaveIdx >= 256 down
                       # to (value % 256), and happens to look "correct" for
                       # small values by coincidence. Kept as a named constant
                       # for offset math below; do not read it as a standalone
                       # 1-byte field.
SAVE_IDX_BASE = 0x1E  # start of the real 4-byte big-endian SaveIdx field
NAME_MAXLEN  = 30

# Source files that can contain randomisable locations
RSC_FILES_OF_INTEREST = {"quest.rsc", "instance.rsc", "resource.rsc", "fx.rsc"}


# ── KPF reading ───────────────────────────────────────────────────────────────

def _find_kpf_files(game_dir: Path) -> list[Path]:
    """Return base-game KPF files sorted alphabetically, excluding mods/."""
    return sorted(
        p for p in game_dir.glob("*.kpf")
        if "mods" not in str(p).lower()
    )


def _read_rsc_from_kpf(kpf_files: list[Path], internal_path: str) -> Optional[bytes]:
    """
    Search all KPF archives for `internal_path` and return its raw bytes.
    Later KPF files override earlier ones (same load order as the game).
    """
    result: Optional[bytes] = None
    for kpf_path in kpf_files:
        try:
            with zipfile.ZipFile(str(kpf_path), "r") as zf:
                names = zf.namelist()
                match = next(
                    (n for n in names
                     if n.replace("\\", "/").lower() == internal_path.lower()),
                    None,
                )
                if match:
                    result = zf.read(match)
        except (zipfile.BadZipFile, KeyError):
            continue
    return result


# ── RSC parsing ───────────────────────────────────────────────────────────────

def _parse_instance_ids_from_rsc(data: bytes) -> Dict[int, int]:
    """
    Return {name_offset: instance_id} for every RSC_ record in `data`.
    name_offset is the byte position where the "RSC_" string starts —
    this matches the `offset` column in locations.csv.
    """
    header = data[:8]
    if header not in (b"Erscv002", b"Erscv001"):
        return {}

    result: Dict[int, int] = {}
    body = data[HEADER_SIZE:]
    n = len(body) // RECORD_SIZE

    # Fixed-stride pass (covers quest.rsc / instance.rsc / resource.rsc)
    for i in range(n):
        chunk    = body[i * RECORD_SIZE : (i + 1) * RECORD_SIZE]
        name_raw = chunk[NAME_OFF : NAME_OFF + 4]
        if name_raw.upper() != b"RSC_":
            continue
        offset      = HEADER_SIZE + i * RECORD_SIZE + NAME_OFF
        instance_id = struct.unpack(">I", chunk[SAVE_IDX_BASE:SAVE_IDX_BASE + 4])[0]
        result[offset] = instance_id

    # Fallback scan for variable-stride files (enemies.rsc etc.)
    # Only fills in offsets not already found by fixed-stride.
    import re
    for m in re.finditer(b"RSC_", data):
        name_pos = m.start()
        if name_pos in result:
            continue
        rec_start = name_pos - NAME_OFF
        if rec_start < HEADER_SIZE:
            continue
        if rec_start + SAVE_IDX_BASE + 4 <= len(data):
            result[name_pos] = struct.unpack(
                ">I", data[rec_start + SAVE_IDX_BASE : rec_start + SAVE_IDX_BASE + 4]
            )[0]

    return result


# ── Main logic ────────────────────────────────────────────────────────────────

def extract_instance_ids(game_dir: str) -> Dict[str, Dict[str, Dict[int, int]]]:
    """
    Return nested dict:  level_id -> source_file -> offset (int) -> instance_id (int)
    for every RSC file found in the game archives.
    """
    game_path = Path(game_dir)
    kpf_files = _find_kpf_files(game_path)
    if not kpf_files:
        raise FileNotFoundError(f"No KPF files found in {game_dir}")

    print(f"Found {len(kpf_files)} KPF archive(s):", [p.name for p in kpf_files])

    # Collect all unique (level, source_file) pairs
    seen: set[tuple[str, str]] = set()
    for kpf_path in kpf_files:
        try:
            with zipfile.ZipFile(str(kpf_path), "r") as zf:
                for name in zf.namelist():
                    parts = name.replace("\\", "/").split("/")
                    if len(parts) >= 3 and parts[0].lower() == "levels":
                        level   = parts[1].lower()
                        srcfile = parts[2].lower()
                        if srcfile in RSC_FILES_OF_INTEREST:
                            seen.add((level, srcfile))
        except zipfile.BadZipFile:
            continue

    print(f"Found {len(seen)} (level, source_file) pairs to parse.")

    result: Dict[str, Dict[str, Dict[int, int]]] = {}

    for level, srcfile in sorted(seen):
        internal = f"levels/{level}/{srcfile}"
        data = _read_rsc_from_kpf(kpf_files, internal)
        if data is None:
            continue
        ids = _parse_instance_ids_from_rsc(data)
        if ids:
            result.setdefault(level, {})[srcfile] = ids

    return result


def _load_locations_csv(csv_path: Path) -> list[dict]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_locations_csv(csv_path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0].keys())
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    ap  = argparse.ArgumentParser(description="Extract instance IDs from Shadow Man Remastered KPF files.")
    ap.add_argument("--game-dir", required=True,
                    help='Path to the game directory, e.g. "C:/Steam/.../Shadow Man Remastered"')
    ap.add_argument("--csv",
                    default=str(Path(__file__).parent.parent / "data" / "locations.csv"),
                    help="Path to locations.csv (default: ../data/locations.csv)")
    ap.add_argument("--output-json",
                    default=str(Path(__file__).parent.parent / "data" / "instance_ids.json"),
                    help="Where to write the JSON mapping")
    ap.add_argument("--update-csv", action="store_true",
                    help="Patch locations.csv in-place to fill in save_idx=0 rows")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print what would change without writing anything")
    args = ap.parse_args()

    print("=== Shadow Man – Instance ID Extractor ===\n")
    ids = extract_instance_ids(args.game_dir)

    # Write JSON
    json_path = Path(args.output_json)
    if not args.dry_run:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        # Convert int keys to strings for JSON serialisation
        json_safe = {
            lvl: {
                src: {str(off): iid for off, iid in offsets.items()}
                for src, offsets in srcs.items()
            }
            for lvl, srcs in ids.items()
        }
        json_path.write_text(json.dumps(json_safe, indent=2))
        print(f"\nWrote instance ID map to: {json_path}")

    # Load CSV and report / patch
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"CSV not found at {csv_path} — skipping CSV update")
        return

    rows = _load_locations_csv(csv_path)
    updates: list[tuple[str, str, str, int, int]] = []  # (level, src, offset_str, old, new)

    for row in rows:
        if int(row.get("save_idx") or 0) != 0:
            continue   # already populated
        level   = row.get("level_id", "")
        srcfile = row.get("source_file", "")
        off_str = row.get("offset", "0")
        try:
            offset = int(off_str, 16)
        except ValueError:
            continue
        iid = ids.get(level, {}).get(srcfile, {}).get(offset)
        if iid is None:
            continue
        updates.append((level, srcfile, off_str, 0, iid))
        if args.update_csv and not args.dry_run:
            row["save_idx"] = str(iid)

    print(f"\n{'Would update' if args.dry_run else 'Updated'} {len(updates)} locations:")
    for level, srcfile, offset, _, new_id in sorted(updates):
        print(f"  {level:12s}  {srcfile:16s}  {offset:8s}  →  save_idx={new_id}")

    if args.update_csv and not args.dry_run and updates:
        _write_locations_csv(csv_path, rows)
        print(f"\nPatched {csv_path}")
    elif args.dry_run and updates:
        print("\n(dry run — nothing written)")
    elif args.update_csv and not updates:
        print("\nNo zero-instance_id rows found for the game's RSC files.")


if __name__ == "__main__":
    main()
