"""
unique_retractor_keys_patch.py
================================
Live-apply patch that gives the standalone randomizer's "Unique Retractor
Keys" feature real in-game teeth. Today `unique_retractor_keys` (fill.py /
patcher.py) only computes `retractor_level_assignment` for progression
logic and the spoiler log -- physically, any of the 5 Retractors still
opens any of the 5 liveside schisms, because nothing in the exe enforces
per-portal identity. This patch adds that enforcement.

DESIGN -- two new PE sections, same technique as secret_mode_section_patch.py
--------------------------------------------------------------------------
  .rkcode   (dynamic)   R+X   three small hook bodies (portal_check [backstop,
                               see HOOK A note below], pickup_check, use_check
                               [HOOK C, the real gate])
  .rkdata   (dynamic)   R+W   TABLE[5*16] + FEEDBACK_STR[40] -- no OWNED[]
                               array anymore (2026-08-17): ownership is now
                               tracked via the game's own save-backed
                               CF_CUSTOM00-04 named flags instead of a
                               scratch byte array in this section, see the
                               PERSISTENCE comment further down.

VA placement is computed DYNAMICALLY at apply time from the target file's
own current SizeOfImage (see _compute_new_vas()) -- always the next free,
page-aligned, GAPLESS RVA past wherever the image actually ends, same as
how secret_mode_section_patch.py's own (proven-working) fixed VAs were
originally derived, just done live instead of hardcoded once. This was NOT
the original design: an earlier version hardcoded two fixed VAs, two pages
PAST the image's actual end, as defensive headroom against ever colliding
with secret_mode_section_patch.py's own fixed .apcode/.apdata VAs. That gap
in the image's virtual address space is what caused a real bug -- applying
the old version to a real exe (even a fresh vanilla one) produced a file
Windows refused to launch ("This app can't run on your PC"), even though
neither manual struct-level PE inspection nor a full pefile parse flagged
anything wrong with it. Root-caused 2026-08-17 by diffing our own output
against a real secret_mode_section_patch.py run against the same baseline
exe -- gapless placement fixed it. Computing placement dynamically (instead
of just moving the hardcoded constant back to the gapless VA) also means
this naturally avoids colliding with whatever's already appended, since
SizeOfImage already reflects it if present -- no headroom guesswork needed.

UNLIKE secret_mode_section_patch.py's SECRET_TABLE, there is NO hardcoded
position table here. The 5 (x, y, z, world_id) entries are per-seed data
-- patcher.py already computes everything needed:
  - retractor_level_assignment (fill.py assumed_fill): {loc_key: liveside_region_name}
  - retractor_actual_xyz (patcher.py post-placement RSC scan): {level_id: {'retractor': [(x,y,z,zone),...]}}
  - LIVESIDE_TO_IVAR1 (this file): the FIXED vanilla {liveside_region_name: world_id}
    bridge table, live-confirmed 2026-08-17 (see CLAUDE.md / the investigation
    doc's "iVar1 <-> liveside region" section) -- breakpointed FUN_14033ee50
    (SetFlag) at all 5 real schisms, read RDX directly.
Wiring loc_key -> level_id -> actual (x,y,z) is patcher.py's job (it already
has both dicts); build_retractor_table() below just takes the final
[(x, y, z, world_id), ...] list and does the byte-level work.

HOOK A -- portal side, FUN_14047d920 (the "a retractor was used on this
schism" handler). NOTE 2026-08-17: live-tested and confirmed this DOES
correctly block the schism from opening on a wrong-retractor attempt --
but a live test also showed the retractor still gets consumed and the
insertion cutscene still plays anyway, because both of those happen in
FUN_14047d920's own CALLER (FUN_1404674a0), unconditionally, BEFORE this
function is ever invoked -- see HOOK C below, which is the real fix for
consumption/animation. Hook A is kept in place regardless, as a harmless
zero-cost backstop (never actually exercised once Hook C blocks
correctly, but cheap insurance against FUN_14047d920 turning out to have
some other, not-yet-found caller that bypasses Hook C's splice point).
Splices in right where vanilla loads iVar1:

    14047d93c: JNZ LAB_14047da4b        <- existing type-check guard (unmodified)
    14047d942: MOV EDX,[RCX+0x120]      <- OVERWRITTEN (6 bytes -> our JMP)
    14047d948: SUB EDX,1                <- untouched vanilla switch chain (resume target)
    ...
    LAB_14047da4b                        <- full early exit (skips flag-set AND
                                             cleanup/despawn) -- same target the
                                             existing type-check failure already uses

  portal_check re-executes the overwritten MOV, checks OWNED[iVar1]: if set,
  jumps back to resume vanilla (0x14047d948) exactly as before; if unset,
  plays the gn0075s.wav feedback line (see FEEDBACK_STR) then jumps straight
  to LAB_14047da4b -- the retractor is neither consumed nor does the schism
  open.

  Register safety at the splice point (confirmed from the disassembly, not
  assumed): RAX (holds the retractor-prop pointer used only for the type
  check already passed) is clobbered anyway inside every switch branch by
  `CALL FUN_140347560` before its next read, so it's free scratch here. RBX
  (== param_1, needed later by cleanup at 0x14047d9b5) and RSP (already
  adjusted by `SUB RSP,0x40` at 0x14047d92a) are the only things that must
  come out of our hook unchanged. RCX is likewise dead after this point in
  the vanilla flow (it gets unconditionally reassigned at 0x14047d99e before
  its next read) -- safe for us to use as scratch too, though we don't need
  to.

HOOK B -- pickup side, FUN_140446500, switchD_140446b7d::caseD_17 (the
retractor-specific case, confirmed live via breakpoint at 0x140446c35):

    140446c35: LEA RCX,[gn0056s.wav]    <- OVERWRITTEN (7 bytes -> our JMP)
    140446c3c: CALL FUN_1403f0060       <- untouched vanilla tail (resume target)
    ...

  pickup_check walks the 5-entry TABLE, compares R14+0x20/0x24/0x28 (the
  picked-up object's own world position, confirmed live-valid at this point
  per the investigation doc's -SS5 validation) against each entry's raw
  x/y/z as exact 32-bit patterns (no epsilon needed -- the earlier live test
  found the retractor prop has no chain-physics jitter, unlike govis), sets
  OWNED[world_id] = 1 on a match, then re-executes the overwritten LEA and
  resumes vanilla exactly as before (still plays gn0056s.wav, still grants
  the item through the normal finalize path -- this hook only ever *adds* a
  side-effect, never changes what vanilla does with the pickup itself).

  Register safety here is LIVE-CONFIRMED, 2026-08-17 (matches Hook A's
  standard, not just argued from disassembly): broke at 0x140446c35 on the
  unpatched exe, manually overwrote RAX/R8/R9/R10/R11 (every register this
  cave actually touches) via Cheat Engine's register editor, resumed --
  tested with two independent sentinels, 0xDEADBEEFDEADBEEF (an obviously-
  invalid pattern) and all-zeros (a different failure class -- catches a
  register used as a null-check or implicit index, which a nonzero garbage
  value wouldn't expose). Pickup SFX played normally, retractor granted
  normally, no crash, both times. Consistent with the ABI argument (two real
  calls, FUN_1403f0060 and FUN_1402bf090, already execute between the splice
  point and the shared finalize tail LAB_140446e2d regardless of this hook,
  so anything genuinely depending on those registers surviving from before
  the switch would already be broken in vanilla) but confirmed empirically
  with two sentinels, not just assumed.

STATUS 2026-08-17 -- live-testing in progress against a real exe (placeholder
table, standalone CLI, not yet a real seed through patcher.py). So far:
Hook A confirmed to correctly block a wrong-schism open. Two real bugs then
found and fixed in this same pass, NEITHER yet re-verified live:
  1. Consumption/animation happened regardless of Hook A's block (both live
     in Hook A's CALLER, not Hook A's own function) -- fixed by adding
     Hook C, which gates the whole sequence before any of it runs. See the
     HOOK C comment above for the disassembly evidence.
  2. Ownership was stored in a throwaway, non-save-backed byte array,
     meaning any picked-up retractor would be silently forgotten on the
     next normal game restart -- fixed by moving ownership to the game's
     own save-backed CF_CUSTOM00-04 named flags via real GetFlag/SetFlag
     calls. See the PERSISTENCE comment above.
  Also still open from before this pass: Hook B's position-match never
  fired for the one real pickup tested (all 5 OWNED bytes stayed 0) --
  likely the hand-typed placeholder-table coordinates not bit-matching the
  live object's actual float32 position (see build_retractor_table()'s own
  docstring for why the REAL per-seed path doesn't have this problem --
  positions there come straight from a struct.unpack, never hand-retyped).
Next: re-apply this rewritten version, confirm the exe still launches, then
re-run the live tests -- wrong schism (should reject, no consume, no
cutscene, flag stays clear across a save/reload), right schism (should
open, consume, and persist through a save/reload), and Hook B's own match
(watch the relevant CF_CUSTOM flag with GetFlag/save-dump tooling rather
than a raw memory byte, since ownership no longer lives in our own section).
"""
import struct
from pathlib import Path

