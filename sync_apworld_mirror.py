"""
sync_apworld_mirror.py
=======================
One-way publish: apworld/ (this repo, the real source of truth) -->
shadow-man-remastered-ap-world (the public-facing mirror repo).

Background (2026-09-18): apworld/'s full history was merged into this
repo via `git subtree` (Phase 4, docs/PHASE4_ARCHITECTURE_SCOPING.md).
Real-world precedent from other Archipelago projects (e.g.
tanjo3/wwrando + tanjo3/tww_apworld) showed the community convention
leans toward a dedicated, single-purpose repo for the AP world itself
-- easier to clone/contribute to without pulling in this repo's whole
standalone-randomizer build tooling, and a cleaner shape if this world
is ever submitted upstream to ArchipelagoMW/Archipelago. So: apworld/
stays the one place development actually happens (never edit the
mirror directly), and this script is the deliberate, manually-run
publish step -- never automatic CI, never silently triggered.

What it does:
  1. Clones shadow-man-remastered-ap-world fresh into a scratch temp
     dir (never reuses a possibly-stale local checkout -- this
     project's own history has real bugs traced back to exactly that
     mistake elsewhere, see CLAUDE.md).
  2. Replaces its entire tracked tree with apworld/'s current contents
     (handles deletions correctly -- clears everything except .git
     first, rather than only overwriting files that still exist).
  3. Commits (only if there's an actual diff -- never an empty commit)
     with a message naming the exact source commit, and pushes.

Does NOT tag or publish a release -- that stays a separate, deliberate
step (see RELEASING.md).

Usage:
    python sync_apworld_mirror.py [--dry-run]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APWORLD_SRC = ROOT / "apworld"
MIRROR_REPO_URL = "https://github.com/bropacman/shadow-man-remastered-ap-world.git"


def run(cmd: list, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print(f"$ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip())
    if check and result.returncode != 0:
        sys.exit(f"ABORTED: command failed with exit code {result.returncode}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                         help="Clone and diff, but don't commit or push")
    args = parser.parse_args()

    if not (APWORLD_SRC / "__init__.py").exists():
        sys.exit(f"ERROR: {APWORLD_SRC} doesn't look like a worlds/shadowman "
                  f"folder (no __init__.py).")

    source_sha = run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT).stdout.strip()
    print(f"Publishing apworld/ as of {source_sha} to the mirror repo...")

    with tempfile.TemporaryDirectory(prefix="apworld_mirror_") as tmp:
        mirror_dir = Path(tmp) / "mirror"
        print(f"\n[1/4] Fresh clone into {mirror_dir}...")
        run(["git", "clone", "--quiet", MIRROR_REPO_URL, str(mirror_dir)])

        print("\n[2/4] Replacing tracked tree with apworld/'s current contents "
              "(clears existing files first so deletions carry over correctly)...")
        for item in mirror_dir.iterdir():
            if item.name == ".git":
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        for item in APWORLD_SRC.iterdir():
            dest = mirror_dir / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        print("\n[3/4] Checking for an actual diff...")
        run(["git", "add", "-A"], cwd=mirror_dir)
        status = run(["git", "status", "--porcelain"], cwd=mirror_dir)
        if not status.stdout.strip():
            print("No changes -- mirror is already up to date with apworld/.")
            return

        print(f"Changes found:\n{status.stdout}")

        if args.dry_run:
            print("--dry-run: stopping before commit/push.")
            return

        print("\n[4/4] Committing and pushing...")
        run(["git", "commit", "-m",
             f"Sync from shadow-man-remastered-randomizer@{source_sha}\n\n"
             f"Automated snapshot publish, not a hand-edited commit -- see "
             f"sync_apworld_mirror.py. Development happens in that repo's "
             f"apworld/ folder; this repo is the public-facing mirror."],
            cwd=mirror_dir)
        run(["git", "push", "origin", "main"], cwd=mirror_dir)

    print("\nDone. Mirror repo updated.")


if __name__ == "__main__":
    main()
