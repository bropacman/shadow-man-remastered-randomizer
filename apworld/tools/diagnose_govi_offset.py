"""
diagnose_govi_offset.py
========================
One-off diagnostic: checks whether GOVI_IID_BACK = 8 (client.py's assumed
byte offset for reading a kexShadowManAIGovi record's instance_id from the
save file) is actually correct.

Why this exists: client.py's own docstring only documents a confirmed
XYZ-coordinate validation for kexShadowManQuestObject records (Book of
Shadows, Book of Prophecy, 3x Eclipser) — none of them dark souls. The
GOVI_* offsets were set to the same "-8 bytes before class name" pattern by
analogy, never independently confirmed against a real dark-soul pickup.
Live testing showed dark-soul pickups producing instance_ids (e.g. 745,
561) that don't match ANY entry in the extracted RSC instance-id database
for ANY level — a strong signal the -8 offset is wrong for Govi records
specifically, even though it works fine for QuestObject records.

What this does:
  1. Loads every known-correct dark-soul instance_id from data/locations.csv
     (category == "soul"), grouped by level_id.
  2. Finds your most recently-modified save file.
  3. For every "kexShadowManAIGovi" occurrence in that save file, scans a
     window of nearby byte offsets and checks each uint32 (little-endian)
     value against the known-correct soul IDs for ALL levels. Any hit tells
     us the real offset.
  4. Does the same for "kexShadowManQuestObject" as a sanity check — this
     one SHOULD show a hit at offset -8, confirming the methodology works.

Usage:
    python diagnose_govi_offset.py
    python diagnose_govi_offset.py --save "C:\\path\\to\\save_00.sav"
"""

from __future__ import annotations

import argparse
import csv
import struct
from collections import Counter
from pathlib import Path
from typing import Dict, Optional, Set

GOVI_CLASS  = b"kexShadowManAIGovi\x00"
QUEST_CLASS = b"kexShadowManQuestObject\x00"

# Known-good offset for QuestObject, per client.py — used as a sanity check.
QUEST_IID_BACK_KNOWN_GOOD = 8

# Window of byte offsets to test around each class-name occurrence.
SCAN_MIN = -160
SCAN_MAX = 160

_SAVE_SUBDIRS = [
    # "ap" first — save_path_patch.py redirects the patched exe's save
    # folder there (2026-07-20); "saves" kept as a fallback. See client.py's
    # _SAVE_SUBDIRS comment for details.
    Path("Saved Games") / "Nightdive Studios" / "Shadowman EX" / "ap",
    Path("Saved Games") / "Nightdive Studios" / "Shadowman EX" / "saves",
    Path("AppData") / "Local" / "Nightdive Studios" / "Shadowman EX" / "ap",
    Path("AppData") / "Local" / "Nightdive Studios" / "Shadowman EX" / "saves",
    Path(".local") / "share" / "Nightdive Studios" / "Shadowman EX" / "ap",
    Path(".local") / "share" / "Nightdive Studios" / "Shadowman EX" / "saves",
]


def _find_save_dir() -> Path | None:
    home = Path.home()
    for sub in _SAVE_SUBDIRS:
        c = home / sub
        if c.is_dir():
            return c
    return None


