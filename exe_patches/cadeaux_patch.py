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

# ── levels.txt launch-time validator (souls != 120 / cadeaux != 666 crash) ───
# Confirmed 2026-07-21 via Ghidra disassembly of FUN_14031c100 (the levels.txt
# parser), listing view around VA 0x14031e276:
#
#   14031e276  41 80 fe 78          CMP   R14B, 0x78       ; cVar22 (dark soul
#                                                           ; total) vs 'x'=120
#   14031e27a  0f 85 3e 03 00 00    JNZ   LAB_14031e5be     ; crash if != 120
#   14031e280  81 fa 9a 02 00 00    CMP   EDX, 0x29a        ; DAT_1407edde8
#                                                           ; (cadeaux total)
#                                                           ; vs 0x29a = 666
#   14031e286  0f 85 3f 03 00 00    JNZ   LAB_14031e5cb     ; crash if != 666
#
# Both failure branches call a format function then `swi(3)` (software
# interrupt / breakpoint), which is what produces the crash dialogs
# ("Total Dark Souls in levels is not 120. Its: N") — confirmed against a
# real in-game crash screenshot the same day.
#
# In an AP multiworld these totals are not meaningful invariants — some of
# Shadow Man's own Dark Souls/Cadeaux can be sent to other players' games,
# and Shadow Man's own slots can hold other players' items instead, so the
# true in-world total can legitimately be anything. Trying to keep the
# declared levels.txt totals artificially pinned to 120/666 turned into
# repeated whack-a-mole (see AP_FEATURE_GAP.md's changelog — a 95-instead-
# of-120 undershoot and a 750-instead-of-666 overshoot were both found and
# fixed independently in patchers/levels_txt_patcher.py before concluding the
# EXE check itself needed to go). The correct fix is here: stop the engine
# from crashing over a mismatch that's expected and harmless, so
# levels_txt_patcher.py is free to write the TRUE totals instead of
# fabricating fake ones.
#
# Patch: NOP out both JNZ instructions (6 bytes each) so execution always
# falls through to the success path regardless of what cVar22/DAT_1407edde8
# actually computed to. The CMP instructions and the underlying count
# computation are left completely untouched — DAT_1407edde8 still ends up
# holding the real computed cadeaux total, which (per cadeaux_patch.py's own
# docstring) is reused downstream as the Fogometers door requirement, so the
# door's threshold will track the true total rather than a stale 666.
#
# IMPORTANT — file offsets below were originally computed as
# VA - 0x140000000 (the convention every other offset in this file uses
# successfully, e.g. THRESHOLD_OFFSET/SOUL_THRESHOLD_PATCH_ADDRS), which gave
# 0x31E27A / 0x31E286. Confirmed WRONG against a real exe (2026-07-21) — the
# bytes there didn't match. Re-derived by directly searching the exe's raw
# bytes for the CMP+JNZ byte sequences instead of trusting VA math; the real
# offsets are 0xC04 bytes earlier: 0x31D67A / 0x31D686. This region of the
# file apparently doesn't follow the flat VA-imagebase convention (likely a
# section alignment quirk) — worth remembering if any *other* offset in this
# neighborhood (e.g. CADEAU_TOTAL_OFFSETS' "levelstxt_validation": 0x31E282
# above, from an earlier, never-actually-applied "FUTURE" note) turns out to
# need the same ~0xC04 correction; that one was derived the same
# VA-subtraction way and has not been independently verified against a real
# exe the way the two offsets below now have.
LEVELS_VALIDATOR_SOUL_JNZ_OFFSET    = 0x31D67A
LEVELS_VALIDATOR_CADEAUX_JNZ_OFFSET = 0x31D686
LEVELS_VALIDATOR_JNZ_VANILLA        = bytes.fromhex("0f853e030000")  # souls JNZ
LEVELS_VALIDATOR_CADEAUX_JNZ_VANILLA = bytes.fromhex("0f853f030000")  # cadeaux JNZ
_NOP6 = b"\x90" * 6


def patch_levels_txt_launch_validator(exe_path: str, *, dry_run: bool = False) -> dict:
    """
    Disable the launch-time crash if levels.txt's dark-soul or cadeaux
    totals don't match the vanilla 120 / 666 constants. Safe in any AP
    context since these totals are not meaningful invariants once items can
    be shuffled between games. See the offset table comment above for the
    full derivation.

    Idempotent: if the JNZ bytes are already NOPed (patch already applied),
    this is a silent no-op. Warns (does not fail) if the bytes at either
    offset don't match either the vanilla JNZ or the already-NOPed form —
    that would mean this game build doesn't match the offsets above.
    """
    if not Path(exe_path).exists():
        print(f"  [levels_validator] EXE not found: {exe_path} — skipping")
        return {}

    data = bytearray(Path(exe_path).read_bytes())
    changed = False
    result = {}

    for label, offset, vanilla in (
        ("soul total (120)",    LEVELS_VALIDATOR_SOUL_JNZ_OFFSET,    LEVELS_VALIDATOR_JNZ_VANILLA),
        ("cadeaux total (666)", LEVELS_VALIDATOR_CADEAUX_JNZ_OFFSET, LEVELS_VALIDATOR_CADEAUX_JNZ_VANILLA),
    ):
        current = bytes(data[offset:offset + 6])
        if current == _NOP6:
            print(f"  [levels_validator] {label} check already disabled @ 0x{offset:X}")
            result[label] = "already-patched"
            continue
        if current != vanilla:
            print(f"  [levels_validator] WARNING: unexpected bytes at 0x{offset:X} "
                  f"({current.hex()}) — expected vanilla JNZ ({vanilla.hex()}). "
                  f"Skipping {label} patch — offsets may not match this build.")
            result[label] = "skipped-unexpected-bytes"
            continue
        if not dry_run:
            data[offset:offset + 6] = _NOP6
        changed = True
        result[label] = "patched"
        print(f"  [levels_validator] {label} launch-crash check disabled @ 0x{offset:X}")

    if changed and not dry_run:
        Path(exe_path).write_bytes(data)
    elif not changed:
        print("  [levels_validator] Unchanged")

    return result


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
