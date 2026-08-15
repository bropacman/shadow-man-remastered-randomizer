"""
secret_mode_section_patch.py
==============================
Live-apply patch for g_dogmode (and, once confirmed working, other
g_<name>mode secret cvars) using TWO NEW PE SECTIONS appended to
thoth_x64_patched.exe, instead of scavenging .rdata/.data "probably dead"
bytes or chaining fragments through scattered .text CC-padding gaps.

Why this design (superseding secret_mode_live_patch.py / dispatcher.py):
  - Contiguity: LAST_KNOWN tracking lives in one real, contiguous block we
    own, not scattered 16-byte gaps walked by pointer arithmetic (the
    earlier design's table-contiguity bug).
  - W^X safety: the new data section is genuinely Read+Write (we choose
    its Characteristics), so LAST_KNOWN/ALIGN_SLOT writes are legal --
    .text is Read+Execute only (confirmed via Jon's Ghidra Memory Map,
    2026-07-31) and would fault on a write, which is what the earlier
    in-.text-cave design would have hit.
  - No guessing: unlike reusing .rdata/.data zero-runs (ambiguous whether
    truly dead or a live runtime buffer), this is space that did not
    exist in the file before we added it -- zero ambiguity about
    ownership.

HOOK SITE HISTORY (important, don't repeat this dead end): the first two
versions of this patch hooked FUN_14046b2c0 (land movement update) and
FUN_14046b590 (swim movement update) directly, each requiring its own
register-preservation stub. Land worked cleanly, live-tested 2026-07-31.
Swim crashed the game on every water entry, even on a freshly-reverted-
to-vanilla exe with only this patch applied (ruling out a stale-patch
interaction). Root-caused via Jon pulling the raw disassembly of the
actual dispatcher, FUN_14045d7f0 (the single per-frame player tick
function that calls EITHER land or swim update based on a flag, then
continues into a long tail of per-frame work regardless of which branch
ran): FUN_14046b590 is called with no visible argument in Ghidra's
decompile (unlike FUN_14046b2c0(param_1), which uses the standard RCX
argument), meaning it very likely uses a non-standard internal ABI (its
"mov rsp,r13" / R11-based access pattern is part of that same custom
convention, not a stray implementation detail) -- exactly why splicing
into its guts kept being fragile no matter how carefully the stack
reordering was done (see git history / CLAUDE.md for the abandoned
per-leaf-function reorder attempt).

FIX: hook the dispatcher instead, at the exact point where the land and
swim branches merge back together (LAB_14045da0d in FUN_14045d7f0 --
CALL FUN_14046b590 falls through a JMP to this label; CALL FUN_14046b2c0
falls straight into it). At that address the code is back in the
dispatcher's own completely normal calling convention (RDI holds the
player pointer, confirmed live both immediately before and after --
CMP byte ptr [RDI+0x1d65d],R14B is the very instruction being replaced),
with no stack tricks anywhere nearby -- the same shape as land's splice
point that already worked, just positioned so it runs exactly once per
frame regardless of which branch was taken. This also confirms land and
swim were never running on different threads (a live theory at one
point) -- they're both invoked from this one single-threaded dispatcher,
so the earlier swim crash really was about that leaf function's own
non-standard ABI, not thread affinity.

Feasibility of the new PE sections confirmed via tools/check_pe_headers.py
against Jon's real thoth_x64_patched.exe (2026-07-31): 184 bytes of
section-table header slack (room for 4 more section headers), current
EOF at file 0xD4D800, SectionAlignment 0x1000 / FileAlignment 0x200,
ImageBase 0x140000000.

GENERALIZED 2026-07-31, per Jon: the single-cvar design above was
confirmed live-working for g_dogmode with zero issues (both land and
water, instant apply, no level transition needed -- see CLAUDE.md), then
extended from one hardcoded cvar to a fixed table of 9 (SECRET_TABLE
below), covering every secret the live /cvarcallbacks survey found
sharing g_dogmode's exact callback pointer (0x140458EF0) -- i.e. the
literal same, already-proven-safe code path, just invoked with a
different cvar handle each time. The other 14 captured secrets (10 with
a null/no callback, 4 with their own distinct, independently-unverified
callback) are deliberately NOT included -- see CLAUDE.md's 2026-07-31
"Generalizing past g_dogmode" writeup for the full bucket breakdown and
why each remaining bucket needs separate handling before it's safe to
add here.

check_secret was generalized from one hardcoded check into a straight-
line unrolled sequence of 9 checks (one per SECRET_TABLE entry, each its
own cvar-VA/last-known-slot pair, all sharing the one CALLBACK_VA) rather
than a runtime-indexed loop -- keeps every generated block structurally
identical to the already-verified single-entry version (same registers,
same sub/call/add pattern, same alignment math, still ends in one `ret`),
and keeps Capstone verification a straightforward per-entry check instead
of having to reason about loop/index correctness in freshly-generated
machine code. tick_entry itself is unchanged -- it still just calls one
function once per frame; that function now does 9 checks instead of 1.

New sections (fixed VAs, valid as long as neither section's content
exceeds one page -- true here by a wide margin):
  .apcode   VA 0x14102B000   R+X   the dispatcher-hook code (tick_entry +
                                   the unrolled multi-secret check)
  .apdata   VA 0x14102C000   R+W   ALIGN_SLOT (qword) + LAST_KNOWN array
                                   (32 bytes -- headroom for future
                                   entries without moving ALIGN_SLOT)

Assembled with Keystone, independently re-verified with Capstone (both
already proven-useful tools in this codebase's patch-verification
workflow) before any bytes are considered trustworthy. Validated against
a synthetic PE built to match Jon's real header layout byte-for-byte:
full apply -> byte-level round trip (appended bytes re-read from the
file match the builder output exactly) -> disassembly re-read directly
from the file -> revert back to vanilla, before ever touching the real
exe. This exact process caught a real header-insertion bug in an earlier
version (see the comment above the section-header insertion below).

Not yet live-tested with this 9-entry design -- next step.
"""
import struct
import sys
from pathlib import Path

