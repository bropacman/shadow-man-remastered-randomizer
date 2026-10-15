"""
deadside_guns_patch.py
=======================
Force-enables the vanilla "I like Dead Side Guns" secret (Secret 14 --
lets Deadside weapons work on Liveside and vice versa) permanently, without
requiring the player to find its hidden in-game unlock trigger (Florida
Summer Camp, near the final mine shaft).

BACKGROUND -- how the secret is normally set
---------------------------------------------
All ~20 secrets (Big Head, Disco, Trippy Mode, etc.) share one generic
writer instruction:

    thoth_x64_patched.exe+3FC7DD   89 41 34   MOV [RCX+0x34], EAX

RCX at that point is a "current menu cursor" pointer (global DAT_140d838c0)
that gets pointed at whichever secret's static struct is currently selected
in the Secrets menu -- confirmed live via Cheat Engine: while the cursor sat
on Deadside Guns, RCX == 0x140CD4B40; moving to the very next secret in menu
order shifted it to exactly 0x140CD4B60 (struct stride 0x20). EAX holds the
value being applied -- observed 0x11 (17) when a secret is toggled off and
0x12 (18) when toggled on, same constant for every secret.

So "always on" for one specific secret means: whenever this write fires
with RCX pointing at THAT secret's fixed struct address, force EAX to 0x12
before the write executes; every other secret's write (any other RCX value)
passes through untouched.

ADDRESS MAPPING
---------------
Same convention as every other *_patch.py in this repo:
  file_offset = VA - IMAGE_BASE - SECTION_DELTA   (SECTION_DELTA = 0xC00)

HOW IT WORKS
------------
The vanilla instruction stream at the hook site is actually 9 bytes, not 3 --
the write is immediately followed by an unrelated reload that a straight
5-byte JMP would otherwise clobber:

  1403fc7dd  89 41 34              MOV [RCX+0x34], EAX      (the write)
  1403fc7e0  8B 05 06 E5 8C 00      MOV EAX, [DAT_140ccacec] (pending-toggle
                                                              queue counter --
                                                              reloaded here,
                                                              then decremented
                                                              and used to
                                                              index a queue
                                                              array a few
                                                              instructions
                                                              later, so this
                                                              reload cannot
                                                              be skipped)

Both are replaced by a 5-byte JMP + 4 NOPs (9 bytes total) into a 3-fragment
chain. 16-byte function alignment caps every ordinary inter-function CC gap
in this exe at 16 bytes (confirmed exhaustively via tools/find_cave.py --
see sprint_patch.py for the same constraint hit previously), so the logic
is split across three separate ~16-byte gaps rather than one big cave,
chained by JMPs (same technique sprint_patch.py's LAND hook already uses):

  FRAG_A (11/16 bytes, VA 0x1401A10B0):
    CMP ECX, 0xCD4B40        ; does RCX (low 32 bits) match our target?
    JMP FRAG_FORCE           ; EFLAGS survive the JMP untouched

  FRAG_FORCE (12/16 bytes, VA 0x1401C2F80):
    JNE skip                 ; short jump, skips the force if not a match
    MOV EAX, 0x12            ; force "on"
    skip: JMP FRAG_C

  FRAG_C (14/16 bytes, VA 0x1401D6430):
    MOV [RCX+0x34], EAX      ; original write, now using original-or-forced EAX
    MOV EAX, [rip+disp]      ; reproduces the clobbered reload (re-targeted
                              ; at 0x140CCACEC, recomputed relative to this
                              ; cave's own location)
    JMP 0x1403FC7E6          ; back into the original function, right after
                              ; the reload it replaces

Remaining bytes in each fragment are padded with NOP (0x90).

Every other secret's write passes through FRAG_A/FRAG_FORCE with the CMP
failing to match, so EAX is left exactly as the game computed it -- this
patch has zero effect on any secret other than Deadside Guns.
"""

import struct
from pathlib import Path

# ── Address mapping (matches every other *_patch.py in this repo) ─────────────
IMAGE_BASE    = 0x140000000
SECTION_DELTA = 0xC00   # .text: vaddr=0x1000, raw=0x400 => delta=0xC00


def _va_to_file(va: int) -> int:
    return va - IMAGE_BASE - SECTION_DELTA


def _file_to_va(file_off: int) -> int:
    return file_off + IMAGE_BASE + SECTION_DELTA


