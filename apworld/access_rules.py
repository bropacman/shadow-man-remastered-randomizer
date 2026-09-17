"""
access_rules.py
───────────────
Named rule functions for Shadow Man Remastered Archipelago randomizer.
"""

from __future__ import annotations
try:
    from BaseClasses import CollectionState
except ImportError:
    CollectionState = object  # type: ignore
from .constants import GATE_VANILLA_SL

# ── Item name constants ───────────────────────────────────────────────────────
#
# BUG FIX (2026-07-24): these used to be the raw RSC engine identifiers
# (e.g. "RSC_X_POIGNE"). items.py's 2026-07-22 friendly-name rework changed
# what AP actually names these items in the itempool (item_table's keys,
# e.g. "Poigne") -- state.has()/state.count() match on Item.name, so every
# rule below was silently checking a name that can never exist in this
# world's item pool anymore, i.e. state.has(_POIGNE, player) was always
# False. This made R.night()/R.poigne()/R.eng_key()/R.prison_key_card() (and
# anything gated behind them) permanently unreachable -- traced via a full
# Generate.py repro + Fill.py instrumentation showing all 20 boss/true_form
# Dark Soul events AND "Defeat Legion" stuck simultaneously with the player
# already holding every unique item by name in balancing_state.prog_items,
# yet R.eng_key()-gated regions (Asylum: Engine Block, Playrooms, Fogometers,
# Experimentation Rooms) still unreachable -- only explainable if eng_key()
# itself was structurally broken, not a fill/placement issue. See also
# schematic()'s matching fix below (same root cause, separate call site).
_ECLIPSER_1      = "La Lune"
_ECLIPSER_2      = "La Lame"
_ECLIPSER_3      = "Le Soleil"
_BATON           = "Baton"
_FLAMBEAU        = "Flambeau"
_MARTEAU         = "Marteau"
_CALABASH        = "Calabash"
_POIGNE          = "Poigne"
_ENG_KEY         = "Engineers Key"
_PRISON_KEY_CARD = "Prison Key Card"

# Active gate→SL mapping for the current seed.
# Standalone uses this via set_gate_remap().
# AP worlds bypass this entirely via _gate_sl parameter.
_current_gate_sl: dict[str, int] = dict(GATE_VANILLA_SL)


def set_gate_remap(gate_remap: dict[str, int]) -> None:
    """
    Standalone only. Called once by patcher.py after _shuffle_gates() runs.
    AP worlds do not call this — they pass _gate_sl directly to R.gate().
    """
    _current_gate_sl.clear()
    _current_gate_sl.update(GATE_VANILLA_SL)
    _current_gate_sl.update(gate_remap)


# ── Gate dependencies ─────────────────────────────────────────────────────────

_LOWER_DEADSIDE_ROUTES: list[list[str]] = [
    ["GATE_DEADSIDE_PATH_6"],
    ["GATE_DEADSIDE_PATH_7"],
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
    # NOTE: GATE_DEADSIDE_CAGEWAYS / GATE_DEADSIDE_PLAYROOMS deliberately
    # stay single-string (front-door-only) entries here. They ALSO have two
    # backtrack routes (confirmed live by Jon 2026-08-03: reaching the lower
    # Deadside cluster via Route B or Route C lets you walk back UP into
    # Cageways/Playrooms from behind, bypassing that gate's own SL entirely
    # — not just its ancestor chain). That can't be modeled here, because
    # R.gate() always re-enforces a gate's OWN soul threshold (step 2) on
    # top of GATE_DEPENDENCIES (step 1) regardless of which route satisfied
    # step 1 — correct for the four _LOWER_DEADSIDE_ROUTES destinations
    # above, wrong for Cageways/Playrooms. Modeled instead as a hand-built
    # OR at the region-connection level — see CAGEWAYS_ROUTES /
    # PLAYROOMS_ROUTES below, and their use in regions.py. Mirrored from
    # the standalone randomizer's own access_rules.py — see that file's
    # matching comment.
}


# ── Soul helpers ──────────────────────────────────────────────────────────────

