"""
retractor_folder_log_patch.py
==============================
Companion to unique_retractor_keys_patch.py. Adds a live-updating "Five
Keys" tracker into the existing FBI Serial Killer Profile folder document
(the "Nettie's File" item) -- one page per liveside world, each showing
either "not found yet" or "key recovered" text depending on the SAME
CF_CUSTOM00-04 save flags unique_retractor_keys_patch.py already owns.

DESIGN
------
The folder reader (FUN_1402f6700 -> FUN_1402f6c80) builds a page's KPF
path as "folder/{page_number}.mup" and loads it -- purely mechanical, no
bounds checking of its own. The actual page-count bound lives in a
per-(language, category) {min,max} table at DAT_140d8c180, populated at
startup (FUN_140034540) from four 16-byte SSE constants in .rdata:

    DAT_14072e4e0 = {folder_max=28, card_max=32}   -- languages 0,3,4,5
    DAT_14072e500 = {folder_max=32, card_max=32}   -- language 1 (already
    DAT_14072e4f0 = {folder_max=31, card_max=32}   -- language 2  bigger --
                                                        left untouched)
    DAT_14072e5f0 = {journal_max=12, shadows_max=164} -- all languages,
                                                          unrelated, untouched

Byte-exact confirmed 2026-08-18 by reading thoth_x64_patched_test.exe
directly: file offset 0x72CCE0 (.rdata, VMA 0x14064b000 / file base
0x649800) holds `01 00 00 00 1c 00 00 00 01 00 00 00 20 00 00 00`, i.e.
{min=1,max=0x1c=28},{min=1,max=0x20=32} -- matches DAT_14072e4e0 exactly.

Languages 1 (French) and 2 (German) already have MORE real pages than
English (32 and 31 respectively) -- almost certainly their own extra
localized content not present in this repo's reference KPF snapshot.
Deliberately NOT touched here: bumping their max too would need
language-aware branching in the hook (which language is active) for no
real benefit, since this randomizer's text is English-only anyway. The
tracker feature simply doesn't appear for FR/DE players; their existing
pages are completely unaffected either way.

PATCH 1 -- bump the shared en/it/es/pt folder max by 5 (28 -> 33), making
pages 29-33 legally navigable (reader wraps to page 1 past this bound --
confirmed live 2026-08-18, Jon's page-29 test wrapped to the cover
instead of stopping or going blank):

    FOLDER_MAX_FILE_OFFSET = 0x72CCE4   (low byte of the max dword)
    FOLDER_MAX_OLD = 0x1C  (28)
    FOLDER_MAX_NEW = 0x21  (33)

PATCH 2 -- new hook (Hook D), splicing the CALL FUN_1402f6c80 inside
FUN_1402f6700 (address 0x1402f673b, the same call already used for every
page load of every category -- folder/card/journal alike). At this exact
point (confirmed from the surrounding disassembly Jon pulled):
    R8D  = category  (0 = folder)
    R9D  = page number (the value we want to conditionally redirect)
    RCX  = param_1 (reader struct ptr) -- also independently available in
           R13 (non-volatile, unclobbered since function entry)
    RDX  = output ptr, LEA'd as [RSP+0x60] just before this call

Hook body: category==folder and page in [29,33] -> slot = page-29 (0..4,
already == world_id-1, matching unique_retractor_keys_patch.py's own
CF_CUSTOM_BASE_INDEX convention) -> GetFlag(CF_CUSTOM_BASE_INDEX+slot) ->
redirect page number to NOTFOUND_PAGE_BASE+slot or FOUND_PAGE_BASE+slot.
Anything else (other category, or page outside our new range) passes
through with the page number completely unchanged -- this hook can only
ever add a redirect, never break normal folder/card/journal navigation.

R14D/R15D used as scratch that survives our own added CALLs -- same
non-volatile-register pattern already live-confirmed in
unique_retractor_keys_patch.py's Hook A/B/C (GetFlag/SetFlag/the flag
accessor never clobber R13/R14/R15 in practice, consistent with the Win64
ABI treating them as callee-saved). RCX/RDX/R8D are volatile and DO get
clobbered by our own GetFlag-family calls, so all three are explicitly
rebuilt (from R13, a fresh [RSP+0x60] LEA, and R14D respectively) right
before re-executing the overwritten call -- not assumed to survive.

NEW CONTENT -- 10 new folder/*.mup files (not yet built into the KPF
patch pipeline here; see build_data_files() below), one "not found" +
one "found" variant per world, at page numbers 500-504 / 510-514 -- picked
well clear of any real content in any locale (English 1-28/29-33 wait no
FR 1-32, DE 1-31) so there's no collision risk even for locales we don't
touch.

STATUS 2026-08-18: structurally verified (Capstone), byte offsets
confirmed against the real exe. NOT yet live-tested.
"""
import struct
from pathlib import Path

