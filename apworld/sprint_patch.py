"""
sprint_patch.py
================
Global Shift-sprint speed patch. Multiplies the player's movement-speed
input by a configurable factor, for BOTH land and water movement, while
either Shift key is held.

BACKGROUND
----------
Land and swim movement each independently read a shared per-object float
at player_obj+0x494 (a "desired speed" scalar, rotated into world space by
a shared direction-basis object at player_obj+0x3f0) at the very top of
their respective per-frame update functions:

  - Land:  FUN_14046b2c0, hook at VA 0x14046B2C4 -- MOVSS XMM3,[RCX+0x494]
  - Swim:  FUN_14046b590, hook at VA 0x14046B795 -- MOVSS XMM7,[RBX+0x494]

That field's normal value already varies (observed 0.5-1.0 on land,
apparently a turn/traction penalty when pushing hard against your current
heading; pinned at 1.0 in water). Rather than reverse-engineer and
preserve that curve, both hooks multiply whatever the game would have
naturally loaded by sprint_multiplier whenever Shift is held -- the
existing curve (and everything else about movement) is untouched, just
scaled on top.

SHIFT DETECTION
---------------
Reuses an existing, already-linked helper instead of adding a new import
or hand-rolling the SDL_GetModState call: FUN_1402183C0 (VA 0x1402183C0)
already does exactly "return 1 in AL if either Shift is held, else 0"
(CALL SDL_GetModState; TEST AL,3; SETNZ AL; RET) -- found by walking the
XREFs on SDL_GetModState's IAT thunk. thoth_x64.exe does not import
GetAsyncKeyState/GetKeyState at all (USER32 is only linked for window-hook
plumbing), so this SDL2-based helper is the only clean option that avoids
PE import-table surgery.

REGISTER SAFETY
----------------
- Swim hook: RBX (player object) and XMM7 (target reg) are BOTH
  non-volatile in the Windows x64 ABI, so no save/restore is needed
  around the CALL -- straight line, no stack juggling.
- Land hook: RCX (player object) IS volatile and is used heavily by the
  rest of FUN_14046b2c0 immediately after the hook, so it's saved/
  restored around the CALL. XMM3 does NOT need saving -- the cave is
  reordered to do the Shift-check *before* loading [RCX+0x494], so XMM3
  is never live across the CALL.
- Both hook sites have RSP 16-byte aligned already at the hook point
  (confirmed via each function's prologue: SUB RSP by a multiple of 16,
  following the ABI's guaranteed RSP%16==8 at function entry), so no
  extra alignment padding is needed before the CALL.

CAVE PLACEMENT
--------------
16-byte function alignment caps every ordinary inter-function CC gap in
this exe at 21 bytes (confirmed by scanning ALL of .text with
tools/find_cave.py) -- nowhere near enough to hold either cave in one
piece. Caves are therefore laid out as follows:

  SWIM (34 bytes: 30-byte cave + 4-byte shared multiplier constant): fits
  whole in the always-free tail of .text, 0x14064A3D5-0x14064A3FF (43
  bytes available).

  IMPORTANT: this is deliberately NOT the same as the larger-looking
  94-byte zero run tools/find_cave.py finds starting at 0x14064A3A2 --
  that includes gad_pickup_patch.py's cave END (0x14064A354-0x14064A3A2,
  which IS used whenever gad-temple shuffle is on) and, more importantly,
  death_penalty_patch.py's reserved 16-byte gap + 35-byte cave
  (0x14064A3A2-0x14064A3D5), which is only zero in a build where death
  penalty is disabled -- it is NOT safe to write there. This patch must
  never touch anything before 0x14064A3D5, or it will collide with
  death_penalty_patch.py whenever both are active for the same seed.

  LAND (~40 bytes): split across three small CC-padding gaps elsewhere in
  .text (7,750+ candidates of 8+ bytes found via tools/find_cave.py),
  chained with connecting JMPs:
    Fragment 1 (21 bytes @ LAND_FRAG1_VA): save RCX, CALL IsShiftHeld,
      restore RCX, JMP -> fragment 2                              (20/21)
    Fragment 2 (16 bytes @ LAND_FRAG2_VA): original MOVSS load,
      TEST AL,AL, JMP -> fragment 3                                (15/16)
    Fragment 3 (16 bytes @ LAND_FRAG3_VA): JZ skip / MULSS by the
      SAME shared multiplier constant used by the swim cave (RIP-relative,
      reachable from anywhere in the module), JMP back to the original
      function                                                     (15/16)

Each hook site's original 8-byte MOVSS is replaced with a 5-byte JMP to
its cave/first fragment, padded with 3 NOPs.

Fragment addresses (LAND_FRAG2_VA/LAND_FRAG3_VA especially) are specific
to this exe build's current padding layout -- re-run tools/find_cave.py
and update if this ever needs re-deriving against a different build.

NOTE (AP world copy): this file is a byte-identical duplicate of the main
repo's sprint_patch.py, kept here purely for cross-repo documentation
parity with health_patch.py/death_penalty_patch.py/cadeaux_patch.py/
soul_threshold_patch.py -- it is NOT imported by anything in this AP world
folder. ap_patcher.py (in the main shadow-man-remastered-randomizer repo)
imports the canonical copy directly and is the sole AP-facing patching
path (worlds/shadowman's own old patcher.py was removed 2026-07-21).
"""
import random
import struct
from pathlib import Path

