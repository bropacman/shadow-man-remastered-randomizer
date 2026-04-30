"""
fill.py
───────
Forward-sweep placement algorithm for Shadow Man Remastered standalone randomizer.
Guarantees beatable seeds without Archipelago installed.

HOW PLACEMENT WORKS
───────────────────
All items — souls, progression, retractors, accumulators, gad, weapons, lore,
and bonus — are placed in a single forward sweep. Items are sorted so that
progression-critical items are placed first and weapons/lore/bonus last, ensuring
named weapon/lore/bonus slots are available when those items are placed.

Each iteration:
  1. Build current state from all items placed so far.
  2. Find all reachable, unfilled slots that accept the current item type.
  3. Place the item, update state, repeat.

Because inventory grows incrementally, gates open naturally as soul count crosses
their thresholds — no sphere boundaries, no assumed future items.

SLOT TAXONOMY
─────────────
SLOT_ACCEPTS defines exactly which item categories each slot type accepts.
This is the single source of truth for placement restrictions.

  soul/cadeaux/barrel slots  → accept soul items only
  weapon slots               → accept weapon items only
  lore slots                 → accept lore items only
  bonus slots                → accept bonus items only
  progression/retractor/
  accumulator/gad slots      → accept progression, retractor, accumulator, gad

GATE SHUFFLE
────────────
When shuffle_gates=True, _shuffle_gates() runs first and assigns new SL
thresholds to all non-locked gates before any placement begins. All three
starting gates (WASTELAND, ASYLUM, PATH_3) are guaranteed SL <= 3.

PROGRESSION BALANCING
─────────────────────
The balancing slider (0-100) controls how aggressively key items are pushed
toward deeper locations. At 0, placement is uniform random. At 100, key items
are strongly biased toward the highest-gated reachable locations.

ECLIPSER MUTUAL DEPENDENCY
──────────────────────────
All three Eclipser parts are mutually dependent — NIGHT requires all three.
_gate_reserved() handles this via GATE_GROUP: it blocks placement behind any
NIGHT-gated slot until all three Eclipsers are already in inventory.
"""

from __future__ import annotations
from dataclasses import dataclass
from collections import Counter
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).parent))

from extracted_locations import RAW_LOCATIONS, LOCATION_TABLE
from access_rules import R, GATE_VANILLA_SL
from constants import GATE_PRESETS

# ── Player constant ────────────────────────────────────────────────────────────

PLAYER = 1

# ── Starting items ─────────────────────────────────────────────────────────────

STARTING_ITEMS: set[str] = {
    "RSC_X_BOOK_OF_SHADOWS"
}

# ── Permanently excluded locations ────────────────────────────────────────────

EXCLUDED_LOCS: frozenset[str] = frozenset({
    "t4ndgad:quest.rsc:0x0072",   # cut content, no entrance
    # "deadside:quest.rsc:0x0102",  # SL10 + POIGNE - Book of Shadows
    # "ah4fogom:quest.rsc:0x2742",  # SL10 + CADEAUX_666 — Light Soul
    # "ah4fogom:quest.rsc:0x26B2",  # SL10 + CADEAUX_666 — Barrel
    # "ah4fogom:quest.rsc:0x26FA",  # SL10 + CADEAUX_666 — Barrel
})

FIXED_SOUL_LOCS: list = [
    loc for loc in RAW_LOCATIONS
    if loc.category in ("boss", "true_form")
]

@dataclass(frozen=True)
class _SynthLoc:
    level_id:     str
    source_file:  str
    offset:       int
    level_region: str
    gate_expr:    str | None
    gate_raw:     str | None = None   # ← ADD
    category:     str = "true_form"

    @property
    def loc_key(self) -> str:
        return f"{self.level_id}:{self.source_file}:0x{self.offset:04X}"


def apply_true_form_remap(loc_key_remap: dict[str, str] | None) -> list:
    if not loc_key_remap:
        return FIXED_SOUL_LOCS

    _enemy_cache: dict | None = None
    def _enemy_table() -> dict:
        nonlocal _enemy_cache
        if _enemy_cache is None:
            from extracted_enemy_locations import ENEMY_TABLE
            _enemy_cache = ENEMY_TABLE
        return _enemy_cache

    remapped = []
    for loc in FIXED_SOUL_LOCS:
        if loc.category != "true_form":
            remapped.append(loc)
            continue
        new_key = loc_key_remap.get(loc.loc_key)
        if new_key is None or new_key == loc.loc_key:
            remapped.append(loc)
            continue
        new_loc = LOCATION_TABLE.get(new_key)
        if new_loc is not None:
            remapped.append(new_loc)
            continue
        enemy_rec = _enemy_table().get(new_key)
        if enemy_rec is None:
            raise KeyError(
                f"true_form_loc_remap points to {new_key!r} which exists in "
                f"neither LOCATION_TABLE nor ENEMY_TABLE. "
                f"Check offset format (0x04AA not 0x4AA)."
            )
        if not enemy_rec.level_region:
            raise ValueError(
                f"True form remapped to {new_key!r} but level_region is not "
                f"mapped. Populate level_region and sub_region in enemy_locations.csv."
            )
        remapped.append(_SynthLoc(
            level_id     = enemy_rec.level_id,
            source_file  = enemy_rec.source_file,
            offset       = enemy_rec.offset,
            level_region = enemy_rec.level_region,
            gate_expr    = enemy_rec.gate_expr,
        ))
    return remapped

# ── Slot taxonomy ──────────────────────────────────────────────────────────────
#
# Single source of truth: maps each slot category to the set of item categories
# that may be placed there. The candidate filter uses this directly.
# insanity mode bypasses these restrictions entirely.