# Vanilla SL→soul-count mapping. Used whenever a call site doesn't pass an
# explicit _soul_thresholds override (see _soul_level() below) — i.e. every
# call site until 2026-07-20, when soul_threshold_mode's resolved thresholds
# (self.sl_thresholds in __init__.py) started getting threaded through
# make_entrance_rule()/make_location_rule()/R.gate()/R.sl*() the same way
# gate_values/_gate_sl already was. Before that fix, AP's fill/logic graph
# always assumed vanilla thresholds regardless of soul_threshold_mode,
# which could desync AP-legal placements from what's actually reachable
# in-game — see options.py's SoulThresholdMode docstring.
# Vanilla Fogometers-door Cadeaux requirement (see cadeaux_666() below).
# Matches FOGOMETERS_REQUIRED_VANILLA in the standalone repo's cadeaux_patch.py.
CADEAUX_666_VANILLA = 666

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
    return state.count("Dark Soul", player)

def _soul_level(state: CollectionState, player: int, level: int,
                 _soul_thresholds: dict[int, int] | None = None) -> bool:
    """
    _soul_thresholds: optional per-call override for the SL→soul-count
                       mapping, mirroring _gate_sl's role in R.gate().
                       None (the default) uses vanilla _SOUL_THRESHOLDS.
    """
    thresholds = _soul_thresholds if _soul_thresholds is not None else _SOUL_THRESHOLDS
    threshold = thresholds[level]
    return True if threshold == 0 else _count_souls(state, player) >= threshold


def _night(state: CollectionState, player: int) -> bool:
    return (
        state.has(_ECLIPSER_1, player)
        and state.has(_ECLIPSER_2, player)
        and state.has(_ECLIPSER_3, player)
    )


def _gate_sl_only(gate_id: str, state: CollectionState, player: int,
                   _gate_sl: dict[str, int] | None = None,
                   _soul_thresholds: dict[int, int] | None = None) -> bool:
    """
    Check only the soul threshold for a gate — no GATE_DEPENDENCIES chain
    walk.

    NOT USED by make_portal_rule() as of the 2026-07-21 fix (kept defined
    for reference/manual debugging only). It used to be, on the theory that
    the ancestor-gate chain only matters for reaching a portal's VANILLA
    destination and stops mattering once that destination is shuffled —
    wrong: the chain models the physical walk to the portal's own location
    in Marrow Gates, which is unaffected by where its cutscene points
    afterward, and still needs walking every time. Confirmed live: this let
    a shuffled Temple of Blood entrance into logic several spheres too
    early, checked against a single low ancestor gate instead of the full
    chain up through its own real gate. make_portal_rule() now uses
    R.gate() instead, matching how the vanilla (unshuffled) portal
    connections in regions.py already evaluate these same gate ids.
    """
    sl_map = _gate_sl if _gate_sl is not None else _current_gate_sl
    sl = sl_map.get(gate_id, GATE_VANILLA_SL.get(gate_id, 0))
    return _soul_level(state, player, sl, _soul_thresholds)


