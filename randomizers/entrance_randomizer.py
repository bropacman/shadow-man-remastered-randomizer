"""
entrance_randomizer.py
──────────────────────
Shuffles all hub ↔ spoke transitions in a single unified pool:

  • 9 Deadside (LEVEL06) portals
  • 5 Dark Engine (LEVEL11) soul gates

  …all drawing from the same 14-spoke pool:
    9 deadside spokes  (wastland, asylum, ah1cagew, ah2playr, ah3lavad,
                        ah4fogom, t1tchgad, t2wlkgad, t3swmgad)
    5 dark engine spokes  (as4dkeng via sg1–sg5 entrances)

  A Deadside portal may lead to a Dark Engine soul gate door, and vice versa.
  The return path always goes back to whichever hub sent you there.

  Note: the cageway tram → as4dkeng connection is NOT in this pool and
  remains fixed/vanilla.

TRANSITION MODEL
────────────────
Each UnifiedTransition describes one hub-portal ↔ spoke pair.

  Portal side (hub outbound):
    portal_folder       — hub folder
    portal_file         — cut file to patch with ExitLevelPos

  Spoke side (destination level + arrival):
    spoke_folder        — destination level folder
    spoke_level_const   — first  arg of ExitLevelPos in portal file
    spoke_arrival       — second arg of ExitLevelPos in portal file
    spoke_exit_file     — exit cut file name
    spoke_exit_folder   — folder containing spoke_exit_file
                          (usually == spoke_folder, but sg4/florida differs)

  Return side (where spoke exit leads back to):
    hub_level_const     — first  arg of ExitLevelPos in spoke exit file
    hub_arrival         — second arg of ExitLevelPos in spoke exit file

PATCHING LOGIC
──────────────
Outbound:
  patch ExitLevelPos(old_spoke_level_const, old_spoke_arrival)
     in portal_folder/portal_file

Inbound:
  patch ExitLevelPos(old_hub_level_const, old_hub_arrival)
     in spoke_exit_folder/spoke_exit_file

Both args replaced so cross-hub returns work correctly.
Deadside LE + deadside exit.cut files have 2 ExitLevelPos occurrences
(primary + Stop-branch). Dark Engine sgXexit.cut files have 1.

REVERT NOTES
────────────
To restore vanilla, re-run with revert=True or restore from backup.

Cut files patched (outbound portals):
  deadside/LE_Wast.cut, LE_Asy1.cut, LE_Cage.cut, LE_Play.cut,
  LE_Lava.cut, LE_Fog.cut, LE_Gad1.cut, LE_Gad2.cut, LE_Gad3.cut
  as4dkeng/sg1exit.cut, sg2exit.cut, sg3exit.cut, sg4exit.cut, sg5exit.cut

Cut files patched (inbound / spoke returns):
  wastland/exit.cut      — vanilla: LEVEL06, MGATE_ARIVE_WLAND
  asylum/exit.cut        — vanilla: LEVEL06, MGATE_ARIVE_ASYS1
  ah1cagew/exit.cut      — vanilla: LEVEL06, MGATE_ARIVE_ACAGE
  ah2playr/exit.cut      — vanilla: LEVEL06, MGATE_ARIVE_APLAY
  ah3lavad/exit.cut      — vanilla: LEVEL06, MGATE_ARIVE_ALAVA
  ah4fogom/exit.cut      — vanilla: LEVEL06, MGATE_ARIVE_AFOGO
  t1tchgad/exit.cut      — vanilla: LEVEL06, MGATE_ARIVE_GADT1
  t2wlkgad/exit.cut      — vanilla: LEVEL06, MGATE_ARIVE_GADT2
  t3swmgad/exit.cut      — vanilla: LEVEL06, MGATE_ARIVE_GADT3
  as4dkeng/sg1exit.cut   — vanilla: LEVEL01, TMENT_ARIVE_SGATE
  as4dkeng/sg2exit.cut   — vanilla: LEVEL02, PRISN_ARIVE_SGATE
  as4dkeng/sg3exit.cut   — vanilla: LEVEL03, UGRND_ARIVE_SGATE
  as4dkeng/sg4exit.cut   — vanilla: LEVEL04, FLORI_ARIVE_SGATE
  as4dkeng/sg5exit.cut   — vanilla: LEVEL05, MOJAV_ARIVE_SGATE
"""

