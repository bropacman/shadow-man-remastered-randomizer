# Session notes — 2026-07-15

Read this + `AP_FEATURE_GAP.md` + `LIVE_MEMORY_TRACKING_NOTES.md` before doing
anything. The old memory files (`project_shadowman_archipelago.md`,
`reference_sandbox_quirks.md`) were unavailable this session; this file is the
replacement source of truth going forward.

## Shipped this session (all in `Archipelago/worlds/shadowman/`)

1. **Tier-1 module refresh** — re-copied current standalone randomizer modules
   with package-relative imports: `enemy_randomizer.py`, `music_randomizer.py`,
   `sfx_randomizer.py`, NEW `ambient_randomizer.py`, NEW `sky_randomizer.py`,
   NEW `extracted_enemy_locations.py` (was missing entirely — enemy shuffle
   crashed generation before). `ENEMY_DIFFICULTY*` + enemy SFX pools appended
   to `constants.py`. Patcher: Step 6d (ambients), Step 9.5 rewritten
   (sfx tuple return, enemies-sfx, sky), `gate_remap` passed to
   `randomize_enemies`. Options added: enemy_mix_movement, enemy_uncap_counts,
   shuffle_ambients, ambient_mode, shuffle_enemies_sfx, shuffle_sky; EnemyMode
   gained `difficulty` (new default, matching standalone).

2. **Client perf/responsiveness fixes** (`client.py`, now 2098 lines) — the
   July-14 live-memory watcher froze the Kivy UI. Fixed: pymem logger capped
   at WARNING; `_get_process()` caches the attach with a 1-byte liveness
   probe; ONE combined heap walk for both signatures
   (`_scan_memory_for_signatures`), chunked 8 MB reads, full scan every 10
   polls, and the walk runs in `run_in_executor` (never on the event loop).

3. **Fill reliability fix** — `max_gate_sl` option (Range 3–10, default 8,
   applied as min() with preset cap in `generate_early`) + progression-soul
   subset in `create_items`: only `max-threshold-used + 20` souls are
   progression (floor SL2=3), rest `useful`. In-game any soul counts, so
   logic is conservative-sound.

## Fill failure — root cause (fully diagnosed, instrumented)

100 of 137 pool items/player are Dark Souls vs gate thresholds SL8=71,
SL9=95, SL10=120 souls — SL10 = the ENTIRE supply (zero slack; no other AP
currency world ships this). AP's `fill_restrictive` transiently loses placed
souls during assumed-state sweeps, reachability collapses, and every item
tried during the collapse lands permanently in `unplaced` (fill never
retries). Measured pre-fix: hard solo ~35% FAIL, easy ~7%, story 0%.
Post-fix: story/easy 100%, hard@SL8 ~90% solo / 100% 2p (small samples —
`tools/fill_stress_test.py` reruns this).