from keystone import Ks, KS_ARCH_X86, KS_MODE_64
import capstone

IMAGE_BASE = 0x140000000

# ---------------------------------------------------------------------
# The single hook site: the merge point right after FUN_14045d7f0's
# land/swim dispatch if-else converges. Confirmed via Jon's raw Ghidra
# Listing dump, 2026-07-31 -- byte-exact match against the vanilla
# instruction "CMP byte ptr [RDI+0x1d65d],R14B" at this address.
# ---------------------------------------------------------------------
DISPATCH_MERGE_VA = 0x14045DA0D
DISPATCH_MERGE_VANILLA = bytes.fromhex("4438b75dd60100")  # cmp byte ptr [rdi+0x1d65d],r14b
DISPATCH_RETURN_VA = DISPATCH_MERGE_VA + len(DISPATCH_MERGE_VANILLA)

# g_dogmode cvar object + its on-change callback -- both confirmed via
# live Cheat Engine capture, stable across restarts (fixed RVA, static
# array, not a heap allocation). See CLAUDE.md 2026-07-29/30 writeups.
DOGMODE_CVAR_VA = 0x140DBA740
CALLBACK_VA = 0x140458EF0

# Every secret cvar whose [handle+0x48] the live /cvarcallbacks survey
# confirmed reads exactly CALLBACK_VA (2026-07-31) -- i.e. every entry
# here shares the literal same, already-live-tested callback function,
# just called with a different cvar's own handle in RCX. Handles captured
# via the corrected RET-breakpoint method (see CLAUDE.md); g_dogmode's
# own row matches DOGMODE_CVAR_VA exactly, which is what validated the
# capture method in the first place. Order matches SECRET_CVAR_RVAS in
# client.py, and each entry's LAST_KNOWN slot is LAST_KNOWN_VA + index.
SECRET_TABLE = [
    ("g_bigheadmode",  0x140DB9FF0),
    ("g_discoclothes", 0x140DBA080),
    ("g_bigshoesmode", 0x140DBA2C0),
    ("g_stetsonmode",  0x140DBA350),
    ("g_nettiemode",   0x140DBA590),
    ("g_duppiemode",   0x140DBA620),
    ("g_deadwingmode", 0x140DBA6B0),
    ("g_dogmode",      DOGMODE_CVAR_VA),
    ("g_betamode",     0x140DBA860),
]

# ---------------------------------------------------------------------
# New section placement. Fixed as long as code+data content each stay
# under one page (0x1000 bytes) -- true here (a few hundred bytes total),
# so the *next* section's VA is always exactly +0x1000 regardless of the
# real content length. Confirmed feasible via tools/check_pe_headers.py
# against the real exe, 2026-07-31 (184 bytes header slack, EOF at file
# 0xD4D800).
# ---------------------------------------------------------------------
NEW_CODE_VA = 0x14102B000
NEW_DATA_VA = 0x14102C000
ALIGN_SLOT_VA = NEW_DATA_VA + 0x00      # qword scratch: caller's rsp, saved across the aligned call
LAST_KNOWN_VA = NEW_DATA_VA + 0x08      # base of a byte array, one slot per SECRET_TABLE entry
                                         # (index i -> LAST_KNOWN_VA + i): last-observed cvar value,
                                         # 0xFF sentinel = "never checked", can never false-match a
                                         # real 0/1 cvar value so the first poll always resyncs.
LAST_KNOWN_ARRAY_SIZE = 32              # headroom for future SECRET_TABLE growth without moving
                                         # ALIGN_SLOT or recomputing any other entry's offset

NEW_RAW_PTR_CODE = 0xD4D800             # current EOF of Jon's real exe, from check_pe_headers.py
FILE_ALIGNMENT = 0x200
SECTION_ALIGNMENT = 0x1000

LAST_KNOWN_SENTINEL = 0xFF              # cvar values are always 0/1 -- 0xFF can never false-match,
                                         # so the first poll always resyncs to whatever is live.

_ks = Ks(KS_ARCH_X86, KS_MODE_64)
_cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
_cs.detail = True