from keystone import Ks, KS_ARCH_X86, KS_MODE_64
import capstone
from capstone.x86 import X86_OP_IMM

IMAGE_BASE = 0x140000000

# ---------------------------------------------------------------------
# Patch 1 -- folder page-count bound (en/it/es/pt shared block only)
# ---------------------------------------------------------------------
FOLDER_MAX_FILE_OFFSET = 0x72CCE4   # low byte of DAT_14072e4e0's max dword
FOLDER_MAX_OLD = 0x1C               # 28
FOLDER_MAX_NEW = 0x21               # 33 (28 + 5 new slots)

# ---------------------------------------------------------------------
# Patch 2 -- Hook D, page-number redirect
# ---------------------------------------------------------------------
READER_FUNC_VA   = 0x1402f6700       # FUN_1402f6700
HOOK_VA          = 0x1402f673b       # CALL FUN_1402f6c80
HOOK_VANILLA     = bytes([0xE8, 0x40, 0x05, 0x00, 0x00])  # CALL FUN_1402f6c80
RESUME_VA        = HOOK_VA + len(HOOK_VANILLA)            # 0x1402f6740
FUN_1402F6C80_VA = 0x1402f6c80

FOLDER_CATEGORY  = 0
NEW_MIN_PAGE     = 29
NEW_MAX_PAGE     = 33

NOTFOUND_PAGE_BASE = 500   # 500..504, one per world (slot 0..4)
FOUND_PAGE_BASE    = 510   # 510..514

# Same save-flag system unique_retractor_keys_patch.py owns -- CF_CUSTOM00-04.
FLAG_MANAGER_ACCESSOR_VA = 0x140347560
GETFLAG_VA                = 0x14033f1d0
CF_CUSTOM_BASE_INDEX      = 279

_ks = Ks(KS_ARCH_X86, KS_MODE_64)
_cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
_cs.detail = True


def _asm(text: str, addr: int) -> bytes:
    encoding, _count = _ks.asm(text, addr)
    if encoding is None:
        raise ValueError(f"assembly failed at 0x{addr:X}:\n{text}")
    return bytes(encoding)


def _hook_text() -> str:
    return f"""
    mov r14d, r8d
    mov r15d, r9d
    cmp r14d, {FOLDER_CATEGORY}
    jne fp_passthrough
    cmp r15d, {NEW_MIN_PAGE}
    jl fp_passthrough
    cmp r15d, {NEW_MAX_PAGE}
    jg fp_passthrough
    sub r15d, {NEW_MIN_PAGE}
    call {FLAG_MANAGER_ACCESSOR_VA}
    mov rcx, rax
    lea edx, [r15 + {CF_CUSTOM_BASE_INDEX}]
    call {GETFLAG_VA}
    test al, al
    jz fp_notfound
    add r15d, {FOUND_PAGE_BASE}
    jmp fp_have_redirect
fp_notfound:
    add r15d, {NOTFOUND_PAGE_BASE}
fp_have_redirect:
    mov r9d, r15d
    jmp fp_restore
fp_passthrough:
    mov r9d, r15d
fp_restore:
    mov r8d, r14d
    mov rcx, r13
    lea rdx, [rsp + 0x60]
    call {FUN_1402F6C80_VA}
    jmp {RESUME_VA}
"""


