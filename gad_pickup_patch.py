"""
gad_pickup_patch.py
===================
Adds RSC_X_GAD_PICKUP as a functional physical pickup item to thoth_x64.exe.

HOW IT WORKS
------------
Six patches total, no .data writes:

  1. String "RSC_X_GAD_PICKUP" -> .rdata at 0x1406A711F
  2. Overwrite RSC_X_FBIFILE dispatch table entry (index 32):
       string ptr -> 0x1406A711F, param1 -> TYPE_ID (default 0x16, Prophecy book)
  3. Write two 22-byte stubs into code cave at 0x14064A3D3:
       Stub1 (+1): ADD [gad_level],1 + MOV RCX,RDI + XOR EDX,EDX
                   + CALL FUN_140459d50 + JMP common_tail
       Stub2 (+4): same but ADD 4
  4. Write 17-byte common_tail into dead space of old case 0x13 handler:
       LEA RCX,[gn0023s.wav] + CALL FUN_1403f0060 + JMP LAB_140446e2d
  5. case 0x16 (gad pickup): JMP -> stub1
  6. case 0x13 (Poigne):     JMP -> stub2
  7. NOP all 8 writes in FUN_140459c00 switch (temple absolute gad sets)

MODEL PICKER
------------
Pass --model RSC_NAME to use a different visual model for the pickup.
The script scans the dispatch table to find the type_id for the given RSC name.
Default is RSC_X_PROPHECY (type_id 0x16, confirmed valid at runtime).
"""

import shutil
import struct
import argparse
import sys
from pathlib import Path

IMAGE_BASE = 0x140000000

GAD_PICKUP_RSC   = "RSC_X_GAD_PICKUP"
GAD_PICKUP_LABEL = "Gad Power Upgrade"

# ── User configuration ────────────────────────────────────────────────────────
# Visual model for the gad pickup item. Must be a valid RSC name in the
# dispatch table with a confirmed runtime model entry.
#
# Safe type_ids (pickup handler case does nothing harmful):
#   0x02, 0x03, 0x06, 0x0A, 0x0B, 0x12  -- shared harmless audio case
#   0x16                                  -- Prophecy book (confirmed working)
#   0x17                                  -- plays audio + UI sound only
#
# Use --list-models to see all RSC names and their type_ids.
# Use --list-models --safe to show only safe candidates.
GAD_MODEL_RSC = "RSC_X_PROPHECY"

# Audio file to play on gad pickup. Set to None for no sound.
GAD_AUDIO_PATH = "audio/speech/generic/gn0083s.wav"

# Audio file to play on Poigne pickup. Set to None for no sound.
POIGNE_AUDIO_PATH = "audio/speech/generic/gn0082s.wav"
# ─────────────────────────────────────────────────────────────────────────────

TYPE_ID = 0x16  # resolved from GAD_MODEL_RSC at patch time -- don't edit this

# Type_ids whose pickup handler cases are safe to redirect to our gad stub.
# These cases do nothing harmful (audio/UI only, no flag sets or game state changes).
SAFE_TYPE_IDS = {
    0x02, 0x03, 0x06, 0x0A, 0x0B, 0x12,  # shared harmless audio case
    0x16,                                  # Prophecy book case (confirmed)
    0x17,                                  # audio + UI sound only
}

HEADER_SIZE  = 8
RECORD_SIZE  = 72
NAME_OFF     = 0x22
NAME_MAXLEN  = 30
ZONE_OFF     = 0x11
INSTANCE_OFF = 0x21
XYZ_OFF      = 0x04

# ── Key addresses ─────────────────────────────────────────────────────────────

GAD_LEVEL_VA  = 0x140F9C1A0
FUN_459D50_VA = 0x140459D50    # apply gad level (textures, flags, abilities)
FUN_AUDIO_VA  = 0x1403F0060    # play audio file
AUDIO_STR_VA  = 0x14071A4C0    # "audio/speech/generic/gn0023s.wav"
JMP_END_VA    = 0x140446E2D    # LAB_140446e2d (end of pickup handler)

