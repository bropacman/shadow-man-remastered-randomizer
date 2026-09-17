"""
client.py
─────────
Archipelago client for Shadow Man Remastered.

TWO-WAY COMMUNICATION
─────────────────────
  Game → AP  (detection) — TWO parallel watchers, same standard AP client
  shape as SNIClient/BizHawkClient's game_watcher-on-an-interval:

    _save_watcher_loop (save-file polling):
      Watches the Steam Cloud save folder for KSAV file changes.
      Parses ``kexShadowManAIGovi`` records to detect govi shell openings.
      Confirms actual soul collection via the SACT (user_activity.dat)
      counter. Parses ``kexShadowManQuestObject`` records (weapons, lore,
      progression, cadeaux) via their reliable instance_id. Source of
      truth for level identification and QuestObject slot resolution.

    _memory_watcher_loop (live process-memory polling):
      Heap-scans the live game process for kexShadowManDarkSoul and
      kexShadowManQuestObject instances (see the DARKSOUL_VTABLE_RVA /
      QUEST_THINKFN_RVA comment block below). Fully resolves Govi checks
      live (position-matched against locations.csv, no save-file wait
      needed). For QuestObject, only detects that SOME pickup happened
      and nudges an immediate save-file re-poll — no reliable live
      per-slot identity was found for that class (see comment block for
      why). Runs alongside the save watcher, not instead of it; both
      paths dedupe against self.locations_checked, so either can win the
      race safely.

  AP → Game  (injection):
    Receives items from the AP server and injects them into the live process
    using pymem + x64 shellcode / direct memory writes.

MEMORY MAP  (all offsets from thoth_x64.exe base, confirmed via CE + Ghidra)
─────────────────────────────────────────────────────────────────────────────
  +0xF9BEC8   Dark Soul count         int32   (0–120)
  +0xF9C1A0   Gad level tier          int32   (0 = none, 1–4 = which power)
  +0xF9C1A4   Poigne flag             byte    (0 = no, 1 = yes)
  +0xF9C1A5   Gad power 1 flag        byte
  +0xF9C1A6   Gad power 2 flag        byte
  +0xF9C1A7   Gad power 3 flag        byte
  +0xF9C1A8   Gad power 4 flag        byte
  +0xF9C380   kexShadowManInventoryLocal object
                vtable[0x30] = GiveItem(this, item_id_u16, flags_i32)

INJECTION METHODS
─────────────────
  Dark Soul:       pm.write_int(base + 0xF9BEC8, count + 1)
  Poigne / keys:   shellcode → vtable[0x30] on inventory object
  Gad Power:       direct byte write to +0xF9C1A5 … +0xF9C1A8,
                   then int32 write to +0xF9C1A0 (tier)

SAVE FILE FORMAT NOTES
──────────────────────
  KSAV:   Kex Engine binary save.  Magic ``KSAV\\x00`` at offset 0.

          kexShadowManAIGovi records (RSC_X_GOVI dark souls):
            state byte   at class-name-start + 0x9D
              0 = govi intact  |  1 = govi shell opened / broken
            position (x, y, z float32 LE) — NOT at a fixed offset; see below.

            IMPORTANT: unlike QuestObject, there is NO usable instance_id
            for Govi records. class-name-start − 8 (the QuestObject pattern,
            assumed to apply here too by analogy) was proven wrong live —
            real dark-soul pickups produced values (745, 561) matching
            nothing in the CSV database for any level.

            Root cause found via tools/diff_save_snapshot.py on a real
            before/after save pair: the field at that offset isn't a
            per-object ID at all. The record's actual stable identity is
            its (x, y, z) world position, confirmed to match
            data/locations.csv coordinates to full float32 precision.
            Location identity is therefore resolved by position-matching
            against EVERY row of locations.csv (all categories, not just
            "soul") — confirmed necessary via tools/diagnose_govi_position_offset.py
            against a real seed's save: govi records matched CSV rows whose
            category was "barrel" and "cadeaux", not "soul", proving the
            patcher (shared by the standalone randomizer and this AP
            world's generate_output()) really does retype ANY location slot
            into a govi depending on fill result — a location's category in
            the CSV describes its vanilla default, not what's actually
            there in a given randomized seed.

            The position field's BYTE OFFSET relative to the class name is
            NOT constant across records, though — a fixed offset (-96,
            confirmed against a swampday save) produced garbage
            (denormalized floats, huge magnitudes) for most records in a
            t1tchgad save. Almost certainly a variable-length field (e.g.
            an embedded per-object name string) sits between the position
            and the class name, shifting every later field's offset by a
            per-object amount. Fix: scan a window of candidate offsets
            around each occurrence at runtime and pick whichever one
            decodes to a position matching a known CSV location — see
            GOVI_POS_SCAN_MIN/MAX and _match_govi_position_scan below.

          kexShadowManQuestObject records (all quest.rsc item pickups:
            progression, weapons, lore, cadeaux containers):
            instance_id  at class-name-start − 8  (uint32 LE)
            state byte   at class-name-start + 0x32
              0 = not yet collected  |  1 = collected
            instance_id matches patcher.py INSTANCE_OFF byte:
              rsc_data[name_offset − 1]  in the level's quest.rsc
            Confirmed via XYZ matching: deadside Book of Shadows (inst 76),
              Book of Prophecy (inst 159), Eclipser ×3 (inst 169/190/206).
            Run tools/extract_instance_ids.py to populate all instance_ids.

          Current level identified by counting ``levels/<folder>/``
          substrings — the most-frequent folder is the active level.

  SACT:   Companion activity file (user_activity.dat).  Magic ``SACT`` at 0.
          Dark Soul count stored as uint32 LE at offset 0x31.

  SIG:    40-byte ASCII SHA1 of the corresponding .sav file.
          Re-computed after any save modification: sha1(sav_bytes).hexdigest()
"""

from __future__ import annotations

import asyncio
import multiprocessing
import ctypes
import hashlib
import json
import logging
import os
import random
import select
import socket
import struct
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import Utils

# Universal Tracker "Tracker Page" integration (2026-07-25) — see
# docs/client-integration.md in the tracker.apworld package. If UT is
# installed, subclass its context/command-processor instead of the plain
# CommonClient ones so this client gets UT's tracker tab (location count,
# in-logic, hinted, Go Mode) for free, computed from this world's own
# access_rules.py/regions.py. Falls back to vanilla CommonClient if UT
# isn't installed, so this client works identically either way.
tracker_loaded = False
try:
    from worlds.tracker.TrackerClient import TrackerGameContext as SuperContext
    from worlds.tracker.TrackerClient import TrackerCommandProcessor as SuperCommandProcessor
    tracker_loaded = True
except ModuleNotFoundError:
    from CommonClient import CommonContext as SuperContext
    from CommonClient import ClientCommandProcessor as SuperCommandProcessor

from CommonClient import (
    get_base_parser,
    gui_enabled,
    logger,
    server_loop,
)
from NetUtils import ClientStatus

# Friendly location display names, straight from this world's own generation
# code (2026-07-26) — see .locations' FRIENDLY_NAMES docstring. Used by the
# GUI tab's per-level location browser INSTEAD OF self.location_names'
# network datapackage lookup: the datapackage is fetched once per server
# connection and can be served from a locally-cached copy keyed only by a
# checksum (standard AP client behavior), so during active shadowman
# development — where this world's code changes far more often than
# whatever triggers that checksum to roll over — the client can end up
# showing a STALE cached name (observed live: raw loc_key hex like
# "ah1cagew:quest.rsc:0x0D62" instead of "Asylum: Cageways - Cadeaux 3",
# even though a fresh generation run embeds the friendly name correctly and
# Universal Tracker's own Tracker Page tab — which re-derives names from a
# local regen rather than the cached network datapackage — showed the
# friendly name for the exact same location). Importing FRIENDLY_NAMES
# directly guarantees the tab always matches whatever this world's code
# currently computes, sidestepping datapackage-cache staleness entirely.
# Safe: .locations only imports .fill/.access_rules/.constants/
# .extracted_locations, none of which import client.py (only __init__.py's
# launch_client() does, and only lazily inside the function body) — no
# circular import.
from .locations import FRIENDLY_NAMES

# Cadeaux Bundle Size (2026-07-27): a bundle representative's received item
# name encodes how many physical cadeaux it's worth ("Cadeaux Bundle x{N}"
# vs. plain "Cadeaux" == 1) — see items.py's cadeaux_bundle_item_name()/
# cadeaux_item_weight() docstrings for the full cross-world rationale.
# .items only imports BaseClasses, same "no circular import" safety as
# .locations above.
from .items import is_cadeaux_item, cadeaux_item_weight, RETRACTOR_KEY_ITEM_NAMES

# pymem logs "Process X is being debugged" at INFO on every attach. With
# watcher loops probing every second this floods the client GUI's log pane
# (thousands of lines → Kivy lag) — keep only warnings and above.
logging.getLogger("pymem").setLevel(logging.WARNING)

# ── AP constants ───────────────────────────────────────────────────────────────

GAME_NAME      = "Shadow Man Remastered"
POLL_INTERVAL  = 1.0

# Dedicated fast-poll interval for the low-latency live signals that don't
# need a heap scan or file I/O (2026-08-03, see _fast_watcher_loop) --
# deliberately much shorter than POLL_INTERVAL so a pickup right next to a
# level-transition door can't race a level change ahead of detection.
# Originally ITEM_PICKUP_COUNTER_RVA only; extended 2026-08-23 (Jon: "the
# counters go up right away on getting [a dark soul] so figured we could
# have the AP location sweep check at the same time") to also cover the
# dark soul flag array / live soul counter, then the same day to also
# cover the Light Soul completion flag (Jon: "i think the light soul
# needs that instant poll check too") -- all are direct fixed-size memory
# reads or plain flag-bit reads, same "no heap scan, no file I/O" cost
# class as the pickup counter, so folding them into this loop costs
# nothing extra per tick.
FAST_POLL_INTERVAL = 0.15
NUM_SAVE_SLOTS = 7

# Minimum real wall-clock seconds since connecting before a Secret Trap is
# ever allowed to apply (2026-08-01, see _inject_item's "secret_trap"
# branch) -- also reused as the same "genuinely live vs. still catching
# up from a reconnect" gate for the "gad"/"poigne_ability"/"soul" apply_now
# decisions (2026-08-02). Originally 20.0 -- a deliberately generous
# margin so a large connect-time backlog, even one delivered across
# several delayed network packets, has plenty of room to fully arrive
# before this ever opens up. Lowered to 5.0 (2026-08-02, Jon's explicit
# call, to get faster mid-level live-apply testing turnaround for the
# just-fixed gad player_ptr bug) -- narrows that safety margin for
# unusually large/slow-arriving backlogs (see the original 20s comment
# above), trading some of that margin for a shorter wait before a
# genuinely-live item's apply_now path (Secret Trap, gad, poigne, soul)
# actually fires. Worth revisiting upward again if a backlog-timing race
# (a Secret Trap or gad/poigne item applying instantly when it should
# have been treated as still-arriving backlog) is ever observed.
SECRET_TRAP_MIN_SECONDS_SINCE_CONNECT = 5.0

# Pacing gap between successive items in _item_inject_loop (2026-08-01) --
# see that loop's own comment. Several item types (give_item, cadeaux) call
# into engine code via CreateRemoteThread (inject_give_item), and a large
# connect-time backlog can fire many of these back to back with no real
# time between them, a load pattern normal solo play never produces.
#
# Widened 0.1 -> 0.5 (2026-08-01, same day): 0.1s didn't stop a repeatable
# ntdll heap-corruption crash at the end of a large backlog replay (see
# CLAUDE.md's crash-signature writeups) -- confirmed via Application
# Verifier / page heap (Jon's live test) to be genuine heap corruption,
# not a false alarm. At the time, pinning the exact instruction responsible
# would have needed a full WinDbg session; instead this was widened as a
# practical mitigation, not a proven fix.
#
# Reverted 0.5 -> 0.1 (2026-08-02): the actual root cause was later found
# and fixed elsewhere entirely -- ap_patcher.py's Step 7 could reapply the
# secret-mode EXE poller patch onto an already-patched thoth_x64_patched.exe
# (a stale work dir / repeated --apply run), silently producing a
# structurally invalid PE (two sections claiming the same virtual address).
# Fixed with a hard-abort in secret_mode_section_patch.py (see CLAUDE.md's
# "reapplying the patch to an already-patched exe silently corrupts it"
# writeup). A full clean test session afterward (fresh seed, fresh patch,
# fresh client.py) produced ZERO crashes, including across a save/close/
# reload cycle -- strong evidence the reapply corruption was the real
# culprit all along, not backlog-injection pacing. Since checks are
# server-tracked either way (a crash never loses AP progress), and Jon
# asked directly whether the slowdown is still needed now that fast
# backlogs have tested clean, reverted to the pre-mitigation value.
#
# Kept as a plain module global (not a hardcoded literal at each call
# site) specifically so `/pacing <seconds>` (ShadowManCommandProcessor)
# can rebind it live for future troubleshooting without a code edit +
# restart -- if the ntdll signature or anything like it ever recurs,
# `/pacing 0.5` (or higher) puts the mitigation straight back in place
# for the rest of that session.
ITEM_INJECT_PACING_SECONDS = 0.1

# patcher.py's Step 7 always writes EXE-level patches (gad pickup dispatch,
# prison keycard fix, health/cadeaux/death-penalty/soul-threshold — see
# patcher.py) to a SEPARATE file, thoth_x64_patched.exe, sitting next to the
# original. It never renames/replaces the original, and nothing currently
# makes Steam launch the patched copy by default — the player has to launch
# thoth_x64_patched.exe themselves. The client therefore targets ONLY the
# patched name: live-memory tracking, item injection, and DeathLink kill
# injection all assume patcher.py's code caves exist, and running against
# the plain vanilla exe would silently skip regions that aren't there.
# VANILLA_EXE_NAME is only used to detect "wrong exe running" and print one
# helpful warning instead of failing silently (see _get_process()).
PATCHED_EXE_NAME = "thoth_x64_patched.exe"
VANILLA_EXE_NAME  = "thoth_x64.exe"

# ── In-game overlay (popup toasts) ─────────────────────────────────────────────
# See overlay_dll/README.md. client.py stays the thing that actually talks to
# the AP server; the DLL only renders whatever we tell it to over a local
# loopback socket. OVERLAY_IPC_PORT must match kIpcPort in dllmain.cpp.
OVERLAY_DLL_NAME  = "ShadowManOverlay.dll"


def _resolve_overlay_dll_path() -> str:
    """
    Real, on-disk path to ShadowManOverlay.dll for _inject_overlay_dll()'s
    LoadLibraryW-style injection -- which needs an actual file on disk no
    matter what, since a DLL sitting inside a zip can't be loaded directly.

    Path(__file__).parent works fine when this module is a plain file in a
    real folder (every dev run so far -- straight out of worlds/shadowman/),
    but breaks once this world ships as a packaged .apworld: Archipelago
    loads those via zipimport, so __file__ resolves to a virtual path like
    ...\\custom_worlds\\shadowman.apworld\\client.py with no real directory
    for .parent to point at. Found 2026-08-15 testing the packaged .apworld
    for the first time (as opposed to the flat dev folder) -- the DLL was
    never missing, os.path.exists() on that virtual sibling path was just
    always False, so _inject_overlay_dll() took its documented soft-failure
    path (log + skip) every single time, silently.

    Fix: try the flat-folder path first (unchanged behavior for normal dev
    runs), and if that's not a real file, fall back to importlib.resources
    -- which transparently handles both a plain directory package and a
    zipimported one -- to read the DLL's bytes out of the package and write
    them to a stable, content-hashed temp file. Hashing the extracted
    filename means a rebuilt DLL with different bytes naturally gets a new
    path instead of silently reusing a stale extraction, and repeat runs
    with an unchanged DLL skip the rewrite entirely.
    """
    flat_path = Path(__file__).parent / OVERLAY_DLL_NAME
    if flat_path.exists():
        return str(flat_path)

    try:
        import importlib.resources as _resources
        data = _resources.files(__package__).joinpath(OVERLAY_DLL_NAME).read_bytes()
    except Exception:
        # Let _inject_overlay_dll()'s own os.path.exists() check take over --
        # same soft-failure log-and-skip behavior as before this fix existed.
        return str(flat_path)

    import tempfile
    digest = hashlib.sha1(data).hexdigest()[:12]
    extracted = Path(tempfile.gettempdir()) / f"ShadowManOverlay_{digest}.dll"
    if not extracted.exists():
        extracted.write_bytes(data)
    return str(extracted)


OVERLAY_DLL_PATH  = _resolve_overlay_dll_path()
OVERLAY_IPC_PORT  = 31727

# ── Memory addresses (RVA from exe base) ──────────────────────────────────────

SOUL_COUNT_RVA    = 0xF9BEC8   # int32,  dark soul count 0–120
GAD_LEVEL_RVA     = 0xF9C1A0   # int32,  overall gad tier 0–4

# Live running cadeaux total (player-wide count used for altar cost /
# Fogometers door thresholds), confirmed 2026-07-21. NOT the same as the
# giveitem-array id 0x05 slot (140F9C380+0x1D bit 29, see
# data/inventory_item_offsets.csv row 5) — that slot is only a one-time
# "ever collected a cadeaux" boolean (confirmed by CSV notes: it never
# updates on repeat pickups) and cannot represent a multi-hundred running
# count on its own. AP_ITEM_INJECTION's "Cadeaux" entry now does BOTH on
# every cross-player receive: GiveItem(0x05) still fires to flip that
# "ever collected" flag (needed for the in-game cadeaux HUD/inventory tab
# to unlock at all — a player who only ever receives cadeaux via AP, never
# physically picking one up locally, would otherwise never trip it), and
# inject_cadeaux() writes the real running total here. The two don't
# double-count each other — GiveItem(0x05) doesn't touch this DWORD.
CADEAUX_COUNT_RVA = 0xDB221C   # int32,  live cadeaux count 0–666

# Voodoo Power (2026-08-03) — found by Jon via Cheat Engine, sitting inside
# the exact same soul_obj/meter cluster as MAX_HEALTH_RVA/CURRENT_HEALTH_RVA
# (0xDB21FC/0xDB2200) and CADEAUX_COUNT_RVA (0xDB221C) above — this object is
# evidently a general HUD/stats struct, not health-specific. Confirmed live
# by Jon: unlike health, a PLAIN raw write here is honored both in the HUD
# meter AND by actual Asson/voodoo weapon gameplay (can you cast) — no
# ModifyStat/CreateRemoteThread call needed at all, same low-risk category
# as set_dark_soul_count/set_cadeaux_count below. The in-game dev console
# has its own "givevoodoo" cheat command that fills this to max — confirms
# it's a real, intentionally fillable/drainable resource, but that console
# command should NOT be called programmatically via send_console_command
# (see FUN_1401b0950's history elsewhere in this file — that path crashed
# the game and once froze Jon's whole computer; deliberately shelved).
# Max confirmed by Jon (2026-08-03): voodoo power's cap IS the live Soul
# Level meter value — i.e. base + SOUL_LEVEL_METER_RVA (0xDB2208, already
# defined below as "current soul level * 1000" — see _sync_soul_level).
# No separate/fixed voodoo max exists; it scales with whatever SL tier the
# player has reached (his own CE capture read 6000 = SL6). This also means
# the cap can legitimately change mid-session (gaining a Dark Soul that
# bumps SL upward) — the hold effect re-reads it fresh every tick rather
# than caching a value from when the effect started.
VOODOO_POWER_RVA  = 0xDB2210   # int32,  live voodoo (Asson) power

# Ammo counts (2026-08-03) — found by Jon, same soul_obj/meter cluster.
# Full struct layout now known from +0x1C through +0x40:
#   +0x1C (0xDB21FC) MAX_HEALTH_RVA
#   +0x20 (0xDB2200) CURRENT_HEALTH_RVA
#   +0x28 (0xDB2208) SOUL_LEVEL_METER_RVA
#   +0x30 (0xDB2210) VOODOO_POWER_RVA
#   +0x34 (0xDB2214) SHOTGUN_AMMO_RVA   ("Shotgun Ammo")
#   +0x38 (0xDB2218) VIOLATOR_AMMO_RVA  ("Violator Ammo Count")
#   +0x3C (0xDB221C) CADEAUX_COUNT_RVA  (already defined above)
#   +0x40 (0xDB2220) NINE_MM_AMMO_RVA   ("9mm ammo")
# Max values below are Jon's own reference capture (he set each to its real
# max and reported the resulting number back) — fixed vanilla constants,
# not a per-seed-randomized option anywhere in this codebase. Same
# plain-write mechanism as voodoo power (no ModifyStat/CreateRemoteThread)
# — per Jon: "we can do it similar to voodoo's traps ... we dont have to
# do it by ammo type ... these effects can apply to all ammo types for
# these 3", i.e. one Ammo Drain / Ammo Max Hold pair that touches all
# three at once rather than per-weapon traps.
SHOTGUN_AMMO_RVA  = 0xDB2214
SHOTGUN_AMMO_MAX  = 99
VIOLATOR_AMMO_RVA = 0xDB2218
VIOLATOR_AMMO_MAX = 999
NINE_MM_AMMO_RVA  = 0xDB2220
NINE_MM_AMMO_MAX  = 500

# (name -> (rva, max)) for the three ammo pools this pair of traps covers.
AMMO_RVAS_AND_CAPS: Dict[str, Tuple[int, int]] = {
    "Shotgun":  (SHOTGUN_AMMO_RVA, SHOTGUN_AMMO_MAX),
    "Violator": (VIOLATOR_AMMO_RVA, VIOLATOR_AMMO_MAX),
    "9mm":      (NINE_MM_AMMO_RVA, NINE_MM_AMMO_MAX),
}

# Vanilla SL->souls mapping (mirrors soul_threshold_patch.py's
# VANILLA_SOUL_THRESHOLDS — duplicated rather than imported so client.py
# stays runnable standalone without pulling in the patcher package). Used as
# the fallback until slot_data's soul_thresholds arrives on Connected, and
# as the reference for logging when a seed's thresholds differ from vanilla.
VANILLA_SOUL_THRESHOLDS: Dict[int, int] = {
    0: 0, 1: 1, 2: 3, 3: 7, 4: 15, 5: 23,
    6: 35, 7: 51, 8: 71, 9: 95, 10: 120,
}

# ── "Shadow Man" GUI tab constants (2026-07-25) ────────────────────────────
#
# The five region names access_rules.py's R.pistons() actually requires
# state.can_reach() on (regions.py's ASYLUM_ENGINE_BLOCK_* constants) —
# duplicated here as plain strings (not imported, same self-contained
# philosophy as VANILLA_SOUL_THRESHOLDS above) so the "proximity to Go
# Mode" checklist in the tab can ask Universal Tracker's region callback
# whether each one is currently in logic. If access_rules.py's pistons()
# ever changes this list, update this tuple to match.
ENGINE_BLOCK_REGIONS: Tuple[str, ...] = (
    "Asylum: Engine Block - London",
    "Asylum: Engine Block - Prison",
    "Asylum: Engine Block - Salvage",
    "Asylum: Engine Block - Queens",
    "Asylum: Engine Block - Florida",
)

# Friendly display names for constants.py's LEVEL_FOLDERS, for the tab's
# per-level completion bars. Sourced from data/locations.csv's level_region
# column (most common value per level_id) — not imported (client.py stays
# standalone), just transcribed once. "asyiggy" isn't in locations.csv at
# all (no checkable AP locations there — Iggy's boss arena), so it's a
# best-guess label; everything else is a direct CSV value. Any level_id not
# in this dict falls back to the raw folder name via _level_display_name().
LEVEL_DISPLAY_NAMES: Dict[str, str] = {
    "swampday": "Louisiana Swampland",
    "tenement": "Mordant Street, Queens, NY",
    "prison":   "Gardelle County Jail, Texas",
    "uground":  "Down Street Station, London",
    "florida":  "Summer Camp, Florida",
    "salvage":  "Salvage Yard, Mojave Desert",
    "swampnit": "Louisiana Swampland (Night)",
    "ntenemnt": "Mordant Street, Queens, NY (Night)",
    "nprison":  "Gardelle County Jail, Texas (Night)",
    "nuground": "Down Street Station, London (Night)",
    "nflorida": "Summer Camp, Florida (Night)",
    "nsalvage": "Salvage Yard, Mojave Desert (Night)",
    "deadside": "Deadside Marrow Gates",
    "wastland": "Deadside: Wasteland",
    "asylum":   "Asylum: Gateways",
    "as2exper": "Asylum: Experimentation Rooms",
    "as3schis": "Asylum: Cathedral of Pain",
    "as4dkeng": "Asylum: Engine Block",
    "t1tchgad": "Temple of Fire (Toucher)",
    "t2wlkgad": "Temple of Prophecy (Marcher)",
    "t3swmgad": "Temple of Blood (Nager)",
    "t4ndgad":  "Bonus Level",
    "ah1cagew": "Asylum: Cageways",
    "ah2playr": "Asylum: Playrooms",
    "ah3lavad": "Asylum: Lavaducts",
    "ah4fogom": "Asylum: The Fogometers",
    "asyiggy":  "Asylum: Iggy",
}

# Display order for the level-completion list — mirrors constants.py's
# LEVEL_FOLDERS (not imported, see above). Anything not in this list (there
# shouldn't be any) sorts to the end via _level_display_name()'s caller.
LEVEL_ORDER: Tuple[str, ...] = (
    "swampday", "tenement", "prison", "uground", "florida", "salvage",
    "swampnit", "ntenemnt", "nprison", "nuground", "nflorida", "nsalvage",
    "deadside", "wastland",
    "asylum", "as2exper", "as3schis", "as4dkeng",
    "t1tchgad", "t2wlkgad", "t3swmgad", "t4ndgad",
    "ah1cagew", "ah2playr", "ah3lavad", "ah4fogom",
    "asyiggy",
)


def _level_display_name(level_id: str) -> str:
    """Friendly name for a level_id, falling back to the raw folder name
    (title-cased) for anything missing from LEVEL_DISPLAY_NAMES."""
    return LEVEL_DISPLAY_NAMES.get(level_id, level_id.title())


# ── Dark soul collected-flag array (2026-07-19, Ghidra-confirmed) ──────────
#
# Solves Govi/dark-soul detection without ever needing to catch a live
# kexShadowManDarkSoul heap object (which the heap-scan path almost never
# does -- see DARKSOUL_VTABLE_RVA's comment, near-zero hits every scan all
# session). Found via Cheat Engine "find out what writes" on
# SOUL_COUNT_RVA, which broke in FUN_1402dfb90 (called from
# FUN_1402dc040 -- the SAME function already confirmed 07-14 to own
# DARKSOUL_STATE_OFF/0x20B8). Decompiled both:
#
#   FUN_1402dc040 (per-frame DarkSoul Think/Update, RDI = the soul object):
#       ... on the frame collection completes:
#       *(RDI + 0x20b8) = 1                      # DARKSOUL_STATE_OFF, confirmed
#       RCX = FUN_1402e0aa0()                    # -> becomes "this" below
#       RDX = *(RDI + 0x1a8)                      # a per-instance pointer (unresolved --
#                                                  #   only reachable while the transient
#                                                  #   object is alive, not used below)
#       FUN_1402dfb90(RCX, RDX)
#
#   FUN_1402dfb90(RCX=this, RDX=lookup_key):
#       ... looks RDX up in a small fixed table, gets an index R12 ...
#       byte[R12 + *(RCX+0x18)] = 1               # <-- the actual persistent flag write
#       R10 = *(RCX+0x8); R10++; *(RCX+0x8) = R10 # SOUL_COUNT_RVA itself
#
# RCX (this) here = base + SOUL_COUNT_RVA - 0x8 = base + 0xF9BEC0 (a
# STATIC address, unlike RDI above) -- so DARKSOUL_FLAGARRAY_PTR_RVA and
# DARKSOUL_FLAGARRAY_LEN_RVA below are readable at any time with no live
# object needed at all.
#
# Confirmed live (2026-07-19): the array index written is NOT a rank or
# a per-level ordinal -- it is the location's save_idx used DIRECTLY as
# the byte offset. Verified exactly against data/locations.csv: after
# collecting 5 t1tchgad souls, indices 58/59/62/63/66 flipped 0->1, and
# t1tchgad's soul rows have save_idx values 56-66 with those exact rows
# at those exact save_idx values. save_idx is globally unique across the
# WHOLE GAME (not reused per level -- confirmed by inspecting the CSV),
# so this is one persistent 121-entry (confirmed: length field read live
# as 0x79) array covering every dark soul in the entire playthrough, not
# something that needs resetting/rebaselining on level transitions the
# way the book-position log's level-scoped data might. A newly-flipped
# index is resolved via the existing _loc_map[(level, save_idx)] lookup
# -- the same one QuestObject items already use -- with self.current_level
# at flip-time, since a soul can only physically be collected in the
# level you're standing in.
DARKSOUL_FLAGARRAY_PTR_RVA = 0xF9BED8  # SOUL_COUNT_RVA+0x10 -> pointer to the flag array
DARKSOUL_FLAGARRAY_LEN_RVA = 0xF9BEE0  # SOUL_COUNT_RVA+0x18 -> array length (confirmed 0x79/121 live)
DARKSOUL_FLAGARRAY_MAX_LEN = 512       # safety bound in case length ever reads oddly

# Numeric level index, confirmed live via Cheat Engine (2026-07-18). Read
# every poll to keep ctx.current_level accurate between saves — previously
# current_level was ONLY set from the save file (_identify_level, in
# _poll_save_folder), which only runs when the .sav file's mtime changes.
# Walking into a new level without triggering a save left current_level
# pointing at the PREVIOUS level, which silently broke level-scoped live
# matching (_match_live_position filters _govi_pos_index candidates by this
# same string). See _read_current_level_live() below.
CURRENT_LEVEL_RVA = 0xF5EE9C  # int32, numeric level index — see LEVEL_NUMBER_TO_ID

# Title/attract-screen state — NOT the same question as CURRENT_LEVEL_RVA.
# The main menu's background demo apparently drives real level-loading code
# (confirmed by the user 2026-07-21: it cycles through levels for attract-
# mode gameplay), so a live level number alone can't tell "in a real player
# session" apart from "menu demo currently showing level N". These five
# addresses are a game-state flag instead — sourced from the community
# LiveSplit ASL autosplitter for this game (bropacman v1.5, based on
# Winning117's original), not independently Ghidra-confirmed here, but
# battle-tested by speedrunners across many menu/reset cycles. The
# autosplitter's own reset condition requires all 5 to agree (anti-false-
# positive design) — kept as-is rather than trusting a single address.
# 1/2 read 65536 when in game, 0 at the title screen; 3/4/5 read 1 at the
# title screen. See _read_is_at_title_screen() below.
TITLE_SCREEN_1_RVA = 0x00F5F17C  # int32
TITLE_SCREEN_2_RVA = 0x00F9C1C4  # int32
TITLE_SCREEN_3_RVA = 0x00F970B0  # int32
TITLE_SCREEN_4_RVA = 0x00F970B8  # int32
TITLE_SCREEN_5_RVA = 0x00F9738C  # int32

# Partial mapping — only levels visited so far during live testing. Numbers
# not in this table fall back to whatever current_level already held (save-
# file value or last known live value), rather than being blanked out.
# Extend as more are confirmed; see docs/dev/LIVE_MEMORY_TRACKING_NOTES.md.
LEVEL_NUMBER_TO_ID: Dict[int, str] = {
    0:  "swampday",
    1:  "tenement",
    2:  "prison",
    3:  "uground",
    4:  "florida",
    5:  "salvage",
    6:  "deadside",
    7:  "wastland",
    8:  "asylum",
    9:  "as2exper",
    10: "as3schis",
    11: "as4dkeng",
    12: "t1tchgad",
    13: "ah1cagew",   # Cageways
    14: "ah2playr",   # Playrooms
    15: "t2wlkgad",
    16: "ah3lavad",   # Lavaducts
    17: "t3swmgad",
    18: "ah4fogom",   # Fogometers
    19: "swampnit",   # Louisiana Swampland, night phase — confirmed live 2026-07-26
    20: "ntenemnt",   # Tenement (Queens), night phase — confirmed live 2026-07-26
    21: "nprison",    # Prison, night phase — confirmed live 2026-07-26
    22: "nuground",   # Underground (London), night phase — confirmed live 2026-07-26
    23: "nflorida",   # Florida, night phase — confirmed live 2026-07-26
    24: "nsalvage",   # Salvage Yard, night phase — confirmed live 2026-07-26
    # still unmapped: t4ndgad, asyiggy (standalone levels, not night variants)
}

# Day/night variant aliasing (confirmed live 2026-07-26): the game engine
# loads a genuinely SEPARATE level folder for a level's night phase (see
# LEVEL_FOLDERS in constants.py — "swampnit" etc. are real, distinct folders,
# not just a lighting flag), and CURRENT_LEVEL_RVA / _identify_level's
# save-scan both faithfully report THAT folder string once night falls.
# But data/locations.csv — and therefore every CHECKABLE_LOCS/AP_LOCATIONS
# entry, _ap_id_to_level, _govi_pos_index, etc. — deliberately keys ALL of a
# night-capable level's quest.rsc location data under its DAY-form level_id
# (e.g. every swamp Govi is "swampday:...", even the NIGHT-gated ones — see
# the "NIGHT required only for GOVI" notes column; night-gating is handled
# purely via the R.night() access rule, not a separate level_id). Left
# uncanonicalized, self.current_level flips to "swampnit" once night falls,
# which then: (a) trips the save-vs-live "wrong save slot" mismatch guard in
# _poll_save_folder (since the two sides disagree on the level string) and
# silently skips that poll's Govi/quest-state detection entirely, and
# (b) makes _match_live_position's `lvl != level` filter reject every
# candidate in self._govi_pos_index (all keyed "swampday"), so no AP marker
# in that level can resolve by position either. This is the root cause of
# night-only swamp Govi checks (and any other night-phase location) never
# clearing despite being collected in-game. Fix: canonicalize night-variant
# ids to their day-form counterpart at both places self.current_level gets
# assigned (live memory + save-file identify), so current_level always
# matches the CSV's day-keyed data regardless of in-game time of day.
LEVEL_NIGHT_TO_DAY: Dict[str, str] = {
    "swampnit": "swampday",
    "ntenemnt": "tenement",
    "nprison":  "prison",
    "nuground": "uground",
    "nflorida": "florida",
    "nsalvage": "salvage",
}


def _canonical_level_id(level_id: Optional[str]) -> Optional[str]:
    """Alias a level's night-phase folder id to its day-form counterpart —
    see LEVEL_NIGHT_TO_DAY above. Location data is always keyed by the day
    form, so every current_level assignment should be passed through this."""
    if level_id is None:
        return None
    return LEVEL_NIGHT_TO_DAY.get(level_id, level_id)
POIGNE_RVA        = 0xF9C1A4   # byte,   Poigne flag
GAD_1_RVA         = 0xF9C1A5   # byte,   Gad power 1
GAD_2_RVA         = 0xF9C1A6   # byte,   Gad power 2
GAD_3_RVA         = 0xF9C1A7   # byte,   Gad power 3
GAD_4_RVA         = 0xF9C1A8   # byte,   Gad power 4
INVENTORY_RVA     = 0xF9C380   # kexShadowManInventoryLocal singleton
VTABLE_GIVE_OFF   = 0x30       # byte offset into vtable → GiveItem fn ptr

# FUN_140459d50 (2026-07-28, from the standalone randomizer's own
# gad_pickup_patch.py, FUN_459D50_VA = 0x140459D50 -- module RVA below
# = VA - 0x140000000, same convention as every other *_RVA in this
# file): the real native gad-pickup path calls this right after
# bumping the gad_level flag, described there as "apply gad level
# (textures, flags, abilities)" -- i.e. the actual propagation step
# that makes a freshly-granted gad ability affect already-initialized
# level hazards immediately, not just future ones. See
# _apply_gad_level_now()'s docstring for why the AP client needs this
# too (Jon's report: gad power granted mid-level still needed a level
# reload/warp to take effect despite the existing level-reentry
# resync workaround).
FUN_459D50_RVA    = 0x459D50

# Per-item possession array (kexShadowManInventoryLocal::GiveItem, reverse-
# engineered 2026-07-18 — see docs/dev/LIVE_MEMORY_TRACKING_NOTES.md "inventory
# possession tracking" section and data/inventory_item_offsets.csv for full
# methodology + all 30 confirmed slots). Each slot is a DWORD at
# INVENTORY_RVA + 0x08 + (slot_index * 4): 0 = not owned, nonzero = owned.
#
# Keyed by AP ITEM NAME (2026-07-19 — was keyed by the item's VANILLA
# loc_key, which sent FALSE CHECKS under shuffle: any GiveItem that set the
# flag — picking the item up at its randomized location, the Book of
# Shadows AP-marker pickup, or received-item injection — made the watcher
# "check" the item's vanilla home, wherever the multiworld had placed
# something else. Confirmed live 2026-07-19: swampday Prison Key Card
# pickup false-checked prison:instance.rsc:0x0A4A; first AP-marker pickup
# false-checked deadside:quest.rsc:0x0102.) The location each flag now
# maps to comes from slot_data's inventory_flag_locs — where fill actually
# placed the item THIS seed — see _build_location_map.
#
# Only one-of-a-kind items with a confirmed giveitem-array slot are listed
# (CSV rows with mechanism=giveitem-array, status=confirmed) — multi-copy
# items (Accumulator/Retractor/Prism/Cadeaux) only expose a running count
# (see ACCUMULATOR_COUNT_RVA etc. below), not per-location identity; those
# need position-based tracking instead.
#
# Deliberately ABSENT:
#   RSC_X_BOOK_OF_SHADOWS (0xF9C394) — the AP marker object IS a Book of
#     Shadows, so this flag flips on the first marker pickup anywhere; it
#     can never identify a location.
#   RSC_X_MAC10 / 0.9-SMG (0xF9C3A8) — no corresponding AP item exists in
#     items.py's pool (RSC_X_MP5 below is the MP-909, a different weapon).
# RSC_X_VIOLATOR lists BOTH confirmed Violator slots (Loose 0xF9C3C4 +
# Accumulator-reward 0xF9C3E4): the engine tracks them separately and it's
# unconfirmed which one a patched RSC_Q_VIOLATOR pickup sets — either flip
# counts.
ITEM_FLAG_RVAS: Dict[str, Tuple[int, ...]] = {
    "Baton":            (0xF9C3CC,),
    "Calabash":         (0xF9C3E0,),
    "Engineers Key":    (0xF9C3B0,),
    "Flambeau":         (0xF9C3B4,),
    "La Lune":   (0xF9C3F0,),   # La Lune
    "La Lame":   (0xF9C3F8,),   # La Lame
    "Le Soleil":   (0xF9C3F4,),   # Le Soleil
    "Marteau":          (0xF9C3C8,),
    "Prison Key Card":  (0xF9C3C0,),
    "Asson":            (0xF9C3A4,),
    "Enseigne":         (0xF9C3A0,),
    "Flashlight":       (0xF9C3AC,),
    "Jacks Schematic":  (0xF9C390,),
    # "Book of Prophecy" removed from items.py's shufflable pool (2026-08-02
    # -- see that file's comment for the full story: its RSC identity
    # collides with Gad Power's own RSC_X_GAD_PICKUP at the engine level,
    # so it can never be safely placed/detected as an independent AP item).
    # This entry is now dead/unreachable (the name will never appear as a
    # real received or self-found item), left in place harmlessly rather
    # than removed, matching this file's usual "supersede, don't delete"
    # convention for entries a companion change made unreachable.
    "Book of Prophecy":         (0xF9C38C,),   # Book of Prophecy
    "Shotgun":          (0xF9C3BC,),
    "Sawed-off Shotgun":         (0xF9C3D4,),   # Sawed-off Shotgun
    "MP-909":              (0xF9C3D0,),   # MP-909
    "Tete de Mort":       (0xF9C3DC,),
    "Violator":         (0xF9C3C4, 0xF9C3E4),
}

# Multi-copy item running totals — WORDs, NOT DWORDs (packed back-to-back;
# reading 4 bytes bleeds into the next field, see the CSV notes). These are
# NOT per-location, so still no use for position-based location-check
# identification. RETRACTOR_COUNT_RVA / ACCUMULATOR_COUNT_RVA ARE now read
# (2026-08-10) by _stackable_giveitem_already_sufficient — a one-off
# redundant-injection guard, not a continuous _poll_live_memory subscriber —
# see that function's own comment for why. PRISM_COUNT_RVA remains unused.
PRISM_COUNT_RVA       = 0xF9C400   # WORD
RETRACTOR_COUNT_RVA   = 0xF9C402   # WORD
ACCUMULATOR_COUNT_RVA = 0xF9C404   # WORD

# ── Unique Retractor Keys (2026-08-18) — per-item dedup ─────────────────────
#
# When this world's UniqueRetractorKeys option is on, items.py has no
# fungible "Retractor" stack at all — instead 5 single-copy items, one per
# liveside region (RETRACTOR_KEY_ITEM_NAMES). RETRACTOR_COUNT_RVA above is a
# single shared counter the engine has no way to attribute to "which
# specific retractor", so it can't back these 5 the way it backs plain
# "Retractor" (see STACKABLE_GIVEITEM_COUNT_RVAS's own comment for that
# mechanism, and the 7-Retractors bug it exists to prevent).
#
# Rather than invent 5 new counter RVAs the engine doesn't natively track,
# this reuses the exact same save-backed CF_CUSTOM00-04 flags
# unique_retractor_keys_patch.py's exe hooks (shadow-man-remastered-
# randomizer repo — a SEPARATE checkout, no import possible) already
# read/write for real per-portal gating, via this file's own
# read_named_flag() (pure memory reads, already built for CF_GOT_LIGHTSOUL/
# Legion detection — no new reverse-engineering needed). Confirmed with Jon
# 2026-08-18 as the preferred approach specifically because it keeps
# exe-side gating and AP-side dedup reading the exact same source of truth
# instead of two counters that could drift apart.
CF_CUSTOM_BASE_INDEX = 279   # must match unique_retractor_keys_patch.py's own
                              # constant of the same name (separate repo)

# world_id (1-5) per liveside region — duplicated from
# unique_retractor_keys_patch.py's LIVESIDE_TO_IVAR1 (separate repo, same
# "duplicate the literal across codebases, comment the source of truth"
# convention this file already follows for other cross-repo constants).
_RETRACTOR_REGION_TO_WORLD_ID: Dict[str, int] = {
    "Mordant Street, Queens, NY":  1,
    "Gardelle County Jail, Texas": 2,
    "Down Street Station, London": 3,
    "Summer Camp, Florida":        4,
    "Salvage Yard, Mojave Desert": 5,
}
# {AP item name -> CF_* flag index}, built from items.py's
# RETRACTOR_KEY_ITEM_NAMES (a real import, same package) combined with the
# world_id table just above. read_named_flag(pm, base, this_index) returns
# whether THIS SPECIFIC region's retractor has already been granted/used
# this save — see RETRACTOR_KEY_ITEM_TO_CF_INDEX's two call sites below.
RETRACTOR_KEY_ITEM_TO_CF_INDEX: Dict[str, int] = {
    item_name: CF_CUSTOM_BASE_INDEX + _RETRACTOR_REGION_TO_WORLD_ID[region] - 1
    for region, item_name in RETRACTOR_KEY_ITEM_NAMES.items()
}

# Light Soul injection — three internal calls replicated from FUN_140327410
# (the givelightsoul handler guards on Cmd_Argc()==2 so cannot be called bare)
LIGHT_SOUL_SETUP_RVA    = 0x2EA6F0   # FUN_1402ea6f0() — setup, no args
LIGHT_SOUL_SOULOBJ_RVA  = 0xDB21E0   # DAT_140db21e0  — soul-points object (this ptr)
LIGHT_SOUL_GETOBJ_RVA   = 0x347560   # FUN_140347560() — returns secondary object ptr
#   vtable offsets used inside the handler:
LIGHT_SOUL_VT_A8        = 0xA8       # byte offset → method called with (this, 3)
LIGHT_SOUL_VT_08        = 0x08       # byte offset → method called with (obj, 0xE2)

# Light Soul possession-flag DETECTION (as opposed to injection, above).
#
# SUPERSEDED 2026-07-27 -- LIGHT_SOUL_FLAG_RVA/LIGHT_SOUL_DEBOUNCE_TICKS
# below are kept as historical/reference constants only; nothing reads them
# anymore (same treatment as DARKSOUL_POSPTR_OFF et al. after that path was
# replaced). Root cause found via a live Cheat Engine session with Jon:
# object+0x44 is NOT a dedicated Light Soul flag at all -- it's shared
# meter/HUD display state (health/voodoo/soul-level/Light-Soul meter all
# write the same object+0x44..0x47 block; confirmed live by watching it
# flip during ordinary shadow-gun charging, and by watching it drop back to
# 0 the instant the on-screen meter closes). Every noise source found via
# "find out what writes here" touches the whole 4-byte block at once;
# only the debug givelightsoul handler touches +0x44 in isolation -- but
# since real UI-refresh writes ALSO hold the block at 1 for multiple
# consecutive polls (not a one-tick blip as originally assumed), no
# duration-based debounce can reliably separate the two. The darksoul
# collected-flag array (DARKSOUL_FLAGARRAY_PTR_RVA) was also ruled out
# live -- Light Soul is its own class (kexShadowManLightSoul, confirmed via
# string dump: "shadowmanlightsoul.cpp"/"LinkNode_ProcessLightSoul"), not a
# kexShadowManDarkSoul, so it never touches that array.
#
# Replacement: CF_GOT_LIGHTSOUL, a genuinely dedicated, non-aliased,
# persistent named completion flag -- see CF_FLAG_OBJ_RVA /
# LIGHT_SOUL_CF_INDEX / read_named_flag below.
LIGHT_SOUL_FLAG_RVA = 0xDB2224   # byte, [DAT_140db21e0+0x44]. SUPERSEDED, see above.
LIGHT_SOUL_DEBOUNCE_TICKS = 3    # SUPERSEDED, see above.

# ── Named completion/cutscene flag table ("CF_*") -- GROUND TRUTH via Ghidra
# + live dumpsaveflags console command, 2026-07-27 ──────────────────────────
#
# Found while chasing the LIGHT_SOUL_FLAG_RVA false-positives above: Ghidra
# string search turned up "CF_GOT_LIGHTSOUL" and dozens of siblings
# (CF_GOTPOIGNE, CF_RETRACTOR01USED..CF_RETRACTOR05USED, CF_ECLIPSER_0/1/2,
# CF_TRUEFORM_KILLED, CF_AI_LEGION_DEAD, CF_LEGION_KILLED_SHAD,
# CF_LEGION_DEAD, CF_A2_TRUEFORM, CF_AS3_SCHISMSEEN/USED,
# CF_AS4_LIGHTDOOR, CF_CATHEDRALSEEN, CF_BEENTO_*, etc.) -- see
# docs/dev/LIVE_MEMORY_TRACKING_NOTES.md for the fuller catalog and index numbers.
# These are a flat array of name-string pointers at 140ca2a90 (index 0 =
# CF_INTRO_SCENE, 0x150/336 entries total, 8 bytes/entry, no adjacent
# per-entry data field -- the array POSITION is the flag's only identity).
#
# The GetFlag(index) -> bool accessor was found by decompiling the game's
# own debug dump functions (FUN_1403475e0 console-print, FUN_1403477d0
# "dumpsaveflags" console command -> writes every flag's index/name/value
# to saveflags.txt in the game's root folder). Both call vtable slot +0x18
# on the SAME singleton object inject_light_soul already uses via
# LIGHT_SOUL_GETOBJ_RVA (FUN_140347560) -- but FUN_140347560's own
# disassembly shows it's a standard MSVC magic-statics singleton accessor
# (thread-safe init-once guard, e.g. _Init_thread_header/_footer) that
# ALWAYS returns the same constant address, &DAT_140f9c680, whether or not
# this was the call that triggered first-time construction. So
# CF_FLAG_OBJ_RVA below IS that object's address directly -- not a pointer
# needing a call/dereference to resolve, the way LIGHT_SOUL_SOULOBJ_RVA's
# sibling object works for injection.
#
# The real accessor itself, FUN_14033f1d0 (found by following
# [live vtable ptr + 0x18] in Ghidra, CE-confirmed against
# thoth_x64.exe+0xF9C680 with a +0x18 vtable offset), decompiles to a
# trivial packed-bitfield test -- no virtual call/injection needed at all,
# just two reads (see read_named_flag):
#   if (*(int*)(this + 0x140b4) == 0): return False   // m_dwLength guard
#   bits_ptr = *(qword*)(this + 0x140a8)              // m_pBits
#   return bool((bits_ptr[index >> 3] >> (index & 7)) & 1)
#
# Confirmed live with Jon (2026-07-27) via dumpsaveflags before/after a
# real Light Soul pickup: index 226 read "226: 0 - CF_GOT_LIGHTSOUL"
# beforehand, "226: 1 - CF_GOT_LIGHTSOUL" immediately after, and held at 1
# on a subsequent dump -- a genuinely persistent, non-aliased signal, unlike
# LIGHT_SOUL_FLAG_RVA above. The first cut of this (shellcode + a
# CreateRemoteThread call to the vtable method every poll) worked but was
# needlessly heavy -- injecting a thread every POLL_INTERVAL (1s) for the
# whole session until the flag resolves, vs. this version's two plain
# reads. Replaced before shipping once Jon flagged the cost and we found
# FUN_14033f1d0's real implementation.
CF_FLAG_OBJ_RVA      = 0xF9C680   # DAT_140f9c680 -- the flag-manager object itself (see above)
CF_FLAG_BITS_PTR_OFF = 0x140a8    # object+0x140a8 -> m_pBits, pointer to the packed bit array
CF_FLAG_LENGTH_OFF   = 0x140b4    # object+0x140b4 -> m_dwLength, array length in BYTES
LIGHT_SOUL_CF_INDEX  = 226        # CF_GOT_LIGHTSOUL's index in the 140ca2a90 name table

# Light Soul's own physical location: save_idx=0 in locations.csv (same
# zero-index pattern as Baton/Calabash/etc.), which excludes it from
# _loc_map's (level, instance_id) keying in _build_location_map. Resolved
# directly by loc_key instead -- there is exactly one Light Soul location
# in the whole game and its physical loc_key never changes under shuffle
# (world geometry is seed-invariant; only the awarded item per slot is).
LIGHT_SOUL_LOC_KEY = "ah4fogom:quest.rsc:0x2742"

# ── Soul Level meter sync (2026-07-20, live disassembly of FUN_1402dfb90) ──
#
# The engine's own dark-soul pickup path (FUN_1402dfb90, called from the
# per-frame Think() function — see DARKSOUL_FLAGARRAY_PTR_RVA's comment
# block) does two things after incrementing SOUL_COUNT_RVA: flips the
# collected-flag array byte (already handled — DARKSOUL_FLAGARRAY_PTR_RVA),
# AND recomputes the Soul Level from the new count via the same cascading
# threshold chain soul_threshold_patch.py patches (CMP R10D,imm8 at
# 0x2df116-0x2df187, file offsets — this function contains the LIVE VA
# equivalents: CMP R10D,0x1/0x3/0x7/0xf/0x17/0x23/0x33/0x47/0x5f/0x78 at
# 1402dfd13-1402dfd88, confirmed via Cheat Engine + Ghidra disassembly of
# FUN_1402dfb90 spanning 1402dfceb-1402dfdef). inject_dark_soul() only does
# the count increment — this recompute step is the gap it left.
#
# The result (level * 1000) is written to DAT_140db2208 — but ONLY via a
# compiler devirtualization fast path taken when the soul-points object's
# own vtable pointer (read from [base+LIGHT_SOUL_SOULOBJ_RVA]) equals the
# statically-known kexShadowManMeterLocal::vftable address. Confirmed live:
#   1402dfceb  MOV R8, [DAT_140db21e0]        ; R8 = object's vtable ptr
#   1402dfcf6  LEA R12, [kexShadowManMeterLocal::vftable]
#   1402dfcfd  CMP R8, R12
#   1402dfd00  JNZ LAB_1402e04a2              ; not-equal -> real virtual call
#   ...
#   1402dfda3  CMP EBX, EAX                   ; EBX=new level*1000, EAX=old value
#   1402dfda5  JLE LAB_1402dfdef              ; write is MONOTONIC -- skipped if new <= old
#   1402dfdb4  CMP R9D, EBX
#   1402dfdb7  JNZ LAB_1402dfdd2
#   1402dfdd2  MOV [DAT_140db2208], EBX       ; <-- the actual write
# When R8 != R12 (object not the exact base class -- e.g. before its lazy
# singleton has constructed), the real code instead calls through the
# object's OWN vtable at offset +0x40 with (this, 3, new_value) -- confirmed
# at 1402dfda7 (MOV RAX,[R8+0x40]) on that branch. _sync_soul_level below
# replicates both paths: direct write when the fast-path condition holds
# (the common case during normal play), shellcode virtual call otherwise.
SOUL_LEVEL_METER_RVA       = 0xDB2208   # int32, current soul level * 1000
SOUL_METER_VTABLE_RVA      = 0x700B48   # kexShadowManMeterLocal::vftable
SOUL_METER_VT_SETVALUE_OFF = 0x40       # byte offset -> SetValue(this, statIdx, value)
SOUL_METER_STAT_IDX        = 3          # "which stat" arg -- same 3 used throughout
                                         # this object's other soul-related calls
                                         # (e.g. LIGHT_SOUL_VT_A8's (this, 3) above)

# Console print — reverse-engineered from FUN_140310c20 (the giveinv handler's
# own "wrong argument count" usage-message branch). It null-terminates a
# buffer, then makes exactly two calls, both taking a buffer pointer as their
# last argument. Confirmed via Ghidra XREFs on the "giveinv" command string;
# the same two-call idiom also appears independently in another function
# (FUN_14033f0c0), suggesting this is the engine's general print/flush path,
# not something specific to giveinv's usage message.
#   FUN_1401b4260(&PTR_vftable_140806760, &DAT_1407ee5d8, buf_ptr)
#   FUN_140202300(&PTR_vftable_140c8de50, buf_ptr)
# Max buffer size observed in the handler is 0x1000 (4096), with content
# capped/null-terminated at 0xFFF (4095) — kept well under that here.
CONSOLE_PRINT_FN1_RVA      = 0x1B4260   # FUN_1401b4260
CONSOLE_PRINT_FN1_ARG1_RVA = 0x806760   # &PTR_vftable_140806760 (constant)
CONSOLE_PRINT_FN1_ARG2_RVA = 0x7EE5D8   # &DAT_1407ee5d8         (constant)
CONSOLE_PRINT_FN2_RVA      = 0x202300   # FUN_140202300
CONSOLE_PRINT_FN2_ARG1_RVA = 0xC8DE50   # &PTR_vftable_140c8de50 (constant)
CONSOLE_PRINT_MAX_LEN      = 0xFF       # plenty for a short AP notification

# ── Legion defeat (CLIENT_GOAL) detection ──────────────────────────────────────
#
# Traced from the achievement system: kexSocialAchievements::SetAchievement is
# driven by a generic "sweep all 35 achievements" routine that, for each id,
# first calls an eligibility check before granting. That eligibility check
# (FUN_1402bf570) is a switch on achievement id; case 0 (ACH_LEGION) does:
#
#   obj = FUN_140347560()                       # lazy-init singleton accessor
#   return (bool)(*obj->vtable[0x18])(obj, 0xE0) # HasFlag(obj, 0xE0)
#
# FUN_140347560 is the exact same accessor already used for Light Soul
# injection (LIGHT_SOUL_GETOBJ_RVA) — a classic MSVC "magic statics" lazy
# singleton that, once initialized, always returns a fixed global pointer
# (DAT_140f9c680). Relative to image base that's 0xF9C680 — right next to the
# already-confirmed INVENTORY_RVA (0xF9C380) cluster of live game-state
# globals. Each achievement id maps to a sequential flag id starting at 0xE0
# (id 0 → 0xE0, id 1 → 0xE1 for ACH_BAD_ENDING, ...), so flag 0xE0 is exactly
# "has the player defeated Legion".
#
# We replicate the HasFlag virtual call remotely (same technique as
# inject_console_print / inject_light_soul) rather than reading a raw
# bit/byte offset, since only the accessor's calling convention has been
# reverse-engineered — not the internal flag storage layout.
FLAG_MANAGER_RVA        = 0xF9C680   # DAT_140f9c680 — same singleton as light soul
FLAG_MANAGER_VT_HASFLAG = 0x18       # vtable offset → HasFlag(this, flag_id) -> bool
LEGION_DEFEATED_FLAG_ID = 0xE0       # achievement id 0 (ACH_LEGION)

# ── DeathLink (current/max health) ──────────────────────────────────────────────
#
# Found via the standalone randomizer repo's own EXE patches, not fresh Ghidra
# work: death_penalty_patch.py documents the death-clamp hook site as
# `MOV [RBX+0x20], EDI` (current health -> 0) and separately reads/writes max
# health at `[RBX+0x1C]` in the same object. health_patch.py independently
# confirms that max-health field's static address is 0x140db21fc. The math
# checks out exactly: 0x140db21e0 + 0x1C = 0x140db21fc — meaning RBX in the
# death-clamp hook is `&DAT_140db21e0`, the *same* "soul-points" object this
# file already uses for Light Soul injection (LIGHT_SOUL_SOULOBJ_RVA). So:
#   max health     = base + 0xDB21FC   (RBX+0x1C)
#   current health = base + 0xDB2200   (RBX+0x20, 4 bytes further)
# Poll current health for DETECTING our own death — that part works fine as
# a plain read.
#
# CONFIRMED (via /deathtest, live, two separate attempts): neither a raw
# write of 0 to current health, NOR calling FUN_14032d120(soul_obj, 1,
# big_negative) (the dispatcher that owns the clamp-to-zero + "stat changed"
# flag at [RBX+0x4B]), actually triggers a real death — health shows 0 but
# nothing happens, even with the game fully focused/ticking (ruling out a
# focus-pause theory) and even with a normal enemy-inflicted death working
# fine at the same time (ruling out an anti-debug/invulnerability theory).
#
# Root cause, found via live debugger breakpoint + call-stack walk in Cheat
# Engine (not Ghidra alone — indirect vtable calls don't show up as normal
# function Xrefs): FUN_14032d120 is ONLY the HUD meter object's ModifyStat
# method (vtable slot 5 of kexShadowManMeterLocal). Real gameplay damage
# calls it too, but ONLY as a side effect of updating the on-screen health
# bar — it has no concept of "death" at all. The actual death trigger is a
# SEPARATE call the real damage-application function (FUN_14046e030, i.e.
# TakeDamage) makes afterward: FUN_14046e930(player_ptr, cause, ...), which
# checks two guard flags (already-dead at [player+0x1D65D], invincible bit
# at [player+0x480]) and — if clear — unconditionally kills: marks dead,
# disables input, switches the camera to the death-cam state, fires the
# "OnPlayerDead" script event, and plays the death animation/sound. It does
# NOT re-check health itself; the caller is responsible for having already
# confirmed health <= 0.
#
# player_ptr is NOT a fixed global like the meter object — it's heap
# allocated per level/session. It's obtained by calling FUN_140458680(),
# a no-argument "get local player" singleton accessor (confirmed live: this
# is exactly what feeds RCX right before the real CALL FUN_14046e030 in the
# disassembly, and the offsets it uses, +0x1D65D / +0x480, match the ones
# FUN_14046e930 reads on its own param_1).
#
# So a real DeathLink kill is three calls in sequence:
#   1. FUN_1402ea6f0()                                     — enable-cheats setup (idempotent)
#   2. FUN_14032d120(soul_obj, 1, big_negative)             — zero the HUD health value (cosmetic/consistency)
#   3. player_ptr = FUN_140458680(); FUN_14046e930(player_ptr, 0, 0)  — the REAL death trigger
MAX_HEALTH_RVA        = 0xDB21FC   # RBX+0x1C in the death-clamp hook
CURRENT_HEALTH_RVA    = 0xDB2200   # RBX+0x20 in the death-clamp hook — read-only use
MODIFY_STAT_FN_RVA    = 0x32D120   # FUN_14032d120 — HUD meter ModifyStat(this, statIndex, delta); cosmetic only
STAT_INDEX_CURRENT_HP = 1          # selects the current-health/death case
LETHAL_DAMAGE_DELTA   = -999999    # guarantees health+delta <= 0 regardless of max health
GET_PLAYER_FN_RVA     = 0x458680   # FUN_140458680() — no-arg "get local player" singleton accessor
DEATH_TRIGGER_FN_RVA  = 0x46E930   # FUN_14046e930(player_ptr, cause, ...) — the real death trigger

# ── Health Effects (Poison / Recovery traps, 2026-08-03) ────────────────────
#
# Jon's ask: a "poison" AP trap (slow health drain over ~1 min) and a
# "health recovery" AP bonus (slow gradual heal over ~1 min), same
# trap/bonus pairing shape as the still-backlogged Voodoo power and ammo
# ideas. Reuses the EXACT call sequence _build_death_shellcode already
# proved out live for DeathLink -- FUN_1402ea6f0 (setup) followed by
# FUN_14032d120/ModifyStat(soul_obj, STAT_INDEX_CURRENT_HP, delta) -- just
# with an arbitrary small delta instead of DeathLink's hardcoded
# LETHAL_DAMAGE_DELTA, and with the real death-trigger step (FUN_14046e930)
# left out entirely. Per this file's own DeathLink derivation notes above:
# ModifyStat is confirmed to modify the REAL underlying health value (not
# just a HUD-only copy) -- real gameplay damage already routes through it
# for exactly that reason -- it just has no concept of "death" on its own.
# That means a delta that's guaranteed to never bring health to <=0 is
# safe by construction: there's no death-check code path to accidentally
# trigger, unlike DeathLink's own use of this same call.
#
# UNVERIFIED so far, same caution this file applies to every other reused
# primitive: every prior use of this exact call was a single one-shot
# invocation (one DeathLink kill, with an extreme delta). This would be
# the first time it's called repeatedly, spaced out over up to a minute,
# with small non-lethal deltas -- untested territory in both respects.
# Test via /simpoison and /simheal (small doses first) before trusting
# this enough to wire into a real AP item — same "prove the primitive,
# then build the item" order this file followed for Secret Trap.
HEALTH_EFFECT_TICK_INTERVAL_SECONDS = 1.0   # gap between ticks — 1s per Jon's request
                                             # 2026-08-03 (was 5.0); still 10x more
                                             # generous than ITEM_INJECT_PACING_SECONDS
                                             # (0.1s), which is already proven safe for
                                             # back-to-back CreateRemoteThread calls —
                                             # this just means more TOTAL calls over a
                                             # 60s effect (60 vs 12), not a burst risk
HEALTH_EFFECT_TOTAL_SECONDS         = 60.0  # total effect duration, Jon's "~1 min" ask
# Percentage-of-max scaling (2026-08-03, Jon's call — auto-scales to
# whatever max health a given seed rolled, rather than a fixed point value
# that hits differently on a 500 vs 5000 max-health seed):
#   poison — drains the WHOLE health pool ("drain your whole health pool
#            so you have to find at least 1 health pickup"), no floor
#            (2026-08-03, Jon: "poison shouldn't have to floor, we can
#            floor it at 0, and if hitting 0 just trigger a death"). The
#            tick that would bring health to <=0 calls the real
#            inject_death() (the same proven DeathLink kill sequence)
#            instead of a partial ModifyStat write, then the effect ends.
#            This sidesteps the exact bug DeathLink's own derivation
#            already found: a raw/ModifyStat write to <=0 does NOT trigger
#            a real death (health shows 0 but nothing happens) — only the
#            dedicated death sequence does. Deliberately NOT setting
#            _ignore_next_death for this — a poison death is a genuine own
#            death and should flow through the normal health-watcher
#            DeathLink-send detection like any other death, not be
#            suppressed the way an incoming DeathLink's own reaction-kill
#            is.
#
#            REVISED 2026-08-05 (Jon): per-tick drain used to be
#            ceil(current_health / ticks_remaining) — recomputed from the
#            player's CURRENT health every tick specifically to guarantee
#            landing on exactly 0 by the final tick regardless of rounding
#            (see the old comment this replaced). That had a real, wrong
#            side effect Jon caught: finding a health pickup mid-poison
#            raised `current`, which then scaled the drain calculation UP
#            to compensate — poison would eat whatever health you found
#            instead of you getting any real benefit from it. Fixed by
#            switching to a fixed per-tick magnitude based on MAX health
#            only — ceil(maximum / ticks), same every tick regardless of
#            any pickups in between (the whole point: poison's intensity
#            is a property of the effect and the seed's max health, not of
#            whatever health you happen to be carrying at that instant).
#            The tradeoff from dropping the old "always hits exactly 0"
#            guarantee is intentional, not a bug: if you heal enough
#            mid-effect to outpace this now-fixed drain, you're meant to
#            survive it — the lethal-tick check below still floors any
#            genuine bring-to-<=0 tick at a real death, it just no longer
#            engineers the math to force that outcome regardless of
#            in-between pickups.
#   heal   — total heal target = 200% of max health ("heal you to full
#            twice") — in practice clamped at max health regardless, this
#            just guarantees a full refill comfortably within the window
#            even starting from empty (reaches max around tick 30 of 60).
#            Already purely max-health-based (per_tick = round(maximum *
#            FRACTION / ticks)) — current health is only ever used to CAP
#            an individual tick's delta so it doesn't overshoot past max,
#            never to scale the per-tick amount itself, so this one didn't
#            need the same fix as poison above.
HEALTH_EFFECT_HEAL_FRACTION         = 2.0   # total heal over the effect, as a fraction of max health (clamped to max)

GAD_POWER_RVAS = [GAD_1_RVA, GAD_2_RVA, GAD_3_RVA, GAD_4_RVA]

# ── Item injection map ────────────────────────────────────────────────────────
#
# Maps AP item name → (method, argument)
#   method "give_item"  → call GiveItem(inventory, arg, 0) via vtable shellcode
#   method "soul"       → increment dark soul count by 1
#   method "gad"        → add <arg> (default 1) to the cumulative count of
#                         real Gad Temple items received, then enable
#                         GAD_1..3_RVA up to that count (see
#                         inject_gad_power). Only "Gad Power" itself uses
#                         this — Poigne has its own independent method
#                         below (FIXED 2026-07-25, see that entry).
#   method "poigne_ability" → writes GAD_4_RVA directly, independent of
#                         the "gad" count above (see inject_poigne_ability
#                         and RSC_X_POIGNE's entry below).
#                         RSC_X_POIGNE uses this, not "give_item": give_item
#                         (0x13) alone was confirmed firing but granting
#                         nothing in-game (2026-07-21). Vanilla's own
#                         gad-power tier system has 4 flag slots
#                         (GAD_1..4_RVA) — the 3 real Gad Temples each set
#                         one (arg=1 apiece via the "Gad Power" AP item),
#                         and Poigne sets the 4th directly on its own,
#                         independent of the temples — a real possession
#                         flag (POIGNE_RVA) exists too, but it's
#                         cosmetic/tracking only, not what enables the
#                         ability.
#                         BUG FIX (2026-07-25, Jon's report): Poigne used
#                         to be routed through the SAME "gad" method with
#                         arg=4, adding 4 to the shared cumulative count
#                         and clamp-enabling every flag up through
#                         min(count, 4) — which meant Poigne, received
#                         with fewer than 3 real Gad Temples already
#                         collected, still enabled ALL of them (touch/
#                         walk/swim) early, since the clamp-and-rewrite
#                         loop doesn't distinguish "earned via temple" from
#                         "inflated by Poigne's +4". access_rules.py's
#                         gad1_hand/gad2_walk/gad3_swim only ever check
#                         state.count("Gad Power") — they know nothing
#                         about Poigne (R.poigne() is a separate
#                         state.has() check) — so this was a real logic
#                         sequence-break, not just a display glitch. Now a
#                         fully separate method/counter; see
#                         inject_gad_power's and inject_poigne_ability's
#                         own docstrings.
#
# RSC_X_* item IDs come from ITEM_IDS in constants.py (decimal = giveinv id).
# ─────────────────────────────────────────────────────────────────────────────

AP_ITEM_INJECTION: Dict[str, Tuple[str, Optional[int]]] = {
    # ── Stackable progression ──────────────────────────────────────────────
    "Dark Soul":              ("soul",       None),
    "Gad Power":              ("gad",        1),      # one of the 3 real Gad Temples
    "Retractor":              ("give_item",  0x17),
    # Unique Retractor Keys (2026-08-18) — all 5 named items give the exact
    # same native GiveItem(0x17) as plain "Retractor": world identity is
    # 100% flag/position-based (CF_CUSTOM00-04, RETRACTOR_KEY_ITEM_TO_CF_INDEX
    # above), never RSC/give-item-id based, matching the design already used
    # by items.py's AP_ITEM_TO_RSC (all 5 -> RSC_X_RETRACT) and the exe
    # patch itself.
    "Retractor - London":     ("give_item",  0x17),
    "Retractor - Prison":     ("give_item",  0x17),
    "Retractor - Florida":    ("give_item",  0x17),
    "Retractor - Salvage":    ("give_item",  0x17),
    "Retractor - Queens":     ("give_item",  0x17),
    "Accumulator":            ("give_item",  0x01),
    "Cadeaux":                ("cadeaux",    0x05),   # GiveItem(0x05) flag + direct write to CADEAUX_COUNT_RVA — see its comment above

    # ── Unique progression ──────────────────────────────────────────────────
    "Poigne":           ("poigne_ability", None),  # NOT give_item, NOT "gad" — see comment above
    "Baton":            ("give_item",  0x03),
    "Calabash":         ("give_item",  0x06),
    "Flambeau":         ("give_item",  0x0B),
    "Marteau":          ("give_item",  0x12),
    "Engineers Key":    ("give_item",  0x09),
    "Prison Key Card":  ("give_item",  0x15),
    "La Lune":   ("give_item",  0x10),   # La Lune
    "La Lame":   ("give_item",  0x11),   # La Lame
    "Le Soleil":   ("give_item",  0x1C),   # Le Soleil

    # ── Weapons ────────────────────────────────────────────────────────────
    "Asson":            ("give_item",  0x02),
    "Enseigne":         ("give_item",  0x0A),
    "Shotgun":          ("give_item",  0x1A),
    "Sawed-off Shotgun":         ("pickup_sys",  0x1A),  # post-loop vtable[1](FUN_140347560(), 0x1A)
    "MP-909":              ("give_item",  0x0D),   # confirmed via live giveinv 13 test ("mp909")
    # RSC_X_TETEDEMORT ("Tête De Mort") isn't in the normal 1–31 giveinv
    # range at all — found by decoding the DAT_140c9af00 "give all" table
    # that FUN_140310c20's giveinv -1 path reads, then confirmed live via
    # giveinv 609 directly (GiveItem has no range restriction; 609 was just
    # never in the community-documented 1–31 list since giveinv -1 never
    # prints raw ids). See constants.py's IDD_TETEDEMORT for the same story.
    "Tete de Mort":       ("give_item",  0x261),
    "Violator":         ("give_item",  0x1E),
    "Flashlight":       ("give_item",  0x0E),

    # ── Lore ───────────────────────────────────────────────────────────────
    "Book of Shadows":  ("give_item",  0x04),
    # Dead/unreachable (2026-08-02) -- "Book of Prophecy" removed from
    # items.py's shufflable pool, see that file's comment. Left here
    # harmlessly since the name can never actually appear as a received
    # item going forward.
    "Book of Prophecy":         ("give_item",  0x16),
    "Jacks Schematic":  ("give_item",  0x18),

    # ── Filler ─────────────────────────────────────────────────────────────
    "Light Soul":       ("light_soul", None),   # givelightsoul handler @ LIGHT_SOUL_RVA
    # Trap/Bonus (2026-08-05 split -- see items.py's _ITEM_DEFINITIONS
    # comment and __init__.py's _roll_trap_bonus_item_name()): what used
    # to be one generic "Trap/Bonus" item name resolved to a category at
    # RUNTIME is now 7 concretely-named items, each carrying its own
    # effect directly in `arg` -- _apply_trap_bonus_now (see
    # _inject_item's "trap_bonus" branch) dispatches on `arg` instead of
    # rolling a category itself. "secret" still has its own further
    # runtime sub-roll (which of the ~17 safe cosmetic secrets), since
    # "Secret Effect" was deliberately kept as one generic bucket.
    "Secret Effect":          ("trap_bonus", "secret"),
    "Trap: Poison":           ("trap_bonus", "poison"),
    "Bonus: Recovery":        ("trap_bonus", "heal"),
    "Trap: Voodoo Drain":     ("trap_bonus", "voodoo_drain"),
    "Bonus: Voodoo Max Hold": ("trap_bonus", "voodoo_hold"),
    "Trap: Ammo Drain":       ("trap_bonus", "ammo_drain"),
    "Bonus: Ammo Max Hold":   ("trap_bonus", "ammo_hold"),
}

# Named /simbacklog scenarios (2026-08-02) -- see ShadowManContext.
# _simulate_items()'s docstring for what this is for. "gad49" reproduces
# the exact item shape from the #48 Gad Power / #49 Poigne crash window
# (a Secret Trap supersede landing in between, twice) that has now
# recurred across multiple live sessions even after several rounds of
# mitigation -- the most concrete, targeted repro attempt available
# without a real AP reconnect.
_SIM_SCENARIOS: Dict[str, List[str]] = {
    "gad49": [
        "Dark Soul", "Cadeaux", "Dark Soul",
        # "Secret Effect" here, not one of the 6 named health/voodoo/ammo
        # items (2026-08-05 rename) -- this scenario specifically
        # reproduces the original secret-category supersede/debounce
        # crash window, which "Secret Effect" is the direct successor of.
        "Gad Power", "Secret Effect", "Secret Effect", "Poigne", "Secret Effect",
    ],
}


def _build_sim_mixed_backlog(n: int) -> List[str]:
    """
    Builds a deterministic (seeded on n, so reruns are reproducible),
    realistic-looking n-item backlog for `/simbacklog mixed` -- heavy on
    Dark Soul/Cadeaux/unique give_item-style items early (mirroring a
    real multiworld reconnect's typical composition), with exactly one
    Gad Power, one Poigne, and a few Secret Traps clustered in the last
    handful of slots.

    That clustering is deliberate, not just copying the gad49 scenario:
    every real ntdll-signature crash so far has landed somewhere in the
    #40s-50s of a much longer backlog, regardless of what filled the
    earlier slots -- consistent with CLAUDE.md's running theory that this
    is positional/cumulative (something about being many items deep into
    a paced burst) rather than tied to any one item's specific content.
    Putting the "interesting" items late and padding the rest with cheap,
    already-proven-safe filler tests that theory directly: a crash here
    would have to come from depth/cumulative load, not from Gad Power/
    Poigne/Secret Trap being early or isolated.
    """
    rng = random.Random(f"siminject-backlog-{n}")
    filler_pool = [
        "Dark Soul", "Cadeaux", "Retractor", "Accumulator",
        "Baton", "Calabash", "Flambeau", "Marteau",
        "Book of Shadows", "Light Soul",
    ]
    # "Secret Effect" (2026-08-05 rename) -- see "gad49"'s own comment above.
    tail = ["Gad Power", "Secret Effect", "Secret Effect", "Poigne", "Secret Effect"]
    body_len = max(0, n - len(tail))
    body = [rng.choice(filler_pool) for _ in range(body_len)]
    return body + tail

# ── KSAV / SACT parsing ───────────────────────────────────────────────────────

# GOVI_CLASS / GOVI_STATE_OFF (kexShadowManAIGovi class-name + state-byte
# offset) REMOVED (2026-08-23) alongside _parse_govi_states() — see that
# function's old site (now a breadcrumb comment below) and
# _poll_save_folder's own comment for the full writeup.
SACT_SOUL_OFF  = 0x31   # uint32 LE: dark soul count in user_activity.dat

# GOVI_IID_BACK (the old "instance_id 8 bytes before class name" assumption,
# copied by analogy from QuestObject) is CONFIRMED WRONG for Govi records —
# live pickups produced values matching nothing in the CSV database. There
# is no reliable per-object ID field for Govi; location identity is instead
# resolved by matching the record's own (x, y, z) position against
# data/locations.csv. See _match_govi_position_scan below and the SAVE FILE
# FORMAT NOTES block above for the full derivation.
#
# A SINGLE FIXED byte offset does NOT work here, confirmed live 2026-07-14
# across two rounds of testing:
#   round 1 — offset -96 "matched" 6/7 records in a swampday save, but only
#     against the WRONG category ("barrel"/"cadeaux" rows, not "soul") at
#     an 86% hit rate. In hindsight this was very likely a coincidental
#     false match, not the real field.
#   round 2 — with a much wider, level-filtered scan (--level t1tchgad
#     --scan-min -2000 --scan-max 2000), offset -96 fell apart completely
#     (0/7 matched in that level), while offset +777 (and its likely
#     duplicate-field twin +789, 12 bytes later — exactly one float32
#     triplet) matched ALL 7/7 t1tchgad records against the CORRECT "soul"
#     category, distance ~0.004-0.006 world units, on a NON-randomized
#     save (so no container-retyping ambiguity either — this is about as
#     clean a confirmation as this data gets).
# The record layout evidently varies enough (per level and/or per object,
# likely a variable-length field — e.g. an embedded name string — between
# the position and the class name) that no single constant can be trusted.
# _match_govi_position_scan scans a window of candidate offsets at runtime
# for each occurrence and picks whichever one decodes to a position
# matching a known CSV location. The window below covers both the +777
# cluster (now the stronger evidence) and the original -96/-245 cluster
# (kept in case it's real for some other level/record variant) with margin.
GOVI_POS_SCAN_MIN  = -350  # bytes before class-name start (most negative)
GOVI_POS_SCAN_MAX  = 850   # bytes after class-name start (most positive)

# GOVI_POS_TOLERANCE: loosened from an initial 0.05 after live testing showed
# WHY it was too tight. Confirmed via a live Cheat Engine breakpoint
# (2026-07-14) that a govi's position isn't static — it's actively jittered
# by a per-frame chain/sway physics simulation (the govi's Think() function
# runs a 10-segment procedural animation loop on its own position data).
# Reading a govi's LIVE position at an arbitrary moment landed ~19-60 world
# units away from locations.csv's canonical (rest) coordinate for the exact
# same object — confirmed by cross-referencing a live memory read against
# the same location the offline save-file diagnostic had already matched.
# The earlier "0.05 units, essentially float32-exact" matches came from a
# save file that had sat settled for a while, not a fresh capture — so 0.05
# only ever worked by accident of timing, not because real matches are
# actually that precise.
#
# Real neighboring-location spacing was checked against data/locations.csv:
# most levels keep distinct locations >85 units apart, but a few (mostly
# barrel clusters) get as close as 64-86 units — uncomfortably close to the
# observed jitter range. So a wide tolerance here is intentional and relies
# on the AMBIGUITY_MARGIN_SQ check in _match_govi_position_scan (not this
# constant) as the actual safety net in tightly-packed areas: a generous
# tolerance means we don't miss a real match to jitter, while the ambiguity
# check independently refuses to guess if some OTHER location is nearly as
# close, rather than picking the wrong one.
GOVI_POS_TOLERANCE = 100.0  # world units — X/Z only, see GOVI_Y_TOLERANCE below

# GOVI_Y_TOLERANCE: separate, much looser tolerance for the Y (height) axis
# only, checked independently from the X/Z circle above rather than blended
# into one 3D distance. Added 2026-07-19 after confirming live that
# patcher.py deliberately shifts a slot's Y coordinate by up to +-240 units
# whenever the placed item's "tallness" category differs from the slot's
# original vanilla item — GOVI_HEIGHT_BOOST/CADEAU_HEIGHT_DROP (+-120) plus
# ITEM_Y_ADJUST (up to another +-120, e.g. Calabash) can stack (constants.py
# in the patcher). GOVI_POS_TOLERANCE (100) can't absorb that on its own.
# Kept as a SEPARATE check (not folded into GOVI_POS_TOLERANCE) rather than
# just dropping Y entirely, because some areas stack multiple distinct
# locations at nearly the same X/Z but different floors/heights — losing Y
# as a signal there would trade one ambiguity problem for another. 500
# comfortably covers the +-240 patcher adjustment plus jitter margin while
# still distinguishing floors that are meaningfully far apart vertically.
GOVI_Y_TOLERANCE = 500.0  # world units, Y axis only

# kexShadowManQuestObject — used for ALL quest.rsc item pickups (progression, lore,
# weapons, cadeaux containers). Confirmed via XYZ coordinate matching against 5 deadside
# locations: Book of Shadows, Book of Prophecy, Eclipser × 3.
#
# State layout (from save analysis):
#   class_name_start + 0x32  → state byte  (0 = not collected, 1 = collected)
#   class_name_start − 8     → instance_id (uint32 LE, same as GOVI_IID_BACK)
#
# The instance_id matches rsc_data[offset − 1] in the level's quest.rsc file (where
# `offset` is the byte position of the "RSC_" string — patcher.py INSTANCE_OFF = 0x21).
QUEST_CLASS     = b"kexShadowManQuestObject\x00"
QUEST_IID_BACK  = 8      # bytes before class-name start → instance_id (uint32 LE)
QUEST_STATE_OFF = 0x32   # bytes after class-name start  → state byte (0=not, 1=collected)

# ── Live memory detection (Govi + QuestObject) ──────────────────────────────────
#
# Standard AP client architecture: a periodic memory-reading watcher — same
# shape as SNIClient/BizHawkClient's "game_watcher on an interval", and the
# same model purpose-built PC-memory AP frameworks (e.g. Archipelago.Core's
# Memory class) use. This is NOT a runtime hook or breakpoint. Breakpoints
# were only ever used as a REVERSE-ENGINEERING TOOL during development (via
# Cheat Engine) to discover the addresses/offsets below; the shipped client
# only ever reads memory on a timer, exactly like the save-file watcher.
#
# kexShadowManDarkSoul (Govi / dark souls) — fully solved:
#   Every live instance stores its own class vtable pointer at +0x0
#   (standard polymorphic C++ layout). Heap-scanning for occurrences of
#   that vtable's address finds every live dark soul object. From there:
#     pos_ptr  = read_ptr(soul_obj + 0x30)       — a separate, non-
#                                                   polymorphic data
#                                                   sub-struct
#     position = read_float32x3(pos_ptr + 0x0)   — jittered by per-frame
#                                                   chain-physics; same
#                                                   tolerance/ambiguity-
#                                                   margin matching as the
#                                                   save-file path applies
#                                                   unchanged
#     state    = read_byte(pos_ptr + 0x20B8)     — 0 = intact, 1 = opened
#   Confirmed live 2026-07-14 via Cheat Engine breakpoint + Ghidra Xref
#   trace (FUN_1402dc040 -> kexShadowManDarkSoul::vftable, cross-checked
#   against FUN_1402de490's *(param_1+0x30) dereference). See
#   docs/dev/LIVE_MEMORY_TRACKING_NOTES.md for the full derivation.
#
# kexShadowManQuestObject (weapons/lore/progression/cadeaux) — partially
# solved. Every live instance stores a shared Think()-callback pointer
# (named "LinkNode_ProcessItem" in the engine's own debug function-name
# registry) at +0x18, constant across every instance regardless of item
# type — usable the same way as a vtable for heap-scanning. From there:
#     item_id = read_uint32(item_obj + 0x20)
#     state   = read_uint32(item_obj + 0x24)   — 0 = not collected,
#                                                 1 = collected/triggered,
#                                                 0x10 = fully finalized
#   IMPORTANT: +0x20/+0x24/+0x28 hold the object's real SPAWN POSITION
#   (x, y, z float32, confirmed via the constructor FUN_140311c50) until
#   the exact moment of collection, when the pickup-handling code
#   (FUN_140446500) overwrites +0x20/+0x24 with item_id/state — so there
#   is no live per-SLOT identity available here, only per-TYPE. A second
#   candidate field (+0x10) looked like a promising per-instance
#   descriptor but is confirmed live (two separate captures, two
#   different item_obj addresses) to read the same FIXED value
#   (0x140D93290) — it's type-level, not instance-level either. Getting a
#   true live per-slot identity would require a constructor-time
#   breakpoint (FUN_140086620 / FUN_140311c50), a much riskier hot-path
#   target than GiveItem's once-per-pickup call — not attempted.
#   So QuestObject detection here is used only as a low-latency TRIGGER:
#   the instant any instance's state changes, an immediate save-file
#   re-poll is forced rather than waiting for the next natural poll tick,
#   while slot resolution still goes through the save file's reliable
#   instance_id (_parse_quest_states / _loc_map) exactly as before. This
#   only shaves latency down to "however fast the game itself writes the
#   save file after a pickup" — it does not eliminate the save-file
#   dependency the way the Govi path does.
# DARKSOUL_VTABLE_RVA / AIGOVI_VTABLE_RVA and the heap-scan detection they
# powered (_scan_darksoul_objects) were REMOVED 2026-07-19 — see
# DARKSOUL_FLAGARRAY_PTR_RVA above for the replacement (a direct,
# Ghidra-confirmed flag-array read, no heap scan or live-object needed at
# all). The heap scan never found anything in practice across the whole
# session (near-zero vtable hits every single poll — kexShadowManDarkSoul
# is too transient for a 1Hz poll to catch), so it was pure dead weight
# once the flag-array path existed. DARKSOUL_POSPTR_OFF/DARKSOUL_POS_OFF/
# DARKSOUL_STATE_OFF (0x30/0x0/0x20B8 respectively) remain accurate,
# Ghidra-confirmed facts about the object's layout (see the big comment
# above FUN_1402dc040's derivation) — kept as historical/reference
# constants even though nothing reads them anymore.
DARKSOUL_POSPTR_OFF  = 0x30       # soul_obj+0x30 -> pos_ptr (data sub-struct)
DARKSOUL_POS_OFF     = 0x0        # pos_ptr+0x0    -> (x,y,z) float32 LE
DARKSOUL_STATE_OFF   = 0x20B8     # pos_ptr+0x20B8 -> state byte

QUEST_THINKFN_RVA     = 0x3115C0  # shared Think() callback ("LinkNode_ProcessItem")
QUEST_THINKPTR_OFF    = 0x18      # item_obj+0x18 -> QUEST_THINKFN_RVA (heap-scan key)
QUEST_ITEMID_LIVE_OFF = 0x20      # item_obj+0x20 -> item_id (uint32); pre/at-collection only
QUEST_STATE_LIVE_OFF  = 0x24      # item_obj+0x24 -> state (uint32)

# ── Book-pickup event log + counter (2026-07-19, GROUND-TRUTH via Ghidra) ──
#
# Solves the QuestObject "no live per-slot identity" problem documented
# above (+0x20/+0x24 get overwritten with item_id/state at the exact
# moment of collection, destroying the position). Covers Archipelago-item
# markers (generate_output in __init__.py retypes every foreign-player
# location to RSC_X_BOOK_OF_SHADOWS regardless of original category, so
# this is the path that resolves those) as well as any other pickup that
# happens to route through this same log.
#
# IMPORTANT: this was ORIGINALLY reverse-engineered by repeatedly
# snapshotting a small memory region and guessing at a "compaction/swap"
# theory from the diffs -- that theory was WRONG and has been fully
# replaced. The real structure was found by using Cheat Engine's "find
# out what writes to this address" on ITEM_PICKUP_COUNTER_RVA, which
# broke at thoth_x64_patched.exe+33F531 inside FUN_14033f510 (Ghidra).
# Decompiled there, confirmed via exact arithmetic (see git history /
# session notes if needed):
#
#   FUN_14033f510(RCX=this, RDX=edx_val, R8=pos_ptr, R9=r9d_val):
#       R10 = *(RCX + 0xC090)                    # current count
#       if R10 >= 0x800: return                   # 2048-entry hard cap
#       *(RCX + 0xC090) = R10 + 1                 # ITEM_PICKUP_COUNTER_RVA
#       entry = RCX + (R10 + 6) * 24              # <-- the log slot for
#                                                  #     THIS call, permanent
#       entry[0x00:0x0C] = *(R8)  (three uint32, i.e. the caller's X/Y/Z)
#       entry[0x10]      = RDX
#       entry[0x14]      = R9D
#       entry[0x0C]      = FUN_1402ea000(FUN_1402e98b0()->field_0x2c)
#
# This is a PLAIN, PERMANENT, APPEND-ONLY ARRAY, not a compacting/swapping
# table -- every call gets its own slot at index R10 (the pre-increment
# counter value), and that slot is NEVER touched again afterwards. Proven
# exactly: RCX (this) = base + ITEM_PICKUP_COUNTER_RVA - 0xC090 =
# base + 0xF9C680, so entry(index) = base + 0xF9C680 + (index+6)*24 =
# base + 0xF9C710 + index*24 -- and base + 0xF9C710 + 0x800*24 (the array
# spanning all 2048 possible slots) lands EXACTLY on
# ITEM_PICKUP_COUNTER_RVA (base + 0xFA8710): the counter field sits
# immediately after the array ends. No guessing involved -- this checks
# out to the byte.
#
# What we DON'T yet know: whether every entry is guaranteed to be a
# "quest item collected" event, or whether other unrelated systems reuse
# this same append-log utility with the same `this` pointer (the
# function has other XREFs from elsewhere in the exe). Confirmed live:
# ITEM_PICKUP_COUNTER_RVA fires on key items + cadeaux, never on dark
# souls, and occasionally on "unrelated events (enemy-related flags)"
# per live testing -- so SOME noise in this log is expected. Two
# safety nets, not mutually exclusive:
#   1. entry[+0x14] (R9D at call time) was 1002 (0x3EA) on every
#      confirmed real quest-item pickup captured so far -- worth
#      tracking as a candidate filter once we have a confirmed
#      counter-example (an enemy-flag false-positive's tag value) to
#      compare against.
#   2. Position resolution itself (_match_live_position) already only
#      accepts a hit within tight tolerance against a KNOWN AP location
#      -- a stray non-pickup entry (enemy position, etc.) will almost
#      always simply fail to resolve, the same as how an excluded
#      category (e.g. cadeaux -- see _SKIP_CATS in locations.py) already
#      does, rather than causing a wrong check to be sent.
BOOKPOS_LOG_BASE_RVA  = 0xF9C710  # address of entry index 0
BOOKPOS_LOG_ENTRY_SIZE = 24
BOOKPOS_LOG_MAX_ENTRIES = 0x800   # hard cap confirmed in FUN_14033f510
BOOKPOS_LOG_QUEST_TAG  = 1002     # entry[+0x14] on every confirmed real pickup so far (see note above -- not yet used as a hard filter)

# Global "an item was collected" counter -- found via Cheat Engine
# increased-value scan (2026-07-19): increments by 1 on every key-item/
# cadeaux/quest-item pickup, confirmed across repeated pickups of the
# SAME item type (not a one-time "first of this type" achievement
# counter) and confirmed to persist -- not reset -- across level
# transitions (reset only on a fresh game load, per FUN_14033f510's
# caller zeroing it at startup). Does NOT increment on dark souls (those
# already have their own signal, SOUL_COUNT_RVA above). This is now the
# authoritative index into the pickup log above -- not just a gate.
ITEM_PICKUP_COUNTER_RVA = 0xFA8710

# Full heap re-scans are the expensive part of live-memory polling — cache
# known-good addresses and only re-scan for NEW/moved objects every N poll
# ticks (level transitions free/reuse the old heap addresses, so a cache
# that's never refreshed would go stale). Every tick in between just
# re-reads the already-cached addresses, which is cheap. Full scans run in
# a worker thread (see _poll_live_memory) so they never stall the UI loop.
MEMORY_FULL_SCAN_EVERY_N_POLLS = 10

# Bytes per ReadProcessMemory call during heap walks — bounds the size of
# the Python bytes objects created (large game regions can be hundreds of
# MB; reading them whole caused multi-second stalls and RAM spikes).
_SCAN_CHUNK = 8 * 1024 * 1024

_SAVE_SUBDIRS = [
    # "ap" listed first: save_path_patch.py (2026-07-20) redirects
    # thoth_x64_patched.exe's save folder from "saves" to "ap" so AP/
    # randomizer playthroughs never share slots with vanilla saves. Every
    # patched exe from that point on writes here. "saves" is kept as a
    # fallback for an exe patched before this change (or if someone points
    # the client at a vanilla-saves folder for diagnostics).
    Path("Saved Games") / "Nightdive Studios" / "Shadowman EX" / "ap",
    Path("Saved Games") / "Nightdive Studios" / "Shadowman EX" / "saves",
    Path("AppData") / "Local"  / "Nightdive Studios" / "Shadowman EX" / "ap",
    Path("AppData") / "Local"  / "Nightdive Studios" / "Shadowman EX" / "saves",
    Path(".local") / "share" / "Nightdive Studios" / "Shadowman EX" / "ap",
    Path(".local") / "share" / "Nightdive Studios" / "Shadowman EX" / "saves",
]


def _find_save_dir() -> Optional[Path]:
    home = Path.home()
    for sub in _SAVE_SUBDIRS:
        c = home / sub
        if c.is_dir():
            return c
    return None


# _parse_govi_states() REMOVED (2026-08-23, Jon's report of free/phantom
# location checks around true-form releases and boss cutscenes) — it
# diffed Govi open/closed state keyed by file_pos (raw save-file byte
# offset, the only key available -- Govi records have no real instance_id,
# see GOVI_IID_BACK's comment above), which drifts whenever an earlier
# part of the save file changes size, spuriously relabeling long-since-
# opened Govis as "new" flips at their shifted offsets. See
# _poll_save_folder's own comment (where this used to be called) for the
# full writeup. GOVI_CLASS/GOVI_STATE_OFF (above) were only ever used to
# locate Govi occurrences for THIS scan -- removed alongside it, since
# _match_govi_position_scan below takes an already-found class_pos as a
# parameter and never needed them itself (its callers now only ever pass
# QuestObject-sourced positions, reusing the Govi tolerance window as a
# calibration hypothesis -- see that function's own docstring).


def _match_govi_position_scan(
    save_bytes: bytes,
    class_pos: int,
    level_id: Optional[str],
    pos_index: List[Tuple[str, float, float, float, int]],
) -> Tuple[Optional[int], Optional[Tuple[float, float, float]]]:
    """
    Resolve a Govi record's AP location by scanning a window of candidate
    byte offsets around its class-name occurrence for an (x, y, z) float32
    triplet that matches a known CSV location within GOVI_POS_TOLERANCE.

    Why scan instead of reading one fixed offset: confirmed live
    (2026-07-14) that the position field's offset relative to the class
    name is NOT constant across records — a fixed offset that matched 6/7
    records in one save produced garbage (denormalized floats, huge
    magnitudes) for most records in a different level's save. Almost
    certainly a variable-length field (e.g. an embedded per-object name
    string) sits between the position and the class name, shifting the
    true offset per object.

    Restricts candidate locations to level_id — this cuts the candidate
    pool from thousands to tens/hundreds, both for speed and to keep
    false-positive risk low across ~1200 scanned offsets. Deliberately does
    NOT fall back to searching every level: that would mean checking
    ~1200 offsets against every location in the whole game (thousands of
    candidates instead of hundreds), which meaningfully raises the odds
    that some unrelated byte pattern coincidentally lands within tolerance
    of SOME location somewhere. A missed check can be retried later by
    collecting the item again / reconnecting; a WRONG check sent to the
    server is much harder to undo — so this fails closed (returns None,
    None) rather than guessing cross-level if level_id is unknown or has
    no candidates. If level identification turns out to be unreliable in
    practice, fix that (_identify_level) rather than papering over it here.

    Also refuses to guess when the match is AMBIGUOUS: if some other
    location (a different ap_id) also comes within AMBIGUITY_MARGIN of the
    winning distance, that signals either two real objects sitting close
    together, or a coincidental hit — either way, not confident enough to
    report. This does NOT reject a record's own known duplicate position
    field (confirmed real: +777 and +789 both decode to the same location
    12 bytes apart) since that's the same ap_id, not a different one.

    Deliberately does NOT filter pos_index by category: the patcher can
    retype any location slot into a govi in a given seed (confirmed via
    tools/diagnose_govi_position_offset.py — real govi records matched CSV
    rows categorized "barrel" and "cadeaux", not "soul").

    Returns (ap_id, position) for the closest match found, or (None, None).
    """
    if not level_id:
        return None, None
    level_candidates = [k for k in pos_index if k[0] == level_id]
    if not level_candidates:
        return None, None

    tol_sq = GOVI_POS_TOLERANCE ** 2
    # Every (offset, candidate) pair that fell within tolerance — kept as a
    # flat list (not just running best/second-best) so the ambiguity check
    # below can correctly ignore duplicate hits on the SAME ap_id.
    hits: List[Tuple[float, int, Tuple[float, float, float]]] = []

    for off in range(GOVI_POS_SCAN_MIN, GOVI_POS_SCAN_MAX + 1):
        idx = class_pos + off
        if idx < 0 or idx + 12 > len(save_bytes):
            continue
        try:
            x, y, z = struct.unpack_from("<fff", save_bytes, idx)
        except struct.error:
            continue
        # Quick sanity filter before the expensive distance loop: real
        # level coordinates are large-ish floats, not NaN/inf/garbage.
        if not all(v == v and abs(v) < 1_000_000 for v in (x, y, z)):
            continue
        for lvl, kx, ky, kz, ap_id in level_candidates:
            # X/Z circle (tight, GOVI_POS_TOLERANCE) + separate Y band
            # (loose, GOVI_Y_TOLERANCE) — see GOVI_Y_TOLERANCE's comment for
            # why these are two independent checks rather than one blended
            # 3D distance. Ranking/ambiguity still uses X/Z distance only,
            # since that's the precise signal; Y is a pass/fail gate.
            if abs(y - ky) > GOVI_Y_TOLERANCE:
                continue
            d2 = (x - kx) ** 2 + (z - kz) ** 2
            if d2 <= tol_sq:
                hits.append((d2, ap_id, (x, y, z)))

    if not hits:
        return None, None

    hits.sort(key=lambda h: h[0])
    best_d2, best_id, best_pos = hits[0]

    # AMBIGUITY_MARGIN: require any OTHER ap_id's closest hit to be at
    # least 3x farther away (9x in squared-distance terms) than the
    # winner. Real matches were ~0.005 units — a genuine match should win
    # by a wide margin, not a photo finish.
    AMBIGUITY_MARGIN_SQ = 9.0
    other_best_d2 = next((d2 for d2, ap_id, _ in hits if ap_id != best_id), None)
    if other_best_d2 is not None and other_best_d2 < best_d2 * AMBIGUITY_MARGIN_SQ:
        return None, None

    return best_id, best_pos


def _parse_quest_states(save_bytes: bytes) -> Dict[int, int]:
    """
    Return {instance_id: state} for every kexShadowManQuestObject record.

    state=1 means the pickup has been collected; state=0 means it hasn't.
    Covers ALL quest.rsc item pickups: progression items, weapons, lore, and
    cadeaux containers.  The instance_id matches patcher.py's INSTANCE_OFF byte
    read from rsc_data[name_offset − 1] in the level's quest.rsc.

    Same class-name-scanning approach the old _parse_govi_states used
    (removed 2026-08-23 — see its breadcrumb comment above), but
    QuestObject DOES have a reliable instance_id (unlike Govi):
        instance_id  at  class_name_start − QUEST_IID_BACK   (uint32 LE)
        state byte   at  class_name_start + QUEST_STATE_OFF
    """
    states: Dict[int, int] = {}
    pos = 0
    while True:
        p = save_bytes.find(QUEST_CLASS, pos)
        if p == -1:
            break
        if p >= QUEST_IID_BACK:
            iid   = struct.unpack_from("<I", save_bytes, p - QUEST_IID_BACK)[0]
            s_pos = p + QUEST_STATE_OFF
            if s_pos < len(save_bytes):
                states[iid] = save_bytes[s_pos]
        pos = p + len(QUEST_CLASS)
    return states


def _parse_questobject_states_by_pos(save_bytes: bytes) -> Dict[int, Tuple[int, int]]:
    """
    Return {file_pos: (instance_id, state)} for every kexShadowManQuestObject
    record, keyed by the class-name occurrence's OWN file offset instead of
    by instance_id (contrast _parse_quest_states, which aggregates by
    instance_id and so collapses multiple records that share one — exactly
    what happens for every RSC_X_BOOK_OF_SHADOWS "this is an Archipelago
    item" marker location, which all intentionally share instance_id=0; see
    generate_output in __init__.py).

    Same scan as _parse_quest_states (same QUEST_CLASS/QUEST_STATE_OFF/
    QUEST_IID_BACK), just keyed differently. Callers should filter to
    instance_id == 0 before doing the (expensive) position-scan resolution
    below — records with a real nonzero instance_id are already handled
    reliably by _parse_quest_states / _loc_map, no need to redundantly
    position-scan those too.
    """
    states: Dict[int, Tuple[int, int]] = {}
    pos = 0
    while True:
        p = save_bytes.find(QUEST_CLASS, pos)
        if p == -1:
            break
        if p >= QUEST_IID_BACK:
            iid   = struct.unpack_from("<I", save_bytes, p - QUEST_IID_BACK)[0]
            s_pos = p + QUEST_STATE_OFF
            if s_pos < len(save_bytes):
                states[p] = (iid, save_bytes[s_pos])
        pos = p + len(QUEST_CLASS)
    return states


def _parse_sact_soul_count(data: bytes) -> Optional[int]:
    if len(data) >= SACT_SOUL_OFF + 4:
        return struct.unpack_from("<I", data, SACT_SOUL_OFF)[0]
    return None


def _identify_level(save_bytes: bytes) -> Optional[str]:
    needle = b"levels/"
    counts: Counter = Counter()
    pos = 0
    while True:
        p = save_bytes.find(needle, pos)
        if p == -1:
            break
        end = save_bytes.find(b"/", p + len(needle))
        if 0 < end - p - len(needle) < 32:
            try:
                folder = save_bytes[p + len(needle):end].decode("ascii")
                if folder:
                    counts[folder] += 1
            except UnicodeDecodeError:
                pass
        pos = p + 1
    return counts.most_common(1)[0][0] if counts else None


# ── Process injection ──────────────────────────────────────────────────────────

_pm_cache: Tuple[Optional[object], Optional[int]] = (None, None)
_warned_vanilla_exe = False


def _get_process():
    """
    Return (pymem.Pymem, exe_base) or (None, None) if the patched game
    isn't running.

    Caches the attached handle: four watcher loops call this every second,
    and a fresh pymem.Pymem() attach per call is expensive (OpenProcess +
    module walk) and used to log at INFO each time. A cheap 1-byte read of
    the PE header validates the cached handle; if the game restarted, the
    read fails and we re-attach — new process, new handle, new base.

    Only ever attaches to PATCHED_EXE_NAME. If that's not found but
    VANILLA_EXE_NAME is running instead, logs ONE helpful warning (not every
    poll — see _warned_vanilla_exe) telling the player to launch the patched
    exe, then returns (None, None) same as "game not running at all" — every
    watcher loop already degrades gracefully to save-file-only behavior when
    this returns (None, None), so this is a safe fallback, not a crash.
    """
    global _pm_cache, _warned_vanilla_exe
    pm, base = _pm_cache
    if pm is not None:
        try:
            pm.read_uchar(base)   # liveness probe — PE header is always readable
            return pm, base
        except Exception:
            _pm_cache = (None, None)
    try:
        import pymem
        import pymem.process
        pm   = pymem.Pymem(PATCHED_EXE_NAME)
        base = pymem.process.module_from_name(
            pm.process_handle, PATCHED_EXE_NAME).lpBaseOfDll
        _pm_cache = (pm, base)
        _warned_vanilla_exe = False
        _inject_overlay_dll(pm)
        return pm, base
    except Exception:
        if not _warned_vanilla_exe:
            try:
                import pymem
                pymem.Pymem(VANILLA_EXE_NAME)   # just probing — is IT running?
                logger.warning(
                    f"[ShadowMan] {VANILLA_EXE_NAME} is running, but this "
                    f"client needs {PATCHED_EXE_NAME} for live memory "
                    f"tracking, item injection, and DeathLink. Launch "
                    f"{PATCHED_EXE_NAME} from your game folder instead "
                    f"(or rename it over {VANILLA_EXE_NAME} so it launches "
                    f"by default), then reconnect. Falling back to "
                    f"save-file-only detection for now.")
                _warned_vanilla_exe = True
            except Exception:
                pass   # neither exe running — normal "game not open yet" case
        return None, None


def _is_patched_exe_running() -> bool:
    """
    Definitive "is PATCHED_EXE_NAME still in the OS process list right
    now" check (2026-08-01), independent of _pm_cache/_get_process()'s
    cheap 1-byte liveness probe. Added after a confirmed live gap: during
    game shutdown, that 1-byte PE-header read can keep succeeding for a
    noticeable window (that page is often one of the last to actually
    become unreadable) even as deeper, game-managed memory (e.g. the
    title-screen-state reads _read_is_at_title_screen relies on) is
    already failing/ambiguous — so a loop gating on "pm is None" alone
    can spin indefinitely on a process that's genuinely on its way out.
    This walks the real OS process list (pymem.process.process_from_name,
    the same primitive _get_process() already uses for its initial
    attach) rather than trusting any cached handle, at the cost of being
    more expensive than the cached probe — only meant to be called
    occasionally (e.g. after a few consecutive ambiguous reads), not on
    every poll tick the way _get_process() is.
    """
    try:
        import pymem.process
        return pymem.process.process_from_name(PATCHED_EXE_NAME) is not None
    except Exception:
        return False


def _overlay_already_injected(pm) -> bool:
    """Check the target process's module list directly rather than trusting
    a Python-side flag — robust across client.py restarts/reattaches, which
    a plain "did we inject" bool wouldn't survive correctly."""
    try:
        import pymem.process
        return pymem.process.module_from_name(
            pm.process_handle, OVERLAY_DLL_NAME) is not None
    except Exception:
        return False


def _inject_overlay_dll(pm) -> bool:
    """
    Load ShadowManOverlay.dll into the game process (classic LoadLibraryW
    injection), so it can render popup toasts for items we receive/send.
    See overlay_dll/README.md for what the DLL does and how it talks back
    to us. No-op (returns True) if it's already loaded, and a soft failure
    (logs + returns False) if the DLL isn't built yet — the client works
    fine without it, this is a purely cosmetic add-on.

    Uses the same pm.allocate()/pm.write_bytes()/CreateRemoteThread pattern
    _remote_exec_shellcode() uses elsewhere in this file, just pointed at
    kernel32's real LoadLibraryW instead of our own shellcode.
    """
    try:
        if _overlay_already_injected(pm):
            return True

        if not os.path.exists(OVERLAY_DLL_PATH):
            logger.info(
                f"[ShadowMan] Overlay DLL not found at {OVERLAY_DLL_PATH} — "
                f"skipping popup overlay (build it via overlay_dll/, see "
                f"overlay_dll/README.md). Item tracking works fine without it.")
            return False

        path_bytes = OVERLAY_DLL_PATH.encode("utf-16-le") + b"\x00\x00"

        buf = pm.allocate(len(path_bytes))
        try:
            pm.write_bytes(buf, path_bytes, len(path_bytes))

            kernel32 = ctypes.windll.kernel32

            # ctypes defaults an unconfigured function's return type to
            # 32-bit `int`. GetProcAddress/GetModuleHandleW/CreateRemoteThread
            # all return pointer-sized values — on this x64 process, leaving
            # restype unset silently truncates the real address and produces
            # garbage (usually reads as NULL). Must set these explicitly.
            kernel32.GetModuleHandleW.restype = ctypes.c_void_p
            kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
            kernel32.GetProcAddress.restype = ctypes.c_void_p
            kernel32.GetProcAddress.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            kernel32.CreateRemoteThread.restype = ctypes.c_void_p
            kernel32.CreateRemoteThread.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                ctypes.c_void_p]
            kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            kernel32.GetExitCodeThread.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]

            load_library_addr = kernel32.GetProcAddress(
                kernel32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
            if not load_library_addr:
                logger.warning("[ShadowMan] Overlay inject: couldn't resolve LoadLibraryW")
                return False

            h = kernel32.CreateRemoteThread(
                pm.process_handle, None, 0,
                ctypes.c_void_p(load_library_addr), ctypes.c_void_p(buf), 0, None)
            if not h:
                logger.warning("[ShadowMan] Overlay inject: CreateRemoteThread failed")
                return False

            kernel32.WaitForSingleObject(h, 5000)
            exit_code = ctypes.c_ulong(0)
            kernel32.GetExitCodeThread(h, ctypes.byref(exit_code))
            kernel32.CloseHandle(h)

            if exit_code.value == 0:
                logger.warning(
                    "[ShadowMan] Overlay inject: LoadLibraryW returned NULL in "
                    "the target process (missing dependency, or 32/64-bit "
                    "mismatch?). Item tracking is unaffected.")
                return False
        finally:
            pm.free(buf)

        logger.info(f"[ShadowMan] Overlay DLL injected: {OVERLAY_DLL_PATH}")
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] Overlay inject failed: {exc}")
        return False


class _OverlayIPC:
    """
    Best-effort TCP client for the injected overlay DLL. Never raises and
    never blocks the event loop for more than the short connect/send
    timeout below — if the DLL isn't injected/listening yet (or the game
    isn't running), every call here is a cheap no-op. See
    overlay_dll/README.md for the wire format.
    """

    _RECONNECT_INTERVAL_S = 5.0
    _SOCKET_TIMEOUT_S = 0.25

    def __init__(self, port: int = OVERLAY_IPC_PORT):
        self._port = port
        self._sock: Optional[socket.socket] = None
        self._last_connect_attempt = 0.0
        # Incoming (DLL -> client.py) buffer for the connect/console panel
        # (2026-08-04, see ipc_server.cpp's SendToClient / overlay.cpp's
        # RenderConnectPanel) — same one socket as the send direction
        # above, just also read from now.
        self._recv_buffer = ""

    def _ensure_connected(self) -> bool:
        if self._sock is not None:
            return True
        now = time.monotonic()
        if now - self._last_connect_attempt < self._RECONNECT_INTERVAL_S:
            return False
        self._last_connect_attempt = now
        try:
            s = socket.create_connection(
                ("127.0.0.1", self._port), timeout=self._SOCKET_TIMEOUT_S)
            s.settimeout(self._SOCKET_TIMEOUT_S)
            self._sock = s
            return True
        except OSError:
            self._sock = None
            return False

    def _send(self, payload: dict) -> None:
        if not self._ensure_connected():
            return
        try:
            self._sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        except OSError:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def item_received(self, item_name: str, sender: str) -> None:
        self._send({"type": "item_received", "item": item_name, "from": sender})

    def item_sent(self, label: str, to: Optional[str] = None) -> None:
        payload = {"type": "item_sent", "item": label}
        if to:
            payload["to"] = to
        self._send(payload)

    def status(self, text: str) -> None:
        self._send({"type": "status", "text": text})

    def secret_trap(self, display_name: str, duration_desc: str) -> None:
        # No dedicated C++ handling needed -- ipc_server.cpp's
        # EventFromFields() already falls back to `ev.title = get("text")`
        # for any unrecognized "type" string (verified directly against
        # the DLL source before adding this), so a brand-new "secret_trap"
        # type renders correctly with zero DLL changes/rebuild required.
        self._send({
            "type": "secret_trap",
            "text": f"Secret Trap: {display_name} ({duration_desc})",
        })

    def trap_bonus(self, text: str) -> None:
        # Generic toast for the non-secret Trap/Bonus categories (health/
        # voodoo/ammo, added 2026-08-03) -- reuses the exact same
        # unrecognized-"type"-falls-back-to-text DLL behavior secret_trap
        # already relies on above, just with a caller-supplied full text
        # string instead of a fixed "Secret Trap: ..." template, since
        # each category's own label shape differs (e.g. "Trap/Bonus:
        # Voodoo Max Hold (60s)").
        self._send({"type": "trap_bonus", "text": text})

    def connected(self, text: str) -> None:
        self._send({"type": "connected", "text": text})

    def disconnected(self, text: str) -> None:
        self._send({"type": "disconnected", "text": text})

    def connect_failed(self, reason: str) -> None:
        """A connect ATTEMPT failed (refused, bad address, timeout, wrong
        password, etc.) — distinct from disconnected(), which just means
        "no longer connected" without necessarily being an error (e.g. an
        intentional /disconnect). Lets the in-game panel show a clear red
        failure state instead of silently doing nothing, which is what a
        failed attempt used to look like before this existed (2026-08-05)."""
        self._send({"type": "connect_failed", "text": reason})

    def poll_incoming(self) -> list:
        """
        Non-blocking check for anything the in-game connect/console panel
        (overlay_dll, added 2026-08-04) queued for us on the same socket —
        a Connect-button press or a typed command. Returns a list of
        parsed dicts, or an empty list if nothing's waiting / not
        connected. Never blocks (uses select() with a zero timeout to
        check readability before ever calling recv()) and never raises —
        same "cheap no-op if the DLL isn't there" convention as _send/
        _ensure_connected above.

        BUG FOUND AND FIXED (2026-08-05): this used to assume
        _ensure_connected() would already have been called by one of the
        outbound _send() calls elsewhere in this class (item toasts,
        status updates, etc.) and just checked `self._sock is None` —
        but on a genuinely fresh session (nothing received/sent yet,
        never connected before — precisely the state a player is in the
        very first time they use the in-game Connect panel, since that's
        the whole point of it) _send() may never have fired even once,
        so _ensure_connected() never ran, self._sock stayed None forever,
        and this silently returned [] every single poll — the panel's
        Connect/Send clicks were being sent by the DLL correctly but
        nothing on this side was ever listening for them. Now actively
        attempts the connection itself (idempotent/rate-limited by
        _ensure_connected's own 5s interval either way, so calling it
        from both directions is safe) instead of passively waiting for
        an unrelated outbound event to have triggered it first.
        """
        if not self._ensure_connected():
            return []
        try:
            readable, _, _ = select.select([self._sock], [], [], 0)
            if not readable:
                return []
            data = self._sock.recv(4096)
            if not data:
                # DLL closed its end — match _send's own disconnect
                # handling so _ensure_connected reconnects later.
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
                return []
            self._recv_buffer += data.decode("utf-8", errors="replace")
        except OSError:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            return []

        out = []
        while "\n" in self._recv_buffer:
            line, self._recv_buffer = self._recv_buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                logger.warning(f"[ShadowMan] Overlay panel sent unparseable line: {line!r}")
        return out


# Process-wide singleton — mirrors _pm_cache above, one overlay connection
# is all we ever need.
_overlay_ipc = _OverlayIPC()


class _MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress",       ctypes.c_void_p),
        ("AllocationBase",    ctypes.c_void_p),
        ("AllocationProtect", ctypes.c_ulong),
        ("PartitionId",       ctypes.c_ushort),
        ("RegionSize",        ctypes.c_size_t),
        ("State",             ctypes.c_ulong),
        ("Protect",           ctypes.c_ulong),
        ("Type",              ctypes.c_ulong),
    ]


_MEM_COMMIT             = 0x1000
_MEM_PRIVATE            = 0x20000
_PAGE_READWRITE         = 0x04
_PAGE_EXECUTE_READWRITE = 0x40
_PAGE_GUARD             = 0x100
_PAGE_NOACCESS          = 0x01
_USERSPACE_ADDR_CAP     = 0x7FFFFFFFFFFF  # user-mode address space ceiling, x64 Windows


def _scan_memory_for_signatures(
    pm, signatures: List[bytes]
) -> Dict[bytes, List[int]]:
    """
    Walk the target process's committed, private, read/write memory (i.e.
    heap — excludes the module's own code/image and read-only pages) ONCE
    and return every address where each signature occurs, aligned to that
    signature's length (so an 8-byte pointer value is only matched at
    8-byte-aligned addresses, matching real C++ object layout instead of
    flooding on sub-byte coincidences).

    Taking multiple signatures per walk matters: the address-space walk and
    ReadProcessMemory calls dominate the cost, so scanning souls + quest
    objects together is ~half the cost of two separate scans. Regions are
    read in _SCAN_CHUNK slices (with a max-sig-length-minus-one overlap so
    boundary-straddling hits aren't missed; aligned hits can't be double-
    counted because a full match never fits inside the overlap window).

    This is a standard live-memory technique used by PC-game AP clients
    (see Archipelago.Core's Memory class) — meant to be called periodically
    from a polling loop, not from a hook/breakpoint. Still expensive; run
    it in a worker thread and only every MEMORY_FULL_SCAN_EVERY_N_POLLS.
    """
    results: Dict[bytes, List[int]] = {sig: [] for sig in signatures}
    mbi      = _MEMORY_BASIC_INFORMATION()
    max_sig  = max(len(s) for s in signatures)
    address  = 0
    kernel32 = ctypes.windll.kernel32

    while address < _USERSPACE_ADDR_CAP:
        ret = kernel32.VirtualQueryEx(
            pm.process_handle, ctypes.c_void_p(address),
            ctypes.byref(mbi), ctypes.sizeof(mbi))
        if ret == 0:
            break
        region_size = mbi.RegionSize or 0x1000
        is_heap_rw = (
            mbi.State == _MEM_COMMIT and
            mbi.Type == _MEM_PRIVATE and
            not (mbi.Protect & _PAGE_GUARD) and
            mbi.Protect != _PAGE_NOACCESS and
            bool(mbi.Protect & (_PAGE_READWRITE | _PAGE_EXECUTE_READWRITE))
        )
        if is_heap_rw:
            base = mbi.BaseAddress or 0
            off = 0
            while off < region_size:
                length = min(_SCAN_CHUNK, region_size - off)
                try:
                    data = pm.read_bytes(base + off, length)
                except Exception:
                    break
                for sig in signatures:
                    sig_len = len(sig)
                    pos = 0
                    while True:
                        idx = data.find(sig, pos)
                        if idx == -1:
                            break
                        if (off + idx) % sig_len == 0:
                            results[sig].append(base + off + idx)
                        pos = idx + 1
                off += length
                if off < region_size:      # overlap for boundary-straddlers
                    off -= (max_sig - 1)
        address += region_size

    return results


def _scan_memory_for_signature(pm, signature: bytes) -> List[int]:
    """Single-signature convenience wrapper around _scan_memory_for_signatures."""
    return _scan_memory_for_signatures(pm, [signature])[signature]


def _scan_quest_objects(
    pm, base: int, hits: Optional[List[int]] = None
) -> List[Tuple[int, int, int]]:
    """
    Heap-scan for live kexShadowManQuestObject instances via their shared
    Think()-callback pointer. Returns (item_obj, item_id, state) per
    instance. See the QUEST_THINKFN_RVA comment block above for why this
    only ever yields item TYPE, never a reliable per-slot identity.
    Pass precomputed `hits` (from a combined _scan_memory_for_signatures
    walk) to skip the expensive scan.
    """
    if hits is None:
        hits = _scan_memory_for_signature(
            pm, struct.pack("<Q", base + QUEST_THINKFN_RVA))
    results: List[Tuple[int, int, int]] = []
    for hit_addr in hits:
        item_obj = hit_addr - QUEST_THINKPTR_OFF
        try:
            item_id = pm.read_uint(item_obj + QUEST_ITEMID_LIVE_OFF)
            state   = pm.read_uint(item_obj + QUEST_STATE_LIVE_OFF)
            results.append((item_obj, item_id, state))
        except Exception:
            continue
    return results


def _match_live_position(
    x: float, y: float, z: float, level_id: Optional[str],
    pos_index: List[Tuple[str, float, float, float, int]],
) -> Optional[int]:
    """
    Match a live-read (x, y, z) position against the CSV position index.
    Reuses the same tolerance + ambiguity-margin logic as
    _match_govi_position_scan (see its docstring for the full reasoning) —
    jitter and false-positive risk are properties of the object, not of
    which method (save file vs. live memory) read its position. Also uses
    the same two-check approach: tight X/Z circle (GOVI_POS_TOLERANCE) plus
    a separate, looser Y band (GOVI_Y_TOLERANCE) — see GOVI_Y_TOLERANCE's
    comment for why Y needs its own, much more permissive check rather than
    being dropped or blended into one 3D distance.
    """
    if not level_id:
        return None
    candidates = [k for k in pos_index if k[0] == level_id]
    if not candidates:
        return None

    tol_sq = GOVI_POS_TOLERANCE ** 2
    hits: List[Tuple[float, int]] = []
    for _lvl, kx, ky, kz, ap_id in candidates:
        if abs(y - ky) > GOVI_Y_TOLERANCE:
            continue
        d2 = (x - kx) ** 2 + (z - kz) ** 2
        if d2 <= tol_sq:
            hits.append((d2, ap_id))
    if not hits:
        return None

    hits.sort(key=lambda h: h[0])
    best_d2, best_id = hits[0]

    AMBIGUITY_MARGIN_SQ = 9.0
    other_best_d2 = next((d2 for d2, ap_id in hits if ap_id != best_id), None)
    if other_best_d2 is not None and other_best_d2 < best_d2 * AMBIGUITY_MARGIN_SQ:
        return None

    return best_id


def _read_bookpos_log_entry(pm, base: int, index: int) -> Optional[Tuple[float, float, float, int, int]]:
    """
    Read ONE entry from the pickup event log at BOOKPOS_LOG_BASE_RVA. See
    that constant's large comment block for the full (Ghidra-confirmed)
    layout and derivation. `index` is the permanent, never-reused slot
    number a pickup was assigned (0-based, same value ITEM_PICKUP_COUNTER_RVA
    held immediately before that pickup's call to FUN_14033f510).

    Returns (x, y, z, edx_val, r9d_val) or None on any read failure /
    out-of-range index. edx_val/r9d_val are the raw FUN_14033f510 call
    arguments recorded alongside the position -- r9d_val is expected to
    be BOOKPOS_LOG_QUEST_TAG (1002) for genuine quest-item pickups; see
    that constant's comment for why this isn't used as a hard filter yet.
    """
    if index < 0 or index >= BOOKPOS_LOG_MAX_ENTRIES:
        return None
    try:
        addr = base + BOOKPOS_LOG_BASE_RVA + index * BOOKPOS_LOG_ENTRY_SIZE
        entry = pm.read_bytes(addr, BOOKPOS_LOG_ENTRY_SIZE)
        x, y, z = struct.unpack_from("<fff", entry, 0)
        edx_val = struct.unpack_from("<i", entry, 0x10)[0]
        r9d_val = struct.unpack_from("<i", entry, 0x14)[0]
        return (x, y, z, edx_val, r9d_val)
    except Exception:
        return None


def _read_darksoul_flagarray(pm, base: int) -> Optional[bytes]:
    """
    Read the current full state of the dark-soul collected-flag array. See
    DARKSOUL_FLAGARRAY_PTR_RVA's large comment block above for the full
    (Ghidra-confirmed) derivation: byte index N in the returned bytes
    corresponds directly to save_idx N (not a rank/ordinal) -- 0 = not yet
    collected, 1 = collected, permanently (a real persistent flag array,
    not an append-log or a compacting table, so byte-for-byte diffing
    against a previous read is safe here).

    The pointer at DARKSOUL_FLAGARRAY_PTR_RVA is a live heap address (not
    a stable module-relative RVA) -- read fresh every call rather than
    cached, in case the game ever reallocates it.

    Returns None on any read failure (pointer null/invalid, length reads
    as 0 or absurd, etc).
    """
    try:
        ptr = pm.read_longlong(base + DARKSOUL_FLAGARRAY_PTR_RVA)
        length = pm.read_longlong(base + DARKSOUL_FLAGARRAY_LEN_RVA)
        if not ptr or length <= 0 or length > DARKSOUL_FLAGARRAY_MAX_LEN:
            return None
        return pm.read_bytes(ptr, length)
    except Exception:
        return None


def _build_give_item_shellcode(inventory_ptr: int, item_id: int,
                                give_fn: int) -> bytes:
    """
    ~40-byte x64 shellcode that calls:
        kexShadowManInventoryLocal::GiveItem(this, item_id & 0xFFFF, 0)

    Windows x64 calling convention:
        RCX = this  (inventory_ptr)
        RDX = item_id (u16)
        R8D = 0     (flags)
    """
    sc  = b"\x48\x83\xec\x28"                              # sub rsp, 0x28
    sc += b"\x48\xb9" + struct.pack("<Q", inventory_ptr)   # mov rcx, inv
    sc += b"\xba"     + struct.pack("<I", item_id & 0xFFFF)# mov edx, item_id
    sc += b"\x45\x31\xc0"                                  # xor r8d, r8d
    sc += b"\x48\xb8" + struct.pack("<Q", give_fn)         # mov rax, give_fn
    sc += b"\xff\xd0"                                      # call rax
    sc += b"\x48\x83\xc4\x28"                              # add rsp, 0x28
    sc += b"\xc3"                                          # ret
    return sc


def _build_apply_gad_shellcode(base: int) -> bytes:
    """
    ~40-byte x64 shellcode that calls:
        player_ptr = FUN_140458680()
        FUN_140459d50(player_ptr, 0)

    FIXED 2026-08-02 (Jon: "the standard rando is able to apply gad
    powers instantly on item pickup, maybe we can learn from that?" —
    re-reading the standalone randomizer's own gad_pickup_patch.py in
    response). Previously (2026-07-28) this guessed player_ptr as
    `base + INVENTORY_RVA` — the kexShadowManInventoryLocal singleton —
    explicitly flagged in that version's own docstring as "best available
    candidate, NOT independently confirmed." That guess was wrong.
    gad_pickup_patch.py's `_build_stub()` — the standalone randomizer's
    OWN static patch, which really does make gad pickups apply instantly,
    safely, in normal solo play — spells out its exact native calling
    convention in its own comment: "MOV RCX,RDI / XOR EDX,EDX / CALL
    FUN_140459d50" labeled plainly "-- player ptr". RDI there is a genuine
    PLAYER object pointer the pickup-dispatch function already had
    loaded — not the inventory singleton, a completely different object.

    This file already has a proven, no-arg "get the real live player
    pointer" accessor for exactly this purpose — GET_PLAYER_FN_RVA /
    FUN_140458680() (see DEATH_TRIGGER_FN_RVA's comment block) — already
    used safely via CreateRemoteThread by `_build_death_shellcode()` for
    DeathLink kills, a working, previously live-tested feature, with the
    identical "call the no-arg accessor first, use its return value as
    the real function's first argument" shape. This function now does the
    same two-step sequence instead of guessing a fixed global: call
    FUN_140458680() for the real player_ptr, THEN call
    FUN_140459d50(player_ptr, 0) with it.

    This is a well-evidenced correction, not a new guess — but it hasn't
    been live-tested yet either (this session has no live game/Ghidra
    access). Worth Jon confirming a mid-level `/siminject Gad Power` (or
    `Poigne`) — i.e. NOT within the first
    SECRET_TRAP_MIN_SECONDS_SINCE_CONNECT seconds of connecting, so
    apply_now actually fires — now visibly applies instantly with no
    crash, the one scenario this whole mechanism was built for and never
    actually verified against the right pointer until now.

    Windows x64 calling convention for the outer call:
        RCX = player_ptr (return value of FUN_140458680(), no args)
        RDX = 0
    """
    get_player_fn = base + GET_PLAYER_FN_RVA   # FUN_140458680
    apply_fn      = base + FUN_459D50_RVA      # FUN_140459d50

    sc  = b"\x48\x83\xec\x28"                              # sub  rsp, 0x28

    # step 1 — player_ptr = FUN_140458680()
    sc += b"\x48\xb8" + struct.pack("<Q", get_player_fn)   # mov  rax, get_player_fn
    sc += b"\xff\xd0"                                       # call rax

    # step 2 — FUN_140459d50(player_ptr, 0). Nothing clobbers RAX between
    # the call above and this MOV, so no callee-saved register is needed
    # to ferry player_ptr across (unlike _build_death_shellcode, which has
    # an intervening call and uses RBX for that reason).
    sc += b"\x48\x89\xc1"                                   # mov  rcx, rax  (player_ptr)
    sc += b"\x33\xd2"                                       # xor  edx, edx
    sc += b"\x48\xb8" + struct.pack("<Q", apply_fn)        # mov  rax, apply_fn
    sc += b"\xff\xd0"                                       # call rax

    sc += b"\x48\x83\xc4\x28"                               # add  rsp, 0x28
    sc += b"\xc3"                                            # ret
    return sc


def _remote_exec_shellcode(pm, shellcode: bytes) -> None:
    """Allocate shellcode in the game process and run it via CreateRemoteThread."""
    buf = pm.allocate(len(shellcode))
    try:
        pm.write_bytes(buf, shellcode, len(shellcode))
        h = ctypes.windll.kernel32.CreateRemoteThread(
            pm.process_handle, None, 0,
            ctypes.c_void_p(buf), None, 0, None)
        ctypes.windll.kernel32.WaitForSingleObject(h, 3000)
        ctypes.windll.kernel32.CloseHandle(h)
    finally:
        pm.free(buf)


def _flush_log_handlers() -> None:
    """
    Force every handler in `logger`'s propagation chain to flush to disk
    immediately (2026-08-01).

    Added specifically for crash forensics: the game (thoth_x64_patched.exe)
    and this AP client are separate processes, so a game crash never takes
    the client's log with it -- but if CommonClient's log handler buffers
    writes, the last few lines logged right before a crash could still be
    sitting in memory, un-flushed, at the moment the crash happens,
    producing exactly the "hard to tell when it crashed" gap Jon reported.
    Called right before every injection that executes code via
    CreateRemoteThread (the identified risk class for the recurring ntdll
    heap-corruption crashes -- see CLAUDE.md) so the "about to apply"
    marker logged just before it is durably on disk before the risky call
    ever runs, not just eventually flushed on its own schedule.

    Walks the logger hierarchy manually (from `logger` up through
    `.parent`, stopping at the first non-propagating logger) rather than
    assuming handlers sit on any one specific logger object, since exactly
    where CommonClient attaches its file handler isn't something this file
    controls or wants to hardcode a guess about.
    """
    try:
        lg = logger
        while lg is not None:
            for h in lg.handlers:
                try:
                    h.flush()
                except Exception:
                    pass
            if not lg.propagate:
                break
            lg = lg.parent
    except Exception:
        pass


# Confirmed 2026-07-29 via Ghidra trace -- FUN_1401b0950's RVA:
# VA 0x1401B0950 - IMAGE_BASE 0x140000000 = 0xB0950. This is the engine's
# console command executor: FUN_1401b0950(module_base, text_ptr, 0, 0),
# same "execute this command text" entry point real bound-key presses use
# (its only 3 callers are all inside FUN_1401adea0, the key-binding
# dispatcher). Real calling convention confirmed off working call sites at
# VA 0x1401ae22b/0x1401ae244/0x1401ae2c0, and independently confirmed live
# via a breakpoint on a real Tab press (R8=R9=0, module_base param proven
# UNUSED by the function body so its value never matters). See CLAUDE.md
# ("Deadside Guns secret force-on") for the full trace: cvar registration
# (FUN_140035ef0) -> getter (FUN_1401b82d0, string-backed dead end) ->
# pivoted to the kexengine.cfg LOAD path -> FUN_140202cb0 -> FUN_1401b0830
# -> FUN_1401b0330 (tokenizer, shared with FUN_1401b0950) -> FUN_1401b6be0
# (cvar lookup) -> FUN_1401b6d00 (setter).
CONSOLE_EXEC_FN_RVA = 0xB0950


# ── Direct cvar memory access (2026-07-30) ──────────────────────────────────
#
# Supersedes send_console_command/thread-hijacking (below) for reading or
# forcing a plain single-instance bool secret cvar (g_dogmode, etc.) -- see
# CLAUDE.md "Pure-static (Ghidra-only) trace of the secret-mode cvar system"
# for the full derivation. No code execution at all: plain memory reads/
# writes to a resolved cvar object, the same class of operation as
# SOUL_COUNT_RVA/CADEAUX_COUNT_RVA above, not a call into engine code.
#
# Cvar object struct (offsets from the object's own base address, confirmed
# via the setter/getter decompiles in CLAUDE.md):
#   +0x18   flags (bit0=bool, bit1=int, bit2=float, bit11=multi-instance)
#   +0x60   -> array of 16-byte {char* valueString, byte dirty} slots
#              (index 0 for single-instance cvars) -- the STRING side; NOT
#              touched by the helpers below, so a raw write here can drift
#              from what a menu/printout displays.
#   +0x78   -> array of 8-byte {int32 cacheTag, int32 cachedValue} slots
#              (index 0 for single-instance cvars) -- cacheTag==1 means
#              "trust cachedValue's low byte for a bool cvar"; anything
#              else means "stale, the engine will recompute from the
#              string side on its next own read."
#
# CONFIRMED STABLE (2026-07-30, two separate full game restarts, identical
# both times) via a live Cheat Engine capture: g_dogmode's cvar object sits
# at a fixed RVA baked into the executable's own image, NOT a heap
# allocation -- so it never needs a runtime lookup, same as any other RVA
# in this file.
DOGMODE_CVAR_RVA = 0xDBA740   # base + this = g_dogmode's cvar object


def _cvar_bool_slot_addr(pm, base: int, cvar_rva: int) -> int:
    """
    Resolve the address of the 8-byte {cacheTag, cachedValue} slot for a
    plain single-instance bool cvar at `cvar_rva`: one pointer dereference
    from the cvar object's own +0x78 field (element 0 IS the array base,
    since single-instance cvars always use index 0).
    """
    return pm.read_longlong(base + cvar_rva + 0x78)


def read_cvar_bool(pm, base: int, cvar_rva: int) -> Optional[bool]:
    """
    Read a plain single-instance bool cvar's live value directly from
    memory -- no code execution. Returns None on failure, including if the
    cache tag hasn't been populated yet (0 from process boot until the
    engine's own code has read this cvar at least once -- see CLAUDE.md).
    """
    try:
        slot = _cvar_bool_slot_addr(pm, base, cvar_rva)
        tag = pm.read_int(slot)
        if tag != 1:
            logger.warning(f"[ShadowMan] read_cvar_bool(rva={cvar_rva:#x}): "
                            f"cache tag={tag}, not yet populated by the engine.")
            return None
        return pm.read_uchar(slot + 4) != 0
    except Exception as exc:
        logger.warning(f"[ShadowMan] read_cvar_bool(rva={cvar_rva:#x}) failed: {exc}")
        return None


def write_cvar_bool(pm, base: int, cvar_rva: int, value: bool) -> bool:
    """
    Force a plain single-instance bool cvar's live cached value directly in
    memory -- no code execution, so no on-change callback fires (see
    CLAUDE.md: for g_dogmode this took effect on the next level transition,
    not instantly, since whatever applies the visible effect apparently
    only re-checks the cvar at level load). Explicitly sets BOTH the cache
    tag (to 1, "valid") and the value byte -- writing only the value byte
    risks the engine's next natural read silently recomputing from the
    (untouched, stale) string side and discarding this write.
    """
    try:
        slot = _cvar_bool_slot_addr(pm, base, cvar_rva)
        pm.write_int(slot, 1)                              # cache tag = valid
        pm.write_bytes(slot + 4, bytes([1 if value else 0]), 1)
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] write_cvar_bool(rva={cvar_rva:#x}, {value}) failed: {exc}")
        return False


# Confirmed 2026-07-30 via a live memory read (pure data read, zero
# execution risk): g_dogmode's cvar object's +0x48 field (the on-change
# callback slot identified in the cvar.cpp trace) reads exactly
# 0x140458EF0. That address decompiles to a function that: walks a table
# of mutually-exclusive g_<name>mode secrets and force-turns-off whichever
# one conflicts with the cvar that was just changed (via FUN_1401b6350,
# the real setter, called directly with a literal string -- NOT through
# FUN_1401b6a60, the numeric-to-string path that touches
# ThreadLocalStoragePointer, so the TLS concern from the thread-hijacking
# section above does not apply to this call); then, gated behind cutscene/
# loading/save-state checks, calls FUN_140459250 (the mesh/skin reload)
# with the player's position carefully saved before and restored after.
# This is almost certainly the real, sanctioned "apply a secret toggle
# live" path -- the same thing the engine's own SetValue pipeline invokes
# automatically, which our raw memory writes above bypass.
#
# CAUTION (2026-07-30): calling this is still live code execution into a
# call chain that has NOT been fully vetted end to end -- FUN_140459250's
# own sub-functions (resource loading, heap allocation) are unverified for
# thread-safety, and mesh/skin loading is exactly the kind of thing that
# can carry GPU/render-thread-affinity requirements that wouldn't show up
# in a decompile. This repo has already hit three live-execution failures
# investigating this exact area (a console-pipeline crash, a thread-
# hijack alignment crash, and a thread-hijack GPU-driver-transition
# freeze that took down the whole computer, not just the game -- see
# CLAUDE.md). This call uses a plain CreateRemoteThread (not hijacking),
# which sidesteps the alignment/driver-suspension risk specifically, and
# targets a single named function with one argument rather than the huge
# console interpreter -- but "meaningfully different from the things that
# already failed" is not the same as "proven safe." Jon's own informed
# call, after the tradeoffs were laid out plainly: try it anyway. Logged
# here so a future session has the full picture, not just the code.
MODE_CVAR_ONCHANGE_CALLBACK_RVA = 0x458EF0   # base + this = FUN_140458ef0


def _build_mode_cvar_callback_shellcode(module_base: int, cvar_handle: int) -> bytes:
    """FUN_140458ef0(cvar_handle) -- single argument, in RCX."""
    sc  = b"\x48\x83\xec\x28"                                                  # sub rsp, 0x28
    sc += b"\x48\xb9" + struct.pack("<Q", cvar_handle)                        # mov rcx, cvar_handle
    sc += b"\x48\xb8" + struct.pack("<Q", module_base + MODE_CVAR_ONCHANGE_CALLBACK_RVA)  # mov rax, callback_fn
    sc += b"\xff\xd0"                                                          # call rax
    sc += b"\x48\x83\xc4\x28"                                                  # add rsp, 0x28
    sc += b"\xc3"                                                              # ret
    return sc


def apply_mode_cvar_live(pm, base: int, cvar_rva: int) -> bool:
    """
    Invoke the real on-change callback (FUN_140458ef0) for a g_<name>mode
    secret cvar, the same thing the engine's own SetValue pipeline runs
    automatically -- so a raw write_cvar_bool() + this call together should
    reproduce the exact effect of toggling the secret through the game's
    own normal path (mutual-exclusion cleanup, model reload, position
    preserved), with no level transition needed. EXPERIMENTAL, live code
    execution -- see the CAUTION note above MODE_CVAR_ONCHANGE_CALLBACK_RVA.
    """
    try:
        handle = base + cvar_rva
        sc = _build_mode_cvar_callback_shellcode(base, handle)
        _remote_exec_shellcode(pm, sc)
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] apply_mode_cvar_live(rva={cvar_rva:#x}) failed: {exc}")
        return False


# ── Secret cvar handle table (2026-07-31) ───────────────────────────────────
#
# Captured live via Cheat Engine: breakpoint set at FUN_1401b6be0's single
# RET instruction (VA 0x1401B6CEF, found from the function's own Ghidra
# listing -- both its "found" and "not found" paths converge to one shared
# epilogue before this RET, and nothing between either path and the RET
# touches RAX), rather than the function's entry. Entry-based capture
# raced: RAX doesn't hold the return value yet at entry, "run till return"
# wasn't reliably landing past whatever internal calls this function makes
# (memset, a couple of CRT string calls), and the SAME lookup
# (g_area51baddys) captured two different RAX values across two attempts
# before this fix -- see CLAUDE.md. Opening the pause menu's Secrets list
# fires this breakpoint once per registered secret, in a fixed table
# order, so one capture pass got the whole list.
#
# g_dogmode's own row (0xDBA740) is an EXACT match to the independently
# confirmed, already-live-tested value used elsewhere in this file
# (DOGMODE_CVAR_RVA) -- validates the capture method. Most entries also
# fall on one consistent 0x90-byte stride from their neighbors, consistent
# with a fixed-size static cvar array; a handful (peasoupmode, the
# area51baddys/area51music pair, deadsidegunmode, wireframemode, vertigo,
# alphaitems) sit outside that lattice -- plausibly registered in a
# different part of the same table, not yet investigated further.
#
# NOT YET CONFIRMED: each cvar's own on-change callback at [handle+0x48].
# Do NOT assume any of these share g_dogmode's callback
# (MODE_CVAR_ONCHANGE_CALLBACK_RVA / FUN_140458ef0) without checking --
# that's exactly what dump_secret_cvar_callbacks() below is for. Nothing
# here is wired into the live-apply dispatcher yet.
IMAGE_BASE = 0x140000000

SECRET_CVAR_RVAS = {
    "g_bigheadmode":      0x140DB9FF0 - IMAGE_BASE,
    "g_discoclothes":     0x140DBA080 - IMAGE_BASE,
    "g_discomusic":       0x140DBA110 - IMAGE_BASE,
    "g_discolights":      0x140DBA1A0 - IMAGE_BASE,
    "g_flameonmode":      0x140DBA230 - IMAGE_BASE,
    "g_peasoupmode":      0x140DBCE20 - IMAGE_BASE,
    "g_bigshoesmode":     0x140DBA2C0 - IMAGE_BASE,
    "g_stetsonmode":      0x140DBA350 - IMAGE_BASE,
    "g_shotgunheadmode":  0x140DBA3E0 - IMAGE_BASE,
    "g_trippymode":       0x140DBA470 - IMAGE_BASE,
    "g_invisiblemode":    0x140DBA500 - IMAGE_BASE,
    "g_area51baddys":     0x140DB29E0 - IMAGE_BASE,
    "g_area51music":      0x140DB2950 - IMAGE_BASE,
    "g_nettiemode":       0x140DBA590 - IMAGE_BASE,
    "g_duppiemode":       0x140DBA620 - IMAGE_BASE,
    "g_deadwingmode":     0x140DBA6B0 - IMAGE_BASE,
    "g_dogmode":          0x140DBA740 - IMAGE_BASE,   # == DOGMODE_CVAR_RVA, confirms the method
    "g_betamode":         0x140DBA860 - IMAGE_BASE,
    "g_deadsidegunmode":  0x140D91D40 - IMAGE_BASE,
    "g_wireframemode":    0x140DBCEB0 - IMAGE_BASE,
    "g_paintballmode":    0x140DBA980 - IMAGE_BASE,
    "g_alphaitems":       0x140DB9E90 - IMAGE_BASE,
    "g_vertigo":          0x140DB7450 - IMAGE_BASE,
}


def read_cvar_callback(pm, base: int, cvar_rva: int) -> Optional[int]:
    """
    Pure memory read of a cvar object's +0x48 field -- the on-change
    callback function pointer, per the cvar.cpp struct-layout trace (see
    CLAUDE.md). Returns the callback's absolute VA, or None on failure.
    No code execution.
    """
    try:
        return pm.read_longlong(base + cvar_rva + 0x48)
    except Exception as exc:
        logger.warning(f"[ShadowMan] read_cvar_callback(rva={cvar_rva:#x}) failed: {exc}")
        return None


def dump_secret_cvar_callbacks(pm, base: int) -> dict:
    """
    Read [handle+0x48] for every entry in SECRET_CVAR_RVAS in one pass --
    pure memory reads, zero execution risk. Returns {name: callback_va or
    None}. Used by /cvarcallbacks to see which secrets share g_dogmode's
    already-confirmed callback (FUN_140458ef0) vs. which have their own
    distinct one that still needs independent verification before it's
    safe to wire into the live-apply dispatcher.
    """
    results = {}
    for name, rva in SECRET_CVAR_RVAS.items():
        results[name] = read_cvar_callback(pm, base, rva)
    return results


def read_cvar_name(pm, base: int, cvar_rva: int, max_len: int = 48) -> Optional[str]:
    """
    Pure memory read of a cvar object's +0x00 field -- the cvar's own name
    string pointer, per the cvar.cpp struct-layout trace (see CLAUDE.md).
    Reads up to max_len bytes at that pointer and returns the portion
    before the first NUL, decoded as ASCII. Returns None on failure. No
    code execution -- exists purely to sanity-check that a hardcoded
    SECRET_CVAR_RVAS/SECRET_TABLE address actually points at the cvar its
    own name claims, rather than a mis-captured neighbor (added 2026-08-01
    after two ntdll heap-corruption crashes surfaced while the 9-entry EXE
    poller was active -- a single missed/duplicated breakpoint hit during
    the original CE capture session would silently shift every name after
    it by one slot, while every recorded address would still look like a
    real, valid, internally-consistent cvar handle with no way to tell
    from the address alone -- see CLAUDE.md's 2026-08-01 writeup).
    """
    try:
        name_ptr = pm.read_longlong(base + cvar_rva + 0x00)
        if not name_ptr:
            return None
        raw = pm.read_bytes(name_ptr, max_len)
        return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")
    except Exception as exc:
        logger.warning(f"[ShadowMan] read_cvar_name(rva={cvar_rva:#x}) failed: {exc}")
        return None


def dump_secret_cvar_names(pm, base: int) -> dict:
    """
    Read [handle+0x00] (the cvar's own name string) for every entry in
    SECRET_CVAR_RVAS -- pure memory reads, zero execution risk. Returns
    {expected_name: actual_name_read_from_memory_or_None}. Used by
    /cvarnames to directly confirm or refute a mis-assigned address in
    SECRET_CVAR_RVAS/SECRET_TABLE.
    """
    results = {}
    for name, rva in SECRET_CVAR_RVAS.items():
        results[name] = read_cvar_name(pm, base, rva)
    return results


# ── Secret-mode EXE poller forensics (2026-08-01) ───────────────────────────
#
# Pure read-only visibility into secret_mode_section_patch.py's LAST_KNOWN
# array (.apdata) -- the EXE-side poller's own record of the last cvar
# value it observed for each of its 9 watched entries. The poller itself
# has no logging of its own (it's raw machine code with nowhere to print
# to), so this is the only way to see what it's actually been doing.
# Added after two ntdll heap-corruption crashes surfaced with the poller
# active and no confirmed root cause (the leading "mis-assigned address"
# theory was ruled out via /cvarnames/read_cvar_name above) -- Jon chose
# to re-enable the poller and gather more data rather than keep guessing
# blind; this is that data-gathering.
#
# Order MUST exactly match secret_mode_section_patch.py's own SECRET_TABLE
# -- LAST_KNOWN[i] in .apdata corresponds 1:1 to this list's index i.
POLLER_SECRET_TABLE = [
    "g_bigheadmode", "g_discoclothes", "g_bigshoesmode", "g_stetsonmode",
    "g_nettiemode", "g_duppiemode", "g_deadwingmode", "g_dogmode", "g_betamode",
]
# secret_mode_section_patch.py: NEW_DATA_VA = 0x14102C000, LAST_KNOWN_VA =
# NEW_DATA_VA + 0x08. RVA form (relative to IMAGE_BASE) for use with the
# base+rva convention every other read_cvar_* helper here already follows.
LAST_KNOWN_RVA = 0x102C000 + 0x08
LAST_KNOWN_POLLER_SENTINEL = 0xFF  # "never checked yet" -- see secret_mode_section_patch.py


def read_poller_last_known(pm, base: int) -> Optional[bytes]:
    """
    Pure memory read of the EXE poller's LAST_KNOWN array -- one byte per
    POLLER_SECRET_TABLE entry, in order. Returns None on any read failure
    (including "poller patch isn't applied to this process" -- in that
    case this RVA holds whatever raw bytes happen to sit there in an
    unpatched exe, so a caller should treat None/a read exception as
    "can't tell", not as a meaningful "all clean" signal). No code
    execution -- same safety class as read_cvar_bool/read_cvar_name.
    """
    try:
        return pm.read_bytes(base + LAST_KNOWN_RVA, len(POLLER_SECRET_TABLE))
    except Exception as exc:
        logger.warning(f"[ShadowMan] read_poller_last_known failed: {exc}")
        return None


# ── Secret Trap AP item (2026-08-01) ────────────────────────────────────────
#
# The 18 of 23 captured secrets confirmed live-working via /secret with
# nothing more than a plain write_cvar_bool() -- 9 via the
# secret_mode_section_patch.py EXE poller (SECRET_TABLE in that file), 9
# more "for free" since their own [handle+0x48] callback is null (checked
# continuously by their own consumer subsystem, no poller/callback needed
# at all). See CLAUDE.md's 2026-07-31 writeups for the full derivation.
# Deliberately excludes the 5 still-unconfirmed secrets (g_shotgunheadmode,
# g_area51baddys, g_wireframemode, g_alphaitems, g_vertigo) -- either their
# own on-change callback is independently unverified (bucket 3, per the
# standing caution on calling unverified game code), or Jon couldn't
# confirm the effect was actually visible when tested.
# g_deadsidegunmode deliberately removed (2026-08-01) -- was here as one of
# the confirmed-safe null-callback bucket (see the "Generalizing past
# g_dogmode" CLAUDE.md writeup: `/secret g_deadsidegunmode 1` applied
# cleanly in isolated manual testing), but Jon reported a crash landing
# right around either this secret or a Gad Power/Poigne injection during a
# real Secret Trap-driven backlog session. Unlike every other entry here
# (pure cosmetic model/render toggles), this cvar changes actual weapon-
# compatibility LOGIC (Deadside weapons usable on Liveside and vice versa)
# and has by far the most fraught history in this codebase -- the entire
# "Deadside Guns secret force-on" investigation (console-command crash,
# thread-hijack alignment bug, thread-hijack GPU-driver freeze that took
# down Jon's whole computer) was about this exact cvar, before the safe
# write_cvar_bool()-only path was ever found. Manual isolated testing
# clearing it once isn't strong enough evidence to keep it in a pool that
# fires unpredictably mid-gameplay, especially once a live crash actually
# coincided with it. Pulled rather than re-investigated -- 17 other
# confirmed-safe purely-cosmetic secrets remain, plenty of variety without
# this one.
TRAP_SAFE_SECRETS = [
    "g_bigheadmode", "g_discoclothes", "g_bigshoesmode", "g_stetsonmode",
    "g_nettiemode", "g_duppiemode", "g_deadwingmode", "g_dogmode", "g_betamode",
    "g_discomusic", "g_discolights", "g_flameonmode", "g_peasoupmode",
    "g_trippymode", "g_invisiblemode", "g_area51music",
    "g_paintballmode",
]

# Human-readable labels for the overlay toast / log lines (2026-08-03, Jon:
# "the pop-up just says secret trap received but now which secret trap").
# Derived from each cvar's own name, not independently confirmed against
# the real in-game pause-menu label strings (the pure-static trace found
# FUN_1404128b0 -- the Secrets List UI builder -- reads real menu text from
# a per-secret format-descriptor table, which would be the authoritative
# source if this ever needs correcting from a live capture). Display-only,
# zero effect on which cvar actually gets written -- safe to fix later.
SECRET_DISPLAY_NAMES: dict[str, str] = {
    "g_bigheadmode":     "Big Head",
    "g_discoclothes":    "Disco Clothes",
    "g_bigshoesmode":    "Big Shoes",
    "g_stetsonmode":     "Stetson",
    "g_nettiemode":      "Nettie",
    "g_duppiemode":      "Duppie",
    "g_deadwingmode":    "Dead Wing",
    "g_dogmode":         "Dog",
    "g_betamode":        "Beta",
    "g_discomusic":      "Disco Music",
    "g_discolights":     "Disco Lights",
    "g_flameonmode":     "Flame On",
    "g_peasoupmode":     "Pea Soup",
    "g_trippymode":      "Trippy",
    "g_invisiblemode":   "Invisible",
    "g_area51music":     "Area 51 Music",
    "g_paintballmode":   "Paintball",
}


# ── Thread hijacking (2026-07-29) ───────────────────────────────────────────
#
# A brand-new CreateRemoteThread-spawned thread calling FUN_1401b0950
# crashed the game to desktop on EVERY live test -- including a plain
# "inventory" command, already proven 100% safe on that exact function via
# a real Tab keypress captured live in a debugger. Every argument was
# independently verified correct first (byte-perfect shellcode encoding,
# RVA/module-base math matching the real breakpoint address, R8=R9=0
# confirmed against a real working call, the module_base parameter proven
# unused by the function body). The enter/leave calls around the function's
# internal critical section were also checked and are plain generic
# _Mtx_lock/_Mtx_unlock wrappers -- no per-thread bookkeeping, safe from any
# thread. With every other variable eliminated, the freshly-created thread
# itself is the remaining explanation.
#
# Fix: don't create a new thread at all. Suspend one of the game's OWN
# already-running threads, point it at a trampoline that saves every
# register, makes the call, restores every register + RFLAGS + volatile
# XMM0-5, and jumps back to the exact original RIP, then resume the thread
# normally. The interrupted thread never "knows" it ran anything else.
#
# EXPERIMENTAL, NOT YET LIVE-TESTED as of this writing.

TH32CS_SNAPTHREAD = 0x00000004
THREAD_SUSPEND_RESUME = 0x0002
THREAD_GET_CONTEXT = 0x0008
THREAD_SET_CONTEXT = 0x0010
_THREAD_HIJACK_ACCESS = THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_SET_CONTEXT

CONTEXT_AMD64 = 0x00100000
CONTEXT_CONTROL = CONTEXT_AMD64 | 0x1
CONTEXT_INTEGER = CONTEXT_AMD64 | 0x2
CONTEXT_FULL = CONTEXT_CONTROL | CONTEXT_INTEGER


class _M128A(ctypes.Structure):
    _fields_ = [("Low", ctypes.c_uint64), ("High", ctypes.c_int64)]


class _CONTEXT(ctypes.Structure):
    """
    Mirror of Windows' AMD64 CONTEXT struct (winnt.h). Only the control +
    integer registers are individually typed since that's all we request
    (CONTEXT_CONTROL | CONTEXT_INTEGER) and all we touch; the floating/XMM/
    vector/debug-trace regions are opaque byte blobs of the correct size
    purely so the struct's total size and the offsets of the fields we DO
    use match the real OS structure. Layout/size (0x4D0 / 1232 bytes) is
    the standard, long-stable AMD64 CONTEXT layout.
    """
    _fields_ = [
        ("P1Home", ctypes.c_uint64), ("P2Home", ctypes.c_uint64),
        ("P3Home", ctypes.c_uint64), ("P4Home", ctypes.c_uint64),
        ("P5Home", ctypes.c_uint64), ("P6Home", ctypes.c_uint64),
        ("ContextFlags", ctypes.c_uint32), ("MxCsr", ctypes.c_uint32),
        ("SegCs", ctypes.c_uint16), ("SegDs", ctypes.c_uint16),
        ("SegEs", ctypes.c_uint16), ("SegFs", ctypes.c_uint16),
        ("SegGs", ctypes.c_uint16), ("SegSs", ctypes.c_uint16),
        ("EFlags", ctypes.c_uint32),
        ("Dr0", ctypes.c_uint64), ("Dr1", ctypes.c_uint64),
        ("Dr2", ctypes.c_uint64), ("Dr3", ctypes.c_uint64),
        ("Dr6", ctypes.c_uint64), ("Dr7", ctypes.c_uint64),
        ("Rax", ctypes.c_uint64), ("Rcx", ctypes.c_uint64),
        ("Rdx", ctypes.c_uint64), ("Rbx", ctypes.c_uint64),
        ("Rsp", ctypes.c_uint64), ("Rbp", ctypes.c_uint64),
        ("Rsi", ctypes.c_uint64), ("Rdi", ctypes.c_uint64),
        ("R8", ctypes.c_uint64), ("R9", ctypes.c_uint64),
        ("R10", ctypes.c_uint64), ("R11", ctypes.c_uint64),
        ("R12", ctypes.c_uint64), ("R13", ctypes.c_uint64),
        ("R14", ctypes.c_uint64), ("R15", ctypes.c_uint64),
        ("Rip", ctypes.c_uint64),
        ("FltSave", ctypes.c_ubyte * 512),
        ("VectorRegister", _M128A * 26),
        ("VectorControl", ctypes.c_uint64),
        ("DebugControl", ctypes.c_uint64),
        ("LastBranchToRip", ctypes.c_uint64),
        ("LastBranchFromRip", ctypes.c_uint64),
        ("LastExceptionToRip", ctypes.c_uint64),
        ("LastExceptionFromRip", ctypes.c_uint64),
    ]


class _THREADENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32), ("cntUsage", ctypes.c_uint32),
        ("th32ThreadID", ctypes.c_uint32), ("th32OwnerProcessID", ctypes.c_uint32),
        ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
        ("dwFlags", ctypes.c_uint32),
    ]


def _list_thread_ids(pid: int) -> list:
    """All thread IDs currently belonging to process `pid`, via Toolhelp32."""
    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap in (0, -1):
        return []
    tids = []
    try:
        te = _THREADENTRY32()
        te.dwSize = ctypes.sizeof(_THREADENTRY32)
        ok = kernel32.Thread32First(snap, ctypes.byref(te))
        while ok:
            if te.th32OwnerProcessID == pid:
                tids.append(te.th32ThreadID)
            ok = kernel32.Thread32Next(snap, ctypes.byref(te))
    finally:
        kernel32.CloseHandle(snap)
    return tids


TH32CS_SNAPMODULE = 0x00000008


class _MODULEENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_uint32), ("th32ModuleID", ctypes.c_uint32),
        ("th32ProcessID", ctypes.c_uint32), ("GlblcntUsage", ctypes.c_uint32),
        ("ProccntUsage", ctypes.c_uint32), ("modBaseAddr", ctypes.c_void_p),
        ("modBaseSize", ctypes.c_uint32), ("hModule", ctypes.c_void_p),
        ("szModule", ctypes.c_char * 256), ("szExePath", ctypes.c_char * 260),
    ]


def _get_module_size(pid: int, module_name: str) -> int:
    """SizeOfImage for `module_name` in process `pid`, or 0 if not found."""
    kernel32 = ctypes.windll.kernel32
    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPMODULE, pid)
    if snap in (0, -1):
        return 0
    try:
        me = _MODULEENTRY32()
        me.dwSize = ctypes.sizeof(_MODULEENTRY32)
        ok = kernel32.Module32First(snap, ctypes.byref(me))
        while ok:
            if me.szModule.decode(errors="ignore").lower() == module_name.lower():
                return me.modBaseSize
            ok = kernel32.Module32Next(snap, ctypes.byref(me))
    finally:
        kernel32.CloseHandle(snap)
    return 0


def _open_hijackable_thread(pid: int, base: int, module_size: int,
                             retry_seconds: float = 5.0, retry_interval: float = 0.25):
    """
    Suspend candidate threads belonging to `pid` (lowest thread ID first)
    until one is found whose captured RIP actually falls inside
    [base, base+module_size) -- i.e. it's genuinely mid-execution of the
    game's own code right now, not idling deep in a kernel-mode wait
    (ntdll/kernel32 blocking calls, common for audio/network/worker
    threads, which spend most of their time NOT executing game code at
    all).

    CONFIRMED NECESSARY (2026-07-29): the original "just pick the lowest
    thread ID, no verification" version produced a hijack that neither
    crashed NOR had any visible effect, with the trampoline's own
    completion flag never getting set -- consistent with our shellcode
    never actually running because the picked thread was suspended
    mid-syscall and silently didn't honor the redirected RIP on resume.
    Any thread that fails to open, or whose RIP doesn't qualify, is
    immediately resumed (if suspended) and released before trying the
    next-lowest candidate, so nothing is left dangling suspended.

    Returns (handle, tid, ctx) for the first qualifying thread -- already
    suspended, with `ctx` the GetThreadContext snapshot already taken (so
    the caller doesn't need to re-fetch it) -- or (None, None, None).

    RETRY LOOP added 2026-07-29 -- a single pass came back empty on the
    first live test (no thread's RIP happened to be inside the module at
    that one instant). Two live hypotheses for why, not yet distinguished:
    (1) plain bad luck -- most threads spend most of their time blocked
    in OS/driver calls, so a single sequential single-instant check per
    thread can plausibly miss every thread's brief "hot" window; (2) the
    game throttles/blocks its main loop while its window isn't focused
    (there's no way to have the AP client's console focused to type
    `/console` AND the game window focused at the same instant), which
    would make literally every thread idle almost permanently until focus
    returns. This retry loop distinguishes cheaply between "needs more
    tries" and "structurally can't work without forcing focus" without
    writing any window-focus/HWND code yet -- if retrying while the user
    alt-tabs back into the game succeeds, that's hypothesis 2 confirmed
    without having built anything new; if it still always fails, that
    rules out plain bad luck as the sole explanation.
    """
    kernel32 = ctypes.windll.kernel32
    deadline = time.monotonic() + retry_seconds
    attempt = 0
    while True:
        attempt += 1
        for tid in sorted(_list_thread_ids(pid)):
            h = kernel32.OpenThread(_THREAD_HIJACK_ACCESS, False, tid)
            if not h:
                continue
            if kernel32.SuspendThread(h) == 0xFFFFFFFF:
                kernel32.CloseHandle(h)
                continue
            ctx_raw, ctx = _alloc_aligned_context()
            ctx.ContextFlags = CONTEXT_FULL
            got = kernel32.GetThreadContext(h, ctypes.byref(ctx))
            if got and base <= ctx.Rip < base + module_size:
                if attempt > 1:
                    logger.info(f"[ShadowMan] _open_hijackable_thread: found a candidate on attempt {attempt}.")
                return h, tid, (ctx_raw, ctx)
            kernel32.ResumeThread(h)
            kernel32.CloseHandle(h)
        if time.monotonic() >= deadline:
            return None, None, None
        time.sleep(retry_interval)


def _enc_mov_r64_imm64(reg: int, imm: int) -> bytes:
    """mov r64, imm64. reg: 0=RAX 1=RCX 2=RDX 3=RBX 4=RSP 5=RBP 6=RSI 7=RDI 8..15=R8..R15."""
    rex = 0x48 | (0x01 if reg >= 8 else 0x00)
    return bytes([rex, 0xB8 + (reg & 0x7)]) + struct.pack("<Q", imm & 0xFFFFFFFFFFFFFFFF)


def _enc_push_r64(reg: int) -> bytes:
    """push r64."""
    if reg >= 8:
        return bytes([0x41, 0x50 + (reg & 0x7)])
    return bytes([0x50 + reg])


def _enc_movdqu_stack(reg: int, disp8: int, store: bool) -> bytes:
    """
    movdqu [rsp+disp8], xmmN (store=True) or movdqu xmmN, [rsp+disp8]
    (store=False). reg must be 0-7 (only volatile XMM0-XMM5 are ever used
    here, so no REX.R extension case is implemented).
    """
    assert 0 <= reg <= 7
    opcode = 0x7F if store else 0x6F
    modrm = 0x44 | (reg << 3)   # mod=01 (disp8), reg=xmmN, rm=100 (SIB follows)
    sib = 0x24                  # scale=0, index=none, base=RSP
    return bytes([0xF3, 0x0F, opcode, modrm, sib, disp8 & 0xFF])


def _build_hijack_trampoline_shellcode(ctx: "_CONTEXT", module_base: int, text_ptr: int,
                                        exec_fn: int, done_flag_ptr: int) -> bytes:
    """
    Runs at the exact RIP of a hijacked (suspended) game thread. Calls
    FUN_1401b0950(module_base, text_ptr, 0, 0), then restores every
    general-purpose register, RFLAGS, and volatile XMM0-XMM5 to their exact
    pre-hijack values (captured in `ctx`, a GetThreadContext snapshot taken
    before any of this runs -- FUN_1401b0950 memsets a 64KB buffer, likely
    vectorized, so volatile XMM state can't be assumed untouched), writes 1
    byte to `done_flag_ptr`, and jumps back to the thread's original RIP --
    so the hijacked thread resumes exactly as if it had never been
    interrupted.

    Stack layout while our own call is in flight: [rsp+0x00..0x1F) is left
    untouched as the callee's own shadow space (required by the Windows x64
    ABI for any call, and FUN_1401b0950's own prologue spills incoming
    registers into exactly this region) -- our XMM spill lives ABOVE that,
    at [rsp+0x20..0x80), so the callee can't clobber it.

    RSP itself is never explicitly restored: every scratch-stack push/sub
    this trampoline does is exactly balanced by a matching pop/add before
    the final jump, so RSP returns to its original value on its own.

    STACK ALIGNMENT (fixed 2026-07-29, see CLAUDE.md "thread hijacking
    trampoline crash" for the live-test result that motivated this): a
    freshly created thread (CreateRemoteThread) is always handed 16-byte-
    aligned RSP by the OS, but a HIJACKED thread's RSP is whatever it
    happened to be at its arbitrary suspension point -- unknown, and not
    guaranteed aligned. `sub rsp, 0x80` above only preserves whatever
    alignment already existed (0x80 is itself a multiple of 16); it does
    NOT force one. The Windows x64 ABI requires RSP % 16 == 0 immediately
    before `call`, and anything using SSE/AVX-aligned stack access
    anywhere in FUN_1401b0950's call graph (e.g. the 64KB memset inside
    FUN_1401af8e0) can fault if that invariant is silently violated on
    the ~50% of hijacks where the interrupted RSP happened to be
    misaligned. Fixed by explicitly forcing alignment only around the
    call itself (`mov r10,rsp` / `and rsp,-16` / ... / `mov rsp,r10`) --
    r10 is safe to clobber for this since it's already fully restored
    from `ctx.R10` later, and restoring RSP from the r10-saved value
    afterward (rather than relying on `sub`/`add` arithmetic symmetry)
    correctly undoes any adjustment `and` made, regardless of how much it
    was.

    EXPERIMENTAL (2026-07-29) -- first live test (before this alignment
    fix) found a hijackable thread and ran the trampoline, but the game
    crashed; this fix has not yet been live-tested itself.
    """
    sc = b""
    sc += b"\x48\x81\xec\x80\x00\x00\x00"                       # sub rsp, 0x80
    for i in range(6):                                          # spill xmm0-5 above shadow space
        sc += _enc_movdqu_stack(i, 0x20 + i * 0x10, store=True)

    sc += b"\x49\x89\xe2"                                       # mov r10, rsp   (save pre-align rsp)
    sc += b"\x48\x83\xe4\xf0"                                   # and rsp, -16   (force 16-byte align for the call)

    sc += _enc_mov_r64_imm64(1, module_base)                    # mov rcx, module_base
    sc += _enc_mov_r64_imm64(2, text_ptr)                       # mov rdx, text_ptr
    sc += b"\x45\x31\xc0"                                       # xor r8d, r8d
    sc += b"\x45\x31\xc9"                                       # xor r9d, r9d
    sc += _enc_mov_r64_imm64(0, exec_fn)                        # mov rax, exec_fn
    sc += b"\xff\xd0"                                           # call rax

    sc += b"\x4c\x89\xd4"                                       # mov rsp, r10   (undo the alignment adjustment exactly)

    for i in range(6):                                          # restore xmm0-5
        sc += _enc_movdqu_stack(i, 0x20 + i * 0x10, store=False)
    sc += b"\x48\x81\xc4\x80\x00\x00\x00"                       # add rsp, 0x80

    sc += _enc_mov_r64_imm64(1, done_flag_ptr)                  # mov rcx, done_flag_ptr (scratch; restored below)
    sc += b"\xc6\x01\x01"                                       # mov byte ptr [rcx], 1

    sc += _enc_mov_r64_imm64(10, ctx.EFlags)                    # mov r10, orig_eflags
    sc += _enc_push_r64(10)                                     # push r10
    sc += b"\x9d"                                               # popfq

    sc += _enc_mov_r64_imm64(11, ctx.Rip)                       # mov r11, orig_rip
    sc += _enc_push_r64(11)                                     # push r11 (for the final `ret`)

    sc += _enc_mov_r64_imm64(0, ctx.Rax)
    sc += _enc_mov_r64_imm64(3, ctx.Rbx)
    sc += _enc_mov_r64_imm64(2, ctx.Rdx)
    sc += _enc_mov_r64_imm64(6, ctx.Rsi)
    sc += _enc_mov_r64_imm64(7, ctx.Rdi)
    sc += _enc_mov_r64_imm64(5, ctx.Rbp)
    sc += _enc_mov_r64_imm64(8, ctx.R8)
    sc += _enc_mov_r64_imm64(9, ctx.R9)
    sc += _enc_mov_r64_imm64(12, ctx.R12)
    sc += _enc_mov_r64_imm64(13, ctx.R13)
    sc += _enc_mov_r64_imm64(14, ctx.R14)
    sc += _enc_mov_r64_imm64(15, ctx.R15)
    sc += _enc_mov_r64_imm64(1, ctx.Rcx)                        # real rcx, now done using it as scratch
    sc += _enc_mov_r64_imm64(10, ctx.R10)                       # real r10
    sc += _enc_mov_r64_imm64(11, ctx.R11)                       # real r11

    sc += b"\xc3"                                               # ret -> pops orig_rip, jumps back
    return sc


def _alloc_aligned_context():
    """
    Returns (raw_buffer, ctx): `ctx` is a _CONTEXT view whose address is
    16-byte aligned, backed by `raw_buffer` (caller MUST keep a reference
    to raw_buffer alive for as long as ctx is used, or Python may free the
    backing memory out from under it).

    The real Windows CONTEXT struct is declared __declspec(align(16)) in
    winnt.h -- GetThreadContext/SetThreadContext can behave unpredictably
    (corrupt reads/writes, since the OS may use aligned SSE-based copies
    internally) if handed a buffer that isn't. A plain `_CONTEXT()` via
    ctypes has no such guarantee. Over-allocate by 15 bytes and round the
    address up to the next 16-byte boundary within that extra space.
    """
    size = ctypes.sizeof(_CONTEXT)
    raw = (ctypes.c_ubyte * (size + 16))()
    addr = ctypes.addressof(raw)
    aligned_addr = (addr + 15) & ~0xF
    ctx = _CONTEXT.from_address(aligned_addr)
    return raw, ctx


def send_console_command(pm, base: int, command: str) -> bool:
    """
    Remotely execute a console command string in the live game process --
    the programmatic equivalent of typing it into the in-game console.
    e.g. send_console_command(pm, base, "g_deadsidegunmode 1")

    Implemented via THREAD HIJACKING, not CreateRemoteThread (2026-07-29) --
    see the "Thread hijacking" section above this function for why. Picks
    one of the game's own already-running threads, suspends it, redirects
    it through a save/call/restore trampoline (_build_hijack_trampoline_shellcode),
    then resumes it.

    EXPERIMENTAL -- not yet live-tested end to end. Returns False (logging
    a warning) on failure. Makes a best effort to resume the target thread
    on any error path so a failed attempt doesn't leave the game frozen.
    """
    kernel32 = ctypes.windll.kernel32
    thread_handle = None
    suspended = False
    text_buf = done_buf = code_buf = None
    try:
        pid = kernel32.GetProcessId(pm.process_handle)
        module_size = _get_module_size(pid, PATCHED_EXE_NAME)
        if module_size == 0:
            logger.warning("[ShadowMan] send_console_command: couldn't determine module size.")
            return False

        logger.info("[ShadowMan] send_console_command: scanning for a hijackable thread "
                    "(retrying for a few seconds -- alt-tab back into the game now if it isn't focused).")
        thread_handle, tid, ctx_pair = _open_hijackable_thread(pid, base, module_size)
        if thread_handle is None:
            logger.warning("[ShadowMan] send_console_command: no live (RIP-in-module) thread found to "
                            "hijack, even after retrying. If the game window wasn't focused during that "
                            "window, that's a real data point -- see CLAUDE.md's focus-throttling hypothesis.")
            return False
        suspended = True   # _open_hijackable_thread returns it already suspended
        ctx_raw, ctx = ctx_pair

        exec_fn = base + CONSOLE_EXEC_FN_RVA
        text = command.encode("ascii") + b"\x00"
        text_buf = pm.allocate(len(text))
        pm.write_bytes(text_buf, text, len(text))

        done_buf = pm.allocate(1)
        pm.write_bytes(done_buf, b"\x00", 1)

        trampoline = _build_hijack_trampoline_shellcode(ctx, base, text_buf, exec_fn, done_buf)
        code_buf = pm.allocate(len(trampoline))
        pm.write_bytes(code_buf, trampoline, len(trampoline))

        ctx.Rip = code_buf
        if not kernel32.SetThreadContext(thread_handle, ctypes.byref(ctx)):
            logger.warning("[ShadowMan] send_console_command: SetThreadContext failed.")
            return False

        if kernel32.ResumeThread(thread_handle) == 0xFFFFFFFF:
            logger.warning("[ShadowMan] send_console_command: ResumeThread failed.")
            return False
        suspended = False

        # Poll for the trampoline's own completion flag rather than assume
        # a fixed delay -- should finish in well under a second.
        for _ in range(40):
            time.sleep(0.05)
            try:
                if pm.read_bytes(done_buf, 1)[0] == 1:
                    return True
            except Exception:
                break
        logger.warning(f"[ShadowMan] send_console_command({command!r}): sent, but completion not confirmed.")
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] send_console_command({command!r}) failed: {exc}")
        return False
    finally:
        if suspended and thread_handle:
            kernel32.ResumeThread(thread_handle)
        if thread_handle:
            kernel32.CloseHandle(thread_handle)
        for buf in (text_buf, done_buf, code_buf):
            if buf:
                try:
                    pm.free(buf)
                except Exception:
                    pass


def _item_already_owned_live(pm, base: int, item_name: str) -> Optional[bool]:
    """
    Check whether item_name's possession flag already reads nonzero live —
    i.e. GiveItem for it has already landed, whether from a genuine native
    pickup or a previous injection (this session's or an earlier one; the
    flag is a persistent field on the live inventory singleton, not
    something that resets on client reconnect).

    Added 2026-08-01, per Jon's question about whether backlog replay
    should identify what's already applied rather than blindly re-running
    every injection on every reconnect. Every AP reconnect resends the
    FULL items_received history (server-side behavior, not something this
    client controls — see the 2026-07-26 double-counting writeup), and
    this client's own items_received_index is in-memory-only, so a client
    restart with the game still running has always meant re-running every
    single injection from scratch — including every give_item/cadeaux
    CreateRemoteThread call — even for items the live game has already had
    applied for the entire time the client was disconnected. That's pure
    redundant risk for anything using CreateRemoteThread (the identified
    risk class for the recurring ntdll heap-corruption crashes — see
    CLAUDE.md), for zero benefit, since a possession flag is idempotent —
    setting it again changes nothing.

    Reuses the exact same read pattern _poll_live_inventory already uses
    live for pickup detection (max() over every RVA in the tuple — some
    items, like Violator, are tracked across more than one address) —
    not a new guess, the same known-correct mechanism this file already
    trusts elsewhere.

    Returns None (not False) if item_name isn't flag-tracked at all, or
    if the read fails — the caller should treat None as "unknown, inject
    anyway" (fail OPEN here, deliberately the opposite sense of the
    title-screen gate's fail-closed convention): an unclear read must
    never cause a real item to be silently skipped, only a CONFIRMED
    already-owned read should ever suppress an injection.
    """
    rvas = ITEM_FLAG_RVAS.get(item_name)
    if not rvas:
        return None
    try:
        return max(pm.read_uint(base + rva) for rva in rvas) != 0
    except Exception:
        return None


# "give_item" items with no ITEM_FLAG_RVAS boolean flag — Retractor and
# Accumulator are multi-copy stackable counters (see RETRACTOR_COUNT_RVA /
# ACCUMULATOR_COUNT_RVA's own comment: "not per-location, not polled yet"),
# so _item_already_owned_live can't help them (it returns None — "unknown,
# inject anyway" — for any item_name not in ITEM_FLAG_RVAS). That "inject
# anyway" fallback is exactly what let a FOREIGN Retractor/Accumulator get
# re-granted on every reconnect/replay that resends AP's full items_received
# history: unlike a one-time possession flag, GiveItem(0x17)/GiveItem(0x01)
# for these two genuinely increments the same live running-total WORD the
# vanilla native pickup handler also increments — see
# _stackable_giveitem_already_sufficient below, added 2026-08-10 after Jon
# reported ending up with 7 Retractors in inventory after only 5 should have
# been grantable (a real seed's spoiler + .apshadowman confirmed exactly 5
# genuine Retractor items existed and every native donor slot was correctly
# retyped away from RSC_X_RETRACT/1/2 — ruling out a stray extra physical
# pickup — leaving redundant re-injection of the 4 foreign-sourced ones on
# some reconnect/replay as the only mechanism that fits "7 after 5", since
# the self-found one was already correctly excluded by the general
# source_player == self.slot skip a few hundred lines up).
STACKABLE_GIVEITEM_COUNT_RVAS: Dict[str, int] = {
    "Retractor":   RETRACTOR_COUNT_RVA,
    "Accumulator": ACCUMULATOR_COUNT_RVA,
}


def _stackable_giveitem_already_sufficient(pm, base: int, item_name: str,
                                            target_count: int) -> Optional[bool]:
    """
    Pure read-only counterpart to _item_already_owned_live, for the two
    "give_item" items that expose a running-total WORD counter instead of a
    one-time possession flag (see STACKABLE_GIVEITEM_COUNT_RVAS above).

    target_count should be _received_count(item_name) — the total number of
    this item AP has ever granted this slot (self-found + foreign combined,
    read fresh from self.items_received, the same authoritative source
    set_dark_soul_count/set_cadeaux_count already recompute against). If the
    live counter already reads >= target_count, every grant AP knows about
    has already physically landed (whether via a genuine native pickup or an
    earlier injection this session or a prior one — the counter is a
    persistent field on the live inventory singleton, not something that
    resets on client reconnect) and firing inject_give_item again would just
    be a duplicate. Deliberately a >= comparison, not ==: harmless if the
    live count is ever ahead of AP's own tally for some unrelated reason,
    and safer than under-counting if the two ever briefly disagree.

    Returns None (not False) if item_name has no known counter RVA or the
    read fails — same fail-OPEN convention as _item_already_owned_live: an
    unclear read must never suppress a real grant, only a confirmed
    already-sufficient read should.
    """
    rva = STACKABLE_GIVEITEM_COUNT_RVAS.get(item_name)
    if rva is None:
        return None
    try:
        return pm.read_ushort(base + rva) >= target_count
    except Exception:
        return None


def inject_give_item(pm, base: int, item_id: int) -> bool:
    """Call kexShadowManInventoryLocal::GiveItem(item_id) in the live process."""
    try:
        inv_ptr  = base + INVENTORY_RVA
        vtable   = pm.read_longlong(inv_ptr)
        give_fn  = pm.read_longlong(vtable + VTABLE_GIVE_OFF)

        # Sanity-check both live-read pointers before ever building
        # shellcode that CALLs through them (2026-08-01). This function is
        # structurally unique among every _remote_exec_shellcode() caller
        # in this file: every other one (inject_gad_power's
        # _apply_gad_level_now, inject_light_soul, check_flag, etc.) calls
        # a FIXED, hardcoded base+RVA function address baked in at build
        # time -- inject_give_item is the only one that resolves a LIVE
        # vtable pointer chain (inv_ptr -> vtable -> give_fn) from Python
        # first, then bakes whatever it read into the shellcode as an
        # immediate. If the inventory singleton isn't fully constructed
        # yet -- a narrow window during a level transition that
        # _inject_item's _read_is_at_title_screen gate doesn't catch,
        # since that only rules out the main menu, not "already past the
        # menu but the object graph mid-transition isn't valid yet" --
        # vtable/give_fn can read as stale or zeroed garbage. Blindly
        # executing CALL through a garbage pointer via CreateRemoteThread
        # is a very plausible source of the delayed, identical-signature
        # (RIP 0x...d3346d in ntdll, same backtrace every time) heap-
        # corruption crashes seen repeatedly during backlog replay --
        # jumping into arbitrary memory and running a few garbage
        # instructions before eventually faulting produces exactly this
        # "corruption discovered later, somewhere unrelated" pattern.
        # Confirmed live 2026-08-01: the same crash signature recurred
        # even with Secret Trap fully excluded from the backlog AND
        # ITEM_INJECT_PACING_SECONDS in place between every item, ruling
        # out both Secret Trap specifically and pure call-frequency
        # racing as the sole explanation -- pointing at a real, standing
        # bug in one of the remaining CreateRemoteThread callers, and
        # this is the only one with a plausible garbage-pointer path.
        # Failing closed here (skip + warn, same shape as the existing
        # title-screen gate) is a strict improvement regardless of
        # whether this turns out to be the whole explanation.
        pid = ctypes.windll.kernel32.GetProcessId(pm.process_handle)
        module_size = _get_module_size(pid, PATCHED_EXE_NAME)
        if (module_size == 0
                or not (base <= vtable < base + module_size)
                or not (base <= give_fn < base + module_size)):
            logger.warning(
                f"[ShadowMan] inject_give_item({item_id:#x}): vtable "
                f"({vtable:#x}) or give_fn ({give_fn:#x}) isn't a valid "
                f"in-module pointer (base={base:#x}, size={module_size:#x}) "
                f"-- inventory object likely not fully constructed yet. "
                f"Refusing to execute rather than risk a garbage CALL.")
            return False

        sc = _build_give_item_shellcode(inv_ptr, item_id, give_fn)
        _remote_exec_shellcode(pm, sc)
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] inject_give_item({item_id:#x}) failed: {exc}")
        return False


def _soul_level_for_count(count: int, thresholds: Dict[int, int]) -> int:
    """
    Return the Soul Level (0-10) implied by a given soul count, per this
    seed's thresholds dict (SL -> souls required, strictly ascending,
    SL0=0). Mirrors the cascading-comparison shape of the CMP R10D,imm8
    chain soul_threshold_patch.py writes into thoth_x64.exe (SL1..SL10 at
    0x2df116..0x2df187) — see that file's module docstring. Used by
    _sync_soul_level to know what value to write to the Soul Level meter.
    """
    level = 0
    for sl in range(1, 11):
        if count >= thresholds.get(sl, VANILLA_SOUL_THRESHOLDS[sl]):
            level = sl
        else:
            break
    return level


def inject_dark_soul(pm, base: int, amount: int = 1) -> bool:
    """
    Increment the dark soul count directly in process memory.

    Only writes SOUL_COUNT_RVA — deliberately does NOT also recompute the
    Soul Level meter (DAT_140db2208) that FUN_1402dfb90 (the engine's own
    pickup path) would normally update in the same breath. Every call site
    MUST follow this with _sync_soul_level(pm, base, thresholds) to close
    that gap — see its docstring and the SOUL_LEVEL_METER_RVA comment block
    above for the full story (found 2026-07-20 via live disassembly).
    Self-collected souls are unaffected by any of this — those go through
    the real engine pickup path, not this injector.
    """
    try:
        addr    = base + SOUL_COUNT_RVA
        current = pm.read_int(addr)
        pm.write_int(addr, min(current + amount, 120))
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] inject_dark_soul failed: {exc}")
        return False


def inject_cadeaux(pm, base: int, amount: int = 1) -> bool:
    """
    Increment the live cadeaux count directly in process memory
    (CADEAUX_COUNT_RVA), the same running total the altar cost / Fogometers
    door thresholds read from.

    Only used for cross-player-received Cadeaux items — self-collected
    cadeaux go through the real engine pickup path (which already updates
    this counter natively) and are skipped before this is ever called; see
    _inject_item's self-found check.

    Callers should also fire GiveItem(0x05) alongside this (see the
    "cadeaux" dispatch branches in _inject_item / _replay_all_received_items)
    to flip the "ever collected a cadeaux" flag — the count and the flag are
    two separate pieces of state; this function only ever touches the count.
    """
    try:
        addr    = base + CADEAUX_COUNT_RVA
        current = pm.read_int(addr)
        pm.write_int(addr, min(current + amount, 666))
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] inject_cadeaux failed: {exc}")
        return False


def set_dark_soul_count(pm, base: int, target: int) -> bool:
    """
    Set the live dark soul count to an ABSOLUTE target value, rather than
    incrementing it the way inject_dark_soul does.

    Idempotent — safe to call any number of times with the same target,
    unlike the add-based inject_dark_soul, which double-counts every
    previously-injected foreign Dark Soul whenever the client replays its
    full items_received history on top of a live/save state that already
    carries those contributions forward. Confirmed live 2026-07-25/26 (Jon:
    reloading a save "was adding on top of my current soul count" after a
    full client reconnect replayed everything from index 0). Since the game
    itself persists SOUL_COUNT_RVA into the .sav file, any save written
    after an earlier injection already reflects it — reloading that save
    (or simply reconnecting) does NOT reset the counter to a native-only
    baseline the way a genuine fresh process start would.

    Callers should pass target = the count of "Dark Soul" entries across
    the FULL items_received list (self-found + foreign combined) — self-
    found contributions are already reflected by the vanilla pickup
    handler by the time an item shows up in items_received, so including
    them in the target is a no-op rather than a double-count; it just
    means this function doesn't need its own self-found special-casing.
    """
    try:
        addr = base + SOUL_COUNT_RVA
        pm.write_int(addr, min(max(target, 0), 120))
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] set_dark_soul_count failed: {exc}")
        return False


def set_cadeaux_count(pm, base: int, target: int) -> bool:
    """
    Set the live cadeaux count to an ABSOLUTE target value, rather than
    incrementing it the way inject_cadeaux does. Same idempotency
    reasoning as set_dark_soul_count above (2026-07-26 fix) — pass
    target = count of "Cadeaux" entries across the full items_received
    list.
    """
    try:
        addr = base + CADEAUX_COUNT_RVA
        pm.write_int(addr, min(max(target, 0), 666))
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] set_cadeaux_count failed: {exc}")
        return False


def read_voodoo_power(pm, base: int) -> Optional[int]:
    """See VOODOO_POWER_RVA notes above."""
    try:
        return pm.read_int(base + VOODOO_POWER_RVA)
    except Exception as exc:
        logger.warning(f"[ShadowMan] read_voodoo_power failed: {exc}")
        return None


def set_voodoo_power(pm, base: int, value: int) -> bool:
    """
    Set the live voodoo power to an ABSOLUTE value via a plain memory write
    — confirmed live by Jon (2026-08-03) to be honored both by the HUD
    meter and by actual Asson/voodoo weapon gameplay, unlike health (which
    needs the ModifyStat call — see apply_health_delta). Only floors at 0;
    caller is responsible for clamping to a sensible max (see
    read_voodoo_power_cap below) — this function has no opinion of its own.
    """
    try:
        addr = base + VOODOO_POWER_RVA
        pm.write_int(addr, max(value, 0))
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] set_voodoo_power failed: {exc}")
        return False


def read_voodoo_power_cap(pm, base: int) -> Optional[int]:
    """
    Voodoo power's real cap — confirmed by Jon (2026-08-03) to be the same
    live value as the Soul Level meter (see SOUL_LEVEL_METER_RVA, "current
    soul level * 1000"), not a separate fixed constant. Reads it fresh each
    call since it legitimately changes mid-session as the player's SL
    tier rises.
    """
    try:
        return pm.read_int(base + SOUL_LEVEL_METER_RVA)
    except Exception as exc:
        logger.warning(f"[ShadowMan] read_voodoo_power_cap failed: {exc}")
        return None


def apply_voodoo_drain(pm, base: int) -> bool:
    """One-shot 'Voodoo Drain' trap — sets voodoo power straight to 0."""
    return set_voodoo_power(pm, base, 0)


def set_ammo(pm, base: int, rva: int, value: int) -> bool:
    """
    Set one ammo pool to an ABSOLUTE value via a plain memory write — same
    mechanism/risk class as set_voodoo_power (confirmed by Jon, 2026-08-03,
    to be honored by both HUD and gameplay for this same struct). Only
    floors at 0; caller clamps to a sensible max (see AMMO_RVAS_AND_CAPS).
    """
    try:
        pm.write_int(base + rva, max(value, 0))
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] set_ammo({rva:#x}) failed: {exc}")
        return False


def read_ammo_counts(pm, base: int) -> Dict[str, Optional[int]]:
    """Read all three tracked ammo pools — for logging/diagnostics."""
    result: Dict[str, Optional[int]] = {}
    for name, (rva, _cap) in AMMO_RVAS_AND_CAPS.items():
        try:
            result[name] = pm.read_int(base + rva)
        except Exception:
            result[name] = None
    return result


def apply_ammo_drain(pm, base: int) -> bool:
    """
    One-shot 'Ammo Drain' / 'No Ammo' trap — sets all three tracked ammo
    pools (Shotgun, Violator, 9mm) to 0 at once, per Jon's call to apply
    this across all ammo types rather than per-weapon. Returns True only
    if every write succeeded.
    """
    ok = True
    for name, (rva, _cap) in AMMO_RVAS_AND_CAPS.items():
        if not set_ammo(pm, base, rva, 0):
            ok = False
        else:
            logger.info(f"[ShadowMan] Ammo Drain: {name} -> 0")
    return ok


def apply_ammo_fill(pm, base: int, log: bool = True) -> bool:
    """
    One-shot fill of all three tracked ammo pools to their known max
    (see AMMO_RVAS_AND_CAPS) — the instant-refill half of the Ammo Max
    Hold effect, also usable standalone.

    log (2026-08-23, Jon's report — /debug off didn't actually stop the
    spam): this function's own per-pool "Ammo Fill: X -> Y" lines are a
    SEPARATE source from _run_ammo_max_hold's outer per-tick summary line
    -- gating only that outer line (the original /debug fix) left these
    three inner lines still firing every tick, unconditionally, since this
    is a plain module-level function with no access to
    self.debug_hold_logging on its own. Defaults to True so any other/
    future one-shot caller keeps logging by default (there happens to be
    only the one call site today, _run_ammo_max_hold, which now passes
    log=self.debug_hold_logging explicitly).
    """
    ok = True
    for name, (rva, cap) in AMMO_RVAS_AND_CAPS.items():
        if not set_ammo(pm, base, rva, cap):
            ok = False
        elif log:
            logger.info(f"[ShadowMan] Ammo Fill: {name} -> {cap}")
    return ok


def _build_soul_meter_setvalue_shellcode(soul_obj: int, new_value: int) -> bytes:
    """
    Real virtual call fallback for _sync_soul_level: (*vtable[0x40])(this, 3, new_value).
    Windows x64 ABI: RCX=this, RDX=statIdx, R8D=value. Same shape as the
    other vtable-call shellcode builders in this file (_build_give_item_shellcode
    et al.) — read the vtable ptr, dereference the target slot, call it.
    """
    sc  = b"\x48\x83\xec\x28"                                       # sub  rsp, 0x28
    sc += b"\x48\xb9" + struct.pack("<Q", soul_obj)                 # mov  rcx, soul_obj (this)
    sc += b"\x48\x8b\x01"                                           # mov  rax, [rcx]     (vtable)
    sc += b"\x48\x8b\x80" + struct.pack("<I", SOUL_METER_VT_SETVALUE_OFF)  # mov rax, [rax+0x40]
    sc += b"\xba" + struct.pack("<I", SOUL_METER_STAT_IDX)          # mov  edx, 3
    sc += b"\x41\xb8" + struct.pack("<I", new_value & 0xFFFFFFFF)   # mov  r8d, new_value
    sc += b"\xff\xd0"                                                # call rax
    sc += b"\x48\x83\xc4\x28"                                        # add  rsp, 0x28
    sc += b"\xc3"                                                     # ret
    return sc


def _sync_soul_level(pm, base: int, thresholds: Dict[int, int], apply_now: bool = True) -> bool:
    """
    Recompute and write the Soul Level meter (DAT_140db2208) after
    inject_dark_soul() has bumped the raw count — the step the engine's own
    pickup path (FUN_1402dfb90) does that a raw count write skips. See the
    SOUL_LEVEL_METER_RVA comment block above for how this was confirmed.
    Reads the current count fresh (rather than trusting a caller-passed
    value) so this stays correct even if amount ever becomes != 1.

    apply_now (2026-08-02, Jon: "we need it for souls too, because we need
    soul level to apply live too" — same pattern gad/poigne got on
    2026-08-01, extended here). The fast-path branch (vtable_ptr ==
    fast_path) is a PLAIN memory write and always runs regardless of
    apply_now — no engine code execution involved, same risk class as
    gad/poigne's flag-byte writes, which also always apply unconditionally.
    Only the fallback branch is gated: when the live soul object isn't
    (yet) the exact base class the fast path assumes, the real fix is a
    CreateRemoteThread virtual call (_build_soul_meter_setvalue_shellcode)
    — the same risk category as _apply_gad_level_now's FUN_140459d50 call,
    and notably the one case where this function needs to execute code AT
    ALL is precisely when the live object doesn't look fully/normally
    initialized yet, which is also the most plausible moment for a
    connect-time backlog replay to catch it in an unstable state. When
    apply_now is False, that branch is skipped entirely (logged, not
    silently dropped) — the plain-write fast path already keeps the meter
    number itself correct in the common case; the fallback's job is only
    the live "make it visually update instantly" step, and — like gad's
    withheld apply — the next real trigger of this function (a later Dark
    Soul receipt, /inject, or manual retry) will pick it back up. Souls
    don't have gad's dedicated per-level-load resync sweep, so this is a
    slightly weaker safety net than gad's, but the CreateRemoteThread call
    itself is the thing every recent mitigation in this investigation has
    been trying to keep out of the connect-time backlog window — skipping
    it there is the more conservative choice even without a resync sweep
    to fall back on.
    """
    try:
        count     = pm.read_int(base + SOUL_COUNT_RVA)
        level     = _soul_level_for_count(count, thresholds)
        new_value = level * 1000

        soul_obj    = base + LIGHT_SOUL_SOULOBJ_RVA
        vtable_ptr  = pm.read_longlong(soul_obj)
        fast_path   = base + SOUL_METER_VTABLE_RVA

        if vtable_ptr == fast_path:
            meter_addr = base + SOUL_LEVEL_METER_RVA
            old_value  = pm.read_int(meter_addr)
            if new_value > old_value:
                pm.write_int(meter_addr, new_value)
                logger.info(f"[ShadowMan] Soul level meter: {old_value} -> {new_value} "
                            f"(SL{level}, count={count}).")
        elif apply_now:
            # Object isn't (yet) the exact base class the fast path assumes —
            # replicate the real virtual call instead.
            sc = _build_soul_meter_setvalue_shellcode(soul_obj, new_value)
            _remote_exec_shellcode(pm, sc)
            logger.info(f"[ShadowMan] Soul level meter synced via virtual call "
                        f"(SL{level}, count={count}).")
        else:
            logger.info(
                f"[ShadowMan] Soul level meter live-apply deferred (not "
                f"confirmed genuinely live yet — soul object isn't the "
                f"fast-path class, would need a CreateRemoteThread virtual "
                f"call) — SL{level} will apply on the next real sync.")
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] _sync_soul_level failed: {exc}")
        return False


def inject_pickup_system(pm, base: int, arg: int) -> bool:
    """
    Grant an item via the secondary grant system used by giveinv -1's post-loop
    call — the only path in the engine that gives RSC_X_SHOTGUN2 (sawed-off).

    Replicates:
        FUN_1402ea6f0()                             # enable-cheats flag (idempotent)
        obj = FUN_140347560()
        (*obj->vtable[1])(obj, arg)

    This is identical to steps 1+3 of _build_light_soul_shellcode; the sawed-off
    uses arg=0x1A, light soul uses arg=0xE2 (plus an extra soul-points call).

    Known args:
        0x1A  → RSC_X_SHOTGUN2 (sawed-off shotgun)
    """
    try:
        setup_fn  = base + LIGHT_SOUL_SETUP_RVA   # FUN_1402ea6f0
        getobj_fn = base + LIGHT_SOUL_GETOBJ_RVA  # FUN_140347560

        sc  = b"\x48\x83\xec\x28"                              # sub  rsp, 0x28
        # step 1 — enable cheats (idempotent)
        sc += b"\x48\xb8" + struct.pack("<Q", setup_fn)        # mov  rax, setup_fn
        sc += b"\xff\xd0"                                       # call rax
        # step 2 — (*FUN_140347560()->vtable[1])(obj, arg)
        sc += b"\x48\xb8" + struct.pack("<Q", getobj_fn)       # mov  rax, getobj_fn
        sc += b"\xff\xd0"                                       # call rax  → rax = obj
        sc += b"\x48\x89\xc1"                                  # mov  rcx, rax     (this)
        sc += b"\x48\x8b\x00"                                  # mov  rax, [rcx]   (vtable)
        sc += b"\x48\x8b\x40\x08"                              # mov  rax, [rax+8] (vtable[1])
        sc += b"\xba" + struct.pack("<I", arg & 0xFFFF)        # mov  edx, arg
        sc += b"\xff\xd0"                                       # call rax
        sc += b"\x48\x83\xc4\x28"                              # add  rsp, 0x28
        sc += b"\xc3"                                           # ret

        _remote_exec_shellcode(pm, sc)
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] inject_pickup_system({arg:#x}) failed: {exc}")
        return False


def _build_light_soul_shellcode(base: int) -> bytes:
    """
    Replicate the body of FUN_140327410 (givelightsoul handler).

    The handler guards on Cmd_Argc() == 2 and returns immediately when called
    from a bare thread (argc == 0), so we replicate its three internal calls
    directly in shellcode instead.

    Decompiled body (argc == 2 path):
        FUN_1402ea6f0();
        (**(vtable @ DAT_140db21e0 + 0xA8))(&DAT_140db21e0, 3);
        obj = FUN_140347560();
        (**(obj->vtable + 0x08))(obj, 0xE2);

    Windows x64 ABI notes:
        • shadow space (0x28) is subtracted once at the top; each `call`
          pushes 8 bytes → callee sees 16-byte-aligned rsp + shadow. ✓
        • Between calls only rax is explicitly loaded, so caller-saved
          registers (rcx/rdx) must be set fresh for each call.
    """
    setup_fn  = base + LIGHT_SOUL_SETUP_RVA
    soul_obj  = base + LIGHT_SOUL_SOULOBJ_RVA   # &DAT_140db21e0 == this ptr
    getobj_fn = base + LIGHT_SOUL_GETOBJ_RVA

    sc = b""
    # ── prologue ──────────────────────────────────────────────────────────────
    sc += b"\x48\x83\xec\x28"                              # sub  rsp, 0x28

    # ── step 1: FUN_1402ea6f0() ───────────────────────────────────────────────
    sc += b"\x48\xb8" + struct.pack("<Q", setup_fn)        # mov  rax, setup_fn
    sc += b"\xff\xd0"                                       # call rax

    # ── step 2: (*vtable[0xA8])(soul_obj, 3) ─────────────────────────────────
    # soul_obj is the address of the object (its first field is the vtable ptr)
    sc += b"\x48\xb9" + struct.pack("<Q", soul_obj)        # mov  rcx, soul_obj (this)
    sc += b"\x48\x8b\x01"                                  # mov  rax, [rcx]   (vtable ptr)
    sc += b"\x48\x8b\x80" + struct.pack("<I", LIGHT_SOUL_VT_A8)  # mov rax, [rax+0xa8]
    sc += b"\xba\x03\x00\x00\x00"                          # mov  edx, 3
    sc += b"\xff\xd0"                                       # call rax

    # ── step 3: obj = FUN_140347560(); (*obj->vtable[1])(obj, 0xe2) ──────────
    sc += b"\x48\xb8" + struct.pack("<Q", getobj_fn)       # mov  rax, getobj_fn
    sc += b"\xff\xd0"                                       # call rax  → rax = obj ptr
    sc += b"\x48\x89\xc1"                                  # mov  rcx, rax     (this = obj)
    sc += b"\x48\x8b\x00"                                  # mov  rax, [rcx]   (vtable ptr)
    sc += b"\x48\x8b\x40" + bytes([LIGHT_SOUL_VT_08])      # mov  rax, [rax+8] (vtable[1])
    sc += b"\xba\xe2\x00\x00\x00"                          # mov  edx, 0xe2
    sc += b"\xff\xd0"                                       # call rax

    # ── epilogue ──────────────────────────────────────────────────────────────
    sc += b"\x48\x83\xc4\x28"                              # add  rsp, 0x28
    sc += b"\xc3"                                           # ret

    return sc


def inject_light_soul(pm, base: int) -> bool:
    """
    Grant the Light Soul (permanent invincibility) by replicating the three
    internal calls that the givelightsoul console command performs.
    See _build_light_soul_shellcode for the full rationale.
    """
    try:
        sc = _build_light_soul_shellcode(base)
        _remote_exec_shellcode(pm, sc)
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] inject_light_soul failed: {exc}")
        return False


def read_named_flag(pm, base: int, index: int) -> Optional[bool]:
    """
    Read a CF_* named completion/cutscene flag by its array index. Pure
    memory reads, no code injection -- see the "Named completion/cutscene
    flag table" comment above CF_FLAG_OBJ_RVA for the full derivation
    (2026-07-27, Ghidra + Jon's live CE/dumpsaveflags session).

    Ghidra decompile of the real accessor (FUN_14033f1d0, found by
    following [vtable+0x18] on the CF_FLAG_OBJ_RVA object) reduces to a
    plain packed-bitfield test:
        if (*(int*)(this + CF_FLAG_LENGTH_OFF) == 0): return False
        bits_ptr = *(qword*)(this + CF_FLAG_BITS_PTR_OFF)
        return bool((bits_ptr[index >> 3] >> (index & 7)) & 1)
    No FUN_140347560()/vtable dispatch needed at all -- that function's own
    disassembly showed it's a standard MSVC magic-statics singleton
    accessor that always returns the same constant address
    (&DAT_140f9c680) once initialized, and CF_FLAG_OBJ_RVA IS that address,
    not a pointer to it. Skipped the length check here since read_uchar
    will simply raise (caught below) if bits_ptr is null/invalid, e.g.
    before this system's first real initialization -- treated the same as
    any other failed-this-poll read.

    Returns None on any pymem read failure so callers can treat that the
    same as "couldn't read this poll" rather than a false negative.
    """
    try:
        bits_ptr = pm.read_longlong(base + CF_FLAG_OBJ_RVA + CF_FLAG_BITS_PTR_OFF)
        if not bits_ptr:
            return None
        byte_val = pm.read_uchar(bits_ptr + (index >> 3))
        return bool((byte_val >> (index & 7)) & 1)
    except Exception as exc:
        logger.warning(f"[ShadowMan] read_named_flag(index={index}) failed: {exc}")
        return None


def write_named_flag(pm, base: int, index: int) -> bool:
    """
    Set a CF_* named completion/cutscene flag by its array index — the
    write-side counterpart to read_named_flag() above.

    Added 2026-08-18 for the Unique Retractor Keys REMOTE-grant path (Jon's
    live repro caught the gap: "does it know if the retractor is in a
    different game that's okay? it will be injected from our client").
    unique_retractor_keys_patch.py's own Hook B (pickup-side) only ever
    sets CF_CUSTOM00-04 when a NATIVE physical pickup happens in THIS
    player's own world — it's woven into the game's native pickup handler
    (case D_17), which only runs for a real in-world interaction. A named
    retractor key whose location Fill placed in ANOTHER player's game
    reaches this player purely over the AP network — this file's own
    "give_item" injection calls the vanilla GiveItem vtable method
    directly (see AP_ITEM_INJECTION's comment), a completely different
    code path than the one Hook B patches into. Without this function, a
    remotely-received named key would show as "have it" in AP's own
    inventory/logic forever, while the corresponding schism's exe-side
    gate (which checks CF_CUSTOM via GetFlag, not AP state) would stay
    permanently locked in that player's game — the item would be granted,
    but nothing in-game would ever actually unlock.

    Pure memory write (read-modify-write OR the correct bit into the same
    packed bit array read_named_flag() reads), no code injection needed —
    safe specifically for CF_CUSTOM00-04 because unique_retractor_keys_
    patch.py's own PERSISTENCE comment (Jon's Ghidra decompile of the real
    SetFlag, FUN_14033ee50) already confirmed that function has NO side
    effects beyond the bit-set itself for any index outside its internal
    special-case switch range [26, 193] — every CF_CUSTOM* index is 279+,
    well clear of that range, so a raw bit-set here is provably equivalent
    to what a real SetFlag(this, index) call would do. Do NOT reuse this
    approach for any flag index inside [26, 193] without re-verifying that
    assumption against the same decompile.

    Returns False (and logs) on any pymem read/write failure, same
    "couldn't confirm this poll, treat as not-yet-set" convention as
    read_named_flag()'s own None return — callers should treat a False
    here as "not confirmed," not as fatal; both call sites below run on
    the same connect/backlog-replay machinery as every other grant, so a
    transient failure here gets another chance on the next reconnect or
    replay pass rather than being silently permanent.
    """
    try:
        bits_ptr = pm.read_longlong(base + CF_FLAG_OBJ_RVA + CF_FLAG_BITS_PTR_OFF)
        if not bits_ptr:
            return False
        byte_addr = bits_ptr + (index >> 3)
        bit = 1 << (index & 7)
        cur = pm.read_uchar(byte_addr)
        if not (cur & bit):
            pm.write_uchar(byte_addr, cur | bit)
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] write_named_flag(index={index}) failed: {exc}")
        return False


def _live_gad_temple_tier(pm, base: int) -> int:
    """
    Read the CURRENT live temple tier (0-3) directly from GAD_1..3_RVA —
    the highest flag index that's actually set right now, per the same
    progressive scan _resync_gad_level uses. Factored out (2026-08-02) so
    inject_gad_power() can use it as a monotonic floor — see that
    function's own comment for why. Returns 0 on a read failure (fails
    toward "assume nothing unlocked yet" rather than raising, matching
    this file's general read-helper convention).
    """
    try:
        temple_tier = 0
        for i, rva in enumerate(GAD_POWER_RVAS[:3], start=1):
            if pm.read_uchar(base + rva):
                temple_tier = i
        return temple_tier
    except Exception:
        return 0


def _resync_gad_level(pm, base: int) -> None:
    """
    Recompute GAD_LEVEL_RVA to match the vanilla console's own "givegad"
    encoding (confirmed by Jon, 2026-07-25): it's a 3-bit value, NOT a
    plain popcount over the 4 GAD_POWER_RVAS bytes.

        givegad 0  — no gad powers
        givegad 1  — Gad Temple 1 (touch)
        givegad 2  — Gad Temple 2 (walk)   [progressive — implies temple 1 too]
        givegad 3  — Gad Temple 3 (swim)   [progressive — implies 1+2 too]
        givegad 4  — Poigne only
        givegad 5  — Poigne + Gad Temple 1
        givegad 6  — Poigne + Gad Temple 2
        givegad 7  — Poigne + Gad Temple 3

    i.e. level = temple_tier (0-3, the highest real Gad Temple reached —
    progressive, so this alone tells you which of 1/2/3 are set) PLUS 4 if
    Poigne (GAD_4_RVA) is set. A REPLACES an earlier version of this fix
    that computed a plain popcount (0-4) instead — wrong, since e.g.
    "Poigne + Gad Temple 2" is popcount 2 but the real encoding is 6, not
    2. GAD_1..3_RVA are still written progressively by inject_gad_power
    and GAD_4_RVA independently by inject_poigne_ability; this just
    recomputes the aggregate display field to match how the game itself
    represents the combined state.
    """
    try:
        temple_tier = _live_gad_temple_tier(pm, base)
        poigne_set = bool(pm.read_uchar(base + GAD_4_RVA))
        pm.write_int(base + GAD_LEVEL_RVA, temple_tier + (4 if poigne_set else 0))
    except Exception as exc:
        logger.warning(f"[ShadowMan] _resync_gad_level failed: {exc}")


def _decode_gad_level(level: int) -> Tuple[int, bool]:
    """
    Inverse of _resync_gad_level's encoding — (temple_tier 0-3, poigne
    bool) from a raw GAD_LEVEL_RVA/"givegad" value 0-7. temple_tier is
    level % 4 (0-3 either way: 0-3 raw, or 4-7 = temple_tier + 4), poigne
    is level >= 4. Used for display only (e.g. "3 abilities total"),
    since the raw 0-7 value itself isn't a meaningful "X / 4" count.
    """
    return level % 4, level >= 4


# Module-level, mirrors _pm_cache's cross-call state pattern (2026-08-01).
# See _apply_gad_level_now's own docstring for why this exists.
_last_gad_apply_now_ts = 0.0
# Lowered 1.0 -> 0.3 (2026-08-02, confirmed live crash-free real-backlog
# report) -- was strictly larger than ITEM_INJECT_PACING_SECONDS (0.5),
# which meant any two gad/poigne items arriving back-to-back in a normal
# backlog (the classic Gad Power immediately followed by Poigne, exactly
# the shape that first surfaced this whole debounce back on 2026-08-01)
# would ALWAYS have the second one's live-apply skipped -- confirmed by
# Jon's own log: item #48 Gad Power's _apply_gad_level_now succeeded and
# visibly live-applied correctly (first real confirmation the 2026-08-02
# player_ptr fix actually works), then item #49 Poigne's own call 0.5s
# later hit this debounce and was skipped, so Poigne's flag was written
# correctly but never visibly took effect live. Now comfortably below
# the 0.5s pacing gap, so consecutive gad/poigne items no longer starve
# each other, while still providing some real rate-limiting against a
# true rapid-fire burst (the original reason this was added).
GAD_APPLY_NOW_MIN_GAP_SECONDS = 0.3


def _apply_gad_level_now(pm, base: int) -> None:
    """
    Best-effort native call to FUN_140459d50 (see _build_apply_gad_shellcode's
    docstring for the full rationale/derivation) -- mirrors the real
    native pickup path's own "apply gad level immediately" step, run
    right after the raw GAD_*_RVA flag bytes are written, so an
    already-in-progress level's hazard actors pick up the change without
    needing a level reload/warp (Jon's report, 2026-07-28).

    player_ptr FIXED 2026-08-02 — see _build_apply_gad_shellcode's own
    docstring. Previously guessed as INVENTORY_RVA (wrong object, never
    independently confirmed); now resolved live each call via the
    already-proven FUN_140458680() accessor, matching both
    gad_pickup_patch.py's own confirmed native calling convention and
    this file's own already-working DeathLink shellcode. Still kept in
    its own try/except so a failure here (silently doing nothing, or in
    the worst case misbehaving) can never take down the flag writes that
    already landed successfully before this runs, or anything else in
    the caller — the pointer fix reduces the risk here, it doesn't
    retire the need for defense in depth.

    Only reached from genuinely-live call sites as of 2026-08-02: the
    "gad"/"poigne_ability" branches in _inject_item (after the
    connect-time elapsed gate) and the /siminject test harness. The
    level-entry resync sweep (_last_gad_resync_level, inside
    _poll_live_memory) no longer calls this at all — it now always
    passes apply_now=False, since that sweep runs on every level
    TRANSITION, and the vanilla engine's own native level-load code
    already reads GAD_*_RVA/GAD_LEVEL_RVA fresh at that exact moment
    (see that block's own comment) — this function was only ever needed
    for the genuine mid-level case (item received with no transition to
    piggyback on).

    Debounced (2026-08-01, Jon's suspicion after a crash landed right
    around a Gad Power injection immediately followed by a Poigne
    injection — items #48/#49 in the same backlog, each independently
    calling this function about a second apart, for two genuinely
    different level values: temple-only, then temple+Poigne). That
    specific crash was later traced to the level-entry sweep calling this
    unconditionally on every transition (fixed 2026-08-02, see above) —
    the debounce is left in place anyway as cheap defense in depth, not
    because it's since been proven to be the actual fix for that crash.
    Skips calling this again if it already ran within
    GAD_APPLY_NOW_MIN_GAP_SECONDS — the GAD_*_RVA flag bytes and
    GAD_LEVEL_RVA are ALWAYS written by the caller regardless of this
    debounce (see inject_gad_power/inject_poigne_ability), so a skipped
    call doesn't lose any state, it just means the mid-level instant
    visual/hazard propagation for that particular call waits for the
    next level transition's own existing resync sweep
    (_last_gad_resync_level) instead of firing again immediately.
    """
    global _last_gad_apply_now_ts
    now = time.monotonic()
    if now - _last_gad_apply_now_ts < GAD_APPLY_NOW_MIN_GAP_SECONDS:
        logger.info(
            "[ShadowMan] _apply_gad_level_now: skipped (fired again too "
            "soon after the last call) — flags are already correctly "
            "written; the next level transition will still pick this up."
        )
        return
    try:
        sc = _build_apply_gad_shellcode(base)
        _remote_exec_shellcode(pm, sc)
        _last_gad_apply_now_ts = now
    except Exception as exc:
        logger.warning(f"[ShadowMan] _apply_gad_level_now failed: {exc}")


def inject_gad_power(pm, base: int, temple_count: int, apply_now: bool = True) -> bool:
    """
    Enable GAD_1_RVA..GAD_3_RVA — the 3 real Gad Temples (touch/walk/swim)
    — up to <temple_count> (0–3) and disable the rest. Does NOT touch
    GAD_4_RVA (Poigne's slot — see inject_poigne_ability): FIXED
    2026-07-25 (Jon's report) — this used to be handed a single shared
    "tier" that lumped Poigne's arg=4 into the same cumulative count as
    the temples, clamped to 4, and rewrote ALL 4 flags based on that one
    number. That meant receiving Poigne with fewer than 3 real Gad Power
    items already collected still set every flag through 4 — i.e. Poigne
    alone silently granted touch/walk/swim early, since access_rules.py's
    gad1_hand/gad2_walk/gad3_swim gate purely on state.count("Gad Power")
    and know nothing about Poigne (R.poigne() is a wholly separate
    state.has() check). temple_count should be the cumulative count of
    real "Gad Power" AP items received ONLY (not Poigne's arg=4).

    Also calls _apply_gad_level_now() (2026-07-28) right after the flag
    writes -- see that function's docstring for why (mid-level hazard
    checks not picking up a freshly-written flag without this).

    apply_now=False (2026-08-01, Jon's call pending a Ghidra look at
    FUN_140459d50 itself) skips that native call entirely -- the flag
    bytes and GAD_LEVEL_RVA are still always written and correct either
    way, only the "make it visible without a level reload" step is
    withheld. Callers pass False for connect-time backlog / mid-session
    replay (see _inject_item / _replay_all_received_items), where this
    function is EXPERIMENTAL and unverified and has coincided with a
    crash; True (the default) for genuinely live, real-time receipt
    during actual play, which has never once reproduced a crash across
    this whole investigation. A skipped apply still lands correctly on
    the player's next level (re)entry via the existing resync sweep
    (_last_gad_resync_level) — no state is lost, only the instant mid-
    level update is deferred.

    NEVER WRITES BACKWARD (2026-08-02, confirmed live crash — Jon's
    hypothesis "maybe the game doesn't like gad powers going backwards").
    temple_count is floored at whatever's ALREADY live
    (_live_gad_temple_tier) before writing, regardless of what the
    caller passed. Confirmed root cause of a real crash: Jon's live game
    already had 2 real temples unlocked (GAD_1/GAD_2 set), but
    self.gad_powers_received — an in-memory, session-local Python
    counter — was stale/behind that (0, from /siminject Gad Power
    exercising this exact desync deliberately; the same transient gap
    can in principle occur during a normal reconnect's brief backlog-
    replay-catch-up window too, before every real "Gad Power" item has
    been reprocessed). Calling inject_gad_power(pm, base, 1) with that
    stale value wrote GAD_1=1, GAD_2=0, GAD_3=0 unconditionally — actively
    ERASING the already-set GAD_2 flag — and the very next level
    transition (which re-asserts from the same stale counter via
    _last_gad_resync_level) crashed. Flooring here makes the write
    monotonic no matter what any caller passes in, closing this off at
    the one place that actually touches the flag bytes rather than
    relying on every caller's own bookkeeping being correct.
    """
    try:
        temple_count = max(0, min(max(temple_count, _live_gad_temple_tier(pm, base)), 3))
        for i, rva in enumerate(GAD_POWER_RVAS[:3], start=1):
            pm.write_bytes(base + rva,
                           bytes([1 if i <= temple_count else 0]), 1)
        _resync_gad_level(pm, base)
        if apply_now:
            _apply_gad_level_now(pm, base)
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] inject_gad_power({temple_count}) failed: {exc}")
        return False


def inject_poigne_ability(pm, base: int, apply_now: bool = True,
                           known_good_temple_tier: Optional[int] = None) -> bool:
    """
    Enable Poigne's actual gameplay effect by writing GAD_4_RVA directly —
    see AP_ITEM_INJECTION's comment: GiveItem(0x13) alone only flips the
    cosmetic POIGNE_RVA inventory checkbox, not this. Independent of the 3
    real Gad Temple flags (inject_gad_power) — added 2026-07-25 alongside
    that function's fix, splitting what used to be one shared "gad" tier
    into two independently-settable halves.

    Also calls _apply_gad_level_now() (2026-07-28) — same "apply
    immediately" reasoning as inject_gad_power(); gad_pickup_patch.py's
    own native stub2 (case 0x13, Poigne) calls the exact same
    FUN_140459d50 as stub1 (case 0x16, real Gad Temples).

    apply_now — same meaning and same 2026-08-01 reasoning as
    inject_gad_power's own parameter; see its docstring.

    known_good_temple_tier (2026-08-02, Jon: "picking up poigne also
    accidentally gave me gad level 1 upon pickup. thats gad level 5
    (gad 1 + poigne), it should've only given me level 4... when i
    warped out my gad level went to the appropriate value of 4"). With
    apply_now=True, _apply_gad_level_now() calls the still-EXPERIMENTAL,
    never-fully-reverse-engineered FUN_140459d50 — the observed symptom
    (an extra, unearned real Gad Temple tier appearing at the exact
    instant Poigne's native apply runs, then correcting itself on the
    very next level transition the same way every other stale-live-byte
    bug in this file has) is consistent with that native call having some
    internal side effect on GAD_1_RVA that this file has never had
    visibility into (plausibly the vanilla "givegad" cheat's own
    semantics never anticipating "Poigne with zero real temples" as an
    input, since normally Poigne is earned well after at least one real
    temple in vanilla progression). Rather than guess at WHY inside a
    function this file has already had to walk back trust in more than
    once today, this corrects the OBSERVABLE SYMPTOM directly: if the
    caller passes a known-good temple tier (self.gad_powers_received,
    the absolute AP-authoritative count — see the 2026-08-02 "Gad Power
    switched to absolute recompute" fix), GAD_1..3_RVA are forced back to
    match it immediately after the native call returns, and GAD_LEVEL_RVA
    is re-derived to match. Deliberately does NOT go through
    inject_gad_power()'s own "NEVER WRITES BACKWARD" floor — that floor
    exists specifically to protect against a possibly-stale CALLER value,
    but here the caller value (self.gad_powers_received) is the trusted,
    authoritative one and live memory is what just got corrupted by the
    native call, so flooring against live memory here would defeat the
    correction entirely. None on this parameter (the default, used by
    every apply_now=False caller, where this native call never runs
    anyway) skips the correction.
    """
    try:
        pm.write_bytes(base + GAD_4_RVA, bytes([1]), 1)
        _resync_gad_level(pm, base)
        if apply_now:
            _apply_gad_level_now(pm, base)
            if known_good_temple_tier is not None:
                tier = max(0, min(known_good_temple_tier, 3))
                for i, rva in enumerate(GAD_POWER_RVAS[:3], start=1):
                    pm.write_bytes(base + rva, bytes([1 if i <= tier else 0]), 1)
                _resync_gad_level(pm, base)
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] inject_poigne_ability failed: {exc}")
        return False


def _build_console_print_shellcode(base: int, buf_ptr: int) -> bytes:
    """
    Calls the same two functions the giveinv handler (FUN_140310c20) uses to
    print its usage message to the in-game console:
        FUN_1401b4260(&PTR_vftable_140806760, &DAT_1407ee5d8, buf_ptr)
        FUN_140202300(&PTR_vftable_140c8de50, buf_ptr)
    buf_ptr must already point at a null-terminated string of at most
    CONSOLE_PRINT_MAX_LEN bytes (plus the null terminator).
    """
    fn1      = base + CONSOLE_PRINT_FN1_RVA
    fn1_arg1 = base + CONSOLE_PRINT_FN1_ARG1_RVA
    fn1_arg2 = base + CONSOLE_PRINT_FN1_ARG2_RVA
    fn2      = base + CONSOLE_PRINT_FN2_RVA
    fn2_arg1 = base + CONSOLE_PRINT_FN2_ARG1_RVA

    sc  = b"\x48\x83\xec\x28"                             # sub rsp, 0x28
    # ── call 1: FUN_1401b4260(fn1_arg1, fn1_arg2, buf_ptr) ──────────────────
    sc += b"\x48\xb9" + struct.pack("<Q", fn1_arg1)       # mov rcx, fn1_arg1
    sc += b"\x48\xba" + struct.pack("<Q", fn1_arg2)       # mov rdx, fn1_arg2
    sc += b"\x49\xb8" + struct.pack("<Q", buf_ptr)        # mov r8,  buf_ptr
    sc += b"\x48\xb8" + struct.pack("<Q", fn1)            # mov rax, fn1
    sc += b"\xff\xd0"                                      # call rax
    # ── call 2: FUN_140202300(fn2_arg1, buf_ptr) ────────────────────────────
    sc += b"\x48\xb9" + struct.pack("<Q", fn2_arg1)       # mov rcx, fn2_arg1
    sc += b"\x48\xba" + struct.pack("<Q", buf_ptr)        # mov rdx, buf_ptr
    sc += b"\x48\xb8" + struct.pack("<Q", fn2)            # mov rax, fn2
    sc += b"\xff\xd0"                                      # call rax
    sc += b"\x48\x83\xc4\x28"                             # add rsp, 0x28
    sc += b"\xc3"                                          # ret
    return sc


def inject_console_print(pm, base: int, message: str) -> bool:
    """
    Print `message` to the in-game console/HUD, using the exact same print
    path the giveinv handler uses for its own usage text. Useful for AP
    notifications (item received, hints, goal complete) without needing a
    separate overlay — see the client.py module docstring for why we're not
    building native chat integration.
    """
    try:
        msg_bytes = message.encode("ascii", errors="replace")[:CONSOLE_PRINT_MAX_LEN]
        msg_bytes += b"\x00"
        buf = pm.allocate(len(msg_bytes))
        try:
            pm.write_bytes(buf, msg_bytes, len(msg_bytes))
            sc = _build_console_print_shellcode(base, buf)
            _remote_exec_shellcode(pm, sc)
        finally:
            pm.free(buf)
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] inject_console_print({message!r}) failed: {exc}")
        return False


def _build_check_flag_shellcode(obj_ptr: int, flag_id: int, out_ptr: int) -> bytes:
    """
    Calls (*obj_ptr->vtable[FLAG_MANAGER_VT_HASFLAG])(obj_ptr, flag_id) and
    writes the 1-byte bool result to out_ptr. Unlike the other inject_*
    shellcodes, this one needs to report a value back to Python, so it writes
    its result into a small scratch buffer we read back after the remote
    thread finishes (CreateRemoteThread's own exit code isn't used for this,
    to keep the calling convention identical to the other helpers here).
    """
    sc  = b"\x48\x83\xec\x28"                                            # sub  rsp, 0x28
    sc += b"\x48\xb9" + struct.pack("<Q", obj_ptr)                       # mov  rcx, obj_ptr (this)
    sc += b"\xba" + struct.pack("<I", flag_id & 0xFFFFFFFF)              # mov  edx, flag_id
    sc += b"\x48\x8b\x01"                                                # mov  rax, [rcx]        (vtable)
    sc += b"\x48\x8b\x80" + struct.pack("<I", FLAG_MANAGER_VT_HASFLAG)   # mov  rax, [rax+0x18]
    sc += b"\xff\xd0"                                                     # call rax
    sc += b"\x49\xb8" + struct.pack("<Q", out_ptr)                       # mov  r8, out_ptr
    sc += b"\x41\x88\x00"                                                 # mov  [r8], al
    sc += b"\x48\x83\xc4\x28"                                            # add  rsp, 0x28
    sc += b"\xc3"                                                         # ret
    return sc


def check_flag(pm, base: int, flag_id: int) -> Optional[bool]:
    """
    Read a boolean game-state flag via the same HasFlag(id) accessor the
    game's own achievement-sweep code uses (see FLAG_MANAGER_RVA notes
    above). Returns None if the read failed (e.g. game not running).
    """
    try:
        obj_ptr = base + FLAG_MANAGER_RVA
        out_buf = pm.allocate(1)
        try:
            pm.write_bytes(out_buf, b"\x00", 1)
            sc = _build_check_flag_shellcode(obj_ptr, flag_id, out_buf)
            _remote_exec_shellcode(pm, sc)
            result = pm.read_bytes(out_buf, 1)
        finally:
            pm.free(out_buf)
        return result[0] != 0
    except Exception as exc:
        logger.warning(f"[ShadowMan] check_flag({flag_id:#x}) failed: {exc}")
        return None


def check_legion_defeated(pm, base: int) -> Optional[bool]:
    """Convenience wrapper — see LEGION_DEFEATED_FLAG_ID notes above."""
    return check_flag(pm, base, LEGION_DEFEATED_FLAG_ID)


def read_current_health(pm, base: int) -> Optional[int]:
    """See CURRENT_HEALTH_RVA notes above."""
    try:
        return pm.read_int(base + CURRENT_HEALTH_RVA)
    except Exception as exc:
        logger.warning(f"[ShadowMan] read_current_health failed: {exc}")
        return None


def _read_is_at_title_screen(pm, base: int) -> Optional[bool]:
    """
    See TITLE_SCREEN_*_RVA above.

    Returns:
      True  — all 5 checks agree we're at the title/attract screen.
      False — both "in game" checks (1/2 == 65536) agree we're not.
      None  — reads failed, or the checks disagree with each other
              (e.g. mid-transition). Callers must treat None the same as
              True (fail closed / not safe to act) — never as a green
              light.
    """
    try:
        t1 = pm.read_int(base + TITLE_SCREEN_1_RVA)
        t2 = pm.read_int(base + TITLE_SCREEN_2_RVA)
        t3 = pm.read_int(base + TITLE_SCREEN_3_RVA)
        t4 = pm.read_int(base + TITLE_SCREEN_4_RVA)
        t5 = pm.read_int(base + TITLE_SCREEN_5_RVA)
    except Exception:
        return None

    if t1 == 0 and t2 == 0 and t3 == 1 and t4 == 1 and t5 == 1:
        return True
    if t1 == 65536 and t2 == 65536:
        return False
    return None   # ambiguous / transitional read — don't guess


def _read_current_level_live(pm, base: int) -> Optional[str]:
    """
    See CURRENT_LEVEL_RVA / LEVEL_NUMBER_TO_ID above. Returns None (not the
    numeric value) whenever the read fails OR the number isn't in the table
    yet — callers should treat None as "no live opinion" and keep whatever
    current_level already held, not blank it out. This keeps unmapped
    levels degrading gracefully to the pre-existing save-file behavior
    instead of regressing them.
    """
    try:
        num = pm.read_int(base + CURRENT_LEVEL_RVA)
    except Exception:
        return None
    level_id = LEVEL_NUMBER_TO_ID.get(num)
    if level_id is None:
        logger.info(f"[ShadowMan] Live level number {num} not yet in "
                     f"LEVEL_NUMBER_TO_ID — falling back to save-file level.")
    # Canonicalize night-phase folder ids to their day-form counterpart —
    # see LEVEL_NIGHT_TO_DAY above. Location data is always keyed by the
    # day form regardless of in-game time of day.
    return _canonical_level_id(level_id)


def _build_death_shellcode(base: int) -> bytes:
    """
    Real DeathLink kill, three calls in sequence (see the DeathLink comment
    block above MAX_HEALTH_RVA for the full derivation):
      1. FUN_1402ea6f0()                        — enable-cheats setup (idempotent)
      2. FUN_14032d120(soul_obj, 1, big_negative) — zero the HUD health value (cosmetic)
      3. FUN_140458680() -> player_ptr; FUN_14046e930(player_ptr, 0, 0) — the REAL death trigger

    Earlier attempts only did step 2, which just updates the on-screen meter
    — it has no concept of death. FUN_14046e930 is the actual trigger, and
    it needs the live player-entity pointer (heap-allocated, not a fixed
    global), fetched fresh each call via FUN_140458680().
    """
    setup_fn      = base + LIGHT_SOUL_SETUP_RVA   # FUN_1402ea6f0
    modify_fn     = base + MODIFY_STAT_FN_RVA     # FUN_14032d120
    soul_obj      = base + LIGHT_SOUL_SOULOBJ_RVA
    get_player_fn = base + GET_PLAYER_FN_RVA      # FUN_140458680
    die_fn        = base + DEATH_TRIGGER_FN_RVA   # FUN_14046e930

    sc  = b"\x48\x83\xec\x28"                                  # sub  rsp, 0x28

    # step 1 — enable cheats (idempotent), same as inject_pickup_system/light_soul
    sc += b"\x48\xb8" + struct.pack("<Q", setup_fn)            # mov  rax, setup_fn
    sc += b"\xff\xd0"                                           # call rax

    # step 2 — player_ptr = FUN_140458680(); keep it in RBX (callee-saved,
    # unclobbered by the next call) across the ModifyStat call below.
    sc += b"\x48\xb8" + struct.pack("<Q", get_player_fn)       # mov  rax, get_player_fn
    sc += b"\xff\xd0"                                           # call rax
    sc += b"\x48\x89\xc3"                                       # mov  rbx, rax   (save player_ptr)

    # step 3 — FUN_14032d120(soul_obj, 1, LETHAL_DAMAGE_DELTA)  (zero HUD health)
    sc += b"\x48\xb9" + struct.pack("<Q", soul_obj)            # mov  rcx, soul_obj (this)
    sc += b"\xba" + struct.pack("<i", STAT_INDEX_CURRENT_HP)   # mov  edx, 1
    sc += b"\x41\xb8" + struct.pack("<i", LETHAL_DAMAGE_DELTA) # mov  r8d, -999999
    sc += b"\x48\xb8" + struct.pack("<Q", modify_fn)           # mov  rax, modify_fn
    sc += b"\xff\xd0"                                           # call rax

    # step 4 — FUN_14046e930(player_ptr, 0, 0)  (the real death trigger)
    sc += b"\x48\x89\xd9"                                       # mov  rcx, rbx  (player_ptr)
    sc += b"\x48\x31\xd2"                                       # xor  rdx, rdx  (param_2 = 0)
    sc += b"\x45\x31\xc0"                                       # xor  r8d, r8d  (param_3 = 0, default death cause)
    sc += b"\x48\xb8" + struct.pack("<Q", die_fn)               # mov  rax, die_fn
    sc += b"\xff\xd0"                                           # call rax

    sc += b"\x48\x83\xc4\x28"                                  # add  rsp, 0x28
    sc += b"\xc3"                                               # ret
    return sc


def inject_death(pm, base: int) -> bool:
    """
    Kill the local player for DeathLink by calling the real stat-modification
    function (see MODIFY_STAT_FN_RVA) instead of writing raw memory — a plain
    write to CURRENT_HEALTH_RVA was tested live via /deathtest and confirmed
    to NOT trigger an actual death.
    """
    try:
        sc = _build_death_shellcode(base)
        _remote_exec_shellcode(pm, sc)
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] inject_death failed: {exc}")
        return False


async def inject_death_until_confirmed(
    pm, base: int, timeout: float = 30.0, poke_interval: float = 2.0
) -> bool:
    """
    Re-assert inject_death() every poke_interval until health is observed to
    drop to 0 AND then rise again (a real death+respawn cycle), or timeout
    elapses. The game appears to pause its Update() loop on window focus
    loss, so a single injected call can land while paused and never get
    processed — polling keeps retrying the kill until the game is actually
    ticking and processes it.

    2026-08-09 (Jon reported weird game states from a DeathLink landing
    during a cutscene, e.g. a Dark Soul pickup): previously this stopped
    re-injecting the instant a health<=0 reading was seen, on the
    assumption that meant the kill had already landed for real. Per this
    file's own DeathLink derivation notes above (see the comment block
    above MAX_HEALTH_RVA / _build_death_shellcode): inject_death() is
    really two independent steps — a cosmetic ModifyStat call that
    unconditionally zeroes the REAL underlying health value with no
    guard at all, and a SEPARATE die_fn (FUN_14046e930) call that is the
    actual death trigger and DOES check guard flags first (already-dead,
    and an invincible bit) before it does anything — silently no-opping
    if either is set. A short pickup/cutscene animation plausibly sets
    that invincible bit for its duration (a common engine pattern for
    "player is busy, don't let a hit interrupt this"). If a DeathLink
    lands during exactly that window, the cosmetic health-zero still
    lands (no guard on it) while the real kill silently doesn't — leaving
    the player stuck "alive" at 0 HP, no death animation, no respawn,
    until something else eventually notices — a genuinely broken
    in-between state, which fits "weird game states" well.

    die_fn's own guard checks make repeat calls harmless either way: if a
    real death already landed, the already-dead guard makes a further
    call a clean no-op; if it was blocked by invincibility, only
    continuing to retry ever gives it a chance to land once that guard
    clears. So this now keeps re-asserting inject_death() every
    poke_interval regardless of whether a health<=0 reading has already
    been seen — the loop only ever stops early on a genuine respawn
    (health rising back above 0 after having been seen at <=0) or on
    timeout. Returns True if a full death+respawn cycle was confirmed,
    False if only the drop-to-0 was seen (or nothing at all) before
    timeout — unchanged from before.

    This reasoning is grounded in this file's own already-documented
    die_fn guard behavior, not a fresh live capture — worth Jon confirming
    a DeathLink landing mid-cutscene now resolves into a normal death
    (once the cutscene/invincibility window ends) instead of leaving the
    player stuck at 0 HP.
    """
    deadline = time.monotonic() + timeout
    seen_dead = False
    while time.monotonic() < deadline:
        health = read_current_health(pm, base)
        if health is not None:
            if health <= 0:
                seen_dead = True
            elif seen_dead and health > 0:
                return True
        inject_death(pm, base)
        await asyncio.sleep(poke_interval)
    return seen_dead


def _build_modify_stat_shellcode(base: int, delta: int) -> bytes:
    """
    See the "Health Effects" comment block above HEALTH_EFFECT_TICK_INTERVAL_SECONDS.
    Calls FUN_1402ea6f0() (idempotent setup, same as every other call site
    that uses this soul_obj) then FUN_14032d120(soul_obj, STAT_INDEX_CURRENT_HP,
    delta) — identical shape to _build_death_shellcode's own steps 1+2,
    just with an arbitrary signed delta instead of a hardcoded lethal one,
    and with no death-trigger call at all. Caller (see _run_health_effect)
    is responsible for clamping `delta` so the resulting health can never
    reach <=0 — this function has no opinion on that, it just applies
    whatever delta it's given.
    """
    setup_fn  = base + LIGHT_SOUL_SETUP_RVA
    modify_fn = base + MODIFY_STAT_FN_RVA
    soul_obj  = base + LIGHT_SOUL_SOULOBJ_RVA

    sc  = b"\x48\x83\xec\x28"                                  # sub  rsp, 0x28
    sc += b"\x48\xb8" + struct.pack("<Q", setup_fn)            # mov  rax, setup_fn
    sc += b"\xff\xd0"                                           # call rax
    sc += b"\x48\xb9" + struct.pack("<Q", soul_obj)            # mov  rcx, soul_obj (this)
    sc += b"\xba" + struct.pack("<i", STAT_INDEX_CURRENT_HP)   # mov  edx, 1
    sc += b"\x41\xb8" + struct.pack("<i", delta)                # mov  r8d, delta
    sc += b"\x48\xb8" + struct.pack("<Q", modify_fn)           # mov  rax, modify_fn
    sc += b"\xff\xd0"                                           # call rax
    sc += b"\x48\x83\xc4\x28"                                  # add  rsp, 0x28
    sc += b"\xc3"                                               # ret
    return sc


def apply_health_delta(pm, base: int, delta: int) -> bool:
    """
    Apply a real (non-cosmetic) health change via ModifyStat — see
    _build_modify_stat_shellcode. Caller must have already clamped `delta`
    to a safe range (resulting health never <=0, never above max) before
    calling this; this function performs no clamping of its own.
    """
    try:
        sc = _build_modify_stat_shellcode(base, delta)
        _remote_exec_shellcode(pm, sc)
        return True
    except Exception as exc:
        logger.warning(f"[ShadowMan] apply_health_delta({delta}) failed: {exc}")
        return False


# ── Command processor ──────────────────────────────────────────────────────────

class ShadowManCommandProcessor(SuperCommandProcessor):
    """
    In-client slash commands. Grouped roughly by who they're for:

      Everyday use:     /savedir, /status, /catchup
      Testing effects:  /testitem, /testbacklog, /testdeath, /poisonme,
                         /healme, /drainvoodoo, /maxvoodoo, /drainammo,
                         /maxammo
      Advanced/debug:   /secret, /checksecrets, /checkcallbacks,
                         /resetsecrets, /pollerstatus, /console, /itemspeed,
                         /debug

    The "testing effects" and "advanced/debug" commands are development
    tools, not something you need for normal play — they exist so effects
    can be tried out without waiting for the right item to actually drop.
    """
    ctx: "ShadowManContext"

    def _cmd_savedir(self, *args: str) -> None:
        """Show or set your save folder path.  Usage: /savedir [path]"""
        if args:
            p = Path(" ".join(args))
            if p.is_dir():
                self.ctx.save_dir = p
                logger.info(f"[ShadowMan] Save folder set to: {p}")
            else:
                logger.warning(f"[ShadowMan] Not a folder: {p}")
        else:
            logger.info(f"[ShadowMan] Save folder: {self.ctx.save_dir or '(not found)'}")

    def _cmd_status(self) -> None:
        """Show your current save, level, health/soul/gad progress, and connection status."""
        ctx = self.ctx
        pm, base = _get_process()   # also logs a one-time warning if only the vanilla exe is found
        game_running = pm is not None
        logger.info(f"[ShadowMan] Patched exe attached ({PATCHED_EXE_NAME}): {game_running}")

        # Prefer a fresh live-memory read for soul/gad/cadeaux over the
        # session-tracked fallbacks below — those only update from a
        # save-file parse (last_soul_count) or from AP items actually
        # processed by THIS client session (gad_powers_received), so a
        # client reconnect mid-game, or simply not having triggered a save
        # write yet, left /status reporting 0 while the real in-game
        # values were already higher (user report, 2026-07-21 — the poll
        # loop's own "Live soul counter: 4 -> 5" log was correct the whole
        # time, /status just wasn't reading the field it comes from).
        live_soul = live_gad = live_cad = None
        if game_running:
            title_state = _read_is_at_title_screen(pm, base)
            in_game_str = "yes" if title_state is False else (
                "no (at title screen)" if title_state is True else "unknown")
            logger.info(f"[ShadowMan] Confirmed in-game (item injection armed): {in_game_str}")
            try:
                live_soul = pm.read_int(base + SOUL_COUNT_RVA)
            except Exception:
                pass
            try:
                live_gad = pm.read_int(base + GAD_LEVEL_RVA)
            except Exception:
                pass
            try:
                live_cad = pm.read_int(base + CADEAUX_COUNT_RVA)
            except Exception:
                pass

        soul_display = live_soul if live_soul is not None else ctx.last_soul_count
        # gad_level_raw matches the "givegad" console encoding (0-7, see
        # _decode_gad_level) — NOT a plain 0-4 ability count on its own.
        gad_level_raw = live_gad if live_gad is not None else (
            ctx.gad_powers_received + (4 if ctx.poigne_ability_received else 0))
        gad_temple_tier, gad_has_poigne = _decode_gad_level(gad_level_raw)
        # Soul Level (SL) implied by soul_display, per this seed's own
        # thresholds (ctx.soul_thresholds — vanilla unless soul_threshold_mode
        # randomized them, see on_package's Connected handler). Uses the same
        # _soul_level_for_count() helper _sync_soul_level() calls after every
        # injection, so this always matches what's actually on the in-game
        # meter rather than assuming vanilla SL/soul mapping.
        sl_display = (
            _soul_level_for_count(soul_display, ctx.soul_thresholds)
            if isinstance(soul_display, int) else None
        )

        logger.info(f"[ShadowMan] Save dir:      {ctx.save_dir or '(not found)'}")
        logger.info(f"[ShadowMan] Current level: {ctx.current_level or '(unknown)'}")
        logger.info(f"[ShadowMan] Soul count:    {soul_display}")
        logger.info(f"[ShadowMan] Soul level:    "
                    f"{f'SL{sl_display}' if sl_display is not None else '(unknown)'}")
        logger.info(
            f"[ShadowMan] Gad powers:    Gad Temple tier {gad_temple_tier}/3, "
            f"Poigne: {'yes' if gad_has_poigne else 'no'} "
            f"(raw givegad value {gad_level_raw})")
        logger.info(f"[ShadowMan] Cadeaux:       "
                    f"{live_cad if live_cad is not None else '(unknown — game not running)'}")
        logger.info(f"[ShadowMan] Checked locs:  {len(ctx.locations_checked)}")
        logger.info(f"[ShadowMan] Items received: {ctx.items_received_index}")

    def _cmd_catchup(self, *args: str) -> None:
        """Re-apply any items you've received but haven't seen show up yet.  Usage: /catchup"""
        asyncio.create_task(self.ctx._replay_all_received_items())

    def _cmd_itemspeed(self, *args: str) -> None:
        """
        Advanced: controls how fast a big batch of items gets applied one
        after another (e.g. right after reconnecting with a backlog of
        checks from other players). Lower is faster but was historically
        used to slow things down as a troubleshooting step if items
        applying back-to-back ever seemed to cause instability — shouldn't
        normally be needed.

        Usage: /itemspeed              -- show the current delay
               /itemspeed <seconds>    -- change it (e.g. /itemspeed 0.5)
        """
        global ITEM_INJECT_PACING_SECONDS
        if not args:
            logger.info(
                f"[ShadowMan] Item apply delay: {ITEM_INJECT_PACING_SECONDS}s between items")
            return
        try:
            new_val = float(args[0])
            if new_val < 0:
                raise ValueError
        except ValueError:
            logger.warning("[ShadowMan] Usage: /itemspeed [seconds >= 0]")
            return
        old_val = ITEM_INJECT_PACING_SECONDS
        ITEM_INJECT_PACING_SECONDS = new_val
        logger.info(
            f"[ShadowMan] Item apply delay: {old_val}s -> {new_val}s "
            f"(this session only — restarting the client resets it to default).")

    def _cmd_testdeath(self) -> None:
        """
        Test the DeathLink kill without waiting for a real one to arrive
        from another player. Usage: /testdeath
        """
        async def _run_testdeath() -> None:
            pm, base = _get_process()
            if pm is None:
                logger.warning("[ShadowMan] Game isn't running.")
                return
            confirmed = await inject_death_until_confirmed(pm, base)
            logger.info(
                f"[ShadowMan] Death test: "
                f"{'confirmed — you died and respawned' if confirmed else 'sent, but no respawn seen yet'}."
            )

        asyncio.create_task(_run_testdeath())

    def _cmd_console(self, *args: str) -> None:
        """
        Advanced: run a console command in the live game remotely, the same
        as typing it into the in-game (~) console yourself.
        Usage: /console g_deadsidegunmode 1
        """
        if not args:
            logger.warning("[ShadowMan] Usage: /console <command text>  e.g. /console g_deadsidegunmode 1")
            return
        command = " ".join(args)
        pm, base = _get_process()
        if pm is None:
            logger.warning("[ShadowMan] Game isn't running.")
            return
        ok = send_console_command(pm, base, command)
        logger.info(f"[ShadowMan] Console command {command!r}: {'sent' if ok else 'failed'}")

    def _cmd_checkcallbacks(self, *args: str) -> None:
        """
        Advanced/diagnostic: checks which secret effects (Big Head,
        Wireframe, etc.) are confirmed safe to apply instantly vs. ones
        that still need a level change to show up. Shouldn't be needed for
        normal play. Usage: /checkcallbacks
        """
        pm, base = _get_process()
        if pm is None:
            logger.warning("[ShadowMan] Game isn't running.")
            return
        known_good = base + MODE_CVAR_ONCHANGE_CALLBACK_RVA
        results = dump_secret_cvar_callbacks(pm, base)
        logger.info(f"[ShadowMan] Checking secret effects against the confirmed-safe instant-apply path...")
        for name, cb in results.items():
            if cb is None:
                logger.info(f"[ShadowMan]   {name:<20} could not read")
            elif cb == known_good:
                logger.info(f"[ShadowMan]   {name:<20} instant-apply confirmed safe")
            else:
                logger.info(f"[ShadowMan]   {name:<20} different — not yet confirmed, may need a level change")

    def _cmd_checksecrets(self, *args: str) -> None:
        """
        Advanced/diagnostic: verifies the game's secret effects (Big Head,
        Wireframe, Dog Mode, etc.) are all mapped to the right memory
        addresses. Run this if secret effects ever seem to apply the wrong
        thing. Usage: /checksecrets
        """
        pm, base = _get_process()
        if pm is None:
            logger.warning("[ShadowMan] Game isn't running.")
            return
        results = dump_secret_cvar_names(pm, base)
        mismatches = 0
        for expected, actual in results.items():
            if actual is None:
                logger.info(f"[ShadowMan]   {expected:<20} could not read")
            elif actual == expected:
                logger.info(f"[ShadowMan]   {expected:<20} OK")
            else:
                mismatches += 1
                logger.info(f"[ShadowMan]   {expected:<20} MISMATCH — memory says {actual!r}")
        if mismatches:
            logger.warning(f"[ShadowMan] {mismatches} mismatch(es) found — "
                            f"don't trust /secret or Trap/Bonus secret effects for these "
                            f"until this is fixed in the code.")
        else:
            logger.info("[ShadowMan] All secret effects are mapped correctly.")

    def _cmd_pollerstatus(self, *args: str) -> None:
        """
        Advanced/diagnostic: shows whether the background system that
        applies secret effects instantly (rather than waiting for a level
        change) looks alive and working right now.
        Usage: /pollerstatus
        """
        pm, base = _get_process()
        if pm is None:
            logger.warning("[ShadowMan] Game isn't running.")
            return
        snapshot = read_poller_last_known(pm, base)
        if snapshot is None:
            logger.warning("[ShadowMan] Couldn't read status — the instant-apply patch "
                            "may not be installed on this game copy, or it isn't "
                            "fully loaded yet.")
            return
        for i, name in enumerate(POLLER_SECRET_TABLE):
            val = snapshot[i]
            tag = " (not seen yet)" if val == LAST_KNOWN_POLLER_SENTINEL else ""
            logger.info(f"[ShadowMan]   {name:<20} last value={val:#04x}{tag}")

    def _cmd_resetsecrets(self, *args: str) -> None:
        """
        Turns off every cosmetic secret effect (Big Head, Wireframe, etc.)
        right now. Useful if one got left on from earlier testing.
        Usage: /resetsecrets
        """
        pm, base = _get_process()
        if pm is None:
            logger.warning("[ShadowMan] Game isn't running.")
            return
        self._reset_all_secrets_off(pm, base)

    def _cmd_secret(self, *args: str) -> None:
        """
        Advanced: check or force one of the game's cosmetic secret effects
        (Big Head, Wireframe, Dog Mode, Disco Lights, etc.) by name.
        Usage: /secret <name>          -- show its current on/off state
               /secret <name> 0|1      -- force it off/on (may need a
                                           level change to visibly apply)
        """
        pm, base = _get_process()
        if pm is None:
            logger.warning("[ShadowMan] Game isn't running.")
            return
        if not args:
            logger.warning(f"[ShadowMan] Usage: /secret <name> [0|1]. Known: {', '.join(SECRET_CVAR_RVAS)}")
            return
        name = args[0]
        if name not in SECRET_CVAR_RVAS:
            logger.warning(f"[ShadowMan] Unknown secret {name!r}. Known: {', '.join(SECRET_CVAR_RVAS)}")
            return
        rva = SECRET_CVAR_RVAS[name]
        if len(args) == 1:
            val = read_cvar_bool(pm, base, rva)
            logger.info(f"[ShadowMan] {name} = {val}")
            return
        try:
            target = bool(int(args[1]))
        except ValueError:
            logger.warning("[ShadowMan] Usage: /secret <name> [0|1]")
            return
        ok = write_cvar_bool(pm, base, rva, target)
        readback = read_cvar_bool(pm, base, rva)
        logger.info(f"[ShadowMan] {name} set to {int(target)}: {'ok' if ok else 'FAILED'}, "
                    f"now reads {readback}")

    def _cmd_testitem(self, *args: str) -> None:
        """
        Give yourself a specific item right now, without needing to
        actually receive it from another player. Handy for testing what an
        item does.
        Usage: /testitem <item name>          e.g. /testitem Gad Power
                                                     /testitem Trap/Bonus
                                                     /testitem Poigne
                                                     /testitem Dark Soul
        """
        if not args:
            logger.warning(
                f"[ShadowMan] Usage: /testitem <item name>. Known: "
                f"{', '.join(AP_ITEM_INJECTION.keys())}")
            return
        item_name = " ".join(args)
        if item_name not in AP_ITEM_INJECTION and not is_cadeaux_item(item_name):
            logger.warning(
                f"[ShadowMan] Unknown item {item_name!r}. Known: "
                f"{', '.join(AP_ITEM_INJECTION.keys())}")
            return
        asyncio.create_task(
            self.ctx._simulate_items([item_name], f"/testitem {item_name}"))

    def _cmd_testbacklog(self, *args: str) -> None:
        """
        Advanced: gives yourself a whole batch of items back to back, the
        same way reconnecting with a pile of unclaimed checks would. Used
        for stress-testing, not normal play.
        Usage: /testbacklog gad49       -- a fixed test batch that mixes
                                            Gad Power, Trap/Bonus, and
                                            Poigne items together
               /testbacklog mixed [n]   -- a random-looking batch of n
                                            items (default 56)
        """
        if not args:
            logger.warning(
                "[ShadowMan] Usage: /testbacklog gad49 | /testbacklog mixed [n]")
            return
        scenario = args[0].lower()
        if scenario == "gad49":
            items = list(_SIM_SCENARIOS["gad49"])
            label = "gad49 (Gad Power/Trap-Bonus/Poigne test batch)"
        elif scenario == "mixed":
            n = 56
            if len(args) > 1 and args[1].isdigit():
                n = int(args[1])
            items = _build_sim_mixed_backlog(n)
            label = f"mixed ({n} items)"
        else:
            logger.warning(
                f"[ShadowMan] Unknown scenario {scenario!r}. Usage: "
                f"/testbacklog gad49 | /testbacklog mixed [n]")
            return
        asyncio.create_task(self.ctx._simulate_items(items, label))

    def _cmd_poisonme(self, *args: str) -> None:
        """
        Start a gradual poison effect on yourself (the same effect a
        Trap/Bonus item can apply) — drains your health over time.
        Usage: /poisonme [seconds]   (default 60; try a small number like
                                       10 for a quick test)
        """
        pm, base = _get_process()
        if pm is None:
            logger.warning("[ShadowMan] Game isn't running.")
            return
        if _read_is_at_title_screen(pm, base) is not False:
            logger.warning("[ShadowMan] You need to be in a level for this to work.")
            return
        seconds = HEALTH_EFFECT_TOTAL_SECONDS
        if args:
            try:
                seconds = float(args[0])
            except ValueError:
                logger.warning("[ShadowMan] Usage: /poisonme [seconds]")
                return
        self.ctx.start_health_effect("poison", total_seconds=seconds)

    def _cmd_healme(self, *args: str) -> None:
        """
        Start a gradual healing effect on yourself (the same effect a
        Trap/Bonus item can apply) — restores your health over time.
        Usage: /healme [seconds]   (default 60)
        """
        pm, base = _get_process()
        if pm is None:
            logger.warning("[ShadowMan] Game isn't running.")
            return
        if _read_is_at_title_screen(pm, base) is not False:
            logger.warning("[ShadowMan] You need to be in a level for this to work.")
            return
        seconds = HEALTH_EFFECT_TOTAL_SECONDS
        if args:
            try:
                seconds = float(args[0])
            except ValueError:
                logger.warning("[ShadowMan] Usage: /healme [seconds]")
                return
        self.ctx.start_health_effect("heal", total_seconds=seconds)

    def _cmd_drainvoodoo(self, *args: str) -> None:
        """
        Instantly empty your Voodoo Power (the same effect a Trap/Bonus
        item can apply). Usage: /drainvoodoo
        """
        pm, base = _get_process()
        if pm is None:
            logger.warning("[ShadowMan] Game isn't running.")
            return
        if _read_is_at_title_screen(pm, base) is not False:
            logger.warning("[ShadowMan] You need to be in a level for this to work.")
            return
        ok = self.ctx.trigger_voodoo_drain(pm, base)
        readback = read_voodoo_power(pm, base)
        logger.info(f"[ShadowMan] Voodoo drain: {'ok' if ok else 'FAILED'} — "
                    f"voodoo power now {readback}")

    def _cmd_maxvoodoo(self, *args: str) -> None:
        """
        Fill your Voodoo Power to max and keep it there for a while (the
        same effect a Trap/Bonus item can apply).
        Usage: /maxvoodoo [seconds]   (default 60)
        """
        pm, base = _get_process()
        if pm is None:
            logger.warning("[ShadowMan] Game isn't running.")
            return
        if _read_is_at_title_screen(pm, base) is not False:
            logger.warning("[ShadowMan] You need to be in a level for this to work.")
            return
        seconds = HEALTH_EFFECT_TOTAL_SECONDS
        if args:
            try:
                seconds = float(args[0])
            except ValueError:
                logger.warning("[ShadowMan] Usage: /maxvoodoo [seconds]")
                return
        self.ctx.start_voodoo_max_hold(total_seconds=seconds)

    def _cmd_drainammo(self, *args: str) -> None:
        """
        Instantly empty all your tracked ammo — Shotgun, Violator, 9mm
        (the same effect a Trap/Bonus item can apply). Usage: /drainammo
        """
        pm, base = _get_process()
        if pm is None:
            logger.warning("[ShadowMan] Game isn't running.")
            return
        if _read_is_at_title_screen(pm, base) is not False:
            logger.warning("[ShadowMan] You need to be in a level for this to work.")
            return
        ok = self.ctx.trigger_ammo_drain(pm, base)
        readback = read_ammo_counts(pm, base)
        logger.info(f"[ShadowMan] Ammo drain: {'ok' if ok else 'FAILED'} — {readback}")

    def _cmd_maxammo(self, *args: str) -> None:
        """
        Fill all your tracked ammo to max and keep it there for a while —
        Shotgun, Violator, 9mm (the same effect a Trap/Bonus item can
        apply). Usage: /maxammo [seconds]   (default 60)
        """
        pm, base = _get_process()
        if pm is None:
            logger.warning("[ShadowMan] Game isn't running.")
            return
        if _read_is_at_title_screen(pm, base) is not False:
            logger.warning("[ShadowMan] You need to be in a level for this to work.")
            return
        seconds = HEALTH_EFFECT_TOTAL_SECONDS
        if args:
            try:
                seconds = float(args[0])
            except ValueError:
                logger.warning("[ShadowMan] Usage: /maxammo [seconds]")
                return
        self.ctx.start_ammo_max_hold(total_seconds=seconds)

    def _cmd_debug(self, *args: str) -> None:
        """
        Show or toggle verbose per-tick logging for Voodoo/Ammo Max Hold
        (the "tick N/M: ok — cap=..." lines these two effects emit every
        HEALTH_EFFECT_TICK_INTERVAL_SECONDS while active). Off by default —
        those lines are development/debug noise during normal play,
        especially with Trap/Bonus's permanent holds running for a while.
        Session-only: resets to off on every client launch.
        Usage: /debug [on|off]   (no argument shows current state)
        """
        ctx = self.ctx
        if not args:
            state = "on" if ctx.debug_hold_logging else "off"
            logger.info(f"[ShadowMan] Voodoo/Ammo Max Hold tick logging: {state}")
            return
        choice = args[0].strip().lower()
        if choice in ("on", "true", "1"):
            ctx.debug_hold_logging = True
        elif choice in ("off", "false", "0"):
            ctx.debug_hold_logging = False
        else:
            logger.warning("[ShadowMan] Usage: /debug [on|off]")
            return
        logger.info(f"[ShadowMan] Voodoo/Ammo Max Hold tick logging: "
                    f"{'on' if ctx.debug_hold_logging else 'off'}")


# ── Main context ───────────────────────────────────────────────────────────────

class ShadowManContext(SuperContext):
    """
    AP client context for Shadow Man Remastered.

    Detection  (game → AP): _save_watcher_loop polls .sav + user_activity.dat
    Injection  (AP → game): _item_inject_loop calls inject_* helpers via pymem
    """

    # Reset away from TrackerGameContext's tags = {"AP", "Tracker"} — this
    # is a real playing client, not a tracker-only client (see UT's
    # docs/client-integration.md). No-op when UT isn't installed, since
    # CommonContext's own default is already {"AP"}.
    tags              = {"AP"}
    game              = GAME_NAME
    items_handling    = 0b111
    command_processor = ShadowManCommandProcessor

    # Save folder
    save_dir: Optional[Path]

    # Per-slot kexShadowManQuestObject state snapshots (all quest.rsc pickups)
    _slot_quest_states: Dict[int, Dict[int, int]]
    # Per-slot kexShadowManQuestObject state snapshots keyed by file_pos
    # instead of instance_id — needed for save_idx=0 records (the
    # RSC_X_BOOK_OF_SHADOWS Archipelago-item marker; see generate_output in
    # __init__.py), which intentionally share instance_id=0 across many
    # locations and so can't be told apart via _slot_quest_states alone.
    # Resolved by position instead, same as Govi. See
    # _parse_questobject_states_by_pos / _match_govi_position_scan.
    _slot_questobj_pos_states: Dict[int, Dict[int, Tuple[int, int]]]
    _slot_mtimes:       Dict[int, float]
    _sact_mtime:        float
    last_soul_count:    int
    current_level:      Optional[str]
    # SL1-SL10 -> souls required for THIS seed, from slot_data's
    # soul_thresholds (see fill_slot_data in __init__.py). Defaults to
    # vanilla until Connected arrives so early polls have something sane.
    # Needed because soul_threshold_mode can randomize these per-seed —
    # any soul-level computation done client-side must use this table,
    # not the hardcoded VANILLA_SOUL_THRESHOLDS.
    soul_thresholds:    Dict[int, int]

    # Location map from slot_data: (level_id, instance_id) → ap_location_id
    # (used for QuestObject records, which DO have a reliable instance_id)
    _loc_map: Dict[Tuple[str, int], int]
    # Position index from slot_data: (level_id, x, y, z, ap_location_id),
    # used for Govi records instead — see _match_govi_position_scan.
    _govi_pos_index: List[Tuple[str, float, float, float, int]]

    # Live-memory watcher state (see _memory_watcher_loop / _poll_live_memory)
    # _live_quest_states: {item_obj: last-seen state} for QuestObject instances
    # _live_scan_tick:    counter gating full heap re-scans (expensive) vs.
    #   cheap cached-address re-reads (every tick) — see
    #   MEMORY_FULL_SCAN_EVERY_N_POLLS
    # _live_scan_attempted: set True after the FIRST full scan this
    #   session, regardless of whether it found anything. Originally added
    #   because live DarkSoul objects were rarely if ever caught by the
    #   (now-removed) Govi heap scan — without this flag, need_scan's old
    #   "rescan if empty" condition forced the expensive (multi-second on a
    #   large heap) full walk on nearly EVERY tick forever. Still useful
    #   now purely as a "have we done the initial scan yet" flag for
    #   QuestObject's own cache.
    _live_quest_states:   Dict[int, int]
    _live_scan_tick:      int
    _live_scan_attempted: bool
    # Inventory-array watcher state (see ITEM_FLAG_RVAS / _poll_live_inventory)
    # _inventory_loc_map:    item_name -> ap_location_id of the location
    #   where THIS seed physically placed that (self-owned) item — built at
    #   Connected time from slot_data's inventory_flag_locs.
    # _live_inventory_states: item_name -> last-seen flag value (max across
    #   the item's RVAs), used to detect the 0 -> nonzero transition. A
    #   first read is baseline-only (prev is None, never fires) so flags
    #   already set at connect time can't produce checks. The injection
    #   paths pre-mark entries to 1 so received-item injection (same
    #   GiveItem the native pickup handler calls) never looks like a
    #   local pickup.
    _inventory_loc_map:     Dict[str, int]
    _live_inventory_states: Dict[str, int]

    # Pickup event log watcher state (see BOOKPOS_LOG_BASE_RVA / _read_bookpos_log_entry)
    # _bookpos_next_index: the next log index we haven't processed yet.
    #   None until the first successful counter read, at which point it's
    #   baselined to the CURRENT counter value (not 0) — we deliberately
    #   don't walk the entire pre-existing log history on connect, since
    #   older entries may predate this AP session entirely.
    _bookpos_next_index: Optional[int]
    # _live_item_pickup_count: last-seen value of ITEM_PICKUP_COUNTER_RVA.
    #   Now doubles as the authoritative log index bound (see
    #   _bookpos_next_index) in addition to being logged on change.
    _live_item_pickup_count: Optional[int]

    # Dark soul flag-array watcher state (see DARKSOUL_FLAGARRAY_PTR_RVA /
    # _read_darksoul_flagarray). _darksoul_flags_prev: previous poll's full
    # byte-array snapshot, diffed against each poll to find newly-flipped
    # (0->1) save_idx indices. None until the first successful read
    # (baseline-only, same reasoning as _bookpos_next_index above — souls
    # collected before this AP session started shouldn't be replayed).
    _darksoul_flags_prev: Optional[bytes]

    # Light Soul flag watcher state (see LIGHT_SOUL_CF_INDEX /
    # _poll_live_light_soul). _light_soul_ap_id: resolved once at
    # Connected time in _build_location_map, by loc_key rather than
    # instance_id (Light Soul has save_idx=0). None if slot_data doesn't
    # contain that loc_key (shouldn't happen, but guarded rather than
    # assumed). _light_soul_resolved: True once CF_GOT_LIGHTSOUL has been
    # read as True and the check sent -- stops polling/re-sending every
    # tick afterward. SUPERSEDED 2026-07-27 the old
    # _light_soul_flag_prev/_light_soul_consec_hits debounce fields (see
    # LIGHT_SOUL_FLAG_RVA's comment) -- CF_GOT_LIGHTSOUL is a genuine
    # one-shot persistent flag, no debounce needed.
    #
    # _light_soul_injected_this_session (2026-08-06, real bug found by
    # Jon: receiving a "Light Soul" item from a DIFFERENT game in the
    # multiworld also credited Shadow Man's own Light Soul LOCATION check,
    # without ever visiting it). Root cause: inject_light_soul()'s
    # shellcode (_build_light_soul_shellcode) replicates the vanilla
    # givelightsoul debug handler's THIRD internal call —
    # (*obj->vtable[1])(obj, 0xE2) — where obj is the exact same
    # flag-manager singleton CF_FLAG_OBJ_RVA/read_named_flag uses, and
    # 0xE2 == 226 == LIGHT_SOUL_CF_INDEX. That call is SetFlag(CF_GOT_
    # LIGHTSOUL) -- the identical flag _poll_live_light_soul polls to
    # detect a genuine physical pickup. Granting the ability (needed for
    # ANY received "Light Soul" item, self-found or foreign -- see
    # ITEM_INJECTION_METHODS' "light_soul" entry, there's no other way to
    # grant it) and setting the flag are NOT separable at the engine
    # level -- inject_light_soul() cannot do one without the other. Set
    # True the moment inject_light_soul() is ever called this session (see
    # its two call sites in _inject_item/_replay_all_received_items) --
    # once True, a CF_GOT_LIGHTSOUL read of True can no longer be trusted
    # as evidence of a genuine physical visit (it may be entirely our own
    # doing), so _poll_live_light_soul stops auto-crediting the location
    # from that point on. KNOWN LIMITATION: if the player receives a
    # "Light Soul" item (from anywhere) BEFORE ever physically visiting
    # Fogometers, the location can no longer be auto-detected via this
    # flag for the rest of the session, even on a later genuine visit --
    # the flag is shared and there's no way to tell the two apart after
    # the fact. A real fix would need an independent, injection-proof
    # physical-presence signal (e.g. a position/coordinate check against
    # the player's live location, mirroring how Govi/AP-marker pickups are
    # already detected) -- not attempted here, would need live
    # investigation to find the right coordinates/mechanism.
    _light_soul_ap_id:        Optional[int]
    _light_soul_resolved:     bool
    _light_soul_injected_this_session: bool

    # Item injection tracking
    # items_received_index: highest AP item index we have processed
    items_received_index: int
    # gad_powers_received: cumulative real "Gad Power" (Gad Temple) items
    # received from AP — NOT including Poigne, which is tracked separately
    # (see poigne_ability_received below; FIXED 2026-07-25, was previously
    # lumped into this same counter — see AP_ITEM_INJECTION's comment).
    gad_powers_received: int
    # Whether we've received (and injected) our own Poigne item this
    # session — independent of gad_powers_received, see
    # inject_poigne_ability.
    poigne_ability_received: bool
    # Ordered record of every item processed this session:
    # (ap_idx, item_name, source_player). source_player is item.player from
    # the NetworkItem — the slot whose LOCATION this item came from, which
    # is our own slot when we found the item in our own Shadow Man world.
    # Used by _replay_all_received_items to re-inject after a game restart,
    # and to skip double-counting self-found Dark Souls (see _inject_item).
    _received_items: List[Tuple[int, str, int]]
    # Item queue from on_package
    _item_queue: asyncio.Queue

    # DeathLink
    # death_link_enabled: read from slot_data on Connected; gates both
    #   sending our own deaths out and (via update_death_link) whether we
    #   ask the server to send us other players' deaths at all.
    death_link_enabled: bool
    # _last_health: previous poll's current-health reading, used to detect
    #   the >0 -> 0 transition (a death) without spamming on every poll
    #   where health is already at 0.
    _last_health: Optional[int]
    # _ignore_next_death: set right before we inject a death ourselves (in
    #   response to an incoming DeathLink) so the health watcher doesn't
    #   observe that self-inflicted zero and broadcast it right back out.
    _ignore_next_death: bool
    # death_link_threshold (2026-07-24): how many of OUR OWN deaths it
    #   takes to actually send one Death Link out — read from slot_data on
    #   Connected (options.py's DeathLinkThreshold; 1 = every death sends,
    #   the old unconditional behavior). Only throttles what we send;
    #   incoming Death Links (on_deathlink()) always kill us immediately
    #   regardless of this counter.
    death_link_threshold: int
    # _own_death_count: how many of our own deaths have occurred since the
    #   last one we actually sent. Resets to 0 every time we send.
    _own_death_count: int

    # ── "Shadow Man" GUI tab state (2026-07-25) ────────────────────────────
    # ap_id -> level_id, built alongside _loc_map/_govi_pos_index in
    # _build_location_map — see that method's comment. Powers the tab's
    # per-level completion bars.
    _ap_id_to_level: Dict[int, str]
    # ap_id -> loc_key (2026-07-26), built the same place as _ap_id_to_level
    # above -- lets the per-level location browser resolve names via the
    # local FRIENDLY_NAMES table instead of the network datapackage. See
    # this file's FRIENDLY_NAMES import comment for why.
    _ap_id_to_loc_key: Dict[int, str]
    # Region names Universal Tracker currently reports as in-logic, updated
    # via the region callback registered in __init__ (tracker_loaded only —
    # stays empty, and the tab just shows its "install UT" fallback, when
    # UT isn't installed). Refreshed on every UT logic recompute, not just
    # ours, so it's usually already current whenever the tab reads it.
    _ut_in_logic_regions: set
    # Location NAMES (not ids — see _on_ut_locations_updated) Universal
    # Tracker currently reports as in-logic, updated via set_callback in
    # __init__ (tracker_loaded only). Powers the GUI tab's per-level
    # location browser's in-logic marker.
    _ut_in_logic_locations: set
    # Whether this seed's piston_combos option is on — from slot_data on
    # Connected (see on_package). Defaults False until then.
    piston_combos_on: bool
    # Whether this seed's Unique Retractor Keys option is on (2026-08-18) —
    # from slot_data on Connected, same pattern as piston_combos_on. Used by
    # _go_mode_prerequisites() to show the 5 named keys individually instead
    # of the flat "Retractors x/5" count.
    unique_retractor_keys_on: bool
    # Last level we re-asserted gad power flags for (see _poll_live_memory's
    # level-transition block, 2026-07-25) — None means "not yet synced this
    # session", not "no level".
    _last_gad_resync_level: Optional[str]
    # Trap/Bonus (2026-08-01, renamed + generalized from "Secret Trap"
    # 2026-08-03) — from slot_data on Connected (see options.py's
    # TrapBonusMode/TrapBonusDuration/TrapBonus{Secrets,Health,Voodoo,Ammo}Enabled).
    # Defaults match those options' own defaults until Connected fires.
    # trap_bonus_mode/trap_bonus_duration are still actively read by
    # _apply_trap_bonus_now (mode/duration are still runtime concerns).
    # The 4 *_enabled flags below are now VESTIGIAL as of 2026-08-05 —
    # category enable/disable moved to generation time
    # (_roll_trap_bonus_item_name() in __init__.py only ever creates an
    # item from an enabled category in the first place, so client.py
    # never needs to re-check this). Left populated from slot_data
    # rather than removed, in case a future feature wants them again.
    trap_bonus_mode: str
    trap_bonus_duration: int
    trap_bonus_secrets_enabled: bool
    trap_bonus_health_enabled: bool
    trap_bonus_voodoo_enabled: bool
    trap_bonus_ammo_enabled: bool
    # Which secret (if any) is currently ON because of a trap, and the
    # pending asyncio revert task for it (None if permanent / already
    # reverted). Only one trap-driven secret is ever meant to be visibly
    # active at once — see _apply_secret_trap's overlap handling.
    _active_secret_trap: Optional[str]
    _active_secret_trap_task: Optional[asyncio.Task]
    # Pending debounce timer for a just-received Secret Trap item — see
    # _apply_secret_trap/_apply_secret_trap_debounced. Distinct from
    # _active_secret_trap_task (that one tracks the REVERT timer for
    # whichever trap is already active; this one tracks the short
    # coalescing window before a newly-received trap is even applied at
    # all).
    _secret_trap_debounce_task: Optional[asyncio.Task]
    # Last-observed snapshot of secret_mode_section_patch.py's LAST_KNOWN
    # array (.apdata) — pure read-only forensics, see
    # _poll_secret_poller_state. None until the first successful read.
    _last_known_poller_snapshot: Optional[bytes]
    # Whether _reset_all_secrets_off has run yet this session — see that
    # method's docstring. Only ever forced True once, on first confirmed
    # in-game transition; never re-armed mid-session so it doesn't stomp
    # a legitimately still-active Secret Trap on a later title-screen
    # round trip.
    _secrets_reset_done: bool
    # Health Effects (Poison / Recovery, 2026-08-03) — the currently-running
    # _run_health_effect task, if any, and which kind ("poison"/"heal") it
    # is. Only one is ever meant to run at once — see start_health_effect.
    # None/None means no effect currently running.
    _active_health_effect_task: Optional[asyncio.Task]
    _active_health_effect_kind: Optional[str]
    # Voodoo Max Hold (2026-08-03) — the currently-running _run_voodoo_max_hold
    # task, if any. Only one is ever meant to run at once — see
    # start_voodoo_max_hold. Separate from the health-effect task above
    # since they're different resources and could otherwise be active
    # simultaneously (e.g. poisoned AND voodoo-boosted at once is fine).
    _active_voodoo_hold_task: Optional[asyncio.Task]
    # Ammo Max Hold (2026-08-03) — same shape as _active_voodoo_hold_task,
    # separate resource/task so poison/voodoo/ammo effects can all run
    # independently of each other.
    _active_ammo_hold_task: Optional[asyncio.Task]

    def __init__(self, server_address: Optional[str],
                 password: Optional[str]) -> None:
        super().__init__(server_address, password)
        self.save_dir             = _find_save_dir()
        self._slot_quest_states   = {}
        self._slot_questobj_pos_states = {}
        self._slot_mtimes         = {}
        self._sact_mtime          = 0.0
        self.last_soul_count      = 0
        self.current_level        = None
        self._confirmed_in_game   = False  # see TITLE_SCREEN_*_RVA / _poll_live_memory
        self.soul_thresholds      = dict(VANILLA_SOUL_THRESHOLDS)
        self._loc_map             = {}
        self._govi_pos_index      = []
        self._live_quest_states   = {}
        self._live_scan_tick      = 0
        self._live_scan_attempted = False
        self._inventory_loc_map     = {}
        self._live_inventory_states = {}
        self._bookpos_next_index      = None
        self._live_item_pickup_count = None
        self._darksoul_flags_prev    = None
        self._light_soul_ap_id       = None
        self._light_soul_resolved    = False
        self._light_soul_injected_this_session = False
        self._live_soul_count        = None
        self.items_received_index = 0
        self.gad_powers_received  = 0
        self.poigne_ability_received = False
        self._received_items      = []
        self._item_queue          = asyncio.Queue()
        # Connection timestamp, used by the Secret Trap connect-time gate
        # below (2026-08-01) -- set in on_package's "Connected" handler.
        self._connected_at: Optional[float] = None
        # Out-of-band idx counter for /siminject and /simbacklog
        # (2026-08-02) -- starts far above any real AP item index so
        # synthetic test injections can never collide with a real one.
        # Deliberately NOT threaded through items_received_index (that's
        # real AP progress tracking, only ever advanced by the real
        # _item_inject_loop) -- sim calls go straight through
        # _inject_item(), which never touches that counter itself.
        self._sim_idx_next = 9_000_000
        self.death_link_enabled   = False
        self._last_health         = None
        self._ignore_next_death   = False
        self.death_link_threshold = 1
        self._own_death_count     = 0
        self._ap_id_to_level      = {}
        self._ap_id_to_loc_key    = {}
        self._ut_in_logic_regions = set()
        self._ut_in_logic_locations = set()
        self.piston_combos_on     = False
        self.unique_retractor_keys_on = False
        self._last_gad_resync_level = None
        self.trap_bonus_mode     = "always_temporary"
        self.trap_bonus_duration = 60
        self.trap_bonus_secrets_enabled = True
        self.trap_bonus_health_enabled  = True
        self.trap_bonus_voodoo_enabled  = True
        self.trap_bonus_ammo_enabled    = True
        self._active_secret_trap      = None
        self._active_secret_trap_task = None
        self._secret_trap_debounce_task = None
        # Lazily created the first time the in-game connect/console panel
        # (overlay_dll, 2026-08-04) sends a command — reused after that,
        # same pattern CommonClient's own console_loop uses for its single
        # persistent ClientCommandProcessor instance (it's stateless
        # besides holding ctx, see ClientCommandProcessor.__init__).
        self._overlay_command_processor = None
        self._last_known_poller_snapshot = None
        self._secrets_reset_done = False
        self._active_health_effect_task = None
        self._active_health_effect_kind = None
        self._active_voodoo_hold_task = None
        self._active_ammo_hold_task = None
        # Voodoo/Ammo Max Hold per-tick logging (2026-08-23, Jon's request):
        # off by default -- the "tick N/M: ok — cap=..." lines these two
        # holds emit every HEALTH_EFFECT_TICK_INTERVAL_SECONDS were cluttering
        # the log during normal play (Trap/Bonus's permanent holds can run
        # for a long time). Session-only toggle via /debug, same pattern as
        # every other runtime-only flag here (e.g. trap_bonus_*_enabled
        # above) -- no persistent config file exists in this client, and
        # this wasn't judged worth adding one just for a log-verbosity knob.
        # Resets to False every launch.
        self.debug_hold_logging = False

        # Universal Tracker region/location callbacks (2026-07-25) — see
        # docs/client-integration.md's "Adding In-Logic Callbacks" section.
        # set_region_callback/set_callback only exist on TrackerGameContext,
        # so this is a no-op (tracker_loaded stays False, tab shows its
        # fallback) when UT isn't installed.
        if tracker_loaded:
            self.set_region_callback(self._on_ut_regions_updated)
            self.set_callback(self._on_ut_locations_updated)

        if self.save_dir:
            logger.info(f"[ShadowMan] Save directory: {self.save_dir}")
        else:
            logger.warning(
                "[ShadowMan] Save directory not found. "
                "Use /savedir <path> to set it manually."
            )

    # ── AP callbacks ──────────────────────────────────────────────────────────

    def _player_label(self, slot: int) -> str:
        """
        "PlayerName (GameName)" for overlay toasts — a bare player name
        doesn't tell you anything about a stranger in a multiworld, and
        that's exactly the situation these toasts are for.  Falls back to
        just the player name if slot_info isn't populated yet for some
        reason (shouldn't normally happen once Connected has been handled).
        """
        name = (
            self.player_names.get(slot, f"Player {slot}")
            if self.player_names else f"Player {slot}")
        game = self.slot_info[slot].game if slot in self.slot_info else None
        return f"{name} ({game})" if game else name

    def on_print_json(self, args: dict) -> None:
        """
        Override to catch ItemSend broadcasts, which is the only place the
        real item + recipient for a location WE just checked is actually
        known — _send_location_checks*() only has the location id at the
        moment it fires, not what was in it. The server sends this to
        every client after every check resolves, so no LocationScouts
        request needed.

        Only toast when we're the SENDER (net_item.player == self.slot)
        and it's actually going to someone else (receiving != sender) —
        the "found their own item" case and the "someone sent us an item"
        case are both already covered by the item_received toast fired
        from _item_inject_loop via the normal ReceivedItems flow.
        """
        try:
            if args.get("type") == "ItemSend":
                net_item = args.get("item")
                receiving = args.get("receiving")
                if (net_item is not None and receiving is not None
                        and net_item.player == self.slot
                        and receiving != net_item.player):
                    # The item's name must be resolved against the RECEIVING
                    # player's game, not ours — it belongs to whatever game
                    # bropacman (etc.) is actually playing, which is very
                    # often not Shadow Man. lookup_in_game() defaults to our
                    # own game and silently falls back to "Unknown item (ID:
                    # ...)" for anything outside it (that's what produced the
                    # huge-number garbage name before this fix).
                    item_name = (
                        self.item_names.lookup_in_slot(net_item.item, receiving)
                        if self.item_names else str(net_item.item))
                    recipient = self._player_label(receiving)
                    _overlay_ipc.item_sent(item_name, recipient)
        except Exception as exc:
            logger.warning(f"[ShadowMan] Overlay item_sent toast failed: {exc}")

        # Friendly-name log line for ItemSend broadcasts on OUR locations
        # (2026-08-03, Jon: "its becoming a significant issue that we cant
        # easily resolve the friendly name to the hex offset"). The default
        # console text CommonClient prints below (super().on_print_json)
        # renders a location_id JSONMessagePart via self.location_names --
        # the NETWORK DATAPACKAGE, cached once per server connection. That
        # datapackage's location names come from whatever Location.name
        # this world's server-side generation registered for that AP id at
        # generation time, which is NOT guaranteed to be a friendly name in
        # every historical seed/case, and even when it is, there's no easy
        # way to go the OTHER direction (a hex offset from data/locations.csv
        # -> which log lines it should produce) without cross-referencing
        # extracted_locations.py/locations.py by hand. This block always
        # tries to resolve net_item.location against OUR OWN location table
        # (self._ap_id_to_loc_key, built fresh from this session's slot_data
        # -- never stale) and logs the friendly name + technical loc_key
        # together, regardless of sender/receiver (self-found included,
        # unlike the toast-only gate above) -- so every ItemSend involving a
        # Shadow Man location is self-documenting in the log without needing
        # a manual CSV lookup. Silently no-ops for locations outside our
        # world (loc_key lookup misses) or if slot_data hasn't populated the
        # map yet.
        try:
            if args.get("type") == "ItemSend":
                net_item = args.get("item")
                if net_item is not None:
                    loc_key = self._ap_id_to_loc_key.get(net_item.location)
                    if loc_key is not None:
                        friendly = FRIENDLY_NAMES.get(loc_key, loc_key)
                        item_name = (
                            self.item_names.lookup_in_slot(net_item.item, net_item.player)
                            if self.item_names else str(net_item.item))
                        finder = self._player_label(net_item.player)
                        logger.info(
                            f"[ShadowMan] Location resolved: {friendly} "
                            f"({loc_key}, ap_id={net_item.location}) -- "
                            f"{item_name} found by {finder}")
        except Exception as exc:
            logger.warning(f"[ShadowMan] Friendly-name location log failed: {exc}")

        super().on_print_json(args)

    async def server_auth(self, password_requested: bool = False) -> None:
        if password_requested and not self.password:
            await super().server_auth(password_requested)
        await self.get_username()
        await self.send_connect()

    def handle_connection_loss(self, msg: str) -> None:
        """CommonContext's own single hook for every connect-attempt
        failure (refused, invalid URI, timeout, OSError) and every
        already-connected lost-connection case alike — see
        CommonClient.py's server_loop, which calls this from each of its
        except branches. Surfacing it to the overlay panel (2026-08-05)
        means a failed Connect click in-game shows a clear red reason
        instead of silently doing nothing."""
        super().handle_connection_loss(msg)
        _overlay_ipc.connect_failed(msg)

    async def connection_closed(self) -> None:
        """Fires unconditionally whenever the connection ends, regardless
        of why (clean /disconnect, lost connection, server-side close) —
        CommonClient.py's server_loop calls this from its own finally
        block. Lets the overlay panel flip back out of "Connected" state
        (button label, read-only fields) the same way regardless of which
        path caused the disconnect."""
        await super().connection_closed()
        _overlay_ipc.disconnected("Disconnected from Archipelago")

    def on_package(self, cmd: str, args: dict) -> None:
        # Required for UT: lets TrackerGameContext see "Connected"/"RoomInfo"
        # etc. and build its own tracker_core off this world's slot_data.
        super().on_package(cmd, args)

        if cmd == "Connected":
            _overlay_ipc.connected("Connected to Archipelago")
            # Absolute timestamp, not reset on subsequent Connected packets
            # within the same process lifetime the way a queue-state flag
            # would be — see SECRET_TRAP_MIN_SECONDS_SINCE_CONNECT / the
            # "trap_bonus" branch in _inject_item for why this replaced
            # the earlier queue-empty-based gate.
            self._connected_at = time.monotonic()
            slot_data = args.get("slot_data", {})
            self._build_location_map(slot_data)
            self.death_link_enabled = bool(slot_data.get("death_link", False))
            self.death_link_threshold = int(slot_data.get("death_link_threshold", 1)) or 1
            self._own_death_count = 0
            # __init__.py's fill_slot_data() reports "on"/"off" (see
            # options.py) — used by the "Shadow Man" tab's Go Mode
            # checklist to know whether Jacks Schematic is part of the
            # actual completion requirement this seed (access_rules.py's
            # pistons(require_schematic=...)).
            # BUG FIX (2026-07-28, Jon's report, confirmed via the
            # diagnostic log added earlier this session —
            # "slot_data['piston_combos']=None -> piston_combos_on=False"
            # even with piston_combos: true at generation time and a fresh
            # reconnect). Root cause was on the WORLD side, not here:
            # fill_slot_data() never actually wrote a "piston_combos" key
            # into its returned dict at all — the only "piston_combos"
            # write in __init__.py lived in generate_output()'s unrelated
            # local-patcher config (the "on"/"off" string embedded in the
            # .apshadowman file for ap_patcher.py, not the live slot_data
            # this client receives). Fixed fill_slot_data() to actually
            # include it, as a plain bool (that config dict's "on"/"off"
            # convention has no bearing here — see its own comment for why
            # IT needs a string; this is a normal AP slot_data flag like
            # death_link right next to it). Updated the read here to match:
            # plain bool(...) instead of the string comparison that was
            # only ever going to see a real key once the write-side bug
            # was fixed anyway.
            self.piston_combos_on = bool(slot_data.get("piston_combos", False))
            logger.info(
                f"[ShadowMan] slot_data['piston_combos']={slot_data.get('piston_combos')!r} "
                f"-> piston_combos_on={self.piston_combos_on}"
            )
            # Unique Retractor Keys (2026-08-18) — same plain-bool slot_data
            # pattern as piston_combos_on just above (see fill_slot_data()'s
            # matching write in __init__.py).
            self.unique_retractor_keys_on = bool(slot_data.get("unique_retractor_keys", False))
            # Trap/Bonus (2026-08-01, renamed + generalized from "Secret
            # Trap" 2026-08-03) — see options.py's TrapBonusMode/
            # TrapBonusDuration/TrapBonus{Secrets,Health,Voodoo,Ammo}Enabled
            # and _apply_trap_bonus() below.
            self.trap_bonus_mode = str(slot_data.get("trap_bonus_mode", "always_temporary"))
            self.trap_bonus_duration = int(slot_data.get("trap_bonus_duration", 60))
            self.trap_bonus_secrets_enabled = bool(slot_data.get("trap_bonus_secrets_enabled", True))
            self.trap_bonus_health_enabled = bool(slot_data.get("trap_bonus_health_enabled", True))
            self.trap_bonus_voodoo_enabled = bool(slot_data.get("trap_bonus_voodoo_enabled", True))
            self.trap_bonus_ammo_enabled = bool(slot_data.get("trap_bonus_ammo_enabled", True))
            _raw_thresholds = slot_data.get("soul_thresholds")
            if _raw_thresholds:
                # NetUtils/JSON round-trips dict keys as strings.
                self.soul_thresholds = {int(k): int(v) for k, v in _raw_thresholds.items()}
                if self.soul_thresholds != VANILLA_SOUL_THRESHOLDS:
                    logger.info(f"[ShadowMan] Soul thresholds (non-vanilla this seed): "
                                f"{self.soul_thresholds}")
            asyncio.create_task(self.update_death_link(self.death_link_enabled))
            if self.locations_checked:
                asyncio.create_task(self.send_msgs([{
                    "cmd": "LocationChecks",
                    "locations": list(self.locations_checked),
                }]))

        elif cmd == "ReceivedItems":
            start_idx = args.get("index", 0)
            for idx, item in enumerate(args.get("items", []), start=start_idx):
                if idx >= self.items_received_index:
                    self._item_queue.put_nowait((idx, item))

    # ── Location map ──────────────────────────────────────────────────────────

    def _build_location_map(self, slot_data: dict) -> None:
        """
        Build two lookups from slot_data's location_map:

          _loc_map        (level_id, instance_id) → ap_location_id
                           Used for QuestObject records, which have a
                           reliable instance_id. Entries with
                           instance_id == 0 are skipped — they need the
                           extraction tool to be run first (see
                           tools/extract_instance_ids.py).

          _govi_pos_index  [(level_id, x, y, z, ap_location_id), ...]
                           Used for Govi records instead, since there is no
                           usable instance_id for them (see
                           _match_govi_position_scan). Built from every
                           entry with a known position, regardless of
                           category — the patcher can retype any location
                           slot into a govi in a given seed.

        source_file is stored so future code can distinguish quest.rsc actors
        (kexShadowManQuestObject) from instance.rsc actors (class TBD).
        """
        raw: dict = slot_data.get("location_map", {})
        self._loc_map.clear()
        self._govi_pos_index.clear()
        self._inventory_loc_map.clear()
        self._ap_id_to_level.clear()
        self._ap_id_to_loc_key.clear()
        skipped_zero = 0
        for _name, entry in raw.items():
            lvl  = entry.get("level_id")
            iid  = entry.get("instance_id")
            apid = entry.get("ap_id")
            if lvl and iid and apid:          # iid == 0 is falsy → skipped
                self._loc_map[(lvl, iid)] = apid
            elif iid == 0:
                skipped_zero += 1

            # Per-level completion tracking for the "Shadow Man" GUI tab —
            # every entry with a level_id + ap_id counts, regardless of
            # whether it's instance_id- or position-tracked, since this is
            # just "which level is this AP location physically in", not a
            # live-detection lookup.
            if lvl and apid:
                self._ap_id_to_level[apid] = lvl
                # slot_data's location_map is keyed by loc_key itself (see
                # fill_slot_data() -- "location_map[loc_name] = {...}" where
                # loc_name iterates location_table.items(), and
                # location_table is keyed by loc.loc_key) -- _name here IS
                # that loc_key. Stashed so _locations_for_level() can look
                # names up in FRIENDLY_NAMES directly (2026-07-26) instead
                # of via the network datapackage, which can lag behind this
                # world's current code (see FRIENDLY_NAMES import comment).
                self._ap_id_to_loc_key[apid] = _name

            x, y, z = entry.get("x"), entry.get("y"), entry.get("z")
            if lvl and apid and x is not None and y is not None and z is not None:
                self._govi_pos_index.append((lvl, float(x), float(y), float(z), apid))

        # Inventory-flag watcher map: item_name -> ap_id of the location
        # where fill placed OUR copy of that item this seed (slot_data
        # inventory_flag_locs, added 2026-07-19). Only items with a
        # confirmed ITEM_FLAG_RVAS entry are tracked (see that dict's
        # comment for why the rest aren't/can't be — and why keying by
        # the item's vanilla location was wrong). Older slot_data without
        # inventory_flag_locs simply leaves the watcher inert.
        flag_locs: dict = slot_data.get("inventory_flag_locs", {})
        for item_name, loc_key in flag_locs.items():
            if item_name not in ITEM_FLAG_RVAS:
                continue
            entry = raw.get(loc_key)
            apid  = entry.get("ap_id") if entry else None
            if apid:
                self._inventory_loc_map[item_name] = apid

        # Light Soul: resolved from inventory_flag_locs (flag_locs above),
        # NOT its own vanilla LIGHT_SOUL_LOC_KEY.
        #
        # BUG FIX (2026-08-09, Jon's report): picking up a relocated Light
        # Soul (e.g. placed on a Deadside slot by Fill) credited the
        # VANILLA Fogometers location instead of the check for wherever it
        # was actually placed this seed. Root cause: "Light Soul" IS a
        # member of _UNIQUE_ITEM_RSC_NAMES (items.py), so fill_slot_data()
        # already computes its correct per-seed loc_key into
        # inventory_flag_locs -- the exact same "which loc_key actually
        # holds this unique item this seed" data Baton/Calabash/every
        # other unique item already resolves through via flag_locs above.
        # But the loop that builds self._inventory_loc_map from flag_locs
        # skips any item_name not in ITEM_FLAG_RVAS, and Light Soul has no
        # entry there (it's tracked via the CF_GOT_LIGHTSOUL named flag,
        # not a fixed-RVA inventory byte like every item ITEM_FLAG_RVAS
        # does cover) -- so it fell through to hardcoding
        # LIGHT_SOUL_LOC_KEY, the item's own NATIVE slot, regardless of
        # where it really landed. The world-side export was always
        # correct; only this client-side lookup was reading the wrong
        # key. flag_locs (raw inventory_flag_locs, unfiltered by
        # ITEM_FLAG_RVAS) already has the right loc_key under "Light
        # Soul" -- falls back to LIGHT_SOUL_LOC_KEY only if that key is
        # missing entirely (older slot_data, or Light Soul wasn't placed
        # as a real item this seed at all), matching the previous
        # behavior for that case.
        _light_soul_loc_key = flag_locs.get("Light Soul", LIGHT_SOUL_LOC_KEY)
        light_soul_entry = raw.get(_light_soul_loc_key)
        self._light_soul_ap_id = (
            light_soul_entry.get("ap_id") if light_soul_entry else None)
        if self._light_soul_ap_id is None:
            logger.warning(
                f"[ShadowMan] Light Soul location {_light_soul_loc_key!r} not "
                f"found in slot_data's location_map -- live flag watcher "
                f"will be inert this session.")

        known = len(self._loc_map)
        logger.info(
            f"[ShadowMan] Location map: {known} instance_id-tracked "
            f"locations, {len(self._govi_pos_index)} position-tracked "
            f"(for govi), {len(self._inventory_loc_map)} inventory-flag-"
            f"tracked (of {len(ITEM_FLAG_RVAS)} known flag items)"
        )
        # (2026-07-28, Jon: "not a helpful log message") — instance_id=0
        # locations are covered by the position-tracked path above anyway,
        # and running tools/extract_instance_ids.py isn't something Jon's
        # actually doing per-session, so surfacing this at info level every
        # connect was just noise pointing at a non-action. Kept at debug
        # level instead, in case the raw count is ever useful again.
        if skipped_zero:
            logger.debug(
                f"[ShadowMan] {skipped_zero} location(s) skipped for "
                f"instance_id-based tracking (instance_id=0) — covered by "
                f"position-tracking instead."
            )

    # ── Save-file watcher ─────────────────────────────────────────────────────

    async def _save_watcher_loop(self) -> None:
        while not self.exit_event.is_set():
            try:
                await self._poll_save_folder()
            except Exception as exc:
                logger.warning(f"[ShadowMan] Save watcher error: {exc}")
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll_save_folder(self) -> None:
        save_dir = self.save_dir
        if not save_dir or not save_dir.is_dir():
            return

        # Read SACT soul count
        sact_path  = save_dir / "user_activity.dat"
        sact_mtime = sact_path.stat().st_mtime if sact_path.exists() else 0.0
        sact_soul  = self.last_soul_count
        if sact_mtime != self._sact_mtime and sact_path.exists():
            self._sact_mtime = sact_mtime
            val = _parse_sact_soul_count(sact_path.read_bytes())
            if val is not None:
                sact_soul = val

        # Determine the active slot: whichever save file has the most recent
        # mtime across ALL slots, not "whichever happens to be examined last
        # in a fixed loop order" (the previous behavior — a real bug, since
        # it silently let a stale/untouched slot's data win over the slot
        # the player is actually using if it simply sorted later by index).
        # The client never asks the player which slot they're using — this
        # auto-detection is the sole source of truth, matching how every
        # other part of this client (save dir discovery, process discovery)
        # is fully automatic with no manual configuration required.
        active_slot: Optional[int] = None
        active_mtime = -1.0
        for slot in range(NUM_SAVE_SLOTS):
            sav_path = save_dir / f"save_{slot:02d}.sav"
            if not sav_path.exists():
                continue
            mtime = sav_path.stat().st_mtime
            if mtime > active_mtime:
                active_mtime = mtime
                active_slot = slot

        if active_slot is None:
            return

        # Only proceed if the active slot's file actually changed since our
        # last poll — otherwise there's nothing new to detect this cycle.
        if active_mtime == self._slot_mtimes.get(active_slot, 0.0):
            return
        self._slot_mtimes[active_slot] = active_mtime

        sav_bytes = (save_dir / f"save_{active_slot:02d}.sav").read_bytes()
        level     = _canonical_level_id(_identify_level(sav_bytes))

        # Sanity check (2026-07-19): the "newest mtime wins" active-slot
        # detection above has no other signal to confirm it picked the
        # RIGHT slot — if some other save_NN.sav's mtime updates for any
        # reason (an autosave, a manual save left over from earlier
        # testing in a different level, etc.), this logic would silently
        # start reading THAT slot's data instead, mid-session, with
        # nothing to catch it. Confirmed live: this produced spurious
        # LocationChecks for levels never actually visited this session
        # (salvage/wastland while actually standing in ah1cagew).
        #
        # self.current_level from the LIVE memory read (CURRENT_LEVEL_RVA,
        # kept fresh every tick in _poll_live_memory) is an independent,
        # much more reliable ground truth for "what level is the player
        # actually in right now" than anything derivable from save-file
        # mtimes. If the save file we just picked identifies as a
        # DIFFERENT level than that, this is almost certainly the wrong
        # slot (or a save mid-write) — skip acting on it entirely rather
        # than trusting its Govi/quest-state data or letting it clobber
        # self.current_level with a wrong value.
        if level and self.current_level and level != self.current_level:
            logger.warning(
                f"[ShadowMan] save_{active_slot:02d}.sav identifies as "
                f"level={level!r}, but live memory confirms we're "
                f"currently in {self.current_level!r} — this looks like "
                f"the wrong save slot (or a save mid-write). Skipping "
                f"this poll's save-file check-detection rather than "
                f"acting on mismatched data.")
            return

        new_quest = _parse_quest_states(sav_bytes)

        if level:
            self.current_level = level

        # ── Live-memory availability gate (2026-07-19) ──────────────────────────
        # The AP-item-marker position-scan below is a FALLBACK: the live
        # pickup-log mechanism (BOOKPOS_LOG_BASE_RVA in _poll_live_memory)
        # covers marker pickups whenever the game is hooked. The baseline
        # dict (new_qpos) is still recomputed and cached every poll
        # regardless of this flag, so if live memory later drops out
        # mid-session the fallback won't wrongly treat pre-existing state
        # as "newly opened."
        pm, base = _get_process()
        live_memory_available = pm is not None and base is not None

        # ── Govi (dark soul) save-file position-scan — REMOVED (2026-08-23,
        # Jon's report: "[ShadowMan] Save-file Govi check: 2 govi
        # state-flip(s) resolved to ap_ids=[...]... giving free location
        # checks for locations the user hasn't reached yet", specifically
        # around true-form releases and boss cutscenes).
        #
        # Root cause: this scan keyed newly-opened Govi records by file_pos
        # — the class-name occurrence's raw BYTE OFFSET within the save —
        # not any stable per-object id (Govi records genuinely have none,
        # see GOVI_IID_BACK's comment above). A story beat like a true-form
        # release or boss cutscene can rewrite enough of the save file's
        # earlier content to shift every LATER Govi record's file_pos.
        # Every shifted record then looks like a brand-new 0->1 flip at its
        # new offset (prev_govi has no entry there), even for Govi shells
        # the player opened long ago — and _match_govi_position_scan's own
        # fuzzy position/tolerance matching (there's no exact per-object id
        # to check against) can then resolve that phantom "flip" against
        # ANY nearby AP location within tolerance, not necessarily the real
        # one, or even one the player has ever been near. Exactly the "free
        # checks for unreached locations" Jon reported.
        #
        # This scan was the only path that could resolve a Dark Soul
        # retyped onto a non-soul slot (see _process_darksoul_watch's
        # unresolved_flip nudge, which used to call into this via
        # _poll_save_folder) -- removing it reopens that narrow gap. Traded
        # off deliberately: silently granting wrong checks for unvisited
        # locations is a worse multiworld-integrity problem than one rare
        # pickup type occasionally needing a manual /siminject or a later
        # fix, and every OTHER pickup type (regular items, dark souls in
        # their own slot, quest.rsc progression/weapons/lore below) is
        # fully covered by the live-memory paths + the QuestObject
        # instance_id-keyed check right below, neither of which shares this
        # file_pos-drift flaw. self.last_soul_count still gets updated from
        # SACT below so /status and other soul-count displays stay current
        # -- only the position-scan-based CHECK-SENDING was removed.
        souls_gained = sact_soul - self.last_soul_count
        if souls_gained > 0:
            self.last_soul_count = sact_soul

        # ── QuestObject (quest.rsc items: progression, weapons, lore) ──────────
        prev_quest = self._slot_quest_states.get(active_slot, {})
        self._slot_quest_states[active_slot] = new_quest

        if level:
            newly_collected = [
                iid for iid, st in new_quest.items()
                if st == 1 and prev_quest.get(iid, 0) == 0
            ]
            if newly_collected:
                await self._send_location_checks(level, newly_collected)

        # ── Archipelago-item marker (RSC_X_BOOK_OF_SHADOWS, save_idx=0) ────────
        # FALLBACK ONLY (see live-memory gate above) — the live pickup-log
        # mechanism (BOOKPOS_LOG_BASE_RVA, in _poll_live_memory) already
        # covers exactly this case ("any pickup that happens to route
        # through this same log," including retyped marker locations) with a
        # real per-event log instead of a wide position-scan guess, and
        # without the double-detection noise this block produces alongside
        # it. Locations whose item belongs to another player's world get
        # retyped to a shared marker item (see generate_output in
        # __init__.py) that intentionally shares instance_id=0 across every
        # such location — _loc_map explicitly skips iid==0 (can't be told
        # apart that way). Resolved by position instead, same mechanism as
        # Govi/Dark Soul. Not gated behind souls_gained — these are ordinary
        # key-item pickups, no soul-count semantics involved.
        prev_qpos = self._slot_questobj_pos_states.get(active_slot, {})
        new_qpos  = _parse_questobject_states_by_pos(sav_bytes)
        self._slot_questobj_pos_states[active_slot] = new_qpos

        newly_marker_ap_ids: List[int] = []
        if not live_memory_available:
            for file_pos, (iid, state) in new_qpos.items():
                if iid != 0 or state != 1:
                    continue
                _prev_iid, prev_state = prev_qpos.get(file_pos, (0, 0))
                if prev_state == 1:
                    continue
                # Diagnostic (2026-07-19): _match_govi_position_scan's scan
                # window (GOVI_POS_SCAN_MIN/MAX) was calibrated specifically for
                # kexShadowManAIGovi's own layout — QuestObject is a different
                # class, likely with position at a different offset from its
                # (longer) class-name string. Reusing the Govi window here is a
                # hypothesis, not confirmed; logging every attempt (not just
                # failures) so we can see the hit rate and whether resolved
                # positions look sane, or the window needs its own calibration.
                ap_id, position = _match_govi_position_scan(
                    sav_bytes, file_pos, level, self._govi_pos_index)
                if ap_id is None:
                    logger.warning(
                        f"[ShadowMan] AP-item marker at file_pos={file_pos:#x} "
                        f"(level={level!r}) did NOT resolve to a position within "
                        f"the Govi-calibrated scan window — this class's position "
                        f"offset likely differs from kexShadowManAIGovi's.")
                    continue
                _loc_key = self._ap_id_to_loc_key.get(ap_id)
                logger.info(
                    f"[ShadowMan] AP-item marker at file_pos={file_pos:#x} "
                    f"resolved to ap_id={ap_id} "
                    f"({FRIENDLY_NAMES.get(_loc_key, '?')}, {_loc_key}) "
                    f"at position={position}.")
                newly_marker_ap_ids.append(ap_id)

        if newly_marker_ap_ids:
            logger.info(
                f"[ShadowMan] Save-file AP-item marker check: "
                f"{len(newly_marker_ap_ids)} resolved to "
                f"ap_ids={newly_marker_ap_ids}.")
            await self._send_location_checks_ap_ids(newly_marker_ap_ids)

    # ── Live memory watcher (low-latency Govi + QuestObject detection) ─────────

    async def _process_pickup_log(self, pm, base: int, level: Optional[str]) -> None:
        """
        Read ITEM_PICKUP_COUNTER_RVA and resolve any newly-added pickup log
        entries (0..count-1, permanent/never-overwritten records) against
        self._govi_pos_index for the given `level`.

        Factored out of _poll_live_memory (2026-08-03) so it can also be
        driven by _fast_watcher_loop, a separate loop with a much shorter
        sleep than POLL_INTERVAL's 1.0s. Jon's report: an AP item sitting
        right next to a level-transition door can be picked up and the
        door walked through in under a second — faster than the main
        once-a-second sweep gets back around to noticing the counter moved.
        Since position-matching is filtered by the CURRENT level (see
        _match_live_position's `lvl != level` check), a poll that only
        catches up AFTER the transition would resolve against the WRONG
        (new) level's position index and either mismatch or, more likely,
        miss entirely (nearest-candidate hundreds of units off) — not just
        "detected a beat late", genuinely unresolvable after the fact,
        since nothing about the pickup log entry itself records which
        level it happened in (see BOOKPOS_LOG_BASE_RVA's structure notes).
        A short, cheap, dedicated poll loop shrinks that race window from
        "up to ~1s" (anything within POLL_INTERVAL) down to "up to
        FAST_POLL_INTERVAL" (a fraction of a second), rather than
        needing the entries to self-describe their own level (which the
        game's own record format doesn't provide).

        Concurrency: safe to call this from two independently-scheduled
        loops. Everything here is synchronous Python between `await`
        points except the final _send_location_checks_ap_ids call, and
        self._bookpos_next_index is advanced to `pickup_count` BEFORE that
        await — so if both loops happen to observe the same incremented
        counter, whichever runs first claims the new range synchronously;
        the other sees `pickup_count > self._bookpos_next_index` already
        false (or a smaller remaining range, if a third increment landed
        in between) and does nothing further. No lock needed under
        asyncio's single-threaded cooperative scheduling.
        """
        try:
            pickup_count = pm.read_uint(base + ITEM_PICKUP_COUNTER_RVA)
        except Exception:
            pickup_count = None

        if pickup_count is None:
            return

        if (self._live_item_pickup_count is not None
                and pickup_count != self._live_item_pickup_count):
            logger.info(
                f"[ShadowMan] Item pickup counter changed: "
                f"{self._live_item_pickup_count} -> {pickup_count}.")

        if self._bookpos_next_index is None:
            # First successful read this session — baseline to the CURRENT
            # count rather than 0. Entries below this index may predate
            # this AP session (earlier play this same game launch) and
            # shouldn't be replayed as fresh pickups.
            self._bookpos_next_index = pickup_count
        elif pickup_count > self._bookpos_next_index:
            newly_marker_ap_ids: List[int] = []
            for idx in range(self._bookpos_next_index, pickup_count):
                entry = _read_bookpos_log_entry(pm, base, idx)
                if entry is None:
                    logger.warning(
                        f"[ShadowMan] Pickup log entry {idx} failed to "
                        f"read — skipping.")
                    continue
                px, py, pz, edx_val, r9d_val = entry
                ap_id = _match_live_position(
                    px, py, pz, level, self._govi_pos_index)
                if ap_id is None:
                    # Diagnostic-only nearest-candidate lookup (no
                    # tolerance check) — distinguishes "nowhere near any
                    # known location" (genuinely untracked category, e.g.
                    # cadeaux — see _SKIP_CATS in locations.py, or an
                    # unrelated non-pickup event sharing this log) from
                    # "just outside tolerance" (would point at a real
                    # matching bug).
                    nearest = None
                    for lvl, kx, ky, kz, cand_id in self._govi_pos_index:
                        if lvl != level:
                            continue
                        d_xz = ((px - kx) ** 2 + (pz - kz) ** 2) ** 0.5
                        if nearest is None or d_xz < nearest[0]:
                            nearest = (d_xz, cand_id, ky)
                    # Friendly name + loc_key for the nearest candidate
                    # (2026-08-03) -- a bare ap_id here meant resolving
                    # "which physical CSV row is this" required a manual
                    # cross-reference against extracted_locations.py every
                    # time; self._ap_id_to_loc_key/FRIENDLY_NAMES are
                    # already built fresh from this session's slot_data
                    # (see _build_location_map), so this is a free,
                    # always-current lookup.
                    near_desc = (
                        f"nearest known loc: "
                        f"{FRIENDLY_NAMES.get(self._ap_id_to_loc_key.get(nearest[1]), '?')} "
                        f"({self._ap_id_to_loc_key.get(nearest[1], '?')}, "
                        f"ap_id={nearest[1]}) at d_xz={nearest[0]:.1f}, "
                        f"dy={abs(py - nearest[2]):.1f}"
                        if nearest else "no known locations for this level at all")
                    logger.warning(
                        f"[ShadowMan] Pickup log entry {idx} at "
                        f"({px:.1f},{py:.1f},{pz:.1f}) (level={level!r}, "
                        f"edx={edx_val}, r9d={r9d_val}) did NOT resolve "
                        f"to any known AP location ({near_desc}).")
                else:
                    _loc_key = self._ap_id_to_loc_key.get(ap_id)
                    logger.info(
                        f"[ShadowMan] Pickup log entry {idx} at "
                        f"({px:.1f},{py:.1f},{pz:.1f}) (level={level!r}, "
                        f"edx={edx_val}, r9d={r9d_val}) resolved to "
                        f"ap_id={ap_id} "
                        f"({FRIENDLY_NAMES.get(_loc_key, '?')}, {_loc_key}).")
                    newly_marker_ap_ids.append(ap_id)
            self._bookpos_next_index = pickup_count
            if newly_marker_ap_ids:
                logger.info(
                    f"[ShadowMan] Live pickup log check: "
                    f"ap_ids={newly_marker_ap_ids}.")
                await self._send_location_checks_ap_ids(newly_marker_ap_ids)

        self._live_item_pickup_count = pickup_count

    async def _process_darksoul_watch(self, pm, base, level: Optional[str]) -> None:
        """
        Read the dark soul collected-flag array (DARKSOUL_FLAGARRAY_PTR_RVA
        — array index == save_idx directly, no heap scan needed) and
        SOUL_COUNT_RVA (increments on every dark soul collection, including
        ones the flag array can't resolve — see below), resolving any
        newly-flipped save_idx against self._loc_map and nudging a save-file
        re-poll for anything the flag array alone can't identify.

        Factored out of _poll_live_memory (2026-08-23, Jon: "the counters
        go up right away on getting [a dark soul] so figured we could have
        the AP location sweep check at the same time") so it can also be
        driven by _fast_watcher_loop, the same shorter-interval loop
        _process_pickup_log uses — see that method's docstring for the
        general reasoning (level read fresh at detection time, safe to call
        from two loops concurrently with no lock). Previously this block
        only ran on the once-a-second _memory_watcher_loop cadence, so a
        dark soul flip could sit unnoticed for up to ~1s even though the
        in-game soul counter itself updates instantly — same class of gap
        FAST_POLL_INTERVAL already closed for regular AP-item pickups.

        Baseline state (self._darksoul_flags_prev / self._live_soul_count)
        is advanced BEFORE the awaits below, not after — mirrors
        _process_pickup_log's self._bookpos_next_index ordering, so that if
        both loops happen to observe the same flip, whichever runs first
        claims it synchronously and the other sees nothing new by the time
        it gets to compare. Any residual double-detection either loop
        ordering can't fully rule out is harmless either way —
        _send_location_checks_ap_ids dedupes against self.locations_checked
        (see _memory_watcher_loop's docstring for the same "redundant
        confirmation is harmless" reasoning applied to the save-file path).
        """
        # ── Dark soul collected-flag array — fully resolved live via direct
        # byte-array diff, no heap scan needed. See
        # DARKSOUL_FLAGARRAY_PTR_RVA's comment block above for the
        # Ghidra-confirmed structure: array index == save_idx directly.
        darksoul_flags = _read_darksoul_flagarray(pm, base)
        if darksoul_flags is not None:
            if self._darksoul_flags_prev is None:
                # First successful read this session — baseline only, same
                # reasoning as the pickup log above (souls collected earlier
                # this game launch, before we connected, shouldn't replay).
                self._darksoul_flags_prev = darksoul_flags
            else:
                prev = self._darksoul_flags_prev
                if len(darksoul_flags) != len(prev):
                    # Length changing would be very unexpected (this isn't
                    # supposed to be level-scoped — see comment above) —
                    # log it and just rebaseline rather than guessing.
                    logger.warning(
                        f"[ShadowMan] Dark soul flag array length changed "
                        f"({len(prev)} -> {len(darksoul_flags)}) — "
                        f"resyncing baseline without resolving.")
                    self._darksoul_flags_prev = darksoul_flags
                else:
                    newly_soul_ap_ids: List[int] = []
                    unresolved_flip = False
                    for save_idx in range(len(darksoul_flags)):
                        if darksoul_flags[save_idx] and not prev[save_idx]:
                            ap_id = self._loc_map.get((level, save_idx))
                            if ap_id is None:
                                # Expected for a Dark Soul retyped onto a
                                # non-soul slot: the flag index is the
                                # placed object's reward id (the target
                                # slot's save_idx, often 0), not anything
                                # resolvable here. The save-file Govi
                                # position scan USED TO be the fallback
                                # resolver for these — removed 2026-08-23
                                # (Jon: it was misfiring, granting free
                                # checks for unreached locations; see
                                # _poll_save_folder's comment). There is no
                                # resolver for this case anymore — this is
                                # now just a warning, and the case needs a
                                # manual /siminject or a future fix. The
                                # _poll_save_folder() nudge below is kept
                                # (harmless, still re-polls the still-live
                                # QuestObject path) but no longer resolves
                                # retyped souls specifically.
                                unresolved_flip = True
                                logger.warning(
                                    f"[ShadowMan] Dark soul flag save_idx="
                                    f"{save_idx} flipped but no AP location "
                                    f"found for (level={level!r}, "
                                    f"save_idx={save_idx}) — this Dark Soul "
                                    f"was likely retyped onto a non-soul "
                                    f"slot; no automatic resolver for that "
                                    f"case exists anymore (see comment "
                                    f"above).")
                            else:
                                _loc_key = self._ap_id_to_loc_key.get(ap_id)
                                logger.info(
                                    f"[ShadowMan] Dark soul flag save_idx="
                                    f"{save_idx} (level={level!r}) resolved "
                                    f"to ap_id={ap_id} "
                                    f"({FRIENDLY_NAMES.get(_loc_key, '?')}, "
                                    f"{_loc_key}).")
                                newly_soul_ap_ids.append(ap_id)
                    # Advance the baseline BEFORE awaiting — see docstring's
                    # concurrency note.
                    self._darksoul_flags_prev = darksoul_flags
                    if newly_soul_ap_ids:
                        logger.info(
                            f"[ShadowMan] Live dark soul flag check: "
                            f"ap_ids={newly_soul_ap_ids}.")
                        await self._send_location_checks_ap_ids(newly_soul_ap_ids)
                    if unresolved_flip:
                        # Same idea as the QuestObject transition nudge:
                        # only helps if the game has already written the
                        # save, but cuts the wait to the next natural poll
                        # when it has.
                        try:
                            await self._poll_save_folder()
                        except Exception as exc:
                            logger.warning(
                                f"[ShadowMan] Forced save re-poll after "
                                f"unresolved dark soul flag flip failed: "
                                f"{exc}")

        # ── Live soul-counter watcher (2026-07-19) ───────────────────────────
        # SOUL_COUNT_RVA increments on EVERY dark soul collection — including
        # the ones the flag array above can't even SEE: a soul retyped onto a
        # non-soul slot carries reward id = the slot's save_idx (usually 0),
        # and flag[0] only transitions once — every later reward-0 soul
        # produces no flip at all, so the unresolved-flip nudge above never
        # fires for them. The counter catches every one. It can't say WHICH
        # soul (that's why the flag array exists), so this is a nudge, not a
        # check: it just forces a save re-poll in case that helps the
        # still-live QuestObject path pick something up. The save-file Govi
        # position scan that used to resolve retyped souls specifically was
        # removed 2026-08-23 (misfiring, free checks for unreached
        # locations — see _poll_save_folder's comment); there is currently
        # no automatic resolver for a soul retyped onto a non-soul slot.
        # Received Dark Souls also bump this counter (inject_dark_soul); the
        # nudge is a harmless near-no-op then (mtime-gated). First read
        # baselines, same as every other live watcher here.
        try:
            soul_count = pm.read_int(base + SOUL_COUNT_RVA)
        except Exception:
            soul_count = None
        if soul_count is not None:
            prev_soul_count = self._live_soul_count
            # Advance BEFORE awaiting — see docstring's concurrency note.
            self._live_soul_count = soul_count
            if prev_soul_count is not None and soul_count > prev_soul_count:
                logger.info(
                    f"[ShadowMan] Live soul counter: {prev_soul_count} -> "
                    f"{soul_count} — nudging save re-poll.")
                try:
                    await self._poll_save_folder()
                except Exception as exc:
                    logger.warning(
                        f"[ShadowMan] Forced save re-poll after soul "
                        f"counter increment failed: {exc}")

    async def _fast_watcher_loop(self) -> None:
        """
        Dedicated fast poll for the handful of live signals cheap enough to
        check every FAST_POLL_INTERVAL instead of waiting for the normal
        once-a-second sweep. Runs alongside _memory_watcher_loop, not
        instead of it — that loop still does everything else (govi
        save-state, gad/poigne resync, secret poller forensics, etc.) at
        the normal POLL_INTERVAL cadence; there's no urgency for any of
        that.

        Originally pickup-counter-only (2026-08-03, Jon: "for AP items
        that are close to level transitions, we can lose detecting an AP
        pickup if we switch levels before a poll... can we still have the
        pickup counter incrementing trigger an instant poll check").
        Extended 2026-08-23 (Jon: "the counters go up right away on
        getting [a dark soul] so figured we could have the AP location
        sweep check at the same time") to also cover the dark soul flag
        array + live soul counter, and again the same day (Jon: "i think
        the light soul needs that instant poll check too") to cover
        CF_GOT_LIGHTSOUL — all three are direct fixed-size memory reads
        (no heap scan, no file I/O; read_named_flag is two plain reads,
        no code injection — see its own docstring), so checking them at
        this cadence instead of POLL_INTERVAL's 1.0s costs nothing extra
        per tick. _poll_live_light_soul in particular is a near-total
        no-op once resolved (self._light_soul_ap_id is None or
        self._light_soul_resolved short-circuits before any read), so it
        stays cheap even polled this often for the entire rest of a
        session after the one real check.

        Each tick does one shared cheap read (current level) plus these
        signals' own cheap reads, and calls the exact same
        _process_pickup_log / _process_darksoul_watch / _poll_live_light_soul
        methods used by the main sweep — see those methods' docstrings for
        why level must be read fresh at detection time rather than reused
        from the main loop's own (potentially stale-by-now) snapshot, and
        for why calling them from two loops concurrently is safe with no
        lock (_poll_live_light_soul needs no such argument -- it's
        idempotent past the first True read via _light_soul_resolved,
        same one-shot latch either loop can set).
        """
        while not self.exit_event.is_set():
            try:
                pm, base = _get_process()
                if pm is not None and base is not None:
                    live_level = _read_current_level_live(pm, base)
                    level = live_level or self.current_level
                    await self._process_pickup_log(pm, base, level)
                    await self._process_darksoul_watch(pm, base, level)
                    await self._poll_live_light_soul(pm, base)
            except Exception as exc:
                logger.warning(f"[ShadowMan] Fast watcher error: {exc}")
            await asyncio.sleep(FAST_POLL_INTERVAL)

    async def _memory_watcher_loop(self) -> None:
        """
        Live process-memory poll loop — runs alongside (not instead of)
        _save_watcher_loop. Standard AP client architecture: a periodic
        memory-reading watcher, same shape as SNIClient/BizHawkClient's
        game_watcher-on-an-interval and Archipelago.Core's Memory-based
        clients. See the DARKSOUL_VTABLE_RVA / QUEST_THINKFN_RVA comment
        block above for the full derivation and what each path can/can't
        resolve on its own.

        Deliberately left running even if the save-file path also detects
        the same check later — _send_location_checks_ap_ids and
        _send_location_checks both dedupe against self.locations_checked,
        so a slower, redundant save-file confirmation is harmless. This
        also means live-memory access failing (game not running yet,
        permissions, a future game patch moving these offsets) degrades
        gracefully back to the pre-existing save-file-only behavior rather
        than breaking detection entirely.
        """
        while not self.exit_event.is_set():
            try:
                pm, base = _get_process()
                if pm is not None and base is not None:
                    await self._poll_live_memory(pm, base)
            except Exception as exc:
                logger.warning(f"[ShadowMan] Memory watcher error: {exc}")
            await asyncio.sleep(POLL_INTERVAL)

    async def _poll_live_memory(self, pm, base: int) -> None:
        do_full_scan = (self._live_scan_tick % MEMORY_FULL_SCAN_EVERY_N_POLLS) == 0
        self._live_scan_tick += 1

        # Poller-state forensics (2026-08-01) — isolated in its own
        # try/except so a read failure here can never break the rest of
        # this poll cycle. See _poll_secret_poller_state's own docstring.
        try:
            self._poll_secret_poller_state(pm, base)
        except Exception as exc:
            logger.warning(f"[ShadowMan] Poller-state watcher error: {exc}")

        # In-game connect/console panel (2026-08-04) — isolated the same
        # way, so a bad line from the DLL can never break the rest of this
        # poll cycle. Deliberately keyed off the same "pm/base found"
        # gating this whole method already has, not a separate loop: the
        # overlay DLL only exists once the game process is running and
        # injected into, so there's never a reason to check for panel
        # commands before that's true anyway.
        try:
            self._poll_overlay_panel_commands()
        except Exception as exc:
            logger.warning(f"[ShadowMan] Overlay panel command watcher error: {exc}")

        # Title/attract-screen transition tracking — separate question from
        # current_level below (see TITLE_SCREEN_*_RVA comment block for why
        # a live level number can't be used for this: the menu's attract
        # demo drives real level-loading code). _confirmed_in_game gates
        # _inject_item's real memory writes (re-read fresh there, this
        # cached copy is only used to decide when to auto-fire a replay).
        # Fires the replay the moment we leave the title screen, and
        # re-arms on the way back to it — covers both "never loaded a save
        # yet" and "quit an in-progress game back to the menu mid-session",
        # which a current_level-only signal couldn't (current_level is
        # intentionally never cleared — see _read_current_level_live).
        title_state = _read_is_at_title_screen(pm, base)
        if title_state is False and not self._confirmed_in_game:
            self._confirmed_in_game = True
            logger.info(
                "[ShadowMan] Confirmed in-game (left the title screen) — "
                "auto-applying any items received while at the menu.")
            asyncio.create_task(self._replay_all_received_items())
            if not self._secrets_reset_done:
                self._secrets_reset_done = True
                self._reset_all_secrets_off(pm, base)
        elif title_state is True and self._confirmed_in_game:
            self._confirmed_in_game = False
            logger.info(
                "[ShadowMan] Back at the title screen — gating item "
                "injection again until you're back in a level.")

        # Live level read takes priority over the save-file value when
        # known (see CURRENT_LEVEL_RVA) — keeps level-scoped matching
        # accurate between saves instead of lagging behind a stale value.
        live_level = _read_current_level_live(pm, base)
        if live_level:
            self.current_level = live_level
        level = self.current_level

        # Gad power / Poigne re-assert on level (re-)entry (2026-07-25 —
        # user report: gad marcher showed as unlocked but lava still
        # killed them; fixed by warping, i.e. a fresh level entry, without
        # reconnecting or re-receiving the item). Neither inject_gad_power
        # nor inject_poigne_ability gates its write on pause/menu/title-
        # screen state (only the title-screen check above does), so
        # either received while already standing in a level almost
        # certainly writes its flag(s) immediately and correctly — the
        # likely culprit is the lava/hazard damage check only reading the
        # relevant flag once, when the level's own actors initialize, not
        # every frame. Re-writing both independently every time a level is
        # (re-)entered guarantees that level's hazards always spawn
        # already seeing the correct flags, regardless of when the AP
        # item(s) actually arrived — and regardless of each other, since
        # the two are no longer the same counter (see that bug fix).
        # Compares against the STRING level value, so a same-level warp
        # (no level_id change) would in theory not retrigger this — DISPROVEN
        # by Jon's own testing (2026-07-25): a same-level warp still
        # retriggered the resync, so this hasn't needed an actual
        # level-load hook so far.
        #
        # apply_now=False on BOTH calls below (2026-08-02, confirmed crash —
        # see CLAUDE.md's "level-entry resync sweep calling _apply_gad_level_now
        # unconditionally" writeup). This block had been calling
        # inject_gad_power()/inject_poigne_ability() with their default
        # apply_now=True on EVERY level transition for any player holding any
        # gad ability — the one call site in this whole file that was never
        # brought in line with the 2026-08-01 apply_now gating everyone else
        # (the "gad"/"poigne_ability" branches in _inject_item and
        # _replay_all_received_items) already got, since this sweep isn't
        # itself a per-item injection branch and was missed in that pass.
        # That meant _apply_gad_level_now()'s still-EXPERIMENTAL/unverified
        # CreateRemoteThread call into FUN_140459d50 fired on every single
        # level load, unconditionally — a real, confirmed crash: Jon received
        # Gad Power + Poigne (both already showing correct, non-stale values
        # in the live pause menu — this was not the stale-counter regression
        # fixed earlier the same day), transitioned into deadside, both
        # "Re-asserted ... on level entry" lines logged, and the game crashed
        # immediately after.
        #
        # The flag bytes (GAD_1..4_RVA, GAD_LEVEL_RVA) are ALWAYS correct in
        # live memory by this point regardless of any level transition —
        # inject_gad_power/inject_poigne_ability already wrote them the
        # instant the item was received, and nothing about a level transition
        # can undo a plain memory write. Per this same block's own original
        # 2026-07-25 writeup, the actual problem that motivated this sweep
        # was never "the flags are wrong at level entry" — it's that a
        # level's hazard actors only read the flag ONCE, at that level's own
        # native init time. Which means a completely ordinary level
        # transition is exactly the case the vanilla engine already handles
        # correctly on its own: its own native level-load code reads
        # GAD_*_RVA/GAD_LEVEL_RVA fresh as part of initializing the new
        # level's actors, the same as it would for a player who legitimately
        # earned those abilities via normal vanilla progression. There is no
        # gap here for _apply_gad_level_now to fill — the mid-level case
        # (received while already standing in a level, no transition, no
        # native init pass to piggyback on) is the ONLY scenario that
        # function was ever needed for, and this sweep only runs on
        # transitions. Calling an unverified CreateRemoteThread function at
        # the exact moment the engine is already doing its own native
        # "apply gad state to this level" work is a plausible direct
        # explanation for the crash, not just a coincidence of timing.
        #
        # The self-heal/flag-rewrite/logging below is left in place — it's
        # cheap, it's all plain memory reads/writes with zero execution
        # risk, and it keeps self.gad_powers_received accurate for this
        # session's own logging even though the rewritten byte values
        # themselves are expected to already be correct no-ops. Only the
        # live-apply call is removed from this path.
        #
        # Gated on self._connected_at is not None (2026-08-02, Jon's report:
        # "opening client.py it seems to set my gad level to 1 by default,
        # even before connecting to archipelago"). _memory_watcher_loop (and
        # therefore this whole _poll_live_memory sweep) starts unconditionally
        # at client launch, well before the user has entered a server address
        # or connected to any AP room (see main()'s asyncio.create_task calls
        # for server_loop/_save_watcher_loop/_memory_watcher_loop/etc, all
        # started together with no connection gate). That means this block
        # could previously fire on the very first level detected after just
        # launching the game — before self.gad_powers_received has any
        # legitimate relationship to AP progress at all (nothing has been
        # received yet this session; it's still its __init__ default of 0).
        # If _live_gad_temple_tier's read of GAD_1..3_RVA ever caught a
        # not-yet-reliable value at that early a point (e.g. read before the
        # relevant player struct is fully initialized post-title-screen), the
        # self-heal below would trust it, and inject_gad_power's own write
        # would cement that reading into the live game as a real, persistent
        # GAD_1_RVA=1 — turning a possible bad read into actual incorrect
        # game state, with no AP item ever involved. There is no legitimate
        # reason for this sweep to touch gad/poigne state before an AP
        # session actually exists, so it's simplest and safest to just not
        # run it at all until then, rather than trying to further validate
        # the read's reliability. self._connected_at is set exactly once, in
        # on_package's "Connected" handler (the same flag already used for
        # the "gad"/"poigne_ability"/"soul" apply_now elapsed-time gates), so
        # this reuses an existing, well-understood signal rather than adding
        # a new one.
        if (level and level != self._last_gad_resync_level
                and self._connected_at is not None):
            # Absolute recompute from _received_count("Gad Power")
            # (2026-08-02, replacing the live-memory self-heal that used to
            # live here — Jon's suggestion: "set gad to 0... then
            # re-apply" rather than delta-applying against whatever's live).
            # The live-memory version (`max(self.gad_powers_received,
            # _live_gad_temple_tier(pm, base))`) was the exact call site
            # that produced an earlier confirmed crash (a stale
            # self.gad_powers_received regressing an already-set GAD_2_RVA
            # flag — since fixed with a floor inside inject_gad_power
            # itself), AND was independently implicated in a second,
            # separate bug the same day (a not-yet-reliable live read this
            # early getting cemented into real game state before AP was
            # even connected — see that writeup). Recomputing from
            # _received_count("Gad Power") instead sidesteps both classes
            # of problem at once: it's authoritative (AP's own
            # items_received list, not an effect of our own past writes or
            # anything else that might be poking this address) and it's 0
            # whenever nothing has actually been received yet, regardless
            # of what live memory happens to contain. inject_gad_power()
            # keeps its own internal "NEVER WRITES BACKWARD" floor against
            # live memory as defense in depth regardless.
            self.gad_powers_received = min(self._received_count("Gad Power"), 3)
            if self.gad_powers_received > 0:
                if inject_gad_power(pm, base, self.gad_powers_received, apply_now=False):
                    logger.info(
                        f"[ShadowMan] Re-asserted Gad Temple count "
                        f"{self.gad_powers_received} on level entry ({level}).")
            if self.poigne_ability_received:
                if inject_poigne_ability(pm, base, apply_now=False):
                    logger.info(
                        f"[ShadowMan] Re-asserted Poigne ability on level "
                        f"entry ({level}).")
            else:
                # Clear stale leftover Poigne state (2026-08-02, Jon: "it
                # looks like it applied on 1st level i was in, then removed
                # after it realized?"). Root cause: GAD_4_RVA is ONLY ever
                # WRITTEN by inject_poigne_ability, and that function always
                # writes 1 -- nothing in this file ever clears it back to 0.
                # Earlier the same day, extensive /siminject Poigne testing
                # (see CLAUDE.md's "Confirmed live 2026-08-02... temple count
                # climbing 2 -> 3 and Poigne active throughout") genuinely
                # wrote GAD_4_RVA=1 into this same running game process. A
                # later, unrelated /inject replay (0 real Poigne items in
                # self._received_items, confirmed by grepping that replay's
                # own log for any "Applying item #N 'Poigne'" line -- there
                # is none) left self.poigne_ability_received correctly False
                # the whole time, so the existing "if received: re-assert"
                # branch above correctly never fired -- but the STALE byte
                # from the earlier test was still sitting in live memory
                # from before, unrelated to anything this replay did, and
                # nothing ever corrected it. _resync_gad_level's own
                # poigne_set read is a direct, honest read of GAD_4_RVA, so
                # a stale 1 there correctly (if confusingly) displayed as
                # "Poigne active" the first level entry -- Jon's own
                # apparent self-correction ("removed after it realized") is
                # most likely the vanilla game's own native level-load logic
                # resyncing ability state from the save file (which never
                # had Poigne) on a later, genuine transition, not anything
                # in this client.
                #
                # Fixed by making this sweep authoritative in BOTH
                # directions now that self.poigne_ability_received is
                # trustworthy (only ever True from a real "poigne_ability"
                # item — see _inject_item/_replay_all_received_items): if
                # AP's own record says Poigne was never received, actively
                # clear a stray live 1 rather than silently leaving it be.
                # Gated on the same elapsed-since-connect grace period as
                # the "gad"/"poigne_ability" apply_now decisions so this
                # can't race a genuine backlog replay that just hasn't
                # reached its real Poigne item yet (self.poigne_ability_
                # received would still be False at that instant even though
                # a real grant is legitimately in flight).
                poigne_elapsed = (
                    time.monotonic() - self._connected_at
                    if self._connected_at is not None else 0.0)
                if poigne_elapsed >= SECRET_TRAP_MIN_SECONDS_SINCE_CONNECT:
                    try:
                        if pm.read_uchar(base + GAD_4_RVA):
                            pm.write_bytes(base + GAD_4_RVA, bytes([0]), 1)
                            _resync_gad_level(pm, base)
                            logger.info(
                                f"[ShadowMan] Cleared stale Poigne flag on "
                                f"level entry ({level}) — AP records show "
                                f"Poigne was never received this session.")
                    except Exception as exc:
                        logger.warning(
                            f"[ShadowMan] Stale Poigne clear failed: {exc}")
            self._last_gad_resync_level = level

        # ── Pickup event log (Book of Shadows / AP-item markers) — fully
        # resolved live via direct indexed reads, no heap scan and no
        # diffing/guessing needed. See BOOKPOS_LOG_BASE_RVA's comment block
        # above for the Ghidra-confirmed structure: ITEM_PICKUP_COUNTER_RVA
        # is the authoritative count of valid entries (0..count-1), each a
        # permanent, never-overwritten record.
        #
        # Extracted into _process_pickup_log() (2026-08-03, Jon: "for AP
        # items close to level transitions, we can lose detecting a pickup
        # if we switch levels before a poll") -- also called from
        # _fast_watcher_loop, a separate, much shorter-interval loop, so a
        # pickup right next to a door gets resolved (using the level read
        # at that same instant, not whichever level this once-a-second
        # sweep happens to catch) long before a level transition can race
        # ahead of it. See that method's docstring for the full reasoning
        # and the concurrency argument for why calling this from two loops
        # is safe.
        await self._process_pickup_log(pm, base, level)

        # ── Dark soul collected-flag array + live soul counter — fully
        # resolved live, no heap scan needed. See _process_darksoul_watch's
        # own docstring: factored out (2026-08-23) so it can also be driven
        # by _fast_watcher_loop at FAST_POLL_INTERVAL, same idea as
        # _process_pickup_log above.
        await self._process_darksoul_watch(pm, base, level)

        # Heap walk for QuestObject only now — Govi/AIGovi signatures were
        # removed 2026-07-19 (see DARKSOUL_FLAGARRAY_PTR_RVA above; the
        # heap-scan Govi path never found anything in practice all
        # session and is fully superseded by the direct flag-array read).
        # Runs in a worker thread: the walk blocks for seconds on large
        # heaps, and running it inline starved the Kivy UI loop (frozen/
        # laggy client, cascading hover/Builder exceptions).
        #
        # need_scan used to also trigger on "not self._live_soul_states" —
        # removed 2026-07-19 for the same reason the signature itself was
        # removed: that condition was true on almost EVERY tick forever
        # (never found anything to populate the cache with), silently
        # turning the "every 10 polls" cadence into "every poll," each one
        # taking multiple seconds on a large heap and inflating the real
        # interval between ALL live signals read this tick — confirmed
        # live via a ~5s gap between the pickup counter changing and the
        # resulting check being sent. _live_scan_attempted now forces
        # exactly one initial scan (to populate state right after
        # connecting) and then defers to the normal do_full_scan cadence.
        need_scan = do_full_scan or not self._live_scan_attempted
        all_hits = None
        if need_scan:
            quest_sig  = struct.pack("<Q", base + QUEST_THINKFN_RVA)
            all_hits   = await asyncio.get_running_loop().run_in_executor(
                None, _scan_memory_for_signatures, pm, [quest_sig])
            self._live_scan_attempted = True

        # ── QuestObject items — live trigger only; save file resolves slot ──
        if all_hits is not None:
            quest_objs = _scan_quest_objects(pm, base, hits=all_hits[quest_sig])
            # Summary log removed (2026-07-20) — the full per-object dump
            # served its purpose finding QuestObject's layout and locating
            # item_obj addresses in Cheat Engine; now that the pickup event
            # log (BOOKPOS_LOG_BASE_RVA) resolves Book of Shadows/AP-marker
            # positions directly, this scan is back to its original job of
            # just being a cheap "something changed" trigger for the
            # save-file re-poll below. It still runs every ~10 polls, so
            # logging it (even a summary) spammed the console with
            # identical lines for no remaining diagnostic value.
        else:
            quest_objs = []
            for item_obj in list(self._live_quest_states.keys()):
                try:
                    item_id = pm.read_uint(item_obj + QUEST_ITEMID_LIVE_OFF)
                    state   = pm.read_uint(item_obj + QUEST_STATE_LIVE_OFF)
                    quest_objs.append((item_obj, item_id, state))
                except Exception:
                    continue

        seen_quest_keys = set()
        quest_transition_seen = False
        for item_obj, item_id, state in quest_objs:
            seen_quest_keys.add(item_obj)
            if state != self._live_quest_states.get(item_obj, 0):
                quest_transition_seen = True
            self._live_quest_states[item_obj] = state
        for stale in set(self._live_quest_states) - seen_quest_keys:
            del self._live_quest_states[stale]

        if quest_transition_seen:
            # Nudge an immediate save-file re-check rather than waiting for
            # the next natural POLL_INTERVAL tick. This only helps if the
            # game has already written the save by the time we get here —
            # it does not eliminate the save-write dependency the way the
            # Govi path does (see QUEST_THINKFN_RVA comment block above for
            # why a full live resolution isn't available for QuestObject).
            try:
                await self._poll_save_folder()
            except Exception as exc:
                logger.warning(
                    f"[ShadowMan] Forced save re-poll after live quest "
                    f"trigger failed: {exc}")

        # ── Inventory-array items (Baton, Calabash, etc.) — fully resolved
        # live, no save file or heap-scan needed. See ITEM_FLAG_RVAS.
        await self._poll_live_inventory(pm, base)

        # ── Light Soul possession flag — fully resolved live, no save file
        # or heap-scan needed. See LIGHT_SOUL_FLAG_RVA. Also driven by
        # _fast_watcher_loop at FAST_POLL_INTERVAL (2026-08-23, Jon: "i
        # think the light soul needs that instant poll check too") — safe
        # to call from both loops, see _fast_watcher_loop's docstring.
        await self._poll_live_light_soul(pm, base)

    async def _poll_live_inventory(self, pm, base: int) -> None:
        """
        Poll the fixed-offset per-item possession flags for the one-of-a-kind
        items in ITEM_FLAG_RVAS, and attribute a 0 -> nonzero flip to the
        location where THIS seed physically placed the item (slot_data
        inventory_flag_locs) — NOT the item's vanilla location. The old
        vanilla-location attribution sent false checks under shuffle (see
        ITEM_FLAG_RVAS comment block for the two confirmed live cases).

        Unlike the Govi/QuestObject paths above, these are static
        singleton-object fields (kexShadowManInventoryLocal, see
        INVENTORY_RVA) — no heap scan needed, just a direct read per known
        address every poll. Cheap enough to do unconditionally rather than
        gating behind MEMORY_FULL_SCAN_EVERY_N_POLLS the way the
        heap-scanned paths are.

        Transition rules (see _live_inventory_states attr docs):
        - prev is None (first successful read): baseline only, never fires —
          a flag already set at connect time (prior session progress) must
          not produce a check.
        - fires only on a confirmed 0 -> nonzero read pair. Injection paths
          pre-mark entries to 1, so received-item injection (which calls
          the same GiveItem as a native pickup) can't fire either.
        """
        if not self._inventory_loc_map:
            return
        newly_collected_ap_ids: List[int] = []
        for item_name, apid in self._inventory_loc_map.items():
            rvas = ITEM_FLAG_RVAS.get(item_name)
            if not rvas:
                continue
            try:
                value = max(pm.read_uint(base + rva) for rva in rvas)
            except Exception:
                continue
            prev = self._live_inventory_states.get(item_name)
            if value != 0 and prev == 0:
                newly_collected_ap_ids.append(apid)
            self._live_inventory_states[item_name] = value

        if newly_collected_ap_ids:
            await self._send_location_checks_ap_ids(newly_collected_ap_ids)

    async def _poll_live_light_soul(self, pm, base: int) -> None:
        """
        Poll the CF_GOT_LIGHTSOUL named completion flag (LIGHT_SOUL_CF_INDEX)
        via the live GetFlag(index) virtual call (read_named_flag) and report
        a check the first time it reads True.

        REPLACES the old LIGHT_SOUL_FLAG_RVA byte + debounce approach
        (2026-07-25/26) -- see that constant's comment for the full
        derivation of why it was abandoned (shared HUD/meter display state,
        not a possession flag; noise held the byte at 1 for multiple
        consecutive polls too, defeating any duration-based debounce).
        CF_GOT_LIGHTSOUL was found via Ghidra string search + a live
        dumpsaveflags console-command test with Jon (2026-07-27): a
        genuinely dedicated, non-aliased, persistent flag -- confirmed 0
        before a real pickup, 1 immediately after, held at 1 afterward.

        CORRECTION (2026-08-06): this docstring used to claim "RSC_X_
        LIGHT_SOUL has no self-inject path of its own, so a genuine True
        read only ever happens from a real local pickup" -- that was
        wrong, and the wrongness is exactly the bug Jon found (receiving
        a "Light Soul" item from a completely DIFFERENT game in the
        multiworld also credited this location, despite never visiting
        it). inject_light_soul() DOES exist and IS called for every
        received "Light Soul" item, self-found or foreign (see
        ITEM_INJECTION_METHODS' "light_soul" entry) -- and its shellcode
        (_build_light_soul_shellcode) replicates the vanilla givelightsoul
        debug handler's three internal calls verbatim, the third of which
        is (*obj->vtable[1])(obj, 0xE2) where obj is the SAME flag-manager
        singleton CF_FLAG_OBJ_RVA uses and 0xE2 == 226 ==
        LIGHT_SOUL_CF_INDEX -- i.e. that call IS SetFlag(CF_GOT_LIGHTSOUL).
        Granting the ability and setting this exact flag are NOT separable
        at the engine level; inject_light_soul() cannot do one without the
        other. See _light_soul_injected_this_session's own docstring for
        the fix and its known limitation.
        """
        if self._light_soul_ap_id is None or self._light_soul_resolved:
            return
        value = read_named_flag(pm, base, LIGHT_SOUL_CF_INDEX)
        if value is None:
            return  # read/injection failed this poll -- try again next tick
        if value:
            self._light_soul_resolved = True
            _loc_key = self._ap_id_to_loc_key.get(self._light_soul_ap_id)
            if self._light_soul_injected_this_session:
                # 2026-08-06 fix -- see _light_soul_injected_this_session's
                # docstring. We ourselves have called inject_light_soul()
                # at some point this session (for a received "Light Soul"
                # item, self-found or foreign), which unavoidably also
                # sets this exact flag as a side effect of granting the
                # ability -- so a True read here can no longer be trusted
                # as proof of a genuine physical Fogometers visit. Marked
                # resolved (stop polling/re-checking every tick) but
                # DELIBERATELY not credited -- sending an unverifiable
                # check would repeat the exact bug this fix closes.
                logger.warning(
                    f"[ShadowMan] CF_GOT_LIGHTSOUL flag read True, but we "
                    f"ourselves already called inject_light_soul() this "
                    f"session (for a received Light Soul item, self-found "
                    f"or from another game) -- this flag can no longer be "
                    f"trusted to mean a genuine physical Fogometers visit, "
                    f"so ap_id={self._light_soul_ap_id} "
                    f"({FRIENDLY_NAMES.get(_loc_key, '?')}, {_loc_key}) is "
                    f"NOT being auto-credited. If you HAVE physically "
                    f"visited it, this location may need to be credited "
                    f"another way.")
                return
            logger.info(
                f"[ShadowMan] CF_GOT_LIGHTSOUL flag confirmed set — resolved "
                f"to ap_id={self._light_soul_ap_id} "
                f"({FRIENDLY_NAMES.get(_loc_key, '?')}, {_loc_key}).")
            await self._send_location_checks_ap_ids([self._light_soul_ap_id])

    # ── Goal (CLIENT_GOAL) watcher ────────────────────────────────────────────

    async def _goal_watcher_loop(self) -> None:
        """
        Poll the live game process for Legion's defeat (see FLAG_MANAGER_RVA /
        check_legion_defeated) and report ClientStatus.CLIENT_GOAL once.

        `finished_game` is CommonContext's standard flag for "goal already
        reported" — checked so we don't spam StatusUpdate every poll and so
        it lines up with how other AP clients track completion.
        """
        while not self.exit_event.is_set():
            try:
                if not self.finished_game:
                    pm, base = _get_process()
                    if pm is not None:
                        defeated = check_legion_defeated(pm, base)
                        if defeated:
                            self.finished_game = True
                            await self.send_msgs([{
                                "cmd": "StatusUpdate",
                                "status": ClientStatus.CLIENT_GOAL,
                            }])
                            logger.info(
                                "[ShadowMan] Legion defeated — CLIENT_GOAL sent."
                            )
            except Exception as exc:
                logger.warning(f"[ShadowMan] Goal watcher error: {exc}")
            await asyncio.sleep(POLL_INTERVAL)

    # ── DeathLink ──────────────────────────────────────────────────────────────

    def on_deathlink(self, data: dict) -> None:
        """
        Dispatched automatically by CommonContext when another linked player
        dies. Kills the local player in response. Runs as a background task
        that keeps re-asserting the kill until it's confirmed (or times out)
        — handles the case where the local game is unfocused/paused at the
        moment the DeathLink arrives; see inject_death_until_confirmed().
        """
        super().on_deathlink(data)
        pm, base = _get_process()
        if pm is None:
            logger.warning("[ShadowMan] DeathLink received but game not running.")
            return
        # Set before injecting so the health watcher's next poll doesn't see
        # this self-inflicted zero and broadcast it back out as our own death.
        self._ignore_next_death = True

        source = data.get("source", "a teammate")
        inject_console_print(pm, base, f"DeathLink: {source} died")

        async def _kill_until_confirmed() -> None:
            confirmed = await inject_death_until_confirmed(pm, base)
            logger.info(
                f"[ShadowMan] DeathLink received — "
                f"{'local player killed (confirmed)' if confirmed else 'kill sent but not confirmed within timeout'}."
            )

        asyncio.create_task(_kill_until_confirmed())

    async def _health_watcher_loop(self) -> None:
        """
        Poll current health (see CURRENT_HEALTH_RVA) and detect the moment
        it drops from >0 to 0 — i.e. only on the transition, not on every
        poll while already at 0.

        Two independent things happen on that transition:
          1. DeathLink send — still fully gated on death_link_enabled (set
             from slot_data on Connected), unchanged from before.
          2. Health Effect cancellation (2026-08-05, unconditional,
             regardless of death_link_enabled) — any currently-running
             poison/heal effect (_active_health_effect_task) is cancelled.
             A poison-caused death is already handled by
             _run_health_effect's own inject_death() branch (which ends
             its loop immediately rather than waiting for this watcher's
             next poll), but this is the only place that catches every
             OTHER cause of death — an enemy, fall damage, an incoming
             DeathLink kill, etc. — any of which can land mid-effect and,
             without this, would leave poison/heal still ticking against a
             freshly-respawned health pool it was never meant to apply to.

        As of 2026-08-05, health is now always polled/tracked (previously
        this whole loop no-opped unless death_link_enabled, meaning
        Health Effects had no death-cancellation at all when DeathLink was
        off) — (2) needs this regardless of whether DeathLink itself is
        enabled for this seed.

        death_link_threshold (2026-07-24, options.py's DeathLinkThreshold):
        only every Nth of OUR OWN deaths actually sends. _own_death_count
        tracks how many of our deaths have happened since the last one we
        sent; it only increments for deaths that would otherwise have been
        sent (i.e. never for _ignore_next_death deaths — those are our own
        reaction to an incoming Death Link, not a new death of ours, and
        must never count toward or trigger our own outgoing threshold).
        Threshold of 1 (default) sends every time, identical to the old
        unconditional behavior.

        Gated on self._confirmed_in_game (2026-08-09, fix for a reported
        false DeathLink trigger on game close/open): this loop polls
        independently of _poll_live_memory (the one that actually maintains
        _confirmed_in_game), so it used to only check "pm is not None" —
        true for a chunk of both process startup (before the player struct
        is meaningfully initialized, at the title/attract screen) and
        process teardown (the handle can stay valid for a beat after the
        game has started tearing down its own memory). Either window can
        make read_current_health briefly return a transient/garbage 0 (or
        a transient nonzero-then-0 pair) with nothing to do with a real
        death — exactly the same class of "must be confirmed in-game, not
        just process-running" gap _inject_item/_read_is_at_title_screen's
        callers were already written to close elsewhere in this file.
        _last_health is reset to None while not confirmed in-game so a
        stale or garbage reading from that window can never be compared
        against once back in a real level, and the transition only starts
        being tracked fresh from the first confirmed-in-game poll onward.
        """
        while not self.exit_event.is_set():
            try:
                pm, base = _get_process()
                if pm is not None and self._confirmed_in_game:
                    health = read_current_health(pm, base)
                    if health is not None:
                        if (self._last_health is not None
                                and self._last_health > 0 and health <= 0):
                            if self._active_health_effect_task is not None:
                                logger.info(
                                    f"[ShadowMan] Health effect ({self._active_health_effect_kind}) "
                                    f"cancelled — player died.")
                                self._active_health_effect_task.cancel()
                                self._active_health_effect_task = None
                                self._active_health_effect_kind = None
                            if self.death_link_enabled:
                                if self._ignore_next_death:
                                    self._ignore_next_death = False
                                else:
                                    self._own_death_count += 1
                                    if self._own_death_count >= self.death_link_threshold:
                                        self._own_death_count = 0
                                        inject_console_print(pm, base, "DeathLink: sent to your team")
                                        await self.send_death("Shadow Man died")
                                    else:
                                        remaining = self.death_link_threshold - self._own_death_count
                                        inject_console_print(
                                            pm, base,
                                            f"DeathLink: death {self._own_death_count}/"
                                            f"{self.death_link_threshold} ({remaining} more until sent)")
                        self._last_health = health
                else:
                    self._last_health = None
            except Exception as exc:
                logger.warning(f"[ShadowMan] Health watcher error: {exc}")
            await asyncio.sleep(POLL_INTERVAL)

    async def _send_location_checks(self, level_id: str,
                                     instance_ids: List[int]) -> None:
        # _parse_quest_states (the caller's source of instance_ids) scans
        # EVERY kexShadowManQuestObject record in the save, not just the ones
        # locations.csv extracted as item pickups -- the same class is also
        # used for plain story/trigger/NPC-state flags that were never part
        # of the AP location audit (confirmed 2026-07-25: swampday's own
        # save_idx range in locations.csv only runs 0-32, but iids like
        # 92/100/131/155/178 show up here routinely -- those aren't dropped
        # AP checks, they're unrelated non-pickup quest flags flipping during
        # normal play). A miss here is expected/benign, not a sign of a
        # broken placement -- log it quietly instead of warning on every save.
        ap_ids: List[int] = []
        for iid in instance_ids:
            ap_id = self._loc_map.get((level_id, iid))
            if ap_id is None:
                logger.debug(
                    f"[ShadowMan] No AP location for {level_id} iid={iid} "
                    f"(likely a non-pickup quest flag, not an error)")
                continue
            if ap_id not in self.locations_checked:
                ap_ids.append(ap_id)
                self.locations_checked.add(ap_id)
        if ap_ids and self.server_task:
            logger.info(f"[ShadowMan] LocationChecks: {ap_ids}")
            await self.send_msgs([{"cmd": "LocationChecks", "locations": ap_ids}])

    async def _send_location_checks_ap_ids(self, ap_ids: List[int]) -> None:
        """
        Same as _send_location_checks, but for callers (Govi/position
        matching) that have already resolved ap_ids directly instead of
        going through the (level_id, instance_id) → ap_id lookup.
        """
        new_ids = [a for a in ap_ids if a not in self.locations_checked]
        for a in new_ids:
            self.locations_checked.add(a)
        if new_ids and self.server_task:
            logger.info(f"[ShadowMan] LocationChecks: {new_ids}")
            await self.send_msgs([{"cmd": "LocationChecks", "locations": new_ids}])

    # ── Item injection loop ────────────────────────────────────────────────────

    async def _item_inject_loop(self) -> None:
        """Process received AP items and inject them into the live game."""
        while not self.exit_event.is_set():
            try:
                idx, item = await asyncio.wait_for(
                    self._item_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            item_name: str = (
                self.item_names.lookup_in_game(item.item)
                if self.item_names else str(item.item)
            )
            sender: str = self._player_label(item.player)

            logger.info(f"[ShadowMan] Received: {item_name} from {sender}")
            self._log_received_item(item_name, sender)
            _overlay_ipc.item_received(item_name, sender)
            self._received_items.append((idx, item_name, item.player))

            # Peek at what's next in the queue, purely for crash forensics
            # (2026-08-01, Jon's suggestion): doesn't consume anything, just
            # previews so that if the game crashes between finishing THIS
            # item and ever logging the next one, the log already shows
            # what was about to be applied — not just what just finished.
            # asyncio.Queue has no public peek; reads its internal deque
            # directly (stable across CPython versions, guarded below in
            # case that internal ever changes) rather than adding a whole
            # second tracking structure just for a log line.
            remaining = self._item_queue.qsize()
            if remaining:
                try:
                    next_idx, next_item = self._item_queue._queue[0]
                    next_name = (
                        self.item_names.lookup_in_game(next_item.item)
                        if self.item_names else str(next_item.item)
                    )
                    logger.info(
                        f"[ShadowMan]     ({remaining} more queued — next "
                        f"up: #{next_idx} '{next_name}')"
                    )
                except Exception:
                    logger.info(f"[ShadowMan]     ({remaining} more queued)")

            pm, base = _get_process()
            if pm is not None:
                inject_console_print(pm, base, f"AP: {item_name} from {sender}")

            await self._inject_item(item_name, idx, item.player)

            # Small pacing gap after EVERY item, not just Secret Trap
            # (2026-08-01, see CLAUDE.md). Several item types routed
            # through _inject_item — "give_item" and "cadeaux" among them
            # — call inject_give_item(), which is kexShadowManInventoryLocal
            # ::GiveItem() executed via CreateRemoteThread — the same risk
            # class as Secret Trap's on-change callback, just a different
            # function, and one this investigation had not previously
            # looked at. A large connect-time backlog can easily contain
            # several such items, previously fired back-to-back with only
            # a bare `asyncio.sleep(0)` cooperative yield between them —
            # essentially as fast as Python can loop, a load pattern
            # normal solo play (one item every so often) never exercises.
            # Confirmed live: the crash persisted with Secret Trap fully
            # excluded from the backlog (same RIP every time), which rules
            # out Secret Trap specifically and points at this loop's
            # OTHER engine-executing calls instead. ITEM_INJECT_PACING_SECONDS
            # gives the game a real moment to settle between successive
            # remote-thread executions; comfortably imperceptible spread
            # across a backlog of items received while disconnected
            # anyway.
            await asyncio.sleep(ITEM_INJECT_PACING_SECONDS)

            # Always advance the index, even if injection failed
            self.items_received_index = max(self.items_received_index, idx + 1)

            # Force Universal Tracker to recompute reachability right away
            # (2026-07-26). Confirmed via tracker.apworld's own TrackerClient.py:
            # on_package() only calls self.updateTracker() on RoomUpdate,
            # Connected, SetReply/Retrieved, and LocationInfo — there is no
            # ReceivedItems branch at all. So UT's own state/prog_items (which
            # _ut_in_logic_regions/_ut_in_logic_locations mirror via the
            # set_region_callback/set_callback hooks) doesn't refresh when a
            # new item arrives; it only catches up the next time something
            # else happens to trigger a RoomUpdate (typically a location
            # check). Confirmed live: Jon received his 5th Retractor with no
            # location check immediately after, and the "Proximity to Go
            # Mode" panel's Engine Block region reachability (which reads
            # _ut_in_logic_regions, fully UT-dependent) stayed stuck showing
            # 0/5 / "not yet reachable" even though _go_mode_prerequisites()
            # (reads items_received directly, UT-independent) correctly
            # showed Retractors 5/5. Explicitly nudging UT here closes that
            # gap regardless of what other packet traffic happens to occur.
            if tracker_loaded:
                try:
                    self.updateTracker()
                except Exception as exc:
                    logger.warning(
                        f"[ShadowMan] updateTracker() after item receive "
                        f"failed: {exc}")

    def _received_count(self, item_name: str) -> int:
        """Count of `item_name` entries across the full items_received
        list (self-found + foreign combined) — the standard AP list,
        always populated regardless of our own injection pipeline. Used
        by the "soul"/"cadeaux" set_dark_soul_count/set_cadeaux_count
        callers (2026-07-26 fix) to recompute an absolute, idempotent
        target rather than incrementally adding per event."""
        if not self.item_names:
            return 0
        return sum(1 for ni in self.items_received
                    if self.item_names.lookup_in_game(ni.item) == item_name)

    def _received_cadeaux_total(self) -> int:
        """Sum of physical-cadeaux VALUE across every received item in the
        "Cadeaux" family — plain "Cadeaux" (worth 1) and every
        "Cadeaux Bundle x{N}" denomination (worth N), per
        items.py's cadeaux_bundle_item_name()/cadeaux_item_weight().

        Cadeaux Bundle Size (2026-07-27): superset of _received_count("Cadeaux")
        for the bundle_size=1 case (every item is plain "Cadeaux", weight 1
        each, identical result) — this is the generalized replacement used by
        set_cadeaux_count's two call sites so a bundle received from ANY
        world (including one whose cadeaux_bundle_size differs from ours)
        grants its full encoded weight instead of being flattened to 1 per
        item. Same idempotent-recompute pattern as _received_count itself:
        always recomputed fresh from the full items_received list, never
        incremented, so replays/reconnects stay safe."""
        if not self.item_names:
            return 0
        total = 0
        for ni in self.items_received:
            name = self.item_names.lookup_in_game(ni.item)
            if is_cadeaux_item(name):
                total += cadeaux_item_weight(name)
        return total

    async def _inject_item(self, item_name: str, idx: int, source_player: int) -> None:
        """
        Route a received item to the correct injection function.

        source_player is item.player from the NetworkItem — the slot whose
        LOCATION this item came from. When source_player == self.slot, we
        found this item in our OWN Shadow Man world, meaning the vanilla
        game already applied its native side effect at the moment of
        physical collection (see the "soul" branch below for why this
        matters specifically for Dark Souls).
        """
        method_info = AP_ITEM_INJECTION.get(item_name)
        if method_info is None and is_cadeaux_item(item_name):
            # Cadeaux Bundle Size (2026-07-27): AP_ITEM_INJECTION is a
            # literal-name lookup and only has a "Cadeaux" key — every
            # concrete "Cadeaux Bundle x{N}" name (see items.py's
            # cadeaux_bundle_item_name()) would otherwise miss this dict
            # entirely and fall through to the "no injection mapping"
            # warning below, silently dropping the whole grant. Route the
            # entire Cadeaux family through the same "cadeaux" method/flag
            # arg as plain "Cadeaux" — the actual physical amount granted
            # is computed separately via _received_cadeaux_total(), not
            # from this arg, so no per-denomination arg is needed here.
            method_info = AP_ITEM_INJECTION["Cadeaux"]
        if method_info is None:
            logger.warning(f"[ShadowMan] No injection mapping for '{item_name}'")
            return

        method, arg = method_info

        # Self-found items (source_player == self.slot): we found this item
        # at a location in our OWN world, so the vanilla game's own pickup
        # handler already applied its native effect at the moment of
        # physical collection. Confirmed for "soul" via FUN_1402dfb90
        # (SOUL_COUNT_RVA unconditionally incremented on pickup — see
        # DARKSOUL_FLAGARRAY_PTR_RVA's comment block). The same applies to
        # "give_item"/"pickup_sys": this file's own INJECTION METHODS notes
        # (top of file) describe these as shellcode calls to the exact same
        # vtable/GiveItem entry point the game's native pickup handler
        # already calls — our injection replicates it rather than being a
        # separate AP-only effect. Re-injecting on top double-grants:
        # harmless for a one-time boolean flag, but a real duplicate for
        # stackable counters (Cadeaux, Accumulator, Retractor). Confirmed
        # live (2026-07-19): a self-found RSC_X_POIGNE double-called
        # GiveItem(0x13).
        #
        # NOT applied to "gad": count is cumulative across ALL real Gad
        # Temple items received this seed (self-found + cross-player);
        # skipping the count for a self-found one would undercount every
        # later injection's tier. Whether the vanilla Gad altar's own
        # native effect independently sets the correct order-independent
        # tier isn't confirmed, so left untouched rather than guessed.
        #
        # NOT applied to "poigne_ability": same reasoning — whether
        # vanilla's native Poigne pickup correctly sets GAD_4_RVA on its
        # own isn't confirmed either (the only confirmed native-vs-
        # injection gap found so far was specifically the GiveItem(0x13)
        # shellcode path — see AP_ITEM_INJECTION's comment), and
        # re-injecting is harmless either way (idempotent boolean flag),
        # so left unconditional rather than guessed.
        #
        # NOT applied to "light_soul": RSC_X_LIGHT_SOUL is a pure AP filler
        # item with no vanilla giveinv id / no native pickup path of its own.
        #
        # NOT applied to "cadeaux" (Cadeaux Bundle Size fix, 2026-07-27+):
        # this used to be a flat/correct assumption when every cadeaux was
        # worth exactly 1 -- vanilla's own native pickup handler already
        # bumps CADEAUX_COUNT_RVA by 1 on physical collection, matching a
        # weight-1 "Cadeaux" item exactly. But a self-found BUNDLE
        # representative worth N (e.g. "Cadeaux Bundle x5") still only ever
        # gets vanilla's flat "+1" from the native handler -- the engine has
        # no concept of bundle weight. Confirmed live (Jon's report): the
        # HUD only ticked up by 1 on pickup, and only corrected to the full
        # 5 after an unrelated save/reload path recomputed it from scratch.
        # Safe to always run this branch regardless of self-found status:
        # GiveItem(0x05) only flips the separate one-time "ever collected"
        # boolean and never touches CADEAUX_COUNT_RVA (see that RVA's
        # comment block above), and set_cadeaux_count() always WRITES an
        # absolute, freshly-recomputed target rather than incrementing (see
        # its own docstring) -- so re-running this for an already-correct
        # weight-1 pickup is a harmless idempotent no-op, while a bundle
        # pickup now gets corrected to its real weight immediately instead
        # of only on the next incidental save/reload.
        # NOT applied to "trap_bonus": like "light_soul", this is a pure
        # AP-only synthetic effect with no vanilla pickup path of its own
        # (see items.py's Trap/Bonus comment) — a self-found copy still
        # needs the actual trap/bonus effect triggered, nothing native does it.
        # Unique Retractor Keys (2026-08-19, Jon's report: physically
        # picking up "Retractor - Prison" in-game left ALL FIVE
        # CF_CUSTOM00-04 flags at 0) — a self-found retractor key used to
        # fall straight into the blanket self-found skip just below and
        # return before EVER reaching the CF_CUSTOM flag logic in the
        # "give_item" branch further down. That meant the only thing that
        # could ever set the flag for a self-found key was Hook B
        # (unique_retractor_keys_patch.py's native pickup-side splice,
        # separate repo) — if Hook B doesn't fire for any reason (Jon's own
        # suspicion: something about the pickup animation/dispatch path it
        # splices into; possibly instant_pickup_patch-adjacent, not fully
        # root-caused), the schism stayed permanently locked with nothing
        # to ever catch it, since a self-found item's own ReceivedItems
        # echo — the only other event that revisits this location — was
        # unconditionally short-circuited before reaching the flag check,
        # and /catchup's replay path had the identical shortcut (see
        # its own matching fix). Give the native GiveItem() call itself the
        # same self-found skip as before (still correct — vanilla's own
        # pickup already ran it; re-injecting would double-count the
        # stackable retractor total, see the comment block above), but
        # ALWAYS run the same flag check the remote-grant path uses
        # (write_named_flag()'s own docstring), backstopping if it's not
        # already set — this is the client-side self-injection Jon asked
        # for specifically for the save flag, not the item count,
        # independent of whatever Hook B did or didn't do.
        if source_player == self.slot and item_name in RETRACTOR_KEY_ITEM_TO_CF_INDEX:
            cf_index = RETRACTOR_KEY_ITEM_TO_CF_INDEX[item_name]
            pm, base = _get_process()
            if pm is not None and base is not None:
                if read_named_flag(pm, base, cf_index):
                    logger.info(
                        f"[ShadowMan] {item_name} was self-found — CF_CUSTOM "
                        f"flag already set (Hook B fired normally).")
                elif write_named_flag(pm, base, cf_index):
                    logger.info(
                        f"[ShadowMan] {item_name} was self-found but its "
                        f"CF_CUSTOM flag wasn't set natively (Hook B "
                        f"apparently didn't fire) — set it directly instead; "
                        f"schism now unlockable.")
                else:
                    logger.warning(
                        f"[ShadowMan] {item_name} self-found but its "
                        f"CF_CUSTOM flag could not be confirmed or repaired "
                        f"— its schism may stay locked until a future "
                        f"reconnect/backlog replay retries this.")
            else:
                logger.info(
                    f"[ShadowMan] {item_name} self-found but game not "
                    f"running — flag check deferred to a future "
                    f"reconnect/backlog replay (/catchup).")
            return

        if source_player == self.slot and method not in ("gad", "poigne_ability", "light_soul", "cadeaux", "trap_bonus"):
            logger.info(
                f"[ShadowMan] {item_name} was self-found (already applied "
                f"natively on physical pickup) — skipping duplicate injection."
            )
            return

        pm, base = _get_process()

        if pm is None:
            logger.info(
                f"[ShadowMan] Game not running — '{item_name}' queued in log. "
                f"Start the game and use /catchup to re-apply."
            )
            return

        # Gate real memory injection on being in an active game session,
        # not just "the exe is running". Confirmed live (2026-07-21):
        # items received while connected at the main menu got injected
        # straight into player/inventory structures that don't exist yet,
        # and starting a New Game afterward crashed. current_level was
        # tried first as the signal but rejected (also 2026-07-21, before
        # any code shipped): the main menu's background demo drives real
        # level-loading code for its attract-mode gameplay, so a live
        # level number can't tell "real player session" apart from "menu
        # demo currently showing level N". _read_is_at_title_screen()
        # reads the dedicated menu-state flags instead (see their RVA
        # comment block) — read FRESH here rather than trusting a cached
        # poll value, so a stale flag can never let an injection through.
        # True (at title) and None (ambiguous/read failure) both fail
        # closed — only a confirmed False (in game) proceeds.
        if _read_is_at_title_screen(pm, base) is not False:
            logger.info(
                f"[ShadowMan] Not confirmed in-game yet (at the title "
                f"screen, or state unclear) — '{item_name}' queued. Will "
                f"auto-apply once you're in a level, or use /catchup.")
            return

        # Pre-mark the possession-flag watcher BEFORE injecting: injection
        # calls the same GiveItem the native pickup handler uses, so the
        # flag flip would otherwise look like a local pickup and false-check
        # wherever OUR copy of this item is placed (if it's placed locally
        # at all). Ordering matters — mark first, then run the shellcode,
        # so the watcher poll can never land in between.
        if item_name in ITEM_FLAG_RVAS:
            self._live_inventory_states[item_name] = 1

        # Crash-forensics marker (2026-08-01): logged + flushed to disk
        # immediately before any injection runs, so if the GAME crashes a
        # moment later, this line is guaranteed to be the last (or the
        # last-but-one, if a later item's own marker also made it out)
        # thing in the client log — telling you exactly which item was
        # mid-injection at crash time instead of having to guess from a
        # burst of "Received:" lines with no clear cutoff. See
        # _flush_log_handlers()'s docstring for why the explicit flush is
        # needed here specifically (the client and game are separate
        # processes, but a buffered log handler could still lose this line
        # before it hits disk).
        logger.info(
            f"[ShadowMan] >>> Applying item #{idx} '{item_name}' "
            f"(method={method}, arg={arg}) — if the game crashes now, "
            f"THIS is the item that was being applied."
        )
        _flush_log_handlers()

        if method == "soul":
            # Set to an absolute target recomputed from the full
            # items_received history, not an increment (2026-07-26 fix —
            # see set_dark_soul_count's docstring). Idempotent regardless
            # of how many times this fires or what order items arrive in.
            #
            # apply_now gated the same way as "gad"/"poigne_ability" below
            # (2026-08-02, Jon's request) — _sync_soul_level's fallback
            # branch is a CreateRemoteThread virtual call, same risk class
            # as _apply_gad_level_now, and was previously firing completely
            # ungated during connect-time backlog replay even after that
            # exact gap was closed for gad/poigne. set_dark_soul_count
            # itself is a plain write and always runs regardless.
            soul_elapsed = (time.monotonic() - self._connected_at
                             if self._connected_at is not None else 0.0)
            ok = set_dark_soul_count(pm, base, self._received_count("Dark Soul"))
            if ok:
                logger.info("[ShadowMan] Dark Soul count synced.")
                _sync_soul_level(
                    pm, base, self.soul_thresholds,
                    apply_now=(soul_elapsed >= SECRET_TRAP_MIN_SECONDS_SINCE_CONNECT))

        elif method == "give_item":
            # Delta check (2026-08-01, see _item_already_owned_live's
            # docstring): skip the CreateRemoteThread call entirely if the
            # live possession flag already reads owned — most relevant on
            # a client reconnect with the game still running, where the
            # full backlog gets resent and every already-applied item
            # would otherwise fire a fully redundant injection.
            #
            # Retractor/Accumulator (2026-08-10, Jon's report of 7
            # Retractors after only 5 should exist — see
            # STACKABLE_GIVEITEM_COUNT_RVAS's comment for the full
            # diagnosis): these have no ITEM_FLAG_RVAS boolean flag, so
            # _item_already_owned_live always returns None ("unknown,
            # inject anyway") for them — the exact gap that let a foreign
            # Retractor/Accumulator get re-granted on a reconnect/replay.
            # Use the running-total counter check instead for these two.
            #
            # Unique Retractor Keys (2026-08-18): the 5 named
            # "Retractor - <region>" items have neither a possession flag
            # (ITEM_FLAG_RVAS) nor a per-item counter — checked first, ahead
            # of STACKABLE_GIVEITEM_COUNT_RVAS, via the save-backed
            # CF_CUSTOM00-04 flag. That flag gets set from TWO different
            # places depending on how this player received the item: the
            # exe patch's own Hook B when it's a real native pickup in this
            # player's world, or write_named_flag() just below when it's a
            # remote grant delivered over the network (see that function's
            # own docstring for why both paths are needed — RETRACTOR_KEY_
            # ITEM_TO_CF_INDEX's comment covers the index derivation).
            if item_name in RETRACTOR_KEY_ITEM_TO_CF_INDEX:
                already_sufficient = read_named_flag(
                    pm, base, RETRACTOR_KEY_ITEM_TO_CF_INDEX[item_name])
            elif item_name in STACKABLE_GIVEITEM_COUNT_RVAS:
                already_sufficient = _stackable_giveitem_already_sufficient(
                    pm, base, item_name, self._received_count(item_name))
            else:
                already_sufficient = _item_already_owned_live(pm, base, item_name)
            if already_sufficient:
                ok = True
                logger.info(
                    f"[ShadowMan] {item_name} already owned live — "
                    f"skipping redundant re-injection."
                )
            else:
                ok = inject_give_item(pm, base, arg)
                if ok:
                    logger.info(f"[ShadowMan] GiveItem({arg:#x}) injected.")
                    # Remote-grant flag set (2026-08-18) — see
                    # write_named_flag()'s own docstring. Hook B (native
                    # pickup) never runs for a give_item-injected grant, so
                    # without this the schism's exe-side gate would stay
                    # permanently locked for a remotely-received key even
                    # though AP correctly considers this player to have it.
                    if item_name in RETRACTOR_KEY_ITEM_TO_CF_INDEX:
                        if write_named_flag(
                                pm, base, RETRACTOR_KEY_ITEM_TO_CF_INDEX[item_name]):
                            logger.info(
                                f"[ShadowMan] {item_name} CF_CUSTOM flag set "
                                f"(remote grant — schism now unlockable).")
                        else:
                            logger.warning(
                                f"[ShadowMan] {item_name} granted but its "
                                f"CF_CUSTOM flag could not be confirmed set — "
                                f"its schism may stay locked until a future "
                                f"reconnect/backlog replay retries this.")

        elif method == "gad":
            # Cumulative count of real Gad Temple items ONLY — Poigne no
            # longer goes through this method (see AP_ITEM_INJECTION's
            # 2026-07-25 bug-fix comment); it has its own independent
            # "poigne_ability" method below.
            #
            # apply_now gated on the same connect-time signal as Secret
            # Trap (2026-08-01, Jon's call pending a Ghidra look at
            # FUN_140459d50 — this whole function is EXPERIMENTAL/
            # unverified and coincided with a crash during connect-time
            # backlog processing). Reuses SECRET_TRAP_MIN_SECONDS_SINCE_CONNECT
            # rather than a new constant — same "is this genuinely live, or
            # still catching up from a reconnect" question Secret Trap
            # already answers this way. Flags are always written correctly
            # either way; a withheld apply still lands on the next level
            # (re)entry via the existing resync sweep.
            gad_elapsed = (time.monotonic() - self._connected_at
                           if self._connected_at is not None else 0.0)
            # Clamped to 3 here (2026-08-02, Jon's report: "Gad Temple
            # count 8" in a re-assert log) -- inject_gad_power() already
            # internally clamps temple_count to [0,3] before ever writing
            # a GAD_*_RVA byte, so the LIVE GAME never actually saw an
            # out-of-range value; this was a display/state bug, not a
            # memory-safety one. But leaving self.gad_powers_received
            # itself uncapped meant it could climb arbitrarily high (only
            # 3 real "Gad Power" items exist in a normal item pool, so
            # this never showed up from real gameplay -- it surfaced from
            # /siminject Gad Power / /simbacklog testing, which has no
            # such natural ceiling and calls this exact branch each time)
            # and every log line downstream (this one, and the level-entry
            # re-assert log a few hundred lines down) printed that
            # unbounded raw counter instead of the real clamped value,
            # which is what actually got written -- confusing/alarming to
            # read even though nothing unsafe happened. Capping the
            # counter itself fixes the log at the source rather than
            # patching each display site separately.
            # Absolute recompute from the authoritative items_received
            # history (2026-08-02, Jon's suggestion: "what if we just set
            # gad to 0 before reloading gad items? delta applying gad seems
            # harder than setting to 0 then re-applying") — same pattern
            # "soul"/"cadeaux" already use via _received_count()/
            # set_dark_soul_count(), and the exact fix that closes out
            # BOTH of today's earlier gad bugs at the root rather than
            # patching around them:
            #   1. The "Gad Power gave count 2" mystery — that incremental
            #      "self-heal against live memory, then += 1" approach
            #      made the result depend on whatever _live_gad_temple_tier
            #      happened to read at that exact moment, which is not
            #      authoritative (it's just an effect of our own past
            #      writes, or in the worst case someone else's memory
            #      poking). _received_count("Gad Power") is authoritative —
            #      it's AP's own items_received list, the literal source of
            #      truth for "how many real Gad Power items has this slot
            #      received" — so the result can never depend on live
            #      memory state at all.
            #   2. The pre-connection false-gad-level-1 bug (same day,
            #      separate writeup) — structurally can't happen via this
            #      branch either now: it only ever runs from a real
            #      "ReceivedItems" packet, and even if it somehow ran with
            #      nothing received yet, _received_count would correctly
            #      return 0, not whatever garbage live memory read.
            # inject_gad_power() itself keeps its own internal "NEVER WRITES
            # BACKWARD" floor against live memory regardless (cheap defense
            # in depth against self.item_names not being populated yet, or
            # any other edge case that could make this recompute return too
            # low a value) — this doesn't remove that safety net, it just
            # means the normal case no longer depends on live memory being
            # trustworthy to get the RIGHT answer, only to avoid the wrong
            # one.
            self.gad_powers_received = min(self._received_count("Gad Power"), 3)
            ok = inject_gad_power(
                pm, base, self.gad_powers_received,
                apply_now=(gad_elapsed >= SECRET_TRAP_MIN_SECONDS_SINCE_CONNECT))
            if ok:
                logger.info(
                    f"[ShadowMan] Gad Temple count {self.gad_powers_received} "
                    f"injected."
                )

        elif method == "poigne_ability":
            # Same connect-time gating as "gad" just above.
            poigne_elapsed = (time.monotonic() - self._connected_at
                               if self._connected_at is not None else 0.0)
            self.poigne_ability_received = True
            # known_good_temple_tier (2026-08-02, see inject_poigne_ability's
            # own docstring) — self.gad_powers_received is itself already an
            # absolute recompute from AP's own items_received history (not
            # live memory), so it's safe to use as the trusted correction
            # target even though this same call may be what just corrupted
            # GAD_1_RVA.
            ok = inject_poigne_ability(
                pm, base,
                apply_now=(poigne_elapsed >= SECRET_TRAP_MIN_SECONDS_SINCE_CONNECT),
                known_good_temple_tier=self.gad_powers_received)
            if ok:
                logger.info("[ShadowMan] Poigne ability injected.")

        elif method == "pickup_sys":
            ok = inject_pickup_system(pm, base, arg)
            if ok:
                logger.info(f"[ShadowMan] pickup_sys({arg:#x}) injected.")

        elif method == "light_soul":
            # Marks self._light_soul_injected_this_session True BEFORE the
            # call (2026-08-06, see that flag's own docstring for the full
            # root cause) -- inject_light_soul()'s shellcode unavoidably
            # also flips CF_GOT_LIGHTSOUL as a side effect of granting the
            # ability, the same flag _poll_live_light_soul watches to
            # detect a genuine physical Fogometers visit. Setting this
            # BEFORE the call (not after) closes a narrow but real race:
            # _poll_live_light_soul runs on both _memory_watcher_loop (~1s)
            # and, since 2026-08-23, _fast_watcher_loop (FAST_POLL_INTERVAL,
            # ~0.15s -- see that loop's docstring) and could otherwise
            # observe the flag flip between this call returning and the
            # next line running. The tighter fast-loop cadence makes that
            # window MORE likely to actually get hit if this were ordered
            # the other way, not less -- pre-setting the flag first is what
            # makes the ordering safe at any poll cadence.
            self._light_soul_injected_this_session = True
            ok = inject_light_soul(pm, base)
            if ok:
                logger.info("[ShadowMan] Light Soul injected.")

        elif method == "cadeaux":
            ok_flag  = inject_give_item(pm, base, arg)      # "ever collected" HUD/tab unlock flag
            # Absolute target, not an increment — same reasoning as "soul"
            # above (2026-07-26 fix, see set_cadeaux_count's docstring).
            # Cadeaux Bundle Size (2026-07-27): weighted total, not a flat
            # per-item count — a received "Cadeaux Bundle x5" is worth 5,
            # not 1 (see _received_cadeaux_total()/items.py's
            # cadeaux_item_weight()).
            ok_count = set_cadeaux_count(pm, base, self._received_cadeaux_total())
            ok = ok_flag and ok_count
            if ok:
                logger.info("[ShadowMan] Cadeaux flag injected, count synced.")

        elif method == "trap_bonus":
            # Renamed + generalized from "secret_trap" (2026-08-03) when
            # this grew from one category (cosmetic secrets) into four
            # (secret/health/voodoo/ammo) — see _apply_trap_bonus_now's
            # docstring for the category-roll logic. The connect-time
            # elapsed gate that used to apply here was REMOVED entirely
            # back on 2026-08-02 (Jon: "now that we know secret trap
            # wasn't the issue.. we can apply the last secret trap on the
            # list?") once Secret Trap was conclusively cleared as a
            # suspect in the ntdll heap-corruption investigation (see
            # CLAUDE.md's "Crash persisted with Secret Trap fully
            # excluded" writeup) — the real causes were inject_give_item's
            # unvalidated vtable pointer and the EXE-patch reapply
            # corruption bug, both fixed elsewhere. The secret category's
            # own apply is still pure write_cvar_bool() — zero
            # CreateRemoteThread — so there was never a safety reason for
            # it to wait out a connect-time window; health/voodoo/ammo
            # each have their own internal gating where it matters
            # (title-screen checks, the still-standing
            # SECRET_TRAP_MIN_SECONDS_SINCE_CONNECT gate on gad/poigne/
            # soul's own CreateRemoteThread calls above is unrelated to
            # this item).
            #
            # Always calls _apply_trap_bonus(idx, arg) unconditionally,
            # relying on the same debounce/supersede mechanism Secret Trap
            # already had (see that method's own docstring) to collapse a
            # burst of many backlog Trap/Bonus items down to just the last
            # one. `arg` (2026-08-05) is now the concrete effect key from
            # AP_ITEM_INJECTION -- e.g. "poison"/"voodoo_hold" -- baked
            # into the item's own name at generation time, not rolled here.
            await self._apply_trap_bonus(idx, arg)

    async def _simulate_items(self, item_names: List[str], label: str) -> None:
        """
        Test-injection harness (2026-08-02, Jon's request: "from client we
        should be able to simulate all these injections to confirm replay
        wont cause issue"). Runs a synthetic list of item names through
        the REAL _inject_item() dispatch -- same method-lookup, same
        title-screen gate, same connect-time apply_now gating, same
        CreateRemoteThread calls (inject_give_item/inject_gad_power/
        inject_poigne_ability/etc.), same Secret Trap debounce/supersede
        path -- with the same ITEM_INJECT_PACING_SECONDS gap between items
        _item_inject_loop uses for a real backlog. The only things NOT
        real: the item ids (idx values come from the dedicated
        _sim_idx_next out-of-band counter, see its own comment) and
        source_player (forced to something other than self.slot so
        nothing gets skipped as "self-found").

        Built so Jon can reproduce/stress-test the exact item-shape and
        burst-size conditions that have coincided with the still-
        unexplained ntdll heap-corruption crashes (see CLAUDE.md) on
        demand, locally, without needing a real multiworld reconnect --
        a much faster iterate-and-retest loop than waiting for another
        real one. A completed run with no crash is real (if imperfect)
        negative evidence for whatever's being tested; a crash reproduced
        this way would be the first ever achieved outside a live AP
        session, which alone would be a big win for this investigation
        (it would mean the mechanism doesn't depend on anything AP-
        server-specific at all).

        Real side effects DO happen for genuinely stackable/cumulative
        state -- gad_powers_received increments for real, Secret Trap
        cvars actually get written and can supersede a real Secret Trap
        that's currently active, Gad Power/Poigne flags actually get set
        in the live game. This is deliberate: a no-op dry run wouldn't
        exercise the real mechanism this is meant to test. "Cadeaux" is
        the one safe no-op exception -- it's recomputed from the real
        items_received history inside _inject_item itself (see
        _received_cadeaux_total), so a sim call just re-writes the
        already-correct value.

        "Dark Soul" is deliberately NOT routed through _inject_item's own
        "soul" branch, for the same reason (2026-08-02, Jon: "we need it
        for souls too, because we need soul level to apply live too") --
        that branch also only ever recomputes an ABSOLUTE target from the
        real items_received history via set_dark_soul_count(), so a sim
        call through it would be just as much of a no-op as Cadeaux, and
        _sync_soul_level() only ever WRITES/executes when new_value >
        old_value -- meaning its own live-apply logic, including the
        CreateRemoteThread virtual-call fallback branch
        (_build_soul_meter_setvalue_shellcode, the soul-level equivalent
        of _apply_gad_level_now/FUN_140459d50), would never actually fire
        during a sim run at all. Instead, "Dark Soul" entries go through
        _simulate_soul_gain(), which bypasses the absolute-recompute
        design and increments the live count for real via
        inject_dark_soul(), then calls _sync_soul_level() the same way
        the real "soul" branch does (apply_now=True, since a sim call is
        meant to exercise the live-apply path on purpose) -- so a sim run
        can actually push the count across an SL threshold and exercise
        the real write, and, if the live soul object isn't the fast-path
        class at that moment, the real CreateRemoteThread virtual call
        too.
        """
        pm, base = _get_process()
        if pm is None:
            logger.warning(
                "[ShadowMan] /sim: game not running -- need a live, "
                "in-game session to test against.")
            return
        # Guaranteed != self.slot regardless of how many real players are
        # in the multiworld, so every simulated item takes the normal
        # "foreign item" path through _inject_item rather than being
        # skipped as self-found.
        sim_player = (self.slot or 0) + 1000
        logger.info(
            f"[ShadowMan] === SIM START: {label} — {len(item_names)} "
            f"item(s), paced {ITEM_INJECT_PACING_SECONDS}s apart (same "
            f"as a real backlog) ===")
        for i, item_name in enumerate(item_names):
            idx = self._sim_idx_next
            self._sim_idx_next += 1
            if i + 1 < len(item_names):
                logger.info(
                    f"[ShadowMan]     (sim {i + 1}/{len(item_names)} — "
                    f"next up: '{item_names[i + 1]}')")
            if item_name == "Dark Soul":
                await self._simulate_soul_gain(pm, base)
            else:
                await self._inject_item(item_name, idx, sim_player)
            await asyncio.sleep(ITEM_INJECT_PACING_SECONDS)
        logger.info(
            f"[ShadowMan] === SIM END: {label} — if the game is still "
            f"running and responsive, this sequence completed without "
            f"crashing. ===")

    async def _simulate_soul_gain(self, pm, base) -> None:
        """
        Sim-only Dark Soul path (2026-08-02) -- see _simulate_items()'s
        docstring for why this exists instead of just calling
        _inject_item("Dark Soul", ...). Bypasses the real "soul" branch's
        absolute-recompute-from-history design (which would make a sim
        call a no-op) and increments the live count for real, then syncs
        the soul level meter the same way the real branch does --
        including, if the live soul object isn't the fast-path class at
        that moment, the actual CreateRemoteThread virtual call
        (_build_soul_meter_setvalue_shellcode) that's the soul-level
        analog of _apply_gad_level_now/FUN_140459d50. apply_now=True
        unconditionally here -- a sim call is meant to exercise the live-
        apply path on purpose, not defer it the way a real connect-time
        backlog item would.

        Same title-screen fail-closed gate as _inject_item, since this
        bypasses that function entirely and needs its own.
        """
        if _read_is_at_title_screen(pm, base) is not False:
            logger.info(
                "[ShadowMan] [sim] Not confirmed in-game yet — Dark Soul "
                "sim skipped.")
            return
        logger.info(
            "[ShadowMan] >>> [sim] Applying Dark Soul (live increment + "
            "soul-level live-apply sync) — if the game crashes now, THIS "
            "is what was being applied.")
        _flush_log_handlers()
        ok = inject_dark_soul(pm, base, 1)
        if ok:
            logger.info(
                "[ShadowMan] [sim] Dark Soul count incremented (live, "
                "bypasses the real absolute-recompute path so the soul-"
                "level live-apply sync actually has something new to "
                "apply).")
            _sync_soul_level(pm, base, self.soul_thresholds, apply_now=True)

    def _poll_overlay_panel_commands(self) -> None:
        """
        Drains anything the in-game connect/console panel (overlay_dll,
        2026-08-04 — F10 in-game) queued for us and dispatches it through
        the exact same entry points typing into this client's own
        terminal already uses: ctx.connect() for a connect request,
        self.command_processor(text) for a /command or chat line — so the
        panel is a thin front end onto existing, already-hardened logic,
        not a new code path of its own. See _OverlayIPC.poll_incoming's
        own docstring for the wire side of this.
        """
        for msg in _overlay_ipc.poll_incoming():
            msg_type = msg.get("type")

            if msg_type == "connect_request":
                server = (msg.get("server") or "").strip()
                name = (msg.get("name") or "").strip()
                password = (msg.get("password") or "").strip()
                if not server:
                    logger.warning(
                        "[ShadowMan] Overlay panel: Connect pressed with no server address.")
                    continue

                # Mirrors ClientCommandProcessor._cmd_connect's own reset
                # (a fresh address always clears any previously-remembered
                # server/password), but sets username/password AFTER the
                # reset rather than before — _cmd_connect itself clears
                # self.ctx.username unconditionally, which would silently
                # wipe out a name set beforehand.
                self.server_address = None
                if password:
                    self.password = password
                if name:
                    self.username = name
                elif not self.username and not self.auth:
                    logger.warning(
                        "[ShadowMan] Overlay panel: no player name given and none set "
                        "previously — if the connection seems to hang, this client is "
                        "waiting for a slot name typed into ITS OWN window (not the "
                        "in-game panel), the same as it would if you'd typed /connect "
                        "there yourself.")
                logger.info(f"[ShadowMan] Overlay panel: connecting to {server!r}...")
                Utils.async_start(self.connect(server), name="connecting (overlay panel)")

            elif msg_type == "disconnect_request":
                # Mirrors ClientCommandProcessor._cmd_disconnect exactly —
                # same call, same fire-and-forget async_start pattern.
                logger.info("[ShadowMan] Overlay panel: disconnecting...")
                Utils.async_start(self.disconnect(), name="disconnecting (overlay panel)")

            elif msg_type == "console_input":
                text = (msg.get("text") or "").strip()
                if not text:
                    continue
                logger.info(f"[ShadowMan] Overlay panel command: {text}")
                if self._overlay_command_processor is None:
                    self._overlay_command_processor = self.command_processor(self)
                self._overlay_command_processor(text)

            else:
                logger.warning(
                    f"[ShadowMan] Overlay panel sent unrecognized message type: {msg_type!r}")

    def _poll_secret_poller_state(self, pm, base: int) -> None:
        """
        Read-only forensics (2026-08-01): reads the EXE poller's
        LAST_KNOWN array once per POLL_INTERVAL and logs any byte that
        changed since the previous poll — a running record of what the
        poller has been observing/acting on, since it has no logging of
        its own. Diff-only (not a full snapshot every tick) to keep the
        log signal-to-noise reasonable — an entry that's genuinely
        oscillating every tick will still show up as a change on nearly
        every poll, which is itself a meaningful signal (a healthy,
        rarely-toggled secret should log a transition only on the rare
        occasions its cvar is actually flipped).
        Note: this samples once per second, not once per frame — a real
        rapid oscillation between two samples would only show the value
        at each sample point, not every individual poller tick in
        between. Still meaningful: if one entry is flapping fast enough
        to differ on nearly every 1s sample, that alone is a strong
        signal something's wrong with that specific entry.
        """
        snapshot = read_poller_last_known(pm, base)
        if snapshot is None:
            return
        prev = self._last_known_poller_snapshot
        if prev is not None and len(prev) == len(snapshot):
            for i, name in enumerate(POLLER_SECRET_TABLE):
                if snapshot[i] != prev[i]:
                    logger.info(
                        f"[ShadowMan] [poller] {name}: LAST_KNOWN "
                        f"{prev[i]:#04x} -> {snapshot[i]:#04x}")
        self._last_known_poller_snapshot = snapshot

    def _reset_all_secrets_off(self, pm, base: int) -> None:
        """
        One-time-per-session cleanup (2026-08-01), added after Jon noticed
        several secrets were ON in-game that no current Secret Trap roll
        had turned on -- almost certainly leftover from the extensive
        manual /secret, /dogmode, /dogmodelive testing throughout this
        whole investigation, persisted via kexengine.cfg (cvars load once
        at boot, then live in memory for the session -- see CLAUDE.md's
        kexengine.cfg section).

        Why this matters beyond tidiness: write_cvar_bool() writes bypass
        the engine's own mutual-exclusion enforcement entirely -- the real
        SetValue pipeline, via a secret's on-change callback
        (FUN_140458EF0), walks a table of mutually-exclusive
        g_<name>mode secrets and force-turns-off whichever one conflicts
        with the one just changed. Normal play can never reach a state
        with more than one secret on at once, because that callback
        always enforces it. Multiple leftover secrets left ON
        simultaneously by raw writes that never went through that
        callback put the game in a state its own mesh/skin reload logic
        (FUN_140459250) may never have been exercised against -- a
        plausible contributing factor to the still-unexplained ntdll
        heap-corruption crashes, independent of whatever else is going
        on. Resetting to a known-clean baseline once per session removes
        this specific risk outright, regardless of whether it turns out
        to be the actual root cause.

        Unconditional writes (doesn't bother reading current state first)
        -- simpler, and guarantees a genuinely clean baseline rather than
        depending on a preceding read succeeding. Only touches
        TRAP_SAFE_SECRETS (the 18 already-proven-safe secrets), same
        boundary every other command here respects -- never the 5
        bucket-3/unconfirmed-callback secrets.
        """
        ok_count = 0
        for name in TRAP_SAFE_SECRETS:
            rva = SECRET_CVAR_RVAS.get(name)
            if rva is None:
                continue
            if write_cvar_bool(pm, base, rva, False):
                ok_count += 1
        logger.info(f"[ShadowMan] Session start: forced {ok_count}/{len(TRAP_SAFE_SECRETS)} "
                    f"secrets to a clean OFF baseline (leftover-testing cleanup).")

    # How long to wait, after receiving a Trap/Bonus item, before actually
    # applying it -- see _apply_trap_bonus's docstring. Long enough to
    # coalesce an entire connect-time backlog burst (which arrives over
    # a handful of milliseconds, not seconds), short enough to be
    # imperceptible for a single live-received item during normal play.
    # (Constant name kept as SECRET_TRAP_* rather than renamed to
    # TRAP_BONUS_* purely to avoid touching every reference across this
    # file for a cosmetic rename with zero behavior change — the debounce
    # now applies to the whole Trap/Bonus item, not just the secret
    # category.)
    SECRET_TRAP_DEBOUNCE_SECONDS = 0.5

    # Gap between a secret supersede's OFF write and the new secret's ON
    # write (2026-08-01) -- see the comment at its use site inside the
    # "secret" branch of _apply_trap_bonus_now. A few real frames' worth
    # is enough for the EXE poller to cleanly observe and act on the OFF
    # write before the ON write ever lands; comfortably imperceptible
    # against the 60s+ durations this feature actually runs at. Only
    # meaningful for the secret category (voodoo/ammo drain-vs-hold
    # supersedes are plain memory writes with no poller/callback timing
    # sensitivity to worry about).
    SECRET_TRAP_SUPERSEDE_GAP_SECONDS = 0.15

    async def _apply_trap_bonus(self, idx: int, effect: str) -> None:
        """
        Debounced entry point for a received Trap/Bonus item (2026-08-01,
        generalized 2026-08-03 from the original secret-only version,
        `effect` param added 2026-08-05 -- see AP_ITEM_INJECTION's comment:
        which concrete item this was (poison/heal/voodoo_drain/
        voodoo_hold/ammo_drain/ammo_hold/secret) is now baked into the
        item's own name at generation time, not rolled here).
        Doesn't apply immediately -- records this as the latest pending
        request and (re)schedules a short timer via
        _apply_trap_bonus_debounced. If ANOTHER Trap/Bonus arrives within
        SECRET_TRAP_DEBOUNCE_SECONDS, the earlier pending request is
        cancelled outright (never physically applied at all) and replaced
        by this one.

        Added after Jon reported a crash immediately on connecting the AP
        client, right as a backlog of previously-received items applied.
        Without this, N trap/bonus items received in a burst (e.g. every
        one collected in a prior session, all delivered again on
        reconnect since AP always resends full items_received history)
        would each physically fire -- for the secret category specifically,
        that meant write ON, get immediately superseded OFF by the next
        one, write ON again, etc., each potentially triggering the
        engine's on-change callback via the EXE poller, all within a
        fraction of a second. Debouncing means only the LAST item in a
        rapid burst is ever physically applied at all.
        """
        if self._secret_trap_debounce_task is not None:
            self._secret_trap_debounce_task.cancel()
        self._secret_trap_debounce_task = asyncio.create_task(
            self._apply_trap_bonus_debounced(idx, effect))

    async def _apply_trap_bonus_debounced(self, idx: int, effect: str) -> None:
        """Sleeps SECRET_TRAP_DEBOUNCE_SECONDS, then calls
        _apply_trap_bonus_now -- unless cancelled first by a newer
        _apply_trap_bonus call (see that method's docstring), in which
        case this coroutine is torn down mid-sleep and never reaches the
        real apply at all.

        Once the delay is over and _apply_trap_bonus_now actually starts
        (2026-08-01 fix — real bug, not just theorized, found via the
        secret category originally): a THIRD Trap/Bonus arriving while
        this one is already mid-application would call .cancel() on THIS
        SAME task (_secret_trap_debounce_task is one slot, always the
        most recent), and that cancellation doesn't stop at the sleep
        above — with nothing catching CancelledError around the call
        below, it can interrupt _apply_trap_bonus_now at any of ITS OWN
        internal awaits too — silently, since an uncaught CancelledError
        here just cancels the task with no log line. Fixed with
        asyncio.shield(): once _apply_trap_bonus_now has actually
        started, it now always runs to completion even if this outer
        task gets cancelled — the cancellation still stops a request
        that's only sitting in the delay above (nothing has happened
        yet, still safe to drop entirely), but can no longer tear an
        in-progress apply apart partway through.
        """
        try:
            await asyncio.sleep(self.SECRET_TRAP_DEBOUNCE_SECONDS)
        except asyncio.CancelledError:
            return
        await asyncio.shield(self._apply_trap_bonus_now(idx, effect))

    async def _apply_trap_bonus_now(self, idx: int, effect: str) -> None:
        """
        Apply one Trap/Bonus effect (2026-08-01, grown 2026-08-03 from a
        single "secret" category into four: secret, health, voodoo, ammo
        — see options.py's TrapBonus{Secrets,Health,Voodoo,Ammo}Enabled).

        `effect` (2026-08-05) is the concrete effect key from
        AP_ITEM_INJECTION's arg -- "poison"/"heal"/"voodoo_drain"/
        "voodoo_hold"/"ammo_drain"/"ammo_hold"/"secret" -- already baked
        into the item's own AP name at generation time by
        __init__.py's _roll_trap_bonus_item_name(), NOT rolled here
        anymore. This is what makes the AP log/tracker/chat show the
        specific effect ("Trap: Poison", "Bonus: Ammo Max Hold", ...)
        instead of a generic "Trap/Bonus" -- the name IS the effect now.
        Still deterministic per (seed, idx) for the two things that
        genuinely remain runtime decisions: which of the ~17 safe secrets
        a "secret" effect becomes, and (for mode="mixed") whether this
        particular pickup is temporary or permanent -- so a replay
        (/inject after a game restart, or _replay_all_received_items)
        reproduces the identical outcome rather than re-rolling, matching
        this project's general "randomness controlled at the seed level"
        convention (see CLAUDE.md's Randomizer Setting Best Practice).

        Mode (self.trap_bonus_mode, from slot_data — see options.py's
        TrapBonusMode) only matters for secret, voodoo_hold, and
        ammo_hold — the sub-effects that have a real "how long does this
        stick around" question:
          always_temporary — reverts automatically after
                              self.trap_bonus_duration seconds.
          always_permanent — left on indefinitely; for secret, only
                              reverts if a LATER Trap/Bonus happens to
                              roll the same secret and turns it back off;
                              for voodoo/ammo hold, keeps re-asserting
                              forever until superseded by that category's
                              own drain effect.
          mixed             — this individual pickup independently rolls
                              temporary vs permanent (50/50), using the
                              same per-(seed, idx) RNG, so it's equally
                              reproducible on replay.
        Poison and heal ignore mode entirely (fixed one-shot duration);
        voodoo/ammo drain are instant with nothing to revert.

        Silently no-ops (logs and returns) if the game isn't running or
        not confirmed in-game at apply time — unlike a real inventory
        item there's nothing to "queue" here since every effect is
        inherently transient; the next received Trap/Bonus (or a fresh
        connection) will apply fresh regardless.
        """
        pm, base = _get_process()
        if pm is None:
            logger.info(f"[ShadowMan] Trap/Bonus #{idx} ({effect}): game not running, skipped.")
            return

        # Light re-check, not a retry loop like _revert_secret_trap's —
        # the debounce window is only SECRET_TRAP_DEBOUNCE_SECONDS (0.5s),
        # so if we're not confirmed in-game that soon after the caller
        # (_inject_item/_replay_all_received_items) already confirmed we
        # were, something bigger just happened (quit to menu, etc.); skip
        # rather than retry-loop for a case this unlikely.
        if _read_is_at_title_screen(pm, base) is not False:
            logger.info(f"[ShadowMan] Trap/Bonus #{idx} ({effect}): not confirmed "
                        f"in-game at apply time, skipped.")
            return

        # Still a per-(seed, idx) rng — only consumed now for the
        # "permanent" roll (mode="mixed") and, for effect=="secret", which
        # specific cvar to pick. Neither of those affects the AP item name
        # itself, so this doesn't need to match the old draw order.
        rng = random.Random(f"{self.seed_name}-trap-bonus-{idx}")

        mode = self.trap_bonus_mode
        if mode == "always_permanent":
            permanent = True
        elif mode == "mixed":
            permanent = rng.random() < 0.5
        else:  # "always_temporary", and any unrecognized value fails safe to temporary
            permanent = False

        if effect == "poison":
            self.start_health_effect("poison")
            logger.info(f"[ShadowMan] Trap/Bonus #{idx}: Poison.")
            _overlay_ipc.trap_bonus("Trap: Poison")
            return

        if effect == "heal":
            self.start_health_effect("heal")
            logger.info(f"[ShadowMan] Trap/Bonus #{idx}: Recovery.")
            _overlay_ipc.trap_bonus("Bonus: Recovery")
            return

        if effect == "voodoo_drain":
            ok = self.trigger_voodoo_drain(pm, base)
            logger.info(f"[ShadowMan] Trap/Bonus #{idx}: Voodoo Drain, "
                        f"{'ok' if ok else 'FAILED'}.")
            _overlay_ipc.trap_bonus("Trap: Voodoo Drain")
            return

        if effect == "voodoo_hold":
            self.start_voodoo_max_hold(
                total_seconds=self.trap_bonus_duration, permanent=permanent)
            duration_desc = "permanent" if permanent else f"{self.trap_bonus_duration}s"
            logger.info(f"[ShadowMan] Trap/Bonus #{idx}: Voodoo Max Hold ({duration_desc}).")
            _overlay_ipc.trap_bonus(f"Bonus: Voodoo Max Hold ({duration_desc})")
            return

        if effect == "ammo_drain":
            ok = self.trigger_ammo_drain(pm, base)
            logger.info(f"[ShadowMan] Trap/Bonus #{idx}: Ammo Drain, "
                        f"{'ok' if ok else 'FAILED'}.")
            _overlay_ipc.trap_bonus("Trap: Ammo Drain")
            return

        if effect == "ammo_hold":
            self.start_ammo_max_hold(
                total_seconds=self.trap_bonus_duration, permanent=permanent)
            duration_desc = "permanent" if permanent else f"{self.trap_bonus_duration}s"
            logger.info(f"[ShadowMan] Trap/Bonus #{idx}: Ammo Max Hold ({duration_desc}).")
            _overlay_ipc.trap_bonus(f"Bonus: Ammo Max Hold ({duration_desc})")
            return

        if effect != "secret":
            logger.warning(f"[ShadowMan] Trap/Bonus #{idx}: unrecognized effect "
                            f"'{effect}' — skipped.")
            return

        # effect == "secret" -- original Secret Trap logic (2026-08-01),
        # unchanged except for the renamed mode/duration fields it reads.
        # Still the one sub-roll left at runtime -- see items.py's
        # _ITEM_DEFINITIONS comment for why "Secret Effect" stayed one
        # generic bucket rather than splitting into 18 per-secret items.
        secret = rng.choice(TRAP_SAFE_SECRETS)
        rva = SECRET_CVAR_RVAS[secret]

        # Everything from here on is wrapped (2026-08-02, Jon: "getting a
        # secret trap while another is active is disabling the prior one
        # but not applying the new one!") -- confirmed live: the log shows
        # the supersede OFF write landing and being logged (poller even
        # confirmed the byte flip), then NOTHING -- no ON attempt, no "ok"/
        # "FAILED" line, nothing at all, even though the very next lines in
        # this function are an unconditional write + an unconditional log
        # call with no branch that could skip both. That combination is
        # only possible if execution never reached them -- i.e. something
        # killed this coroutine between the OFF write and the ON write,
        # most likely during the SECRET_TRAP_SUPERSEDE_GAP_SECONDS sleep.
        #
        # This coroutine runs orphaned inside asyncio.shield() (see
        # _apply_trap_bonus_debounced) -- nothing ever awaits its return
        # value or checks it for an exception. If it raises ANYTHING
        # (including a real exception from write_cvar_bool/_get_process,
        # or -- theoretically shouldn't happen under shield(), but hasn't
        # been proven not to for this exact asyncio version/pattern -- a
        # CancelledError delivered directly to this task rather than the
        # outer debounce task), Python's default handler for an orphaned
        # task's unhandled exception logs it via the stdlib `asyncio`
        # logger, NOT this file's own `logger` -- meaning it could be
        # silently missing from the [ShadowMan]-prefixed log Jon is
        # reading even though something real is going wrong. Wrapping the
        # whole thing and logging via `logger` (then re-raising, so
        # asyncio's own cancellation/task bookkeeping isn't broken by
        # swallowing it) turns whatever this actually is into a visible,
        # diagnosable log line the next time it happens, regardless of
        # whether it's cancellation, a real exception, or something else
        # entirely.
        try:
            # A different secret is currently active (or pending revert) —
            # shut it off first so effects never visually stack.
            if self._active_secret_trap is not None and self._active_secret_trap != secret:
                if self._active_secret_trap_task is not None:
                    self._active_secret_trap_task.cancel()
                    self._active_secret_trap_task = None
                prior_rva = SECRET_CVAR_RVAS.get(self._active_secret_trap)
                if prior_rva is not None:
                    write_cvar_bool(pm, base, prior_rva, False)
                    logger.info(f"[ShadowMan] Trap/Bonus: {self._active_secret_trap} OFF "
                                f"(superseded by {secret})")
                self._active_secret_trap = None
                # Small settling gap before the new secret's ON write (2026-08-01,
                # confirmed live: a supersede's OFF+ON writes landing within the
                # same frame or two let the EXE poller's per-entry cache-tag
                # check transiently skip the second write, since both entries
                # can be mid-transition at once — observed live as betamode's
                # OFF applying cleanly but the superseding bigheadmode ON never
                # visibly taking effect until a full game restart). Gives the
                # poller a full, clean tick to observe and act on the OFF write
                # on its own before the ON write ever lands.
                await asyncio.sleep(self.SECRET_TRAP_SUPERSEDE_GAP_SECONDS)

            # Same secret re-rolled while its own revert is still pending —
            # cancel that timer; this pickup's mode/duration takes over
            # instead of stacking two reverts against the same cvar.
            if self._active_secret_trap == secret and self._active_secret_trap_task is not None:
                self._active_secret_trap_task.cancel()
                self._active_secret_trap_task = None

            ok = write_cvar_bool(pm, base, rva, True)
            duration_desc = "permanent" if permanent else f"{self.trap_bonus_duration}s"
            logger.info(
                f"[ShadowMan] Trap/Bonus #{idx}: rolled secret -> {secret} ON "
                f"({duration_desc}) "
                f"{'ok' if ok else 'FAILED'}"
            )
            if ok:
                self._active_secret_trap = secret
                self._active_secret_trap_task = None if permanent else asyncio.create_task(
                    self._revert_secret_trap(secret, rva, self.trap_bonus_duration))
                # In-game overlay toast naming the specific effect (2026-08-03,
                # Jon: "the pop-up just says secret trap received but now
                # which secret trap") -- the generic "Secret Effect" toast
                # at RECEIPT time (fired from the normal ReceivedItems
                # flow, 2026-08-05: this is the one remaining name that's
                # still generic — every other Trap/Bonus item's receipt
                # toast already shows its real effect, since the name
                # itself now says exactly what it is) can't know which of
                # the ~17 safe secrets this will roll into; that's only
                # decided here, at apply time.
                _overlay_ipc.secret_trap(
                    SECRET_DISPLAY_NAMES.get(secret, secret), duration_desc)
        except asyncio.CancelledError:
            logger.warning(
                f"[ShadowMan] Trap/Bonus #{idx}: apply for {secret} was "
                f"CANCELLED mid-write (between the supersede OFF write and "
                f"the new ON write) — this should be impossible under "
                f"asyncio.shield() unless something cancelled THIS specific "
                f"task directly, not the outer debounce task. This is the "
                f"root cause of 'disables the prior one but never applies "
                f"the new one' if you're seeing this line.")
            raise
        except Exception as exc:
            logger.warning(
                f"[ShadowMan] Trap/Bonus #{idx}: apply for {secret} raised "
                f"an unexpected exception after the supersede OFF write: "
                f"{exc!r}. This exception would otherwise have been silently "
                f"lost — this coroutine runs orphaned under asyncio.shield(), "
                f"so nothing else was watching for it.")
            raise

    async def _revert_secret_trap(self, secret: str, rva: int, duration: float) -> None:
        """
        Companion to _apply_secret_trap — waits `duration` seconds then
        writes the secret back off. Re-fetches the process handle at
        revert time rather than trusting whatever was captured when the
        trap first applied, since the game may have restarted in the
        meantime (same defensive pattern as every other watcher loop in
        this file) — if so, there's nothing live to revert and this is a
        harmless no-op.

        Gated on _read_is_at_title_screen the same way _inject_item/
        _replay_all_received_items gate the ON write (2026-08-01 fix) —
        this was a real, confirmed gap: unlike those two entry points,
        this delayed write previously only checked "is the process
        running", never "is the player actually confirmed in-game right
        now". The revert timer fires on its own schedule, completely
        independent of what the player happens to be doing, so it could
        land mid-level-transition, mid-cutscene, or at the title screen —
        exactly the class of "writing into memory that doesn't have a
        valid player object right now" this codebase already identified
        as crash-causing for every OTHER AP item (see _inject_item's own
        title-screen-gate comment: "items received while connected at the
        main menu got injected straight into player/inventory structures
        that don't exist yet, and starting a New Game afterward crashed").
        Both of the ntdll heap-corruption crashes investigated this
        session happened right around a transition-like moment (entering
        deadside, end of a cutscene) — a timer-driven write landing at an
        arbitrary, ungated moment is a very plausible contributor.
        Retries every POLL_INTERVAL until either confirmed in-game (then
        writes) or the process disappears (then bails) — same "queue and
        auto-apply once you're in a level" shape every other gated write
        in this file already uses, just as a small local loop instead of
        the general item queue, since this is a one-off timer task, not
        part of that queue.

        Can be cancelled at any point in this wait (both the initial sleep
        and the retry loop) by a later _apply_secret_trap call (see its
        overlap handling) — in that case this coroutine is torn down by
        asyncio before reaching the cleanup below, and the caller that
        cancelled it is responsible for the actual off-write/state
        cleanup itself, so nothing here needs to run for that case.
        Clears self._active_secret_trap/_active_secret_trap_task only if
        THIS secret is still the recorded active one (guards against a
        rare race where this finishes just as something else already
        replaced it).
        """
        await asyncio.sleep(duration)
        ambiguous_streak = 0
        while self._active_secret_trap == secret and not self.exit_event.is_set():
            pm, base = _get_process()
            if pm is None:
                logger.info(f"[ShadowMan] Secret Trap auto-revert for {secret} skipped — game not running.")
                break
            title_state = _read_is_at_title_screen(pm, base)
            if title_state is False:
                ok = write_cvar_bool(pm, base, rva, False)
                logger.info(f"[ShadowMan] Secret Trap: {secret} OFF (auto-revert) {'ok' if ok else 'FAILED'}")
                break
            # title_state is None or True here. None specifically means
            # "reads failed or disagreed" (see _read_is_at_title_screen's
            # docstring) -- during shutdown this can persist for a real
            # stretch even after the process is effectively gone, since
            # _get_process()'s cached-handle liveness probe (one PE-header
            # byte) tends to keep succeeding well after game-managed memory
            # like the title-screen reads stops being reliable. Confirmed
            # live 2026-08-01: this loop logged "deferred... will retry"
            # repeatedly through an entire game shutdown instead of ever
            # hitting the `pm is None` exit. After a few consecutive
            # ambiguous reads, fall back to an actual OS process-list check
            # (more expensive, so not done every tick) rather than trusting
            # the cheap probe alone.
            if title_state is None:
                ambiguous_streak += 1
                if ambiguous_streak >= 3 and not _is_patched_exe_running():
                    logger.info(f"[ShadowMan] Secret Trap auto-revert for {secret} skipped — "
                                f"{PATCHED_EXE_NAME} is no longer running.")
                    break
            else:
                ambiguous_streak = 0
            logger.info(f"[ShadowMan] Secret Trap auto-revert for {secret} deferred — "
                        f"not confirmed in-game yet, will retry.")
            await asyncio.sleep(POLL_INTERVAL)
        if self._active_secret_trap == secret:
            self._active_secret_trap = None
            self._active_secret_trap_task = None

    def start_health_effect(self, kind: str, **kwargs) -> None:
        """
        Start a poison (kind="poison") or recovery (kind="heal") health
        effect (2026-08-03) — cancels whatever health effect is already
        running first, since only one is ever meant to be active at once
        (simpler than Secret Trap's overlap handling: there's no ON/OFF
        cvar state to restore here, a cancelled effect just stops ticking).
        kwargs are forwarded to _run_health_effect (e.g. total_seconds=).
        This is a plain (non-async) method — it only schedules the task,
        callers (e.g. /simpoison, or eventually a real item's inject
        branch) don't need to await it.
        """
        if self._active_health_effect_task is not None:
            self._active_health_effect_task.cancel()
            logger.info(f"[ShadowMan] Health effect: {self._active_health_effect_kind} "
                        f"cancelled, superseded by {kind}.")
        self._active_health_effect_kind = kind
        self._active_health_effect_task = asyncio.create_task(
            self._run_health_effect(kind, **kwargs))

    async def _run_health_effect(
        self, kind: str,
        total_seconds: float = HEALTH_EFFECT_TOTAL_SECONDS,
        tick_interval: float = HEALTH_EFFECT_TICK_INTERVAL_SECONDS,
    ) -> None:
        """
        Gradual health drain ("poison") or recovery ("heal") over
        total_seconds, applied in ticks every tick_interval seconds via
        apply_health_delta() (a real ModifyStat call, not a cosmetic
        overlay — see the "Health Effects" comment block above
        HEALTH_EFFECT_TICK_INTERVAL_SECONDS for why this is believed safe,
        and why it's still UNVERIFIED for this specific repeated/small-delta
        usage pattern).

        Re-reads current/max health fresh from live memory before every
        single tick rather than precomputing a fixed plan. Recovery is
        clamped so it never exceeds max health. Poison has no floor
        (2026-08-03, Jon's call) — the tick that would bring health to
        <=0 calls the real inject_death() instead of a partial ModifyStat
        write (which, per this file's own DeathLink derivation notes, does
        NOT trigger an actual death on its own), then the effect ends
        immediately rather than continuing to tick. A tick where the
        player isn't confirmed in-game (mid level transition, alt-tabbed
        to a menu, etc.) or where a health read fails is skipped, not
        treated as a fatal error — the effect just continues on the next
        tick instead of aborting outright.

        Both effects' per-tick magnitude is based on MAX health only
        (2026-08-05, see HEALTH_EFFECT_HEAL_FRACTION's own comment for the
        full history) — current health is read fresh each tick only to
        decide whether THIS tick would be lethal (poison) or to cap
        overshoot past max (heal), never to scale the delta itself. A
        health pickup landing mid-effect changes how much benefit you get
        from it, not how hard the effect is hitting you.

        Any death (poison-caused or otherwise) also stops whichever health
        effect is currently running — see _health_watcher_loop, which
        cancels _active_health_effect_task the moment it sees health drop
        to <=0, regardless of cause. This function's own inject_death()
        branch below only covers a poison-caused death specifically
        (ends the loop immediately rather than waiting for the next watcher
        poll); the watcher's cancellation is what catches every other cause
        (an enemy, a DeathLink kill, fall damage, etc.) so a health effect
        never keeps ticking against a freshly-respawned health pool.

        Only one health effect is ever meant to run at once — see
        start_health_effect, which is the only intended caller.
        """
        kind_label = "Poison" if kind == "poison" else "Recovery"
        ticks = max(1, round(total_seconds / tick_interval))
        logger.info(f"[ShadowMan] {kind_label} effect starting — {total_seconds:.0f}s "
                    f"total, {ticks} ticks, ~{tick_interval:.0f}s apart.")
        try:
            for i in range(ticks):
                await asyncio.sleep(tick_interval)

                pm, base = _get_process()
                if pm is None:
                    logger.info(f"[ShadowMan] {kind_label} effect: game not running, stopping.")
                    return
                if _read_is_at_title_screen(pm, base) is not False:
                    logger.info(f"[ShadowMan] {kind_label} effect: not confirmed in-game, "
                                f"tick {i + 1}/{ticks} skipped.")
                    continue

                current = read_current_health(pm, base)
                try:
                    maximum = pm.read_int(base + MAX_HEALTH_RVA)
                except Exception:
                    maximum = None
                if current is None or maximum is None or maximum <= 0:
                    logger.info(f"[ShadowMan] {kind_label} effect: health read failed, "
                                f"tick {i + 1}/{ticks} skipped.")
                    continue

                if kind == "poison":
                    if current <= 0:
                        # Shouldn't normally happen (the lethal tick below
                        # ends the effect), but guard anyway.
                        logger.info(f"[ShadowMan] Poison effect: already at {current} HP, "
                                    f"tick {i + 1}/{ticks} skipped.")
                        continue
                    # Fixed magnitude based on MAX health only (2026-08-05,
                    # replacing the old ceil(current/ticks_remaining)
                    # formula — see HEALTH_EFFECT_HEAL_FRACTION's own
                    # comment above for the full history of why that was
                    # wrong: it re-scaled UP whenever a health pickup raised
                    # `current` mid-effect, so poison ate whatever health
                    # you found instead of you benefiting from it). Same
                    # ceil(maximum/ticks) amount every tick regardless of
                    # `current` or any pickups in between; no longer
                    # engineered to force landing on exactly 0 by the final
                    # tick — a tick that happens to bring health to <=0
                    # still triggers a real death below, but if you've
                    # healed enough to outlast this fixed drain, you're
                    # meant to survive it.
                    # ceil(maximum / ticks) is a POSITIVE HP amount --
                    # -(-maximum // ticks) alone (without the extra negation
                    # below) computes exactly that magnitude, not a negative
                    # delta. Bug caught live 2026-08-05 (Jon: "poison was
                    # raising my health each time... the delta was positive
                    # instead of negative") -- the drain amount must be
                    # negated separately from the ceiling-division trick
                    # that produces it.
                    magnitude = -(-maximum // ticks)  # ceil(maximum / ticks) — positive
                    per_tick = -magnitude             # negative delta — this is what drains health
                    if current + per_tick <= 0:
                        # This tick would be lethal. A raw/ModifyStat write
                        # to <=0 does NOT trigger a real death on its own
                        # (see the DeathLink derivation notes above
                        # HEALTH_EFFECT_TICK_INTERVAL_SECONDS) — only the
                        # dedicated death sequence does, so use that
                        # instead of apply_health_delta for this tick, and
                        # stop ticking once it fires. Deliberately NOT
                        # setting _ignore_next_death — this is a genuine
                        # own death and should flow through the normal
                        # DeathLink-send detection like any other death.
                        ok = inject_death(pm, base)
                        logger.info(f"[ShadowMan] Poison effect: lethal tick "
                                    f"({current} -> <=0) — triggered a real death, "
                                    f"{'ok' if ok else 'FAILED'}. Effect ending.")
                        return
                    delta = per_tick
                else:  # "heal"
                    total_heal = maximum * HEALTH_EFFECT_HEAL_FRACTION
                    per_tick = round(total_heal / ticks)
                    delta = min(per_tick, maximum - current)  # never exceed max
                    if delta <= 0:
                        logger.info(f"[ShadowMan] Recovery effect: already at max "
                                    f"({current}/{maximum}), tick {i + 1}/{ticks} skipped.")
                        continue

                ok = apply_health_delta(pm, base, delta)
                new_health = read_current_health(pm, base)
                logger.info(f"[ShadowMan] {kind_label} tick {i + 1}/{ticks}: delta={delta} "
                            f"{'ok' if ok else 'FAILED'} — health now {new_health}/{maximum}")
        finally:
            if self._active_health_effect_task is asyncio.current_task():
                self._active_health_effect_task = None
                self._active_health_effect_kind = None

    def trigger_voodoo_drain(self, pm, base: int) -> bool:
        """
        'Voodoo Drain' trap entry point (2026-08-03) — cancels any
        currently-running Voodoo Max Hold FIRST, then does the one-shot
        drain-to-0 write (apply_voodoo_drain). Per Jon: drain and hold are
        the same "category" (voodoo power) and should never coexist — the
        most recently applied one should win, not just whichever happens
        to write last on the next tick. Without this cancellation, a drain
        landing while a hold is active would get silently undone within
        ~1 second by the hold's own next tick re-filling it back to the
        cap — this makes the drain actually stick instead.
        """
        if self._active_voodoo_hold_task is not None:
            self._active_voodoo_hold_task.cancel()
            self._active_voodoo_hold_task = None
            logger.info("[ShadowMan] Voodoo Max Hold cancelled — superseded by Voodoo Drain.")
        return apply_voodoo_drain(pm, base)

    def start_voodoo_max_hold(
        self, total_seconds: float = HEALTH_EFFECT_TOTAL_SECONDS,
        permanent: bool = False,
    ) -> None:
        """
        Start the "Voodoo Max Hold" effect (2026-08-03) — pins voodoo power
        at its live cap (see read_voodoo_power_cap — confirmed by Jon to be
        the same value as the Soul Level meter, not a fixed constant) for
        total_seconds, re-asserting it every HEALTH_EFFECT_TICK_INTERVAL_SECONDS
        (normal Asson/voodoo casting would otherwise drain it back down
        between writes). Cancels any already-running hold first — only one
        is ever meant to be active at once, mirroring start_health_effect's
        overlap handling.

        permanent (2026-08-03, added for Trap/Bonus's TrapBonusMode support):
        when True, ticks forever instead of stopping after total_seconds —
        only ever stopped by a later Voodoo Drain (trigger_voodoo_drain) or
        a fresh hold superseding it. total_seconds is ignored in this case
        except as the tick-count fallback if the loop somehow needs one
        (it doesn't — see _run_voodoo_max_hold).
        """
        if self._active_voodoo_hold_task is not None:
            self._active_voodoo_hold_task.cancel()
            logger.info("[ShadowMan] Voodoo Max Hold: previous hold cancelled, starting a new one.")
        self._active_voodoo_hold_task = asyncio.create_task(
            self._run_voodoo_max_hold(total_seconds, permanent=permanent))

    async def _run_voodoo_max_hold(
        self, total_seconds: float = HEALTH_EFFECT_TOTAL_SECONDS,
        tick_interval: float = HEALTH_EFFECT_TICK_INTERVAL_SECONDS,
        permanent: bool = False,
    ) -> None:
        """
        Re-reads the live voodoo power cap (read_voodoo_power_cap — the
        Soul Level meter value, which can legitimately rise mid-session)
        and re-asserts it via set_voodoo_power() every tick_interval
        seconds for total_seconds — a plain memory write, no
        CreateRemoteThread involved, so this is lower-risk than the health
        effects even though it runs just as many ticks. Skips (not aborts)
        any tick where the player isn't confirmed in-game or the cap read
        fails, same convention as _run_health_effect. Only one hold is
        ever meant to run at once — see start_voodoo_max_hold, the only
        intended caller.

        permanent=True loops forever (`while True` over an unbounded tick
        counter, purely for logging) instead of stopping after a fixed
        tick count — the loop body itself (re-read cap, re-write) is
        identical either way, so a stray game-restart/process-not-found
        return still ends the task the same way it always did.
        """
        ticks = max(1, round(total_seconds / tick_interval))
        if permanent:
            logger.info("[ShadowMan] Voodoo Max Hold starting — PERMANENT, pinning at "
                        "the live Soul Level cap every tick until superseded.")
        else:
            logger.info(f"[ShadowMan] Voodoo Max Hold starting — {total_seconds:.0f}s total, "
                        f"{ticks} ticks, pinning at the live Soul Level cap each tick.")
        try:
            i = 0
            while permanent or i < ticks:
                await asyncio.sleep(tick_interval)
                pm, base = _get_process()
                if pm is None:
                    logger.info("[ShadowMan] Voodoo Max Hold: game not running, stopping.")
                    return
                if _read_is_at_title_screen(pm, base) is not False:
                    if self.debug_hold_logging:
                        logger.info(f"[ShadowMan] Voodoo Max Hold: not confirmed in-game, "
                                    f"tick {i + 1} skipped.")
                    i += 1
                    continue
                cap = read_voodoo_power_cap(pm, base)
                if cap is None:
                    if self.debug_hold_logging:
                        logger.info(f"[ShadowMan] Voodoo Max Hold: cap read failed, "
                                    f"tick {i + 1} skipped.")
                    i += 1
                    continue
                ok = set_voodoo_power(pm, base, cap)
                if self.debug_hold_logging:
                    logger.info(f"[ShadowMan] Voodoo Max Hold tick {i + 1}"
                                f"{'' if permanent else f'/{ticks}'}: "
                                f"{'ok' if ok else 'FAILED'} — cap={cap}")
                i += 1
        finally:
            if self._active_voodoo_hold_task is asyncio.current_task():
                self._active_voodoo_hold_task = None

    def trigger_ammo_drain(self, pm, base: int) -> bool:
        """
        'Ammo Drain' / 'No Ammo' trap entry point (2026-08-03) — cancels
        any currently-running Ammo Max Hold FIRST, then does the one-shot
        drain-to-0 write (apply_ammo_drain). Same reasoning as
        trigger_voodoo_drain — drain and hold are one "category" (ammo)
        and shouldn't coexist, most-recently-applied wins.
        """
        if self._active_ammo_hold_task is not None:
            self._active_ammo_hold_task.cancel()
            self._active_ammo_hold_task = None
            logger.info("[ShadowMan] Ammo Max Hold cancelled — superseded by Ammo Drain.")
        return apply_ammo_drain(pm, base)

    def start_ammo_max_hold(
        self, total_seconds: float = HEALTH_EFFECT_TOTAL_SECONDS,
        permanent: bool = False,
    ) -> None:
        """
        Start the "Ammo Max Hold" effect (2026-08-03) — fills all three
        tracked ammo pools (Shotgun, Violator, 9mm) to their known max and
        keeps re-asserting that every HEALTH_EFFECT_TICK_INTERVAL_SECONDS
        for total_seconds, so normal firing can't drain them back down.
        Unlike voodoo's cap, these maxes are fixed constants (see
        AMMO_RVAS_AND_CAPS), not something that needs a live read. Cancels
        any already-running hold first — only one is ever meant to be
        active at once, same shape as start_voodoo_max_hold.

        permanent (2026-08-03, added for Trap/Bonus's TrapBonusMode
        support): when True, ticks forever instead of stopping after
        total_seconds — only ever stopped by a later Ammo Drain
        (trigger_ammo_drain) or a fresh hold superseding it.
        """
        if self._active_ammo_hold_task is not None:
            self._active_ammo_hold_task.cancel()
            logger.info("[ShadowMan] Ammo Max Hold: previous hold cancelled, starting a new one.")
        self._active_ammo_hold_task = asyncio.create_task(
            self._run_ammo_max_hold(total_seconds, permanent=permanent))

    async def _run_ammo_max_hold(
        self, total_seconds: float = HEALTH_EFFECT_TOTAL_SECONDS,
        tick_interval: float = HEALTH_EFFECT_TICK_INTERVAL_SECONDS,
        permanent: bool = False,
    ) -> None:
        """
        Re-asserts all three ammo pools at their known max (apply_ammo_fill)
        every tick_interval seconds for total_seconds — plain memory
        writes, no CreateRemoteThread. Skips (not aborts) any tick where
        the player isn't confirmed in-game, same convention as
        _run_voodoo_max_hold. Only one hold is ever meant to run at once —
        see start_ammo_max_hold, the only intended caller.

        permanent=True loops forever instead of stopping after a fixed
        tick count — same shape as _run_voodoo_max_hold's own permanent
        support.
        """
        ticks = max(1, round(total_seconds / tick_interval))
        if permanent:
            logger.info("[ShadowMan] Ammo Max Hold starting — PERMANENT, pinning "
                        "Shotgun/Violator/9mm at max every tick until superseded.")
        else:
            logger.info(f"[ShadowMan] Ammo Max Hold starting — {total_seconds:.0f}s total, "
                        f"{ticks} ticks, pinning Shotgun/Violator/9mm at max each tick.")
        try:
            i = 0
            while permanent or i < ticks:
                await asyncio.sleep(tick_interval)
                pm, base = _get_process()
                if pm is None:
                    logger.info("[ShadowMan] Ammo Max Hold: game not running, stopping.")
                    return
                if _read_is_at_title_screen(pm, base) is not False:
                    if self.debug_hold_logging:
                        logger.info(f"[ShadowMan] Ammo Max Hold: not confirmed in-game, "
                                    f"tick {i + 1} skipped.")
                    i += 1
                    continue
                ok = apply_ammo_fill(pm, base, log=self.debug_hold_logging)
                if self.debug_hold_logging:
                    logger.info(f"[ShadowMan] Ammo Max Hold tick {i + 1}"
                                f"{'' if permanent else f'/{ticks}'}: "
                                f"{'ok' if ok else 'FAILED'}")
                i += 1
        finally:
            if self._active_ammo_hold_task is asyncio.current_task():
                self._active_ammo_hold_task = None

    async def _replay_all_received_items(self) -> None:
        """
        Re-inject every item received this session (used by /catchup after a
        game restart while the AP client remains connected).

        On a full client reconnect the AP server sends all items again from
        index 0, so _item_inject_loop handles that automatically.  This method
        covers only the mid-session case where the game process was restarted
        without disconnecting from AP.

        Gad Power tier is recalculated from the replay order so the cumulative
        tier injected into memory is always consistent.
        """
        if not self._received_items:
            logger.info("[ShadowMan] No items recorded this session — nothing to replay.")
            return

        pm, base = _get_process()
        if pm is None:
            logger.warning("[ShadowMan] Game not running — start the game first.")
            return

        # Same fail-closed check as _inject_item — covers /catchup being run
        # by hand while still at the title screen (e.g. right after
        # connecting, before loading a save).
        if _read_is_at_title_screen(pm, base) is not False:
            logger.info(
                "[ShadowMan] Not confirmed in-game yet (at the title "
                "screen, or state unclear) — not replaying yet. This will "
                "auto-retry once you're in a level.")
            return

        logger.info(
            f"[ShadowMan] Replaying {len(self._received_items)} item(s)...")

        # Reset stateful counters so the replay rebuilds the correct end state
        saved_gad = self.gad_powers_received
        self.gad_powers_received = 0

        failed = 0
        for _idx, item_name, source_player in self._received_items:
            method_info = AP_ITEM_INJECTION.get(item_name)
            if method_info is None and is_cadeaux_item(item_name):
                # Cadeaux Bundle Size (2026-07-27): see the identical fallback
                # in _inject_item() — AP_ITEM_INJECTION only has a literal
                # "Cadeaux" key, so every "Cadeaux Bundle x{N}" name needs
                # the same reroute here or replay silently skips it entirely
                # (worse than _inject_item's warn-and-return: this bare
                # `continue` wouldn't even log).
                method_info = AP_ITEM_INJECTION["Cadeaux"]
            if method_info is None:
                continue
            method, arg = method_info

            # Same self-found reasoning as _inject_item's live path (see its
            # comment block): a reloaded save file's persisted state already
            # reflects whatever native effect the vanilla pickup handler
            # applied at physical-collection time, for "soul" and
            # "give_item"/"pickup_sys" alike. "gad" is deliberately excluded
            # (tier recount needs every real Gad Temple item, self-found or
            # not), and so are "poigne_ability" (same not-confirmed-native
            # reasoning, harmless to re-inject) and "light_soul" (no native
            # pickup path of its own). "cadeaux" is ALSO excluded now
            # (Cadeaux Bundle Size fix, 2026-07-27+) — same reasoning as
            # _inject_item's identical exclusion: a self-found bundle
            # representative still only gets vanilla's flat native "+1" on
            # physical pickup, so replay must still re-run set_cadeaux_count
            # to correct it up to the item's real weight. Safe unconditionally
            # since set_cadeaux_count is an absolute recompute, not an
            # increment, and GiveItem(0x05) never touches the running count
            # (see CADEAUX_COUNT_RVA's comment block).
            # Same pre-mark as _inject_item: replay re-runs GiveItem, which
            # must not look like a local pickup to the flag watcher.
            if item_name in ITEM_FLAG_RVAS:
                self._live_inventory_states[item_name] = 1

            # "trap_bonus" (renamed from "secret_trap" 2026-08-03) stays in
            # this exclusion list so both self-found and foreign copies
            # alike reach the dispatch below (rather than taking the
            # ok=True shortcut just above) — but as of 2026-08-01 that
            # dispatch branch deliberately does NOT re-apply the trap/bonus
            # effect during replay at all (see its own comment) rather
            # than re-rolling it as this comment used to say. Kept in the
            # tuple purely so the two code paths stay consistent with each
            # other, not because replay still needs to do anything special
            # for it.
            # Unique Retractor Keys (2026-08-19) — same gap as _inject_item's
            # identical fix above: a self-found retractor key used to take
            # the plain "ok = True" shortcut just below without ever
            # touching the CF_CUSTOM flag, which meant /catchup replaying a
            # self-found key whose Hook B never fired (see _inject_item's
            # comment for the fuller story) could NEVER repair it — the one
            # path write_named_flag()'s own docstring calls out as the
            # retry mechanism was itself skipped for exactly the case that
            # needs retrying. Still skip the native GiveItem() re-injection
            # (vanilla already ran it; re-injecting double-counts the
            # stackable total), but always check/backstop the flag first.
            if source_player == self.slot and item_name in RETRACTOR_KEY_ITEM_TO_CF_INDEX:
                cf_index = RETRACTOR_KEY_ITEM_TO_CF_INDEX[item_name]
                pm, base = _get_process()
                if pm is None:
                    logger.warning("[ShadowMan] Game closed during replay — stopping.")
                    self.gad_powers_received = saved_gad
                    return
                if read_named_flag(pm, base, cf_index):
                    ok = True
                else:
                    ok = write_named_flag(pm, base, cf_index)
                    if ok:
                        logger.info(
                            f"[ShadowMan] [replay] {item_name} self-found but "
                            f"its CF_CUSTOM flag wasn't set natively — "
                            f"repaired via write_named_flag().")
                    else:
                        logger.warning(
                            f"[ShadowMan] [replay] {item_name} self-found but "
                            f"its CF_CUSTOM flag could not be confirmed or "
                            f"repaired.")
            elif source_player == self.slot and method not in ("gad", "poigne_ability", "light_soul", "cadeaux", "trap_bonus"):
                ok = True
            else:
                # Re-obtain process handle each item so a crash mid-replay is caught
                pm, base = _get_process()
                if pm is None:
                    logger.warning("[ShadowMan] Game closed during replay — stopping.")
                    self.gad_powers_received = saved_gad
                    return

                # Same crash-forensics marker as _inject_item (2026-08-01)
                # — see that call site's comment / _flush_log_handlers()'s
                # docstring for why this is logged AND flushed right here,
                # immediately before the dispatch that may run injected
                # code via CreateRemoteThread.
                logger.info(
                    f"[ShadowMan] >>> [replay] Applying item #{_idx} "
                    f"'{item_name}' (method={method}, arg={arg}) — if the "
                    f"game crashes now, THIS is the item that was being "
                    f"applied."
                )
                _flush_log_handlers()

                if method == "soul":
                    # Absolute target, not an increment (2026-07-26 fix) —
                    # same reasoning as _inject_item's "soul" branch. Makes
                    # replay itself idempotent: a save reload or reconnect
                    # that already carries forward previously-injected
                    # foreign souls no longer gets them re-added on top.
                    #
                    # apply_now=False unconditionally (2026-08-02) — same
                    # reasoning as "gad"/"poigne_ability" just below: this
                    # whole method is the "reapply after a mid-session game
                    # restart" path, never genuinely live, so
                    # _sync_soul_level's CreateRemoteThread fallback branch
                    # is always withheld here. The meter's plain-write fast
                    # path still always runs regardless.
                    ok = set_dark_soul_count(pm, base, self._received_count("Dark Soul"))
                    if ok:
                        _sync_soul_level(pm, base, self.soul_thresholds, apply_now=False)
                elif method == "give_item":
                    # Same delta check as _inject_item (2026-08-01) — see
                    # _item_already_owned_live's docstring. Retractor/
                    # Accumulator use the running-total counter check
                    # instead (2026-08-10) — see
                    # STACKABLE_GIVEITEM_COUNT_RVAS's comment. The 5 named
                    # Unique Retractor Keys items (2026-08-18) use the
                    # CF_CUSTOM00-04 flag check instead — see
                    # RETRACTOR_KEY_ITEM_TO_CF_INDEX's comment.
                    if item_name in RETRACTOR_KEY_ITEM_TO_CF_INDEX:
                        already_sufficient = read_named_flag(
                            pm, base, RETRACTOR_KEY_ITEM_TO_CF_INDEX[item_name])
                    elif item_name in STACKABLE_GIVEITEM_COUNT_RVAS:
                        already_sufficient = _stackable_giveitem_already_sufficient(
                            pm, base, item_name, self._received_count(item_name))
                    else:
                        already_sufficient = _item_already_owned_live(pm, base, item_name)
                    if already_sufficient:
                        ok = True
                    else:
                        ok = inject_give_item(pm, base, arg)
                        # Remote-grant flag set (2026-08-18) — same
                        # reasoning as _inject_item's identical block above;
                        # this replay path is exactly the kind of "future
                        # reconnect/backlog replay" a failed write here
                        # would be retried by, so keep both call sites
                        # consistent rather than relying solely on the
                        # other one.
                        if ok and item_name in RETRACTOR_KEY_ITEM_TO_CF_INDEX:
                            write_named_flag(
                                pm, base, RETRACTOR_KEY_ITEM_TO_CF_INDEX[item_name])
                elif method == "gad":
                    # apply_now=False (2026-08-01, Jon's call pending a
                    # Ghidra look at FUN_140459d50) — this whole method is
                    # the "reapply after a mid-session game restart" path,
                    # never genuinely live, so the native live-apply call
                    # is always withheld here regardless of timing. Flags
                    # are still written correctly; the next level (re)entry
                    # picks them up via the existing resync sweep.
                    #
                    # Absolute recompute from _received_count("Gad Power")
                    # (2026-08-02) instead of incrementing — same fix and
                    # reasoning as _inject_item's "gad" branch above (Jon's
                    # "set to 0, then re-apply" suggestion). Idempotent
                    # regardless of how many times this branch fires during
                    # the replay loop, matching "soul"'s already-established
                    # pattern a few branches up.
                    self.gad_powers_received = min(self._received_count("Gad Power"), 3)
                    ok = inject_gad_power(pm, base, self.gad_powers_received,
                                           apply_now=False)
                elif method == "poigne_ability":
                    # Same reasoning as "gad" just above.
                    self.poigne_ability_received = True
                    ok = inject_poigne_ability(pm, base, apply_now=False)
                elif method == "pickup_sys":
                    ok = inject_pickup_system(pm, base, arg)
                elif method == "light_soul":
                    # Same reasoning as _inject_item's "light_soul" branch
                    # (2026-08-06) — mark BEFORE the call, not after, to
                    # close the same narrow poll-loop race.
                    self._light_soul_injected_this_session = True
                    ok = inject_light_soul(pm, base)
                elif method == "cadeaux":
                    ok_flag  = inject_give_item(pm, base, arg)
                    # Absolute target, not an increment (2026-07-26 fix) —
                    # same reasoning as "soul" above. Weighted total per
                    # Cadeaux Bundle Size (2026-07-27) — see
                    # _received_cadeaux_total().
                    ok_count = set_cadeaux_count(pm, base, self._received_cadeaux_total())
                    ok = ok_flag and ok_count
                elif method == "trap_bonus":
                    # Renamed from "secret_trap" (2026-08-03). RESTORED to
                    # the replay path 2026-08-02 (Jon: "now that we know
                    # secret trap wasn't the issue.. we can apply the last
                    # secret trap on the list?") once Secret Trap was
                    # conclusively cleared as a suspect in the ntdll
                    # heap-corruption investigation (see CLAUDE.md) — the
                    # real causes were inject_give_item's unvalidated
                    # vtable pointer and the EXE-patch reapply corruption
                    # bug, both fixed elsewhere. The secret category's own
                    # apply has never been anything but plain
                    # write_cvar_bool() calls (zero CreateRemoteThread), so
                    # there's no safety reason to skip it here; health/
                    # voodoo/ammo route through their own gated primitives.
                    #
                    # Calls the same _apply_trap_bonus(idx, arg) the live
                    # path uses, relying on its existing debounce/supersede
                    # mechanism to collapse a replay batch containing
                    # several Trap/Bonus items down to just the last one.
                    # `arg` (2026-08-05) is the concrete effect key baked
                    # into this item's own name at generation time.
                    # The location check itself was always credited via
                    # LocationChecks regardless — only the in-game effect
                    # was ever affected.
                    await self._apply_trap_bonus(_idx, arg)
                    ok = True
                else:
                    ok = True   # unknown method — skip silently

            if not ok:
                failed += 1
            # Pacing gap, not just a bare event-loop yield (2026-08-01,
            # see _item_inject_loop's identical fix and its own comment) --
            # this loop calls the same inject_give_item()
            # (CreateRemoteThread-based) for "give_item"/"cadeaux" items
            # and previously only did asyncio.sleep(0) between entries.
            await asyncio.sleep(ITEM_INJECT_PACING_SECONDS)

        logger.info(
            f"[ShadowMan] Replay complete — "
            f"{len(self._received_items) - failed} OK, {failed} failed."
        )

    def _log_received_item(self, item_name: str, sender: str) -> None:
        if not self.save_dir:
            return
        log_path = self.save_dir / "received_items.log"
        try:
            with log_path.open("a", encoding="utf-8") as f:
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{ts}] {item_name}  (from {sender})\n")
        except OSError as exc:
            logger.warning(f"[ShadowMan] Log write failed: {exc}")

    # ── "Shadow Man" GUI tab data helpers (2026-07-25) ─────────────────────────
    #
    # Pure data methods the tab widgets call on their own Clock timer — kept
    # here (not on the widgets) so they're usable/testable without kivy
    # imported, and so they read the same ctx state _cmd_status already
    # trusts rather than a second copy of it.

    def _on_ut_regions_updated(self, regions: List[str]) -> bool:
        """Universal Tracker region callback — see __init__'s
        set_region_callback() call. Just mirrors UT's latest in-logic
        region list; the tab reads _ut_in_logic_regions on its own timer
        rather than reacting to this directly."""
        self._ut_in_logic_regions = set(regions)
        return True

    def _on_ut_locations_updated(self, locations: List[str]) -> bool:
        """Universal Tracker location callback (2026-07-25, see __init__'s
        set_callback() call and tracker.apworld's docs/client-integration.md
        — "Adding In-Logic Callbacks"). UT calls this with the current
        in-logic LOCATION NAMES (TrackerCore.updateTracker()'s
        in_logic_locations field, e.g. temp_loc.name strings) whenever it
        recomputes reachability. Powers the GUI tab's per-level location
        browser's in-logic marker; mirrored into a set for O(1) lookup
        rather than reacting to this directly."""
        self._ut_in_logic_locations = set(locations)
        return True

    def _engine_block_reachable(self) -> Optional[List[Tuple[str, bool]]]:
        """[(region_name, reachable), ...] for the 5 ENGINE_BLOCK_REGIONS,
        per Universal Tracker's latest logic pass — access_rules.py's
        pistons() requires state.can_reach() on all five. Returns None
        (not merely an empty/all-False list) when UT isn't installed, so
        the tab can tell "not reachable yet" apart from "can't tell"."""
        if not tracker_loaded:
            return None
        return [(name, name in self._ut_in_logic_regions) for name in ENGINE_BLOCK_REGIONS]

    def _has_jacks_schematic(self) -> bool:
        """Whether we've received our own Jacks Schematic yet — only
        relevant to Go Mode when this seed's piston_combos option is on
        (see access_rules.py's pistons(), require_schematic). Checked
        against items_received (the standard AP list, always populated
        regardless of our own injection pipeline) rather than
        _received_items, so this is correct even before the game process
        is attached."""
        if not self.item_names:
            return False
        return any(
            self.item_names.lookup_in_game(ni.item) == "Jacks Schematic"
            for ni in self.items_received
        )

    def _go_mode_prerequisites(self) -> List[Tuple[str, bool, str]]:
        """[(label, satisfied, detail), ...] — the concrete item / soul-
        level requirements that gate Engine Block access end-to-end,
        spelled out individually rather than folded into a single
        reachable y/n per region the way _engine_block_reachable() does
        (2026-07-25, requested after the per-level location browser).

        Traced from regions.py's actual entrance rules: Deadside Marrow
        -> Asylum Gateways -> [eng_key] -> Cathedral -> [SL2 + 5
        Retractors] -> each liveside region -> [Night (all 3 Eclipser
        parts) + a region-specific extra] -> that region's Engine Block
        sub-area. Night's per-region extras (see regions.py's
        LIVESIDE_PRISON/QUEENS/SALVAGE entrance rules): Prison needs
        Prison Key Card, Queens needs Poigne, Salvage needs Gad Power x3
        (gad3_swim); London/Florida need nothing beyond Night itself.

        Jacks Schematic (2026-07-26, Jon's clarification): when this
        seed's piston_combos option is on, the schematic isn't only a
        Go Mode/Victory requirement (access_rules.py's pistons()) — it
        also gates the actual Engine Block completions themselves (the
        as4dkeng piston-combo barrels living inside each of the 5 Engine
        Block sub-areas, per pistons()'s own docstring: "every location
        gated behind PISTONS ... requires holding it first"). Only
        included here (conditionally) when piston_combos_on, so this
        list's length can change between updates once slot_data arrives
        — see ShadowManGoModeLayout.update()'s rebuild-on-count-change
        handling below, same pattern as the level/location panels.

        Pure item-count / soul-threshold checks against items_received
        and the same live soul read the Overview panel does — unlike
        _engine_block_reachable(), this needs no Universal Tracker and
        works identically whether or not UT is installed."""
        def _has(name: str) -> bool:
            return any(self.item_names.lookup_in_game(ni.item) == name
                        for ni in self.items_received)

        def _count(name: str) -> int:
            return sum(1 for ni in self.items_received
                        if self.item_names.lookup_in_game(ni.item) == name)

        eclipser_names = ("La Lune", "La Lame", "Le Soleil")
        eclipser_have = [n for n in eclipser_names if _has(n)]
        retractor_n = _count("Retractor")
        gad_n = _count("Gad Power")

        pm, base = _get_process()
        live_soul = None
        if pm is not None:
            try:
                live_soul = pm.read_int(base + SOUL_COUNT_RVA)
            except Exception:
                pass
        soul_count = live_soul if live_soul is not None else self.last_soul_count
        sl = (_soul_level_for_count(soul_count, self.soul_thresholds)
              if isinstance(soul_count, int) else None)
        sl_ok = sl is not None and sl >= 2

        eclipser_detail = f"{len(eclipser_have)}/3"
        if 0 < len(eclipser_have) < 3:
            missing = ", ".join(n for n in eclipser_names if n not in eclipser_have)
            eclipser_detail += f" (missing: {missing})"

        prereqs = [
            ("Engineers Key",        _has("Engineers Key"), "held" if _has("Engineers Key") else "not held"),
            ("Soul Level 2",         sl_ok,                  f"SL {sl if sl is not None else '—'}"),
        ]
        # Unique Retractor Keys (2026-08-18): named-key mode has no fungible
        # "Retractor" item at all (items.py's create_items() branch), so
        # retractor_n (a count of literal "Retractor") would always read 0/5
        # here and misreport "not held" even with all 5 named keys in hand.
        # Show each region's own key instead — mirrors how Prison Key Card/
        # Poigne/Gad Power are already shown individually below.
        if self.unique_retractor_keys_on:
            for _item_name in RETRACTOR_KEY_ITEM_NAMES.values():
                _held = _has(_item_name)
                prereqs.append((_item_name, _held, "held" if _held else "not held"))
        else:
            prereqs.append(("Retractors", retractor_n >= 5, f"{retractor_n}/5"))
        prereqs += [
            ("Eclipser (Night)",     len(eclipser_have) == 3, eclipser_detail),
            ("Prison Key Card (Prison)", _has("Prison Key Card"), "held" if _has("Prison Key Card") else "not held"),
            ("Poigne (Queens)",      _has("Poigne"),         "held" if _has("Poigne") else "not held"),
            ("Gad Power x3 (Salvage)", gad_n >= 3,           f"{gad_n}/3"),
        ]
        if self.piston_combos_on:
            schematic_ok = self._has_jacks_schematic()
            prereqs.append((
                "Jacks Schematic (Piston Combos)", schematic_ok,
                "held" if schematic_ok else "not held — also gates Engine Block piston barrels",
            ))
        return prereqs

    def _level_completion_stats(self) -> List[Tuple[str, str, int, int]]:
        """[(level_id, display_name, checked_count, total_count), ...] for
        every level with at least one AP-checkable location this seed,
        ordered per LEVEL_ORDER. Uses server_locations/checked_locations
        (standard CommonContext state — server-authoritative, populated
        whether or not Universal Tracker is installed) against
        _ap_id_to_level. level_id is included (2026-07-25) so the GUI tab
        can wire a click on a level row straight into
        _locations_for_level(level_id) without a separate name->id lookup."""
        totals: Dict[str, int] = {}
        done:   Dict[str, int] = {}
        for ap_id, level_id in self._ap_id_to_level.items():
            if ap_id not in self.server_locations:
                continue
            totals[level_id] = totals.get(level_id, 0) + 1
            if ap_id in self.checked_locations:
                done[level_id] = done.get(level_id, 0) + 1
        order = {lvl: i for i, lvl in enumerate(LEVEL_ORDER)}
        levels = sorted(totals.keys(), key=lambda lvl: order.get(lvl, len(LEVEL_ORDER)))
        return [(lvl, _level_display_name(lvl), done.get(lvl, 0), totals[lvl]) for lvl in levels]

    def _locations_for_level(self, level_id: str) -> List[Tuple[str, int, bool, Optional[bool]]]:
        """[(location_name, ap_id, checked, in_logic), ...] for every
        AP-checkable location this seed placed in level_id — powers the
        GUI tab's per-level location browser.

        Name resolution (2026-07-26): prefers FRIENDLY_NAMES[loc_key]
        (this world's own generation-time naming, imported directly —
        see this file's FRIENDLY_NAMES import comment) over
        self.location_names.lookup_in_game(ap_id) (the network
        datapackage, which can serve a stale locally-cached copy from
        before a naming change). Falls back to the datapackage name only
        if _ap_id_to_loc_key doesn't have this ap_id for some reason
        (e.g. slot_data from an older client version).

        checked comes from the same checked_locations set
        _level_completion_stats() uses. in_logic is None when Universal
        Tracker isn't installed (same "can't tell" vs. "not yet"
        convention as _engine_block_reachable), else looked up against
        _ut_in_logic_locations (see _on_ut_locations_updated) by the
        location's own name — UT's callback reports names, not ids, and
        (since UT re-derives names via its own local regen rather than
        the cached datapackage) should now actually match the
        FRIENDLY_NAMES-sourced name above rather than silently never
        matching a stale hex one.

        Sorted remaining/open checks first (2026-07-26, "more relevant
        about remaining/open checks") — reachable-and-unchecked, then
        unchecked with unknown logic (no UT), then unchecked-but-not-yet-
        reachable, then already-checked last; alphabetical within each
        group. The point is to put what's actually actionable right now
        at the top instead of burying it alphabetically among locations
        already done."""
        out: List[Tuple[str, int, bool, Optional[bool]]] = []
        for ap_id, lvl in self._ap_id_to_level.items():
            if lvl != level_id or ap_id not in self.server_locations:
                continue
            loc_key = self._ap_id_to_loc_key.get(ap_id)
            name = FRIENDLY_NAMES.get(loc_key) if loc_key else None
            if name is None:
                name = self.location_names.lookup_in_game(ap_id)
            checked = ap_id in self.checked_locations
            in_logic = (name in self._ut_in_logic_locations) if tracker_loaded else None
            out.append((name, ap_id, checked, in_logic))

        def _sort_key(entry: Tuple[str, int, bool, Optional[bool]]):
            name, _ap_id, checked, in_logic = entry
            if checked:
                rank = 3
            elif in_logic is True:
                rank = 0
            elif in_logic is None:
                rank = 1
            else:  # in_logic is False -- known not yet reachable
                rank = 2
            return (rank, name)

        out.sort(key=_sort_key)
        return out

    # ── GUI ───────────────────────────────────────────────────────────────────

    def make_gui(self):
        # super().make_gui() resolves to TrackerGameContext's manager class
        # (which already carries the "Tracker Page" tab) when UT is
        # installed, or plain kvui.GameManager otherwise — either way we
        # just subclass whatever comes back and add our own settings on
        # top, per UT's docs/client-integration.md. run_gui() itself is
        # inherited unchanged from CommonContext/TrackerGameContext.
        ui = super().make_gui()

        # kivy imports are deferred to here (like TrackerGameContext's own
        # make_gui() does) rather than module level, so this file still
        # imports cleanly in --nogui/CLI-only mode. make_gui() only ever
        # runs when gui_enabled, at which point kvui has already imported
        # kivy via super().make_gui() above.
        from kivy.clock import Clock
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.uix.progressbar import ProgressBar
        from kivy.uix.scrollview import ScrollView
        from kivy.uix.behaviors import ButtonBehavior

        ctx = self

        class _ClickableRow(ButtonBehavior, BoxLayout):
            """Plain BoxLayout row that's also pressable — used for level
            rows in ShadowManLevelProgressLayout (2026-07-25) so clicking a
            level populates ShadowManLocationsLayout below with that
            level's locations. No visual chrome of its own; ButtonBehavior
            just adds on_press/on_release."""
            pass

        def _stat_label(text: str) -> "Label":
            lbl = Label(
                text=text, markup=True, size_hint_y=None, height="22dp",
                halign="left", valign="middle",
            )
            lbl.bind(size=lambda l, s: setattr(l, "text_size", s))
            return lbl

        class ShadowManOverviewLayout(BoxLayout):
            """Header stats — connection/level/health plus the same live
            soul/gad/cadeaux reads _cmd_status uses (see _get_process()'s
            own docstring: a 5th caller on a GUI timer is exactly the
            caching pattern it was built for)."""

            def __init__(self) -> None:
                super().__init__(orientation="vertical", size_hint_y=None, spacing="2dp")
                self.bind(minimum_height=self.setter("height"))
                self.add_widget(Label(
                    text="[b]Shadow Man Remastered[/b]", markup=True, font_size="20dp",
                    size_hint_y=None, height="30dp", halign="left", valign="middle",
                ))
                self.title_lbl = self.children[0]
                self.title_lbl.bind(size=lambda l, s: setattr(l, "text_size", s))

                # Victory banner (2026-07-26) — mirrors ctx.finished_game,
                # CommonContext's standard "goal already reported" flag, set
                # True by _goal_watcher_loop() the moment Legion's defeat is
                # detected and CLIENT_GOAL is sent. Empty/no height when not
                # finished so it doesn't reserve dead space the rest of the
                # session.
                self.victory_lbl = Label(
                    text="", markup=True, font_size="16dp",
                    size_hint_y=None, height=0, halign="left", valign="middle",
                )
                self.victory_lbl.bind(size=lambda l, s: setattr(l, "text_size", s))
                self.add_widget(self.victory_lbl)

                self.level_lbl    = _stat_label("Level: —")
                self.locs_lbl     = _stat_label("Locations: 0/0")
                self.souls_lbl    = _stat_label("Dark Souls: 0  (SL —)")
                # Souls-until-next-SL (2026-07-28, Jon's request — "showing
                # soul threshold new values would be helpful ... or
                # actually souls required till next SL"). This seed's
                # thresholds can be randomized (soul_threshold_mode), so a
                # player has no static reference for how close they are —
                # this reads ctx.soul_thresholds (the real, per-seed dict,
                # set from slot_data on Connected) the same way souls_lbl's
                # own SL number already does via _soul_level_for_count().
                self.souls_next_lbl = _stat_label("")
                self.cadeaux_lbl  = _stat_label("Cadeaux: —")
                self.gad_lbl      = _stat_label("Gad Powers: Temple 0/3, Poigne: no")
                self.health_lbl   = _stat_label("Health: —")
                self.deathlink_lbl = _stat_label("Death Link: off")
                for w in (self.level_lbl, self.locs_lbl, self.souls_lbl,
                          self.souls_next_lbl, self.cadeaux_lbl, self.gad_lbl,
                          self.health_lbl, self.deathlink_lbl):
                    self.add_widget(w)

            def update(self) -> None:
                pm, base = _get_process()
                live_soul = live_gad = live_cad = live_health = None
                if pm is not None:
                    try:
                        live_soul = pm.read_int(base + SOUL_COUNT_RVA)
                    except Exception:
                        pass
                    try:
                        live_gad = pm.read_int(base + GAD_LEVEL_RVA)
                    except Exception:
                        pass
                    try:
                        live_cad = pm.read_int(base + CADEAUX_COUNT_RVA)
                    except Exception:
                        pass
                    live_health = read_current_health(pm, base)

                soul_display = live_soul if live_soul is not None else ctx.last_soul_count
                # Raw value matches the "givegad" console encoding (0-7,
                # see _decode_gad_level) — decoded below for a clean
                # display rather than shown as a bare 0-7 number.
                gad_level_raw = live_gad if live_gad is not None else (
                    ctx.gad_powers_received + (4 if ctx.poigne_ability_received else 0))
                gad_temple_tier, gad_has_poigne = _decode_gad_level(gad_level_raw)
                sl_display = (
                    _soul_level_for_count(soul_display, ctx.soul_thresholds)
                    if isinstance(soul_display, int) else None
                )

                if ctx.finished_game:
                    self.victory_lbl.height = "26dp"
                    self.victory_lbl.text = (
                        "[b][color=00FA9A]GAME COMPLETE — Legion Defeated. "
                        "Go Mode reached.[/color][/b]"
                    )
                else:
                    self.victory_lbl.height = 0
                    self.victory_lbl.text = ""

                self.level_lbl.text = f"Level: [color=00FA9A]{ctx.current_level or '(unknown)'}[/color]"
                self.locs_lbl.text = (
                    f"Locations: [color=00FA9A]{len(ctx.checked_locations)}[/color] / "
                    f"[color=00FA9A]{len(ctx.server_locations)}[/color]"
                )
                self.souls_lbl.text = (
                    f"Dark Souls: [color=00FA9A]{soul_display}[/color]  "
                    f"(SL [color=00FA9A]{sl_display if sl_display is not None else '—'}[/color])"
                )
                if sl_display is None:
                    self.souls_next_lbl.text = ""
                elif sl_display >= 10:
                    self.souls_next_lbl.text = "  [color=00FA9A]SL 10 (max)[/color]"
                else:
                    _next_sl = sl_display + 1
                    _next_req = ctx.soul_thresholds.get(_next_sl, VANILLA_SOUL_THRESHOLDS[_next_sl])
                    _needed = max(0, _next_req - soul_display)
                    self.souls_next_lbl.text = (
                        f"  [color=AAAAAA]{_needed} more for SL {_next_sl} "
                        f"({_next_req} total)[/color]"
                    )
                self.cadeaux_lbl.text = (
                    f"Cadeaux: [color=00FA9A]{live_cad if live_cad is not None else '—'}[/color]"
                )
                self.gad_lbl.text = (
                    f"Gad Powers: Temple [color=00FA9A]{gad_temple_tier}[/color]/3, "
                    f"Poigne: [color=00FA9A]{'yes' if gad_has_poigne else 'no'}[/color]"
                )
                self.health_lbl.text = (
                    f"Health: [color=00FA9A]{live_health if live_health is not None else '—'}[/color]"
                )
                if ctx.death_link_enabled:
                    self.deathlink_lbl.text = (
                        f"Death Link: [color=FF6B6B]on[/color]  "
                        f"(our deaths since last send: {ctx._own_death_count}/{ctx.death_link_threshold})"
                    )
                else:
                    self.deathlink_lbl.text = "Death Link: off"

        class ShadowManLevelProgressLayout(BoxLayout):
            """Per-level completion bars — creative liberty vs. Peggle's
            layout, since Shadow Man doesn't have per-level "unlock" items
            the way Peggle does; completion % is the natural equivalent.

            Rows are clickable (2026-07-25, via _ClickableRow) — selecting
            one calls the on_select callback (wired to
            ShadowManLocationsLayout.select_level in ShadowManContent) to
            populate the location browser below with that level's
            AP-checkable locations."""

            def __init__(self, on_select) -> None:
                super().__init__(orientation="vertical", size_hint_y=None, spacing="4dp")
                self.bind(minimum_height=self.setter("height"))
                self.add_widget(Label(
                    text="[b]Level Completion[/b]  "
                         "[color=888888](click a level to see its locations)[/color]",
                    markup=True, font_size="18dp",
                    size_hint_y=None, height="26dp", halign="left", valign="middle",
                ))
                self.children[0].bind(size=lambda l, s: setattr(l, "text_size", s))
                self.on_select = on_select
                self.selected_level_id: Optional[str] = None
                # display_name -> (row, name_lbl, pct_lbl, bar, level_id)
                self.rows: Dict[str, Tuple["_ClickableRow", "Label", "Label", "ProgressBar", str]] = {}

            def _select(self, level_id: str, display_name: str) -> None:
                # Toggle (2026-07-28, Jon: "clicking on a level again
                # should minimize its locations, because scrolling is
                # really slow on that page") — ShadowManLocationsLayout
                # renders one Label per location with no virtualization,
                # so a big level's list makes the whole tab slow to
                # scroll past. Clicking the already-selected level again
                # collapses it back down instead of just re-rendering the
                # same list a second time.
                if level_id == self.selected_level_id:
                    self.selected_level_id = None
                    self.on_select(None, "")
                else:
                    self.selected_level_id = level_id
                    self.on_select(level_id, display_name)

            def update(self) -> None:
                stats = ctx._level_completion_stats()
                for level_id, name, done, total in stats:
                    pct = int(round(100 * done / total)) if total else 0
                    if name not in self.rows:
                        row = _ClickableRow(orientation="horizontal", size_hint_y=None,
                                             height="20dp", spacing="8dp")
                        lbl = Label(text=name, markup=True, size_hint_x=0.55, size_hint_y=None,
                                    height="20dp", halign="left", valign="middle",
                                    shorten=True)
                        lbl.bind(size=lambda l, s: setattr(l, "text_size", s))
                        bar = ProgressBar(max=100, value=pct, size_hint_x=0.30)
                        pct_lbl = Label(text=f"{done}/{total} ({pct}%)", size_hint_x=0.15,
                                        size_hint_y=None, height="20dp",
                                        halign="right", valign="middle")
                        pct_lbl.bind(size=lambda l, s: setattr(l, "text_size", s))
                        row.add_widget(lbl)
                        row.add_widget(bar)
                        row.add_widget(pct_lbl)
                        row.bind(on_release=lambda _row, lvl=level_id, disp=name:
                                  self._select(lvl, disp))
                        self.add_widget(row)
                        self.rows[name] = (row, lbl, pct_lbl, bar, level_id)
                    else:
                        _row, lbl, pct_lbl, bar, _lvl = self.rows[name]
                        bar.value = pct
                        pct_lbl.text = f"{done}/{total} ({pct}%)"
                    row, lbl, pct_lbl, bar, lvl = self.rows[name]
                    selected = lvl == self.selected_level_id
                    lbl.text = f"[color={'00FA9A' if selected else 'FFFFFF'}]{name}[/color]"

        class ShadowManLocationsLayout(BoxLayout):
            """Per-location breakdown for one selected level (2026-07-25) —
            click a row in Level Completion above to populate this. Lists
            every AP-checkable location this seed placed in that level
            (see _locations_for_level), with checked/unchecked status and,
            only when Universal Tracker is installed, an in-logic marker."""

            def __init__(self) -> None:
                super().__init__(orientation="vertical", size_hint_y=None, spacing="2dp")
                self.bind(minimum_height=self.setter("height"))
                self.header_lbl = Label(
                    text="[b]Locations[/b]  [color=888888](click a level above)[/color]",
                    markup=True, font_size="18dp", size_hint_y=None, height="26dp",
                    halign="left", valign="middle",
                )
                self.header_lbl.bind(size=lambda l, s: setattr(l, "text_size", s))
                self.add_widget(self.header_lbl)
                self.selected_level: Optional[str] = None
                self.display_name: str = ""
                self.row_labels: List["Label"] = []

            def select_level(self, level_id: Optional[str], display_name: str) -> None:
                self.selected_level = level_id
                self.display_name = display_name
                if level_id is None:
                    # Collapse (2026-07-28, see ShadowManLevelProgressLayout
                    # ._select's toggle comment) — clear the rendered rows
                    # rather than just leaving self.selected_level at None,
                    # since update() below only rebuilds rows on a count
                    # change and would otherwise leave the old level's
                    # (possibly long) list sitting on screen untouched.
                    for lbl in self.row_labels:
                        self.remove_widget(lbl)
                    self.row_labels = []
                    self.header_lbl.text = (
                        "[b]Locations[/b]  [color=888888](click a level above)[/color]"
                    )
                    return
                self.update()

            def update(self) -> None:
                if self.selected_level is None:
                    return
                locs = ctx._locations_for_level(self.selected_level)
                remaining = sum(1 for _n, _a, checked, _l in locs if not checked)
                self.header_lbl.text = (
                    f"[b]Locations — {self.display_name}[/b]  "
                    f"([color=FFA500]{remaining}[/color]/{len(locs)} remaining)"
                )
                # Rebuild rows only when the count changes (location sets
                # are fixed per seed/level — only checked/in_logic churn,
                # and the resulting re-sort, mid-session) rather than every
                # 0.5s tick.
                if len(self.row_labels) != len(locs):
                    for lbl in self.row_labels:
                        self.remove_widget(lbl)
                    self.row_labels = []
                    for _ in locs:
                        lbl = _stat_label("")
                        self.add_widget(lbl)
                        self.row_labels.append(lbl)
                # locs is sorted remaining/open-first (see
                # _locations_for_level) so already-checked locations sink
                # to the bottom as they're completed, keeping whatever's
                # still actionable at the top of the list.
                for lbl, (name, ap_id, checked, in_logic) in zip(self.row_labels, locs):
                    if checked:
                        mark, color = "done", "00FA9A"
                    elif in_logic is True:
                        mark, color = "in logic", "FFA500"
                    elif in_logic is False:
                        mark, color = "not yet reachable", "666666"
                    else:
                        mark, color = "unchecked", "AAAAAA"
                    lbl.text = f"  [color={color}]{name} — {mark}[/color]"

        class ShadowManGoModeLayout(BoxLayout):
            """"Proximity to Go Mode" — Go Mode itself is exactly
            state.has("Victory", player), which reduces (per the "Defeat
            Legion" event's rule in __init__.py) to holding Jacks
            Schematic (if piston_combos is on) plus reaching all 5 Engine
            Block regions. The Prerequisites section (2026-07-26) spells
            out the concrete items/soul-level that actually gate that
            reachability (Engineers Key, SL2, 5 Retractors, the 3
            Eclipser parts, plus each liveside region's own extra item —
            see ctx._go_mode_prerequisites()) — pure item-count checks,
            so unlike the per-region breakdown below it works with or
            without Universal Tracker installed. The per-region
            breakdown itself still needs UT (logic engine), so that part
            alone degrades to a fallback message without it."""

            def __init__(self) -> None:
                super().__init__(orientation="vertical", size_hint_y=None, spacing="2dp")
                self.bind(minimum_height=self.setter("height"))
                self.add_widget(Label(
                    text="[b]Proximity to Go Mode[/b]", markup=True, font_size="18dp",
                    size_hint_y=None, height="26dp", halign="left", valign="middle",
                ))
                self.children[0].bind(size=lambda l, s: setattr(l, "text_size", s))
                self.summary_lbl = _stat_label("—")
                self.add_widget(self.summary_lbl)

                self.add_widget(Label(
                    text="[b]Prerequisites[/b]", markup=True, font_size="15dp",
                    size_hint_y=None, height="22dp", halign="left", valign="middle",
                ))
                self.children[0].bind(size=lambda l, s: setattr(l, "text_size", s))

                # BUG FIX (2026-08-19, Jon: "why does it seem like gad level
                # 3 and poigne dropped off go-mode requirements on ap_gui
                # tracker?" / "and prison key card") — self.prereq_lbls used
                # to be a plain list of Labels add_widget()'d straight into
                # this layout, permanently sized right here from whatever
                # ctx._go_mode_prerequisites() returned at __init__ time.
                # But slot_data hasn't necessarily arrived yet when __init__
                # runs, so unique_retractor_keys_on is still its default
                # (False) and _go_mode_prerequisites() returns the flat
                # single "Retractors" row rather than the 5 named per-region
                # rows — self.prereq_lbls got permanently sized for the FLAT
                # case (7 rows: Engineers Key, Soul Level 2, Retractors,
                # Eclipser (Night), Prison Key Card, Poigne, Gad Power x3).
                # update() below re-fetches prereqs fresh every 0.5s
                # (correctly reflecting unique_retractor_keys_on once
                # slot_data resolves — 11 rows once the flat Retractors row
                # becomes 5 named ones), but was zip()ing that longer fresh
                # list against this stale fixed-size widget list — zip()
                # silently truncates to the shorter side, so everything past
                # the original 7 (Eclipser (Night), Prison Key Card, Poigne,
                # Gad Power x3) never got a widget and simply stopped
                # rendering the moment unique_retractor_keys_on flipped true.
                # Fix: give prereq rows their own dedicated sub-layout
                # (prereq_box) so they can be rebuilt on a count change
                # without disturbing this layout's other widgets' positions
                # (jacks_lbl / engine_header_lbl / region_lbls all come after
                # them and must stay put) — same "rebuild rows only when the
                # count changes" pattern ShadowManLocationsLayout.update()
                # below already uses for per-location rows.
                self.prereq_box = BoxLayout(orientation="vertical", size_hint_y=None, spacing="2dp")
                self.prereq_box.bind(minimum_height=self.prereq_box.setter("height"))
                self.add_widget(self.prereq_box)
                self.prereq_lbls: List["Label"] = []
                _init_prereqs = [p for p in ctx._go_mode_prerequisites()
                                  if p[0] != "Jacks Schematic (Piston Combos)"]
                self._rebuild_prereq_rows(_init_prereqs)

                # Jacks Schematic (2026-07-28, Jon: "please also move jacks
                # schematics with the prerequisites") — a dedicated,
                # always-created slot right here in widget-build order, so
                # it's visually grouped with the rest of Prerequisites
                # instead of landing after the Engine Block section below
                # (the previous "append new rows at whatever index the
                # layout is currently at" approach put it there since it
                # only became known -- piston_combos_on -- after slot_data
                # arrived, well after this section's widgets already
                # existed). Built unconditionally, same collapse-when-
                # inapplicable pattern as ShadowManOverviewLayout's
                # victory_lbl: height=0/empty text when this seed doesn't
                # have piston_combos on, real content otherwise.
                self.jacks_lbl = _stat_label("")
                self.jacks_lbl.height = 0
                self.add_widget(self.jacks_lbl)

                # (2026-07-28, Jon: "we can show 0/5 and track engine
                # blocks that are beatable") — header now doubles as a
                # summary count, same style as summary_lbl above it.
                # Kept as an instance attr (unlike the other section
                # headers here) so update() can rewrite its text.
                self.engine_header_lbl = Label(
                    text="[b]Engine Block regions[/b]", markup=True, font_size="15dp",
                    size_hint_y=None, height="22dp", halign="left", valign="middle",
                )
                self.engine_header_lbl.bind(size=lambda l, s: setattr(l, "text_size", s))
                self.add_widget(self.engine_header_lbl)
                self.region_lbls: List["Label"] = []
                for name in ENGINE_BLOCK_REGIONS:
                    lbl = _stat_label(f"  {name}: —")
                    self.region_lbls.append(lbl)
                    self.add_widget(lbl)
                # No separate schematic_lbl here anymore (2026-07-28, Jon:
                # "jack schematics is showing twice now.. it should just
                # show up next to prerequisites") — Jacks Schematic is
                # already its own row in the Prerequisites list above
                # (added 2026-07-26) whenever piston_combos_on; this
                # section used to ALSO show it a second time below the
                # per-region list, which was pure duplication once that
                # Prerequisites row existed.

            def _rebuild_prereq_rows(self, base_prereqs) -> None:
                """(Re)builds self.prereq_lbls, inside self.prereq_box, to
                match len(base_prereqs). Called once from __init__ and again
                from update() whenever the prereq count changes — in
                practice, once slot_data resolves unique_retractor_keys_on
                sometime after __init__ already ran with the default-False
                (flat "Retractors" row) shape. See the BUG FIX comment above
                self.prereq_box's construction for the full story."""
                for lbl in self.prereq_lbls:
                    self.prereq_box.remove_widget(lbl)
                self.prereq_lbls = []
                for label, _ok, _detail in base_prereqs:
                    lbl = _stat_label(f"  {label}: —")
                    self.prereq_lbls.append(lbl)
                    self.prereq_box.add_widget(lbl)

            def update(self) -> None:
                prereqs = ctx._go_mode_prerequisites()
                # Jacks Schematic (2026-07-28) is the one prereq that has
                # its own dedicated always-present widget slot (jacks_lbl,
                # collapsed to height=0 when absent) rather than living in
                # prereq_box/prereq_lbls — split it off by name rather than
                # relying on list length/position, since piston_combos_on
                # (and therefore whether it's present at all) only becomes
                # known once slot_data arrives, same as unique_retractor_keys_on
                # below.
                base_prereqs = [p for p in prereqs if p[0] != "Jacks Schematic (Piston Combos)"]
                jacks_entry  = next((p for p in prereqs if p[0] == "Jacks Schematic (Piston Combos)"), None)

                # Rebuild rows only when the count changes (mirrors
                # ShadowManLocationsLayout.update() below) — this is what
                # picks up the flat-Retractors-row (1) → 5-named-rows jump
                # once unique_retractor_keys_on resolves true after
                # __init__ already built prereq_lbls for the flat case.
                if len(self.prereq_lbls) != len(base_prereqs):
                    self._rebuild_prereq_rows(base_prereqs)

                for lbl, (label, ok, detail) in zip(self.prereq_lbls, base_prereqs):
                    color = "00FA9A" if ok else "FFA500"
                    lbl.text = f"  {label}: [color={color}]{detail}[/color]"

                if jacks_entry is not None:
                    label, ok, detail = jacks_entry
                    color = "00FA9A" if ok else "FFA500"
                    self.jacks_lbl.height = "22dp"
                    self.jacks_lbl.text = f"  {label}: [color={color}]{detail}[/color]"
                else:
                    self.jacks_lbl.height = 0
                    self.jacks_lbl.text = ""

                blocks = ctx._engine_block_reachable()
                if blocks is None:
                    self.summary_lbl.text = (
                        "[color=888888]Install Universal Tracker (tracker.apworld) "
                        "for logic-based region reachability[/color]"
                    )
                    self.engine_header_lbl.text = "[b]Engine Block regions[/b]"
                    for lbl in self.region_lbls:
                        lbl.text = ""
                    return

                reached = sum(1 for _, ok in blocks if ok)
                schematic_required = ctx.piston_combos_on
                schematic_ok = ctx._has_jacks_schematic() if schematic_required else True

                # BUG FIX (2026-07-28, Jon's report): this summary line used
                # to count "5 Engine Block regions reachable per UT" (+1 for
                # Jacks Schematic) as the X/Y shown here — a completely
                # different, UT-reachability-only metric than the
                # Prerequisites list rendered directly above it. Jon's own
                # screenshot showed 2 of the 7 listed prerequisites
                # satisfied (Engineers Key, Soul Level 2) while this line
                # read "0/5" — right underneath a list that plainly showed
                # 2 things held. The fix: count against `prereqs` (the same
                # list the Prerequisites section renders, and per its own
                # docstring "the concrete item / soul-level requirements
                # that gate Engine Block access end-to-end") instead of
                # `blocks`. `blocks`/`reached` (Universal Tracker's live
                # region-reachability pass) is kept as the authoritative
                # signal for the Yes/No decision itself — reaching a region
                # in logic is the real, final gate; a held item can still
                # be short of reachability for other reasons UT knows about
                # that this item checklist doesn't model — but the visible
                # X/Y count should match the list the player is looking at.
                prereq_total = len(prereqs)
                prereq_done  = sum(1 for _, ok, _ in prereqs if ok)

                if reached == len(blocks) and schematic_ok:
                    self.summary_lbl.text = "[color=00FA9A][b]Go Mode: Yes[/b][/color]"
                else:
                    self.summary_lbl.text = (
                        f"Go Mode: [color=FFA500]{prereq_done}/{prereq_total}[/color] requirements met"
                    )

                # "Beatable" (2026-07-28, Jon: "track engine blocks that
                # are beatable") — UT's own reachability (`ok`) only
                # covers getting INTO a region; when piston_combos is on,
                # actually clearing that Engine Block's piston-combo
                # barrels also needs Jacks Schematic (see
                # _go_mode_prerequisites' docstring — it "gates the actual
                # Engine Block completions themselves"), which is a single
                # global item, not per-region, so it either unlocks all 5
                # at once or none of them.
                beatable_n = sum(1 for _, ok in blocks if ok and schematic_ok)
                header_color = "00FA9A" if beatable_n == len(blocks) else "FFA500"
                self.engine_header_lbl.text = (
                    f"[b]Engine Block regions[/b]  "
                    f"([color={header_color}]{beatable_n}/{len(blocks)}[/color] beatable)"
                )
                for lbl, (name, ok) in zip(self.region_lbls, blocks):
                    if ok and schematic_ok:
                        color, mark = "00FA9A", "beatable"
                    elif ok:
                        color, mark = "FFA500", "reachable — needs Jacks Schematic"
                    else:
                        color, mark = "888888", "not yet reachable"
                    lbl.text = f"  {name}: [color={color}]{mark}[/color]"

        class ShadowManContent(ScrollView):
            def __init__(self) -> None:
                super().__init__()
                self.layout = BoxLayout(orientation="vertical", size_hint_y=None,
                                         spacing="12dp", padding="8dp")
                self.layout.bind(minimum_height=self.layout.setter("height"))

                self.overview  = ShadowManOverviewLayout()
                self.locations = ShadowManLocationsLayout()
                self.levels    = ShadowManLevelProgressLayout(on_select=self.locations.select_level)
                self.go_mode   = ShadowManGoModeLayout()
                for w in (self.overview, self.levels, self.locations, self.go_mode):
                    self.layout.add_widget(w)
                self.add_widget(self.layout)

                self.timer = Clock.schedule_interval(self.update, 0.5)

            def update(self, *_) -> None:
                try:
                    self.overview.update()
                    self.levels.update()
                    self.locations.update()
                    self.go_mode.update()
                except Exception:
                    import traceback
                    logger.debug(
                        "[ShadowMan] GUI tab update error:\n" + traceback.format_exc())

        class ShadowManTabLayout(BoxLayout):
            def __init__(self) -> None:
                super().__init__(orientation="vertical")
                self.content = ShadowManContent()
                self.add_widget(self.content)

        class ShadowManManager(ui):
            logging_pairs = [("Client", "Archipelago")]
            base_title    = "Shadow Man Remastered AP Client"

            def build(self):
                container = super().build()
                self.shadowman_tab_layout = ShadowManTabLayout()
                self.add_client_tab("Shadow Man", self.shadowman_tab_layout)
                return container

        return ShadowManManager


# ── Entry point ────────────────────────────────────────────────────────────────

async def main(args) -> None:
    Utils.init_logging("ShadowManClient", exception_logger="Client")
    ctx = ShadowManContext(args.connect, args.password)

    ctx.server_task = asyncio.create_task(server_loop(ctx), name="server loop")
    asyncio.create_task(ctx._save_watcher_loop(),   name="save watcher")
    asyncio.create_task(ctx._memory_watcher_loop(), name="memory watcher")
    asyncio.create_task(ctx._fast_watcher_loop(), name="fast watcher")
    asyncio.create_task(ctx._item_inject_loop(),  name="item injector")
    asyncio.create_task(ctx._goal_watcher_loop(), name="goal watcher")
    asyncio.create_task(ctx._health_watcher_loop(), name="health watcher")

    if tracker_loaded:
        ctx.run_generator()
    if gui_enabled:
        ctx.run_gui()
    ctx.run_cli()

    await ctx.exit_event.wait()
    await ctx.shutdown()


# Single-instance guard (2026-08-04), added alongside launch_game.bat and
# the overlay DLL's own optional auto-launch — both can now attempt to
# start this client even when one might already be running (e.g. Jon
# starts it by hand, then also double-clicks launch_game.bat out of
# habit). Two concurrent client.py instances would both attach to the
# game process and both independently fire the same CreateRemoteThread-
# based injection calls — exactly the kind of uncoordinated concurrent
# access this project's crash history (see CLAUDE.md) has repeatedly
# traced real bugs back to, so this is worth closing off structurally
# rather than hoping it never happens. A named Windows mutex is the
# standard technique for this: process-wide, auto-released on exit
# (clean or crashed) with no cleanup code needed, and CreateMutexW's own
# return tells us directly whether we're the first instance.
_singleton_mutex = None  # kept alive for the process lifetime — see _acquire_singleton_lock


def _acquire_singleton_lock() -> bool:
    global _singleton_mutex
    ERROR_ALREADY_EXISTS = 183
    _singleton_mutex = ctypes.windll.kernel32.CreateMutexW(
        None, False, "Global\\ShadowManAPClientSingleton")
    if not _singleton_mutex:
        # CreateMutexW itself failed (very unlikely) — fail OPEN here
        # rather than block the client from ever starting over a Windows
        # API hiccup neither side can diagnose from this alone.
        return True
    return ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def launch() -> None:
    if not _acquire_singleton_lock():
        # logger isn't configured yet at this point (Utils.init_logging
        # happens inside main()) — plain print is deliberate, not a typo.
        print(
            "[ShadowMan] Another instance of this client already appears to be "
            "running — exiting rather than risk two copies concurrently polling "
            "the game/AP server. Close the other one first if you actually meant "
            "to start a second client.")
        return

    parser = get_base_parser(description="Shadow Man Remastered Archipelago Client")
    if multiprocessing.parent_process() is not None:
        # Running as a multiprocessing child -- i.e. launched via the
        # Archipelago Launcher's component system (launch_subprocess() in
        # worlds/shadowman/__init__.py), not as a direct "python client.py"
        # invocation. Found 2026-08-04: Windows' "spawn" multiprocessing
        # start method (set globally by Launcher.py) re-injects the ORIGINAL
        # PARENT process's sys.argv into every child it creates
        # (multiprocessing.spawn's prep-data "sys_argv" restore -- this is
        # normal, documented multiprocessing behavior, not a bug in this
        # file). That's harmless when Launcher.py itself was started with no
        # CLI args (the normal interactive-GUI path -- sys.argv is just
        # ["Launcher.py"], nothing left over for a child to inherit). But
        # ap_gui.py's own "Launch Game + Client" button invokes
        # Launcher.py directly with a positional component name
        # ("Shadow Man Remastered Client", via launch_client.bat) --
        # that string is still sitting in the PARENT's sys.argv, gets
        # faithfully restored into THIS child process, and this parser
        # (which defines no positional at all, only --connect/--password/
        # --nogui) rejected it as an unrecognized argument -- confirmed via
        # the exact reported error ("unrecognized arguments: Shadow Man
        # Remastered Client"). Two earlier fix attempts assumed this was a
        # shell/cmd.exe quoting bug (it wasn't -- both were reverted/
        # superseded, see ap_gui.py's own inline history on
        # _launch_ap_client for that dead end). Parsing against an
        # explicit empty argv here sidesteps the whole inherited-sys.argv
        # question entirely, regardless of how this process was launched.
        args = parser.parse_args([])
    else:
        args = parser.parse_args()
    asyncio.run(main(args))


if __name__ == "__main__":
    launch()