def build_hook_code(code_va: int):
    code = _asm(_hook_text(), code_va)
    return code, code_va


def verify_with_capstone(code: bytes, code_va: int) -> list:
    errors = []
    insns = list(_cs.disasm(bytes(code), code_va))
    jmps = [i.operands[0].imm for i in insns
            if i.mnemonic == "jmp" and i.operands and i.operands[0].type == X86_OP_IMM]
    calls = [i.operands[0].imm for i in insns
             if i.mnemonic == "call" and i.operands and i.operands[0].type == X86_OP_IMM]
    if RESUME_VA not in jmps:
        errors.append(f"no jmp to RESUME_VA (0x{RESUME_VA:X})")
    if FLAG_MANAGER_ACCESSOR_VA not in calls:
        errors.append(f"no call to FLAG_MANAGER_ACCESSOR_VA (0x{FLAG_MANAGER_ACCESSOR_VA:X})")
    if GETFLAG_VA not in calls:
        errors.append(f"no call to GETFLAG_VA (0x{GETFLAG_VA:X})")
    if FUN_1402F6C80_VA not in calls:
        errors.append(f"no call to FUN_1402F6C80_VA (0x{FUN_1402F6C80_VA:X})")
    # Both redirect paths and the passthrough path must all end up setting r9d.
    r9d_writes = [i for i in insns if i.mnemonic == "mov" and i.operands
                  and i.operands[0].type == 1 and _cs.reg_name(i.operands[0].reg) == "r9d"]
    if len(r9d_writes) < 2:
        errors.append(f"expected at least 2 writes to r9d (redirect + passthrough), found {len(r9d_writes)}")
    return errors


def _make_jmp_hook(hook_va: int, vanilla: bytes, target_va: int) -> bytes:
    disp = target_va - (hook_va + 5)
    patch = b"\xE9" + struct.pack("<i", disp)
    patch += b"\x90" * (len(vanilla) - len(patch))
    return patch


def _va_to_text_file(va: int) -> int:
    from death_penalty_patch import SECTION_DELTA
    return va - IMAGE_BASE - SECTION_DELTA


def _read_size_of_image(data: bytes) -> int:
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    coff_off = pe_off + 4
    opt_off = coff_off + 20
    return struct.unpack_from("<I", data, opt_off + 56)[0]


def _align_up(v: int, a: int) -> int:
    return (v + a - 1) // a * a


SECTION_ALIGNMENT = 0x1000
FILE_ALIGNMENT = 0x200


def _find_section(data: bytes, name: bytes):
    """Locates an existing section by name in the CURRENT section table and
    returns (header_file_offset, virtual_address, virtual_size, raw_ptr,
    raw_size), or None if not present. name8 comparison strips trailing
    NULs the same way _section_header() pads on write."""
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    coff_off = pe_off + 4
    num_sections = struct.unpack_from("<H", data, coff_off + 2)[0]
    size_opt_hdr = struct.unpack_from("<H", data, coff_off + 16)[0]
    opt_off = coff_off + 20
    sect_table_off = opt_off + size_opt_hdr
    name8 = name[:8].ljust(8, b"\x00")
    for i in range(num_sections):
        off = sect_table_off + i * 40
        if bytes(data[off:off + 8]) == name8:
            virt_size, virt_addr, raw_size, raw_ptr = struct.unpack_from("<IIII", data, off + 8)
            return off, virt_addr, virt_size, raw_ptr, raw_size
    return None


