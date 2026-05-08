"""
sfx_randomizer.py
─────────────────
Shuffles sound effects within curated pools.

Two pools are supported:
  1. Voice lines — all files under audio/speech/generic/ shuffle globally.
  2. Weapon sounds — manually mapped pools shuffle within each sound type
     (fire sounds swap with fire sounds, reload with reload, etc.).

Output: {internal_kpf_path: local_extracted_path} dict for mod KPF packing.
Caller (patcher.py) merges this into mod_files before build_and_install_mod.
"""

from __future__ import annotations
import random
from pathlib import Path
from constants import WEAPON_SOUND_SETS

# ── Voice line config ──────────────────────────────────────────────────────────

VOICE_PATH_PATTERN = "audio/speech/generic/*"

# Individual stems to exclude from voice shuffling if needed
EXCLUDED_VOICE_STEMS: frozenset[str] = frozenset({
    # e.g. "gn0001s",
})

def _swap_set_pool(
    rng: random.Random,
    sets: list[list[str]],
    kpf_index,
    kpf_files: list[str],
    out_root: Path,
    label: str,
) -> dict[str, str]:
    """
    Shuffle a list of sound sets within themselves.
    Each set stays together — source files tile to fill slot count if sizes differ.
    Returns {internal_path: local_path} for mod packing.
    """
    from kpf_handler import find_file_in_kpf, extract_file_from_kpf

    if not sets:
        return {}

    # Shuffle which source set fills each slot set
    sources = sets[:]
    rng.shuffle(sources)

    mod_files: dict[str, str] = {}
    changed = 0

    for slot_set, source_set in zip(sets, sources):
        if slot_set == source_set:
            continue
        # Tile source files to match slot count
        tiled = [source_set[i % len(source_set)] for i in range(len(slot_set))]
        for slot_path, source_path in zip(slot_set, tiled):
            if slot_path == source_path:
                continue
            matches = find_file_in_kpf(kpf_index, source_path)
            if not matches:
                print(f"  WARNING [{label}]: source not found in KPF: {source_path}")
                continue
            internal, kpf_name = matches[0]
            kpf_full = str(Path(kpf_index.kpf_dir) / kpf_name)
            local_dir = out_root / Path(slot_path).parent
            local_dir.mkdir(parents=True, exist_ok=True)
            local_path = local_dir / Path(slot_path).name
            ok = extract_file_from_kpf(kpf_full, internal, str(local_path))
            if not ok:
                print(f"  WARNING [{label}]: extraction failed: {source_path}")
                continue
            mod_files[slot_path] = str(local_path)
            changed += 1

    total_files = sum(len(s) for s in sets)
    print(f"  SFX [{label}]: {changed}/{total_files} file(s) swapped across {len(sets)} sets")
    return mod_files

def _extract_and_swap(
    rng: random.Random,
    pool: list[str],
    kpf_files: list[str],
    kpf_index,
    out_root: Path,
    label: str,
) -> dict[str, str]:
    """
    Shuffle a flat list of internal KPF paths within themselves.
    Extracts source files locally with swapped slot names.
    Returns {internal_path: local_path} for mod packing.
    """
    from kpf_handler import find_file_in_kpf, extract_file_from_kpf

    if not pool:
        return {}

    sources = pool[:]
    rng.shuffle(sources)
    swap_map = dict(zip(pool, sources))

    mod_files: dict[str, str] = {}
    changed = 0

    for slot_path, source_path in swap_map.items():
        if slot_path == source_path:
            continue

        matches = find_file_in_kpf(kpf_index, source_path)
        if not matches:
            print(f"  WARNING [{label}]: source not found in KPF: {source_path}")
            continue

        internal, kpf_name = matches[0]
        kpf_full = str(Path(kpf_index.kpf_dir) / kpf_name)

        local_dir = out_root / Path(slot_path).parent
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / Path(slot_path).name

        ok = extract_file_from_kpf(kpf_full, internal, str(local_path))
        if not ok:
            print(f"  WARNING [{label}]: extraction failed: {source_path}")
            continue

        mod_files[slot_path] = str(local_path)
        changed += 1

    print(f"  SFX [{label}]: {changed}/{len(pool)} file(s) swapped")
    return mod_files


def shuffle_sfx(
    rng: random.Random,
    kpf_files: list[str],
    work_dir: str,
    shuffle_voices: bool = True,
    shuffle_weapons: bool = True,
    dry_run: bool = False,
) -> dict[str, str]:
    """
    Shuffle SFX pools and return {internal_kpf_path: local_path} for mod packing.
    """
    try:
        from kpf_handler import build_kpf_index, find_file_in_kpf
    except ImportError:
        print("  WARNING: kpf_handler.py not found — SFX shuffle skipped")
        return {}

    kpf_index = build_kpf_index(kpf_files)
    out_root = Path(work_dir) / "sfx_shuffle"
    out_root.mkdir(parents=True, exist_ok=True)
    mod_files: dict[str, str] = {}

    if dry_run:
        return {}

    # ── Voice lines ───────────────────────────────────────────────────────────
    if shuffle_voices:
        voice_pool = [
            path for path, _ in find_file_in_kpf(kpf_index, VOICE_PATH_PATTERN)
            if path.lower().endswith(".wav")
            and Path(path).stem not in EXCLUDED_VOICE_STEMS
        ]
        if voice_pool:
            mod_files.update(_extract_and_swap(
                rng, voice_pool, kpf_files, kpf_index, out_root, "voices"
            ))
        else:
            print(f"  WARNING: no voice files found at {VOICE_PATH_PATTERN}")

    # ── Weapon sounds ─────────────────────────────────────────────────────────
    if shuffle_weapons:
        if WEAPON_SOUND_SETS:
            for pool_name, sets in WEAPON_SOUND_SETS.items():
                mod_files.update(_swap_set_pool(
                    rng, sets, kpf_index, kpf_files, out_root, f"weapons/{pool_name}"
                ))
        else:
            print("  SFX [weapons]: WEAPON_SOUND_SETS is empty — populate in constants.py")

    return mod_files


def sfx_spoiler_section(mod_files: dict[str, str], work_dir: str) -> list[str]:
    out_root = Path(work_dir) / "sfx_shuffle"
    lines = [
        "",
        "── SFX SHUFFLE ─────────────────────────────────────────",
        "",
        f"  {'Slot (plays here)':<55}  {'Source (file used)'}",
        f"  {'─'*55}  {'─'*40}",
    ]
    for slot, local in sorted(mod_files.items()):
        try:
            source = Path(local).relative_to(out_root)
        except ValueError:
            source = Path(local).name
        lines.append(f"  {slot:<55}  {source}")
    return lines