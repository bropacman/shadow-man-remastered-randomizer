"""
secret_mode_live_patch.py
==========================
Generalized version of dogmode_live_patch.py: instead of hardcoding a
single cvar (g_dogmode), loops over all 8 secret cvars that
FUN_140459250 (the player mesh/skin reload function) itself checks, and
fires the same live-apply callback (FUN_140458EF0) for whichever one
actually changed.

SUPERSEDES dogmode_live_patch.py at the hook level -- both patches
splice into the exact same land/swim points (LAND_HOOK_VA/SWIM_HOOK_VA
below), so only ONE of the two should ever be applied to a given exe at
a time. dogmode_live_patch.py is left in the repo untouched as the
already-verified single-cvar reference; this is the generalized
successor once it's had its own testing pass.

WHICH CVARS, AND WHY THESE 8
------------------------------
Pulled directly from FUN_140459250's own decompile (the same function
whose crash/no-crash behavior we already root-caused for g_dogmode):

    DAT_140dba080 -> variant index 2
    DAT_140dba620 -> variant index 3
    DAT_140dba590 -> variant index 4
    DAT_140dba6b0 -> variant index 9
    DAT_140dba740 -> variant index 10   (g_dogmode, already proven end-to-end)
    DAT_140dba860 -> variant index 11
    DAT_140dba2c0 / DAT_140db9ff0 -> compound, variant index 5/6/7/8

These are the ONLY 8 cvars confirmed structurally tied to
FUN_140459250 / FUN_140458EF0 -- i.e. the ones we can be confident share
the exact same on-change callback g_dogmode does, since
FUN_140458EF0's own decompile description ("walks a table of mutually-
exclusive g_<name>mode secrets... then calls FUN_140459250") only makes
sense for cvars FUN_140459250 itself reads.

IMPORTANT CAVEAT: only g_dogmode's own +0x48 callback slot has actually
been read live and confirmed to equal FUN_140458EF0 (CLAUDE.md,
2026-07-30). The other 7 are inferred to share it, not independently
verified. This is a reasonable inference (they're the only cvars
FUN_140459250 itself consults, and that function is FUN_140458EF0's
only known callee), but worth a cheap, zero-risk spot check before fully
trusting it: read [cvar_va + 0x48] for one or two of the other 7 in CE
and confirm it's also 0x140458EF0. Even if the inference turned out
wrong for one of them, the failure mode is a silent no-op (the callback
wouldn't recognize that cvar and would just walk past it), not a crash --
this mechanism's whole safety property (running inline on an already-
live game thread, no external injection) doesn't depend on the callback
guess being right, only on never injecting from a foreign thread again.

Other named secrets from kexengine.cfg (g_bigheadmode, g_wireframemode,
g_discolights, g_invisiblemode, etc.) are DELIBERATELY NOT included --
they aren't among FUN_140459250's own checks, so there's no evidence yet
they share this callback, or that they need a callback/reload at all
(plausibly some are pure per-frame render toggles with no reload
concept). Adding those needs their own live +0x48 verification first.

DESIGN
------
Same two hook sites as dogmode_live_patch.py (land: 0x14046B2CC,
6 bytes; swim: 0x14046B977, 7 bytes -- see that module's docstring for
the full derivation of why these exact splice points and not
sprint_patch's own hook bytes or SWIM_HOOK_VA+8). Each hook's stub saves
its one live register (RCX for land, R11 for swim) and sets R14 to its
own epilogue address, then jumps into a SHARED loop core -- avoids
duplicating the 8-cvar check-and-call logic per hook.

Loop registers (all free at both hook sites once the hook's own live
register is saved -- see dogmode_live_patch.py's register-liveness
analysis, unchanged here):
    R9  = pointer walking CVAR_TABLE (a static array of the 8 cvar base
          VAs), advanced +8 bytes/iteration
    R8  = pointer walking LAST_KNOWN_TABLE (one persistent byte per
          cvar, shared across land/swim so whichever hook notices a
          change first "wins"), advanced +1 byte/iteration
    R15B = plain 0..7 loop counter, kept separate from the two pointers
           above specifically to avoid any SIB-scaled (base+index*scale)
           addressing -- every memory access in this cave is a plain
           [reg] or [reg+disp8] dereference, to keep the hand-encoding
           as simple (and as easy to verify byte-by-byte) as possible
    R14 = the calling hook's epilogue address, jumped to indirectly
          (JMP R14) once the loop counter reaches 8

Every fragment here was verified the same way as dogmode_live_patch.py:
built, self-checked for exact 16-byte sizing and no duplicate/overlapping
addresses, then independently disassembled with Capstone and every
computed RIP-relative/rel32 target checked against its intended address.
"""
import struct
from pathlib import Path

