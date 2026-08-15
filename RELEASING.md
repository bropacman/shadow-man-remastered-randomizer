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
| Version lives in | `gui.py`'s embedded HTML (`v1.1.5` as of this writing) | `ap_gui.py`'s `COMPANION_VERSION` (`v1.2.2` as of this writing) — bump this for companion-app changes; check whether the AP world itself should carry its own version marker separately | Same `COMPANION_VERSION` as the AP world row — one bump covers both |
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

1. `build_apworld.bat` (optionally pass your Archipelago checkout's
   `worlds\shadowman` path as an argument if it's not at the default
   location baked into the script) — runs the drift check, then packages
   `dist\apworld\shadowman.apworld`.
2. Bump `COMPANION_VERSION` in `ap_gui.py` if this is a real release —
   covers both the AP world row and the AP Companion exe below, one bump.
3. `build_ap_gui.bat` — packages the AP Companion into
   `dist\ap_companion\shadow_man_ap_companion.exe`. Ship this alongside
   `shadowman.apworld`; per the README, it's the recommended way for
   players to generate their YAML and apply their seed.
4. Post/upload `shadowman.apworld` and `shadow_man_ap_companion.exe`
   together wherever AP players get them from (rename either to include
   the version if you want, e.g. `shadowman-v1.2.3.apworld` — the AP
   loader doesn't care about the filename, only the folder name inside
   the zip).

## What this does *not* cover yet

This is the "light touch" pass: build/release tooling only, no source
reorganization. `access_rules.py`/`fill.py`/`regions.py`/`constants.py`/etc.
still live flush at the repo root and are still hand-copied into the AP
world checkout — `tools/check_apworld_sync.py` makes that relationship
visible and checkable, it doesn't remove it. A deeper reorg (e.g. pulling
genuinely shared modules into their own package, or generating the AP
world's copies from this repo's instead of hand-copying) is a separate,
higher-risk pass for later.
