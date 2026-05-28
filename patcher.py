"""
Shadow Man Remastered - Core Randomizer Patcher
================================================
Handles four types of randomization:
  1. Item shuffling in quest.rsc / fx.rsc / instance.rsc (dark souls, key items, weapons)
     Key/progression items use assumed fill (fill.py) to guarantee beatable seeds.
     Souls, cadeaux, and barrels use pool-based shuffle (no logic dependency).
  2. Cadeaux/soul pool mixing
  3. Soul level gate shuffling via links.e2o - shuffles the +0x2E SL threshold
     field in each 0x0C00 record independently per gate. SL0 and SL10 are always
     locked. ARC decoration names in deadside/events.rsc are updated to match.
  4. Scripted EXE reward patching (Gad temples, Flambeau) via exe_patcher.py

assumed_fill() is called ONCE in run_patcher() and its result is shared between
randomize_items() (RSC patches) and randomize_scripted_rewards() (EXE patches).
This guarantees key items can't be double-placed regardless of which system
receives them.

Level identity throughout is the folder name (e.g. "deadside"), never a numeric ID.
This matches fill.py, extracted_locations.py, and the filesystem.

Usage:
    python patcher.py --seed 12345 --game-dir "C:/.../Shadow Man Remastered" --config config.yaml
"""

import sys
import os
import re
import csv
import json
import shutil
import struct
import random
import argparse
import yaml
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from constants import (
    LEVEL_FOLDERS, SOUL_RSC_FILES, ENEMY_RSC_FILES, GATE_VANILLA_SL, GATE_PRESETS,
    CADEAU_HEIGHT_DROP, GOVI_HEIGHT_BOOST, ITEM_Y_ADJUST,
    SOUL_SLOT_MARKER_FX, SOUL_SLOT_MARKER_FX_Y, DARK_SOUL_SLOT_MARKER_FX_Y, DAY_NIGHT_MIRRORS, GAD_PICKUP_EXPECTED_OFFSETS,
    STARTING_ITEM_POOL, ASSET_OVERRIDES, MSH_OVERRIDES, BARREL_SLOT_MARKER_FX, BARREL_SLOT_MARKER_FX_Y,
    GAD_BLOCKER_RSC, GAD_BLOCKER_SITES, GAD_INJECTION_SITES, GAD_ASSET_OVERRIDES,
    GATE_E2O_POSITIONS, E2O_MATCH_RADIUS, LEVEL_NAMES, BARREL_RSC_SUBSTITUTIONS,
    PROGRESSION_IN_GOVI_LIFT, DARK_SOUL_SLOT_ITEM_DROP, PROGRESSION_IN_CADEAUX_LIFT, PROGRESSION_IN_BARREL_LIFT,
)
from fill import (
    simulate_playthrough, CHECKABLE_LOCS, FIXED_SOUL_LOCS,
    apply_true_form_remap, STARTING_ITEMS, assumed_fill, validate_fill, _shuffle_gates,
    UNVERIFIED_LOCS, EXCLUDED_LEVELS,
)
import regions as _regions
from randomizers.enemy_randomizer import (
    randomize_enemies, enemy_spoiler_section,
    randomize_true_forms, true_form_spoiler_section,
)
# boss_randomizer import intentionally omitted — boss shuffle shelved pending
# enmevent.evt fight-trigger investigation.  Re-add when ready:
#   from randomizers.boss_randomizer import (
#       disable_boss_arenas, randomize_bosses_as_enemies, boss_spoiler_section,
#   )
from randomizers.ambient_randomizer import randomize_ambients, ambient_spoiler_section
from randomizers.entrance_randomizer import (
    UNIFIED_TRANSITIONS, apply_unified_shuffle, shuffle_unified, unified_spoiler_section,
)
from randomizers.music_randomizer import shuffle_music
from randomizers.sfx_randomizer import shuffle_sfx, sfx_spoiler_section
from randomizers.sky_randomizer import shuffle_sky, sky_spoiler_section
from gad_pickup_patch import apply_gad_pickup_patch, apply_prison_keycard_patch
from cadeaux_patch import apply_cadeau_step_patch
from health_patch import apply_health_patch
from death_penalty_patch import apply_death_penalty_patch
from soul_threshold_patch import (
    SOUL_THRESHOLD_MODES,
    VANILLA_SOUL_THRESHOLDS as _VANILLA_SL_THRESH_PATCH,
    randomize_soul_thresholds,
    apply_soul_threshold_patch,
)
from setup_gad_records import inject_record, _find_existing
from rsc_utils import inject_rsc_record, build_rsc_record
from patchers.levels_txt_patcher import patch_levels_txt, strip_levels_txt
from patchers.loc_english_patcher import patch_loc_english_for_tracker
from kpf_handler import (
    find_kpf_files, build_kpf_index, extract_game_files, which_kpf_has_levels,
    find_file_in_kpf, extract_file_from_kpf, build_and_install_mod,
    find_mods_dir, remove_mod_kpf,
)

if getattr(sys, 'frozen', False):
    # If the app is running as a bundle (exe)
    bundle_dir = sys._MEIPASS
    if bundle_dir not in sys.path:
        sys.path.append(bundle_dir)

# ── Constants ─────────────────────────────────────────────────────────────────

HEADER_SIZE  = 8     # "Erscv002" file magic, sits before first record
RECORD_SIZE  = 72    # every record is exactly 72 bytes, no exceptions
NAME_OFF     = 0x22  # byte offset within a record where the RSC_ string begins
NAME_MAXLEN  = 30    # max bytes available for the name before the next field
ZONE_OFF     = 0x11  # zone/cluster group this record belongs to — read-only, never written
SAVE_IDX_OFF = 0x1E  # save-game ID — 4-byte big-endian; NAME_OFF - SAVE_IDX_OFF = 4
INSTANCE_OFF = 0x21  # last byte of SAVE_IDX (kept for reference; use SAVE_IDX_OFF for reads/writes)
XYZ_OFF      = 0x04  # start of the three little-endian floats for world position (X, Y, Z)

_RSC_TO_FRIENDLY = {v: k for k, v in STARTING_ITEM_POOL.items()}


def _int_or_random(val: str):
    """Argparse type that accepts an integer or the literal string 'random'."""
    if val == "random":
        return "random"
    return int(val)


def _resolve_random_config(config: dict, rng: random.Random) -> None:
    """Resolve 'random' sentinel values using the seeded RNG.

    Called once at the very start of run_patcher(), before any other processing,
    so that the spoiler log and all downstream code see concrete values.
    Mirrors the same defaults and clamping used in cadeaux_patch / health_patch.
    """
    if config.get("gate_preset") == "random":
        config["gate_preset"] = rng.choice(["open", "easy", "medium", "hard", "chaos"])
        print(f"  [random] gate_preset → {config['gate_preset']}")
    if config.get("entrance_mode") == "random":
        config["entrance_mode"] = rng.choice(["off", "deadside_only", "cross_hub"])
        print(f"  [random] entrance_mode → {config['entrance_mode']}")
    if str(config.get("insanity", 0)) == "random":
        config["insanity"] = rng.randint(0, 3)
        print(f"  [random] insanity → {config['insanity']}")
    if config.get("enemy_mode") == "random":
        config["enemy_mode"] = rng.choice(["difficulty", "contextual", "full"])
        print(f"  [random] enemy_mode → {config['enemy_mode']}")
    if str(config.get("shuffle_enemies", False)) == "random":
        config["shuffle_enemies"] = rng.choice([True, False])
        print(f"  [random] shuffle_enemies → {config['shuffle_enemies']}")
    if str(config.get("shuffle_true_forms", False)) == "random":
        config["shuffle_true_forms"] = rng.choice([True, False])
        print(f"  [random] shuffle_true_forms → {config['shuffle_true_forms']}")
    if str(config.get("enemy_mix_movement", False)) == "random":
        config["enemy_mix_movement"] = rng.choice([True, False])
        print(f"  [random] enemy_mix_movement → {config['enemy_mix_movement']}")
    if str(config.get("enemy_uncap_counts", False)) == "random":
        config["enemy_uncap_counts"] = rng.choice([True, False])
        print(f"  [random] enemy_uncap_counts → {config['enemy_uncap_counts']}")
    if str(config.get("progression_balancing", 50)) == "random":
        config["progression_balancing"] = rng.randint(0, 100)
        print(f"  [random] progression_balancing → {config['progression_balancing']}")
    if str(config.get("soul_threshold_mode", "off")) == "random":
        config["soul_threshold_mode"] = rng.choice(list(SOUL_THRESHOLD_MODES))
        print(f"  [random] soul_threshold_mode → {config['soul_threshold_mode']}")
    if str(config.get("death_penalty", 0)) == "random":
        config["death_penalty"] = rng.randint(1, 10)
        print(f"  [random] death_penalty → step {config['death_penalty']} (-{config['death_penalty'] * 1000}/death)")
    if str(config.get("shuffle_progression", True)) == "random":
        config["shuffle_progression"] = rng.choice([True, False])
        print(f"  [random] shuffle_progression → {config['shuffle_progression']}")
    if str(config.get("shuffle_weapons", True)) == "random":
        config["shuffle_weapons"] = rng.choice([True, False])
        print(f"  [random] shuffle_weapons → {config['shuffle_weapons']}")
    if str(config.get("shuffle_lore", True)) == "random":
        config["shuffle_lore"] = rng.choice([True, False])
        print(f"  [random] shuffle_lore → {config['shuffle_lore']}")
    if str(config.get("shuffle_bonus", False)) == "random":
        config["shuffle_bonus"] = rng.choice([True, False])
        print(f"  [random] shuffle_bonus (light soul) → {config['shuffle_bonus']}")
    if str(config.get("shuffle_gad_temples", True)) == "random":
        config["shuffle_gad_temples"] = rng.choice([True, False])
        print(f"  [random] shuffle_gad_temples → {config['shuffle_gad_temples']}")
    config["shuffle_prisms"] = False  # not yet implemented
    if str(config.get("shuffle_retractors", True)) == "random":
        config["shuffle_retractors"] = rng.choice([True, False])
        print(f"  [random] shuffle_retractors → {config['shuffle_retractors']}")
    if str(config.get("shuffle_accumulators", True)) == "random":
        config["shuffle_accumulators"] = rng.choice([True, False])
        print(f"  [random] shuffle_accumulators → {config['shuffle_accumulators']}")
    if str(config.get("shuffle_eclipsers", True)) == "random":
        config["shuffle_eclipsers"] = rng.choice([True, False])
        print(f"  [random] shuffle_eclipsers → {config['shuffle_eclipsers']}")

    # ── Health (mirrors health_patch.py defaults/clamping) ───────────────────
    if str(config.get("starting_health", 5)) == "random":
        lo = int(config.get("starting_health_min", 1))
        hi = int(config.get("starting_health_max", 10))
        config["starting_health"] = rng.randint(lo, hi)
        print(f"  [random] starting_health → {config['starting_health']}/10")
    if str(config.get("altar_health_grant", 1)) == "random":
        lo = int(config.get("altar_health_grant_min", 1))
        hi = int(config.get("altar_health_grant_max", 5))
        config["altar_health_grant"] = rng.randint(lo, hi)
        print(f"  [random] altar_health_grant → {config['altar_health_grant']}/10")

    # ── Cadeaux (mirrors cadeaux_patch.py defaults/clamping) ─────────────────
    _ALTAR_MAX  = 133   # floor(666 / 5)
    _FOG_VANILLA = 666
    altar = config.get("altar_cadeaux_required", 100)
    if altar == "random":
        lo    = int(config.get("altar_cadeaux_required_min", 50))
        hi    = int(config.get("altar_cadeaux_required_max", 100))
        altar = max(1, min(_ALTAR_MAX, rng.randint(lo, hi)))
        config["altar_cadeaux_required"] = altar
        print(f"  [random] altar_cadeaux_required → {altar}")
    else:
        altar = max(1, min(_ALTAR_MAX, int(altar)))

    fog = config.get("fogometers_cadeaux_required", _FOG_VANILLA)
    if fog == "random":
        fog_lo = int(config.get("fogometers_cadeaux_required_min", altar * 5))
        fog_hi = int(config.get("fogometers_cadeaux_required_max", _FOG_VANILLA))
        fog    = max(altar * 5, min(_FOG_VANILLA, rng.randint(fog_lo, fog_hi)))
        config["fogometers_cadeaux_required"] = fog
        print(f"  [random] fogometers_cadeaux_required → {fog}")


VANILLA_SL_THRESHOLDS = {
    0:   0,
    1:   1,
    2:   3,
    3:   7,
    4:  15,
    5:  23,
    6:  35,
    7:  51,
    8:  71,
    9:  95,
    10: 120,
}

# XZ positions for matching gate_id -> links.e2o 0x0C00 record (tolerance +-500 units)
# GATE_E2O_POSITIONS and E2O_MATCH_RADIUS imported from constants

# links.e2o format constants
E2O_HEADER        = b"Ee2ov004"
E2O_RECORD_SIZE   = 74
E2O_RECORD_OFF    = 0x10        # first record starts here
E2O_TYPE_OFF      = 0x2A        # u16 LE: 0x0C00 = coffin gate, 0x0D00 = console/trigger
E2O_SL_OFF        = 0x2E        # u16 LE: SL threshold = sl_index * 2560
E2O_GATE_TYPE     = 0x0C00
E2O_SL_SCALE      = 2560        # SL1 = 2560, SL2 = 5120, ... SL10 = 25600
E2O_MATCH_RADIUS  = 500         # XZ tolerance for gate matching (game units)

# X position offset within an e2o record (float32 LE)
E2O_X_OFF = 0x06
E2O_Z_OFF = 0x0E


def _sl_to_e2o(sl: int) -> int:
    return sl * E2O_SL_SCALE


def _e2o_to_sl(val: int) -> int:
    return val // E2O_SL_SCALE


# ── Object type sets ──────────────────────────────────────────────────────────

