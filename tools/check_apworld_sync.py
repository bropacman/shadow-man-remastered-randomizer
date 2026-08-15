"""
tools/check_apworld_sync.py
============================
Compares this repo's own copies of the files that are ALSO manually
duplicated (by hand, copy-pasted) into the Archipelago world checkout's
worlds/shadowman/ folder, and reports which pairs have drifted.

This repo and the AP world keep independent copies of about two dozen
files that must stay logically in sync but are never synced by a Python
import -- see this repo's own CLAUDE.md "Cross-Repo Drift & Port Review"
section for the real, shipped bugs that has already caused (the
UNVERIFIED_LOCS filter missing from the AP world's fill.py for weeks,
the True Form enemy-shuffle fix landing in one copy of enemy_randomizer.py
and not the other, etc). This script doesn't fix anything -- it's a
read-only pre-release check. Run it before cutting an apworld build
(build_apworld.bat already runs it automatically, non-fatally) and
eyeball anything it flags before shipping.

Two categories of pair, because not every same-named file is meant to be
a byte-for-byte copy:

  NEAR_IDENTICAL -- small modules with no AP-specific branching of their
      own (the cosmetic randomizers, gad_pickup_patch, levels_txt_patcher,
      sprint_patch, death_penalty_patch, the two extracted_*.py data
      dumps). These are expected to either match exactly or differ by at
      most a couple of trivial lines. ANY real drift here is flagged as a
      warning -- this is exactly the class of silent bug this script
      exists to catch.

  EXPECTED_DIVERGENT -- files that legitimately carry a lot of AP-only
      logic on top of shared core (access_rules.py, constants.py, fill.py,
      regions.py, locations.py, cadeaux_patch.py, kpf_handler.py,
      save_path_patch.py, setup_gad_records.py, soul_threshold_patch.py).
      These are NEVER expected to be identical -- reported for visibility
      only, never as a warning. Use --diff <name> to actually read one and
      judge for yourself whether a given change ported correctly.

Usage:
    python tools/check_apworld_sync.py
    python tools/check_apworld_sync.py --ap-dir "D:\\Archipelago\\worlds\\shadowman"
    python tools/check_apworld_sync.py --diff fill.py      # full unified diff
    python tools/check_apworld_sync.py --quiet             # warnings only, for CI/build-script use
"""

from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

# Default location of the Archipelago checkout's worlds/shadowman/ folder.
# Override with --ap-dir if your checkout lives somewhere else.
DEFAULT_AP_DIR = r"C:\Users\jonat\Documents\Archipelago-0.6.7\worlds\shadowman"

# (path relative to this repo's root, path relative to worlds/shadowman/)
NEAR_IDENTICAL: list[tuple[str, str]] = [
    ("death_penalty_patch.py", "death_penalty_patch.py"),
    ("extracted_enemy_locations.py", "extracted_enemy_locations.py"),
    ("extracted_locations.py", "extracted_locations.py"),
    ("gad_pickup_patch.py", "gad_pickup_patch.py"),
    ("health_patch.py", "health_patch.py"),
    ("patchers/levels_txt_patcher.py", "levels_txt_patcher.py"),
    ("randomizers/ambient_randomizer.py", "ambient_randomizer.py"),
    ("randomizers/enemy_randomizer.py", "enemy_randomizer.py"),
    ("randomizers/music_randomizer.py", "music_randomizer.py"),
    ("randomizers/sfx_randomizer.py", "sfx_randomizer.py"),
    ("randomizers/sky_randomizer.py", "sky_randomizer.py"),
    ("sprint_patch.py", "sprint_patch.py"),
]

EXPECTED_DIVERGENT: list[tuple[str, str]] = [
    ("access_rules.py", "access_rules.py"),
    ("cadeaux_patch.py", "cadeaux_patch.py"),
    ("constants.py", "constants.py"),
    ("fill.py", "fill.py"),
    ("kpf_handler.py", "kpf_handler.py"),
    ("locations.py", "locations.py"),
    ("regions.py", "regions.py"),
    ("save_path_patch.py", "save_path_patch.py"),
    ("setup_gad_records.py", "setup_gad_records.py"),
    ("soul_threshold_patch.py", "soul_threshold_patch.py"),
]

ALL_PAIRS = {name: (repo_rel, ap_rel, "near-identical")
             for repo_rel, ap_rel in NEAR_IDENTICAL
             for name in (Path(repo_rel).name,)}
ALL_PAIRS.update({Path(repo_rel).name: (repo_rel, ap_rel, "expected-divergent")
                   for repo_rel, ap_rel in EXPECTED_DIVERGENT})


def _load(path: Path) -> tuple[bytes, list[str]] | None:
    if not path.exists():
        return None
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace").splitlines()
    return raw, text


# The AP world is a real Python package (worlds.shadowman), so every module
# it imports from a sibling in this same list has to use a package-relative
# `from .foo import ...` instead of this repo's plain `from foo import ...`.
# That substitution alone accounts for most of the line-level "differences"
# in the near-identical set and is NOT drift -- it's required for the AP
# copy to import at all. Normalize it away before deciding whether a pair
# needs a warning; --diff still shows the real, un-normalized text.
_RELATIVE_IMPORT_RE = __import__("re").compile(r"^(\s*from )\.(\w)")


