"""
build_all.py
============
One command for every rebuild after a code or data change, in dependency
order. Wraps the existing scripts; it doesn't replace them.

  1. naming   apply data/naming_log.csv, if there is one (tools/apply_naming_log.py)
  2. data     regenerate both extracted_locations.py from data/locations.csv
  3. checks   drift check (blocking) + compile check of every tracked .py
  4. dll      rebuild the overlay DLL, only if overlay_dll/src is newer than
              apworld/ShadowManOverlay.dll (or --dll); copied into apworld/
  5. apworld  dist/apworld/shadowman.apworld
  6. companion dist/ap_companion/shadow_man_ap_companion.exe (build_ap_gui.bat)
  7. standalone dist/standalone/shadow_man_randomizer.exe (build.bat)

Builds package whatever is on disk, so uncommitted changes are listed
before anything runs. Nothing is committed, pushed, tagged or published.

Usage:
    python build_all.py                      # everything
    python build_all.py --only apworld       # just these steps (comma-separated)
    python build_all.py --skip standalone    # everything but these
    python build_all.py --dll                # force a DLL rebuild
    python build_all.py --no-dll             # never rebuild the DLL
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from release_apworld import run, compile_check

ROOT = Path(__file__).resolve().parent
AP_DIR = ROOT / "apworld"
DLL_SRC = ROOT / "overlay_dll"
DLL_BUILD = DLL_SRC / "build"
DLL_OUT = DLL_BUILD / "Release" / "ShadowManOverlay.dll"
DLL_SHIPPED = AP_DIR / "ShadowManOverlay.dll"
NAMING_LOG = ROOT / "data" / "naming_log.csv"

STEPS = ["naming", "data", "checks", "dll", "apworld", "companion", "standalone"]
ARTIFACTS = {
    "apworld":    ROOT / "dist" / "apworld" / "shadowman.apworld",
    "companion":  ROOT / "dist" / "ap_companion" / "shadow_man_ap_companion.exe",
    "standalone": ROOT / "dist" / "standalone" / "shadow_man_randomizer.exe",
    "dll":        DLL_SHIPPED,
}


def run_bat(name: str) -> None:
    # stdin=DEVNULL lets the .bat's trailing `pause` return instead of
    # waiting for a key press.
    print(f"\n$ {name}")
    result = subprocess.run(["cmd", "/c", str(ROOT / name)], cwd=ROOT,
                            stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        sys.exit(f"\nABORTED: {name} failed with exit code {result.returncode}")


def dll_is_stale() -> bool:
    if not DLL_SHIPPED.exists():
        return True
    built = DLL_SHIPPED.stat().st_mtime
    sources = [DLL_SRC / "CMakeLists.txt", *(DLL_SRC / "src").rglob("*")]
    return any(p.is_file() and p.stat().st_mtime > built for p in sources)


def build_dll() -> None:
    if not shutil.which("cmake"):
        sys.exit("ABORTED: cmake not found on PATH (needed for the overlay DLL).")
    run(["cmake", "-S", str(DLL_SRC), "-B", str(DLL_BUILD), "-A", "x64"], cwd=ROOT)
    run(["cmake", "--build", str(DLL_BUILD), "--config", "Release"], cwd=ROOT)
    if not DLL_OUT.exists():
        sys.exit(f"ABORTED: DLL build finished but {DLL_OUT} is missing.")
    shutil.copy2(DLL_OUT, DLL_SHIPPED)
    print(f"  copied -> {DLL_SHIPPED.relative_to(ROOT)}")


def warn_uncommitted() -> None:
    status = subprocess.run(["git", "status", "--short"], cwd=ROOT,
                            capture_output=True, text=True).stdout.rstrip()
    if status:
        print("NOTE: uncommitted changes on disk will be included in these builds:")
        for line in status.splitlines():
            print(f"    {line}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Rebuild everything in dependency order.")
    ap.add_argument("--only", help="comma-separated steps to run: " + ",".join(STEPS))
    ap.add_argument("--skip", help="comma-separated steps to skip")
    dll = ap.add_mutually_exclusive_group()
    dll.add_argument("--dll", action="store_true", help="force the DLL rebuild")
    dll.add_argument("--no-dll", action="store_true", help="never rebuild the DLL")
    args = ap.parse_args()
    # Child scripts print ✓/→; the default Windows console codepage can't.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    def parse(s):
        names = [x.strip() for x in s.split(",") if x.strip()] if s else []
        bad = [n for n in names if n not in STEPS]
        if bad:
            ap.error(f"unknown step(s) {bad}; choose from {STEPS}")
        return names

    steps = parse(args.only) or list(STEPS)
    steps = [s for s in steps if s not in parse(args.skip)]
    if args.no_dll and "dll" in steps:
        steps.remove("dll")

    print("=" * 70)
    print("Shadow Man Remastered -- build all")
    print("Steps: " + ", ".join(steps))
    print("=" * 70)
    warn_uncommitted()
    t0 = time.time()
    done = []

    def header(step, text):
        print(f"\n[{len(done) + 1}/{len(steps)}] {step}: {text}")

    if "naming" in steps:
        if NAMING_LOG.exists():
            header("naming", "applying data/naming_log.csv")
            run([sys.executable, "tools/apply_naming_log.py"], cwd=ROOT)
        else:
            header("naming", "no data/naming_log.csv, nothing to apply")
        done.append("naming")

    if "data" in steps:
        header("data", "regenerating extracted_locations.py (root and apworld/)")
        run([sys.executable, "tools/generate.py"], cwd=ROOT)
        (AP_DIR / "data").mkdir(exist_ok=True)
        shutil.copy2(ROOT / "data" / "locations.csv", AP_DIR / "data" / "locations.csv")
        run([sys.executable, "tools/generate.py"], cwd=AP_DIR)
        done.append("data")

    if "checks" in steps:
        header("checks", "drift check (blocking) and compile check")
        run([sys.executable, "tools/check_apworld_sync.py", "--ap-dir", str(AP_DIR)], cwd=ROOT)
        compile_check(ROOT)
        done.append("checks")

    if "dll" in steps:
        if args.dll or dll_is_stale():
            header("dll", "overlay_dll/src changed, rebuilding ShadowManOverlay.dll")
            build_dll()
        else:
            header("dll", "up to date, skipped (use --dll to force)")
        done.append("dll")

    if "apworld" in steps:
        header("apworld", "packaging shadowman.apworld")
        run([sys.executable, "build_apworld.py", "--source", str(AP_DIR),
             "--output", str(ARTIFACTS["apworld"])], cwd=ROOT)
        done.append("apworld")

    if "companion" in steps:
        header("companion", "building the AP Companion exe (build_ap_gui.bat)")
        run_bat("build_ap_gui.bat")
        done.append("companion")

    if "standalone" in steps:
        header("standalone", "building the standalone exe (build.bat)")
        run_bat("build.bat")
        done.append("standalone")

    print("\n" + "=" * 70)
    print(f"Done in {time.time() - t0:.0f}s. Artifacts:")
    for step, path in ARTIFACTS.items():
        if step in done and path.exists():
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
            print(f"  {path.relative_to(ROOT)}  ({stamp})")
    print("\nNot automated: version bumps, commit/tag, sync_apworld_mirror.py, "
          "publishing (see RELEASING.md).")


if __name__ == "__main__":
    main()