def apply_patch(exe_path: str, dry_run: bool = True, verify_only: bool = False, force: bool = False):
    """
    Piggyback fix (2026-08-18, Jon's call after hitting "Not enough header
    slack for 1 new section header" on a real seed): this used to request
    its OWN new PE section (.fpcode) for Hook D's ~100 bytes of code, the
    same way unique_retractor_keys_patch.py adds .rkcode/.rkdata. Header
    slack is a hard-capped, shared budget across every patch that does this
    (184 bytes total, confirmed 2026-07-31 -- room for exactly 4 new
    section headers) -- secret_mode_section_patch already spends 2 and
    unique_retractor_keys_patch spends 2 more, leaving nothing for this
    file's 1 more.

    Growing that budget would mean shifting SizeOfHeaders and every
    EXISTING section's PointerToRawData, which would invalidate
    death_penalty_patch.SECTION_DELTA -- a single hardcoded constant every
    VA-based hook patch in this codebase (6 files) derives its file offsets
    from. Far too big a blast radius for one feature's flavor text.

    Instead: unique_retractor_keys_patch.py's own .rkcode section reserves
    a FULL page (SECTION_ALIGNMENT=0x1000) of R+X virtual address space,
    but its actual 3-hook code (confirmed 258 bytes as of 2026-08-18) only
    uses the first slice of that page's FILE_ALIGNMENT-padded raw region
    (512 bytes raw, 254 bytes already zero-padded and unused). Hook D's
    code (97 bytes) fits comfortably inside that existing slack (369 bytes
    total needed out of 512 available raw, and nowhere near the full
    0x1000 virtual page .rkdata starts at) -- so this file now APPENDS
    Hook D's code into .rkcode's own already-allocated-but-empty tail and
    bumps ONLY that section's existing VirtualSize field (an in-place edit
    to an existing 40-byte header, not a new one), instead of asking for a
    brand new section header at all. No NumberOfSections change, no
    SizeOfImage change, no file growth, no shifting of .rkdata or anything
    appended after it -- the smallest change that could plausibly work.

    Depends on unique_retractor_keys_patch.py's own apply_patch() having
    already run against this exact file (ap_patcher.py's Step 7 always
    calls them in that order, in the same try block) -- .rkcode must
    already exist in the section table, or this raises immediately.
    """
    path = Path(exe_path)
    data = bytearray(path.read_bytes())

    # --- sanity-check + apply the max-bound byte patch ---
    cur_max_byte = data[FOLDER_MAX_FILE_OFFSET]
    if cur_max_byte not in (FOLDER_MAX_OLD, FOLDER_MAX_NEW):
        msg = (f"folder max byte at file offset 0x{FOLDER_MAX_FILE_OFFSET:X} is "
               f"0x{cur_max_byte:02X}, expected vanilla 0x{FOLDER_MAX_OLD:02X} or "
               f"already-patched 0x{FOLDER_MAX_NEW:02X} -- wrong exe or unexpected build.")
        if verify_only:
            print(f"WARNING (verify-only): {msg}")
        else:
            raise RuntimeError(msg)

    off_d = _va_to_text_file(HOOK_VA)
    actual_d = bytes(data[off_d:off_d + len(HOOK_VANILLA)])
    if actual_d != HOOK_VANILLA:
        msg = (f"hook site does not match expected vanilla bytes -- got "
               f"{actual_d.hex()} (expected {HOOK_VANILLA.hex()}). Already patched, "
               f"or wrong exe.")
        if verify_only:
            print(f"WARNING (verify-only): {msg}")
        else:
            print(f"ABORTING: {msg}")
            if not force:
                raise RuntimeError("retractor_folder_log_patch.apply_patch: refusing to "
                                    "patch an already-patched (or non-vanilla) exe.")

    rkcode = _find_section(data, b".rkcode")
    if rkcode is None:
        raise RuntimeError(
            "retractor_folder_log_patch.apply_patch: no .rkcode section found -- "
            "unique_retractor_keys_patch.apply_patch() must run against this exact "
            "file BEFORE this patch (ap_patcher.py's Step 7 always does this in "
            "order); running this file standalone against an unpatched exe isn't "
            "supported by the piggyback approach."
        )
    rkcode_hdr_off, rkcode_va, rkcode_virt_size, rkcode_raw_ptr, rkcode_raw_size = rkcode
    rkcode_va += IMAGE_BASE

    # 16-byte alignment for the new code's start -- not required for x86
    # correctness (no alignment restriction on a jmp/call target), purely
    # so the appended code reads cleanly in a disassembler instead of
    # starting mid-cacheline right after the last existing hook's bytes.
    gap = _align_up(rkcode_virt_size, 16) - rkcode_virt_size
    hookd_va = rkcode_va + rkcode_virt_size + gap
    hookd_raw_off = rkcode_raw_ptr + rkcode_virt_size + gap

    code, code_va = build_hook_code(hookd_va)
    errors = verify_with_capstone(code, code_va)
    if errors:
        print("CAPSTONE VERIFICATION FAILED:")
        for e in errors:
            print("  -", e)
        raise SystemExit(1)
    print(f"Capstone verification passed. Hook D code: {len(code)} bytes @ 0x{code_va:X} "
          f"(piggybacked into .rkcode's existing page, {gap}-byte alignment gap after "
          f"its {rkcode_virt_size} bytes of existing hooks)")

    new_virt_size = (hookd_va - rkcode_va) + len(code)
    if new_virt_size > rkcode_raw_size:
        raise ValueError(
            f"retractor_folder_log_patch: Hook D's code no longer fits in .rkcode's "
            f"existing {rkcode_raw_size}-byte raw region (would need {new_virt_size}) "
            f"-- unique_retractor_keys_patch.py's own hooks grew, or this file's Hook "
            f"D grew. Re-run with the real exe and re-check "
            f"tools/check_pe_headers.py-style slack before assuming the piggyback "
            f"still fits."
        )
    if new_virt_size > SECTION_ALIGNMENT:
        raise ValueError(
            f"retractor_folder_log_patch: Hook D's code would push .rkcode's "
            f"VirtualSize ({new_virt_size}) past its own page boundary "
            f"(0x{SECTION_ALIGNMENT:X}) -- would collide with .rkdata, which starts "
            f"exactly one page after .rkcode's VA."
        )

    if verify_only:
        print("verify_only=True -- not modifying the file.")
        return
    if dry_run:
        print(f"dry_run=True -- would write {len(code)} bytes at file offset "
              f"0x{hookd_raw_off:X} and bump .rkcode's VirtualSize "
              f"{rkcode_virt_size} -> {new_virt_size}. Not modifying the file.")
        return

    # --- apply: write Hook D's code into .rkcode's existing padding, bump
    # ONLY that section's VirtualSize field (offset+8 within its 40-byte
    # header -- see _section_header()'s pack format in
    # unique_retractor_keys_patch.py: Name(8s) then VirtualSize(I) next) ---
    data[hookd_raw_off:hookd_raw_off + len(code)] = code
    struct.pack_into("<I", data, rkcode_hdr_off + 8, new_virt_size)

    # --- apply the two real patches (unchanged from before) ---
    data[FOLDER_MAX_FILE_OFFSET] = FOLDER_MAX_NEW
    hook_bytes = _make_jmp_hook(HOOK_VA, HOOK_VANILLA, code_va)
    data[off_d:off_d + len(hook_bytes)] = hook_bytes

    path.write_bytes(bytes(data))
    print(f"Patched. folder max byte 0x{FOLDER_MAX_OLD:02X}->0x{FOLDER_MAX_NEW:02X}, "
          f"hook -> 0x{code_va:X}, .rkcode VirtualSize {rkcode_virt_size} -> {new_virt_size} "
          f"(no new section header needed).")