# Far enough from NEW_CODE_VA that Keystone can never mistake a
# placeholder forward-reference for something reachable by a short
# (rel8) jmp -- keeps pass-1 (length measurement) and pass-2 (real
# addresses) encodings the same length. DISPATCH_RETURN_VA is
# ~0xFC0000 away from NEW_CODE_VA, comfortably in rel32-only range, but
# we don't want to rely on that coincidence -- force it explicitly.
_FAR_PLACEHOLDER = NEW_CODE_VA + 0x2000000

_PRESERVE_PROLOGUE = """
    pushfq
    push rax
    push rbx
    push rcx
    push rdx
    push rsi
    push rdi
    push rbp
    push r8
    push r9
    push r10
    push r11
    push r12
    push r13
    push r14
    push r15
    sub rsp, 0x100
    movups [rsp+0x00], xmm0
    movups [rsp+0x10], xmm1
    movups [rsp+0x20], xmm2
    movups [rsp+0x30], xmm3
    movups [rsp+0x40], xmm4
    movups [rsp+0x50], xmm5
    movups [rsp+0x60], xmm6
    movups [rsp+0x70], xmm7
    movups [rsp+0x80], xmm8
    movups [rsp+0x90], xmm9
    movups [rsp+0xA0], xmm10
    movups [rsp+0xB0], xmm11
    movups [rsp+0xC0], xmm12
    movups [rsp+0xD0], xmm13
    movups [rsp+0xE0], xmm14
    movups [rsp+0xF0], xmm15
"""
# movups (unaligned) is deliberate, not movaps -- we do not know rsp's
# 16-byte alignment at an arbitrary inline injection point, and assuming
# it is aligned is exactly the class of bug that caused the earlier
# thread-hijacking crash (see CLAUDE.md, stack-alignment section).

_PRESERVE_EPILOGUE = """
    movups xmm0, [rsp+0x00]
    movups xmm1, [rsp+0x10]
    movups xmm2, [rsp+0x20]
    movups xmm3, [rsp+0x30]
    movups xmm4, [rsp+0x40]
    movups xmm5, [rsp+0x50]
    movups xmm6, [rsp+0x60]
    movups xmm7, [rsp+0x70]
    movups xmm8, [rsp+0x80]
    movups xmm9, [rsp+0x90]
    movups xmm10, [rsp+0xA0]
    movups xmm11, [rsp+0xB0]
    movups xmm12, [rsp+0xC0]
    movups xmm13, [rsp+0xD0]
    movups xmm14, [rsp+0xE0]
    movups xmm15, [rsp+0xF0]
    add rsp, 0x100
    pop r15
    pop r14
    pop r13
    pop r12
    pop r11
    pop r10
    pop r9
    pop r8
    pop rbp
    pop rdi
    pop rsi
    pop rdx
    pop rcx
    pop rbx
    pop rax
    popfq
"""


def _asm(text: str, addr: int) -> bytes:
    encoding, _count = _ks.asm(text, addr)
    if encoding is None:
        raise ValueError(f"assembly failed at 0x{addr:X}:\n{text}")
    return bytes(encoding)


def _tick_entry_text(call_target: int, return_target: int) -> str:
    """Runs once per frame, unconditionally, at the point where
    FUN_14045d7f0's land/swim dispatch if-else merges back together.
    Plain calling convention here (RDI = player pointer, no stack
    tricks) -- same preservation pattern already proven safe for the old
    land_entry hook, just positioned so both land and swim frames hit it."""
    return _PRESERVE_PROLOGUE + f"""
    mov r9, {ALIGN_SLOT_VA}
    mov [r9], rsp
    and rsp, -16
    call {call_target}
    mov r9, {ALIGN_SLOT_VA}
    mov rsp, [r9]
""" + _PRESERVE_EPILOGUE + f"""
    cmp byte ptr [rdi+0x1d65d], r14b
    jmp {return_target}
"""


