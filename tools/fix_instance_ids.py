"""
fix_save_idxs.py — Audit and repair instance_id values in extracted_locations.py
                      and data/locations.csv.

The RSC record stores a 4-byte big-endian save-profile index at bytes 0x1E-0x21.
The original extractor read only byte 0x21 (the last byte), which is wrong for any
cadeaux whose index >= 256.  This tool reads the real 4-byte value from a game RSC
file and compares it against the location table, reporting (and optionally fixing):

  • Wrong save_idxs  — stored as last-byte, should be full 4-byte value.
  • Missed cadeaux      — listed as "barrel" because last-byte was 0, but the full
                          4-byte save index is non-zero (TrackType == 0x0002).

Both extracted_locations.py and data/locations.csv are updated when --apply is used.

Usage:
    python tools/fix_save_idxs.py --game-dir "C:/path/to/Shadow Man Remastered"
    python tools/fix_save_idxs.py --game-dir "..." --apply   # write fixes

Optional filters:
    --level ah4fogom     # only audit one level
    --rsc   quest.rsc    # only audit one RSC file within that level
"""

import argparse
import csv
import io
import re
import struct
import sys
from pathlib import Path


# ── Constants (must match patcher.py) ────────────────────────────────────────
NAME_OFF    = 0x22   # byte offset where RSC_ name starts within a record
TRACK_OFF   = 0x1C   # 2-byte big-endian TrackType flag
SAVE_OFF    = 0x1E   # 4-byte big-endian save-profile index
RECORD_SIZE = 72

TRACK_PERSISTENT = 0x0002   # barrel is a cadeaux (saves to player profile)
TRACK_VOLATILE_A = 0x0020   # plain barrel / no save
TRACK_VOLATILE_B = 0x0021   # plain barrel / no save (variant)

BARREL_KEYWORDS = ("BARREL", "CRATE", "PACKBOX")
CADEAUX_KEYWORDS = ("CADEAUX",)

RSC_PATTERN = re.compile(rb"RSC_[A-Z0-9_]+\x00")


# ── RSC parsing ───────────────────────────────────────────────────────────────

def parse_rsc(path: Path) -> dict[int, dict]:
    """Return {name_offset: {name, track, save_idx, is_cadeaux_barrel}} for every record."""
    data = path.read_bytes()
    results = {}
    for m in RSC_PATTERN.finditer(data):
        name_offset = m.start()
        rec_start   = name_offset - NAME_OFF
        if rec_start < 0:
            continue
        rec = data[rec_start : rec_start + RECORD_SIZE]
        if len(rec) < NAME_OFF:
            continue
        track    = struct.unpack(">H", rec[TRACK_OFF : TRACK_OFF + 2])[0]
        save_idx = struct.unpack(">I", rec[SAVE_OFF  : SAVE_OFF  + 4])[0]
        name     = m.group().decode().rstrip("\x00")

        is_barrel          = any(k in name for k in BARREL_KEYWORDS)
        is_cadeaux_explicit = any(k in name for k in CADEAUX_KEYWORDS)
        is_cadeaux_barrel  = is_barrel and track == TRACK_PERSISTENT and save_idx != 0

        results[name_offset] = {
            "name":               name,
            "track":              track,
            "save_idx":           save_idx,
            "last_byte":          save_idx & 0xFF,
            "is_barrel":          is_barrel,
            "is_cadeaux_explicit": is_cadeaux_explicit,
            "is_cadeaux_barrel":  is_cadeaux_barrel,
        }
    return results


# ── extracted_locations.py parsing ───────────────────────────────────────────

