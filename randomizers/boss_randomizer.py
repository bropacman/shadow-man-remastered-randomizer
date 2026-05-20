"""
boss_randomizer.py
──────────────────
Two-part boss shuffle for Shadow Man Remastered:

1. disable_boss_arenas(levels_path)
   Zeros the record count in enmevent.evt (and enmlinks.e2o where present)
   for each of the 5 serial killer boss levels, preventing the scripted arena
   fight trigger from firing.  The boss room becomes an ordinary room.

2. randomize_bosses_as_enemies(rng, config)
   Places each of the 5 serial killer boss RSC names exactly once into a
   randomly chosen ground enemy slot anywhere in the game (any context_group).
   The boss's original reward (dark soul item ID) travels with its RSC so
   killing the roaming boss drops the correct key item regardless of where
   it landed.

Together these turn boss encounters into a world-wide hunt: the arena is
empty, but each serial killer is hiding somewhere as a ground enemy.

Usage
─────
    from randomizers.boss_randomizer import (
        disable_boss_arenas, randomize_bosses_as_enemies, boss_spoiler_section
    )

    n = disable_boss_arenas(levels_path)
    patches, placement = randomize_bosses_as_enemies(rng, config)
    # patches:   {(level_id, source_file): {offset: patch_dict}} — feed to patch_rsc_file()
    # placement: {boss_rsc: (level_id, source_file, offset)}     — for spoiler log
"""

from __future__ import annotations
import random
from pathlib import Path


# ── Boss definitions ───────────────────────────────────────────────────────────

# RSC name → reward item ID (instance_id from the primary boss CSV record).
# This is the dark soul item that drops when the boss is killed.
BOSS_RSC_REWARDS: dict[str, int] = {
    "RSC_X_AVERY_MARX":  9,
    "RSC_X_BATRACHIAN":  10,
    "RSC_X_JACK":        11,
    "RSC_X_MARCO_CRUZ":  13,
    "RSC_X_MILTON_PIKE": 12,
}

# Human-readable names for spoiler output.
BOSS_FRIENDLY: dict[str, str] = {
    "RSC_X_AVERY_MARX":  "Avery Marx",
    "RSC_X_BATRACHIAN":  "Victor Batrachian",
    "RSC_X_JACK":        "Jack the Ripper",
    "RSC_X_MARCO_CRUZ":  "Marco Cruz",
    "RSC_X_MILTON_PIKE": "Milton Pike",
}

# Levels whose arena trigger files must be disabled.
_BOSS_LEVELS = ["tenement", "prison", "uground", "salvage", "florida"]

# Arena trigger files and the byte offset of their record count field.
# Both formats: 8-byte magic, then 2-byte record count at offset 8.
_TRIGGER_FILES = ["enmevent.evt", "enmlinks.e2o"]
_RECORD_COUNT_OFFSET = 8


# ── Part 1: Disable arena fight triggers ──────────────────────────────────────

def disable_boss_arenas(levels_path: Path) -> int:
    """
    Zero out the record count in enmevent.evt (and enmlinks.e2o if present)
    for each of the 5 serial killer levels, preventing the arena fight trigger.

    Both files use the same header layout:
        [0x00–0x07]  magic string (e.g. "Eevtv002")
        [0x08–0x09]  record count (big-endian uint16) ← zeroed here

    Returns the number of files patched.
    """
    patched = 0
    for level in _BOSS_LEVELS:
        for fname in _TRIGGER_FILES:
            path = levels_path / level / fname
            if not path.exists():
                continue
            data = bytearray(path.read_bytes())
            if len(data) < _RECORD_COUNT_OFFSET + 2:
                print(f"  WARNING: {level}/{fname} too small to patch — skipped")
                continue
            old_count = (data[_RECORD_COUNT_OFFSET] << 8) | data[_RECORD_COUNT_OFFSET + 1]
            if old_count == 0:
                continue  # already disabled
            data[_RECORD_COUNT_OFFSET]     = 0x00
            data[_RECORD_COUNT_OFFSET + 1] = 0x00
            path.write_bytes(bytes(data))
            patched += 1
    return patched


# ── Part 2: Place bosses as roaming enemies ────────────────────────────────────