# ── Address mapping (matches every other *_patch.py in this repo) ─────────────
IMAGE_BASE    = 0x140000000
SECTION_DELTA = 0xC00   # .text: vaddr=0x1000, raw=0x400 => delta=0xC00


def _va_to_file(va: int) -> int:
    return va - IMAGE_BASE - SECTION_DELTA


def _file_to_va(file_off: int) -> int:
    return file_off + IMAGE_BASE + SECTION_DELTA


# ── Hook sites ──────────────────────────────────────────────────────────────
LAND_HOOK_VA      = 0x14046B2C4
LAND_HOOK_FILE    = _va_to_file(LAND_HOOK_VA)
LAND_HOOK_VANILLA = bytes.fromhex("F30F109994040000")   # MOVSS XMM3,[RCX+0x494]

SWIM_HOOK_VA      = 0x14046B795
SWIM_HOOK_FILE    = _va_to_file(SWIM_HOOK_VA)
SWIM_HOOK_VANILLA = bytes.fromhex("F30F10BB94040000")   # MOVSS XMM7,[RBX+0x494]

# Existing, already-linked "return 1 in AL if either Shift held" helper.
IS_SHIFT_HELD_VA = 0x1402183C0

# ── Cave / fragment locations ──────────────────────────────────────────────
SWIM_CAVE_VA   = 0x14064A3D5   # always-free .text tail; do NOT go below this
SWIM_CAVE_FILE = _va_to_file(SWIM_CAVE_VA)
SWIM_CAVE_SIZE = 34            # 30-byte cave + 4-byte multiplier constant

LAND_FRAG1_VA   = 0x1405FF881
LAND_FRAG1_FILE = _va_to_file(LAND_FRAG1_VA)
LAND_FRAG1_SIZE = 20           # of 21 available

LAND_FRAG2_VA   = 0x14014DFE0
LAND_FRAG2_FILE = _va_to_file(LAND_FRAG2_VA)
LAND_FRAG2_SIZE = 15           # of 16 available

LAND_FRAG3_VA   = 0x1401841C0
LAND_FRAG3_FILE = _va_to_file(LAND_FRAG3_VA)
LAND_FRAG3_SIZE = 15           # of 16 available

DEFAULT_MULTIPLIER     = 2.0
DEFAULT_MULTIPLIER_MIN = 1.5
DEFAULT_MULTIPLIER_MAX = 4.0


# ── Small encoders ──────────────────────────────────────────────────────────

def _jmp_rel32(from_va: int, to_va: int) -> bytes:
    """E9 rel32 -- from_va is the address of the JMP opcode byte itself."""
    rel = to_va - (from_va + 5)
    return bytes([0xE9]) + struct.pack('<i', rel)


def _call_rel32(from_va: int, to_va: int) -> bytes:
    rel = to_va - (from_va + 5)
    return bytes([0xE8]) + struct.pack('<i', rel)