# Dispatch table
TABLE_VA    = 0x140C9CEE0
TABLE_COUNT = 0x2DC
ENTRY_SIZE  = 32

# ── Section info ──────────────────────────────────────────────────────────────

SECTIONS = [
    {'name': '.text',  'vaddr': 0x00001000, 'vsize': 0x00649354,
     'raw':  0x00000400, 'rsize': 0x00649400},
    {'name': '.rdata', 'vaddr': 0x0064B000, 'vsize': 0x001A0D74,
     'raw':  0x00649800, 'rsize': 0x001A0E00},
    {'name': '.data',  'vaddr': 0x007EC000, 'vsize': 0x007D8DC8,
     'raw':  0x007EA600, 'rsize': 0x004FEE00},
]


def _va_to_file(va: int) -> int | None:
    rva = va - IMAGE_BASE
    for s in SECTIONS:
        if s['vaddr'] <= rva < s['vaddr'] + s['vsize']:
            return rva - (s['vaddr'] - s['raw'])
    # also check raw region for .text padding
    for s in SECTIONS:
        foff = rva - (s['vaddr'] - s['raw'])
        if s['raw'] <= foff < s['raw'] + s['rsize']:
            return foff
    return None


def _read_cstr(data: bytes, offset: int, maxlen: int = 64) -> str:
    end = data.find(b'\x00', offset, offset + maxlen)
    if end == -1:
        end = offset + maxlen
    try:
        return data[offset:end].decode('ascii')
    except UnicodeDecodeError:
        return ''


def lookup_type_id(exe_data: bytes, rsc_name: str) -> int | None:
    """Scan the dispatch table for rsc_name and return its type_id (param1)."""
    table_foff = _va_to_file(TABLE_VA)
    if table_foff is None:
        return None
    for i in range(TABLE_COUNT):
        entry_off = table_foff + i * ENTRY_SIZE
        if entry_off + ENTRY_SIZE > len(exe_data):
            break
        str_ptr = struct.unpack_from('<Q', exe_data, entry_off)[0]
        param1  = struct.unpack_from('<Q', exe_data, entry_off + 8)[0]
        if str_ptr == 0:
            continue
        str_foff = _va_to_file(str_ptr)
        if str_foff is None or str_foff >= len(exe_data):
            continue
        name = _read_cstr(exe_data, str_foff)
        if name.lower() == rsc_name.lower():
            return param1
    return None


# ── Patch constants ───────────────────────────────────────────────────────────

# Patch 1: strings in .rdata
STRING_FILE_OFF       = 0x6A591F
STRING_VA             = 0x1406A711F

# Gad audio string written right after RSC string (18 bytes later)
GAD_AUDIO_STR_FILE_OFF    = STRING_FILE_OFF + 18
GAD_AUDIO_STR_VA          = STRING_VA + 18

# Poigne audio string written after gad audio string (40 bytes later)
POIGNE_AUDIO_STR_FILE_OFF = GAD_AUDIO_STR_FILE_OFF + 40
POIGNE_AUDIO_STR_VA       = GAD_AUDIO_STR_VA + 40

# Patch 2: dispatch table entry (RSC_X_FBIFILE index 32)
ENTRY_FILE_OFF        = 0xC9B8E0
ENTRY_STR_PTR_OFF     = ENTRY_FILE_OFF + 0x00
ENTRY_PARAM1_OFF      = ENTRY_FILE_OFF + 0x08
ENTRY_VANILLA_STR_PTR = 0x1407013C8
ENTRY_VANILLA_PARAM1  = 0x0

# Patch 3: code cave — expanded to cover stubs + both tails
CAVE_VA       = 0x14064A354
CAVE_FILE_OFF = 0x649754
CAVE_VANILLA  = bytes(78)  # all zeros, covers stubs + both tails

# Patch 4a: gad pickup audio tail — now inside cave, not in Poigne handler
TAIL_VA       = 0x14064A380
TAIL_FILE_OFF = 0x649780