# ── Entrance randomizer ───────────────────────────────────────────────────────
# BUG FIX (2026-07-21, user-caught): this table used to map each of the 9
# Deadside hub-portal cutscene files to a gate based on a "physical position,
# independent of destination" theory — the idea being that GATE_DEPENDENCIES'
# ancestor-chain walk models "you must have already satisfied earlier gates
# because you physically pass them on the way to the DESTINATION region",
# which stops being meaningful once a portal's destination is shuffled to an
# unrelated region. That reasoning doesn't hold: the ancestor chain isn't
# about the destination at all, it's about the physical walk through Marrow
# Gates to reach a given portal's OWN location, which is completely
# unaffected by where that portal's cutscene happens to point afterward.
# Confirmed live: a seed shuffled LE_Cage.cut (Cageways) to lead to Temple of
# Blood, and the old table + _gate_sl_only (leaf-only, no chain walk) let
# Temple of Blood's contents into logic behind only GATE_DEADSIDE_PATH_3 (a
# seed-rolled SL3, 6 souls) instead of the full chain up through
# GATE_DEADSIDE_CAGEWAYS itself (SL7, 34 souls that seed) — reachable many
# spheres too early.
#
# Fixed by mapping each portal to the exact same gate id it already uses in
# the vanilla (entrance_shuffle=None) gate_connections list in regions.py —
# that list is the proven-correct source for "what gates this portal", full
# stop, regardless of entrance shuffle. make_portal_rule() below was also
# switched from _gate_sl_only to R.gate() so the full ancestor chain (per
# GATE_DEPENDENCIES) gets walked, exactly like the vanilla list already does.
# Only Temple of Prophecy (LE_Gad2.cut) needs the route-list form here: its
# vanilla connection isn't a single named gate, it's a hand-built
# "PATH_7, or (CAGEWAYS and PLAYROOMS and PATH_6), or (ASYLUM and BATON and
# GAD2_WALK)" OR-condition — this reproduces that exact expression, still
# evaluated via R.gate() per token. Third route added 2026-07-26 (Jon,
# confirmed live in-game — see regions.py's matching TEMPLE_PROPHECY
# connection comment for the full story): this table previously only had
# the first two routes, missing the same Asylum+Baton+Gad2 shortcut
# _LOWER_DEADSIDE_ROUTES already grants Lavaducts/La Lame/Blood/Fogometers.
# LE_Cage.cut/LE_Play.cut switched to the route-list form too on 2026-08-03
# — see CAGEWAYS_ROUTES/PLAYROOMS_ROUTES below; unlike Temple of Prophecy
# their front door is a real named gate with its own SL, so route 1 of each
# list is that single gate (full R.gate() check, own SL enforced) and
# routes 2/3 are the backtrack bypass. Mirrored from the standalone
# randomizer's own access_rules.py — see that file's matching comment.

# Cageways and Playrooms are each reachable a second/third way, alongside
# their own front door: once you land in the lower Deadside cluster via
# Route B (Path 7) or Route C (Asylum + Baton + Gad2 lava walk), you can
# freely backtrack UP into Cageways and Playrooms from behind, bypassing
# that gate's own soul-level requirement entirely — confirmed live by Jon
# 2026-08-03. Route A (Path 6) is deliberately NOT included: reaching
# Path 6 already requires having passed through Cageways/Playrooms on the
# way there, so adding it back as an alternate route into either of them
# would be circular. Used below for both the vanilla marrow connections
# (via make_portal_rule, same factory entrance-shuffle already uses) and
# entrance-shuffle (via DEADSIDE_PORTAL_GATE). Also reused by fill.py's
# REGION_GATES heuristic table.
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


def make_portal_rule(gate: str | list, gate_values: dict[str, int], player: int,
                      sl_thresholds: dict[int, int] | None = None):
    """
    Build the AP entrance rule for a Deadside portal under entrance shuffle,
    given its DEADSIDE_PORTAL_GATE entry — either a single gate_id string,
    or a route list of alternative token routes (each route a list of
    "GATE_*" ids, "BATON", or "GAD2_WALK" — same shape _LOWER_DEADSIDE_ROUTES
    uses, reused here for Temple of Prophecy's hand-built OR-condition).
    Ported from the standalone's regions.py-local _make_routes_rule
    (2026-07-21), generalized into a single factory alongside
    make_entrance_rule/make_location_rule.

    Uses R.gate() (not _gate_sl_only) as of the 2026-07-21 fix — see
    DEADSIDE_PORTAL_GATE's comment block for why: the ancestor-gate chain
    (GATE_DEPENDENCIES) is about the physical walk to reach a portal's own
    location in Marrow Gates, not about its (possibly shuffled) destination,
    so it needs to be walked here exactly like the vanilla connections list
    already walks it via R.gate().
    """
    if isinstance(gate, str):
        gate_id = gate
        return lambda state: R.gate(
            gate_id, state, player, _gate_sl=gate_values, _soul_thresholds=sl_thresholds)

    routes = gate

    def _token(t: str, state) -> bool:
        if t.startswith("GATE_"):
            return R.gate(t, state, player, _gate_sl=gate_values,
                           _soul_thresholds=sl_thresholds)
        if t == "BATON":
            return R.baton(state, player)
        if t == "GAD2_WALK":
            return R.gad2_walk(state, player)
        return False

    return lambda state: any(all(_token(t, state) for t in route) for route in routes)


