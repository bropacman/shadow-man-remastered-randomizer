"""
regions.py
──────────
Builds the Archipelago region graph for Shadow Man Remastered.

Structure:
    Menu
     └─→ (free)      Louisiana Swampland
     └─→ (free)      Deadside Marrow Gates
     └─→ GATE_DEADSIDE_WASTELAND    Deadside - Wasteland
     └─→ GATE_DEADSIDE_ASYLUM       Asylum: Gateways
     └─→ GATE_DEADSIDE_ASYLUM       Asylum: Cathedral of Pain
     └─→ GATE_DEADSIDE_ASYLUM       Asylum: Experimentation Rooms
     └─→ 5x Retractor               Down Street Station, London
     └─→ 5x Retractor               Gardelle County Jail, Texas
     └─→ 5x Retractor               Salvage Yard, Mojave Desert
     └─→ 5x Retractor               Mordant Street, Queens, NY
     └─→ 5x Retractor               Summer Camp, Florida
     └─→ GATE_DEADSIDE_PATH_3       Temple of Fire (Toucher)
     └─→ GATE_DEADSIDE_CAGEWAYS     Asylum: Cageways
     └─→ GATE_DEADSIDE_CAGEWAYS     Asylum: Engine Block
     └─→ GATE_DEADSIDE_PLAYROOMS    Asylum: Playrooms
     └─→ PATH_7 | (CAGEWAYS+PLAYROOMS+PATH_6)   Temple of Prophecy (Marcher)
     └─→ GATE_DEADSIDE_LAVADUCTS    Asylum: Lavaducts
     └─→ GATE_DEADSIDE_BLOOD        Temple of Blood (Nager)
     └─→ GATE_FOGOMETERS_INTERIOR   Asylum: The Fogometers

# Liveside levels require all 5 retractors — the game gates entry on having
# collected one retractor per level already visited.

All deadside/temple region gates resolve through R.gate(gate_id, ...) which
looks up the current shuffled SL requirement from access_rules._current_gate_sl.
"""

from __future__ import annotations

from BaseClasses import Region, MultiWorld, LocationProgressType
from extracted_locations import (
    RAW_LOCATIONS,
    FREE_LOCATIONS,
    GATED_LOCATIONS,
    GATES_BY_REGION,
)
from access_rules import R, set_gate_remap, R as _R, _gate_sl_only, CAGEWAYS_ROUTES, PLAYROOMS_ROUTES
from locations import ShadowManLocation   # AP Location subclass — defined in locations.py


# ── Region name constants ─────────────────────────────────────────────────────

MENU                      = "Menu"
LOUISIANA_SWAMPLAND       = "Louisiana Swampland"
DEADSIDE_MARROW_GATES     = "Deadside Marrow Gates"
DEADSIDE_WASTELAND        = "Deadside - Wasteland"
ASYLUM_GATEWAYS           = "Asylum: Gateways"
ASYLUM_CATHEDRAL          = "Asylum: Cathedral of Pain"
ASYLUM_EXPERIMENTATION    = "Asylum: Experimentation Rooms"
LIVESIDE_LONDON           = "Down Street Station, London"
LIVESIDE_PRISON           = "Gardelle County Jail, Texas"
LIVESIDE_FLORIDA          = "Summer Camp, Florida"
LIVESIDE_SALVAGE          = "Salvage Yard, Mojave Desert"
LIVESIDE_QUEENS           = "Mordant Street, Queens, NY"
TEMPLE_FIRE               = "Temple of Fire (Toucher)"
ASYLUM_CAGEWAYS           = "Asylum: Cageways"
ASYLUM_ENGINE_BLOCK       = "Asylum: Engine Block"
ASYLUM_ENGINE_BLOCK_LONDON       = "Asylum: Engine Block - London"
ASYLUM_ENGINE_BLOCK_PRISON       = "Asylum: Engine Block - Prison"
ASYLUM_ENGINE_BLOCK_FLORIDA       = "Asylum: Engine Block - Florida"
ASYLUM_ENGINE_BLOCK_SALVAGE       = "Asylum: Engine Block - Salvage"
ASYLUM_ENGINE_BLOCK_QUEENS       = "Asylum: Engine Block - Queens"
ASYLUM_PLAYROOMS          = "Asylum: Playrooms"
TEMPLE_PROPHECY           = "Temple of Prophecy (Marcher)"
ASYLUM_LAVADUCTS          = "Asylum: Lavaducts"
TEMPLE_BLOOD              = "Temple of Blood (Nager)"
ASYLUM_FOGOMETERS         = "Asylum: The Fogometers"