# Matches a full RawLocation(...) call (possibly spanning one line)
LOC_RE = re.compile(
    r'RawLocation\s*\(\s*'
    r'"(?P<level_id>[^"]+)"\s*,\s*'      # level_id
    r'"(?P<rsc_file>[^"]+)"\s*,\s*'      # rsc_file
    r'(?P<offset>0x[0-9A-Fa-f]+)\s*,\s*' # offset (hex)
    r'"(?P<name>[^"]+)"\s*,\s*'          # name
    r'"(?P<rsc_name>[^"]+)"\s*,\s*'      # rsc_name
    r'"(?P<category>[^"]+)"\s*,\s*'      # category
    r'(?P<region>[^,]+)\s*,\s*'          # region (None or "...")
    r'(?P<access>[^,]+)\s*,\s*'          # access (None or "...")
    r'(?P<save_idx>[^,]+)\s*,\s*'     # save_idx (int or None)
    r'(?P<is_tracked>[^,]+)\s*,\s*'      # is_tracked
    r'(?P<is_verified>[^,]+)\s*,\s*'     # is_verified
    r'"(?P<zone>[^"]+)"\s*,\s*'          # zone
    r'(?P<x>[^,]+)\s*,\s*'              # x
    r'(?P<y>[^,]+)\s*,\s*'              # y
    r'(?P<z>[^,]+)\s*,\s*'              # z
    r'(?P<notes>[^)]+)'                  # notes
    r'\)',
    re.DOTALL,
)


