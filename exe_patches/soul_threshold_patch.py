"""
soul_threshold_patch.py
=======================
Patches the soul threshold values (SL1–SL10) in thoth_x64.exe.

Each Soul Level gate requires a minimum number of accumulated dark souls.
Vanilla values:
    SL1=1, SL2=3, SL3=7, SL4=15, SL5=23,
    SL6=35, SL7=51, SL8=71, SL9=95, SL10=120

Each patch site is a CMP R10D, imm8 instruction in thoth_x64.exe.
The imm8 byte is written directly (single byte, unsigned, max 255).
Values must be strictly ascending; the final value is always 120 (max souls in game).

Usage pattern:
    thresholds = randomize_soul_thresholds(rng)          # early, before spoiler log
    apply_soul_threshold_patch(exe_path, thresholds)     # Step 7, with other EXE patches
"""

import random
from pathlib import Path

# ── Patch addresses (file offsets into thoth_x64.exe) ────────────────────────
SOUL_THRESHOLD_PATCH_ADDRS = [
    0x2df116,  # SL1  — CMP R10D, imm8  (vanilla: 1)
    0x2df122,  # SL2  — CMP R10D, imm8  (vanilla: 3)
    0x2df12e,  # SL3  — CMP R10D, imm8  (vanilla: 7)
    0x2df13a,  # SL4  — CMP R10D, imm8  (vanilla: 15)
    0x2df146,  # SL5  — CMP R10D, imm8  (vanilla: 23)
    0x2df156,  # SL6  — CMP R10D, imm8  (vanilla: 35)
    0x2df162,  # SL7  — CMP R10D, imm8  (vanilla: 51)
    0x2df16e,  # SL8  — CMP R10D, imm8  (vanilla: 71)
    0x2df17a,  # SL9  — CMP R10D, imm8  (vanilla: 95)
    0x2df187,  # SL10 — CMP R10D, imm8  (vanilla: 120)
]

VANILLA_SOUL_THRESHOLDS = {
    0:  0,
    1:  1,
    2:  3,
    3:  7,
    4:  15,
    5:  23,
    6:  35,
    7:  51,
    8:  71,
    9:  95,
    10: 120,
}

_MAX_SOULS = 120
_LEVELS    = 10

SOUL_THRESHOLD_MODES = ("progressive", "balanced", "random")


def _make_ascending(values: list, count: int, max_souls: int) -> list:
    """
    Force a list of ints to be strictly ascending within [1, max_souls-1].
    Forward pass raises duplicates/inversions; backward pass reins in any
    values that were pushed above the ceiling.
    """
    result = [max(1, min(max_souls - 1, v)) for v in values]
    result.sort()
    for i in range(1, len(result)):
        if result[i] <= result[i - 1]:
            result[i] = result[i - 1] + 1
    for i in range(len(result) - 1, -1, -1):
        ceiling = max_souls - 1 - (len(result) - 1 - i)
        if result[i] > ceiling:
            result[i] = ceiling
    return result


def randomize_soul_thresholds(
    rng: random.Random,
    max_souls: int = _MAX_SOULS,
    levels: int = _LEVELS,
    mode: str = "random",
) -> dict:
    """
    Generate a randomized soul threshold map.

    mode:
        "random"      — purely random (original behaviour): picks (levels-1)
                        distinct values uniformly from [1, max_souls-1].
        "balanced"    — roughly equal spacing: divides [1, max_souls-1] into
                        (levels-1) equal windows and picks one value per window,
                        producing thresholds with similar-sized gaps throughout.
        "progressive" — gaps grow geometrically: early SLs are cheap, later SLs
                        demand large soul counts.  Growth ratio is seeded so
                        each run is different.

    SL0 is always 0 (free).  SL10 is always max_souls (120).
    All intermediate values are strictly ascending.

    Returns dict[int, int] mapping SL index → souls required.
    """
    inner_count = levels - 1  # 9 intermediate values

    if mode == "balanced":
        # Divide (0, max_souls) into inner_count equal windows; pick one per window.
        window = (max_souls - 1) / inner_count
        raw = [round(rng.uniform(1 + i * window, 1 + (i + 1) * window - 0.5))
               for i in range(inner_count)]
        inner = _make_ascending(raw, inner_count, max_souls)

    elif mode == "progressive":
        # Geometric gap progression: g, g*r, g*r^2, … g*r^(levels-1) sum to max_souls.
        # Add per-gap noise, then re-normalise so the total is still max_souls.
        r = rng.uniform(1.15, 1.50)
        base_gaps = [r ** i for i in range(levels)]
        noisy_gaps = [g * rng.uniform(0.75, 1.25) for g in base_gaps]
        total = sum(noisy_gaps)
        scaled_gaps = [g * max_souls / total for g in noisy_gaps]
        cumulative = 0.0
        raw = []
        for i in range(inner_count):
            cumulative += scaled_gaps[i]
            raw.append(round(cumulative))
        inner = _make_ascending(raw, inner_count, max_souls)

    else:  # "random"
        inner = sorted(rng.sample(range(1, max_souls), inner_count))

    values = [0] + inner + [max_souls]   # length = levels + 1
    return {sl: v for sl, v in enumerate(values)}


def apply_soul_threshold_patch(
    exe_path: str,
    thresholds: dict,
    *,
    dry_run: bool = False,
) -> None:
    """
    Write randomized soul thresholds to thoth_x64.exe.

    thresholds: dict returned by randomize_soul_thresholds() — keys 0..10.
    SL0 (always 0) is skipped; SL1..SL10 map to the 10 patch addresses.

    Raises ValueError if any value exceeds 255 or sequence is not ascending.
    """
    if not Path(exe_path).exists():
        print(f"  [soul_thresholds] EXE not found: {exe_path} — skipping")
        return

    # Build ordered list SL1..SL10
    sl_values = [thresholds[sl] for sl in range(1, _LEVELS + 1)]

    # Validate
    for i, v in enumerate(sl_values, start=1):
        if v > 255:
            raise ValueError(f"  [soul_thresholds] SL{i} value {v} exceeds 255 (imm8 max)")
    for i in range(len(sl_values) - 1):
        if sl_values[i] >= sl_values[i + 1]:
            raise ValueError(
                f"  [soul_thresholds] Values not strictly ascending: "
                f"SL{i+1}={sl_values[i]} >= SL{i+2}={sl_values[i+1]}"
            )

    # Check if all values are vanilla — skip write in that case
    vanilla_values = [VANILLA_SOUL_THRESHOLDS[sl] for sl in range(1, _LEVELS + 1)]
    if sl_values == vanilla_values:
        print("  [soul_thresholds] Unchanged (vanilla)")
        return

    data = bytearray(Path(exe_path).read_bytes())

    for sl, (addr, val) in enumerate(zip(SOUL_THRESHOLD_PATCH_ADDRS, sl_values), start=1):
        vanilla = VANILLA_SOUL_THRESHOLDS[sl]
        if not dry_run:
            data[addr] = val
        if val != vanilla:
            print(f"  [soul_thresholds] SL{sl:>2}: {vanilla:>3} → {val:>3}  @ 0x{addr:X}")
        else:
            print(f"  [soul_thresholds] SL{sl:>2}: {val:>3} (unchanged)  @ 0x{addr:X}")

    if not dry_run:
        Path(exe_path).write_bytes(data)
        print(f"  [soul_thresholds] Thresholds written to {Path(exe_path).name}")
