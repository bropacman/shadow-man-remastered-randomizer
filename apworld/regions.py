"""
regions.py
──────────
Builds the Archipelago region graph for Shadow Man Remastered.
"""

from __future__ import annotations

from BaseClasses import Region, MultiWorld, LocationProgressType
from .access_rules import (
    R, BoundR, make_entrance_rule, make_location_rule, make_portal_rule,
    DEADSIDE_PORTAL_GATE, CAGEWAYS_ROUTES, PLAYROOMS_ROUTES,
)
from .extracted_locations import FREE_LOCATIONS, GATED_LOCATIONS
from .locations import location_table, FRIENDLY_NAMES
from .fill import CADEAUX_666_LOCS, DEPTH_BUCKETS
from .items import RETRACTOR_KEY_ITEM_NAMES

# ── Region name constants ─────────────────────────────────────────────────────

MENU                         = "Menu"
LOUISIANA_SWAMPLAND          = "Louisiana Swampland"
DEADSIDE_MARROW_GATES        = "Deadside Marrow Gates"
DEADSIDE_WASTELAND           = "Deadside - Wasteland"
ASYLUM_GATEWAYS              = "Asylum: Gateways"
ASYLUM_CATHEDRAL             = "Asylum: Cathedral of Pain"
ASYLUM_EXPERIMENTATION       = "Asylum: Experimentation Rooms"
LIVESIDE_LONDON              = "Down Street Station, London"
LIVESIDE_PRISON              = "Gardelle County Jail, Texas"
LIVESIDE_FLORIDA             = "Summer Camp, Florida"
LIVESIDE_SALVAGE             = "Salvage Yard, Mojave Desert"
LIVESIDE_QUEENS              = "Mordant Street, Queens, NY"
TEMPLE_FIRE                  = "Temple of Fire (Toucher)"
ASYLUM_CAGEWAYS              = "Asylum: Cageways"
ASYLUM_ENGINE_BLOCK          = "Asylum: Engine Block"
ASYLUM_ENGINE_BLOCK_LONDON   = "Asylum: Engine Block - London"
ASYLUM_ENGINE_BLOCK_PRISON   = "Asylum: Engine Block - Prison"
ASYLUM_ENGINE_BLOCK_FLORIDA  = "Asylum: Engine Block - Florida"
ASYLUM_ENGINE_BLOCK_SALVAGE  = "Asylum: Engine Block - Salvage"
ASYLUM_ENGINE_BLOCK_QUEENS   = "Asylum: Engine Block - Queens"
ASYLUM_PLAYROOMS             = "Asylum: Playrooms"
TEMPLE_PROPHECY              = "Temple of Prophecy (Marcher)"
ASYLUM_LAVADUCTS             = "Asylum: Lavaducts"
TEMPLE_BLOOD                 = "Temple of Blood (Nager)"
ASYLUM_FOGOMETERS            = "Asylum: The Fogometers"

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
    LIVESIDE_FLORIDA,
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
]

LIVESIDE_REGIONS = {
    LIVESIDE_LONDON,
    LIVESIDE_PRISON,
    LIVESIDE_FLORIDA,
    LIVESIDE_SALVAGE,
    LIVESIDE_QUEENS,
}

# ── Entrance randomizer — portal/region join table ────────────────────────────
# Maps each of the 9 Deadside hub-portal cutscene files (DEADSIDE_PORTAL_GATE
# keys, access_rules.py) to the AP region it leads to in VANILLA/unshuffled
# form. Ported from the standalone's SPOKE_FOLDER_TO_PRIMARY_REGION
# (access_rules.py there), collapsed directly to portal_file since AP has no
# need for the intermediate spoke_folder concept those keys represent in the
# standalone (2026-07-21, entrance randomizer — deadside_only mode).
DEADSIDE_PORTAL_REGION: dict[str, str] = {
    "LE_Wast.cut": DEADSIDE_WASTELAND,
    "LE_Asy1.cut": ASYLUM_GATEWAYS,
    "LE_Cage.cut": ASYLUM_CAGEWAYS,
    "LE_Play.cut": ASYLUM_PLAYROOMS,
    "LE_Lava.cut": ASYLUM_LAVADUCTS,
    "LE_Fog.cut":  ASYLUM_FOGOMETERS,
    "LE_Gad1.cut": TEMPLE_FIRE,
    "LE_Gad2.cut": TEMPLE_PROPHECY,
    "LE_Gad3.cut": TEMPLE_BLOOD,
}
DEADSIDE_PORTAL_FILES: tuple[str, ...] = tuple(DEADSIDE_PORTAL_REGION.keys())


# ── Helpers ───────────────────────────────────────────────────────────────────

def _connect(source: Region, target: Region, rule=None, name: str | None = None) -> None:
    """
    Wraps Region.connect() with an explicit entrance name.

    AP defaults unnamed entrances to f"{source.name} -> {target.name}", which
    makes path/spoiler dumps show the target region twice in a row (e.g.
    "Cageways -> Cageways -> Cageways -> [GATE...]"). Naming convention
    (2026-07-20): "{ShortDestination} [{requirement}]" — the bracket tag
    mirrors the same convention already used for gated sub-region names in
    _build_sub_regions() below, so gate-locked entrances and gate-locked
    location sub-regions read consistently in path output. Free connections
    (no gate) omit the bracket tag entirely.
    """
    source.connect(target, name=name, rule=rule)


# ── Sub-region builder ────────────────────────────────────────────────────────

# "barrel" is permanently excluded — never becomes an AP location regardless
# of any option.
#
# "cadeaux" is conditionally excluded here (2026-07-21): whether cadeaux
# pickups become real AP locations at all is now gated on the Cadeauxsanity
# option (renamed from "Insanity" 2026-08-23 to avoid colliding with the
# standalone randomizer's differently-scoped graded "insanity" tiers), not
# unconditional. Previously (2026-07-20 —
# 2026-07-21 morning) cadeaux locations always existed as AP checks, with
# cadeauxsanity only toggling an item_rule restriction in __init__.py's
# set_rules() (Cadeaux-only vs any item). That meant turning cadeauxsanity off
# still surfaced ~657 cadeaux checks/hints in the tracker — not what an
# "optional as a location" toggle should do. Off now means excluded
# entirely, same as barrel; on means real AP locations with no restriction
# (set_rules()'s item_rule block was removed as part of this change — no
# longer needed either way). See __init__.py's create_items() for the
# matching item-pool-size adjustment this requires.
_SKIP_CATS = frozenset({"barrel"})


