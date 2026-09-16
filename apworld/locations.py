"""
locations.py
────────────
Location definitions and ID table for Shadow Man Remastered.

ID range: 1_000_001_000 – 1_000_099_999

Every entry in CHECKABLE_LOCS becomes an AP location.
FIXED_SOUL_LOCS (boss/true_form) also become locations but are
pre-filled in pre_fill() — they still need IDs so AP can track them.
"""

from __future__ import annotations
from dataclasses import dataclass
from BaseClasses import Location

from .fill import CHECKABLE_LOCS, FIXED_SOUL_LOCS


BASE_ID = 1_000_001_000


@dataclass(frozen=True)
class LocationData:
    code:        int
    region_name: str
    raw:         object   # the RawLocation — kept for patcher access


class ShadowManLocation(Location):
    game = "Shadow Man Remastered"

    def __init__(self, player: int, name: str, raw, parent):
        # `name` is now the friendly display name (see FRIENDLY_NAMES /
        # _build_friendly_names below) -- NOT the technical loc_key, so the
        # code lookup must go through raw.loc_key instead of `name`.
        # code is None for event locations — all ours have codes
        data = location_table.get(raw.loc_key)
        super().__init__(player, name, data.code if data else None, parent)
        self.raw = raw


# ── Build location_table ──────────────────────────────────────────────────────
#
# Checkable locs first (these participate in fill), then fixed soul locs
# (pre-filled, but still registered so get_location() works).
#
# loc_key is the stable unique identifier used as the AP location name.
# Format: "{level_id}:{source_file}:0x{offset:04X}"

# This table is the static, full AP location-ID registry — every cadeaux
# row gets an ID here regardless of the Cadeauxsanity option, same as
# every other category.
#
# "barrel" ALSO reserves an ID here now (2026-08-01, Secret Trap barrel
# promotion — was unconditionally excluded before this). Mirrors cadeaux
# exactly: reserving the ID doesn't mean every barrel row becomes a real
# location — regions.py's create_regions()/_build_sub_regions() only
# actually connects the specific loc_keys in a given world's
# barrel_promoted_locs (see __init__.py's generate_early()), same
# per-seed-decision pattern as cadeaux's own _SKIP_CATS/cadeauxsanity gate.
#
# Whether a given world INSTANCE actually turns a cadeaux-category ID into
# a real, reachable Location is a separate, per-seed decision made in
# regions.py's create_regions()/_build_sub_regions() (its own _SKIP_CATS,
# conditioned on the cadeauxsanity option — see that file's comment). Keeping
# the ID reserved here either way matches how AP's datapackage generally
# works: a fixed superset of possible location IDs, with each generated
# seed only instantiating the subset it actually uses.
_SKIP_CATS: set[str] = set()

def _build_location_table() -> dict[str, LocationData]:
    table: dict[str, LocationData] = {}
    idx = 0

    for loc in CHECKABLE_LOCS:
        if loc.category in _SKIP_CATS:
            continue
        table[loc.loc_key] = LocationData(
            code        = BASE_ID + idx,
            region_name = loc.level_region,
            raw         = loc,
        )
        idx += 1

    for loc in FIXED_SOUL_LOCS:
        if loc.loc_key in table:
            continue
        table[loc.loc_key] = LocationData(
            code        = BASE_ID + idx,
            region_name = loc.level_region,
            raw         = loc,
        )
        idx += 1

    return table


location_table: dict[str, LocationData] = _build_location_table()


# ── Friendly display names ──────────────────────────────────────────────────
#
# AP's Location.name IS the string shown in spoiler logs, hints, and any
# other AP-core-generated output (BaseClasses.py's Location.__repr__ just
# returns self.name -- there's no separate "display name" hook). Historically
# this world used the technical loc_key ("ah3lavad:quest.rsc:0x0462") as
# Location.name directly, which meant spoiler logs showed technical strings
# instead of readable ones (unlike most other AP worlds -- e.g. OOT's
# HintList.py hardcodes literal names like "KF Shop Item 1").
#
# loc_key remains the ONLY key used for internal patcher/client lookups
# (location_table is still keyed by it, raw.loc_key is still how every
# other module identifies a location) -- this table only supplies what gets
# displayed. Every consumer that used to treat location.name as the
# technical key must go through location.raw.loc_key instead now.
#
# Friendly names alone aren't unique (553 locations are literally all named
# "Cadeaux" per data/locations.csv) -- AP requires globally-unique location
# names per player, so duplicates within the same level_region get a running
# counter suffix, matching the convention other AP worlds use for repeated
# location types (e.g. OOT/MM's "Shop Item 1", "Shop Item 2", ...).

def _build_friendly_names() -> dict[str, str]:
    from collections import defaultdict

    groups: dict[tuple[str, str], list] = defaultdict(list)
    for loc_data in location_table.values():
        raw = loc_data.raw
        base = raw.friendly_name or raw.object or raw.category or "Location"
        groups[(raw.level_region, base)].append(raw)

    names: dict[str, str] = {}
    for (level_region, base), raws in groups.items():
        if len(raws) == 1:
            names[raws[0].loc_key] = f"{level_region} - {base}"
            continue
        # Stable, deterministic order for numbering: by zone then loc_key
        # (loc_key sorts by file offset, i.e. definition order in quest.rsc).
        for i, raw in enumerate(
                sorted(raws, key=lambda r: (r.zone or "", r.loc_key)), start=1):
            names[raw.loc_key] = f"{level_region} - {base} {i}"

    return names


FRIENDLY_NAMES: dict[str, str] = _build_friendly_names()