from keystone import Ks, KS_ARCH_X86, KS_MODE_64
import capstone
from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG

IMAGE_BASE = 0x140000000

# ---------------------------------------------------------------------
# Fixed vanilla ground truth -- live-confirmed 2026-08-17 (see module
# docstring). NOT seed-randomized; this is the bridge between fill.py's
# per-seed retractor_level_assignment (loc_key -> liveside region name)
# and the exe's own iVar1 numbering.
# ---------------------------------------------------------------------
LIVESIDE_TO_IVAR1 = {
    "Mordant Street, Queens, NY":     1,   # LIVESIDE_QUEENS
    "Gardelle County Jail, Texas":    2,   # LIVESIDE_PRISON
    "Down Street Station, London":    3,   # LIVESIDE_LONDON
    "Summer Camp, Florida":           4,   # LIVESIDE_FLORIDA
    "Salvage Yard, Mojave Desert":    5,   # LIVESIDE_SALVAGE
}

# ---------------------------------------------------------------------
# Hook A -- portal side (FUN_14047d920)
# ---------------------------------------------------------------------
PORTAL_FUNC_VA        = 0x14047d920
PORTAL_HOOK_VA         = 0x14047d942
PORTAL_HOOK_VANILLA    = bytes([0x8B, 0x91, 0x20, 0x01, 0x00, 0x00])  # MOV EDX,[RCX+0x120]
PORTAL_RESUME_VA       = PORTAL_HOOK_VA + len(PORTAL_HOOK_VANILLA)     # 0x14047d948, SUB EDX,1
PORTAL_FAIL_EXIT_VA    = 0x14047da4b   # LAB_14047da4b -- full early exit (flag-set + cleanup both skipped)
FLAG_MANAGER_ACCESSOR_VA = 0x140347560  # FUN_140347560 -- flag-manager singleton accessor, see PERSISTENCE below

# ---------------------------------------------------------------------
# Hook B -- pickup side (FUN_140446500, caseD_17)
# ---------------------------------------------------------------------
PICKUP_HOOK_VA       = 0x140446c35
PICKUP_HOOK_VANILLA  = bytes([0x48, 0x8D, 0x0D, 0x6C, 0xBF, 0x2D, 0x00])  # LEA RCX,[gn0056s.wav]
PICKUP_RESUME_VA     = PICKUP_HOOK_VA + len(PICKUP_HOOK_VANILLA)          # 0x140446c3c, CALL FUN_1403f0060
GN0056S_STR_VA       = 0x140722BA8   # decoded from the LEA's own disp32 (0x2DBF6C) against its own RIP --
                                      # the vanilla "gn0056s.wav" (retractor pickup) voice line, unchanged
FUN_1403F0060_VA     = 0x1403f0060   # the shared "play this speech path" call every pickup case already uses

# ---------------------------------------------------------------------
# Hook C -- retractor-USE handler, FUN_1404674a0 (calls FUN_14047d920).
# Found 2026-08-17 by walking Hook A's own call stack after a live "wrong
# schism" test: the schism correctly stayed closed (Hook A works), but the
# retractor was still consumed and the insertion cutscene still played --
# both happen in THIS caller, unconditionally, BEFORE FUN_14047d920 is
# ever invoked (retractor-count decrement via a vtable call on the
# inventory singleton with EDX=0x17 at 0x140467512; the cutscene/camera-
# mode trigger at 0x1404674b7 [MOV [RAX+0x25a8],2] and the animation call
# FUN_1404752e0 at 0x140467528 -- all before the CALL FUN_14047d920 at
# 0x140467564). Hook A is kept as a harmless backstop (see its own
# comment above); Hook C is the real fix -- it gates the whole sequence
# before any of it runs, instead of trying to block the open after the
# fact.
# ---------------------------------------------------------------------
USE_FUNC_VA           = 0x1404674a0
USE_HOOK_VA            = 0x1404674b2   # CALL FUN_1402d4870 (camera-mgr singleton getter)
USE_HOOK_VANILLA       = bytes([0xE8, 0xB9, 0xD3, 0xE6, 0xFF])
USE_RESUME_VA          = USE_HOOK_VA + len(USE_HOOK_VANILLA)  # 0x1404674b7, MOV [RAX+0x25a8],2
USE_CLEANUP_RETURN_VA  = 0x14046759c   # LAB_14046759c -- shared vanilla epilogue/return (restores RBX/RSP/RDI,
                                        # RETs). Jumped to directly on a not-owned attempt -- skips the retractor
                                        # decrement, camera-mode write, animation trigger, AND the call into
                                        # FUN_14047d920 entirely, not just the open.
CAMERA_MGR_GETTER_VA   = 0x1402d4870   # the overwritten CALL's own target, re-executed on the owned path

