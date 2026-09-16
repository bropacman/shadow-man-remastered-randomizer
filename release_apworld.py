"""
release_apworld.py
===================
Formalizes the AP world / AP Companion release pipeline (Phase 3 of
docs/REPO_RESTRUCTURING_PLAN.md) — encodes the roughly 15 manual tool
calls a real release (2026-09-15/16) needed by hand into one script:
regenerate this repo's own generated data, sync it into the AP world
checkout, regenerate there too, run the cross-repo drift check as a
REAL blocking gate (not the advisory warning build_apworld.bat's own
copy of this step gives), compile-check both repos, then build both
shippable artifacts.

Scope, deliberately limited: this script does NOT commit, push, tag,
or publish anything. Those stay explicit, confirmed actions — a
release is a "visible to others" event and shouldn't happen as a side
effect of running a build script. What this DOES remove is the
error-prone manual plumbing *between* those steps — the same plumbing
that, run by hand, already produced one silently-stale
extracted_locations.py and one missed sfx_randomizer.py sync earlier
this project's history.

What this does NOT sync: only the two auto-generated data files
(data/locations.csv -> extracted_locations.py, in both repos) are
synced automatically. Every other hand-copied file (sprint_patch.py,
gad_pickup_patch.py, the "expected-divergent" set, etc.) still needs a
human to port real changes over by hand, same as always — this script
only guarantees you'll be TOLD if you forgot (step 3's drift check is
a hard abort here, not a warning), not that the porting itself is
automated.

Usage:
    python release_apworld.py [--ap-dir PATH] [--skip-companion]

Requires this repo's own build dependencies already installed
(pywebview, pyinstaller, pyyaml, keystone-engine, capstone — see
RELEASING.md) unless --skip-companion is passed.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_AP_DIR = Path(r"C:\Users\jonat\Documents\Archipelago-0.6.7\worlds\shadowman")


def run(cmd: list, cwd: Path | None = None) -> None:
    print(f"\n$ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        sys.exit(f"\nABORTED: command failed with exit code {result.returncode} "
                  f"(cwd={cwd or Path.cwd()})")


def compile_check(repo_root: Path) -> None:
    """Mirrors CI's own compile-check step exactly: git ls-files, not a
    raw glob, so this only ever checks what's actually committed."""
    listed = subprocess.run(["git", "ls-files", "*.py"], cwd=repo_root,
                             capture_output=True, text=True, check=True)
    py_files = [line for line in listed.stdout.splitlines() if line.strip()]
    if not py_files:
        sys.exit(f"ABORTED: git ls-files found no .py files under {repo_root} "
                  f"-- wrong path, or not a git repo?")
    run([sys.executable, "-m", "py_compile", *py_files], cwd=repo_root)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ap-dir", type=Path, default=DEFAULT_AP_DIR,
                         help=f"Path to the AP world checkout's worlds/shadowman "
                              f"folder (default: {DEFAULT_AP_DIR})")
    parser.add_argument("--skip-companion", action="store_true",
                         help="Skip building the AP Companion exe (faster "
                              "iteration when only the apworld itself changed)")
    args = parser.parse_args()

    ap_dir: Path = args.ap_dir
    if not (ap_dir / "__init__.py").exists():
        sys.exit(f"ERROR: {ap_dir} doesn't look like a worlds/shadowman folder "
                  f"(no __init__.py). Pass --ap-dir to point at the right one.")

    print("=" * 70)
    print("Shadow Man Remastered -- AP release pipeline")
    print(f"AP world checkout: {ap_dir}")
    print("=" * 70)

    print("\n[1/7] Regenerating this repo's own extracted_locations.py "
          "(in case data/locations.csv changed since it was last run)...")
    run([sys.executable, "tools/generate.py"], cwd=ROOT)

    print("\n[2/7] Syncing data/locations.csv into the AP world checkout "
          "and regenerating its extracted_locations.py...")
    (ap_dir / "data").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "data" / "locations.csv", ap_dir / "data" / "locations.csv")
    run([sys.executable, "tools/generate.py"], cwd=ap_dir)

    print("\n[3/7] Cross-repo drift check -- BLOCKING here (unlike "
          "build_apworld.bat's own non-fatal copy of this step), since "
          "this script is meant to run right before a real release.")
    run([sys.executable, "tools/check_apworld_sync.py", "--ap-dir", str(ap_dir)], cwd=ROOT)

    print("\n[4/7] Compile-checking this repo...")
    compile_check(ROOT)

    print("\n[5/7] Compile-checking the AP world checkout...")
    compile_check(ap_dir)

    print("\n[6/7] Building shadowman.apworld...")
    run([sys.executable, "build_apworld.py", "--source", str(ap_dir),
         "--output", str(ROOT / "dist" / "apworld" / "shadowman.apworld")], cwd=ROOT)

    if args.skip_companion:
        print("\n[7/7] Skipping AP Companion build (--skip-companion).")
    else:
        print("\n[7/7] Building the AP Companion exe (this installs/upgrades "
              "pyinstaller, pywebview, pyyaml, keystone-engine, capstone -- "
              "can take a few minutes)...")
        run(["cmd", "/c", "build_ap_gui.bat"], cwd=ROOT)

    apworld_path = ROOT / "dist" / "apworld" / "shadowman.apworld"
    companion_path = ROOT / "dist" / "ap_companion" / "shadow_man_ap_companion.exe"

    print("\n" + "=" * 70)
    print("Build pipeline complete. Artifacts:")
    print(f"  {apworld_path}" + (" (built)" if apworld_path.exists() else " -- MISSING"))
    if not args.skip_companion:
        print(f"  {companion_path}" + (" (built)" if companion_path.exists() else " -- MISSING"))
    print("""
Still needed -- deliberately NOT automated, do these yourself:
  1. Spot-check the built .apworld's contents look right (e.g. unzip it
     and confirm the files you expect are actually in there).
  2. Bump COMPANION_VERSION in ap_gui.py if this is a real release, and
     re-run this script (the build embeds whatever's currently there).
  3. Commit + push both repos' source changes.
  4. Tag both repos (AP world repo: vX.Y.Z on its own 0.x track;
     standalone repo, only if the source commit needs its own marker:
     apworld-vX.Y.Z -- see RELEASING.md for the convention).
  5. Write release notes and actually publish (gh release create) --
     this script builds artifacts, it doesn't decide what changed or
     announce anything.
""")


if __name__ == "__main__":
    main()