def revert_patch(exe_path: str, dry_run: bool = True):
    """
    Only reverts the two behavior-visible changes (folder max byte, Hook D's
    jmp) -- same as before the piggyback rework. Deliberately does NOT try
    to shrink .rkcode's VirtualSize back down: Hook D's code becomes
    unreachable dead bytes once its own jmp is reverted to the vanilla
    CALL, harmless either way, and this file has never tried to fully
    undo unique_retractor_keys_patch.py's own section additions either
    (same reasoning -- inert leftover bytes in an already-oversized page
    aren't worth the extra risk of getting the shrink-back-down math wrong).
    """
    path = Path(exe_path)
    data = bytearray(path.read_bytes())
    reverted = False

    cur_max_byte = data[FOLDER_MAX_FILE_OFFSET]
    print(f"folder max byte currently: 0x{cur_max_byte:02X} (vanilla: 0x{FOLDER_MAX_OLD:02X})")
    if cur_max_byte == FOLDER_MAX_NEW:
        reverted = True
        if not dry_run:
            data[FOLDER_MAX_FILE_OFFSET] = FOLDER_MAX_OLD

    off_d = _va_to_text_file(HOOK_VA)
    current = bytes(data[off_d:off_d + len(HOOK_VANILLA)])
    print(f"hook currently: {current.hex()}  (vanilla: {HOOK_VANILLA.hex()})")
    if current != HOOK_VANILLA:
        reverted = True
        if not dry_run:
            data[off_d:off_d + len(HOOK_VANILLA)] = HOOK_VANILLA

    if dry_run:
        print("dry_run=True -- not modifying the file.")
        return
    if reverted:
        path.write_bytes(bytes(data))
        print("Reverted.")


