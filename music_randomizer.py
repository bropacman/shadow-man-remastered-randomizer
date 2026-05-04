"""
music_randomizer.py
───────────────────
Shuffles music tracks globally across all audio/music/ slots in the KPF.

Excluded tracks (ambient/sting/non-looping) are left in their vanilla slots.
All other tracks are shuffled: track A plays where track B used to, etc.

Output: {internal_kpf_path: local_extracted_path} dict ready for mod KPF packing.
The caller (patcher.py) merges this into mod_files before build_and_install_mod.
"""

from __future__ import annotations
import random
from pathlib import Path


# ── Exclusion list ─────────────────────────────────────────────────────────────
#
# Tracks excluded from shuffling — ambience, stings, one-shots that would feel
# wrong in any other slot. Add filenames (no path, no extension) here.

EXCLUDED_FOLDERS: frozenset[str] = frozenset({

})

EXCLUDED_TRACKS: frozenset[str] = frozenset({
    # populate as you identify ambient/sting tracks
    "menu.flac"
})

import struct

# ── FLAC duration parsing ──────────────────────────────────────────────────────

def _flac_duration_seconds(data: bytes) -> float | None:
    """
    Parse FLAC STREAMINFO block and return track duration in seconds.
    No external dependencies — reads raw FLAC header bytes only.
    Returns None if not a valid FLAC or header is malformed.
    """
    if len(data) < 42 or data[:4] != b'fLaC':
        return None
    hdr = struct.unpack_from(">I", data, 4)[0]
    if ((hdr >> 24) & 0x7F) != 0 or (hdr & 0xFFFFFF) < 34:
        return None
    # STREAMINFO starts at byte 8; fields of interest are at bytes 10-17
    b8 = struct.unpack_from(">Q", data, 18)[0]  # file offset 8+10 = 18
    sample_rate   = (b8 >> 44) & 0xFFFFF
    total_samples =  b8        & 0xFFFFFFFFF
    if sample_rate == 0:
        return None
    return total_samples / sample_rate


# ── Duration bucketing ─────────────────────────────────────────────────────────
#
# Tracks are bucketed by duration before shuffling so a 4-second sting never
# lands in a slot that expects a 3-minute loop, and vice versa.
# Bucket boundaries are in seconds — adjust if game tracks cluster differently.

DURATION_BUCKETS: list[tuple[str, float, float]] = [
    ("short",  0.0,   29.9),   # < 1 min: stings, transitions, short loops
    ("medium", 30.0, 59.9),  # < 1 min: stings, transitions, short loops
    ("long",  60.0, 9999.0),   # >= 1 min: full level tracks
]

def _duration_bucket(seconds: float | None) -> str:
    if seconds is None:
        return "long"   # unknown → treat as long, safer default
    for name, lo, hi in DURATION_BUCKETS:
        if lo <= seconds < hi:
            return name
    return "long"

def _stem(internal_path: str) -> str:
    """Return the filename stem (no extension) from an internal KPF path."""
    return Path(internal_path).stem