# ---------------------------------------------------------------------
# Retractor-key PERSISTENCE -- CF_CUSTOM00..04 named flags, 2026-08-17.
# ---------------------------------------------------------------------
# Original design stored "has this retractor been picked up" as a raw
# OWNED[5] byte array in our own appended .rkdata section -- plain process
# memory, zero-initialized on every exe launch, completely disconnected
# from the save file. Jon caught this live: it wasn't just wrong under
# adversarial testing (restarting the game mid-test), it would silently
# forget every picked-up retractor on ANY normal save/quit/relaunch --
# i.e. broken for essentially all real play. Fixed by using the game's
# own existing save-backed named-flag system instead of inventing a
# parallel one -- fully reverse-engineered already (investigation doc
# SS1, originally found 2026-07-27 chasing Light Soul; the same system
# client.py's read_named_flag() already depends on surviving saves for
# AP's own tracking):
#   this    = FUN_140347560()                    singleton accessor, called
#                                                  fresh at each use (matches
#                                                  FUN_14047d920's own usage)
#   GetFlag = FUN_14033f1d0(this, index) -> bool  direct call, pure read
#   SetFlag = FUN_14033ee50(this, index)          via this->vtable[1], same
#                                                  dispatch shape FUN_14047d920
#                                                  already uses for the
#                                                  vanilla CF_RETRACTOR0XUSED
#                                                  flags
#
# Indices: CF_CUSTOM00..CF_CUSTOM04 (279-283 / 0x117-0x11B), decoded from
# the name table at 140ca2a90 (index = (addr-140ca2a90)/8) -- a full
# CF_CUSTOM00..CF_CUSTOM50 block, clearly reserved by the original devs
# for exactly this kind of use, not wired to any vanilla name. Confirmed
# safe three ways, not just "looks unused":
#   1. Structurally outside FUN_14033ee50's own side-effect switch -- that
#      switch only special-cases index-0x1a in [0, 0xa7] (i.e. raw index
#      26-193); every CF_CUSTOM* index is > 193, so SetFlag always takes
#      the plain no-op default path (switchD_14033ef47::caseD_1b) for
#      these -- guaranteed by the range check itself, not an absence of
#      decompiled evidence.
#   2. Comfortably inside the flag array's already-allocated capacity --
#      CF_SAVE_LIMIT_HARD (index 333) is a real, working vanilla flag, so
#      the backing bit array is already sized well past 283.
#   3. grep-confirmed: nothing in this codebase (randomizer or the AP
#      client) already reads/writes CF_CUSTOM00-04.
# flag_index = CF_CUSTOM_BASE_INDEX + world_id - 1  (world_id 1..5 -> 279..283)
# ---------------------------------------------------------------------
CF_CUSTOM_BASE_INDEX = 279       # CF_CUSTOM00, world_id 1 (Mordant Street, Queens)
GETFLAG_VA           = 0x14033f1d0   # FUN_14033f1d0(this, index) -> bool
SETFLAG_VTABLE_OFF   = 8             # this->vtable[1], matches FUN_14047d920's (*plVar2->vtable[1])(plVar2, idx)

# ---------------------------------------------------------------------
# New section placement -- BUG FOUND AND FIXED 2026-08-17: this used to be
# two fixed, hardcoded VAs (0x14102D000/0x14102E000), deliberately placed
# two pages PAST where the original image actually ends (0x14102B000, per
# a real exe's SizeOfImage), as defensive headroom against ever colliding
# with secret_mode_section_patch.py's own fixed .apcode/.apdata VAs. That
# gap is what broke it: applying the patch to a real exe (even a fresh
# vanilla one) produced a file Windows refused to launch ("This app can't
# run on your PC"), while secret_mode_section_patch.py's OWN gapless
# placement (new section starts exactly at the prior image end, zero gap)
# is proven-working -- Jon has playtested tons of AP seeds built with it.
# Neither manual struct-level inspection nor a full pefile parse flagged
# anything wrong with the "gapped" version -- the two-page hole in the
# image's virtual address space is a real-loader-only rejection neither
# tool checks for. Root-caused by diffing our own output against a real
# secret_mode_section_patch.py run against the same baseline exe.
#
# Fix: compute placement DYNAMICALLY from the target file's own current
# SizeOfImage at apply time (see _compute_new_vas() / apply_patch() below)
# -- always gapless, and it naturally avoids colliding with whatever's
# already appended (secret_mode's sections or anything else), since
# SizeOfImage already reflects them if present. No more hardcoded VAs.
# ---------------------------------------------------------------------
TABLE_ENTRY_SIZE  = 16
TABLE_ENTRIES     = 5
FEEDBACK_STR_SIZE = 40

FEEDBACK_STR = b"audio/speech/generic/gn0075s.wav\x00"
assert len(FEEDBACK_STR) <= FEEDBACK_STR_SIZE

FILE_ALIGNMENT = 0x200
SECTION_ALIGNMENT = 0x1000

_ks = Ks(KS_ARCH_X86, KS_MODE_64)
_cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
_cs.detail = True


def _asm(text: str, addr: int) -> bytes:
    encoding, _count = _ks.asm(text, addr)
    if encoding is None:
        raise ValueError(f"assembly failed at 0x{addr:X}:\n{text}")
    return bytes(encoding)


def _data_layout(data_va: int):
    """Offsets within the appended data section, relative to its own base
    VA -- unchanged regardless of where that base actually ends up. No more
    OWNED[] byte array (2026-08-17) -- ownership now lives in the game's own
    save-backed CF_CUSTOM00-04 flags (see PERSISTENCE comment above), so the
    data section only needs the per-seed position TABLE and the feedback
    string."""
    table_va = data_va
    feedback_str_va = table_va + TABLE_ENTRIES * TABLE_ENTRY_SIZE
    return table_va, feedback_str_va


def _read_size_of_image(data: bytes) -> int:
    """Parses just enough of the PE header to get the current SizeOfImage
    (an RVA-space size, e.g. 0x102B000 on a fresh vanilla exe) -- used to
    compute a gapless placement for our own new sections."""
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    coff_off = pe_off + 4
    opt_off = coff_off + 20
    return struct.unpack_from("<I", data, opt_off + 56)[0]


def _compute_new_vas(data: bytes):
    """Next free, page-aligned, GAPLESS RVA past the image's current end --
    same derivation secret_mode_section_patch.py's own (proven-working)
    fixed VAs were originally computed from, just done dynamically here
    instead of hardcoded once and left stale."""
    size_of_image = _read_size_of_image(data)
    code_va = IMAGE_BASE + _align_up(size_of_image, SECTION_ALIGNMENT)
    data_va = code_va + SECTION_ALIGNMENT
    return code_va, data_va


# ---------------------------------------------------------------------
# Cave bodies
# ---------------------------------------------------------------------

def _portal_check_text(feedback_str_va: int) -> str:
    """BACKSTOP ONLY (see module docstring's Hook A note) -- Hook C is the
    real gate now. Re-executes the overwritten `MOV EDX,[RCX+0x120]`, then
    gates the vanilla switch on GetFlag(this, CF_CUSTOM_BASE_INDEX+iVar1-1)
    instead of a raw OWNED[] byte. iVar1 outside 1..5 (shouldn't happen --
    only 5 real schisms exist) falls straight through to vanilla, unblocked.
    r15d holds a copy of the original iVar1 across the GetFlag call (R15 is
    non-volatile per the Win64 ABI) so EDX can be correctly restored before
    resuming -- vanilla's own next instruction (SUB EDX,1) depends on EDX
    still holding iVar1, and our own GetFlag call needs EDX for the flag
    index argument in between, so it has to be saved and put back."""
    return f"""
    mov edx, dword ptr [rcx+0x120]
    mov r15d, edx
    cmp r15d, 1
    jl  pc_resume
    cmp r15d, 5
    jg  pc_resume
    call {FLAG_MANAGER_ACCESSOR_VA}
    mov rcx, rax
    lea edx, [r15 + {CF_CUSTOM_BASE_INDEX - 1}]
    call {GETFLAG_VA}
    test al, al
    jnz pc_resume
    lea rcx, [{feedback_str_va}]
    call {FUN_1403F0060_VA}
    jmp {PORTAL_FAIL_EXIT_VA}
pc_resume:
    mov edx, r15d
    jmp {PORTAL_RESUME_VA}
"""


