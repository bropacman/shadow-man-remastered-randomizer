"""
health_patch.py
===============
Patches starting max health and altar health grant in thoth_x64.exe.

1. STARTING MAX HEALTH
   The player's max health at game start.
   Vanilla: 5000 (0x1388). Instruction: MOV [140DB21FC], 0x1388
   File offset: 0x2E939C (LE32, 4 bytes)
   Scale: 1–10 where each step = 1000 units.

2. ALTAR HEALTH GRANT
   Health granted per life altar interaction.
   Vanilla: 1000 (0x3E8). Instruction: MOV R8D, 0x3E8 in FUN_14043f2f0.
   File offset: 0x43E9CD (LE32, 4 bytes)
   Scale: 1–10 where each step = 1000 units.
   Note: 5 altars × grant = total possible health from altars.
   Starting health + (5 × grant) should not exceed cap (10000).

The health cap (0x2710 = 10000) is left untouched.
Current health is set to max health by the game on spawn.
"""

import random
import struct
from pathlib import Path

# ── Patch offsets ─────────────────────────────────────────────────────────────
MAX_HEALTH_OFFSET          = 0x2E939C   # MOV [DAT_140db21fc], imm32 — LE32
CURRENT_HEALTH_INIT_OFFSET = 0x72C450   # qword constant low dword — LE32, vanilla = 5000
ALTAR_HEALTH_GRANT_OFFSET  = 0x43E9CD   # MOV R8D, imm32 in altar handler — LE32

MAX_HEALTH_VANILLA         = 5000
ALTAR_HEALTH_GRANT_VANILLA = 1000
HEALTH_STEP                = 1000
HEALTH_CAP                 = 10000


def _scale_to_health(scale: float) -> int:
    return max(HEALTH_STEP // 2, min(HEALTH_CAP, round(scale * HEALTH_STEP)))


def _health_to_scale(health: int) -> float:
    return max(0.5, min(10.0, health / HEALTH_STEP))


def apply_health_patch(
    exe_path: str,
    rng: random.Random,
    config: dict,
    *,
    dry_run: bool = False,
) -> dict:
    """
    Patch starting max health and altar health grant.

    Config keys:
        starting_health            float 0.5–10, or "random" (default: 5)
        starting_health_min        float, lower bound for random (default: 0.5)
        starting_health_max        float, upper bound for random (default: 10)
        altar_health_grant         float 0.5–10, or "random" (default: 1)
        altar_health_grant_min     float, lower bound for random (default: 0.5)
        altar_health_grant_max     float, upper bound for random (default: 5)

    Returns dict with applied values for spoiler log.
    """
    if not Path(exe_path).exists():
        print(f"  [health] EXE not found: {exe_path} — skipping")
        return {}

    data = bytearray(Path(exe_path).read_bytes())
    results = {}
    changed = False

    # ── Starting max health ───────────────────────────────────────────────────
    scale = config.get("starting_health", 5)
    if scale == "random":
        lo = float(config.get("starting_health_min", 0.5))
        hi = float(config.get("starting_health_max", 10))
        scale = round(rng.uniform(lo, hi) * 2) / 2  # snap to nearest 0.5
    scale = max(0.5, min(10.0, float(scale)))
    health = _scale_to_health(scale)

    actual = struct.unpack_from("<I", data, MAX_HEALTH_OFFSET)[0]
    if actual != MAX_HEALTH_VANILLA:
        print(f"  [health] Note: existing start health is {actual} (not vanilla {MAX_HEALTH_VANILLA})")
    if health != MAX_HEALTH_VANILLA:
        if not dry_run:
            struct.pack_into("<I", data, MAX_HEALTH_OFFSET, health)
        changed = True
    print(f"  [health] Starting max health : {health} (scale {scale}/10)  (vanilla: {MAX_HEALTH_VANILLA})  @ 0x{MAX_HEALTH_OFFSET:X}")
    results["starting_health"] = health
    results["starting_health_scale"] = scale

    actual_c = struct.unpack_from("<I", data, CURRENT_HEALTH_INIT_OFFSET)[0]
    if actual_c != MAX_HEALTH_VANILLA:
        print(f"  [health] Note: existing current health init is {actual_c} (not vanilla {MAX_HEALTH_VANILLA})")
    if not dry_run:
        struct.pack_into("<I", data, CURRENT_HEALTH_INIT_OFFSET, health)
    print(f"  [health] Current health init : {health}  @ 0x{CURRENT_HEALTH_INIT_OFFSET:X}")

    # ── Altar health grant ────────────────────────────────────────────────────
    grant_scale = config.get("altar_health_grant", 1)
    if grant_scale == "random":
        lo = float(config.get("altar_health_grant_min", 0.5))
        hi = float(config.get("altar_health_grant_max", 5))
        grant_scale = round(rng.uniform(lo, hi) * 2) / 2  # snap to nearest 0.5
    grant_scale = max(0.5, min(10.0, float(grant_scale)))
    grant = _scale_to_health(grant_scale)

    # Warn if starting health + 5 altars exceeds cap
    if health + 5 * grant > HEALTH_CAP:
        print(f"  [health] Warning: start {health} + 5×{grant} = {health + 5*grant} exceeds cap {HEALTH_CAP}")

    actual_g = struct.unpack_from("<I", data, ALTAR_HEALTH_GRANT_OFFSET)[0]
    if actual_g != ALTAR_HEALTH_GRANT_VANILLA:
        print(f"  [health] Note: existing altar grant is {actual_g} (not vanilla {ALTAR_HEALTH_GRANT_VANILLA})")
    if grant != ALTAR_HEALTH_GRANT_VANILLA:
        if not dry_run:
            struct.pack_into("<I", data, ALTAR_HEALTH_GRANT_OFFSET, grant)
        changed = True
    print(f"  [health] Altar health grant  : {grant} (scale {grant_scale}/10)  (vanilla: {ALTAR_HEALTH_GRANT_VANILLA})  @ 0x{ALTAR_HEALTH_GRANT_OFFSET:X}")
    results["altar_health_grant"] = grant
    results["altar_health_grant_scale"] = grant_scale

    if changed and not dry_run:
        Path(exe_path).write_bytes(data)

    return results