def parse_extracted_locations(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    locs = []
    for m in LOC_RE.finditer(text):
        g = m.groupdict()
        iid_raw = g["save_idx"].strip()
        locs.append({
            "level_id":    g["level_id"],
            "rsc_file":    g["rsc_file"],
            "offset":      int(g["offset"], 16),
            "name":        g["name"],
            "rsc_name":    g["rsc_name"],
            "category":    g["category"].strip(),
            "save_idx": int(iid_raw) if iid_raw.lstrip("-").isdigit() else None,
            "is_tracked":  g["is_tracked"].strip(),
            "raw_match":   m.group(),
            "span":        m.span(),
        })
    return locs


# ── Audit ─────────────────────────────────────────────────────────────────────

def audit(levels_dir: Path, level_filter: str | None, rsc_filter: str | None,
          apply: bool, locations_path: Path) -> None:

    locs = parse_extracted_locations(locations_path)

    # Group locations by (level_id, rsc_file)
    by_file: dict[tuple, list] = {}
    for loc in locs:
        key = (loc["level_id"], loc["rsc_file"])
        by_file.setdefault(key, []).append(loc)

    fixes: list[tuple[str, str]] = []   # (old_text, new_text) to apply to the file

    total_wrong   = 0
    total_missed  = 0
    total_ok      = 0
    skipped       = 0

    for (level_id, rsc_file), level_locs in sorted(by_file.items()):
        if level_filter and level_id != level_filter:
            continue
        if rsc_filter and rsc_file != rsc_filter:
            continue

        rsc_path = levels_dir / level_id / rsc_file
        if not rsc_path.exists():
            skipped += 1
            continue

        ground_truth = parse_rsc(rsc_path)

        wrong_iid   = []
        missed_cad  = []

        for loc in level_locs:
            gt = ground_truth.get(loc["offset"])
            if gt is None:
                continue

            stored_iid = loc["save_idx"] or 0
            real_idx   = gt["save_idx"]

            # Case 1: barrel in location table but RSC says it's a cadeaux barrel
            if (loc["category"] == "barrel"
                    and gt["is_cadeaux_barrel"]
                    and real_idx != 0):
                missed_cad.append((loc, gt))

            # Case 2: cadeaux in location table with wrong (truncated) save_idx
            elif (loc["category"] == "cadeaux"
                    and stored_iid != real_idx
                    and real_idx > 0):
                wrong_iid.append((loc, gt))

            else:
                total_ok += 1

        if wrong_iid or missed_cad:
            print(f"\n{'='*70}")
            print(f"  Level: {level_id}  /  RSC: {rsc_file}")
            print(f"{'='*70}")

        for loc, gt in wrong_iid:
            total_wrong += 1
            print(f"  WRONG iid  0x{loc['offset']:04X}  stored={loc['save_idx']}  "
                  f"real={gt['save_idx']}  ({loc['name']})")
            if apply:
                fixes.append(_make_iid_fix(loc, gt["save_idx"]))

        for loc, gt in missed_cad:
            total_missed += 1
            print(f"  MISSED cad 0x{loc['offset']:04X}  last_byte=0  "
                  f"real_save_idx={gt['save_idx']}  ({loc['name']})")
            if apply:
                fixes.append(_make_missed_fix(loc, gt["save_idx"]))

    print(f"\n--- Summary ---")
    print(f"  OK:              {total_ok}")
    print(f"  Wrong iid:       {total_wrong}")
    print(f"  Missed cadeaux:  {total_missed}")
    if skipped:
        print(f"  Skipped (RSC not found): {skipped} level/file combos")

    if apply and fixes:
        _apply_fixes(locations_path, fixes)
        print(f"\n  Applied {len(fixes)} fix(es) to {locations_path.name}")
    elif fixes:
        print(f"\n  Run with --apply to write {len(fixes)} fix(es) to extracted_locations.py")


# ── Fix generators ────────────────────────────────────────────────────────────

def _make_iid_fix(loc: dict, real_idx: int) -> tuple[str, str]:
    """Fix: update the save_idx field in an existing cadeaux location."""
    old = loc["raw_match"]
    # The save_idx sits right after the access field and before is_tracked.
    # Replace the integer value in-place using a targeted sub.
    old_iid_str = str(loc["save_idx"])
    # Build a pattern that matches the save_idx in context
    # (between the access/region field and is_tracked field)
    pattern = re.compile(
        r'(?P<before>,\s*)' + re.escape(old_iid_str) + r'(?P<after>\s*,\s*(?:True|False))'
    )
    new = pattern.sub(lambda m: m.group("before") + str(real_idx) + m.group("after"), old, count=1)
    if new == old:
        print(f"    WARNING: couldn't auto-patch iid for 0x{loc['offset']:04X} — do it manually")
    return (old, new)


def _make_missed_fix(loc: dict, real_idx: int) -> tuple[str, str]:
    """Fix: change category from 'barrel' to 'cadeaux', set save_idx, set is_tracked=True."""
    old = loc["raw_match"]
    new = old

    # 1. Category: "barrel" → "cadeaux"
    new = new.replace('"barrel"', '"cadeaux"', 1)

    # 2. save_idx: 0 → real_idx (the field between access and is_tracked)
    #    It appears as ', 0, False, True,' or ', 0, False, False,'
    #    We match the 0 that is followed by , True/False
    iid_pat = re.compile(r'(,\s*)0(\s*,\s*(?:True|False)\s*,\s*(?:True|False))')
    new = iid_pat.sub(lambda m: m.group(1) + str(real_idx) + m.group(2), new, count=1)

    # 3. is_tracked: first False after save_idx → True
    #    After our iid replacement the pattern looks like ', 481, False, True,' or similar
    track_pat = re.compile(r'(,\s*' + re.escape(str(real_idx)) + r'\s*,\s*)False')
    new = track_pat.sub(lambda m: m.group(1) + 'True', new, count=1)

    if new == old:
        print(f"    WARNING: couldn't auto-patch missed cadeaux for 0x{loc['offset']:04X} — do it manually")
    return (old, new)


def _apply_fixes(path: Path, fixes: list[tuple[str, str]]) -> None:
    text = path.read_text(encoding="utf-8")
    for old, new in fixes:
        if old == new:
            continue
        if old not in text:
            print(f"  WARNING: couldn't find text to replace — skipping a fix")
            continue
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


# ── CSV support ───────────────────────────────────────────────────────────────

CSV_COLUMNS = [
    "level_id", "source_file", "friendly_name", "offset", "object",
    "category", "save_idx", "is_tracked", "is_verified", "zone",
    "level_region", "sub_region", "x", "y", "z", "notes",
]


def audit_csv(levels_dir: Path, level_filter: str | None, rsc_filter: str | None,
              apply: bool, csv_path: Path) -> None:
    """Audit and optionally fix data/locations.csv."""
    text   = csv_path.read_text(encoding="utf-8", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows   = list(reader)

    # Cache parsed RSC files so we don't re-read the same file twice
    rsc_cache: dict[tuple, dict] = {}

    wrong   = 0
    missed  = 0
    changed = 0

    for row in rows:
        level_id = row.get("level_id", "").strip()
        rsc_file = row.get("source_file", "").strip()
        category = row.get("category", "").strip().lower()
        iid_raw  = row.get("save_idx", "0").strip()

        if level_filter and level_id != level_filter:
            continue
        if rsc_filter and rsc_file != rsc_filter:
            continue

        try:
            offset = int(row.get("offset", "0"), 16)
        except ValueError:
            continue

        key = (level_id, rsc_file)
        if key not in rsc_cache:
            rsc_path = levels_dir / level_id / rsc_file
            rsc_cache[key] = parse_rsc(rsc_path) if rsc_path.exists() else {}
        ground_truth = rsc_cache[key]

        gt = ground_truth.get(offset)
        if gt is None:
            continue

        stored_iid = int(iid_raw) if iid_raw.lstrip("-").isdigit() else 0
        real_idx   = gt["save_idx"]

        if category == "barrel" and gt["is_cadeaux_barrel"] and real_idx != 0:
            missed += 1
            print(f"  CSV MISSED cad  {level_id}/{rsc_file} 0x{offset:04X}  "
                  f"real_save_idx={real_idx}")
            if apply:
                row["category"]    = "cadeaux"
                row["save_idx"] = str(real_idx)
                row["is_tracked"]  = "TRUE"
                if row.get("friendly_name", "").startswith("Barrel"):
                    row["friendly_name"] = "Cadeaux – Barrel"
                changed += 1

        elif category == "cadeaux" and stored_iid != real_idx and real_idx > 0:
            wrong += 1
            print(f"  CSV WRONG iid   {level_id}/{rsc_file} 0x{offset:04X}  "
                  f"stored={stored_iid}  real={real_idx}")
            if apply:
                row["save_idx"] = str(real_idx)
                changed += 1

    print(f"\n  CSV — wrong iid: {wrong}  missed cadeaux: {missed}")

    if apply and changed:
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=CSV_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        csv_path.write_text(out.getvalue(), encoding="utf-8")
        print(f"  Applied {changed} fix(es) to {csv_path.name}")
    elif wrong + missed > 0 and not apply:
        print(f"  Run with --apply to fix {wrong + missed} CSV row(s)")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game-dir",    default=None,
                        help="Path to the Shadow Man Remastered install dir "
                             "(looks for levels/ subfolder inside)")
    parser.add_argument("--levels-dir",  default=None,
                        help="Path directly to the extracted levels folder "
                             "(e.g. your work-dir/levels). Takes priority over --game-dir.")
    parser.add_argument("--level",       default=None,
                        help="Only audit this level ID (e.g. ah4fogom)")
    parser.add_argument("--rsc",         default=None,
                        help="Only audit this RSC filename (e.g. quest.rsc)")
    parser.add_argument("--apply",       action="store_true",
                        help="Write fixes to extracted_locations.py and locations.csv "
                             "(dry-run by default)")
    parser.add_argument("--locations",   default="extracted_locations.py",
                        help="Path to extracted_locations.py (default: ./extracted_locations.py)")
    args = parser.parse_args()

    # Resolve the levels directory
    if args.levels_dir:
        levels_dir = Path(args.levels_dir)
    elif args.game_dir:
        levels_dir = Path(args.game_dir) / "levels"
    else:
        print("ERROR: provide --levels-dir or --game-dir", file=sys.stderr)
        sys.exit(1)

    locations_path = Path(args.locations)

    if not levels_dir.is_dir():
        print(f"ERROR: levels dir not found: {levels_dir}", file=sys.stderr)
        print(f"  Try --levels-dir pointing directly at your extracted levels folder.")
        sys.exit(1)
    if not locations_path.exists():
        print(f"ERROR: locations file not found: {locations_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning levels at: {levels_dir}")
    audit(levels_dir, args.level, args.rsc, args.apply, locations_path)

    # Also fix data/locations.csv
    csv_path = locations_path.parent / "data" / "locations.csv"
    if csv_path.exists():
        print(f"\n--- data/locations.csv ---")
        audit_csv(levels_dir, args.level, args.rsc, args.apply, csv_path)
    else:
        print(f"\n  (data/locations.csv not found at {csv_path}, skipping)")


if __name__ == "__main__":
    main()