def _pickup_check_text(table_va: int) -> str:
    """Walks the 5-entry TABLE comparing R14's own position (still live;
    register safety for R8/R9/R10/R11/RAX live-confirmed 2026-08-17 -- see
    module docstring's Hook B section) against each entry as raw 32-bit
    patterns. On a match, calls SetFlag(this, CF_CUSTOM_BASE_INDEX+world_id-1)
    -- the real save-backed flag, not a scratch byte -- via the same
    this->vtable[1] dispatch FUN_14047d920 already uses for the vanilla
    CF_RETRACTOR0XUSED flags. world_id is stashed in r15d (non-volatile)
    across that call; R14 (the picked-up object, needed by vanilla after
    resume) is also non-volatile and expected to survive both the
    FLAG_MANAGER_ACCESSOR_VA call and the vtable-dispatched SetFlag call
    automatically -- still needs a live check, same as everything else here.
    Always falls through to re-running the overwritten LEA and resuming
    vanilla -- a non-match (shouldn't happen for a real RSC_X_RETRACT*
    pickup) just means no flag gets set, never blocks the pickup itself."""
    return f"""
    xor r11d, r11d
pick_loop:
    cmp r11d, 5
    jge pick_done
    mov r10, {table_va}
    mov r8d, r11d
    shl r8d, 4
    add r10, r8
    mov eax, dword ptr [r14+0x20]
    cmp eax, dword ptr [r10+0x00]
    jne pick_next
    mov eax, dword ptr [r14+0x24]
    cmp eax, dword ptr [r10+0x04]
    jne pick_next
    mov eax, dword ptr [r14+0x28]
    cmp eax, dword ptr [r10+0x08]
    jne pick_next
    movzx r15d, byte ptr [r10+0x0c]
    call {FLAG_MANAGER_ACCESSOR_VA}
    mov r10, rax
    mov rax, qword ptr [r10]
    mov rcx, r10
    lea edx, [r15 + {CF_CUSTOM_BASE_INDEX - 1}]
    call qword ptr [rax+{SETFLAG_VTABLE_OFF}]
    jmp pick_done
pick_next:
    inc r11d
    jmp pick_loop
pick_done:
    lea rcx, [{GN0056S_STR_VA}]
    jmp {PICKUP_RESUME_VA}
"""


def _use_check_text(feedback_str_va: int) -> str:
    """HOOK C -- the real fix for consumption/animation (see module
    docstring). Splices right after FUN_1404674a0 sets RDI = player pointer,
    reading the pending portal object from [RDI+0x318] (the SAME field
    vanilla itself reads a few instructions later, at 0x140467550, right
    before calling FUN_14047d920 -- never written within this function, so
    it must already be valid this early) and its +0x120 world id, gated
    against the same GetFlag(this, CF_CUSTOM_BASE_INDEX+world_id-1) check
    portal_check uses. Owned -> re-executes the overwritten CALL and falls
    through to vanilla exactly as before (consume + camera-mode change +
    animation + open, all untouched). Not owned -> plays the feedback line
    and jumps straight to the function's own shared cleanup/return tail
    (LAB_14046759c) -- skipping the retractor-count decrement, the
    camera-mode write believed to gate the insertion cutscene, the
    animation trigger, AND the call into FUN_14047d920 entirely, not just
    blocking the open. r15d holds world_id (non-volatile) across the
    GetFlag call; RDI itself is also non-volatile and expected to survive
    our calls automatically -- needs live confirmation like everything
    else new here."""
    return f"""
    mov rax, qword ptr [rdi+0x318]
    test rax, rax
    jz  uc_owned
    mov r15d, dword ptr [rax+0x120]
    cmp r15d, 1
    jl  uc_owned
    cmp r15d, 5
    jg  uc_owned
    call {FLAG_MANAGER_ACCESSOR_VA}
    mov rcx, rax
    lea edx, [r15 + {CF_CUSTOM_BASE_INDEX - 1}]
    call {GETFLAG_VA}
    test al, al
    jnz uc_owned
    lea rcx, [{feedback_str_va}]
    call {FUN_1403F0060_VA}
    jmp {USE_CLEANUP_RETURN_VA}
uc_owned:
    call {CAMERA_MGR_GETTER_VA}
    jmp {USE_RESUME_VA}
"""


def resolve_retractor_destinations(retractor_level_assignment, progression_placement):
    """{native_loc_key: destination_loc_key} -- for each of the 5 retractor
    identities in retractor_level_assignment, where progression_placement
    actually put it this seed. Shared by build_retractor_table() (needs the
    destination to look up its real position) and patcher.py's Five Keys
    folder-page builder (needs the destination to describe where the player
    actually found it, e.g. "Recovered from: <friendly location>").

    See build_retractor_table()'s docstring for why this indirection exists:
    retractor_level_assignment's own keys are NATIVE (vanilla) locations --
    an identity marker, not where the shuffle actually placed the reward.
    """
    _destination_by_native: dict[str, str] = {}
    for dest_loc_key, source_loc in progression_placement.items():
        if getattr(source_loc, "category", None) != "retractor":
            continue
        native_loc_key = getattr(source_loc, "loc_key", None)
        if native_loc_key in retractor_level_assignment:
            _destination_by_native[native_loc_key] = dest_loc_key
    return _destination_by_native


