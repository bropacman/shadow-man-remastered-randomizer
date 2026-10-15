# CLAUDE.md — Shadow Man Remastered Randomizer

Working rules and hard-won lessons for this repo. The full dated
investigation history (crash hunts, reverse-engineering logs, every fix
write-up up to 2026-10-05) is archived locally at
`docs/archive/CLAUDE_history_*.md` — not in git. When touching a subsystem
below, grep that archive for its name before redesigning anything; most
"new" ideas here have already been tried.

---

## 1. Repo layout and where to edit

One repo, three shipped artifacts (see `RELEASING.md`):

| Artifact | Source | Entry points |
|---|---|---|
| Standalone randomizer (single-player .exe) | repo root | `patcher.py`, `gui.py` |
| AP world (`shadowman.apworld`) | **`apworld/`** | `apworld/__init__.py`, `options.py`, `items.py`, `client.py` |
| AP Companion (.exe) | repo root | `ap_gui.py` (Generate YAML / Apply AP Seed), `ap_patcher.py`, `apply_ap_seed.py` |

- **`apworld/` is the only place to edit AP world code.** It was merged in
  with `git subtree` on 2026-09-16 (`docs/PHASE4_ARCHITECTURE_SCOPING.md`):
  ordinary tracked files, not a nested repo or submodule.
- Build: `python build_apworld.py` → `dist/apworld/shadowman.apworld`.
  To test, back up then replace `custom_worlds/shadowman.apworld` in the
  local Archipelago install (sibling folder `../Archipelago-0.6.7`). That
  install has no `worlds/shadowman/` source folder.
- Public mirror repo `shadow-man-remastered-ap-world` is published from
  `apworld/` only by the maintainer running `python sync_apworld_mirror.py`.
  Never edit the mirror; never run the sync unasked.
- `../Archipelago/worlds/shadowman/` is an outdated July 2026 copy. Ignore it.
- Archived history mentions `Archipelago-0.6.7/worlds/shadowman/`,
  `Archipelago/worlds/shadowman/` or "both checkouts" — read those as `apworld/`.
- Commit or push only when asked. Releases follow `RELEASING.md`.

## 2. File integrity protocol

Before editing or reasoning about any file over ~500 lines, check its line
count (`wc -l`). If it looks suspiciously short, or a file ends mid-function
or mid-statement, stop and report "Possible truncated mount — stopping"
rather than editing. The bash sandbox can see a stale cached copy of
mounted files; confirm content with the Read tool, not bash `grep`/`sed`.

## 3. Duplicated code — the biggest source of bugs here

Several files exist both at repo root (standalone) and in `apworld/`, and
are kept in sync by hand, not by import:

- Near-identical pairs: EXE patch modules (root copies live in
  `exe_patches/`, AP copies flat in `apworld/`: `death_penalty_patch.py`,
  `soul_threshold_patch.py`, `health_patch.py`, `cadeaux_patch.py`,
  `gad_pickup_patch.py`, …), `kpf_handler.py`, randomizer modules.
  `python tools/check_apworld_sync.py` reports drift (defaults to
  `apworld/`; `--diff <file>` for a full diff). `apworld/gad_pickup_patch.py`
  inlines its own RSC-record injection because `rsc_utils.py` isn't
  shipped in the apworld — permanent known "drift", keep it equivalent.
- Inside `exe_patches/`, modules import each other relatively
  (`from .death_penalty_patch import …`), matching the AP copies so the
  drift checker sees no difference; root modules like `rsc_utils` stay
  absolute. Callers use `from exe_patches.X import …`; a module's own CLI
  runs as `python -m exe_patches.X` from the repo root.
- Deliberately divergent pairs: `fill.py`, `access_rules.py`, `regions.py`,
  `constants.py`, `locations.py`. The **root `fill.py` is canonical** for
  `CHECKABLE_LOCS`/`UNVERIFIED_LOCS` filtering.
- `exe_patches/dark_engine_patch.py` and `randomizers/entrance_randomizer.py` are
  imported directly by `ap_patcher.py` — not duplicated.
- `data/locations.csv` and `apworld/data/locations.csv` are separate copies
  kept identical (see §5).

**Standing rule:** when porting a function between copies (e.g.
`patcher.py` → `ap_patcher.py`, root `fill.py` → `apworld/fill.py`), diff
the ported function line by line against its source. Whole blocks have
silently gone missing in ports more than once. Hand-written logic rules are
often duplicated in several places too (e.g. a region route existed in four
copies across both sides) — grep for every copy before calling a fix done.

## 4. Options and GUIs

- **Every patcher option must support `random`, resolved at the seed level**
  (standalone: `_resolve_random_config()` in `patcher.py`; AP: Archipelago's
  native `random` keyword).
