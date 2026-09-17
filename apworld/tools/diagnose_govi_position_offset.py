"""
diagnose_govi_position_offset.py
=================================
Finds the real byte offset (relative to a kexShadowManAIGovi class-name
occurrence) where the object's (x, y, z) world position is stored in the
save file, by matching against every known position in data/locations.csv.

Why this exists: GOVI_IID_BACK = 8 in client.py (the "read instance_id 8
bytes before the class name, same as QuestObject") is confirmed WRONG for
Govi records specifically — live dark-soul pickups produced instance_ids
(745, 561) matching nothing in the RSC-derived database. A manual byte-diff
of one confirmed before/after save pair (a dark-soul pickup in swampday)
showed the record's (x, y, z) position sitting roughly 200 bytes AFTER the
class name, and that position matched locations.csv's "Govi - Dark Soul 5"
coordinates to full float32 precision. This script generalizes that single
manual finding into an automated, self-verifying scan across every Govi
record in a save file, so we don't repeat the mistake of hardcoding an
offset from one example without confirming it holds up broadly — the same
mistake that produced the wrong GOVI_IID_BACK value in the first place.

IMPORTANT — this intentionally does NOT filter candidate matches by
category == "soul". The randomizer's patcher (shared by the standalone
randomizer and the AP build's own generate_output()) can place a dark soul
at ANY location slot — a spot that's a cadeaux or key item in vanilla can
become a govi in a given seed, and vice versa (confirmed 2026-07-14).
locations.csv's x/y/z columns are extracted directly from level
geometry/RSC placement data and describe WHERE a slot physically is, not
what item/container ends up there in a given seed — so the match pool is
every row with non-null x/y/z, regardless of category.

What this does:
  1. Loads every (level_id, x, y, z, loc_key, friendly_name, category) row
     from data/locations.csv with non-null x/y/z.
  2. Finds your most recently-modified save file (or --save).
  3. For every "kexShadowManAIGovi" occurrence, tries a range of candidate
     byte offsets (relative to class-name start) for a 12-byte (x,y,z)
     float32 LE triplet, and checks each against every known position
     (Euclidean distance). Scores each candidate offset by how many
     occurrences got a near-exact match AND how many DISTINCT locations
     were matched (a real position field should match a different location
     per occurrence, not coincidentally hit the same row's values
     repeatedly).

Usage:
    python diagnose_govi_position_offset.py
    python diagnose_govi_position_offset.py --save "C:\\path\\to\\save_00.sav"
    python diagnose_govi_position_offset.py --scan-min -50 --scan-max 400
"""

from __future__ import annotations

import argparse
import csv
import struct
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

GOVI_CLASS = b"kexShadowManAIGovi\x00"

# Candidate offset window (bytes relative to class-name start) to test for
# the start of the (x, y, z) float triplet. Positive = after class-name
# start. A manual diff of one confirmed pickup found the match around +200,
# so the default window covers that with generous margin rather than just
# confirming our own prior finding.
SCAN_MIN = -300
SCAN_MAX = 300

MATCH_TOLERANCE = 1.0  # world units; a float32 round-trip should be near-exact

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


class KnownLoc(NamedTuple):
    level_id: str
    x: float
    y: float
    z: float
    loc_key: str
    friendly_name: str
    category: str


def _find_save_dir() -> Optional[Path]:
    home = Path.home()
    for sub in _SAVE_SUBDIRS:
        c = home / sub
        if c.is_dir():
            return c
    return None