def build_retractor_table(retractor_level_assignment, progression_placement, scanned_positions):
    """Bridges patcher.py's already-computed per-seed structures into the
    [(x, y, z, world_id), ...] list apply_patch() needs.

    retractor_level_assignment: {native_loc_key: liveside_region_name}, from
      fill.py's assumed_fill() (unique_retractor_keys=True) -- each of the 5
      NATIVE (vanilla) retractor pickup locations, tagged with which liveside
      world it's assigned to unlock this seed. This is purely an IDENTITY
      marker -- the actual reward gets shuffled to any level by the normal
      item placement pipeline, same as every other progression item
      (confirmed live 2026-08-18, Jon: "these items can move to any level").

    progression_placement: {destination_loc_key: source_loc}, fill.py's main
      placement dict (patcher.py's own `progression_placement`). source_loc
      is the original item object -- source_loc.loc_key is its NATIVE
      identity (the same key retractor_level_assignment is keyed by) and
      source_loc.category == "retractor" flags the 5 relevant entries. This
      is what actually tells us where each retractor identity ended up.

    scanned_positions: {loc_key: (x, y, z, zone)}, patcher.py's post-placement
      RSC scan (Step 4b.6), keyed by the same loc_key scheme
      (folder:source_file:0xOFFSET) progression_placement uses -- gives the
      real on-disk (x, y, z) for the destination slot, including any
      y_adjust patch_rsc_file already baked in.

    2026-08-18 REWRITE: the original version grouped scanned retractor
    objects by LEVEL and matched against retractor_level_assignment's own
    (NATIVE) loc_key's level -- silently assuming a retractor is never
    shuffled out of its own native level. That assumption is wrong by
    design and broke on the first live test (as2exper -- one of the 5
    native retractor levels -- scanned 0 retractor objects because that
    seed's shuffle moved its native retractor elsewhere and put something
    else there instead). This version resolves each retractor's ACTUAL
    destination via progression_placement instead of assuming it never
    moved, so it's correct regardless of where the shuffle sends it.

    Hard-fails (matching this codebase's existing _HARD_FAIL_BUNDLES
    discipline in patcher.py -- refuse to silently generate a seed the exe
    patch can't actually enforce) if:
      - retractor_level_assignment isn't exactly 5 entries,
      - a liveside region name isn't in LIVESIDE_TO_IVAR1,
      - a retractor's native identity can't be found as a "retractor"
        category entry anywhere in progression_placement (most likely a
        starting-item bundle moved it off the normal placement pipeline
        entirely -- callers should already guard for that combination
        before calling this, see patcher.py's existing _HARD_FAIL_BUNDLES
        pattern, but this is a second line of defense),
      - a resolved destination loc_key has no scanned position (would mean
        the RSC scan and the placement data disagree -- needs investigation,
        not expected in normal operation).
    """
    if len(retractor_level_assignment) != 5:
        raise ValueError(
            f"build_retractor_table: expected exactly 5 retractor loc_key "
            f"assignments, got {len(retractor_level_assignment)}"
        )

    _destination_by_native = resolve_retractor_destinations(
        retractor_level_assignment, progression_placement
    )

    table = []
    for native_loc_key, liveside_name in retractor_level_assignment.items():
        world_id = LIVESIDE_TO_IVAR1.get(liveside_name)
        if world_id is None:
            raise ValueError(
                f"build_retractor_table: unrecognized liveside region "
                f"'{liveside_name}' for loc_key '{native_loc_key}' -- not in LIVESIDE_TO_IVAR1"
            )

        dest_loc_key = _destination_by_native.get(native_loc_key)
        if dest_loc_key is None:
            raise ValueError(
                f"build_retractor_table: could not find where retractor "
                f"'{native_loc_key}' (assigned to {liveside_name}) was "
                f"actually placed -- no 'retractor'-category entry for it in "
                f"progression_placement. Likely a starting-item bundle moved "
                f"it off the normal placement pipeline entirely."
            )

        pos = scanned_positions.get(dest_loc_key)
        if pos is None:
            raise ValueError(
                f"build_retractor_table: retractor '{native_loc_key}' "
                f"(assigned to {liveside_name}) was placed at "
                f"'{dest_loc_key}' but no scanned position was found there "
                f"-- RSC scan and placement disagree, needs investigation."
            )
        x, y, z, _zone = pos
        table.append((x, y, z, world_id))

    if len(table) != 5:
        raise ValueError(
            f"build_retractor_table: resolved {len(table)}/5 retractor positions"
        )

    table.sort(key=lambda e: e[3])  # cosmetic -- order output by world_id
    return table


def build_retractor_table_from_ap(retractor_key_locs, scanned_positions):
    """AP entry point (2026-08-18) -- ap_patcher.py's equivalent of
    build_retractor_table() above, but simpler: AP's own Fill has already
    resolved "which loc_key does each region's key end up at" for us, so
    there's no native-identity/destination-resolution indirection to redo
    here (contrast build_retractor_table()'s docstring, which exists
    entirely to work around NOT having that information directly).

    retractor_key_locs: {liveside_region_name: loc_key}, built by the AP
      world's generate_output() (ShadowManWorld, worlds/shadowman/__init__.py)
      from the real per-seed Fill placement of the 5 named
      "Retractor - <region>" items -- each key is one of LIVESIDE_TO_IVAR1's
      5 region names, each value is the loc_key of whichever location Fill
      actually placed that specific item's physical pickup at in THIS
      player's world. Read out of the .apshadowman JSON's top-level
      "retractor_key_locs" key (apply_ap_seed.py) or passed in-process
      (AP's own generate_output() calling run_patcher() directly, if that
      path is ever wired up).

    scanned_positions: {loc_key: (x, y, z, zone)}, same shape
      build_retractor_table() expects -- ap_patcher.py's own Step 1
      records_by_folder scan, flattened and keyed by loc_key.

    retractor_key_locs may legitimately hold anywhere from 0 to 5 entries
    (2026-08-18 fix, Jon's live repro: this used to hard-fail with
    "expected exactly 5 ... got 4"). generate_output() only ever includes
    SELF-FOUND placements -- a copy Fill sent to another player's game has
    no physical pickup in THIS player's world at all (see that function's
    own comment), so any real multiplayer seed can land anywhere from 0 to
    5 of the 5 named keys in your own world. That's expected, not an error.

    The exe's own data layout is still a FIXED 5-slot table regardless
    (TABLE_ENTRIES=5, hardcoded into both build_data_section()'s struct
    packing AND _pickup_check_text()'s generated loop bound -- literal
    `cmp r11d, 5` -- neither can be made variable-length without hand-
    patching the generated assembly), so a world_id with no self-found
    location here still gets a real, structurally valid table SLOT --
    just with a sentinel position (NaN x/y/z) that can never bit-for-bit
    match any real in-game object's actual coordinates (the pickup-check
    loop does a raw 32-bit integer compare against the picked-up object's
    live position, not a semantic/float comparison, so any bit pattern
    that no real level geometry will ever produce works as "never
    matches" -- NaN chosen because it's obviously not a real coordinate
    and can't arise from legitimate level data). Hook B's scan loop
    simply never fires for that slot. The corresponding CF_CUSTOM flag
    for that world_id is instead expected to be set REMOTELY, by
    client.py's write_named_flag() call, the moment AP delivers that
    specific named retractor item over the network instead of via a
    physical pickup -- see that function's own comment in client.py for
    the full split between "native pickup sets the flag via Hook B here"
    and "remote grant sets the flag directly over there."

    Still hard-fails (same discipline as build_retractor_table()) if a
    region name isn't in LIVESIDE_TO_IVAR1, or a resolved loc_key has no
    scanned position -- both indicate a real data problem, unlike "some
    keys are remote," which is the one case this function now tolerates.
    """
    table_by_world_id = {}
    for region_name, loc_key in retractor_key_locs.items():
        world_id = LIVESIDE_TO_IVAR1.get(region_name)
        if world_id is None:
            raise ValueError(
                f"build_retractor_table_from_ap: unrecognized liveside region "
                f"'{region_name}' in retractor_key_locs -- not in LIVESIDE_TO_IVAR1"
            )

        pos = scanned_positions.get(loc_key)
        if pos is None:
            raise ValueError(
                f"build_retractor_table_from_ap: region '{region_name}' key "
                f"was placed at '{loc_key}' but no scanned position was found "
                f"there -- RSC scan and AP placement disagree, needs investigation."
            )
        x, y, z, _zone = pos
        table_by_world_id[world_id] = (x, y, z, world_id)

    # NaN sentinel for any world_id with no self-found location this seed
    # (its key was placed in another player's game) -- see docstring above.
    _NAN = float("nan")
    table = [
        table_by_world_id.get(world_id, (_NAN, _NAN, _NAN, world_id))
        for world_id in range(1, 6)
    ]
    return table