from __future__ import annotations

import re
import random
from dataclasses import dataclass
from pathlib import Path

from constants import LEVEL_NAMES as _LEVEL_NAMES


# ── Hub level constants ───────────────────────────────────────────────────────

_DS  = "LEVEL06_DEADSIDE_MARROW_GATES"
_DKE = "LEVEL11_ASYLUM_STATION_4_THE_DARK_ENGINE"


# ── Transition descriptor ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class UnifiedTransition:
    """One hub-portal ↔ spoke-level pair, vanilla configuration."""

    # Portal (outbound) side
    portal_folder:          str   # hub folder containing the portal file
    portal_file:            str   # outbound cut file in portal_folder
    # The vanilla content of the portal file — what ExitLevelPos currently says.
    # For Deadside LE files: (spoke_level_const, spoke_arrival) — they point outward.
    # For DKE sgXexit files: (hub_level_const, hub_arrival) — they point back inward.
    portal_find:    str   # first  arg currently in portal file ExitLevelPos
    portal_arrival: str   # second arg currently in portal file ExitLevelPos

    # Spoke (destination) side
    spoke_folder:      str   # destination level folder
    spoke_level_const: str   # first  arg of ExitLevelPos in portal file
    spoke_arrival:     str   # second arg of ExitLevelPos in portal file
    spoke_exit_file:   str | None   # exit cut filename; None for bidirectional
                                    # door triggers (sg1/2/3/5) — outbound patch covers both
    spoke_exit_folder: str | None   # folder for spoke_exit_file; None when spoke_exit_file is None

    # Return side
    hub_level_const:   str   # first  arg of ExitLevelPos in spoke exit file
    hub_arrival:       str   # second arg of ExitLevelPos in spoke exit file


# ── Vanilla transition table (14 entries) ────────────────────────────────────