SLOT_ACCEPTS: dict[str, frozenset[str]] = {
    # Soul-eligible slots — only soul items go here
    "soul":        frozenset({"soul"}),
    "cadeaux":     frozenset({"soul"}),
    "barrel":      frozenset({"soul"}),
    # Named + progression slots — all share the same pool
    "weapon":      frozenset({"progression", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
    "lore":        frozenset({"progression", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
    "bonus":       frozenset({"progression", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
    "progression": frozenset({"progression", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
    "retractor":   frozenset({"progression", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
    "accumulator": frozenset({"progression", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
    "gad":         frozenset({"progression", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
}

# Derived sets used for pool building and fallback logic
ALL_SLOT_CATS  = frozenset(SLOT_ACCEPTS.keys())
SOUL_SLOT_CATS = frozenset(k for k, v in SLOT_ACCEPTS.items() if "soul" in v)
PROG_SLOT_CATS = frozenset(k for k, v in SLOT_ACCEPTS.items() if "progression" in v)
WLB_CATS       = frozenset({"weapon", "lore", "bonus"})  # placed last in sweep
FILLER_SLOT_CATS = frozenset({"barrel", "cadeaux"})  # no logic dependency, filled in filler phase

# ── Location pool ──────────────────────────────────────────────────────────────

_CORE_CATS      = ALL_SLOT_CATS | frozenset({"true_form", "boss"})
_ALWAYS_EXCLUDE = frozenset({"enemy"})


def _is_ap_location(raw) -> bool:
    return raw.category in _CORE_CATS


def _is_checkable(raw) -> bool:
    return raw.category in ALL_SLOT_CATS and raw.category not in _ALWAYS_EXCLUDE


AP_LOCATIONS: list = [r for r in RAW_LOCATIONS if _is_ap_location(r)]

CHECKABLE_LOCS: list = [
    l for l in AP_LOCATIONS
    if l.loc_key not in EXCLUDED_LOCS and _is_checkable(l)
]

# ── Gate region mapping ────────────────────────────────────────────────────────

LIVESIDE_REGIONS: frozenset[str] = frozenset({
    "Down Street Station, London",
    "Gardelle County Jail, Texas",
    "Salvage Yard, Mojave Desert",
    "Mordant Street, Queens, NY",
    "Summer Camp, Florida",
    "Asylum: Engine Block - London",
    "Asylum: Engine Block - Prison",
    "Asylum: Engine Block - Florida",
    "Asylum: Engine Block - Salvage",
    "Asylum: Engine Block - Queens",
})

REGION_GATES: dict[str, object] = {
    "Deadside - Wasteland"         : "GATE_DEADSIDE_WASTELAND",
    "Asylum: Gateways"             : "GATE_DEADSIDE_ASYLUM",
    "Asylum: Cathedral of Pain"    : "GATE_DEADSIDE_ASYLUM",
    "Asylum: Experimentation Rooms": "GATE_DEADSIDE_ASYLUM",
    "Temple of Fire (Toucher)"     : "GATE_DEADSIDE_PATH_3",
    "Asylum: Cageways"             : "GATE_DEADSIDE_CAGEWAYS",
    "Asylum: Engine Block"         : "GATE_DEADSIDE_CAGEWAYS",
    "Asylum: Playrooms"            : "GATE_DEADSIDE_PLAYROOMS",
    "Temple of Prophecy (Marcher)" : [
        ["GATE_DEADSIDE_PATH_7"],
        ["GATE_DEADSIDE_CAGEWAYS", "GATE_DEADSIDE_PLAYROOMS", "GATE_DEADSIDE_PATH_6"],
    ],
    "Asylum: Lavaducts"            : "GATE_DEADSIDE_LAVADUCTS",
    "Temple of Blood (Nager)"      : "GATE_DEADSIDE_BLOOD",
    "Asylum: The Fogometers"       : "GATE_DEADSIDE_FOGOMETERS",
}

VANILLA_GAD_REGIONS: list[str] = [
    "Temple of Fire (Toucher)",
    "Temple of Prophecy (Marcher)",
    "Temple of Blood (Nager)",
]

_SOUL_THRESHOLDS: dict[int, int] = {
    0:0, 1:1, 2:3, 3:7, 4:15, 5:23, 6:35, 7:51, 8:71, 9:95, 10:120,
}

# ── Gate shuffle ───────────────────────────────────────────────────────────────

def _shuffle_gates(
    rng: random.Random,
    locked: frozenset[str] = frozenset(),
    max_sl: int | None = None,
    safe: bool = True,
) -> dict[str, int]:
    """
    Shuffle SL thresholds across all non-locked gates.

    locked:  gates excluded from shuffling — always keep their vanilla SL.
    max_sl:  if set, clamps the shuffleable SL pool to values <= max_sl.
    safe:    if True, enforces a hierarchy of gate caps before returning:
               1. Gates with fixed souls or named slots: SL <= 9
               2. Starting gates (WASTELAND, ASYLUM, PATH_3): SL <= 3
               3. WASTELAND: SL <= 2  (tighter — needs 7-soul start circuit)
               4. PATH_3: SL <= 5    (Temple of Fire must open mid-game)
             Caps are enforced in this order so tighter constraints don't
             get undone by looser ones running after them.
    """
    starting_gates = {
        "GATE_DEADSIDE_WASTELAND",
        "GATE_DEADSIDE_ASYLUM",
        "GATE_DEADSIDE_PATH_3",
    }

    shuffleable = [g for g in GATE_VANILLA_SL if g not in locked]
    sl_pool = [GATE_VANILLA_SL[g] for g in shuffleable]

    if max_sl is not None:
        sl_pool = [min(sl, max_sl) for sl in sl_pool]

    rng.shuffle(sl_pool)
    temp_map = dict(zip(shuffleable, sl_pool))

    if not safe:
        gate_remap = {g: GATE_VANILLA_SL[g] for g in locked if g in GATE_VANILLA_SL}
        gate_remap.update(temp_map)
        return gate_remap

    # ── Build SL9-cap set ─────────────────────────────────────────────────────
    # Gates controlling regions with fixed souls (boss/true_form) or named
    # progression slots cannot go to SL10 — 120 souls is unreachable if
    # the slots needed to accumulate them are locked behind SL10.
    protected_regions = (
        {loc.level_region for loc in FIXED_SOUL_LOCS} |
        {loc.level_region for loc in CHECKABLE_LOCS if loc.category in PROG_SLOT_CATS}
    )
    sl9_cap_gates = set()
    for region, gate in REGION_GATES.items():
        if region in protected_regions:
            if isinstance(gate, str):
                sl9_cap_gates.add(gate)
            else:
                for route in gate:
                    sl9_cap_gates.update(route)

    # ── Define constraints in priority order ──────────────────────────────────
    # Each entry: (gate_id, max_allowed_sl, excluded_swap_targets)
    # Constraints are applied in order — later ones cannot undo earlier ones
    # because swap targets are filtered to only gates that won't violate
    # already-applied constraints.
    constraints = []

    # 1. SL9 cap for all protected gates
    for g in sl9_cap_gates:
        constraints.append((g, 9, sl9_cap_gates | starting_gates))

    # 2. Starting gates: SL3
    for g in starting_gates:
        constraints.append((g, 3, starting_gates))

    # 3. WASTELAND: tighter SL2
    constraints.append(("GATE_DEADSIDE_WASTELAND", 2, starting_gates))

    # 4. PATH_3: SL5
    constraints.append(("GATE_DEADSIDE_PATH_3", 5, starting_gates))

    # ── Apply constraints ─────────────────────────────────────────────────────
    for gate_id, max_sl_allowed, excluded_targets in constraints:
        if gate_id not in temp_map:
            continue
        if temp_map[gate_id] <= max_sl_allowed:
            continue
        # Find a swap partner: not excluded, not already over its own cap
        for other_g in shuffleable:
            if other_g == gate_id:
                continue
            if other_g in excluded_targets:
                continue
            if temp_map[other_g] > max_sl_allowed:
                continue
            temp_map[gate_id], temp_map[other_g] = temp_map[other_g], temp_map[gate_id]
            break

    gate_remap = {g: GATE_VANILLA_SL[g] for g in locked if g in GATE_VANILLA_SL}
    gate_remap.update(temp_map)
    return gate_remap

def build_gate_rules(gate_remap: dict[str, int] | None = None) -> dict:
    from access_rules import set_gate_remap
    import regions as regions_module

    set_gate_remap(gate_remap or {})

    class MockWorld:
        def __init__(self):
            self.regions = []
            self.player = PLAYER

    world = MockWorld()
    parent_map = {}
    original_connect = regions_module._connect

    def recording_connect(source, target, rule=None):
        parent_map[target.name] = (source.name, rule or (lambda state: True))
        original_connect(source, target, rule)

    regions_module._connect = recording_connect
    regions_module.create_regions(world, PLAYER)
    regions_module._connect = original_connect

    rules = {}

    def get_rule(name):
        if name in rules:
            return rules[name]
        if name not in parent_map:
            rules[name] = lambda state: True
            return rules[name]
        parent_name, entrance_rule = parent_map[name]
        parent_rule = get_rule(parent_name)
        rules[name] = lambda state, e=entrance_rule, p=parent_rule: p(state) and e(state)
        return rules[name]

    for region in world.regions:
        get_rule(region.name)

    return rules


_LEVEL_RULES: dict = build_gate_rules(gate_remap=None)

# ── State ──────────────────────────────────────────────────────────────────────

class FakeState:
    __slots__ = ("inv", "soul_count", "retractor_count", "cadeaux_count", "reached_regions")

    def __init__(
        self,
        inv: dict[str, int] | None = None,
        soul_count: int = 0,
        retractor_count: int = 0,
        cadeaux_count: int = 0,
        reached_regions: set[str] | None = None,
    ):
        self.inv = dict(inv) if inv else {}
        self.soul_count = soul_count
        self.retractor_count = retractor_count
        self.cadeaux_count = cadeaux_count
        self.reached_regions = reached_regions or set()
        for item in STARTING_ITEMS:
            self.inv.setdefault(item, 1)

    def has(self, item: str, player: int) -> bool:
        return self.inv.get(item, 0) >= 1

    def count(self, item: str, player: int) -> int:
        if item == "_souls":      return self.soul_count
        if item == "_retractors": return self.retractor_count
        if item == "_cadeaux":    return self.cadeaux_count
        return self.inv.get(item, 0)

    def can_reach(self, name: str, _type: str, _player: int) -> bool:
        return name in self.reached_regions

# ── Reachability ───────────────────────────────────────────────────────────────

def _reachable(loc, state: FakeState, level_rules: dict) -> bool:
    region_rule = level_rules.get(loc.level_region)
    if region_rule and not region_rule(state):
        return False
    if not loc.gate_expr:
        return True
    return eval(loc.gate_expr, {"R": R, "state": state, "player": PLAYER})

# ── Gate reservation ───────────────────────────────────────────────────────────
#
# Prevents placing an item into a slot whose local rule requires something not
# yet in inventory — e.g. don't place behind ENG_KEY until ENG_KEY is placed,
# or behind NIGHT until all three Eclipsers are placed.

GATE_KEY: dict[str, str] = {
    "r.eng_key(":         "RSC_X_ENGINEERS_KEY",
    "r.prison_key_card(": "RSC_X_PRISON_KEY_CARD",
    "r.calabash(":        "RSC_X_CALABASH",
    "r.baton(":           "RSC_X_BATON",
    "r.flambeau(":        "RSC_X_FLAMBEAU",
    "r.marteau(":         "RSC_X_MARTEAU",
    "r.poigne(":          "RSC_X_POIGNE",
}

GATE_GROUP: dict[str, frozenset] = {
    # NIGHT requires all three Eclipsers — block placement until all are in inventory
    "r.night(": frozenset({"RSC_X_ECLIPSER_PART1", "RSC_X_ECLIPSER_PART2", "RSC_X_ECLIPSER_PART3"}),
}

GATE_COUNT: dict[str, tuple[str, int]] = {
    "r.x3_accumulator(": ("RSC_X_ACCUMULATOR", 3),
    "r.cadeaux_666(":    ("cadeaux", 553),
    # can update to 666 upon full mapping of cadeaux
    # currently at 553/666 cadeaux
    # "r.cadeaux_666(":    ("cadeaux", 666),
    "r.gad1_hand(":      ("RSC_X_GAD_PICKUP", 1),
    "r.gad2_walk(":      ("RSC_X_GAD_PICKUP", 2),
    "r.gad3_swim(":      ("RSC_X_GAD_PICKUP", 3),
}


def _gate_reserved(loc, state: FakeState) -> bool:
    if not loc.gate_expr:
        return False
    expr_l = loc.gate_expr.lower()

    for token, key_item in GATE_KEY.items():
        if token in expr_l and not state.has(key_item, PLAYER):
            return True

    for token, group_items in GATE_GROUP.items():
        if token in expr_l and not all(state.has(i, PLAYER) for i in group_items):
            return True

    for token, (item, count) in GATE_COUNT.items():
        if token in expr_l:
            if item == "cadeaux"   and state.cadeaux_count < count:   return True
            if item == "retractor" and state.retractor_count < count: return True
            if item not in ("cadeaux", "retractor") and state.count(item, PLAYER) < count:
                return True

    return False

# ── Unique items ───────────────────────────────────────────────────────────────

ECLIPSER_ITEMS = frozenset({"RSC_X_ECLIPSER_PART1", "RSC_X_ECLIPSER_PART2", "RSC_X_ECLIPSER_PART3"})
MAJOR_TOOLS    = frozenset({
    "RSC_X_BATON", "RSC_X_FLAMBEAU", "RSC_X_MARTEAU", "RSC_X_CALABASH",
    "RSC_X_POIGNE", "RSC_X_ENGINEERS_KEY", "RSC_X_PRISON_KEY_CARD",
})
ALL_UNIQUES = ECLIPSER_ITEMS | MAJOR_TOOLS

# ── Item pool ──────────────────────────────────────────────────────────────────

def build_item_pool(
    locations: list,
    shuffle_weapons: bool = True,
    shuffle_lore: bool = True,
    shuffle_bonus: bool = False,
) -> list:
    """
    Build the item pool for placement. Always includes all logic-critical items
    (souls, progression, retractors, accumulators, gad). Includes weapons/lore/bonus
    based on their respective shuffle flags.
    """
    include_cats = {"soul", "progression", "retractor", "accumulator", "gad"}
    if shuffle_weapons: include_cats.add("weapon")
    if shuffle_lore:    include_cats.add("lore")
    if shuffle_bonus:   include_cats.add("bonus")

    pool = [loc for loc in locations if loc.category in include_cats]

    # Uniqueness audit
    non_soul_counts = Counter(loc.object for loc in pool if loc.category != "soul")
    for item in ALL_UNIQUES:
        count = non_soul_counts.get(item, 0)
        if count > 1:
            raise ValueError(f"DATA BUG: '{item}' exists {count} times in CSV!")
        if count == 0:
            print(f"Warning: '{item}' not found in pool.")

    soul_ids = [loc.instance_id for loc in pool if loc.category == "soul"]
    duped = [sid for sid, n in Counter(soul_ids).items() if n > 1]
    if duped:
        raise ValueError(f"DATA BUG: Duplicate soul instance_ids: {duped}")

    return pool


# ── Simulate playthrough ───────────────────────────────────────────────────────

def simulate_playthrough(
    placement, locations, level_rules,
    debug=False, collect_spheres=False, shuffle_gad_temples=False,
    item_category=None,
):
    """
    Simulate a full playthrough of the given placement.

    item_category: pre-built {object: category} dict. Pass this in from
    assumed_fill to avoid rebuilding it on every iteration of the sweep (~130x).
    If None, it is built internally — used by validate_fill and patcher.
    """
    inv: dict[str, int] = {}
    soul_count = retractor_count = cadeaux_count = 0
    reached_keys: set[str] = set()
    reached_regions: set[str] = set()
    fixed_keys = {l.loc_key for l in locations if l.category in ("boss", "true_form")}
    loc_by_key = {l.loc_key: l for l in locations}

    if item_category is None:
        item_category = {
            p.object: p.category
            for p in build_item_pool(locations, shuffle_weapons=True, shuffle_lore=True, shuffle_bonus=False)
        }

    spheres = []

    for sphere in range(1, 601):
        progress = False
        st = FakeState(inv, soul_count, retractor_count, cadeaux_count, reached_regions)
        sphere_progs = []
        sphere_cadeaux_count = 0
        sphere_regions = []
        soul_count_start = soul_count

        for region_name, rule in level_rules.items():
            if region_name not in reached_regions and rule(st):
                if "[" not in region_name:
                    reached_regions.add(region_name)
                    sphere_regions.append(region_name)
                    progress = True
                else:
                    reached_regions.add(region_name)

        if not shuffle_gad_temples:
            for i, region in enumerate(VANILLA_GAD_REGIONS):
                grant_key = f"vanilla_gad:{region}"
                current_gad = inv.get("RSC_X_GAD_PICKUP", 0)
                if region in reached_regions and grant_key not in reached_keys and current_gad == i:
                    reached_keys.add(grant_key)
                    inv["RSC_X_GAD_PICKUP"] = current_gad + 1
                    progress = True

        current_sl = next(
            (sl for sl, th in sorted(_SOUL_THRESHOLDS.items(), reverse=True) if soul_count >= th), 0
        )

        for loc in locations:
            if loc.loc_key in reached_keys:
                continue
            if not _reachable(loc, st, level_rules):
                continue

            reached_keys.add(loc.loc_key)
            progress = True

            if loc.level_region not in reached_regions:
                reached_regions.add(loc.level_region)
                sphere_regions.append(loc.level_region)

            if loc.loc_key in fixed_keys:
                soul_count += 1
                continue

            if loc.loc_key not in placement:
                continue

            source_loc = placement[loc.loc_key]
            placed_object = source_loc.object
            category = item_category.get(placed_object, "filler")
            item_friendly = source_loc.friendly_name or placed_object

            if category == "soul":
                if loc.level_region in LIVESIDE_REGIONS:
                    night_state = FakeState(inv, soul_count, retractor_count, cadeaux_count, reached_regions)
                    if not R.night(night_state, PLAYER):
                        reached_keys.discard(loc.loc_key)  # don't mark as reached yet
                        continue
                soul_count += 1
            elif category == "retractor":   retractor_count += 1
            elif category == "cadeaux":     cadeaux_count += 1
            elif category == "accumulator": inv[placed_object] = inv.get(placed_object, 0) + 1
            else:                           inv[placed_object] = inv.get(placed_object, 0) + 1

            if debug or collect_spheres:
                if category == "cadeaux" or "cadeaux" in placed_object.lower():
                    sphere_cadeaux_count += 1
                elif category in ("progression", "retractor", "accumulator", "gad",
                                  "weapon", "lore", "bonus"):
                    placed_loc = loc_by_key.get(loc.loc_key)
                    slot_name = (placed_loc.friendly_name or placed_object) if placed_loc else placed_object
                    left = f"\"[{loc.level_region}] {slot_name}\":"
                    sphere_progs.append((placed_object, left, item_friendly, loc.level_region))

        if not progress:
            break

        if debug:
            print(f"\n[SPHERE {sphere:2}] Souls: {soul_count_start}→{soul_count} (SL{current_sl})")
            if sphere_regions: print(f"  🗺️  New Areas : {', '.join(sphere_regions)}")
            if sphere_progs:
                for obj, left, friendly, region in sphere_progs:
                    print(f"  🔑 {left:<80} {friendly}")
            if sphere_cadeaux_count > 0:
                print(f"  🎁 Cadeaux   : Found {sphere_cadeaux_count} total")

        if collect_spheres:
            spheres.append({
                "sphere":      sphere,
                "souls_start": soul_count_start,
                "souls_end":   soul_count,
                "sl":          current_sl,
                "new_areas":   list(sphere_regions),
                "items":       list(sphere_progs),
                "cadeaux":     sphere_cadeaux_count,
            })

    return reached_keys, FakeState(inv, soul_count, retractor_count, cadeaux_count, reached_regions), spheres


# ── Assumed fill ───────────────────────────────────────────────────────────────

def assumed_fill(
    rng: random.Random,
    verbose: bool = False,
    progression_balancing: int = 50,
    gate_remap: dict[str, int] | None = None,
    shuffle_gates: bool = False,
    no_soul_gates: bool = False,
    lock_gates: frozenset[str] = frozenset(),
    max_sl: int | None = None,
    safe: bool = True,
    insanity: int = 0,
    shuffle_weapons: bool = True,
    shuffle_lore: bool = True,
    shuffle_bonus: bool = False,
    shuffle_gad_temples: bool = False,
    true_form_loc_remap: dict[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, int]]:

    # ── Step 1: Gates ─────────────────────────────────────────────────────────
    if shuffle_gates:
        gate_remap = _shuffle_gates(rng, locked=lock_gates, max_sl=max_sl, safe=safe)

    if gate_remap:
        level_rules = build_gate_rules(gate_remap)
    else:
        gate_remap = {g: GATE_VANILLA_SL[g] for g in GATE_VANILLA_SL}
        level_rules = _LEVEL_RULES

    preset_zeroed = lock_gates
    if preset_zeroed:
        for g in preset_zeroed:
            if g in gate_remap:
                gate_remap[g] = 0
        level_rules = build_gate_rules(gate_remap)

    if no_soul_gates:
        for g in gate_remap:
            gate_remap[g] = 0
        level_rules = build_gate_rules(gate_remap)

    if verbose:
        print("\n── GATE THRESHOLDS ──────────────────────────────────")
        for gate_id in sorted(gate_remap):
            sl = gate_remap[gate_id]
            souls = _SOUL_THRESHOLDS[sl]
            vanilla_sl = GATE_VANILLA_SL.get(gate_id, sl)
            changed = " ←" if sl != vanilla_sl else ""
            locked = " (locked)" if sl == 10 else " (open)" if sl == 0 else ""
            print(f"  {gate_id:<35} SL{sl:>2}  ({souls:>3} souls){locked}{changed}")
        print()

    active_fixed_soul_locs = apply_true_form_remap(true_form_loc_remap)

    # ── Step 2: Build candidate pool ──────────────────────────────────────────
    active_slot_cats = set(SLOT_ACCEPTS.keys())
    if not shuffle_gad_temples:
        active_slot_cats.discard("gad")

    if insanity >= 3:
        candidate_pool = [
            l for l in CHECKABLE_LOCS
            if l.loc_key not in EXCLUDED_LOCS
            and l.category not in {"enemy", "boss", "true_form", "scripted"}
        ]
    else:
        candidate_pool = [
            l for l in CHECKABLE_LOCS
            if l.category in active_slot_cats
            and (shuffle_gad_temples or l.category != "gad")
        ]
    if verbose:
        slot_cat_counts = Counter(loc.category for loc in candidate_pool)
        print(f"  Candidate pool: {len(candidate_pool)} total")
        for cat, count in sorted(slot_cat_counts.items()):
            print(f"    {cat:<15}: {count}")
        prog_wlb_total = sum(count for cat, count in slot_cat_counts.items()
                             if cat in PROG_SLOT_CATS)
        print(f"    {'─' * 15}")
        print(f"    prog/WLB total : {prog_wlb_total}")
    unplaced = {loc.loc_key for loc in candidate_pool}
    placement: dict[str, str] = {}

    # ── Step 3: Build item pool ────────────────────────────────────────────────
    item_pool = build_item_pool(
        [l for l in candidate_pool if l.category not in FILLER_SLOT_CATS],
        shuffle_weapons=shuffle_weapons,
        shuffle_lore=shuffle_lore,
        shuffle_bonus=shuffle_bonus,
    )
    rng.shuffle(item_pool)

    # ── Item placement order ───────────────────────────────────────────────────
    # Progression items placed before souls, WLB last.
    # This ensures key items claim their slots while the candidate pool is
    # widest, before souls consume reachable locations.
    def _placement_priority(item) -> float:
        if item.category in WLB_CATS:
            return 2.0
        if item.category == "soul":
            return rng.uniform(0, 2)
        # progression, retractor, accumulator, gad
        return rng.uniform(0.0, 1)

    item_pool.sort(key=_placement_priority)
    if verbose:
        item_cat_counts = Counter(item.category for item in item_pool)
        print(f"  Item pool: {len(item_pool)} total")
        for cat, count in sorted(item_cat_counts.items()):
            print(f"    {cat:<15}: {count}")

    # ── Step 4: Build item_category lookup ────────────────────────────────────
    # Built once here and passed to every simulate_playthrough call to avoid
    # rebuilding it ~130 times per seed.
    item_category = {
        p.object: p.category
        for p in build_item_pool(
            [l for l in candidate_pool if l.category not in FILLER_SLOT_CATS],
            shuffle_weapons=True, shuffle_lore=True, shuffle_bonus=False,
        )
    }

    # ── Step 5: Weighted slot choice ──────────────────────────────────────────
    exponent = progression_balancing / 50.0

    def _weighted_choice(candidates: list):
        if not candidates:
            return None
        if exponent == 0.0 or len(candidates) == 1:
            return rng.choice(candidates)

        def _weight(loc) -> float:
            if item.category == "soul":
                return 1.0

            # depth_score proxies how hard this slot is to reach.
            # For region-gated slots it mirrors the gate's SL value.
            # For locally-gated slots we floor it at a depth equivalent
            # so late-game conditions rank appropriately without stacking
            # on top of already-deep regions.
            gate = REGION_GATES.get(loc.level_region)
            if gate is None:
                depth = 0
            elif isinstance(gate, str):
                depth = gate_remap.get(gate, GATE_VANILLA_SL.get(gate, 0))
            else:
                depth = min(
                    max(gate_remap.get(g, GATE_VANILLA_SL.get(g, 0)) for g in route)
                    for route in gate
                )

            if loc.gate_expr:
                expr_l = loc.gate_expr.lower()

                # ── Hard floors — override base SL entirely ────────────────────────
                if "r.night(" in expr_l:
                    depth += 15 # all 3 eclipsers + eng_key + retractors
                elif loc.level_region in LIVESIDE_REGIONS:
                    depth += 5 # retractors + SL2 entry

                # ── Mid floors — only kick in when base SL is lower ────────────────
                if "r.calabash(" in expr_l:
                    depth += 7 # gates Queens engine block access
                if "r.marteau(" in expr_l:
                    depth += 6  # mid-late tool
                if "r.baton(" in expr_l:
                    depth += 6  # mid-late tool, asylum shortcut
                if "r.flambeau(" in expr_l:
                    depth += 5  # mid tool, gates Temple of Fire upper
                if "r.poigne(" in expr_l:
                    depth += 4  # late progression, gates Queens engine

                # ── Additive nudges — stack on top of base ─────────────────────────
                if "r.x3_accumulator(" in expr_l:
                    depth += 2  # Violator reward slot, mid-late sub-gate
                if "r.prison_key_card(" in expr_l:
                    depth += 1  # late progression, gates Prison engine
                if "r.eng_key(" in expr_l:
                    depth += 1  # mid-game sub-gate
                if "r.gad3_swim(" in expr_l:
                    depth += 5  # all three temples required
                elif "r.gad2_walk(" in expr_l:
                    depth += 3  # two temples required
                elif "r.gad1_hand(" in expr_l:
                    depth += 1  # one temple required

            return ((depth + 1) ** 2) ** exponent

        weights = [_weight(loc) for loc in candidates]
        total = sum(weights)
        r = rng.random() * total
        cumul = 0.0
        for loc, w in zip(candidates, weights):
            cumul += w
            if r <= cumul:
                return loc
        return candidates[-1]

    # ── Step 6: Placement sweep ────────────────────────────────────────────────
    # Soulswap fallback: when any non-soul item has no valid slot, swap it with
    # the next soul in the queue to grow the reachable set, then re-queue the
    # blocked item. ENG_KEY is also moved forward since it unblocks many slots.
    fallback_soul_swaps = fallback_no_slot = 0

    for i, item in enumerate(item_pool):

        reached_keys, st, _ = simulate_playthrough(
            placement,
            candidate_pool + active_fixed_soul_locs,
            level_rules,
            shuffle_gad_temples=shuffle_gad_temples,
            item_category=item_category,
        )

        def _slot_ok(loc) -> bool:
            if insanity >= 3:
                return True
            if insanity >= 2 and loc.category in {"soul", "cadeaux"}:
                return True
            if insanity >= 1 and loc.category == "soul":
                return True
            return item.category in SLOT_ACCEPTS.get(loc.category, frozenset())

        def _liveside_ok(loc) -> bool:
            if item.category == "retractor" and loc.level_region in LIVESIDE_REGIONS:
                return False
            if item.category == "soul" and loc.level_region in LIVESIDE_REGIONS:
                return R.night(st, PLAYER)
            return True

        def _candidates():
            return [
                loc for loc in candidate_pool
                if loc.loc_key in unplaced
                   and loc.loc_key in reached_keys
                   and not _gate_reserved(loc, st)
                   and _slot_ok(loc)
                   and _liveside_ok(loc)
            ]

        candidates = _candidates()

        # ── Soulswap ───────────────────────────────────────────────────────────
        if not candidates and item.category != "soul":
            for j in range(i + 1, len(item_pool)):
                if item_pool[j].category == "soul":
                    fallback_soul_swaps += 1
                    soul = item_pool.pop(j)
                    item_pool[i] = soul
                    item_pool.insert(i + 1, item)
                    item = soul
                    if verbose:
                        print(f"   ⚠️  No slot — placing soul, re-queuing {item_pool[i + 1].object!r}")
                    eng_key_pos = next(
                        (k for k in range(i + 2, len(item_pool))
                         if item_pool[k].object == "RSC_X_ENGINEERS_KEY"),
                        None
                    )
                    if eng_key_pos is not None and eng_key_pos > i + 2:
                        item_pool.insert(i + 2, item_pool.pop(eng_key_pos))
                        if verbose:
                            print(f"   🔑 ENG_KEY moved forward")
                    break
            candidates = _candidates()

        # ── No slot ────────────────────────────────────────────────────────────
        chosen = _weighted_choice(candidates)
        if chosen is None:
            fallback_no_slot += 1
            print(f"   ❌  No slot for {item.object!r} (cat={item.category}) "
                  f"soul_count={st.soul_count}")
            continue
        placement[chosen.loc_key] = item
        unplaced.discard(chosen.loc_key)

        if verbose:
            tag = "[S]" if item.category == "soul" else "[P]"
            print(f"{i + 1:3d}. {tag} {item.object:25} -> "
                  f"{chosen.level_region:25} | "
                  f"Req: {chosen.gate_raw or 'free':20} | "
                  f"Souls: {st.soul_count}")

    if verbose:
        print("── PLACEMENT COMPLETE ──\n")
        print(f"  Fallback soul swaps : {fallback_soul_swaps}")
        print(f"  No slot failures    : {fallback_no_slot}")

        loc_by_key = {loc.loc_key: loc for loc in candidate_pool}
        soul_regions = Counter(
            loc_by_key[loc_key].level_region
            for loc_key, item in placement.items()
            if item.category == "soul" and loc_key in loc_by_key
        )
        print("\n── SOUL DISTRIBUTION ──")
        for region, count in sorted(soul_regions.items(), key=lambda x: -x[1]):
            print(f"  {count:3d}  {region}")
        print(f"  {'─' * 3}")
        print(f"  {sum(soul_regions.values()):3d}  total")

    # ── Step 7: Filler ────────────────────────────────────────────────────────
    # All unfilled barrel and cadeaux slots receive a random filler item.
    # Soul slots are intentionally excluded — phase 1 places all soul items
    # into soul slots directly, so any soul slot not in placement was simply
    # not needed and should stay vanilla.
    # Both remaining_locs and filler_items draw from CHECKABLE_LOCS (not
    # candidate_pool) to ensure complete coverage of all levels including
    # liveside, which phase 1 may have excluded from its candidate set.

    include_cats = {"soul", "progression", "retractor", "accumulator", "gad"}
    if shuffle_weapons: include_cats.add("weapon")
    if shuffle_lore:    include_cats.add("lore")
    if shuffle_bonus:   include_cats.add("bonus")

    filler_slot_cats_remaining = (FILLER_SLOT_CATS | include_cats) if insanity >= 1 else (FILLER_SLOT_CATS | {"soul"})

    remaining_locs = [
        loc.loc_key for loc in CHECKABLE_LOCS
        if loc.loc_key not in placement
           and loc.category in filler_slot_cats_remaining
    ]
    rng.shuffle(remaining_locs)

    filler_items = [
        loc for loc in CHECKABLE_LOCS
        if loc.loc_key not in placement
        and loc.category in filler_slot_cats_remaining
    ]
    rng.shuffle(filler_items)

    if filler_items:
        for i, loc_key in enumerate(remaining_locs):
            placement[loc_key] = filler_items[i % len(filler_items)]
    else:
        print("  WARNING: no filler items available for remaining slots")

    return placement, gate_remap


# ── Validation ─────────────────────────────────────────────────────────────────

def validate_fill(
    placement: dict[str, str],
    verbose: bool = False,
    gate_remap: dict[str, int] | None = None,
    shuffle_gad_temples: bool = False,
    true_form_loc_remap: dict[str, str] | None = None,  # ← ADD
) -> tuple[bool, str]:

    placed_objects = [v.object if hasattr(v, "object") else v for v in placement.values()]
    for item_name in ALL_UNIQUES:
        count = placed_objects.count(item_name)
        if count > 1:
            print(f"⚠️ LOGIC WARNING: {item_name} placed {count} times!")

    active_fixed_soul_locs = apply_true_form_remap(true_form_loc_remap)
    reached_keys, final_state, _ = simulate_playthrough(
        placement,
        CHECKABLE_LOCS + active_fixed_soul_locs,
        level_rules=build_gate_rules(gate_remap),
        debug=verbose,
        shuffle_gad_temples=False,
    )

    _loc_by_key_all = {**LOCATION_TABLE, **{l.loc_key: l for l in active_fixed_soul_locs}}
    final_state.reached_regions = {
        _loc_by_key_all[k].level_region
        for k in reached_keys if k in _loc_by_key_all
    }

    goal_met = R.pistons(final_state, PLAYER)

    checkable_locs = [
        l for l in CHECKABLE_LOCS
        if (shuffle_gad_temples or l.category != "gad")
           and l.category not in FILLER_SLOT_CATS
    ]
    total_checkable = len(checkable_locs)
    checkable_keys = {l.loc_key for l in checkable_locs}
    reached_count = sum(1 for k in reached_keys if k in checkable_keys)

    ok = goal_met and reached_count == total_checkable
    lines = ["PASS" if ok else "FAIL"]

    if verbose or not ok:
        lines.append(f"  Locations: {reached_count}/{total_checkable}")
        lines.append(f"  Goal (PISTONS): {'OK' if goal_met else 'NO'}")

        unreached = [loc for loc in checkable_locs if loc.loc_key not in reached_keys]
        if unreached:
            lines.append(f"  Unreached ({len(unreached)}):")
            for loc in unreached[:15]:
                item_at = placement.get(loc.loc_key)
                item_str = item_at.object if hasattr(item_at, 'object') else '(empty)'
                lines.append(f"    [{loc.level_region}] {loc.gate_raw or 'free'} — {item_str}")
            if len(unreached) > 15:
                lines.append(f"    ... and {len(unreached) - 15} more")

        if not goal_met:
            lines.append("  Missing for PISTONS:")
            for label, met in [
                ("NIGHT (3 eclipsers)", R.night(final_state, PLAYER)),
                ("Poigne",              R.poigne(final_state, PLAYER)),
                ("Prison Key Card",     R.prison_key_card(final_state, PLAYER)),
                ("Gad Pickups",         final_state.count("RSC_X_GAD_PICKUP", PLAYER)),
                ("5x Retractor",        final_state.count("_retractors", PLAYER) >= 5),
                ("3x Accumulator",      final_state.count("RSC_X_ACCUMULATOR", PLAYER) >= 3),
                ("Total Souls",         final_state.soul_count),
            ]:
                status = "OK" if isinstance(met, bool) and met else (
                    "NO" if isinstance(met, bool) else met)
                lines.append(f"    {status}  {label}")

    return ok, "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, time

    parser = argparse.ArgumentParser()
    parser.add_argument("--seed",  type=int, default=None)
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--gate-preset",
                        choices=["story", "easy", "hard", "chaos"],
                        default=None,
                        help="Gate difficulty preset")
    parser.add_argument("--max-sl", type=int, default=None,
                        help="Cap the maximum SL any shuffled gate can receive (1-10)")
    parser.add_argument("--shuffle-gad-temples", action="store_true",
                        help="Shuffle gad powers as physical pickups")
    args = parser.parse_args()
    seeds = [args.seed] if args.seed is not None else [
        random.randint(0, 99_999_999) for _ in range(args.seeds)
    ]

    temp_pool = build_item_pool(CHECKABLE_LOCS)
    print("Shadow Man Remastered - Assumed Fill")
    print(f"AP locations : {len(AP_LOCATIONS)}  "
          f"(checkable: {len(CHECKABLE_LOCS)}, excluded: {len(EXCLUDED_LOCS)})")
    print(f"Item pool    : {len(temp_pool)}  |  Source: extracted_locations.py")
    if args.gate_preset:
        print(f"Gate preset  : {args.gate_preset}")
    print()

    passed = failed = chaos_failed = 0
    t0 = time.time()
    p = GATE_PRESETS.get(args.gate_preset, {})

    for seed in seeds:
        rng = random.Random(seed)
        placement, gate_remap = assumed_fill(
            rng,
            verbose=(args.verbose and len(seeds) == 1),
            shuffle_gates=p.get("shuffle_gates", False),
            no_soul_gates=p.get("no_soul_gates", False),
            lock_gates=p.get("lock_gates", frozenset()),
            max_sl=args.max_sl if args.max_sl is not None else p.get("max_sl"),
            safe=p.get("safe", True),
            shuffle_gad_temples=args.shuffle_gad_temples,
        )
        ok, report = validate_fill(
            placement,
            verbose=args.verbose,
            gate_remap=gate_remap,
            shuffle_gad_temples=args.shuffle_gad_temples,
        )

        if ok:
            passed += 1
            if args.verbose or len(seeds) == 1:
                changed = {g: sl for g, sl in gate_remap.items()
                           if sl != GATE_VANILLA_SL.get(g)}
                suffix = f" [gates: {changed}]" if changed else ""
                print(f"Seed {seed}{suffix}: {report}")
        elif args.gate_preset == "chaos":
            chaos_failed += 1
            print(f"Seed {seed}: FAIL (chaos — unbeatable seed expected occasionally)")
        else:
            failed += 1
            print(f"Seed {seed}: {report}")

        elapsed = time.time() - t0
        total = passed + failed + chaos_failed
        print("-" * 55)
        print(f"Results : {passed}/{total} passed  "
              f"({elapsed:.1f}s, {elapsed / len(seeds) * 1000:.0f}ms/seed)")
        if chaos_failed:
            print(f"  Chaos failures: {chaos_failed}/{total} (expected)")
        print("✅ All seeds beatable" if failed == 0 else f"❌ WARNING: {failed} unbeatable seed(s)")