def build_code_section(code_va: int, data_va: int):
    """Three independent bodies (no cross-calls between them, so unlike
    secret_mode_section_patch.py's tick_entry/check_secret pair, no two-pass
    far-placeholder trick is needed -- every jmp/call target here is either
    a fixed vanilla VA or a purely local, already-known-length label).
    Returns (code_bytes, portal_va, pickup_va, use_va)."""
    table_va, feedback_str_va = _data_layout(data_va)
    portal_bytes = _asm(_portal_check_text(feedback_str_va), code_va)
    pickup_va = code_va + len(portal_bytes)
    pickup_bytes = _asm(_pickup_check_text(table_va), pickup_va)
    use_va = pickup_va + len(pickup_bytes)
    use_bytes = _asm(_use_check_text(feedback_str_va), use_va)
    return portal_bytes + pickup_bytes + use_bytes, code_va, pickup_va, use_va


def build_data_section(retractor_table) -> bytes:
    """retractor_table: list of exactly 5 (x, y, z, world_id) tuples, x/y/z
    as Python floats (32-bit precision expected -- these must be the EXACT
    same bit pattern the RSC record itself carries, i.e. read back with
    struct '<fff' the same way patcher.py's retractor_actual_xyz scan
    already does, not re-typed/rounded by hand), world_id in 1..5.

    No more OWNED[] byte array (2026-08-17) -- ownership lives in the
    game's own save-backed CF_CUSTOM00-04 flags now, see the module
    docstring's PERSISTENCE section.
    TABLE[5*16] -- one 16-byte entry per table row: x(f32) y(f32) z(f32)
                   world_id(u8) + 3 pad bytes.
    FEEDBACK_STR[40] -- null-terminated, zero-padded.
    """
    assert len(retractor_table) == TABLE_ENTRIES, \
        f"expected exactly {TABLE_ENTRIES} retractor table entries, got {len(retractor_table)}"
    table = bytearray()
    seen_ids = set()
    for x, y, z, world_id in retractor_table:
        assert 1 <= world_id <= 5, f"world_id must be 1..5, got {world_id}"
        assert world_id not in seen_ids, f"duplicate world_id {world_id} in retractor_table"
        seen_ids.add(world_id)
        table += struct.pack("<fffB3x", x, y, z, world_id)
    feedback = FEEDBACK_STR + bytes(FEEDBACK_STR_SIZE - len(FEEDBACK_STR))
    return bytes(table) + feedback


def _align_up(v: int, a: int) -> int:
    return (v + a - 1) // a * a


def _make_jmp_hook(hook_va: int, vanilla: bytes, target_va: int) -> bytes:
    disp = target_va - (hook_va + 5)
    patch = b"\xE9" + struct.pack("<i", disp)
    patch += b"\x90" * (len(vanilla) - len(patch))
    return patch


def verify_with_capstone(code: bytes, portal_va: int, pickup_va: int, use_va: int,
                          table_va: int) -> list:
    """Independent structural re-check, same rigor/spirit as
    secret_mode_section_patch.py's verify_with_capstone(). Confirms control
    flow shape, not runtime register-liveness correctness -- the
    non-volatile-register-survives-our-CALLs argument used throughout the
    GetFlag/SetFlag rewrite (see module docstring's PERSISTENCE section) is
    NOT something static disassembly of OUR OWN bytes can confirm; that
    still needs a live test against the real exe before it's trusted."""
    errors = []

    def disasm(addr, blob):
        return list(_cs.disasm(bytes(blob), addr))

    def imm_targets(insns, mnemonics):
        return [i.operands[0].imm for i in insns
                if i.mnemonic in mnemonics and i.operands and i.operands[0].type == X86_OP_IMM]

    def calls_to(insns, target_va):
        return target_va in imm_targets(insns, ("call",))

    portal_bytes = code[0:pickup_va - portal_va]
    pickup_bytes = code[pickup_va - portal_va:use_va - portal_va]
    use_bytes = code[use_va - portal_va:]

    # portal_check (backstop): must jmp to both PORTAL_RESUME_VA and
    # PORTAL_FAIL_EXIT_VA, must call the flag accessor + GetFlag + the
    # feedback-audio function.
    p_insns = disasm(portal_va, portal_bytes)
    p_jmps = imm_targets(p_insns, ("jmp",))
    if PORTAL_RESUME_VA not in p_jmps:
        errors.append(f"portal_check: no jmp to PORTAL_RESUME_VA (0x{PORTAL_RESUME_VA:X})")
    if PORTAL_FAIL_EXIT_VA not in p_jmps:
        errors.append(f"portal_check: no jmp to PORTAL_FAIL_EXIT_VA (0x{PORTAL_FAIL_EXIT_VA:X})")
    if not calls_to(p_insns, FLAG_MANAGER_ACCESSOR_VA):
        errors.append(f"portal_check: no call to FLAG_MANAGER_ACCESSOR_VA (0x{FLAG_MANAGER_ACCESSOR_VA:X})")
    if not calls_to(p_insns, GETFLAG_VA):
        errors.append(f"portal_check: no call to GETFLAG_VA (0x{GETFLAG_VA:X})")
    if not calls_to(p_insns, FUN_1403F0060_VA):
        errors.append(f"portal_check: no call to FUN_1403F0060_VA (0x{FUN_1403F0060_VA:X})")

    # pickup_check: must jmp to PICKUP_RESUME_VA, must reference table_va as
    # an immediate, must have exactly 3 dword compares against
    # [r14+0x20/0x24/0x28] (the position match), must call the flag accessor
    # and then indirectly through a [reg+SETFLAG_VTABLE_OFF] call (the
    # vtable-dispatched SetFlag).
    k_insns = disasm(pickup_va, pickup_bytes)
    k_jmps = imm_targets(k_insns, ("jmp",))
    if PICKUP_RESUME_VA not in k_jmps:
        errors.append(f"pickup_check: no jmp to PICKUP_RESUME_VA (0x{PICKUP_RESUME_VA:X})")
    if not any(op.type == X86_OP_IMM and op.imm == table_va
               for i in k_insns for op in i.operands):
        errors.append(f"pickup_check: table_va (0x{table_va:X}) immediate not found")
    if not calls_to(k_insns, FLAG_MANAGER_ACCESSOR_VA):
        errors.append(f"pickup_check: no call to FLAG_MANAGER_ACCESSOR_VA (0x{FLAG_MANAGER_ACCESSOR_VA:X})")
    # Position reads happen as `mov reg, dword ptr [r14+disp]` (loaded into a
    # register, then separately cmp'd against the table entry) -- NOT as a
    # direct `cmp ..., [r14+disp]`, so this must scan `mov` loads, not `cmp`.
    r14_load_disps = sorted(
        i.operands[1].mem.disp for i in k_insns
        if i.mnemonic == "mov" and len(i.operands) == 2
        and i.operands[1].type == X86_OP_MEM and i.operands[1].mem.base != 0
        and _cs.reg_name(i.operands[1].mem.base) == "r14"
    )
    if r14_load_disps != [0x20, 0x24, 0x28]:
        errors.append(f"pickup_check: expected mov loads from [r14+0x20/0x24/0x28], found disps {r14_load_disps}")
    # The vtable-dispatched SetFlag call: `call qword ptr [reg+8]`.
    vtable_calls = [i for i in k_insns if i.mnemonic == "call" and i.operands
                    and i.operands[0].type == X86_OP_MEM and i.operands[0].mem.disp == SETFLAG_VTABLE_OFF]
    if not vtable_calls:
        errors.append(f"pickup_check: no vtable-dispatched SetFlag call "
                       f"(call qword ptr [reg+{SETFLAG_VTABLE_OFF}]) found")

    # use_check (Hook C, the real fix): must jmp to both USE_RESUME_VA and
    # USE_CLEANUP_RETURN_VA, must call the flag accessor, GetFlag, the
    # feedback-audio function, and re-execute CAMERA_MGR_GETTER_VA.
    u_insns = disasm(use_va, use_bytes)
    u_jmps = imm_targets(u_insns, ("jmp",))
    if USE_RESUME_VA not in u_jmps:
        errors.append(f"use_check: no jmp to USE_RESUME_VA (0x{USE_RESUME_VA:X})")
    if USE_CLEANUP_RETURN_VA not in u_jmps:
        errors.append(f"use_check: no jmp to USE_CLEANUP_RETURN_VA (0x{USE_CLEANUP_RETURN_VA:X})")
    if not calls_to(u_insns, FLAG_MANAGER_ACCESSOR_VA):
        errors.append(f"use_check: no call to FLAG_MANAGER_ACCESSOR_VA (0x{FLAG_MANAGER_ACCESSOR_VA:X})")
    if not calls_to(u_insns, GETFLAG_VA):
        errors.append(f"use_check: no call to GETFLAG_VA (0x{GETFLAG_VA:X})")
    if not calls_to(u_insns, FUN_1403F0060_VA):
        errors.append(f"use_check: no call to FUN_1403F0060_VA (0x{FUN_1403F0060_VA:X})")
    if not calls_to(u_insns, CAMERA_MGR_GETTER_VA):
        errors.append(f"use_check: no call to CAMERA_MGR_GETTER_VA (0x{CAMERA_MGR_GETTER_VA:X})")
    # [rdi+0x318] load must appear (the pending-portal-object read).
    if not any(i.mnemonic == "mov" and len(i.operands) == 2
               and i.operands[1].type == X86_OP_MEM and i.operands[1].mem.base != 0
               and _cs.reg_name(i.operands[1].mem.base) == "rdi" and i.operands[1].mem.disp == 0x318
               for i in u_insns):
        errors.append("use_check: no mov load from [rdi+0x318] found (expected pending-portal-object read)")

    return errors