UNIFIED_TRANSITIONS: list[UnifiedTransition] = [

    # ── Deadside portals → deadside spokes (9) ───────────────────────────────
    UnifiedTransition(
        portal_folder="deadside", portal_file="LE_Wast.cut",
        portal_find="LEVEL07_DEADSIDE_WASTELAND", portal_arrival="WLAND_ARIVE_MGATE",
        spoke_folder="wastland",  spoke_level_const="LEVEL07_DEADSIDE_WASTELAND",
        spoke_arrival="WLAND_ARIVE_MGATE",
        spoke_exit_file="exit.cut", spoke_exit_folder="wastland",
        hub_level_const=_DS, hub_arrival="MGATE_ARIVE_WLAND",
    ),
    UnifiedTransition(
        portal_folder="deadside", portal_file="LE_Asy1.cut",
        portal_find="LEVEL08_ASYLUM_STATION_1_GATEWAY", portal_arrival="ASYS1_ARIVE_MGATE",
        spoke_folder="asylum",    spoke_level_const="LEVEL08_ASYLUM_STATION_1_GATEWAY",
        spoke_arrival="ASYS1_ARIVE_MGATE",
        spoke_exit_file="exit.cut", spoke_exit_folder="asylum",
        hub_level_const=_DS, hub_arrival="MGATE_ARIVE_ASYS1",
    ),
    UnifiedTransition(
        portal_folder="deadside", portal_file="LE_Cage.cut",
        portal_find="LEVEL13_ASYLUM_HUB_1_THE_CAGEWAYS", portal_arrival="ACAGE_ARIVE_MGATE",
        spoke_folder="ah1cagew",  spoke_level_const="LEVEL13_ASYLUM_HUB_1_THE_CAGEWAYS",
        spoke_arrival="ACAGE_ARIVE_MGATE",
        spoke_exit_file="exit.cut", spoke_exit_folder="ah1cagew",
        hub_level_const=_DS, hub_arrival="MGATE_ARIVE_ACAGE",
    ),
    UnifiedTransition(
        portal_folder="deadside", portal_file="LE_Play.cut",
        portal_find="LEVEL14_ASYLUM_HUB_2_THE_PLAYROOMS", portal_arrival="APLAY_ARIVE_MGATE",
        spoke_folder="ah2playr",  spoke_level_const="LEVEL14_ASYLUM_HUB_2_THE_PLAYROOMS",
        spoke_arrival="APLAY_ARIVE_MGATE",
        spoke_exit_file="exit.cut", spoke_exit_folder="ah2playr",
        hub_level_const=_DS, hub_arrival="MGATE_ARIVE_APLAY",
    ),
    # NOTE: LE_Lava.cut uses APLAY_ARIVE_MGATE — copy-paste error in original
    # files, preserved verbatim so the regex matches live content.
    UnifiedTransition(
        portal_folder="deadside", portal_file="LE_Lava.cut",
        portal_find="LEVEL16_ASYLUM_HUB_3_THE_LAVADUCTS", portal_arrival="APLAY_ARIVE_MGATE",
        spoke_folder="ah3lavad",  spoke_level_const="LEVEL16_ASYLUM_HUB_3_THE_LAVADUCTS",
        spoke_arrival="APLAY_ARIVE_MGATE",
        spoke_exit_file="exit.cut", spoke_exit_folder="ah3lavad",
        hub_level_const=_DS, hub_arrival="MGATE_ARIVE_ALAVA",
    ),
    UnifiedTransition(
        portal_folder="deadside", portal_file="LE_Fog.cut",
        portal_find="LEVEL18_ASYLUM_HUB_4_THE_FOGOMETERS", portal_arrival="AFOGO_ARIVE_MGATE",
        spoke_folder="ah4fogom",  spoke_level_const="LEVEL18_ASYLUM_HUB_4_THE_FOGOMETERS",
        spoke_arrival="AFOGO_ARIVE_MGATE",
        spoke_exit_file="exit.cut", spoke_exit_folder="ah4fogom",
        hub_level_const=_DS, hub_arrival="MGATE_ARIVE_AFOGO",
    ),
    UnifiedTransition(
        portal_folder="deadside", portal_file="LE_Gad1.cut",
        portal_find="LEVEL12_GAD_TEMPLE_1_TOUCH_GAD", portal_arrival="GADT1_ARIVE_MGATE",
        spoke_folder="t1tchgad",  spoke_level_const="LEVEL12_GAD_TEMPLE_1_TOUCH_GAD",
        spoke_arrival="GADT1_ARIVE_MGATE",
        spoke_exit_file="exit.cut", spoke_exit_folder="t1tchgad",
        hub_level_const=_DS, hub_arrival="MGATE_ARIVE_GADT1",
    ),
    UnifiedTransition(
        portal_folder="deadside", portal_file="LE_Gad2.cut",
        portal_find="LEVEL15_GAD_TEMPLE_2_WALK_GAD", portal_arrival="GADT2_ARIVE_MGATE",
        spoke_folder="t2wlkgad",  spoke_level_const="LEVEL15_GAD_TEMPLE_2_WALK_GAD",
        spoke_arrival="GADT2_ARIVE_MGATE",
        spoke_exit_file="exit.cut", spoke_exit_folder="t2wlkgad",
        hub_level_const=_DS, hub_arrival="MGATE_ARIVE_GADT2",
    ),
    UnifiedTransition(
        portal_folder="deadside", portal_file="LE_Gad3.cut",
        portal_find="LEVEL17_GAD_TEMPLE_3_SWIM_GAD", portal_arrival="GADT3_ARIVE_MGATE",
        spoke_folder="t3swmgad",  spoke_level_const="LEVEL17_GAD_TEMPLE_3_SWIM_GAD",
        spoke_arrival="GADT3_ARIVE_MGATE",
        spoke_exit_file="exit.cut", spoke_exit_folder="t3swmgad",
        hub_level_const=_DS, hub_arrival="MGATE_ARIVE_GADT3",
    ),

    # ── Dark Engine soul gates → dark engine spokes (5) ──────────────────────
    # The outbound portal is the liveside level's soulexit.cut (or soulgate.cut
    # for tenement) — this is what sends the player FROM the liveside level INTO
    # Dark Engine. Patching it redirects which DKE section (or deadside spoke)
    # the player enters.
    #
    # The inbound return is as4dkeng/sgXexit.cut — walking back through the soul
    # gate door sends the player back to wherever the return portal is.
    #
    # sgXentr.cut files are pure arrival cutscenes (PositionActor only) and
    # are never patched.
    #
    # tenement uses soulgate.cut (not soulexit.cut).
    UnifiedTransition(
        portal_folder="tenement", portal_file="soulgate.cut",
        portal_find=_DKE, portal_arrival="ASYS4_ARIVE_TMENT",
        spoke_folder="as4dkeng",  spoke_level_const=_DKE,
        spoke_arrival="ASYS4_ARIVE_TMENT",
        spoke_exit_file="sg1exit.cut", spoke_exit_folder="as4dkeng",
        hub_level_const="LEVEL01_NEW_YORK_TENEMENT_DAY",
        hub_arrival="TMENT_ARIVE_SGATE",
    ),
    UnifiedTransition(
        portal_folder="prison",   portal_file="soulexit.cut",
        portal_find=_DKE, portal_arrival="ASYS4_ARIVE_PRISN",
        spoke_folder="as4dkeng",  spoke_level_const=_DKE,
        spoke_arrival="ASYS4_ARIVE_PRISN",
        spoke_exit_file="sg2exit.cut", spoke_exit_folder="as4dkeng",
        hub_level_const="LEVEL02_TEXAS_PRISON_DAY",
        hub_arrival="PRISN_ARIVE_SGATE",
    ),
    UnifiedTransition(
        portal_folder="uground",  portal_file="soulexit.cut",
        portal_find=_DKE, portal_arrival="ASYS4_ARIVE_UGRND",
        spoke_folder="as4dkeng",  spoke_level_const=_DKE,
        spoke_arrival="ASYS4_ARIVE_UGRND",
        spoke_exit_file="sg3exit.cut", spoke_exit_folder="as4dkeng",
        hub_level_const="LEVEL03_LONDON_UNDERGROUND_DAY",
        hub_arrival="UGRND_ARIVE_SGATE",
    ),
    UnifiedTransition(
        portal_folder="florida",  portal_file="soulexit.cut",
        portal_find=_DKE, portal_arrival="ASYS4_ARIVE_FLORI",
        spoke_folder="as4dkeng",  spoke_level_const=_DKE,
        spoke_arrival="ASYS4_ARIVE_FLORI",
        spoke_exit_file="sg4exit.cut", spoke_exit_folder="as4dkeng",
        hub_level_const="LEVEL04_FLORIDA_SUMMER_CAMP_DAY",
        hub_arrival="FLORI_ARIVE_SGATE",
    ),
    UnifiedTransition(
        portal_folder="salvage",  portal_file="soulexit.cut",
        portal_find=_DKE, portal_arrival="ASYS4_ARIVE_MOJAV",
        spoke_folder="as4dkeng",  spoke_level_const=_DKE,
        spoke_arrival="ASYS4_ARIVE_MOJAV",
        spoke_exit_file="sg5exit.cut", spoke_exit_folder="as4dkeng",
        hub_level_const="LEVEL05_MOJAVE_DESERT_SALVAGE_YARD_DAY",
        hub_arrival="MOJAV_ARIVE_SGATE",
    ),
]

