"""
spawn_buffet.py — Shadow Man Remastered item spawn test tool.

Extracts asylum/quest.rsc from the game KPFs, injects a configurable list
of RSC records at fixed world coordinates, and packages the result as a
lightweight mod KPF.  Useful for testing item appearance, track_type
behaviour, and save_idx persistence without touching the real game files.

Usage:
    python tools/spawn_buffet.py
    python tools/spawn_buffet.py --game-dir "D:/Steam/steamapps/common/Shadow Man Remastered"
    python tools/spawn_buffet.py --out-dir "C:/some/other/mods/dir"

Delete shadowman_buffet.kpf from the mods/ directory to restore vanilla.
"""

import argparse
import struct
import sys
import zipfile
from pathlib import Path

# ── RSC constants ──────────────────────────────────────────────────────────────
HEADER_SIZE  = 8
RECORD_SIZE  = 72
XYZ_OFF      = 0x04
ZONE_OFF     = 0x11
INSTANCE_OFF = 0x21
NAME_OFF     = 0x22
NAME_MAXLEN  = 30
TRACK_OFF    = 0x1C   # 2-byte big-endian track_type
SAVE_IDX_OFF = 0x1E   # 4-byte big-endian save_idx
COUNT_BYTE   = 9      # data[9] = live-record count (engine only loads up to this)

DEFAULT_GAME_DIR = r"C:\Program Files (x86)\Steam2\steamapps\common\Shadow Man Remastered"
MOD_NAME         = "shadowman_buffet.kpf"
DEFAULT_ZONE     = 2

# ── Track_type test list ───────────────────────────────────────────────────────
# One RSC_X_BARREL_D per track_type value, each with a unique save_idx.
# Includes barrel variants, cadeaux, unknowns, and true-form entity type codes
# so we can see what weird behaviour (if any) the engine exhibits for each.
#
# save_idx is in the 0x0F00 range — safe from real game data.

BARREL_NAME = "RSC_X_BARREL_D"

TRACK_TYPES = [
    (0x0000, "no-persist"),
    (0x0001, "unknown-01"),
    (0x0002, "cadeaux"),
    (0x0003, "unknown-03"),
    (0x0004, "unknown-04"),
    (0x0005, "unknown-05"),
    (0x000B, "true-form-0B"),
    (0x000C, "true-form-0C"),
    (0x000D, "true-form-0D"),
    (0x000E, "true-form-0E"),
    (0x000F, "true-form-0F"),
    (0x0010, "true-form-10"),
    (0x0011, "true-form-11"),
    (0x0012, "true-form-12"),
    (0x0013, "true-form-13"),
    (0x0014, "true-form-14"),
    (0x0015, "true-form-15"),
    (0x0020, "barrel-20"),
    (0x0021, "barrel-21"),
    (0x0022, "barrel-22"),
    (0x0023, "barrel-23"),
    (0x0024, "barrel-24"),
]

X_START   = 2359
X_STEP    = 271
Z_ROW1    = 4283
Z_ROW2    = 4531
Y_DEFAULT = 10
SAVE_BASE = 0x0F00

# 22 items across two rows of 12 + 10, using the original coordinate grid
ITEMS = [
    (
        BARREL_NAME,
        X_START + (i % 12) * X_STEP,
        Y_DEFAULT,
        Z_ROW1 if i < 12 else Z_ROW2,
        track,
        SAVE_BASE + i,
    )
    for i, (track, _label) in enumerate(TRACK_TYPES)
]


# ── RSC helpers ────────────────────────────────────────────────────────────────

def _build_record(name: str, instance_id: int, x: float, y: float, z: float,
                  zone: int, track_type: int = 0, save_idx: int = 0) -> bytes:
    record = bytearray(RECORD_SIZE)
    struct.pack_into("<fff",  record, XYZ_OFF,      x, y, z)
    struct.pack_into(">H",   record, TRACK_OFF,    track_type)
    struct.pack_into(">I",   record, SAVE_IDX_OFF, save_idx)
    record[ZONE_OFF]     = zone & 0xFF
    record[INSTANCE_OFF] = instance_id & 0xFF
    name_bytes = name.encode("ascii")[: NAME_MAXLEN - 1]
    record[NAME_OFF : NAME_OFF + len(name_bytes)] = name_bytes
    return bytes(record)