# Patch 4b: Poigne audio tail — immediately after gad tail, inside cave
POIGNE_TAIL_VA       = 0x14064A391
POIGNE_TAIL_FILE_OFF = 0x649791

# Patch 5: case 0x16 (gad pickup type_id)
CASE16_VA       = 0x140446C29
CASE16_FILE_OFF = 0x446029
CASE16_VANILLA  = bytes([0x48,0x8D,0x0D,0x18,0xBF,0x2D,0x00,
                          0xE9,0xF3,0x01,0x00,0x00])

# Patch 6: case 0x13 (Poigne)
CASE13_VA       = 0x140446B7F
CASE13_FILE_OFF = 0x445F7F
CASE13_VANILLA  = bytes([0xE8,0xDC,0x09,0xF0,0xFF,
                          0xBA,0x46,0x00,0x00,0x00,
                          0x48,0x8B])

# Temple NOP sites
TEMPLE_NOP_SITES = [
    ("gad1_case1",   0x4590B4, bytes([0x89, 0x2B]),                         2),
    ("gad2_case3",   0x4590B8, bytes([0xC7, 0x03, 0x02, 0x00, 0x00, 0x00]), 6),
    ("gad3_case7",   0x4590C0, bytes([0xC7, 0x03, 0x03, 0x00, 0x00, 0x00]), 6),
    ("gad4_case8",   0x4590C8, bytes([0xC7, 0x03, 0x04, 0x00, 0x00, 0x00]), 6),
    ("gad5_case9",   0x4590D0, bytes([0xC7, 0x03, 0x05, 0x00, 0x00, 0x00]), 6),
    ("gad6_caseb",   0x4590D8, bytes([0xC7, 0x03, 0x06, 0x00, 0x00, 0x00]), 6),
    ("gad7_casef",   0x4590E0, bytes([0xC7, 0x03, 0x07, 0x00, 0x00, 0x00]), 6),
    ("gad0_default", 0x4590E8, bytes([0xC7, 0x03, 0x00, 0x00, 0x00, 0x00]), 6),
]

# NOP the JZ that skips FUN_140331970 for RSC_X_PRISON_KEY_CARD (type_id 0x15)
# VA 0x14033AC22, file offset 0x33A022
# 74 08 (JZ +8) → 90 90 (NOP NOP)
PRISON_KEY_PATCHES = [
    {
        "offset": 0x311775,
        "expected": bytes([0xc7, 0x03, 0x00, 0xb8, 0x1e, 0xc6,
                           0xc7, 0x47, 0x24, 0x00, 0x00, 0x15, 0x43,
                           0xc7, 0x47, 0x28, 0x00, 0xc0, 0x49, 0x44,
                           0xc7, 0x47, 0x14, 0xdb, 0x0f, 0xc9, 0xbf,
                           0xc7, 0x47, 0x18, 0xdb, 0x0f, 0xc9, 0xbf,
                           0x44, 0x89, 0x7f, 0x1c,
                           0xeb, 0x07]),
        "patch": bytes([0x90] * 38) + bytes([0xeb, 0x07]),
        "label": "prison_key: NOP hardcoded position writes (keep JMP)",
        "size": 40,
    },
]

VERIFY_SITES = {
    "string_site":        (STRING_FILE_OFF,          bytes(18)),
    "gad_audio_str":      (GAD_AUDIO_STR_FILE_OFF,   bytes(40)),
    "poigne_audio_str":   (POIGNE_AUDIO_STR_FILE_OFF, bytes(40)),
    "entry_str_ptr":      (ENTRY_STR_PTR_OFF,         struct.pack('<Q', ENTRY_VANILLA_STR_PTR)),
    "entry_param1":       (ENTRY_PARAM1_OFF,          struct.pack('<Q', ENTRY_VANILLA_PARAM1)),
    "cave":               (CAVE_FILE_OFF,             CAVE_VANILLA),
    "case16":             (CASE16_FILE_OFF,           CASE16_VANILLA),
    "case13":             (CASE13_FILE_OFF,           CASE13_VANILLA),
}