def _jz_rel8(from_va: int, to_va: int) -> bytes:
    rel = to_va - (from_va + 2)
    assert -128 <= rel <= 127, f"JZ target out of short-jump range: {rel}"
    return bytes([0x74]) + struct.pack('<b', rel)


def _mulss_ripconst(instr_va: int, const_va: int) -> bytes:
    """F3 0F 59 /r [rip+disp32] -- MULSS xmm,[rip+disp32]. instr_va is the
    address of the F3 byte; disp is relative to the END of this 8-byte
    instruction. ModRM 0x1D = mod=00,reg=011(XMM3),rm=101(RIP-relative);
    0x35 = mod=00,reg=110(XMM6) -- both built explicitly below per-register
    since XMM3 (land) and XMM7 (swim) need different reg fields."""
    raise NotImplementedError  # see _mulss_xmm3_ripconst / _mulss_xmm7_ripconst


def _mulss_xmm3_ripconst(instr_va: int, const_va: int) -> bytes:
    disp = const_va - (instr_va + 8)
    return bytes([0xF3, 0x0F, 0x59, 0x1D]) + struct.pack('<i', disp)


def _mulss_xmm7_ripconst(instr_va: int, const_va: int) -> bytes:
    disp = const_va - (instr_va + 8)
    return bytes([0xF3, 0x0F, 0x59, 0x3D]) + struct.pack('<i', disp)


# ── Cave builders ────────────────────────────────────────────────────────────

def build_swim_cave() -> bytes:
    """
    34 bytes at SWIM_CAVE_VA (30-byte cave + 4-byte multiplier constant).
    No register preservation needed -- RBX/XMM7 both non-volatile.

        +00  MOVSS  XMM7,[RBX+0x494]        original load          8
        +08  CALL   IsShiftHeld                                    5
        +13  TEST   AL,AL                                          2
        +15  JZ     +25 (skip)                                     2
        +17  MULSS  XMM7,[rip -> +30]                               8
        +25  JMP    SWIM_HOOK_VA+8          skip: lands here        5
        +30  <multiplier float32>                                  4
    """
    base = SWIM_CAVE_VA
    cave = bytearray()

    cave += SWIM_HOOK_VANILLA                                          # +0..7
    cave += _call_rel32(base + 8, IS_SHIFT_HELD_VA)                     # +8..12
    cave += bytes([0x84, 0xC0])                                        # +13..14 TEST AL,AL
    cave += _jz_rel8(base + 15, base + 25)                              # +15..16 JZ -> +25
    cave += _mulss_xmm7_ripconst(base + 17, base + 30)                  # +17..24
    cave += _jmp_rel32(base + 25, SWIM_HOOK_VA + len(SWIM_HOOK_VANILLA))# +25..29
    cave += b"\x00\x00\x00\x00"                                        # +30..33 placeholder, patched by apply

    assert len(cave) == SWIM_CAVE_SIZE, f"swim cave size mismatch: {len(cave)}"
    return bytes(cave)