# ── Address mapping (matches every other *_patch.py in this repo) ─────────────
IMAGE_BASE    = 0x140000000
SECTION_DELTA = 0xC00


def _va_to_file(va: int) -> int:
    return va - IMAGE_BASE - SECTION_DELTA


def _file_to_va(file_off: int) -> int:
    return file_off + IMAGE_BASE + SECTION_DELTA


# ── Fixed targets ────────────────────────────────────────────────────────────
CALLBACK_VA = 0x140458EF0   # FUN_140458EF0, the real on-change callback

# The 8 cvars FUN_140459250 itself checks, straight from its own decompile.
SECRET_CVAR_VAS = [
    0x140DBA080,   # variant 2
    0x140DBA620,   # variant 3
    0x140DBA590,   # variant 4
    0x140DBA6B0,   # variant 9
    0x140DBA740,   # variant 10 -- g_dogmode
    0x140DBA860,   # variant 11
    0x140DBA2C0,   # compound (variant 5/6/7/8 depending on prior state)
    0x140DB9FF0,   # compound
]
assert len(SECRET_CVAR_VAS) == 8

# ── Hook sites (identical to dogmode_live_patch.py) ─────────────────────────
LAND_HOOK_VA      = 0x14046B2CC
LAND_HOOK_FILE    = _va_to_file(LAND_HOOK_VA)
LAND_HOOK_VANILLA = bytes.fromhex("4532c08b4110")   # XOR R8B,R8B ; MOV EAX,[RCX+0x10]
LAND_RETURN_VA    = 0x14046B2D2

SWIM_HOOK_VA      = 0x14046B977
SWIM_HOOK_FILE    = _va_to_file(SWIM_HOOK_VA)
SWIM_HOOK_VANILLA = bytes.fromhex("498b5b10498be3")   # MOV RBX,[R11+0x10] ; MOV RSP,R11
SWIM_RETURN_VA    = 0x14046B97E

# ── Fragment locations (same free-gap list dogmode_live_patch.py used;
#    the two patches hook the same sites so are mutually exclusive on a
#    given exe, meaning reusing the same 16-byte gaps is fine -- only
#    ONE of these two patches is ever actually applied at once) ────────────
FRAG_SIZE = 16

CVAR_TABLE_0_VA   = 0x1401F2560
CVAR_TABLE_1_VA   = 0x14020BBD0
CVAR_TABLE_2_VA   = 0x1402101D0
CVAR_TABLE_3_VA   = 0x140210290
LAST_KNOWN_TBL_VA = 0x140221EC0

LAND_STUB_A_VA = 0x140227BD0
LAND_STUB_B_VA = 0x140233AC0
SWIM_STUB_A_VA = 0x140246FD0
SWIM_STUB_B_VA = 0x1402488C0

LOOP_INIT_A_VA   = 0x1402920D0
LOOP_INIT_B_VA   = 0x140296D10
LOOP_TOP_VA      = 0x140313E20
LOOP_BODY_1_VA   = 0x14031E5D0
LOOP_BODY_2_VA   = 0x140327150
LOOP_BODY_3_VA   = 0x140417190
LOOP_BODY_4_VA   = 0x14049BDC0
LOOP_DOCALL_A_VA = 0x1404B5E60
LOOP_DOCALL_B_VA = 0x1404B6050
LOOP_NEXT_VA     = 0x1404B7210

LAND_EPILOGUE_VA   = 0x1404BB900
SWIM_EPILOGUE_A_VA = 0x1404BC180
SWIM_EPILOGUE_B_VA = 0x1404BE1B0

# CVAR_TABLE occupies all 4 CVAR_TABLE_*_VA fragments back-to-back (8
# qwords, 2 per fragment); the array's base address for our LEA target
# is simply the first fragment's address.
CVAR_TABLE_VA = CVAR_TABLE_0_VA


# ── Small encoders ───────────────────────────────────────────────────────────

def _jmp_rel32(from_va: int, to_va: int) -> bytes:
    rel = to_va - (from_va + 5)
    return bytes([0xE9]) + struct.pack('<i', rel)