def _normalize_relative_imports(lines: list[str]) -> list[str]:
    return [_RELATIVE_IMPORT_RE.sub(r"\1\2", line) for line in lines]


def _diff_line_count(a: list[str], b: list[str]) -> int:
    diff = difflib.unified_diff(a, b, lineterm="")
    return sum(1 for l in diff if l.startswith(("+", "-")) and not l.startswith(("+++", "---")))


def check(repo_dir: Path, ap_dir: Path, quiet: bool) -> int:
    """Returns the number of NEAR_IDENTICAL pairs that show real drift."""
    warnings = 0
    rows: list[tuple[str, str, str]] = []  # (name, category, status)

    for repo_rel, ap_rel, category in [
        (r, a, "near-identical") for r, a in NEAR_IDENTICAL
    ] + [
        (r, a, "expected-divergent") for r, a in EXPECTED_DIVERGENT
    ]:
        repo_path = repo_dir / repo_rel
        ap_path = ap_dir / ap_rel
        repo_loaded = _load(repo_path)
        ap_loaded = _load(ap_path)

        if repo_loaded is None or ap_loaded is None:
            missing_side = "repo" if repo_loaded is None else "AP world"
            rows.append((repo_rel, category, f"MISSING on {missing_side} side ({repo_path if repo_loaded is None else ap_path})"))
            if category == "near-identical":
                warnings += 1
            continue

        raw_repo, text_repo = repo_loaded
        raw_ap, text_ap = ap_loaded

        if raw_repo == raw_ap:
            rows.append((repo_rel, category, "identical"))
            continue

        changed = _diff_line_count(text_repo, text_ap)

        if category == "near-identical":
            # Re-check after normalizing away the expected relative-import
            # rewrite -- a pair that's only different because of `.foo`
            # vs `foo` imports isn't drift, it's the AP package doing what
            # it has to do.
            normalized_changed = _diff_line_count(
                _normalize_relative_imports(text_repo), _normalize_relative_imports(text_ap)
            )
            if normalized_changed == 0:
                rows.append((repo_rel, category, f"identical (only relative-import rewrite, {changed} raw diff-line(s))"))
                continue
            warnings += 1
            rows.append((repo_rel, category, f"DRIFT -- {normalized_changed} differing diff-line(s) beyond the expected relative-import rewrite (sizes {len(raw_repo)}B/{len(raw_ap)}B)"))
        else:
            rows.append((repo_rel, category, f"differs (expected) -- {changed} diff-line(s), sizes {len(raw_repo)}B/{len(raw_ap)}B"))

    name_w = max(len(r[0]) for r in rows) + 2
    for name, category, status in rows:
        if quiet and not status.startswith("DRIFT") and "MISSING" not in status:
            continue
        marker = "!!" if status.startswith("DRIFT") or "MISSING" in status else "  "
        print(f"{marker} {name:<{name_w}} [{category:<18}] {status}")

    print()
    if warnings:
        print(f"{warnings} file(s) in the near-identical set have drifted -- "
              f"review with --diff <filename> before shipping the apworld.")
    else:
        print("No unexpected drift in the near-identical set.")
    return warnings


def show_diff(repo_dir: Path, ap_dir: Path, name: str) -> int:
    if name not in ALL_PAIRS:
        print(f"'{name}' isn't a known repo<->apworld pair. Known names:")
        for n in sorted(ALL_PAIRS):
            print(f"  {n}")
        return 1
    repo_rel, ap_rel, _ = ALL_PAIRS[name]
    repo_path, ap_path = repo_dir / repo_rel, ap_dir / ap_rel
    loaded_repo, loaded_ap = _load(repo_path), _load(ap_path)
    if loaded_repo is None or loaded_ap is None:
        print(f"Can't diff -- missing file(s):\n  repo: {repo_path} ({'ok' if loaded_repo else 'MISSING'})\n  ap:   {ap_path} ({'ok' if loaded_ap else 'MISSING'})")
        return 1
    _, text_repo = loaded_repo
    _, text_ap = loaded_ap
    diff = difflib.unified_diff(text_repo, text_ap, fromfile=str(repo_path), tofile=str(ap_path), lineterm="")
    for line in diff:
        print(line)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo-dir", type=Path, default=Path(__file__).resolve().parent.parent,
                         help="Root of this repo (default: parent of tools/)")
    parser.add_argument("--ap-dir", type=Path, default=Path(DEFAULT_AP_DIR),
                         help=f"Path to worlds/shadowman/ (default: {DEFAULT_AP_DIR})")
    parser.add_argument("--diff", metavar="FILENAME",
                         help="Print a full unified diff for one pair (e.g. fill.py) and exit")
    parser.add_argument("--quiet", action="store_true",
                         help="Only print rows that need attention (drift / missing)")
    args = parser.parse_args()

    if not args.ap_dir.is_dir():
        print(f"AP world folder not found: {args.ap_dir}\n"
              f"Pass --ap-dir if your Archipelago checkout lives somewhere else.")
        sys.exit(2)

    if args.diff:
        sys.exit(show_diff(args.repo_dir, args.ap_dir, args.diff))

    warnings = check(args.repo_dir, args.ap_dir, args.quiet)
    sys.exit(1 if warnings else 0)


if __name__ == "__main__":
    main()