def build_land_fragments() -> tuple:
    """Returns (frag1, frag2, frag3) bytes for the three land cave pieces."""

    # Fragment 1 @ LAND_FRAG1_VA (20 of 21 bytes)
    #   PUSH RCX ; SUB RSP,8 ; CALL IsShiftHeld ; ADD RSP,8 ; POP RCX ; JMP frag2
    b1 = base1 = LAND_FRAG1_VA
    f1 = bytearray()
    f1 += bytes([0x51])                                    # +0  PUSH RCX
    f1 += bytes([0x48, 0x83, 0xEC, 0x08])                   # +1  SUB RSP,8
    f1 += _call_rel32(base1 + 5, IS_SHIFT_HELD_VA)          # +5  CALL IsShiftHeld
    f1 += bytes([0x48, 0x83, 0xC4, 0x08])                   # +10 ADD RSP,8
    f1 += bytes([0x59])                                     # +14 POP RCX
    f1 += _jmp_rel32(base1 + 15, LAND_FRAG2_VA)             # +15 JMP frag2
    assert len(f1) == LAND_FRAG1_SIZE, f"land frag1 size mismatch: {len(f1)}"

    # Fragment 2 @ LAND_FRAG2_VA (15 of 16 bytes)
    #   MOVSS XMM3,[RCX+0x494] ; TEST AL,AL ; JMP frag3
    base2 = LAND_FRAG2_VA
    f2 = bytearray()
    f2 += LAND_HOOK_VANILLA                                 # +0  original load (8)
    f2 += bytes([0x84, 0xC0])                                # +8  TEST AL,AL
    f2 += _jmp_rel32(base2 + 10, LAND_FRAG3_VA)              # +10 JMP frag3
    assert len(f2) == LAND_FRAG2_SIZE, f"land frag2 size mismatch: {len(f2)}"

    # Fragment 3 @ LAND_FRAG3_VA (15 of 16 bytes)
    #   JZ skip ; MULSS XMM3,[rip -> shared swim multiplier constant] ; skip: JMP back
    base3 = LAND_FRAG3_VA
    f3 = bytearray()
    f3 += _jz_rel8(base3 + 0, base3 + 10)                    # +0  JZ -> +10 (skip)
    const_va = SWIM_CAVE_VA + 30                             # shared multiplier constant
    f3 += _mulss_xmm3_ripconst(base3 + 2, const_va)          # +2  MULSS XMM3,[rip+const]
    f3 += _jmp_rel32(base3 + 10, LAND_HOOK_VA + len(LAND_HOOK_VANILLA))  # +10 JMP back
    assert len(f3) == LAND_FRAG3_SIZE, f"land frag3 size mismatch: {len(f3)}"

    return bytes(f1), bytes(f2), bytes(f3)


def build_hook_patch(hook_va: int, target_va: int) -> bytes:
    """5-byte JMP + 3 NOPs, replacing an 8-byte MOVSS at hook_va."""
    return _jmp_rel32(hook_va, target_va) + bytes([0x90, 0x90, 0x90])


# ── Verify / apply ────────────────────────────────────────────────────────────

_SITES = {
    "land_hook":  (LAND_HOOK_FILE,  LAND_HOOK_VANILLA),
    "swim_hook":  (SWIM_HOOK_FILE,  SWIM_HOOK_VANILLA),
    "swim_cave":  (SWIM_CAVE_FILE,  bytes(SWIM_CAVE_SIZE)),          # must be all-zero
    "land_frag1": (LAND_FRAG1_FILE, bytes([0xCC] * LAND_FRAG1_SIZE)),# must be all-CC
    "land_frag2": (LAND_FRAG2_FILE, bytes([0xCC] * LAND_FRAG2_SIZE)),
    "land_frag3": (LAND_FRAG3_FILE, bytes([0xCC] * LAND_FRAG3_SIZE)),
}


def verify_vanilla(data: bytes) -> list:
    """Return a list of site names whose current bytes don't match the
    expected pre-patch state. Empty list == safe to patch."""
    problems = []
    for name, (file_off, expected) in _SITES.items():
        actual = data[file_off:file_off + len(expected)]
        if actual != expected:
            problems.append(name)
    return problems


def _resolve_multiplier(config: dict, rng: random.Random) -> float:
    mult = config.get("sprint_multiplier", DEFAULT_MULTIPLIER)
    if mult == "random":
        lo = float(config.get("sprint_multiplier_min", DEFAULT_MULTIPLIER_MIN))
        hi = float(config.get("sprint_multiplier_max", DEFAULT_MULTIPLIER_MAX))
        mult = round(rng.uniform(lo, hi), 2)
    return max(1.0, float(mult))