def _write_slot(data: bytearray, record: bytes) -> tuple[bytearray, int]:
    """
    Find the first fully-zeroed slot and write the record there.
    Updates data[COUNT_BYTE] to cover the written slot (with gap zeroing).
    Appends a new slot if no empty slot exists.
    Returns (updated_data, slot_index).
    """
    assert len(record) == RECORD_SIZE

    n = (len(data) - HEADER_SIZE) // RECORD_SIZE
    slot = None
    for i in range(n):
        off = HEADER_SIZE + i * RECORD_SIZE
        if bytes(data[off : off + RECORD_SIZE]) == bytes(RECORD_SIZE):
            slot = i
            break

    if slot is None:
        # Append a new slot
        data += bytearray(RECORD_SIZE)
        slot = n

    off = HEADER_SIZE + slot * RECORD_SIZE
    data[off : off + RECORD_SIZE] = record

    # Zero any gap between old live count and the new slot
    old_count = data[COUNT_BYTE]
    for i in range(old_count, slot):
        gap_off = HEADER_SIZE + i * RECORD_SIZE
        data[gap_off : gap_off + RECORD_SIZE] = bytes(RECORD_SIZE)

    data[COUNT_BYTE] = min(slot + 1, 255)
    return data, slot


# ── KPF helpers ───────────────────────────────────────────────────────────────

def _find_kpfs(game_dir: Path) -> list[Path]:
    return sorted(game_dir.glob("*.kpf"), key=lambda p: p.name)


def _extract_asylum_quest(kpf_files: list[Path]) -> bytearray:
    for kpf in kpf_files:
        with zipfile.ZipFile(kpf) as z:
            for name in z.namelist():
                if "asylum" in name.lower() and "quest.rsc" in name.lower():
                    return bytearray(z.read(name))
    raise FileNotFoundError("asylum/quest.rsc not found in any KPF")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--game-dir", default=DEFAULT_GAME_DIR,
                        help="Path to Shadow Man Remastered install directory")
    parser.add_argument("--out-dir", default=None,
                        help="Where to write the mod KPF (default: <game-dir>/mods/)")
    parser.add_argument("--zone", type=int, default=DEFAULT_ZONE,
                        help=f"Zone byte for injected records (default: {DEFAULT_ZONE})")
    args = parser.parse_args()

    game_path = Path(args.game_dir)
    mods_dir  = Path(args.out_dir) if args.out_dir else game_path / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)

    kpf_files = _find_kpfs(game_path)
    if not kpf_files:
        print("ERROR: No KPF files found in", game_path)
        sys.exit(1)

    print("Extracting vanilla asylum/quest.rsc...")
    data = _extract_asylum_quest(kpf_files)
    print(f"  {len(data)} bytes, {(len(data) - HEADER_SIZE) // RECORD_SIZE} slots, "
          f"live count (data[9]) = {data[COUNT_BYTE]}")

    # Build a label map for pretty output
    track_label = {t: lbl for t, lbl in TRACK_TYPES}

    print(f"\nSpawning {len(ITEMS)} {BARREL_NAME} records — one per track_type (zone={args.zone}):")
    print(f"  {'#':>3}  {'slot':>4}  {'name':<24} {'x':>5} {'z':>5}  "
          f"{'track_type':<12}  {'save_idx'}")
    print("  " + "-" * 75)
    for idx, (item_name, x, y, z, track_type, save_idx) in enumerate(ITEMS):
        inst   = (idx + 1) & 0xFF
        record = _build_record(item_name, inst, x, y, z, args.zone,
                               track_type=track_type, save_idx=save_idx)
        data, slot = _write_slot(data, record)
        lbl = track_label.get(track_type, "")
        print(f"  [{idx:>3}] slot={slot:>3}  {item_name:<24} {x:>5} {z:>5}  "
              f"0x{track_type:04X} {lbl:<10}  0x{save_idx:04X}")

    print(f"\nFinal live count (data[9]) = {data[COUNT_BYTE]}")

    out_kpf = mods_dir / MOD_NAME
    with zipfile.ZipFile(out_kpf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("levels/asylum/quest.rsc", bytes(data))

    print(f"\nCreated: {out_kpf}")
    print("Remove shadowman_buffet.kpf from mods/ to restore vanilla.")


if __name__ == "__main__":
    main()