- **Adding a standalone option touches 5 places in `gui.py`:** HTML input
  (+ `…Rng` 🎲 button), `getConfig()`, `FIELD_SHORT` (unique 2–3 char code),
  `FIELD_DEFAULTS`, `applyConfig()` (rng-aware restore) — then `_build_cmd()`
  emits the CLI flag. Booleans with random emit `--foo-random`; patcher adds
  a store_true arg and resolves in `_resolve_random_config()`.
- `ap_gui.py` enforces option combinations in embedded JS (`sync*Dependency`,
  `syncCadeauxConstraints`, `onGatePresetChange`). Hand-written YAMLs bypass
  that; the AP world enforces the same rules in
  `ShadowManWorld._validate_options()` (`apworld/__init__.py`, called first in
  `generate_early()`; scope: `docs/scope_yaml_option_validation.md`). New
  option-combination rules go in both `ap_gui.py` and that method; keep their
  behavior in step.
- After editing embedded GUI JS, extract the `<script>` block and run
  `node --check` on it; `py_compile` doesn't see JS errors.

## 5. Location data

`data/locations.csv` (~3050 rows) is the master list. Columns include
`level_id, source_file, offset, save_idx, category, object, friendly_name,
sub_region, level_region, can_softlock, x, y, z, is_verified, notes`.

- **loc_key** = `"{level_id}:{source_file}:{offset}"`. Use the offset string
  exactly as the CSV has it (most are `0x0612`, a few levels use `0x5ca`).
- `sub_region` holds the logic requirement expression (`N`, `ENG_KEY`,
  `GAD2_WALK & GATE_DEADSIDE_PATH_3`, …), not a room name.
- `is_verified`: `TRUE` or blank = real (blank = not yet audited);
  `FALSE` = phantom/invisible/out of bounds → excluded from
  `CHECKABLE_LOCS` and suppressed in patcher validation.
- `can_softlock`: `TRUE` = reachable but may trap the player; key items are
  never placed there. Otherwise `NULL`.
- Night levels (`swampnit`, `nprison`, …) share their day level's rows.
- **After any edit:** copy to `apworld/data/locations.csv`, then run
  `python tools/generate.py` and `python apworld/tools/generate.py`.
  Never edit `extracted_locations.py` by hand.
- **Naming workflow:** `tools/ce_location_namer.lua` (Cheat Engine) shows
  nearby locations live and appends names/flags/notes to
  `data/naming_log.csv`; `python tools/apply_naming_log.py [--dry-run]`
  merges into both CSVs. Naming style: `{room/landmark} [{item tag}]
  {object} {n}`, number omitted when only one exists.

Fill terms: `RAW_LOCATIONS` (every row) → `AP_LOCATIONS` (placement
categories) → `CHECKABLE_LOCS` (minus excluded, non-checkable, unverified).
`FIXED_SOUL_LOCS` = boss/true-form souls, never shuffled.

## 6. How items are patched and detected