def round_robin_by_group(rng, items: list, key_fn, n: int | None = None) -> list:
    """
    Diversity helper (2026-08-02, Jon's request: "picking slots with a
    variety of subregions for more interesting logic placement... too
    many slots may just be free or just eng_key"). Interleaves `items` in
    round-robin order across groups defined by key_fn(item), so
    consecutive positions in the returned list are drawn from as many
    DIFFERENT groups as possible before repeating any one group.

    Why this matters over a plain rng.shuffle(): a uniform shuffle has no
    notion of "group" at all, so any downstream logic that picks from a
    shuffled list in order (or chunks it) still ends up representing each
    group roughly proportional to how many raw items it contributed —
    which means a big, densely-populated group (e.g. a "free"/no-gate
    bucket, or a heavily-farmed gate like ENG_KEY) dominates purely
    because it has more candidates, not because that's actually desired.
    Round-robin interleaving instead guarantees near-even group
    representation regardless of each group's raw size: with G groups,
    the first G items returned are guaranteed one from each group, the
    next G are a second item from each still-non-exhausted group, etc.

    Each group's own items are shuffled with `rng` first (so WHICH
    specific item comes out of a group is still random), and the ORDER
    groups are visited in is also shuffled with `rng` (so no group
    structurally "goes first" every time) — only the INTERLEAVING itself
    is structured, everything else stays exactly as randomized as before.

    n=None (default) returns ALL items reordered — a full round-robin
    permutation where nothing is dropped, just diversified in position.
    Used where every item still needs to end up somewhere, just spread
    out (e.g. cadeaux bundle chunking, where every eligible cadeaux still
    needs to land in some chunk). A concrete n returns only the first n
    items of that same round-robin order — used for picking a genuinely
    SMALLER, diverse SUBSET out of a larger pool (e.g. barrel promotion),
    guaranteeing no single group can dominate the result just because it
    happened to have more raw candidates than everyone else.
    """
    groups: dict = {}
    for it in items:
        groups.setdefault(key_fn(it), []).append(it)
    group_keys = list(groups.keys())
    rng.shuffle(group_keys)
    for k in group_keys:
        rng.shuffle(groups[k])

    target = len(items) if n is None else min(n, len(items))
    result: list = []
    round_idx = 0
    while len(result) < target:
        progressed = False
        for k in group_keys:
            if round_idx < len(groups[k]):
                result.append(groups[k][round_idx])
                progressed = True
                if len(result) >= target:
                    break
        if not progressed:
            break
        round_idx += 1
    return result


def nested_round_robin_by_group(rng, items: list, key_fns: list, n: int | None = None) -> list:
    """
    Level-spread fix (2026-08-18, Jon's follow-up to the depth-bucket pass
    above: "otherwise the levels with the most checks are gonna have all
    the location checks in them most likely, like the temple levels and
    certain asylum levels"). round_robin_by_group() above is a single flat
    grouping pass — every distinct key_fn(item) value is a sibling group
    competing equally for round-robin turns, REGARDLESS of what tier that
    group belongs to. Once barrel promotion's key became (level_region,
    gate_raw, depth_bucket), that stopped being harmless: a level that
    happens to fan out into more (gate, depth) sub-groups gets that many
    MORE total picks than a level that only fans out into one or two, purely
    as a side effect of AP logic granularity or raw candidate volume — not
    because more spread was actually wanted. Confirmed empirically before
    writing this fix (n=200 barrel promotion, same seed): the flat 3-tuple
    key gave Asylum: Engine Block (as4dkeng, 6 AP regions x several gates)
    23 picks while Underground/Cageways/Cathedral of Pain (1-2 sub-groups
    each) got as few as 6 — a ~4x disparity, exactly Jon's prediction.

    This function makes grouping HIERARCHICAL instead of flat: key_fns is
    an ORDERED list, outermost tier first (e.g. [level_id, level_region,
    gate_raw, depth_bucket]). At every tier, items are grouped by that
    tier's key_fn and round-robin-interleaved — but each group's internal
    order is itself already a full round-robin ordering from the tier
    below, computed recursively. The result: a group at the OUTER tier
    (e.g. one physical level) gets exactly one turn per round no matter how
    many INNER-tier sub-groups it happens to contain — a level with 8
    (gate, depth) combinations and a level with 1 still each get 1 pick per
    round at the level tier, then whichever level's turn it is draws from
    its own already-diversified inner ordering. Re-running the same n=200
    barrel-promotion scenario with key_fns=[level_id, level_region,
    gate_raw, depth_bucket] (same seed) flattened the 6-23 range above down
    to 12-13 across all 16 levels — every level within one pick of a
    perfectly even 200/16 = 12.5 share.

    Base case (deepest tier, or a group of size <=1): just an rng.shuffle()
    — same as round_robin_by_group()'s own per-group shuffle, so which
    SPECIFIC item comes out of a leaf group is still fully random, only the
    INTERLEAVING at every tier above it is structured.

    n=None returns every item reordered (nothing dropped). A concrete n
    truncates the fully-ordered result to the first n items — same
    semantics as round_robin_by_group()'s own n parameter, and for the same
    reason: the top of a hierarchical round-robin order is exactly the set
    you'd want for a smaller, still-fairly-spread subset.
    """
    def _recurse(subitems: list, depth: int) -> list:
        if depth >= len(key_fns) or len(subitems) <= 1:
            shuffled = list(subitems)
            rng.shuffle(shuffled)
            return shuffled

        key_fn = key_fns[depth]
        groups: dict = {}
        for it in subitems:
            groups.setdefault(key_fn(it), []).append(it)
        keys = list(groups.keys())
        rng.shuffle(keys)

        # Each sub-group is already a complete round-robin ordering from
        # every tier below this one — recursing BEFORE interleaving is what
        # makes this hierarchical rather than just a deeper flat grouping.
        ordered_subgroups = [_recurse(groups[k], depth + 1) for k in keys]

        result: list = []
        round_idx = 0
        progressed = True
        while progressed:
            progressed = False
            for sub in ordered_subgroups:
                if round_idx < len(sub):
                    result.append(sub[round_idx])
                    progressed = True
            round_idx += 1
        return result

    ordered = _recurse(items, 0)
    return ordered if n is None else ordered[:min(n, len(ordered))]