# ── Patch builders ────────────────────────────────────────────────────────────

def _build_stub(stub_va: int, add_val: int, jmp_dst: int) -> bytes:
    """
    ADD dword ptr [gad_level], N   (7 bytes)
    MOV RCX, RDI                   (3 bytes) -- player ptr
    XOR EDX, EDX                   (2 bytes)
    CALL FUN_140459d50             (5 bytes) -- apply gad level immediately
    JMP  audio_tail                (5 bytes) -- play audio + return
    = 22 bytes
    """
    s = bytearray()
    rip = stub_va + 7
    s += bytes([0x83, 0x05]) + struct.pack('<i', GAD_LEVEL_VA - rip) + bytes([add_val])
    s += bytes([0x48, 0x8B, 0xCF])
    s += bytes([0x33, 0xD2])
    rip = stub_va + len(s) + 5
    s += bytes([0xE8]) + struct.pack('<i', FUN_459D50_VA - rip)
    rip = stub_va + len(s) + 5
    s += bytes([0xE9]) + struct.pack('<i', jmp_dst - rip)
    return bytes(s)


def _find_audio_va(exe_data: bytes, audio_path: str) -> int | None:
    """Scan .rdata for an existing audio path string and return its VA."""
    needle = audio_path.encode('ascii')
    idx = exe_data.find(needle)
    if idx == -1:
        return None
    # Convert file offset to VA using .rdata section
    rdata = next(s for s in SECTIONS if s['name'] == '.rdata')
    foff_start = rdata['raw']
    foff_end   = rdata['raw'] + rdata['rsize']
    if foff_start <= idx < foff_end:
        return IMAGE_BASE + idx + (rdata['vaddr'] - rdata['raw'])
    return None


def _build_audio_tail(tail_va: int, audio_va: int | None) -> bytes:
    """
    If audio_va is set:
      LEA  RCX, [audio_str]          (7 bytes)
      CALL FUN_1403f0060             (5 bytes)
    JMP  LAB_140446e2d               (5 bytes)
    Padded to 17 bytes.
    """
    s = bytearray()
    if audio_va is not None:
        rip = tail_va + 7
        s += bytes([0x48, 0x8D, 0x0D]) + struct.pack('<i', audio_va - rip)
        rip = tail_va + len(s) + 5
        s += bytes([0xE8]) + struct.pack('<i', FUN_AUDIO_VA - rip)
    rip = tail_va + len(s) + 5
    s += bytes([0xE9]) + struct.pack('<i', JMP_END_VA - rip)
    s += bytes([0x90] * (17 - len(s)))
    return bytes(s)

def _build_tail(audio_va: int | None = None) -> bytes:
    return _build_audio_tail(TAIL_VA, audio_va)

def _build_poigne_tail(audio_va: int | None = None) -> bytes:
    return _build_audio_tail(POIGNE_TAIL_VA, audio_va)


def _jmp_patch(src_va: int, dst_va: int, total: int) -> bytes:
    rip = src_va + 5
    return bytes([0xE9]) + struct.pack('<i', dst_va - rip) + bytes([0x90] * (total - 5))


def _find_case_handler(exe_data: bytes, type_id: int) -> tuple[int | None, int | None, bytes | None]:
    """
    Find the jump targets table entry for a given type_id and return
    (file_offset, va, vanilla_bytes_12) of the case handler.
    Returns (None, None, None) if not found.
    """
    # Jump targets table at VA 0x14064A368 (in .text raw region, delta 0xC00)
    TARGET_TABLE_VA   = 0x14064A368
    TARGET_TABLE_FOFF = TARGET_TABLE_VA - IMAGE_BASE - 0xC00

    # Switch data table at VA 0x14064A398 maps (type_id-1) -> target index
    SWITCH_DATA_VA   = 0x14064A398
    SWITCH_DATA_FOFF = SWITCH_DATA_VA - IMAGE_BASE - 0xC00

    # type_id range in the switch is 1..0x1e
    if not (1 <= type_id <= 0x1E):
        return None, None, None

    idx = exe_data[SWITCH_DATA_FOFF + (type_id - 1)]
    target_rel = struct.unpack_from('<I', exe_data, TARGET_TABLE_FOFF + idx * 4)[0]
    case_va   = IMAGE_BASE + target_rel
    case_foff = case_va - IMAGE_BASE - 0xC00
    if case_foff < 0 or case_foff + 12 > len(exe_data):
        return None, None, None
    vanilla = bytes(exe_data[case_foff : case_foff + 12])
    return case_foff, case_va, vanilla


