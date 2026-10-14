"""
tools/ci_fill_check.py
----------------------
Fast pass/fail smoke test for the standalone fill, used by CI.

Runs assumed_fill() + validate_fill() on a FIXED list of seeds across the
gate presets that must always produce a beatable seed (vanilla, easy,
medium, hard), plus regression seeds for bugs that have bitten before.
Exits 1 if any seed is unbeatable. Fixed seeds keep failures reproducible:
re-run one locally with

    python fill.py --seed <seed> [--gate-preset <preset>] -v

Chaos is deliberately not here: its gate shuffle can be unbeatable by
design, and patcher.run_assumed_fill() re-rolls the gates until it isn't.

For wider, slower statistics use tools/test_fill.py instead.

Usage:
    python tools/ci_fill_check.py              # all cases, all cores
    python tools/ci_fill_check.py --workers 4
"""
from __future__ import annotations

import argparse
import contextlib
import io
import random
import sys
import time
from multiprocessing import Pool, cpu_count
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (preset or None for vanilla gates, seed, why it's here)
REGRESSION_CASES: list[tuple[str | None, int, str]] = [
    (None,     78607896, "Calabash no-slot: neutral items took the last free slots"),
    ("medium", 31221875, "ENG_KEY no-slot: needed a logic-item displacement"),
    ("medium", 30751022, "retractor/ENG_KEY no-slot chain, 4 displacements"),
]

SEEDS_PER_PRESET = 4
PRESETS: list[str | None] = [None, "easy", "medium", "hard"]


def _cases() -> list[tuple[str | None, int, str]]:
    # Deterministic "random" seeds, so the list is stable between runs
    # but isn't hand-picked to pass.
    r = random.Random(0x5EED)
    cases = list(REGRESSION_CASES)
    for preset in PRESETS:
        for _ in range(SEEDS_PER_PRESET):
            cases.append((preset, r.randint(0, 99_999_999), ""))
    return cases


def _run(case: tuple[str | None, int, str]) -> tuple[str | None, int, bool, str, float]:
    preset, seed, _why = case
    sys.path.insert(0, str(ROOT))
    from fill import assumed_fill, validate_fill
    from constants import GATE_PRESETS

    p = GATE_PRESETS.get(preset, {}) if preset else {}
    buf = io.StringIO()
    t0 = time.perf_counter()
    try:
        with contextlib.redirect_stdout(buf):
            placement, gate_remap, retractor_level_assignment = assumed_fill(
                random.Random(seed),
                shuffle_gates=p.get("shuffle_gates", False),
                no_soul_gates=p.get("no_soul_gates", False),
                open_gates_n=p.get("open_gates_n", 0),
                lock_gates=p.get("lock_gates", frozenset()),
                max_sl=p.get("max_sl"),
                safe=p.get("safe", True),
            )
            ok, report = validate_fill(
                placement,
                gate_remap=gate_remap,
                retractor_level_assignment=retractor_level_assignment,
            )
    except Exception as exc:  # a crash is a failure too, not a CI error
        ok, report = False, f"{type(exc).__name__}: {exc}"
    no_slot = [l.strip() for l in buf.getvalue().splitlines() if "No slot for" in l]
    if not ok and no_slot:
        report += "\n  " + "\n  ".join(no_slot[:5])
    return preset, seed, ok, report, time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args()

    cases = _cases()
    workers = args.workers or cpu_count()
    print(f"Fill check: {len(cases)} seeds, {workers} workers")
    t0 = time.perf_counter()
    failures = []
    with Pool(workers) as pool:
        for preset, seed, ok, report, secs in pool.imap_unordered(_run, cases):
            label = f"{preset or 'vanilla':<7} seed {seed:>8}"
            print(f"  {'ok  ' if ok else 'FAIL'} {label}  ({secs:.0f}s)", flush=True)
            if not ok:
                failures.append((label, report))

    print(f"\n{len(cases) - len(failures)}/{len(cases)} passed "
          f"in {time.perf_counter() - t0:.0f}s")
    for label, report in failures:
        print(f"\nFAIL {label}\n{report}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
