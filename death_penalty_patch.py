"""
death_penalty_patch.py
======================
Patches thoth_x64.exe to reduce max health by step*1000 on each death,
with a floor of step*1000 (if max health is already at or below the floor,
no further reduction).

ADDRESS MAPPING
---------------
The .text section has vaddr=0x1000, raw offset=0x400, so:
  file_offset = VA - IMAGE_BASE - SECTION_DELTA   (SECTION_DELTA = 0xC00)
All addresses below are expressed as Ghidra VAs; file offsets are derived.

HOW IT WORKS
------------
A code cave is injected into the zero-padded region immediately after the
gad-pickup cave (DEATH_CAVE_VA = 0x14064A3B2, 16 bytes after the gad cave end).

The hook fires at the death-clamp point — the instruction that writes 0 to
current health when it would go negative.  Instead of falling through to the
original EB 17 short jump, we divert to our cave, which:

  1. Performs the original clamped-to-zero write (MOV [RBX+0x20], EDI)
  2. Loads max health into EDI and subtracts step*1000
  3. If the result is still >= step*1000 (floor), writes it back
  4. Otherwise clamps to step*1000 (the floor) and writes that
  5. Restores EDI to 0 (as the caller left it via XOR EDI,EDI before the hook)
  6. Jumps to the target of the original EB 17

Cave layout (35 bytes at VA 0x14064A3B2):
  +00  89 7B 20           MOV [RBX+0x20], EDI      ; original write (current hp = 0, EDI=0)
  +03  8B 7B 1C           MOV EDI, [RBX+0x1C]      ; read max health into EDI
  +06  81 EF xx xx xx xx  SUB EDI, step*1000        ; tentative reduction
  +0C  81 FF xx xx xx xx  CMP EDI, step*1000        ; still >= floor?
  +12  7D xx              JGE +? (-> write)          ; if yes, write reduced value
  +14  BF xx xx xx xx     MOV EDI, step*1000        ; clamp to floor
  +19  89 7B 1C           MOV [RBX+0x1C], EDI      ; write new max health  (write:)
  +1C  33 FF              XOR EDI, EDI              ; restore EDI=0 (as caller left it)
  +1E  E9 xx xx xx xx     JMP DEATH_RETURN_VA       ; target of original EB 17

Hook site (Ghidra VA 0x14032D1B9):
  Vanilla : 89 7B 20 EB 17   (MOV [RBX+0x20], EDI; JMP +0x17)
  Patched : E9 xx xx xx xx   (JMP -> cave; bytes computed from addresses)
"""

import struct
from pathlib import Path

# ── Addresses ─────────────────────────────────────────────────────────────────

IMAGE_BASE    = 0x140000000
SECTION_DELTA = 0xC00   # .text: vaddr=0x1000, raw=0x400  =>  delta = 0xC00


def _va_to_file(va: int) -> int:
    return va - IMAGE_BASE - SECTION_DELTA


def _file_to_va(file_off: int) -> int:
    return file_off + IMAGE_BASE + SECTION_DELTA


# Hook site — Ghidra VA confirmed; file offset derived
DEATH_HOOK_VA       = 0x14032D1B9
DEATH_HOOK_FILE_OFF = _va_to_file(DEATH_HOOK_VA)
DEATH_HOOK_VANILLA  = bytes([0x89, 0x7B, 0x20, 0xEB, 0x17])

# Code cave — 16-byte buffer past the gad-pickup cave end (0x14064A3A2)
DEATH_CAVE_VA       = 0x14064A3B2
DEATH_CAVE_FILE_OFF = _va_to_file(DEATH_CAVE_VA)

# Return target — destination of the original EB 17 short jump, confirmed
DEATH_RETURN_VA     = 0x14032D1D5

# Hook patch bytes — computed from addresses so a typo can never mis-redirect
_hook_rip        = DEATH_HOOK_VA + 5
_hook_rel        = DEATH_CAVE_VA - _hook_rip
DEATH_HOOK_PATCH = bytes([0xE9]) + struct.pack('<i', _hook_rel)

# Constants
MAX_HEALTH_VANILLA  = 5000
DEATH_PENALTY_STEP  = 1000
DEATH_PENALTY_FLOOR = 1000


# ── Cave builder ──────────────────────────────────────────────────────────────