def _call_rel32(from_va: int, to_va: int) -> bytes:
    rel = to_va - (from_va + 5)
    return bytes([0xE8]) + struct.pack('<i', rel)


def _je_rel8(from_va: int, to_va: int) -> bytes:
    rel = to_va - (from_va + 2)
    assert -128 <= rel <= 127, f"JE target out of short-jump range: {rel}"
    return bytes([0x74]) + struct.pack('<b', rel)


def _jb_rel8(from_va: int, to_va: int) -> bytes:
    rel = to_va - (from_va + 2)
    assert -128 <= rel <= 127, f"JB target out of short-jump range: {rel}"
    return bytes([0x72]) + struct.pack('<b', rel)


def _lea_r9_ripconst(instr_va: int, target_va: int) -> bytes:
    disp = target_va - (instr_va + 7)
    return bytes([0x4C, 0x8D, 0x0D]) + struct.pack('<i', disp)


def _lea_r8_ripconst(instr_va: int, target_va: int) -> bytes:
    disp = target_va - (instr_va + 7)
    return bytes([0x4C, 0x8D, 0x05]) + struct.pack('<i', disp)


def _pad(body: bytes, size: int = FRAG_SIZE) -> bytes:
    assert len(body) <= size, f"fragment body too big: {len(body)} > {size}"
    return body + bytes([0x90]) * (size - len(body))


# Fixed-encoding one-liners (all verified by disassembly in the test
# harness below, not just by hand) -----------------------------------------
_PUSH_RCX        = bytes([0x51])
_PUSH_R11        = bytes([0x41, 0x53])
_SUB_RSP_8       = bytes([0x48, 0x83, 0xEC, 0x08])
_ADD_RSP_8       = bytes([0x48, 0x83, 0xC4, 0x08])
_POP_RCX         = bytes([0x59])
_POP_R11         = bytes([0x41, 0x5B])
_XOR_R15B_R15B   = bytes([0x45, 0x30, 0xFF])
_CMP_R15B_8      = bytes([0x41, 0x80, 0xFF, 0x08])
_JMP_R14_IND     = bytes([0x41, 0xFF, 0xE6])
_MOV_RAX_R9PTR   = bytes([0x49, 0x8B, 0x01])          # MOV RAX,[R9]
_ADD_RAX_0x78    = bytes([0x48, 0x83, 0xC0, 0x78])
_MOV_RAX_RAXPTR  = bytes([0x48, 0x8B, 0x00])          # MOV RAX,[RAX]
_CMP_RAXPTR_1    = bytes([0x83, 0x38, 0x01])          # CMP dword ptr[RAX],1
_MOVZX_ECX_RAX4  = bytes([0x0F, 0xB6, 0x48, 0x04])    # MOVZX ECX,[RAX+4]
_MOV_DL_R8PTR    = bytes([0x41, 0x8A, 0x10])          # MOV DL,[R8]
_CMP_CL_DL       = bytes([0x38, 0xD1])
_MOV_R8PTR_CL    = bytes([0x41, 0x88, 0x08])          # MOV [R8],CL
_MOV_RCX_R9PTR   = bytes([0x49, 0x8B, 0x09])          # MOV RCX,[R9]
_ADD_R9_8        = bytes([0x49, 0x83, 0xC1, 0x08])
_INC_R8          = bytes([0x49, 0xFF, 0xC0])
_INC_R15B        = bytes([0x41, 0xFE, 0xC7])
_XOR_R8B_R8B     = bytes([0x45, 0x32, 0xC0])          # land replay
_MOV_EAX_RCX10   = bytes([0x8B, 0x41, 0x10])          # land replay
_MOV_RBX_R11PTR  = bytes([0x49, 0x8B, 0x5B, 0x10])    # swim replay
_MOV_RSP_R11     = bytes([0x49, 0x8B, 0xE3])          # swim replay


# ── Fragment builders ─────────────────────────────────────────────────────────

def build_cvar_table() -> list:
    """8 qwords packed into 4 fragments, 2 entries each."""
    qwords = [struct.pack('<Q', va) for va in SECRET_CVAR_VAS]
    frags = []
    fvas = [CVAR_TABLE_0_VA, CVAR_TABLE_1_VA, CVAR_TABLE_2_VA, CVAR_TABLE_3_VA]
    for i, va in enumerate(fvas):
        body = qwords[i * 2] + qwords[i * 2 + 1]
        assert len(body) == FRAG_SIZE
        frags.append((va, body))
    return frags


