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
  accumulator/gad/eclipser   → accept progression, retractor, accumulator, gad, eclipser

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
from constants import GATE_PRESETS, ITEM_GATE_IDS, COFFIN_GATE_ORDER
import regions as _regions

# ── Player constant ────────────────────────────────────────────────────────────

PLAYER = 1

# ── Starting items ─────────────────────────────────────────────────────────────

STARTING_ITEMS: set[str] = {
    "RSC_X_BOOK_OF_SHADOWS"
}

# ── Permanently excluded locations ────────────────────────────────────────────

EXCLUDED_LOCS: frozenset[str] = frozenset({
    # "deadside:quest.rsc:0x0102",  # SL10 + POIGNE - Book of Shadows
    # "ah4fogom:quest.rsc:0x2742",  # SL10 + CADEAUX_666 — Light Soul
    # "ah4fogom:quest.rsc:0x26B2",  # SL10 + CADEAUX_666 — Barrel
    # "ah4fogom:quest.rsc:0x26FA",  # SL10 + CADEAUX_666 — Barrel
})

# Level IDs that are cut/inaccessible and must be excluded entirely from the
# randomizer pool, regardless of how many entries are in the CSV for them.
EXCLUDED_LEVELS: frozenset[str] = frozenset({
    "t4ndgad",   # cut random bonus level temple t4
})

FIXED_SOUL_LOCS: list = [
    loc for loc in RAW_LOCATIONS
    if loc.category in ("boss", "true_form")
]

# Barrel and crate RSC names used when the cadeaux pool is exhausted and
# remaining slots need a plain (no-reward) container.  Weighted by vanilla
# frequency so the visual distribution stays roughly natural.
_PLAIN_BARREL_POOL: list[tuple[str, int]] = [
    ("RSC_X_BARREL_A",  1112),
    ("RSC_X_BARREL_D",   804),
    ("RSC_X_BARREL_L",   432),
    ("RSC_X_BARREL",     111),
    ("RSC_TE_PACKBOX1",  121),
    ("RSC_FL_CRATE",      62),
    ("RSC_UN_CRATES",     25),
    ("RSC_TE_PACKBOX2",   14),
]
_PLAIN_BARREL_NAMES:    list[str] = [name   for name, _   in _PLAIN_BARREL_POOL]
_PLAIN_BARREL_WEIGHTS:  list[int] = [weight for _,    weight in _PLAIN_BARREL_POOL]


