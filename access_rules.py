"""
access_rules.py
───────────────
Named rule functions for Shadow Man Remastered Archipelago randomizer.

Each function corresponds to one gate token from the CSV sub_region column.
All functions share the signature:
    (state: CollectionState, player: int) -> bool

Soul gate access is now handled via R.gate(gate_id, state, player), which
resolves the gate's current SL requirement from the active seed remap.
Vanilla slN() methods are retained for liveside SL2 checks and compound
expressions that reference soul count independently of a specific gate.
"""

from __future__ import annotations
try:
    from BaseClasses import CollectionState
except ImportError:
    CollectionState = object  # type: ignore
from constants import GATE_VANILLA_SL

# ── Item name constants ───────────────────────────────────────────────────────

_ECLIPSER_1      = "RSC_X_ECLIPSER_PART1"
_ECLIPSER_2      = "RSC_X_ECLIPSER_PART2"
_ECLIPSER_3      = "RSC_X_ECLIPSER_PART3"
_BATON           = "RSC_X_BATON"
_FLAMBEAU        = "RSC_X_FLAMBEAU"
_MARTEAU         = "RSC_X_MARTEAU"
_CALABASH        = "RSC_X_CALABASH"
_POIGNE          = "RSC_X_POIGNE"
_ENG_KEY         = "RSC_X_ENGINEERS_KEY"
_PRISON_KEY_CARD = "RSC_X_PRISON_KEY_CARD"
_ACCUMULATOR     = "RSC_X_ACCUMULATOR"
_GAD_PICKUP      = "RSC_X_GAD_PICKUP"
_SCHEMATIC       = "RSC_X_JACKS_SCHEMATIC"

# Active gate→SL mapping for the current seed.
# Populated at generation time by set_gate_remap().
# Defaults to vanilla until explicitly set.
_current_gate_sl: dict[str, int] = dict(GATE_VANILLA_SL)

# Set to True when piston_combos=random so R.pistons() requires the
# schematic item in inventory (analogous to Light Arrows in OoT).
_piston_combos_random: bool = False


def set_piston_combos_random(val: bool) -> None:
    """Called by fill.py assumed_fill/validate_fill when piston_combos=random."""
    global _piston_combos_random
    _piston_combos_random = val


# Set to True when the "Unique Retractor Keys" option is on. When enabled,
# each of the 5 liveside levels requires its own specific retractor (a
# per-seed randomized 1:1 assignment computed in fill.py) instead of the
# vanilla shared "any 5 retractors" gate. Populated at generation time by
# set_unique_retractor_keys(), called from fill.py's assumed_fill()/
# validate_fill() (same pattern as set_piston_combos_random/
# set_soul_thresholds — reset to False at the end of each call so it never
# bleeds into a subsequent, unrelated generation).
#
# This module only needs the boolean toggle, not the actual loc_key->region
# assignment map — that mapping is only needed where a retractor is
# physically COLLECTED (fill.py's simulate_playthrough, to know which
# virtual "_retractor_key:<region>" flag to set), whereas the rules below
# only ever need to ask "do I already have THIS region's key" using a
# region name that's already hardcoded per rule (see _LIVESIDE_* below).
_unique_retractor_keys: bool = False


def set_unique_retractor_keys(val: bool) -> None:
    """Called by fill.py assumed_fill/validate_fill when unique_retractor_keys is on."""
    global _unique_retractor_keys
    _unique_retractor_keys = val


# Liveside region friendly names — must exactly match regions.py's
# LIVESIDE_LONDON/LIVESIDE_PRISON/LIVESIDE_SALVAGE/LIVESIDE_QUEENS/
# LIVESIDE_FLORIDA constants. Duplicated here (not imported) because
# regions.py imports R from this module — importing regions.py back would
# be circular. Matches this file's existing convention of hardcoding item
# name strings locally (_BATON, _CALABASH, etc.) rather than importing them.
_LIVESIDE_LONDON  = "Down Street Station, London"
_LIVESIDE_PRISON  = "Gardelle County Jail, Texas"
_LIVESIDE_FLORIDA = "Summer Camp, Florida"
_LIVESIDE_SALVAGE = "Salvage Yard, Mojave Desert"
_LIVESIDE_QUEENS  = "Mordant Street, Queens, NY"


