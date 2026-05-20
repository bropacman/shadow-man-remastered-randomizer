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

# Active gate→SL mapping for the current seed.
# Populated at generation time by set_gate_remap().
# Defaults to vanilla until explicitly set.
_current_gate_sl: dict[str, int] = dict(GATE_VANILLA_SL)


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
}


# ── Soul helpers ──────────────────────────────────────────────────────────────

_SOUL_THRESHOLDS: dict[int, int] = {
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


def _count_souls(state, player) -> int:
    return state.count("_souls", player)


def _soul_level(state: CollectionState, player: int, level: int) -> bool:
    threshold = _SOUL_THRESHOLDS[level]
    return True if threshold == 0 else _count_souls(state, player) >= threshold


def _night(state: CollectionState, player: int) -> bool:
    return (
        state.has(_ECLIPSER_1, player)
        and state.has(_ECLIPSER_2, player)
        and state.has(_ECLIPSER_3, player)
    )

def _gate_sl_only(gate_id: str, state, player: int) -> bool:
    """Check only the soul threshold for a gate — no dependency chain.
    Used by entrance shuffle logic where portals bypass physical Deadside traversal."""
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
        return state.has(_FLAMBEAU, player)

    def baton(self, state: CollectionState, player: int) -> bool:
        return state.has(_BATON, player)

    def calabash(self, state: CollectionState, player: int) -> bool:
        return state.has(_CALABASH, player)

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
        return state.count("_retractors", player) >= 5

    # ── Liveside level completions ────────────────────────────────────────────

    def florida(self, state: CollectionState, player: int) -> bool:
        return _night(state, player) and state.count("_retractors", player) >= 5

    def london(self, state: CollectionState, player: int) -> bool:
        return _night(state, player) and state.count("_retractors", player) >= 5

    def queens(self, state: CollectionState, player: int) -> bool:
        return (
                _night(state, player)
                and state.has(_POIGNE, player)
                and state.count("_retractors", player) >= 5
        )

    def prison(self, state: CollectionState, player: int) -> bool:
        return (
                _night(state, player)
                and state.has(_PRISON_KEY_CARD, player)
                and state.count("_retractors", player) >= 5
        )

    def salvage(self, state: CollectionState, player: int) -> bool:
        return (
                _night(state, player)
                and self.gad3_swim(state, player)
                and state.count("_retractors", player) >= 5
        )

    def pistons(self, state, player) -> bool:
        # These must match the level_region column in your CSV exactly
        sections = [
            "Asylum: Engine Block - London",
            "Asylum: Engine Block - Prison",
            "Asylum: Engine Block - Salvage",
            "Asylum: Engine Block - Queens",
            "Asylum: Engine Block - Florida"
        ]
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

# Which coffin gate in the Marrow Gates hub physically guards each portal?
# Fixed game geography — does not change with entrance randomization.
DEADSIDE_PORTAL_GATE: dict[str, str | list] = {
    "LE_Wast.cut": "GATE_DEADSIDE_MARROW",
    "LE_Asy1.cut": "GATE_DEADSIDE_WASTELAND",
    "LE_Gad1.cut": "GATE_DEADSIDE_PATH_3",
    "LE_Cage.cut": "GATE_DEADSIDE_PATH_3",
    "LE_Play.cut": "GATE_DEADSIDE_CAGEWAYS",
    "LE_Lava.cut": _LOWER_DEADSIDE_ROUTES,
    "LE_Fog.cut":  _LOWER_DEADSIDE_ROUTES,
    "LE_Gad2.cut": _LOWER_DEADSIDE_ROUTES,
    "LE_Gad3.cut": _LOWER_DEADSIDE_ROUTES,
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