DARK_SOUL_TYPES  = {"RSC_X_GOVI", "RSC_X_DARK_SOUL"}
CADEAUX_TYPES    = {"RSC_CADEAUX", "RSC_X_CADEAUX", "RSC_PICKUP_CADEAUX"}
# "Barrel-like" breakables that act as item containers.
# These should participate in barrel/cadeaux/soul mixing the same way barrels do.
BARREL_TYPES     = {
    "RSC_X_BARREL_D", "RSC_X_BARREL_L", "RSC_X_BARREL_A",
    "RSC_X_BARREL",            # generic barrel
    "RSC_EXPLOSIVE_BARREL",    # breakable barrel variant
    "RSC_FL_CRATE",            # Florida crate
    "RSC_UN_CRATES",           # London crate stack
    "RSC_TE_PACKBOX1",         # Tenement boxes
    "RSC_TE_PACKBOX2",
}
STATIC_TYPES     = {"RSC_X_WEAPON_ALTAR"}
ACTOR_TYPES      = {"RSC_X_NETTIE", "RSC_X_JACKS-LADY-FRIEND"}
BONUS_TYPES      = {"RSC_X_LIGHT_SOUL"}
WEAPON_TYPES     = {
    "RSC_X_ASSON", "RSC_X_SHOTGUN", "RSC_X_SHOTGUN2",
    "RSC_X_ENSEIGNE", "RSC_X_MP5", "RSC_X_TETEDEMORT", "RSC_X_DESERTEAGLE",
}
LORE_TYPES       = {"RSC_X_BOOK_OF_SHADOWS", "RSC_X_PROPHECY", "RSC_X_JACKS_SCHEMATIC"}
PROGRESSION_TYPES = {
    "RSC_X_ENGINEERS_KEY", "RSC_X_RETRACT", "RSC_X_RETRACT1", "RSC_X_RETRACT2",
    "RSC_X_POIGNE", "RSC_X_PATHSOFSHADOW",
    "RSC_X_PRISON_KEY_CARD", "RSC_X_BATON", "RSC_X_FLAMBEAU", "RSC_X_MARTEAU",
    "RSC_X_CALABASH", "RSC_X_ACCUMULATOR", "RSC_X_FLASHLIGHT",
}
ECLIPSER_TYPES = {
    "RSC_X_ECLIPSER_PART1", "RSC_X_ECLIPSER_PART2", "RSC_X_ECLIPSER_PART3",
}

ABILITY_TYPES = {
    "RSC_X_GAD_PICKUP"
}

# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class QuestRecord:
    offset: int
    name: str
    zone: int
    save_idx: int
    x: float
    y: float
    z: float
    raw: bytes
    source_file: str = "quest.rsc"
    folder: str = ""

    @property
    def has_drop(self) -> bool:
        return self.save_idx != 0

    @property
    def category(self) -> str:
        if self.name in DARK_SOUL_TYPES:    return "soul"
        if self.name in CADEAUX_TYPES:      return "cadeaux"
        if self.name in BARREL_TYPES:       return "cadeaux" if self.has_drop else "barrel"
        if self.name in ABILITY_TYPES:      return "ability"
        if self.name in ECLIPSER_TYPES:     return "eclipser"
        if self.name in PROGRESSION_TYPES:  return "progression"
        if self.name in WEAPON_TYPES:       return "weapon"
        if self.name in LORE_TYPES:         return "lore"
        if self.name in BONUS_TYPES:        return "bonus"
        if self.name in STATIC_TYPES:       return "static"
        if self.name in ACTOR_TYPES:        return "actor"
        if self.name in ABILITY_TYPES:      return "gad"

        return "filler"

    @property
    def loc_key(self) -> str:
        return f"{self.folder}:{self.source_file}:0x{self.offset:04X}"


# ── RSC parsing & patching ────────────────────────────────────────────────────

def parse_rsc_file(filepath: str, folder: str = "") -> list:
    data = open(filepath, "rb").read()
    header = data[:8]
    if header not in (b"Erscv002", b"Erscv001"):
        raise ValueError(f"Unknown RSC header {header!r}: {filepath}")

    records = []

    # Try fixed-stride first
    body = data[HEADER_SIZE:]
    n_fixed = len(body) // RECORD_SIZE
    fixed_records = []
    for i in range(n_fixed):
        chunk = body[i * RECORD_SIZE : (i + 1) * RECORD_SIZE]
        name_part = chunk[NAME_OFF:].split(b'\x00')[0]
        if not name_part.upper().startswith(b'RSC_'):
            continue
        name = name_part.decode("ascii", errors="replace")
        x, y, z = struct.unpack_from("<fff", chunk, XYZ_OFF)
        fixed_records.append(QuestRecord(
            offset=HEADER_SIZE + i * RECORD_SIZE + NAME_OFF,
            name=name,
            zone=chunk[ZONE_OFF],
            save_idx=struct.unpack('>I', chunk[SAVE_IDX_OFF:SAVE_IDX_OFF + 4])[0],
            x=x, y=y, z=z,
            raw=chunk,
            folder=folder,
        ))

    # If fixed-stride found reasonable records use it
    # otherwise fall back to RSC_ scanning (for enemies.rsc etc.)
    body_rsc_count = sum(1 for i in range(n_fixed)
                         if body[i*RECORD_SIZE + NAME_OFF:i*RECORD_SIZE + NAME_OFF + 4].upper() == b'RSC_'
                         or body[i*RECORD_SIZE + NAME_OFF] == 0)

    use_scanning = len(fixed_records) == 0 or body_rsc_count < n_fixed * 0.3

    if use_scanning:
        seen = set()
        for m in re.finditer(b'RSC_', data):
            name_pos = m.start()
            if name_pos in seen:
                continue
            seen.add(name_pos)
            rec_start = name_pos - NAME_OFF
            if rec_start < HEADER_SIZE:
                continue
            name_bytes = data[name_pos:name_pos + NAME_MAXLEN].split(b'\x00')[0]
            if not name_bytes:
                continue
            name = name_bytes.decode("ascii", errors="replace")
            x, y, z = struct.unpack_from("<fff", data, rec_start + XYZ_OFF)
            records.append(QuestRecord(
                offset=name_pos,
                name=name,
                zone=data[rec_start + ZONE_OFF],
                save_idx=struct.unpack('>I', data[rec_start + SAVE_IDX_OFF:rec_start + SAVE_IDX_OFF + 4])[0],
                x=x, y=y, z=z,
                raw=data[rec_start:rec_start + RECORD_SIZE],
                folder=folder,
            ))
    else:
        records = fixed_records
        # Supplemental scan: catch any RSC_ strings the stride parser missed
        # (e.g. records after a mid-file section header that shifts alignment).
        stride_offsets = {r.offset for r in records}
        seen = set(stride_offsets)
        for m in re.finditer(b'RSC_', data):
            name_pos = m.start()
            if name_pos in seen:
                continue
            seen.add(name_pos)
            rec_start = name_pos - NAME_OFF
            if rec_start < HEADER_SIZE:
                continue
            name_bytes = data[name_pos:name_pos + NAME_MAXLEN].split(b'\x00')[0]
            if not name_bytes:
                continue
            name = name_bytes.decode("ascii", errors="replace")
            x, y, z = struct.unpack_from("<fff", data, rec_start + XYZ_OFF)
            records.append(QuestRecord(
                offset=name_pos,
                name=name,
                zone=data[rec_start + ZONE_OFF],
                save_idx=struct.unpack('>I', data[rec_start + SAVE_IDX_OFF:rec_start + SAVE_IDX_OFF + 4])[0],
                x=x, y=y, z=z,
                raw=data[rec_start:rec_start + RECORD_SIZE],
                folder=folder,
            ))

    return records

def patch_rsc_file(filepath: str, patches: dict, record_templates: dict = None) -> None:
    data = bytearray(open(filepath, "rb").read())
    size_before = len(data)
    for anchor_offset, p in patches.items():
        if anchor_offset + NAME_MAXLEN > len(data):
            print(f"  ERROR: anchor_offset 0x{anchor_offset:04X} is out of bounds "
                  f"(file size 0x{len(data):04X}) — skipping {p.get('name')}")
            continue
        if data[anchor_offset:anchor_offset+4] != b"RSC_":
            print(f"  WARNING: Expected RSC_ at 0x{anchor_offset:04X}, "
                  f"got {data[anchor_offset:anchor_offset+4]!r} — skipping")
            continue

        rec_start = anchor_offset - NAME_OFF
        template  = (record_templates or {}).get(p['name'])

        if template:
            rec_start = anchor_offset - NAME_OFF
            # Copy track_type (2-byte big-endian at 0x1C) from the template for
            # the new RSC type.  This is essential when slot type changes — e.g.
            # a cadeaux item placed into a barrel slot: the barrel slot keeps its
            # original 0x0020 (volatile) track_type unless we overwrite it with
            # 0x0002 (persistent/cadeaux) from the template.  The save_idx write
            # below (anchor_offset - 4 = rec_start + 0x1E) is unaffected because
            # track_type sits at 0x1C–0x1D, two bytes before the save_idx field.
            # NOTE: the old write (data[rec_start + 0x20] = template[0x20]) was a
            # no-op — 0x20 falls inside save_idx (0x1E–0x21) and was immediately
            # overwritten by the struct.pack_into below.
            data[rec_start + 0x1C] = template[0x1C]
            data[rec_start + 0x1D] = template[0x1D]

        # Then write name and save_idx as before
        new_name = p['name'].encode("ascii")
        if len(new_name) >= NAME_MAXLEN:
            new_name = new_name[:NAME_MAXLEN - 1]
        data[anchor_offset: anchor_offset + NAME_MAXLEN] = b"\x00" * NAME_MAXLEN
        data[anchor_offset: anchor_offset + len(new_name)] = new_name

        reward = p.get('reward')
        if reward is not None:
            struct.pack_into('>I', data, anchor_offset - 4, reward)

        y_adjust = p.get('y_adjust', 0.0)
        if y_adjust != 0.0:
            y_off = rec_start + XYZ_OFF + 4
            current_y = struct.unpack_from("<f", data, y_off)[0]
            struct.pack_into("<f", data, y_off, current_y + y_adjust)

    if len(data) != size_before:
        print(f"  ERROR: patch_rsc_file changed file size from {size_before} to {len(data)}!")
    with open(filepath, "wb") as f:
        f.write(data)

# ── Gad temple cutscene suppression ──────────────────────────────────────────
#
# 0x0D00 trigger records in gad temple links.e2o files with these @0x2E values
# fire the post-puzzle cutscene and drop the player into lava.
# The EXE gad NOP already handles the ability grant side; these triggers are
# the level-side event that causes the cinematic + death sequence.
# Zeroing @0x2E disables the event dispatch while leaving geometry intact.

GAD_CUTSCENE_EVENT_IDS = frozenset({
    0xFA00,  # fires on button-press triggers near gad platform
    0xC800,  # fires on button-press triggers near gad platform
    0x2602,  # fires on platform approach after all buttons pressed
})
GAD_TEMPLE_LEVELS      = frozenset({"t1tchgad", "t2wlkgad", "t3swmgad"})

E2O_TRIGGER_TYPE = 0x0D00

# XZ world position of the gad platform in each temple, used to restrict cutscene
# trigger zeroing to records that are near the platform.  Coffin gate interaction
# triggers share the same event IDs but are thousands of units away, so a generous
# radius keeps them untouched.
# Built lazily from GAD_INJECTION_SITES: (folder, filename, x, y, z, zone).
_GAD_PLATFORM_XZ: dict[str, tuple[float, float]] = {
    folder: (x, z) for folder, _fn, x, _y, z, _zone in GAD_INJECTION_SITES
}
_GAD_TRIGGER_RADIUS = 10_000   # game units; known gad triggers are ≤~500 from platform;
                                # nearest coffin gate is ~6800+ away

EVT_HEADER_SIZE = 16
EVT_RECORD_SIZE = 58
EVT_AABB_OFF = 0x20
EVT_AABB_SIZE = 24

# Signature at bytes[4:6] that identifies the gad pickup cutscene record.
# t2wlkgad and t3swmgad are EXCLUDED: their exit cutscene records share this
# signature, so zeroing it breaks level exit. The links.e2o proximity-guard
# zeroing alone is sufficient to suppress the gad pickup event for t2 and t3.
_GAD_EVT_SIGNATURE = bytes([0xFF, 0xA6])
GAD_CUTSCENE_EVT_LEVELS = frozenset({"t1tchgad"})

def _zero_gad_cutscene_evt(levels_path: Path, folder: str) -> bool:
    """
    Zero the AABB float data only in cutscene.evt records whose bytes[4:6]
    match _GAD_EVT_SIGNATURE — the gad pickup cutscene trigger.
    Exit cutscene records have a different signature and are left untouched.
    Returns True if at least one record was zeroed.
    """
    if folder not in GAD_CUTSCENE_EVT_LEVELS:
        return False
    evt_path = levels_path / folder / "cutscene.evt"
    if not evt_path.exists():
        print(f"  WARNING: {folder}/cutscene.evt not found - skipping")
        return False
    data = bytearray(evt_path.read_bytes())
    n = (len(data) - EVT_HEADER_SIZE) // EVT_RECORD_SIZE
    zeroed = 0
    for i in range(n):
        pos = EVT_HEADER_SIZE + i * EVT_RECORD_SIZE
        if data[pos+4:pos+6] != _GAD_EVT_SIGNATURE:
            continue
        aabb_start = pos + EVT_AABB_OFF
        if aabb_start + EVT_AABB_SIZE <= len(data):
            data[aabb_start : aabb_start + EVT_AABB_SIZE] = bytes(EVT_AABB_SIZE)
            zeroed += 1
    if zeroed:
        evt_path.write_bytes(bytes(data))
    return zeroed > 0

def _zero_gad_cutscene_triggers(data: bytearray, folder: str) -> int:
    """
    Zero @0x2E on 0x0D00 event trigger records whose value is a known
    gad cutscene or lava-death event ID AND whose XZ position is within
    _GAD_TRIGGER_RADIUS of the gad platform.

    The proximity guard is critical: coffin gate interaction triggers share
    the same event IDs (0xFA00 / 0xC800) but are thousands of units away
    from the gad platform.  Without it, coffin gates in temple levels
    become non-interactable.

    Returns count of records zeroed.
    """
    if folder not in GAD_TEMPLE_LEVELS:
        return 0
    plat = _GAD_PLATFORM_XZ.get(folder)
    n = (len(data) - E2O_RECORD_OFF) // E2O_RECORD_SIZE
    zeroed = 0
    for i in range(n):
        pos = E2O_RECORD_OFF + i * E2O_RECORD_SIZE
        if struct.unpack_from("<H", data, pos + E2O_TYPE_OFF)[0] != E2O_TRIGGER_TYPE:
            continue
        val = struct.unpack_from("<H", data, pos + E2O_SL_OFF)[0]
        if val not in GAD_CUTSCENE_EVENT_IDS:
            continue
        # Proximity guard — skip records far from the gad platform
        if plat is not None:
            rx = struct.unpack_from("<f", data, pos + E2O_X_OFF)[0]
            rz = struct.unpack_from("<f", data, pos + E2O_Z_OFF)[0]
            dist = ((rx - plat[0]) ** 2 + (rz - plat[1]) ** 2) ** 0.5
            if dist > _GAD_TRIGGER_RADIUS:
                continue
        struct.pack_into("<H", data, pos + E2O_SL_OFF, 0)
        zeroed += 1
    return zeroed

# ── Soul gate SL shuffling via links.e2o ──────────────────────────────────────

