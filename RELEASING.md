# Releasing

This repo produces three independent shippable artifacts from one shared
codebase: the **standalone randomizer** (a single .exe for players who
just want to randomize their own single-player game), the **AP world**
(`shadowman.apworld`, for players using Archipelago multiworld), and the
**AP Companion** (a single .exe wrapping `ap_gui.py` — generates a
player's YAML and applies their `.apshadowman` seed, no Python install
required). They have different audiences and, for the standalone vs. the
AP-facing pair, different distribution channels — see the discussion
that led here if you want the reasoning. This file is the practical "how."

The AP Companion ships alongside the AP world (same `COMPANION_VERSION`,
same release) rather than the standalone exe, even though its source
lives in this repo next to `gui.py` — it only ever talks to AP seeds
(`.apshadowman` files), never the standalone's own seed format.

## The three builds

| | Standalone | AP world | AP Companion |
|---|---|---|---|
| Build script | `build.bat` | `build_apworld.bat` | `build_ap_gui.bat` |
| Output | `dist\standalone\shadow_man_randomizer.exe` | `dist\apworld\shadowman.apworld` | `dist\ap_companion\shadow_man_ap_companion.exe` |
| Entry point | `gui.py` / `patcher.py` | `worlds/shadowman/__init__.py` (in your Archipelago checkout) + `ap_patcher.py`/`ap_gui.py` (in this repo) | `ap_gui.py` (same file the AP world's own `ap_patcher.py`/`apply_ap_seed.py` path uses) |
| Version lives in | `gui.py`'s embedded HTML (`v1.2.0` as of this writing) | `ap_gui.py`'s `COMPANION_VERSION` (`v0.1.1` as of this writing — its own independent track, starting over at 0.x since this is the first release with the AP Companion exe and it's still genuinely beta) — bump this for companion-app changes; check whether the AP world itself should carry its own version marker separately | Same `COMPANION_VERSION` as the AP world row — one bump covers both |
| Ships to | GitHub release / itch.io / wherever standalone players look | Archipelago's world list / Discord / manually to players who already run Archipelago | Same place as the AP world — the two are meant to be grabbed together |

`dist/` and `build/` are both gitignored — nothing here is meant to be
committed, only built fresh and uploaded.

## Before every release: check for drift

This repo and the Archipelago world checkout keep independent, hand-copied
versions of about two dozen files (`fill.py`, `enemy_randomizer.py`, the
EXE patch modules, etc.) that have to stay logically in sync but are never
synced by a Python import. This has caused real, shipped bugs before (see
CLAUDE.md's "Cross-Repo Drift & Port Review" section). Run:

```
python tools\check_apworld_sync.py
```

`build_apworld.bat` already runs this automatically as a non-fatal
pre-flight step — read its output before you actually ship. Anything it
flags with `!!` is worth a look with `--diff <filename>` before cutting
the apworld. Files it doesn't flag are either byte-identical or expected
to diverge (they carry real AP-specific logic on top of shared core) —
see the script's own docstring for the full breakdown of which is which.

## Cutting a standalone release

1. `build.bat` — installs/updates `pyinstaller`/`pywebview`/`pyyaml`,
   builds from `Shadow Man Randomizer.spec`, output lands in
   `dist\standalone\shadow_man_randomizer.exe`.
2. Bump the version string in `gui.py`'s HTML header if this is a real
   release (not just a local test build).
3. Zip the exe (plus README/LICENSE if you want them alongside) and
   upload as its own GitHub release asset, e.g.
   `shadow-man-randomizer-v1.1.6.zip`.

## Cutting an AP world release

Both the standalone randomizer and the AP world/Companion now live in
this one repo (`apworld/` — merged 2026-09-16 via `git subtree`, full
history preserved; see `docs/PHASE4_ARCHITECTURE_SCOPING.md`). No
second checkout to manage.

1. Bump `COMPANION_VERSION` in `ap_gui.py` if this is a real release —
   covers both the AP world row and the AP Companion exe below, one bump.
   Do this *before* the next step so the built manifest/UI embed the
   right version.
2. `python release_apworld.py` (`--skip-companion` for a faster
   apworld-only iteration; `--ap-dir` only if you're building from
   somewhere other than this repo's own `apworld/`, e.g. a separate
   Archipelago checkout during local dev) — formalizes what used to be
   ~15 manual steps (added 2026-09-16, see
   `docs/REPO_RESTRUCTURING_PLAN.md` Phase 3): regenerates
   `extracted_locations.py` (both this repo's own copy and `apworld/`'s),
   runs the drift check between root-level and `apworld/`-level shared
   files as a **hard abort** (not `build_apworld.bat`'s own non-fatal
   version of this step — reminder: the "expected-divergent" files this
   checks are genuinely, permanently different implementations by
   design, not accidental drift to eliminate — see
   `docs/PHASE4_ARCHITECTURE_SCOPING.md`'s correction on this),
   compile-checks the whole repo, then builds
   `dist\apworld\shadowman.apworld` and
   `dist\ap_companion\shadow_man_ap_companion.exe`. Prints a checklist
   of what's still a deliberate manual step (this file's own remaining
   items below) rather than doing them for you.
3. Post/upload `shadowman.apworld` and `shadow_man_ap_companion.exe`
   together wherever AP players get them from (rename either to include
   the version if you want, e.g. `shadowman-v0.1.1.apworld` — the AP
   loader doesn't care about the filename, only the folder name inside
   the zip).

`build_apworld.bat`/`build_ap_gui.bat` still exist and still work
standalone (e.g. if you only need one artifact rebuilt) — `release_
apworld.py` calls the same underlying `build_apworld.py`/`build_ap_gui.bat`
internally, it doesn't replace them, just the manual plumbing *around*
them.

### Tag convention

One repo, two independent version tracks (formalized 2026-09-16,
updated the same day once the AP world merged in):

- **Standalone exe**: `vX.Y.Z` tags — e.g. `v1.2.0`. Its own track,
  bumped in `gui.py`'s HTML header.
- **AP world / AP Companion**: `apworld-vX.Y.Z` tags — its own
  independent `0.x` track matching `COMPANION_VERSION` (`ap_gui.py`).
  Deliberately a different shape from the standalone exe's plain
  `vX.Y.Z`, even though both now live in the same repo's tag namespace
  — otherwise `git tag -l` would be ambiguous about which artifact a
  given tag describes. (Pre-merge, `shadow-man-remastered-ap-world` had
  its own separate `v0.1.0`/`v0.1.1` tags in its own repo; those stay as
  historical record there, `apworld-vX.Y.Z` here is the track going
  forward.)

## What this does *not* cover yet

This is the "light touch" pass: build/release tooling only, no source
reorganization. `access_rules.py`/`fill.py`/`regions.py`/`constants.py`/etc.
still live flush at the repo root and are still hand-copied into the AP
world checkout — `tools/check_apworld_sync.py` makes that relationship
visible and checkable, it doesn't remove it. A deeper reorg (e.g. pulling
genuinely shared modules into their own package, or generating the AP
world's copies from this repo's instead of hand-copying) is a separate,
higher-risk pass for later.
