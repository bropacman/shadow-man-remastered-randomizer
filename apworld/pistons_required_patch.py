"""
pistons_required_patch.py
=========================
"N of 6 pistons required": once N Dark Engine pistons are stopped, every
remaining piston control treats its bars as solved the next time it ticks
and runs its own vanilla stop sequence (animation, sounds,
SetFlag(CF_DARKENG_STOPPEDn)). Downstream, the game sees six natural
solves. Design and findings: docs/scope_pistons_required.md.

HOOK -- kexShadowManPistonControl tick (0x140423850), state 2 (active).
At 0x140423a9e it checks its three linked kexShadowManPistonLevel bars:

    mov rax, [rdi+0x298] ; mov rcx, [rax+0x30] ; cmp byte [rcx+0x29c], r15b ; je NOT_SOLVED
    mov rax, [rdi+0x2a0] ; ...                                                ; je NOT_SOLVED
    mov rax, [rdi+0x2a8] ; ...                                                ; je NOT_SOLVED
    ; falls through to SOLVED_VA (0x140423ae6)

The first instruction becomes `jmp cave` (+ 2 NOPs). The cave re-runs the
three checks; if all pass it jumps to SOLVED_VA as vanilla would. On a
miss it counts flags 98..103 (CF_DARKENG_STOPPED1..6 = bits 2..7 of byte
12 of the flag manager's bit array, the same m_pBits field client.py's
read_named_flag() reads) and jumps to SOLVED_VA when count >= N, else to
NOT_SOLVED_VA. N is baked in at patch time; N == 6 means no patch.

Only rax/rcx (volatile) are clobbered. Both jump targets overwrite them
before reading them, and r15 stays untouched (vanilla expects it 0).

PLACEMENT -- one new R+X section, `.pscode`, at
IMAGE_BASE + align_up(SizeOfImage, SectionAlignment) read from the file at
apply time (gapless, CLAUDE.md §8). secret_mode_section_patch.py still
uses fixed VAs at the vanilla image end, so this patch must be applied
AFTER it (and after any other section-appending patch); the pipelines
apply it last.

The vanilla header has room for only 4 extra section headers (table ends
at 0x348, first raw data at 0x400). secret_mode_section_patch and
unique_retractor_keys_patch take 2 each, so with both applied there is no
slot for `.pscode`. In that case the cave goes in the zero raw slack of
an appended code section and that section's VirtualSize grows to cover
it -- the same trick retractor_folder_log_patch uses for its Hook D.
.rkcode still has 128 bytes free after Hook D; the cave is 109.

This module is duplicated as apworld/pistons_required_patch.py; keep the
two byte-identical (tools/check_apworld_sync.py).
"""

import struct
from pathlib import Path

IMAGE_BASE = 0x140000000
SECTION_DELTA = 0xC00            # .text: vaddr 0x1000, raw 0x400
FILE_ALIGNMENT = 0x200
SECTION_ALIGNMENT = 0x1000
SECTION_NAME = b".pscode"

HOOK_VA = 0x140423A9E            # file 0x422E9E
SOLVED_VA = 0x140423AE6          # "all three bars at target" path
NOT_SOLVED_VA = 0x1404243D8      # vanilla miss target of all three je's

# The full 72-byte bar check -- verified before patching even though only
# the first 7 bytes are overwritten.
HOOK_VANILLA = bytes.fromhex(
    "488b8798020000488b48304438b99c0200000f8422090000"
    "488b87a0020000488b48304438b99c0200000f840a090000"
    "488b87a8020000488b48304438b99c0200000f84f2080000"
)
HOOK_PATCH_LEN = 7               # mov rax, [rdi+0x298]

FLAG_MANAGER_VA = 0x140F9C680    # DAT_140f9c680, the CF_* flag manager
FLAG_BITS_PTR_VA = FLAG_MANAGER_VA + 0x140A8   # -> m_pBits (qword)
STOPPED_FLAG_FIRST = 98          # CF_DARKENG_STOPPED1
STOPPED_BYTE = STOPPED_FLAG_FIRST >> 3    # 12
STOPPED_SHIFT = STOPPED_FLAG_FIRST & 7    # 2

