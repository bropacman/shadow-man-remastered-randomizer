"""
Shadow Man Remastered — AP Patcher (ported copy, 2026-07-20)
==============================================================
This is a faithful port of worlds/shadowman/patcher.py from the Archipelago
world (same repo family, different codebase) — kept in sync manually, same
pattern already used for save_path_patch.py/cadeaux_patch.py/health_patch.py/
etc. between these two repos. Only the imports were changed (this repo's
flat/randomizers/patchers layout instead of the AP world's relative-import
package layout) — the logic is untouched.

Receives pre-computed item placement (originally: from Archipelago's fill
algorithm) and writes the result to game files. Unlike the rest of THIS
repo's own patcher.py, this module never runs its own fill — it's driven
entirely by a placement dict decided elsewhere. In the AP world that's
generate_early()/generate_output(); here, that "elsewhere" is
apply_ap_seed.py, which reconstructs progression_placement/gate_remap/
config/seed from a *.apshadowman JSON file (written by the AP world's
generate_output() — see that method's docstring for why the actual
patching got moved out of AP's own generation step and into this separate
local tool).

Entry point:
    run_patcher(
        game_dir              = str,
        seed                  = int,
        config                = dict,
        output_dir            = str,
        progression_placement = dict[loc_key, RawLocation-like],
        gate_remap            = dict[gate_id, sl],
    )

progression_placement values only ever need two attributes read off them —
.object (RSC name to write) and .save_idx (save-game reward ID) — confirmed
by grepping every source_loc.* access in this file. apply_ap_seed.py
reconstructs them as a tiny local namedtuple with just those two fields
rather than the AP world's full RawLocation (which carries CSV columns this
module never touches).
"""

from __future__ import annotations

import csv
import json
import shutil
import struct
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from constants import (
    LEVEL_FOLDERS, SOUL_RSC_FILES, ENEMY_RSC_FILES,
    GATE_VANILLA_SL, CADEAU_HEIGHT_DROP, GOVI_HEIGHT_BOOST, ITEM_Y_ADJUST,
    ASSET_OVERRIDES, GAD_ASSET_OVERRIDES, AP_ASSET_OVERRIDES, MSH_OVERRIDES,
    PROGRESSION_IN_GOVI_LIFT, DARK_SOUL_SLOT_ITEM_DROP,
    PROGRESSION_IN_CADEAUX_LIFT, PROGRESSION_IN_BARREL_LIFT,
    SOUL_SLOT_MARKER_FX, SOUL_SLOT_MARKER_FX_Y, DARK_SOUL_SLOT_MARKER_FX_Y,
    BARREL_SLOT_MARKER_FX, BARREL_SLOT_MARKER_FX_Y, DAY_NIGHT_MIRRORS,
    BARREL_RSC_SUBSTITUTIONS,
)
from rsc_utils import build_rsc_record, inject_rsc_record
from fill import UNVERIFIED_LOCS, CHECKABLE_LOCS
from randomizers.entrance_randomizer import (
    UNIFIED_TRANSITIONS, UnifiedShuffle, apply_unified_shuffle, unified_spoiler_section,
)
from dark_engine_patch import (
    randomize_dark_engine, apply_dark_engine_patch, extract_and_patch_journal,
    JOURNAL_MUP_PATH, PISTON_NAMES, VANILLA_TABLE as DARK_ENGINE_VANILLA_TABLE,
)

# ── Entrance randomizer (2026-07-21, Task 19) ────────────────────────────────
# AP's ShadowManWorld currently only implements EntranceMode.deadside_only
# (see options.py's EntranceMode docstring — cross_hub, which also mixes in
# the 5 Dark Engine soul gates, is not implemented on the AP side yet). This
# repo's randomizers/entrance_randomizer.py models both modes via one
# UNIFIED_TRANSITIONS table of 14 hub-portal <-> spoke pairs; restrict to the
# 9 Deadside entries here so ap_patcher.py only ever touches the files an AP
# seed can actually have shuffled. If AP ever gains cross_hub support, widen
# this back to UNIFIED_TRANSITIONS and thread the extra DKE constraint data
# through instead of filtering it out.
DEADSIDE_UNIFIED_TRANSITIONS = [t for t in UNIFIED_TRANSITIONS if t.spoke_folder != "as4dkeng"]

# ── Constants ─────────────────────────────────────────────────────────────────

HEADER_SIZE  = 8     # "Erscv002" file magic, sits before first record
RECORD_SIZE  = 72    # every record is exactly 72 bytes, no exceptions
NAME_OFF     = 0x22  # byte offset within a record where the RSC_ string begins
NAME_MAXLEN  = 30    # max bytes available for the name before the next field
ZONE_OFF     = 0x11  # zone/cluster group this record belongs to — read-only, never written
# BUG (fixed): INSTANCE_OFF = 0x21 was treated as a standalone 1-byte "instance
# ID" field, both for reads (parse_rsc_file) and — more seriously — for writes
# (patch_rsc_file used to do `data[anchor_offset - 1] = reward & 0xFF`, i.e.
# it silently truncated and wrote a corrupted save-tracking ID into shipped
# seeds for any item whose real SaveIdx was >= 256). 0x21 is actually just the
# LAST byte of a 4-byte big-endian SaveIdx field that starts at 0x1E — see
# TECHNICAL.md §10.4 in the standalone repo and its patcher.py's SAVE_IDX_OFF.
# Kept as a comment for anyone grepping history; use SAVE_IDX_OFF below.
SAVE_IDX_OFF = 0x1E  # save-game SaveIdx — 4-byte big-endian; the real field.
XYZ_OFF      = 0x04  # start of the three little-endian floats for world position (X, Y, Z)
# Rotation fields (TECHNICAL.md §10.2, standalone repo) — object orientation,
# in integer degrees, big-endian despite the record otherwise being
# little-endian (same exception pattern as SAVE_IDX_OFF). Axis identity is
# reverse-engineered inference ("likely pitch/roll" / "likely yaw" per that
# doc), not a confirmed engine spec, but the offsets/sizes/endianness
# themselves are established from binary observation. Never read or written
# by patch_rsc_file() before 2026-07-25 — retyping a slot always inherited
# whatever rotation the ORIGINAL occupant had baked in.
ROTATION_A_OFF = 0x14  # 2-byte big-endian signed int16
ROTATION_B_OFF = 0x16  # 4-byte big-endian signed int32

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

# ── Gate E2O constants ────────────────────────────────────────────────────────

E2O_HEADER        = b"Ee2ov004"
E2O_RECORD_SIZE   = 74
E2O_RECORD_OFF    = 0x10
E2O_TYPE_OFF      = 0x2A
E2O_SL_OFF        = 0x2E
E2O_GATE_TYPE     = 0x0C00
E2O_SL_SCALE      = 2560
E2O_MATCH_RADIUS  = 500
E2O_X_OFF         = 0x06
E2O_Z_OFF         = 0x0E

# XZ positions for matching gate_id -> links.e2o 0x0C00 record
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

# ── Object type sets ──────────────────────────────────────────────────────────

DARK_SOUL_TYPES  = {"RSC_X_GOVI", "RSC_X_DARK_SOUL"}
CADEAUX_TYPES    = {"RSC_CADEAUX", "RSC_X_CADEAUX", "RSC_PICKUP_CADEAUX"}
BARREL_TYPES     = {
    "RSC_X_BARREL_D", "RSC_X_BARREL_L", "RSC_X_BARREL_A",
    "RSC_X_BARREL",
    "RSC_EXPLOSIVE_BARREL",
    "RSC_FL_CRATE",
    "RSC_UN_CRATES",
    "RSC_TE_PACKBOX1",
    "RSC_TE_PACKBOX2",
}

# ── Level name display map ────────────────────────────────────────────────────

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

# ── Gate ARC deco matching ────────────────────────────────────────────────────

ARC_PREFIXES = (b"RSC_X_COFFIN_GATE_ARC", b"RSC_X_COFGATE_ARC")
GATE_MATCH_RADIUS = 600.0

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


# ── Data structure ────────────────────────────────────────────────────────────

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
    def loc_key(self) -> str:
        return f"{self.folder}:{self.source_file}:0x{self.offset:04X}"


# ── RSC parsing & patching ────────────────────────────────────────────────────

def parse_rsc_file(filepath: str, folder: str = "") -> list:
    data = open(filepath, "rb").read()
    header = data[:8]
    if header not in (b"Erscv002", b"Erscv001"):
        raise ValueError(f"Unknown RSC header {header!r}: {filepath}")

    records = []
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
            instance_id=struct.unpack_from(">I", chunk, SAVE_IDX_OFF)[0],
            x=x, y=y, z=z,
            raw=chunk,
            folder=folder,
        ))

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
                instance_id=struct.unpack_from(">I", data, rec_start + SAVE_IDX_OFF)[0],
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
            # BUG FIX (2026-07-25, ported from patcher.py — cross-repo drift,
            # see CLAUDE.md's "Cross-Repo Drift & Port Review"): copy
            # track_type (2-byte big-endian at 0x1C) from the template for
            # the new RSC type. Essential when a slot's type changes — e.g.
            # a Dark Soul/govi placed into a slot that wasn't originally a
            # soul object keeps its original track_type (governing which
            # engine behavior/animation the object runs) unless overwritten
            # from the template. The old write here — data[rec_start + 0x20]
            # = template[0x20] — was a no-op: 0x20 falls inside the save_idx
            # field (0x1E-0x21) and gets immediately overwritten by the
            # struct.pack_into reward write below, so track_type was never
            # actually being copied for any AP-patched slot. Reported
            # symptom: a placed Dark Soul rendering as a flat static shape
            # instead of its normal floating-worm animation — consistent
            # with the object still running whatever behavior its track_type
            # said it was before retyping.
            data[rec_start + 0x1C] = template[0x1C]
            data[rec_start + 0x1D] = template[0x1D]

        new_name = p['name'].encode("ascii")
        if len(new_name) >= NAME_MAXLEN:
            new_name = new_name[:NAME_MAXLEN - 1]
        data[anchor_offset: anchor_offset + NAME_MAXLEN] = b"\x00" * NAME_MAXLEN
        data[anchor_offset: anchor_offset + len(new_name)] = new_name

        reward = p.get('reward')
        if reward is not None:
            struct.pack_into(">I", data, rec_start + SAVE_IDX_OFF, reward & 0xFFFFFFFF)

        y_adjust = p.get('y_adjust', 0.0)
        if y_adjust != 0.0:
            y_off = rec_start + XYZ_OFF + 4
            current_y = struct.unpack_from("<f", data, y_off)[0]
            struct.pack_into("<f", data, y_off, current_y + y_adjust)

        # Rotation override (2026-07-25, Jon's request — the Book of
        # Shadows/AP-marker model, see MSH_OVERRIDES' bookofshadows.msh ->
        # sworm.msh swap in constants.py, needs to stand upright regardless
        # of what slot it lands in). Retyping a slot has never touched
        # ROTATION_A/B before now, so a marker inherited whatever rotation
        # the ORIGINAL occupant had — a barrel lying on its side, an angled
        # cadeaux, etc. rotation_a/rotation_b are set to 0 specifically for
        # RSC_X_BOOK_OF_SHADOWS in make_patch() below, which is the most
        # common baseline value per TECHNICAL.md's own observation ("zero in
        # the majority of records") and should match the mesh's own
        # authored rest pose if it was modeled upright -- NOT independently
        # confirmed live as "vertical" for this specific custom mesh (sworm.
        # msh). If it still doesn't look upright in-game, that means 0/0
        # isn't this mesh's rest orientation and the values below need
        # tuning from an actual in-game observation, not guessed again.
        rotation_a = p.get('rotation_a')
        if rotation_a is not None:
            struct.pack_into(">h", data, rec_start + ROTATION_A_OFF, rotation_a)
        rotation_b = p.get('rotation_b')
        if rotation_b is not None:
            struct.pack_into(">i", data, rec_start + ROTATION_B_OFF, rotation_b)

    if len(data) != size_before:
        print(f"  ERROR: patch_rsc_file changed file size from {size_before} to {len(data)}!")
    with open(filepath, "wb") as f:
        f.write(data)