def build_death_penalty_cave(step: int = 1) -> bytes:
    step = max(1, min(10, int(step)))
    penalty = step * 1000
    floor   = 1000

    cave = bytearray()
    cave_va = DEATH_CAVE_VA

    # +00  MOV [RBX+0x20], EDI   -- original clamped-to-zero write (EDI=0)
    cave += bytes([0x89, 0x7B, 0x20])
    # +03  MOV EDI, [RBX+0x1C]   -- read max health
    cave += bytes([0x8B, 0x7B, 0x1C])
    # +06  SUB EDI, penalty
    cave += bytes([0x81, 0xEF]) + struct.pack('<I', penalty)
    # +0C  CMP EDI, floor
    cave += bytes([0x81, 0xFF]) + struct.pack('<I', floor)
    # +12  JGE write              -- if still >= floor, write as-is
    jge_pos = len(cave)
    cave += bytes([0x7D, 0x00])
    # +14  MOV EDI, floor         -- clamp to floor
    cave += bytes([0xBF]) + struct.pack('<I', floor)
    # +19  write: MOV [RBX+0x1C], EDI
    write_pos = len(cave)
    cave += bytes([0x89, 0x7B, 0x1C])
    # +1C  XOR EDI, EDI           -- restore EDI=0
    cave += bytes([0x33, 0xFF])
    # +1E  JMP DEATH_RETURN_VA
    jmp_pos = len(cave)
    rip = cave_va + jmp_pos + 5
    cave += bytes([0xE9]) + struct.pack('<i', DEATH_RETURN_VA - rip)

    # Fix up JGE: jump to write_pos
    cave[jge_pos + 1] = write_pos - (jge_pos + 2)

    assert len(cave) == 35, f"Cave size mismatch: {len(cave)}"
    return bytes(cave)

# ── Patch / verify helpers ────────────────────────────────────────────────────

def _verify_vanilla(data: bytes) -> None:
    """Raise RuntimeError if the hook site doesn't look vanilla (or already patched)."""
    actual = data[DEATH_HOOK_FILE_OFF : DEATH_HOOK_FILE_OFF + 5]
    if actual == DEATH_HOOK_PATCH:
        raise RuntimeError(
            "[death_penalty] EXE already has the death-penalty patch applied."
        )
    if actual != DEATH_HOOK_VANILLA:
        raise RuntimeError(
            f"[death_penalty] Vanilla verify failed at hook site "
            f"(file offset 0x{DEATH_HOOK_FILE_OFF:X} / VA 0x{DEATH_HOOK_VA:X}).\n"
            f"  Expected : {DEATH_HOOK_VANILLA.hex(' ')}\n"
            f"  Got      : {actual.hex(' ')}\n"
            "  Wrong EXE version, or partially patched."
        )


def dump_patch_state(exe_path: str) -> None:
    """
    Print a human-readable diff of the hook site and cave region for debugging.
    Confirms what bytes are actually in the EXE at each location, and whether
    the computed JMP targets resolve correctly.
    """
    data = Path(exe_path).read_bytes()

    # ── Hook site ─────────────────────────────────────────────────────────────
    hook = data[DEATH_HOOK_FILE_OFF : DEATH_HOOK_FILE_OFF + 5]
    hook_status = (
        "PATCHED ✓" if hook == DEATH_HOOK_PATCH
        else "VANILLA" if hook == DEATH_HOOK_VANILLA
        else "UNEXPECTED"
    )
    print(f"\n[death_penalty] Hook site  file:0x{DEATH_HOOK_FILE_OFF:X}  VA:0x{DEATH_HOOK_VA:X}")
    print(f"  Expected (patched) : {DEATH_HOOK_PATCH.hex(' ')}")
    print(f"  Actual             : {hook.hex(' ')}  [{hook_status}]")

    if hook[0] == 0xE9:
        rel = struct.unpack('<i', hook[1:5])[0]
        resolved = DEATH_HOOK_VA + 5 + rel
        match = "✓ -> cave" if resolved == DEATH_CAVE_VA else f"✗ -> 0x{resolved:X} (expected 0x{DEATH_CAVE_VA:X})"
        print(f"  JMP resolves to    : 0x{resolved:X}  [{match}]")

    # ── Cave region ───────────────────────────────────────────────────────────
    expected_cave = build_death_penalty_cave(step=1)   # step=1 as reference
    cave_bytes = data[DEATH_CAVE_FILE_OFF : DEATH_CAVE_FILE_OFF + len(expected_cave)]
    cave_empty = cave_bytes == bytes(len(expected_cave))
    print(f"\n[death_penalty] Cave region  file:0x{DEATH_CAVE_FILE_OFF:X}  VA:0x{DEATH_CAVE_VA:X}  ({len(expected_cave)} bytes)")
    print(f"  Contents: {cave_bytes.hex(' ')}")
    if cave_empty:
        print("  Status  : EMPTY (patch not written, or wrong EXE)")
    elif cave_bytes == expected_cave:
        print("  Status  : MATCHES step=1 cave ✓")
    else:
        print("  Status  : WRITTEN (non-zero, step may differ from 1 -- check bytes manually)")

    # ── Return JMP inside cave (at cave offset +0x1E) ─────────────────────────
    if not cave_empty and len(cave_bytes) > 0x1E and cave_bytes[0x1E] == 0xE9:
        rel2 = struct.unpack('<i', cave_bytes[0x1F:0x23])[0]
        rip2 = DEATH_CAVE_VA + 0x1E + 5
        resolved2 = rip2 + rel2
        match2 = "✓" if resolved2 == DEATH_RETURN_VA else f"✗ expected 0x{DEATH_RETURN_VA:X}"
        print(f"  Return JMP -> 0x{resolved2:X}  [{match2}]")

    print()


