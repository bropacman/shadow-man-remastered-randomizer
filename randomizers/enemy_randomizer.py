"""
enemy_randomizer.py
───────────────────
Enemy type shuffler for Shadow Man Remastered randomizer.

Three modes, selected via config["enemy_mode"]:

  "difficulty"  — DEFAULT. Sorts enemies into difficulty tiers (1-5) defined in
                  constants.ENEMY_DIFFICULTY, then assigns enemies to slots based
                  on region depth. Early regions receive low-tier enemies; deep,
                  late-game regions receive high-tier enemies. Falls back to
                  full-random for any slot missing level_region data.
                  Always respects movement_type.

  "full"        — Shuffles globally across all levels within each movement_type
                  bucket. No difficulty weighting — purely random.

  "contextual"  — Shuffles within each (context_group, movement_type) bucket.
                  A deadside_interior ground enemy only swaps with other
                  deadside_interior ground enemies across all levels.

In all modes:
  - Only category == "enemy" records are shuffled.
  - category == "enemy_locked" records are never modified.
  - movement_type is ALWAYS respected — ground/flying/swimming never mix.
  - Slot positions are fixed — only RSC_ names change.
  - Slots occupied by true form patches are skipped.

Difficulty tiers (constants.ENEMY_DIFFICULTY):
  1 — Basic fodder (Deadworms, Deadsiders, Zombies)
  2 — Common early/mid (Guards, Dogs, Gators)
  3 — Mid-game threats (Hookmen, Surgeons, Grinders, Seraphs)
  4 — Elite/dangerous (Grinder Shields, Painkillers, HD variants)
  5 — Hardest regulars (Adepts, Matriarchs)
  0 — Special/never placed by difficulty mode (True Form placeholder)

Adding new enemy types:
  - Add entry to constants.ENEMY_DIFFICULTY with appropriate tier
  - Flip category to "enemy" in enemy_locations.csv
  - Set context_group, movement_type, level_region, sub_region
  - Re-run generate_enemies.py
  - No code changes needed.
"""

from __future__ import annotations
import random
from pathlib import Path
from collections import defaultdict

# Movement types to keep isolated even when enemy_mix_movement is enabled.
_MIXED_MOVEMENT_EXCLUDE: frozenset[str] = frozenset()