# ── Soul gate SL shuffling via links.e2o ──────────────────────────────────────

def _sl_to_e2o(sl: int) -> int:
    return sl * E2O_SL_SCALE


def _e2o_to_sl(val: int) -> int:
    return val // E2O_SL_SCALE


def _parse_e2o_gates(data: bytes, folder: str) -> list[dict]:
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
    best_id   = None
    best_dist = float("inf")
    for gate_id, (gfolder, gx, gz) in GATE_E2O_POSITIONS.items():
        if gfolder != folder:
            continue
        dist = abs(x - gx) + abs(z - gz)
        if dist < best_dist and dist < E2O_MATCH_RADIUS:
            best_dist = dist
            best_id = gate_id
    return best_id


def randomize_gate_sl_links(
    gate_remap: dict[str, int],
    levels_path: Path,
) -> None:
    """Write shuffled SL threshold values from gate_remap into each level's links.e2o file."""
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

def patch_gate_arc_decos(
    levels_path: Path,
    gate_remap: dict[str, int],
) -> int:
    import re
    if not gate_remap:
        return 0

    # Per-folder override: which RSC file holds the ARC deco records.
    # Most levels keep them in events.rsc; ah4fogom (Fogometers) uses
    # instance.rsc instead. Missing this override means the Fogometers
    # Interior gate's ARC deco record is never found (events.rsc has no
    # matching record there), so the SL requirement patches correctly but
    # the visual decoration silently stays vanilla — no warning either,
    # since events.rsc itself does exist, it just doesn't hold this
    # record. Ported from patcher.py's standalone (non-AP) fix, confirmed
    # 2026-07-26 (Jon: "value was correct but the decoration was
    # incorrect" on the inside Fogometers gate, default SL10).
    _FOLDER_ARC_RSC: dict[str, str] = {
        "ah4fogom": "instance.rsc",
    }

    folders = set(folder for folder, dx, dz in _DECO_TO_GATE)
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
            # a non-deadside gate gets shuffled to a number with no COFGATE
            # asset. Ported from patcher.py's standalone fix alongside the
            # instance.rsc override above.
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


# ── Spoiler log ───────────────────────────────────────────────────────────────

def _spoiler_gate_section(gate_remap: dict[str, int]) -> list[str]:
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


def _spoiler_soul_thresholds_section(sl_thresholds_result: dict, mode: str) -> list[str]:
    """
    Format the Tier 2 soul-threshold-randomization results (SL tier -> souls
    required) for the spoiler log. Added 2026-07-22 — this data was already
    computed (sl_thresholds_result, Step 6e) and written to soul_thresholds.json,
    but was never appended to the human-readable .txt spoiler log, so the log's
    only SL-related section (_spoiler_gate_section, written earlier at Step 4,
    before sl_thresholds_result exists) always showed VANILLA soul counts even
    when this mode was active and the real in-game requirement differed.
    """
    lines = [
        "",
        "── SOUL THRESHOLD RANDOMIZATION (Tier 2) ───────────────",
        "",
        f"  Mode: {mode}",
        f"  {'SL Tier':<10}  {'Vanilla souls':>13}  {'This seed':>10}",
        f"  {'─'*10}  {'─'*13}  {'─'*10}",
    ]
    for sl in range(1, 11):
        van_souls = VANILLA_SL_THRESHOLDS[sl]
        new_souls = sl_thresholds_result.get(sl, van_souls)
        note = " ←" if new_souls != van_souls else ""
        lines.append(f"  SL{sl:<8}  {van_souls:>13}  {new_souls:>10}{note}")
    lines.append(
        "\n  NOTE: gate SL requirements above (── SOUL GATE SL REQUIREMENTS ──) "
        "still show souls translated via VANILLA thresholds — combine that "
        "section's SL tier per gate with this section's per-tier souls for "
        "the real in-game requirement when this mode is active."
    )
    return lines


def _spoiler_piston_combos_section(table: dict) -> list[str]:
    """
    Format the piston combination table for the spoiler log. Ported from
    the standalone's patcher.py _spoiler_piston_combos_section() (2026-07-21,
    Task 27) — dark_engine_patch.py's PISTON_NAMES/VANILLA_TABLE are already
    imported at module level here, unlike the standalone's version which
    imports them locally inside this function.
    """
    lines = [
        "",
        "── DARK ENGINE COMBINATIONS ────────────────────────────",
        "",
        f"  {'Piston':<38} {'Vanilla':>7}  {'New':>5}",
        f"  {'─'*38}  {'─'*7}  {'─'*5}",
    ]
    for pid in range(1, 7):
        bars = table[pid]
        van  = DARK_ENGINE_VANILLA_TABLE[pid]
        combo   = f"{bars[0]}-{bars[1]}-{bars[2]}"
        vanilla = f"{van[0]}-{van[1]}-{van[2]}"
        changed = " ←" if bars != van else ""
        name = PISTON_NAMES[pid]
        lines.append(f"  {name:<38} {vanilla:>7}  {combo:>5}{changed}")
    return lines


def write_spoiler_log(output_path, seed, patches_by_folder, gate_remap,
                      records_by_folder, config, piston_combo_table=None) -> None:
    lines = [
        "=" * 60,
        "SHADOW MAN REMASTERED - ARCHIPELAGO SPOILER LOG",
        "=" * 60,
        f"Seed: {seed}",
        f"Gate preset: {config.get('gate_preset', 'none')}",
        f"Shuffle weapons: {config.get('shuffle_weapons', True)}",
        f"Shuffle lore: {config.get('shuffle_lore', True)}",
        f"Shuffle bonus: {config.get('shuffle_bonus', False)}",
        f"Shuffle enemies: {config.get('shuffle_enemies', False)}",
        f"Enemy mode: {config.get('enemy_mode', 'full')}",
        f"Enemy mix movement: {config.get('enemy_mix_movement', False)}",
        f"Enemy uncap counts: {config.get('enemy_uncap_counts', False)}",
        f"Shuffle true forms: {config.get('shuffle_true_forms', False)}",
        f"Shuffle ambients: {config.get('shuffle_ambients', False)}",
        f"Ambient mode: {config.get('ambient_mode', 'none')}",
        f"Shuffle music: {config.get('shuffle_music', False)}",
        f"Shuffle voices: {config.get('shuffle_voices', False)}",
        f"Shuffle weapons SFX: {config.get('shuffle_weapons_sfx', False)}",
        f"Shuffle enemies SFX: {config.get('shuffle_enemies_sfx', False)}",
        f"Shuffle sky: {config.get('shuffle_sky', False)}",
        f"Progression balancing: {config.get('progression_balancing', 50)}",
        f"Insanity: {config.get('insanity', False)}",
        f"Starting health: {config.get('starting_health', 'default')}",
        f"Altar health grant: {config.get('altar_health_grant', 'default')}",
        f"Altar cadeaux required: {config.get('altar_cadeaux_required', 'default')}",
        f"Fogometers cadeaux required: {config.get('fogometers_cadeaux_required', 'default')}",
        f"Death penalty: {config.get('death_penalty', 'default')}",
        f"Sprint multiplier: {config.get('sprint_multiplier', 0) or 'off'}",
        f"Soul threshold mode: {config.get('soul_threshold_mode', 'default')}",
        f"Entrance mode: {config.get('entrance_mode', 'off')}",
        f"Piston combos: {config.get('piston_combos', 'off')}",
        "",
    ]
    # NOTE: this header list was previously missing most of the config keys
    # above (only had the first 9) — that gap made a normal single-run
    # generation look like it might have been produced by an older/partial
    # code path. The config dict actually passed to run_patcher always had
    # these values; this was a display gap only, not a generation bug.

    lines += _spoiler_gate_section(gate_remap)

    if piston_combo_table is not None:
        lines += _spoiler_piston_combos_section(piston_combo_table)

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

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Spoiler log written to: {output_path}")


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


# ── RSC patch generation ──────────────────────────────────────────────────────

# Every CHECKABLE_LOCS row (is_verified is not False) whose category is
# "cadeaux" — i.e. every cadeaux slot the standalone tooling considers a
# real, visible, collectable spot in-game. Used below to find verified
# cadeaux locations that this seed's progression_placement never touched
# (see the "neuter excluded verified cadeaux locations" pass in
# write_placement_patches()) — computed once at import time since
# CHECKABLE_LOCS itself is a static module-level constant.
VERIFIED_CADEAUX_LOC_KEYS: frozenset[str] = frozenset(
    l.loc_key for l in CHECKABLE_LOCS if l.category == "cadeaux"
)


