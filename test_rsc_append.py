"""
test_rsc_append.py
==================
Tests different strategies for appending a record to a quest.rsc file.
Run this against the vanilla t1tchgad quest.rsc to find what the engine accepts.

Usage:
    python test_rsc_append.py <path_to_vanilla_t1tchgad_quest.rsc> <output_dir>

Produces one output file per strategy. Load each in-game to see which works.
"""

import struct
import shutil
import sys
from pathlib import Path

import argparse

HEADER_SIZE  = 8
RECORD_SIZE  = 72
NAME_OFF     = 0x22
NAME_MAXLEN  = 30
ZONE_OFF     = 0x11
INSTANCE_OFF = 0x21
XYZ_OFF      = 0x04
COUNT_BYTE   = 9

EXEX_SIGNATURE = b'EXEX'

# GAD_PICKUP record to inject — known coords for t1tchgad
GAD_NAME = "RSC_X_ASSON"
GAD_X, GAD_Y, GAD_Z, GAD_ZONE = -559.4, 340.0, 35710.8, 16

def pack_to_mod(quest_rsc_path: Path, game_dir: str, mod_name: str = "test_append.kpf"):
    sys.path.insert(0, str(Path(game_dir).parent))  # find kpf_handler
    from kpf_handler import find_mods_dir, create_mod_kpf
    mods_dir = find_mods_dir(game_dir)
    create_mod_kpf(mods_dir, {"levels/t1tchgad/quest.rsc": str(quest_rsc_path)}, mod_name=mod_name)
    print(f"  Packed -> {mods_dir}/{mod_name}")

def build_record(name, x, y, z, zone):
    r = bytearray(RECORD_SIZE)
    struct.pack_into("<fff", r, XYZ_OFF, x, y, z)
    r[ZONE_OFF] = zone & 0xFF
    nb = name.encode("ascii")[:NAME_MAXLEN - 1]
    r[NAME_OFF:NAME_OFF + len(nb)] = nb
    return bytes(r)


def find_exex(data):
    n = (len(data) - HEADER_SIZE) // RECORD_SIZE
    for i in range(n):
        off = HEADER_SIZE + i * RECORD_SIZE
        if data[off + 4:off + 8] == EXEX_SIGNATURE:
            return i
    return None


def dump_boundary(data, label):
    n = (len(data) - HEADER_SIZE) // RECORD_SIZE
    trailer = data[HEADER_SIZE + n * RECORD_SIZE:]
    exex = find_exex(data)
    print(f"\n  [{label}]")
    print(f"  size={len(data)}  n_full={n}  data[9]={data[9]}  exex_slot={exex}  trailer={len(trailer)}b")
    for i in range(max(0, data[9] - 2), min(n, data[9] + 5)):
        off = HEADER_SIZE + i * RECORD_SIZE
        chunk = data[off:off + RECORD_SIZE]
        name = chunk[NAME_OFF:NAME_OFF + 30].split(b'\x00')[0].decode('ascii', errors='replace') or "(empty)"
        is_exex = chunk[4:8] == EXEX_SIGNATURE
        live = i < data[9]
        print(f"    rec[{i:>3}] {'LIVE ' if live else 'headr'}  {'<<EXEX>> ' if is_exex else '         '}  {name}")


def strategy_overwrite_exex(raw):
    """Old behavior: overwrite EXEX slot with new record, bump data[9]."""
    data = bytearray(raw)
    exex = find_exex(data)
    if exex is None:
        return None, "no EXEX found"
    record = build_record(GAD_NAME, GAD_X, GAD_Y, GAD_Z, GAD_ZONE)
    off = HEADER_SIZE + exex * RECORD_SIZE
    data[off:off + RECORD_SIZE] = record
    data[COUNT_BYTE] = min(exex + 1, 255)
    return bytes(data), f"overwrote EXEX at slot {exex}, data[9]={data[COUNT_BYTE]}"


def strategy_push_exex_forward(raw):
    """New rsc_utils behavior: push EXEX forward, write before it."""
    data = bytearray(raw)
    n = (len(data) - HEADER_SIZE) // RECORD_SIZE
    trailer = bytes(data[HEADER_SIZE + n * RECORD_SIZE:])
    data = bytearray(data[:HEADER_SIZE + n * RECORD_SIZE])

    exex = find_exex(data)
    if exex is None:
        return None, "no EXEX found"
    if exex >= n - 1:
        return None, "EXEX at last slot, no room to push"

    record = build_record(GAD_NAME, GAD_X, GAD_Y, GAD_Z, GAD_ZONE)
    exex_off = HEADER_SIZE + exex * RECORD_SIZE
    exex_rec = bytes(data[exex_off:exex_off + RECORD_SIZE])
    data[exex_off:exex_off + RECORD_SIZE] = record
    data[exex_off + RECORD_SIZE:exex_off + 2 * RECORD_SIZE] = exex_rec
    data[COUNT_BYTE] = min(exex + 1, 255)
    return bytes(data) + trailer, f"pushed EXEX from slot {exex} to {exex+1}, data[9]={data[COUNT_BYTE]}"


def strategy_headroom_no_count_bump(raw):
    """Write into first zeroed headroom slot, do NOT bump data[9]."""
    data = bytearray(raw)
    n = (len(data) - HEADER_SIZE) // RECORD_SIZE
    count = data[COUNT_BYTE]
    record = build_record(GAD_NAME, GAD_X, GAD_Y, GAD_Z, GAD_ZONE)

    slot = None
    for i in range(count, n - 1):
        chunk = data[HEADER_SIZE + i * RECORD_SIZE:HEADER_SIZE + (i + 1) * RECORD_SIZE]
        if bytes(chunk) == bytes(RECORD_SIZE):
            slot = i
            break

    if slot is None:
        return None, "no zeroed headroom slot found"

    off = HEADER_SIZE + slot * RECORD_SIZE
    data[off:off + RECORD_SIZE] = record
    # deliberately NOT bumping data[9]
    return bytes(data), f"wrote to headroom slot {slot}, data[9] unchanged at {count}"