def randomize_enemies(
    rng: random.Random,
    levels_path: Path,
    config: dict,
    true_form_patches: dict[tuple[str, str], dict[int, dict]] | None = None,
    gate_remap: dict[str, int] | None = None,   # ← ADD
) -> dict[tuple[str, str], dict[int, dict]]:
    """
    Shuffle enemy types and return patches_by_folder.
    Shape: {(level_id, source_file): {offset: {"name": str, ...}}}
    Pass directly to patch_rsc_file().
    """
    from extracted_enemy_locations import (
        SLOTS_BY_CONTEXT, SLOTS_BY_MOVEMENT,
        AMBIENT_BY_CONTEXT, AMBIENT_BY_MOVEMENT,
    )
    mode         = config.get("enemy_mode", "difficulty")
    mix_movement = config.get("enemy_mix_movement", False)
    patches_by_folder: dict[tuple[str, str], dict[int, dict]] = {}
    total_shuffled = 0
    total_skipped  = 0

    occupied_keys: set[str] = set()
    for (lid, sf), patches in (true_form_patches or {}).items():
        for offset in patches:
            occupied_keys.add(f"{lid}:{sf}:0x{offset:04X}")

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
            if rec.loc_key in occupied_keys:
                total_skipped += 1
                continue
            if new_name == rec.object:
                total_skipped += 1
                continue
            patches_by_folder.setdefault(
                (rec.level_id, rec.source_file), {}
            )[rec.offset] = _make_patch(rec, new_name)
            total_shuffled += 1

    if mode in ("full", "difficulty"):
        if mix_movement:
            # Combine all slots into one pool, keeping _MIXED_MOVEMENT_EXCLUDE types isolated.
            all_slots  = [s for mv_slots in SLOTS_BY_MOVEMENT.values() for s in mv_slots]
            to_mix     = [s for s in all_slots if s.movement_type not in _MIXED_MOVEMENT_EXCLUDE]
            to_isolate: dict[str, list] = defaultdict(list)
            for s in all_slots:
                if s.movement_type in _MIXED_MOVEMENT_EXCLUDE:
                    to_isolate[s.movement_type].append(s)
            # Mixed group uses "_any_" as the movement key; isolated groups use their own type.
            slot_groups = [("_any_", to_mix)] + [(mt, sl) for mt, sl in sorted(to_isolate.items())]
        else:
            slot_groups = list(sorted(SLOTS_BY_MOVEMENT.items()))

        for movement, slots in slot_groups:
            if mode == "full":
                names = [r.object for r in slots]
                rng.shuffle(names)
                _apply(slots, names)
            else:
                # Build (movement_key, tier) name pools — movement_key is "_any_" when mixing.
                diff_buckets = _build_difficulty_buckets(slots, gate_remap, movement_key=movement)
                # Shuffle each pool so draws are random within tier
                for pool in diff_buckets.values():
                    rng.shuffle(pool)
                # Cursors track position in each pool
                cursors: dict[tuple[str, int], int] = defaultdict(int)

                names = []
                unmapped = []
                for rec in slots:
                    depth = _total_depth(rec.level_region, rec.sub_region or "N", gate_remap)
                    if depth == 0 and not rec.level_region:
                        # No region data — fall back to full random, handle after
                        unmapped.append((len(names), rec))
                        names.append(None)
                        continue
                    target_tier = _depth_to_tier(depth)
                    target_tier = rng.choices([1, 2, 3, 4, 5], weights=_TIER_WEIGHTS[target_tier])[0]
                    # Try target tier, then expand outward ±1, ±2
                    chosen = None
                    for delta in (0, 1, -1, 2, -2, 3, -3, 4, -4):
                        t = target_tier + delta
                        if t < 1 or t > 5:
                            continue
                        key = (movement, t)
                        pool = diff_buckets.get(key)
                        if pool:
                            idx = cursors[key] % len(pool)
                            chosen = pool[idx]
                            cursors[key] += 1
                            break
                    names.append(chosen or rec.object)  # no-op if nothing found

                # Handle unmapped slots — full random from whole movement pool
                if unmapped:
                    fallback_pool = [r.object for r in slots]
                    rng.shuffle(fallback_pool)
                    fb_idx = 0
                    for slot_idx, rec in unmapped:
                        names[slot_idx] = fallback_pool[fb_idx % len(fallback_pool)]
                        fb_idx += 1

                _apply(slots, names)
    else:  # contextual
        if mix_movement:
            # Group by context_group only; movement types not in _MIXED_MOVEMENT_EXCLUDE pool freely.
            by_group: dict[str, list] = defaultdict(list)
            isolated_ctx: dict[tuple[str, str], list] = {}
            for (group, movement), slots in SLOTS_BY_CONTEXT.items():
                if movement in _MIXED_MOVEMENT_EXCLUDE:
                    isolated_ctx[(group, movement)] = slots
                else:
                    by_group[group].extend(slots)
            for group, slots in sorted(by_group.items()):
                names = [r.object for r in slots]
                rng.shuffle(names)
                _apply(slots, names)
            for (group, movement), slots in sorted(isolated_ctx.items()):
                names = [r.object for r in slots]
                rng.shuffle(names)
                _apply(slots, names)
        else:
            for (group, movement), slots in sorted(SLOTS_BY_CONTEXT.items()):
                names = [r.object for r in slots]
                rng.shuffle(names)
                _apply(slots, names)


    _print_summary(patches_by_folder, total_shuffled, total_skipped, mode)
    return patches_by_folder


def enemy_spoiler_section(
    patches_by_folder: dict[tuple[str, str], dict[int, dict]],
    header: str = "── ENEMY SHUFFLE ───────────────────────────────────────",
) -> list[str]:
    from extracted_enemy_locations import ENEMY_TABLE

    LEVEL_NAMES = {
        "ah1cagew": "Cageways",        "ah2playr": "Playrooms",
        "ah3lavad": "Lavaducts",       "ah4fogom": "Fogometers",
        "as2exper": "Experimentation", "as3schis": "Schism Chambers",
        "as4dkeng": "Dark Engine",     "asylum":   "Asylum Gateway",
        "deadside": "Deadside",        "wastland": "Wasteland",
        "t1tchgad": "Temple of Fire",  "t3swmgad": "Temple of Blood",
        "prison":   "Texas Prison",    "nprison":  "Texas Prison (Night)",
        "salvage":  "Salvage Yard",
    }

    lines = ["", "── ENEMY SHUFFLE ───────────────────────────────────────", ""]
    for (folder, source_file), patches in sorted(patches_by_folder.items()):
        if not patches:
            continue
        level_name = LEVEL_NAMES.get(folder, folder)
        lines.append(f"  {level_name} [{source_file}]  ({len(patches)} changes)")
        for offset, pd in sorted(patches.items()):
            loc_key   = f"{folder}:{source_file}:0x{offset:04X}"
            original  = ENEMY_TABLE.get(loc_key)
            orig_name = original.object if original else "???"
            lines.append(f"    0x{offset:04X}  {orig_name:<35} -> {pd['name']}")
        lines.append("")

    total = sum(len(p) for p in patches_by_folder.values())
    lines.append(f"  Total enemy slots changed: {total}")
    return lines