def compute_cadeaux_bundle_representatives(bundle_size: int, rng) -> dict[str, int]:
    """
    Cadeaux Bundle Size (2026-07-27, Jon's request): reduce ~657 individual
    cadeaux AP checks down to a smaller, configurable number by grouping
    them into bundles and only surfacing one representative per bundle as
    a real AP location.

    GLOBAL chunking (redesigned 2026-07-28, Jon's explicit call): every
    eligible cadeaux loc_key across the WHOLE game is gathered into one
    flat list, shuffled with the per-seed rng, and chunked into groups of
    `bundle_size`. This deliberately does NOT scope chunks to a single
    region/gate bucket anymore -- the original design (see git history /
    this function's pre-2026-07-28 docstring) kept every bundle inside one
    reachability bucket so a bundle's "worth" always matched the physical
    pacing of wherever it sat (early-game cadeaux couldn't get hollowed
    out to fund a late-game payout or vice versa). Jon explicitly opted
    out of that guarantee in exchange for predictable bundle sizes: the
    old per-bucket approach produced up to ~87 separate remainder bundles
    (one per bucket whose count wasn't a clean multiple of bundle_size),
    which looked confusing in the spoiler log even though it was working
    exactly as designed. Global chunking instead produces AT MOST ONE
    remainder bundle for the entire game (e.g. 653 total cadeaux at
    bundle_size 5 -> 130 bundles of x5 plus one final x3), at the cost of
    "scrambling" the reward economy -- a bundle's representative can now
    sit behind a totally different gate than some of the cadeaux whose
    value it absorbed.

    DIVERSITY PASS before chunking (2026-08-02, Jon's request): although
    chunk membership is still effectively random, the candidate list is
    interleaved via round_robin_by_group() (grouped by (level_region,
    gate_raw)) before being cut into bundle_size chunks, so each chunk --
    and therefore the one representative picked from it -- draws from a
    variety of subregions/gates rather than clustering wherever raw
    cadeaux density happens to be highest. See round_robin_by_group()'s
    own docstring for why a plain shuffle doesn't achieve this on its own.

    Still 100% safe for AP's own fill/logic correctness despite dropping
    the bucket-safety guarantee: _build_sub_regions() places each
    representative into ITS OWN, individually-accurate region/gate
    sub-region via a simple loc_key membership check against this
    function's return value (see _cadeaux_ok() below) -- completely
    independent of which OTHER loc_keys happened to land in the same
    chunk. A representative's AP-logic reachability rule is always
    exactly correct for wherever it physically sits; only its ITEM's
    value (the "weight") now potentially reflects cadeaux collected from
    unrelated parts of the game. This can never produce an unreachable
    item or an over-promised location -- it can only make the cadeaux
    economy's pacing feel disconnected from region/gate progression,
    which Jon has explicitly said is an acceptable tradeoff.

    One loc_key per chunk is chosen at random via the caller-supplied
    per-seed rng (pass self.random from generate_early(), matching every
    other per-seed randomization in this world) to remain a real AP
    location. Every other loc_key in that chunk is simply omitted from
    the returned mapping -- see _build_sub_regions()'s use of this
    function for what that means downstream (same treatment as any
    category excluded via _SKIP_CATS: never becomes an AP location, stays
    physically untouched/vanilla).

    Returns {representative_loc_key: weight}, where weight is the TRUE
    size of that specific chunk -- not a flat bundle_size -- since the
    total cadeaux count doesn't always divide evenly by bundle_size (e.g.
    653 cadeaux at bundle_size 5 -> 130 chunks of 5, one chunk of 3).
    Callers use this weight both to size the "Cadeaux Bundle x{weight}"
    item created for that representative (see items.py's
    cadeaux_bundle_item_name()) and, implicitly, to keep the total
    achievable cadeaux value conserved (sum of all weights == total
    checkable cadeaux count, same as before bundling existed).

    bundle_size <= 1 returns every cadeaux loc_key mapped to weight 1 (no
    bundling), so this is a no-op at the option's default -- every
    representative's item is plain "Cadeaux", same as before this option
    existed.
    """
    # BUG CAUGHT BEFORE SHIPPING (2026-07-27): FREE_LOCATIONS/GATED_LOCATIONS
    # are built from RAW_LOCATIONS -- ALL rows, including is_verified=False
    # phantom/unverified ones that CHECKABLE_LOCS (and therefore
    # locations.py's location_table) deliberately excludes. _build_sub_regions()
    # already guards each individual location with `if raw.loc_key not in
    # location_table: continue`, so if an excluded row got chosen as a
    # bundle's representative, that ENTIRE bundle would silently produce
    # zero real Locations -- while this function still counted it as one,
    # overestimating len(...) relative to what create_regions() actually
    # instantiates and creating a "Cadeaux" item with no location to hold
    # it (breaks AP's items-must-match-open-locations invariant). Filtering
    # to only location_table-eligible rows here keeps this function's count
    # exactly matching what _build_sub_regions() will really create.
    # can_softlock (2026-08-15, Jon's request): never let a ledge/one-way-
    # drop cadeaux row become a bundle representative (or, at bundle_size
    # <= 1, a standalone real location) -- excluding it here at the source
    # means it can never be picked as a chunk's representative in the first
    # place, so no chunk ever silently loses its only real location. Same
    # reasoning as the barrel candidate filter in __init__.py's
    # generate_early() (search can_softlock there for the full writeup).
    eligible = lambda l: l.category == "cadeaux" and l.loc_key in location_table and not l.can_softlock

    reps: dict[str, int] = {}

    if bundle_size <= 1:
        for locs in FREE_LOCATIONS.values():
            for l in locs:
                if eligible(l):
                    reps[l.loc_key] = 1
        for by_gate in GATED_LOCATIONS.values():
            for locs in by_gate.values():
                for l in locs:
                    if eligible(l):
                        reps[l.loc_key] = 1
        return reps

    all_cadeaux_raw: list = []
    for locs in FREE_LOCATIONS.values():
        all_cadeaux_raw.extend(l for l in locs if eligible(l))
    for by_gate in GATED_LOCATIONS.values():
        for locs in by_gate.values():
            all_cadeaux_raw.extend(l for l in locs if eligible(l))

    # Stable base order before interleaving, so the RNG draws inside
    # round_robin_by_group are the only source of seed-to-seed variation
    # (matches this codebase's other per-seed randomization patterns --
    # deterministic input, rng-driven shuffle/choice).
    all_cadeaux_raw.sort(key=lambda l: l.loc_key)

    # Diversity pass (2026-08-02, Jon's request: "picking slots with a
    # variety of subregions for more interesting logic placement... too
    # many slots may just be free or just eng_key"). Grouped by
    # (level_region, gate_raw) -- the SAME granularity FREE_LOCATIONS/
    # GATED_LOCATIONS themselves use (level_region alone would still let
    # every representative cluster behind whichever single gate happens
    # to gate the most cadeaux within a region, e.g. ENG_KEY specifically,
    # which was Jon's literal example) -- so "free" (gate_raw=None)
    # cadeaux in a region form their own group, and each distinct gate
    # requirement within that region forms its own separate group. Round-
    # robin interleaving this BEFORE chunking means each bundle_size-sized
    # chunk draws from as many different subregion/gate groups as
    # possible, so the one representative picked from each chunk is
    # naturally spread across the game's variety of gates/subregions
    # rather than clustering wherever raw cadeaux density happens to be
    # highest (a plain global shuffle has no notion of "group" at all, so
    # it still ends up representing each group roughly proportional to
    # its raw candidate count -- exactly the "too many slots are just
    # free/eng_key" symptom Jon described). This only changes WHICH items
    # end up adjacent to each other before chunking -- chunk composition,
    # weight-per-chunk, and the "one random representative per chunk" pick
    # below are otherwise unchanged, so the total achievable cadeaux value
    # is still fully conserved (sum of weights == total checkable cadeaux
    # count) regardless of this reordering.
    #
    # Depth-bucket dimension (2026-08-18, Jon's idea): (level_region,
    # gate_raw) alone still leaves liveside interiors nearly undiversified,
    # since gate_raw is None for almost every liveside cadeaux row (the
    # region's own entrance rule lives on the region CONNECTION, not on
    # individual locations) -- most liveside cadeaux collapsed into one
    # (region, None) group regardless of how far into the level they
    # physically sit. fill.py's DEPTH_BUCKETS adds a third grouping
    # dimension -- early/mid/late thirds of that region's own zone range,
    # via the RSC record's own sector tag -- so that flat group now splits
    # by physical progression too, same reasoning as barrel promotion's
    # identical addition in __init__.py's generate_early(). See
    # DEPTH_BUCKETS's own docstring in fill.py for the full derivation and
    # real-data verification.
    #
    # Level-spread fix (2026-08-18, Jon's follow-up: "otherwise the levels
    # with the most checks are gonna have all the location checks in them
    # most likely, like the temple levels and certain asylum levels"). A
    # flat (level_region, gate_raw, depth_bucket) key still let a level that
    # happens to fan out into more sub-groups (more gates, or a region split
    # like Engine Block's 6-way schism division) dominate purely from having
    # more competing groups, not because more of its cadeaux were actually
    # wanted. nested_round_robin_by_group() makes the grouping hierarchical
    # instead -- level_id gets the outermost round-robin tier, so every
    # PHYSICAL level gets one turn per round regardless of how many
    # sub-groups it fans out into beneath that -- see that function's own
    # docstring for the full rationale and the real-data verification (a
    # ~4x per-level disparity flattened to within one pick of dead-even).
    all_cadeaux = nested_round_robin_by_group(
        rng, all_cadeaux_raw,
        key_fns=[
            lambda l: l.level_id,
            lambda l: l.level_region,
            lambda l: l.gate_raw,
            lambda l: DEPTH_BUCKETS.get(l.loc_key, 1),
        ])

    for i in range(0, len(all_cadeaux), bundle_size):
        chunk = all_cadeaux[i:i + bundle_size]
        reps[rng.choice(chunk).loc_key] = len(chunk)

    return reps

