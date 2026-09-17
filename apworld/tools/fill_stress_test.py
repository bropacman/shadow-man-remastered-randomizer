#!/usr/bin/env python3
"""
fill_stress_test.py
───────────────────
Stress-test AP generation for Shadow Man Remastered without running full
Generate.py. Sets up N solo/multiworld players with a given gate preset and
runs AP's real distribute_items_restrictive across many seeds, reporting
failures.

Run from the Archipelago root (or anywhere — it finds the root itself):

    python worlds/shadowman/tools/fill_stress_test.py --players 2 --preset hard --seeds 40
    python worlds/shadowman/tools/fill_stress_test.py --preset easy --seeds 100 --max-gate-sl 8

Baseline numbers measured 2026-07-15 (BEFORE the max_gate_sl + soul-subset
fix): hard solo ~65% OK, hard 2p ~92% OK, easy ~93%, story 100%.
AFTER the fix (shipped in options.py/__init__.py the same day):
story 100%, easy 100%, hard capped @SL8 ~90% solo / 100% 2p (small samples).
If numbers regress below these, a change broke fill behavior.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from argparse import Namespace  # noqa: E402


def setup(world_type, players, seed, overrides):
    from BaseClasses import CollectionState, MultiWorld
    from worlds.AutoWorld import call_all

    mw = MultiWorld(players)
    mw.game = {p: world_type.game for p in range(1, players + 1)}
    mw.player_name = {p: f"Tester{p}" for p in mw.player_ids}
    mw.set_seed(seed)
    args = Namespace()
    for p in range(1, players + 1):
        for key, option in world_type.options_dataclass.type_hints.items():
            val = overrides.get(key, option.default)
            uo = getattr(args, key, {})
            uo[p] = option.from_any(val)
            setattr(args, key, uo)
    mw.set_options(args)
    mw.state = CollectionState(mw)
    for step in ("generate_early", "create_regions", "create_items", "set_rules",
                 "connect_entrances", "generate_basic", "pre_fill"):
        call_all(mw, step)
    return mw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--players", type=int, default=1)
    ap.add_argument("--preset", default="hard",
                    choices=["story", "easy", "hard", "chaos"])
    ap.add_argument("--seeds", type=int, default=40)
    ap.add_argument("--start-seed", type=int, default=200)
    ap.add_argument("--max-gate-sl", type=int, default=None,
                    help="override the max_gate_sl option (default: option default)")
    a = ap.parse_args()

    from worlds.shadowman import ShadowManWorld
    from Fill import distribute_items_restrictive, FillError

    overrides = {"gate_preset": a.preset}
    if a.max_gate_sl is not None:
        overrides["max_gate_sl"] = a.max_gate_sl

    fails = []
    for seed in range(a.start_seed, a.start_seed + a.seeds):
        mw = setup(ShadowManWorld, a.players, seed, overrides)
        try:
            distribute_items_restrictive(mw)
        except FillError as e:
            fails.append(seed)
            print(f"  seed {seed} FAILED: {str(e).splitlines()[0]}")
    ok = a.seeds - len(fails)
    print(f"\n{a.preset} x{a.players}p: {ok}/{a.seeds} OK "
          f"({100 * ok / a.seeds:.0f}%)  failing seeds: {fails}")


if __name__ == "__main__":
    main()