# ---------------------------------------------------------------------
# New folder/*.mup content
# ---------------------------------------------------------------------
# ── "Recovered from" fun-fact composition ────────────────────────────────────
# gate_raw (extracted_locations.py) is already a clean token expression --
# "PRISON_KEY_CARD", "FLAMBEAU & MARTEAU & BATON & CALABASH", "ENG_KEY |
# GAD2_WALK" -- so turning it into a readable clause is just per-token
# translation + join, no real boolean-logic parsing needed (this codebase's
# gate_raw values never nest AND/OR together in the same expression).
#
# GAD1_HAND/GAD2_WALK/GAD3_SWIM deliberately all map to the same vague
# "mastery of the Gad" phrase rather than guessing at specific ability names
# we don't actually know (climbing/walking/swimming) -- Jon's call,
# 2026-08-18: "we can be vague, it's okay, or ambiguous."
_TOKEN_FRIENDLY = {
    "BATON": "the Baton",
    "FLAMBEAU": "the Flambeau",
    "MARTEAU": "the Marteau",
    "CALABASH": "the Calabash",
    "ENG_KEY": "the Engineer's Key",
    "PRISON_KEY_CARD": "the Prison Key Card",
    "X3_ACCUMULATOR": "the Accumulator",
    "CADEAUX_666": "666 cadeaux",
    "NIGHT": "nightfall",
    "PISTONS": "the piston puzzle",
    "SCHEMATIC": "Jack's Schematic",
    "GAD1_HAND": "mastery of the Gad",
    "GAD2_WALK": "mastery of the Gad",
    "GAD3_SWIM": "mastery of the Gad",
}


def _friendly_token(tok: str) -> str:
    tok = tok.strip()
    if tok in _TOKEN_FRIENDLY:
        return _TOKEN_FRIENDLY[tok]
    if tok.startswith("SL") and tok[2:].isdigit():
        return f"SL{tok[2:]}"
    if tok.startswith("GATE_"):
        return f"the {tok[len('GATE_'):].replace('_', ' ').title()} gate"
    return tok.replace("_", " ").title()