def _print_summary(patches_by_folder: dict, total_shuffled: int,
                   total_skipped: int, mode: str) -> None:
    from collections import Counter
    type_counter: Counter = Counter()
    for patches in patches_by_folder.values():
        for pd in patches.values():
            type_counter[pd["name"]] += 1

    levels_changed = len({folder for folder, _ in patches_by_folder})
    print(f"  Legion's minions reshuffled [{mode}]: {total_shuffled} creatures displaced "
          f"across {levels_changed} levels  ({total_skipped} unchanged)")
    if type_counter:
        top = type_counter.most_common(5)
        print(f"  Top placements: "
              + "  ".join(f"{n}x {t.replace('RSC_','')}" for t, n in top))

TRUE_FORM_SWAP_CONTEXT_GROUPS: frozenset[str] = frozenset({
    "deadside",
    "liveside_night"
})

_TRUE_FORM_INELIGIBLE_LEVELS: frozenset[str] = frozenset({
    "swampday",
    "salvage",
    "florida",
})

def _sub_region_depth(sub_region: str | None) -> int:
    if not sub_region or sub_region == "N":
        return 0
    s = sub_region.upper()
    depth = 0
    if "ENG_KEY"   in s: depth += 1
    if "GAD2_WALK" in s: depth += 2
    if "GAD3_SWIM" in s: depth += 5
    if "GAD1_HAND" in s: depth += 1
    if "NIGHT"     in s: depth += 15
    if "CALABASH"  in s: depth += 7
    if "MARTEAU"   in s: depth += 6
    if "BATON"     in s: depth += 6
    if "FLAMBEAU"  in s: depth += 5
    if "POIGNE"    in s: depth += 4
    return depth


def _region_sl_depth(level_region: str | None, gate_remap: dict[str, int] | None) -> int:
    if not level_region:
        return 0
    from fill import REGION_GATES
    from access_rules import GATE_VANILLA_SL

    gate = REGION_GATES.get(level_region)
    if gate is None:
        return 0
    effective = gate_remap or GATE_VANILLA_SL
    if isinstance(gate, str):
        return effective.get(gate, GATE_VANILLA_SL.get(gate, 0))
    return min(
        max(effective.get(g, GATE_VANILLA_SL.get(g, 0)) for g in route)
        for route in gate
    )


def _total_depth(level_region: str | None,
                 sub_region:   str | None,
                 gate_remap:   dict[str, int] | None) -> int:
    return _region_sl_depth(level_region, gate_remap) + _sub_region_depth(sub_region)

def _depth_to_tier(depth: int) -> int:
    if depth <= 2:  return 1
    if depth <= 5:  return 2
    if depth <= 9:  return 3
    if depth <= 14: return 4
    return 5


# Weighted tier draw for difficulty mode.
# Each row = base tier (1-5), columns = probability weights for tiers 1-5.
# Higher base tier shifts the distribution right while keeping low-tier tails,
# so early areas still feel manageable but can occasionally surprise you.
_TIER_WEIGHTS: dict[int, list[int]] = {
    1: [60, 20, 10, 5, 5],
    2: [40, 30, 20, 5, 5],
    3: [25, 30, 30, 10, 5],
    4: [15, 15, 30, 25, 15],
    5: [10, 10, 20, 25, 35],
}


def _build_difficulty_buckets(
    slots: list,
    gate_remap: dict[str, int] | None,
    movement_key: str | None = None,
) -> dict[tuple[str, int], list[str]]:
    """
    Returns {(movement_key, tier): [rsc_name, ...]} pool to draw from.
    movement_key overrides rec.movement_type — pass "_any_" when mixing movement types
    so that the bucket key matches what the caller looks up.
    """
    from constants import ENEMY_DIFFICULTY, ENEMY_DIFFICULTY_DEFAULT
    buckets: dict[tuple[str, int], list[str]] = defaultdict(list)
    for rec in slots:
        mt   = movement_key if movement_key is not None else rec.movement_type
        tier = ENEMY_DIFFICULTY.get(rec.object, ENEMY_DIFFICULTY_DEFAULT)
        key  = (mt, tier)
        buckets[key].append(rec.object)
    return buckets

def _slot_is_mapped(rec) -> bool:
    return bool(rec.level_region) and rec.sub_region is not None and rec.sub_region != ""


