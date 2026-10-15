# Scope: make Shadow Man AP options resilient to hand-written YAMLs

Hand-off prompt for an agent. Read this whole file before starting.

## Background

`ap_gui.py` (this repo) blocks bad option combinations in its embedded JS
(`syncCadeauxConstraints`, `syncCadeauxGatedDependency`,
`syncCadeauxBundleDependency`, `syncUniqueRetractorKeysDependency`,
`syncDeathLinkThresholdDependency`, `syncCombineSfxDependency`,
`onGatePresetChange`, `onEnemiesChange` — roughly lines 940–1190). A YAML
written by hand, or in Archipelago's Options Creator, bypasses all of that.
The AP world must enforce the same rules itself, in `generate_early()`,
which is the only place that sees every YAML source *and* the resolved
value of options set to `random`.

## Where to work

The AP world source is `apworld/` in this repo
(`C:\Users\jonat\Documents\shadow-man-remastered-randomizer\apworld\`),
merged in via `git subtree` — plain tracked files, not a nested repo. It is
the only place to edit. Do **not** edit:

- `C:\Users\jonat\Documents\Archipelago\worlds\shadowman\` (outdated July copy)
- the installed `Archipelago-0.6.7\custom_worlds\shadowman.apworld` directly
- the public mirror repo (published only via `scripts/sync_apworld_mirror.py`, by Jon)

Some `CLAUDE.md` sections still say the source lives in
`Archipelago-0.6.7/worlds/shadowman/` or `Archipelago/worlds/shadowman/`;
those are stale — `apworld/` wins. Line numbers below refer to
`apworld/__init__.py` as of 2026-09-16 and may drift; search, don't trust them.

To test, build with `python scripts/build_apworld.py` (writes
`dist/apworld/shadowman.apworld`). Before installing it, copy the existing
`Archipelago-0.6.7\custom_worlds\shadowman.apworld` to
`shadowman.apworld.bak-<YYYYMMDD>` so Jon can roll back, then copy the new
build over it. Say in your report that you did this.

## Phase 1 — validation in `generate_early()`

Add one method, e.g. `_validate_options()`, called at the top of
`generate_early()`. For each rule below: correct the value in place and log
a warning that names the player and both options, e.g.
`logging.warning(f"Shadow Man ({self.player_name}): fogometers_cadeaux_required 50 raised to 500 (must be >= 5x altar_cadeaux_required).")`.
Use `self.options.<name>.value = ...` to correct. Do not raise unless a
rule says so.

| # | Condition | Action |
|---|---|---|
| 1 | `fogometers_cadeaux_required < 5 * altar_cadeaux_required` | Raise fog to `min(666, 5 * altar)`, warn. (Logic already uses this value silently around `__init__.py` line ~592; the change makes it visible and makes slot_data/spoiler agree.) |
| 2 | `cadeaux_gated_content` on, `cadeauxsanity` off | Set `cadeaux_gated_content` off, warn. |
| 3 | `cadeaux_bundle_size > 1`, `cadeauxsanity` off | Set to 1, warn. |
| 4 | `unique_retractor_keys` on and `"Retractor"` in `start_inventory` or `start_inventory_from_pool` | Replace each `"Retractor"` with a distinct key from `RETRACTOR_KEY_ITEM_NAMES`, chosen with `self.random`, warn. If more than 5 requested, cap at 5 and warn. (Today AP gives a flat Retractor that logic ignores — confirmed in `Main.py` ~line 161–175.) |
| 5 | `trap_bonus_count > 0` and all four `trap_bonus_*_enabled` off | Enable `trap_bonus_secrets_enabled`, warn. (Code already falls back to secrets around line ~733; make it explicit.) |
| 6 | `combine_voice_and_enemy_sfx` on, but `shuffle_voices` or `shuffle_enemies_sfx` off | Set combine off, warn. |
| 7 | `gate_preset == story` and `max_gate_sl` / `open_gates_n` not default | Warn only ("ignored with the story preset"). |
| 8 | `shuffle_enemies` off and any of `enemy_mode`, `enemy_mix_movement`, `enemy_uncap_counts` not default | Warn only. |
| 9 | `death_link` off and `death_link_threshold != 1` | Warn only. |

Order matters: run rules on resolved values (generate_early already sees
rolled randoms). Do not change any option names, ranges, or defaults in
this phase — existing YAMLs must keep working unchanged.

Check whether Universal Tracker re-generation (the `UT` passthrough paths
in `generate_early`, see `fill_slot_data`/`interpret_slot_data` usage) could
re-run validation against slot_data; corrections must be idempotent.

## Phase 2 — tooltips

For every option involved in a rule above, rewrite the first line of the
class docstring to state the dependency plainly, e.g.
"Only applies when Cadeaux Key Items is on." Keep the rest of the
docstring. Add `OptionGroup`s (Archipelago `Options.OptionGroup`, listed in
the World's `web.option_groups` / `option_groups`) so dependent options
sit under their parent: Cadeaux, Enemies, Audio, Traps & Bonuses, Gates.
Check the template YAML renders (run `Launcher.py` → "Generate Template
Options" or `python -c "from Options import generate_yaml_templates; ..."`).

## Out of scope

- Merging options into combined Choices (e.g. `cadeaux_mode`) — later phase,
  needs a migration plan for existing YAMLs.
- Changing `ap_gui.py` behaviour. Optional stretch only if Phase 1–2 are
  done: expose the Python rule list so the GUI could reuse the same messages.
- `ap_patcher.py`, `client.py`, the standalone `patcher.py`/`gui.py`.
- Committing, running `scripts/sync_apworld_mirror.py`, or making a release — Jon
  reviews and does those.

## Verification (required, report results honestly)

1. `python -m py_compile` on every touched file.
2. Build one test YAML per rule (plus one clean baseline) in a temp
   `Players` folder and run, from `Archipelago-0.6.7`:
   `.venv\Scripts\python.exe Generate.py --player_files_path <tmp> --outputpath <tmp_out>`
   Each must generate successfully, emit the expected warning, and the
   spoiler/slot data must show the corrected value. The baseline must
   produce no new warnings.
3. Generate once with every involved option set to `random` (5 seeds) and
   confirm no failures.
4. Report: files changed, each rule's test result, anything you could not
   verify.

## Documentation

`CLAUDE.md` is a short list of standing rules — don't append a history
entry to it. Update only its §4 "Options and GUIs" bullet about
`generate_early()` so it says the validation now exists and where (method
name), and that new option-combination rules go in both `ap_gui.py` and
that method. Put the detailed write-up (rules added, test results, what's
unverified) at the end of your final report instead.
