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
from constants import (LEVEL_FOLDERS, SOUL_RSC_FILES, ENEMY_RSC_FILES, GATE_VANILLA_SL, GATE_PRESETS,
                       CADEAU_HEIGHT_DROP, GOVI_HEIGHT_BOOST, PROGRESSION_IN_SOUL_LIFT, ITEM_Y_ADJUST,
                       SOUL_SLOT_MARKER_FX, SOUL_SLOT_MARKER_FX_Y, DAY_NIGHT_MIRRORS, GAD_PICKUP_EXPECTED_OFFSETS,
                       STARTING_ITEM_POOL, ASSET_OVERRIDES, MSH_OVERRIDES)
from enemy_randomizer import randomize_enemies, enemy_spoiler_section, randomize_true_forms, true_form_spoiler_section
from gad_pickup_patch import apply_gad_pickup_patch, apply_prison_keycard_patch

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
INSTANCE_OFF = 0x21  # unique save-game instance ID — read-only, never written
XYZ_OFF      = 0x04  # start of the three little-endian floats for world position (X, Y, Z)

_RSC_TO_FRIENDLY = {v: k for k, v in STARTING_ITEM_POOL.items()}

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
GATE_E2O_POSITIONS: dict[str, tuple[str, int, int]] = {
    "GATE_DEADSIDE_MARROW"      : ("deadside",    -836,  20326),
    "GATE_DEADSIDE_WASTELAND"   : ("deadside",     437,  23503),
    "GATE_DEADSIDE_ASYLUM"      : ("deadside",    -641,  25394),
    "GATE_DEADSIDE_PATH_3"      : ("deadside",   -2580,  26716),
    "GATE_DEADSIDE_LALUNE"      : ("deadside",   -3245,  29072),
    "GATE_DEADSIDE_CAGEWAYS"    : ("deadside",    2319,  24462),
    "GATE_DEADSIDE_PLAYROOMS"   : ("deadside",    4034,  21491),
    "GATE_DEADSIDE_PATH_6"      : ("deadside",    -989,  19729),
    "GATE_DEADSIDE_LAVADUCTS"   : ("deadside",    -509,  15790),
    "GATE_DEADSIDE_PATH_7"      : ("deadside",     305,  22806),
    "GATE_DEADSIDE_LALAME"      : ("deadside",   -1234,  11068),
    "GATE_DEADSIDE_BLOOD"       : ("deadside",   -3147,  15634),
    "GATE_DEADSIDE_FOGOMETERS"  : ("deadside",   -1746,  14396),
    "GATE_DEADSIDE_MYSTERY"     : ("deadside",   -2865,   5298),
    "GATE_WASTELAND_ENSEIGNE"   : ("wastland",    5057,   7727),
    "GATE_FIRE_POIGNE"          : ("t1tchgad",     920,   4399),
    "GATE_FIRE_FLAMBEAU"        : ("t1tchgad",    6322,   4686),
    "GATE_PROPHECY_INTERIOR"    : ("t2wlkgad",   -3940, -13135),
    "GATE_BLOOD_INTERIOR"       : ("t3swmgad",   -1899, -11809),
    "GATE_FOGOMETERS_INTERIOR"  : ("ah4fogom",  -14955,  11890),
}

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
    "RSC_X_ECLIPSER_PART1", "RSC_X_ECLIPSER_PART2", "RSC_X_ECLIPSER_PART3",
    "RSC_X_PRISON_KEY_CARD", "RSC_X_BATON", "RSC_X_FLAMBEAU", "RSC_X_MARTEAU",
    "RSC_X_CALABASH", "RSC_X_ACCUMULATOR", "RSC_X_FLASHLIGHT",
}

ABILITY_TYPES = {
    "RSC_X_GAD_PICKUP"
}

# ── Level registry ────────────────────────────────────────────────────────────