def _retractor_key_item(region: str) -> str:
    """Virtual (non-RSC) item name used to track a per-level retractor key
    in FakeState/CollectionState's inventory. Must match the key fill.py's
    simulate_playthrough() writes when a retractor assigned to `region` is
    collected."""
    return f"_retractor_key:{region}"


def set_gate_remap(gate_remap: dict[str, int]) -> None:
    """
    Called once by patcher.py after randomize_gate_sl_links() runs.
    gate_remap: {gate_id: new_sl_int} — full mapping for all 20 gates.
    """
    _current_gate_sl.clear()
    _current_gate_sl.update(GATE_VANILLA_SL)
    _current_gate_sl.update(gate_remap)


# ── Gate dependencies ─────────────────────────────────────────────────────────
#
# Maps each gate to the gates (and abilities) that must be passable before the
# player can physically reach it — independent of the gate's own SL requirement.
#
# Format:
#   str        → single gate that must be passable
#   list[list] → OR of AND-combinations (alternative approach routes)
#                Items starting with "GATE_" are resolved via R.gate().
#                "BATON" and "GAD2_WALK" are item/ability checks.
#
# Gates with no entry here have no physical gate dependency (freely reachable
# from the marrow gates hub once you're in deadside, or depend only on their
# region which is handled separately in fill.py).

_LOWER_DEADSIDE_ROUTES: list[list[str]] = [
    # Route A: le soleil path (cageways → playrooms → path_6)
    ["GATE_DEADSIDE_PATH_6"],
    # Route B: upper prophecy path (path_3 → path_7)
    ["GATE_DEADSIDE_PATH_7"],
    # Route C: asylum baton teleport shortcut
    ["GATE_DEADSIDE_ASYLUM", "BATON", "GAD2_WALK"],
]

GATE_DEPENDENCIES: dict[str, object] = {
    "GATE_DEADSIDE_WASTELAND"  : "GATE_DEADSIDE_MARROW",
    "GATE_DEADSIDE_ASYLUM"     : "GATE_DEADSIDE_WASTELAND",
    "GATE_DEADSIDE_PATH_3"     : "GATE_DEADSIDE_ASYLUM",
    "GATE_DEADSIDE_LALUNE"     : "GATE_DEADSIDE_ASYLUM",
    "GATE_DEADSIDE_CAGEWAYS"   : "GATE_DEADSIDE_PATH_3",
    "GATE_DEADSIDE_PLAYROOMS"  : "GATE_DEADSIDE_CAGEWAYS",
    "GATE_DEADSIDE_PATH_6"     : "GATE_DEADSIDE_PLAYROOMS",
    "GATE_DEADSIDE_PATH_7"     : "GATE_DEADSIDE_PATH_3",
    "GATE_DEADSIDE_LAVADUCTS"  : _LOWER_DEADSIDE_ROUTES,
    "GATE_DEADSIDE_LALAME"     : _LOWER_DEADSIDE_ROUTES,
    "GATE_DEADSIDE_BLOOD"      : _LOWER_DEADSIDE_ROUTES,
    "GATE_DEADSIDE_FOGOMETERS" : _LOWER_DEADSIDE_ROUTES,
    # GATE_DEADSIDE_MARROW   — no dependency, freely reachable
    # GATE_DEADSIDE_MYSTERY  — locked SL10, dependency irrelevant
    # Non-deadside gates     — region dependency only, handled in fill.py
    #
    # NOTE: GATE_DEADSIDE_CAGEWAYS / GATE_DEADSIDE_PLAYROOMS deliberately
    # stay single-string (front-door-only) entries here. They ALSO have two
    # backtrack routes (confirmed live by Jon 2026-08-03: reaching the lower
    # Deadside cluster via Route B or Route C lets you walk back UP into
    # Cageways/Playrooms from behind, bypassing that gate's own SL entirely
    # — not just its ancestor chain). That can't be modeled here, because
    # R.gate() always re-enforces a gate's OWN soul threshold (step 2) on
    # top of GATE_DEPENDENCIES (step 1) regardless of which route satisfied
    # step 1 — correct for the four _LOWER_DEADSIDE_ROUTES destinations
    # above (their own door genuinely still needs to be opened no matter
    # which path you took to reach it), but wrong for Cageways/Playrooms,
    # where the backtrack walks around the door, not just around the
    # ancestor chain leading up to it. Modeled instead as a hand-built OR at
    # the region-connection level — see CAGEWAYS_ROUTES / PLAYROOMS_ROUTES
    # below, and their use in regions.py (mirrors how Temple of Prophecy has
    # no single "own gate" of its own either).
}