def _describe_gate_raw(gate_raw: str) -> str:
    """'PRISON_KEY_CARD' -> 'the Prison Key Card'; 'A & B & C' -> 'A, B, and C';
    'A | B' -> 'A or B'. Returns '' for None/empty (freely accessible)."""
    if not gate_raw:
        return ""
    if "|" in gate_raw and "&" not in gate_raw:
        parts = [_friendly_token(t) for t in gate_raw.split("|")]
        return " or ".join(parts)
    parts = [_friendly_token(t) for t in gate_raw.split("&")]
    if len(parts) <= 2:
        return " and ".join(parts)
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


# Narrative variants instead of a flat "requiring X" label -- Jon's request,
# 2026-08-18: "more of a narrative". Picked deterministically by slot (not
# RNG) so the same seed always renders the same page, but the 5 pages don't
# all read identically. {req} is always mid/end-sentence and lowercase-led
# ("the Prison Key Card") by design -- _describe_gate_raw() never capitalizes
# it, so no template puts {req} at the start of a sentence.
_NONE_VARIANTS = [
    "Recovered from {region}. No resistance — it was just sitting there, waiting to be found.",
    "Recovered from {region}. Whoever hid it there didn't try very hard.",
    "Recovered from {region}. Nothing stood between us and it.",
    "Recovered from {region}. Practically in plain sight, once you knew where to look.",
    "Recovered from {region}. No lock, no guard, no trick — just there.",
]
_SOME_VARIANTS = [
    "Recovered from {region}. Wouldn't have gotten near it without {req}.",
    "Recovered from {region}. Took {req} just to get close.",
    "Recovered from {region}. Buried behind {req}.",
    "Recovered from {region}. Whoever hid it wasn't making it easy — needed {req} first.",
    "Recovered from {region}. Not without {req} in hand.",
]


def _format_found_note(region: str, gate_raw: str = None, slot: int = 0) -> str:
    req = _describe_gate_raw(gate_raw)
    if req:
        template = _SOME_VARIANTS[slot % len(_SOME_VARIANTS)]
        return template.format(region=region, req=req)
    template = _NONE_VARIANTS[slot % len(_NONE_VARIANTS)]
    return template.format(region=region)