def shuffle_music(
    rng: random.Random,
    kpf_files: list[str],
    work_dir: str,
    dry_run: bool = False,
) -> dict[str, str]:
    """
    Find all audio/music/**/*.flac entries in the KPF archives, shuffle their
    contents globally (excluding EXCLUDED_TRACKS), extract swapped files to
    work_dir/music_shuffle/, and return {internal_path: local_path} for mod packing.

    Returns empty dict if no music files found or dry_run=True.
    """
    try:
        from kpf_handler import build_kpf_index, find_file_in_kpf, extract_file_from_kpf
    except ImportError:
        print("  WARNING: kpf_handler.py not found — music shuffle skipped")
        return {}

    import zipfile

    kpf_index = build_kpf_index(kpf_files)

    # Find all music tracks in KPF
    all_music = [
        path for path, _ in find_file_in_kpf(kpf_index, "audio/music/*/*")
        if path.lower().endswith(".flac")
    ]

    if not all_music:
        print("  WARNING: no audio/music/ tracks found in KPF — music shuffle skipped")
        return {}

    # Split into shuffleable and excluded
    excluded = [
        p for p in all_music
        if Path(p).parent.name in EXCLUDED_FOLDERS
        or _stem(p) in EXCLUDED_TRACKS
    ]
    shuffleable = [
        p for p in all_music
        if Path(p).parent.name not in EXCLUDED_FOLDERS
        and _stem(p) not in EXCLUDED_TRACKS
    ]

    if not shuffleable:
        print("  WARNING: all music tracks are excluded — nothing to shuffle")
        return {}

    print(f"  Music: {len(shuffleable)} tracks to shuffle, {len(excluded)} excluded")

    if dry_run:
        return {}

    # Shuffle: build a mapping {slot_path: source_path}
    # Read durations and assign buckets
    durations: dict[str, float | None] = {}
    for path in shuffleable:
        matches = find_file_in_kpf(kpf_index, path)
        if not matches:
            durations[path] = None
            continue
        internal, kpf_name = matches[0]
        kpf_full = str(Path(kpf_index.kpf_dir) / kpf_name)
        # Read just the first 64 bytes — enough for FLAC STREAMINFO
        try:
            import zipfile
            with zipfile.ZipFile(kpf_full, "r") as zf:
                with zf.open(internal) as f:
                    header = f.read(64)
            durations[path] = _flac_duration_seconds(header)
        except Exception:
            durations[path] = None

    # Group by bucket and report
    from collections import defaultdict
    buckets: dict[str, list[str]] = defaultdict(list)
    for path in shuffleable:
        buckets[_duration_bucket(durations[path])].append(path)

    for bname, tracks in sorted(buckets.items()):
        dur_strs = [f"{durations[t]:.0f}s" if durations[t] else "?" for t in tracks]
        print(f"  Music [{bname}]: {len(tracks)} tracks  ({', '.join(dur_strs)})")

    # Shuffle within each bucket independently
    swap_map: dict[str, str] = {}
    for bname, tracks in buckets.items():
        sources = tracks[:]
        rng.shuffle(sources)
        swap_map.update(zip(tracks, sources))

    # Extract swapped files to work_dir/music_shuffle/
    out_root = Path(work_dir) / "music_shuffle"
    out_root.mkdir(parents=True, exist_ok=True)

    mod_files: dict[str, str] = {}
    changed = 0

    for slot_path, source_path in swap_map.items():
        if slot_path == source_path:
            continue  # vanilla — no need to pack

        # Find which KPF contains source_path
        matches = find_file_in_kpf(kpf_index, source_path)
        if not matches:
            print(f"  WARNING: source track not found in any KPF: {source_path}")
            continue

        internal, kpf_name = matches[0]
        kpf_full = str(Path(kpf_index.kpf_dir) / kpf_name)

        # Extract source track to a local path named after the slot
        local_name = Path(slot_path).name  # keep slot filename
        local_dir = out_root / Path(slot_path).parent
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / local_name

        ok = extract_file_from_kpf(kpf_full, internal, str(local_path))
        if not ok:
            print(f"  WARNING: failed to extract {source_path} from {kpf_name}")
            continue

        mod_files[slot_path] = str(local_path)
        changed += 1

    print(f"  Music: {changed} track(s) swapped into mod KPF")
    return mod_files


def music_spoiler_section(
    swap_map: dict[str, str],
    excluded: list[str],
) -> list[str]:
    """Format the music shuffle section for the spoiler log."""
    lines = [
        "",
        "── MUSIC SHUFFLE ───────────────────────────────────────",
        "",
    ]
    if excluded:
        lines.append(f"  Excluded (vanilla): {len(excluded)} track(s)")
        for p in sorted(excluded):
            lines.append(f"    {p}")
        lines.append("")

    lines.append(f"  {'Slot (plays here)':<50}  {'Source (track used)'}")
    lines.append(f"  {'─'*50}  {'─'*50}")
    for slot, source in sorted(swap_map.items()):
        if slot != source:
            lines.append(f"  {slot:<50}  {source}")
    return lines