# ── RSC record injection ──────────────────────────────────────────────────────

def _append_gad_pickup_record(
    quest_rsc_path: Path,
    x: float, y: float, z: float,
    zone: int,
) -> int:
    data = bytearray(quest_rsc_path.read_bytes())
    remainder = (len(data) - HEADER_SIZE) % RECORD_SIZE
    if remainder:
        data = data[:len(data) - remainder]
    body = data[HEADER_SIZE:]
    n = len(body) // RECORD_SIZE
    record = bytearray(RECORD_SIZE)
    struct.pack_into("<fff", record, XYZ_OFF, x, y, z)
    record[ZONE_OFF]     = zone & 0xFF
    record[INSTANCE_OFF] = 0
    name_bytes = GAD_PICKUP_RSC.encode('ascii')[:NAME_MAXLEN - 1]
    record[NAME_OFF : NAME_OFF + len(name_bytes)] = name_bytes
    slot = None
    for i in range(n):
        if body[i*RECORD_SIZE+NAME_OFF : i*RECORD_SIZE+NAME_OFF+NAME_MAXLEN] == bytes(NAME_MAXLEN):
            slot = i
            break
    if slot is not None:
        off = HEADER_SIZE + slot * RECORD_SIZE
        data[off : off + RECORD_SIZE] = record
    else:
        data += record
        off = len(data) - RECORD_SIZE
    data[9] = min(data[9] + 1, 255)
    quest_rsc_path.write_bytes(bytes(data))
    return off + NAME_OFF


def inject_gad_pickup_records(
    levels_path: Path,
    spawn_sites: list[tuple[str, float, float, float, int]],
    dry_run: bool = False,
) -> None:
    for folder, x, y, z, zone in spawn_sites:
        quest_rsc = levels_path / folder / "quest.rsc"
        if not quest_rsc.exists():
            print(f"  [gad_pickup] WARNING: {folder}/quest.rsc not found -- skipping")
            continue
        data = quest_rsc.read_bytes()
        if GAD_PICKUP_RSC.encode('ascii') in data:
            print(f"  [gad_pickup] {folder}/quest.rsc already has {GAD_PICKUP_RSC} -- skipping")
            continue
        if dry_run:
            print(f"  [gad_pickup] DRY RUN -- would inject {GAD_PICKUP_RSC} "
                  f"into {folder}/quest.rsc zone={zone} ({x},{y},{z})")
            continue
        off = _append_gad_pickup_record(quest_rsc, x, y, z, zone)
        print(f"  [gad_pickup] {folder}/quest.rsc -- injected {GAD_PICKUP_RSC} "
              f"@ offset 0x{off:04X} zone={zone} ({x},{y},{z})")


# ── Main patch ────────────────────────────────────────────────────────────────