# Portal lists by hub type
# DKE portals are now in liveside level folders (soulexit.cut / soulgate.cut)
# identified by whether their spoke_folder is "as4dkeng"

# Fast lookup by (portal_folder, portal_file) tuple — portal_file alone is not
# unique since multiple liveside levels share "soulexit.cut".
# Also keep a spoke-exit lookup by (spoke_exit_folder, spoke_exit_file).
_TRANSITION_BY_PORTAL_ID: dict[tuple[str,str], UnifiedTransition] = {
    (t.portal_folder, t.portal_file): t for t in UNIFIED_TRANSITIONS
}
# Portal IDs are (portal_folder, portal_file) tuples throughout
_ALL_PORTAL_IDS  = [(t.portal_folder, t.portal_file) for t in UNIFIED_TRANSITIONS]
_DS_PORTAL_IDS   = [(t.portal_folder, t.portal_file) for t in UNIFIED_TRANSITIONS if t.spoke_folder != "as4dkeng"]
_DKE_PORTAL_IDS  = [(t.portal_folder, t.portal_file) for t in UNIFIED_TRANSITIONS if t.spoke_folder == "as4dkeng"]


# ── Shuffle result ────────────────────────────────────────────────────────────

@dataclass
class UnifiedShuffle:
    """
    Full shuffled entrance configuration.

    outbound: (portal_folder, portal_file) → (dest_portal_folder, dest_portal_file)
      Maps each portal ID tuple to the spoke it now leads to (identified by
      the spoke's own portal ID tuple).

    inbound: (spoke_portal_folder, spoke_portal_file) → (hub_portal_folder, hub_portal_file)
      Maps each spoke ID tuple to the portal it now returns to.
    """
    mode:     str
    outbound: dict[tuple[str,str], tuple[str,str]]   # portal_id → dest spoke portal_id
    inbound:  dict[tuple[str,str], tuple[str,str]]   # spoke portal_id → return portal_id