def _check_all_secrets_text() -> str:
    """
    Unrolled sequence of one check per SECRET_TABLE entry, each an exact
    structural copy of the original single-cvar block (same registers,
    same sub rsp,0x28 / call / add rsp,0x28 pattern already proven correct
    and live-tested for g_dogmode alone) -- just repeated once per entry
    with that entry's own cvar VA and LAST_KNOWN slot substituted in, and
    a unique per-entry label so Keystone doesn't collide on `skip`. Ends
    in one shared `ret` after every entry has been checked.

    Cache-tag validation (added 2026-08-01, see CLAUDE.md's "why /secret
    worked but the poller didn't" writeup): [rcx+0x78] points at an 8-byte
    {tag(4), value(4)} slot. The real engine's own getter (FUN_1401B82D0)
    always checks tag==1 before trusting the value byte -- tag!=1 means
    "stale, the cache hasn't been recomputed from the string side yet".
    The original version of this loop skipped that check and read
    [rcx+4] unconditionally, which is fine 99.9% of the time but means a
    transient reinit window (e.g. the cvar registry's cache array getting
    rebuilt around a level load) can be observed as a bogus, non-real
    value change -- and this poller, unlike a one-off manual /secret
    write, runs unconditionally every single frame forever, so it's
    guaranteed to eventually sample exactly that window. Fixed by adding
    `cmp dword ptr [rcx], 1 / jne skip_i` before ever reading the value
    byte: if the tag isn't valid this tick, this entry is left alone
    entirely (no LAST_KNOWN update, no callback call) and simply
    re-checked next tick once the engine's own cache has settled.

    One-callback-per-tick throttle (added 2026-08-01, after the tag-check
    fix alone still crashed on a fresh exe's very first level load): every
    LAST_KNOWN slot starts at LAST_KNOWN_SENTINEL (0xFF), which can never
    equal a real 0/1 cvar value -- so the very first time this function
    ever runs after a fresh patch, ALL 9 entries read as "changed"
    simultaneously and (pre-fix) called CALLBACK_VA nine times back to
    back inside one invocation. Real gameplay never does this -- a player
    toggling secrets via the pause menu triggers this callback one at a
    time, with real time between each; nothing in the original game ever
    calls it 9 times in immediate succession. Fixed by making each
    entry's block `ret` immediately after its own callback call instead
    of falling through to check the next entry -- so at most ONE callback
    fires per invocation (per frame). A cold-boot resync now spreads
    across up to 9 separate frames instead of colliding into one, same
    total settle time (well under a second at any real framerate) but
    never more than one call in flight at once. Entries that didn't
    change still fall through to the next entry's check within the same
    call, same as before -- only an entry that actually fires short-
    circuits the rest.

    Value sanity check (added 2026-08-01, after both fixes above and
    Jon still saw all 9 LAST_KNOWN slots flip to 0xFF -- from an already-
    synced 0x00, not the initial sentinel -- in the first couple of
    seconds after a fresh process launch, well before the title screen).
    That timing is earlier than either prior fix targets: it's neither a
    mid-session reinit (tag check) nor a cold-boot-vs-sentinel mismatch
    (throttle) -- it's the cache slot's TAG reading valid (==1) while the
    whole cvar object may not be fully constructed yet this early in
    process startup, so the VALUE byte underneath a "valid" tag can still
    be garbage. Rather than chase the exact early-boot mechanism (the
    struct layout notes a separate, never-confirmed "deferred until
    subsystem ready" flag at a different offset that might be the real
    signal), added an unconditional value sanity check instead: `cmp dl,
    1 / ja skip_i` immediately after reading the value byte -- a real
    cvar bool is always exactly 0 or 1, so any other byte (0xFF included)
    is rejected outright regardless of why it showed up, no LAST_KNOWN
    update, no callback call. This is a strictly defensive addition on
    top of the tag check, not a replacement for it -- both conditions
    must hold (tag valid AND value in {0,1}) before this entry is ever
    trusted.
    """
    blocks = []
    for i, (name, cvar_va) in enumerate(SECRET_TABLE):
        # NOTE: no ";"-style comments here identifying each block by name
        # (e.g. "; -- g_bigheadmode --") -- Keystone does not treat ";" as
        # a comment leader (a bare "; foo" raises KS_ERR_ASM_MNEMONICFAIL,
        # not a silent skip), and specifically a "--" sequence inside one
        # of these put Keystone's parser into a genuine infinite loop
        # (reproduced and confirmed 2026-07-31, not a timing fluke) rather
        # than erroring -- caught by a build_code_section() smoke test
        # hanging indefinitely. Keep block identity in the Python source
        # (SECRET_TABLE order) instead of inline assembly comments.
        last_known_addr = LAST_KNOWN_VA + i
        blocks.append(f"""
    mov r10, {cvar_va}
    mov rcx, [r10+0x78]
    cmp dword ptr [rcx], 1
    jne skip_{i}
    movzx edx, byte ptr [rcx+4]
    cmp dl, 1
    ja skip_{i}
    mov r9, {last_known_addr}
    cmp dl, [r9]
    je skip_{i}
    mov [r9], dl
    mov rcx, r10
    sub rsp, 0x28
    mov rax, {CALLBACK_VA}
    call rax
    add rsp, 0x28
    ret
skip_{i}:
""")
    return "".join(blocks) + "\n    ret\n"


def build_code_section():
    """Two blocks: tick_entry, check_secret. Two-pass assembly: pass 1
    measures tick_entry's length using a guaranteed-far placeholder (so
    jmp always picks rel32, matching pass 2's real far target -- call has
    no short form so it's unaffected); check_secret has no forward
    references so its bytes are already final in pass 1. Pass 2 assembles
    tick_entry for real once check_secret's final address is known.
    Returns (code_bytes, tick_va, check_va)."""
    tick_len = len(_asm(_tick_entry_text(_FAR_PLACEHOLDER, _FAR_PLACEHOLDER), NEW_CODE_VA))
    check_bytes = _asm(_check_all_secrets_text(), NEW_CODE_VA)

    tick_va = NEW_CODE_VA
    check_va = tick_va + tick_len

    tick_bytes = _asm(_tick_entry_text(check_va, DISPATCH_RETURN_VA), tick_va)
    check_bytes = _asm(_check_all_secrets_text(), check_va)

    assert len(tick_bytes) == tick_len, "tick_entry length changed between passes"

    return (tick_bytes + check_bytes), tick_va, check_va