# ── Rule namespace ────────────────────────────────────────────────────────────

class _Rules:

    def gate(self, gate_id: str, state: CollectionState, player: int,
             _gate_sl: dict[str, int] | None = None,
             _soul_thresholds: dict[int, int] | None = None) -> bool:
        """
        Check whether the player can pass a named coffin gate.
        _gate_sl: optional per-call override for the gate→SL mapping.
                  Standalone passes None (uses module-level _current_gate_sl).
                  AP world passes self.gate_values to avoid global state mutation.
        _soul_thresholds: optional per-call override for the SL→soul-count
                  mapping, same idea — AP world passes self.sl_thresholds.
        """
        dep = GATE_DEPENDENCIES.get(gate_id)
        if dep is not None:
            if isinstance(dep, str):
                if not self.gate(dep, state, player, _gate_sl, _soul_thresholds):
                    return False
            else:
                def _token(t: str) -> bool:
                    if t.startswith("GATE_"):
                        return self.gate(t, state, player, _gate_sl, _soul_thresholds)
                    if t == "BATON":
                        return self.baton(state, player)
                    if t == "GAD2_WALK":
                        return self.gad2_walk(state, player)
                    return False
                if not any(all(_token(t) for t in route) for route in dep):
                    return False

        sl_map = _gate_sl if _gate_sl is not None else _current_gate_sl
        sl = sl_map.get(gate_id, GATE_VANILLA_SL.get(gate_id, 0))
        return _soul_level(state, player, sl, _soul_thresholds)

    def flambeau(self, state: CollectionState, player: int,
                 _soul_thresholds: dict[int, int] | None = None) -> bool:
        """
        BUG FIX (2026-08-09, Jon's report): Flambeau doesn't actually work
        in-game until the player has Voodoo Power (SL1) -- physically
        holding the item isn't enough, same real-game requirement as
        Calabash below. Was checking state.has() alone, which could put
        Flambeau-gated checks in logic before the player could reach SL1
        in a genuinely adversarial fill (e.g. a shuffled seed where SL1
        sits behind more than the vanilla 1 soul and Flambeau itself
        happens to get placed very early). _soul_thresholds threads
        through this seed's own resolved self.sl_thresholds via BoundR
        below (not the vanilla default) -- same reasoning as the
        create_items() Dark-Soul-provisioning fix earlier the same day:
        using the wrong (vanilla) SL1 value here would silently reproduce
        that exact class of bug for these two items specifically.
        """
        return state.has(_FLAMBEAU, player) and _soul_level(state, player, 1, _soul_thresholds)

    def baton(self, state: CollectionState, player: int) -> bool:
        return state.has(_BATON, player)

    def calabash(self, state: CollectionState, player: int,
                 _soul_thresholds: dict[int, int] | None = None) -> bool:
        """See flambeau() above — same SL1/Voodoo Power requirement, same
        fix, same date."""
        return state.has(_CALABASH, player) and _soul_level(state, player, 1, _soul_thresholds)

    def marteau(self, state: CollectionState, player: int) -> bool:
        return state.has(_MARTEAU, player)

    def poigne(self, state: CollectionState, player: int) -> bool:
        return state.has(_POIGNE, player)

    def gad1_hand(self, state, player) -> bool:
        return state.count("Gad Power", player) >= 1

    def gad2_walk(self, state, player) -> bool:
        return state.count("Gad Power", player) >= 2

    def gad3_swim(self, state, player) -> bool:
        return state.count("Gad Power", player) >= 3

    def eng_key(self, state: CollectionState, player: int) -> bool:
        return state.has(_ENG_KEY, player)

    def prison_key_card(self, state: CollectionState, player: int) -> bool:
        return state.has(_PRISON_KEY_CARD, player)

    def x3_accumulator(self, state: CollectionState, player: int) -> bool:
        return state.count("Accumulator", player) >= 3

    def schematic(self, state, player) -> bool:
        """Player has Jack's Schematic. Referenced by pistons() below when
        piston_combos is on — see that method and options.py's PistonCombos
        docstring (2026-07-21).

        BUG FIX (2026-07-24): was checking state.has("RSC_X_JACKS_SCHEMATIC",
        ...) — the pre-rework RSC identifier. items.py's item_table names
        this item "Jacks Schematic" (see its 2026-07-22 friendly-name
        rework), so this always evaluated False, making the piston_combos
        completion condition permanently unreachable whenever that option
        was on. Same root cause as the _ECLIPSER_1/_BATON/_POIGNE/_ENG_KEY/
        _PRISON_KEY_CARD constants above."""
        return state.has("Jacks Schematic", player)

    def cadeaux_666(self, state, player, _cadeaux_required: int | None = None,
                     _cadeaux_trackable: bool = True) -> bool:
        """
        Gate on actually having collected enough Cadeaux, not an
        unconditional pass. FIXED 2026-07-24 (Jon's report) -- this used
        to just `return True`, so the Light Soul location and the 5
        ah4fogom Painkiller/Sentinel enemy checks that gate_expr-reference
        R.cadeaux_666() were treated as always reachable regardless of
        actual Cadeaux count, same class of bug as the soul-threshold
        desync fixed 2026-07-20 (see _soul_level()'s docstring above).

        _cadeaux_required: optional per-call override for the required
                            count, mirroring _soul_thresholds/_gate_sl's
                            role elsewhere in this file. None (the
                            default) uses CADEAUX_666_VANILLA. AP worlds
                            pass self.options.fogometers_cadeaux_required
                            via BoundR — see BoundR.cadeaux_666() below.

        _cadeaux_trackable: when False, falls back to the old unconditional
                            True. This matters because Cadeaux locations
                            are only AP-tracked when Cadeauxsanity is on
                            (see __init__.py's _cadeaux_identity_map
                            docstring) — with Cadeauxsanity off, no
                            "Cadeaux"-family AP item exists
                            in the pool at all, so
                            state.count_group("Cadeaux", player) could
                            never reach the requirement and this location
                            would become permanently unreachable in logic,
                            even though a real player can still collect the
                            (un-randomized, native) vanilla cadeaux and
                            open the door same as always. AP worlds pass
                            self._options.cadeauxsanity via BoundR — see
                            BoundR.cadeaux_666() below.
        """
        if not _cadeaux_trackable:
            return True
        required = (_cadeaux_required if _cadeaux_required is not None
                    else CADEAUX_666_VANILLA)
        # Cadeaux Bundle Size (2026-07-27): "Cadeaux" is no longer the only
        # concrete item name this can show up as -- a bundle representative
        # worth more than one physical cadeaux is "Cadeaux Bundle x{N}"
        # instead (see items.py), a distinct item per denomination so that
        # value survives a cross-world send. state.count_group() sums
        # across every name in the "Cadeaux" item_name_group (see
        # __init__.py's ShadowManWorld.item_name_groups) instead of
        # matching one literal name, so this still counts every
        # Cadeaux-family item the player has, regardless of denomination.
        # `required` itself is unchanged semantics -- __init__.py's
        # generate_early() already caps it (cadeaux_required_logic) at how
        # many Cadeaux-family items can ever exist, i.e. a COUNT of items,
        # not a sum of their weights -- matching what count_group() itself
        # returns (item occurrences, not their individual "worth").
        return state.count_group("Cadeaux", player) >= required

    def night(self, state: CollectionState, player: int) -> bool:
        return _night(state, player)

    # NOTE: these five are currently dead code in AP (nothing calls them —
    # liveside entry is enforced by regions.py entrance rules). Kept at the
    # standalone repo's flat >=5 threshold for parity. Standalone's
    # can_reach_liveside() (also flat >=5 there) was removed here entirely;
    # the old AP copy carried a stale recursive "count > regions reached"
    # formula that was never called and is ordering-unsafe.

    def florida(self, state, player) -> bool:
        return _night(state, player) and state.count("Retractor", player) >= 5

    def london(self, state, player) -> bool:
        return _night(state, player) and state.count("Retractor", player) >= 5

    def queens(self, state, player) -> bool:
        return (
                _night(state, player)
                and state.has(_POIGNE, player)
                and state.count("Retractor", player) >= 5
        )

    def prison(self, state, player) -> bool:
        return (
                _night(state, player)
                and state.has(_PRISON_KEY_CARD, player)
                and state.count("Retractor", player) >= 5
        )

    def salvage(self, state, player) -> bool:
        return (
                _night(state, player)
                and self.gad3_swim(state, player)
                and state.count("Retractor", player) >= 5
        )

    def pistons(self, state, player, require_schematic: bool = False) -> bool:
        """
        require_schematic: True when this world's PistonCombos option is on
        (see options.py) — the piston combinations are randomized per-seed
        and only readable in-game via Jack's Schematic's journal entry, so
        beating the game (and every location gated behind PISTONS —
        extracted_locations.py/extracted_enemy_locations.py's as4dkeng
        barrels + Legion) requires holding it first. Defaults to False so
        direct 2-arg calls (the "R.pistons(state, player)" gate_expr strings
        baked into those two extracted_*.py files, eval'd via BoundR — see
        BoundR.pistons() below, which is what actually supplies the live
        per-world value) keep working unchanged when the option is off.
        """
        sections = [
            "Asylum: Engine Block - London",
            "Asylum: Engine Block - Prison",
            "Asylum: Engine Block - Salvage",
            "Asylum: Engine Block - Queens",
            "Asylum: Engine Block - Florida"
        ]
        if require_schematic and not self.schematic(state, player):
            return False
        return all(state.can_reach(s, "Region", player) for s in sections)

    # _soul_thresholds: optional per-call override, same as R.gate() — see
    # _soul_level()'s docstring. AP world's regions.py passes
    # self.sl_thresholds for the one caller that currently uses these
    # directly (the liveside SL2 check). No gate_expr string in
    # extracted_locations.py calls R.sl0-10() today (only R.gate()), so
    # BoundR doesn't proxy these with an override yet — if a future
    # gate_expr ever adds an "R.slN(...)" call, BoundR needs a matching
    # override added too, the same way it already has one for gate().

    def sl0(self, state: CollectionState, player: int,
            _soul_thresholds: dict[int, int] | None = None) -> bool:
        return True

    def sl1(self, state: CollectionState, player: int,
            _soul_thresholds: dict[int, int] | None = None) -> bool:
        return _soul_level(state, player, 1, _soul_thresholds)

    def sl2(self, state: CollectionState, player: int,
            _soul_thresholds: dict[int, int] | None = None) -> bool:
        return _soul_level(state, player, 2, _soul_thresholds)

    def sl3(self, state: CollectionState, player: int,
            _soul_thresholds: dict[int, int] | None = None) -> bool:
        return _soul_level(state, player, 3, _soul_thresholds)

    def sl4(self, state: CollectionState, player: int,
            _soul_thresholds: dict[int, int] | None = None) -> bool:
        return _soul_level(state, player, 4, _soul_thresholds)

    def sl5(self, state: CollectionState, player: int,
            _soul_thresholds: dict[int, int] | None = None) -> bool:
        return _soul_level(state, player, 5, _soul_thresholds)

    def sl6(self, state: CollectionState, player: int,
            _soul_thresholds: dict[int, int] | None = None) -> bool:
        return _soul_level(state, player, 6, _soul_thresholds)

    def sl7(self, state: CollectionState, player: int,
            _soul_thresholds: dict[int, int] | None = None) -> bool:
        return _soul_level(state, player, 7, _soul_thresholds)

    def sl8(self, state: CollectionState, player: int,
            _soul_thresholds: dict[int, int] | None = None) -> bool:
        return _soul_level(state, player, 8, _soul_thresholds)

    def sl9(self, state: CollectionState, player: int,
            _soul_thresholds: dict[int, int] | None = None) -> bool:
        return _soul_level(state, player, 9, _soul_thresholds)

    def sl10(self, state: CollectionState, player: int,
             _soul_thresholds: dict[int, int] | None = None) -> bool:
        return _soul_level(state, player, 10, _soul_thresholds)

    # region_access and engine_section_logic removed — handled by AP regions directly