# ── Shuffle generation ────────────────────────────────────────────────────────

def shuffle_unified(rng: random.Random, mode: str = "deadside_only", shuffle_gad_temples: bool = False) -> UnifiedShuffle:
    """
    Generate a randomised entrance mapping.

    mode="deadside_only":
      Only the 9 Deadside portals are shuffled, among the 9 deadside spokes.
      The 5 Dark Engine soul gates remain vanilla (each leads to its paired
      liveside level as normal).

    mode="cross_hub":
      All 14 portals (9 Deadside + 5 Dark Engine) are shuffled together as
      one coupled permutation across all 14 spokes. A Deadside portal may
      lead to a Dark Engine soul gate door and vice versa. Inbound is the
      exact inverse of outbound.
    """
    if mode not in ("deadside_only", "cross_hub"):
        raise ValueError(f"Unknown entrance mode: {mode!r}")

    if mode == "deadside_only":
        # Shuffle only Deadside portals among deadside spokes.
        # DKE portals map to themselves (vanilla).
        ds_dest = list(_DS_PORTAL_IDS)
        rng.shuffle(ds_dest)
        outbound = {
            **dict(zip(_DS_PORTAL_IDS,  ds_dest)),
            **dict(zip(_DKE_PORTAL_IDS, _DKE_PORTAL_IDS)),  # vanilla — no change
        }
        inbound = {spoke: portal for portal, spoke in outbound.items()}

    else:  # cross_hub
        # Single coupled permutation across all 14 portals and spokes.
        #
        # Constraint A: the asylum spoke MUST be reached from a Deadside coffin
        # gate. Assigning it to a soul gate exit is a physically impossible
        # game state -- you cannot reach Asylum without first completing a
        # liveside level, but liveside requires Asylum (Cathedral). Circular.
        _ASYLUM_ID  = ("deadside", "LE_Asy1.cut")   # spoke ID for Asylum: Gateways
        _SALVAGE_ID = ("salvage",  "soulexit.cut")  # soul gate portal for Salvage

        # Gad temple spoke IDs — the three physical temple level folders.
        # Completing Salvage requires GAD3 -> GAD2 -> GAD1 (all three in order),
        # so the player already has every gad power before using the Salvage soul
        # gate.  Assigning a gad temple as Salvage's destination would make its
        # gad power permanently inaccessible (when temples are not shuffled) or
        # permanently block any gad pickup placed there (when temples are shuffled).
        _GAD_SPOKE_IDS = frozenset(
            pid for pid in _ALL_PORTAL_IDS
            if _TRANSITION_BY_PORTAL_ID[pid].spoke_folder in
               {"t1tchgad", "t2wlkgad", "t3swmgad"}
        )

        # 1. Pick one Deadside portal at random to always receive the asylum spoke.
        asylum_portal = rng.choice(_DS_PORTAL_IDS)

        remaining_portals = [p for p in _ALL_PORTAL_IDS if p != asylum_portal]
        remaining_dests   = [d for d in _ALL_PORTAL_IDS if d != _ASYLUM_ID]

        # 2. Constraint B: Salvage soul gate cannot lead to any gad temple spoke.
        #    Salvage requires GAD3 (and thus GAD1+GAD2) to complete, so all gad
        #    powers are already held before the gate fires.  Skip this constraint
        #    only when gad powers are freely shuffled as items (shuffle_gad_temples),
        #    but even then a gad item placed behind Salvage would be unreachable
        #    (fill must respect this separately).
        if not shuffle_gad_temples:
            allowed_salvage_dests = [d for d in remaining_dests if d not in _GAD_SPOKE_IDS]
            salvage_dest = rng.choice(allowed_salvage_dests)
            remaining_portals = [p for p in remaining_portals if p != _SALVAGE_ID]
            remaining_dests   = [d for d in remaining_dests   if d != salvage_dest]
            rng.shuffle(remaining_dests)
            outbound = {asylum_portal: _ASYLUM_ID, _SALVAGE_ID: salvage_dest}
        else:
            rng.shuffle(remaining_dests)
            outbound = {asylum_portal: _ASYLUM_ID}

        # 3. Shuffle the rest freely.
        outbound.update(dict(zip(remaining_portals, remaining_dests)))
        inbound  = {spoke: portal for portal, spoke in outbound.items()}

    # Sanity-check: outbound must be a bijection over the full 14-portal set.
    # If this assertion fires the zip pairing is off (off-by-one, duplicate
    # removal applied twice, etc.) — every portal must appear exactly once as
    # a key AND exactly once as a value so that every spoke gets exactly one rule.
    _portal_set = set(_ALL_PORTAL_IDS)
    assert set(outbound.keys())   == _portal_set, (
        f"shuffle_unified ({mode}): outbound KEYS missing portals: "
        f"{_portal_set - set(outbound.keys())}"
    )
    assert set(outbound.values()) == _portal_set, (
        f"shuffle_unified ({mode}): outbound VALUES missing spokes: "
        f"{_portal_set - set(outbound.values())}"
    )

    return UnifiedShuffle(mode=mode, outbound=outbound, inbound=inbound)