BAR_OFFSETS = (0x298, 0x2A0, 0x2A8)
BAR_LEVEL_OFF = 0x30
BAR_AT_TARGET_OFF = 0x29C

PISTONS_TOTAL = 6


def _align_up(v: int, a: int) -> int:
    return (v + a - 1) // a * a


def _va_to_text_file(va: int) -> int:
    return va - IMAGE_BASE - SECTION_DELTA


def _cave_text(required: int, flag_disp: int) -> str:
    checks = "\n".join(
        f"mov rax, qword ptr [rdi + {off:#x}]\n"
        f"mov rcx, qword ptr [rax + {BAR_LEVEL_OFF:#x}]\n"
        f"cmp byte ptr [rcx + {BAR_AT_TARGET_OFF:#x}], r15b\n"
        f"je count"
        for off in BAR_OFFSETS
    )
    # The tally is a shift loop rather than 6 unrolled steps or popcnt:
    # it keeps the cave small enough for .rkcode's leftover slack.
    return f"""
{checks}
jmp {SOLVED_VA:#x}
count:
mov rax, qword ptr [rip + {flag_disp:#x}]
test rax, rax
jz fail
movzx eax, byte ptr [rax + {STOPPED_BYTE:#x}]
shr eax, {STOPPED_SHIFT}
xor ecx, ecx
tally:
shr eax, 1
adc ecx, 0
test eax, eax
jnz tally
cmp ecx, {required}
jae {SOLVED_VA:#x}
fail:
jmp {NOT_SOLVED_VA:#x}
"""


def build_cave(cave_va: int, required: int) -> bytes:
    """Assembles the cave at cave_va. Two passes: the rip-relative flag
    load's displacement depends on where that instruction lands, which
    the first pass (displacement 0) tells us; disp32 is fixed-width, so
    the layout doesn't move."""
    import capstone
    from keystone import Ks, KS_ARCH_X86, KS_MODE_64

    if not 0 <= required < PISTONS_TOTAL:
        raise ValueError(f"pistons_required cave needs 0 <= N < 6, got {required}")
    ks = Ks(KS_ARCH_X86, KS_MODE_64)
    cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    cs.detail = True

    def asm(disp: int) -> bytes:
        enc, _ = ks.asm(_cave_text(required, disp), cave_va)
        if not enc:
            raise ValueError("pistons_required cave: assembly failed")
        return bytes(enc)

    first = asm(0)
    rip_insns = [i for i in cs.disasm(first, cave_va)
                 if any(op.type == capstone.x86.X86_OP_MEM
                        and op.mem.base == capstone.x86.X86_REG_RIP
                        for op in i.operands)]
    if len(rip_insns) != 1:
        raise ValueError(f"pistons_required cave: expected 1 rip-relative load, got {len(rip_insns)}")
    ins = rip_insns[0]
    disp = FLAG_BITS_PTR_VA - (ins.address + ins.size)
    code = asm(disp)
    if len(code) != len(first):
        raise ValueError("pistons_required cave: layout changed between passes")
    return code