def build_data_section() -> bytes:
    """ALIGN_SLOT (8 bytes) + a LAST_KNOWN_ARRAY_SIZE-byte array, one
    sentinel byte per SECRET_TABLE entry (0xFF = never checked yet, so the
    first poll after patching always resyncs to whatever the cvar's real
    live value is), zero-padded past the entries actually in use."""
    assert len(SECRET_TABLE) <= LAST_KNOWN_ARRAY_SIZE, \
        "SECRET_TABLE grew past LAST_KNOWN_ARRAY_SIZE -- bump the array size"
    last_known = bytes([LAST_KNOWN_SENTINEL] * len(SECRET_TABLE))
    last_known += bytes(LAST_KNOWN_ARRAY_SIZE - len(SECRET_TABLE))  # zero-padded
    return struct.pack("<Q", 0) + last_known


def _align_up(v: int, a: int) -> int:
    return (v + a - 1) // a * a


def _make_jmp_hook(hook_va: int, vanilla: bytes, target_va: int) -> bytes:
    disp = target_va - (hook_va + 5)
    patch = b"\xE9" + struct.pack("<i", disp)
    patch += b"\x90" * (len(vanilla) - len(patch))
    return patch


def verify_with_capstone(code: bytes, tick_va: int, check_va: int):
    """Independent re-check of every control-flow edge, matching the
    verification rigor already established for the earlier patch
    versions. Uses real Capstone operand types (X86_OP_MEM/X86_OP_IMM/
    X86_OP_REG) rather than op_str string prefixes, which don't reliably
    distinguish store vs load for two-operand memory instructions like
    movups."""
    from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG

    errors = []

    def disasm(addr, blob):
        return list(_cs.disasm(bytes(blob), addr))

    tick_bytes = code[0:check_va - tick_va]
    check_bytes = code[check_va - tick_va:]

    def direct_call_targets(insns):
        out = []
        for i in insns:
            if i.mnemonic == "call" and i.operands and i.operands[0].type == X86_OP_IMM:
                out.append(i.operands[0].imm)
        return out

    def count_indirect_calls_to(insns, target_imm):
        """Like a boolean 'was target_imm ever loaded then called', but
        counts every occurrence instead of short-circuiting on the first
        -- needed now that check_secret calls the same CALLBACK_VA once
        per SECRET_TABLE entry and every occurrence must be accounted
        for, not just at-least-one."""
        loaded_regs = set()
        count = 0
        for i in insns:
            if i.mnemonic in ("mov", "movabs") and len(i.operands) == 2 \
               and i.operands[0].type == X86_OP_REG and i.operands[1].type == X86_OP_IMM \
               and i.operands[1].imm == target_imm:
                loaded_regs.add(i.reg_name(i.operands[0].reg))
            if i.mnemonic == "call" and i.operands and i.operands[0].type == X86_OP_REG:
                if i.reg_name(i.operands[0].reg) in loaded_regs:
                    count += 1
        return count

    def jmp_targets(insns):
        return [i.operands[0].imm for i in insns
                if i.mnemonic == "jmp" and i.operands and i.operands[0].type == X86_OP_IMM]

    def imm_present(insns, value):
        return any(op.type == X86_OP_IMM and op.imm == value for i in insns for op in i.operands)

    # tick_entry: must call check_secret (direct) and jmp to DISPATCH_RETURN_VA,
    # and must re-execute the exact vanilla CMP as its second-to-last instruction
    insns = disasm(tick_va, tick_bytes)
    if check_va not in direct_call_targets(insns):
        errors.append(f"tick_entry: no direct call to check_secret (0x{check_va:X}) found")
    if DISPATCH_RETURN_VA not in jmp_targets(insns):
        errors.append(f"tick_entry: no jmp to DISPATCH_RETURN_VA (0x{DISPATCH_RETURN_VA:X}) found")
    cmp_insns = [i for i in insns if i.mnemonic == "cmp" and i.operands
                 and i.operands[0].type == X86_OP_MEM]
    if not cmp_insns:
        errors.append("tick_entry: no 'cmp byte ptr [rdi+0x1d65d], r14b' vanilla re-execution found")
    elif insns.index(cmp_insns[-1]) != len(insns) - 2:
        errors.append("tick_entry: vanilla cmp is not the second-to-last instruction "
                       "(expected immediately before the final jmp)")

    # check_secret: must indirectly call CALLBACK_VA exactly once per
    # SECRET_TABLE entry, reference every entry's own cvar VA and its own
    # LAST_KNOWN slot as immediates, and end in ret
    insns_check = disasm(check_va, check_bytes)
    call_count = count_indirect_calls_to(insns_check, CALLBACK_VA)
    if call_count != len(SECRET_TABLE):
        errors.append(f"check_secret: expected {len(SECRET_TABLE)} indirect calls to CALLBACK_VA "
                       f"(0x{CALLBACK_VA:X}), found {call_count}")

    # Cache-tag validation (2026-08-01 fix): each entry must compare the
    # dword at [rcx] (the cache slot's tag) against immediate 1 before
    # ever touching the value byte -- confirms the "skip this tick if the
    # cache is mid-reinit" guard actually made it into the assembled
    # bytes, not just the Python source.
    tag_check_count = sum(
        1 for i in insns_check
        if i.mnemonic == "cmp" and len(i.operands) == 2
        and i.operands[0].type == X86_OP_MEM and i.operands[1].type == X86_OP_IMM
        and i.operands[1].imm == 1
    )
    if tag_check_count != len(SECRET_TABLE):
        errors.append(f"check_secret: expected {len(SECRET_TABLE)} cache-tag validity checks "
                       f"(cmp dword ptr [rcx], 1), found {tag_check_count}")

    # Value sanity check (2026-08-01 fix): each entry must compare DL
    # (the value byte, a REG operand -- distinct from the tag check above,
    # which compares a MEM operand) against immediate 1, immediately
    # followed by a `ja` (unsigned above) rejecting anything but 0/1 --
    # confirms the "reject non-boolean garbage even with a valid tag"
    # guard actually made it into the assembled bytes.
    value_check_count = 0
    for idx, i in enumerate(insns_check):
        if i.mnemonic == "cmp" and len(i.operands) == 2 \
           and i.operands[0].type == X86_OP_REG and i.operands[1].type == X86_OP_IMM \
           and i.operands[1].imm == 1:
            nxt = insns_check[idx + 1:idx + 2]
            if nxt and nxt[0].mnemonic == "ja":
                value_check_count += 1
            else:
                errors.append(f"check_secret: cmp dl,1 at index {idx} not immediately "
                               f"followed by 'ja' (value sanity guard malformed)")
    if value_check_count != len(SECRET_TABLE):
        errors.append(f"check_secret: expected {len(SECRET_TABLE)} value sanity checks "
                       f"(cmp dl, 1 / ja), found {value_check_count}")

    # One-callback-per-tick throttle (2026-08-01 fix): every indirect call
    # to CALLBACK_VA must be immediately followed by "add rsp, 0x28" then
    # "ret" -- confirms each entry short-circuits out of check_secret right
    # after firing, instead of falling through to check subsequent entries
    # in the same invocation (which is what let a cold-boot resync fire
    # all 9 callbacks back to back in one call).
    loaded_regs = set()
    throttle_checked = 0
    for idx, i in enumerate(insns_check):
        if i.mnemonic in ("mov", "movabs") and len(i.operands) == 2 \
           and i.operands[0].type == X86_OP_REG and i.operands[1].type == X86_OP_IMM \
           and i.operands[1].imm == CALLBACK_VA:
            loaded_regs.add(i.reg_name(i.operands[0].reg))
        if i.mnemonic == "call" and i.operands and i.operands[0].type == X86_OP_REG \
           and i.reg_name(i.operands[0].reg) in loaded_regs:
            throttle_checked += 1
            nxt = [n.mnemonic for n in insns_check[idx + 1:idx + 3]]
            if nxt != ["add", "ret"]:
                errors.append(f"check_secret: CALLBACK_VA call at index {idx} not immediately "
                               f"followed by 'add rsp,0x28 / ret' (throttle missing) -- found {nxt}")
    if throttle_checked != len(SECRET_TABLE):
        errors.append(f"check_secret: throttle check only inspected {throttle_checked} calls, "
                       f"expected {len(SECRET_TABLE)}")
    for i, (name, cvar_va) in enumerate(SECRET_TABLE):
        if not imm_present(insns_check, cvar_va):
            errors.append(f"check_secret: {name}'s cvar VA (0x{cvar_va:X}) immediate not found")
        last_known_addr = LAST_KNOWN_VA + i
        if not imm_present(insns_check, last_known_addr):
            errors.append(f"check_secret: {name}'s LAST_KNOWN slot (0x{last_known_addr:X}) immediate not found")
    if insns_check[-1].mnemonic != "ret":
        errors.append(f"check_secret: last instruction is {insns_check[-1].mnemonic}, expected ret")

    # push/pop, pushfq/popfq, and movups save/restore balance in tick_entry
    pushes = sum(1 for i in insns if i.mnemonic == "push")
    pops = sum(1 for i in insns if i.mnemonic == "pop")
    if pushes != pops:
        errors.append(f"tick_entry: push/pop imbalance ({pushes} pushes vs {pops} pops)")
    pushfq = sum(1 for i in insns if i.mnemonic == "pushfq")
    popfq = sum(1 for i in insns if i.mnemonic == "popfq")
    if pushfq != popfq:
        errors.append(f"tick_entry: pushfq/popfq imbalance ({pushfq} vs {popfq})")

    movups_store = 0
    movups_load = 0
    for i in insns:
        if i.mnemonic != "movups":
            continue
        dst, src = i.operands[0], i.operands[1]
        if dst.type == X86_OP_MEM and src.type == X86_OP_REG:
            movups_store += 1
        elif dst.type == X86_OP_REG and src.type == X86_OP_MEM:
            movups_load += 1
        else:
            errors.append(f"tick_entry: unexpected movups operand shape: {i.op_str}")
    if movups_store != 16 or movups_load != 16:
        errors.append(f"tick_entry: expected 16 movups stores + 16 loads (xmm0-15), got {movups_store} stores / {movups_load} loads")

    store_slots = {}
    load_slots = {}
    for i in insns:
        if i.mnemonic != "movups":
            continue
        dst, src = i.operands[0], i.operands[1]
        if dst.type == X86_OP_MEM and src.type == X86_OP_REG:
            store_slots[i.reg_name(src.reg)] = dst.mem.disp
        elif dst.type == X86_OP_REG and src.type == X86_OP_MEM:
            load_slots[i.reg_name(dst.reg)] = src.mem.disp
    if store_slots != load_slots:
        errors.append(f"tick_entry: movups store/load slot mapping mismatch: "
                       f"stored={store_slots} loaded={load_slots}")

    return errors