def patch_gad_pickup(
    exe_path: str,
    *,
    shuffle_temples: bool = True,
    dry_run: bool = False,
    in_place: bool = False,
    model_rsc: str | None = None,
) -> str:
    path = Path(exe_path)
    if not path.exists():
        raise FileNotFoundError(f"EXE not found: {exe_path}")

    data = bytearray(path.read_bytes())

    # Resolve model type_id from GAD_MODEL_RSC constant
    resolved = lookup_type_id(bytes(data), model_rsc or GAD_MODEL_RSC)
    if resolved is None:
        raise RuntimeError(
            f"RSC name '{model_rsc or GAD_MODEL_RSC}' not found in dispatch table. "
            f"Run --list-models to see available entries."
        )
    type_id = resolved
    model_name = model_rsc or GAD_MODEL_RSC
    if type_id not in SAFE_TYPE_IDS:
        raise RuntimeError(
            f"type_id 0x{type_id:X} for '{model_name}' is not in SAFE_TYPE_IDS.\n"
            f"Its pickup handler case may set game flags or alter state.\n"
            f"Safe type_ids: {sorted(f'0x{x:02X}' for x in SAFE_TYPE_IDS)}\n"
            f"Run --list-models --safe to see safe RSC candidates."
        )
    print(f"  [gad_pickup] Model: {model_name} -> type_id 0x{type_id:X} (safe)")

    # Verify vanilla
    print("  [gad_pickup] Verifying vanilla EXE bytes...")
    for site_name, (off, expected) in VERIFY_SITES.items():
        actual = bytes(data[off: off + len(expected)])
        if actual != expected:
            raise RuntimeError(
                f"Vanilla verify failed at '{site_name}' (file offset {off:#x}).\n"
                f"  Expected: {expected.hex(' ')}\n"
                f"  Got:      {actual.hex(' ')}\n"
                f"  EXE may be wrong version or already patched."
            )
    print("  [gad_pickup] All vanilla bytes verified ✓")

    if dry_run:
        print("  [gad_pickup] DRY RUN -- no changes written")
        return exe_path

    # 1. String
    string_bytes = GAD_PICKUP_RSC.encode('ascii') + b'\x00\x00'
    data[STRING_FILE_OFF : STRING_FILE_OFF + len(string_bytes)] = string_bytes
    print(f"  [gad_pickup] String -> .rdata 0x{STRING_FILE_OFF:X}")

    # 2. Table entry
    struct.pack_into('<Q', data, ENTRY_STR_PTR_OFF, STRING_VA)
    struct.pack_into('<Q', data, ENTRY_PARAM1_OFF,  type_id)
    print(f"  [gad_pickup] Entry 32 -> RSC_X_GAD_PICKUP / type_id 0x{type_id:X}")

    # 3. Cave stubs (stub1 -> gad tail, stub2 -> poigne tail)
    cave = _build_stub(CAVE_VA, 1, TAIL_VA) + _build_stub(CAVE_VA + 22, 4, POIGNE_TAIL_VA)
    data[CAVE_FILE_OFF : CAVE_FILE_OFF + len(cave)] = cave
    print(f"  [gad_pickup] Stubs (+1, +4) -> cave 0x{CAVE_FILE_OFF:X}")

    # 4a. Gad pickup audio tail
    gad_audio_va = None
    if GAD_AUDIO_PATH:
        gad_audio_va = _find_audio_va(bytes(data), GAD_AUDIO_PATH)
        if gad_audio_va is None:
            audio_bytes = GAD_AUDIO_PATH.encode('ascii') + b'\x00'
            if len(audio_bytes) <= 40:
                data[GAD_AUDIO_STR_FILE_OFF : GAD_AUDIO_STR_FILE_OFF + len(audio_bytes)] = audio_bytes
                gad_audio_va = GAD_AUDIO_STR_VA
                print(f"  [gad_pickup] Gad audio string written to .rdata")
            else:
                print(f"  [gad_pickup] WARNING: gad audio path too long (max 39 chars), skipping")
        if gad_audio_va:
            print(f"  [gad_pickup] Gad audio: {GAD_AUDIO_PATH}")
    tail = _build_tail(gad_audio_va)
    data[TAIL_FILE_OFF : TAIL_FILE_OFF + len(tail)] = tail

    # 4b. Poigne audio tail
    poigne_audio_va = None
    if POIGNE_AUDIO_PATH:
        poigne_audio_va = _find_audio_va(bytes(data), POIGNE_AUDIO_PATH)
        if poigne_audio_va is None:
            # Write string into .rdata next to our RSC string
            audio_bytes = POIGNE_AUDIO_PATH.encode('ascii') + b'\x00'
            if len(audio_bytes) <= 40:
                data[POIGNE_AUDIO_STR_FILE_OFF : POIGNE_AUDIO_STR_FILE_OFF + len(audio_bytes)] = audio_bytes
                poigne_audio_va = POIGNE_AUDIO_STR_VA
                print(f"  [gad_pickup] Poigne audio string written to .rdata")
            else:
                print(f"  [gad_pickup] WARNING: poigne audio path too long (max 39 chars), skipping")
        if poigne_audio_va:
            print(f"  [gad_pickup] Poigne audio: {POIGNE_AUDIO_PATH}")
    poigne_tail = _build_poigne_tail(poigne_audio_va)
    data[POIGNE_TAIL_FILE_OFF : POIGNE_TAIL_FILE_OFF + len(poigne_tail)] = poigne_tail
    print(f"  [gad_pickup] Audio tails -> 0x{TAIL_FILE_OFF:X}, 0x{POIGNE_TAIL_FILE_OFF:X}")

    # 5. case 0x16 -> stub1
    data[CASE16_FILE_OFF : CASE16_FILE_OFF + 12] = _jmp_patch(CASE16_VA, CAVE_VA, 12)
    print(f"  [gad_pickup] Case 0x16 -> stub1")

    # 6. case 0x13 -> stub2
    data[CASE13_FILE_OFF : CASE13_FILE_OFF + 12] = _jmp_patch(CASE13_VA, CAVE_VA + 22, 12)
    print(f"  [gad_pickup] Case 0x13 -> stub2")

    # 7. NOP temple writes
    if shuffle_temples:
        for name, off, vanilla, count in TEMPLE_NOP_SITES:
            actual = bytes(data[off : off + count])
            if actual == vanilla:
                data[off : off + count] = bytes([0x90] * count)
                print(f"  [gad_pickup] NOPed {name} @ 0x{off:X}")
            elif actual == bytes([0x90] * count):
                print(f"  [gad_pickup] {name} already NOPed")
            else:
                print(f"  [gad_pickup] WARNING: {name} unexpected bytes {actual.hex(' ')}")
    else:
        print("  [gad_pickup] Temple NOPs skipped")

    out_path = exe_path if in_place else str(
        path.with_stem(path.stem + "_patched")
    )
    Path(out_path).write_bytes(data)
    print(f"  [gad_pickup] Written: {out_path}")
    return out_path