# ── Soul helpers ──────────────────────────────────────────────────────────────

VANILLA_SOUL_THRESHOLDS: dict[int, int] = {
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

# Active SL→soul-count mapping. Matches vanilla until set_soul_thresholds() is called.
_current_soul_thresholds: dict[int, int] = dict(VANILLA_SOUL_THRESHOLDS)


def set_soul_thresholds(thresholds: dict[int, int] | None) -> None:
    """
    Called by fill.py (assumed_fill / validate_fill) and patcher.py when
    soul thresholds are randomized.  Pass None to reset to vanilla.
    """
    global _current_soul_thresholds
    _current_soul_thresholds = dict(thresholds) if thresholds is not None else dict(VANILLA_SOUL_THRESHOLDS)


def _count_souls(state, player) -> int:
    return state.count("_souls", player)


def _soul_level(state: CollectionState, player: int, level: int) -> bool:
    threshold = _current_soul_thresholds[level]
    return True if threshold == 0 else _count_souls(state, player) >= threshold


def _night(state: CollectionState, player: int) -> bool:
    return (
        state.has(_ECLIPSER_1, player)
        and state.has(_ECLIPSER_2, player)
        and state.has(_ECLIPSER_3, player)
    )

def _gate_sl_only(gate_id: str, state, player: int) -> bool:
    """
    Check only the soul threshold for a gate — no dependency chain.

    NOT USED by regions.py's entrance-shuffle portal rules as of the
    2026-07-21 fix (kept defined for reference/manual debugging only). It
    used to be, on the theory that portals "bypass physical Deadside
    traversal" once shuffled — wrong: the ancestor-gate chain models the
    physical walk to a portal's own location in Marrow Gates, which is
    unaffected by where its cutscene points afterward, and still needs
    walking every time. See DEADSIDE_PORTAL_GATE's comment block below for
    the full story. regions.py now uses R.gate() for portal rules instead,
    matching how the vanilla (unshuffled) _spoke_connections list already
    evaluates these same gate ids.
    """
    sl = _current_gate_sl.get(gate_id, GATE_VANILLA_SL.get(gate_id, 0))
    return _soul_level(state, player, sl)

# ── Rule namespace ────────────────────────────────────────────────────────────

class _Rules:

    # ── Gate access ───────────────────────────────────────────────────────────

    def gate(self, gate_id: str, state: CollectionState, player: int) -> bool:
        """
        Check whether the player can pass a named coffin gate.

        Two conditions must both be true:
          1. Physical reachability — all gate dependencies in GATE_DEPENDENCIES
             must themselves be passable (checked recursively).
          2. Soul threshold — the player's soul count must meet the gate's
             current SL requirement (post-remap for shuffled seeds).

        GATE_DEADSIDE_MARROW has SL0 (threshold=0) and no dependency, so it
        is always True — there are no collectible souls before it.
        """
        # ── Step 1: physical reachability ─────────────────────────────────────
        dep = GATE_DEPENDENCIES.get(gate_id)
        if dep is not None:
            if isinstance(dep, str):
                # single gate dependency
                if not self.gate(dep, state, player):
                    return False
            else:
                # OR of AND-combinations
                def _token(t: str) -> bool:
                    if t.startswith("GATE_"):
                        return self.gate(t, state, player)
                    if t == "BATON":
                        return self.baton(state, player)
                    if t == "GAD2_WALK":
                        return self.gad2_walk(state, player)
                    return False
                if not any(all(_token(t) for t in route) for route in dep):
                    return False

        # ── Step 2: soul threshold ─────────────────────────────────────────────
        sl = _current_gate_sl.get(gate_id, GATE_VANILLA_SL.get(gate_id, 0))
        return _soul_level(state, player, sl)

    # ── Shadow weapons / abilities ────────────────────────────────────────────

    def flambeau(self, state: CollectionState, player: int) -> bool:
        # BUG FIX (2026-08-09, Jon's report): Flambeau doesn't actually
        # work in-game until the player has Voodoo Power (SL1) — physically
        # holding the item isn't enough, same real requirement as Calabash
        # below. Ported from the AP world's identical fix the same day —
        # see that repo's access_rules.py for the full writeup.
        # _soul_level() here always reads the module-level
        # _current_soul_thresholds (set via set_soul_thresholds()), so this
        # automatically respects whatever this seed's soul_threshold_mode
        # resolved, same as every other gate check in this file.
        return state.has(_FLAMBEAU, player) and _soul_level(state, player, 1)

    def baton(self, state: CollectionState, player: int) -> bool:
        return state.has(_BATON, player)

    def calabash(self, state: CollectionState, player: int) -> bool:
        """See flambeau() above — same SL1/Voodoo Power requirement, same
        fix, same date."""
        return state.has(_CALABASH, player) and _soul_level(state, player, 1)

    def marteau(self, state: CollectionState, player: int) -> bool:
        return state.has(_MARTEAU, player)

    def poigne(self, state: CollectionState, player: int) -> bool:
        return state.has(_POIGNE, player)

    # ── Gad powers ────────────────────────────────────────────────────────────

    def gad1_hand(self, state, player) -> bool:
        return state.count(_GAD_PICKUP, player) >= 1

    def gad2_walk(self, state, player) -> bool:
        # GAD2 requires GAD1 first (powers acquired in order)
        return self.gad1_hand(state, player) and state.count(_GAD_PICKUP, player) >= 2

    def gad3_swim(self, state, player) -> bool:
        # GAD3 requires GAD1 + GAD2 first (powers acquired in order)
        return self.gad2_walk(state, player) and state.count(_GAD_PICKUP, player) >= 3

    # ── Key items ─────────────────────────────────────────────────────────────

    def eng_key(self, state: CollectionState, player: int) -> bool:
        return state.has(_ENG_KEY, player)

    def prison_key_card(self, state: CollectionState, player: int) -> bool:
        return state.has(_PRISON_KEY_CARD, player)

    def x3_accumulator(self, state: CollectionState, player: int) -> bool:
        return state.count(_ACCUMULATOR, player) >= 3

    def cadeaux_666(self, state, player) -> bool:
        return True
        # cant return to counting Cadeauxs unless we map out all Cadeaux
        # currently mapped 553/666 cadeaux
        # return state.count("_cadeaux", player) >= 666

    # ── Night ─────────────────────────────────────────────────────────────────

    def night(self, state: CollectionState, player: int) -> bool:
        return _night(state, player)

    # ── Liveside level entry ────────────────────────────────────────────

    def can_reach_liveside(self, state, player, current_region) -> bool:
        if _unique_retractor_keys:
            return state.has(_retractor_key_item(current_region), player)
        return state.count("_retractors", player) >= 5

    def _retractors_ok(self, state, player, region: str) -> bool:
        """Shared retractor-gate check for the 5 completion rules below —
        either this region's own key (unique_retractor_keys mode) or the
        vanilla shared 5-count (default)."""
        if _unique_retractor_keys:
            return state.has(_retractor_key_item(region), player)
        return state.count("_retractors", player) >= 5

    # ── Liveside level completions ────────────────────────────────────────────

    def florida(self, state: CollectionState, player: int) -> bool:
        return _night(state, player) and self._retractors_ok(state, player, _LIVESIDE_FLORIDA)

    def london(self, state: CollectionState, player: int) -> bool:
        return _night(state, player) and self._retractors_ok(state, player, _LIVESIDE_LONDON)

    def queens(self, state: CollectionState, player: int) -> bool:
        return (
                _night(state, player)
                and state.has(_POIGNE, player)
                and self._retractors_ok(state, player, _LIVESIDE_QUEENS)
        )

    def prison(self, state: CollectionState, player: int) -> bool:
        return (
                _night(state, player)
                and state.has(_PRISON_KEY_CARD, player)
                and self._retractors_ok(state, player, _LIVESIDE_PRISON)
        )

    def salvage(self, state: CollectionState, player: int) -> bool:
        return (
                _night(state, player)
                and self.gad3_swim(state, player)
                and self._retractors_ok(state, player, _LIVESIDE_SALVAGE)
        )

    def schematic(self, state, player) -> bool:
        """Player has Jack's Schematic (required to know dark engine combinations)."""
        return state.has(_SCHEMATIC, player)

    def pistons(self, state, player) -> bool:
        # These must match the level_region column in your CSV exactly
        sections = [
            "Asylum: Engine Block - London",
            "Asylum: Engine Block - Prison",
            "Asylum: Engine Block - Salvage",
            "Asylum: Engine Block - Queens",
            "Asylum: Engine Block - Florida"
        ]
        # When piston combinations are randomized the schematic journal is
        # the only way to learn the new combinations — treat it as required.
        if _piston_combos_random and not self.schematic(state, player):
            return False
        return all(state.can_reach(s, "Region", player) for s in sections)

    # ── Soul level methods ────────────────────────────────────────────────────
    # Retained for liveside SL2 EXE-hardcoded checks and any compound exprs
    # that check soul count independently of a specific physical gate.

    def sl0(self, state: CollectionState, player: int) -> bool:
        return True

    def sl1(self, state: CollectionState, player: int) -> bool:
        return _soul_level(state, player, 1)

    def sl2(self, state: CollectionState, player: int) -> bool:
        return _soul_level(state, player, 2)

    def sl3(self, state: CollectionState, player: int) -> bool:
        return _soul_level(state, player, 3)

    def sl4(self, state: CollectionState, player: int) -> bool:
        return _soul_level(state, player, 4)

    def sl5(self, state: CollectionState, player: int) -> bool:
        return _soul_level(state, player, 5)

    def sl6(self, state: CollectionState, player: int) -> bool:
        return _soul_level(state, player, 6)

    def sl7(self, state: CollectionState, player: int) -> bool:
        return _soul_level(state, player, 7)

    def sl8(self, state: CollectionState, player: int) -> bool:
        return _soul_level(state, player, 8)

    def sl9(self, state: CollectionState, player: int) -> bool:
        return _soul_level(state, player, 9)

    def sl10(self, state: CollectionState, player: int) -> bool:
        return _soul_level(state, player, 10)


# ── Singleton ─────────────────────────────────────────────────────────────────
R = _Rules()


# ── Entrance randomization support ───────────────────────────────────────────
# Defined after R so completion-rule lambdas can close over it.

# Cageways and Playrooms are each reachable a second/third way, alongside
# their own front door: once you land in the lower Deadside cluster via
# Route B (Path 7) or Route C (Asylum + Baton + Gad2 lava walk), you can
# freely backtrack UP into Cageways and Playrooms from behind, bypassing
# that gate's own soul-level requirement entirely — confirmed live by Jon
# 2026-08-03. Route A (Path 6) is deliberately NOT included: reaching
# Path 6 already requires having passed through Cageways/Playrooms on the
# way there, so adding it back as an alternate route into either of them
# would be circular. Used by DEADSIDE_PORTAL_GATE below (entrance-shuffle
# portal rules) and by regions.py's hand-built OR connection rules for
# ASYLUM_CAGEWAYS/ASYLUM_PLAYROOMS (vanilla) — see that file's matching
# comment. Also reused by fill.py's REGION_GATES heuristic table.
CAGEWAYS_ROUTES: list[list[str]] = [
    ["GATE_DEADSIDE_CAGEWAYS"],                        # front door — own SL still enforced
    ["GATE_DEADSIDE_PATH_7"],                           # Route B backtrack
    ["GATE_DEADSIDE_ASYLUM", "BATON", "GAD2_WALK"],     # Route C backtrack
]
PLAYROOMS_ROUTES: list[list[str]] = [
    ["GATE_DEADSIDE_PLAYROOMS"],                        # front door — own SL still enforced
    ["GATE_DEADSIDE_PATH_7"],                           # Route B backtrack
    ["GATE_DEADSIDE_ASYLUM", "BATON", "GAD2_WALK"],     # Route C backtrack
]

# Which coffin gate in the Marrow Gates hub physically guards each portal?
# BUG FIX (2026-07-21, caught in the AP world via a real seed, then found to
# be identical here): this table used to be built on a "physical position,
# independent of destination" theory, and regions.py's portal-rule builder
# used _gate_sl_only (soul threshold only, no GATE_DEPENDENCIES ancestor
# chain) on the reasoning that the chain only matters for reaching a
# portal's VANILLA destination. Wrong — the chain models the physical walk
# through Marrow Gates to reach a portal's own location, which doesn't
# change just because its cutscene now points somewhere else. Fixed by
# mapping each portal to the exact same gate id the vanilla
# _spoke_connections list above already uses for that portal's destination
# (proven-correct, unaffected by entrance shuffle either way), and by
# switching regions.py's portal-rule construction from _gate_sl_only to
# R.gate() so the ancestor chain gets walked. Temple of Prophecy (LE_Gad2.cut)
# needs the route-list form: its vanilla connection isn't a single named
# gate, it's "PATH_7, or (CAGEWAYS and PLAYROOMS and PATH_6), or (ASYLUM and
# BATON and GAD2_WALK)". Third route added 2026-07-26 (Jon, confirmed live
# in-game — reached Temple of Prophecy via the Asylum SL gate + Baton + Gad2
# walk-on-lava shortcut while this table only modeled the first two routes,
# missing the same shortcut _LOWER_DEADSIDE_ROUTES already grants
# Lavaducts/La Lame/Blood/Fogometers). LE_Cage.cut/LE_Play.cut switched to
# the route-list form too on 2026-08-03 — see CAGEWAYS_ROUTES/PLAYROOMS_ROUTES
# above; unlike Temple of Prophecy their front door is a real named gate
# with its own SL, so route 1 of each list is that single gate (full R.gate()
# check, own SL enforced) and routes 2/3 are the backtrack bypass. Mirrored
# into the AP world's copy of this same table — see that file's matching
# comment.
DEADSIDE_PORTAL_GATE: dict[str, str | list] = {
    "LE_Wast.cut": "GATE_DEADSIDE_WASTELAND",
    "LE_Asy1.cut": "GATE_DEADSIDE_ASYLUM",
    "LE_Gad1.cut": "GATE_DEADSIDE_PATH_3",
    "LE_Cage.cut": CAGEWAYS_ROUTES,
    "LE_Play.cut": PLAYROOMS_ROUTES,
    "LE_Lava.cut": "GATE_DEADSIDE_LAVADUCTS",
    "LE_Fog.cut":  "GATE_DEADSIDE_FOGOMETERS",
    "LE_Gad2.cut": [
        ["GATE_DEADSIDE_PATH_7"],
        ["GATE_DEADSIDE_CAGEWAYS", "GATE_DEADSIDE_PLAYROOMS", "GATE_DEADSIDE_PATH_6"],
        ["GATE_DEADSIDE_ASYLUM", "BATON", "GAD2_WALK"],
    ],
    "LE_Gad3.cut": "GATE_DEADSIDE_BLOOD",
}

# Spoke folder → the primary region connected directly from Deadside Marrow Gates.
# Sub-regions (Cathedral, Engine Block, etc.) cascade from here via
# their own internal connections and are handled in regions.build_level_rules.
SPOKE_FOLDER_TO_PRIMARY_REGION: dict[str, str] = {
    "wastland":  "Deadside - Wasteland",
    "asylum":    "Asylum: Gateways",
    "ah1cagew":  "Asylum: Cageways",
    "ah2playr":  "Asylum: Playrooms",
    "ah3lavad":  "Asylum: Lavaducts",
    "ah4fogom":  "Asylum: The Fogometers",
    "t1tchgad":  "Temple of Fire (Toucher)",
    "t2wlkgad":  "Temple of Prophecy (Marcher)",
    "t3swmgad":  "Temple of Blood (Nager)",
}

# DKE spoke_arrival tag → Engine Block sub-region name.
DKE_ARRIVAL_TO_REGION: dict[str, str] = {
    "ASYS4_ARIVE_TMENT": "Asylum: Engine Block - Queens",
    "ASYS4_ARIVE_PRISN": "Asylum: Engine Block - Prison",
    "ASYS4_ARIVE_UGRND": "Asylum: Engine Block - London",
    "ASYS4_ARIVE_FLORI": "Asylum: Engine Block - Florida",
    "ASYS4_ARIVE_MOJAV": "Asylum: Engine Block - Salvage",
}

# Can the player complete a liveside level and use its soul gate?
# Becomes the access rule for a Deadside spoke when a soul gate leads there
# in cross_hub mode. Uses R.{level}() which already encodes Night + items.
LIVESIDE_COMPLETION_RULES: dict[str, callable] = {
    "tenement": lambda state, player: R.queens(state, player),
    "prison":   lambda state, player: R.prison(state, player),
    "uground":  lambda state, player: R.london(state, player),
    "florida":  lambda state, player: R.florida(state, player),
    "salvage":  lambda state, player: R.salvage(state, player),
}