def _plain_barrel(rng: random.Random):
    """Return a minimal source-loc sentinel for a plain barrel slot."""
    from types import SimpleNamespace
    name = rng.choices(_PLAIN_BARREL_NAMES, weights=_PLAIN_BARREL_WEIGHTS, k=1)[0]
    return SimpleNamespace(
        object=name,
        save_idx=0,
        friendly_name=name,
    )


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
    "weapon":      frozenset({"progression", "eclipser", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
    "lore":        frozenset({"progression", "eclipser", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
    "bonus":       frozenset({"progression", "eclipser", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
    "progression": frozenset({"progression", "eclipser", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
    "retractor":   frozenset({"progression", "eclipser", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
    "accumulator": frozenset({"progression", "eclipser", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
    "gad":         frozenset({"progression", "eclipser", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
    "eclipser":    frozenset({"progression", "eclipser", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}),
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

# Loc keys explicitly confirmed as phantom / invisible / unreachable in-game.
# Excluded from CHECKABLE_LOCS so nothing is ever placed there, and exported so
# patcher.py can suppress them from the coverage-validation error count.
UNVERIFIED_LOCS: frozenset[str] = frozenset(
    l.loc_key for l in AP_LOCATIONS
    if l.is_verified is False
)

CHECKABLE_LOCS: list = [
    l for l in AP_LOCATIONS
    if l.loc_key not in EXCLUDED_LOCS
    and _is_checkable(l)
    # Never place souls or key items in locations that haven't been verified as
    # real, visible, collectable spots in-game.  is_verified=None means the field
    # wasn't filled in yet and we give the benefit of the doubt; is_verified=False
    # means we explicitly confirmed the slot is phantom/invisible/unreachable and
    # it must be kept at its vanilla value.
    and l.loc_key not in UNVERIFIED_LOCS
]

# ── Gate region mapping ────────────────────────────────────────────────────────

LIVESIDE_REGIONS: frozenset[str] = frozenset({
    "Louisiana Swampland",
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
    # Liveside levels — only reachable through Asylum Cathedral of Pain.
    # Base depth = Asylum gate SL so nudges (+3 liveside, +4 night, +N gad)
    # stack on a real floor instead of zero.
    "Down Street Station, London":    "GATE_DEADSIDE_ASYLUM",
    "Gardelle County Jail, Texas":    "GATE_DEADSIDE_ASYLUM",
    "Salvage Yard, Mojave Desert":    "GATE_DEADSIDE_ASYLUM",
    "Mordant Street, Queens, NY":     "GATE_DEADSIDE_ASYLUM",
    "Summer Camp, Florida":           "GATE_DEADSIDE_ASYLUM",
    "Asylum: Engine Block - London":  "GATE_DEADSIDE_ASYLUM",
    "Asylum: Engine Block - Prison":  "GATE_DEADSIDE_ASYLUM",
    "Asylum: Engine Block - Florida": "GATE_DEADSIDE_ASYLUM",
    "Asylum: Engine Block - Salvage": "GATE_DEADSIDE_ASYLUM",
    "Asylum: Engine Block - Queens":  "GATE_DEADSIDE_ASYLUM",
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
    max_sl:  if set, clamps the pool to values <= max_sl.
    safe:    if True, enforces a hierarchy of gate caps before returning:
               1. Gates with fixed souls or named slots: SL <= 9
               2. Starting gates (WASTELAND, ASYLUM, PATH_3): SL <= 3
               3. WASTELAND: SL <= 2  (tighter — needs 7-soul start circuit)
               4. PATH_3: SL <= 5    (Temple of Fire must open mid-game)
             Caps are enforced in order so tighter constraints aren't undone.
    """
    starting_gates = {
        "GATE_DEADSIDE_WASTELAND",
        "GATE_DEADSIDE_ASYLUM",
        "GATE_DEADSIDE_PATH_3",
    }

    shuffleable = [g for g in GATE_VANILLA_SL if g not in locked]
    hi = max_sl if max_sl is not None else 10
    # Pool = linearly-spaced values from 1..hi, one per shuffleable gate.
    # Guarantees a uniform spread — no pile-up at the cap. SL0 is reserved
    # for open_gates_n; the shuffle itself never produces free gates.
    n = len(shuffleable)
    sl_pool = [round(1 + (hi - 1) * i / max(n - 1, 1)) for i in range(n)]
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
    # sorted() ensures deterministic constraint order regardless of PYTHONHASHSEED.
    for g in sorted(sl9_cap_gates):
        constraints.append((g, 9, sl9_cap_gates | starting_gates))

    # 2. Starting gates: SL3
    for g in sorted(starting_gates):
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

# ── Region depth probing ───────────────────────────────────────────────────────

# Items given to the probe FakeState so that the only thing limiting
# region access during depth-probing is soul count, not missing key items.
_PROBE_ITEMS: dict[str, int] = {
    "RSC_X_ENGINEERS_KEY":   1,
    "RSC_X_PRISON_KEY_CARD": 1,
    "RSC_X_CALABASH":        1,
    "RSC_X_BATON":           1,
    "RSC_X_FLAMBEAU":        1,
    "RSC_X_MARTEAU":         1,
    "RSC_X_POIGNE":          1,
    "RSC_X_ECLIPSER_PART1":  1,
    "RSC_X_ECLIPSER_PART2":  1,
    "RSC_X_ECLIPSER_PART3":  1,
    "RSC_X_ACCUMULATOR":     3,
    "RSC_X_GAD_PICKUP":      3,
}


def _build_region_depth_map(level_rules: dict) -> dict[str, int]:
    """
    Probe each entry in level_rules to find the minimum SL at which that
    region becomes reachable by a maximally-equipped player.

    Returns region_name -> SL int (0–10). Used by _weight() so that
    progression-balancing depth scores are correct in entrance-shuffle mode,
    where REGION_GATES no longer reflects actual access order.
    """
    all_regions = set(level_rules.keys())
    depth_map: dict[str, int] = {}
    for region, rule in level_rules.items():
        for sl in range(11):
            probe = FakeState(
                inv=_PROBE_ITEMS,
                soul_count=_SOUL_THRESHOLDS[sl],
                retractor_count=10,
                cadeaux_count=666,
                reached_regions=all_regions,
            )
            try:
                if rule(probe):
                    depth_map[region] = sl
                    break
            except Exception:
                depth_map[region] = 0
                break
        else:
            depth_map[region] = 10  # unreachable even at SL10 — treat as deepest
    return depth_map

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
    # BUG-FIX: the old guard was `if region_rule and not region_rule(state)`.
    # When region_rule is None (key missing from level_rules) the truthiness
    # check short-circuits to False, skipping the region gate entirely and
    # letting the location pass on gate_expr alone.  A missing key means the
    # region was never registered — treat it as unreachable, not as free.
    if region_rule is None or not region_rule(state):
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
    "r.cadeaux_666(":    ("cadeaux", 666),
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
    shuffle_retractors: bool = True,
    shuffle_accumulators: bool = True,
    shuffle_eclipsers: bool = True,
    shuffle_prisms: bool = False,
    shuffle_gad_temples: bool = False,
) -> list:
    """
    Build the item pool for placement. Always includes all logic-critical items
    (souls, progression, gad). Retractors, accumulators, and eclipsers are included
    by default but can be excluded when their shuffle flags are False (they then
    stay vanilla and are credited to the player via baseline counts in the fill).
    Weapons/lore/bonus/prisms included based on their respective shuffle flags.

    When shuffle_gad_temples is True, RSC_X_PROPHECY is excluded from the pool.
    The gad pickup patch repurposes that RSC type for the gad power visuals, and
    the asset overrides replace PROPHECY.PNG with gad-themed art — so the vanilla
    Book of Prophecy lore item would look like a 4th gad power pickup in-game.
    Its location slot remains in the candidate pool and receives a different item.
    """
    include_cats = {"soul", "progression", "gad"}
    if shuffle_retractors:   include_cats.add("retractor")
    if shuffle_accumulators: include_cats.add("accumulator")
    if shuffle_eclipsers:    include_cats.add("eclipser")
    if shuffle_weapons:      include_cats.add("weapon")
    if shuffle_lore:         include_cats.add("lore")
    if shuffle_bonus:        include_cats.add("bonus")
    if shuffle_prisms:       include_cats.add("prism")

    pool = [loc for loc in locations if loc.category in include_cats]

    # When gad temples are shuffled, RSC_X_PROPHECY is repurposed as the gad
    # power pickup model and PROPHECY.PNG is replaced with gad art. Keeping the
    # vanilla Book of Prophecy in the item pool would give the player a visually
    # identical 4th gad pickup. Remove it; its slot still receives another item.
    if shuffle_gad_temples:
        pool = [loc for loc in pool if loc.object != "RSC_X_PROPHECY"]

    # Uniqueness audit — skip items that are intentionally excluded from the pool
    # (e.g. eclipser parts when shuffle_eclipsers=False).
    intentionally_excluded: set[str] = set()
    if not shuffle_eclipsers:
        intentionally_excluded |= ECLIPSER_ITEMS
    non_soul_counts = Counter(loc.object for loc in pool if loc.category != "soul")
    for item in ALL_UNIQUES:
        if item in intentionally_excluded:
            continue
        count = non_soul_counts.get(item, 0)
        if count > 1:
            raise ValueError(f"DATA BUG: '{item}' exists {count} times in CSV!")
        if count == 0:
            print(f"Warning: '{item}' not found in pool.")

    soul_ids = [loc.save_idx for loc in pool if loc.category == "soul"]
    duped = [sid for sid, n in Counter(soul_ids).items() if n > 1]
    if duped:
        raise ValueError(f"DATA BUG: Duplicate soul save_idx values: {duped}")

    return pool


# ── Simulate playthrough ───────────────────────────────────────────────────────

def simulate_playthrough(
    placement, locations, level_rules,
    debug=False, collect_spheres=False, shuffle_gad_temples=False,
    item_category=None,
    baseline_retractor_count: int = 0,
    baseline_inv: dict | None = None,
):
    """
    Simulate a full playthrough of the given placement.

    item_category: pre-built {object: category} dict. Pass this in from
    assumed_fill to avoid rebuilding it on every iteration of the sweep (~130x).
    If None, it is built internally — used by validate_fill and patcher.

    baseline_retractor_count: retractors the player has from vanilla (unshuffled)
    positions. Used when shuffle_retractors=False so gate checks work correctly.
    baseline_inv: additional pre-credited items (e.g. accumulators, eclipser parts)
    for categories that are not being shuffled.
    """
    inv: dict[str, int] = dict(baseline_inv) if baseline_inv else {}
    soul_count = cadeaux_count = 0
    retractor_count = baseline_retractor_count
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
                elif category in ("progression", "eclipser", "retractor", "accumulator", "gad",
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
    open_gates_n: int = 0,
    lock_gates: frozenset[str] = frozenset(),
    max_sl: int | None = None,
    safe: bool = True,
    insanity: int = 0,
    shuffle_weapons: bool = True,
    shuffle_lore: bool = True,
    shuffle_bonus: bool = False,
    shuffle_retractors: bool = True,
    shuffle_accumulators: bool = True,
    shuffle_eclipsers: bool = True,
    shuffle_prisms: bool = False,
    shuffle_gad_temples: bool = False,
    starting_item: str | None = None,
    true_form_loc_remap: dict[str, str] | None = None,
    entrance_shuffle=None,   # UnifiedShuffle | None
) -> tuple[dict[str, str], dict[str, int]]:

    # ── Step 1: Gates ─────────────────────────────────────────────────────────
    # Add starting item to inventory so logic system knows player already has it
    if starting_item:
        STARTING_ITEMS.add(starting_item)

    if shuffle_gates:
        gate_remap = _shuffle_gates(rng, locked=lock_gates, max_sl=max_sl, safe=safe)
    if not gate_remap:
        gate_remap = {g: GATE_VANILLA_SL[g] for g in GATE_VANILLA_SL}

    if no_soul_gates:
        for g in gate_remap:
            gate_remap[g] = 0

    if open_gates_n is not None and open_gates_n > 0:
        remainder = [g for g in GATE_VANILLA_SL if g not in COFFIN_GATE_ORDER]
        rng.shuffle(remainder)
        ordered = list(COFFIN_GATE_ORDER) + remainder
        for g in ordered[:open_gates_n]:
            if g in gate_remap:
                gate_remap[g] = 0

    level_rules = _regions.build_level_rules(gate_remap, entrance_shuffle)

    region_depth_map = _build_region_depth_map(level_rules)

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

    # ── Baseline counts for unshuffled categories ─────────────────────────────
    # When a category is not shuffled, its items stay vanilla. The simulation
    # must credit the player with those items so gate checks work correctly.
    _bl_retractor = 0 if shuffle_retractors else 5
    _bl_inv: dict[str, int] = {}
    if not shuffle_accumulators:
        _bl_inv["RSC_X_ACCUMULATOR"] = 3
    if not shuffle_eclipsers:
        _bl_inv.update({
            "RSC_X_ECLIPSER_PART1": 1,
            "RSC_X_ECLIPSER_PART2": 1,
            "RSC_X_ECLIPSER_PART3": 1,
        })

    # ── Step 2: Build candidate pool ──────────────────────────────────────────
    active_slot_cats = set(SLOT_ACCEPTS.keys())
    if not shuffle_gad_temples:
        active_slot_cats.discard("gad")
    if not shuffle_retractors:
        active_slot_cats.discard("retractor")
    if not shuffle_accumulators:
        active_slot_cats.discard("accumulator")
    if not shuffle_eclipsers:
        active_slot_cats.discard("eclipser")
    if not shuffle_prisms:
        active_slot_cats.discard("prism")

    if insanity >= 3:
        candidate_pool = [
            l for l in CHECKABLE_LOCS
            if l.loc_key not in EXCLUDED_LOCS
            and l.level_id not in EXCLUDED_LEVELS
            and l.category not in {"enemy", "boss", "true_form", "scripted"}
            and not l.can_softlock
        ]
    else:
        candidate_pool = [
            l for l in CHECKABLE_LOCS
            if l.category in active_slot_cats
            and l.level_id not in EXCLUDED_LEVELS
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
        shuffle_retractors=shuffle_retractors,
        shuffle_accumulators=shuffle_accumulators,
        shuffle_gad_temples=shuffle_gad_temples,
        shuffle_eclipsers=shuffle_eclipsers,
        shuffle_prisms=shuffle_prisms,
    )
    rng.shuffle(item_pool)

    # ── Item placement order ───────────────────────────────────────────────────
    # Items are sorted by a random draw within their priority band (lower = placed first):
    #   progression, retractor, gad  ->  0.0 – 1.0   (key items first, widest pool)
    #   soul                          ->  0.2 – 1.0   (after prog, before weapons)
    #   weapon                        ->  0.5 – 2.0
    #   accumulator, bonus            ->  0.8 – 2.5
    #   lore                          ->  2.0 – 3.0   (last, filler)
    # Souls share the lower half of the band with progression so they claim
    # reachable slots before weapons crowd them out, reducing soulswap fallbacks.
    def _placement_priority(item) -> float:
        if item.category in {"soul"}:
            return rng.uniform(0.0, 1.5)
        if item.category in {"progression", "gad"}:
            return rng.uniform(0.1, 1.0)
        if item.category in {"eclipser", "retractor"}:
            return rng.uniform(0.2, 1.0)
        if item.category in {"weapon"}:
            return rng.uniform(0.4, 2.0)
        if item.category in {"accumulator", "bonus"}:
            return rng.uniform(0.4, 2.5)
        # lore
        return rng.uniform(2, 3.0)

    item_pool.sort(key=_placement_priority)

    # ── Remove starting item from pool if specified ────────────────────────
    if starting_item:
        for i, loc in enumerate(item_pool):
            if loc.object == starting_item:
                item_pool.pop(i)
                print(f"  Starting item: {starting_item} removed from pool")
                break
        else:
            print(f"  WARNING: starting item {starting_item!r} not found in pool")

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
            shuffle_retractors=shuffle_retractors,
            shuffle_accumulators=shuffle_accumulators,
            shuffle_eclipsers=shuffle_eclipsers,
            shuffle_prisms=shuffle_prisms,
            shuffle_gad_temples=shuffle_gad_temples,
        )
    }

    # ── Step 5: Weighted slot choice ──────────────────────────────────────────
    exponent = progression_balancing / 50.0

    def _level_soul_count(level_id: str) -> int:
        return sum(
            1 for k, v in placement.items()
            if k.split(":")[0] == level_id and hasattr(v, "category") and v.category == "soul"
        )

    def _level_key_count(level_id: str) -> int:
        return sum(
            1 for k, v in placement.items()
            if k.split(":")[0] == level_id and hasattr(v, "category") and v.category in {"progression", "eclipser", "retractor", "accumulator", "gad","weapon","lore","bonus"}
        )

    def _weighted_choice(candidates: list):
        if not candidates:
            return None
        if exponent == 0.0 or len(candidates) == 1:
            return rng.choice(candidates)

        def _weight(loc) -> float:
            # depth_score proxies how hard this slot is to reach.
            #
            # Region depth is bracketed into a small tier (0–4) so that item
            # gate requirements dominate the score — a shallow slot behind Night
            # should outweigh a deep slot with no gate (metroidvania feel).
            #
            # Tiers: SL 0 → 0, SL 1–4 → 1, SL 5–7 → 2, SL 8–9 → 3, SL 10 → 4
            sl = region_depth_map.get(loc.level_region, 0)
            if sl == 0:
                depth = 0
            elif sl <= 5:
                depth = 1
            elif sl <= 7:
                depth = 2
            elif sl <= 9:
                depth = 3
            else:
                depth = 4

            if loc.gate_expr:
                expr_l = loc.gate_expr.lower()

                # ── Hard floor ─────────────────────────────────────────────────
                if "r.x3_accumulator(" in expr_l:
                    depth = max(depth, 10)  # Violator reward slot

                # ── Additive nudges ────────────────────────────────────────────
                if "r.calabash(" in expr_l:
                    depth += 4  # gates Queens engine block access
                if "r.marteau(" in expr_l:
                    depth += 3  # mid-late tool
                if "r.baton(" in expr_l:
                    depth += 3  # mid-late tool, asylum shortcut
                if "r.flambeau(" in expr_l:
                    depth += 2  # mid tool, gates Temple of Fire upper
                if "r.poigne(" in expr_l:
                    depth += 2   # late progression, gates Queens engine

                if "r.night(" in expr_l:
                    depth += 3   # all 3 eclipsers + eng_key + retractors
                if "r.prison_key_card(" in expr_l:
                    depth += 0.5   # late progression, gates Prison engine
                if "r.eng_key(" in expr_l:
                    depth += 0.5   # mid-game sub-gate

                # Gad nudges only apply outside the temples themselves —
                # gad requirements in other levels are the interesting placements.
                if loc.level_region not in {
                    "Temple of Fire (Toucher)",
                    "Temple of Prophecy (Marcher)",
                    "Temple of Blood (Nager)",
                }:
                    if "r.gad3_swim(" in expr_l:
                        depth += 1.5   # all three temples required
                    elif "r.gad2_walk(" in expr_l:
                        depth += 1   # two temples required
                    elif "r.gad1_hand(" in expr_l:
                        depth += 0.5   # one temple required

            # ── gate_raw nudges (region-level soul gates) ─────────────────────
            # These are Deadside coffin gates and temple interior gates that
            # gate_expr alone doesn't capture — they're region transitions
            # requiring soul level, not item checks.  Boosts stack with any
            # item-gate nudges above for locs that require both.
            if loc.gate_raw:
                gate_r = loc.gate_raw.upper()
                if "INTERIOR" in gate_r:
                    depth += 2  # temple interior soul gate (Prophecy/Blood/Fogometers)
                if "MYSTERY" in gate_r:
                    depth += 2    # Deadside mystery gate — deepest area
                if "LALAME" in gate_r:
                    depth += 2    # La Lame eclipser soul gate (SL7 vanilla)
                if "LALUNE" in gate_r:
                    depth += 2  # La Lune eclipser soul gate (SL3 vanilla)
                if "FIRE_FLAMBEAU" in gate_r:
                    depth += 2  # Temple of Fire upper gate (SL5)
                if "FIRE_POIGNE" in gate_r:
                    depth += 2    # Temple of Fire lower gate (SL4)
                if "ENSEIGNE" in gate_r:
                    depth += 2    # Wasteland enseigne interior gate (SL6)

            # ── Govi slot nudge ───────────────────────────────────────────────
            # Govi slots are physically harder to reach in-level than open
            # cadeaux/barrel slots, so bias key items and souls toward them.
            if loc.object == "RSC_X_GOVI":
                depth += 2

            if item.category == "soul":
                already = _level_soul_count(loc.level_id)
                effective_depth = max(depth, 0.5)
                depth = effective_depth / (1 + already * 0.8)
                # Deadside Marrow Gates is hub world — less interesting for
                # soul placement than the spokes.  Pull its weight down.
                if loc.level_id == "deadside":
                    depth *= 0.4
            elif item.category in {"progression", "eclipser", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}:
                already = _level_key_count(loc.level_id)
                effective_depth = max(depth, 0.5)
                depth = effective_depth / (1 + already * 0.5)

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
            baseline_retractor_count=_bl_retractor,
            baseline_inv=_bl_inv or None,
        )

        def _slot_ok(loc) -> bool:
            if loc.can_softlock:
                return False
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

    include_cats = {"soul", "progression", "gad"}
    if shuffle_retractors:   include_cats.add("retractor")
    if shuffle_accumulators: include_cats.add("accumulator")
    if shuffle_eclipsers:    include_cats.add("eclipser")
    if shuffle_weapons:      include_cats.add("weapon")
    if shuffle_lore:         include_cats.add("lore")
    if shuffle_bonus:        include_cats.add("bonus")
    if shuffle_prisms:       include_cats.add("prism")

    # Items to place — all cadeaux + any barrels whose slot is still unfilled.
    # Cadeaux are always included regardless of whether their slot was claimed
    # in phase 1 (e.g. by a soul): a displaced cadeaux item still needs to be
    # placed somewhere so the total $cadeaux count stays at 666.  Displaced
    # cadeaux spill into barrel slots that would otherwise receive plain barrels.
    filler_items = [
        loc for loc in CHECKABLE_LOCS
        if loc.category == "cadeaux"                                    # always keep every cadeaux item
        or (loc.category in FILLER_SLOT_CATS and loc.loc_key not in placement)  # barrels only if unfilled
    ]
    rng.shuffle(filler_items)

    # Slots to fill — unfilled soul slots + unfilled barrel/cadeaux slots
    # (key item slots left empty by insanity get filled too).
    # Gad slots are excluded when shuffle_gad_temples is off: they're not in
    # candidate_pool and not filler-eligible, so including them causes
    # i % len(filler_items) to wrap and duplicate cadeaux RSCs in placement,
    # inflating the levels.txt $cadeaux count by one per gad slot (3 total).
    remaining_locs = [
        loc.loc_key for loc in CHECKABLE_LOCS
        if loc.loc_key not in placement
        and loc.level_id not in EXCLUDED_LEVELS
        and (shuffle_gad_temples  or loc.category != "gad")
        and (shuffle_retractors   or loc.category != "retractor")
        and (shuffle_accumulators or loc.category != "accumulator")
        and (shuffle_eclipsers    or loc.category != "eclipser")
        and (shuffle_prisms       or loc.category != "prism")
    ]
    rng.shuffle(remaining_locs)

    # Place filler 1-for-1 — no wrap.  Once the cadeaux pool is exhausted,
    # remaining slots receive a plain barrel (save_idx=0) drawn from the
    # vanilla barrel RSC pool so we never create more cadeaux than exist in
    # vanilla and never duplicate instance IDs.
    for i, loc_key in enumerate(remaining_locs):
        if i < len(filler_items):
            placement[loc_key] = filler_items[i]
        else:
            placement[loc_key] = _plain_barrel(rng)

    # Clean up starting item from STARTING_ITEMS so it doesn't persist between calls
    if starting_item:
        STARTING_ITEMS.discard(starting_item)

    return placement, gate_remap

# ── Validation ─────────────────────────────────────────────────────────────────

def validate_fill(
    placement: dict[str, str],
    verbose: bool = False,
    gate_remap: dict[str, int] | None = None,
    shuffle_gad_temples: bool = False,
    shuffle_retractors: bool = True,
    shuffle_accumulators: bool = True,
    shuffle_eclipsers: bool = True,
    starting_item: str | None = None,
    true_form_loc_remap: dict[str, str] | None = None,
    entrance_shuffle=None,
) -> tuple[bool, str]:

    if starting_item:
        STARTING_ITEMS.add(starting_item)

    placed_objects = [v.object if hasattr(v, "object") else v for v in placement.values()]
    for item_name in ALL_UNIQUES:
        if not shuffle_eclipsers and item_name in ECLIPSER_ITEMS:
            continue  # eclipsers intentionally stay vanilla
        count = placed_objects.count(item_name)
        if count > 1:
            print(f"⚠️ LOGIC WARNING: {item_name} placed {count} times!")

    _bl_retractor = 0 if shuffle_retractors else 5
    _bl_inv: dict[str, int] = {}
    if not shuffle_accumulators:
        _bl_inv["RSC_X_ACCUMULATOR"] = 3
    if not shuffle_eclipsers:
        _bl_inv.update({
            "RSC_X_ECLIPSER_PART1": 1,
            "RSC_X_ECLIPSER_PART2": 1,
            "RSC_X_ECLIPSER_PART3": 1,
        })

    active_fixed_soul_locs = apply_true_form_remap(true_form_loc_remap)
    reached_keys, final_state, _ = simulate_playthrough(
        placement,
        CHECKABLE_LOCS + active_fixed_soul_locs,
        level_rules=_regions.build_level_rules(gate_remap, entrance_shuffle),
        debug=verbose,
        shuffle_gad_temples=False,
        baseline_retractor_count=_bl_retractor,
        baseline_inv=_bl_inv or None,
    )

    _loc_by_key_all = {**LOCATION_TABLE, **{l.loc_key: l for l in active_fixed_soul_locs}}
    final_state.reached_regions = {
        _loc_by_key_all[k].level_region
        for k in reached_keys if k in _loc_by_key_all
    }

    goal_met = R.pistons(final_state, PLAYER)

    checkable_locs = [
        l for l in CHECKABLE_LOCS
        if (shuffle_gad_temples  or l.category != "gad")
           and (shuffle_retractors   or l.category != "retractor")
           and (shuffle_accumulators or l.category != "accumulator")
           and (shuffle_eclipsers    or l.category != "eclipser")
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

    if starting_item:
        STARTING_ITEMS.discard(starting_item)

    return ok, "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, time

    parser = argparse.ArgumentParser()
    parser.add_argument("--seed",  type=int, default=None)
    parser.add_argument("--seeds", type=int, default=100)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--gate-preset",
                        choices=["open", "easy", "medium", "hard", "chaos"],
                        default=None)
    parser.add_argument("--max-sl", type=int, default=None,
                        help="Cap the maximum SL any shuffled gate can receive (1-10)")
    parser.add_argument("--shuffle-gad-temples", action="store_true",
                        help="Shuffle gad powers as physical pickups")
    parser.add_argument("--starting-item", default=None,
                        help="RSC name of item to place at swamp church start location")
    parser.add_argument("--insanity", nargs="?", const=3, type=int, default=0,
                        help="Insanity tier 1-3. Bare --insanity = tier 3.")
    parser.add_argument("--entrance-mode", default=None,
                        choices=["deadside_only", "cross_hub"],
                        help="Enable entrance randomization mode")
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
    if args.entrance_mode:
        print(f"Entrance mode: {args.entrance_mode}")
    print()

    passed = failed = chaos_failed = 0
    t0 = time.time()
    p = GATE_PRESETS.get(args.gate_preset, {})

    for seed in seeds:
        rng = random.Random(seed)
        entrance_shuffle = None
        if args.entrance_mode:
            from randomizers.entrance_randomizer import shuffle_unified, unified_spoiler_section
            entrance_shuffle = shuffle_unified(random.Random(seed ^ 0xE117), mode=args.entrance_mode, shuffle_gad_temples=args.shuffle_gad_temples)
            if args.verbose or len(seeds) == 1:
                print()
                print(unified_spoiler_section(entrance_shuffle))
                print()
        placement, gate_remap = assumed_fill(
            rng,
            verbose=(args.verbose and len(seeds) == 1),
            shuffle_gates=p.get("shuffle_gates", False),
            no_soul_gates=p.get("no_soul_gates", False),
            lock_gates=p.get("lock_gates", frozenset()),
            max_sl=args.max_sl if args.max_sl is not None else p.get("max_sl"),
            safe=p.get("safe", True),
            shuffle_gad_temples=args.shuffle_gad_temples,
            starting_item=args.starting_item,
            insanity=args.insanity or 0,
            entrance_shuffle=entrance_shuffle,
        )
        ok, report = validate_fill(
            placement,
            verbose=args.verbose,
            gate_remap=gate_remap,
            shuffle_gad_temples=args.shuffle_gad_temples,
            starting_item=args.starting_item,
            entrance_shuffle=entrance_shuffle,
        )

        if ok:
            passed += 1
            if args.verbose or len(seeds) == 1:
                changed = {g: sl for g, sl in gate_remap.items()
                           if sl != GATE_VANILLA_SL.get(g)}
                suffix = f" [gates: {changed}]" if changed else ""
                print(f"Seed {seed}{suffix}: {report}")

            if args.verbose or len(seeds) == 1:
                from collections import defaultdict

                soul_dist = defaultdict(int)
                key_dist = defaultdict(int)
                for loc_key, item in placement.items():
                    level = loc_key.split(":")[0]
                    cat = item.category if hasattr(item, "category") else "filler"
                    if cat == "soul":
                        soul_dist[level] += 1
                    elif cat in {"progression", "eclipser", "retractor", "accumulator", "gad", "weapon", "lore", "bonus"}:
                        key_dist[level] += 1
                print("\n── SOUL / KEY DISTRIBUTION BY LEVEL ───────────────────")
                all_levels = sorted(soul_dist.keys() | key_dist.keys())
                for level in all_levels:
                    print(f"  {level:<20}  souls: {soul_dist[level]:>3}  keys: {key_dist[level]:>3}")
                print()
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
