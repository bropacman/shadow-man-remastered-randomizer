"""
setup_gad_records.py
====================
One-time setup tool that injects RSC_X_GAD_PICKUP records into the vanilla
KPF archives so they exist at fixed, known offsets in quest.rsc files.

WHY THIS EXISTS
---------------
The randomizer treats RSC_X_GAD_PICKUP temple slots as physical locations
(items can be placed there). For those loc_keys to be stable and hardcodeable
in extracted_locations.py, the records must exist at fixed offsets in the
game files — not appended dynamically at randomizer runtime.

SAFETY
------
RSC_X_GAD_PICKUP is not in the vanilla dispatch table so the engine drops
it silently ("Unknown rsc obj class"). Vanilla playthroughs are unaffected.
The EXE patch is required for the items to actually appear in-game.

IDEMPOTENT
----------
Running this script multiple times is safe — it checks if records already
exist at the expected zone/coords before injecting. The idempotency check
searches the entire file, not just the live record window, to guard against
duplicate records from earlier development runs.

IMPORTANT: RUN ON CLEAN VANILLA FILES
--------------------------------------
This script must be run against unmodified vanilla KPF archives. If you
suspect your install has accumulated duplicate injections from earlier test
runs (symptom: seeing double gad pickups in-game with 6 total instead of 3),
verify your game files via Steam before running this script:

  Steam → Library → Shadow Man Remastered → Properties
        → Installed Files → Verify integrity of game files

Then re-run setup_gad_records.py once on the restored vanilla files.

HOW INJECTION WORKS
-------------------
Each quest.rsc has a record count stored at file offset 9 (i.e. record[0]
byte[1]).  The engine reads exactly that many records and ignores anything
beyond — so simply appending a record at EOF is invisible to the engine.

Instead we use the same strategy as the spawner:
  1. Find the first empty slot (all-zero name) within the live record window,
     OR use the slot at index `count` if no empty slot exists (the files are
     pre-padded with zero records beyond the live window).
  2. Write the record into that slot in-place.
  3. Increment the count byte at file offset 9.

The resulting name_offset is stable across runs (same slot is chosen
every time) and can be hardcoded in extracted_locations.py.

USAGE
-----
    python setup_gad_records.py --game-dir "C:/path/to/Shadow Man Remastered"
    python setup_gad_records.py --game-dir "C:/path/to/Shadow Man Remastered" --dry-run
    python setup_gad_records.py --game-dir "C:/path/to/Shadow Man Remastered" --verify

OUTPUT
------
Prints the file offsets of injected records so you can update
extracted_locations.py with the correct hardcoded values.

TROUBLESHOOTING
---------------
Double gad pickups in-game (6 total instead of 3):
  The KPF contains duplicate RSC_X_GAD_PICKUP records from multiple setup
  runs. Verify game files via Steam, then re-run this script on the restored
  vanilla files.

Wrong offsets after re-running:
  If extracted_locations.py has hardcoded offsets that no longer match after
  a Steam verify + re-setup, update the three RawLocation entries for
  RSC_X_GAD_PICKUP with the new offsets printed by this script.
"""

import struct
import shutil
import argparse
from pathlib import Path

# ── RSC format constants ──────────────────────────────────────────────────────
HEADER_SIZE  = 8
RECORD_SIZE  = 72
NAME_OFF     = 0x22
NAME_MAXLEN  = 30
ZONE_OFF     = 0x11
INSTANCE_OFF = 0x21
XYZ_OFF      = 0x04

# File offset of the live-record count byte.
# This is record[0][1] (the second byte of the first record body).
COUNT_BYTE = 9

GAD_PICKUP_RSC = "RSC_X_GAD_PICKUP"

# ── Injection sites ───────────────────────────────────────────────────────────
# Each entry: (folder, filename, x, y, z, zone)
# Coordinates and zones from in-game survey of temple puzzle reward locations.
#
# AUTHORITATIVE SOURCE: these coordinates are what gets baked into the RSC
# records at setup time. The x/y/z values in extracted_locations.py must match
# these — if you change a coordinate here, update extracted_locations.py to
# match and re-run setup_gad_records.py on clean vanilla files.
GAD_INJECTION_SITES = [
    ("t1tchgad", "quest.rsc", -559.4,  340.0,  35710.8, 16),  # Temple of Fire (Touch)
    ("t2wlkgad", "quest.rsc",  256.0,  395.0,   1280.0,  9),  # Temple of Prophecy (Walk)
    ("t3swmgad", "quest.rsc", -1535.8, 670.0,  -4988.9,  7),  # Temple of Blood (Swim)
]