def verify_cave(code: bytes, cave_va: int, required: int) -> list:
    """Structural check by operand type, independent of the assembler text."""
    import capstone
    from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_OP_REG, X86_REG_RIP

    cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    cs.detail = True
    insns = list(cs.disasm(code, cave_va))
    errors = []
    if sum(i.size for i in insns) != len(code):
        errors.append("cave does not disassemble cleanly end to end")

    def reg(r):
        return cs.reg_name(r) if r else None

    bar_loads = [i.operands[1].mem.disp for i in insns
                 if i.mnemonic == "mov" and len(i.operands) == 2
                 and i.operands[0].type == X86_OP_REG and reg(i.operands[0].reg) == "rax"
                 and i.operands[1].type == X86_OP_MEM and reg(i.operands[1].mem.base) == "rdi"]
    if bar_loads != list(BAR_OFFSETS):
        errors.append(f"bar pointer loads from rdi: {[hex(d) for d in bar_loads]}")

    bar_cmps = [i for i in insns
                if i.mnemonic == "cmp" and len(i.operands) == 2
                and i.operands[0].type == X86_OP_MEM and i.operands[0].size == 1
                and reg(i.operands[0].mem.base) == "rcx"
                and i.operands[0].mem.disp == BAR_AT_TARGET_OFF
                and i.operands[1].type == X86_OP_REG and reg(i.operands[1].reg) == "r15b"]
    if len(bar_cmps) != 3:
        errors.append(f"expected 3 `cmp byte [rcx+0x29c], r15b`, got {len(bar_cmps)}")

    def targets(mnemonic):
        return [i.operands[0].imm for i in insns
                if i.mnemonic == mnemonic and i.operands and i.operands[0].type == X86_OP_IMM]

    # je x4: three bar misses -> the rip-relative flag load (count label),
    # then the null check -> the final `jmp NOT_SOLVED_VA` (fail label).
    by_addr = {i.address: i for i in insns}
    je = targets("je")
    if len(je) != 4:
        errors.append(f"expected 4 je, got {len(je)}")
    else:
        count = by_addr.get(je[0])
        if len(set(je[:3])) != 1 or count is None or not any(
                op.type == X86_OP_MEM and op.mem.base == X86_REG_RIP for op in count.operands):
            errors.append(f"bar-miss je's don't land on the flag load: {[hex(t) for t in je[:3]]}")
        fail = by_addr.get(je[3])
        if fail is None or fail.mnemonic != "jmp" or fail.operands[0].imm != NOT_SOLVED_VA:
            errors.append(f"null-check je doesn't land on `jmp NOT_SOLVED`: {je[3]:#x}")
    jmps = targets("jmp")
    if sorted(jmps) != sorted([SOLVED_VA, NOT_SOLVED_VA]):
        errors.append(f"jmp targets {[hex(t) for t in jmps]}")
    if targets("jae") != [SOLVED_VA]:
        errors.append(f"jae targets {[hex(t) for t in targets('jae')]}")

    rip_loads = [i.address + i.size + i.operands[1].mem.disp for i in insns
                 if len(i.operands) == 2 and i.operands[1].type == X86_OP_MEM
                 and i.operands[1].mem.base == X86_REG_RIP]
    if rip_loads != [FLAG_BITS_PTR_VA]:
        errors.append(f"flag bits pointer load resolves to {[hex(a) for a in rip_loads]}")

    byte_loads = [i.operands[1].mem.disp for i in insns
                  if i.mnemonic == "movzx" and i.operands[1].type == X86_OP_MEM
                  and i.operands[1].size == 1]
    if byte_loads != [STOPPED_BYTE]:
        errors.append(f"flag byte load disps {byte_loads}")

    shifts = [i.operands[1].imm for i in insns
              if i.mnemonic == "shr" and len(i.operands) == 2
              and i.operands[1].type == X86_OP_IMM]
    if shifts != [STOPPED_SHIFT, 1]:
        errors.append(f"shr immediates {shifts}, expected [{STOPPED_SHIFT}, 1]")

    # Tally loop: shr eax,1 / adc ecx,0 / test eax,eax / jne back to that shr.
    # After `shr eax, 2` at most 6 bits remain, so it runs at most 6 times.
    adcs = [i for i in insns if i.mnemonic == "adc"]
    jnes = [i for i in insns if i.mnemonic == "jne"]
    top = by_addr.get(jnes[0].operands[0].imm) if len(jnes) == 1 else None
    if len(adcs) != 1 or top is None or top.mnemonic != "shr" \
            or not top.address < adcs[0].address < jnes[0].address:
        errors.append("tally loop (shr / adc / test / jne back) not found")

    n_cmps = [i.operands[1].imm for i in insns
              if i.mnemonic == "cmp" and len(i.operands) == 2
              and i.operands[0].type == X86_OP_REG and reg(i.operands[0].reg) == "ecx"
              and i.operands[1].type == X86_OP_IMM]
    if n_cmps != [required]:
        errors.append(f"N compare immediates {n_cmps}, expected [{required}]")

    if not any(i.mnemonic == "test" and reg(i.operands[0].reg) == "rax" for i in insns):
        errors.append("no null check on the bits pointer")
    return errors


