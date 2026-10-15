"""
save_path_patch.py
===================
Redirects Shadow Man Remastered's save folder from "saves/" to "rando/" (a
sibling folder next to the vanilla saves directory), so standalone
randomizer playthroughs never share save slots with vanilla saves —
without touching the game's own save/load code at all, and with zero
setup steps for the player.

(This is a per-repo copy: the Archipelago world's copy of this same module,
worlds/shadowman/save_path_patch.py, uses "ap/" instead of "rando/", so AP
multiworld saves, standalone randomizer saves, and vanilla saves all land
in three separate folders that never collide with each other.)

WHY AN EXE PATCH INSTEAD OF A FILESYSTEM REDIRECT
--------------------------------------------------
An NTFS junction (saves -> some other folder) was considered first, but it
redirects at the OS level, which is global to the machine: it would also
redirect the UNPATCHED thoth_x64.exe (vanilla play), forcing a manual
toggle every time the player switches between vanilla and randomizer.
Patching the path INTO the patched exe means the redirect only exists in
thoth_x64_patched.exe -- launching vanilla thoth_x64.exe still reads/writes
the original "saves/" folder, untouched, no toggling ever required.

HOW IT WORKS
------------
FUN_14033e3b0 (thoth_x64.exe) is the single shared function that appends
the save-directory leaf name onto the game's base config path. Confirmed
via Ghidra (2026-07-20):
  - Its "saves/" string reference has exactly ONE XREF (the LEA at
    14033e408) -- an isolated, single-purpose literal, not shared with
    unrelated systems (contrast with "Shadowman EX"/"Nightdive Studios",
    which have 20 and 12 XREFs respectively across config/log/console-
    history code -- patching either of those would have much wider blast
    radius for no reason).
  - FUN_14033e3b0 itself is called from 9 separate save-related call sites
    (load, write, enumerate, etc.), all funneling through this one
    function -- so patching this one literal covers every save-directory
    consumer in the game.

Two edits, both IN-PLACE (no new space needed, no pointer redirect):

  1. The "saves/" string itself (.rdata, VA 0x140704760, 7 bytes incl. the
     null terminator) -> "rando/\0". Confirmed via the Ghidra string dump
     that the very next data item ("g_saveslot") starts immediately at
     VA 0x140704768 -- zero slack beyond the original 8-byte window. "rando/"
     is 6 chars, same length as "saves/", so it fits with room to spare
     (unlike a longer name, which would need scratch space + a LEA
     displacement patch -- the technique gad_pickup_patch.py uses for its
     longer RSC_X_GAD_PICKUP string).
  2. The explicit length argument passed alongside it -- FUN_14033e3b0's own
     `MOV R8D, 6` at VA 0x14033e402 (immediate byte at 0x14033e404, right
     before the LEA that loads the string's address) -- stays 6, since
     "rando/" is the same length as "saves/" (patched anyway, from
     NEW_LEAF, so this stays correct automatically if NEW_LEAF ever changes).

Confirmed via decompile that FUN_1401a6610 (the string-builder call this
feeds into) takes the length as an explicit parameter rather than scanning
for a null terminator, and builds into a heap-allocated string object
(operator_new), not a fixed-size stack buffer -- so there's no
truncation/overflow risk from changing the length value itself. (This
patch keeps the new string SHORTER than the original anyway, so the point
is moot here, but matters if NEW_LEAF is ever changed to something longer
that still fits the 8-byte window, e.g. "aps/".)

RESULT
------
thoth_x64_patched.exe reads/writes save_00.sav..save_09.sav and
user_activity.dat from:
    <Saved Games>\\Nightdive Studios\\Shadowman EX\\rando\\
instead of the vanilla:
    <Saved Games>\\Nightdive Studios\\Shadowman EX\\saves\\
The "rando" folder is created by the game itself the first time it saves
(same as "saves" already is) -- nothing needs to pre-create it. Vanilla
saves are completely untouched since only the patched exe resolves the
different path.
"""

from pathlib import Path
from typing import Optional

IMAGE_BASE = 0x140000000

# Mirrors gad_pickup_patch.py's SECTIONS table -- same exe, same layout.
# Different sections have DIFFERENT file-offset deltas (.text: 0xC00,
# .rdata: 0x1800) -- do not reuse one section's delta for an address in
# another section.
SECTIONS = [
    {'name': '.text',  'vaddr': 0x00001000, 'vsize': 0x00649354,
     'raw':  0x00000400, 'rsize': 0x00649400},
    {'name': '.rdata', 'vaddr': 0x0064B000, 'vsize': 0x001A0D74,
     'raw':  0x00649800, 'rsize': 0x001A0E00},
    {'name': '.data',  'vaddr': 0x007EC000, 'vsize': 0x007D8DC8,
     'raw':  0x007EA600, 'rsize': 0x004FEE00},
]