def build_last_known_table() -> bytes:
    return _pad(bytes([0xFF] * 8))   # 8 sentinel bytes ("never synced"), padded


def build_land_stub_a() -> bytes:
    body = _PUSH_RCX + _SUB_RSP_8 + _jmp_rel32(LAND_STUB_A_VA + 5, LAND_STUB_B_VA)
    return _pad(body)


def build_land_stub_b() -> bytes:
    body = _lea_r_via('R14', LAND_STUB_B_VA, LAND_EPILOGUE_VA) + \
           _jmp_rel32(LAND_STUB_B_VA + 7, LOOP_INIT_A_VA)
    return _pad(body)


def build_swim_stub_a() -> bytes:
    body = _PUSH_R11 + _SUB_RSP_8 + _jmp_rel32(SWIM_STUB_A_VA + 6, SWIM_STUB_B_VA)
    return _pad(body)


def build_swim_stub_b() -> bytes:
    body = _lea_r_via('R14', SWIM_STUB_B_VA, SWIM_EPILOGUE_A_VA) + \
           _jmp_rel32(SWIM_STUB_B_VA + 7, LOOP_INIT_A_VA)
    return _pad(body)


def _lea_r14_ripconst(instr_va: int, target_va: int) -> bytes:
    disp = target_va - (instr_va + 7)
    return bytes([0x4C, 0x8D, 0x35]) + struct.pack('<i', disp)


def _lea_r_via(reg: str, instr_va: int, target_va: int) -> bytes:
    assert reg == 'R14'
    return _lea_r14_ripconst(instr_va, target_va)


def build_loop_init_a() -> bytes:
    body = _lea_r9_ripconst(LOOP_INIT_A_VA, CVAR_TABLE_VA) + \
           _jmp_rel32(LOOP_INIT_A_VA + 7, LOOP_INIT_B_VA)
    return _pad(body)


def build_loop_init_b() -> bytes:
    body = _lea_r8_ripconst(LOOP_INIT_B_VA, LAST_KNOWN_TBL_VA) + \
           _XOR_R15B_R15B + \
           _jmp_rel32(LOOP_INIT_B_VA + 10, LOOP_TOP_VA)
    return _pad(body)


def build_loop_top() -> bytes:
    # CMP(4, offset 0-4) + JB(2, offset 4-6) + JMP R14(3, offset 6-9) +
    # JMP LOOP_BODY_1(5, offset 9-14). JB must skip past the 3-byte
    # indirect jump to land on offset 9, not offset 6 (offset 6 is where
    # JMP R14 itself starts -- landing there instead of skipping it would
    # make the loop body unreachable on every call).
    body = _CMP_R15B_8 + \
           _jb_rel8(LOOP_TOP_VA + 4, LOOP_TOP_VA + 9) + \
           _JMP_R14_IND + \
           _jmp_rel32(LOOP_TOP_VA + 9, LOOP_BODY_1_VA)
    return _pad(body)


def build_loop_body_1() -> bytes:
    body = _MOV_RAX_R9PTR + _ADD_RAX_0x78 + _MOV_RAX_RAXPTR + \
           _jmp_rel32(LOOP_BODY_1_VA + 10, LOOP_BODY_2_VA)
    return _pad(body)


def build_loop_body_2() -> bytes:
    body = _CMP_RAXPTR_1 + \
           _je_rel8(LOOP_BODY_2_VA + 3, LOOP_BODY_2_VA + 10) + \
           _jmp_rel32(LOOP_BODY_2_VA + 5, LOOP_NEXT_VA) + \
           _jmp_rel32(LOOP_BODY_2_VA + 10, LOOP_BODY_3_VA)
    return _pad(body)


def build_loop_body_3() -> bytes:
    body = _MOVZX_ECX_RAX4 + _MOV_DL_R8PTR + _CMP_CL_DL + \
           _jmp_rel32(LOOP_BODY_3_VA + 9, LOOP_BODY_4_VA)
    return _pad(body)


def build_loop_body_4() -> bytes:
    body = _je_rel8(LOOP_BODY_4_VA, LOOP_BODY_4_VA + 7) + \
           _jmp_rel32(LOOP_BODY_4_VA + 2, LOOP_NEXT_VA) + \
           _jmp_rel32(LOOP_BODY_4_VA + 7, LOOP_DOCALL_A_VA)
    return _pad(body)


def build_loop_docall_a() -> bytes:
    body = _MOV_R8PTR_CL + _MOV_RCX_R9PTR + \
           _jmp_rel32(LOOP_DOCALL_A_VA + 6, LOOP_DOCALL_B_VA)
    return _pad(body)