# ── File patching helpers ─────────────────────────────────────────────────────

def _patch_exitlevelpos(path: Path,
                        old_level: str, old_arrival: str,
                        new_level: str, new_arrival:  str) -> None:
    """Replace all ExitLevelPos(old_level, old_arrival) calls in a cut file."""
    text = path.read_text(encoding="utf-8", errors="replace")
    pattern = (
        r"(ExitLevelPos\s*\(\s*)"
        + re.escape(old_level)
        + r"(\s*,\s*)"
        + re.escape(old_arrival)
        + r"(\s*\))"
    )
    patched, count = re.subn(
        pattern,
        rf"\g<1>{new_level}\g<2>{new_arrival}\g<3>",
        text,
    )
    if count == 0:
        raise RuntimeError(
            f"entrance_randomizer: no ExitLevelPos match in {path}\n"
            f"  expected: ({old_level}, {old_arrival})"
        )
    path.write_text(patched, encoding="utf-8")


# ── Apply shuffle ─────────────────────────────────────────────────────────────

def apply_unified_shuffle(shuffle:     UnifiedShuffle,
                          scripts_dir: Path,
                          verbose:     bool = False) -> None:
    """
    Write the shuffled entrance mapping to disk.

    scripts_dir — root of the extracted cutscene/scripts/ directory.
    """

    # ── Outbound patches ──────────────────────────────────────────────────────
    for hub_portal_id, dest_spoke_id in shuffle.outbound.items():
        vanilla_portal = _TRANSITION_BY_PORTAL_ID[hub_portal_id]
        new_spoke      = _TRANSITION_BY_PORTAL_ID[dest_spoke_id]

        # Skip vanilla assignments (deadside_only mode keeps DKE portals vanilla)
        if hub_portal_id == dest_spoke_id:
            continue

        portal_path = scripts_dir / vanilla_portal.portal_folder / hub_portal_id[1]

        # Both Deadside and DKE portal files get patched to point to the new
        # spoke's level const and arrival — always the outbound destination.
        new_level   = new_spoke.spoke_level_const
        new_arrival = new_spoke.spoke_arrival

        _patch_exitlevelpos(
            portal_path,
            vanilla_portal.portal_find, vanilla_portal.portal_arrival,
            new_level,                           new_arrival,
        )

        if verbose:
            print(f"  OUTBOUND [{vanilla_portal.portal_folder:8s}] "
                  f"{hub_portal_id[1]:16s} → {new_spoke.spoke_folder} "
                  f"({new_arrival})")

    # ── Inbound (return) patches ──────────────────────────────────────────────
    for spoke_id, return_portal_id in shuffle.inbound.items():
        vanilla_spoke = _TRANSITION_BY_PORTAL_ID[spoke_id]
        return_portal = _TRANSITION_BY_PORTAL_ID[return_portal_id]

        exit_path = (scripts_dir
                     / vanilla_spoke.spoke_exit_folder
                     / vanilla_spoke.spoke_exit_file)

        # Patch spoke exit file to return to the correct hub
        if return_portal.portal_folder == "as4dkeng":
            new_hub_level   = return_portal.spoke_level_const   # _DKE
            new_hub_arrival = return_portal.spoke_arrival        # ASYS4_ARIVE_Xxxx
        else:
            new_hub_level   = return_portal.hub_level_const     # LEVEL06_DEADSIDE
            new_hub_arrival = return_portal.hub_arrival          # MGATE_ARIVE_Xxxx

        _patch_exitlevelpos(
            exit_path,
            vanilla_spoke.hub_level_const, vanilla_spoke.hub_arrival,
            new_hub_level,                  new_hub_arrival,
        )

        if verbose:
            print(f"  RETURN   {vanilla_spoke.spoke_folder:12s} "
                  f"({vanilla_spoke.spoke_exit_folder}/{vanilla_spoke.spoke_exit_file}) "
                  f"→ [{return_portal.portal_folder}] {return_portal_id[1]}")