# ── Singleton ─────────────────────────────────────────────────────────────────
R = _Rules()


# ── AP helpers (module-level, use R singleton) ────────────────────────────────

class BoundR:
    """
    Wraps the R singleton with a fixed gate_values (+ sl_thresholds,
    piston_combos) dict/flag for AP worlds. Used when evaluating gate_expr
    strings so R.gate()/R.pistons() route through the per-world overrides
    instead of the module-level _current_gate_sl / vanilla _SOUL_THRESHOLDS
    / pistons()'s require_schematic default.
    """
    def __init__(self, gate_values: dict[str, int], player: int,
                 sl_thresholds: dict[int, int] | None = None,
                 piston_combos: bool = False,
                 cadeaux_required: int | None = None,
                 cadeaux_trackable: bool = True):
        self._gv = gate_values
        self._p  = player
        self._st = sl_thresholds
        self._pc = piston_combos
        self._cr = cadeaux_required
        self._ct = cadeaux_trackable

    def __getattr__(self, name):
        return getattr(R, name)

    def gate(self, gate_id: str, state, player: int) -> bool:
        return R.gate(gate_id, state, player, _gate_sl=self._gv, _soul_thresholds=self._st)

    def cadeaux_666(self, state, player) -> bool:
        return R.cadeaux_666(state, player, _cadeaux_required=self._cr,
                              _cadeaux_trackable=self._ct)

    def flambeau(self, state, player: int) -> bool:
        # 2026-08-09: without this override, gate_expr strings' fixed 2-arg
        # "R.flambeau(state, player)" calls would fall through __getattr__
        # to the bare R singleton and silently default to vanilla SL1
        # (=1 soul) instead of this seed's own resolved thresholds — same
        # bug class as gate()/cadeaux_666() above already guard against.
        return R.flambeau(state, player, _soul_thresholds=self._st)

    def calabash(self, state, player: int) -> bool:
        return R.calabash(state, player, _soul_thresholds=self._st)

    def pistons(self, state, player) -> bool:
        # gate_expr strings (extracted_locations.py/extracted_enemy_locations.py's
        # as4dkeng barrels + Legion) always call "R.pistons(state, player)" —
        # a fixed 2-arg call, since eval()'s namespace only supplies state/player
        # (see make_location_rule() below). This override supplies this
        # world's piston_combos flag the same way gate() supplies gate_values.
        return R.pistons(state, player, require_schematic=self._pc)