# ── Hook site ───────────────────────────────────────────────────────────────
HOOK_VA       = 0x1403FC7DD
HOOK_FILE_OFF = _va_to_file(HOOK_VA)
HOOK_VANILLA  = bytes([0x89, 0x41, 0x34, 0x8B, 0x05, 0x06, 0xE5, 0x8C, 0x00])
HOOK_SIZE     = len(HOOK_VANILLA)   # 9

HOOK_RETURN_VA = 0x1403FC7E6   # TEST EAX,EAX -- right after the clobbered reload

# ── Fragment locations (16-byte CC-padding gaps, found via tools/find_cave.py,
#    cross-checked against every VA already reserved by gad_pickup_patch.py /
#    death_penalty_patch.py / sprint_patch.py -- no overlap) ──────────────────
FRAG_A_VA     = 0x1401A10B0
FRAG_FORCE_VA = 0x1401C2F80
FRAG_C_VA     = 0x1401D6430
FRAG_SIZE     = 16   # all three gaps are exactly 16 bytes of 0xCC filler

FRAG_A_FILE     = _va_to_file(FRAG_A_VA)
FRAG_FORCE_FILE = _va_to_file(FRAG_FORCE_VA)
FRAG_C_FILE     = _va_to_file(FRAG_C_VA)

# ── Secret identity / forced state ──────────────────────────────────────────
# Struct pointer observed live via Cheat Engine while the Secrets-menu cursor
# sat on "I like Dead Side Guns" -- compared against the low 32 bits of RCX
# (safe: every pointer this accessor ever sees lives in this module's static
# data, all sharing the same upper 32 bits under this exe's fixed image base).
DEADSIDE_GUNS_STRUCT_VA  = 0x140CD4B40
DEADSIDE_GUNS_STRUCT_LOW = DEADSIDE_GUNS_STRUCT_VA - IMAGE_BASE   # 0xCD4B40

SECRET_ON_VALUE = 0x12   # observed "enabled" constant, shared by every secret

# Reload target the clobbered instruction originally pointed at (the pending
# secret-toggle queue counter, DAT_140ccacec) -- reproduced in FRAG_C.
RELOAD_TARGET_VA = 0x140CCACEC


# ── Cave builders ────────────────────────────────────────────────────────────

def build_frag_a() -> bytes:
    # CMP ECX, DEADSIDE_GUNS_STRUCT_LOW
    cmp_ins = bytes([0x81, 0xF9]) + struct.pack('<I', DEADSIDE_GUNS_STRUCT_LOW)
    # JMP FRAG_FORCE_VA
    jmp_rip = FRAG_A_VA + len(cmp_ins) + 5
    jmp_ins = bytes([0xE9]) + struct.pack('<i', FRAG_FORCE_VA - jmp_rip)
    body = cmp_ins + jmp_ins
    assert len(body) <= FRAG_SIZE, f"FRAG_A body too big: {len(body)}"
    return body + bytes([0x90]) * (FRAG_SIZE - len(body))


def build_frag_force() -> bytes:
    # JNE skip  (short jump, skip is the JMP FRAG_C instruction 5 bytes ahead)
    jne_ins = bytes([0x75, 0x05])
    # MOV EAX, SECRET_ON_VALUE
    mov_ins = bytes([0xB8]) + struct.pack('<I', SECRET_ON_VALUE)
    # skip: JMP FRAG_C_VA
    jmp_rip = FRAG_FORCE_VA + len(jne_ins) + len(mov_ins) + 5
    jmp_ins = bytes([0xE9]) + struct.pack('<i', FRAG_C_VA - jmp_rip)
    body = jne_ins + mov_ins + jmp_ins
    assert len(body) <= FRAG_SIZE, f"FRAG_FORCE body too big: {len(body)}"
    return body + bytes([0x90]) * (FRAG_SIZE - len(body))


