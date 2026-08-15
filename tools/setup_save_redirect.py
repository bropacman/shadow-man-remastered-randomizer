"""
setup_save_redirect.py
=======================
SUPERSEDED (2026-07-20) — do not use. Kept only for reference.

Save-folder redirection is now handled automatically by save_path_patch.py,
applied as part of every patcher run (see run_patcher()'s Step 7 in
patcher.py). It patches thoth_x64_patched.exe directly so the redirect only
affects the patched exe — vanilla thoth_x64.exe keeps using the original
"saves" folder untouched, no toggling needed, no script to run. This
filesystem-junction approach was rejected because a junction is global to
the machine: it would also redirect the UNPATCHED vanilla exe, forcing a
manual switch every time the player alternated between vanilla and
randomizer play. See save_path_patch.py's module docstring for the current
mechanism.

Original docstring below, preserved for context only:
---------------------------------------------------------------------------
Redirects Shadow Man Remastered's save folder so randomizer/Archipelago
playthroughs never share save slots with vanilla saves.

No EXE patching involved. The game (and Steam Cloud, and every other tool)
resolves its save folder via Windows' "Saved Games" known-folder plus a
fixed subpath:

    <Saved Games>\\Nightdive Studios\\Shadowman EX\\saves

This script backs that real folder up once, creates a sibling
`saves_archipelago` folder, and replaces `saves` with an NTFS *directory
junction* pointing at it. Junctions are transparent to path resolution — the
game, client.py's _find_save_dir(), and Explorer all just see a normal
folder called `saves` — and unlike symlinks, junctions do NOT require
Administrator privileges on Windows.

Commands
--------
    python setup_save_redirect.py status    Show current state, do nothing.
    python setup_save_redirect.py setup      Back up vanilla saves, create the
                                              junction (idempotent — safe to
                                              re-run).
    python setup_save_redirect.py restore   Undo: remove the junction, restore
                                              the original vanilla saves folder.

On Linux/Proton (no native "Saved Games" folder), the equivalent path is
~/.local/share/Nightdive Studios/Shadowman EX/saves and a plain symlink is
used instead of a junction (symlinks work unprivileged on Linux).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

# Mirrors client.py's _SAVE_SUBDIRS (worlds/shadowman/client.py) — same
# candidate list, same priority order, so this always targets whichever
# folder the game/client actually use.
_SAVE_SUBDIRS = [
    Path("Saved Games") / "Nightdive Studios" / "Shadowman EX" / "saves",
    Path("AppData") / "Local" / "Nightdive Studios" / "Shadowman EX" / "saves",
    Path(".local") / "share" / "Nightdive Studios" / "Shadowman EX" / "saves",
]

REDIRECT_NAME = "saves_archipelago"
BACKUP_NAME   = "saves_vanilla_backup"

FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_reparse_point(path: Path) -> bool:
    """True if `path` is a junction/symlink rather than a real directory."""
    if not path.exists():
        return False
    if os.name == "nt":
        try:
            attrs = path.stat().st_file_attributes  # Windows-only attribute
        except AttributeError:
            attrs = 0
        return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)
    return path.is_symlink()


def _find_existing_saves_path() -> Path | None:
    """
    Return the first candidate path that currently exists (as a real dir OR
    an already-set-up redirect), searched in the same priority order
    client.py uses. None if the game has never been run / no saves folder
    exists yet anywhere.
    """
    home = Path.home()
    for sub in _SAVE_SUBDIRS:
        c = home / sub
        if c.exists():
            return c
    return None


def _default_saves_path() -> Path:
    """
    Where the saves folder SHOULD live if none exists yet — the first
    (highest-priority) candidate, i.e. the modern "Saved Games" location on
    Windows. Only used by `setup` when nothing exists yet (fresh install
    that hasn't been launched once).
    """
    return Path.home() / _SAVE_SUBDIRS[0]


def _make_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        # Directory junction — no admin rights required (unlike mklink /D
        # symlinks). /J takes absolute paths.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"mklink /J failed: {result.stdout}{result.stderr}")
    else:
        os.symlink(target, link, target_is_directory=True)


def cmd_status() -> None:
    saves = _find_existing_saves_path()
    if saves is None:
        print("No saves folder found yet (game may never have been launched).")
        print(f"Would create at: {_default_saves_path()}")
        return

    print(f"Saves path:  {saves}")
    if _is_reparse_point(saves):
        redirect_target = saves.parent / REDIRECT_NAME
        backup          = saves.parent / BACKUP_NAME
        print(f"  -> Already redirected (junction/symlink).")
        print(f"  Archipelago saves: {redirect_target}  "
              f"({'exists' if redirect_target.is_dir() else 'MISSING'})")
        print(f"  Vanilla backup:    {backup}  "
              f"({'exists' if backup.is_dir() else 'none'})")
    else:
        n = len(list(saves.glob("save_*.sav"))) if saves.is_dir() else 0
        print(f"  -> Real folder (not redirected). {n} save file(s) present.")
        print(f"  Run `setup` to switch to a separate Archipelago save folder.")


def cmd_setup() -> None:
    saves = _find_existing_saves_path()
    if saves is not None and _is_reparse_point(saves):
        print(f"Already redirected: {saves} — nothing to do. "
              f"(Run `status` to see details, `restore` to undo.)")
        return

    if saves is None:
        # Fresh install, nothing to back up — just create the parent chain
        # and the redirect target directly at the default path.
        saves = _default_saves_path()
        saves.parent.mkdir(parents=True, exist_ok=True)
        redirect_target = saves.parent / REDIRECT_NAME
        redirect_target.mkdir(parents=True, exist_ok=True)
        _make_link(saves, redirect_target)
        print(f"No existing saves found. Created fresh Archipelago saves "
              f"folder and linked it at:\n  {saves} -> {redirect_target}")
        return

    # Real vanilla folder exists — back it up before replacing it.
    backup = saves.parent / BACKUP_NAME
    if backup.exists():
        raise SystemExit(
            f"Refusing to overwrite existing backup at {backup}. "
            f"If you already ran `setup` once, use `status`/`restore` instead."
        )

    redirect_target = saves.parent / REDIRECT_NAME
    if redirect_target.exists():
        raise SystemExit(
            f"{redirect_target} already exists but {saves} is not yet "
            f"redirected — this is an unexpected in-between state. "
            f"Please check both folders manually before retrying."
        )

    print(f"Backing up vanilla saves:\n  {saves} -> {backup}")
    shutil.move(str(saves), str(backup))

    redirect_target.mkdir(parents=True, exist_ok=True)
    _make_link(saves, redirect_target)
    print(f"Created Archipelago saves folder and linked it in:\n"
          f"  {saves} -> {redirect_target}")
    print("\nDone. Vanilla saves are safe in the backup folder above. "
          "New in-game saves now go to the Archipelago folder — pick any "
          "unused save slot in-game to start a randomizer file.")


def cmd_restore() -> None:
    saves = _find_existing_saves_path()
    if saves is None or not _is_reparse_point(saves):
        print("Not currently redirected — nothing to restore.")
        return

    backup = saves.parent / BACKUP_NAME
    if not backup.is_dir():
        raise SystemExit(
            f"No vanilla backup found at {backup} — refusing to remove the "
            f"redirect, since that would leave no `saves` folder at all. "
            f"(Your Archipelago saves are still intact at "
            f"{saves.parent / REDIRECT_NAME}.)"
        )

    if os.name == "nt":
        saves.rmdir()  # removes the junction itself, not its target's contents
    else:
        saves.unlink()

    shutil.move(str(backup), str(saves))
    print(f"Restored vanilla saves to:\n  {saves}\n"
          f"(Archipelago saves remain untouched at "
          f"{saves.parent / REDIRECT_NAME} — re-run `setup` to switch back.)")


def main() -> None:
    commands = {"status": cmd_status, "setup": cmd_setup, "restore": cmd_restore}
    if len(sys.argv) != 2 or sys.argv[1] not in commands:
        print(f"Usage: python {Path(__file__).name} {{status|setup|restore}}")
        sys.exit(1)
    commands[sys.argv[1]]()


if __name__ == "__main__":
    main()
