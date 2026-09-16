"""
build_apworld.py
=================
Packages worlds/shadowman/ (from a local Archipelago checkout) into a
shadowman.apworld file — a plain zip with one top-level folder named after
the module ("shadowman/"). Drop the result into another Archipelago
install's custom_worlds/ folder (or worlds/, replacing/adding the module)
and it's picked up like any built-in world.

Also writes a top-level archipelago.json manifest into the zip (2026-09-16,
found while investigating this repo's own test setup) — REQUIRED, not
optional, despite this module's own former docstring claiming "no
manifest, no build metadata" was fine, modeled after mm_recomp.apworld.
Confirmed directly against a real local Archipelago 0.6.7 install: BOTH
mm_recomp.apworld (bad precedent, not actually a working example) and this
repo's own previously-built shadowman.apworld log
"Invalid or missing manifest file ... This apworld will stop working with
Archipelago 0.7.0" on load. Traced the exact mechanism in worlds/__init__.py/
worlds/Files.py: on AP < 0.7.0 a missing/invalid archipelago.json is only a
logged warning (silent enough that this went unnoticed), but
APWorldContainer.read_contents() unconditionally requires manifest["game"]
and manifest["compatible_version"] <= 7 (container_version), and AP >= 0.7.0
raises InvalidDataError instead of warning — the apworld would fail to load
at all. Schema/values confirmed against APWorldContainer.get_manifest()
itself (worlds/Files.py) and worlds/soe/tools/make_manifest.py's existing
precedent for a world that does this correctly.

Excludes dev-only content that isn't needed at runtime:
  - __pycache__/, *.pyc          — compiled bytecode, regenerated on import
  - tools/                        — codegen/diagnostic scripts (Ghidra/CE
                                     investigation tools, data/locations.csv
                                     -> extracted_locations.py generator) —
                                     confirmed nothing at runtime imports
                                     from tools/ or reads data/ directly;
                                     extracted_locations.py already has the
                                     CSV's contents baked in as Python code.
  - data/                         — codegen SOURCE for extracted_locations.py
                                     / extracted_enemy_locations.py, not
                                     read at runtime (see above)
  - *.sav                         — before.sav/after.sav, live-testing
                                     snapshots, not part of the world
  - AP_FEATURE_GAP.md,
    SESSION_NOTES_*.md,
    LIVE_MEMORY_TRACKING_NOTES.md — internal dev/session notes, not useful
                                     to a player installing the world
  - launch_client.bat,
    launch_game.bat                 — single-machine dev convenience scripts,
                                     not portable: launch_client.bat hardcodes
                                     this dev's own Archipelago checkout path
                                     (cd /d "C:/Users/.../Archipelago-0.6.7"),
                                     launch_game.bat hardcodes this dev's own
                                     game install path. Neither can be made
                                     "universal" by editing them — the real
                                     paths are only known on each player's own
                                     machine, and nothing in this repo's build
                                     step has access to another player's
                                     install layout. Not a functionality gap:
                                     ap_gui.py's own "Launch Game + Client" /
                                     "Launch Game Only" / "Launch Client Only"
                                     buttons already do exactly this, correctly
                                     and portably, reading each player's own
                                     game_dir/ap_dir from their gui_prefs.json
                                     (see ap_gui.py's launch_game()/
                                     launch_client_only()) — and the
                                     Archipelago Launcher's own "Shadow Man
                                     Remastered Client" menu entry (registered
                                     in __init__.py) covers the client-only
                                     case with zero path configuration needed
                                     at all. These two .bat files were always
                                     just this dev's personal shortcuts around
                                     functionality the project already has a
                                     portable answer for; they stay in the
                                     source checkout for local convenience,
                                     just never got shipped inside the
                                     package other players install.
  - .git/                         — (added 2026-08-31, Jon's ask -- "what
                                     takes up space?") the apworld repo's own
                                     git history/object database. Was being
                                     zipped in whole: measured 2026-08-31 at
                                     ~4.85MB of a 6.96MB built .apworld
                                     (~69% of the file), for zero runtime
                                     value -- a player's AP install never
                                     touches a world's git history.
  - docs/                         — (added 2026-08-31) README/guide
                                     screenshots, referenced by guide_en.md's
                                     markdown (not by README.md, which has no
                                     image refs). AP's own tutorial-page
                                     rendering (WebHostLib/misc.py) reads
                                     pre-generated docs out of the webhost's
                                     own static/generated/docs/ tree, never
                                     out of an installed world's package
                                     contents, so these images have no
                                     runtime reader once bundled — GitHub
                                     still renders guide_en.md with working
                                     images regardless, since it reads them
                                     straight from the repo, not the built
                                     .apworld. Was ~1.14MB uncompressed
                                     (~1.07MB compressed) of the same build.
  - _to_delete/                   — (added 2026-08-31) scratch holding pen
                                     for files pending manual deletion (see
                                     CLAUDE.md's device_bash delete-permission
                                     workflow) — never part of the world.
  - _patcher.py.removed_*         — (added 2026-08-31) old patcher.py,
                                     renamed rather than deleted when it was
                                     removed 2026-07-21 (see generate_output()'s
                                     docstring in __init__.py) — dead weight,
                                     not the actual runtime module.
  - .github/                      — (added 2026-09-16, caught the very first
                                     time this repo got a CI workflow) GitHub
                                     Actions config — build/test infra, never
                                     read by a player's Archipelago install.
  - .gitignore                    — (added 2026-09-16) dev-only git config.
  - test/                         — (added 2026-09-16, same day the AP
                                     world repo got its first test/ package)
                                     WorldTestBase-based regression tests,
                                     run via `pytest worlds/shadowman/test/`
                                     against a real Archipelago checkout —
                                     never imported by anything at runtime
                                     once a world is installed as a plain
                                     .apworld.

Kept: every .py file actually needed at runtime, guide_en.md (referenced by
name in __init__.py's WebWorld.tutorials — required, not just documentation),
README.md, ShadowManRemastered.yaml (example player options file).

Usage:
    python build_apworld.py --source "C:/path/to/Archipelago/worlds/shadowman" \\
                             --output "dist/apworld/shadowman.apworld"

(or just run build_apworld.bat, which also runs tools/check_apworld_sync.py
first as a non-fatal pre-flight drift check)
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import zipfile
from pathlib import Path

MODULE_NAME = "shadowman"

EXCLUDE_DIRS = {"__pycache__", "tools", "data", ".git", ".github", "docs", "_to_delete", "test"}
EXCLUDE_FILE_PATTERNS = [
    "*.pyc", "*.sav", ".gitignore",
    "AP_FEATURE_GAP.md", "SESSION_NOTES_*.md", "LIVE_MEMORY_TRACKING_NOTES.md",
    # Single-machine dev convenience scripts with hardcoded local paths —
    # see the module docstring's "Excludes" section for why these can't
    # just be edited to be portable, and what covers the same need instead.
    "launch_client.bat", "launch_game.bat",
    # Renamed-not-deleted debris (see module docstring's "Excludes" section).
    "_patcher.py.removed_*",
    # The overlay DLL's own local prefs file (server/name/password + window
    # positions, see overlay_dll/README.md) -- written next to the DLL during
    # live testing, i.e. right into this same source folder. Personal state,
    # never something to ship to other players (2026-08-31, caught while
    # investigating apworld size).
    "ap_overlay_prefs.json",
]


def _is_excluded(rel_path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel_path.parts[:-1]):
        return True
    return any(fnmatch.fnmatch(rel_path.name, pat) for pat in EXCLUDE_FILE_PATTERNS)


def _world_version() -> str:
    """
    Bare version string (no leading "v") for archipelago.json's
    world_version field. Reuses ap_gui.py's COMPANION_VERSION rather than
    inventing a separate number to track -- this repo's convention is
    already "one bump covers the AP world row and the AP Companion exe
    together" (see RELEASING.md), so the manifest should say the same
    thing the Companion's own UI does.

    Reads COMPANION_VERSION out of ap_gui.py's source text via regex
    rather than importing the module -- ap_gui.py imports pywebview/pyyaml
    at module level, real GUI-app dependencies this packaging script has
    no other reason to require.
    """
    text = (Path(__file__).parent / "ap_gui.py").read_text(encoding="utf-8")
    match = re.search(r'^COMPANION_VERSION\s*=\s*"v?([^"]+)"', text, re.MULTILINE)
    if not match:
        raise SystemExit("Could not find COMPANION_VERSION in ap_gui.py")
    return match.group(1)


def _build_manifest() -> dict:
    # Schema/values confirmed against worlds/Files.py's own
    # APWorldContainer.get_manifest() (the canonical writer AP itself
    # uses) -- compatible_version=7 matches that class's own hardcoded
    # value, not container_version generically, since a world manifest
    # specifically always uses 7 regardless of what container_version
    # happens to be on a given AP release.
    return {
        "game": "Shadow Man Remastered",
        "compatible_version": 7,
        "world_version": _world_version(),
    }


def build(source: Path, output: Path) -> list[str]:
    if not source.is_dir():
        raise SystemExit(f"Source folder not found: {source}")
    if not (source / "__init__.py").exists():
        raise SystemExit(f"{source} doesn't look like a world package (no __init__.py)")

    output.parent.mkdir(parents=True, exist_ok=True)

    included: list[str] = []
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(source.rglob("*")):
            if path.is_dir():
                continue
            rel = path.relative_to(source)
            if _is_excluded(rel):
                continue
            arcname = f"{MODULE_NAME}/{rel.as_posix()}"
            zf.write(path, arcname=arcname)
            included.append(arcname)

        # archipelago.json lives at the world's own package root inside the
        # zip (worlds/__init__.py's loader walks the whole extracted
        # namelist looking for a file ending in "archipelago.json", so the
        # exact directory depth isn't load-bearing -- placed here to match
        # APWorldContainer's own manifest_path default and every other
        # bundled world's convention).
        manifest_arcname = f"{MODULE_NAME}/archipelago.json"
        zf.writestr(manifest_arcname, json.dumps(_build_manifest(), indent=4))
        included.append(manifest_arcname)

    return included


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--source", type=Path, required=True,
                         help="Path to worlds/shadowman (the folder containing __init__.py)")
    parser.add_argument("--output", type=Path, default=Path("dist") / "apworld" / f"{MODULE_NAME}.apworld",
                         help="Output .apworld path (default: dist/apworld/shadowman.apworld -- "
                              "kept in its own subfolder, separate from dist/standalone/, since "
                              "the two ship independently; see RELEASING.md)")
    args = parser.parse_args()

    included = build(args.source, args.output)
    print(f"Wrote {args.output} ({len(included)} files, "
          f"{args.output.stat().st_size / 1024:.1f} KB)")
    for name in included:
        print(f"  {name}")


if __name__ == "__main__":
    main()