def make_entrance_rule(gate_id: str, gate_values: dict[str, int], player: int,
                        sl_thresholds: dict[int, int] | None = None):
    """
    Returns a CollectionState → bool lambda for an AP entrance.
    Uses _gate_sl/_soul_thresholds overrides so gate shuffle and
    soul_threshold_mode are both per-world safe.
    """
    return lambda state: R.gate(gate_id, state, player, _gate_sl=gate_values,
                                 _soul_thresholds=sl_thresholds)


def make_location_rule(gate_expr: str, gate_values: dict[str, int], player: int,
                        sl_thresholds: dict[int, int] | None = None,
                        piston_combos: bool = False,
                        cadeaux_required: int | None = None,
                        cadeaux_trackable: bool = True):
    """
    Returns a CollectionState → bool lambda for an AP location rule.
    Evaluates the gate_expr string with a BoundR so R.gate()/R.pistons()/
    R.cadeaux_666() use the per-world gate_values/sl_thresholds/
    piston_combos/cadeaux_required/cadeaux_trackable instead of
    module-level defaults, the require_schematic=False default, or
    CADEAUX_666_VANILLA always-trackable behavior.
    """
    bound_r = BoundR(gate_values, player, sl_thresholds, piston_combos,
                      cadeaux_required, cadeaux_trackable)
    return lambda state, expr=gate_expr, br=bound_r, p=player: eval(
        expr, {"R": br, "state": state, "player": p}
    )