def randomize_bosses_as_enemies(
    rng: random.Random,
    config: dict,
) -> tuple[dict[tuple[str, str], dict[int, dict]], dict[str, tuple]]:
    """
    Shuffle the 5 serial killer boss RSC names into 5 random ground enemy slots
    anywhere in the game (any context_group, movement_type=ground, exactly once
    each).  Each boss keeps its original reward (dark soul item ID) so killing
    the roaming boss drops the correct key item.

    Call AFTER the enemy randomizer so these patches override any enemy shuffle
    that already landed on those slots.

    Returns
    -------
    patches_by_folder
        {(level_id, source_file): {offset: patch_dict}}
        Merge over enemy patches and feed to patch_rsc_file().

    boss_placement
        {boss_rsc: (level_id, source_file, offset)}
        For spoiler logging.
    """
    from extracted_enemy_locations import SLOTS_BY_MOVEMENT

    # Dark Engine (as4dkeng) is gated behind retractors — a boss with a
    # required dark soul placed there could softlock seeds where the player
    # hasn't opened the Engine yet.  Block it from the candidate pool.
    _EXCLUDED_LEVELS: frozenset[str] = frozenset({"as4dkeng"})

    boss_rscs = list(BOSS_RSC_REWARDS.keys())
    rng.shuffle(boss_rscs)

    # All ground enemy slots except excluded levels, in random order.
    # Slots with a null/empty sub_region are unverified — exclude them, same
    # as the true form randomizer does via _slot_is_mapped().
    ground_slots = [
        s for s in SLOTS_BY_MOVEMENT.get("ground", [])
        if s.level_id not in _EXCLUDED_LEVELS
        and s.level_region
        and s.sub_region
    ]
    rng.shuffle(ground_slots)

    patches_by_folder: dict[tuple[str, str], dict[int, dict]] = {}
    boss_placement:    dict[str, tuple] = {}

    # Pick the first 5 distinct slots (shuffled list guarantees no repeats).
    for boss_rsc, slot in zip(boss_rscs, ground_slots):
        key = (slot.level_id, slot.source_file)
        patches_by_folder.setdefault(key, {})[slot.offset] = {
            "name":        boss_rsc,
            "reward":      BOSS_RSC_REWARDS[boss_rsc],
            "logic":       int(slot.zone) if slot.zone else 0,
            "y_adjust":    0.0,
            "source_file": slot.source_file,
        }
        boss_placement[boss_rsc] = (slot.level_id, slot.source_file, slot.offset)
        print(f"  {BOSS_FRIENDLY.get(boss_rsc, boss_rsc):<22}"
              f" → {slot.level_id}/{slot.source_file} @ 0x{slot.offset:04X}")

    return patches_by_folder, boss_placement


# ── Spoiler ────────────────────────────────────────────────────────────────────

# Level display names — expanded to cover the full game since bosses can land
# anywhere.  Falls back to level_id if a name isn't listed here.
LEVEL_NAMES: dict[str, str] = {
    "tenement":  "Mordant Street, Queens, NY",
    "prison":    "Gardelle County Jail, Texas",
    "uground":   "Down Street Station, London",
    "salvage":   "Salvage Yard, Mojave Desert",
    "florida":   "Summer Camp, Florida",
    "swampday":  "Louisiana Swamp",
    "london":    "Down Street Station, London",
    "queens":    "Mordant Street, Queens, NY",
    "deadside":  "Deadside",
    "wastland":  "Wasteland",
    "asylum":    "Asylum",
    "as2exper":  "Asylum: Experimentation Rooms",
}


def boss_spoiler_section(boss_placement: dict[str, tuple]) -> list[str]:
    """
    Build a human-readable spoiler section from the placement dict returned
    by randomize_bosses_as_enemies().
    """
    lines = [
        "",
        "── BOSS SHUFFLE ─────────────────────────────────────────",
        "",
        "  Arena fights disabled — serial killers are hiding in the world:",
        "",
    ]

    col = max(len(BOSS_FRIENDLY.get(r, r)) for r in boss_placement) + 2
    for boss_rsc, (level_id, source_file, offset) in sorted(
        boss_placement.items(), key=lambda kv: BOSS_FRIENDLY.get(kv[0], kv[0])
    ):
        name       = BOSS_FRIENDLY.get(boss_rsc, boss_rsc)
        level_name = LEVEL_NAMES.get(level_id, level_id)
        lines.append(
            f"  {name:<{col}}  {level_name} [{source_file}] @ 0x{offset:04X}"
        )

    lines.append("")
    lines.append(f"  {len(boss_placement)}/5 serial killers displaced")
    return lines