LEVEL_NAMES = {
    "swampday": "Louisiana Swampland",      "tenement": "New York Tenement",
    "prison":   "Texas Prison",             "uground":  "London Underground",
    "florida":  "Florida Summer Camp",      "salvage":  "Mojave Desert Salvage Yard",
    "swampnit": "Louisiana Swampland (Night)", "ntenemnt": "New York Tenement (Night)",
    "nprison":  "Texas Prison (Night)",     "nuground": "London Underground (Night)",
    "nflorida": "Florida Summer Camp (Night)", "nsalvage": "Mojave Desert Salvage Yard (Night)",
    "deadside": "Deadside Marrow Gates",    "wastland": "Deadside Wasteland",
    "asylum":   "Asylum Gateway",           "as2exper": "Experimentation Rooms",
    "as3schis": "Schism Chambers",          "as4dkeng": "Dark Engine",
    "t1tchgad": "Touch Gad Temple",         "t2wlkgad": "Walk Gad Temple",
    "t3swmgad": "Swim Gad Temple",          "t4ndgad":  "Unknown Area",
    "ah1cagew": "Cageways",                 "ah2playr": "Playrooms",
    "ah3lavad": "Lavaducts",                "ah4fogom": "Fogometers",
    "asyiggy":  "Asylum (Iggy)",
}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class QuestRecord:
    offset: int
    name: str
    zone: int
    instance_id: int
    x: float
    y: float
    z: float
    raw: bytes
    source_file: str = "quest.rsc"
    folder: str = ""

    @property
    def has_drop(self) -> bool:
        return self.instance_id != 0

    @property
    def category(self) -> str:
        if self.name in DARK_SOUL_TYPES:    return "soul"
        if self.name in CADEAUX_TYPES:      return "cadeaux"
        if self.name in BARREL_TYPES:       return "cadeaux" if self.has_drop else "barrel"
        if self.name in ABILITY_TYPES:      return "ability"
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
            instance_id=chunk[INSTANCE_OFF],
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
        import re
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
                instance_id=data[rec_start + INSTANCE_OFF],
                x=x, y=y, z=z,
                raw=data[rec_start:rec_start + RECORD_SIZE],
                folder=folder,
            ))
    else:
        records = fixed_records

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
            # data[rec_start + 0x1E] = template[0x1E]
            data[rec_start + 0x20] = template[0x20]

        # Then write name and instance_id as before
        new_name = p['name'].encode("ascii")
        if len(new_name) >= NAME_MAXLEN:
            new_name = new_name[:NAME_MAXLEN - 1]
        data[anchor_offset: anchor_offset + NAME_MAXLEN] = b"\x00" * NAME_MAXLEN
        data[anchor_offset: anchor_offset + len(new_name)] = new_name

        reward = p.get('reward')
        if reward is not None:
            data[anchor_offset - 1] = reward & 0xFF

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

GAD_CUTSCENE_EVENT_IDS = frozenset({0xFA00, 0xC800})
GAD_TEMPLE_LEVELS      = frozenset({"t1tchgad", "t2wlkgad", "t3swmgad"})

E2O_TRIGGER_TYPE = 0x0D00

GAD_CUTSCENE_EVT_LEVELS = frozenset({"t1tchgad", "t2wlkgad", "t3swmgad"})
EVT_HEADER_SIZE = 16
EVT_RECORD_SIZE = 58
EVT_AABB_OFF = 0x20
EVT_AABB_SIZE = 24

def _zero_gad_cutscene_evt(levels_path: Path, folder: str) -> bool:
    """
    Zero the AABB float data in all cutscene.evt records for gad temple levels.
    Structural/flag bytes are preserved so the file remains parseable.
    A zero-sized box at origin will never intersect the player.
    Returns True if file was modified.
    """
    if folder not in GAD_CUTSCENE_EVT_LEVELS:
        return False
    evt_path = levels_path / folder / "cutscene.evt"
    if not evt_path.exists():
        print(f"  WARNING: {folder}/cutscene.evt not found - skipping")
        return False
    data = bytearray(evt_path.read_bytes())
    n = (len(data) - EVT_HEADER_SIZE) // EVT_RECORD_SIZE
    for i in range(n):
        pos = EVT_HEADER_SIZE + i * EVT_RECORD_SIZE + EVT_AABB_OFF
        if pos + EVT_AABB_SIZE <= len(data):
            data[pos : pos + EVT_AABB_SIZE] = bytes(EVT_AABB_SIZE)
    evt_path.write_bytes(bytes(data))
    return True

def _zero_gad_cutscene_triggers(data: bytearray, folder: str) -> int:
    """
    Zero @0x2E on all 0x0D00 event trigger records whose value is a known
    gad cutscene or lava-death event ID.  Returns count of records zeroed.
    Safe to call unconditionally — no-ops for non-temple folders.
    """
    if folder not in GAD_TEMPLE_LEVELS:
        return 0
    n = (len(data) - E2O_RECORD_OFF) // E2O_RECORD_SIZE
    zeroed = 0
    for i in range(n):
        pos = E2O_RECORD_OFF + i * E2O_RECORD_SIZE
        if struct.unpack_from("<H", data, pos + E2O_TYPE_OFF)[0] != E2O_TRIGGER_TYPE:
            continue
        val = struct.unpack_from("<H", data, pos + E2O_SL_OFF)[0]
        if val in GAD_CUTSCENE_EVENT_IDS:
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
    import re
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

    ARC_PREFIXES = (b"RSC_X_COFFIN_GATE_ARC", b"RSC_X_COFGATE_ARC")
    total_changed = 0

    for folder in folders:
        rsc_path = levels_path / folder / "events.rsc"
        if not rsc_path.exists():
            print(f"  WARNING: {folder}/events.rsc not found - ARC decos not patched")
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
            new_name = matched_prefix + str(new_arc_num).encode("ascii")
            data[name_pos: name_pos + NAME_MAXLEN] = (
                new_name + b'\x00' * (NAME_MAXLEN - len(new_name))
            )
            changed += 1
            total_changed += 1

        if changed:
            rsc_path.write_bytes(bytes(data))
            print(f"  Gate decos: {changed} ARC record(s) renamed in {folder}/events.rsc")

    return total_changed

def _spoiler_gate_section(gate_remap: dict[str, int]) -> list[str]:
    """Format the soul gate section for the spoiler log."""
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
        old_souls = VANILLA_SL_THRESHOLDS[old_sl]
        new_souls = VANILLA_SL_THRESHOLDS[new_sl]
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

