"""
publish_release.py
==================
Publish already-built releases in one command (build first with
build_all.py). Shows the plan and asks before doing anything.

  --ap          sync apworld/ to the mirror repo (sync_apworld_mirror.py),
                tag apworld-vX.Y.Z here, and create release vX.Y.Z on the
                mirror with shadowman.apworld, the AP Companion exe and the
                example ShadowManRemastered.yaml
  --standalone  tag vX.Y.Z here and create the release here with the
                standalone exe

Versions come from COMPANION_VERSION (ap_gui.py) and RANDOMIZER_VERSION
(constants.py), the same ones build_all.py sets.

Refuses to run unless: on main, working tree clean, main matches
origin/main, the release files exist, the built apworld's manifest
matches COMPANION_VERSION, and the tag/release doesn't exist yet.

Usage:
    python publish_release.py --ap --notes-ap dist/release/notes-ap-v0.1.5.md
    python publish_release.py --standalone --notes-standalone notes.md --title "Headline features"
    python publish_release.py --ap ... --dry-run     # preflight + plan only
    python publish_release.py --ap ... --yes         # skip the confirmation
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from build_all import read_version, ARTIFACTS, EXAMPLE_YAML, RELEASE_DIR, MIRROR_REPO, STANDALONE_REPO

ROOT = Path(__file__).resolve().parent


def sh(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=check)


def run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}")
    if subprocess.run(cmd, cwd=ROOT).returncode != 0:
        sys.exit(f"\nABORTED: step failed: {' '.join(cmd)}\n"
                 "Steps before it already happened; re-run after fixing (finished "
                 "steps are detected and skipped).")


def tag_exists(tag: str) -> bool:
    return bool(sh(["git", "tag", "-l", tag]).stdout.strip())


def release_exists(tag: str, repo: str) -> bool:
    return sh(["gh", "release", "view", tag, "--repo", repo], check=False).returncode == 0


def preflight(args) -> list[str]:
    problems = []
    if sh(["git", "branch", "--show-current"]).stdout.strip() != "main":
        problems.append("not on main (git switch main)")
    if sh(["git", "status", "--porcelain", "--untracked-files=no"]).stdout.strip():
        problems.append("working tree has uncommitted changes")
    sh(["git", "fetch", "-q", "origin", "main", "--tags"], check=False)
    if sh(["git", "rev-parse", "HEAD"]).stdout != sh(["git", "rev-parse", "origin/main"]).stdout:
        problems.append("local main differs from origin/main (git pull)")
    if sh(["gh", "auth", "status"], check=False).returncode != 0:
        problems.append("gh is not logged in (gh auth login)")

    if args.ap:
        ver = read_version("ap")
        for key in ("apworld", "companion"):
            if not ARTIFACTS[key].exists():
                problems.append(f"missing {ARTIFACTS[key].relative_to(ROOT)} (run build_all.py)")
        if ARTIFACTS["apworld"].exists():
            with zipfile.ZipFile(ARTIFACTS["apworld"]) as z:
                name = next((n for n in z.namelist() if n.endswith("archipelago.json")), None)
                built = json.loads(z.read(name)).get("world_version") if name else None
            if built != ver[1:]:
                problems.append(f"built apworld is {built}, COMPANION_VERSION is {ver} (rebuild)")
        if not args.notes_ap or not Path(args.notes_ap).exists():
            problems.append("--notes-ap file missing")
        if release_exists(ver, MIRROR_REPO):
            problems.append(f"release {ver} already exists on {MIRROR_REPO}")

    if args.standalone:
        ver = read_version("standalone")
        if not ARTIFACTS["standalone"].exists():
            problems.append(f"missing {ARTIFACTS['standalone'].relative_to(ROOT)} (run build_all.py)")
        if not args.notes_standalone or not Path(args.notes_standalone).exists():
            problems.append("--notes-standalone file missing")
        if not args.title:
            problems.append("--title missing (the part after the version, e.g. \"Sprint & Saves\")")
        if release_exists(ver, STANDALONE_REPO):
            problems.append(f"release {ver} already exists on {STANDALONE_REPO}")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description="Publish built releases.")
    ap.add_argument("--ap", action="store_true", help="publish the AP world + Companion")
    ap.add_argument("--standalone", action="store_true", help="publish the standalone exe")
    ap.add_argument("--notes-ap", help="markdown release notes for the AP release")
    ap.add_argument("--notes-standalone", help="markdown release notes for the standalone release")
    ap.add_argument("--title", help="standalone release headline (after the version)")
    ap.add_argument("--dry-run", action="store_true", help="preflight and plan only")
    ap.add_argument("--yes", action="store_true", help="don't ask for confirmation")
    args = ap.parse_args()
    if not (args.ap or args.standalone):
        ap.error("choose --ap and/or --standalone")

    problems = preflight(args)
    if problems:
        print("Not ready to publish:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)

    plan: list[tuple[str, list[str]]] = []
    if args.ap:
        ver = read_version("ap")
        tag = f"apworld-{ver}"
        assets = [str(p.relative_to(ROOT))
                  for p in (ARTIFACTS["apworld"], ARTIFACTS["companion"], EXAMPLE_YAML)]
        plan.append(("sync apworld/ to the mirror",
                     [sys.executable, "sync_apworld_mirror.py"]))
        if not tag_exists(tag):
            plan.append((f"tag {tag}", ["git", "tag", tag]))
        plan.append((f"push tag {tag}", ["git", "push", "origin", tag]))
        plan.append((f"create {ver} on {MIRROR_REPO}",
                     ["gh", "release", "create", ver, *assets, "--repo", MIRROR_REPO,
                      "--prerelease", "--title", f"Shadow Man Remastered AP World {ver} - Beta",
                      "--notes-file", args.notes_ap]))
    if args.standalone:
        ver = read_version("standalone")
        if not tag_exists(ver):
            plan.append((f"tag {ver}", ["git", "tag", ver]))
        plan.append((f"push tag {ver}", ["git", "push", "origin", ver]))
        plan.append((f"create {ver} on {STANDALONE_REPO}",
                     ["gh", "release", "create", ver, str(ARTIFACTS["standalone"].relative_to(ROOT)),
                      "--repo", STANDALONE_REPO,
                      "--title", f"Shadow Man Remastered Randomizer {ver} - {args.title}",
                      "--notes-file", args.notes_standalone]))

    print("Preflight OK. Plan:")
    for i, (label, cmd) in enumerate(plan, 1):
        print(f"  {i}. {label}")
    if args.dry_run:
        print("\n--dry-run: nothing published. Mirror diff preview:")
        run([sys.executable, "sync_apworld_mirror.py", "--dry-run"]) if args.ap else None
        return
    if not args.yes and input("\nPublish now? This is public. [y/N] ").strip().lower() != "y":
        sys.exit("Cancelled; nothing published.")

    for label, cmd in plan:
        print(f"\n== {label}")
        run(cmd)
    print("\nPublished. Delete any stale draft releases from the repo's Releases page.")


if __name__ == "__main__":
    main()
