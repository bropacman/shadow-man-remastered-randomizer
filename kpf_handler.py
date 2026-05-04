"""
Shadow Man Remastered - KPF Mod Handler
========================================
The game natively supports a mods/ folder. Any .kpf file placed there
is loaded BEFORE the main ShadowManEX0*.kpf archives, acting as an
override layer. Rules:
  - KPF files must use ZIP_STORED (no compression, level 0)
  - Only files that differ from vanilla need to be included
  - Multiple mod KPFs are supported (load order = alphabetical)
  - Delete the mod KPF to instantly restore vanilla

This means we NEVER touch the original KPF archives.
"""

import os
import zipfile
import fnmatch
from pathlib import Path
from dataclasses import dataclass, field

MOD_KPF_NAME = "shadowman_randomizer.kpf"
from constants import KPF_TARGET_EXTENSIONS

# ── KPF Reading (originals) ───────────────────────────────────────────────────

def find_kpf_files(game_dir: str) -> list[str]:
    """Find all base game .kpf files, sorted by name. Excludes mods/ folder."""
    game_path = Path(game_dir)
    kpfs = sorted(p for p in game_path.glob("*.kpf")
                  if "mods" not in str(p).lower())
    return [str(k) for k in kpfs]


def find_mods_dir(game_dir: str) -> Path:
    """Locate the mods/ folder, searching game dir and parent."""
    for base in [Path(game_dir), Path(game_dir).parent]:
        for candidate in ["mods", "Mods", "MODS"]:
            p = base / candidate
            if p.is_dir():
                return p
    mods = Path(game_dir) / "mods"
    mods.mkdir(exist_ok=True)
    return mods


@dataclass
class KpfIndex:
    file_to_kpf: dict = field(default_factory=dict)
    kpf_dir: str = ""


def build_kpf_index(kpf_files: list) -> KpfIndex:
    index = KpfIndex(kpf_dir=str(Path(kpf_files[0]).parent) if kpf_files else "")
    for kpf_path in kpf_files:
        kpf_name = Path(kpf_path).name
        try:
            with zipfile.ZipFile(kpf_path, 'r') as zf:
                for info in zf.infolist():
                    if not info.is_dir():
                        internal = info.filename.replace('\\', '/')
                        index.file_to_kpf[internal] = kpf_name
        except zipfile.BadZipFile:
            print(f"  WARNING: {kpf_name} is not a valid ZIP, skipping")
    return index


def find_file_in_kpf(index, pattern: str) -> list:
    """Find files matching a glob pattern. Returns [(internal_path, kpf_name)]."""
    return sorted(
        (path, kpf) for path, kpf in index.file_to_kpf.items()
        if fnmatch.fnmatch(path.lower(), pattern.lower())
    )


def extract_file_from_kpf(kpf_path: str, internal_path: str, output_path: str) -> bool:
    """Extract a single file from a KPF to a local path."""
    try:
        with zipfile.ZipFile(kpf_path, 'r') as zf:
            names = zf.namelist()
            match = next((n for n in names if n.replace('\\', '/') == internal_path), None)
            if not match:
                match = next((n for n in names
                              if n.lower().replace('\\', '/') == internal_path.lower()), None)
            if not match:
                return False
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with zf.open(match) as src, open(output_path, 'wb') as dst:
                dst.write(src.read())
            return True
    except zipfile.BadZipFile:
        return False


def extract_all_matching(kpf_files: list, pattern: str, output_dir: str) -> list:
    """Extract all files matching pattern from KPFs into output_dir."""
    extracted = []
    for kpf_path in kpf_files:
        try:
            with zipfile.ZipFile(kpf_path, 'r') as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    internal = info.filename.replace('\\', '/')
                    if fnmatch.fnmatch(internal.lower(), pattern.lower()):
                        dest = Path(output_dir) / internal
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info) as src, open(dest, 'wb') as dst:
                            dst.write(src.read())
                        extracted.append(str(dest))
        except zipfile.BadZipFile:
            continue
    return extracted