def build_data_files(output_dir: str, found_locations: dict = None):
    """Writes the 10 new folder/*.mup files (500-504 not-found, 510-514
    found) into output_dir/folder/. Caller is responsible for merging
    these into the mod KPF's mod_files dict at internal paths
    'folder/500.mup' etc., same as loc_english.txt/levels.txt today.

    found_locations: optional {slot (0-4): (level_region, gate_raw)}, e.g.
    {1: ("Gardelle County Jail, Texas", "PRISON_KEY_CARD")}. When a slot has
    an entry, its FOUND page gets an extra "Recovered from: <region>[,
    requiring <items>]" line -- the actual physical spot and access
    requirements this seed's shuffle placed that retractor behind, resolved
    by patcher.py via unique_retractor_keys_patch.resolve_retractor_destinations()
    + extracted_locations' level_region/gate_raw fields. level_region alone
    already captures level name AND sub-region where a level has named
    sub-areas (e.g. "Asylum: Lavaducts" vs a flatter level's own name used
    as-is, e.g. "Gardelle County Jail, Texas") -- Jon's request, 2026-08-18:
    "just say the level name, and then something about the sub regions or
    items it took to acquire it." gate_raw supplies the items/gates part;
    None/empty means freely accessible, no requirement clause shown. Slots
    with no entry at all (standalone/manual test runs with no real seed
    behind them) just skip the line -- no placeholder text, since we'd be
    making it up.

    2026-08-18: retheme pass (Jon) -- these are RETRACTORS that open
    SCHISMS to LIVESIDE, not "keys" to "doors". Old copy mixed the two
    metaphors (one line literally said "key for this door"); rewritten
    throughout to match the game's own vocabulary.
    """
    out = Path(output_dir) / "folder"
    out.mkdir(parents=True, exist_ok=True)
    found_locations = found_locations or {}

    WORLDS = [
        (1, "MORDANT STREET, QUEENS, NY"),
        (2, "GARDELLE COUNTY JAIL, TEXAS"),
        (3, "DOWN STREET STATION, LONDON"),
        (4, "SUMMER CAMP, FLORIDA"),
        (5, "SALVAGE YARD, MOJAVE DESERT"),
    ]
    NOTFOUND_TEXT = {
        1: "Still sealed. Haven't turned up the retractor that opens this schism yet.",
        2: "Still sealed. Nothing on this schism yet -- keep an eye out.",
        3: "Still sealed. Haven't found the retractor for this schism.",
        4: "Still sealed. No sign of the retractor yet.",
        5: "Still sealed. Still looking for the retractor.",
    }
    FOUND_TEXT = {i: "Retractor recovered. This schism to Liveside will open now." for i in range(1, 6)}

    _BASE = (
        "COMMENT         <\r\n\r\n"
        "                FBI SERIAL KILLER PROFILE FOLDER\r\n\r\n"
        "                English\r\n\r\n"
        "                The Five Schisms -- {region}\r\n"
        "---------------------------------------------------------->\r\n"
        "POSITION        <0, 0>\r\n"
        "IMAGE           <Current, \"Data\\Folder\\BackPg01.Bmp\", 540, 480>\r\n"
        "POSITION        <540, 0>\r\n"
        "IMAGE           <Current, \"Data\\Folder\\Side01.Bmp\", 100, 480>\r\n"
        "FONT            <\"Courier New Bold\", 8, 15>\r\n"
        "COLOR           <20, 20, 10>\r\n"
        "POSITION        <225, 90>\r\n"
        "STRING          <\"THE FIVE SCHISMS\">\r\n"
        "FONT            <\"Courier New\", 7, 14>\r\n"
        "POSITION        <125, 140>\r\n"
        "MARGIN          <125, 515>\r\n"
        "STRING          <\"{region}\">\r\n"
        "NEWLINE         <>\r\n"
        "NEWLINE         <>\r\n"
        "STRING          <\"{body}\">\r\n"
    )
    _FACT_BLOCK = (
        "NEWLINE         <>\r\n"
        "NEWLINE         <>\r\n"
        "STRING          <\"{note}\">\r\n"
    )
    _END = "END             <>\r\n"

    written = []
    for slot, (world_id, region) in enumerate(WORLDS):
        nf_path = out / f"{NOTFOUND_PAGE_BASE + slot}.mup"
        fnd_path = out / f"{FOUND_PAGE_BASE + slot}.mup"

        nf_path.write_text(_BASE.format(region=region, body=NOTFOUND_TEXT[world_id]) + _END)

        fnd_content = _BASE.format(region=region, body=FOUND_TEXT[world_id])
        found_data = found_locations.get(slot)
        if found_data:
            found_region, found_gate_raw = found_data
            note = _format_found_note(found_region, found_gate_raw, slot=slot)
            fnd_content += _FACT_BLOCK.format(note=note)
        fnd_content += _END
        fnd_path.write_text(fnd_content)

        written += [str(nf_path), str(fnd_path)]
    return written


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("exe")
    p.add_argument("--dry-run", action="store_true", default=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--verify-only", action="store_true")
    p.add_argument("--revert", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--build-pages", metavar="DIR", help="Write the 10 new folder/*.mup files to DIR")
    args = p.parse_args()
    if args.build_pages:
        files = build_data_files(args.build_pages)
        print(f"Wrote {len(files)} files:")
        for f in files:
            print(" ", f)
    if args.revert:
        revert_patch(args.exe, dry_run=not args.apply)
    else:
        apply_patch(args.exe, dry_run=not args.apply, verify_only=args.verify_only, force=args.force)