def _va_to_text_file(va: int) -> int:
    # .text SECTION_DELTA convention shared across this codebase
    from death_penalty_patch import SECTION_DELTA
    return va - IMAGE_BASE - SECTION_DELTA


def revert_patch(exe_path: str, dry_run: bool = True):
    """Restore the single hook site to its vanilla bytes. Does NOT remove
    the appended .apcode/.apdata sections -- they're simply left
    unreferenced/dead once nothing jmps into them, which is harmless and
    avoids a second, riskier round of header surgery just to undo this."""
    path = Path(exe_path)
    data = bytearray(path.read_bytes())

    off = _va_to_text_file(DISPATCH_MERGE_VA)
    current = bytes(data[off:off + len(DISPATCH_MERGE_VANILLA)])

    print(f"Dispatch-merge hook currently: {current.hex()}  (vanilla: {DISPATCH_MERGE_VANILLA.hex()})")

    if current == DISPATCH_MERGE_VANILLA:
        print("Hook is already vanilla -- nothing to revert.")
        return

    if dry_run:
        print("dry_run=True -- not modifying the file. Re-run with dry_run=False to apply.")
        return

    data[off:off + len(DISPATCH_MERGE_VANILLA)] = DISPATCH_MERGE_VANILLA
    path.write_bytes(bytes(data))
    print("Reverted hook site to vanilla bytes.")