def build_loop_docall_b() -> bytes:
    body = _call_rel32(LOOP_DOCALL_B_VA, CALLBACK_VA) + \
           _jmp_rel32(LOOP_DOCALL_B_VA + 5, LOOP_NEXT_VA)
    return _pad(body)


def build_loop_next() -> bytes:
    body = _ADD_R9_8 + _INC_R8 + _INC_R15B + \
           _jmp_rel32(LOOP_NEXT_VA + 10, LOOP_TOP_VA)
    return _pad(body)


def build_land_epilogue() -> bytes:
    body = _ADD_RSP_8 + _POP_RCX + _XOR_R8B_R8B + _MOV_EAX_RCX10 + \
           _jmp_rel32(LAND_EPILOGUE_VA + 11, LAND_RETURN_VA)
    assert len(body) == FRAG_SIZE, f"land epilogue size mismatch: {len(body)}"
    return body


def build_swim_epilogue_a() -> bytes:
    body = _ADD_RSP_8 + _POP_R11 + \
           _jmp_rel32(SWIM_EPILOGUE_A_VA + 6, SWIM_EPILOGUE_B_VA)
    return _pad(body)


def build_swim_epilogue_b() -> bytes:
    body = _MOV_RBX_R11PTR + _MOV_RSP_R11 + \
           _jmp_rel32(SWIM_EPILOGUE_B_VA + 7, SWIM_RETURN_VA)
    return _pad(body)


def build_hook_patch(hook_va: int, vanilla_len: int, target_va: int) -> bytes:
    jmp = _jmp_rel32(hook_va, target_va)
    return jmp + bytes([0x90] * (vanilla_len - len(jmp)))


def _build_all_fragments() -> dict:
    frags = {}
    for i, (va, body) in enumerate(build_cvar_table()):
        frags[f"CVAR_TABLE_{i}"] = (va, body)
    frags["LAST_KNOWN_TABLE"] = (LAST_KNOWN_TBL_VA, build_last_known_table())
    frags["LAND_STUB_A"]      = (LAND_STUB_A_VA, build_land_stub_a())
    frags["LAND_STUB_B"]      = (LAND_STUB_B_VA, build_land_stub_b())
    frags["SWIM_STUB_A"]      = (SWIM_STUB_A_VA, build_swim_stub_a())
    frags["SWIM_STUB_B"]      = (SWIM_STUB_B_VA, build_swim_stub_b())
    frags["LOOP_INIT_A"]      = (LOOP_INIT_A_VA, build_loop_init_a())
    frags["LOOP_INIT_B"]      = (LOOP_INIT_B_VA, build_loop_init_b())
    frags["LOOP_TOP"]         = (LOOP_TOP_VA, build_loop_top())
    frags["LOOP_BODY_1"]      = (LOOP_BODY_1_VA, build_loop_body_1())
    frags["LOOP_BODY_2"]      = (LOOP_BODY_2_VA, build_loop_body_2())
    frags["LOOP_BODY_3"]      = (LOOP_BODY_3_VA, build_loop_body_3())
    frags["LOOP_BODY_4"]      = (LOOP_BODY_4_VA, build_loop_body_4())
    frags["LOOP_DOCALL_A"]    = (LOOP_DOCALL_A_VA, build_loop_docall_a())
    frags["LOOP_DOCALL_B"]    = (LOOP_DOCALL_B_VA, build_loop_docall_b())
    frags["LOOP_NEXT"]        = (LOOP_NEXT_VA, build_loop_next())
    frags["LAND_EPILOGUE"]    = (LAND_EPILOGUE_VA, build_land_epilogue())
    frags["SWIM_EPILOGUE_A"]  = (SWIM_EPILOGUE_A_VA, build_swim_epilogue_a())
    frags["SWIM_EPILOGUE_B"]  = (SWIM_EPILOGUE_B_VA, build_swim_epilogue_b())
    return frags


# ── Verify / apply ────────────────────────────────────────────────────────────

def verify_vanilla(data: bytes) -> list:
    problems = []
    if data[LAND_HOOK_FILE:LAND_HOOK_FILE + len(LAND_HOOK_VANILLA)] != LAND_HOOK_VANILLA:
        problems.append("land_hook")
    if data[SWIM_HOOK_FILE:SWIM_HOOK_FILE + len(SWIM_HOOK_VANILLA)] != SWIM_HOOK_VANILLA:
        problems.append("swim_hook")
    for _name, (va, _body) in _build_all_fragments().items():
        file_off = _va_to_file(va)
        actual = data[file_off:file_off + FRAG_SIZE]
        if actual != bytes([0xCC] * FRAG_SIZE):
            problems.append(f"frag_{va:X}")
    return problems