def _parse_e2o_gates(data: bytes, folder: str) -> list[dict]:
    """
    Parse all 0x0C00 (coffin gate) records from a links.e2o file.
    Returns list of dicts with keys: rec_idx, x, z, sl_val, sl_int, file_off
    """
    if data[:8] != E2O_HEADER:
        return []

    gates = []
    pos = E2O_RECORD_OFF
    rec_idx = 0
    while pos + E2O_RECORD_SIZE <= len(data):
        rec_type = struct.unpack_from("<H", data, pos + E2O_TYPE_OFF)[0]
        if rec_type == E2O_GATE_TYPE:
            x   = struct.unpack_from("<f", data, pos + E2O_X_OFF)[0]
            z   = struct.unpack_from("<f", data, pos + E2O_Z_OFF)[0]
            val = struct.unpack_from("<H", data, pos + E2O_SL_OFF)[0]
            sl  = _e2o_to_sl(val)
            gates.append({
                "rec_idx":  rec_idx,
                "x":        x,
                "z":        z,
                "sl_val":   val,
                "sl_int":   sl,
                "file_off": pos + E2O_SL_OFF,
                "folder":   folder,
            })
        pos += E2O_RECORD_SIZE
        rec_idx += 1
    return gates


def _match_gate_id(x: float, z: float, folder: str) -> str | None:
    """
    Match an e2o record's (folder, x, z) to a gate_id via GATE_E2O_POSITIONS.
    Returns gate_id string or None if no match within E2O_MATCH_RADIUS.
    """
    best_id   = None
    best_dist = float("inf")
    for gate_id, (gfolder, gx, gz) in GATE_E2O_POSITIONS.items():
        if gfolder != folder:
            continue
        dist = abs(x - gx) + abs(z - gz)   # Manhattan distance is fine here
        if dist < best_dist and dist < E2O_MATCH_RADIUS:
            best_dist = dist
            best_id = gate_id
    return best_id


def randomize_gate_sl_links(
    gate_remap: dict[str, int],
    levels_path: Path,
) -> None:
    """
    Write shuffled SL threshold values from gate_remap into each level's
    links.e2o file. Gate shuffle logic lives in fill._shuffle_gates().
    """
    gate_folders = {"deadside", "wastland", "t1tchgad", "t2wlkgad", "t3swmgad", "ah4fogom"}
    e2o_data_cache: dict[str, bytearray] = {}

    for folder in gate_folders:
        e2o_path = levels_path / folder / "links.e2o"
        if not e2o_path.exists():
            print(f"  WARNING: {folder}/links.e2o not found - skipping")
            continue
        raw = e2o_path.read_bytes()
        e2o_data_cache[folder] = bytearray(raw)
        recs = _parse_e2o_gates(raw, folder)
        for rec in recs:
            gate_id = _match_gate_id(rec["x"], rec["z"], folder)
            print(f"  [{folder}] record x={rec['x']:.0f} z={rec['z']:.0f} -> matched: {gate_id}")
            if gate_id is None:
                continue
            new_sl = gate_remap.get(gate_id)
            if new_sl is None:
                continue
            new_val = _sl_to_e2o(new_sl)
            struct.pack_into("<H", e2o_data_cache[folder], rec["file_off"], new_val)

    for folder, buf in e2o_data_cache.items():
        e2o_path = levels_path / folder / "links.e2o"
        e2o_path.write_bytes(bytes(buf))

    changed = {gid: sl for gid, sl in gate_remap.items()
               if sl != GATE_VANILLA_SL.get(gid)}
    if changed:
        print(f"  Soul gates: wrote {len(changed)} gate(s) to links.e2o")
        for gate_id in sorted(changed):
            old_sl    = GATE_VANILLA_SL[gate_id]
            new_sl    = changed[gate_id]
            old_souls = VANILLA_SL_THRESHOLDS[old_sl]
            new_souls = VANILLA_SL_THRESHOLDS[new_sl]
            print(f"    {gate_id:<28}  SL{old_sl} ({old_souls:3} souls)"
                  f" -> SL{new_sl} ({new_souls:3} souls)")
    else:
        print("  Soul gates: no changes from vanilla")


# ── Gate ARC decoration patching ─────────────────────────────────────────────
#
# Each coffin gate in deadside has a companion RSC_X_COFFIN_GATE_ARCn record
# whose number visually matches the SL requirement shown on the ring.
# ARC number == SL number (ARC1=SL1 ... ARC9=SL9).
#
# When gate shuffle moves a gate from SL3->SL7, the ARC3 deco near that gate
# must be renamed to ARC7 so the in-world display stays accurate.
#
# ARC0  = Path of Shadows gate — always locked, never remapped.
# ARC10 = Final locked gate — never remapped.

ARC_PREFIXES = (b"RSC_X_COFFIN_GATE_ARC", b"RSC_X_COFGATE_ARC")

GATE_MATCH_RADIUS = 600.0

def patch_gate_arc_decos(
    levels_path: Path,
    gate_remap: dict[str, int],
) -> int:
    if not gate_remap:
        return 0

    _DECO_TO_GATE: dict[tuple[str, int, int], str] = {
        ("deadside", -836, 20326): "GATE_DEADSIDE_MARROW",
        ("deadside", -2865, 5298): "GATE_DEADSIDE_MYSTERY",
        ("deadside",   478,  23424): "GATE_DEADSIDE_WASTELAND",
        ("deadside",  -672,  25472): "GATE_DEADSIDE_ASYLUM",
        ("deadside", -3169,  29123): "GATE_DEADSIDE_LALUNE",
        ("deadside", -2655,  26682): "GATE_DEADSIDE_PATH_3",
        ("deadside",  2333,  24384): "GATE_DEADSIDE_CAGEWAYS",
        ("deadside",  3999,  21568): "GATE_DEADSIDE_PLAYROOMS",
        ("deadside", -1067,  19714): "GATE_DEADSIDE_PATH_6",
        ("deadside",  -478,  15712): "GATE_DEADSIDE_LAVADUCTS",
        ("deadside",   224,  22781): "GATE_DEADSIDE_PATH_7",
        ("deadside", -1312,  11041): "GATE_DEADSIDE_LALAME",
        ("deadside", -3171,  15712): "GATE_DEADSIDE_BLOOD",
        ("deadside", -1824,  14367): "GATE_DEADSIDE_FOGOMETERS",
        ("t1tchgad",   916,   4480): "GATE_FIRE_POIGNE",
        ("t1tchgad",  6329,   4608): "GATE_FIRE_FLAMBEAU",
        ("t2wlkgad", -3904, -13056): "GATE_PROPHECY_INTERIOR",
        ("t3swmgad", -1978, -11808): "GATE_BLOOD_INTERIOR",
        ("wastland",  5088,   7808): "GATE_WASTELAND_ENSEIGNE",
        ("ah4fogom", -14955, 11890): "GATE_FOGOMETERS_INTERIOR",
    }

    # Folders to scan — derived from _DECO_TO_GATE keys
    folders = set(folder for folder, dx, dz in _DECO_TO_GATE)

    # Per-folder override: which RSC file holds the ARC deco records.
    # Most levels keep them in events.rsc; ah4fogom uses instance.rsc.
    _FOLDER_ARC_RSC: dict[str, str] = {
        "ah4fogom": "instance.rsc",
    }

    ARC_PREFIXES = (b"RSC_X_COFFIN_GATE_ARC", b"RSC_X_COFGATE_ARC")
    total_changed = 0

    for folder in folders:
        rsc_name = _FOLDER_ARC_RSC.get(folder, "events.rsc")
        rsc_path = levels_path / folder / rsc_name
        if not rsc_path.exists():
            print(f"  WARNING: {folder}/{rsc_name} not found - ARC decos not patched")
            continue

        data = bytearray(rsc_path.read_bytes())
        changed = 0

        for m in re.finditer(rb'RSC_X_COF(?:FIN_GATE_ARC|GATE_ARC)(\d+)', data):
            name_pos = m.start()
            arc_num = int(m.group(1))

            matched_prefix = next(p for p in ARC_PREFIXES if data[name_pos:name_pos + len(p)] == p)

            rec_start = name_pos - NAME_OFF
            if rec_start < HEADER_SIZE:
                continue

            rx, _, rz = struct.unpack_from("<fff", data, rec_start + XYZ_OFF)
            rx_r, rz_r = round(rx), round(rz)

            # Match deco position to gate_id via _DECO_TO_GATE
            new_arc_num = None
            for (dx, dz) in [(dx, dz) for (f, dx, dz) in _DECO_TO_GATE if f == folder]:
                if abs(rx_r - dx) < GATE_MATCH_RADIUS and abs(rz_r - dz) < GATE_MATCH_RADIUS:
                    gate_id = _DECO_TO_GATE.get((folder, dx, dz))
                    if gate_id:
                        new_arc_num = gate_remap.get(gate_id)
                    break

            if new_arc_num is None or new_arc_num == arc_num:
                continue

            print(f"  [{folder}] Renaming ARC{arc_num} at ({rx_r},{rz_r}) -> ARC{new_arc_num}")
            # Always use the full COFFIN_GATE_ARC prefix — all SL numbers (0-10)
            # exist under this name in deadside. The short COFGATE_ARC variant
            # only covers a subset of numbers, causing invisible gates when
            # a non-deadside gate gets shuffled to a number with no COFGATE asset.
            new_name = b"RSC_X_COFFIN_GATE_ARC" + str(new_arc_num).encode("ascii")
            data[name_pos: name_pos + NAME_MAXLEN] = (
                new_name + b'\x00' * (NAME_MAXLEN - len(new_name))
            )
            changed += 1
            total_changed += 1

        if changed:
            rsc_path.write_bytes(bytes(data))
            print(f"  Gate decos: {changed} ARC record(s) renamed in {folder}/{rsc_name}")

    return total_changed

def _spoiler_gate_section(gate_remap: dict[str, int], sl_thresholds: dict | None = None) -> list[str]:
    """Format the soul gate section for the spoiler log.

    sl_thresholds: if provided (randomized thresholds), used for soul counts instead
    of VANILLA_SL_THRESHOLDS. Both old and new soul counts reflect the effective map.
    """
    effective = sl_thresholds if sl_thresholds is not None else VANILLA_SL_THRESHOLDS
    lines = [
        "",
        "── SOUL GATE SL REQUIREMENTS ──────────────────────────",
        "",
        f"  {'Gate ID':<32}  {'Old SL':>6}  {'New SL':>6}  {'Old souls':>10}  {'New souls':>10}",
        f"  {'─'*32}  {'─'*6}  {'─'*6}  {'─'*10}  {'─'*10}",
    ]
    for gate_id in sorted(GATE_VANILLA_SL):
        old_sl    = GATE_VANILLA_SL[gate_id]
        new_sl    = gate_remap.get(gate_id, old_sl)
        old_souls = effective[old_sl]
        new_souls = effective[new_sl]
        if new_sl == 0 and old_sl != 0:
            note = " (open)"
        elif old_sl == new_sl:
            note = ""
        else:
            note = " ←"
        lines.append(
            f"  {gate_id:<32}  SL{old_sl:>2}    SL{new_sl:>2}    "
            f"{old_souls:>6} souls  {new_souls:>6} souls{note}"
        )
    return lines


def _spoiler_soul_threshold_section(thresholds: dict) -> list[str]:
    """Format the randomized soul threshold section for the spoiler log."""
    lines = [
        "",
        "── SOUL THRESHOLDS ─────────────────────────────────────",
        "",
        f"  {'SL':>4}  {'Vanilla':>8}  {'Randomized':>10}",
        f"  {'─'*4}  {'─'*8}  {'─'*10}",
    ]
    for sl in range(1, 11):
        vanilla = VANILLA_SL_THRESHOLDS[sl]
        rand    = thresholds[sl]
        changed = " ←" if rand != vanilla else ""
        lines.append(f"  SL{sl:>2}  {vanilla:>8}  {rand:>10}{changed}")
    return lines

def apply_msh_overrides(randomizer_dir, work_path, kpf_index=None) -> dict:
    """Scale MSH vertex tables and return as mod_files dict for KPF packing."""
    mod_files = {}
    VERT_OFF = 0x340
    N_VERTS  = 8

    for kpf_path, scale in MSH_OVERRIDES:
        data = None
        if kpf_index:
            matches = find_file_in_kpf(kpf_index, kpf_path)
            if matches:
                tmp = Path(work_path) / "msh_overrides" / Path(kpf_path).name
                tmp.parent.mkdir(parents=True, exist_ok=True)
                extract_file_from_kpf(
                    str(Path(kpf_index.kpf_dir) / matches[0][1]),
                    matches[0][0],
                    str(tmp),
                )
                data = bytearray(tmp.read_bytes())

        if data is None:
            print(f"  WARNING: MSH override source not found — {kpf_path}")
            continue
        if len(data) != 1024:
            print(f"  WARNING: {kpf_path} is not a standard box MSH ({len(data)} bytes) — skipping")
            continue
        for i in range(N_VERTS):
            for axis in range(3):
                off = VERT_OFF + i * 24 + axis * 4
                val = struct.unpack_from('<f', data, off)[0]
                struct.pack_into('<f', data, off, val * scale)
        out_path = Path(work_path) / "msh_overrides" / Path(kpf_path).name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(bytes(data))
        mod_files[kpf_path] = str(out_path)
        print(f"  [msh] {kpf_path} (scale {scale}x)")

    return mod_files

# ── Debug helpers ─────────────────────────────────────────────────────────────

def audit_govi_patches(filepath: str, patches: dict, records: list) -> None:
    # Debug helper — intentionally silent in normal output to avoid spoiling placements.
    pass


def verify_patch(filepath: str, patches: dict) -> None:
    data = open(filepath, "rb").read()
    ok = fail = 0
    for anchor_offset, p in patches.items():
        actual_name    = data[anchor_offset:anchor_offset+30].split(b'\x00')[0].decode('ascii', errors='replace')
        actual_reward  = struct.unpack(">I", data[anchor_offset - 4:anchor_offset])[0]
        expected_reward = p.get('reward')
        name_ok   = (actual_name == p['name'])
        reward_ok = (expected_reward is None or actual_reward == expected_reward)
        if name_ok and reward_ok:
            ok += 1
        else:
            fail += 1
            reasons = []
            if not name_ok:   reasons.append(f"Name {actual_name!r}!={p['name']!r}")
            if not reward_ok: reasons.append(f"ID {actual_reward}!={expected_reward}")
            print(f"  [!] MISMATCH at 0x{anchor_offset:04X}: {' | '.join(reasons)}")
    print(f"  Verification: {ok} passed, {fail} failed.")


# ── Assumed fill ──────────────────────────────────────────────────────────────