def _make_hook(cave_va: int) -> bytes:
    disp = cave_va - (HOOK_VA + 5)
    return b"\xE9" + struct.pack("<i", disp) + b"\x90" * (HOOK_PATCH_LEN - 5)


def verify_hook(hook: bytes, cave_va: int) -> list:
    import capstone
    from capstone.x86 import X86_OP_IMM

    cs = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    cs.detail = True
    insns = list(cs.disasm(hook, HOOK_VA))
    errors = []
    if not insns or insns[0].mnemonic != "jmp" or insns[0].operands[0].type != X86_OP_IMM \
            or insns[0].operands[0].imm != cave_va:
        errors.append("hook is not `jmp cave`")
    if [i.mnemonic for i in insns[1:]] != ["nop"] * (HOOK_PATCH_LEN - 5):
        errors.append("hook padding is not NOPs")
    return errors


def _pe_fields(data):
    pe_off = struct.unpack_from("<I", data, 0x3C)[0]
    coff_off = pe_off + 4
    opt_off = coff_off + 20
    return {
        "coff_off": coff_off,
        "opt_off": opt_off,
        "num_sections": struct.unpack_from("<H", data, coff_off + 2)[0],
        "size_opt_hdr": struct.unpack_from("<H", data, coff_off + 16)[0],
        "section_alignment": struct.unpack_from("<I", data, opt_off + 32)[0],
        "file_alignment": struct.unpack_from("<I", data, opt_off + 36)[0],
        "size_of_image_off": opt_off + 56,
        "size_of_image": struct.unpack_from("<I", data, opt_off + 56)[0],
    }