def _find_latest_save(save_dir: Path) -> Path | None:
    candidates = sorted(
        save_dir.glob("save_*.sav"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return candidates[0] if candidates else None


def _identify_level(save_bytes: bytes) -> Optional[str]:
    """Mirrors client.py's _identify_level() exactly."""
    needle = b"levels/"
    counts: Counter = Counter()
    pos = 0
    while True:
        p = save_bytes.find(needle, pos)
        if p == -1:
            break
        end = save_bytes.find(b"/", p + len(needle))
        if 0 < end - p - len(needle) < 32:
            try:
                folder = save_bytes[p + len(needle):end].decode("ascii")
                if folder:
                    counts[folder] += 1
            except UnicodeDecodeError:
                pass
        pos = p + 1
    return counts.most_common(1)[0][0] if counts else None


def _load_known_ids(csv_path: Path, only_category: str | None) -> Dict[str, Set[int]]:
    """
    Return {level_id: {known-correct instance_ids}}.
    If only_category is given (e.g. "soul"), restrict to that category —
    otherwise include every non-zero instance_id regardless of category.
    """
    by_level: Dict[str, Set[int]] = {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if only_category is not None and row.get("category") != only_category:
                continue
            try:
                iid = int(row["save_idx"])
            except (KeyError, ValueError):
                continue
            if iid == 0:
                continue
            by_level.setdefault(row["level_id"], set()).add(iid)
    return by_level


def _all_known_ids(by_level: Dict[str, Set[int]]) -> Dict[int, str]:
    """Flatten to {instance_id: level_id} for fast reverse lookup."""
    flat: Dict[int, str] = {}
    for level, ids in by_level.items():
        for iid in ids:
            flat[iid] = level
    return flat


def _scan_class(save_bytes: bytes, class_bytes: bytes, known_ids: Dict[int, str],
                 label: str) -> None:
    """
    For each candidate offset, track BOTH how many occurrences matched a
    known-correct ID AND how many DISTINCT values were matched. A real
    per-object instance_id field should match every occurrence with a
    different value each time (no two objects share an ID) — an offset
    that matches every occurrence but always with the SAME value is almost
    certainly a coincidental hit on some unrelated fixed byte pattern
    (padding, a flag, a version number, etc.), not the real field.
    """
    print(f"\n=== Scanning for {label} ({class_bytes!r}) ===")
    pos = 0
    match_count = 0
    hit_counts: Dict[int, int] = {}
    hit_values: Dict[int, Set[int]] = {}
    while True:
        p = save_bytes.find(class_bytes, pos)
        if p == -1:
            break
        match_count += 1
        for back in range(SCAN_MIN, SCAN_MAX + 1):
            idx = p - back  # back > 0 means "N bytes before class name"
            if 0 <= idx <= len(save_bytes) - 4:
                val = struct.unpack_from("<I", save_bytes, idx)[0]
                if val in known_ids:
                    hit_counts[back] = hit_counts.get(back, 0) + 1
                    hit_values.setdefault(back, set()).add(val)
        pos = p + len(class_bytes)

    print(f"  {match_count} occurrence(s) found in save file.")
    if not hit_counts:
        print("  No offset in the scanned window matched any known-correct ID.")
        print("  (Try widening SCAN_MIN/SCAN_MAX, or the record layout may "
              "differ more than expected.)")
        return

    scored = sorted(
        hit_counts.items(),
        key=lambda kv: (-kv[1], -len(hit_values[kv[0]])),
    )
    print("  Top candidates — IID_BACK expressed the same way client.py's "
          "*_IID_BACK constants are (positive = N bytes BEFORE class-name "
          "start, negative = N bytes AFTER). Look for matched == total AND "
          "distinct == total — that's a real per-object ID field:")
    for back, count in scored[:15]:
        distinct = len(hit_values[back])
        marker = "  <-- current client.py assumption" if back == 8 else ""
        flag = ""
        if count == match_count and distinct == match_count:
            flag = "  *** ALL MATCHED, ALL DISTINCT — LIKELY THE REAL FIELD ***"
        elif count == match_count:
            flag = "  (all matched but same value repeated — probably NOT it)"
        sign = "before" if back >= 0 else "after"
        print(f"    IID_BACK = {back:<5d} ({abs(back)} bytes {sign} class name) "
              f": {count}/{match_count} matched, {distinct} distinct value(s)"
              f"{marker}{flag}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", help="Path to a specific save_XX.sav file. "
                                    "If omitted, auto-detects the most "
                                    "recently modified one.")
    ap.add_argument("--csv", default=None,
                     help="Path to locations.csv. Defaults to "
                          "../data/locations.csv relative to this script.")
    args = ap.parse_args()

    csv_path = Path(args.csv) if args.csv else (
        Path(__file__).resolve().parent.parent / "data" / "locations.csv"
    )
    if not csv_path.is_file():
        print(f"locations.csv not found at {csv_path} — pass --csv explicitly.")
        return

    if args.save:
        save_path = Path(args.save)
    else:
        save_dir = _find_save_dir()
        if save_dir is None:
            print("Could not auto-detect the save folder — pass --save explicitly.")
            return
        save_path = _find_latest_save(save_dir)
        if save_path is None:
            print(f"No save_*.sav files found in {save_dir}")
            return

    print(f"Save file: {save_path}")
    print(f"CSV:       {csv_path}")

    save_bytes = save_path.read_bytes()

    current_level = _identify_level(save_bytes)
    print(f"Current level (from save): {current_level!r}")

    soul_by_level = _load_known_ids(csv_path, only_category="soul")
    nonsoul_by_level = _load_known_ids(csv_path, only_category=None)
    # Quest objects are everything the CSV tracks that ISN'T a dark soul —
    # drop soul entries so the sanity-check pool matches what QuestObject
    # records actually represent (lore, weapons, cadeaux, progression, etc).
    for level, ids in soul_by_level.items():
        nonsoul_by_level.setdefault(level, set())
        nonsoul_by_level[level] -= ids

    # Restrict to the current level only — pooling all levels' IDs together
    # makes small integers collide with unrelated bytes constantly and
    # drowns out the real signal.
    if current_level and current_level in soul_by_level:
        soul_ids = {iid: current_level for iid in soul_by_level[current_level]}
    else:
        print("  (Could not restrict to current level for souls — using all levels, "
              "expect noisy results.)")
        soul_ids = _all_known_ids(soul_by_level)

    if current_level and current_level in nonsoul_by_level:
        nonsoul_ids = {iid: current_level for iid in nonsoul_by_level[current_level]}
    else:
        print("  (Could not restrict to current level for quest objects — using all "
              "levels, expect noisy results.)")
        nonsoul_ids = _all_known_ids(nonsoul_by_level)

    print(f"Loaded {len(soul_ids)} known-correct dark-soul instance_ids for "
          f"{current_level!r}.")
    print(f"Loaded {len(nonsoul_ids)} known-correct non-soul instance_ids for "
          f"{current_level!r}.")

    _scan_class(save_bytes, QUEST_CLASS, nonsoul_ids,
                "kexShadowManQuestObject (sanity check — expect a hit at -8)")
    _scan_class(save_bytes, GOVI_CLASS, soul_ids,
                "kexShadowManAIGovi (the one we're actually debugging)")


if __name__ == "__main__":
    main()