def run_assumed_fill(rng, config, gate_remap=None, entrance_shuffle=None):
    shuffle_prog = config.get("shuffle_progression", True)
    gate_preset  = config.get("gate_preset")

    # Resolve gate kwargs from preset or raw config
    if gate_preset and gate_preset in GATE_PRESETS:
        p = GATE_PRESETS[gate_preset]
        gate_kwargs = {
            "shuffle_gates": p["shuffle_gates"],
            "no_soul_gates": p["no_soul_gates"],
            "lock_gates": p["lock_gates"],
            "max_sl":       config.get("max_sl")       if config.get("max_sl")       is not None else p["max_sl"],
            "open_gates_n": config.get("open_gates_n") if config.get("open_gates_n") is not None else p.get("open_gates_n", 0),
            "safe": p["safe"],
        }
        print(f"  Soul gate decree : {gate_preset}")
    else:
        gate_kwargs = {
            "shuffle_gates": config.get("shuffle_soul_gates", False),
            "no_soul_gates": False,
            "lock_gates": frozenset(),
            "max_sl":       None,
            "open_gates_n": config.get("open_gates_n") or 0,
            "safe": True,
        }

    if not shuffle_prog:
        if gate_kwargs["shuffle_gates"]:
            try:
                gate_remap = _shuffle_gates(
                    rng,
                    locked=gate_kwargs["lock_gates"],
                    max_sl=gate_kwargs["max_sl"],
                    safe=gate_kwargs["safe"],
                )
                changed = sum(1 for g, sl in gate_remap.items()
                              if sl != GATE_VANILLA_SL.get(g))
                print(f"  Progression untouched (--no-shuffle-progression)")
                print(f"  Soul gate seals: {changed} reforged")
            except ImportError:
                print("  WARNING: fill.py not found - gate shuffle skipped")
                gate_remap = {g: GATE_VANILLA_SL[g] for g in GATE_VANILLA_SL}
        else:
            print("  Progression untouched (--no-shuffle-progression)")
            gate_remap = {g: GATE_VANILLA_SL[g] for g in GATE_VANILLA_SL}
        return {}, gate_remap

    placement, gate_remap = assumed_fill(
        rng,
        progression_balancing=config.get("progression_balancing", 50),
        shuffle_gad_temples=config.get("shuffle_gad_temples", False),
        entrance_shuffle=entrance_shuffle,
        insanity=int(config.get("insanity", 0)),
        shuffle_weapons=config.get("shuffle_weapons", True),
        shuffle_lore=config.get("shuffle_lore", True),
        shuffle_bonus=config.get("shuffle_bonus", False),
        shuffle_prisms=config.get("shuffle_prisms", False),
        shuffle_retractors=config.get("shuffle_retractors", True),
        shuffle_accumulators=config.get("shuffle_accumulators", True),
        shuffle_eclipsers=config.get("shuffle_eclipsers", True),
        starting_item=config.get("starting_item"),
        **gate_kwargs,
    )

    MAX_RETRIES = 5
    for attempt in range(MAX_RETRIES):
        retry_seed = rng.randint(0, 99_999_999) ^ attempt
        retry_rng = random.Random(retry_seed)
        if attempt > 0:
            print(f"  ⚠️  Seed failed validation — retry {attempt}/{MAX_RETRIES - 1}...")
            placement, gate_remap = assumed_fill(
                retry_rng,
                progression_balancing=config.get("progression_balancing", 50),
                shuffle_gad_temples=config.get("shuffle_gad_temples", False),
                entrance_shuffle=entrance_shuffle,
                insanity=int(config.get("insanity", 0)),
                shuffle_weapons=config.get("shuffle_weapons", True),
                shuffle_lore=config.get("shuffle_lore", True),
                shuffle_bonus=config.get("shuffle_bonus", False),
                shuffle_prisms=config.get("shuffle_prisms", False),
                starting_item=config.get("starting_item"),
                **gate_kwargs,
            )
        ok, report = validate_fill(
            placement,
            gate_remap=gate_remap,
            shuffle_gad_temples=config.get("shuffle_gad_temples", False),
            shuffle_retractors=config.get("shuffle_retractors", True),
            shuffle_accumulators=config.get("shuffle_accumulators", True),
            shuffle_eclipsers=config.get("shuffle_eclipsers", True),
            starting_item=config.get("starting_item"),
            entrance_shuffle=entrance_shuffle,
        )
        if ok:
            break
    else:
        raise RuntimeError(f"Seed failed logic validation after {MAX_RETRIES} attempts:\n{report}")

    changed = sum(1 for g, sl in gate_remap.items()
                  if sl != GATE_VANILLA_SL.get(g))
    print(f"  Relics scattered: {len(placement)} placements  "
          f"[soul gates: {changed} reforged]")

    if gate_preset == "chaos":
        formerly_locked = {"GATE_DEADSIDE_MARROW", "GATE_DEADSIDE_MYSTERY", "GATE_FOGOMETERS_INTERIOR"}
        shuffled_locked = [g for g in formerly_locked
                           if gate_remap.get(g) != GATE_VANILLA_SL.get(g)]
        if shuffled_locked:
            print(f"  ⚠️  CHAOS: {len(shuffled_locked)} sealed gate(s) have been broken open — "
                  f"expect unusual progression")

    n = gate_kwargs.get("open_gates_n", 0)
    if n:
        print(f"  First {n} gate(s) forced open (SL0)")

    return placement, gate_remap

# ── RSC item randomization ────────────────────────────────────────────────────

def write_placement_patches(
    records_by_folder: dict,
    progression_placement: dict,
    shuffle_gad_temples: bool = False,
) -> dict:
    """
    Convert fill.py placement decisions into RSC file patches.
    Fill owns all placement logic — this function is a dumb writer.
    """
    all_rec_index = {
        rec.loc_key: rec
        for folder, records in records_by_folder.items()
        for rec in records
    }

    missing_from_index = [
        loc_key for loc_key in progression_placement
        if loc_key not in all_rec_index
    ]
    if missing_from_index:
        print(f"  [DBG] {len(missing_from_index)} placement loc_keys not found in parsed records:")
        for lk in missing_from_index[:5]:
            print(f"      {lk}")

    def make_patch(rec, new_name, save_idx):
        new_tall          = new_name in DARK_SOUL_TYPES
        old_dark_soul_slot = rec.name == "RSC_X_DARK_SOUL"
        old_govi_slot      = rec.name == "RSC_X_GOVI"
        old_any_soul_slot  = old_dark_soul_slot or old_govi_slot
        old_cadeaux_slot   = rec.name in CADEAUX_TYPES
        old_barrel_slot    = rec.name in BARREL_TYPES
        new_is_key = (new_name not in DARK_SOUL_TYPES
                      and new_name not in CADEAUX_TYPES
                      and new_name not in BARREL_TYPES)
        if new_tall and not old_any_soul_slot:
            y_adj = GOVI_HEIGHT_BOOST
        elif new_is_key and old_dark_soul_slot:
            y_adj = DARK_SOUL_SLOT_ITEM_DROP
        elif new_is_key and old_govi_slot:
            y_adj = PROGRESSION_IN_GOVI_LIFT
        elif new_is_key and old_cadeaux_slot:
            y_adj = PROGRESSION_IN_CADEAUX_LIFT
        elif new_is_key and old_barrel_slot:
            y_adj = PROGRESSION_IN_BARREL_LIFT
        elif not new_tall and old_any_soul_slot:
            y_adj = CADEAU_HEIGHT_DROP
        else:
            y_adj = 0.0
        y_adj += ITEM_Y_ADJUST.get((new_name, rec.source_file), ITEM_Y_ADJUST.get((new_name, None), 0.0))
        return {"name": new_name, "reward": save_idx,
                "logic": rec.zone, "y_adjust": y_adj, "source_file": rec.source_file}

    patches_by_folder: dict = {}
    marker_sites: list = []  # (folder, x, y, z, zone) for SOUL_SLOT_MARKER_FX injection
    matched = 0

    for loc_key, source_loc in progression_placement.items():
        rec = all_rec_index.get(loc_key)
        if rec is None:
            continue
        if rec.name == "RSC_X_GAD_PICKUP" and not shuffle_gad_temples:
            k = (rec.folder, rec.source_file)
            patches_by_folder.setdefault(k, {})[rec.offset] = {
                "name": "RSC_X_BARREL_D",
                "reward": 0,
                "logic": rec.zone,
                "y_adjust": 0.0,
                "source_file": rec.source_file,
            }
            continue
        rsc_name = source_loc.object

        # RSC_X_VIOLATOR requires accumulator window activation to be collectible.
        # Always use RSC_Q_VIOLATOR which is a standard pickup.
        # Two RSC_Q_VIOLATOR instances cover both inventory slots correctly.
        if rsc_name == "RSC_X_VIOLATOR":
            rsc_name = "RSC_Q_VIOLATOR"

        # Substitute barrel RSC names whose assets have been replaced with
        # custom visuals — prevents the marker crate appearing at filler spots.
        rsc_name = BARREL_RSC_SUBSTITUTIONS.get(rsc_name, rsc_name)

        save_idx = source_loc.save_idx
        old_soul_slot = rec.name in DARK_SOUL_TYPES or rec.name in CADEAUX_TYPES
        old_barrel_slot = rec.name in BARREL_TYPES
        new_is_key = (rsc_name not in DARK_SOUL_TYPES
                      and rsc_name not in CADEAUX_TYPES
                      and rsc_name not in BARREL_TYPES)
        if new_is_key and old_soul_slot:
            altar_y_off = DARK_SOUL_SLOT_MARKER_FX_Y if rec.name == "RSC_X_DARK_SOUL" else SOUL_SLOT_MARKER_FX_Y
            marker_sites.append((rec.folder, rec.source_file, rec.x, rec.y + altar_y_off, rec.z, rec.zone,
                                 SOUL_SLOT_MARKER_FX))
        elif new_is_key and old_barrel_slot:
            marker_sites.append((rec.folder, rec.source_file, rec.x, rec.y + BARREL_SLOT_MARKER_FX_Y, rec.z, rec.zone,
                                 BARREL_SLOT_MARKER_FX))
        k = (rec.folder, rec.source_file)
        patches_by_folder.setdefault(k, {})[rec.offset] = \
            make_patch(rec, rsc_name, save_idx if save_idx is not None else rec.save_idx)
        matched += 1

    print(f"  RSC patches: {matched} locations written from fill placement")
    soul_patch_count = sum(
        1 for patches in patches_by_folder.values()
        for p in patches.values()
        if p["name"] in DARK_SOUL_TYPES
    )
    other_patch_count = sum(
        1 for patches in patches_by_folder.values()
        for p in patches.values()
        if p["name"] not in DARK_SOUL_TYPES
    )
    print(f"  RSC patches breakdown: {soul_patch_count} soul patches, {other_patch_count} non-soul patches")

    return patches_by_folder, marker_sites

# ── Special item FX injection ───────────────────────────────────────────────────

def _inject_one_fx_record(rsc_path: Path, rsc_name: str, x: float, y: float, z: float, zone: int) -> bool:
    # Only inject directly into quest.rsc — other RSC types (instance.rsc etc.) use
    # byte 9 as a flags/type field, not a live-window count, so inject_rsc_record's
    # headroom logic corrupts that byte and crashes the level.
    if rsc_path.name != "quest.rsc":
        quest_path = rsc_path.parent / "quest.rsc"
        if quest_path.exists():
            return _inject_one_fx_record(quest_path, rsc_name, x, y, z, zone)
        print(f"  WARNING: marker skipped — no quest.rsc in {rsc_path.parent.name}")
        return False

    raw = rsc_path.read_bytes()
    n_full = (len(raw) - HEADER_SIZE) // RECORD_SIZE
    trailer = raw[HEADER_SIZE + n_full * RECORD_SIZE:]
    data = bytearray(raw[:HEADER_SIZE + n_full * RECORD_SIZE])

    record = build_rsc_record(rsc_name, x, y, z, zone)
    slot = inject_rsc_record(data, record, allow_expand=True)

    if slot is None:
        print(f"  WARNING: no space in {rsc_path.parent.name}/{rsc_path.name} — marker skipped")
        return False

    rsc_path.write_bytes(bytes(data) + trailer)
    print(f"  {rsc_path.parent.name}/{rsc_path.name}  +1 {rsc_name} slot {slot} (zone {zone})")
    return True


def inject_special_item_fx(marker_sites: list, levels_path) -> int:
    total = 0
    for folder, source_file, x, y, z, zone, fx_name in marker_sites:
        rsc_path = Path(levels_path) / folder / source_file
        if not rsc_path.exists():
            print(f"  WARNING: marker skipped — {folder}/{source_file} not found")
            continue
        if _inject_one_fx_record(rsc_path, fx_name, x, y, z, zone):
            total += 1
        mirror = DAY_NIGHT_MIRRORS.get(folder)
        if mirror:
            mirror_path = Path(levels_path) / mirror / source_file
            if mirror_path.exists():
                if _inject_one_fx_record(mirror_path, fx_name, x, y, z, zone):
                    total += 1
    return total

# ── Spoiler log ───────────────────────────────────────────────────────────────