def _build_record(x: float, y: float, z: float, zone: int) -> bytes:
    record = bytearray(RECORD_SIZE)
    struct.pack_into("<fff", record, XYZ_OFF, x, y, z)
    record[ZONE_OFF]     = zone & 0xFF
    record[INSTANCE_OFF] = 0
    name_bytes = GAD_PICKUP_RSC.encode('ascii')[:NAME_MAXLEN - 1]
    record[NAME_OFF : NAME_OFF + len(name_bytes)] = name_bytes
    return bytes(record)


def _find_existing(data: bytes, zone: int, x: float, y: float, z: float) -> int | None:
    """
    Return the NAME_OFF-relative file offset of an RSC_X_GAD_PICKUP record
    that matches the expected zone and approximate coordinates, or None.

    We match on zone + coords rather than name alone to avoid false-positives
    against the vanilla RSC_X_GAD_PICKUP records that already exist in the
    file for the altar/temple gad mechanic.
    """
    count = data[COUNT_BYTE]
    needle = GAD_PICKUP_RSC.encode('ascii')
    body = data[HEADER_SIZE:]
    for i in range(count):
        off = i * RECORD_SIZE
        rec_name = body[off + NAME_OFF : off + NAME_OFF + len(needle)]
        if rec_name != needle:
            continue
        rec_zone = body[off + ZONE_OFF]
        if rec_zone != (zone & 0xFF):
            continue
        rx, ry, rz = struct.unpack_from("<fff", body, off + XYZ_OFF)
        if abs(rx - x) < 1.0 and abs(ry - y) < 1.0 and abs(rz - z) < 1.0:
            return HEADER_SIZE + off + NAME_OFF
    return None


def inject_record(
    data: bytearray,
    x: float, y: float, z: float,
    zone: int,
) -> tuple[int, bool]:
    from rsc_utils import inject_rsc_record, build_rsc_record, HEADER_SIZE, RECORD_SIZE, NAME_OFF

    existing = _find_existing(bytes(data), zone, x, y, z)
    if existing is not None:
        return existing, True

    record = build_rsc_record(GAD_PICKUP_RSC, x, y, z, zone)
    slot = inject_rsc_record(data, record, allow_expand=True)
    name_offset = HEADER_SIZE + slot * RECORD_SIZE + NAME_OFF
    return name_offset, False


def hex_offset(off: int) -> str:
    return f"0x{off:04X}"


