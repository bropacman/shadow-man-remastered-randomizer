"""
count_cadeaux.py — Ground-truth cadeaux census from actual RSC files.

Walks EVERY RSC file in the levels directory, finds every record that is a
cadeaux (explicit RSC type OR any RSC record with TrackType=0x0002 and
save_idx>0), and cross-references against extracted_locations.py /
data/locations.csv to show:

  • Total cadeaux in game files
  • Which ones are MISSING from the location table entirely
  • Duplicate save_idx values (same slot used twice — probably a data bug)
  • Save_idx gaps (holes in the sequence)

Usage:
    python tools/count_cadeaux.py --levels-dir "C:/path/to/levels"
    python tools/count_cadeaux.py --levels-dir "..." --verbose   # show every record
    python tools/count_cadeaux.py --levels-dir "..." --list-rsc-files  # list all RSC files found
    python tools/count_cadeaux.py --levels-dir "..." --dump-level salvage  # dump all records in a level
    python tools/count_cadeaux.py --levels-dir "..." --raw-scan salvage    # find TrackType=0x0002 at any alignment
"""

import argparse
import csv as csv_mod
import re
import struct
import sys
from pathlib import Path
from collections import defaultdict

# ── levels.txt level-number → directory-name mapping ─────────────────────────
# Update this if your level directory names differ.
LEVEL_DIR_MAP: dict[int, list[str]] = {
    0:  ["swampday", "swampnit"],
    1:  ["tenement", "ntenemnt"],
    2:  ["prison",   "nprison"],
    3:  ["uground",  "nuground"],
    4:  ["florida",  "nflorida"],
    5:  ["salvage",  "nsalvage"],
    6:  ["deadside"],
    7:  ["wastland"],
    8:  ["asylum"],
    9:  ["as2exper"],
    10: ["as3schis"],
    11: ["as4dkeng"],
    12: ["t1tchgad"],
    13: ["ah1cagew"],
    14: ["ah2playr"],
    15: ["t2wlkgad"],
    16: ["ah3lavad"],
    17: ["t3swmgad"],
    18: ["ah4fogom"],
}
# Reverse lookup: dir name → level number
DIR_TO_LEVEL: dict[str, int] = {
    d: n for n, dirs in LEVEL_DIR_MAP.items() for d in dirs
}

# ── RSC format constants ──────────────────────────────────────────────────────
NAME_OFF       = 0x22
TRACK_TYPE_OFF = 0x1C
SAVE_IDX_OFF   = 0x1E
RECORD_SIZE    = 72

TRACK_PERSISTENT = 0x0002
HEADER_SIZE      = 8
NAME_MAXLEN      = 30

CADEAUX_RSC = {"RSC_CADEAUX", "RSC_X_CADEAUX", "RSC_PICKUP_CADEAUX"}

# Only barrel/crate/packbox RSC types count as cadeaux when they have
# TrackType=0x0002.  Light halos, NPCs, lifts, altars, etc. also use
# TrackType=0x0002 for unrelated purposes and must be excluded.
BARREL_KEYWORDS = ("BARREL", "CRATE", "PACKBOX")

# These RSC file types are entirely non-item (enemies, audio, events).
# Explicit CADEAUX_RSC names still trigger regardless of file type.
NON_ITEM_RSC_FILES = {
    "enemies.rsc", "enemy.rsc", "enemys.rsc",
    "audio.rsc", "snd.rsc", "sound.rsc",
    "events.rsc", "aclmlogo.rsc", "day.rsc", "night.rsc",
}


# ── RSC scanner ───────────────────────────────────────────────────────────────

