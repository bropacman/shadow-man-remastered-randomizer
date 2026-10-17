"""
life_altar_patch.py
===================
Keeps the Wasteland life altars usable at max health, for the AP world's
Life Altar Checks option (docs/scope_life_altar_checks.md).

The altar handler FUN_14043f2f0 refuses an offering when max health
(DAT_140db21fc) is exactly 10000:

    14043f4d8: mov eax, dword ptr [DAT_140db21fc]
    14043f4de: cmp eax, 0x2710                     3D 10 27 00 00
    14043f4e3: je  0x14043f9b8   (refuse)          0F 84 CF 04 00 00

Granting health is a separate add-then-clamp-to-10000 further down, so
removing the refusal leaves the cap intact: at 10000 an offering still
spends Cadeaux and sets its CF_LIFEALTAR flag (which sends the check), it
just adds no health. Without this, a big Altar Health Grant (or high
Starting Health) can hit the cap before all five altars are used, leaving
offering checks unreachable.

The `je` is replaced by a 6-byte NOP. Raises if the bytes are neither
vanilla nor already patched.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .death_penalty_patch import _va_to_file

CMP_VA     = 0x14043F4DE
REFUSE_VA  = 0x14043F4E3
CMP_BYTES  = bytes.fromhex("3D10270000")       # cmp eax, 0x2710
VANILLA_JE = bytes.fromhex("0F84CF040000")     # je 0x14043f9b8
PATCHED_JE = bytes.fromhex("660F1F440000")     # nop word ptr [rax+rax*1+0x0]


def apply_life_altar_patch(exe_path: str, *, dry_run: bool = False) -> bool:
    """Remove the altar's max-health refusal. Returns True if the exe is
    patched afterwards (including already patched)."""
    path = Path(exe_path)
    data = bytearray(path.read_bytes())
    cmp_off = _va_to_file(CMP_VA)
    je_off = _va_to_file(REFUSE_VA)

    if bytes(data[cmp_off:cmp_off + len(CMP_BYTES)]) != CMP_BYTES:
        raise RuntimeError(
            f"life_altar_patch: expected `cmp eax, 0x2710` at file 0x{cmp_off:X}, "
            f"found {bytes(data[cmp_off:cmp_off + 5]).hex()} -- wrong exe build?")
    current = bytes(data[je_off:je_off + len(VANILLA_JE)])
    if current == PATCHED_JE:
        print("  [life_altar] Already patched -- skipping")
        return True
    if current != VANILLA_JE:
        raise RuntimeError(
            f"life_altar_patch: unexpected bytes at file 0x{je_off:X}: "
            f"{current.hex()} (expected vanilla {VANILLA_JE.hex()})")

    data[je_off:je_off + len(PATCHED_JE)] = PATCHED_JE
    if not dry_run:
        path.write_bytes(bytes(data))
    print(f"  [life_altar] Max-health refusal removed @ 0x{je_off:X} "
          f"(altars stay usable at 10000 max health)")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("exe", help="Path to a copy of thoth_x64.exe")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    apply_life_altar_patch(args.exe, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