def apply_patch(exe_path: str, required: int, dry_run: bool = False) -> bool:
    """Patches exe_path in place so N = `required` stopped pistons stop the
    rest. Returns False (and touches nothing) when required >= 6. Raises
    on non-vanilla hook bytes -- always patch a fresh vanilla copy."""
    required = int(required)
    if required >= PISTONS_TOTAL:
        print(f"Pistons required: {required} / 6 -- vanilla, exe untouched")
        return False
    if required < 1:
        raise ValueError(f"pistons_required must be 1..6, got {required}")

    path = Path(exe_path)
    data = bytearray(path.read_bytes())

    off = _va_to_text_file(HOOK_VA)
    actual = bytes(data[off:off + len(HOOK_VANILLA)])
    if actual != HOOK_VANILLA:
        raise RuntimeError(
            f"pistons_required_patch: hook bytes at 0x{HOOK_VA:X} are not vanilla "
            f"(got {actual.hex()}). Already patched, or wrong exe -- patch a fresh "
            f"vanilla copy instead.")

    f = _pe_fields(data)
    if f["section_alignment"] != SECTION_ALIGNMENT or f["file_alignment"] != FILE_ALIGNMENT:
        raise RuntimeError("pistons_required_patch: unexpected PE alignment values")

    sect_table_off = f["opt_off"] + f["size_opt_hdr"]
    num = f["num_sections"]
    headers = [struct.unpack_from("<8sIIIII", data, sect_table_off + i * 40) for i in range(num)]
    if any(h[0].rstrip(b"\x00") == SECTION_NAME for h in headers):
        raise RuntimeError("pistons_required_patch: .pscode section already present")
    first_raw_ptr = min(h[4] for h in headers if h[4])
    has_header_slack = sect_table_off + (num + 1) * 40 <= first_raw_ptr

    # Probe length: the cave's size doesn't depend on where it lands.
    cave_len = len(build_cave(IMAGE_BASE + 0x1000, required))

    if has_header_slack:
        # Normal path: a new gapless section at the image's current end.
        host = None
        cave_rva = _align_up(f["size_of_image"], f["section_alignment"])
    else:
        # The vanilla header only has room for 4 extra section headers, and
        # secret_mode_section_patch + unique_retractor_keys_patch use all 4.
        # Then the cave goes in the zero raw slack of one of their code
        # sections (inside its own page), and that section's VirtualSize
        # grows to cover it. No header is added, SizeOfImage is unchanged.
        host = None
        for i, (name, vsize, va, rsize, rptr, _) in enumerate(headers):
            chars = struct.unpack_from("<I", data, sect_table_off + i * 40 + 36)[0]
            if name.rstrip(b"\x00") == b".text" or not chars & 0x20000000:
                continue
            start = _align_up(vsize, 16)
            end = start + cave_len
            if end <= rsize and _align_up(end, f["section_alignment"]) <= _align_up(
                    max(vsize, 1), f["section_alignment"]) \
                    and not any(data[rptr + start:rptr + end]):
                host = i
                cave_rva = va + start
                break
        if host is None:
            raise RuntimeError("pistons_required_patch: no header slack for a new section and "
                               "no appended code section with room for the cave")
    cave_va = IMAGE_BASE + cave_rva

    code = build_cave(cave_va, required)
    hook = _make_hook(cave_va)
    errors = verify_cave(code, cave_va, required) + verify_hook(hook, cave_va)
    if errors:
        raise RuntimeError("pistons_required_patch: Capstone verification failed:\n  - "
                           + "\n  - ".join(errors))

    where = ("new section .pscode" if host is None
             else f"slack of {headers[host][0].rstrip(bytes(1)).decode()}")
    print(f"Pistons required: {required} / 6 -- cave 0x{cave_va:X} ({len(code)} bytes, {where})")
    if dry_run:
        return True

    if host is None:
        raw_ptr = _align_up(len(data), FILE_ALIGNMENT)
        raw_size = _align_up(len(code), FILE_ALIGNMENT)
        data.extend(b"\x00" * (raw_ptr - len(data)))
        data.extend(code)
        data.extend(b"\x00" * (raw_size - len(code)))

        chars = 0x00000020 | 0x20000000 | 0x40000000   # CODE | EXECUTE | READ
        hdr = struct.pack("<8sIIIIIIHHI", SECTION_NAME.ljust(8, b"\x00"), len(code),
                          cave_rva, raw_size, raw_ptr, 0, 0, 0, 0, chars)
        hdr_off = sect_table_off + num * 40
        data[hdr_off:hdr_off + 40] = hdr     # overwrite header slack, never insert
        struct.pack_into("<H", data, f["coff_off"] + 2, num + 1)
        struct.pack_into("<I", data, f["size_of_image_off"],
                         _align_up(cave_rva + len(code), f["section_alignment"]))
    else:
        _, vsize, va, _, rptr, _ = headers[host]
        start = cave_rva - va
        data[rptr + start:rptr + start + len(code)] = code
        struct.pack_into("<I", data, sect_table_off + host * 40 + 8, start + len(code))

    data[off:off + HOOK_PATCH_LEN] = hook
    path.write_bytes(bytes(data))
    return True


def revert_patch(exe_path: str) -> bool:
    """Restores the hook's first instruction. Leaves the .pscode section in
    place (unreferenced, harmless), same as the other section patches."""
    path = Path(exe_path)
    data = bytearray(path.read_bytes())
    off = _va_to_text_file(HOOK_VA)
    if bytes(data[off:off + HOOK_PATCH_LEN]) == HOOK_VANILLA[:HOOK_PATCH_LEN]:
        return False
    if data[off] != 0xE9:
        raise RuntimeError("pistons_required_patch: hook site is neither vanilla nor our jmp")
    data[off:off + HOOK_PATCH_LEN] = HOOK_VANILLA[:HOOK_PATCH_LEN]
    path.write_bytes(bytes(data))
    return True


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("exe")
    p.add_argument("--required", type=int, default=3)
    p.add_argument("--apply", action="store_true", help="write the file (default: dry run)")
    p.add_argument("--revert", action="store_true")
    a = p.parse_args()
    if a.revert:
        print("reverted" if revert_patch(a.exe) else "already vanilla")
    else:
        apply_patch(a.exe, a.required, dry_run=not a.apply)