def scan_rsc(path: Path) -> list[dict]:
    """
    Structured scan: iterate every 72-byte record slot starting at HEADER_SIZE.
    This is more reliable than regex scanning because it respects the actual
    binary layout and won't miss records with unusual RSC names.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return []

    if len(data) < HEADER_SIZE + RECORD_SIZE:
        return []

    n_slots = (len(data) - HEADER_SIZE) // RECORD_SIZE
    check_persistent = path.name not in NON_ITEM_RSC_FILES

    results = []
    for i in range(n_slots):
        off = HEADER_SIZE + i * RECORD_SIZE
        rec = data[off : off + RECORD_SIZE]
        if len(rec) < RECORD_SIZE:
            break
        if bytes(rec) == bytes(RECORD_SIZE):   # all-zero slot (headroom/unused)
            continue

        track    = struct.unpack(">H", rec[TRACK_TYPE_OFF : TRACK_TYPE_OFF + 2])[0]
        save_idx = struct.unpack(">I", rec[SAVE_IDX_OFF   : SAVE_IDX_OFF   + 4])[0]

        # Extract null-terminated name from NAME_OFF field
        name_field = rec[NAME_OFF : NAME_OFF + NAME_MAXLEN]
        null_pos   = name_field.find(b"\x00")
        raw_name   = name_field[:null_pos] if null_pos >= 0 else name_field
        try:
            name = raw_name.decode("ascii").strip()
        except UnicodeDecodeError:
            name = raw_name.decode("latin-1").strip()

        # The "offset" key matches the convention in the location table:
        # it is the file offset of the first byte of the RSC name string.
        name_offset = off + NAME_OFF

        is_barrel           = any(k in name for k in BARREL_KEYWORDS)
        is_explicit_cadeaux = name in CADEAUX_RSC
        # Persistent barrel = barrel type with TrackType=0x0002 (save_idx may be 0
        # — the game assigns slot indices at runtime for some levels).
        # Non-barrel persistent items (light halos, NPCs, lifts…) are NOT cadeaux.
        is_persistent_barrel = check_persistent and track == TRACK_PERSISTENT and is_barrel

        if is_explicit_cadeaux or is_persistent_barrel:
            results.append({
                "level_id": path.parent.name,
                "rsc_file": path.name,
                "offset":   name_offset,
                "rsc_name": name,
                "save_idx": save_idx,
                "track":    track,
                "kind":     ("explicit" if is_explicit_cadeaux
                             else ("barrel0" if save_idx == 0 else "barrel")),
            })
    return results


# ── Location table loader ─────────────────────────────────────────────────────

def load_table_keys(locations_py: Path, csv_path: Path) -> set[str]:
    """Return the set of loc_keys present in the location table as cadeaux."""
    keys = set()

    # From extracted_locations.py — parse RawLocation calls
    if locations_py.exists():
        text = locations_py.read_text(encoding="utf-8", errors="replace")
        loc_re = re.compile(
            r'RawLocation\s*\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*(0x[0-9A-Fa-f]+)\s*,'
            r'[^"]*"[^"]*"\s*,\s*"[^"]*"\s*,\s*"([^"]+)"'
        )
        for m in loc_re.finditer(text):
            level_id, rsc_file, offset_hex, category = m.groups()
            if category == "cadeaux":
                key = f"{level_id}:{rsc_file}:{int(offset_hex,16):#06x}"
                keys.add(key)

    # From CSV
    if csv_path.exists():
        with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv_mod.DictReader(f)
            for row in reader:
                if row.get("category", "").strip().lower() == "cadeaux":
                    try:
                        offset = int(row["offset"], 16)
                    except (ValueError, KeyError):
                        continue
                    key = f"{row['level_id']}:{row['source_file']}:{offset:#06x}"
                    keys.add(key)

    return keys


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--levels-dir", required=True,
                        help="Path to the extracted levels directory")
    parser.add_argument("--locations",  default="extracted_locations.py",
                        help="Path to extracted_locations.py")
    parser.add_argument("--verbose",        action="store_true",
                        help="Print every found cadeaux record")
    parser.add_argument("--list-rsc-files", action="store_true",
                        help="List every RSC file found across levels (for discovery)")
    parser.add_argument("--levels-txt",     default=None,
                        help="Path to reference/levels.txt for per-level expected counts")
    parser.add_argument("--dump-level",     default=None, metavar="LEVEL_ID",
                        help="Dump ALL non-zero records for the given level directory")
    parser.add_argument("--raw-scan",       default=None, metavar="LEVEL_ID",
                        help="Raw byte scan for TrackType=0x0002 at any alignment in a level")
    args = parser.parse_args()

    levels_dir     = Path(args.levels_dir)
    locations_path = Path(args.locations)
    csv_path       = locations_path.parent / "data" / "locations.csv"

    if not levels_dir.is_dir():
        print(f"ERROR: levels dir not found: {levels_dir}", file=sys.stderr)
        sys.exit(1)

    # ── --dump-level: show ALL records in a level dir ────────────────────────
    if args.dump_level:
        level_dir = levels_dir / args.dump_level
        if not level_dir.is_dir():
            print(f"ERROR: level dir not found: {level_dir}", file=sys.stderr)
            sys.exit(1)
        for rsc_path in sorted(level_dir.glob("*.rsc")):
            data = rsc_path.read_bytes()
            n_slots = (len(data) - HEADER_SIZE) // RECORD_SIZE if len(data) >= HEADER_SIZE + RECORD_SIZE else 0
            count_byte = data[9] if len(data) > 9 else 0
            print(f"\n{rsc_path.name}  ({len(data)} bytes, {n_slots} slots, count_byte={count_byte})")
            for i in range(n_slots):
                off = HEADER_SIZE + i * RECORD_SIZE
                rec = data[off : off + RECORD_SIZE]
                if bytes(rec) == bytes(RECORD_SIZE):
                    continue
                track    = struct.unpack(">H", rec[TRACK_TYPE_OFF : TRACK_TYPE_OFF + 2])[0]
                save_idx = struct.unpack(">I", rec[SAVE_IDX_OFF   : SAVE_IDX_OFF   + 4])[0]
                name_field = rec[NAME_OFF : NAME_OFF + NAME_MAXLEN]
                null_pos   = name_field.find(b"\x00")
                raw_name   = name_field[:null_pos] if null_pos >= 0 else name_field
                try:
                    name = raw_name.decode("ascii").strip()
                except UnicodeDecodeError:
                    name = repr(raw_name)
                marker = " *** PERSISTENT" if track == TRACK_PERSISTENT else ""
                print(f"  slot {i:3d}  off=0x{off + NAME_OFF:04X}  track=0x{track:04X}  "
                      f"save_idx={save_idx:4d}  {name}{marker}")
        return

    # ── --raw-scan: find TrackType=0x0002 at ANY byte alignment ─────────────
    if args.raw_scan:
        import binascii
        level_dir = levels_dir / args.raw_scan
        if not level_dir.is_dir():
            print(f"ERROR: level dir not found: {level_dir}", file=sys.stderr)
            sys.exit(1)
        PERSISTENT_BYTES = struct.pack(">H", TRACK_PERSISTENT)   # b'\x00\x02'
        for rsc_path in sorted(level_dir.glob("*.rsc")):
            data = rsc_path.read_bytes()
            hits = []
            pos = 0
            while True:
                pos = data.find(PERSISTENT_BYTES, pos)
                if pos < 0:
                    break
                # Treat this as TRACK_TYPE_OFF within a record; compute record start
                rec_start = pos - TRACK_TYPE_OFF
                if rec_start >= HEADER_SIZE and rec_start + RECORD_SIZE <= len(data):
                    rec      = data[rec_start : rec_start + RECORD_SIZE]
                    save_idx = struct.unpack(">I", rec[SAVE_IDX_OFF : SAVE_IDX_OFF + 4])[0]
                    name_field = rec[NAME_OFF : NAME_OFF + NAME_MAXLEN]
                    null_pos   = name_field.find(b"\x00")
                    raw_name   = name_field[:null_pos] if null_pos >= 0 else name_field
                    try:
                        name = raw_name.decode("ascii").strip()
                    except UnicodeDecodeError:
                        name = repr(raw_name)
                    hits.append((pos, rec_start, save_idx, name))
                pos += 1
            if hits:
                print(f"\n{rsc_path.name}  ({len(hits)} persistent hits):")
                for track_off, rec_start, save_idx, name in hits:
                    aligned = (rec_start - HEADER_SIZE) % RECORD_SIZE == 0
                    align_str = "OK" if aligned else f"MISALIGNED(+{(rec_start-HEADER_SIZE)%RECORD_SIZE})"
                    print(f"  track@0x{track_off:04X}  rec@0x{rec_start:04X}  "
                          f"save_idx={save_idx:4d}  {name!r}  [{align_str}]")
        return

    # ── Scan all RSC files ────────────────────────────────────────────────────
    all_cadeaux: list[dict] = []
    files_scanned = 0
    rsc_filenames: set[str] = set()

    for level_dir in sorted(levels_dir.iterdir()):
        if not level_dir.is_dir():
            continue
        for rsc_path in sorted(level_dir.glob("*.rsc")):
            rsc_filenames.add(rsc_path.name)
            records = scan_rsc(rsc_path)
            all_cadeaux.extend(records)
            files_scanned += 1

    print(f"Scanned {files_scanned} RSC files across {levels_dir.name}")
    if args.list_rsc_files:
        print(f"RSC file types found ({len(rsc_filenames)}): {sorted(rsc_filenames)}")
    print(f"Total cadeaux records found in game files: {len(all_cadeaux)}")

    # ── Duplicate save_idx check ──────────────────────────────────────────────
    by_save_idx: dict[int, list[dict]] = defaultdict(list)
    for rec in all_cadeaux:
        by_save_idx[rec["save_idx"]].append(rec)

    dups = {idx: recs for idx, recs in by_save_idx.items() if len(recs) > 1 and idx != 0}
    if dups:
        print(f"\n{'='*60}")
        print(f"DUPLICATE save_idx values ({len(dups)} slots used by >1 record):")
        for idx in sorted(dups):
            print(f"  save_idx={idx}:")
            for r in dups[idx]:
                print(f"    {r['level_id']}/{r['rsc_file']} 0x{r['offset']:04X} {r['rsc_name']}")

    # ── save_idx range and gaps ───────────────────────────────────────────────
    nonzero_idxs = sorted(r["save_idx"] for r in all_cadeaux if r["save_idx"] > 0)
    if nonzero_idxs:
        lo, hi = nonzero_idxs[0], nonzero_idxs[-1]
        full_range = set(range(lo, hi + 1))
        found_set  = set(nonzero_idxs)
        gaps       = sorted(full_range - found_set)
        print(f"\nsave_idx range: {lo} – {hi}  (span of {hi - lo + 1})")
        print(f"Distinct save_idx values: {len(found_set)}")
        if gaps:
            print(f"Gaps in sequence ({len(gaps)}): {gaps[:40]}"
                  + (" ..." if len(gaps) > 40 else ""))

    # ── Cross-reference against location table ────────────────────────────────
    table_keys = load_table_keys(locations_path, csv_path)

    missing: list[dict] = []
    for rec in all_cadeaux:
        key = f"{rec['level_id']}:{rec['rsc_file']}:{rec['offset']:#06x}"
        if key not in table_keys:
            missing.append(rec)

    print(f"\nIn location table as cadeaux: {len(table_keys)}")
    print(f"Missing from table entirely:  {len(missing)}")

    if missing:
        print(f"\n{'='*60}")
        print("MISSING from location table:")
        by_level = defaultdict(list)
        for r in missing:
            by_level[r["level_id"]].append(r)
        for level_id in sorted(by_level):
            recs = by_level[level_id]
            print(f"\n  {level_id}/ ({len(recs)} missing):")
            for r in sorted(recs, key=lambda x: x["offset"]):
                print(f"    {r['rsc_file']} 0x{r['offset']:04X}  "
                      f"save_idx={r['save_idx']:4d}  {r['rsc_name']}")

    # ── Per-level breakdown vs levels.txt expected counts ────────────────────
    levels_txt_path = Path(args.levels_txt) if args.levels_txt else None
    if levels_txt_path and levels_txt_path.exists():
        # Parse $level N and $cadeaux N from the file
        lt_text = levels_txt_path.read_text(encoding="utf-8", errors="replace")
        level_expected: dict[int, int] = {}
        cur_level = None
        for line in lt_text.splitlines():
            lm = re.match(r'^\$level\s+(\d+)', line)
            if lm:
                cur_level = int(lm.group(1))
            cm = re.match(r'^\s*\$cadeaux\s+(\d+)', line)
            if cm and cur_level is not None:
                level_expected[cur_level] = int(cm.group(1))

        # Count found cadeaux per level directory
        found_by_dir: dict[str, int] = defaultdict(int)
        for r in all_cadeaux:
            found_by_dir[r["level_id"]] += 1

        print(f"\n{'='*60}")
        print(f"Per-level comparison (RSC scan vs levels.txt):")
        print(f"{'Lv':>3}  {'Dir(s)':<22}  {'Expected':>8}  {'RSC-Found':>9}  {'Diff':>5}")
        print("-"*55)
        grand_exp = grand_found = 0
        for lnum in sorted(level_expected):
            dirs   = LEVEL_DIR_MAP.get(lnum, [])
            exp    = level_expected[lnum]
            found  = sum(found_by_dir[d] for d in dirs)
            diff   = found - exp
            grand_exp   += exp
            grand_found += found
            mark = "  <SHORT" if diff < 0 else ("  OVER" if diff > 0 else "")
            print(f"{lnum:3d}  {str(dirs):<22}  {exp:8d}  {found:9d}  {diff:+5d}{mark}")
        print("-"*55)
        print(f"{'TOT':>3}  {'':22}  {grand_exp:8d}  {grand_found:9d}  {grand_found-grand_exp:+5d}")

    if args.verbose:
        print(f"\n{'='*60}")
        print("All cadeaux records:")
        for r in sorted(all_cadeaux, key=lambda x: (x["level_id"], x["rsc_file"], x["offset"])):
            print(f"  {r['level_id']}/{r['rsc_file']} 0x{r['offset']:04X}  "
                  f"save_idx={r['save_idx']:4d}  {r['kind']:8s}  {r['rsc_name']}")


if __name__ == "__main__":
    main()