def extract_game_files(kpf_files: list, output_dir: str, level_folders: list, game_dir: str = None) -> KpfIndex:
    mod_kpf_files = []
    if game_dir:
        mods_dir = find_mods_dir(game_dir)
        mod_kpf = mods_dir / MOD_KPF_NAME
        if mod_kpf.exists():
            mod_kpf_files = [str(mod_kpf)]

    index = build_kpf_index(kpf_files)
    out = Path(output_dir)
    extracted_count = 0
    target_extensions = KPF_TARGET_EXTENSIONS
    valid_folders = {f.lower() for f in level_folders}

    # Mod KPF first (priority), then base KPFs
    for kpf_path in mod_kpf_files + kpf_files:
        try:
            with zipfile.ZipFile(kpf_path, 'r') as zf:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    internal = info.filename.replace('\\', '/')
                    path_parts = internal.lower().split("/")
                    dest = out / internal

                    # Don't overwrite files already extracted from mod KPF
                    if dest.exists() and kpf_path not in mod_kpf_files:
                        continue

                    if len(path_parts) >= 2:
                        folder = path_parts[-2]
                        filename = path_parts[-1]
                        extension = Path(filename).suffix
                        if folder in valid_folders and extension in target_extensions:
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(info) as src, open(dest, 'wb') as dst:
                                dst.write(src.read())
                            extracted_count += 1

                    low_path = internal.lower()
                    if low_path in ["levels/levels.txt", "scripts/levels.txt"]:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(info) as src, open(dest, 'wb') as dst:
                            dst.write(src.read())
                        extracted_count += 1
        except zipfile.BadZipFile:
            continue

    print(f"  Extracted {extracted_count} file(s) to {out}")
    return index

def which_kpf_has_levels(index: KpfIndex) -> str:
    """Return the name of the first KPF that contains a levels/ file."""
    for internal in sorted(index.file_to_kpf.keys()):
        if internal.lower().startswith("levels/"):
            return index.file_to_kpf[internal]
    return ""


# ── Mod KPF Creation ──────────────────────────────────────────────────────────

def create_mod_kpf(mods_dir, files: dict, mod_name: str = MOD_KPF_NAME) -> str:
    """
    Create a mod KPF with ZIP_STORED (no compression) as required by the game.
    files: {internal_kpf_path: local_file_path}
    """
    mods_path = Path(mods_dir)
    mods_path.mkdir(parents=True, exist_ok=True)
    output_path = mods_path / mod_name

    with zipfile.ZipFile(output_path, 'w', compression=zipfile.ZIP_STORED) as zf:
        for internal_path, local_path in files.items():
            zf.write(local_path, internal_path.replace('\\', '/'))

    size_kb = output_path.stat().st_size // 1024
    print(f"  Created: {output_path}")
    print(f"  {len(files)} file(s), {size_kb} KB, ZIP_STORED (no compression)")
    return str(output_path)


def remove_mod_kpf(mods_dir, mod_name: str = MOD_KPF_NAME) -> bool:
    """Delete the randomizer mod KPF to restore vanilla."""
    path = Path(mods_dir) / mod_name
    if path.exists():
        path.unlink()
        print(f"  Removed: {path}")
        return True
    return False


def mod_kpf_exists(mods_dir, mod_name: str = MOD_KPF_NAME) -> bool:
    return (Path(mods_dir) / mod_name).exists()


def build_and_install_mod(game_dir: str, modified_files: dict,
                          mod_name: str = MOD_KPF_NAME) -> str:
    """One-shot: remove any existing randomizer mod, install new one."""
    mods_dir = find_mods_dir(game_dir)
    if mod_kpf_exists(mods_dir):
        remove_mod_kpf(mods_dir)
    return create_mod_kpf(mods_dir, modified_files, mod_name)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Shadow Man KPF Mod Tool")
    sub = parser.add_subparsers(dest="cmd")

    ls = sub.add_parser("list", help="List files in base KPFs matching a pattern")
    ls.add_argument("game_dir")
    ls.add_argument("pattern", nargs="?", default="*/quest.rsc")

    rm = sub.add_parser("remove", help="Remove randomizer mod (restore vanilla)")
    rm.add_argument("game_dir")

    st = sub.add_parser("status", help="Show current mod status")
    st.add_argument("game_dir")

    args = parser.parse_args()

    if args.cmd == "list":
        kpfs = find_kpf_files(args.game_dir)
        index = build_kpf_index(kpfs)
        matches = find_file_in_kpf(index, args.pattern)
        print(f"Files matching '{args.pattern}':")
        for path, kpf in matches:
            print(f"  [{kpf}]  {path}")

    elif args.cmd == "remove":
        mods_dir = find_mods_dir(args.game_dir)
        if remove_mod_kpf(mods_dir):
            print("Vanilla restored.")
        else:
            print("No randomizer mod found.")

    elif args.cmd == "status":
        mods_dir = find_mods_dir(args.game_dir)
        mod_path = Path(mods_dir) / MOD_KPF_NAME
        if mod_path.exists():
            size_kb = mod_path.stat().st_size // 1024
            with zipfile.ZipFile(mod_path) as zf:
                count = len([i for i in zf.infolist() if not i.is_dir()])
            print(f"Randomizer mod ACTIVE: {mod_path}")
            print(f"  {count} file(s), {size_kb} KB")
        else:
            print("No randomizer mod active — vanilla game")