def _va_to_text_file(va: int) -> int:
    from death_penalty_patch import SECTION_DELTA
    return va - IMAGE_BASE - SECTION_DELTA


def revert_patch(exe_path: str, dry_run: bool = True):
    """Restores all three hook sites to vanilla. Does NOT remove the appended
    .rkcode/.rkdata sections -- same rationale as secret_mode_section_patch.py:
    unreferenced-but-present is harmless, and removing them would be a
    riskier second round of header surgery for no real benefit."""
    path = Path(exe_path)
    data = bytearray(path.read_bytes())
    reverted_any = False
    for name, va, vanilla in (
        ("portal", PORTAL_HOOK_VA, PORTAL_HOOK_VANILLA),
        ("pickup", PICKUP_HOOK_VA, PICKUP_HOOK_VANILLA),
        ("use", USE_HOOK_VA, USE_HOOK_VANILLA),
    ):
        off = _va_to_text_file(va)
        current = bytes(data[off:off + len(vanilla)])
        print(f"{name} hook currently: {current.hex()}  (vanilla: {vanilla.hex()})")
        if current == vanilla:
            print(f"  {name}: already vanilla, nothing to revert")
            continue
        reverted_any = True
        if not dry_run:
            data[off:off + len(vanilla)] = vanilla
    if dry_run:
        print("dry_run=True -- not modifying the file.")
        return
    if reverted_any:
        path.write_bytes(bytes(data))
        print("Reverted hook site(s) to vanilla bytes.")