def strategy_headroom_bump_count(raw):
    """Write into first zeroed headroom slot, bump data[9]."""
    data = bytearray(raw)
    n = (len(data) - HEADER_SIZE) // RECORD_SIZE
    count = data[COUNT_BYTE]
    record = build_record(GAD_NAME, GAD_X, GAD_Y, GAD_Z, GAD_ZONE)

    slot = None
    for i in range(count, n - 1):
        chunk = data[HEADER_SIZE + i * RECORD_SIZE:HEADER_SIZE + (i + 1) * RECORD_SIZE]
        if bytes(chunk) == bytes(RECORD_SIZE):
            slot = i
            break

    if slot is None:
        return None, "no zeroed headroom slot found"

    off = HEADER_SIZE + slot * RECORD_SIZE
    data[off:off + RECORD_SIZE] = record
    data[COUNT_BYTE] = min(slot + 1, 255)
    return bytes(data), f"wrote to headroom slot {slot}, data[9] bumped to {data[COUNT_BYTE]}"


def strategy_skip_exex_use_next(raw):
    """Skip EXEX slot, write into slot immediately after EXEX, bump data[9] past EXEX."""
    data = bytearray(raw)
    n = (len(data) - HEADER_SIZE) // RECORD_SIZE
    exex = find_exex(data)
    if exex is None:
        return None, "no EXEX found"
    if exex + 1 >= n:
        return None, "no slot after EXEX"

    record = build_record(GAD_NAME, GAD_X, GAD_Y, GAD_Z, GAD_ZONE)
    off = HEADER_SIZE + (exex + 1) * RECORD_SIZE
    data[off:off + RECORD_SIZE] = record
    # bump past EXEX and the new record
    data[COUNT_BYTE] = min(exex + 2, 255)
    return bytes(data), f"wrote after EXEX at slot {exex+1}, data[9]={data[COUNT_BYTE]}"


STRATEGIES = [
    ("1_overwrite_exex",          strategy_overwrite_exex),
    ("2_push_exex_forward",       strategy_push_exex_forward),
    ("3_headroom_no_count_bump",  strategy_headroom_no_count_bump),
    ("4_headroom_bump_count",     strategy_headroom_bump_count),
    ("5_skip_exex_use_next",      strategy_skip_exex_use_next),
]


def main():
    parser = argparse.ArgumentParser(description="Test RSC append strategies")
    parser.add_argument("game_dir", help="Path to Shadow Man Remastered install")
    parser.add_argument("output_dir", help="Directory to write strategy files")
    parser.add_argument("--pack", type=int, default=None,
                        help="Pack strategy N (1-5) into game mods folder")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from kpf_handler import find_kpf_files, build_kpf_index, extract_game_files, find_mods_dir, create_mod_kpf

    game_dir = Path(args.game_dir)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Extract vanilla t1tchgad/quest.rsc from KPF
    print("Extracting vanilla t1tchgad/quest.rsc from KPF...")
    kpf_files = find_kpf_files(str(game_dir))
    if not kpf_files:
        print("ERROR: no KPF files found")
        sys.exit(1)
    work = out / "_extract"
    extract_game_files(kpf_files, str(work), ["t1tchgad"])
    quest_path = work / "levels" / "t1tchgad" / "quest.rsc"
    if not quest_path.exists():
        print("ERROR: t1tchgad/quest.rsc not found in KPF")
        sys.exit(1)

    raw = quest_path.read_bytes()
    print(f"Vanilla: size={len(raw)}  data[9]={raw[9]}")
    dump_boundary(raw, "vanilla")

    # Generate all strategy files
    print("\nGenerating strategy variants...")
    generated = {}
    for name, fn in STRATEGIES:
        result, desc = fn(raw)
        if result is None:
            print(f"\n  SKIP {name}: {desc}")
            continue
        outpath = out / f"{name}_quest.rsc"
        outpath.write_bytes(result)
        dump_boundary(bytearray(result), name)
        print(f"  desc: {desc}")
        generated[name] = outpath

    if args.pack is not None:
        if not (1 <= args.pack <= len(STRATEGIES)):
            print(f"\nERROR: --pack must be 1-{len(STRATEGIES)}")
            sys.exit(1)

        strat_name = STRATEGIES[args.pack - 1][0]
        quest_file = generated.get(strat_name)
        if not quest_file:
            print(f"\nERROR: strategy {args.pack} ({strat_name}) was skipped")
            sys.exit(1)

        mods_dir = find_mods_dir(str(game_dir))
        create_mod_kpf(
            mods_dir,
            {"levels/t1tchgad/quest.rsc": str(quest_file)},
            mod_name="test_append.kpf",
        )
        print(f"\nPacked strategy {args.pack} ({strat_name}) -> {mods_dir}/test_append.kpf")
        print(f"Load Touch Gad Temple in-game to test.")
        print(f"Delete test_append.kpf from mods when done.")
    else:
        print(f"\nRun with --pack N to test a strategy in-game.")
        print(f"Strategies: {', '.join(str(i+1) for i in range(len(STRATEGIES)))}")


if __name__ == "__main__":
    main()