def apply_sprint_patch(
    exe_path: str,
    rng: random.Random,
    config: dict,
    *,
    dry_run: bool = False,
) -> dict:
    """
    Patch in the global Shift-sprint speed multiplier (land + water).

    Config keys:
        sprint_multiplier            float >= 1.0, or "random" (default: 2.0)
        sprint_multiplier_min        float, lower bound for random (default: 1.5)
        sprint_multiplier_max        float, upper bound for random (default: 4.0)

    Returns dict with the applied multiplier for the spoiler log, or {}
    if skipped/failed.
    """
    path = Path(exe_path)
    if not path.exists():
        print(f"  [sprint] EXE not found: {exe_path} -- skipping")
        return {}

    data = bytearray(path.read_bytes())

    problems = verify_vanilla(bytes(data))
    if problems:
        print(f"  [sprint] WARNING: unexpected bytes at {problems} -- "
              f"skipping (already patched, or wrong EXE build/version)")
        return {}

    multiplier = _resolve_multiplier(config, rng)
    print(f"  [sprint] Shift-sprint multiplier: {multiplier}x  "
          f"(land hook 0x{LAND_HOOK_VA:X}, swim hook 0x{SWIM_HOOK_VA:X})")

    if dry_run:
        print("  [sprint] DRY RUN -- no bytes written")
        return {"sprint_multiplier": multiplier}

    # Swim cave (constant baked in directly)
    swim_cave = bytearray(build_swim_cave())
    swim_cave[30:34] = struct.pack('<f', multiplier)
    data[SWIM_CAVE_FILE:SWIM_CAVE_FILE + SWIM_CAVE_SIZE] = swim_cave

    # Land fragments
    f1, f2, f3 = build_land_fragments()
    data[LAND_FRAG1_FILE:LAND_FRAG1_FILE + LAND_FRAG1_SIZE] = f1
    data[LAND_FRAG2_FILE:LAND_FRAG2_FILE + LAND_FRAG2_SIZE] = f2
    data[LAND_FRAG3_FILE:LAND_FRAG3_FILE + LAND_FRAG3_SIZE] = f3

    # Hook sites (written last, so a crash/interrupt mid-patch never leaves
    # a hook pointing at a half-written cave)
    data[SWIM_HOOK_FILE:SWIM_HOOK_FILE + 8] = build_hook_patch(SWIM_HOOK_VA, SWIM_CAVE_VA)
    data[LAND_HOOK_FILE:LAND_HOOK_FILE + 8] = build_hook_patch(LAND_HOOK_VA, LAND_FRAG1_VA)

    path.write_bytes(data)
    print(f"  [sprint] Patched -- swim cave 0x{SWIM_CAVE_VA:X}, "
          f"land fragments 0x{LAND_FRAG1_VA:X}/0x{LAND_FRAG2_VA:X}/0x{LAND_FRAG3_VA:X}")
    return {"sprint_multiplier": multiplier}


def verify_sprint_patch(exe_path: str) -> bool:
    """Return True if the patch is applied, False if vanilla, raise on
    unexpected bytes (already-patched vs vanilla is distinguished by
    checking whether the hook sites are JMPs or the original MOVSS)."""
    data = Path(exe_path).read_bytes()
    land_actual = data[LAND_HOOK_FILE:LAND_HOOK_FILE + 8]
    swim_actual = data[SWIM_HOOK_FILE:SWIM_HOOK_FILE + 8]

    land_patched = land_actual[0] == 0xE9
    swim_patched = swim_actual[0] == 0xE9

    if land_patched and swim_patched:
        return True
    if land_actual == LAND_HOOK_VANILLA and swim_actual == SWIM_HOOK_VANILLA:
        return False
    raise RuntimeError(
        f"[sprint] Inconsistent/unexpected hook state -- "
        f"land: {land_actual.hex(' ')}  swim: {swim_actual.hex(' ')}"
    )


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Apply the global Shift-sprint speed patch to Shadow Man Remastered EXE"
    )
    parser.add_argument("exe", help="Path to thoth_x64.exe (vanilla or already patched)")
    parser.add_argument("--multiplier", type=float, default=DEFAULT_MULTIPLIER,
                        help=f"Sprint speed multiplier (default {DEFAULT_MULTIPLIER})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Verify and build cave/fragments but write nothing")
    parser.add_argument("--verify", action="store_true",
                        help="Check patch status and exit")
    args = parser.parse_args()

    if args.verify:
        try:
            applied = verify_sprint_patch(args.exe)
            print(f"Sprint patch: {'APPLIED' if applied else 'VANILLA'}")
            sys.exit(0)
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(2)

    result = apply_sprint_patch(
        args.exe,
        random.Random(),
        {"sprint_multiplier": args.multiplier},
        dry_run=args.dry_run,
    )
    if result:
        print("Done.")
    else:
        print("Skipped (see warnings above).")
        sys.exit(1)