def verify_death_penalty(exe_path: str) -> bool:
    """Return True if patch is applied, False if vanilla, raise on unexpected bytes."""
    data = Path(exe_path).read_bytes()
    actual = data[DEATH_HOOK_FILE_OFF : DEATH_HOOK_FILE_OFF + 5]
    if actual == DEATH_HOOK_PATCH:
        return True
    if actual == DEATH_HOOK_VANILLA:
        return False
    raise RuntimeError(
        f"[death_penalty] Unexpected bytes at hook site: {actual.hex(' ')}"
    )


# ── Main patch entry point ────────────────────────────────────────────────────

def patch_death_penalty(exe_path: str, *, step: int = 1, dry_run: bool = False) -> None:
    """
    Apply the death-penalty patch in-place to *exe_path*.

    *step* is 1-10; penalty = step * 1000, floor = step * 1000.
    Raises RuntimeError if vanilla verification fails.
    """
    step = max(1, min(10, int(step)))
    path = Path(exe_path)
    if not path.exists():
        raise FileNotFoundError(f"[death_penalty] EXE not found: {exe_path}")

    data = bytearray(path.read_bytes())

    # Vanilla check
    _verify_vanilla(bytes(data))
    print(f"  [death_penalty] Vanilla bytes verified ✓")

    cave = build_death_penalty_cave(step=step)

    # Verify cave region is zeroed (unused)
    cave_region = bytes(data[DEATH_CAVE_FILE_OFF : DEATH_CAVE_FILE_OFF + len(cave)])
    if cave_region != bytes(len(cave)):
        raise RuntimeError(
            f"[death_penalty] Cave region at 0x{DEATH_CAVE_FILE_OFF:X} is not zero-padded "
            f"-- conflict with another patch?\n"
            f"  Found: {cave_region.hex(' ')}"
        )

    if dry_run:
        print("  [death_penalty] DRY RUN -- no bytes written")
        return

    # Write cave
    data[DEATH_CAVE_FILE_OFF : DEATH_CAVE_FILE_OFF + len(cave)] = cave
    print(f"  [death_penalty] Cave ({len(cave)} bytes) -> file:0x{DEATH_CAVE_FILE_OFF:X}  VA:0x{DEATH_CAVE_VA:X}")

    # Write hook
    data[DEATH_HOOK_FILE_OFF : DEATH_HOOK_FILE_OFF + 5] = DEATH_HOOK_PATCH
    print(f"  [death_penalty] Hook -> file:0x{DEATH_HOOK_FILE_OFF:X}  VA:0x{DEATH_HOOK_VA:X}")

    path.write_bytes(data)
    penalty = step * 1000
    print(f"  [death_penalty] Max health -{penalty} on death (floor: {penalty})")


# ── Convenience wrapper (used by patcher.py) ─────────────────────────────────

def apply_death_penalty_patch(exe_path: str, *, step: int = 1, dry_run: bool = False) -> bool:
    """
    Returns True on success, False on skip/error.
    Skips silently if the patch is already applied.

    *step* is 1-10; penalty = step * 1000, floor = step * 1000.
    """
    if not Path(exe_path).exists():
        print(f"  [death_penalty] EXE not found: {exe_path} -- skipping")
        return False
    try:
        already = verify_death_penalty(exe_path)
    except RuntimeError as e:
        print(f"  [death_penalty] WARNING: {e}")
        return False
    if already:
        print("  [death_penalty] Patch already applied -- skipping")
        return True
    try:
        patch_death_penalty(exe_path, step=step, dry_run=dry_run)
        return True
    except (RuntimeError, FileNotFoundError) as e:
        print(f"  [death_penalty] ERROR: {e}")
        return False