def write_placement_patches(
    records_by_folder: dict,
    progression_placement: dict,
) -> tuple[dict, list]:
    """
    Convert AP-provided placement decisions into RSC file patches.

    shuffle_gad_temples removed as a parameter (2026-08-15): a gad temple's
    RSC_X_GAD_PICKUP record used to be overwritten with a decorative
    RSC_X_BARREL_D (silently discarding whatever AP placed there) whenever
    the option was off -- but nothing upstream ever excluded gad temple
    locations from AP's own fillable location pool, so that branch could
    (and did) throw away real placements, up to and including required
    progression items, with no error anywhere. Gad temple records now
    always go through the normal placement path below, same as any other
    location.
    progression_placement maps loc_key → RawLocation namedtuple
    (with .object = RSC name, .save_idx = save-game ID).

    Returns (patches_by_folder, marker_sites). marker_sites is populated
    whenever a key item (including AP's foreign-item "Book of Archipelago"
    marker) lands in a soul/govi/dark-soul/cadeaux/barrel slot, so
    inject_special_item_fx() can spawn the matching altar/pot visual —
    ported from the standalone's patcher.py write_placement_patches()
    (2026-07-20), replacing the older binary GOVI_HEIGHT_BOOST/
    CADEAU_HEIGHT_DROP-only y_adj logic with the newer 6-way branch that
    distinguishes govi/dark_soul/cadeaux/barrel slots individually.
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
        new_tall            = new_name in DARK_SOUL_TYPES
        old_dark_soul_slot  = rec.name == "RSC_X_DARK_SOUL"
        old_govi_slot       = rec.name == "RSC_X_GOVI"
        old_any_soul_slot   = old_dark_soul_slot or old_govi_slot
        old_cadeaux_slot    = rec.name in CADEAUX_TYPES
        old_barrel_slot     = rec.name in BARREL_TYPES
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
        # ITEM_Y_ADJUST keys are (new_name, old_name_or_None)
        y_adj += ITEM_Y_ADJUST.get((new_name, None), 0.0)
        patch = {"name": new_name, "reward": instance_id,
                 "logic": rec.zone, "y_adjust": y_adj, "source_file": rec.source_file}
        # Force the Book of Shadows/AP-marker model to stand upright instead
        # of inheriting whatever rotation the original slot's object had.
        # CONFIRMED LIVE (2026-07-25, Jon): ROTATION_A_OFF ("likely
        # pitch/roll" per TECHNICAL.md) is the right axis for tipping the
        # book upright. Tried rotation_a=270 (clean tip, no clipping) paired
        # with rotation_b (yaw) at 180/90/0 to fix facing direction —
        # ROTATION_B_OFF turned out NOT to control facing at all; the book
        # faced away from the viewer identically regardless of its value.
        # Reverted to rotation_a=90, which WAS confirmed to face the
        # correct direction on its own — the tradeoff is it sits
        # lower/clips slightly at the slot's native height, compensated via
        # ITEM_Y_ADJUST's ("RSC_X_BOOK_OF_SHADOWS", None) entry in
        # constants.py. rotation_b left at 0 (no confirmed effect either
        # way). If a genuine third rotation field is ever found (the
        # record's several "unknown/padding" byte ranges are the only
        # candidates — see TECHNICAL.md §10.2), that could let this drop
        # the Y compensation instead — not pursued further here since it
        # needs live reverse-engineering (Cheat Engine breakpointing),
        # which can't be done from this environment.
        if new_name == "RSC_X_BOOK_OF_SHADOWS":
            # CONFIRMED LIVE (2026-07-25, Jon) — final combination, standing
            # upright, facing the viewer, right-side up: rotation_a=90 (pitch,
            # gets the facing direction right) + rotation_b=180 (yaw, flips
            # it right-side up -- despite rotation_b appearing to have no
            # effect on facing when tested earlier alongside rotation_a=270;
            # the two fields don't compose the same way from a different
            # starting pitch). Y-height compensated separately via
            # ITEM_Y_ADJUST's ("RSC_X_BOOK_OF_SHADOWS", None) entry in
            # constants.py (rotation_a=90 sits lower/clips at the slot's
            # native height without it).
            patch["rotation_a"] = 90
            patch["rotation_b"] = 180
        return patch

    patches_by_folder: dict = {}
    marker_sites: list = []  # (folder, source_file, x, y, z, zone, fx_name)
    matched = 0

    for loc_key, source_loc in progression_placement.items():
        rec = all_rec_index.get(loc_key)
        if rec is None:
            continue
        rsc_name = source_loc.object

        # RSC_X_VIOLATOR requires accumulator window activation — use standard pickup variant
        if rsc_name == "RSC_X_VIOLATOR":
            rsc_name = "RSC_Q_VIOLATOR"

        # Substitute barrel RSC names whose assets have been replaced with
        # custom visuals — prevents the marker crate appearing at filler spots.
        # Ported from standalone patcher.py (2026-07-21, was missing from the
        # original AP port — see write_placement_patches() docstring history).
        rsc_name = BARREL_RSC_SUBSTITUTIONS.get(rsc_name, rsc_name)

        # When a cadeaux-carrying barrel (category="cadeaux", object=RSC_X_BARREL_*)
        # is placed ANYWHERE, normalize its RSC name to RSC_X_CADEAUX. Original
        # 2026-07-21 port only normalized the CROSS-level case (source_loc.level_id
        # != rec.folder), on the assumption that a same-level reuse of a barrel-
        # shaped cadeaux donor's identity was safe to leave alone. FIXED
        # 2026-08-02 (Jon's report + in-game verification): that assumption was
        # wrong. Three real seed locations -- Deadside Wasteland's "Govi - Dark
        # Soul 23" and "Pot 33" (both patched with a wastland-level donor at a
        # wastland-level destination) and Temple of Fire's "Gad Power Upgrade"
        # (a t1tchgad-level donor at a t1tchgad-level destination) -- all landed
        # a "Cadeaux Bundle x10" item, all kept the donor's raw barrel object
        # (RSC_X_BARREL_D / RSC_X_BARREL_A) since level_id == rec.folder in every
        # case, and all three showed up in-game as empty, non-functional barrels
        # (confirmed directly by Jon, not inferred). Per Jon: "barrel cadeaux
        # aren't as consistent when moving location so its safer to just turn
        # them into a cadeaux item" -- dropped the level_id comparison entirely
        # so this now fires unconditionally for any barrel-typed cadeaux-category
        # placement, same-level or cross-level. Detection for cadeaux is a
        # save-file position/coordinate scan against locations.csv (see
        # CLAUDE.md's Item Tracking & Patching Reference table), not object-name
        # dependent, so forcing a clean RSC_X_CADEAUX here doesn't change how the
        # pickup is tracked -- only that it's now guaranteed to be a real,
        # functional cadeaux pickup rather than whatever the donor's own object
        # happened to be.
        if (rsc_name in BARREL_TYPES
                and hasattr(source_loc, "category") and source_loc.category == "cadeaux"):
            rsc_name = "RSC_X_CADEAUX"

        instance_id = source_loc.save_idx
        k = (rec.folder, rec.source_file)
        patches_by_folder.setdefault(k, {})[rec.offset] = \
            make_patch(rec, rsc_name, instance_id if instance_id is not None else rec.instance_id)
        matched += 1

        # Insanity-mode / AP foreign-item marker sites — a key item (including
        # the "Book of Archipelago" marker used for other players' items)
        # landing in a soul/govi/cadeaux/barrel slot gets a matching altar
        # (soul/govi) or pot (cadeaux/barrel) FX object spawned next to it.
        old_soul_slot    = rec.name in DARK_SOUL_TYPES
        old_cadeaux_slot = rec.name in CADEAUX_TYPES
        old_barrel_slot  = rec.name in BARREL_TYPES
        new_is_key = (rsc_name not in DARK_SOUL_TYPES
                      and rsc_name not in CADEAUX_TYPES
                      and rsc_name not in BARREL_TYPES)
        if new_is_key and old_soul_slot:
            altar_y_off = DARK_SOUL_SLOT_MARKER_FX_Y if rec.name == "RSC_X_DARK_SOUL" else SOUL_SLOT_MARKER_FX_Y
            marker_sites.append((rec.folder, rec.source_file, rec.x, rec.y + altar_y_off, rec.z, rec.zone,
                                 SOUL_SLOT_MARKER_FX))
        elif new_is_key and (old_cadeaux_slot or old_barrel_slot):
            marker_sites.append((rec.folder, rec.source_file, rec.x, rec.y + BARREL_SLOT_MARKER_FX_Y, rec.z, rec.zone,
                                 BARREL_SLOT_MARKER_FX))

    print(f"  RSC patches: {matched} locations written from AP placement")

    # ── Neuter excluded verified cadeaux locations (2026-08-15, Jon's request) ──
    # CadeauxBundleSize (regions.py's compute_cadeaux_bundle_representatives())
    # groups every verified cadeaux loc_key into bundles and only surfaces ONE
    # representative per bundle as a real AP location; every other loc_key in
    # that bundle is intentionally never entered into progression_placement —
    # by design, its "worth" is fully absorbed into the representative's
    # "Cadeaux Bundle xN" item instead (see that function's docstring: 653
    # verified cadeaux total, at bundle_size 5 that's 130 bundles of x5 + one
    # x3, every one of the 653 accounted for through a bundle item). Left
    # untouched, though, the leftover physical barrel/pot at each
    # non-representative loc_key is still a real, functioning vanilla cadeaux
    # pickup in-game — collecting it would hand the player a genuine extra
    # cadeaux on top of the 653 already credited via bundle items, breaking
    # the game's own 666-total accounting (653 verified + 13 permanently
    # untouched is_verified=False rows, see UNVERIFIED_LOCS/fill.py, already
    # exactly totals 666 without these leftovers). So: any VERIFIED_CADEAUX_
    # LOC_KEYS entry NOT claimed by this seed's progression_placement gets
    # retyped to a plain, reward-less RSC_X_BARREL_D here — same shape as the
    # old shuffle_gad_temples neuter branch this file used to have (name +
    # reward=0), so patch_rsc_file()'s existing record_templates mechanism
    # copies a real barrel's track_type over automatically. is_verified=False
    # cadeaux rows are NOT in VERIFIED_CADEAUX_LOC_KEYS (CHECKABLE_LOCS
    # already excludes them) and are correctly left alone — those stay
    # vanilla on purpose, they're the 13 that make up the rest of the 666.
    excluded_cadeaux = VERIFIED_CADEAUX_LOC_KEYS - set(progression_placement.keys())
    neutered = 0
    for loc_key in excluded_cadeaux:
        rec = all_rec_index.get(loc_key)
        if rec is None:
            continue
        k = (rec.folder, rec.source_file)
        patches_by_folder.setdefault(k, {})[rec.offset] = {
            "name": "RSC_X_BARREL_D",
            "reward": 0,
            "logic": rec.zone,
            "y_adjust": 0.0,
            "source_file": rec.source_file,
        }
        neutered += 1
    if neutered:
        print(f"  RSC patches: {neutered} excluded verified cadeaux location(s) neutered to empty barrels")

    return patches_by_folder, marker_sites


# ── Asset / MSH overrides ────────────────────────────────────────────────────
# Ported from the standalone's patcher.py apply_msh_overrides() (2026-07-20).

def apply_msh_overrides(randomizer_dir, work_path, kpf_index=None) -> dict:
    """Scale MSH vertex tables and return as a mod_files dict for KPF packing."""
    from kpf_handler import find_file_in_kpf, extract_file_from_kpf

    mod_files = {}

    for kpf_path, scale, local_src in MSH_OVERRIDES:
        data = None

        # Prefer a local source file over extracting from the KPF.
        if local_src:
            src_path = Path(randomizer_dir) / local_src
            if src_path.exists():
                data = bytearray(src_path.read_bytes())
            else:
                print(f"  WARNING: MSH local source not found — {local_src}")

        if data is None and kpf_index:
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

        # EMshV001 layout: byte 15 = vertex count; vertex table is always
        # at the end of the file (vert_off = file_size - n_verts * 24).
        n_verts  = data[15]
        vert_off = len(data) - n_verts * 24

        for i in range(n_verts):
            for axis in range(3):
                off = vert_off + i * 24 + axis * 4
                if off + 4 > len(data):
                    break
                val = struct.unpack_from('<f', data, off)[0]
                struct.pack_into('<f', data, off, val * scale)

        out_path = Path(work_path) / "msh_overrides" / Path(kpf_path).name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(bytes(data))
        mod_files[kpf_path] = str(out_path)
        src_label = local_src or "(from KPF)"
        print(f"  [msh] {kpf_path} ← {src_label} (scale {scale}x)")

    return mod_files


# ── Special item FX injection ───────────────────────────────────────────────
# Ported from the standalone's patcher.py inject_special_item_fx() (2026-07-20).
# Spawns a small altar/pot marker object next to a key item that landed in a
# soul/govi/cadeaux/barrel slot (insanity-mode placements, including AP's
# foreign-item "Book of Archipelago" marker — see write_placement_patches()).

def _inject_one_fx_record(rsc_path: Path, rsc_name: str, x: float, y: float, z: float, zone: int) -> bool:
    # Only inject directly into quest.rsc — other RSC types use byte 9 as a
    # flags/type field, not a live-window count, so injecting there corrupts it.
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


# ── Unified entrance shuffle (.cut patches) ──────────────────────────────────
# Ported from the standalone's patcher.py Step 4e (2026-07-21, Task 19).
# Applies AP's deadside_only portal shuffle by rewriting ExitLevelPos() calls
# in the extracted cutscene .cut script files — the same mechanism the
# standalone's own randomizer uses (randomizers/entrance_randomizer.py),
# reused here rather than reimplemented so both tools share one source of
# truth for the transition table and the regex patch logic.

def build_deadside_unified_shuffle(entrance_shuffle: dict[str, str]) -> UnifiedShuffle:
    """
    Convert AP's plain dict[portal_file, dest_portal_file] (both "LE_*.cut"
    filenames, folder always "deadside" — see DEADSIDE_PORTAL_FILES in the
    AP world's regions.py) into the (portal_folder, portal_file)-keyed
    UnifiedShuffle apply_unified_shuffle() expects.

    outbound[("deadside", portal_file)] = ("deadside", dest_portal_file)
    inbound is the exact inverse — deadside_only is a pure bijection over the
    9 Deadside spokes, no cross-hub coupling, so inverting is always safe.
    """
    outbound = {("deadside", pf): ("deadside", dpf) for pf, dpf in entrance_shuffle.items()}
    inbound  = {spoke: portal for portal, spoke in outbound.items()}
    return UnifiedShuffle(mode="deadside_only", outbound=outbound, inbound=inbound)


def apply_entrance_shuffle_patches(
    entrance_shuffle: dict[str, str],
    work_path: Path,
    levels_path: Path,
    using_kpf: bool,
    kpf_index=None,
) -> dict[str, str]:
    """
    Extract the 9 Deadside portal .cut files (+ their spoke exit.cut files)
    from the KPF if needed, apply the shuffle, and return a dict of
    {kpf_internal_path: local_path} ready for repack_after_patch()'s
    extra_mod_files. No-ops (returns {}) if entrance_shuffle is falsy.
    """
    if not entrance_shuffle:
        return {}

    from kpf_handler import find_file_in_kpf, extract_file_from_kpf

    CUT_PREFIX  = "cutscene/scripts"
    scripts_dir = work_path / CUT_PREFIX
    scripts_dir.mkdir(parents=True, exist_ok=True)

    print("\nThe portals between worlds tremble -- entrances reshuffled (deadside_only)...")

    if using_kpf and kpf_index is not None:
        cut_paths_needed = (
            [f"{CUT_PREFIX}/{t.portal_folder}/{t.portal_file}"
             for t in DEADSIDE_UNIFIED_TRANSITIONS if t.portal_file is not None]
            + [f"{CUT_PREFIX}/{t.spoke_folder}/{t.spoke_exit_file}"
               for t in DEADSIDE_UNIFIED_TRANSITIONS]
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

    shuffle = build_deadside_unified_shuffle(entrance_shuffle)
    apply_unified_shuffle(shuffle, scripts_dir, verbose=False)
    print(f"  {len(shuffle.outbound)} outbound + {len(shuffle.inbound)} return "
          f"route(s) patched (see spoiler log for the mapping)")

    entrance_cut_files: dict[str, str] = {}
    for t in DEADSIDE_UNIFIED_TRANSITIONS:
        rels = [f"{CUT_PREFIX}/{t.spoke_folder}/{t.spoke_exit_file}"]
        if t.portal_file is not None:
            rels.append(f"{CUT_PREFIX}/{t.portal_folder}/{t.portal_file}")
        for rel in rels:
            local = work_path / rel
            if local.exists():
                entrance_cut_files[rel] = str(local)

    return entrance_cut_files


# ── KPF repack ────────────────────────────────────────────────────────────────

def repack_after_patch(game_dir, patches_by_folder, gate_remap, config,
                       spoiler_path, work_dir, seed, extra_mod_files=None):
    try:
        from kpf_handler import (find_kpf_files, build_kpf_index,
                                   find_file_in_kpf, build_and_install_mod)
    except ImportError:
        print("\nkpf_handler not found - skipping mod KPF creation")
        return

    kpf_files = find_kpf_files(game_dir)
    if not kpf_files:
        print("\nNo KPF files found - cannot determine internal paths")
        return

    print("\nBuilding randomizer mod KPF...")
    kpf_index = build_kpf_index(kpf_files)
    mod_files = {}
    for folder in LEVEL_FOLDERS:
        for filename in SOUL_RSC_FILES | ENEMY_RSC_FILES:
            local = Path(work_dir) / "levels" / folder / filename
            if local.exists():
                matches  = find_file_in_kpf(kpf_index, f"*/{folder}/{filename}")
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

        for folder in ("deadside", "t1tchgad", "t2wlkgad", "t3swmgad", "wastland"):
            local = Path(work_dir) / "levels" / folder / "events.rsc"
            if local.exists():
                matches  = find_file_in_kpf(kpf_index, f"*/{folder}/events.rsc")
                internal = matches[0][0] if matches else f"levels/{folder}/events.rsc"
                mod_files[internal] = str(local)

    # levels.txt tracker patch (mirrors standalone repack) — the active
    # variant written by Step 9.7 gets packed so the in-game tracker and
    # per-level $cadeaux counts ship with the mod.
    _ltxt = Path(work_dir) / "scripts" / "levels.txt"
    if _ltxt.exists():
        matches = find_file_in_kpf(kpf_index, "scripts/levels.txt")
        internal = matches[0][0] if matches else "scripts/levels.txt"
        mod_files[internal] = str(_ltxt)

    if extra_mod_files:
        mod_files.update(extra_mod_files)

    if not mod_files:
        print("  Nothing to pack into mod KPF")
        return
    build_and_install_mod(game_dir, mod_files, seed)


# ── Validation ────────────────────────────────────────────────────────────────

def validate_final_seed(work_dir: str, progression_placement: dict = None,
                         patches_by_folder: dict = None) -> None:
    print("\n── Final seed validation ──────────────────────────")
    levels_path = Path(work_dir) / "levels"
    error_count = 0
    soul_id_zero_count = 0

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
                    # NOTE (2026-07-25): used to count toward error_count.
                    # As of __init__.py's _soul_identity_map() fix earlier
                    # today (removed "Pass 2", which borrowed an identity
                    # from a still-live native soul slot elsewhere -- see
                    # that method's docstring for why that was a real
                    # collision bug), a Dark Soul placed on a non-soul slot
                    # with a naturally-zero native save_idx now routinely
                    # ends up with instance_id=0. That's the safe,
                    # intentional fallback: the client's save-file Govi
                    # position scan resolves these instead of the live
                    # flag-array watcher (which can't use save_idx=0 -- see
                    # client.py's _poll_live_light_soul/_match_govi_position_scan
                    # comments). No longer a patching error, just informational.
                    soul_id_zero_count += 1
                if not r.name.upper().startswith("RSC_"):
                    print(f"  [!] Bad name {r.name!r}: {folder}/{source_file} at 0x{r.offset:04X}")
                    error_count += 1
                if len(r.name) >= NAME_MAXLEN:
                    print(f"  [!] Name too long {r.name!r}: {folder}/{source_file} at 0x{r.offset:04X}")
                    error_count += 1

    if soul_id_zero_count:
        print(f"  [info] {soul_id_zero_count} Dark Soul(s) with save_idx=0 "
              f"(placed on a non-soul slot -- resolves via the save-file "
              f"position scan, not an error)")

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
        # locations.csv) — the AP world's fill.py excludes these from
        # CHECKABLE_LOCS entirely (2026-07-21 fix), so they should never appear
        # here in practice. Kept as a defensive net matching patcher.py's own
        # validate_final_seed(), in case that exclusion is ever loosened.
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
        print(f"  ✅ Patch validation passed.")
    else:
        print(f"  ❌ {error_count} patching error(s) found.")


# ── Main entry point ──────────────────────────────────────────────────────────

def run_patcher(
    game_dir: str,
    seed: int,
    config: dict,
    output_dir: str,
    progression_placement: dict,
    gate_remap: dict[str, int],
    entrance_shuffle: dict[str, str] | None = None,
) -> None:
    """
    AP entry point.  Called from ShadowManWorld.generate_output().

    progression_placement: dict[loc_key, RawLocation]
        AP-resolved item placement.  Each value is a RawLocation namedtuple
        whose .object field holds the RSC name to write and .save_idx
        holds the save-game reward ID (4-byte BE, see SAVE_IDX_OFF).

    gate_remap: dict[gate_id, sl]
        Gate soul-level assignments computed during generate_early().

    entrance_shuffle: dict[portal_file, dest_portal_file] | None
        Deadside portal shuffle computed during generate_early() when
        EntranceMode is deadside_only (see options.py). None when the mode
        is off (default) — added 2026-07-21, Task 19/20.
    """
    from kpf_handler import (find_kpf_files, build_kpf_index,
                               extract_game_files, which_kpf_has_levels)

    rng       = random.Random(seed)
    game_path = Path(game_dir)

    # ── KPF extraction ────────────────────────────────────────────────────────

    kpf_files = find_kpf_files(game_dir)
    using_kpf = bool(kpf_files)

    if using_kpf:
        work_path = game_path / f"_randomizer_work_{seed}"
        # Never reuse an existing work dir as-is: if one is already present
        # (same seed re-applied, a prior interrupted run, etc.), bump to the
        # next free suffix (_01, _02, ...) so this run always starts from a
        # brand-new directory that extract_game_files() has to populate fresh
        # from the vanilla base KPFs. Ported from patcher.py's run_patcher()
        # (2026-07-25) — that copy already carried this fix, ap_patcher.py's
        # copy did not, which is exactly the kind of silent cross-repo drift
        # CLAUDE.md warns about. Without this, extract_game_files()'s
        # "skip if dest already exists" shortcut would leave stale,
        # previously-patched bytes in place instead of re-extracting vanilla
        # for this run.
        if work_path.exists():
            n = 1
            while True:
                candidate = game_path / f"_randomizer_work_{seed}_{n:02d}"
                if not candidate.exists():
                    work_path = candidate
                    break
                n += 1
        work_path.mkdir()
        print(f"Shadow Man Remastered Archipelago Randomizer")
        print(f"Seed: {seed}  |  Mode: KPF repack  |  Found {len(kpf_files)} KPF archives")
        print()
        print("Extracting game files from KPFs...")
        kpf_index   = extract_game_files(kpf_files, str(work_path), LEVEL_FOLDERS)
        levels_kpf  = which_kpf_has_levels(kpf_index)
        print(f"  Core data KPF: {levels_kpf}")
        levels_path = work_path / "levels"
    else:
        work_path   = game_path
        levels_path = game_path / "levels"
        kpf_index   = None
        print(f"Shadow Man Remastered Archipelago Randomizer")
        print(f"Seed: {seed}  |  Mode: Direct file edit  |  Game dir: {game_dir}")

    # ── Pre-step: inject GAD records ─────────────────────────────────────────

    from setup_gad_records import GAD_INJECTION_SITES, inject_record, _find_existing
    print("\nInjecting RSC_X_GAD_PICKUP records...")
    for folder, filename, x, y, z, zone in GAD_INJECTION_SITES:
        rsc_path = levels_path / folder / filename
        if rsc_path.exists():
            data = bytearray(rsc_path.read_bytes())
            off, already = inject_record(data, x, y, z, zone)
            if not already:
                rsc_path.write_bytes(bytes(data))
            status = "already present" if already else "injected"
            print(f"  {folder}/{filename} @ 0x{off:04X} ({status})")

    # BUG FIX (2026-07-28): this used to be `out_path = Path(output_dir)` --
    # output_dir is a caller-supplied parameter (apply_ap_seed.py computes it
    # as game_dir / f"_randomizer_work_{seed}", mirroring work_path's naming
    # -- but *without* knowledge of the bump-to-next-free-suffix logic above,
    # since that logic is entirely internal to this function and never
    # reported back to the caller). Whenever work_path got bumped to _01/_02/
    # etc. because a same-seed work dir already existed, output_dir stayed
    # pointed at the OLD, un-bumped directory -- so object_map.csv,
    # spoiler_seed_<seed>.txt, and soul_thresholds.json (the three artifacts
    # built from out_path below) silently kept landing next to a STALE
    # extraction instead of the fresh one this run actually patched and
    # packed into the mod KPF. Confirmed live: Jon's own patch-application
    # log showed KPF extraction correctly going to
    # "..._randomizer_work_<seed>_02" while "Object map:"/"Spoiler log
    # written to:"/"Soul thresholds:" all logged the un-suffixed path.
    # These 3 files are pure debug/diagnostic output (never read back by the
    # patching or repack steps -- confirmed via grep, out_path/output_dir
    # has no other consumers in this file), but a stale spoiler log is
    # exactly the kind of thing that misleads debugging a "wrong item
    # in-game" report, since it's the natural first place to check what a
    # seed *should* have placed. Using work_path directly instead of a
    # separately-computed output_dir guarantees these always describe the
    # same run whose bytes actually got packed into the mod KPF.
    out_path = work_path
    out_path.mkdir(parents=True, exist_ok=True)

    # Verify injection succeeded
    missing_gad = []
    for folder, filename, x, y, z, zone in GAD_INJECTION_SITES:
        rsc_path = levels_path / folder / filename
        if rsc_path.exists():
            data = rsc_path.read_bytes()
            if _find_existing(data, zone, x, y, z) is None:
                missing_gad.append(f"{folder}/{filename}")
    if missing_gad:
        print(f"  WARNING: GAD_PICKUP records missing after injection: {missing_gad}")

    print()

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
        souls = sum(1 for r in records if r.name in DARK_SOUL_TYPES)
        print(f"  {folder:<12}: {souls} souls  [{', '.join(files_found)}]")

    # Object map CSV
    object_map = [
        {"folder": folder, "source_file": rec.source_file,
         "offset": f"0x{rec.offset:04X}", "name": rec.name,
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

    # ── Step 2: Write gate SL values to links.e2o ────────────────────────────

    gates_changed = any(
        gate_remap.get(g) != GATE_VANILLA_SL.get(g)
        for g in GATE_VANILLA_SL
    )
    if gates_changed:
        print("\nWriting soul gate requirements to links.e2o...")
        randomize_gate_sl_links(gate_remap, levels_path=levels_path)

        # Ensure events.rsc files are present for ARC deco patching
        if using_kpf:
            from kpf_handler import find_file_in_kpf, extract_file_from_kpf
            for folder in ("deadside", "t1tchgad", "t2wlkgad", "t3swmgad", "wastland"):
                events_path = levels_path / folder / "events.rsc"
                if not events_path.exists():
                    try:
                        matches = find_file_in_kpf(kpf_index, f"*/{folder}/events.rsc")
                        if matches:
                            events_path.parent.mkdir(parents=True, exist_ok=True)
                            extract_file_from_kpf(
                                str(Path(kpf_index.kpf_dir) / matches[0][1]),
                                matches[0][0],
                                str(events_path),
                            )
                    except Exception as e:
                        print(f"  WARNING: events.rsc extraction failed for {folder}: {e}")

    # ── Step 3: Generate RSC placement patches ────────────────────────────────

    print("\nPatching RSC items...")
    patches_by_folder, marker_sites = write_placement_patches(
        records_by_folder,
        progression_placement=progression_placement,
    )
    print(f"  {sum(len(p) for p in patches_by_folder.values())} RSC patches generated")

    # ── Step 3.5: Piston combo randomization (2026-07-21, Task 27) ───────────
    # Computed here (before the spoiler log) so it can be reported there too,
    # mirroring the standalone's own Step 5 placement. Uses this function's
    # own `rng` (not a value threaded from AP) — unlike gate_remap/
    # soul_thresholds_precomputed, nothing else (client, region logic) needs
    # to agree on the EXACT combo values, only on whether Jack's Schematic is
    # required to read them (that's config["piston_combos"] plus AP's own
    # region-graph/completion_condition gating, both already resolved before
    # this file ever runs — see access_rules.py's pistons()/schematic() and
    # options.py's PistonCombos in the AP world). randomize_dark_engine()
    # itself returns the vanilla table untouched when config["piston_combos"]
    # is "off" (the default AP sends — see generate_output()'s config dict).
    piston_combo_table = randomize_dark_engine(rng, config)

    # ── Step 4: Write spoiler log ─────────────────────────────────────────────

    spoiler_path = out_path / f"spoiler_seed_{seed}.txt"
    write_spoiler_log(
        str(spoiler_path), seed, patches_by_folder,
        gate_remap, records_by_folder, config,
        piston_combo_table=piston_combo_table,
    )

    # ── Step 5: Build record templates ───────────────────────────────────────

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
            patches  = patches_by_folder.get(key, {})
            rsc_file = folder_path / source_file
            if not rsc_file.exists() or not patches:
                continue
            # NOTE (2026-07-21): audit_govi_patches()/verify_patch() used to run
            # here for quest.rsc, same as the standalone's patcher.py. Removed
            # for the AP path specifically — verify_patch()'s reward/save_idx
            # check flags a "MISMATCH" any time a soul's save_idx was
            # intentionally reassigned by _soul_identity_map() (every AP seed
            # that moves a Dark Soul to a new slot does this on purpose, see
            # generate_output()'s Dark Soul retype comment), which is expected
            # AP behavior, not a real patching failure. The standalone never
            # reassigns soul identity this way, so its own patcher.py call
            # stays as a genuine correctness check. Both functions are still
            # defined above for manual debugging if needed.
            patch_rsc_file(str(rsc_file), patches, record_templates=record_templates)
            print(f"  [{source_file.upper().replace('.RSC','')}] "
                  f"{folder}/{source_file} ({len(patches)} changes)")

    # ── Step 6.5: Inject special item FX for insanity/AP-marker placements ───
    # Ported from the standalone's patcher.py Step 6b.5 (2026-07-20), then
    # DISABLED for the AP path specifically (2026-07-21 — user request).
    # The standalone needs this: it conceals the real identity of a key
    # item placed into a former soul/govi/cadeaux/barrel slot, since the
    # physical pickup itself would otherwise give away what's there. An AP
    # item never reveals its identity through its physical pickup in the
    # first place (it's always a generic AP marker until read via hint or
    # collected) — so there's nothing to conceal, and the extra
    # RSC_X_WEAPON_ALTAR/RSC_UN_CRATES decoy objects this spawns are just
    # visual clutter with no functional purpose in a multiworld. Left
    # write_placement_patches() and inject_special_item_fx() (below)
    # untouched — marker_sites is still built, just never consumed here —
    # so this is a one-line revert if that judgment ever changes, and the
    # standalone's own patcher.py call (Step 6b.5) is untouched and still
    # fires normally.
    #
    # if marker_sites:
    #     print("\nMarking the sacred hiding places with voodoo sigils...")
    #     n_markers = inject_special_item_fx(marker_sites, levels_path)
    #     print(f"  {n_markers} insanity sigil(s) bound")

    # ── Step 6b: Enemy shuffle ────────────────────────────────────────────────

    enemy_patches = {}
    true_form_loc_remap = None
    tf_patches = {}

    if config.get("shuffle_true_forms", False):
        from randomizers.enemy_randomizer import randomize_true_forms, true_form_spoiler_section
        tf_patches, true_form_loc_remap = randomize_true_forms(rng, gate_remap)

    if config.get("shuffle_enemies", False) or config.get("shuffle_true_forms", False):
        print("\nShuffling enemies...")
        from randomizers.enemy_randomizer import randomize_enemies, enemy_spoiler_section

        if config.get("shuffle_enemies", False):
            enemy_patches = randomize_enemies(rng, levels_path, config,
                                              true_form_patches=tf_patches,
                                              gate_remap=gate_remap)
        else:
            enemy_patches = {}

        for folder_key, patches in tf_patches.items():
            enemy_patches.setdefault(folder_key, {}).update(patches)

        for (folder, source_file) in sorted(enemy_patches):
            patches  = enemy_patches[(folder, source_file)]
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

    # ── Step 6c: Append enemy/true form sections to spoiler log ──────────────

    if enemy_patches or true_form_loc_remap:
        from randomizers.enemy_randomizer import enemy_spoiler_section, true_form_spoiler_section
        with open(str(spoiler_path), "a", encoding="utf-8") as f:
            if enemy_patches:
                f.write("\n" + "\n".join(enemy_spoiler_section(enemy_patches)))
            if true_form_loc_remap:
                f.write("\n" + "\n".join(true_form_spoiler_section(true_form_loc_remap)))

    # ── Step 6d: Ambient creature shuffle ─────────────────────────────────────
    # Cosmetic only: shuffles friendly/ambient creatures (rats, egrets, flies,
    # butterflies, friendly fish) across their slots. Mirrors standalone
    # patcher Step 6b.6.

    if config.get("shuffle_ambients", False):
        print("\nThe spirits of the wild slip between their haunts...")
        from randomizers.ambient_randomizer import randomize_ambients, ambient_spoiler_section
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
        for key, patches in ambient_patches.items():
            patches_by_folder.setdefault(key, {}).update(patches)
        if ambient_patches:
            with open(str(spoiler_path), "a", encoding="utf-8") as f:
                f.write("\n" + "\n".join(ambient_spoiler_section(ambient_patches)))

    # ── Step 6e: Soul threshold randomization (Tier 2) ────────────────────────
    # Computed here (not inside the Step 7 EXE-patch block) so it's available
    # for the Step 8 JSON regardless of whether thoth_x64.exe was found.

    from soul_threshold_patch import randomize_soul_thresholds, apply_soul_threshold_patch

    _st_mode = config.get("soul_threshold_mode", "off")
    sl_thresholds_result = None
    if _st_mode != "off":
        _precomputed = config.get("soul_thresholds_precomputed")
        if _precomputed is not None:
            # AP world path: generate_early() already rolled this via
            # self.random and handed it to fill_slot_data() too — reuse it
            # instead of drawing again from `rng`, otherwise the client would
            # be told a different SL->souls mapping than what's actually
            # baked into the patched EXE (two independent rng streams).
            sl_thresholds_result = {int(k): int(v) for k, v in _precomputed.items()}
            print(f"\n  [soul_thresholds] Mode={_st_mode} (precomputed by AP world): "
                  f"{ {sl: sl_thresholds_result[sl] for sl in range(1, 11)} }")
            # FIXED 2026-07-24: this used to unconditionally warn that
            # access_rules.py "still assumes VANILLA soul thresholds" --
            # that was true before the 2026-07-20/21 fix (see that file's
            # _SOUL_THRESHOLDS comment block), which threaded self.sl_thresholds
            # through R.gate()/R.sl1-10()/make_entrance_rule()/make_location_rule()
            # end to end. This is the normal AP path (generate_early() always
            # sets self.sl_thresholds and hands it to both fill_slot_data() and
            # here), so the values above ARE what AP's own logic graph assumed
            # when building this seed -- no desync, nothing to warn about.
            print("  [soul_thresholds] access_rules.py logic uses this exact "
                  "resolved SL->soul mapping (threaded via generate_early()'s "
                  "self.sl_thresholds) -- no desync between logic and EXE.")
        else:
            sl_thresholds_result = randomize_soul_thresholds(rng, mode=_st_mode)
            print(f"\n  [soul_thresholds] Mode={_st_mode}: "
                  f"{ {sl: sl_thresholds_result[sl] for sl in range(1, 11)} }")
            # This branch means no precomputed value arrived from the AP
            # world (unexpected for a normal AP-generated seed -- generate_
            # early() always sets self.sl_thresholds when mode != "off").
            # Unlike the precomputed branch above, THIS roll is local to
            # this patcher run and was never seen by access_rules.py during
            # generation, so it genuinely can desync logic from the exe.
            print("  [soul_thresholds] WARNING: no precomputed thresholds "
                  "were supplied (soul_thresholds_precomputed missing from "
                  "config) -- rolled a fresh local mapping instead. AP's "
                  "access_rules.py logic was built using whatever mapping "
                  "generate_early() actually resolved, NOT this one -- if "
                  "they differ, gate reachability in-game will desync from "
                  "what AP's logic graph assumed. This should not happen "
                  "for a normal AP-generated seed; check how this patcher "
                  "run was invoked if you see this.")
        with open(str(spoiler_path), "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(
                _spoiler_soul_thresholds_section(sl_thresholds_result, _st_mode)))

    # ── Step 7: EXE patches ───────────────────────────────────────────────────

    exe_src = list(game_path.glob("thoth_x64.exe"))
    if exe_src:
        src     = exe_src[0]
        patched = src.parent / "thoth_x64_patched.exe"
        shutil.copy2(str(src), str(patched))

        from gad_pickup_patch import apply_gad_pickup_patch, apply_prison_keycard_patch
        from save_path_patch import apply_save_path_patch
        from instant_pickup_patch import apply_instant_pickup_patch
        apply_prison_keycard_patch(str(patched))

        # Always on, unconditional — removes the camera-lock/DOF-blur/player
        # freeze/reach-animation/inventory-popup sequence that every pickup
        # (not just AP's Book-of-Shadows foreign-item marker) triggers via
        # FUN_140446500, plus the finalize-side camera release-control call
        # that was left unmatched once the lock was removed (was causing a
        # visible camera reframe/"snap back" — fixed 2026-07-24). See
        # instant_pickup_patch.py's module docstring for the full patch
        # breakdown and CLAUDE.md for the investigation history. Applies to
        # every seed; there's no meaningful "keep the slow version" use case
        # for an AP seed with hundreds of checks.
        apply_instant_pickup_patch(str(patched))

        # Always on, unconditional — randomizer/AP saves must never share
        # slots with vanilla saves. Only the patched exe is affected (see
        # save_path_patch.py's module docstring for why this beats a
        # filesystem-level redirect); launching vanilla thoth_x64.exe still
        # reads/writes the original folder untouched, no player action
        # required either way.
        #
        # leaf="ap/" (NOT the module's own "rando/" default) — this file is
        # the ported copy of the AP world's patcher.py, driven by
        # apply_ap_seed.py for Archipelago seeds, so it must redirect to the
        # same "ap" folder the AP world's own save_path_patch.py uses.
        # Bug fixed 2026-07-20: this call was passing no leaf at all, so it
        # silently fell through to the local module's "rando/" default,
        # meaning AP-applied seeds' saves landed in the wrong folder
        # (invisible to client.py's soul-sync/save-replay logic, which only
        # checks "ap" before falling back to vanilla "saves").
        apply_save_path_patch(str(patched), leaf="ap/")

        apply_gad_pickup_patch(str(patched), shuffle_temples=True)
        print(f"\nEXE: patches written to {patched.name}")

        # ── Tier 2: cadeaux / health / death penalty / soul threshold ────────
        from cadeaux_patch import apply_cadeau_step_patch, patch_levels_txt_launch_validator
        from health_patch import apply_health_patch
        from death_penalty_patch import apply_death_penalty_patch
        from sprint_patch import apply_sprint_patch

        # DISABLED (2026-07-22 — user report): NOPing the two JNZ checks
        # here made the game launch fine, but opening the in-game inventory
        # menu started raising exceptions/errors. Whatever those two
        # comparisons actually gate turned out not to be limited to "crash
        # if totals are wrong" — there was more going on in that function
        # than the Ghidra read caught, and NOPing a JNZ blind past the crash
        # call also skips whatever real work sits between it and the next
        # confirmed-safe instruction, if any does. Reverting to NOT patching
        # this at all. Instead, the fix moves back to the levels.txt content
        # side (see Step 9.7 below): AP seeds now write the vanilla-safe
        # STRIPPED levels.txt unconditionally, which never rewrites the
        # $darksoul/$cadeaux totals away from vanilla 120/666 in the first
        # place, so the native check simply passes on its own — no EXE
        # patch, no risk from one, at the cost of no per-seed map hints for
        # now. patch_levels_txt_launch_validator() itself is left defined
        # in cadeaux_patch.py in case this needs revisiting with a fuller
        # disassembly of the function.
        # patch_levels_txt_launch_validator(str(patched))

        apply_cadeau_step_patch(str(patched), rng, config)
        apply_health_patch(str(patched), rng, config)

        if config.get("death_penalty", 0):
            apply_death_penalty_patch(str(patched), step=config["death_penalty"])

        if config.get("sprint_multiplier", 0):
            apply_sprint_patch(str(patched), rng, config)

        if sl_thresholds_result is not None:
            apply_soul_threshold_patch(str(patched), sl_thresholds_result)

        # 2026-07-21 (Task 27) — dark_engine_patch.py itself no-ops (prints
        # "unchanged") when piston_combo_table == VANILLA_TABLE, but the
        # config check here skips the call (and its file read/write) entirely
        # for the common off case, matching the standalone's own guard.
        if config.get("piston_combos", "off") != "off":
            apply_dark_engine_patch(str(patched), piston_combo_table)

        # Secret Trap live-apply poller — RE-ENABLED 2026-08-01, root cause
        # of the two ntdll heap-corruption crashes below still UNCONFIRMED.
        #
        # Timeline: wired in as an auto-apply step, then suspended same day
        # after Jon hit two crashes with an identical RIP/backtrace (ntdll
        # heap-validation code, garbage-looking register state) at two
        # unrelated in-game triggers (entering deadside, end of a
        # cutscene) — the signature of delayed heap corruption, not a
        # coincidence. Leading hypothesis at the time: one of the 8
        # non-dogmode SECRET_TABLE addresses was mis-assigned during the
        # original CE capture, feeding a wrong/garbage handle into
        # CALLBACK_VA every frame. Built `/cvarnames` (client.py, pure
        # memory read, zero execution risk) to check this directly — it
        # found exactly ONE mismatch, `g_bigshoemode` vs. the cvar's real
        # in-memory name `g_bigshoesmode` (missing an "s"). That's a
        # cosmetic label typo in OUR OWN Python string, not an addressing
        # bug — the address itself is a real, valid, correctly-positioned
        # cvar handle (proven by reading a plausible cvar-name string back
        # out of it at all). So the leading hypothesis is RULED OUT: all 9
        # poller addresses are confirmed real and correctly assigned. The
        # actual crash cause is still unknown after re-checking the
        # assembly by hand (stack alignment across repeated same-frame
        # calls, register preservation, LAST_KNOWN indexing) with nothing
        # else found wrong.
        #
        # Jon's informed call after this was laid out plainly: re-enable
        # anyway and gather more data rather than keep guessing blind. If
        # a third crash happens, the most useful data to capture is the
        # new crashlog (check whether RIP/backtrace matches the first two
        # exactly again, or differs) plus the client.py log's Secret Trap
        # ON/OFF timestamps in the minutes before it, to start correlating
        # which specific secret(s) were toggling around the crash.
        #
        # Trap/Bonus items work fine either way — this only controls
        # whether the 9 poller-covered secrets apply instantly vs. at the
        # next level load. The other 9 of TRAP_SAFE_SECRETS (null
        # on-change callback) were never affected by any of this.
        #
        # Renamed from secret_trap_count 2026-08-03 when the item grew
        # into 4 categories (secret/health/voodoo/ammo) — also now gated
        # on trap_bonus_secrets_enabled, since there's no point touching
        # the exe for a poller that can never fire if the secrets category
        # itself is turned off. Only the secrets category needs this EXE
        # patch at all — health/voodoo/ammo effects are plain memory
        # writes or CreateRemoteThread calls handled entirely in client.py,
        # nothing in ap_patcher.py's own pipeline.
        if (int(config.get("trap_bonus_count", 0)) > 0
                and config.get("trap_bonus_secrets_enabled", True)):
            import secret_mode_section_patch
            try:
                secret_mode_section_patch.apply_patch(str(patched), dry_run=False)
                print("EXE: Trap/Bonus (secrets) live-apply poller patched in.")
            except Exception as exc:
                print(f"WARNING: Trap/Bonus (secrets) live-apply poller patch failed "
                      f"({exc}) — secret-category Trap/Bonus items will still work via "
                      f"plain write_cvar_bool(), but the 9 poller-covered "
                      f"secrets (g_dogmode etc.) will only visibly apply at "
                      f"the next level transition instead of instantly.")
    else:
        print("\nWARNING: thoth_x64.exe not found - EXE patches skipped")

    # Deadside Guns secret force-on -- NOT an EXE patch: edits kexengine.cfg,
    # a separate per-user config file outside game_dir entirely, so it's
    # unconditional here rather than nested inside the thoth_x64.exe-gated
    # block above (mirrors the standalone patcher.py's own placement).
    if config.get("deadside_guns", False):
        from kexengine_cfg_patch import apply_deadside_guns_toggle
        apply_deadside_guns_toggle(True)

    # ── Step 8: Gate remap JSON ───────────────────────────────────────────────

    threshold_json = out_path / "soul_thresholds.json"
    with open(threshold_json, "w") as f:
        json.dump({
            "seed": seed,
            "vanilla_sl_thresholds": VANILLA_SL_THRESHOLDS,
            "gate_remap": {g: sl for g, sl in gate_remap.items()},
            "effective_thresholds": {
                # NOTE (updated 2026-07-24): always computed from
                # VANILLA_SL_THRESHOLDS regardless of soul_threshold_mode --
                # that USED TO reflect a real gap in AP's access_rules.py
                # (which assumed vanilla thresholds no matter what), but that
                # gap was fixed 2026-07-20/21 (self.sl_thresholds now threads
                # through the whole rule chain -- see access_rules.py's
                # _SOUL_THRESHOLDS comment block). This field is now purely
                # informational (gate->SL mapping under GATE-VANILLA SL
                # values, i.e. ignoring soul_threshold_mode on purpose) --
                # see "in_game_sl_thresholds" below for the mapping AP's
                # logic graph (and the patched EXE) actually use when
                # soul_threshold_mode is active.
                gate_id: VANILLA_SL_THRESHOLDS[gate_remap.get(gate_id, GATE_VANILLA_SL[gate_id])]
                for gate_id in GATE_VANILLA_SL
            },
            "soul_threshold_mode": _st_mode,
            "in_game_sl_thresholds": sl_thresholds_result,
        }, f, indent=2)
    print(f"\nSoul thresholds: {threshold_json}")

    # ── Step 9: ARC deco patch ────────────────────────────────────────────────

    if gates_changed:
        n_arc = patch_gate_arc_decos(levels_path, gate_remap)
        if n_arc == 0:
            print("  Gate decos: no changes from vanilla")

    # ── Step 9.5: SFX + music shuffle ────────────────────────────────────────

    music_files  = {}
    sfx_files    = {}
    sfx_swap_log = {}
    sky_files    = {}

    if config.get("shuffle_music", False) and using_kpf:
        from randomizers.music_randomizer import shuffle_music
        music_files = shuffle_music(rng, kpf_files, str(work_path))

    if (config.get("shuffle_voices", False) or config.get("shuffle_weapons_sfx", False)
            or config.get("shuffle_enemies_sfx", False)) and using_kpf:
        from randomizers.sfx_randomizer import shuffle_sfx
        sfx_files, sfx_swap_log = shuffle_sfx(
            rng, kpf_files, str(work_path),
            shuffle_voices=config.get("shuffle_voices", False),
            shuffle_weapons=config.get("shuffle_weapons_sfx", False),
            shuffle_enemies=config.get("shuffle_enemies_sfx", False),
        )

    if sfx_files:
        from randomizers.sfx_randomizer import sfx_spoiler_section
        sfx_lines = sfx_spoiler_section(sfx_swap_log)
        with open(str(spoiler_path), "a", encoding="utf-8") as f:
            f.write("\n" + "\n".join(sfx_lines))

    if config.get("shuffle_sky", False) and using_kpf:
        from randomizers.sky_randomizer import shuffle_sky, sky_spoiler_section
        sky_files = shuffle_sky(rng, kpf_files, str(work_path))
        if sky_files:
            with open(str(spoiler_path), "a", encoding="utf-8") as f:
                f.write("\n" + "\n".join(sky_spoiler_section(sky_files)))

    # ── Step 9.6: Asset / MSH overrides ───────────────────────────────────────
    # Ported from the standalone's patcher.py Step 9.7 (2026-07-20). Numbered
    # 9.6 here (not 9.7) to avoid colliding with this file's own pre-existing
    # Step 9.7 below (levels.txt tracker). Textures (ASSET_OVERRIDES, plus
    # GAD_ASSET_OVERRIDES -- gad temples are always shuffled now, see
    # options.py's ShadowManOptions comment) and meshes
    # (MSH_OVERRIDES — the crate→pot swap insanity/AP-marker cadeaux and
    # barrel placements rely on, see Step 6.5) get packed into the mod KPF.
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

    for src_rel, dst_rel in GAD_ASSET_OVERRIDES:
        src = randomizer_dir / src_rel
        if not src.exists():
            print(f"  WARNING: Gad asset override missing — {src_rel}")
            continue
        internal = dst_rel.replace("\\", "/")
        asset_mod_files[internal] = str(src)
        print(f"  [asset] {src_rel} → {internal}")

    # AP marker icon — always applied here (every ap_patcher.py run is an AP
    # seed), unlike GAD_ASSET_OVERRIDES above which is gated on Gad shuffle.
    for src_rel, dst_rel in AP_ASSET_OVERRIDES:
        src = randomizer_dir / src_rel
        if not src.exists():
            print(f"  WARNING: AP marker asset override missing — {src_rel}")
            continue
        internal = dst_rel.replace("\\", "/")
        asset_mod_files[internal] = str(src)
        print(f"  [asset] {src_rel} → {internal}")

    msh_mod_files = {}
    if using_kpf:
        # build_kpf_index already imported at the top of run_patcher()
        msh_mod_files = apply_msh_overrides(randomizer_dir, str(work_path), kpf_index=build_kpf_index(kpf_files))

    # ── Step 9.7: levels.txt tracker + cadeaux counts ─────────────────────────
    # Ported from the standalone's Step 4b.6/4c (2026-07-19).
    #
    # ALWAYS uses strip_levels_txt() — never patch_levels_txt(). The
    # placement-accurate hints mode (patch_levels_txt(), formerly gated on
    # a "patch_tracker" option) rewrites the file's $darksoul/$cadeaux
    # directives to match this seed's real AP placement, which almost
    # never sums back to the vanilla 120/666 the game's launch-time
    # validator hard-requires — items can be sent to/received from other
    # players' games in a multiworld, so a slot's own local totals
    # essentially never match vanilla. That was previously worked around
    # by NOPing the validator's crash check directly in the EXE, but that
    # turned out to have side effects (inventory menu exceptions) beyond
    # what was understood at the time (2026-07-22).
    #
    # The "patch_tracker" option itself was removed from the AP world
    # (options.py) and ap_gui.py entirely on 2026-08-05 (per Jon) since it
    # was already a no-op here — this comment is kept to explain why AP
    # seeds unconditionally get the safe, vanilla-totals-preserving
    # stripped hints, not a decision this file makes based on any config
    # value anymore. patch_levels_txt() itself is untouched and still
    # called by the standalone's own patcher.py, which doesn't have this
    # problem (a single-player seed's totals always still sum to vanilla).
    from patchers.levels_txt_patcher import strip_levels_txt

    # Ground-truth XYZ scan for $retractor/$accumulator coord suffixes —
    # scans the ALREADY-PATCHED RSC files so directives exactly match the
    # binary (standalone Step 4b.6).
    _RETRACT_NAMES = frozenset({"RSC_X_RETRACT", "RSC_X_RETRACT1", "RSC_X_RETRACT2"})
    _ACCUM_NAMES   = frozenset({"RSC_X_ACCUMULATOR"})
    retractor_actual_xyz: dict = {}
    for _rsc_path in sorted(levels_path.rglob("*.rsc")):
        _level_id = _rsc_path.parent.name
        _data = _rsc_path.read_bytes()
        _n = (len(_data) - HEADER_SIZE) // RECORD_SIZE
        for _i in range(_n):
            _base = HEADER_SIZE + _i * RECORD_SIZE
            _name = _data[_base + NAME_OFF: _base + NAME_OFF + 30].split(b'\x00')[0]
            try:
                _name_str = _name.decode("ascii")
            except UnicodeDecodeError:
                continue
            if _name_str in _RETRACT_NAMES or _name_str in _ACCUM_NAMES:
                _x, _y, _z = struct.unpack_from("<fff", _data, _base + XYZ_OFF)
                _zone = _data[_base + ZONE_OFF]
                _dtype = "accumulator" if _name_str in _ACCUM_NAMES else "retractor"
                retractor_actual_xyz.setdefault(_level_id, {}).setdefault(_dtype, []).append(
                    (_x, _y, _z, int(_zone))
                )

    _scripts_dir = work_path / "scripts"
    _scripts_dir.mkdir(parents=True, exist_ok=True)
    _levels_vanilla  = _scripts_dir / "levels_vanilla.txt"
    _levels_stripped = _scripts_dir / "levels_stripped.txt"
    _levels_hints    = _scripts_dir / "levels_hints.txt"
    _levels_active   = _scripts_dir / "levels.txt"

    _levels_txt_src = work_path / "scripts" / "levels.txt"
    if not _levels_txt_src.exists():
        _levels_txt_src = work_path / "levels" / "levels.txt"
    if _levels_txt_src.exists() and not _levels_vanilla.exists():
        shutil.copy2(_levels_txt_src, _levels_vanilla)

    if _levels_vanilla.exists():
        print("\nWeaving the oracle scrolls...")
        try:
            strip_levels_txt(_levels_vanilla, _levels_stripped)
            shutil.copy2(_levels_stripped, _levels_active)
            print("  Oracle mode: secrets kept (levels_stripped.txt) — "
                  "accurate per-seed hints disabled for AP seeds, see "
                  "Step 9.7's comment above")
        except Exception as _ltxt_exc:
            print(f"  [levels_txt] WARNING: patch failed ({_ltxt_exc}) — "
                  f"levels.txt left as-is; tracker may show vanilla hints.")
    else:
        print("\n  [levels_txt] WARNING: levels_vanilla.txt not found — tracker not patched")

    # ── Step 9.7b: loc_english.txt — "Book of Archipelago" rename ────────────
    # AP's foreign-item marker retypes the physical pickup to the vanilla
    # RSC_X_BOOK_OF_SHADOWS resource (see write_placement_patches() / Step
    # 6.5 above) — the RSC type string itself can't be renamed without
    # engine-level changes (it's a real native item id), but the in-game
    # inventory/pickup display text is just a loc_english.txt string and IS
    # safe to rename. Ported from patcher.py's Step 4d vanilla-extraction
    # pattern (2026-07-20); unlike that step this override is unconditional
    # (not gated on any option) since every ap_patcher.py run is an AP
    # seed and the marker should always read "Book of Archipelago", never
    # the vanilla "Book of Shadows" name.
    from patchers.loc_english_patcher import patch_loc_english

    _loc_dir      = work_path / "localization"
    _loc_dir.mkdir(parents=True, exist_ok=True)
    _leng_vanilla = _loc_dir / "loc_english_vanilla.txt"
    _leng_active  = _loc_dir / "loc_english.txt"

    if not _leng_vanilla.exists() and using_kpf:
        from kpf_handler import find_file_in_kpf, extract_file_from_kpf
        try:
            _leng_matches = find_file_in_kpf(kpf_index, "localization/loc_english.txt")
            if _leng_matches:
                extract_file_from_kpf(
                    str(Path(kpf_index.kpf_dir) / _leng_matches[0][1]),
                    _leng_matches[0][0],
                    str(_leng_vanilla),
                )
            else:
                print("\n  [loc_english] WARNING: loc_english.txt not found in KPF — AP marker rename skipped")
        except Exception as _e:
            print(f"\n  [loc_english] WARNING: extraction failed: {_e}")

    loc_english_files = {}
    if _leng_vanilla.exists():
        print("\nRenaming the Book of Shadows marker to Book of Archipelago...")
        replaced, _ = patch_loc_english(
            str(_leng_vanilla), str(_leng_active),
            overrides={"i_book_of_shadows": "Book of Archipelago"},
        )
        print(f"  [loc_english] {replaced} key(s) renamed")
        if using_kpf:
            from kpf_handler import find_file_in_kpf as _find_leng
            _leng_kpf_matches = _find_leng(kpf_index, "localization/loc_english.txt")
            _leng_internal = _leng_kpf_matches[0][0] if _leng_kpf_matches else "localization/loc_english.txt"
            loc_english_files[_leng_internal] = str(_leng_active)

    # ── Step 9.8: Unified entrance shuffle (.cut patches) ─────────────────────
    # Ported from the standalone's patcher.py Step 4e (2026-07-21, Task 19).
    # Rewrites ExitLevelPos() calls in the extracted cutscene .cut files so
    # the 9 Deadside portals lead to whichever spoke entrance_shuffle
    # assigned them (region graph side was handled back in Tasks 15-18 —
    # this is the physical/cutscene side that makes the game itself route
    # the player to match). No-op when entrance_shuffle is None (mode off).
    entrance_cut_files = apply_entrance_shuffle_patches(
        entrance_shuffle, work_path, levels_path, using_kpf,
        kpf_index=kpf_index if using_kpf else None,
    )
    if entrance_shuffle:
        _shuffle_for_spoiler = build_deadside_unified_shuffle(entrance_shuffle)
        with open(str(spoiler_path), "a", encoding="utf-8") as f:
            f.write("\n\n" + unified_spoiler_section(_shuffle_for_spoiler))

    # ── Step 9.9: Piston combo journal patch (2026-07-21, Task 27) ───────────
    # Ported from the standalone's patcher.py journal-patch call (its Step
    # 9.8, this repo's numbering just happens to already have an unrelated
    # 9.8 above — see comment there). Rewrites journal/11.MUp's 6 STRING
    # combination values to match the EXE table written in Step 7 above, so
    # Jack's Schematic actually shows the right numbers in-game. KPF-only
    # (the journal page is a packed asset, not a loose game file) and only
    # runs when piston_combos is on — extract_and_patch_journal() itself
    # rebuilds its own kpf_index from kpf_files (matching the standalone's
    # exact usage; not worth threading the already-built one through since
    # this function is stdlib-only and self-contained by design).
    piston_journal_files: dict[str, str] = {}
    if using_kpf and config.get("piston_combos", "off") != "off":
        mup_local = extract_and_patch_journal(kpf_files, piston_combo_table, str(work_path))
        if mup_local:
            from kpf_handler import find_file_in_kpf as _find_mup
            _mup_matches  = _find_mup(kpf_index, JOURNAL_MUP_PATH)
            _mup_internal = _mup_matches[0][0] if _mup_matches else JOURNAL_MUP_PATH
            piston_journal_files[_mup_internal] = mup_local

    # ── Step 10: Repack KPF ───────────────────────────────────────────────────

    if using_kpf:
        repack_after_patch(
            str(game_path), patches_by_folder, gate_remap,
            config, str(spoiler_path), str(work_path), seed,
            extra_mod_files={**music_files, **sfx_files, **sky_files,
                              **asset_mod_files, **msh_mod_files, **loc_english_files,
                              **entrance_cut_files, **piston_journal_files},
        )

    validate_final_seed(str(work_path), progression_placement, patches_by_folder)
    print(f"\nDone! Seed {seed} applied.")
    print(f"Spoiler log: {spoiler_path}")