def apply_patch(exe_path: str, dry_run: bool = True, verify_only: bool = False, force: bool = False):
    path = Path(exe_path)
    data = bytearray(path.read_bytes())

    # --- sanity: vanilla bytes still present at the hook site? ---
    off = _va_to_text_file(DISPATCH_MERGE_VA)
    actual = bytes(data[off:off + len(DISPATCH_MERGE_VANILLA)])

    if actual != DISPATCH_MERGE_VANILLA:
        # HARD abort, not a warning (2026-08-01, see CLAUDE.md's "reapply
        # corruption" writeup): confirmed via a reproduced test that
        # continuing here against an already-patched file appends a SECOND
        # .apcode/.apdata pair at NEW_CODE_VA/NEW_DATA_VA -- the exact same
        # RVAs the first apply already used, since those are fixed
        # constants, not computed from the file's current state. That
        # produces a PE with two sections claiming the identical virtual
        # address, pointing at different file offsets -- invalid structure
        # with undefined loader behavior, and a very plausible source of
        # the hard-to-pin-down crashes this investigation chased for days
        # before this was found. There is no safe way to "re-apply" this
        # patch in place; the correct workflow is always a fresh vanilla
        # copy, never re-running this against a file it's already touched.
        if verify_only:
            print(f"WARNING (verify-only, not aborting): dispatch-merge hook site does not "
                  f"match expected vanilla bytes (got {actual.hex()}, expected "
                  f"{DISPATCH_MERGE_VANILLA.hex()}) -- already patched, or wrong exe. "
                  f"Applying for real against this file would corrupt it -- start from a "
                  f"fresh vanilla copy instead.")
        else:
            print(f"ABORTING: dispatch-merge hook site does not match expected vanilla bytes "
                  f"(got {actual.hex()}, expected {DISPATCH_MERGE_VANILLA.hex()}) -- this file "
                  f"is already patched (or is the wrong exe). Re-running --apply against an "
                  f"already-patched file would append a SECOND .apcode/.apdata pair at the "
                  f"same virtual addresses as the first, corrupting the PE image. Copy a fresh "
                  f"vanilla thoth_x64.exe over this file and re-run --apply against that "
                  f"instead. (If you genuinely intend to force this, revert first with "
                  f"--revert, or use --force to bypass this check.)")
            if not force:
                # RuntimeError, not SystemExit -- ap_patcher.py's call site
                # wraps this in `except Exception` to degrade gracefully
                # (Secret Trap items still work via plain write_cvar_bool
                # even if this poller patch fails to apply); SystemExit is
                # a BaseException subclass and would NOT be caught there,
                # crashing the whole seed-patch run instead of degrading.
                raise RuntimeError(
                    "secret_mode_section_patch.apply_patch: refusing to patch an "
                    "already-patched (or non-vanilla) exe -- see the ABORTING message "
                    "above for why this would corrupt the file.")

    code, tick_va, check_va = build_code_section()
    errors = verify_with_capstone(code, tick_va, check_va)
    if errors:
        print("CAPSTONE VERIFICATION FAILED:")
        for e in errors:
            print("  -", e)
        raise SystemExit(1)
    print(f"Capstone verification passed. Code section: {len(code)} bytes "
          f"(tick_entry@0x{tick_va:X} len={check_va - tick_va}, "
          f"check_secret@0x{check_va:X} len={len(code) - (check_va - tick_va)})")

    data_blob = build_data_section()

    if verify_only:
        print("verify_only=True -- not modifying the file.")
        return

    if len(data) != NEW_RAW_PTR_CODE:
        print(f"NOTE: file length is 0x{len(data):X}, not the expected 0x{NEW_RAW_PTR_CODE:X} "
              f"recorded from the earlier header check -- recomputing append point from the "
              f"file's actual current length instead (safe either way, just flagging in case "
              f"the exe changed since that check).")

    code_raw_ptr = _align_up(len(data), FILE_ALIGNMENT)
    code_raw_size = _align_up(len(code), FILE_ALIGNMENT)
    data_raw_ptr = code_raw_ptr + code_raw_size
    data_raw_size = _align_up(len(data_blob), FILE_ALIGNMENT)

    code_rva = NEW_CODE_VA - IMAGE_BASE
    data_rva = NEW_DATA_VA - IMAGE_BASE
    if data_rva != code_rva + SECTION_ALIGNMENT:
        raise ValueError("NEW_DATA_VA is not exactly one page after NEW_CODE_VA -- "
                          "code section grew past 4KB? re-derive VAs.")

    print(f"code section: RVA 0x{code_rva:X}  RawPtr 0x{code_raw_ptr:X}  RawSize 0x{code_raw_size:X}")
    print(f"data section: RVA 0x{data_rva:X}  RawPtr 0x{data_raw_ptr:X}  RawSize 0x{data_raw_size:X}")

    if dry_run:
        print("dry_run=True -- not modifying the file. Re-run with dry_run=False to apply.")
        return

    # --- append section payloads (padded to their RawSize) ---
    data.extend(b"\x00" * (code_raw_ptr - len(data)))
    data.extend(code)
    data.extend(b"\x00" * (code_raw_size - len(code)))
    data.extend(data_blob)
    data.extend(b"\x00" * (data_raw_size - len(data_blob)))

    # --- PE header surgery ---
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    coff_off = pe_off + 4
    num_sections = struct.unpack_from("<H", data, coff_off + 2)[0]
    size_opt_hdr = struct.unpack_from("<H", data, coff_off + 16)[0]
    opt_off = coff_off + 20
    section_alignment = struct.unpack_from("<I", data, opt_off + 32)[0]
    size_of_image_off = opt_off + 56
    size_of_image = struct.unpack_from("<I", data, size_of_image_off)[0]

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

    code_hdr = _section_header(b".apcode", len(code), code_rva, code_raw_size, code_raw_ptr, CODE | X | R)
    data_hdr = _section_header(b".apdata", len(data_blob), data_rva, data_raw_size, data_raw_ptr, INIT_DATA | R | W)

    # IMPORTANT: this must be an in-place OVERWRITE of existing header
    # slack, not a Python list-style insert. data[off:off] = X (a
    # zero-length target slice) inserts and shifts every subsequent byte
    # in the file right by len(X) -- which would silently invalidate every
    # RawPtr computed above, since headers sit near the front of the file
    # and everything (including the section content we just appended)
    # comes after them. Caught via a byte-level round-trip check against
    # a synthetic PE before ever running this against the real exe.
    new_hdrs = code_hdr + data_hdr
    insert_off = sect_table_off + num_sections * 40
    data[insert_off:insert_off + len(new_hdrs)] = new_hdrs

    # NumberOfSections
    struct.pack_into("<H", data, coff_off + 2, num_sections + 2)

    # SizeOfImage
    new_size_of_image = _align_up(data_rva + section_alignment, section_alignment)
    if new_size_of_image > size_of_image:
        struct.pack_into("<I", data, size_of_image_off, new_size_of_image)

    # --- .text hook patch ---
    hook_patch = _make_jmp_hook(DISPATCH_MERGE_VA, DISPATCH_MERGE_VANILLA, tick_va)
    data[off:off + len(hook_patch)] = hook_patch

    path.write_bytes(bytes(data))
    print(f"Patched. Dispatch-merge hook -> 0x{tick_va:X}, "
          f"check_secret @ 0x{check_va:X}, data @ 0x{NEW_DATA_VA:X}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("exe")
    p.add_argument("--dry-run", action="store_true", default=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--verify-only", action="store_true")
    p.add_argument("--revert", action="store_true", help="restore vanilla bytes at the hook site")
    p.add_argument("--force", action="store_true",
                    help="bypass the already-patched safety check (DANGEROUS -- see the abort "
                         "message; almost always the wrong answer, use a fresh vanilla exe instead)")
    args = p.parse_args()
    if args.revert:
        revert_patch(args.exe, dry_run=not args.apply)
    else:
        apply_patch(args.exe, dry_run=not args.apply, verify_only=args.verify_only, force=args.force)