def apply_msh_overrides(randomizer_dir, work_path, kpf_index=None) -> dict:
    """Scale MSH vertex tables and return as mod_files dict for KPF packing."""
    mod_files = {}
    VERT_OFF = 0x340
    N_VERTS  = 8

    for kpf_path, scale in MSH_OVERRIDES:
        data = None
        if kpf_index:
            from kpf_handler import find_file_in_kpf, extract_file_from_kpf
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
    data = open(filepath, "rb").read()
    soul_records = [r for r in records if r.name in DARK_SOUL_TYPES]
    print(f"  Soul audit for {filepath} - {len(soul_records)} soul records:")
    for rec in soul_records:
        current = data[rec.offset:rec.offset+32].split(b'\x00')[0].decode('ascii', errors='replace')
        patch   = patches.get(rec.offset)
        planned = patch['name'] if patch else "NOT IN PATCH MAP"
        print(f"    0x{rec.offset:04X}: current={current!r} -> planned={planned!r}")


def verify_patch(filepath: str, patches: dict) -> None:
    data = open(filepath, "rb").read()
    ok = fail = 0
    for anchor_offset, p in patches.items():
        actual_name    = data[anchor_offset:anchor_offset+30].split(b'\x00')[0].decode('ascii', errors='replace')
        actual_reward  = data[anchor_offset - 1]
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