def run_setup(game_dir: str, dry_run: bool = False, verify: bool = False) -> None:
    game_path = Path(game_dir)

    # Try KPF workflow first
    try:
        from kpf_handler import (find_kpf_files, build_kpf_index,
                                  extract_game_files, build_and_install_mod,
                                  find_file_in_kpf)
        kpf_files = find_kpf_files(game_dir)
        using_kpf = bool(kpf_files)
    except ImportError:
        kpf_files  = []
        using_kpf  = False

    if using_kpf:
        work_path = game_path / "_gad_setup_work"
        work_path.mkdir(exist_ok=True)
        print(f"Mode: KPF  |  Found {len(kpf_files)} archive(s)")
        print(f"Work dir: {work_path}")
        kpf_index  = build_kpf_index(kpf_files)
        folders    = {folder for folder, _, _, _, _, _ in GAD_INJECTION_SITES}
        extract_game_files(kpf_files, str(work_path), list(folders))
        levels_path = work_path / "levels"
    else:
        levels_path = game_path / "levels"
        work_path   = game_path
        print(f"Mode: Direct file edit")

    print()

    if verify:
        print("── Verifying RSC_X_GAD_PICKUP records ──────────────────────")
        all_ok = True

        from kpf_handler import find_mods_dir
        import zipfile as zf_mod
        mods_dir = find_mods_dir(game_dir)
        mod_kpf = mods_dir / "shadowman_randomizer.kpf"
        mod_files = {}
        if mod_kpf.exists():
            with zf_mod.ZipFile(mod_kpf) as z:
                for name in z.namelist():
                    mod_files[name.replace('\\', '/')] = z.read(name)

        for folder, filename, x, y, z, zone in GAD_INJECTION_SITES:
            rsc_path = levels_path / folder / filename

            mod_key = f"levels/{folder}/{filename}"
            if mod_key in mod_files:
                data = mod_files[mod_key]
            elif rsc_path.exists():
                data = rsc_path.read_bytes()
            else:
                print(f"  ❌ {folder}/{filename} NOT FOUND")
                all_ok = False
                continue

            existing = _find_existing(data, zone, x, y, z)
            if existing is not None:
                rx, ry, rz = struct.unpack_from("<fff", data, existing - NAME_OFF + XYZ_OFF)
                count = data[COUNT_BYTE]
                slot  = (existing - HEADER_SIZE - NAME_OFF) // RECORD_SIZE
                print(f"  ✅ {folder}/{filename} @ {hex_offset(existing)}"
                      f"  slot={slot} (count={count})"
                      f"  ({rx:.1f}, {ry:.1f}, {rz:.1f})")
            else:
                print(f"  ❌ {folder}/{filename} — record NOT found (or outside live window)")
                all_ok = False
        print()
        print("✅ All records present." if all_ok else "❌ Some records missing — run setup.")
        return

    print("── Injecting RSC_X_GAD_PICKUP records ──────────────────────")
    print(f"{'Dry run' if dry_run else 'LIVE'}")
    print()

    mod_files_out = {}
    results       = []

    for folder, filename, x, y, z, zone in GAD_INJECTION_SITES:
        rsc_path = levels_path / folder / filename
        if not rsc_path.exists():
            print(f"  ⚠️  {folder}/{filename} not found — skipping")
            results.append((folder, filename, None, False))
            continue

        data = bytearray(rsc_path.read_bytes())
        count_before = data[COUNT_BYTE]

        name_off, already = inject_record(data, x, y, z, zone)

        status = "already present" if already else ("would inject" if dry_run else "injected")
        slot   = (name_off - HEADER_SIZE - NAME_OFF) // RECORD_SIZE
        print(f"  {folder}/{filename}")
        print(f"    slot:   {slot}  (count {count_before} → {data[COUNT_BYTE]})")
        print(f"    offset: {hex_offset(name_off)}  ({status})")
        print(f"    coords: ({x}, {y}, {z})  zone={zone}")
        print()

        results.append((folder, filename, name_off, already))

        if not dry_run:
            rsc_path.write_bytes(bytes(data))
            if using_kpf:
                matches  = find_file_in_kpf(kpf_index, f"*/{folder}/{filename}")
                internal = matches[0][0] if matches else f"levels/{folder}/{filename}"
                mod_files_out[internal] = str(rsc_path)

    if using_kpf and mod_files_out and not dry_run:
        print("── Repacking KPF ────────────────────────────────────────────")
        build_and_install_mod(game_dir, mod_files_out)
        print()
        shutil.rmtree(str(work_path), ignore_errors=True)

    print("── Add/update these rows in data/locations.csv ────────────")
    print()

    LEVEL_NAMES = {
        "t1tchgad": "Temple of Fire (Toucher)",
        "t2wlkgad": "Temple of Prophecy (Marcher)",
        "t3swmgad": "Temple of Blood (Nager)",
    }
    GATE_RAWS = {
        "t1tchgad": "GATE_FIRE_POIGNE",
        "t2wlkgad": "GATE_PROPHECY_INTERIOR",
        "t3swmgad": "GATE_BLOOD_INTERIOR",
    }

    print("level_id,source_file,offset,friendly_name,object,category,"
          "level_region,sub_region,instance_id,is_tracked,is_verified,"
          "zone,x,y,z,notes")

    for folder, filename, name_off, already in results:
        if name_off is None:
            continue
        site = next((s for s in GAD_INJECTION_SITES if s[0] == folder), None)
        if site is None:
            continue
        _, _, x, y, z, zone = site
        level_name = LEVEL_NAMES.get(folder, folder)
        gate_raw   = GATE_RAWS.get(folder, "")
        print(f"{folder},{filename},0x{name_off:04X},"
              f"Gad Power Upgrade,{GAD_PICKUP_RSC},gad,"
              f"{level_name},{gate_raw},"
              f"0,False,False,{zone},{x},{y},{z},")

    print()
    print("Then run:  python tools/generate.py")
    if dry_run:
        print()
        print("(dry run — no files were modified)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Inject RSC_X_GAD_PICKUP records into Shadow Man Remastered KPF files"
    )
    parser.add_argument("--game-dir", required=True,
        help="Path to Shadow Man Remastered install directory")
    parser.add_argument("--dry-run", action="store_true",
        help="Show what would be done without modifying files")
    parser.add_argument("--verify", action="store_true",
        help="Check if records are already present")
    args = parser.parse_args()

    run_setup(args.game_dir, dry_run=args.dry_run, verify=args.verify)