def build_frag_c() -> bytes:
    # MOV [RCX+0x34], EAX   -- original write, now possibly-forced EAX
    write_ins = bytes([0x89, 0x41, 0x34])
    # MOV EAX, [rip+disp32]  -- reproduces the clobbered reload of DAT_140ccacec
    reload_rip = FRAG_C_VA + len(write_ins) + 6
    reload_ins = bytes([0x8B, 0x05]) + struct.pack('<i', RELOAD_TARGET_VA - reload_rip)
    # JMP HOOK_RETURN_VA
    jmp_rip = FRAG_C_VA + len(write_ins) + len(reload_ins) + 5
    jmp_ins = bytes([0xE9]) + struct.pack('<i', HOOK_RETURN_VA - jmp_rip)
    body = write_ins + reload_ins + jmp_ins
    assert len(body) <= FRAG_SIZE, f"FRAG_C body too big: {len(body)}"
    return body + bytes([0x90]) * (FRAG_SIZE - len(body))


def build_hook_patch() -> bytes:
    hook_rip = HOOK_VA + 5
    jmp_ins = bytes([0xE9]) + struct.pack('<i', FRAG_A_VA - hook_rip)
    body = jmp_ins
    assert len(body) <= HOOK_SIZE, f"Hook patch too big: {len(body)}"
    return body + bytes([0x90]) * (HOOK_SIZE - len(body))


# ── Verify / apply helpers ───────────────────────────────────────────────────

_FRAG_FILL_BYTE = 0xCC


def _frag_is_untouched(data: bytes, file_off: int) -> bool:
    region = data[file_off: file_off + FRAG_SIZE]
    return region == bytes([_FRAG_FILL_BYTE]) * FRAG_SIZE


def _frag_matches_ours(data: bytes, file_off: int, expected: bytes) -> bool:
    return data[file_off: file_off + FRAG_SIZE] == expected


def verify_deadside_guns(exe_path: str) -> bool:
    """Return True if patch is applied, False if vanilla, raise on unexpected bytes."""
    data = Path(exe_path).read_bytes()
    hook = data[HOOK_FILE_OFF: HOOK_FILE_OFF + HOOK_SIZE]
    hook_patch = build_hook_patch()

    if hook == hook_patch:
        # Confirm fragments match too -- a matching hook with mismatched
        # fragments would mean a corrupt/partial previous application.
        if (_frag_matches_ours(data, FRAG_A_FILE, build_frag_a())
                and _frag_matches_ours(data, FRAG_FORCE_FILE, build_frag_force())
                and _frag_matches_ours(data, FRAG_C_FILE, build_frag_c())):
            return True
        raise RuntimeError(
            "[deadside_guns] Hook is patched but fragment bytes don't match "
            "-- corrupt or partial previous application."
        )
    if hook == HOOK_VANILLA:
        return False
    raise RuntimeError(
        f"[deadside_guns] Unexpected bytes at hook site (file 0x{HOOK_FILE_OFF:X} "
        f"/ VA 0x{HOOK_VA:X}).\n"
        f"  Expected vanilla : {HOOK_VANILLA.hex(' ')}\n"
        f"  Expected patched : {hook_patch.hex(' ')}\n"
        f"  Got              : {hook.hex(' ')}\n"
        "  Wrong EXE version, or hook site shifted."
    )


def _verify_fragments_free(data: bytes) -> None:
    for name, file_off, va in (
        ("FRAG_A", FRAG_A_FILE, FRAG_A_VA),
        ("FRAG_FORCE", FRAG_FORCE_FILE, FRAG_FORCE_VA),
        ("FRAG_C", FRAG_C_FILE, FRAG_C_VA),
    ):
        if not _frag_is_untouched(data, file_off):
            raise RuntimeError(
                f"[deadside_guns] {name} at file 0x{file_off:X} / VA 0x{va:X} "
                f"is not the expected all-0xCC filler -- conflict with another "
                f"patch, or wrong EXE version.\n"
                f"  Found: {data[file_off:file_off+FRAG_SIZE].hex(' ')}"
            )