def _va_to_file(va: int) -> Optional[int]:
    rva = va - IMAGE_BASE
    for s in SECTIONS:
        if s['vaddr'] <= rva < s['vaddr'] + s['vsize']:
            return rva - (s['vaddr'] - s['raw'])
    return None


# ── Patch sites ───────────────────────────────────────────────────────────────

NEW_LEAF = "rando/"   # default for the standalone patcher.py call path --
                      # must stay <= 7 bytes incl. null (confirmed budget:
                      # "g_saveslot" string starts immediately after, zero
                      # slack beyond it). ap_patcher.py (this repo's ported
                      # copy of the AP world's patcher, used by
                      # apply_ap_seed.py) explicitly passes leaf="ap/"
                      # instead -- see apply_save_path_patch()'s docstring
                      # for why this must not silently default to "rando/"
                      # for AP-applied seeds.

STRING_VA       = 0x140704760
STRING_FILE_OFF = _va_to_file(STRING_VA)
STRING_SLOT_LEN = 8            # confirmed available before "g_saveslot" begins
STRING_VANILLA  = b"saves/\x00\x00"

LEN_IMM_VA       = 0x14033E404   # immediate operand byte of `MOV R8D, 6`
LEN_IMM_FILE_OFF = _va_to_file(LEN_IMM_VA)
LEN_IMM_VANILLA  = 0x06


def apply_save_path_patch(exe_path: str, *, leaf: str = NEW_LEAF, dry_run: bool = False) -> None:
    """
    Redirect the save-directory leaf from "saves/" to `leaf` in
    thoth_x64_patched.exe. Idempotent -- skips if already applied. Raises
    if the vanilla bytes don't match at either site (unknown exe version,
    or already patched to a different value) rather than writing blind.

    `leaf` MUST be passed explicitly by callers that care which save folder
    they get -- this repo's own patcher.py relies on the "rando/" default,
    but ap_patcher.py (the ported copy of the AP world's patcher.py, driven
    by apply_ap_seed.py) needs "ap/" so Archipelago-applied seeds land in
    the same save folder the AP world itself would have used, matching
    client.py's _SAVE_SUBDIRS lookup order (checks "ap" before "saves").
    Getting this wrong doesn't corrupt anything -- the exe just ends up
    reading/writing a save folder your AP client isn't looking in.
    """
    leaf_bytes = leaf.encode("ascii") + b"\x00"
    if len(leaf_bytes) > STRING_SLOT_LEN:
        raise ValueError(
            f"leaf {leaf!r} ({len(leaf_bytes)} bytes incl. null) doesn't fit "
            f"the confirmed {STRING_SLOT_LEN}-byte slot -- would corrupt the "
            f"adjacent 'g_saveslot' string."
        )
    len_imm_new = len(leaf)

    path = Path(exe_path)
    if not path.exists():
        print(f"  [save_path] EXE not found: {exe_path} — skipping")
        return

    data = bytearray(path.read_bytes())

    new_string_bytes = leaf_bytes.ljust(STRING_SLOT_LEN, b"\x00")
    current_string   = bytes(data[STRING_FILE_OFF:STRING_FILE_OFF + STRING_SLOT_LEN])
    current_len_imm  = data[LEN_IMM_FILE_OFF]

    if current_string == new_string_bytes and current_len_imm == len_imm_new:
        print(f"  [save_path] Already patched (saves -> {leaf!r})")
        return

    if current_string != STRING_VANILLA:
        raise ValueError(
            f"  [save_path] Unexpected bytes at string site (file offset "
            f"0x{STRING_FILE_OFF:X}): {current_string!r} "
            f"(expected {STRING_VANILLA!r}). Refusing to patch — "
            f"exe version mismatch?"
        )
    if current_len_imm != LEN_IMM_VANILLA:
        raise ValueError(
            f"  [save_path] Unexpected length immediate (file offset "
            f"0x{LEN_IMM_FILE_OFF:X}): {current_len_imm} "
            f"(expected {LEN_IMM_VANILLA}). Refusing to patch — "
            f"exe version mismatch?"
        )

    if not dry_run:
        data[STRING_FILE_OFF:STRING_FILE_OFF + STRING_SLOT_LEN] = new_string_bytes
        data[LEN_IMM_FILE_OFF] = len_imm_new
        path.write_bytes(data)

    print(f"  [save_path] Save folder redirected: 'saves/' -> {leaf!r} "
          f"(string @ 0x{STRING_FILE_OFF:X}, length imm @ 0x{LEN_IMM_FILE_OFF:X})")
