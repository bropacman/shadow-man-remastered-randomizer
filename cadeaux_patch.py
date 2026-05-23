"""
cadeaux_patch.py
===============
Patches two cadeaux interaction values in thoth_x64.exe.

Both values are always set to the same number (altar_cadeaux_required),
so the minimum required and the cost per interaction are identical.

1. INTERACTION THRESHOLD (altar_cadeaux_required)
   Minimum cadeaux to interact with an altar or the Fogometers door.
   Vanilla: 100. Instruction: CMP EAX, 0x64 in FUN_14043f2f0.
   File offset: 0x43E940 (single byte)

2. INTERACTION COST
   Cadeaux subtracted per successful interaction. Vanilla: 100.
   Instruction: LEA R8D, [RDX+imm8] in FUN_14043f2f0.
   File offset: 0x43E9B7 (single byte)
   Encoding: patch_byte = (248 - value) & 0xFF
   (RDX=8 at call site, R8D = 8 + signed(imm8) = -value)
   Vanilla patch_byte = 0x94 (value=100: 248-100=148=0x94)

Patches are in two functions:
  FUN_14043f2f0: threshold check + cost (offsets 0x43E940 and 0x43E9B7)
    - The Fogometers cadeaux door (first interaction)
    - All 5 life altars in Wasteland (subsequent interactions)
  Altar flags loop (offset 0x42B74D): SUB EDI, imm8 — Fogometers door loop check

The total cadeaux always sums to 666 (enforced by levels.txt validation).
The door always requires all 666 cadeaux. The threshold controls when
you can interact; the cost is always the same as the threshold.


FUTURE: CHANGING THE TOTAL CADEAUX REQUIREMENT (666 → N)
---------------------------------------------------------
Five EXE sites must be changed atomically plus levels.txt updated.
All LE32 (4 bytes each):
  0x7EDDE8  — DAT_1407edde8 base threshold
  0x32D3E2  — CMP ECX, N in meter adjuster
  0x32D3ED  — MOV [RBX+3C], N in meter adjuster
  0x32D3F2  — ADD EAX, N in meter adjuster
  0x31E282  — CMP EDX, N in levels.txt validator
Plus levels.txt $cadeaux values must sum to N.
"""

import random
import struct
from pathlib import Path

# ── Patch offsets (hardcoded for Shadow Man Remastered) ───────────────────────
THRESHOLD_OFFSET  = 0x43E940   # CMP EAX, imm8 — single byte
THRESHOLD_LOOP_OFFSET = 0x42B74D   # SUB EDI, imm8 in altar flags loop — single byte
                                    # vanilla = 0x64 (100), patched directly (no encoding)
STEP_OFFSET       = 0x43E9B7   # LEA R8D, [RDX+imm8] displacement — single byte

FOGOMETERS_REQUIRED_OFFSET   = 0x7EDDE8   # DAT_1407edde8 — LE32, vanilla = 666
FOGOMETERS_MOV_OFFSET = 0x42B72C  # MOV EDI, [DAT_1407edde8] — 6 bytes
FOGOMETERS_MOV2_OFFSET = 0x42B78C  # MOV EAX, [DAT_1407edde8] — 6 bytes
FOGOMETERS_REQUIRED_VANILLA  = 666
FOGOMETERS_500_OFFSET = 0x42B794  # MOV R8D, 0x1f4 immediate — LE32, vanilla = 500

THRESHOLD_VANILLA = 0x64       # 100
STEP_IMM_VANILLA  = 0x94       # displacement for step=100

CADEAU_THRESHOLD_VANILLA = 100
VANILLA_TOTAL            = 666

# ── Future: total cadeaux requirement patch sites ─────────────────────────────
CADEAU_TOTAL_OFFSETS = {
    "dat_1407edde8":        0x7EDDE8,
    "meter_cap_cmp":        0x32D3E2,
    "meter_cap_mov":        0x32D3ED,
    "meter_cap_add":        0x32D3F2,
    "levelstxt_validation": 0x31E282,
}


def _step_to_imm8(step: int) -> int:
    """Convert step value to LEA displacement byte. step=100 → 0x94."""
    return (248 - step) & 0xFF


def _imm8_to_step(imm8: int) -> int:
    """Convert LEA displacement byte back to step value."""
    return (248 - imm8) & 0xFF


