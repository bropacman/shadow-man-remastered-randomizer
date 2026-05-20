"""
tools/test_fill.py
------------------
Stress-tests assumed_fill over N random seeds and reports:
  - Item placement frequency  -- which regions each key item lands in
  - Sphere depth stats        -- which sphere each key item becomes reachable
    (opt-in with --depth; adds one simulate_playthrough pass per seed)

Parallelises across all CPU cores automatically.
Each fill takes ~10-15 s, so 50 seeds on an 8-core machine finishes in ~1-2 min.

Usage examples
--------------
# 50 seeds, vanilla gates (default)
python tools/test_fill.py

# 100 seeds, medium preset, insanity 1, with depth tracking
python tools/test_fill.py --count 100 --gate-preset medium --insanity 1 --depth

# 200 seeds, chaos gates, max SL 6, 4 workers
python tools/test_fill.py --count 200 --gate-preset chaos --max-sl 6 --workers 4

# Start with Engineers Key, track sphere depths
python tools/test_fill.py --starting-item RSC_X_ENGINEERS_KEY --depth
"""
from __future__ import annotations

import argparse
import random
import sys
import time
from collections import Counter
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from constants import GATE_PRESETS

# RSC object name -> display label for items we track
PROG_ITEMS: dict[str, str] = {
    "RSC_X_ENGINEERS_KEY":   "Engineers Key",
    "RSC_X_POIGNE":          "Poigne",
    "RSC_X_BATON":           "Baton",
    "RSC_X_FLAMBEAU":        "Flambeau",
    "RSC_X_MARTEAU":         "Marteau",
    "RSC_X_CALABASH":        "Calabash",
    "RSC_X_ECLIPSER_PART1":  "Eclipser 1",
    "RSC_X_ECLIPSER_PART2":  "Eclipser 2",
    "RSC_X_ECLIPSER_PART3":  "Eclipser 3",
    "RSC_X_PRISON_KEY_CARD": "Prison Key Card",
    "RSC_X_ACCUMULATOR":     "Accumulator",
    "RSC_X_RETRACT":         "Retractor",
}


# ---------------------------------------------------------------------------
# Worker -- runs in a subprocess so each seed has its own clean module state
# ---------------------------------------------------------------------------

def _run_seed(args: tuple[int, dict[str, Any]]) -> dict[str, Any]:
    """Run assumed_fill (and optionally simulate_playthrough) for one seed."""
    seed, cfg = args

    import fill as _fill_module  # noqa: F401  (imported for side-effect / isolation)
    from fill import (
        assumed_fill, simulate_playthrough,
        build_gate_rules, CHECKABLE_LOCS, FIXED_SOUL_LOCS,
    )

    result: dict[str, Any] = {
        "seed":         seed,
        "ok":           False,
        "error":        None,
        "region_hits":  {k: [] for k in PROG_ITEMS},
        "sl_hits":      {k: [] for k in PROG_ITEMS},
        "sphere_hits":  {k: [] for k in PROG_ITEMS},
        "total_spheres": None,
        "soul_per_sphere": {},
    }

    loc_by_key = {loc.loc_key: loc for loc in CHECKABLE_LOCS}
    rng = random.Random(seed)

    true_form_loc_remap = None
    if cfg["shuffle_true_forms"]:
        try:
            from randomizers.enemy_randomizer import randomize_true_forms
            _, true_form_loc_remap = randomize_true_forms(rng)
        except Exception:
            pass

    entrance_shuffle = None
    if cfg["entrance_mode"] and cfg["entrance_mode"] != "off":
        from randomizers.entrance_randomizer import shuffle_unified
        entrance_shuffle = shuffle_unified(rng, mode=cfg["entrance_mode"])

    try:
        placement, gate_remap = assumed_fill(
            rng=rng,
            verbose=False,
            progression_balancing=cfg["progression_balancing"],
            shuffle_gates=cfg["shuffle_gates"],
            no_soul_gates=cfg["no_soul_gates"],
            open_gates_n=cfg["open_gates_n"],
            lock_gates=cfg["lock_gates"],
            max_sl=cfg["max_sl"],
            safe=cfg["safe"],
            insanity=cfg["insanity"],
            starting_item=cfg["starting_item"],
            true_form_loc_remap=true_form_loc_remap,
            entrance_shuffle=entrance_shuffle,
        )
    except Exception as exc:
        result["error"] = str(exc)
        return result

    result["ok"] = True

    from fill import REGION_GATES
    from access_rules import GATE_VANILLA_SL

    for slot_key, item_obj in placement.items():
        rsc = item_obj.object
        if rsc not in PROG_ITEMS:
            continue
        slot = loc_by_key.get(slot_key)
        if slot:
            result["region_hits"][rsc].append(slot.level_region)
            # SL bucket: how deeply gated is this slot?
            gate = REGION_GATES.get(slot.level_region)
            if gate is None:
                sl = 0
            elif isinstance(gate, str):
                sl = gate_remap.get(gate, GATE_VANILLA_SL.get(gate, 0))
            else:
                sl = min(
                    max(gate_remap.get(g, GATE_VANILLA_SL.get(g, 0)) for g in route)
                    for route in gate
                )
            result["sl_hits"][rsc].append(sl)

    if cfg["depth"]:
        try:
            if entrance_shuffle is not None:
                from fill import build_entrance_gate_rules
                level_rules = build_entrance_gate_rules(gate_remap, entrance_shuffle)
            else:
                level_rules = build_gate_rules(gate_remap)
            locations   = list(CHECKABLE_LOCS) + list(FIXED_SOUL_LOCS)
            _, _, spheres = simulate_playthrough(
                placement=placement,
                locations=locations,
                level_rules=level_rules,
                collect_spheres=True,
            )
            result["total_spheres"] = len(spheres)
            for s_data in spheres:
                gained = s_data["souls_end"] - s_data["souls_start"]
                if gained > 0:
                    n = s_data["sphere"]
                    result["soul_per_sphere"][n] = result["soul_per_sphere"].get(n, 0) + gained
                for (obj, _left, _friendly, _region) in s_data["items"]:
                    if obj in PROG_ITEMS:
                        result["sphere_hits"][obj].append(s_data["sphere"])
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _bar(fraction: float, width: int = 26) -> str:
    filled = round(fraction * width)
    return "#" * filled + "." * (width - filled)


