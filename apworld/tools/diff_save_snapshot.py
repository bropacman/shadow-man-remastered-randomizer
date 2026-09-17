"""
diff_save_snapshot.py
======================
Byte-level diff between two save-file snapshots (before/after a single known
pickup), used to pin down the real kexShadowManAIGovi record layout without
needing to reverse-engineer the C++ save/load code.

Why this exists: static Ghidra analysis of the save-file loader dead-ended in
generic KPF-archive-loading machinery rather than the actor-specific
serialization logic, and brute-force offset scanning across many pickups at
once produced too much coincidental noise to read cleanly. A single, known
pickup's before/after diff sidesteps both problems — whatever changed is
either the "collected" state flag or something feeding into it, full stop.

How to use:
  1. Copy your current save file somewhere safe as a "before" snapshot,
     e.g.:
         copy "C:\\Users\\jonat\\Saved Games\\Nightdive Studios\\Shadowman EX\\saves\\save_06.sav" before.sav
  2. In-game, collect exactly ONE dark soul (note roughly where, so we can
     cross-reference its real instance_id from the CSV afterward).
  3. Save the game (or let it autosave), then copy the save again as "after":
         copy "C:\\Users\\jonat\\Saved Games\\Nightdive Studios\\Shadowman EX\\saves\\save_06.sav" after.sav
  4. Run:
         python diff_save_snapshot.py before.sav after.sav

What it prints: every contiguous byte range that changed, plus — for each
one — a hex+ASCII dump of a window around the nearest preceding
kexShadowManAIGovi / kexShadowManQuestObject class-name occurrence, with the
changed byte(s) marked. That should make the state-flag offset and the
instance_id field visually obvious for this one confirmed example.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path
from typing import List, Optional, Tuple

GOVI_CLASS  = b"kexShadowManAIGovi\x00"
QUEST_CLASS = b"kexShadowManQuestObject\x00"

DUMP_BEFORE = 64   # bytes to show before the class-name start
DUMP_AFTER  = 220  # bytes to show after the class-name start


def _find_diff_spans(before: bytes, after: bytes) -> List[Tuple[int, int]]:
    """
    Return a list of (start, end) byte ranges (end exclusive) where the two
    buffers differ. Adjacent/near differing bytes are merged into a single
    span (within a small gap tolerance) so one record's change shows up as
    one span instead of several.
    """
    n = min(len(before), len(after))
    diff_positions = [i for i in range(n) if before[i] != after[i]]
    if len(before) != len(after):
        print(f"WARNING: file sizes differ (before={len(before)}, "
              f"after={len(after)}) — comparing only the first {n} bytes. "
              f"This can happen if the save format re-serializes with "
              f"different padding; spans near the end may be unreliable.")

    if not diff_positions:
        return []

    spans: List[Tuple[int, int]] = []
    gap_tolerance = 8
    start = diff_positions[0]
    prev = diff_positions[0]
    for pos in diff_positions[1:]:
        if pos - prev > gap_tolerance:
            spans.append((start, prev + 1))
            start = pos
        prev = pos
    spans.append((start, prev + 1))
    return spans


def _find_nearest_class_before(data: bytes, pos: int) -> Optional[Tuple[bytes, int]]:
    """Return (class_bytes, class_start_pos) for whichever of GOVI_CLASS /
    QUEST_CLASS has the closest occurrence at or before `pos`, or None."""
    best: Optional[Tuple[bytes, int]] = None
    for cls in (GOVI_CLASS, QUEST_CLASS):
        search_from = max(0, pos - 4000)  # don't scan the whole file every time
        p = data.rfind(cls, search_from, pos + len(cls))
        if p != -1:
            if best is None or p > best[1]:
                best = (cls, p)
    return best


def _hex_dump(data: bytes, base_addr: int, start: int, end: int,
              highlight: Tuple[int, int]) -> None:
    """Print a hex+ASCII dump of data[start:end], marking bytes in
    [highlight[0], highlight[1]) with asterisks."""
    hi_start, hi_end = highlight
    row = 16
    for row_start in range(start - (start % row), end, row):
        chunk_start = max(row_start, start)
        chunk_end = min(row_start + row, end)
        if chunk_start >= chunk_end:
            continue
        hex_parts = []
        ascii_parts = []
        for i in range(row_start, row_start + row):
            if start <= i < end:
                b = data[i]
                marker_l = "[" if hi_start <= i < hi_end else " "
                marker_r = "]" if hi_start <= i < hi_end else " "
                hex_parts.append(f"{marker_l}{b:02x}{marker_r}")
                ascii_parts.append(chr(b) if 32 <= b < 127 else ".")
            else:
                hex_parts.append("    ")
                ascii_parts.append(" ")
        addr = base_addr + row_start
        print(f"  {addr:08x}  {' '.join(hex_parts)}  {''.join(ascii_parts)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("before", help="Path to the 'before' save snapshot")
    ap.add_argument("after", help="Path to the 'after' save snapshot")
    ap.add_argument("--proximity", type=int, default=400,
                     help="Only full-dump spans whose nearest class name is "
                          "within this many bytes (default 400). Spans "
                          "farther away (or with no class nearby) are almost "
                          "certainly unrelated noise — position, camera, "
                          "playtime counters, etc.")
    ap.add_argument("--max-dumps", type=int, default=5,
                     help="Max number of full hex dumps to print, closest "
                          "spans first (default 5).")
    ap.add_argument("--full", action="store_true",
                     help="Ignore --proximity/--max-dumps and full-dump "
                          "every span (very verbose — only for deep manual "
                          "digging).")
    args = ap.parse_args()

    before_path = Path(args.before)
    after_path = Path(args.after)
    before = before_path.read_bytes()
    after = after_path.read_bytes()

    print(f"Before: {before_path} ({len(before)} bytes)")
    print(f"After:  {after_path} ({len(after)} bytes)")

    spans = _find_diff_spans(before, after)
    if not spans:
        print("\nNo differences found — did the save actually change? "
              "(Check you copied the right file / the game actually saved.)")
        return

    # Pair every span with its nearest class-name info up front so we can
    # sort/filter before printing anything.
    annotated = []
    for s, e in spans:
        nearest = _find_nearest_class_before(after, s)
        if nearest is None:
            annotated.append((s, e, None, None, None))
        else:
            cls_bytes, cls_pos = nearest
            rel = cls_pos - s
            annotated.append((s, e, cls_bytes, cls_pos, rel))

    print(f"\n{len(spans)} changed byte range(s) found. Summary (closest to "
          f"a class name first):\n")

    def sort_key(item):
        _, _, _, _, rel = item
        return abs(rel) if rel is not None else float("inf")

    annotated_sorted = sorted(annotated, key=sort_key)

    for idx, (s, e, cls_bytes, cls_pos, rel) in enumerate(annotated_sorted):
        if cls_bytes is None:
            print(f"  #{idx + 1}: [{s:#x}, {e:#x}) ({e - s} byte(s)) — "
                  f"no class name nearby, likely unrelated")
        else:
            label = "Govi" if cls_bytes == GOVI_CLASS else "QuestObject"
            sign = "before" if rel >= 0 else "after"
            print(f"  #{idx + 1}: [{s:#x}, {e:#x}) ({e - s} byte(s)) — "
                  f"{abs(rel)} bytes {sign} nearest {label} class name "
                  f"(IID_BACK={rel})")

    if args.full:
        to_dump = annotated_sorted
    else:
        to_dump = [
            item for item in annotated_sorted
            if item[2] is not None and abs(item[4]) <= args.proximity
        ][: args.max_dumps]
        skipped = len(annotated_sorted) - len(to_dump)
        if skipped > 0:
            print(f"\nShowing full detail for the {len(to_dump)} closest "
                  f"span(s) only ({skipped} skipped — outside "
                  f"--proximity={args.proximity} or capped by --max-dumps="
                  f"{args.max_dumps}; re-run with --full to see everything, "
                  f"or widen --proximity/--max-dumps).")

    for s, e, cls_bytes, cls_pos, rel in to_dump:
        print(f"\n=== Detail: bytes [{s:#x}, {e:#x}) ({e - s} byte(s) changed) ===")
        old_slice = before[s:e]
        new_slice = after[s:e]
        print(f"  before: {old_slice.hex()}")
        print(f"  after:  {new_slice.hex()}")

        if cls_bytes is None:
            print("  (No kexShadowManAIGovi/QuestObject class-name found "
                  "nearby.)")
            continue

        label = "kexShadowManAIGovi" if cls_bytes == GOVI_CLASS else "kexShadowManQuestObject"
        sign = "before" if rel >= 0 else "after"
        print(f"  Nearest class name: {label} at file offset {cls_pos:#x}. "
              f"The changed byte(s) sit {abs(rel)} bytes {sign} class-name "
              f"start (IID_BACK = {rel} if this change IS the state flag).")

        dump_start = max(0, cls_pos - DUMP_BEFORE)
        dump_end = min(len(after), cls_pos + len(cls_bytes) + DUMP_AFTER)
        print(f"  Hex dump around the class name (changed byte(s) marked "
              f"[xx]), from the AFTER file:")
        _hex_dump(after, 0, dump_start, dump_end, (s, e))

        print("  uint32 (LE) values in this window that look like plausible "
              "instance_ids (1-2000):")
        for off in range(dump_start, dump_end - 3):
            val = struct.unpack_from("<I", after, off)[0]
            if 0 < val < 2000:
                rel_to_class = cls_pos - off
                print(f"    file offset {off:#x} (IID_BACK={rel_to_class}): {val}")


if __name__ == "__main__":
    main()
