"""
scan_weapon_sfx.py
──────────────────
Lists every audio/sfx/weapons/* file found in the game KPFs, grouped by
weapon folder, then flags which files are already mapped in WEAPON_SOUND_SETS
and which are not.

Usage (run from the repo root):
    python tools/scan_weapon_sfx.py

The script auto-discovers KPF files the same way the randomizer does —
just point GAME_DIR at your Shadow Man Remastered installation if it's not
auto-detected.
"""

import sys
import os
import argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from kpf_handler import find_kpf_files, build_kpf_index, find_file_in_kpf
from constants import WEAPON_SOUND_SETS

# ── Config ────────────────────────────────────────────────────────────────────

# Leave empty to auto-detect from common Steam paths, or pass --game-dir on CLI.
GAME_DIR = ""

_STEAM_GUESSES = [
    r"C:\Program Files (x86)\Steam\steamapps\common\Shadow Man Remastered",
    r"C:\Program Files (x86)\Steam2\steamapps\common\Shadow Man Remastered",
    r"C:\Program Files\Steam\steamapps\common\Shadow Man Remastered",
    r"D:\SteamLibrary\steamapps\common\Shadow Man Remastered",
    r"E:\SteamLibrary\steamapps\common\Shadow Man Remastered",
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scan weapon SFX in Shadow Man Remastered KPFs.")
    p.add_argument("--game-dir", default="", help="Path to Shadow Man Remastered install folder.")
    return p.parse_args()


def _find_game_dir(cli_override: str = "") -> str | None:
    if cli_override and Path(cli_override).exists():
        return cli_override
    if cli_override:
        print(f"WARNING: --game-dir path not found: {cli_override!r}")
    if GAME_DIR and Path(GAME_DIR).exists():
        return GAME_DIR
    for p in _STEAM_GUESSES:
        if Path(p).exists():
            return p
    return None


def _already_mapped() -> set[str]:
    """Collect every path already listed in WEAPON_SOUND_SETS (any pool)."""
    mapped: set[str] = set()
    for sets in WEAPON_SOUND_SETS.values():
        for s in sets:
            mapped.update(s)
    return mapped


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    args = _parse_args()
    game_dir = _find_game_dir(args.game_dir)
    if not game_dir:
        print("ERROR: Could not find Shadow Man Remastered installation.")
        print("Set GAME_DIR at the top of this script and try again.")
        sys.exit(1)

    print(f"Game dir : {game_dir}")
    kpf_files = find_kpf_files(game_dir)
    if not kpf_files:
        print("ERROR: No .kpf files found in game directory.")
        sys.exit(1)
    print(f"KPF files: {[Path(k).name for k in kpf_files]}\n")

    index = build_kpf_index(kpf_files)
    all_weapon_files = [
        path for path, _ in find_file_in_kpf(index, "audio/sfx/weapons/*")
        if path.lower().endswith(".wav")
    ]

    if not all_weapon_files:
        print("No weapon audio files found — check the KPF paths.")
        sys.exit(1)

    mapped = _already_mapped()

    # Group by weapon folder
    by_weapon: dict[str, list[str]] = {}
    for path in sorted(all_weapon_files):
        parts = path.split("/")
        weapon = parts[3] if len(parts) > 3 else "?"
        by_weapon.setdefault(weapon, []).append(path)

    # Print grouped listing
    total = 0
    unmapped_total = 0
    print("=" * 70)
    print(f"{'WEAPON':<20}  {'FILE':<35}  STATUS")
    print("=" * 70)
    for weapon, files in sorted(by_weapon.items()):
        for path in files:
            status = "  mapped" if path in mapped else "  UNMAPPED"
            stem = Path(path).name
            print(f"  {weapon:<18}  {stem:<35}  {status}")
            total += 1
            if path not in mapped:
                unmapped_total += 1
        print()

    print("=" * 70)
    print(f"Total weapon audio files : {total}")
    print(f"Already mapped           : {total - unmapped_total}")
    print(f"Unmapped                 : {unmapped_total}")
    print()

    # Summary of unmapped files, grouped by inferred sound type
    unmapped = [p for p in sorted(all_weapon_files) if p not in mapped]
    if unmapped:
        print("── Unmapped files ──────────────────────────────────────────────────")
        for p in unmapped:
            print(f"  {p}")


if __name__ == "__main__":
    main()