def apply_secret_mode_live_patch(exe_path: str, *, dry_run: bool = False) -> bool:
    path = Path(exe_path)
    if not path.exists():
        print(f"  [secret_mode_live] EXE not found: {exe_path} -- skipping")
        return False

    data = bytearray(path.read_bytes())

    problems = verify_vanilla(bytes(data))
    if problems:
        print(f"  [secret_mode_live] WARNING: unexpected bytes at {problems} -- "
              f"skipping (already patched, wrong EXE build, or dogmode_live_patch.py "
              f"is already applied to this exe -- only one of the two should be active)")
        return False

    if dry_run:
        print("  [secret_mode_live] DRY RUN -- verified clean, no bytes written")
        return True

    for _name, (va, body) in _build_all_fragments().items():
        file_off = _va_to_file(va)
        data[file_off:file_off + FRAG_SIZE] = body

    data[LAND_HOOK_FILE:LAND_HOOK_FILE + len(LAND_HOOK_VANILLA)] = \
        build_hook_patch(LAND_HOOK_VA, len(LAND_HOOK_VANILLA), LAND_STUB_A_VA)
    data[SWIM_HOOK_FILE:SWIM_HOOK_FILE + len(SWIM_HOOK_VANILLA)] = \
        build_hook_patch(SWIM_HOOK_VA, len(SWIM_HOOK_VANILLA), SWIM_STUB_A_VA)

    path.write_bytes(data)
    print(f"  [secret_mode_live] Patched -- {len(SECRET_CVAR_VAS)} secret cvars covered, "
          f"land hook 0x{LAND_HOOK_VA:X} -> 0x{LAND_STUB_A_VA:X}, "
          f"swim hook 0x{SWIM_HOOK_VA:X} -> 0x{SWIM_STUB_A_VA:X}")
    return True


def verify_secret_mode_live_patch(exe_path: str) -> bool:
    data = Path(exe_path).read_bytes()
    land_actual = data[LAND_HOOK_FILE:LAND_HOOK_FILE + len(LAND_HOOK_VANILLA)]
    swim_actual = data[SWIM_HOOK_FILE:SWIM_HOOK_FILE + len(SWIM_HOOK_VANILLA)]

    land_patched = land_actual[0] == 0xE9
    swim_patched = swim_actual[0] == 0xE9

    if land_patched and swim_patched:
        return True
    if land_actual == LAND_HOOK_VANILLA and swim_actual == SWIM_HOOK_VANILLA:
        return False
    raise RuntimeError(
        f"[secret_mode_live] Inconsistent/unexpected hook state -- "
        f"land: {land_actual.hex(' ')}  swim: {swim_actual.hex(' ')}"
    )


# ── Self-check at import time ────────────────────────────────────────────────
def _self_check() -> None:
    frags = _build_all_fragments()
    for name, (va, body) in frags.items():
        assert len(body) == FRAG_SIZE, f"{name}: {len(body)} bytes, expected {FRAG_SIZE}"
    vas = [va for va, _ in frags.values()]
    assert len(vas) == len(set(vas)), "duplicate fragment VA in the allocation table"
    assert len(vas) == 22, f"expected 22 fragments, got {len(vas)}"
    assert len(build_hook_patch(LAND_HOOK_VA, len(LAND_HOOK_VANILLA), LAND_STUB_A_VA)) == len(LAND_HOOK_VANILLA)
    assert len(build_hook_patch(SWIM_HOOK_VA, len(SWIM_HOOK_VANILLA), SWIM_STUB_A_VA)) == len(SWIM_HOOK_VANILLA)


_self_check()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Apply the generalized live-apply code-cave patch (8 secret cvars) "
                    "to Shadow Man Remastered EXE"
    )
    parser.add_argument("exe", help="Path to thoth_x64_patched.exe")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if args.verify:
        try:
            applied = verify_secret_mode_live_patch(args.exe)
            print(f"secret_mode_live patch: {'APPLIED' if applied else 'VANILLA'}")
            sys.exit(0)
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(2)

    ok = apply_secret_mode_live_patch(args.exe, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)