def _find_latest_save(save_dir: Path) -> Optional[Path]:
    candidates = sorted(
        save_dir.glob("save_*.sav"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return candidates[0] if candidates else None


def _load_known_positions(csv_path: Path) -> List[KnownLoc]:
    """
    Every row with non-null x/y/z, regardless of category — a slot's
    physical position is fixed by level geometry; what item/container type
    ends up there is decided per-seed by the fill algorithm, so we can't
    pre-filter to "soul" rows without potentially missing the correct match.
    """
    out: List[KnownLoc] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                x = float(row["x"])
                y = float(row["y"])
                z = float(row["z"])
            except (KeyError, ValueError, TypeError):
                continue
            offset_raw = row.get("offset", "") or ""
            try:
                offset_int = int(offset_raw, 0)
            except ValueError:
                offset_int = 0
            loc_key = f"{row['level_id']}:{row['source_file']}:0x{offset_int:04X}"
            out.append(KnownLoc(
                level_id=row["level_id"], x=x, y=y, z=z, loc_key=loc_key,
                friendly_name=row.get("friendly_name", "") or "",
                category=row.get("category", "") or "",
            ))
    return out


def _nearest_match_sq(x: float, y: float, z: float,
                       known: List[KnownLoc]) -> Tuple[Optional[KnownLoc], float]:
    """Returns (best KnownLoc or None, squared distance)."""
    best: Optional[KnownLoc] = None
    best_d2 = float("inf")
    for k in known:
        d2 = (x - k.x) ** 2 + (y - k.y) ** 2 + (z - k.z) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best = k
    return best, best_d2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--save", help="Path to a specific save_XX.sav file. "
                                    "If omitted, auto-detects the most "
                                    "recently modified one.")
    ap.add_argument("--csv", default=None,
                     help="Path to locations.csv. Defaults to "
                          "../data/locations.csv relative to this script.")
    ap.add_argument("--scan-min", type=int, default=SCAN_MIN)
    ap.add_argument("--scan-max", type=int, default=SCAN_MAX)
    ap.add_argument("--tolerance", type=float, default=MATCH_TOLERANCE,
                     help="Max Euclidean distance (world units) to count as "
                          "a match (default 1.0).")
    ap.add_argument("--level", default=None,
                     help="Restrict known positions to this level_id (e.g. "
                          "'t1tchgad'). Strongly recommended when widening "
                          "--scan-min/--scan-max a lot — cuts the candidate "
                          "pool from thousands to tens/hundreds, which is "
                          "both much faster and lowers coincidental-match "
                          "risk across a wide offset window.")
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

    known = _load_known_positions(csv_path)
    if args.level:
        before = len(known)
        known = [k for k in known if k.level_id == args.level]
        print(f"Loaded {before} known positions from CSV, restricted to "
              f"level_id={args.level!r} -> {len(known)} candidates (all "
              f"categories, not just 'soul').")
    else:
        print(f"Loaded {len(known)} known positions from CSV (all categories, "
              f"not just 'soul', all levels — pass --level to narrow this "
              f"down for wider/faster scans).")

    save_bytes = save_path.read_bytes()

    occurrences: List[int] = []
    pos = 0
    while True:
        p = save_bytes.find(GOVI_CLASS, pos)
        if p == -1:
            break
        occurrences.append(p)
        pos = p + len(GOVI_CLASS)
    print(f"{len(occurrences)} kexShadowManAIGovi occurrence(s) found in save file.")
    if not occurrences:
        return

    tol_sq = args.tolerance ** 2

    # candidate_off -> list of (occurrence_index, matched KnownLoc, distance)
    hits: Dict[int, List[Tuple[int, KnownLoc, float]]] = {}

    for off in range(args.scan_min, args.scan_max + 1):
        for occ_idx, p in enumerate(occurrences):
            idx = p + off
            if idx < 0 or idx + 12 > len(save_bytes):
                continue
            try:
                x, y, z = struct.unpack_from("<fff", save_bytes, idx)
            except struct.error:
                continue
            # Quick sanity filter: real level coordinates are large-ish
            # floats, not NaN/inf/garbage.
            if not all(v == v and abs(v) < 1_000_000 for v in (x, y, z)):
                continue
            k, d2 = _nearest_match_sq(x, y, z, known)
            if k is not None and d2 <= tol_sq:
                hits.setdefault(off, []).append((occ_idx, k, d2 ** 0.5))

    if not hits:
        print(f"\nNo candidate offset in [{args.scan_min}, {args.scan_max}] "
              f"produced any match within tolerance={args.tolerance}. Try "
              f"widening --scan-min/--scan-max or --tolerance.")
        return

    scored = sorted(
        hits.items(),
        key=lambda kv: (-len(kv[1]), -len({h[1].loc_key for h in kv[1]})),
    )

    print(f"\nTop candidate offsets (match_count = occurrences matched, "
          f"distinct = distinct locations matched — a real position field "
          f"should have distinct ≈ match_count, not the same location hit "
          f"repeatedly, which would signal a coincidental fixed-byte-pattern "
          f"match instead):\n")
    for off, matches in scored[:15]:
        distinct = len({h[1].loc_key for h in matches})
        flag = ""
        if distinct == len(matches) and len(matches) > 1:
            flag = "  *** LIKELY THE REAL FIELD ***"
        print(f"  offset = {off:+5d}  ({len(matches)}/{len(occurrences)} "
              f"occurrence(s) matched, {distinct} distinct location(s)){flag}")

    best_off, best_matches = scored[0]
    print(f"\nDetail for best candidate offset ({best_off:+d}):")
    for occ_idx, k, dist in best_matches:
        p = occurrences[occ_idx]
        print(f"  occurrence @ file offset {p:#x} -> {k.loc_key}  "
              f"\"{k.friendly_name}\"  (category={k.category}, "
              f"dist={dist:.4f})")

    print(f"\nIf this looks right, set in client.py:")
    print(f"  GOVI_POS_OFFSET = {best_off}   # bytes after class-name start "
          f"-> (x, y, z) float32 LE triplet")
    print(f"and match against ALL of locations.csv (any category), not just "
          f"category == 'soul'.")


if __name__ == "__main__":
    main()
