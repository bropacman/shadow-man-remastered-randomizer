"""
build_all.py
============
One command for every rebuild after a code or data change, in dependency
order. Wraps the existing scripts; it doesn't replace them.

  0. version  --ap-version / --standalone-version: set COMPANION_VERSION
              (ap_gui.py; also the apworld manifest) / RANDOMIZER_VERSION
              (constants.py) before anything is built
  1. naming   apply data/naming_log.csv, if there is one (tools/apply_naming_log.py)
  2. data     regenerate both extracted_locations.py from data/locations.csv
  3. checks   drift check (blocking) + compile check of every tracked .py
  4. dll      rebuild the overlay DLL, only if overlay_dll/src is newer than
              apworld/ShadowManOverlay.dll (or --dll); copied into apworld/
  5. apworld  dist/apworld/shadowman.apworld
  6. companion dist/ap_companion/shadow_man_ap_companion.exe (build_ap_gui.bat)
  7. standalone dist/standalone/shadow_man_randomizer.exe (build.bat)
  8. package  versioned release folders under dist/release/, plus the tag and
              `gh release create` commands to run yourself; opens dist/release/
              in Explorer when done (--no-open to skip)

Builds package whatever is on disk, so uncommitted changes are listed
before anything runs. Nothing is committed, pushed, tagged or published.

Usage:
    python build_all.py                          # everything, current versions
    python build_all.py --ap-version patch       # v0.1.4 -> v0.1.5, then build
    python build_all.py --standalone-version 1.3.0
    python build_all.py --only apworld           # just these steps (comma-separated)
    python build_all.py --skip standalone        # everything but these
    python build_all.py --dll / --no-dll         # force / never rebuild the DLL
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from release_apworld import run, compile_check

ROOT = Path(__file__).resolve().parent
AP_DIR = ROOT / "apworld"
DLL_SRC = ROOT / "overlay_dll"
DLL_BUILD = DLL_SRC / "build"
DLL_OUT = DLL_BUILD / "Release" / "ShadowManOverlay.dll"
DLL_SHIPPED = AP_DIR / "ShadowManOverlay.dll"
NAMING_LOG = ROOT / "data" / "naming_log.csv"
RELEASE_DIR = ROOT / "dist" / "release"
MIRROR_REPO = "bropacman/shadow-man-remastered-ap-world"
STANDALONE_REPO = "bropacman/shadow-man-remastered-randomizer"

STEPS = ["naming", "data", "checks", "dll", "apworld", "companion", "standalone", "package"]
ARTIFACTS = {
    "apworld":    ROOT / "dist" / "apworld" / "shadowman.apworld",
    "companion":  ROOT / "dist" / "ap_companion" / "shadow_man_ap_companion.exe",
    "standalone": ROOT / "dist" / "standalone" / "shadow_man_randomizer.exe",
    "dll":        DLL_SHIPPED,
}

# (file, constant) holding each track's version, e.g. COMPANION_VERSION = "v0.1.4"
VERSION_FILES = {
    "ap":         (ROOT / "ap_gui.py", "COMPANION_VERSION"),
    "standalone": (ROOT / "constants.py", "RANDOMIZER_VERSION"),
}


# ── Versions ──────────────────────────────────────────────────────────────────

def _version_re(const: str) -> re.Pattern:
    return re.compile(rf'^({const}\s*=\s*")v(\d+)\.(\d+)\.(\d+)(")', re.M)


def read_version(track: str) -> str:
    path, const = VERSION_FILES[track]
    m = _version_re(const).search(path.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f"ABORTED: no {const} = \"vX.Y.Z\" line in {path.name}")
    return f"v{m.group(2)}.{m.group(3)}.{m.group(4)}"


def resolve_version(current: str, spec: str) -> str:
    """spec is major/minor/patch or an explicit X.Y.Z (with or without v)."""
    major, minor, patch = (int(x) for x in current[1:].split("."))
    if spec == "major":
        return f"v{major + 1}.0.0"
    if spec == "minor":
        return f"v{major}.{minor + 1}.0"
    if spec == "patch":
        return f"v{major}.{minor}.{patch + 1}"
    if re.fullmatch(r"v?\d+\.\d+\.\d+", spec):
        return spec if spec.startswith("v") else f"v{spec}"
    sys.exit(f"ABORTED: version {spec!r} is not major/minor/patch or X.Y.Z")


def write_version(track: str, new: str) -> None:
    path, const = VERSION_FILES[track]
    raw = path.read_bytes().decode("utf-8")
    nums = new[1:].split(".")
    raw, n = _version_re(const).subn(
        lambda m: f"{m.group(1)}v{nums[0]}.{nums[1]}.{nums[2]}{m.group(5)}", raw, count=1)
    if n != 1:
        sys.exit(f"ABORTED: couldn't update {const} in {path.name}")
    path.write_bytes(raw.encode("utf-8"))


# ── Build helpers ─────────────────────────────────────────────────────────────

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


# ── Release packaging ─────────────────────────────────────────────────────────

def package(done: list[str]) -> list[str]:
    """Copy fresh artifacts into versioned release folders; return the
    commands to tag and publish them (printed, never run)."""
    cmds: list[str] = []
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    ap_ver, sa_ver = read_version("ap"), read_version("standalone")

    ap_files = [ARTIFACTS[k] for k in ("apworld", "companion") if k in done]
    if ap_files:
        # File names stay as-is: AP loads a world by its .apworld file name.
        out = RELEASE_DIR / f"shadow-man-ap-world-{ap_ver}"
        out.mkdir(parents=True, exist_ok=True)
        for f in ap_files:
            shutil.copy2(f, out / f.name)
        print(f"  {out.relative_to(ROOT)}\\  ({', '.join(f.name for f in ap_files)})")
        assets = " ".join(f'"{(out / f.name).relative_to(ROOT)}"' for f in ap_files)
        cmds += [
            f"git tag apworld-{ap_ver}",
            f"python sync_apworld_mirror.py",
            f'gh release create {ap_ver} {assets} --repo {MIRROR_REPO} --prerelease '
            f'--title "Shadow Man Remastered AP World {ap_ver} - Beta" --notes-file <notes.md>',
        ]

    if "standalone" in done:
        RELEASE_DIR.mkdir(parents=True, exist_ok=True)
        zip_path = RELEASE_DIR / f"shadow-man-randomizer-{sa_ver}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(ARTIFACTS["standalone"], ARTIFACTS["standalone"].name)
            for extra in ("README.md", "LICENSE"):
                if (ROOT / extra).exists():
                    z.write(ROOT / extra, extra)
        print(f"  {zip_path.relative_to(ROOT)}")
        cmds += [
            f"git tag {sa_ver}",
            f'gh release create {sa_ver} "{zip_path.relative_to(ROOT)}" --repo {STANDALONE_REPO} '
            f'--title "Shadow Man Remastered Randomizer {sa_ver} - <headline features>" '
            f"--notes-file <notes.md>",
        ]
    return cmds


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Rebuild everything in dependency order.")
    ap.add_argument("--only", help="comma-separated steps to run: " + ",".join(STEPS))
    ap.add_argument("--skip", help="comma-separated steps to skip")
    ap.add_argument("--ap-version", metavar="SPEC",
                    help="set the AP world/Companion version: major, minor, patch or X.Y.Z")
    ap.add_argument("--standalone-version", metavar="SPEC",
                    help="set the standalone version: major, minor, patch or X.Y.Z")
    dll = ap.add_mutually_exclusive_group()
    dll.add_argument("--dll", action="store_true", help="force the DLL rebuild")
    dll.add_argument("--no-dll", action="store_true", help="never rebuild the DLL")
    ap.add_argument("--no-open", action="store_true",
                    help="don't open dist/release/ in Explorer when done")
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

    for track, spec in (("ap", args.ap_version), ("standalone", args.standalone_version)):
        old = read_version(track)
        if spec:
            new = resolve_version(old, spec)
            write_version(track, new)
            print(f"Version ({track}): {old} -> {new}  ({VERSION_FILES[track][0].name})")
        else:
            print(f"Version ({track}): {old}")

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

    cmds = []
    if "package" in steps:
        header("package", f"release folders under {RELEASE_DIR.relative_to(ROOT)}")
        cmds = package(done)
        done.append("package")

    print("\n" + "=" * 70)
    print(f"Done in {time.time() - t0:.0f}s. Artifacts:")
    for step, path in ARTIFACTS.items():
        if step in done and path.exists():
            stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
            print(f"  {path.relative_to(ROOT)}  ({stamp})")
    if cmds:
        print("\nTo release, after testing and committing (not run for you):")
        for c in cmds:
            print(f"  {c}")
    else:
        print("\nNot automated: commit/tag, sync_apworld_mirror.py, publishing (see RELEASING.md).")

    if "package" in done:
        print(f"\nRelease files: {RELEASE_DIR}")
        if not args.no_open and hasattr(os, "startfile"):
            os.startfile(RELEASE_DIR)


if __name__ == "__main__":
    main()