def apply_cadeau_step_patch(
    exe_path: str,
    rng: random.Random,
    config: dict,
    *,
    dry_run: bool = False,
) -> dict:
    """
    Patch cadeaux interaction threshold and cost (always equal).

    Config keys:
        altar_cadeaux_required      int 1-133, or "random" (default: 100)
        altar_cadeaux_required_min  int, lower bound for random (default: 50)
        altar_cadeaux_required_max  int, upper bound for random (default: 100)
        fogometers_cadeaux_required int or "random" (default: 666)
                                    min = 5 × altar cost, max = 666
        fogometers_cadeaux_required_min  int, lower bound for random (default: 5 × altar)
        fogometers_cadeaux_required_max  int, upper bound for random (default: 666)

    Both the minimum required and the per-interaction cost are set to
    altar_cadeaux_required.

    Returns dict with applied values for spoiler log.
    """
    if not Path(exe_path).exists():
        print(f"  [cadeaux] EXE not found: {exe_path} — skipping")
        return {}

    ALTAR_MAX = 133  # floor(666 / 5)

    # Resolve altar threshold
    threshold = config.get("altar_cadeaux_required", CADEAU_THRESHOLD_VANILLA)
    if threshold == "random":
        lo = int(config.get("altar_cadeaux_required_min", 50))
        hi = int(config.get("altar_cadeaux_required_max", 100))
        threshold = rng.randint(lo, hi)
    threshold = max(1, min(ALTAR_MAX, int(threshold)))

    # Resolve fogometers requirement (must be resolved after threshold)
    fog_required = config.get("fogometers_cadeaux_required", FOGOMETERS_REQUIRED_VANILLA)
    if fog_required == "random":
        fog_lo = int(config.get("fogometers_cadeaux_required_min", threshold * 5))
        fog_hi = int(config.get("fogometers_cadeaux_required_max", FOGOMETERS_REQUIRED_VANILLA))
        fog_required = rng.randint(fog_lo, fog_hi)
    fog_required = max(threshold * 5, min(FOGOMETERS_REQUIRED_VANILLA, int(fog_required)))

    # Early exit only when both values are vanilla
    if threshold == CADEAU_THRESHOLD_VANILLA and fog_required == FOGOMETERS_REQUIRED_VANILLA:
        print(f"  [cadeaux] Unchanged (vanilla)")
        return {"altar_cadeaux_required": threshold, "fogometers_cadeaux_required": fog_required}

    data = bytearray(Path(exe_path).read_bytes())

    # ── Altar threshold + cost ────────────────────────────────────────────────
    if threshold != CADEAU_THRESHOLD_VANILLA:
        actual_t = data[THRESHOLD_OFFSET]
        if actual_t != THRESHOLD_VANILLA:
            print(f"  [cadeaux] Note: threshold byte is 0x{actual_t:02X} (not vanilla 0x{THRESHOLD_VANILLA:02X})")
        if not dry_run:
            data[THRESHOLD_OFFSET] = threshold
        print(f"  [cadeaux] Life Altar Threshold : {threshold} cadeaux  (vanilla: {CADEAU_THRESHOLD_VANILLA})  @ 0x{THRESHOLD_OFFSET:X}")

        actual_l = data[THRESHOLD_LOOP_OFFSET]
        if actual_l != THRESHOLD_VANILLA:
            print(f"  [cadeaux] Note: loop threshold byte is 0x{actual_l:02X} (not vanilla 0x{THRESHOLD_VANILLA:02X})")
        if not dry_run:
            data[THRESHOLD_LOOP_OFFSET] = threshold
        print(f"  [cadeaux] Life Altar Loop Amount : {threshold} cadeaux  (vanilla: {CADEAU_THRESHOLD_VANILLA})  @ 0x{THRESHOLD_LOOP_OFFSET:X}")

        actual_s = data[STEP_OFFSET]
        if actual_s != STEP_IMM_VANILLA:
            print(f"  [cadeaux] Note: cost byte is 0x{actual_s:02X} (current cost={_imm8_to_step(actual_s)})")
        if not dry_run:
            data[STEP_OFFSET] = _step_to_imm8(threshold)
        print(f"  [cadeaux] Life Altar Cost : {threshold} cadeaux  (vanilla: {CADEAU_THRESHOLD_VANILLA})  @ 0x{STEP_OFFSET:X}")

    # ── Fogometers door requirement ───────────────────────────────────────────
    if fog_required != FOGOMETERS_REQUIRED_VANILLA:
        if not dry_run:
            data[FOGOMETERS_MOV_OFFSET] = 0xBF  # MOV EDI, imm32
            struct.pack_into("<I", data, FOGOMETERS_MOV_OFFSET + 1, fog_required)
            data[FOGOMETERS_MOV_OFFSET + 5] = 0x90  # NOP
            data[FOGOMETERS_MOV2_OFFSET] = 0xB8  # MOV EAX, imm32
            struct.pack_into("<I", data, FOGOMETERS_MOV2_OFFSET + 1, fog_required)
            data[FOGOMETERS_MOV2_OFFSET + 5] = 0x90  # NOP
            struct.pack_into("<I", data, FOGOMETERS_500_OFFSET, 5 * threshold)
        print(f"  [cadeaux] Fog door : {fog_required} cadeaux  @ 0x{FOGOMETERS_MOV_OFFSET:X}")
        print(f"  [cadeaux] Fog cost : {fog_required} base, {5 * threshold} subtracted = {fog_required - 5 * threshold} net  @ 0x{FOGOMETERS_MOV2_OFFSET:X}")
        print(f"  [cadeaux] Fog 500  : {5 * threshold} (5 × {threshold})  @ 0x{FOGOMETERS_500_OFFSET:X}")

    if not dry_run:
        Path(exe_path).write_bytes(data)

    return {"altar_cadeaux_required": threshold, "fogometers_cadeaux_required": fog_required}
