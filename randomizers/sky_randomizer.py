"""
sky_randomizer.py
─────────────────
Shuffles sky texture assets across levels.

Each level may have a sky/tga/ folder containing named TGA files such as:
  000sky.tga, 001hills.tga, 002cloud.tga, 003hills.tga, 004hill.tga, 005sun.tga

Shuffling is per-filename: all copies of 000sky.tga across different levels are
pooled and shuffled among themselves, and likewise for each other filename.
A level that lacks a given file is simply excluded from that file's pool —
no file is ever created where one didn't exist in vanilla.

Output: {internal_kpf_path: local_extracted_path} dict ready for mod KPF packing.
"""

from __future__ import annotations
import random
from pathlib import Path
from collections import defaultdict


def shuffle_sky(
    rng: random.Random,
    kpf_files: list[str],
    work_dir: str,
    dry_run: bool = False,
) -> dict[str, str]:
    """
    Find all levels/*/sky/tga/*.tga entries in the KPF archives, shuffle their
    contents per-filename across levels, extract swapped files to
    work_dir/sky_shuffle/, and return {internal_path: local_path} for mod packing.

    Returns empty dict if no sky files found or dry_run=True.
    """
    try:
        from kpf_handler import build_kpf_index, find_file_in_kpf, extract_file_from_kpf
    except ImportError:
        print("  WARNING: kpf_handler.py not found — sky shuffle skipped")
        return {}

    kpf_index = build_kpf_index(kpf_files)

    # Find all sky TGA files: levels/*/sky/tga/*.tga
    all_sky = [
        path for path, _ in find_file_in_kpf(kpf_index, "levels/*/sky/tga/*")
        if path.lower().endswith(".tga")
    ]

    if not all_sky:
        print("  WARNING: no sky textures found in KPF — sky shuffle skipped")
        return {}

    # Group by filename — only shuffle files that share the same name
    by_name: dict[str, list[str]] = defaultdict(list)
    for path in all_sky:
        by_name[Path(path).name.lower()].append(path)

    # Only pool filenames that appear in 2+ levels (otherwise nothing to swap)
    shuffleable = {name: paths for name, paths in by_name.items() if len(paths) >= 2}
    singletons  = {name: paths for name, paths in by_name.items() if len(paths) < 2}

    total_slots = sum(len(p) for p in shuffleable.values())
    print(f"  The skies above Deadside tremble — {total_slots} sky textures across "
          f"{len(shuffleable)} filename(s) to shuffle "
          f"({sum(len(p) for p in singletons.values())} unique singleton(s) untouched)")

    if dry_run:
        return {}

    # Build swap map: for each filename pool, shuffle sources across slots
    swap_map: dict[str, str] = {}
    for name, slots in shuffleable.items():
        sources = slots[:]
        rng.shuffle(sources)
        swap_map.update(zip(slots, sources))

    # Extract swapped files
    out_root = Path(work_dir) / "sky_shuffle"
    out_root.mkdir(parents=True, exist_ok=True)

    mod_files: dict[str, str] = {}
    changed = 0

    for slot_path, source_path in swap_map.items():
        if slot_path == source_path:
            continue  # vanilla — skip

        matches = find_file_in_kpf(kpf_index, source_path)
        if not matches:
            print(f"  WARNING: sky source not found in any KPF: {source_path}")
            continue

        internal, kpf_name = matches[0]
        kpf_full = str(Path(kpf_index.kpf_dir) / kpf_name)

        local_dir = out_root / Path(slot_path).parent
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / Path(slot_path).name

        ok = extract_file_from_kpf(kpf_full, internal, str(local_path))
        if not ok:
            print(f"  WARNING: failed to extract {source_path} from {kpf_name}")
            continue

        mod_files[slot_path] = str(local_path)
        changed += 1

    print(f"  Sky: {changed} texture(s) now painting a stranger horizon")
    return mod_files


def sky_spoiler_section(swap_map: dict[str, str]) -> list[str]:
    """Format the sky shuffle section for the spoiler log."""
    lines = [
        "",
        "── SKY SHUFFLE ─────────────────────────────────────────",
        "",
        f"  {'Slot (level)':<40}  {'Source (level)':<40}  File",
        f"  {'─'*40}  {'─'*40}  {'─'*20}",
    ]
    for slot, source in sorted(swap_map.items()):
        if slot == source:
            continue
        slot_level  = Path(slot).parts[-4] if len(Path(slot).parts) > 3 else Path(slot).parts[-2] if len(Path(slot).parts) > 1 else slot
        src_level   = Path(source).parts[-4] if len(Path(source).parts) > 3 else Path(source).parts[-2] if len(Path(source).parts) > 1 else source
        fname       = Path(slot).name
        lines.append(f"  {slot_level:<40}  {src_level:<40}  {fname}")
    return lines