# ── Diagnostic: scan EXE for hook candidates ─────────────────────────────────

def find_death_hook(exe_path: str) -> None:
    """
    Scan the EXE for candidate death-clamp hook sites and print their
    file offsets + VAs.  Run this on the vanilla EXE when DEATH_HOOK_VA
    needs to be (re)calibrated.
    """
    data = Path(exe_path).read_bytes()

    # Pass 1: exact vanilla pattern
    exact = DEATH_HOOK_VANILLA
    hits = []
    start = 0
    while True:
        idx = data.find(exact, start)
        if idx == -1:
            break
        hits.append(idx)
        start = idx + 1

    print(f"\n[find_hook] Exact pattern {exact.hex(' ')} (MOV [RBX+0x20],EDI ; JMP+0x17):")
    if hits:
        for h in hits:
            ctx = data[h - 4 : h + 12].hex(' ')
            print(f"  file:0x{h:X}  VA:0x{_file_to_va(h):X}  context: ...{ctx}...")
    else:
        print("  (none found)")

    # Pass 2: MOV [RBX+disp8], EDI followed by EB 17 (any displacement)
    hits2 = []
    for i in range(len(data) - 4):
        if data[i] == 0x89 and data[i+1] == 0x7B and data[i+3] == 0xEB and data[i+4] == 0x17:
            if i not in hits:
                hits2.append(i)
    print(f"\n[find_hook] Variant MOV [RBX+?],EDI ; EB 17:")
    if hits2:
        for h in hits2:
            ctx = data[h - 4 : h + 12].hex(' ')
            print(f"  file:0x{h:X}  VA:0x{_file_to_va(h):X}  disp=0x{data[h+2]:02X}  context: ...{ctx}...")
    else:
        print("  (none found)")

    # Pass 3: all MOV [RBX+0x20], EDI occurrences
    hits3 = []
    p3 = bytes([0x89, 0x7B, 0x20])
    start = 0
    while True:
        idx = data.find(p3, start)
        if idx == -1:
            break
        if idx not in hits and idx not in hits2:
            hits3.append(idx)
        start = idx + 1
    print(f"\n[find_hook] All 89 7B 20 (MOV [RBX+0x20],EDI) occurrences:")
    if hits3:
        for h in hits3[:20]:
            ctx = data[h - 4 : h + 10].hex(' ')
            print(f"  file:0x{h:X}  VA:0x{_file_to_va(h):X}  context: ...{ctx}...")
        if len(hits3) > 20:
            print(f"  ... ({len(hits3) - 20} more)")
    else:
        print("  (none found)")

    # Pass 4: what's at the current hook site
    actual = data[DEATH_HOOK_FILE_OFF : DEATH_HOOK_FILE_OFF + 8]
    print(f"\n[find_hook] Bytes at DEATH_HOOK_FILE_OFF (0x{DEATH_HOOK_FILE_OFF:X} / VA 0x{DEATH_HOOK_VA:X}):")
    print(f"  {actual.hex(' ')}")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Apply death-penalty patch to Shadow Man Remastered EXE"
    )
    parser.add_argument("exe", help="Path to thoth_x64.exe (vanilla or already patched)")
    parser.add_argument("--step", type=int, default=1,
                        help="Penalty step 1-10 (penalty = step * 1000, floor = step * 1000)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Verify and build cave but write nothing")
    parser.add_argument("--verify", action="store_true",
                        help="Check patch status and exit")
    parser.add_argument("--dump", action="store_true",
                        help="Dump hook and cave bytes for debugging")
    parser.add_argument("--find-hook", action="store_true",
                        help="Scan EXE for death-clamp hook candidates (run on vanilla EXE)")
    args = parser.parse_args()

    if args.dump:
        dump_patch_state(args.exe)
        sys.exit(0)

    if args.find_hook:
        find_death_hook(args.exe)
        sys.exit(0)

    if args.verify:
        try:
            applied = verify_death_penalty(args.exe)
            print(f"Death penalty patch: {'APPLIED' if applied else 'VANILLA'}")
            sys.exit(0)
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(2)

    try:
        patch_death_penalty(args.exe, step=args.step, dry_run=args.dry_run)
        print("Done.")
    except (RuntimeError, FileNotFoundError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)