def run_assumed_fill(rng: random.Random, config: dict,
                     gate_remap: dict[str, int] | None = None) -> tuple[dict, dict[str, int]]:
    shuffle_prog = config.get("shuffle_progression", True)
    gate_preset  = config.get("gate_preset")

    # Resolve gate kwargs from preset or raw config
    if gate_preset and gate_preset in GATE_PRESETS:
        p = GATE_PRESETS[gate_preset]
        gate_kwargs = {
            "shuffle_gates": p["shuffle_gates"],
            "no_soul_gates": p["no_soul_gates"],
            "lock_gates": p["lock_gates"],
            "max_sl": config.get("max_sl") if config.get("max_sl") is not None else p["max_sl"],
            "safe": p["safe"],
            "sl_spread": p.get("sl_spread", 4),
        }
        print(f"  Gate preset  : {gate_preset}")
    else:
        gate_kwargs = {
            "shuffle_gates": config.get("shuffle_soul_gates", False),
            "no_soul_gates": False,
            "lock_gates": frozenset(),
            "max_sl": None,
            "safe": True,
            "sl_spread": 4,
        }

    if not shuffle_prog:
        if gate_kwargs["shuffle_gates"]:
            try:
                from fill import _shuffle_gates
                gate_remap = _shuffle_gates(
                    rng,
                    locked=gate_kwargs["lock_gates"],
                    max_sl=gate_kwargs["max_sl"],
                    safe=gate_kwargs["safe"],
                    sl_spread=gate_kwargs["sl_spread"],
                )
                changed = sum(1 for g, sl in gate_remap.items()
                              if sl != GATE_VANILLA_SL.get(g))
                print(f"  Assumed fill : skipped (--no-progression)")
                print(f"  Gate shuffle : {changed} gate(s) changed")
            except ImportError:
                print("  WARNING: fill.py not found - gate shuffle skipped")
                gate_remap = {g: GATE_VANILLA_SL[g] for g in GATE_VANILLA_SL}
        else:
            print("  Assumed fill : skipped (--no-progression)")
            gate_remap = {g: GATE_VANILLA_SL[g] for g in GATE_VANILLA_SL}
        return {}, gate_remap

    try:
        from fill import assumed_fill
    except ImportError:
        print("  WARNING: fill.py not found - progression items will not be shuffled")
        return {}, {g: GATE_VANILLA_SL[g] for g in GATE_VANILLA_SL}

    placement, gate_remap = assumed_fill(
        rng,
        progression_balancing=config.get("progression_balancing", 50),
        shuffle_gad_temples=config.get("shuffle_gad_temples", False),
        insanity=int(config.get("insanity", 0)),
        shuffle_weapons=config.get("shuffle_weapons", True),
        shuffle_lore=config.get("shuffle_lore", True),
        shuffle_bonus=config.get("shuffle_bonus", False),
        starting_item=config.get("starting_item"),
        **gate_kwargs,
    )

    changed = sum(1 for g, sl in gate_remap.items()
                  if sl != GATE_VANILLA_SL.get(g))
    print(f"  Assumed fill : {len(placement)} RSC placements  "
          f"[gates: {changed} changed]")

    if gate_preset == "chaos":
        formerly_locked = {"GATE_DEADSIDE_MARROW", "GATE_DEADSIDE_MYSTERY", "GATE_FOGOMETERS_INTERIOR"}
        shuffled_locked = [g for g in formerly_locked
                           if gate_remap.get(g) != GATE_VANILLA_SL.get(g)]
        if shuffled_locked:
            print(f"  ⚠️  CHAOS: {len(shuffled_locked)} formerly-locked gate(s) shuffled — "
                  f"expect unusual progression")

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

    def make_patch(rec, new_name, instance_id):
        new_tall     = new_name in DARK_SOUL_TYPES
        old_tall     = rec.name in DARK_SOUL_TYPES
        old_soul_slot = rec.name in DARK_SOUL_TYPES or rec.name in CADEAUX_TYPES
        new_is_key   = (new_name not in DARK_SOUL_TYPES
                        and new_name not in CADEAUX_TYPES
                        and new_name not in BARREL_TYPES)
        if new_tall and not old_tall:
            y_adj = GOVI_HEIGHT_BOOST          # govi into non-govi slot → raise
        elif new_is_key and old_soul_slot:
            y_adj = PROGRESSION_IN_SOUL_LIFT   # key/weapon/lore into soul or cadeaux slot → raise
        elif not new_tall and old_tall:
            y_adj = CADEAU_HEIGHT_DROP         # small soul item replacing a govi → drop
        else:
            y_adj = 0.0
        y_adj += ITEM_Y_ADJUST.get(new_name, 0.0)
        return {"name": new_name, "reward": instance_id,
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

        instance_id = source_loc.instance_id
        old_soul_slot = rec.name in DARK_SOUL_TYPES or rec.name in CADEAUX_TYPES
        new_is_key    = (rsc_name not in DARK_SOUL_TYPES
                         and rsc_name not in CADEAUX_TYPES
                         and rsc_name not in BARREL_TYPES)
        if new_is_key and old_soul_slot:
            marker_sites.append((rec.folder, rec.source_file, rec.x, rec.y + SOUL_SLOT_MARKER_FX_Y, rec.z, rec.zone))
        k = (rec.folder, rec.source_file)
        patches_by_folder.setdefault(k, {})[rec.offset] = \
            make_patch(rec, rsc_name, instance_id if instance_id is not None else rec.instance_id)
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
    from rsc_utils import inject_rsc_record, build_rsc_record, HEADER_SIZE, RECORD_SIZE, NAME_OFF

    raw = rsc_path.read_bytes()
    n_full = (len(raw) - HEADER_SIZE) // RECORD_SIZE
    trailer = raw[HEADER_SIZE + n_full * RECORD_SIZE:]
    data = bytearray(raw[:HEADER_SIZE + n_full * RECORD_SIZE])

    record = build_rsc_record(rsc_name, x, y, z, zone)
    allow_expand = rsc_path.name == "quest.rsc"

    slot = inject_rsc_record(data, record, allow_expand=allow_expand)

    if slot is None:
        if rsc_path.name != "quest.rsc":
            quest_path = rsc_path.parent / "quest.rsc"
            if quest_path.exists():
                print(f"  {rsc_path.parent.name}/{rsc_path.name} no space — falling back to quest.rsc")
                return _inject_one_fx_record(quest_path, rsc_name, x, y, z, zone)
        print(f"  WARNING: no space in {rsc_path.parent.name}/{rsc_path.name} — marker skipped")
        return False

    rsc_path.write_bytes(bytes(data) + trailer)
    print(f"  {rsc_path.parent.name}/{rsc_path.name}  +1 {rsc_name} slot {slot} (zone {zone})")
    return True


def inject_special_item_fx(marker_sites: list, levels_path) -> int:
    total = 0
    for folder, source_file, x, y, z, zone in marker_sites:
        rsc_path = Path(levels_path) / folder / source_file
        if not rsc_path.exists():
            print(f"  WARNING: marker skipped — {folder}/{source_file} not found")
            continue
        if _inject_one_fx_record(rsc_path, SOUL_SLOT_MARKER_FX, x, y, z, zone):
            total += 1
        mirror = DAY_NIGHT_MIRRORS.get(folder)
        if mirror:
            mirror_path = Path(levels_path) / mirror / source_file
            if mirror_path.exists():
                if _inject_one_fx_record(mirror_path, SOUL_SLOT_MARKER_FX, x, y, z, zone):
                    total += 1
    return total

# ── Spoiler log ───────────────────────────────────────────────────────────────

def write_spoiler_log(output_path, seed, patches_by_folder, gate_remap,
                      records_by_folder, config, spheres=None) -> None:

    starting_rsc = config.get('starting_item', None)
    starting_friendly = _RSC_TO_FRIENDLY.get(starting_rsc, starting_rsc) if starting_rsc else 'none'

    lines = [
        "=" * 60,
        "SHADOW MAN REMASTERED - RANDOMIZER SPOILER LOG",
        "=" * 60,
        f"Seed: {seed}",
        f"Progression balancing: {config.get('progression_balancing', 50)}/100",
        f"Randomize key items: {config.get('shuffle_progression', True)}",
        f"Starting item: {starting_friendly}",
        f"Gate preset: {config.get('gate_preset', 'none')}",
        f"Shuffle gad temples: {config.get('shuffle_gad_temples', False)}",
        f"Shuffle weapons: {config.get('shuffle_weapons', True)}",
        f"Shuffle lore: {config.get('shuffle_lore', True)}",
        f"Shuffle bonus: {config.get('shuffle_bonus', False)}",
        f"Shuffle enemies: {config.get('shuffle_enemies', False)}",
        f"Enemy mode: {config.get('enemy_mode', 'difficulty')}",
        f"Shuffle music: {config.get('shuffle_music', False)}",
        "",
    ]

    lines += _spoiler_gate_section(gate_remap)

    if spheres:
        lines.append("── PLAYTHROUGH ─────────────────────────────────────────────────────")
        lines.append("")
        for s in spheres:
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
    print(f"Spoiler log written to: {output_path}")


# ── KPF repack ────────────────────────────────────────────────────────────────

def repack_after_patch(game_dir, patches_by_folder, gate_remap, config,
                       spoiler_path, work_dir, extra_mod_files=None):
    try:
        from kpf_handler import (find_kpf_files, build_kpf_index,
                                  find_file_in_kpf, build_and_install_mod)
    except ImportError:
        print("\nkpf_handler.py not found - skipping mod KPF creation")
        return

    kpf_files = find_kpf_files(game_dir)
    if not kpf_files:
        print("\nNo KPF files found - cannot determine internal paths")
        return

    print("\nBuilding randomizer mod KPF...")
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

        # Include patched events.rsc files for all levels with gate ARC decos
        for folder in ("deadside", "t1tchgad", "t2wlkgad", "t3swmgad", "wastland"):
            local = Path(work_dir) / "levels" / folder / "events.rsc"
            if local.exists():
                matches  = find_file_in_kpf(kpf_index, f"*/{folder}/events.rsc")
                internal = matches[0][0] if matches else f"levels/{folder}/events.rsc"
                mod_files[internal] = str(local)
            else:
                print(f"  [DBG events] {folder}: local file NOT FOUND at {local}")

    if config.get("shuffle_gad_temples", False):
        for folder in GAD_TEMPLE_LEVELS:
            for filename in ("links.e2o", "cutscene.evt"):
                local = Path(work_dir) / "levels" / folder / filename
                if local.exists():
                    matches = find_file_in_kpf(kpf_index, f"*/{folder}/{filename}")
                    internal = matches[0][0] if matches else f"levels/{folder}/{filename}"
                    mod_files[internal] = str(local)

    if extra_mod_files:
        mod_files.update(extra_mod_files)

    if not mod_files:
        print("  Nothing to pack into mod KPF")
        return
    build_and_install_mod(game_dir, mod_files)


# ── Validation ────────────────────────────────────────────────────────────────

def validate_final_seed(work_dir: str, progression_placement: dict = None, patches_by_folder: dict = None) -> None:
    print("\n── Final seed validation ──────────────────────────")
    levels_path = Path(work_dir) / "levels"
    error_count = 0

    # ── 1. File record integrity ──────────────────────────────────────────────
    for folder in LEVEL_FOLDERS:
        for source_file in SOUL_RSC_FILES:
            rsc_file = levels_path / folder / source_file
            if not rsc_file.exists():
                continue
            try:
                records = parse_rsc_file(str(rsc_file), folder)
            except ValueError:
                continue
            for r in records:
                if r.name in DARK_SOUL_TYPES and r.instance_id == 0:
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
        missing = [
            loc_key for loc_key in progression_placement
            if loc_key not in patched_keys
        ]
        if missing:
            print(f"  [!] {len(missing)} placements not written to files:")
            for lk in missing[:10]:
                print(f"      {lk}")
            if len(missing) > 10:
                print(f"      ... and {len(missing) - 10} more")
            error_count += len(missing)

    if error_count == 0:
        print(f"  ✅ Patch validation passed.")
    else:
        print(f"  ❌ {error_count} patching error(s) found.")

def run_patcher(game_dir, seed, config, output_dir=None, dry_run=False, use_kpf=True):
    from kpf_handler import (find_kpf_files, build_kpf_index,
                              extract_game_files, which_kpf_has_levels)

    rng       = random.Random(seed)
    game_path = Path(game_dir)

    # ── KPF extraction (now uses updated KPFs) ────────────────────────────────

    kpf_files = find_kpf_files(game_dir) if use_kpf else []
    using_kpf = bool(kpf_files)

    if using_kpf:
        work_path = game_path / f"_randomizer_work_{seed}"
        work_path.mkdir(exist_ok=True)
        print(f"Shadow Man Remastered Randomizer")
        print(f"Seed: {seed}  |  Mode: KPF repack  |  Found {len(kpf_files)} KPF archives")
        print()
        print("Extracting game files from KPFs...")
        kpf_index = extract_game_files(kpf_files, str(work_path), LEVEL_FOLDERS)
        # Don't pass game_dir — we always want to extract from vanilla base KPFs,
        # not the previously installed randomizer mod.
        # kpf_index = extract_game_files(kpf_files, str(work_path), LEVEL_FOLDERS, game_dir=str(game_path))
        levels_kpf = which_kpf_has_levels(kpf_index)
        print(f"  Core data KPF: {levels_kpf}")
        levels_path = work_path / "levels"
    else:
        work_path   = game_path
        levels_path = game_path / "levels"
        kpf_index   = None
        print(f"Shadow Man Remastered Randomizer")
        print(f"Seed: {seed}  |  Mode: Direct file edit  |  Game dir: {game_dir}")

    # ── Pre-step: always inject gad records so they appear in parsed data ─────
    from setup_gad_records import GAD_INJECTION_SITES, inject_record, _find_existing
    if config.get("shuffle_gad_temples", False):
        print("\nInjecting RSC_X_GAD_PICKUP records...")
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
    print("Parsing RSC files...")
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
        print(f"  {folder:<12}: {souls} souls  {weps} weapons  {prog} keys  {lore} lore"
              f"  [{', '.join(files_found)}]")

    # Object map CSV
    object_map = [
        {"folder": folder, "source_file": rec.source_file,
         "offset": f"0x{rec.offset:04X}", "name": rec.name, "category": rec.category,
         "instance_id": rec.instance_id, "has_drop": rec.has_drop, "zone": rec.zone,
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
        print(f"Object map: {map_path}")

    # ── Step 2: Run assumed fill (includes gate shuffle) ─────────────────────
    print("\nRunning assumed fill...")
    progression_placement, gate_remap = run_assumed_fill(rng, config)

    # Compute true form remap now (needs gate_remap) so simulate_playthrough
    # uses correct fixed soul positions for sphere log
    true_form_loc_remap = None
    tf_patches_early = {}
    if config.get("shuffle_true_forms", False):
        tf_patches_early, true_form_loc_remap = randomize_true_forms(rng, gate_remap)

    from fill import (simulate_playthrough, CHECKABLE_LOCS, FIXED_SOUL_LOCS,
                      build_gate_rules, apply_true_form_remap, STARTING_ITEMS)
    if config.get("starting_item"):
        STARTING_ITEMS.add(config["starting_item"])
    active_fixed_soul_locs = apply_true_form_remap(true_form_loc_remap)
    level_rules = build_gate_rules(gate_remap)
    _, _, spheres = simulate_playthrough(
        progression_placement,
        CHECKABLE_LOCS + active_fixed_soul_locs,
        level_rules,
        collect_spheres=True,
        shuffle_gad_temples=config.get("shuffle_gad_temples", False),
    )

    if config.get("starting_item"):
        STARTING_ITEMS.discard(config["starting_item"])

    # ── Step 3: Write gate SL values to links.e2o ────────────────────────────
    gates_changed = any(
        gate_remap.get(g) != GATE_VANILLA_SL.get(g)
        for g in GATE_VANILLA_SL
    )
    if gates_changed:
        print("\nWriting soul gate requirements to links.e2o...")
        randomize_gate_sl_links(gate_remap, levels_path=levels_path)

        if not (levels_path / "deadside" / "events.rsc").exists() and using_kpf:
            try:
                from kpf_handler import find_file_in_kpf, extract_file_from_kpf
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

    # ── Step 3b: Suppress gad temple cutscenes ────────────────────────────────
    if config.get("shuffle_gad_temples", False):
        print("\nSuppressing gad temple cutscene triggers...")

        # Ensure cutscene.evt is extracted for each temple level
        if using_kpf:
            try:
                from kpf_handler import find_file_in_kpf, extract_file_from_kpf
                for folder in GAD_TEMPLE_LEVELS:
                    evt_path = levels_path / folder / "cutscene.evt"
                    if not evt_path.exists():
                        matches = find_file_in_kpf(kpf_index, f"*/{folder}/cutscene.evt")
                        if matches:
                            evt_path.parent.mkdir(parents=True, exist_ok=True)
                            extract_file_from_kpf(
                                str(Path(kpf_index.kpf_dir) / matches[0][1]),
                                matches[0][0],
                                str(evt_path),
                            )
            except Exception as e:
                print(f"  WARNING: cutscene.evt extraction failed: {e}")

        for folder in GAD_TEMPLE_LEVELS:
            e2o_path = levels_path / folder / "links.e2o"
            if e2o_path.exists():
                buf = bytearray(e2o_path.read_bytes())
                n = _zero_gad_cutscene_triggers(buf, folder)
                if n:
                    e2o_path.write_bytes(bytes(buf))
                    print(f"  [{folder}] Zeroed {n} cutscene trigger(s) in links.e2o")
            if _zero_gad_cutscene_evt(levels_path, folder):
                print(f"  [{folder}] Zeroed cutscene.evt")

    # ── Step 4: RSC item patching ─────────────────────────────────────────────
    print("\nPatching RSC items...")
    patches_by_folder, marker_sites = write_placement_patches(
        records_by_folder,
        progression_placement=progression_placement,
        shuffle_gad_temples=config.get("shuffle_gad_temples", False),
    )
    print(f"  {sum(len(p) for p in patches_by_folder.values())} RSC patches generated")

    # ── Step 4b: Starting item patch ─────────────────────────────────────────────
    starting_item_rsc = config.get("starting_item")
    if starting_item_rsc:
        swamp_instance = levels_path / "swampday" / "instance.rsc"
        if swamp_instance.exists():
            instance_id = None
            template = None
            for folder, records in records_by_folder.items():
                for rec in records:
                    if rec.name == starting_item_rsc:
                        instance_id = rec.instance_id
                        template = rec.raw
                        break
                if instance_id is not None:
                    break
            patch_rsc_file(str(swamp_instance), {
                0x17CA: {
                    "name": starting_item_rsc,
                    "reward": instance_id or 0,
                    "y_adjust": 0.0,
                    "source_file": "instance.rsc",
                }
            }, record_templates={starting_item_rsc: template} if template else None)
            print(f"  Starting item: {starting_item_rsc} placed at swampday church")
        else:
            print(f"  WARNING: swampday/instance.rsc not found — starting item not placed")

    # ── Step 5: Spoiler log ───────────────────────────────────────────────────
    spoiler_path = out_path / f"spoiler_seed_{seed}.txt"
    write_spoiler_log(
        str(spoiler_path), seed, patches_by_folder,
        gate_remap, records_by_folder, config,
        spheres=spheres,
    )

    if dry_run:
        print("\nDry run complete - no files modified")
        return

    # ── Build record templates from parsed data ───────────────────────────────
    record_templates: dict[str, bytes] = {}
    for folder, records in records_by_folder.items():
        for rec in records:
            if rec.name not in record_templates:
                record_templates[rec.name] = rec.raw


    # ── Step 6: Apply RSC patches ─────────────────────────────────────────────
    print("\nApplying RSC patches...")

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
        print("\nShuffling enemies...")

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

    # ── Step 6b.5: Inject special item FX for insanity placements ────────────
    if marker_sites:
        # print("\nSKIPPING Injecting special item FX markers for key items in soul/cadeaux slots...")
        print("\nInjecting special item FX markers for key items in soul/cadeaux slots...")
        n_markers = inject_special_item_fx(marker_sites, levels_path)
        print(f"  {n_markers} {SOUL_SLOT_MARKER_FX} record(s) injected")

    # ── Step 6c: Append enemy/true form sections to spoiler log ──────────────
    if enemy_patches or true_form_loc_remap:
        with open(str(spoiler_path), "a", encoding="utf-8") as f:
            if enemy_patches:
                f.write("\n" + "\n".join(enemy_spoiler_section(enemy_patches)))
            if true_form_loc_remap:
                f.write("\n" + "\n".join(true_form_spoiler_section(true_form_loc_remap)))

    # ── Step 7: EXE patches ───────────────────────────────────────────────────────
    exe_src = list(game_path.glob("thoth_x64.exe"))
    if exe_src:
        src = exe_src[0]
        patched = src.parent / "thoth_x64_patched.exe"
        shutil.copy2(str(src), str(patched))

        # Always: prison key card render fix
        apply_prison_keycard_patch(str(patched), dry_run=dry_run)

        # Conditional: gad pickup dispatch patch + temple NOPs
        if config.get("shuffle_gad_temples", False):
            apply_gad_pickup_patch(
                str(patched),
                shuffle_temples=True,
                dry_run=dry_run,
            )
            print(f"\nEXE: patches written to {patched.name}")
        else:
            print(f"\nEXE: prison key card fix written to {patched.name}")
    else:
        print("\nWARNING: thoth_x64.exe not found - EXE patches skipped")

    # ── Step 8: Gate remap JSON ───────────────────────────────────────────────
    threshold_json = out_path / "soul_thresholds.json"
    with open(threshold_json, "w") as f:
        json.dump({
            "seed": seed,
            "vanilla_sl_thresholds": VANILLA_SL_THRESHOLDS,
            "gate_remap": {g: sl for g, sl in gate_remap.items()},
            "effective_thresholds": {
                gate_id: VANILLA_SL_THRESHOLDS[gate_remap.get(gate_id, GATE_VANILLA_SL[gate_id])]
                for gate_id in GATE_VANILLA_SL
            },
        }, f, indent=2)
    print(f"\nSoul thresholds: {threshold_json}")

    # ── Step 9: ARC deco patch + KPF repack ──────────────────────────────────
    if gates_changed and using_kpf:
        try:
            from kpf_handler import find_file_in_kpf, extract_file_from_kpf
            for folder in ("deadside", "t1tchgad", "t2wlkgad", "t3swmgad", "wastland"):
                events_path = levels_path / folder / "events.rsc"
                if not events_path.exists():
                    matches = find_file_in_kpf(kpf_index, f"*/{folder}/events.rsc")
                    if matches:
                        events_path.parent.mkdir(parents=True, exist_ok=True)
                        extract_file_from_kpf(
                            str(Path(kpf_index.kpf_dir) / matches[0][1]),
                            matches[0][0],
                            str(events_path),
                        )
        except Exception as e:
            print(f"  WARNING: events.rsc extraction failed: {e}")

    if gates_changed:
        n_arc = patch_gate_arc_decos(levels_path, gate_remap)
        if n_arc == 0:
            print("  Gate decos: no changes from vanilla")

    # ── Step 9.5: SFX + music shuffle ────────────────────────────────────────
    music_files = {}
    sfx_files = {}

    if config.get("shuffle_music", False) and using_kpf:
        from music_randomizer import shuffle_music
        music_files = shuffle_music(rng, kpf_files, str(work_path), dry_run=dry_run)

    if (config.get("shuffle_voices", False) or config.get("shuffle_weapons_sfx", False)) \
            and using_kpf:
        from sfx_randomizer import shuffle_sfx
        sfx_files = shuffle_sfx(
            rng, kpf_files, str(work_path),
            shuffle_voices=config.get("shuffle_voices", False),
            shuffle_weapons=config.get("shuffle_weapons_sfx", False),
            dry_run=dry_run,
        )

    if sfx_files:
        from sfx_randomizer import sfx_spoiler_section
        sfx_lines = sfx_spoiler_section(sfx_files, str(work_path))
        with open(str(spoiler_path), "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(sfx_lines))

    # ── Step 9.7: Build asset override mod files ──────────────────────────────
    randomizer_dir = Path(__file__).resolve().parent
    asset_mod_files = {}
    for src_rel, dst_rel in ASSET_OVERRIDES:
        src = randomizer_dir / src_rel
        if not src.exists():
            print(f"  WARNING: asset override missing — {src_rel}")
            continue
        # dst_rel is the in-KPF path — normalize to forward slashes
        internal = dst_rel.replace("\\", "/")
        asset_mod_files[internal] = str(src)
        print(f"  [asset] {src_rel} → {internal}")

    msh_mod_files = apply_msh_overrides(randomizer_dir, work_path, kpf_index=kpf_index)

    # ── Step 10: Repack the KPF ───────────────────────────────────────────────
    if using_kpf:
        repack_after_patch(
            str(game_path), patches_by_folder, gate_remap,
            config, str(spoiler_path), str(work_path),
            extra_mod_files={**music_files, **sfx_files, **asset_mod_files, **msh_mod_files},
        )

    validate_final_seed(str(work_path), progression_placement, patches_by_folder)
    print(f"\nDone! Seed {seed} applied.")
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
    parser.add_argument("--progression-balancing", type=int, default=50)
    parser.add_argument("--no-progression",        action="store_true")
    parser.add_argument("--gate-preset",
                        choices=["open", "easy", "medium", "hard", "chaos"],
                        default=None,
                        help="Gate difficulty preset: open=all gates free, easy=light shuffle SL5 cap, "
                             "medium=standard shuffle SL8 cap, hard=full shuffle, chaos=fully unconstrained")
    parser.add_argument("--max-sl", type=int, default=None,
                        help="Cap the maximum SL any shuffled gate can receive (1-10)")
    parser.add_argument("--no-weapons",            action="store_true")
    parser.add_argument("--no-lore",               action="store_true")
    parser.add_argument("--shuffle-bonus", action="store_true",
                        help="Include bonus items (Light Soul) in the shuffle pool")
    parser.add_argument("--shuffle-gad-temples", action="store_true",
                        help="Shuffle gad powers as physical pickups (requires EXE patch)")
    parser.add_argument("--starting-item", default=None,
                        help="RSC name of item to place at swamp church (e.g. RSC_X_ENGINEERS_KEY)")
    parser.add_argument("--insanity", nargs="?", const=3, type=int, default=0,
                        help="Insanity tier 1-3: 1=soul/govi slots, 2=+cadeaux slots, 3=all slots. Bare --insanity = tier 3.")
    parser.add_argument("--shuffle-enemies", action="store_true",
                        help="Randomize enemy types in each level")
    parser.add_argument("--enemy-mode", choices=["difficulty", "full", "contextual"],
                        default="difficulty",
                        help="difficulty: depth-weighted placement by enemy tier (default). "
                             "full: purely random within movement type. "
                             "contextual: shuffle within context_group pools.")
    parser.add_argument("--shuffle-true-forms", action="store_true",
                        help="Shuffle true form enemy positions with regular enemies")
    parser.add_argument("--shuffle-music", action="store_true",
                        help="Shuffle music tracks globally across all levels")
    parser.add_argument("--shuffle-voices", action="store_true",
                        help="Shuffle Shadow Man generic voice lines")
    parser.add_argument("--shuffle-weapons-sfx", action="store_true",
                        help="Shuffle weapon fire/reload sounds within each category")
    args = parser.parse_args()

    if args.restore:
        try:
            from kpf_handler import find_mods_dir, remove_mod_kpf
            mods_dir = find_mods_dir(args.game_dir)
            if remove_mod_kpf(mods_dir):
                print("Vanilla restored — randomizer mod removed.")
            else:
                print("No randomizer mod found — already vanilla.")
        except ImportError:
            print("kpf_handler.py not found")
        exit(0)

    config = {
        "progression_balancing": args.progression_balancing,
        "shuffle_progression":   not args.no_progression,
        "gate_preset":           args.gate_preset,
        "max_sl":                args.max_sl,
        "shuffle_weapons":       not args.no_weapons,
        "shuffle_lore":          not args.no_lore,
        "shuffle_bonus":         args.shuffle_bonus,
        "shuffle_gad_temples":   args.shuffle_gad_temples,
        "starting_item":         args.starting_item,
        "insanity":              args.insanity or 0,
        "shuffle_enemies":       args.shuffle_enemies,
        "enemy_mode":            args.enemy_mode,
        "shuffle_true_forms":    args.shuffle_true_forms,
        "shuffle_music":         args.shuffle_music,
        "shuffle_voices":        args.shuffle_voices,
        "shuffle_weapons_sfx":   args.shuffle_weapons_sfx,
    }
    if args.config and Path(args.config).exists():
        yaml_data = yaml.safe_load(Path(args.config).read_text())
        if yaml_data:
            config.update(yaml_data.get("Shadow Man Remastered", yaml_data))

    seed = args.seed if args.seed is not None else random.randint(0, 99999999)
    run_patcher(game_dir=args.game_dir, seed=seed, config=config,
                output_dir=args.output_dir, dry_run=args.dry_run, use_kpf=True)