def apply_patch(exe_path: str, retractor_table, dry_run: bool = True,
                 verify_only: bool = False, force: bool = False):
    """retractor_table: list of exactly 5 (x, y, z, world_id) tuples -- see
    build_data_section()'s docstring. Caller (patcher.py) is responsible for
    building this from retractor_level_assignment + retractor_actual_xyz +
    LIVESIDE_TO_IVAR1 (TODO, not yet wired -- see module docstring)."""
    path = Path(exe_path)
    data = bytearray(path.read_bytes())

    off_a = _va_to_text_file(PORTAL_HOOK_VA)
    off_b = _va_to_text_file(PICKUP_HOOK_VA)
    off_c = _va_to_text_file(USE_HOOK_VA)
    actual_a = bytes(data[off_a:off_a + len(PORTAL_HOOK_VANILLA)])
    actual_b = bytes(data[off_b:off_b + len(PICKUP_HOOK_VANILLA)])
    actual_c = bytes(data[off_c:off_c + len(USE_HOOK_VANILLA)])

    if actual_a != PORTAL_HOOK_VANILLA or actual_b != PICKUP_HOOK_VANILLA or actual_c != USE_HOOK_VANILLA:
        msg = (f"hook site(s) do not match expected vanilla bytes -- "
               f"portal got {actual_a.hex()} (expected {PORTAL_HOOK_VANILLA.hex()}), "
               f"pickup got {actual_b.hex()} (expected {PICKUP_HOOK_VANILLA.hex()}), "
               f"use got {actual_c.hex()} (expected {USE_HOOK_VANILLA.hex()}). "
               f"Already patched, or wrong exe. Re-running --apply against an "
               f"already-patched file would append a SECOND .rkcode/.rkdata pair "
               f"at the same VAs as the first, corrupting the PE image -- start "
               f"from a fresh vanilla copy instead.")
        if verify_only:
            print(f"WARNING (verify-only, not aborting): {msg}")
        else:
            print(f"ABORTING: {msg}")
            if not force:
                raise RuntimeError(
                    "unique_retractor_keys_patch.apply_patch: refusing to patch an "
                    "already-patched (or non-vanilla) exe -- see the ABORTING message above.")

    # --- Parse header fields needed for placement BEFORE building the code/
    # data sections (moved up from the tail end of this function) -- the new
    # sections' VAs are no longer hardcoded, they're derived from the target
    # file's own current SizeOfImage (see _compute_new_vas()'s docstring for
    # why: a fixed "+2 pages of headroom" constant is what caused the
    # original launch-failure bug this replaced).
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    coff_off = pe_off + 4
    num_sections = struct.unpack_from("<H", data, coff_off + 2)[0]
    size_opt_hdr = struct.unpack_from("<H", data, coff_off + 16)[0]
    opt_off = coff_off + 20
    section_alignment = struct.unpack_from("<I", data, opt_off + 32)[0]
    size_of_image_off = opt_off + 56
    size_of_image = struct.unpack_from("<I", data, size_of_image_off)[0]

    new_code_va, new_data_va = _compute_new_vas(data)

    code, portal_va, pickup_va, use_va = build_code_section(new_code_va, new_data_va)
    table_va, feedback_str_va = _data_layout(new_data_va)
    errors = verify_with_capstone(code, portal_va, pickup_va, use_va, table_va)
    if errors:
        print("CAPSTONE VERIFICATION FAILED:")
        for e in errors:
            print("  -", e)
        raise SystemExit(1)
    print(f"Capstone verification passed. Code section: {len(code)} bytes "
          f"(portal_check@0x{portal_va:X} len={pickup_va - portal_va}, "
          f"pickup_check@0x{pickup_va:X} len={use_va - pickup_va}, "
          f"use_check@0x{use_va:X} len={len(code) - (use_va - portal_va)})")

    data_blob = build_data_section(retractor_table)

    if verify_only:
        print("verify_only=True -- not modifying the file.")
        return

    code_raw_ptr = _align_up(len(data), FILE_ALIGNMENT)
    code_raw_size = _align_up(len(code), FILE_ALIGNMENT)
    data_raw_ptr = code_raw_ptr + code_raw_size
    data_raw_size = _align_up(len(data_blob), FILE_ALIGNMENT)

    code_rva = new_code_va - IMAGE_BASE
    data_rva = new_data_va - IMAGE_BASE
    if data_rva != code_rva + SECTION_ALIGNMENT:
        raise ValueError("computed data VA is not exactly one page after code VA -- "
                          "code section grew past 4KB? _compute_new_vas() needs revisiting.")

    print(f"code section: RVA 0x{code_rva:X}  RawPtr 0x{code_raw_ptr:X}  RawSize 0x{code_raw_size:X}")
    print(f"data section: RVA 0x{data_rva:X}  RawPtr 0x{data_raw_ptr:X}  RawSize 0x{data_raw_size:X}")

    if dry_run:
        print("dry_run=True -- not modifying the file. Re-run with dry_run=False to apply.")
        return

    data.extend(b"\x00" * (code_raw_ptr - len(data)))
    data.extend(code)
    data.extend(b"\x00" * (code_raw_size - len(code)))
    data.extend(data_blob)
    data.extend(b"\x00" * (data_raw_size - len(data_blob)))

    # --- PE header surgery (identical shape to secret_mode_section_patch.py --
    # see that file's apply_patch() for the "why in-place overwrite, not
    # insert" note; same logic applies here verbatim) ---
    sect_table_off = opt_off + size_opt_hdr
    new_sect_table_end = sect_table_off + (num_sections + 2) * 40
    first_raw_ptr = min(struct.unpack_from("<I", data, sect_table_off + i * 40 + 20)[0]
                         for i in range(num_sections))
    if new_sect_table_end > first_raw_ptr:
        raise ValueError("Not enough header slack for 2 new section headers -- "
                          "re-run tools/check_pe_headers.py to confirm current slack.")

    def _section_header(name: bytes, virt_size: int, virt_addr: int,
                         raw_size: int, raw_ptr: int, chars: int) -> bytes:
        name8 = name[:8].ljust(8, b"\x00")
        return struct.pack("<8sIIIIIIHHI", name8, virt_size, virt_addr,
                            raw_size, raw_ptr, 0, 0, 0, 0, chars)

    R = 0x40000000
    W = 0x80000000
    X = 0x20000000
    CODE = 0x00000020
    INIT_DATA = 0x00000040

    code_hdr = _section_header(b".rkcode", len(code), code_rva, code_raw_size, code_raw_ptr, CODE | X | R)
    data_hdr = _section_header(b".rkdata", len(data_blob), data_rva, data_raw_size, data_raw_ptr, INIT_DATA | R | W)

    new_hdrs = code_hdr + data_hdr
    insert_off = sect_table_off + num_sections * 40
    data[insert_off:insert_off + len(new_hdrs)] = new_hdrs

    struct.pack_into("<H", data, coff_off + 2, num_sections + 2)

    new_size_of_image = _align_up(data_rva + section_alignment, section_alignment)
    if new_size_of_image > size_of_image:
        struct.pack_into("<I", data, size_of_image_off, new_size_of_image)

    # --- .text hook patches ---
    hook_a = _make_jmp_hook(PORTAL_HOOK_VA, PORTAL_HOOK_VANILLA, portal_va)
    data[off_a:off_a + len(hook_a)] = hook_a
    hook_b = _make_jmp_hook(PICKUP_HOOK_VA, PICKUP_HOOK_VANILLA, pickup_va)
    data[off_b:off_b + len(hook_b)] = hook_b
    hook_c = _make_jmp_hook(USE_HOOK_VA, USE_HOOK_VANILLA, use_va)
    data[off_c:off_c + len(hook_c)] = hook_c

    path.write_bytes(bytes(data))
    print(f"Patched. portal_check(backstop) -> 0x{portal_va:X}, pickup_check -> 0x{pickup_va:X}, "
          f"use_check -> 0x{use_va:X}, data @ 0x{new_data_va:X}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("exe")
    p.add_argument("--dry-run", action="store_true", default=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--verify-only", action="store_true")
    p.add_argument("--revert", action="store_true")
    p.add_argument("--force", action="store_true")
    args = p.parse_args()
    if args.revert:
        revert_patch(args.exe, dry_run=not args.apply)
    else:
        # Placeholder table for standalone script testing only -- real
        # per-seed values come from patcher.py's build_retractor_table()
        # (wired into run_patcher(), see the investigation doc). FIXED
        # 2026-08-17: these 5 x/y/z values used to be hand-typed from the
        # investigation doc's 4-decimal-place coordinate table -- close
        # enough to read, but not guaranteed to round-trip to the exact
        # same float32 bit pattern the live pickup object actually carries,
        # and Hook B's position match is an exact 32-bit compare with no
        # tolerance. Confirmed live: with the old truncated values, all 5
        # CF_CUSTOM flags stayed 0 after a real pickup -- the match never
        # fired for any world_id. Re-derived byte-exact from the reference
        # RSC files themselves (struct.unpack_from('<fff', ...) at XYZ_OFF
        # within each level's one RSC_X_RETRACT*/instance-or-quest.rsc
        # record -- same method and offsets patcher.py's own Step 4b.6 scan
        # uses), not retyped by hand -- round-trip-verified to reproduce
        # the identical raw bytes read from each file. World-id mapping
        # matches the original placeholder ordering (Experimentation
        # Rooms=1, Cageways=2, Playrooms=3, Lavaducts=4, Fogometers=5).
        _placeholder_table = [
            (3259.05810546875, 434.6064758300781, -18166.19140625, 1),    # as2exper (Experimentation Rooms)
            (16406.490234375, 945.2928466796875, 17216.921875, 2),        # ah1cagew (Cageways)
            (1305.9588623046875, 2225.28857421875, -8125.869140625, 3),   # ah2playr (Playrooms)
            (24698.18359375, 4133.4375, 23727.26953125, 4),               # ah3lavad (Lavaducts) -- instance.rsc, not quest.rsc
            (601.352783203125, 1712.34423828125, 13794.140625, 5),        # ah4fogom (Fogometers)
        ]
        apply_patch(args.exe, _placeholder_table, dry_run=not args.apply,
                    verify_only=args.verify_only, force=args.force)