def _build_sub_regions(
    level_region: Region,
    level_name: str,
    multiworld: MultiWorld,
    player: int,
    gate_values: dict[str, int],
    location_factory,
    sl_thresholds: dict[int, int] | None = None,
    piston_combos: bool = False,
    cadeauxsanity: bool = False,
    cadeaux_required: int | None = None,
    cadeaux_gated_content: bool = False,
    cadeaux_bundle_representatives: dict[str, int] | None = None,
    barrel_promoted_locs: frozenset[str] | None = None,
) -> None:
    skip_cats = _SKIP_CATS if cadeauxsanity else (_SKIP_CATS | {"cadeaux"})

    # Secret Trap barrel promotion (2026-08-01): "barrel" is already in
    # skip_cats unconditionally (via module-level _SKIP_CATS above) — this
    # is a per-loc_key EXCEPTION to that, not a category-wide toggle like
    # cadeaux/cadeauxsanity. Mirrors _cadeaux_ok() below in shape, just inverted
    # (an inclusion list becoming a further restriction, vs. an exclusion
    # list becoming a specific exception).
    def _category_ok(raw) -> bool:
        if raw.category not in skip_cats:
            return True
        if raw.category == "barrel" and barrel_promoted_locs is not None:
            return raw.loc_key in barrel_promoted_locs
        return False
    # CadeauxGatedContent option (2026-07-24): off (default) excludes the
    # Fogometers Light Soul location (fill.py's CADEAUX_666_LOCS) the same
    # way skip_cats excludes whole categories — see options.py's
    # CadeauxGatedContent docstring and create_regions()'s param doc above.
    skip_loc_keys = frozenset() if cadeaux_gated_content else CADEAUX_666_LOCS

    # Cadeaux Bundle Size (2026-07-27): cadeaux_bundle_representatives is
    # the set built by compute_cadeaux_bundle_representatives() -- when set,
    # a cadeaux-category row only becomes a real AP location if it's one of
    # the chosen bundle representatives; every other cadeaux row is treated
    # as if it were in skip_cats (excluded, stays vanilla). None (or bundle
    # size <= 1) means "no bundling" -- every cadeaux row passes, matching
    # pre-bundling behavior exactly. This check is a no-op whenever cadeauxsanity
    # is off, since "cadeaux" is already fully excluded via skip_cats by
    # then regardless of bundle membership.
    def _cadeaux_ok(raw) -> bool:
        if raw.category != "cadeaux" or cadeaux_bundle_representatives is None:
            return True
        return raw.loc_key in cadeaux_bundle_representatives

    for raw in FREE_LOCATIONS.get(level_name, []):
        if not _category_ok(raw):
            continue
        if raw.loc_key in skip_loc_keys:
            continue
        if raw.loc_key not in location_table:
            continue
        if not _cadeaux_ok(raw):
            continue
        loc = location_factory(player, FRIENDLY_NAMES.get(raw.loc_key, raw.loc_key), raw, level_region)
        level_region.locations.append(loc)

    for gate_raw, locs in GATED_LOCATIONS.get(level_name, {}).items():
        filtered = [
            l for l in locs
            if _category_ok(l)
            and l.loc_key not in skip_loc_keys
            and l.loc_key in location_table
            and _cadeaux_ok(l)
        ]
        if not filtered:
            continue
        gate_expr = filtered[0].gate_expr
        sub_name  = f"{level_name} [{gate_raw}]"
        sub       = Region(sub_name, player, multiworld)

        for raw in filtered:
            loc = location_factory(player, FRIENDLY_NAMES.get(raw.loc_key, raw.loc_key), raw, sub)
            if raw.loc_key in CADEAUX_666_LOCS:
                # BUG FIX (2026-07-24): balance_multiworld_progression() (and
                # the forward-fill pass itself) only ever collects
                # advancement/progression items into its own internal
                # CollectionState -- Cadeaux is filler and never gets
                # collected there regardless of collect_item()'s fix above,
                # since those algorithms guard with `if location.advancement`
                # before ever calling state.collect() at all. That means
                # R.cadeaux_666() can never evaluate True inside THOSE
                # specific simulations, no matter what -- so if this location
                # were ever handed a progression/useful item, whichever
                # algorithm needs to prove that item reachable would fail
                # exactly like the original "Not all required items
                # reachable" crash this whole investigation started from.
                # Marking it EXCLUDED (Location.can_fill() then refuses any
                # item where item.advancement or item.useful) guarantees it
                # only ever holds ordinary filler, so nothing structural can
                # ever depend on its unreachable-in-simulation rule.
                loc.progress_type = LocationProgressType.EXCLUDED
            sub.locations.append(loc)

        multiworld.regions.append(sub)
        # cadeaux_trackable=cadeauxsanity: Cadeaux locations (and thus the
        # "Cadeaux" AP item) only exist in the pool when Cadeauxsanity is on
        # (see __init__.py's _cadeaux_identity_map docstring) — R.cadeaux_666()
        # falls back to always-True when it's off, matching the pre-2026-07-24
        # behavior for that mode (see cadeaux_666()'s docstring in access_rules.py).
        rule_fn = make_location_rule(gate_expr, gate_values, player, sl_thresholds,
                                      piston_combos, cadeaux_required, cadeauxsanity)
        # sub_name already carries the "[gate_raw]" bracket tag and a unique
        # level prefix, so it doubles as a clean, collision-free entrance name.
        _connect(level_region, sub, rule=rule_fn, name=sub_name)