def apply_prison_keycard_patch(exe_path: str, *, dry_run: bool = False) -> bool:
    if not Path(exe_path).exists():
        print(f"  [prison_key] EXE not found: {exe_path} -- skipping")
        return False
    data = bytearray(Path(exe_path).read_bytes())
    for p in PRISON_KEY_PATCHES:
        off  = p["offset"]
        size = p["size"]
        actual = bytes(data[off:off + size])
        if actual == p["patch"]:
            print(f"  [prison_key] Already patched: {p['label']}")
            continue
        if actual != p["expected"]:
            print(f"  [prison_key] Unexpected bytes at {p['label']}: {actual.hex()}")
            return False
        if not dry_run:
            data[off:off + size] = p["patch"]
        print(f"  ✓ {p['label']}")
    if not dry_run:
        Path(exe_path).write_bytes(data)
    return True

def verify_gad_pickup(exe_path: str) -> bool:
    data = Path(exe_path).read_bytes()
    actual = data[STRING_FILE_OFF : STRING_FILE_OFF + len(GAD_PICKUP_RSC)]
    if actual == GAD_PICKUP_RSC.encode('ascii'):
        return True
    if actual == bytes(len(GAD_PICKUP_RSC)):
        return False
    raise RuntimeError(
        f"Unexpected bytes at string site -- wrong version or partially patched. "
        f"Got: {actual.hex(' ')}"
    )


def restore_gad_pickup(exe_path: str) -> bool:
    backup = Path(exe_path).with_suffix(".exe.bak")
    if backup.exists():
        shutil.copy2(str(backup), exe_path)
        print(f"  [gad_pickup] Restored from {backup}")
        return True
    print(f"  [gad_pickup] No backup found at {backup}")
    return False


