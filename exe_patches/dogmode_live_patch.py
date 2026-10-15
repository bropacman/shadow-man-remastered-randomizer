"""
dogmode_live_patch.py
======================
Static EXE code-cave patch that makes g_dogmode (and, by extension, any
plain single-instance g_<name>mode secret cvar) apply LIVE -- no level
transition needed -- without ever executing code from an externally
injected thread.

BACKGROUND -- why this exists
------------------------------
Full derivation lives in CLAUDE.md ("Live-apply investigation" and
"Pure-static (Ghidra-only) trace of the secret-mode cvar system"). Short
version:

  - A secret cvar's live cached bool value sits at a fixed, static address
    (confirmed stable across restarts): the cvar object at
    DOGMODE_CVAR_VA (base+0xDBA740 for g_dogmode), whose +0x78 field is a
    pointer to an {int32 cacheTag, int32 cachedValue} slot.
  - The engine's own on-change callback for ALL g_<name>mode secrets is
    FUN_140458EF0 (CALLBACK_VA), confirmed via a live read of
    DOGMODE_CVAR_VA+0x48 (the callback slot). It handles mutual-exclusion
    cleanup and calls FUN_140459250 (mesh/skin reload) with the player's
    position saved/restored.
  - Calling CALLBACK_VA from a client.py-injected CreateRemoteThread
    works fine turning a secret ON, but CRASHES turning it OFF --
    root-caused via a real crash log to a genuine, unsynchronized
    cross-thread data race: the injected thread mutates shared D3D11
    resource-cache state (an unlocked intrusive-list splice in
    FUN_140459250, feeding into a sampler-state hash table used by
    CommitShaderProgramResources) WHILE the game's own real render/main
    thread is concurrently using that same state during its own normal
    per-frame work. The crash backtrace showed 14+ frames of coherent,
    in-module native code below the fault -- i.e. it crashed on a
    DIFFERENT, already-running game thread, not on the injected one.
  - Toggling the SAME cvar via the in-game developer console (the real,
    single-threaded, engine-sanctioned SetValue pipeline) is completely
    safe, repeatably, in both directions -- confirmed live. That rules
    out "the reload logic is buggy" and confirms "a second, foreign
    thread touching this state concurrently is the actual problem."

FIX: don't inject a second thread at all. Splice the SAME callback call
into the game's own instruction stream, on an already-running game
thread, executing synchronously as part of its normal per-frame work --
structurally identical to how the constructor (FUN_1404596b0) and script
dispatcher (FUN_14029f340) already call this same reload safely, just
running far more often (every frame) instead of only at scripted events.

HOOK SITES
----------
Reused sprint_patch.py's own land/swim per-frame movement-update hooks
(FUN_14046b2c0 / FUN_14046b590) as the "runs every frame" anchor, but
NOT at sprint's own hook bytes (that would collide with sprint_patch
whenever both are active for the same seed). Instead, spliced in
immediately after each: sprint's own cave/fragment chain always returns
execution to exactly HOOK_VA+len(vanilla) whether sprint is installed or
not, so hooking there is safe to compose with sprint_patch unconditionally.

  LAND:  0x14046B2CC (LAND_HOOK_VA+8), 6 bytes vanilla
         (XOR R8B,R8B ; MOV EAX,[RCX+0x10]), returns to 0x14046B2D2.
         Only RCX is live at this point (used constantly for the rest of
         the function) -- everything else (RAX/RDX/R8/R9/R10/R11) is
         still unestablished this early in the function, and the two
         replaced instructions get replayed verbatim at the end of our
         cave, so nothing else needs preserving.

  SWIM:  0x14046B977, 7 bytes vanilla
         (MOV RBX,[R11+0x10] ; MOV RSP,R11), returns to 0x14046B97E.
         Deliberately NOT hooked at SWIM_HOOK_VA+8 (0x14046B79D) --
         that lands mid-way through a dense SIMD block where several
         VOLATILE xmm registers (xmm0-xmm3) are live across the splice,
         which the ABI does NOT require a callee to preserve. Hooking
         instead at the function's own epilogue tail (after all the real
         per-frame math is computed and stored, and after the callee-
         saved xmm6-15 registers have already been restored from the
         stack) means nothing is live except R11 (used two instructions
         later to restore RBX/RSP before RET).

Confirmed via live crash-log cross-reference (2026-07-31): the crash
signature (CALL [RAX+0x20] through a garbage RAX, deep in
CommitShaderProgramResources) is a genuine cross-thread race, not
something inherent to the reload logic itself -- so running it inline,
synchronously, on one of these existing per-frame hooks, sidesteps the
failure mode by construction rather than by luck.

GATING LOGIC
------------
Each hook fires every frame (land or swim respectively), but only
actually calls anything when the cvar's cached value has changed since
the last check:
  1. Deref DOGMODE_CVAR_VA+0x78 -> slot pointer.
  2. If cache tag != 1 (engine hasn't populated it yet), skip.
  3. Read the cached bool byte. If it matches LAST_KNOWN (a persistent
     byte embedded in the cave's own free padding, shared between the
     land and swim chains so whichever hook notices the change first
     "wins" and the other doesn't redundantly re-call), skip.
  4. Otherwise: update LAST_KNOWN, then CALL CALLBACK_VA with
     RCX = DOGMODE_CVAR_VA (the handle), exactly reproducing what the
     engine's own SetValue pipeline would do.

CAVE LAYOUT
-----------
This exe's inter-function CC gaps cap at 16 bytes (16-byte function
alignment; confirmed exhaustively via tools/find_cave.py, same
constraint every other *_patch.py in this repo already documents), so
the logic is split into small (<=16 byte) fragments chained by JMP
rel32, same technique as sprint_patch.py's land hook and the shelved
deadside_guns_patch.py's 3-fragment chain. Every conditional branch uses
the "Jcc rel8 over an unconditional JMP rel32" idiom so the Jcc's target
is always in-fragment (short-jump range) while the real far target is
always a full-range JMP.

9 fragments for LAND, 10 for SWIM (its finish sequence needed an extra
split to fit ADD RSP + POP R11 + both replayed instructions + JMP-back
under 16 bytes) -- 19 total, allocated from the free-gap list found by
`python tools/find_cave.py thoth_x64_patched.exe --min-size 16` run
2026-07-31, deliberately skipping the 3 gaps already reserved by
deadside_guns_patch.py (0x1401A10B0 / 0x1401C2F80 / 0x1401D6430) even
though that patch is currently unapplied/reverted, to avoid a latent
collision if it's ever revived.
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


# ── Fixed targets ────────────────────────────────────────────────────────────
DOGMODE_CVAR_VA           = 0x140DBA740          # g_dogmode cvar object (static, confirmed stable across restarts)
DOGMODE_SLOT_PTR_FIELD_VA = DOGMODE_CVAR_VA + 0x78  # holds a pointer to the {tag,value} slot
CALLBACK_VA               = 0x140458EF0          # FUN_140458EF0, the real on-change callback

# ── Hook sites ──────────────────────────────────────────────────────────────
LAND_HOOK_VA      = 0x14046B2CC
LAND_HOOK_FILE    = _va_to_file(LAND_HOOK_VA)
LAND_HOOK_VANILLA = bytes.fromhex("4532c08b4110")   # XOR R8B,R8B ; MOV EAX,[RCX+0x10]
LAND_RETURN_VA    = 0x14046B2D2

SWIM_HOOK_VA      = 0x14046B977
SWIM_HOOK_FILE    = _va_to_file(SWIM_HOOK_VA)
SWIM_HOOK_VANILLA = bytes.fromhex("498b5b10498be3")   # MOV RBX,[R11+0x10] ; MOV RSP,R11
SWIM_RETURN_VA    = 0x14046B97E

# ── Fragment locations (16-byte CC-padding gaps, found via
#    tools/find_cave.py 2026-07-31; excludes 0x1401A10B0/0x1401C2F80/
#    0x1401D6430, already reserved by deadside_guns_patch.py) ────────────────
FRAG_SIZE = 16

L1_VA         = 0x1401F2560   # PUSH RCX ; SUB RSP,8 ; JMP L2                      (+ LAST_KNOWN byte at +15)
L2_VA         = 0x14020BBD0   # LEA RAX,[rip->slot field] ; MOV RAX,[RAX] ; JMP L3
L3_VA         = 0x1402101D0   # CMP [RAX],1 ; JE +5 ; JMP FINISH ; JMP L4
L4_VA         = 0x140210290   # MOVZX ECX,[RAX+4] ; CMP [rip->LAST_KNOWN],CL ; JMP L5
L5_VA         = 0x140221EC0   # JE +5 ; JMP FINISH ; JMP DOCALL_A
LDA_VA        = 0x140227BD0   # MOV [rip->LAST_KNOWN],CL ; JMP DOCALL_B
LDB_VA        = 0x140233AC0   # LEA RCX,[rip->DOGMODE_CVAR_VA] ; JMP DOCALL_C
LDC_VA        = 0x140246FD0   # CALL CALLBACK_VA ; JMP FINISH
LFIN_VA       = 0x1402488C0   # ADD RSP,8 ; POP RCX ; XOR R8B,R8B ; MOV EAX,[RCX+0x10] ; JMP LAND_RETURN_VA

S1_VA         = 0x1402920D0   # PUSH R11 ; SUB RSP,8 ; JMP S2
S2_VA         = 0x140296D10   # LEA RAX,[rip->slot field] ; MOV RAX,[RAX] ; JMP S3
S3_VA         = 0x140313E20   # CMP [RAX],1 ; JE +5 ; JMP SFIN_A ; JMP S4
S4_VA         = 0x14031E5D0   # MOVZX ECX,[RAX+4] ; CMP [rip->LAST_KNOWN],CL ; JMP S5
S5_VA         = 0x140327150   # JE +5 ; JMP SFIN_A ; JMP SDOCALL_A
SDA_VA        = 0x140417190   # MOV [rip->LAST_KNOWN],CL ; JMP SDOCALL_B
SDB_VA        = 0x14049BDC0   # LEA RCX,[rip->DOGMODE_CVAR_VA] ; JMP SDOCALL_C
SDC_VA        = 0x1404B5E60   # CALL CALLBACK_VA ; JMP SFIN_A
SFIN_A_VA     = 0x1404B6050   # ADD RSP,8 ; POP R11 ; JMP SFIN_B
SFIN_B_VA     = 0x1404B7210   # MOV RBX,[R11+0x10] ; MOV RSP,R11 ; JMP SWIM_RETURN_VA

# LAST_KNOWN lives embedded in L1's own padding (byte 15 of that 16-byte
# gap) -- shared by both the land and swim chains, so whichever notices a
# change first updates it and the other chain sees the update too instead
# of redundantly re-calling. 0xFF sentinel = "never synced yet" (forces
# one harmless extra call/check the first time either hook runs after a
# fresh patch/process start).
LAST_KNOWN_VA = L1_VA + 15


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


def _lea_rax_ripconst(instr_va: int, target_va: int) -> bytes:
    """48 8D 05 disp32 -- LEA RAX,[rip+disp]. instr_va is the address of
    the REX byte; disp is relative to the end of this 7-byte instruction."""
    disp = target_va - (instr_va + 7)
    return bytes([0x48, 0x8D, 0x05]) + struct.pack('<i', disp)


def _lea_rcx_ripconst(instr_va: int, target_va: int) -> bytes:
    disp = target_va - (instr_va + 7)
    return bytes([0x48, 0x8D, 0x0D]) + struct.pack('<i', disp)


def _cmp_rip8_cl(instr_va: int, target_va: int) -> bytes:
    """38 0D disp32 -- CMP byte ptr [rip+disp],CL. instr_va is the address
    of the opcode byte; disp is relative to the end of this 6-byte instr."""
    disp = target_va - (instr_va + 6)
    return bytes([0x38, 0x0D]) + struct.pack('<i', disp)


def _mov_rip8_cl(instr_va: int, target_va: int) -> bytes:
    disp = target_va - (instr_va + 6)
    return bytes([0x88, 0x0D]) + struct.pack('<i', disp)


def _pad(body: bytes, size: int = FRAG_SIZE) -> bytes:
    assert len(body) <= size, f"fragment body too big: {len(body)} > {size}"
    return body + bytes([0x90]) * (size - len(body))


# ── Fragment builders ─────────────────────────────────────────────────────────

def build_l1() -> bytes:
    body = bytearray()
    body += bytes([0x51])                                  # PUSH RCX
    body += bytes([0x48, 0x83, 0xEC, 0x08])                 # SUB RSP,0x8
    body += _jmp_rel32(L1_VA + 5, L2_VA)                    # JMP L2
    padded = bytearray(_pad(bytes(body)))
    padded[15] = 0xFF                                        # LAST_KNOWN sentinel
    assert len(padded) == FRAG_SIZE
    return bytes(padded)


def build_l2() -> bytes:
    body = bytearray()
    body += _lea_rax_ripconst(L2_VA, DOGMODE_SLOT_PTR_FIELD_VA)  # LEA RAX,[rip->slot field]
    body += bytes([0x48, 0x8B, 0x00])                        # MOV RAX,[RAX]
    body += _jmp_rel32(L2_VA + 10, L3_VA)                    # JMP L3
    return _pad(bytes(body))


def build_l3(finish_va: int, next_va: int) -> bytes:
    body = bytearray()
    body += bytes([0x83, 0x38, 0x01])                        # CMP dword ptr[RAX],1
    body += _je_rel8(L3_VA + 3, L3_VA + 10)                  # JE +5 (to the JMP next_va below)
    body += _jmp_rel32(L3_VA + 5, finish_va)                 # JMP FINISH (tag != 1 -> skip)
    body += _jmp_rel32(L3_VA + 10, next_va)                  # JMP next (tag == 1 -> continue)
    return _pad(bytes(body))


def build_l4(next_va: int) -> bytes:
    body = bytearray()
    body += bytes([0x0F, 0xB6, 0x48, 0x04])                  # MOVZX ECX,[RAX+4]
    body += _cmp_rip8_cl(L4_VA + 4, LAST_KNOWN_VA)            # CMP [rip->LAST_KNOWN],CL
    body += _jmp_rel32(L4_VA + 10, next_va)                  # JMP next
    return _pad(bytes(body))


def build_l5(finish_va: int, docall_va: int) -> bytes:
    body = bytearray()
    body += _je_rel8(L5_VA, L5_VA + 7)                        # JE +5 (to the JMP docall_va below)
    body += _jmp_rel32(L5_VA + 2, finish_va)                  # JMP FINISH (unchanged -> skip)
    body += _jmp_rel32(L5_VA + 7, docall_va)                  # JMP DOCALL_A (changed -> call)
    return _pad(bytes(body))


def build_docall_a(va: int, next_va: int) -> bytes:
    body = bytearray()
    body += _mov_rip8_cl(va, LAST_KNOWN_VA)                   # MOV [rip->LAST_KNOWN],CL
    body += _jmp_rel32(va + 6, next_va)                       # JMP next
    return _pad(bytes(body))


def build_docall_b(va: int, next_va: int) -> bytes:
    body = bytearray()
    body += _lea_rcx_ripconst(va, DOGMODE_CVAR_VA)            # LEA RCX,[rip->DOGMODE_CVAR_VA]
    body += _jmp_rel32(va + 7, next_va)                       # JMP next
    return _pad(bytes(body))


def build_docall_c(va: int, finish_va: int) -> bytes:
    body = bytearray()
    body += _call_rel32(va, CALLBACK_VA)                      # CALL CALLBACK_VA
    body += _jmp_rel32(va + 5, finish_va)                     # JMP FINISH
    return _pad(bytes(body))


def build_land_finish() -> bytes:
    body = bytearray()
    body += bytes([0x48, 0x83, 0xC4, 0x08])                   # ADD RSP,0x8
    body += bytes([0x59])                                     # POP RCX
    body += bytes([0x45, 0x32, 0xC0])                         # XOR R8B,R8B          (replay)
    body += bytes([0x8B, 0x41, 0x10])                         # MOV EAX,[RCX+0x10]   (replay)
    body += _jmp_rel32(LFIN_VA + 11, LAND_RETURN_VA)          # JMP back
    assert len(body) == FRAG_SIZE, f"land finish size mismatch: {len(body)}"
    return bytes(body)


def build_swim_finish_a() -> bytes:
    body = bytearray()
    body += bytes([0x48, 0x83, 0xC4, 0x08])                   # ADD RSP,0x8
    body += bytes([0x41, 0x5B])                                # POP R11
    body += _jmp_rel32(SFIN_A_VA + 6, SFIN_B_VA)               # JMP SFIN_B
    return _pad(bytes(body))


def build_swim_finish_b() -> bytes:
    body = bytearray()
    body += bytes([0x49, 0x8B, 0x5B, 0x10])                    # MOV RBX,[R11+0x10]   (replay)
    body += bytes([0x49, 0x8B, 0xE3])                          # MOV RSP,R11           (replay)
    body += _jmp_rel32(SFIN_B_VA + 7, SWIM_RETURN_VA)           # JMP back
    return _pad(bytes(body))


def build_s1() -> bytes:
    body = bytearray()
    body += bytes([0x41, 0x53])                                # PUSH R11
    body += bytes([0x48, 0x83, 0xEC, 0x08])                     # SUB RSP,0x8
    body += _jmp_rel32(S1_VA + 6, S2_VA)                        # JMP S2
    return _pad(bytes(body))


def build_hook_patch(hook_va: int, vanilla_len: int, target_va: int) -> bytes:
    """5-byte JMP + NOP padding, replacing `vanilla_len` bytes at hook_va."""
    jmp = _jmp_rel32(hook_va, target_va)
    return jmp + bytes([0x90] * (vanilla_len - len(jmp)))


# ── Assembled fragment map ───────────────────────────────────────────────────

def _build_all_fragments() -> dict:
    return {
        "L1":     (L1_VA,     build_l1()),
        "L2":     (L2_VA,     build_l2()),
        "L3":     (L3_VA,     build_l3(LFIN_VA, L4_VA)),
        "L4":     (L4_VA,     build_l4(L5_VA)),
        "L5":     (L5_VA,     build_l5(LFIN_VA, LDA_VA)),
        "LDA":    (LDA_VA,    build_docall_a(LDA_VA, LDB_VA)),
        "LDB":    (LDB_VA,    build_docall_b(LDB_VA, LDC_VA)),
        "LDC":    (LDC_VA,    build_docall_c(LDC_VA, LFIN_VA)),
        "LFIN":   (LFIN_VA,   build_land_finish()),

        "S1":     (S1_VA,     build_s1()),
        "S2":     (S2_VA,     _build_s2()),
        "S3":     (S3_VA,     _build_s3()),
        "S4":     (S4_VA,     _build_s4()),
        "S5":     (S5_VA,     _build_s5()),
        "SDA":    (SDA_VA,    build_docall_a(SDA_VA, SDB_VA)),
        "SDB":    (SDB_VA,    build_docall_b(SDB_VA, SDC_VA)),
        "SDC":    (SDC_VA,    build_docall_c(SDC_VA, SFIN_A_VA)),
        "SFIN_A": (SFIN_A_VA, build_swim_finish_a()),
        "SFIN_B": (SFIN_B_VA, build_swim_finish_b()),
    }


# The swim chain's L2/L3/L4/L5-equivalents share identical *logic* to land's
# (same cvar check, same LAST_KNOWN), just at different addresses/targets --
# built explicitly rather than via the land functions directly, since those
# hardcode L-prefixed VA constants in their disp calculations.

def _build_s2() -> bytes:
    body = bytearray()
    body += _lea_rax_ripconst(S2_VA, DOGMODE_SLOT_PTR_FIELD_VA)
    body += bytes([0x48, 0x8B, 0x00])
    body += _jmp_rel32(S2_VA + 10, S3_VA)
    return _pad(bytes(body))


def _build_s3() -> bytes:
    body = bytearray()
    body += bytes([0x83, 0x38, 0x01])
    body += _je_rel8(S3_VA + 3, S3_VA + 10)
    body += _jmp_rel32(S3_VA + 5, SFIN_A_VA)
    body += _jmp_rel32(S3_VA + 10, S4_VA)
    return _pad(bytes(body))


def _build_s4() -> bytes:
    body = bytearray()
    body += bytes([0x0F, 0xB6, 0x48, 0x04])
    body += _cmp_rip8_cl(S4_VA + 4, LAST_KNOWN_VA)
    body += _jmp_rel32(S4_VA + 10, S5_VA)
    return _pad(bytes(body))


def _build_s5() -> bytes:
    body = bytearray()
    body += _je_rel8(S5_VA, S5_VA + 7)
    body += _jmp_rel32(S5_VA + 2, SFIN_A_VA)
    body += _jmp_rel32(S5_VA + 7, SDA_VA)
    return _pad(bytes(body))


# ── Verify / apply ────────────────────────────────────────────────────────────

_ALL_FRAG_VAS = [
    L1_VA, L2_VA, L3_VA, L4_VA, L5_VA, LDA_VA, LDB_VA, LDC_VA, LFIN_VA,
    S1_VA, S2_VA, S3_VA, S4_VA, S5_VA, SDA_VA, SDB_VA, SDC_VA, SFIN_A_VA, SFIN_B_VA,
]


def verify_vanilla(data: bytes) -> list:
    """Return a list of problem descriptions; empty list == safe to patch."""
    problems = []
    if data[LAND_HOOK_FILE:LAND_HOOK_FILE + len(LAND_HOOK_VANILLA)] != LAND_HOOK_VANILLA:
        problems.append("land_hook")
    if data[SWIM_HOOK_FILE:SWIM_HOOK_FILE + len(SWIM_HOOK_VANILLA)] != SWIM_HOOK_VANILLA:
        problems.append("swim_hook")
    for va in _ALL_FRAG_VAS:
        file_off = _va_to_file(va)
        actual = data[file_off:file_off + FRAG_SIZE]
        if actual != bytes([0xCC] * FRAG_SIZE):
            problems.append(f"frag_{va:X}")
    return problems


def apply_dogmode_live_patch(exe_path: str, *, dry_run: bool = False) -> bool:
    path = Path(exe_path)
    if not path.exists():
        print(f"  [dogmode_live] EXE not found: {exe_path} -- skipping")
        return False

    data = bytearray(path.read_bytes())

    problems = verify_vanilla(bytes(data))
    if problems:
        print(f"  [dogmode_live] WARNING: unexpected bytes at {problems} -- "
              f"skipping (already patched, wrong EXE build, or a fragment "
              f"collision with another patch)")
        return False

    if dry_run:
        print("  [dogmode_live] DRY RUN -- verified clean, no bytes written")
        return True

    for _name, (va, body) in _build_all_fragments().items():
        file_off = _va_to_file(va)
        data[file_off:file_off + FRAG_SIZE] = body

    data[LAND_HOOK_FILE:LAND_HOOK_FILE + len(LAND_HOOK_VANILLA)] = \
        build_hook_patch(LAND_HOOK_VA, len(LAND_HOOK_VANILLA), L1_VA)
    data[SWIM_HOOK_FILE:SWIM_HOOK_FILE + len(SWIM_HOOK_VANILLA)] = \
        build_hook_patch(SWIM_HOOK_VA, len(SWIM_HOOK_VANILLA), S1_VA)

    path.write_bytes(data)
    print(f"  [dogmode_live] Patched -- land hook 0x{LAND_HOOK_VA:X} -> 0x{L1_VA:X}, "
          f"swim hook 0x{SWIM_HOOK_VA:X} -> 0x{S1_VA:X}")
    return True


def verify_dogmode_live_patch(exe_path: str) -> bool:
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
        f"[dogmode_live] Inconsistent/unexpected hook state -- "
        f"land: {land_actual.hex(' ')}  swim: {swim_actual.hex(' ')}"
    )


# ── Self-check: run at import time so a size/encoding mistake fails loudly
#    the moment this module is loaded, not silently at patch time ───────────
def _self_check() -> None:
    frags = _build_all_fragments()
    for name, (va, body) in frags.items():
        assert len(body) == FRAG_SIZE, f"{name}: {len(body)} bytes, expected {FRAG_SIZE}"
    vas = [va for va, _ in frags.values()]
    assert len(vas) == len(set(vas)), "duplicate fragment VA in the allocation table"
    assert len(build_hook_patch(LAND_HOOK_VA, len(LAND_HOOK_VANILLA), L1_VA)) == len(LAND_HOOK_VANILLA)
    assert len(build_hook_patch(SWIM_HOOK_VA, len(SWIM_HOOK_VANILLA), S1_VA)) == len(SWIM_HOOK_VANILLA)


_self_check()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Apply the live-apply code-cave patch for g_dogmode to Shadow Man Remastered EXE"
    )
    parser.add_argument("exe", help="Path to thoth_x64_patched.exe")
    parser.add_argument("--dry-run", action="store_true",
                        help="Verify fragment sites are clean but write nothing")
    parser.add_argument("--verify", action="store_true",
                        help="Check patch status and exit")
    args = parser.parse_args()

    if args.verify:
        try:
            applied = verify_dogmode_live_patch(args.exe)
            print(f"dogmode_live patch: {'APPLIED' if applied else 'VANILLA'}")
            sys.exit(0)
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(2)

    ok = apply_dogmode_live_patch(args.exe, dry_run=args.dry_run)
    sys.exit(0 if ok else 1)