def _pct(n: int, total: int) -> str:
    return f"{n / total * 100:5.1f}%" if total else "    -- "


def _median(lst: list[int]) -> float:
    if not lst:
        return 0.0
    s = sorted(lst)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Shadow Man Randomizer -- fill stress tester",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--count", type=int, default=50,
                        help="Number of seeds to run (default: 50)")
    parser.add_argument("--seed-start", type=int, default=0,
                        help="First seed value (default: 0)")
    parser.add_argument("--gate-preset",
                        choices=list(GATE_PRESETS) + ["none"], default="none",
                        help="Gate preset (default: none = vanilla gates, no shuffle)")
    parser.add_argument("--max-sl", type=int, default=None, metavar="N",
                        help="Override the preset's max SL cap (0-10)")
    parser.add_argument("--insanity", type=int, choices=[0, 1, 2, 3], default=0,
                        help="Insanity tier: 0=off 1=soul slots 2=+cadeaux 3=all")
    parser.add_argument("--starting-item", default=None, metavar="RSC_NAME",
                        help="Grant this item at run start (e.g. RSC_X_ENGINEERS_KEY)")
    parser.add_argument("--shuffle-true-forms", action="store_true",
                        help="Shuffle true-form enemy positions before each fill")
    parser.add_argument("--progression-balancing", type=int, default=50, metavar="N",
                        help="0-100, higher pushes key items deeper (default: 50)")
    parser.add_argument("--entrance-mode",
                        choices=["off", "deadside_only", "cross_hub"], default="off",
                        help="Entrance randomizer mode (default: off)")
    parser.add_argument("--depth", action="store_true",
                        help="Run simulate_playthrough too to collect sphere-depth stats")
    parser.add_argument("--show-regions", type=int, default=10, metavar="N",
                        help="Top N regions to show per item (default: 10)")
    parser.add_argument("--workers", type=int, default=None, metavar="N",
                        help="Parallel worker processes (default: all CPU cores)")
    args = parser.parse_args()

    preset        = GATE_PRESETS.get(args.gate_preset, {})
    shuffle_gates = preset.get("shuffle_gates", False)
    no_soul_gates = preset.get("no_soul_gates", False)
    open_gates_n  = preset.get("open_gates_n",  0)
    lock_gates    = preset.get("lock_gates",    frozenset())
    max_sl        = args.max_sl if args.max_sl is not None else preset.get("max_sl", None)
    safe          = preset.get("safe", True)

    cfg: dict[str, Any] = dict(
        entrance_mode=args.entrance_mode,
        shuffle_gates=shuffle_gates,
        no_soul_gates=no_soul_gates,
        open_gates_n=open_gates_n,
        lock_gates=lock_gates,
        max_sl=max_sl,
        safe=safe,
        insanity=args.insanity,
        starting_item=args.starting_item,
        shuffle_true_forms=args.shuffle_true_forms,
        progression_balancing=args.progression_balancing,
        depth=args.depth,
    )

    workers = min(args.workers or cpu_count(), 5)
    seeds   = list(range(args.seed_start, args.seed_start + args.count))
    W = 74

    print("=" * W)
    print("  Shadow Man Remastered -- Fill Stress Test")
    print("=" * W)
    print(f"  Seeds            : {seeds[0]} - {seeds[-1]}  ({len(seeds)} total)")
    sl_note = f"  (max-sl override -> {max_sl})" if args.max_sl is not None else ""
    print(f"  Gate preset      : {args.gate_preset}{sl_note}")
    open_note = f"  open_n={open_gates_n}" if open_gates_n else ""
    print(f"  Shuffle gates    : {shuffle_gates}{open_note}")
    print(f"  Max SL cap       : {max_sl if max_sl is not None else 'none'}")
    print(f"  Insanity         : {args.insanity}")
    print(f"  Starting item    : {args.starting_item or 'none'}")
    print(f"  True forms       : {args.shuffle_true_forms}")
    print(f"  Prog. balancing  : {args.progression_balancing}")
    print(f"  Entrance mode    : {args.entrance_mode}")
    print(f"  Depth tracking   : {args.depth}")
    print(f"  Workers          : {workers}")
    print("=" * W)

    region_freq:   dict[str, Counter]    = {k: Counter() for k in PROG_ITEMS}
    sl_freq:       dict[str, Counter]    = {k: Counter() for k in PROG_ITEMS}
    sphere_depths: dict[str, list[int]]  = {k: []        for k in PROG_ITEMS}
    # soul_sphere_gained[sphere_num] = list of souls gained in that sphere (one per seed)
    soul_sphere_gained: dict[int, list[int]] = {}
    total_spheres_list: list[int] = []
    failed_seeds:  list[int] = []
    done = 0

    print(f"\n  Running {len(seeds)} seeds across {workers} workers ...\n")
    t0 = time.perf_counter()

    with Pool(processes=workers) as pool:
        work = [(s, cfg) for s in seeds]
        for result in pool.imap_unordered(_run_seed, work, chunksize=1):
            done += 1
            seed = result["seed"]

            if not result["ok"]:
                failed_seeds.append(seed)
                print(f"  FAIL seed {seed}: {result['error']}")
                continue

            for rsc, regions in result["region_hits"].items():
                region_freq[rsc].update(regions)
            for rsc, buckets in result["sl_hits"].items():
                sl_freq[rsc].update(buckets)

            for rsc, hits in result["sphere_hits"].items():
                sphere_depths[rsc].extend(hits)

            if result["total_spheres"] is not None:
                total_spheres_list.append(result["total_spheres"])
            for snum, gained in result["soul_per_sphere"].items():
                soul_sphere_gained.setdefault(snum, []).append(gained)

            if done % 10 == 0 or done == len(seeds):
                elapsed = time.perf_counter() - t0
                rate    = done / elapsed
                eta     = (len(seeds) - done) / rate if rate > 0 else 0
                print(f"  ... {done:>4}/{len(seeds)}"
                      f"  ({elapsed:5.1f}s elapsed, ~{eta:.0f}s remaining)")

    elapsed_total = time.perf_counter() - t0
    successful    = len(seeds) - len(failed_seeds)

    print()
    print("=" * W)
    fail_note = f"  ({len(failed_seeds)} failed)" if failed_seeds else ""
    print(f"  Completed {successful}/{len(seeds)} seeds in {elapsed_total:.1f}s{fail_note}")
    if total_spheres_list:
        avg_s = sum(total_spheres_list) / len(total_spheres_list)
        print(f"  Avg spheres/seed : {avg_s:.1f}"
              f"  (min {min(total_spheres_list)}, max {max(total_spheres_list)})")
    print("=" * W)

    if args.depth:
        print()
        print("-" * W)
        print("  SPHERE DEPTH  (when each item type first becomes reachable)")
        print("-" * W)
        hdr = f"  {'Item':<21}  {'N':>5}  {'Avg':>5}  {'Med':>5}  {'Min':>4}  {'Max':>4}  Spread"
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for rsc, label in PROG_ITEMS.items():
            d = sphere_depths[rsc]
            if not d:
                print(f"  {label:<21}  no data")
                continue
            avg_d  = sum(d) / len(d)
            med_d  = _median(d)
            spread = max(d) - min(d)
            print(f"  {label:<21}  {len(d):>5}  {avg_d:>5.1f}  {med_d:>5.1f}"
                  f"  {min(d):>4}  {max(d):>4}  {spread:>6}")

        # Souls by sphere
        print()
        print("-" * W)
        print("  SOULS BY SPHERE  (avg souls gained per sphere across all seeds)")
        print("-" * W)
        max_sphere = max(soul_sphere_gained, default=0)
        avg_gained_vals = [
            sum(v) / len(v) for v in soul_sphere_gained.values() if v
        ]
        max_avg_gained = max(avg_gained_vals) if avg_gained_vals else 1.0
        BAR_W = 30
        cumul_avg = 0.0
        print(f"  {'Sph':>3}  {'Seeds':>5}  {'Avg Gained':>10}  {'Avg Cumul':>9}  Bar (each # ~ {max_avg_gained/BAR_W:.1f} souls)")
        print("  " + "-" * 70)
        for snum in range(1, max_sphere + 1):
            vals = soul_sphere_gained.get(snum, [])
            if not vals:
                continue
            avg_gained = sum(vals) / len(vals)
            cumul_avg += avg_gained
            bar_len = round(avg_gained / max_avg_gained * BAR_W)
            bar = "#" * bar_len
            print(f"  {snum:>3}  {len(vals):>5}  {avg_gained:>10.1f}  {cumul_avg:>9.1f}  {bar}")



    # SL gate-bucket distribution (always shown; entrance-agnostic)
    print()
    print("-" * W)
    note = "  (NOTE: reflects slot gate depth, not access order)" if args.entrance_mode != "off" else ""
    print(f"  SLOT GATE DEPTH (SL bucket each item landed in){note}")
    print("-" * W)
    SL_SOULS = {0:0, 1:1, 2:3, 3:7, 4:15, 5:23, 6:35, 7:51, 8:71, 9:95, 10:120}
    for rsc, label in PROG_ITEMS.items():
        ctr = sl_freq[rsc]
        if not ctr:
            continue
        total = sum(ctr.values())
        parts = []
        for sl in sorted(ctr):
            souls = SL_SOULS.get(sl, "?")
            locked = " (locked)" if sl == 10 else ""
            tag = f"SL{sl}/{souls}souls{locked}"
            count = ctr[sl]
            parts.append((tag, count, count / total))
        print(f"\n  -- {label}  ({total} placements)")
        for tag, count, frac in parts:
            print(f"    {tag:<22}  {count:4d}  {_pct(count, total)}  {_bar(frac)}")

    print()
    print("-" * W)
    region_note = "  (physical location -- may not reflect access order with entrance randomizer)" if args.entrance_mode != "off" else ""
    print(f"  PLACEMENT FREQUENCY  (top {args.show_regions} regions per item){region_note}")
    print("-" * W)

    for rsc, label in PROG_ITEMS.items():
        ctr = region_freq[rsc]
        if not ctr:
            print(f"\n  {label} -- never placed in any seed")
            continue

        total = sum(ctr.values())
        top   = ctr.most_common(args.show_regions)
        rest  = total - sum(v for _, v in top)

        divider = "-" * max(0, W - len(label) - 7)
        print(f"\n  -- {label}  ({total} placements) {divider}")
        for region, count in top:
            frac = count / total
            print(f"    {region:<38}  {count:4d}  {_pct(count, total)}  {_bar(frac)}")
        if rest > 0:
            other = "(other regions)"
            print(f"    {other:<38}  {rest:4d}  {_pct(rest, total)}")

    print()
    print("-" * W)
    elapsed_str = f"{elapsed_total:.1f}s total"
    print(f"  Done.  ({elapsed_str})")
    print("-" * W)


if __name__ == "__main__":
    main()
