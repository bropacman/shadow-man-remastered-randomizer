"""
ambient_randomizer.py
─────────────────────
Shuffles friendly/ambient creature types for Shadow Man Remastered.

Three modes, selected via config["ambient_mode"]:

  "global"      — DEFAULT. Throws every ambient into one pool and shuffles
                  freely — no movement_type or context_group bucketing.
                  A rat can become a fish, a butterfly can become an egret.

  "full"        — Shuffles globally across all levels within each
                  movement_type bucket. A rat in the swamp can become any other
                  ground ambient across all levels, but ground/flying/swimming
                  never mix.

  "contextual"  — Shuffles within each (context_group, movement_type) bucket.
                  A liveside_day ground ambient only swaps with other
                  liveside_day ground ambients.

In all modes:
  - Only category == "ambient" records are shuffled.
  - category == "ambient_locked" records are never modified.
  - Slot positions are fixed — only RSC_ names change.

Ambient creature pools by movement_type:
  ground   — rats
  flying   — egrets, flies, butterflies
  swimming — friendly fish

Adding new ambient types:
  - Add rows to data/enemy_locations.csv with category="ambient"
  - Set context_group (e.g. "liveside_day"), movement_type, level_region
  - Re-run tools/generate_enemies.py
  - No code changes needed.
"""

from __future__ import annotations
import random
from pathlib import Path
from collections import defaultdict


def randomize_ambients(
    rng: random.Random,
    levels_path: Path,
    config: dict,
) -> dict[tuple[str, str], dict[int, dict]]:
    """
    Shuffle ambient creature types and return patches_by_folder.
    Shape: {(level_id, source_file): {offset: {"name": str, ...}}}
    Pass directly to patch_rsc_file().
    """
    from extracted_enemy_locations import AMBIENT_ALL, AMBIENT_BY_CONTEXT, AMBIENT_BY_MOVEMENT

    mode = config.get("ambient_mode", "global")
    patches_by_folder: dict[tuple[str, str], dict[int, dict]] = {}
    total_shuffled = 0
    total_skipped  = 0

    def _make_patch(rec, new_name: str) -> dict:
        return {
            "name":        new_name,
            "reward":      rec.save_idx if rec.save_idx else 0,
            "logic":       int(rec.zone) if rec.zone else 0,
            "y_adjust":    0.0,
            "source_file": rec.source_file,
        }

    def _apply(slots, names):
        nonlocal total_shuffled, total_skipped
        for rec, new_name in zip(slots, names):
            if new_name is None:
                total_skipped += 1
                continue
            if new_name == rec.object:
                total_skipped += 1
                continue
            patches_by_folder.setdefault(
                (rec.level_id, rec.source_file), {}
            )[rec.offset] = _make_patch(rec, new_name)
            total_shuffled += 1

    if mode == "global":
        # One pool, no bucketing — any ambient can become any other ambient.
        names = [r.object for r in AMBIENT_ALL]
        rng.shuffle(names)
        _apply(AMBIENT_ALL, names)
    elif mode == "full":
        for movement, slots in sorted(AMBIENT_BY_MOVEMENT.items()):
            names = [r.object for r in slots]
            rng.shuffle(names)
            _apply(slots, names)
    else:  # contextual
        for (group, movement), slots in sorted(AMBIENT_BY_CONTEXT.items()):
            names = [r.object for r in slots]
            rng.shuffle(names)
            _apply(slots, names)

    _print_summary(patches_by_folder, total_shuffled, total_skipped, mode)
    return patches_by_folder


def ambient_spoiler_section(
    patches_by_folder: dict[tuple[str, str], dict[int, dict]],
) -> list[str]:
    from extracted_enemy_locations import ENEMY_TABLE

    LEVEL_NAMES = {
        "swampday":  "Louisiana Swamp",
        "florida":   "Summer Camp, Florida",
        "london":    "Down Street Station, London",
        "prison":    "Gardelle County Jail, Texas",
        "salvage":   "Salvage Yard, Mojave Desert",
        "queens":    "Mordant Street, Queens, NY",
        "deadside":  "Deadside",
        "wastland":  "Wasteland",
    }

    lines = ["", "── AMBIENT SHUFFLE ─────────────────────────────────────", ""]
    for (folder, source_file), patches in sorted(patches_by_folder.items()):
        if not patches:
            continue
        level_name = LEVEL_NAMES.get(folder, folder)
        lines.append(f"  {level_name} [{source_file}]  ({len(patches)} changes)")
        for offset, pd in sorted(patches.items()):
            loc_key  = f"{folder}:{source_file}:0x{offset:04X}"
            original = ENEMY_TABLE.get(loc_key)
            orig_name = original.object if original else "???"
            lines.append(f"    0x{offset:04X}  {orig_name:<35} -> {pd['name']}")
        lines.append("")

    total = sum(len(p) for p in patches_by_folder.values())
    lines.append(f"  Total ambient slots changed: {total}")
    return lines


def _print_summary(
    patches_by_folder: dict,
    total_shuffled: int,
    total_skipped: int,
    mode: str,
) -> None:
    from collections import Counter
    type_counter: Counter = Counter()
    for patches in patches_by_folder.values():
        for pd in patches.values():
            type_counter[pd["name"]] += 1

    levels_changed = len({folder for folder, _ in patches_by_folder})
    print(f"  Ambient creatures reshuffled [{mode}]: {total_shuffled} critters displaced "
          f"across {levels_changed} levels  ({total_skipped} unchanged)")
    if type_counter:
        top = type_counter.most_common(5)
        print(f"  Top placements: "
              + "  ".join(f"{n}x {t.replace('RSC_','')}" for t, n in top))