# ── Main builder ──────────────────────────────────────────────────────────────

def create_regions(
    multiworld: MultiWorld,
    player: int,
    gate_values: dict[str, int],
    location_factory,
    sl_thresholds: dict[int, int] | None = None,
    entrance_shuffle: dict[str, str] | None = None,
    piston_combos: bool = False,
    cadeauxsanity: bool = False,
    cadeaux_required: int | None = None,
    cadeaux_gated_content: bool = False,
    cadeaux_bundle_representatives: dict[str, int] | None = None,
    barrel_promoted_locs: frozenset[str] | None = None,
    unique_retractor_keys: bool = False,
) -> None:
    """
    Called from ShadowManWorld.create_regions().
    gate_values: the shuffled gate→SL mapping for this world instance.
    sl_thresholds: this world's resolved SL→soul-count mapping (see
                   __init__.py's generate_early(), self.sl_thresholds) —
                   None means every rule falls back to vanilla thresholds.
                   Threaded through so soul_threshold_mode is respected by
                   the logic graph, not just the EXE patch/client slot_data.
    entrance_shuffle: this world's resolved Deadside portal→portal mapping
                   (see __init__.py's generate_early(), self.entrance_shuffle)
                   — None (entrance_mode "off") builds the fixed vanilla
                   marrow→region connections; otherwise the 9 marrow
                   connections are built from this mapping instead (see the
                   "Marrow → deadside/temple regions" block below).
    piston_combos: this world's PistonCombos option (see options.py) — when
                   True, the as4dkeng barrels/Legion locations whose
                   gate_expr is "R.pistons(state, player)" require Jack's
                   Schematic (threaded via BoundR in access_rules.py).
                   Only affects _build_sub_regions() below; the completion
                   condition itself is set separately in __init__.py's
                   set_rules() (R.pistons() is called directly there, not
                   through a gate_expr, so it takes its own explicit
                   require_schematic= argument).
    location_factory: ShadowManLocation constructor, passed in to avoid
                      circular imports between regions.py and locations.py.
    cadeauxsanity: this world's Cadeauxsanity option (renamed from
                   "Insanity" 2026-08-23) — when False, cadeaux-category
                   locations are excluded from the AP location pool
                   entirely (same treatment as "barrel", see _SKIP_CATS
                   above); when True they're included with no item-type
                   restriction. Threaded down into _build_sub_regions()
                   below.
    cadeaux_required: this world's resolved fogometers_cadeaux_required
                   option value (see __init__.py) — the real Cadeaux count
                   R.cadeaux_666() gates on (2026-07-24 fix; it used to
                   unconditionally return True regardless of this option).
                   None falls back to CADEAUX_666_VANILLA (666) in
                   access_rules.py. Threaded down into _build_sub_regions()
                   below the same way sl_thresholds/piston_combos are.
    cadeaux_gated_content: this world's CadeauxGatedContent option (see
                   options.py) — when False (default), the Fogometers Light
                   Soul location (fill.py's CADEAUX_666_LOCS) is excluded
                   from the AP location pool entirely, same treatment as
                   "barrel"/"enemy". When True it's included, backed by
                   generate_early()'s precollected-Cadeaux shortfall fix
                   (see __init__.py) to keep it solvable. Threaded down
                   into _build_sub_regions() below.
    cadeaux_bundle_representatives: this world's precomputed result of
                   compute_cadeaux_bundle_representatives() (see
                   __init__.py's generate_early(), self.random used to
                   build it once and store it on self so create_items()'s
                   item-pool sizing and this function's location
                   instantiation always agree on the exact same set).
                   None (or a bundle size of 1) means no bundling -- every
                   cadeaux-category row becomes its own AP location, same
                   as before this option existed. Threaded down into
                   _build_sub_regions() below.
    barrel_promoted_locs: Secret Trap barrel promotion (2026-08-01) — this
                   world's precomputed set of "barrel" category loc_keys
                   (normally always excluded, see _SKIP_CATS above) that
                   become real AP locations for this seed. Computed once in
                   __init__.py's generate_early() via self.random, same
                   "compute once, thread down, both create_items() and this
                   function agree on the exact set" pattern as
                   cadeaux_bundle_representatives just above. None means no
                   barrels promoted (every barrel row stays excluded, same
                   as before this feature existed).
    unique_retractor_keys: this world's UniqueRetractorKeys option (see
                   options.py) — when True, each liveside region's entrance
                   rule (below) requires that region's own dedicated
                   "Retractor - <region>" item (see items.py's
                   RETRACTOR_KEY_ITEM_NAMES) instead of the vanilla flat
                   "any 5 Retractors" count. No separate per-seed assignment
                   step is needed here (unlike the standalone randomizer's
                   retractor_level_assignment) — each named item already has
                   a fixed region identity, so AP's own fill decides where
                   each one lands, same as any other progression item.
    """
    # ── Create all regions ────────────────────────────────────────────────────
    regions: dict[str, Region] = {}
    for name in ALL_REGIONS:
        r = Region(name, player, multiworld)
        regions[name] = r
        multiworld.regions.append(r)

    menu    = regions[MENU]
    swamp   = regions[LOUISIANA_SWAMPLAND]
    marrow  = regions[DEADSIDE_MARROW_GATES]

    # ── Menu → Swampland (free) ───────────────────────────────────────────────
    _connect(menu, swamp, name="Swampland")

    # ── Swampland → Marrow Gates (free — entering deadside) ──────────────────
    _connect(swamp, marrow, name="Marrow Gates")

    # ── Marrow → deadside/temple regions (gate-locked) ───────────────────────
    # NOTE: ASYLUM_CATHEDRAL and ASYLUM_EXPERIMENTATION are deliberately NOT
    # in this list — they connect from ASYLUM_GATEWAYS (gated by eng_key)
    # below, not directly from marrow. See that block's comment for why this
    # matters (2026-07-20 fix — this used to connect both straight from
    # marrow, which silently dropped the eng_key requirement and let fill
    # place the Engineers Key behind its own lock, e.g. inside Florida,
    # which requires passing through Cathedral which requires the key).
    #
    # Entrance randomizer (2026-07-21, deadside_only mode): when
    # entrance_shuffle is set (a dict[portal_file, dest_portal_file] built
    # in __init__.py's generate_early(), see DEADSIDE_PORTAL_FILES), these
    # 9 connections (the 8 below plus Temple of Prophecy just after) are
    # built from the shuffled portal→region mapping instead of the fixed
    # vanilla list. Each of the 9 Deadside portal files keeps its OWN
    # physical soul-gate requirement — the same gate id it uses in the
    # vanilla gate_connections list below, full GATE_DEPENDENCIES ancestor
    # chain walked via R.gate() (make_portal_rule, fixed 2026-07-21 — see
    # DEADSIDE_PORTAL_GATE's comment block in access_rules.py) — but now
    # leads to whichever region entrance_shuffle assigned it. Internal
    # sub-connections below (Cathedral/Experimentation/Engine Block/
    # liveside) are unaffected either way — they key off the named regions
    # themselves ("however you reached Asylum: Gateways"), not the portal
    # that led there, so AP's own reachability graph composes them
    # correctly regardless of what the portal shuffle assigned.
    if entrance_shuffle is None:
        gate_connections = [
            (DEADSIDE_WASTELAND, "GATE_DEADSIDE_WASTELAND"),
            (ASYLUM_GATEWAYS,    "GATE_DEADSIDE_ASYLUM"),
            (TEMPLE_FIRE,        "GATE_DEADSIDE_PATH_3"),
            (ASYLUM_LAVADUCTS,   "GATE_DEADSIDE_LAVADUCTS"),
            (TEMPLE_BLOOD,       "GATE_DEADSIDE_BLOOD"),
            (ASYLUM_FOGOMETERS,  "GATE_DEADSIDE_FOGOMETERS"),
        ]
        for region_name, gate_id in gate_connections:
            _connect(
                marrow,
                regions[region_name],
                rule=make_entrance_rule(gate_id, gate_values, player, sl_thresholds),
                name=f"{region_name} [{gate_id}]",
            )

        # Cageways and Playrooms (2026-08-03, Jon confirmed live): each
        # reachable via their own front door OR the same lower-Deadside
        # backtrack routes Temple of Prophecy uses below (Path 7 alone, or
        # Asylum+Baton+Gad2) — backtracking from the lower Deadside cluster
        # bypasses that gate's own SL entirely, not just its ancestor chain,
        # so make_entrance_rule's single-gate form (used above) is wrong for
        # these two now. Uses make_portal_rule — the same route-list factory
        # entrance-shuffle already uses below — since CAGEWAYS_ROUTES /
        # PLAYROOMS_ROUTES are in that same shape. See their own comment in
        # access_rules.py.
        _connect(
            marrow,
            regions[ASYLUM_CAGEWAYS],
            rule=make_portal_rule(CAGEWAYS_ROUTES, gate_values, player, sl_thresholds),
            name=f"{ASYLUM_CAGEWAYS} [front door, or Path 7, or Asylum+Baton+Gad2]",
        )
        _connect(
            marrow,
            regions[ASYLUM_PLAYROOMS],
            rule=make_portal_rule(PLAYROOMS_ROUTES, gate_values, player, sl_thresholds),
            name=f"{ASYLUM_PLAYROOMS} [front door, or Path 7, or Asylum+Baton+Gad2]",
        )

        # Temple of Prophecy: three independent routes. Third route added
        # 2026-07-26 (Jon, confirmed live in-game) — the hand-built rule
        # here only modeled "Path 7 alone" or "Cageways+Playrooms+Path 6",
        # missing the same Asylum+Baton+Gad2 shortcut _LOWER_DEADSIDE_ROUTES
        # already grants its sibling lower-Deadside gates (Lavaducts,
        # La Lame, Blood, Fogometers). Jon reached Temple of Prophecy via
        # that exact "more items required" path (Asylum SL gate + Baton +
        # Gad 2 walk-on-lava) while AP logic still showed it unreachable —
        # confirmed missing route, not a false positive on his end.
        _connect(
            marrow,
            regions[TEMPLE_PROPHECY],
            rule=lambda state, gv=gate_values, p=player, st=sl_thresholds: (
                R.gate("GATE_DEADSIDE_PATH_7", state, p, _gate_sl=gv, _soul_thresholds=st) or (
                    R.gate("GATE_DEADSIDE_CAGEWAYS", state, p, _gate_sl=gv, _soul_thresholds=st) and
                    R.gate("GATE_DEADSIDE_PLAYROOMS", state, p, _gate_sl=gv, _soul_thresholds=st) and
                    R.gate("GATE_DEADSIDE_PATH_6", state, p, _gate_sl=gv, _soul_thresholds=st)
                ) or (
                    R.gate("GATE_DEADSIDE_ASYLUM", state, p, _gate_sl=gv, _soul_thresholds=st) and
                    R.baton(state, p) and
                    R.gad2_walk(state, p)
                )
            ),
            name=f"{TEMPLE_PROPHECY} [Path 7, or Cageways+Playrooms+Path 6, or Asylum+Baton+Gad2]",
        )
    else:
        for portal_file, dest_portal_file in entrance_shuffle.items():
            gate        = DEADSIDE_PORTAL_GATE[portal_file]
            dest_region = DEADSIDE_PORTAL_REGION[dest_portal_file]
            rule        = make_portal_rule(gate, gate_values, player, sl_thresholds)
            gate_label  = gate if isinstance(gate, str) else "lower-Deadside routes"
            _connect(
                marrow,
                regions[dest_region],
                rule=rule,
                name=f"{dest_region} [{portal_file} portal, {gate_label}]",
            )

    # Asylum Gateways → Cathedral / Experimentation Rooms (eng_key-gated,
    # OR Gad 2 walk-on-lava bypass — confirmed live by Jon 2026-08-03: Gad 2
    # lets you walk around the Eng Key-locked door entirely, same "physical
    # shortcut around a locked door" shape as the Cageways/Playrooms
    # backtrack above; OR'd directly here rather than needing a separate
    # route-list constant since Cathedral/Experimentation have no coffin
    # gate of their own, only the eng_key item check).
    # Matches the standalone's topology: these two areas are NOT directly
    # reachable from Marrow — you must first pass through Gateways (its own
    # coffin-gate check, above) and then hold the Engineers Key (or Gad 2).
    # This also matters for Florida/liveside access below, which requires
    # passing through Cathedral.
    _connect(regions[ASYLUM_GATEWAYS], regions[ASYLUM_CATHEDRAL],
              rule=lambda state, p=player: R.eng_key(state, p) or R.gad2_walk(state, p),
              name=f"{ASYLUM_CATHEDRAL} [eng_key or gad2_walk]")
    _connect(regions[ASYLUM_GATEWAYS], regions[ASYLUM_EXPERIMENTATION],
              rule=lambda state, p=player: R.eng_key(state, p) or R.gad2_walk(state, p),
              name=f"{ASYLUM_EXPERIMENTATION} [eng_key or gad2_walk]")

    # NOTE: Temple of Prophecy's marrow connection is built above, inside the
    # entrance_shuffle if/else block (vanilla: the "two independent routes"
    # rule right after gate_connections; shuffled: folded into the generic
    # portal loop like every other Deadside destination). A duplicate
    # unconditional _connect() used to live here too — leftover from before
    # the 2026-07-21 entrance-randomizer restructuring — which caused an
    # "already exists in the entrance cache" crash in vanilla mode (two
    # identically-named entrances marrow -> Temple of Prophecy) and a
    # spurious backdoor route in shuffled mode (bypassing whatever portal
    # entrance_shuffle actually assigned to Temple of Prophecy). Removed.

    # ── Asylum internal connections ───────────────────────────────────────────
    # Cageways → Engine Block hub (2026-08-06 fix, Jon confirmed live: this
    # was modeled as a free connection once Cageways is reached, in both
    # this repo and the standalone -- WRONG, per Jon: reaching Engine Block
    # from Cageways requires the Engineers Key, same as the separate
    # Gateways -> Cathedral/Experimentation path just above. Found via a
    # real UT false-positive report ("Asylum: Engine Block - Barrel -
    # Asylum 14" showing in-logic without eng_key) rather than assumed --
    # confirmed with Jon before changing, per this file's own repeated
    # history of wrong gate-topology assumptions elsewhere in this area.
    _connect(regions[ASYLUM_CAGEWAYS], regions[ASYLUM_ENGINE_BLOCK],
              rule=lambda state, p=player: R.eng_key(state, p),
              name=f"{ASYLUM_ENGINE_BLOCK} [eng_key]")

    # ── Liveside entry from Asylum Cathedral ──────────────────────────────────
    # Fixed 2026-07-20: this used to connect straight from marrow, which (via
    # the same edge-sourcing bug as Cathedral/Experimentation above) dropped
    # the real prerequisite entirely — the standalone requires passing
    # through ASYLUM_CATHEDRAL (which itself needs eng_key) to reach any
    # liveside region. Sourcing from Cathedral here restores that chain:
    # Marrow → Asylum Gateways → [eng_key] → Cathedral → [SL2 + 5 Retractors] → liveside.
    #
    # Each liveside region requires SL2 plus ALL 5 Retractors, matching the
    # standalone randomizer's flat >=5 rule. The in-game mechanic is
    # "Retractor count > liveside regions already visited", which is
    # order-dependent: the previous graduated 1–5 model (London=1 … Salvage=5)
    # let fill place e.g. Retractor #2 inside London while the player could
    # legally spend their only visit on Salvage first, stranding it (softlock).
    # Flat >=5 means logic never expects liveside checks until every Retractor
    # is held — with 5 held, count(5) > visited(≤4) always, so any visit order
    # works. Conservative but ordering-safe, same trade the standalone made.
    # SL2 is unaffected by gate shuffle (it's not a coffin gate) — R.sl2()
    # used directly rather than R.gate(). It IS affected by soul_threshold_mode
    # though, so sl_thresholds is still passed through (see 2026-07-20 fix).
    # Unique Retractor Keys (2026-08-18): when on, each region's rule swaps
    # the flat >=5 count for state.has() on that region's own dedicated
    # "Retractor - <region>" item — ki=_key_item captures the loop variable
    # by default-arg, same pattern p=player/st=sl_thresholds already use
    # here, so each closure keeps its own region's item name instead of all
    # five sharing whatever `level` happens to be after the loop ends.
    for level in (
        LIVESIDE_LONDON,
        LIVESIDE_FLORIDA,
        LIVESIDE_PRISON,
        LIVESIDE_QUEENS,
        LIVESIDE_SALVAGE,
    ):
        if unique_retractor_keys:
            _key_item = RETRACTOR_KEY_ITEM_NAMES[level]
            _connect(
                regions[ASYLUM_CATHEDRAL],
                regions[level],
                rule=lambda state, p=player, st=sl_thresholds, ki=_key_item: (
                    R.sl2(state, p, _soul_thresholds=st) and
                    state.has(ki, p)
                ),
                name=f"{level} [SL2 + {_key_item}]",
            )
        else:
            _connect(
                regions[ASYLUM_CATHEDRAL],
                regions[level],
                rule=lambda state, p=player, st=sl_thresholds: (
                    R.sl2(state, p, _soul_thresholds=st) and
                    state.count("Retractor", p) >= 5
                ),
                name=f"{level} [SL2 + 5 Retractors]",
            )

    # ── Liveside → Engine Block sections (Night + special item) ──────────────
    engine_rules = {
        LIVESIDE_LONDON:  lambda state, p=player: R.night(state, p),
        LIVESIDE_FLORIDA: lambda state, p=player: R.night(state, p),
        LIVESIDE_PRISON:  lambda state, p=player: R.night(state, p) and R.prison_key_card(state, p),
        LIVESIDE_QUEENS:  lambda state, p=player: R.night(state, p) and R.poigne(state, p),
        LIVESIDE_SALVAGE: lambda state, p=player: R.night(state, p) and R.gad3_swim(state, p),
    }
    engine_sections = {
        LIVESIDE_LONDON:  ASYLUM_ENGINE_BLOCK_LONDON,
        LIVESIDE_FLORIDA: ASYLUM_ENGINE_BLOCK_FLORIDA,
        LIVESIDE_PRISON:  ASYLUM_ENGINE_BLOCK_PRISON,
        LIVESIDE_QUEENS:  ASYLUM_ENGINE_BLOCK_QUEENS,
        LIVESIDE_SALVAGE: ASYLUM_ENGINE_BLOCK_SALVAGE,
    }
    engine_tags = {
        LIVESIDE_LONDON:  "Night",
        LIVESIDE_FLORIDA: "Night",
        LIVESIDE_PRISON:  "Night + Prison Key Card",
        LIVESIDE_QUEENS:  "Night + Poigne",
        LIVESIDE_SALVAGE: "Night + Gad3 Swim",
    }
    for level, section in engine_sections.items():
        _connect(
            regions[level],
            regions[section],
            rule=engine_rules[level],
            name=f"{section} [{engine_tags[level]}]",
        )

    # ── Populate locations ────────────────────────────────────────────────────
    for name in ALL_REGIONS:
        if name == MENU:
            continue
        _build_sub_regions(
            regions[name], name, multiworld, player,
            gate_values, location_factory, sl_thresholds, piston_combos,
            cadeauxsanity, cadeaux_required, cadeaux_gated_content,
            cadeaux_bundle_representatives, barrel_promoted_locs,
        )