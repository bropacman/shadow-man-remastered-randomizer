"""
build_apworld.py
=================
Packages worlds/shadowman/ (from a local Archipelago checkout) into a
shadowman.apworld file — a plain zip with one top-level folder named after
the module ("shadowman/"), same format as any other custom AP world (e.g.
mm_recomp.apworld: a zip containing mm_recomp/__init__.py and siblings, no
manifest, no build metadata). Drop the result into another Archipelago
install's custom_worlds/ folder (or worlds/, replacing/adding the module)
and it's picked up like any built-in world.

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
import zipfile
from pathlib import Path

MODULE_NAME = "shadowman"

EXCLUDE_DIRS = {"__pycache__", "tools", "data"}
EXCLUDE_FILE_PATTERNS = [
    "*.pyc", "*.sav",
    "AP_FEATURE_GAP.md", "SESSION_NOTES_*.md", "LIVE_MEMORY_TRACKING_NOTES.md",
]


def _is_excluded(rel_path: Path) -> bool:
    if any(part in EXCLUDE_DIRS for part in rel_path.parts[:-1]):
        return True
    return any(fnmatch.fnmatch(rel_path.name, pat) for pat in EXCLUDE_FILE_PATTERNS)


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