ALL_REGIONS = [
    MENU,
    LOUISIANA_SWAMPLAND,
    DEADSIDE_MARROW_GATES,
    DEADSIDE_WASTELAND,
    ASYLUM_GATEWAYS,
    ASYLUM_CATHEDRAL,
    ASYLUM_EXPERIMENTATION,
    LIVESIDE_LONDON,
    LIVESIDE_PRISON,
    LIVESIDE_SALVAGE,
    LIVESIDE_QUEENS,
    TEMPLE_FIRE,
    ASYLUM_CAGEWAYS,
    ASYLUM_ENGINE_BLOCK,
    ASYLUM_ENGINE_BLOCK_LONDON,
    ASYLUM_ENGINE_BLOCK_PRISON,
    ASYLUM_ENGINE_BLOCK_FLORIDA,
    ASYLUM_ENGINE_BLOCK_SALVAGE,
    ASYLUM_ENGINE_BLOCK_QUEENS,
    ASYLUM_PLAYROOMS,
    TEMPLE_PROPHECY,
    ASYLUM_LAVADUCTS,
    TEMPLE_BLOOD,
    ASYLUM_FOGOMETERS,
    LIVESIDE_FLORIDA,
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_region(name: str, multiworld: MultiWorld, player: int) -> Region:
    return Region(name, player, multiworld)


def _add_locations(region: Region, locs: list, player: int) -> None:
    """Add a list of RawLocations to an AP Region as ShadowManLocation objects."""
    for raw in locs:
        loc = ShadowManLocation(player, raw.loc_key, raw, region)
        region.locations.append(loc)


def _connect(
    source: Region,
    target: Region,
    rule=None,
) -> None:
    """Connect two regions, optionally with an access rule."""
    source.connect(target, rule=rule)


def _eval_routes(routes: list, state, player: int) -> bool:
    """
    Evaluate a route list (OR of AND-token-routes — same shape as
    access_rules._LOWER_DEADSIDE_ROUTES / CAGEWAYS_ROUTES / PLAYROOMS_ROUTES)
    against a CollectionState. Used for Cageways/Playrooms, which — unlike
    every gate in GATE_DEPENDENCIES — have a backtrack route that bypasses
    their own gate's SL entirely (confirmed live by Jon 2026-08-03), so
    they can't be expressed as a single R.gate() call the way every other
    region connection here is. Small, independent copy of the same
    tokenizer already duplicated in R.gate()'s step-1 check and this file's
    own entrance-shuffle _make_routes_rule — matches this codebase's
    existing pattern of small, separately-verified duplicates.
    """
    def _token(t: str) -> bool:
        if t.startswith("GATE_"):
            return R.gate(t, state, player)
        if t == "BATON":
            return R.baton(state, player)
        if t == "GAD2_WALK":
            return R.gad2_walk(state, player)
        return False
    return any(all(_token(t) for t in route) for route in routes)


# ── Sub-region builder ────────────────────────────────────────────────────────

def _build_sub_regions(
    level_region: Region,
    level_name: str,
    multiworld: MultiWorld,
    player: int,
) -> None:
    """
    For a given level region, create sub-regions for each unique gate expression
    and connect them from the level region with the appropriate rule.

    Free locations (gate_expr is None) are added directly to the level region.
    """
    # Add free locations directly to the level region
    for raw in FREE_LOCATIONS.get(level_name, []):
        loc = ShadowManLocation(player, raw.loc_key, raw, level_region)
        level_region.locations.append(loc)

    # Create one sub-region per unique gate_raw, connect with parsed rule
    for gate_raw, locs in GATED_LOCATIONS.get(level_name, {}).items():
        gate_expr = locs[0].gate_expr   # all locs in this bucket share the same expr
        sub_name  = f"{level_name} [{gate_raw}]"
        sub       = _make_region(sub_name, multiworld, player)

        _add_locations(sub, locs, player)
        multiworld.regions.append(sub)

        # Build the rule lambda from the pre-parsed gate_expr string.
        # gate_expr is a Python expression like:
        #   "R.gate('GATE_WASTELAND_ENSEIGNE', state, player)"
        #   "R.gate('GATE_DEADSIDE_PATH_6', state, player) and R.gad2_walk(state, player)"
        # We compile it into a callable via eval with R and player in scope.
        rule_fn = eval(  # noqa: S307
            f"lambda state: {gate_expr}",
            {"R": R, "player": player},
        )
        _connect(level_region, sub, rule=rule_fn)


# ── Main builder ──────────────────────────────────────────────────────────────

def create_regions(multiworld: MultiWorld, player: int) -> None:
    """
    Entry point called from __init__.py during world generation.
    Creates all regions, connects them from Menu, and populates locations.
    """
    # ── Create all top-level regions ─────────────────────────────────────────
    regions: dict[str, Region] = {}
    for name in ALL_REGIONS:
        r = _make_region(name, multiworld, player)
        regions[name] = r
        multiworld.regions.append(r)

    # ── Connect Menu → level regions ─────────────────────────────────────────

    menu = regions[MENU]
    swampland = regions[LOUISIANA_SWAMPLAND]
    marrow = regions[DEADSIDE_MARROW_GATES]

    # ── 1. The Starting Entry ────────────────────────────────────────────────
    # Only Swampland is free from the Menu
    _connect(menu, swampland, None)

    # ── 2. The Deadside Entry ────────────────────────────────────────────────
    # Swampland leads to Marrow Gates (The "physical" requirement)
    _connect(swampland, marrow, None)

    # ── 3. The Everything Else ───────────────────────────────────────────────
    # Every other region is now a sub-region of Deadside Marrow Gates.
    # The logic must now pass through Marrow Gates to reach these.
    #
    # Gate-locked regions use R.gate(gate_id, state, player) so the rule
    # automatically reflects whatever SL was shuffled onto that gate.
    #
    # Liveside levels use vanilla R.sl2() — their SL2 check is EXE-hardcoded
    # and is never affected by gate shuffling.
    #
    # Temple of Prophecy has three independent routes (third added
    # 2026-07-26 — Jon confirmed live in-game reaching it via Route C while
    # this only modeled A/B, matching the same Asylum+Baton+Gad2 shortcut
    # _LOWER_DEADSIDE_ROUTES already grants Lavaducts/La Lame/Blood/
    # Fogometers):
    #   Route A: GATE_DEADSIDE_PATH_7 alone
    #   Route B: GATE_DEADSIDE_CAGEWAYS + GATE_DEADSIDE_PLAYROOMS + GATE_DEADSIDE_PATH_6
    #   Route C: GATE_DEADSIDE_ASYLUM + Baton + Gad 2 (walk on lava)
    #
    # Cageways and Playrooms (2026-08-03 — Jon confirmed live): also each
    # reachable via Route B (Path 7 alone) or Route C (Asylum+Baton+Gad2),
    # on top of their own front door — backtracking from the lower Deadside
    # cluster bypasses that gate's own SL entirely, not just its ancestor
    # chain, so this can't be R.gate("GATE_DEADSIDE_CAGEWAYS"/"...PLAYROOMS")
    # alone anymore. See CAGEWAYS_ROUTES/PLAYROOMS_ROUTES in access_rules.py.

    connections: list[tuple[str, object]] = [
        # Coffin gate regions
        (DEADSIDE_WASTELAND, lambda state: R.gate("GATE_DEADSIDE_WASTELAND", state, player)),
        (ASYLUM_GATEWAYS, lambda state: R.gate("GATE_DEADSIDE_ASYLUM", state, player)),
        (TEMPLE_FIRE, lambda state: R.gate("GATE_DEADSIDE_PATH_3", state, player)),
        (ASYLUM_CAGEWAYS, lambda state: _eval_routes(CAGEWAYS_ROUTES, state, player)),
        (ASYLUM_PLAYROOMS, lambda state: _eval_routes(PLAYROOMS_ROUTES, state, player)),
        (ASYLUM_LAVADUCTS, lambda state: R.gate("GATE_DEADSIDE_LAVADUCTS", state, player)),
        (TEMPLE_BLOOD, lambda state: R.gate("GATE_DEADSIDE_BLOOD", state, player)),
        (ASYLUM_FOGOMETERS, lambda state: R.gate("GATE_DEADSIDE_FOGOMETERS", state, player)),
        (TEMPLE_PROPHECY, lambda state: (
                R.gate("GATE_DEADSIDE_PATH_7", state, player) or
                (R.gate("GATE_DEADSIDE_CAGEWAYS", state, player) and
                 R.gate("GATE_DEADSIDE_PLAYROOMS", state, player) and
                 R.gate("GATE_DEADSIDE_PATH_6", state, player)) or
                (R.gate("GATE_DEADSIDE_ASYLUM", state, player) and
                 R.baton(state, player) and
                 R.gad2_walk(state, player))
        ))
    ]

    for region_name, rule in connections:
        # Use 'marrow' as the source instead of 'menu'
        _connect(marrow, regions[region_name], rule=rule)

    # Eng Key OR Gad 2 (walk on lava) — confirmed live by Jon 2026-08-03:
    # Gad 2's lava-walk lets you bypass the Eng Key-locked door into
    # Cathedral of Pain / Experimentation Rooms entirely, same "physical
    # shortcut around a locked door" shape as the Cageways/Playrooms
    # backtrack above (just OR'd directly here rather than needing a
    # separate route-list constant, since there's no own-gate-SL
    # complication — Cathedral/Experimentation have no coffin gate of
    # their own, only the eng_key item check).
    _connect(regions[ASYLUM_GATEWAYS], regions[ASYLUM_CATHEDRAL], rule=lambda state: R.eng_key(state, player) or R.gad2_walk(state, player))
    _connect(regions[ASYLUM_GATEWAYS], regions[ASYLUM_EXPERIMENTATION], rule=lambda state: R.eng_key(state, player) or R.gad2_walk(state, player))
    # Cageways → Engine Block hub (2026-08-06 fix, Jon confirmed live: this
    # was modeled as a free connection once Cageways is reached -- WRONG,
    # reaching Engine Block from Cageways requires the Engineers Key, same
    # as the Gateways -> Cathedral/Experimentation path just above. Found
    # via a real AP-side UT false-positive report ("Asylum: Engine Block -
    # Barrel - Asylum 14" showing in-logic without eng_key), ported here
    # for parity per this file's own cross-repo drift warning.
    _connect(regions[ASYLUM_CAGEWAYS], regions[ASYLUM_ENGINE_BLOCK], rule=lambda state: R.eng_key(state, player))

    # 1. Define Level -> (Engine Section, Completion Rule)
    # Note: 'None' for London/Florida if they only require Night
    liveside_configs = {
        LIVESIDE_LONDON: (ASYLUM_ENGINE_BLOCK_LONDON, lambda s: R.night(s, player)),
        LIVESIDE_FLORIDA: (ASYLUM_ENGINE_BLOCK_FLORIDA, lambda s: R.night(s, player)),
        LIVESIDE_PRISON: (ASYLUM_ENGINE_BLOCK_PRISON,
                          lambda s: R.night(s, player) and s.has("RSC_X_PRISON_KEY_CARD", player)),
        LIVESIDE_QUEENS: (ASYLUM_ENGINE_BLOCK_QUEENS, lambda s: R.night(s, player) and s.has("RSC_X_POIGNE", player)),
        LIVESIDE_SALVAGE: (ASYLUM_ENGINE_BLOCK_SALVAGE, lambda s: R.night(s, player) and R.gad3_swim(s, player)),
    }

    # 2. Loop through to create the gated connections
    for level, (section, completion_rule) in liveside_configs.items():
        # Cathedral -> Level (Entry remains the same: Just Retractors)
        _connect(
            regions[ASYLUM_CATHEDRAL],
            regions[level],
            rule=lambda state, l=level: R.can_reach_liveside(state, player, l)
        )

        # Level -> Engine Section (Exit: Requires Night + Special Item)
        _connect(
            regions[level],
            regions[section],
            rule=completion_rule
        )

    # ── 4. Populate locations ────────────────────────────────────────────────
    level_names = [n for n in ALL_REGIONS if n != MENU]
    for name in level_names:
        _build_sub_regions(regions[name], name, multiworld, player)


# ── Standalone level rules ────────────────────────────────────────────────────

def build_level_rules(
    gate_remap: dict[str, int] | None = None,
    entrance_shuffle=None,  # UnifiedShuffle | None
) -> dict[str, callable]:
    """
    Build a {region_name: callable(state) -> bool} dict for the standalone
    simulator in fill.py.

    Vanilla (entrance_shuffle=None):
        Mirrors the connections in create_regions exactly — same gates, same
        parent-AND-entrance composition.

    Entrance shuffle:
        Spoke-level connections are replaced by the shuffle mapping.
        All internal sub-region logic (Cathedral, liveside, engine block)
        is identical to vanilla and composed the same way.
    """
    set_gate_remap(gate_remap or {})

    rules: dict[str, callable] = {}

    # Always reachable
    rules[MENU]                  = lambda state: True
    rules[LOUISIANA_SWAMPLAND]   = lambda state: True
    rules[DEADSIDE_MARROW_GATES] = lambda state: True

    if entrance_shuffle is None:
        # ── Vanilla spoke connections — mirrors create_regions connections list
        _spoke_connections: list[tuple[str, callable]] = [
            (DEADSIDE_WASTELAND, lambda state: _R.gate("GATE_DEADSIDE_WASTELAND", state, 1)),
            (ASYLUM_GATEWAYS,    lambda state: _R.gate("GATE_DEADSIDE_ASYLUM",    state, 1)),
            (TEMPLE_FIRE,        lambda state: _R.gate("GATE_DEADSIDE_PATH_3",    state, 1)),
            # Cageways/Playrooms (2026-08-03): front door OR the same lower-
            # Deadside backtrack routes as Temple of Prophecy — see
            # CAGEWAYS_ROUTES/PLAYROOMS_ROUTES comment in access_rules.py.
            (ASYLUM_CAGEWAYS,    lambda state: _eval_routes(CAGEWAYS_ROUTES, state, 1)),
            (ASYLUM_PLAYROOMS,   lambda state: _eval_routes(PLAYROOMS_ROUTES, state, 1)),
            (ASYLUM_LAVADUCTS,   lambda state: _R.gate("GATE_DEADSIDE_LAVADUCTS", state, 1)),
            (TEMPLE_BLOOD,       lambda state: _R.gate("GATE_DEADSIDE_BLOOD",     state, 1)),
            (ASYLUM_FOGOMETERS,  lambda state: _R.gate("GATE_DEADSIDE_FOGOMETERS",state, 1)),
            (TEMPLE_PROPHECY,    lambda state: (
                _R.gate("GATE_DEADSIDE_PATH_7", state, 1) or (
                    _R.gate("GATE_DEADSIDE_CAGEWAYS",  state, 1) and
                    _R.gate("GATE_DEADSIDE_PLAYROOMS", state, 1) and
                    _R.gate("GATE_DEADSIDE_PATH_6",    state, 1)
                ) or (
                    _R.gate("GATE_DEADSIDE_ASYLUM", state, 1) and
                    _R.baton(state, 1) and
                    _R.gad2_walk(state, 1)
                )
            )),
        ]
        for region, rule in _spoke_connections:
            rules[region] = rule

    else:
        # ── Entrance-shuffled spoke connections
        from access_rules import (
            DEADSIDE_PORTAL_GATE, SPOKE_FOLDER_TO_PRIMARY_REGION,
            DKE_ARRIVAL_TO_REGION, LIVESIDE_COMPLETION_RULES,
            _LOWER_DEADSIDE_ROUTES,
        )
        from randomizers.entrance_randomizer import _TRANSITION_BY_PORTAL_ID

        # Fixed 2026-07-21 (see DEADSIDE_PORTAL_GATE's comment block in
        # access_rules.py): uses R.gate() now, not _gate_sl_only — the
        # ancestor-gate chain (GATE_DEPENDENCIES) models the physical walk
        # to a portal's own location in Marrow Gates and must be walked
        # regardless of where the portal's cutscene has been shuffled to.
        def _make_routes_rule(routes: list) -> callable:
            def rule(state) -> bool:
                def _token(t: str) -> bool:
                    if t.startswith("GATE_"): return _R.gate(t, state, 1)
                    if t == "BATON":          return _R.baton(state, 1)
                    if t == "GAD2_WALK":      return _R.gad2_walk(state, 1)
                    return False
                return any(all(_token(t) for t in route) for route in routes)
            return rule

        for portal_id, dest_id in entrance_shuffle.outbound.items():
            portal_folder, portal_file = portal_id
            dest = _TRANSITION_BY_PORTAL_ID[dest_id]

            # Portal-side rule
            if portal_folder == "deadside":
                gate = DEADSIDE_PORTAL_GATE[portal_file]
                if isinstance(gate, str):
                    gid = gate
                    portal_rule = lambda state, g=gid: _R.gate(g, state, 1)
                else:
                    portal_rule = _make_routes_rule(gate)
            else:
                completion = LIVESIDE_COMPLETION_RULES[portal_folder]
                portal_rule = lambda state, c=completion: c(state, 1)

            # Destination region
            if dest.spoke_folder == "as4dkeng":
                region = DKE_ARRIVAL_TO_REGION.get(dest.spoke_arrival)
                if region is None:
                    raise ValueError(
                        f"build_level_rules: no region for DKE arrival "
                        f"{dest.spoke_arrival!r} — add to DKE_ARRIVAL_TO_REGION"
                    )
            else:
                region = SPOKE_FOLDER_TO_PRIMARY_REGION.get(dest.spoke_folder)
                if region is None:
                    raise ValueError(
                        f"build_level_rules: no region for spoke folder "
                        f"{dest.spoke_folder!r} — add to SPOKE_FOLDER_TO_PRIMARY_REGION"
                    )

            rules[region] = portal_rule

        # Assertion: every expected spoke region got a rule
        expected = (
            set(SPOKE_FOLDER_TO_PRIMARY_REGION.values()) |
            set(DKE_ARRIVAL_TO_REGION.values())
        )
        missing = expected - set(rules.keys())
        assert not missing, f"build_level_rules: regions with no rule assigned: {missing}"

    # ── Internal sub-regions — identical for both paths ───────────────────────

    # Asylum: Cathedral + Experimentation require Gateways + (Eng Key OR
    # Gad 2 walk-on-lava bypass — confirmed live by Jon 2026-08-03, mirrors
    # create_regions()'s matching connection comment above).
    if ASYLUM_GATEWAYS in rules:
        gw = rules[ASYLUM_GATEWAYS]
        rules[ASYLUM_CATHEDRAL]     = lambda state, p=gw: p(state) and (_R.eng_key(state, 1) or _R.gad2_walk(state, 1))
        rules[ASYLUM_EXPERIMENTATION] = lambda state, p=gw: p(state) and (_R.eng_key(state, 1) or _R.gad2_walk(state, 1))

    # Asylum: Engine Block — requires Cageways AND the Engineers Key
    # (2026-08-06 fix, Jon confirmed live: this used to just alias
    # Cageways' own rule directly, treating Engine Block as free once
    # Cageways is reached -- wrong, see create_regions()'s matching
    # connection comment above for the full writeup).
    if ASYLUM_CAGEWAYS in rules:
        cw = rules[ASYLUM_CAGEWAYS]
        rules[ASYLUM_ENGINE_BLOCK] = lambda state, p=cw: p(state) and _R.eng_key(state, 1)

    # Liveside levels — Cathedral + retractors
    # If Cathedral is unreachable (e.g. Asylum assigned to a soul gate and
    # that soul gate is itself unreachable), liveside stays False.
    _liveside_list = [
        LIVESIDE_LONDON, LIVESIDE_PRISON, LIVESIDE_SALVAGE,
        LIVESIDE_QUEENS, LIVESIDE_FLORIDA,
    ]
    if ASYLUM_CATHEDRAL in rules:
        cat = rules[ASYLUM_CATHEDRAL]
        for lr in _liveside_list:
            rules[lr] = lambda state, p=cat, r=lr: p(state) and _R.can_reach_liveside(state, 1, r)
    else:
        for lr in _liveside_list:
            rules[lr] = lambda state: False

    # Engine Block sections — only set if entrance shuffle didn't claim them
    _engine_configs: list[tuple[str, str, callable]] = [
        (LIVESIDE_LONDON,  ASYLUM_ENGINE_BLOCK_LONDON,
         lambda state: _R.night(state, 1)),
        (LIVESIDE_PRISON,  ASYLUM_ENGINE_BLOCK_PRISON,
         lambda state: _R.night(state, 1) and _R.prison_key_card(state, 1)),
        (LIVESIDE_SALVAGE, ASYLUM_ENGINE_BLOCK_SALVAGE,
         lambda state: _R.night(state, 1) and _R.gad3_swim(state, 1)),
        (LIVESIDE_QUEENS,  ASYLUM_ENGINE_BLOCK_QUEENS,
         lambda state: _R.night(state, 1) and _R.poigne(state, 1)),
        (LIVESIDE_FLORIDA, ASYLUM_ENGINE_BLOCK_FLORIDA,
         lambda state: _R.night(state, 1)),
    ]
    for liveside, engine, completion in _engine_configs:
        if engine not in rules:
            lv = rules.get(liveside)
            if lv is not None:
                rules[engine] = lambda state, p=lv, cr=completion: p(state) and cr(state)

    return rules