# ── Spoiler log ───────────────────────────────────────────────────────────────

def _spoke_friendly(t: UnifiedTransition) -> str:
    """
    Human-readable name for the spoke described by transition t.

    All five Dark Engine spokes share spoke_folder="as4dkeng"; we distinguish
    them by the liveside level (portal_folder) they connect to, e.g.
    "Dark Engine (New York Tenement)".
    All other spokes map directly via LEVEL_NAMES[spoke_folder].
    """
    if t.spoke_folder == "as4dkeng":
        liveside = _LEVEL_NAMES.get(t.portal_folder, t.portal_folder)
        return f"Dark Engine ({liveside})"
    return _LEVEL_NAMES.get(t.spoke_folder, t.spoke_folder)


def _portal_friendly(t: UnifiedTransition) -> str:
    """
    Human-readable name for the outbound portal described by transition t.

    Deadside portals are named after their vanilla spoke destination:
      "Deadside → {spoke level name}"
    Soul gates are named after their liveside level:
      "{liveside level name} Soul Gate"
    """
    if t.spoke_folder == "as4dkeng":
        liveside = _LEVEL_NAMES.get(t.portal_folder, t.portal_folder)
        return f"{liveside} Soul Gate"
    spoke_name = _LEVEL_NAMES.get(t.spoke_folder, t.spoke_folder)
    return f"[Deadside → {spoke_name}]"


# Build at module load — maps portal_id tuple to its friendly name.
_PORTAL_FRIENDLY: dict[tuple[str, str], str] = {
    (t.portal_folder, t.portal_file): _portal_friendly(t)
    for t in UNIFIED_TRANSITIONS
}


def unified_spoiler_section(shuffle: UnifiedShuffle) -> str:
    W = 36   # column width for left side

    lines = [
        f"Entrance Shuffle ({shuffle.mode})",
        "=" * 60,
        f"{'Portal (outbound)':{W}}  →  Destination",
        "-" * 60,
    ]
    for portal_id, dest_id in sorted(shuffle.outbound.items(), key=lambda x: str(x)):
        p_name = _PORTAL_FRIENDLY.get(portal_id, f"{portal_id[0]}/{portal_id[1]}")
        d      = _TRANSITION_BY_PORTAL_ID[dest_id]
        d_name = _spoke_friendly(d)
        lines.append(f"  {p_name:{W}}  →  {d_name}")

    lines += ["", f"{'Return path (spoke exit)':{W}}  →  Portal", "-" * 60]
    for spoke_id, return_id in sorted(shuffle.inbound.items(), key=lambda x: str(x)):
        s      = _TRANSITION_BY_PORTAL_ID[spoke_id]
        s_name = _spoke_friendly(s)
        r_name = _PORTAL_FRIENDLY.get(return_id, f"{return_id[0]}/{return_id[1]}")
        lines.append(f"  {s_name:{W}}  →  {r_name}")

    return "\n".join(lines)


# ── Convenience ───────────────────────────────────────────────────────────────

def randomize_unified(rng:                random.Random,
                      scripts_dir:        Path,
                      mode:               str  = "deadside_only",
                      shuffle_gad_temples: bool = False,
                      verbose:            bool = False) -> UnifiedShuffle:
    """Generate a unified entrance shuffle and apply it immediately."""
    shuffle = shuffle_unified(rng, mode=mode, shuffle_gad_temples=shuffle_gad_temples)
    apply_unified_shuffle(shuffle, scripts_dir, verbose=verbose)
    return shuffle