def write_spoiler_log(output_path, seed, patches_by_folder, gate_remap,
                      records_by_folder, config, spheres=None,
                      entrance_shuffle=None, soul_thresholds=None) -> None:
    starting_rsc = config.get('starting_item', None)
    starting_friendly = _RSC_TO_FRIENDLY.get(starting_rsc, starting_rsc) if starting_rsc else 'none'

    max_sl     = config.get('max_sl')
    open_gates = config.get('open_gates_n')
    insanity   = config.get('insanity', 0)

    settings_str = config.get('settings_string')

    lines = [
        "=" * 60,
        "SHADOW MAN REMASTERED - RANDOMIZER SPOILER LOG",
        "=" * 60,
        f"Seed: {seed}",
        *([ f"Settings: {settings_str}" ] if settings_str else []),
        "",
        "── GAMEPLAY ────────────────────────────────────────────",
        f"  Randomize key items:   {config.get('shuffle_progression', True)}",
        f"  Shuffle gad temples:   {config.get('shuffle_gad_temples', False)}",
        f"  Shuffle weapons:       {config.get('shuffle_weapons', True)}",
        f"  Shuffle lore:          {config.get('shuffle_lore', True)}",
        f"  Shuffle light soul:    {config.get('shuffle_bonus', False)}",
        f"  Shuffle prisms:        {config.get('shuffle_prisms', False)}",
        f"  Shuffle retractors:    {config.get('shuffle_retractors', True)}",
        f"  Shuffle accumulators:  {config.get('shuffle_accumulators', True)}",
        f"  Shuffle eclipsers:     {config.get('shuffle_eclipsers', True)}",
        f"  Starting item:         {starting_friendly}",
        f"  Patch tracker:         {config.get('patch_tracker', False)}",
        "",
        "── COFFIN GATES ────────────────────────────────────────",
        f"  Gate preset:           {config.get('gate_preset', 'none')}",
        f"  Max SL override:       {max_sl if max_sl is not None else 'none'}",
        f"  Open first N gates:    {open_gates if open_gates is not None else 'preset'}",
        f"  SL threshold mode:     {config.get('soul_threshold_mode', 'off')}",
        "",
        "── ENTRANCE RANDOMIZER ─────────────────────────────────",
        f"  Entrance mode:         {config.get('entrance_mode', 'off')}",
        "",
        "── GAMEPLAY TUNING ─────────────────────────────────────",
        f"  Starting health:       {config.get('starting_health', 5)}/10",
        f"  Altar health grant:    {config.get('altar_health_grant', 1)}/10",
        f"  Altar cadeaux cost:    {config.get('altar_cadeaux_required', 100)}",
        f"  Fogometers cadeaux:    {config.get('fogometers_cadeaux_required', 666)}",
        f"  Insanity tier:         {insanity if insanity else 'off'}",
        f"  Progression balancing: {config.get('progression_balancing', 50)}/100",
        *([ f"  Death penalty:         -{config['death_penalty'] * 1000} per death "
            f"(floor: {config['death_penalty'] * 1000})" ]
          if config.get("death_penalty", 0) else
          [ "  Death penalty:         off" ]),
        "",
        "── ENEMIES ─────────────────────────────────────────────",
        f"  Shuffle enemies:       {config.get('shuffle_enemies', False)}",
        f"  Enemy mode:            {config.get('enemy_mode', 'difficulty') if config.get('shuffle_enemies', False) else 'N/A'}",
        f"  Mix movement types:    {config.get('enemy_mix_movement', False) if config.get('shuffle_enemies', False) else 'N/A'}",
        f"  Uncap enemy counts:    {config.get('enemy_uncap_counts', False) if config.get('shuffle_enemies', False) else 'N/A'}",
        f"  Shuffle true forms:    {config.get('shuffle_true_forms', False)}",
        "",
        "── COSMETICS ───────────────────────────────────────────",
        f"  Shuffle music:         {config.get('shuffle_music', False)}",
        f"  Shuffle voices:        {config.get('shuffle_voices', False)}",
        f"  Shuffle weapon SFX:    {config.get('shuffle_weapons_sfx', False)}",
        f"  Shuffle enemy SFX:     {config.get('shuffle_enemies_sfx', False)}",
        f"  Shuffle ambients:      {config.get('shuffle_ambients', False)}",
        f"  Ambient mode:          {config.get('ambient_mode', 'global')}",
        f"  Shuffle sky:           {config.get('shuffle_sky', False)}",
        "",
    ]

    if soul_thresholds is not None:
        lines += _spoiler_soul_threshold_section(soul_thresholds)

    lines += _spoiler_gate_section(gate_remap, sl_thresholds=soul_thresholds)

    if entrance_shuffle is not None:
        lines += ["", unified_spoiler_section(entrance_shuffle)]

    if spheres:
        lines.append("")
        lines.append("── PLAYTHROUGH ─────────────────────────────────────────────────────")
        lines.append("")
        for s in spheres:
            if not s["items"] and not s["cadeaux"]:
                continue
            header = (f"  Sphere {s['sphere']:<2}"
                      f"  [Souls: {s['souls_start']}→{s['souls_end']}, SL{s['sl']}]")
            if s["new_areas"]:
                header += f"  New areas: {', '.join(s['new_areas'])}"
            lines.append(header)
            for obj, left, item_friendly, region in s["items"]:
                lines.append(f"    {left:<80} {item_friendly}")
            if s["cadeaux"]:
                lines.append(f"    [cadeaux]  {s['cadeaux']} found")
            lines.append("")

    lines += ["", "── ITEM LOCATIONS ─────────────────────────────────────", ""]
    for (folder, source_file), patches in sorted(patches_by_folder.items()):
        if not patches:
            continue
        orig_map = {r.offset: r.name
                    for r in records_by_folder.get(folder, [])
                    if r.source_file == source_file}
        meaningful = {
            offset: (orig_map.get(offset, "???"), pd["name"])
            for offset, pd in patches.items()
            if pd["name"] not in CADEAUX_TYPES and pd["name"] not in BARREL_TYPES
        }
        if not meaningful:
            continue
        lines.append(f"\n  {LEVEL_NAMES.get(folder, folder)} [{source_file}]:")
        for offset, (orig, new_name) in sorted(meaningful.items()):
            lines.append(f"    0x{offset:04X}: {orig:<35} -> {new_name}")

    # lines += ["", "── CADEAUX LOCATIONS ──────────────────────────────────", ""]
    # cadeaux_by_folder: dict = {}
    # for folder, records in records_by_folder.items():
    #     for rec in records:
    #         k        = (folder, rec.source_file)
    #         new_name = patches_by_folder.get(k, {}).get(rec.offset, {}).get("name", rec.name)
    #         if new_name in CADEAUX_TYPES or (new_name in BARREL_TYPES and rec.has_drop):
    #             cadeaux_by_folder.setdefault(folder, []).append(
    #                 (rec.offset, rec.name, new_name, rec.source_file, rec.has_drop))
    #
    # total_cadeaux = 0
    # for folder in sorted(cadeaux_by_folder):
    #     entries = sorted(cadeaux_by_folder[folder])
    #     total_cadeaux += len(entries)
    #     lines.append(f"\n  {LEVEL_NAMES.get(folder, folder)}:  ({len(entries)} cadeaux)")
    #     for offset, orig, new_name, source_file, has_drop in entries:
    #         slot = ("barrel slot - break it" if orig in BARREL_TYPES else
    #                 "soul slot"              if orig in DARK_SOUL_TYPES else
    #                 "cadeaux slot"           if orig in CADEAUX_TYPES else orig)
    #         lines.append(f"    0x{offset:04X}: [{source_file}]  {new_name:<25} ← {slot}")
    #
    # lines.append(f"\n  Total cadeaux: {total_cadeaux}")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Spoiler log: {output_path}")


# ── KPF repack ────────────────────────────────────────────────────────────────

def repack_after_patch(game_dir, patches_by_folder, gate_remap, config,
                       spoiler_path, work_dir, extra_mod_files=None):
    kpf_files = find_kpf_files(game_dir)
    if not kpf_files:
        print("\nNo KPF files found - cannot determine internal paths")
        return

    print("\nForging the mod KPF — binding the ritual into the world...")
    kpf_index = build_kpf_index(kpf_files)
    mod_files = {}
    for folder in LEVEL_FOLDERS:
        for filename in SOUL_RSC_FILES | ENEMY_RSC_FILES:  # ← add ENEMY_RSC_FILES
            local = Path(work_dir) / "levels" / folder / filename
            if local.exists():
                matches = find_file_in_kpf(kpf_index, f"*/{folder}/{filename}")
                internal = matches[0][0] if matches else f"levels/{folder}/{filename}"
                mod_files[internal] = str(local)

    gates_changed = any(
        gate_remap.get(g) != GATE_VANILLA_SL.get(g)
        for g in GATE_VANILLA_SL
    )

    if gates_changed:
        for folder in {"deadside", "wastland", "t1tchgad", "t2wlkgad", "t3swmgad", "ah4fogom"}:
            local = Path(work_dir) / "levels" / folder / "links.e2o"
            if local.exists():
                matches  = find_file_in_kpf(kpf_index, f"*/{folder}/links.e2o")
                internal = matches[0][0] if matches else f"levels/{folder}/links.e2o"
                mod_files[internal] = str(local)

        # Include patched ARC deco RSC files for all levels with gate ARC decos.
        # Most levels use events.rsc; ah4fogom uses instance.rsc (see _FOLDER_ARC_RSC
        # in patch_gate_arc_decos and _ARC_RSC_BY_FOLDER in Step 9).
        _ARC_RSC_PACK = {
            "deadside":  "events.rsc",
            "t1tchgad":  "events.rsc",
            "t2wlkgad":  "events.rsc",
            "t3swmgad":  "events.rsc",
            "wastland":  "events.rsc",
            "ah4fogom":  "instance.rsc",
        }
        for folder, arc_rsc in _ARC_RSC_PACK.items():
            local = Path(work_dir) / "levels" / folder / arc_rsc
            if local.exists():
                matches  = find_file_in_kpf(kpf_index, f"*/{folder}/{arc_rsc}")
                internal = matches[0][0] if matches else f"levels/{folder}/{arc_rsc}"
                mod_files[internal] = str(local)
            else:
                print(f"  [DBG arc_rsc] {folder}/{arc_rsc}: local file NOT FOUND at {local}")

    # levels.txt tracker patch
    _ltxt = Path(work_dir) / "scripts" / "levels.txt"
    if _ltxt.exists():
        matches = find_file_in_kpf(kpf_index, "scripts/levels.txt")
        internal = matches[0][0] if matches else "scripts/levels.txt"
        mod_files[internal] = str(_ltxt)

    # loc_english.txt tracker patch
    _leng = Path(work_dir) / "localization" / "loc_english.txt"
    if _leng.exists():
        matches = find_file_in_kpf(kpf_index, "localization/loc_english.txt")
        internal = matches[0][0] if matches else "localization/loc_english.txt"
        mod_files[internal] = str(_leng)

    if extra_mod_files:
        mod_files.update(extra_mod_files)

    if not mod_files:
        print("  Nothing to seal — the KPF stays untouched")
        return
    build_and_install_mod(game_dir, mod_files)


# ── Validation ────────────────────────────────────────────────────────────────

def validate_final_seed(work_dir: str, progression_placement: dict = None, patches_by_folder: dict = None) -> None:
    print("\n── Run validation ──────────────────────────────────")
    levels_path = Path(work_dir) / "levels"
    error_count = 0

    # ── 1. File record integrity ──────────────────────────────────────────────
    for folder in LEVEL_FOLDERS:
        if folder in EXCLUDED_LEVELS:
            continue
        for source_file in SOUL_RSC_FILES:
            rsc_file = levels_path / folder / source_file
            if not rsc_file.exists():
                continue
            try:
                records = parse_rsc_file(str(rsc_file), folder)
            except ValueError:
                continue
            for r in records:
                if r.name in DARK_SOUL_TYPES and r.save_idx == 0:
                    print(f"  [!] Soul ID=0: {folder}/{source_file} at 0x{r.offset:04X}")
                    error_count += 1
                if not r.name.upper().startswith("RSC_"):
                    print(f"  [!] Bad name {r.name!r}: {folder}/{source_file} at 0x{r.offset:04X}")
                    error_count += 1
                if len(r.name) >= NAME_MAXLEN:
                    print(f"  [!] Name too long {r.name!r}: {folder}/{source_file} at 0x{r.offset:04X}")
                    error_count += 1

    # ── 2. Placement coverage check ───────────────────────────────────────────
    if progression_placement and patches_by_folder:
        patched_keys = {
            f"{folder}:{source_file}:0x{offset:04X}"
            for (folder, source_file), patches in patches_by_folder.items()
            for offset in patches
        }
        all_missing = [
            loc_key for loc_key in progression_placement
            if loc_key not in patched_keys
        ]
        # Suppress loc_keys that are explicitly unverified (is_verified=False in
        # locations.csv) — fill never places items there, so any appearance here
        # is a ghost entry, not a real coverage failure.
        skipped_unverified = [lk for lk in all_missing if lk in UNVERIFIED_LOCS]
        missing = [lk for lk in all_missing if lk not in UNVERIFIED_LOCS]
        if skipped_unverified:
            print(f"  [skip] {len(skipped_unverified)} unverified loc(s) not written (expected, is_verified=False):")
            for lk in skipped_unverified[:5]:
                print(f"      {lk}")
            if len(skipped_unverified) > 5:
                print(f"      ... and {len(skipped_unverified) - 5} more")
        if missing:
            print(f"  [!] {len(missing)} placements not written to files:")
            for lk in missing[:10]:
                print(f"      {lk}")
            if len(missing) > 10:
                print(f"      ... and {len(missing) - 10} more")
            error_count += len(missing)

    if error_count == 0:
        print(f"  ✅ Seed confirmed beatable.")
    else:
        print(f"  ❌ {error_count} validation error(s) — seed may not be beatable. Check output above.")