def apply_gad_pickup_patch(
    exe_path: str,
    shuffle_temples: bool,
    *,
    dry_run: bool = False,
    levels_path: Path | None = None,
    spawn_sites: list | None = None,
    model_rsc: str | None = None,
) -> bool:
    if not Path(exe_path).exists():
        print(f"  [gad_pickup] EXE not found: {exe_path} -- skipping")
        return False
    try:
        already_done = verify_gad_pickup(exe_path)
    except RuntimeError as e:
        print(f"  [gad_pickup] WARNING: {e}")
        return False
    if already_done:
        print("  [gad_pickup] Patch already applied -- skipping")
        if shuffle_temples and not dry_run and levels_path and spawn_sites:
            inject_gad_pickup_records(levels_path, spawn_sites, dry_run=dry_run)
        return True
    try:
        patch_gad_pickup(
            exe_path,
            shuffle_temples=shuffle_temples,
            dry_run=dry_run,
            in_place=True,
            model_rsc=model_rsc,
        )
        if levels_path and spawn_sites:
            inject_gad_pickup_records(levels_path, spawn_sites, dry_run=dry_run)
        return True
    except (RuntimeError, FileNotFoundError) as e:
        print(f"  [gad_pickup] ERROR: {e}")
        return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Inject RSC_X_GAD_PICKUP into Shadow Man Remastered EXE"
    )
    parser.add_argument("exe",
        help="Path to thoth_x64.exe (vanilla)")
    parser.add_argument("--model",
        default=None, metavar="RSC_NAME",
        help="RSC name to use as visual model (default: RSC_X_PROPHECY). "
             "Must exist in the dispatch table. Example: --model RSC_X_BOOK_OF_SHADOWS")
    parser.add_argument("--list-models",
        action="store_true",
        help="List all RSC names in the dispatch table and exit")
    parser.add_argument("--safe",
        action="store_true",
        help="With --list-models: show only safe type_id candidates")
    parser.add_argument("--dry-run",       action="store_true")
    parser.add_argument("--no-temple-nop", action="store_true")
    parser.add_argument("--verify",        action="store_true")
    parser.add_argument("--restore",       action="store_true")
    args = parser.parse_args()

    if args.restore:
        sys.exit(0 if restore_gad_pickup(args.exe) else 1)

    if args.verify:
        try:
            applied = verify_gad_pickup(args.exe)
            print(f"Gad pickup patch: {'APPLIED' if applied else 'VANILLA'}")
            sys.exit(0)
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(2)

    if args.list_models:
        data = Path(args.exe).read_bytes()
        table_foff = _va_to_file(TABLE_VA)
        safe_only = args.safe
        print(f"Dispatch table RSC entries ({'safe only' if safe_only else 'all'}):")
        print(f"  {'Idx':>4}  {'type_id':>8}  {'Safe':>5}  Name")
        print(f"  {'─'*4}  {'─'*8}  {'─'*5}  {'─'*40}")
        for i in range(TABLE_COUNT):
            off = table_foff + i * ENTRY_SIZE
            str_ptr = struct.unpack_from('<Q', data, off)[0]
            param1  = struct.unpack_from('<Q', data, off + 8)[0]
            if str_ptr == 0:
                continue
            sfoff = _va_to_file(str_ptr)
            if sfoff is None or sfoff >= len(data):
                continue
            name = _read_cstr(data, sfoff)
            if not name.startswith('RSC_'):
                continue
            is_safe = param1 in SAFE_TYPE_IDS
            if safe_only and not is_safe:
                continue
            safe_str = '✓' if is_safe else ' '
            print(f"  [{i:>3}]  0x{param1:04X}    {safe_str:>5}  {name}")
        sys.exit(0)

    try:
        patch_gad_pickup(
            args.exe,
            shuffle_temples=not args.no_temple_nop,
            dry_run=args.dry_run,
            model_rsc=args.model,
        )
        print("Done.")
    except (RuntimeError, FileNotFoundError) as e:
        print(f"ERROR: {e}")
        sys.exit(1)