def dump_patch_state(exe_path: str) -> None:
    """Print a human-readable diff of the hook site and all three fragments."""
    data = Path(exe_path).read_bytes()

    hook = data[HOOK_FILE_OFF: HOOK_FILE_OFF + HOOK_SIZE]
    hook_patch = build_hook_patch()
    status = ("PATCHED ✓" if hook == hook_patch
              else "VANILLA" if hook == HOOK_VANILLA
              else "UNEXPECTED")
    print(f"\n[deadside_guns] Hook  file:0x{HOOK_FILE_OFF:X}  VA:0x{HOOK_VA:X}")
    print(f"  Expected (patched) : {hook_patch.hex(' ')}")
    print(f"  Expected (vanilla) : {HOOK_VANILLA.hex(' ')}")
    print(f"  Actual             : {hook.hex(' ')}  [{status}]")

    for name, file_off, va, builder in (
        ("FRAG_A", FRAG_A_FILE, FRAG_A_VA, build_frag_a),
        ("FRAG_FORCE", FRAG_FORCE_FILE, FRAG_FORCE_VA, build_frag_force),
        ("FRAG_C", FRAG_C_FILE, FRAG_C_VA, build_frag_c),
    ):
        region = data[file_off: file_off + FRAG_SIZE]
        expected = builder()
        frag_status = ("MATCHES ✓" if region == expected
                        else "UNTOUCHED (0xCC)" if _frag_is_untouched(data, file_off)
                        else "UNEXPECTED")
        print(f"\n[deadside_guns] {name}  file:0x{file_off:X}  VA:0x{va:X}")
        print(f"  Expected: {expected.hex(' ')}")
        print(f"  Actual  : {region.hex(' ')}  [{frag_status}]")
    print()


def patch_deadside_guns(exe_path: str, *, dry_run: bool = False) -> None:
    """Apply the Deadside Guns force-on patch in-place to *exe_path*."""
    path = Path(exe_path)
    if not path.exists():
        raise FileNotFoundError(f"[deadside_guns] EXE not found: {exe_path}")

    data = bytearray(path.read_bytes())
    data_bytes = bytes(data)

    hook = data_bytes[HOOK_FILE_OFF: HOOK_FILE_OFF + HOOK_SIZE]
    if hook == build_hook_patch():
        raise RuntimeError("[deadside_guns] EXE already has this patch applied.")
    if hook != HOOK_VANILLA:
        raise RuntimeError(
            f"[deadside_guns] Vanilla verify failed at hook site "
            f"(file 0x{HOOK_FILE_OFF:X} / VA 0x{HOOK_VA:X}).\n"
            f"  Expected : {HOOK_VANILLA.hex(' ')}\n"
            f"  Got      : {hook.hex(' ')}\n"
            "  Wrong EXE version, or partially patched."
        )
    print("  [deadside_guns] Vanilla hook bytes verified ✓")

    _verify_fragments_free(data_bytes)
    print("  [deadside_guns] All 3 fragment slots verified free ✓")

    frag_a = build_frag_a()
    frag_force = build_frag_force()
    frag_c = build_frag_c()
    hook_patch = build_hook_patch()

    if dry_run:
        print("  [deadside_guns] DRY RUN -- no bytes written")
        return

    data[FRAG_A_FILE: FRAG_A_FILE + FRAG_SIZE] = frag_a
    print(f"  [deadside_guns] FRAG_A      -> file:0x{FRAG_A_FILE:X}  VA:0x{FRAG_A_VA:X}")

    data[FRAG_FORCE_FILE: FRAG_FORCE_FILE + FRAG_SIZE] = frag_force
    print(f"  [deadside_guns] FRAG_FORCE  -> file:0x{FRAG_FORCE_FILE:X}  VA:0x{FRAG_FORCE_VA:X}")

    data[FRAG_C_FILE: FRAG_C_FILE + FRAG_SIZE] = frag_c
    print(f"  [deadside_guns] FRAG_C      -> file:0x{FRAG_C_FILE:X}  VA:0x{FRAG_C_VA:X}")

    data[HOOK_FILE_OFF: HOOK_FILE_OFF + HOOK_SIZE] = hook_patch
    print(f"  [deadside_guns] Hook        -> file:0x{HOOK_FILE_OFF:X}  VA:0x{HOOK_VA:X}")

    path.write_bytes(data)
    print("  [deadside_guns] Deadside Guns secret forced ON permanently")