| Item | Patched in the RSC record | Client detects collection by |
|---|---|---|
| Dark Soul, native slot | nothing | live per-level darksoul flag array (index = save_idx) |
| Dark Soul, relocated | `object` → soul RSC, `track_type` copied from soul template, `save_idx` borrowed from a donor soul slot | same flag array, borrowed index |
| Donor soul slot | `save_idx` → 0 only | whatever its new item uses |
| Cadeaux, native | nothing | save-file position scan vs CSV coords |
| Cadeaux, displaced | `object`/`save_idx` from a donor cadeaux | same position scan |
| Barrel-shaped object in a cadeaux slot | always normalized to `RSC_X_CADEAUX` (same-level too) | position scan |
| Foreign player's item | `RSC_X_BOOK_OF_SHADOWS`, `save_idx` 0, upright rotation, y +30 | position scan |
| Unique key items / weapons | `object` renamed | live inventory flag (non-zero = owned) |
| Gad Power / Poigne | dispatch-table case redirect (`gad_pickup_patch.py`, reuses `RSC_X_PROPHECY`'s case) | live gad flag bytes |
| Retractor / Accumulator | position table at patch time | live counter + position match |
| Light Soul | never moved | flag byte == 1, debounced 3 polls |

- Gad pickups share Book of Prophecy's dispatch case, so **Book of Prophecy
  must stay out of the item pool** (one touch would credit both).
- Verified cadeaux locations not chosen as AP locations (bundling) are
  neutered to empty `RSC_X_BARREL_D`, so they can't hand out extra cadeaux.
- "No AP location for `<level>` iid=N" in the client log is benign: the
  save's quest-object list includes non-pickup story flags.

## 7. client.py (live memory) rules

These come from a long series of real crashes. Don't relearn them.

- **Prefer plain memory reads/writes.** Executing engine code from an
  injected thread (`CreateRemoteThread`) has caused crashes, heap
  corruption and a full system freeze. Thread hijacking is shelved —
  don't retry it. Engine calls that must happen in the game's own thread
  are done by static EXE patches instead (see §8).
- **Gate every write on being in-game** (`_confirmed_in_game` /
  `_read_is_at_title_screen`), including delayed/timer writes and DeathLink.
  A `None` from the title check means "unknown", not "in game".
- **AP resends the whole `items_received` history on every reconnect.**
  Counters (Dark Souls, Cadeaux, Gad tier) are recomputed absolutely from
  `items_received`, never incremented. Stackable `give_item` items
  (Retractor/Accumulator) are skipped when the live counter already
  matches. Gad flag writes never lower an already-set temple flag.
- Native "apply now" calls (gad level, soul meter fallback) are withheld
  for the first `SECRET_TRAP_MIN_SECONDS_SINCE_CONNECT` seconds after
  connecting (backlog) and during replay, and never run in the level-entry
  resync sweep — the engine's own level load applies them.
- Level IDs: live numbers 19–24 are night phases; canonicalize to the day
  `level_id` everywhere (`_canonical_level_id`).
- Client debug commands (archived history uses older names like
  `/siminject`): `/testitem`, `/testbacklog`, `/itemspeed`, `/catchup`,
  `/status`, `/secret`, `/checksecrets`, `/checkcallbacks`, `/pollerstatus`,
  `/resetsecrets`, `/testdeath`, `/poisonme`, `/healme`, `/drainvoodoo`,
  `/maxvoodoo`, `/drainammo`, `/maxammo`, `/debug`. `/console` is the
  shelved remote-execution path — don't use it.

## 8. EXE patching rules

- **Always patch a fresh vanilla copy.** `ap_patcher.py` copies
  `thoth_x64.exe` → `thoth_x64_patched.exe` before every apply. Patch
  scripts must hard-abort (raise, not warn) if hook bytes aren't vanilla —
  reapplying a section-appending patch produced an invalid PE and the
  hardest crash hunt in the project's history.
- **New PE sections must be gapless:** VA = image base +
  `align_up(SizeOfImage, SectionAlignment)` read from the file at apply
  time. A hardcoded "safety gap" makes Windows refuse to launch the exe,
  and no static checker (incl. `pefile`) catches it.
- `.text` padding gaps are ≤16 bytes; chain fragments
  (`tools/find_cave.py`) or append a section. Assemble with Keystone,
  verify with Capstone (operand types, not string matching), then do a full
  apply → re-read from file → re-disassemble → revert round trip on a
  synthetic PE before touching a real exe. Keystone can hang on `;`
  comments containing `--`.
- Live cvar ("secret mode") work: handles are fixed RVAs; writes set the
  cache tag *and* value; the per-frame poller (`secret_mode_section_patch.py`)
  hooks the player tick dispatcher merge point, validates tag and value,
  and makes at most one callback per frame.
- `kexengine.cfg` cvars are read once at game boot; patch-time edits work,
  mid-session edits don't.

## 9. Patching pipeline gotchas

- Every run must extract into a brand-new work directory; reusing one let
  stale patched bytes leak into a new seed.
- `ap_patcher.py` is the only AP patching path (the AP world's old
  `patcher.py` was removed).
- `patch_gate_arc_decos`: Fogometers' ARC decoration is in `instance.rsc`,
  every other level's is in `events.rsc`; always rename to the full
  `RSC_X_COFFIN_GATE_ARC` prefix.
- General enemy shuffle must never move True Forms (filtered structurally).

## 10. Verifying work

Report honestly what was and wasn't verified. Typical bar:
`python -m py_compile` on touched files; `node --check` on embedded JS;
offline reproduction against real data (real CSV, real `.apshadowman`,
real RSC dumps); for logic changes, real `assumed_fill()`/`validate_fill()`
or AP `Generate.py` runs across several seeds. Most game-side changes can't
be live-tested from a session — say so and say what the maintainer should
check in game.

## 11. Open threads

- AP option validation in `generate_early()` — `docs/scope_yaml_option_validation.md`.
- "N of 6 pistons required" goal option — blocked on capturing the piston
  completion flags (`dumpsaveflags` before/after one piston).
- Book of Shadows pickup animation — kill it or make it an instant grant;
  start from `gad_pickup_patch.py`'s dispatch-table work.
- 2x item meshes: MP-909 and Light Soul meshes not yet identified.
- Unique Retractor Keys exe enforcement (`unique_retractor_keys_patch.py`) —
  confirm it launches and only the matching schism opens.
- Further reference: `docs/TECHNICAL.md`, `apworld/docs/dev/*.md`.
