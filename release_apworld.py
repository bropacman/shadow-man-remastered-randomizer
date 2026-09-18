"""
release_apworld.py
===================
Formalizes the AP world / AP Companion release pipeline (Phase 3 of
docs/REPO_RESTRUCTURING_PLAN.md) — encodes the roughly 15 manual tool
calls a real release (2026-09-15/16) needed by hand into one script.

Updated 2026-09-16 for the Phase 4 monorepo merge (see
docs/PHASE4_ARCHITECTURE_SCOPING.md): apworld/ is now a subfolder of
this same repo, with its own full history preserved via `git subtree`.
This script no longer syncs between two separate checkouts on disk —
it regenerates this repo's own generated data (which now includes
apworld/'s copy, since both live in the same working tree), runs the
intra-repo drift check between root-level and apworld/-level shared
files as a REAL blocking gate (not the advisory warning
build_apworld.bat's own copy of this step gives), compile-checks the
whole repo (apworld/ included automatically — `git ls-files` from repo
root already covers it), then builds both shippable artifacts.

Scope, deliberately limited: this script does NOT commit, push, tag,
or publish anything. Those stay explicit, confirmed actions — a
release is a "visible to others" event and shouldn't happen as a side
effect of running a build script. What this DOES remove is the
error-prone manual plumbing *between* those steps — the same plumbing
that, run by hand, already produced one silently-stale
extracted_locations.py and one missed sfx_randomizer.py sync earlier
this project's history.

What this does NOT sync: only the two auto-generated data files
(data/locations.csv -> extracted_locations.py) are synced automatically,
now via a plain intra-repo copy. Every other file in the "expected-
divergent" set (access_rules.py, fill.py, regions.py, etc. — genuinely
different implementations by design, see PHASE4_ARCHITECTURE_SCOPING.md)
and the not-yet-de-duplicated "near-identical" set still needs a human
to port real changes over by hand — this script only guarantees you'll
be TOLD if you forgot (step 3's drift check is a hard abort here, not a
warning), not that the porting itself is automated. Phase 4b (de-
duplicating the true-shared files so there's nothing left to port) is
a separate, not-yet-done effort.

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
DEFAULT_AP_DIR = ROOT / "apworld"


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
                         help=f"Path to the AP world's own files (default: "
                              f"{DEFAULT_AP_DIR}, this repo's own apworld/ "
                              f"subfolder since the Phase 4 monorepo merge)")
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
    print(f"AP world source: {ap_dir}")
    print("=" * 70)

    print("\n[1/5] Regenerating extracted_locations.py (this repo's own, and "
          "apworld/'s copy) from data/locations.csv...")
    run([sys.executable, "tools/generate.py"], cwd=ROOT)
    (ap_dir / "data").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "data" / "locations.csv", ap_dir / "data" / "locations.csv")
    run([sys.executable, "tools/generate.py"], cwd=ap_dir)

    print("\n[2/5] Drift check between root-level and apworld/-level shared "
          "files -- BLOCKING here (unlike build_apworld.bat's own non-fatal "
          "copy of this step), since this script is meant to run right "
          "before a real release.")
    run([sys.executable, "tools/check_apworld_sync.py", "--ap-dir", str(ap_dir)], cwd=ROOT)

    print("\n[3/5] Compile-checking the whole repo (apworld/ included "
          "automatically -- git ls-files from repo root already covers it, "
          "no separate check needed post-merge)...")
    compile_check(ROOT)

    print("\n[4/5] Building shadowman.apworld...")
    run([sys.executable, "build_apworld.py", "--source", str(ap_dir),
         "--output", str(ROOT / "dist" / "apworld" / "shadowman.apworld")], cwd=ROOT)

    if args.skip_companion:
        print("\n[5/5] Skipping AP Companion build (--skip-companion).")
    else:
        print("\n[5/5] Building the AP Companion exe (this installs/upgrades "
              "pyinstaller, pywebview, pyyaml, keystone-engine, capstone -- "
              "can take a few minutes)...")
        # Absolute path, not a bare "build_ap_gui.bat" -- `cmd /c` launched
        # from a subprocess (as opposed to typed directly into a cmd.exe
        # session) doesn't reliably resolve a relative script name against
        # cwd on this setup; confirmed live (first real run of this script
        # with --skip-companion omitted failed with "'build_ap_gui.bat' is
        # not recognized"). Absolute path sidesteps the resolution
        # question entirely.
        run(["cmd", "/c", str(ROOT / "build_ap_gui.bat")], cwd=ROOT)

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
  3. Commit + push this repo's source changes (one repo now, post-merge).
  4. Tag it apworld-vX.Y.Z (AP world/Companion's own track, separate
     from this repo's own standalone vX.Y.Z tags -- see RELEASING.md
     for the convention).
  5. Write release notes and actually publish (gh release create) --
     this script builds artifacts, it doesn't decide what changed or
     announce anything.
""")


if __name__ == "__main__":
    main()