**Durable fixes, in order of value:**
- **Location-pool expansion (Jon's call, agreed best):** open cadeaux (553)
  and barrel (2326) slots as AP locations. Current ratio 137 items : 157
  locations is the tightest in AP; healthy worlds run 3:1+. Needs: locations.py
  filter change, item-pool padding, client tracking for those slots
  (instance_ids — see the 37-skipped problem), patcher slot-accepts audit.
- **Soul-threshold scaling (DK64/SM64 pattern):** copy standalone
  `soul_threshold_patch.py` (Tier 2), have AP write LOWER in-game SL
  requirements + matching `_SOUL_THRESHOLDS` in logic. Note: scaling alone
  tested ≈ hard@9 ≈ 80–90%; it complements, doesn't replace, expansion.

## Live-memory Govi test — STILL NOT RUN (top priority)

Every room Jon connected to so far was generated with a pre-coordinates
apworld → connect line shows `0 position-tracked (for govi)` → NO govi can
match, live or save path. All "No AP location matched Govi" spam traces to
this. **He must regenerate + host fresh.** YAMLs: `Players/TestPlayer.yaml` +
`Players/shadow_man_test.yaml` (TestPlayer2; has Tier-1 cosmetic toggles;
`game_dir` must be set or patching is skipped). Use `gate_preset: story` or
`easy` (100% gen) for the test. Success = `LocationChecks` within ~5 s of
cracking a govi, no save. Watch for `No AP location matched live Govi`.
Client must be freshly launched (old code ran in every session before
2026-07-15 evening).

## Other open items

- `No AP location for <level> iid=N` warnings (t1tchgad 743/751/561, asylum
  188, wastland 417/291/243): believed = the 37 slot_data entries with
  instance_id=0. Run `tools/extract_instance_ids.py`, regenerate, confirm.
- Second repo `shadow-man-remastered-archipelago` NOT mounted all session —
  nothing mirrored. Changed/new files to sync: client.py, patcher.py,
  options.py, __init__.py, constants.py, enemy/music/sfx/ambient/sky
  randomizers, extracted_enemy_locations.py, AP_FEATURE_GAP.md, this file,
  tools/fill_stress_test.py. Best practice agreed: single repo + NTFS
  junction into `Archipelago/worlds/` (package name decision:
  `shadow_man_remastered`), plus a scripted `sync_from_standalone.py` for
  vendored standalone modules. Not yet done.
- Tier 2 (EXE knobs) not started: soul_threshold_patch, health_patch,
  death_penalty_patch, cadeaux_patch, dark_engine_patch + options.

## Session continued (2026-07-15, later)

1. **Fill stress test run** (`tools/fill_stress_test.py`, sandbox venv —
   `pip install --break-system-packages` schema/jellyfish/jinja2/orjson/cymem/
   bsdiff4/platformdirs/certifi/pyyaml, `SKIP_REQUIREMENTS_UPDATE=1` to dodge
   the 3.11 check in ModuleUpdate.py, which `fill_stress_test.py` never
   imports anyway): story x1p 40/40 (100%), easy x1p 40/40 (100%), hard x1p
   @SL8 36/40 (90%), hard x2p @SL8 19/20 (95%). All match/close to the
   docstring's post-fix baselines — no regression.

2. **Govi test — still not run.** Set `gate_preset: story` on both
   `Players/TestPlayer.yaml` and `Players/shadow_man_test.yaml` (were `hard`
   — this is the literal cause of the last Generate.py FillError in
   `logs/Generate_..._19_20_40.txt`, 2p @ hard, matches the ~90% hard rate
   above). `game_dir` is still blank on both — Jon must fill it in before
   Generate.py will apply patches. Test still needs to be run for real.

3. **Repo mount correction.** `Archipelago/shadow-man-remastered-randomizer -
   Non AP` (mounted all session) is a stale/partial standalone copy — missing
   the Tier-2 patch files, `gui.py`, `randomizers/`, `patchers/`, and its own
   `CLAUDE.md`. The real, current standalone repo was separately mounted at
   `C:\Users\jonat\Documents\shadow-man-remastered-randomizer` (has `.git`,
   `CLAUDE.md`, all Tier-2 files). Use THIS one for any future standalone
   sync — not the "- Non AP" folder. Neither of these is the AP-side second
   repo (`shadow-man-remastered-archipelago`) mentioned below in "Other open
   items" — that one is still not mounted, so the NTFS-junction /
   `sync_from_standalone.py` setup is still not done.

4. **Tier 2 shipped** (all in `Archipelago/worlds/shadowman/`) — see
   `AP_FEATURE_GAP.md`'s second status update for full detail. Copied
   `soul_threshold_patch.py`, `health_patch.py`, `death_penalty_patch.py`,
   `cadeaux_patch.py` unchanged from the correct standalone repo (stdlib-only,
   no import fixes needed). Added 6 options + wired `generate_output()` and
   `patcher.py` Step 6e/7. `soul_threshold_mode` defaults off and carries an
   explicit warning — it desyncs from `access_rules.py`'s `_SOUL_THRESHOLDS`
   (not fixed; would need that file touched, which needs Jon's go-ahead
   first). Not execution-tested against a real EXE.

## Environment quirks (Cowork sandbox)

- The Linux-sandbox view of mounted folders goes STALE in BOTH directions,
  including torn mid-line file snapshots. The Read/Edit/Write file tools see
  the true host files. NEVER trust bash `wc/grep/py_compile` on
  recently-edited files; NEVER write through bash over host files. Verify
  edits with Read only.
  - Concretely reproduced this session: after 3 Edit-tool calls to
    `patcher.py`/`options.py`/`__init__.py`, bash's `wc -l` stayed stale by
    tens of lines for 20+ seconds (`sleep 20` didn't fix it), and a `grep`
    immediately after showed the NEW content while `wc -l` in the same
    breath still showed the OLD count — the staleness is per-call, not a
    fixed snapshot, so don't trust one bash call just because a later one
    "looks right." Only Read is reliable for verifying edits.
- Sandbox Python is 3.10 (AP wants 3.11+): bypass `ModuleUpdate` by inlining
  the test-setup (see fill_stress_test.py) and run against a /tmp overlay
  built from symlinks + repaired copies of any stale files.
- Background processes don't survive between bash calls; 45 s hard timeout
  per call — chunk long suites.
