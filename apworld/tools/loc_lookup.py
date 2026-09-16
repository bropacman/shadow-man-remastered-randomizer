"""
loc_lookup.py
=============
Standalone lookup tool: resolves between the different ways a Shadow Man
AP location gets referred to across the pipeline -- friendly name,
loc_key (level:source_file:0xOFFSET), raw CSV row, AP location id,
save_idx, and level_region -- so you don't have to manually cross-
reference data/locations.csv / extracted_locations.py / a live client
log by hand every time.

Built 2026-08-03 after repeated back-and-forth trying to resolve things
like "asylum:quest.rsc:0x1932" or "ap_id=1000002076" or a raw CSV row
pasted from the sheet back to a friendly name and vice versa. AP's own
location ID (BASE_ID + idx in locations.py) depends on iteration order
over CHECKABLE_LOCS/FIXED_SOUL_LOCS, which isn't something you can
compute by hand from an offset alone -- this tool imports the REAL
fill.py/extracted_locations.py/access_rules.py from this world folder
(not a reimplementation) and replicates locations.py's exact
BASE_ID + idx assignment, so results always match what the actual
client/server assign. It deliberately avoids importing locations.py
itself (which needs BaseClasses.Location) or __init__.py (the full AP
world, needs the whole framework importable) -- fill.py/access_rules.py/
constants.py have zero BaseClasses dependency, confirmed by inspection,
so this only needs those three plus extracted_locations.py.

Usage:
    python loc_lookup.py asylum:quest.rsc:0x1932
    python loc_lookup.py --ap-id 1000002076
    python loc_lookup.py --csv-row "asylum,quest.rsc,0x0F12,228,cadeaux,RSC_X_BARREL_A,Cadeaux - Barrel,Asylum: Gateways,N,NULL,10,5627.9463,29.8248,13310.6953,0x0002,TRUE,TRUE,"
    python loc_lookup.py --name "Govi - Dark Soul 32"
    python loc_lookup.py --level asylum --offset 0x1932
    python loc_lookup.py --near asylum 4936.5 2.5 12930.5      (nearest by position)

Run from anywhere -- it locates this world's own folder relative to
itself, no need to be run from the Archipelago repo root.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import types
from pathlib import Path

SHADOWMAN_DIR = Path(__file__).resolve().parent.parent
FAKE_PKG = "_shadowman_lookup"


def _stub_base_classes():
    """
    fill.py's module-level `_LEVEL_RULES = build_gate_rules(...)` transitively
    imports regions.py, which needs BaseClasses.Region/MultiWorld/
    LocationProgressType -- not available outside a real Archipelago
    checkout with its dependencies importable. Only class *references* are
    needed to satisfy the import (regions.py subclasses/instantiates them
    inside function bodies we never call here, e.g. create_regions()) --
    plain stand-in classes are enough to let the real fill.py/regions.py/
    access_rules.py/locations logic import and run unmodified.
    """
    if "BaseClasses" in sys.modules:
        return
    bc = types.ModuleType("BaseClasses")

    class Region:
        def __init__(self, *a, **k):
            self.locations = []
            self.exits = []
            self.entrances = []

    class MultiWorld:
        pass

    class LocationProgressType:
        DEFAULT = 0
        PRIORITY = 1
        EXCLUDED = 2

    class Location:
        def __init__(self, player=None, name=None, code=None, parent=None):
            self.player = player
            self.name = name
            self.code = code
            self.parent_region = parent

    class Item:
        def __init__(self, *a, **k):
            pass

    class ItemClassification:
        progression = 1
        useful = 2
        filler = 4
        trap = 8

    class CollectionState:
        pass

    bc.Region = Region
    bc.MultiWorld = MultiWorld
    bc.LocationProgressType = LocationProgressType
    bc.Location = Location
    bc.Item = Item
    bc.ItemClassification = ItemClassification
    bc.CollectionState = CollectionState
    sys.modules["BaseClasses"] = bc


def _load_shadowman_modules():
    """
    Import fill.py / access_rules.py / constants.py / extracted_locations.py
    as submodules of a fake package pointed at this world's own folder --
    without ever executing the real worlds/shadowman/__init__.py (which
    needs the full AP framework importable). See module docstring.
    """
    _stub_base_classes()

    if FAKE_PKG not in sys.modules:
        pkg = types.ModuleType(FAKE_PKG)
        pkg.__path__ = [str(SHADOWMAN_DIR)]
        pkg.__package__ = FAKE_PKG
        sys.modules[FAKE_PKG] = pkg

    fill = importlib.import_module(f"{FAKE_PKG}.fill")
    return fill


def _build_location_table(fill):
    """Mirror locations.py's _build_location_table() exactly, without
    needing BaseClasses.Location at all -- we only need the ID<->loc_key
    mapping, not real Location objects."""
    BASE_ID = 1_000_001_000
    table = {}
    idx = 0
    for loc in fill.CHECKABLE_LOCS:
        table[loc.loc_key] = (BASE_ID + idx, loc)
        idx += 1
    for loc in fill.FIXED_SOUL_LOCS:
        if loc.loc_key in table:
            continue
        table[loc.loc_key] = (BASE_ID + idx, loc)
        idx += 1
    return table


def _build_friendly_names(table):
    """Mirror locations.py's _build_friendly_names() exactly."""
    from collections import defaultdict
    groups = defaultdict(list)
    for loc_key, (ap_id, raw) in table.items():
        base = raw.friendly_name or raw.object or raw.category or "Location"
        groups[(raw.level_region, base)].append(raw)
    names = {}
    for (level_region, base), raws in groups.items():
        if len(raws) == 1:
            names[raws[0].loc_key] = f"{level_region} - {base}"
            continue
        for i, raw in enumerate(
                sorted(raws, key=lambda r: (r.zone or "", r.loc_key)), start=1):
            names[raw.loc_key] = f"{level_region} - {base} {i}"
    return names