def revert_deadside_guns(exe_path: str, *, dry_run: bool = False) -> None:
    """
    Restore the hook site to vanilla and the three fragment slots to their
    original 0xCC filler. Safe to call even if the fragments were never
    written (e.g. hook alone somehow ended up patched) -- each region is
    restored independently based on its own current state.
    """
    path = Path(exe_path)
    if not path.exists():
        raise FileNotFoundError(f"[deadside_guns] EXE not found: {exe_path}")

    data = bytearray(path.read_bytes())
    data_bytes = bytes(data)

    hook = data_bytes[HOOK_FILE_OFF: HOOK_FILE_OFF + HOOK_SIZE]
    hook_patch = build_hook_patch()
    if hook == HOOK_VANILLA:
        print("  [deadside_guns] Hook already vanilla -- nothing to revert there")
    elif hook == hook_patch:
        if not dry_run:
            data[HOOK_FILE_OFF: HOOK_FILE_OFF + HOOK_SIZE] = HOOK_VANILLA
        print(f"  [deadside_guns] Hook        -> restored vanilla @ file:0x{HOOK_FILE_OFF:X}")
    else:
        raise RuntimeError(
            f"[deadside_guns] Hook site is neither vanilla nor our patch -- "
            f"refusing to touch it.\n  Got: {hook.hex(' ')}"
        )

    fill = bytes([_FRAG_FILL_BYTE]) * FRAG_SIZE
    for name, file_off, va, builder in (
        ("FRAG_A", FRAG_A_FILE, FRAG_A_VA, build_frag_a),
        ("FRAG_FORCE", FRAG_FORCE_FILE, FRAG_FORCE_VA, build_frag_force),
        ("FRAG_C", FRAG_C_FILE, FRAG_C_VA, build_frag_c),
    ):
        region = data_bytes[file_off: file_off + FRAG_SIZE]
        expected = builder()
        if region == fill:
            print(f"  [deadside_guns] {name}      -> already 0xCC, nothing to revert")
        elif region == expected:
            if not dry_run:
                data[file_off: file_off + FRAG_SIZE] = fill
            print(f"  [deadside_guns] {name}      -> restored 0xCC @ file:0x{file_off:X}")
        else:
            raise RuntimeError(
                f"[deadside_guns] {name} at file 0x{file_off:X} / VA 0x{va:X} is "
                f"neither our patch nor 0xCC filler -- refusing to touch it.\n"
                f"  Got: {region.hex(' ')}"
            )

    if dry_run:
        print("  [deadside_guns] DRY RUN -- no bytes written")
        return

    path.write_bytes(data)
    print("  [deadside_guns] Reverted to vanilla")


# ── Convenience wrapper (used by patcher.py / ap_patcher.py) ─────────────────

def apply_deadside_guns_patch(exe_path: str, *, dry_run: bool = False) -> bool:
    """
    Returns True on success, False on skip/error.
    Skips silently if the patch is already applied.
    """
    if not Path(exe_path).exists():
        print(f"  [deadside_guns] EXE not found: {exe_path} -- skipping")
        return False
    try:
        already = verify_deadside_guns(exe_path)
    except RuntimeError as e:
        print(f"  [deadside_guns] WARNING: {e}")
        return False
    if already:
        print("  [deadside_guns] Patch already applied -- skipping")
        return True
    try:
        patch_deadside_guns(exe_path, dry_run=dry_run)
        return True
    except (RuntimeError, FileNotFoundError) as e:
        print(f"  [deadside_guns] ERROR: {e}")
        return False


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Force-enable the 'I like Dead Side Guns' secret in Shadow Man Remastered"
    )
    parser.add_argument("exe", help="Path to thoth_x64.exe or thoth_x64_patched.exe")
    parser.add_argument("--dry-run", action="store_true",
                         help="Verify and build fragments/hook but write nothing")
    parser.add_argument("--verify", action="store_true",
                         help="Check patch status and exit")
    parser.add_argument("--dump", action="store_true",
                         help="Dump hook and fragment bytes for debugging")
    parser.add_argument("--revert", action="store_true",
                         help="Restore hook + fragments to vanilla/0xCC")
    args = parser.parse_args()

    if args.dump:
        dump_patch_state(args.exe)
        sys.exit(0)

    if args.revert:
        try:
            revert_deadside_guns(args.exe, dry_run=args.dry_run)
            print("Done.")
        except (RuntimeError, FileNotFoundError) as e:
            print(f"ERROR: {e}")
            sys.exit(1)
        sys.exit(0)

    if args.verify:
        try:
            applied = verify_deadside_guns(args.exe)
            print(f"Deadside Guns patch: {'APPLIED' if applied else 'VANILLA'}")
            sys.exit(0)
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(2)

    try:
        patch_deadside_guns(args.exe, dry_run=args.dry_run)
        print("Done.")
    except (RuntimeError, FileNotFoundError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)