def run_patcher(game_dir, seed, config, output_dir=None, dry_run=False, use_kpf=True):
    rng       = random.Random(seed)
    _resolve_random_config(config, rng)

    # Generate soul thresholds early (before spoiler log) so they appear in the log
    sl_thresholds_result = None
    _st_mode = config.get("soul_threshold_mode", "off")
    if _st_mode and _st_mode != "off":
        sl_thresholds_result = randomize_soul_thresholds(rng, mode=_st_mode)
        print(f"  [soul_thresholds] Mode={_st_mode}: { {sl: sl_thresholds_result[sl] for sl in range(1, 11)} }")

    game_path = Path(game_dir)

    # ── KPF extraction (now uses updated KPFs) ────────────────────────────────

    kpf_files = find_kpf_files(game_dir) if use_kpf else []
    using_kpf = bool(kpf_files)

    if using_kpf:
        work_path = game_path / f"_randomizer_work_{seed}"

        # ── Clean up stale working files ──────────────────────────────────────
        # If a work dir for this seed already exists (left over from a prior run
        # or a prior randomizer version), don't wipe it — the user may want it.
        # Instead, bump to the next free suffix (_01, _02, …) so each run gets
        # its own clean directory and stale files can never bleed into the new pack.
        if work_path.exists():
            n = 1
            while True:
                candidate = game_path / f"_randomizer_work_{seed}_{n:02d}"
                if not candidate.exists():
                    work_path = candidate
                    break
                n += 1
        work_path.mkdir()

        print(f"☽  Shadow Man Remastered Randomizer  ☽")
        print(f"Seed: {seed}  |  The voodoo stirs tonight  |  {len(kpf_files)} archives to reshape")
        print()
        print("Tearing open the veil between worlds...")
        kpf_index = extract_game_files(kpf_files, str(work_path), LEVEL_FOLDERS)
        # Don't pass game_dir — we always want to extract from vanilla base KPFs,
        # not the previously installed randomizer mod.
        # kpf_index = extract_game_files(kpf_files, str(work_path), LEVEL_FOLDERS, game_dir=str(game_path))
        levels_kpf = which_kpf_has_levels(kpf_index)
        print(f"  Archive of souls: {levels_kpf}")
        levels_path = work_path / "levels"
    else:
        work_path   = game_path
        levels_path = game_path / "levels"
        kpf_index   = None
        print(f"☽  Shadow Man Remastered Randomizer  ☽")
        print(f"Seed: {seed}  |  Marking Deadside directly  |  {game_dir}")

    # ── Pre-step: always inject gad records so they appear in parsed data ─────
    if config.get("shuffle_gad_temples", False):
        print("\nPreparing the Gad shrines for their new guardians...")
    for folder, filename, x, y, z, zone in GAD_INJECTION_SITES:
        rsc_path = levels_path / folder / filename
        if rsc_path.exists():
            data = bytearray(rsc_path.read_bytes())
            off, already = inject_record(data, x, y, z, zone)
            if not already:
                rsc_path.write_bytes(bytes(data))
            expected = GAD_PICKUP_EXPECTED_OFFSETS.get(folder)
            if expected is not None and off != expected:
                print(f"  WARNING: {folder} GAD_PICKUP offset mismatch — "
                      f"got 0x{off:04X}, expected 0x{expected:04X}. "
                      f"Update extracted_locations.py and constants.py.")
            if config.get("shuffle_gad_temples", False):
                status = "already present" if already else "injected"
                print(f"  {folder}/{filename} @ 0x{off:04X} ({status})")

    # ── Pre-step: inject GAD platform blocker into instance.rsc ─────────────────
    if config.get("shuffle_gad_temples", False):
        _blocker_tag = GAD_BLOCKER_RSC.encode("ascii")
        for _folder, _bx, _by, _bz, _bzone in GAD_BLOCKER_SITES:
            _blocker_path = levels_path / _folder / "instance.rsc"
            if not _blocker_path.exists():
                continue
            _bdata = bytearray(_blocker_path.read_bytes())
            if _blocker_tag not in _bdata:
                inject_rsc_record(_bdata, build_rsc_record(GAD_BLOCKER_RSC, _bx, _by, _bz, _bzone), allow_expand=True)
                _blocker_path.write_bytes(bytes(_bdata))
                print(f"  [gad_blocker] {GAD_BLOCKER_RSC} injected into {_folder}/instance.rsc  [PLACEHOLDER COORDS]")
            else:
                print(f"  [gad_blocker] {GAD_BLOCKER_RSC} already present in {_folder}/instance.rsc")

    out_path = Path(output_dir) if output_dir else work_path
    out_path.mkdir(parents=True, exist_ok=True)
    print()

    # Verify injection succeeded
    missing = []
    for folder, filename, x, y, z, zone in GAD_INJECTION_SITES:
        rsc_path = levels_path / folder / filename
        if rsc_path.exists():
            data = rsc_path.read_bytes()
            if _find_existing(data, zone, x, y, z) is None:
                missing.append(f"{folder}/{filename}")
    if missing:
        print(f"  WARNING: GAD_PICKUP records missing after injection: {missing}")

    # ── Step 1: Parse RSC files ───────────────────────────────────────────────
    print("Reading the whispers of Deadside...")
    records_by_folder: dict = {}
    for folder in LEVEL_FOLDERS:
        folder_path = levels_path / folder
        if not folder_path.exists():
            continue
        records = []
        files_found = []
        for filename in SOUL_RSC_FILES:
            path = folder_path / filename
            if not path.exists():
                continue
            try:
                file_records = parse_rsc_file(str(path), folder)
                for r in file_records:
                    r.source_file = filename
                records.extend(file_records)
                files_found.append(filename)
            except ValueError as e:
                print(f"  Skipping {folder}/{filename}: {e}")
        if not records:
            continue
        records_by_folder[folder] = records
        souls = sum(1 for r in records if r.category == "soul")
        weps  = sum(1 for r in records if r.category == "weapon")
        prog  = sum(1 for r in records if r.category == "progression")
        lore  = sum(1 for r in records if r.category == "lore")
        print(f"  {folder:<12}: {souls} dark souls  {weps} weapons  {prog} relics  {lore} codex"
              f"  [{', '.join(files_found)}]")

    # Object map CSV
    object_map = [
        {"folder": folder, "source_file": rec.source_file,
         "offset": f"0x{rec.offset:04X}", "name": rec.name, "category": rec.category,
         "save_idx": rec.save_idx, "has_drop": rec.has_drop, "zone": rec.zone,
         "x": round(rec.x, 2), "y": round(rec.y, 2), "z": round(rec.z, 2)}
        for folder, records in records_by_folder.items()
        for rec in records
    ]
    if object_map:
        map_path = out_path / "object_map.csv"
        with open(map_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=object_map[0].keys())
            writer.writeheader()
            writer.writerows(object_map)
        print(f"World manifest: {map_path}")

    # ── Resolve random starting item before fill ──────────────────────────────
    if config.get("random_starting_item"):
        starting_item_rsc = rng.choice(list(STARTING_ITEM_POOL.values()))
        config["starting_item"] = starting_item_rsc

    # ── Step 1b: Build entrance shuffle (needed by fill for correct logic) ────
    entrance_shuffle = None
    entrance_mode = config.get("entrance_mode", "off")
    if entrance_mode and entrance_mode != "off":
        entrance_rng = random.Random(seed ^ 0xE117)
        entrance_shuffle = shuffle_unified(
            entrance_rng,
            mode=entrance_mode,
            shuffle_gad_temples=config.get("shuffle_gad_temples", False),
        )

    # ── Step 2: Run assumed fill (includes gate shuffle) ─────────────────────
    print("\nLegion's chaos reshapes the world — scattering the relics...")
    progression_placement, gate_remap = run_assumed_fill(rng, config, entrance_shuffle=entrance_shuffle)

    # Compute true form remap now (needs gate_remap) so simulate_playthrough
    # uses correct fixed soul positions for sphere log
    true_form_loc_remap = None
    tf_patches_early = {}
    if config.get("shuffle_true_forms", False):
        tf_patches_early, true_form_loc_remap = randomize_true_forms(rng, gate_remap)

    active_fixed_soul_locs = apply_true_form_remap(true_form_loc_remap)
    # Sphere simulation deferred to after Step 4e so entrance_shuffle is known;
    # using vanilla level_rules here would let Wasteland open at SL1 even when
    # the entrance shuffle has placed the Wasteland spoke behind the salvage gate.
    spheres = None

    # ── Step 3: Write gate SL values to links.e2o ────────────────────────────
    gates_changed = any(
        gate_remap.get(g) != GATE_VANILLA_SL.get(g)
        for g in GATE_VANILLA_SL
    )
    if gates_changed:
        print("\nReforging the soul gate seals across Deadside...")
        randomize_gate_sl_links(gate_remap, levels_path=levels_path)

        if not (levels_path / "deadside" / "events.rsc").exists() and using_kpf:
            try:
                matches = find_file_in_kpf(kpf_index, "levels/deadside/events.rsc")
                if matches:
                    (levels_path / "deadside").mkdir(parents=True, exist_ok=True)
                    extract_file_from_kpf(
                        str(Path(kpf_index.kpf_dir) / matches[0][1]),
                        matches[0][0],
                        str(levels_path / "deadside" / "events.rsc"),
                    )
            except Exception as e:
                print(f"  WARNING: events.rsc extraction failed: {e}")

    # ── Step 4: RSC item patching ─────────────────────────────────────────────
    print("\nScattering the relics across Deadside...")
    patches_by_folder, marker_sites = write_placement_patches(
        records_by_folder,
        progression_placement=progression_placement,
        shuffle_gad_temples=config.get("shuffle_gad_temples", False),
    )
    print(f"  {sum(len(p) for p in patches_by_folder.values())} hiding spots claimed")

    # ── Step 4b: Starting item patch ─────────────────────────────────────────────
    starting_item_rsc = config.get("starting_item")
    # Eclipser is stored as Part 1 in the pool but the actual piece is random.
    if starting_item_rsc == "RSC_X_ECLIPSER_PART1":
        starting_item_rsc = rng.choice([
            "RSC_X_ECLIPSER_PART1",
            "RSC_X_ECLIPSER_PART2",
            "RSC_X_ECLIPSER_PART3",
        ])
    if starting_item_rsc:
        swamp_instance = levels_path / "swampday" / "instance.rsc"
        if swamp_instance.exists():
            save_idx = None
            template = None
            for folder, records in records_by_folder.items():
                for rec in records:
                    if rec.name == starting_item_rsc:
                        save_idx = rec.save_idx
                        template = rec.raw
                        break
                if save_idx is not None:
                    break
            starting_y = 20.0 + ITEM_Y_ADJUST.get((starting_item_rsc, "instance.rsc"), ITEM_Y_ADJUST.get((starting_item_rsc, None), 0.0))
            patch_rsc_file(str(swamp_instance), {
                0x17CA: {
                    "name": starting_item_rsc,
                    "reward": save_idx or 0,
                    "y_adjust": starting_y,
                    "source_file": "instance.rsc",
                }
            }, record_templates={starting_item_rsc: template} if template else None)
            print(f"  A gift stirs in the Bayou swamp — {starting_item_rsc} awaits Michael's arrival")
        else:
            print(f"  WARNING: swampday/instance.rsc not found — starting item not placed")

    # ── Step 4c: levels.txt tracker ──────────────────────────────────────────
    # Always generate all variants so the user can swap modes without re-running:
    #   levels_vanilla.txt  — read-only vanilla copy (never overwritten)
    #   levels_stripped.txt — item directives removed (no hints)
    #   levels_hints.txt    — accurate randomized item hints
    # The active variant (based on config) is copied to levels.txt for packing.
    #
    # IMPORTANT: levels_vanilla.txt is the stable read-only source for both
    # patchers. levels.txt (the active file) lives at the same path as the
    # KPF-extracted source and gets overwritten by the final copy step — if
    # either patcher read from it instead, a re-run would see the stripped
    # output (653 cadeaux) rather than the vanilla total (666).
    _scripts_dir = work_path / "scripts"
    _scripts_dir.mkdir(parents=True, exist_ok=True)

    _levels_vanilla  = _scripts_dir / "levels_vanilla.txt"
    _levels_stripped = _scripts_dir / "levels_stripped.txt"
    _levels_hints    = _scripts_dir / "levels_hints.txt"
    _levels_active   = _scripts_dir / "levels.txt"

    # Locate the KPF-extracted levels.txt (may sit in scripts/ or levels/)
    _levels_txt_src = work_path / "scripts" / "levels.txt"
    if not _levels_txt_src.exists():
        _levels_txt_src = work_path / "levels" / "levels.txt"

    # Save a vanilla copy if we haven't already (guards against re-runs where
    # _levels_active has already been overwritten with a stripped/hints variant).
    if _levels_txt_src.exists() and not _levels_vanilla.exists():
        shutil.copy2(_levels_txt_src, _levels_vanilla)

    if _levels_vanilla.exists():
        print("\nWeaving the oracle scrolls...")
        strip_levels_txt(_levels_vanilla, _levels_stripped)
        patch_levels_txt(_levels_vanilla, progression_placement, gate_remap,
                         _levels_hints, true_form_loc_remap=true_form_loc_remap)

        # Copy selected variant to the active file for KPF packing
        if config.get("patch_tracker", False):
            shutil.copy2(_levels_hints, _levels_active)
            print("  Oracle mode: hints revealed (levels_hints.txt)")
        else:
            shutil.copy2(_levels_stripped, _levels_active)
            print("  Oracle mode: secrets kept (levels_stripped.txt)")
        print("  To switch hint mode: copy levels_hints.txt or levels_stripped.txt "
              "over levels.txt and reinstall the KPF")
    else:
        print("\n  [levels_txt] WARNING: levels_vanilla.txt not found — tracker not patched")

    # ── Step 4d: loc_english.txt tracker labels ───────────────────────────────
    # Generates loc_english_hints.txt (item names) from the read-only vanilla
    # source and copies it to loc_english.txt for packing when patch_tracker
    # is enabled.
    #
    # IMPORTANT: use loc_english_vanilla.txt as the read-only source so that
    # re-runs always patch from clean vanilla, not from a previously patched
    # output (which would corrupt the file on every successive run).
    _loc_dir   = work_path / "localization"
    _loc_dir.mkdir(parents=True, exist_ok=True)
    _leng_vanilla = _loc_dir / "loc_english_vanilla.txt"

    # Extract vanilla from KPF if not yet saved
    if not _leng_vanilla.exists() and using_kpf:
        try:
            _leng_matches = find_file_in_kpf(kpf_index, "localization/loc_english.txt")
            if _leng_matches:
                extract_file_from_kpf(
                    str(Path(kpf_index.kpf_dir) / _leng_matches[0][1]),
                    _leng_matches[0][0],
                    str(_leng_vanilla),
                )
            else:
                print("\n  [loc_english] WARNING: loc_english.txt not found in KPF — skipping")
                _leng_vanilla = None
        except Exception as _e:
            print(f"\n  [loc_english] WARNING: extraction failed: {_e}")
            _leng_vanilla = None

    _leng_base = _leng_vanilla  # alias for clarity below
    if _leng_base and _leng_base.exists():
        _leng_hints  = _loc_dir / "loc_english_hints.txt"
        _leng_active = _loc_dir / "loc_english.txt"

        print("\nRenaming the relics in the Book of Names...")
        patch_loc_english_for_tracker(
            _leng_base, _leng_hints,
            shuffle_gad_temples=config.get("shuffle_gad_temples", False),
        )

        # Copy to active file for KPF packing
        if config.get("patch_tracker", False):
            shutil.copy2(_leng_hints, _leng_active)
            print("  Relic names updated for the tracker (loc_english_hints.txt)")

    # ── Step 4e: Unified entrance + soul-gate shuffle ─────────────────────────
    entrance_cut_files = {}
    if entrance_shuffle is not None:
        CUT_PREFIX = "cutscene/scripts"
        scripts_dir = work_path / CUT_PREFIX
        scripts_dir.mkdir(parents=True, exist_ok=True)
        print(f"\nThe portals between worlds tremble -- entrances reshuffled ({entrance_mode})...")

        if using_kpf:
            cut_paths_needed = (
                    [f"{CUT_PREFIX}/{t.portal_folder}/{t.portal_file}"
                     for t in UNIFIED_TRANSITIONS if t.portal_file is not None]
                    + [f"{CUT_PREFIX}/{t.spoke_folder}/{t.spoke_exit_file}"
                       for t in UNIFIED_TRANSITIONS]
            )
            for kpf_rel in cut_paths_needed:
                local = work_path / kpf_rel
                if local.exists():
                    local.unlink()
                matches = find_file_in_kpf(kpf_index, kpf_rel)
                if matches:
                    local.parent.mkdir(parents=True, exist_ok=True)
                    extract_file_from_kpf(
                        str(Path(kpf_index.kpf_dir) / matches[0][1]),
                        matches[0][0],
                        str(local),
                    )
                else:
                    print(f"  WARNING: {kpf_rel} not found in KPF -- entrance patch may be incomplete")

            _rsc_kpf_rel = "levels/as3schis/events.rsc"
            _rsc_local = levels_path / "as3schis" / "events.rsc"
            if _rsc_local.exists():
                _rsc_local.unlink()
            _rsc_matches = find_file_in_kpf(kpf_index, _rsc_kpf_rel)
            if _rsc_matches:
                _rsc_local.parent.mkdir(parents=True, exist_ok=True)
                extract_file_from_kpf(
                    str(Path(kpf_index.kpf_dir) / _rsc_matches[0][1]),
                    _rsc_matches[0][0],
                    str(_rsc_local),
                )
            else:
                print(f"  WARNING: {_rsc_kpf_rel} not found in KPF -- schism byte patch will fail")

        # Shuffle already built in Step 1b — just apply it to disk
        apply_unified_shuffle(entrance_shuffle, scripts_dir, verbose=True)

        for _t in UNIFIED_TRANSITIONS:
            _rels = [f"{CUT_PREFIX}/{_t.spoke_folder}/{_t.spoke_exit_file}"]
            if _t.portal_file is not None:
                _rels.append(f"{CUT_PREFIX}/{_t.portal_folder}/{_t.portal_file}")
            for _rel in _rels:
                _local = work_path / _rel
                if _local.exists():
                    entrance_cut_files[_rel] = str(_local)

        _rsc_local = levels_path / "as3schis" / "events.rsc"
        if _rsc_local.exists():
            entrance_cut_files["levels/as3schis/events.rsc"] = str(_rsc_local)

    # ── Step 4f: Sphere simulation (needs entrance_shuffle for correct rules) ──
    # Build level_rules using entrance-shuffle-aware rules when applicable so
    # the spoiler-log spheres reflect the actual reachability logic (e.g.
    # Wasteland locked behind the salvage gate, not vanilla GATE_DEADSIDE_WASTELAND).
    _sphere_level_rules = _regions.build_level_rules(gate_remap, entrance_shuffle)

    # When items aren't shuffled they stay at their vanilla locations and are
    # never added to progression_placement by fill.  Inject those vanilla slots
    # directly so the simulation collects them at the correct sphere (i.e. when
    # the player first reaches their location), rather than pre-granting them
    # in baseline_inv which would make them appear available from sphere 1.
    _sphere_placement = dict(progression_placement)
    for _loc in CHECKABLE_LOCS:
        if _loc.loc_key in _sphere_placement:
            continue  # fill already placed something here; vanilla item is gone
        if not config.get("shuffle_eclipsers", True) and _loc.category == "eclipser":
            _sphere_placement[_loc.loc_key] = _loc
        elif not config.get("shuffle_retractors", True) and _loc.category == "retractor":
            _sphere_placement[_loc.loc_key] = _loc
        elif not config.get("shuffle_accumulators", True) and _loc.category == "accumulator":
            _sphere_placement[_loc.loc_key] = _loc

    if config.get("starting_item"):
        STARTING_ITEMS.add(config["starting_item"])
    _, _, spheres = simulate_playthrough(
        _sphere_placement,
        CHECKABLE_LOCS + active_fixed_soul_locs,
        _sphere_level_rules,
        collect_spheres=True,
        shuffle_gad_temples=config.get("shuffle_gad_temples", False),
    )
    if config.get("starting_item"):
        STARTING_ITEMS.discard(config["starting_item"])

    # ── Step 5: Spoiler log ───────────────────────────────────────────────────
    spoiler_path = out_path / f"spoiler_seed_{seed}.txt"
    write_spoiler_log(
        str(spoiler_path), seed, patches_by_folder,
        gate_remap, records_by_folder, config,
        spheres=spheres,
        entrance_shuffle=entrance_shuffle,
        soul_thresholds=sl_thresholds_result,
    )

    if dry_run:
        print("\nThe ritual was a vision — no files were changed. Remove --dry-run to commit it.")
        return

    # ── Build record templates from parsed data ───────────────────────────────
    record_templates: dict[str, bytes] = {}
    for folder, records in records_by_folder.items():
        for rec in records:
            if rec.name not in record_templates:
                record_templates[rec.name] = rec.raw


    # ── Step 6: Apply RSC patches ─────────────────────────────────────────────
    print("\nBinding the chaos into the world...")

    for folder in LEVEL_FOLDERS:
        folder_path = levels_path / folder
        if not folder_path.exists():
            continue
        for source_file in SOUL_RSC_FILES:
            key      = (folder, source_file)
            patches        = patches_by_folder.get(key, {})
            rsc_file = folder_path / source_file
            if not rsc_file.exists() or not patches:
                continue
            level_records = [r for r in records_by_folder.get(folder, [])
                             if r.source_file == source_file]
            if source_file == "quest.rsc":
                audit_govi_patches(str(rsc_file), patches, level_records)
            patch_rsc_file(str(rsc_file), patches, record_templates=record_templates)
            if source_file == "quest.rsc":
                verify_patch(str(rsc_file), patches)
            print(f"  [{source_file.upper().replace('.RSC','')}] "
                  f"{folder}/{source_file} ({len(patches)} changes)")

    # ── Step 6b: Enemy shuffle ────────────────────────────────────────────────
    if config.get("shuffle_enemies", False) or config.get("shuffle_true_forms", False):
        print("\nLegion repositions his minions across Deadside...")

        tf_patches = tf_patches_early  # computed in Step 2, rng already advanced

        if config.get("shuffle_enemies", False):
            enemy_patches = randomize_enemies(rng, levels_path, config,
                                              true_form_patches=tf_patches,
                                              gate_remap=gate_remap)
        else:
            enemy_patches = {}

        for folder_key, patches in tf_patches.items():
            enemy_patches.setdefault(folder_key, {}).update(patches)

        for (folder, source_file) in sorted(enemy_patches):
            patches = enemy_patches[(folder, source_file)]
            if not patches:
                continue
            rsc_file = levels_path / folder / source_file
            if not rsc_file.exists():
                print(f"  WARNING: {folder}/{source_file} not found — skipping")
                continue
            patch_rsc_file(str(rsc_file), patches, record_templates=record_templates)
            print(f"  [{source_file.upper().replace('.RSC', '')}] "
                  f"{folder}/{source_file} ({len(patches)} changes)")

        for key, patches in enemy_patches.items():
            patches_by_folder.setdefault(key, {}).update(patches)

    else:
        enemy_patches = {}

    # ── Step 6b.5: Boss shuffle — SHELVED ────────────────────────────────────
    # Boss shuffle is disabled pending investigation of the enmevent.evt
    # fight-trigger format across all 5 boss levels.  Code lives in
    # randomizers/boss_randomizer.py; re-wire here when ready.
    boss_placement: dict = {}

    # ── Step 6b.6: Ambient creature shuffle ──────────────────────────────────
    # Shuffles friendly/ambient creatures (rats, egrets, flies, butterflies,
    # friendly fish) across their slots.  Remove or set shuffle_ambients=False
    # to disable without touching any other code.
    ambient_patches: dict = {}
    if config.get("shuffle_ambients", False):
        print("\nThe spirits of the wild slip between their haunts...")
        ambient_patches = randomize_ambients(rng, levels_path, config)
        for (folder, source_file), patches in sorted(ambient_patches.items()):
            if not patches:
                continue
            rsc_file = levels_path / folder / source_file
            if not rsc_file.exists():
                print(f"  WARNING: {folder}/{source_file} not found — ambient patch skipped")
                continue
            patch_rsc_file(str(rsc_file), patches, record_templates=record_templates)
            print(f"  [{source_file.upper().replace('.RSC', '')}] "
                  f"{folder}/{source_file} ({len(patches)} changes)")
        patches_by_folder.update(ambient_patches)

    # ── Step 6b.5: Inject special item FX for insanity placements ────────────
    if marker_sites:
        # print("\nSKIPPING Injecting special item FX markers for key items in soul/cadeaux slots...")
        print("\nMarking the sacred hiding places with voodoo sigils...")
        n_markers = inject_special_item_fx(marker_sites, levels_path)
        print(f"  {n_markers} insanity sigil(s) bound")

    # ── Step 6c: Append enemy/true form/boss sections to spoiler log ─────────
    if enemy_patches or true_form_loc_remap or boss_placement or ambient_patches:
        with open(str(spoiler_path), "a", encoding="utf-8") as f:
            if enemy_patches:
                header = (
                    "── ENEMY SHUFFLE ───────────────────────────────────────"
                    if config.get("shuffle_enemies")
                    else "── TRUE FORM PATCHES ───────────────────────────────────"
                )
                f.write("\n" + "\n".join(enemy_spoiler_section(enemy_patches, header=header)))
            if true_form_loc_remap:
                f.write("\n" + "\n".join(true_form_spoiler_section(true_form_loc_remap)))
            if boss_placement:
                f.write("\n" + "\n".join(boss_spoiler_section(boss_placement)))
            if ambient_patches:
                f.write("\n" + "\n".join(ambient_spoiler_section(ambient_patches)))

    # (entrance shuffle section now written inline in write_spoiler_log,
    #  right after the soul gate block)

    # ── Step 7: EXE patches ───────────────────────────────────────────────────────
    exe_src = list(game_path.glob("thoth_x64.exe"))
    if exe_src:
        src = exe_src[0]
        patched = src.parent / "thoth_x64_patched.exe"
        try:
            # Delete any existing patched EXE before copying.  If we just
            # overwrite in-place, a PermissionError mid-copy can leave a
            # partially-written file; worse, a stale buggy EXE from a prior
            # randomizer version would persist if the copy silently fails.
            # Deleting first ensures either a clean new file or no file at all.
            if patched.exists():
                patched.unlink()
            shutil.copy2(str(src), str(patched))

            # Always: prison key card render fix
            apply_prison_keycard_patch(str(patched), dry_run=dry_run)

            # Cadeaux interaction threshold/cost patch
            cadeau_result = apply_cadeau_step_patch(
                str(patched), rng, config, dry_run=dry_run
            )

            # Health patch
            health_result = apply_health_patch(
                str(patched), rng, config, dry_run=dry_run
            )

            # Death penalty patch
            if config.get("death_penalty", 0):
                apply_death_penalty_patch(str(patched), step=config["death_penalty"], dry_run=dry_run)

            # Soul threshold patch
            if sl_thresholds_result is not None:
                apply_soul_threshold_patch(str(patched), sl_thresholds_result, dry_run=dry_run)

            # Conditional: gad pickup dispatch patch + temple NOPs
            if config.get("shuffle_gad_temples", False):
                apply_gad_pickup_patch(
                    str(patched),
                    shuffle_temples=True,
                    dry_run=dry_run,
                )
                print(f"\nThe Asylum's code has been rewritten — EXE patched: {patched.name}")
            else:
                print(f"\nPrison key card fixed — EXE patched: {patched.name}")

        except PermissionError:
            print(
                f"\n⚠ Cannot write to {patched.name} — is Shadow Man Remastered currently running?\n"
                f"  Close the game and run the randomizer again."
            )
    else:
        print("\nWARNING: thoth_x64.exe not found - EXE patches skipped")

    # ── Step 8: Gate remap JSON ───────────────────────────────────────────────
    threshold_json = out_path / "soul_thresholds.json"
    effective_sl = sl_thresholds_result if sl_thresholds_result is not None else VANILLA_SL_THRESHOLDS
    with open(threshold_json, "w") as f:
        json.dump({
            "seed": seed,
            "vanilla_sl_thresholds": VANILLA_SL_THRESHOLDS,
            "randomized_sl_thresholds": sl_thresholds_result,
            "gate_remap": {g: sl for g, sl in gate_remap.items()},
            "effective_thresholds": {
                gate_id: effective_sl[gate_remap.get(gate_id, GATE_VANILLA_SL[gate_id])]
                for gate_id in GATE_VANILLA_SL
            },
        }, f, indent=2)
    print(f"\nSoul gate manifest sealed: {threshold_json}")

    # ── Step 9: ARC deco patch + KPF repack ──────────────────────────────────
    # Per-folder RSC file that holds ARC deco records (must match _FOLDER_ARC_RSC
    # inside patch_gate_arc_decos).
    _ARC_RSC_BY_FOLDER = {
        "deadside":  "events.rsc",
        "t1tchgad":  "events.rsc",
        "t2wlkgad":  "events.rsc",
        "t3swmgad":  "events.rsc",
        "wastland":  "events.rsc",
        "ah4fogom":  "instance.rsc",   # ARC deco lives in instance.rsc, not events.rsc
    }
    if gates_changed and using_kpf:
        try:
            for folder, arc_rsc in _ARC_RSC_BY_FOLDER.items():
                arc_path = levels_path / folder / arc_rsc
                if not arc_path.exists():
                    matches = find_file_in_kpf(kpf_index, f"*/{folder}/{arc_rsc}")
                    if matches:
                        arc_path.parent.mkdir(parents=True, exist_ok=True)
                        extract_file_from_kpf(
                            str(Path(kpf_index.kpf_dir) / matches[0][1]),
                            matches[0][0],
                            str(arc_path),
                        )
        except Exception as e:
            print(f"  WARNING: ARC deco RSC extraction failed: {e}")

    if gates_changed:
        n_arc = patch_gate_arc_decos(levels_path, gate_remap)
        if n_arc == 0:
            print("  Gate decos: no changes from vanilla")

    # ── Step 9.5: SFX + music shuffle ────────────────────────────────────────
    music_files = {}
    sfx_files = {}
    sfx_swap_log = {}
    sky_files = {}

    if config.get("shuffle_music", False) and using_kpf:
        music_files = shuffle_music(rng, kpf_files, str(work_path), dry_run=dry_run)

    if (config.get("shuffle_voices", False) or config.get("shuffle_weapons_sfx", False)
            or config.get("shuffle_enemies_sfx", False)) and using_kpf:
        sfx_files, sfx_swap_log = shuffle_sfx(
            rng, kpf_files, str(work_path),
            shuffle_voices=config.get("shuffle_voices", False),
            shuffle_weapons=config.get("shuffle_weapons_sfx", False),
            shuffle_enemies=config.get("shuffle_enemies_sfx", False),
            dry_run=dry_run,
        )

    if sfx_files:
        sfx_lines = sfx_spoiler_section(sfx_swap_log)
        with open(str(spoiler_path), "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(sfx_lines))

    sky_files: dict[str, str] = {}
    if config.get("shuffle_sky", False) and using_kpf:
        sky_files = shuffle_sky(rng, kpf_files, str(work_path), dry_run=dry_run)

    if sky_files:
        sky_lines = sky_spoiler_section(sky_files)
        with open(str(spoiler_path), "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(sky_lines))

    # ── Step 9.7: Build asset override mod files ──────────────────────────────
    randomizer_dir = Path(__file__).resolve().parent
    asset_mod_files = {}
    for src_rel, dst_rel in ASSET_OVERRIDES:
        src = randomizer_dir / src_rel
        if not src.exists():
            print(f"  WARNING: asset override missing — {src_rel}")
            continue
        internal = dst_rel.replace("\\", "/")
        asset_mod_files[internal] = str(src)
        print(f"  [asset] {src_rel} → {internal}")

    if config.get("shuffle_gad_temples", False):
        for src_rel, dst_rel in GAD_ASSET_OVERRIDES:
            src = randomizer_dir / src_rel
            if not src.exists():
                print(f"  WARNING: gad asset override missing — {src_rel}")
                continue
            internal = dst_rel.replace("\\", "/")
            asset_mod_files[internal] = str(src)
            print(f"  [asset/gad] {src_rel} → {internal}")

    msh_mod_files = apply_msh_overrides(randomizer_dir, work_path, kpf_index=kpf_index)

    # ── Step 10: Repack the KPF ───────────────────────────────────────────────
    if using_kpf:
        repack_after_patch(
            str(game_path), patches_by_folder, gate_remap,
            config, str(spoiler_path), str(work_path),
            extra_mod_files={
                **music_files, **sfx_files, **sky_files, **asset_mod_files,
                **msh_mod_files, **entrance_cut_files,
            },
        )

    validate_final_seed(str(work_path), progression_placement, patches_by_folder)
    print(f"\n✨ The ritual is complete. Seed {seed} has been bound to Deadside.")
    print(f"Spoiler log: {spoiler_path}")

# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Shadow Man Remastered Randomizer")
    parser.add_argument("--game-dir",
                        default=str(Path(__file__).resolve().parent.parent),
                        help="Path to Shadow Man Remastered install directory (default: script location)")
    parser.add_argument("--output-dir",            default=None)
    parser.add_argument("--seed",                  type=int, default=None)
    parser.add_argument("--config",                default=None)
    parser.add_argument("--dry-run",               action="store_true")
    parser.add_argument("--restore",               action="store_true")
    parser.add_argument("--progression-balancing", type=_int_or_random, default=50)
    parser.add_argument("--shuffle-progression", action=argparse.BooleanOptionalAction, default=True,
                        help="Shuffle key progression items using assumed-fill (default: on)")
    parser.add_argument("--shuffle-key-items-random", action="store_true",
                        help="Randomly decide per-seed whether to shuffle key items")
    parser.add_argument("--gate-preset",
                        choices=["open", "easy", "medium", "hard", "chaos", "random"],
                        default=None,
                        help="Gate difficulty preset: open=all gates free, easy=light shuffle SL7 cap 6 gates open, "
                             "medium=standard shuffle SL8 cap 3 gates open, hard=full shuffle 1 gate open, "
                             "chaos=fully unconstrained, random=chosen randomly per-seed")
    parser.add_argument("--max-sl", type=int, default=None,
                        help="Cap the maximum SL any shuffled gate can receive (1-10)")
    parser.add_argument("--shuffle-weapons", action=argparse.BooleanOptionalAction, default=True,
                        help="Shuffle weapons (default: on)")
    parser.add_argument("--shuffle-weapons-random", action="store_true",
                        help="Randomly decide per-seed whether to shuffle weapons")
    parser.add_argument("--shuffle-lore", action=argparse.BooleanOptionalAction, default=True,
                        help="Shuffle lore items (default: on)")
    parser.add_argument("--shuffle-lore-random", action="store_true",
                        help="Randomly decide per-seed whether to shuffle lore items")
    parser.add_argument("--shuffle-light-soul", action="store_true",
                        help="Include the Light Soul bonus item in the shuffle pool")
    parser.add_argument("--shuffle-light-soul-random", action="store_true",
                        help="Randomly decide per-seed whether to include the Light Soul in the shuffle pool")
    parser.add_argument("--shuffle-gad-temples", action=argparse.BooleanOptionalAction, default=True,
                        help="Shuffle gad powers as physical pickups via EXE patch (default: on)")
    parser.add_argument("--shuffle-gad-temples-random", action="store_true",
                        help="Randomly decide per-seed whether to shuffle gad temples")
    parser.add_argument("--shuffle-prisms", action=argparse.BooleanOptionalAction, default=False,
                        help="Shuffle prism items as progression items (requires prism locations in CSV)")
    parser.add_argument("--shuffle-prisms-random", action="store_true",
                        help="Randomly decide per-seed whether to shuffle prisms")
    parser.add_argument("--shuffle-retractors", action=argparse.BooleanOptionalAction, default=True,
                        help="Shuffle retractor items (default: on). When off, all 5 retractors stay vanilla.")
    parser.add_argument("--shuffle-retractors-random", action="store_true",
                        help="Randomly decide per-seed whether to shuffle retractors")
    parser.add_argument("--shuffle-accumulators", action=argparse.BooleanOptionalAction, default=True,
                        help="Shuffle accumulator items (default: on). When off, all 3 accumulators stay vanilla.")
    parser.add_argument("--shuffle-accumulators-random", action="store_true",
                        help="Randomly decide per-seed whether to shuffle accumulators")
    parser.add_argument("--shuffle-eclipsers", action=argparse.BooleanOptionalAction, default=True,
                        help="Shuffle eclipser parts (default: on). When off, all 3 eclipser parts stay vanilla.")
    parser.add_argument("--shuffle-eclipsers-random", action="store_true",
                        help="Randomly decide per-seed whether to shuffle eclipser parts")
    parser.add_argument("--starting-item", default=None,
                        help="RSC name of item to place at swamp church (e.g. RSC_X_ENGINEERS_KEY)")
    parser.add_argument("--random-starting-item", action="store_true",
                        help="Pick a random starting item using the seed RNG")
    parser.add_argument("--insanity", nargs="?", const=3, type=_int_or_random, default=0,
                        help="Insanity tier 1-3: 1=soul/govi slots, 2=+cadeaux slots, 3=all slots. Bare --insanity = tier 3. Pass 'random' to randomize per-seed.")
    parser.add_argument("--shuffle-enemies", action="store_true",
                        help="Randomize enemy types in each level")
    parser.add_argument("--enemy-mode", choices=["difficulty", "full", "contextual", "random"],
                        default="difficulty",
                        help="difficulty: depth-weighted placement by enemy tier (default). "
                             "full: purely random within movement type. "
                             "contextual: shuffle within context_group pools.")
    parser.add_argument("--enemy-mix-movement", action="store_true",
                        help="Allow enemies to swap across movement type pools "
                             "(ground/flying/etc) during enemy shuffle")
    parser.add_argument("--shuffle-true-forms", action="store_true",
                        help="Shuffle true form enemy positions with regular enemies")
    parser.add_argument("--shuffle-enemies-random", action="store_true",
                        help="Randomly decide per-seed whether to shuffle enemies")
    parser.add_argument("--shuffle-true-forms-random", action="store_true",
                        help="Randomly decide per-seed whether to shuffle true forms")
    parser.add_argument("--enemy-mix-movement-random", action="store_true",
                        help="Randomly decide per-seed whether to mix enemy movement types")
    parser.add_argument("--enemy-uncap-counts", action="store_true",
                        help="Uncap enemy type counts: each slot independently samples from the "
                             "pool with replacement, so any type can appear 0 or many times")
    parser.add_argument("--enemy-uncap-counts-random", action="store_true",
                        help="Randomly decide per-seed whether to uncap enemy type counts")
    parser.add_argument("--shuffle-ambients", action="store_true",
                        help="Shuffle friendly/ambient creatures (rats, egrets, flies, butterflies, fish) "
                             "across their spawn slots")
    parser.add_argument("--ambient-mode",
                        choices=["global", "full", "contextual"],
                        default="global",
                        help="global: one pool, no bucketing (default). "
                             "full: shuffle within movement_type. "
                             "contextual: shuffle within context_group + movement_type.")
    parser.add_argument("--shuffle-music", action="store_true",
                        help="Shuffle music tracks globally across all levels")
    parser.add_argument("--shuffle-voices", action="store_true",
                        help="Shuffle Shadow Man generic voice lines")
    parser.add_argument("--shuffle-weapons-sfx", action="store_true",
                        help="Shuffle weapon fire/reload sounds within each category")
    parser.add_argument("--shuffle-enemies-sfx", action="store_true",
                        help="Shuffle enemy SFX within each sound-type pool "
                             "(pain sets swap with pain sets, startle with startle, attack with attack)")
    parser.add_argument("--shuffle-sky", action="store_true",
                        help="Shuffle sky textures across levels (per-filename pool — "
                             "000sky.tga swaps with other 000sky.tga files, etc.)")
    parser.add_argument("--open-gates", type=int, default=None, metavar="N",
                        help="Force the first N gates (by vanilla SL order) to SL0, overriding the preset default")
    parser.add_argument("--patch-tracker", action="store_true",
                        help="Rewrite levels.txt map badges to reflect randomized item locations "
                             "(default: strip all item badges to avoid incorrect vanilla hints)")
    parser.add_argument("--altar-cadeaux-required", type=_int_or_random, default=None,
                        help="Cadeaux required and spent per altar/door interaction (1-133, vanilla: 100). Pass 'random' to randomize per-seed.")
    parser.add_argument("--fogometers-cadeaux-required", type=_int_or_random, default=None,
                        help="Cadeaux required to open Fogometers door (min: 5×altar, max: 666, vanilla: 666). Pass 'random' to randomize per-seed.")
    parser.add_argument("--starting-health", type=_int_or_random, default=None,
                        help="Starting max health scale 1-10, where each step = 1000 units (vanilla: 5). Pass 'random' to randomize per-seed.")
    parser.add_argument("--altar-health-grant", type=_int_or_random, default=None,
                        help="Health granted per life altar interaction, scale 1-10 (vanilla: 1). Pass 'random' to randomize per-seed.")
    parser.add_argument("--soul-threshold-mode", choices=["progressive", "balanced", "random"],
                        default=None,
                        help="Randomize SL1–SL10 soul requirements. "
                             "progressive=geometric ramp, balanced=even spacing, random=fully random.")
    parser.add_argument("--soul-threshold-mode-random", action="store_true",
                        help="Pick soul threshold mode randomly per seed.")
    parser.add_argument("--settings-string", default=None,
                        help="Base64 settings string from the GUI — recorded in spoiler log for reproducibility")
    parser.add_argument("--death-penalty", type=int, default=0,
                        help="Reduce max health by step*1000 on each death (floor: step*1000). "
                             "0 = disabled, 1–10 = enabled with that step. "
                             "E.g. --death-penalty 1 gives -1000/death (vanilla equivalent).")
    parser.add_argument("--death-penalty-random", action="store_true",
                        help="Randomly pick a death-penalty step (1–10) per seed.")
    parser.add_argument("--entrance-mode",
                        choices=["off", "deadside_only", "cross_hub", "random"],
                        default="off",
                        help="Shuffle hub <-> spoke entrances. "
                             "deadside_only: shuffle the 9 Deadside portals among themselves; "
                             "Dark Engine soul gates remain vanilla. "
                             "cross_hub: all 14 portals (Deadside + Dark Engine) shuffled "
                             "together; a Deadside portal may lead to a Dark Engine spoke. "
                             "off (default): vanilla entrances.")
    # --soul-gate-mode removed: soul gates are now included in --entrance-mode pool
    args = parser.parse_args()

    if args.restore:
        mods_dir = find_mods_dir(args.game_dir)
        if remove_mod_kpf(mods_dir):
            print("The Mark has faded. Liveside restored to its natural state.")
        else:
            print("No trace of the ritual found — the world is already untouched.")
        sys.exit(0)

    config = {
        "progression_balancing": args.progression_balancing,
        "shuffle_progression":   "random" if args.shuffle_key_items_random else args.shuffle_progression,
        "gate_preset":           args.gate_preset,
        "max_sl":                args.max_sl,
        "shuffle_weapons":       "random" if args.shuffle_weapons_random else args.shuffle_weapons,
        "shuffle_lore":          "random" if args.shuffle_lore_random else args.shuffle_lore,
        "shuffle_bonus":         "random" if args.shuffle_light_soul_random else args.shuffle_light_soul,
        "shuffle_gad_temples":   "random" if args.shuffle_gad_temples_random else args.shuffle_gad_temples,
        "shuffle_prisms":        "random" if args.shuffle_prisms_random else args.shuffle_prisms,
        "shuffle_retractors":    "random" if args.shuffle_retractors_random else args.shuffle_retractors,
        "shuffle_accumulators":  "random" if args.shuffle_accumulators_random else args.shuffle_accumulators,
        "shuffle_eclipsers":     "random" if args.shuffle_eclipsers_random else args.shuffle_eclipsers,
        "starting_item":         args.starting_item,
        "random_starting_item":  args.random_starting_item,
        "insanity":              args.insanity or 0,
        "shuffle_enemies":       "random" if args.shuffle_enemies_random else args.shuffle_enemies,
        "enemy_mode":            args.enemy_mode,
        "enemy_mix_movement":    "random" if args.enemy_mix_movement_random else args.enemy_mix_movement,
        "enemy_uncap_counts":    "random" if args.enemy_uncap_counts_random else args.enemy_uncap_counts,
        "shuffle_true_forms":    "random" if args.shuffle_true_forms_random else args.shuffle_true_forms,
        "shuffle_ambients":      args.shuffle_ambients,
        "ambient_mode":          args.ambient_mode,
        "shuffle_music":         args.shuffle_music,
        "shuffle_voices":        args.shuffle_voices,
        "shuffle_weapons_sfx":   args.shuffle_weapons_sfx,
        "shuffle_enemies_sfx":   args.shuffle_enemies_sfx,
        "shuffle_sky":           args.shuffle_sky,
        "patch_tracker":         args.patch_tracker,
        "open_gates_n":          args.open_gates,
        "entrance_mode":         args.entrance_mode,
        "altar_cadeaux_required":      args.altar_cadeaux_required or 100,
        "fogometers_cadeaux_required": args.fogometers_cadeaux_required or 666,
        "starting_health":             args.starting_health or 5,
        "altar_health_grant":          args.altar_health_grant or 1,
        "soul_threshold_mode":         "random" if args.soul_threshold_mode_random else (args.soul_threshold_mode or "off"),
        "death_penalty":               "random" if args.death_penalty_random else args.death_penalty,
        "settings_string":             args.settings_string,
    }
    if args.config and Path(args.config).exists():
        yaml_data = yaml.safe_load(Path(args.config).read_text())
        if yaml_data:
            config.update(yaml_data.get("Shadow Man Remastered", yaml_data))

    seed = args.seed if args.seed is not None else random.randint(1000000000, 9999999999)
    run_patcher(game_dir=args.game_dir, seed=seed, config=config,
                output_dir=args.output_dir, dry_run=args.dry_run, use_kpf=True)