def _print_entry(loc_key, ap_id, raw, friendly):
    print(f"  loc_key:       {loc_key}")
    print(f"  ap_id:         {ap_id}")
    print(f"  friendly_name: {friendly}")
    print(f"  level_id:      {raw.level_id}")
    print(f"  source_file:   {raw.source_file}")
    print(f"  offset:        0x{raw.offset:04X}")
    print(f"  category:      {raw.category}")
    print(f"  object:        {raw.object}")
    print(f"  level_region:  {raw.level_region}")
    print(f"  save_idx:      {raw.save_idx}")
    print(f"  gate:          {raw.gate_raw!r}")
    print(f"  x, y, z:       {raw.x}, {raw.y}, {raw.z}")
    print(f"  is_verified:   {raw.is_verified}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("loc_key", nargs="?", help="level:source_file:0xOFFSET")
    ap.add_argument("--ap-id", type=int, help="AP location id, e.g. 1000002076")
    ap.add_argument("--csv-row", help="a raw data/locations.csv row (paste it verbatim)")
    ap.add_argument("--name", help="substring match against friendly names")
    ap.add_argument("--level", help="level_id, use with --offset")
    ap.add_argument("--offset", help="hex offset (0x....), use with --level")
    ap.add_argument("--near", nargs=4, metavar=("LEVEL", "X", "Y", "Z"),
                     help="find the nearest known location to a live x/y/z")
    args = ap.parse_args()

    fill = _load_shadowman_modules()
    table = _build_location_table(fill)
    friendly_names = _build_friendly_names(table)

    def show(loc_key):
        if loc_key not in table:
            print(f"'{loc_key}' is not a checkable AP location (not in "
                  f"CHECKABLE_LOCS/FIXED_SOUL_LOCS -- excluded, unverified, "
                  f"or a typo).")
            return
        ap_id, raw = table[loc_key]
        _print_entry(loc_key, ap_id, raw, friendly_names.get(loc_key, "?"))

    if args.loc_key:
        show(args.loc_key)
        return

    if args.ap_id is not None:
        match = next((lk for lk, (aid, _) in table.items() if aid == args.ap_id), None)
        if match is None:
            print(f"No location found with ap_id={args.ap_id}.")
        else:
            show(match)
        return

    if args.csv_row:
        parts = [p.strip() for p in args.csv_row.split(",")]
        level_id, source_file, offset = parts[0], parts[1], parts[2]
        loc_key = f"{level_id}:{source_file}:0x{int(offset, 16):04X}"
        show(loc_key)
        return

    if args.level and args.offset:
        loc_key = f"{args.level}:quest.rsc:0x{int(args.offset, 16):04X}"
        if loc_key not in table:
            # try pickups.rsc / instance.rsc too
            for sf in ("pickups.rsc", "instance.rsc"):
                alt = f"{args.level}:{sf}:0x{int(args.offset, 16):04X}"
                if alt in table:
                    loc_key = alt
                    break
        show(loc_key)
        return

    if args.name:
        needle = args.name.lower()
        hits = [(lk, name) for lk, name in friendly_names.items() if needle in name.lower()]
        if not hits:
            print(f"No friendly names matching '{args.name}'.")
        for lk, name in sorted(hits, key=lambda t: t[1]):
            ap_id, raw = table[lk]
            print(f"  {name}  ->  {lk}  (ap_id={ap_id})")
        return

    if args.near:
        level, x, y, z = args.near
        x, y, z = float(x), float(y), float(z)
        best = None
        for loc_key, (ap_id, raw) in table.items():
            if raw.level_id != level:
                continue
            if raw.x is None or raw.y is None or raw.z is None:
                continue
            d = ((x - raw.x) ** 2 + (z - raw.z) ** 2) ** 0.5
            if best is None or d < best[0]:
                best = (d, loc_key)
        if best is None:
            print(f"No known locations with position data for level '{level}'.")
        else:
            print(f"Nearest match: d_xz={best[0]:.1f}")
            show(best[1])
        return

    ap.print_help()


if __name__ == "__main__":
    main()