def randomize_true_forms(
    rng: random.Random,
    gate_remap: dict[str, int] | None = None,
    swap_context_groups: frozenset[str] = TRUE_FORM_SWAP_CONTEXT_GROUPS,
) -> tuple[dict[tuple[str, str], dict[int, dict]], dict[str, str]]:
    from extracted_enemy_locations import ENEMY_TABLE

    eligible = [
        rec for rec in ENEMY_TABLE.values()
        if rec.category == "enemy"
        # and rec.context_group in swap_context_groups
       and rec.level_id not in _TRUE_FORM_INELIGIBLE_LEVELS
       and rec.movement_type == "ground"
       and _slot_is_mapped(rec)
    ]

    if not eligible:
        return {}, {}

    true_form_keys: frozenset[str] = frozenset(
        rec.loc_key for rec in eligible if rec.object == "RSC_X_TRUE_FORM"
    )

    if not true_form_keys:
        return {}, {}

    depths = {
        rec.loc_key: _total_depth(rec.level_region, rec.sub_region, gate_remap)
        for rec in eligible
    }

    buckets: dict[int, list] = defaultdict(list)
    for rec in eligible:
        buckets[depths[rec.loc_key]].append(rec)

    patches_by_folder: dict[tuple[str, str], dict[int, dict]] = {}
    loc_key_remap: dict[str, str] = {}

    for depth, bucket in sorted(buckets.items()):
        indices  = list(range(len(bucket)))
        shuffled = list(indices)
        if len(bucket) > 1:
            attempts = 0
            while shuffled == indices and attempts < 20:
                rng.shuffle(shuffled)
                attempts += 1

            # Ensure no true form is a fixed point (maps back to its own slot).
            # A random permutation of a large bucket gives each TF a ~1/n chance
            # of staying in place; resolve those with targeted swaps.
            tf_idx_set = {i for i, rec in enumerate(bucket)
                          if rec.loc_key in true_form_keys}
            for tf_idx in sorted(tf_idx_set):
                if shuffled[tf_idx] == tf_idx:
                    # Prefer a swap target whose current destination isn't tf_idx
                    # (avoids creating a new fixed point at the swap partner).
                    candidates = [j for j in range(len(bucket))
                                  if j != tf_idx and shuffled[j] != tf_idx]
                    if not candidates:          # degenerate: fall back to any other position
                        candidates = [j for j in range(len(bucket)) if j != tf_idx]
                    swap_with = rng.choice(candidates)
                    shuffled[tf_idx], shuffled[swap_with] = shuffled[swap_with], shuffled[tf_idx]

        for src_idx, dst_idx in enumerate(shuffled):
            src = bucket[src_idx]
            dst = bucket[dst_idx]

            if src.loc_key == dst.loc_key:
                if src.loc_key in true_form_keys:
                    loc_key_remap[src.loc_key] = dst.loc_key
                continue

            # ← ADD THIS: skip swaps that don't involve a true form at all
            if src.loc_key not in true_form_keys and dst.loc_key not in true_form_keys:
                continue

            key = (dst.level_id, dst.source_file)
            reward = src.save_idx if src.loc_key in true_form_keys and src.save_idx else \
                (dst.save_idx if dst.save_idx else 0)
            patches_by_folder.setdefault(key, {})[dst.offset] = {
                "name": src.object,
                "reward": reward,
                "logic": int(dst.zone) if dst.zone else 0,
                "y_adjust": 0.0,
                "source_file": dst.source_file,
            }

            if src.loc_key in true_form_keys:
                loc_key_remap[src.loc_key] = dst.loc_key

    return patches_by_folder, loc_key_remap


def true_form_spoiler_section(loc_key_remap: dict[str, str]) -> list[str]:
    from extracted_enemy_locations import ENEMY_TABLE

    lines = ["", "── TRUE FORM SHUFFLE ───────────────────────────────────", ""]
    moved = [(v, n) for v, n in sorted(loc_key_remap.items()) if v != n]
    if not moved:
        lines.append("  (no true forms relocated)")
    else:
        for vanilla_key, new_key in moved:
            v = ENEMY_TABLE.get(vanilla_key)
            n = ENEMY_TABLE.get(new_key)
            v_label = f"{(v.level_region or v.level_id) if v else vanilla_key} {vanilla_key.split(':')[-1]}"
            n_label = f"{(n.level_region or n.level_id) if n else new_key} {new_key.split(':')[-1]}"
            lines.append(f"  {v_label:<55} -> {n_label}")
        lines.append(f"\n  Total true forms relocated: {len(moved)}")
    return lines
