# AP world vs. standalone randomizer — feature gap audit (2026-07-15)

## Current state (as of 2026-07-21 night)

The world now has an explicit "Defeat Legion" → "Victory" event location/item
(`__init__.py`'s `create_regions()`, via `BaseClasses.Region.add_event()`),
so the spoiler playthrough ends on a discrete goal line
(`Defeat Legion: Victory`) the same way most other AP worlds' spoilers do —
previously `completion_condition` was a bare state-check with nothing
backing it in the region graph, so nothing showed up as a final sphere entry.
`set_rules()`'s completion condition now reads `state.has("Victory", player)`
through that event instead of calling `R.pistons()` directly.

Dark Souls placed by AP fill into a liveside level (London/Florida/Prison/
Queens/Salvage) are now written as `RSC_X_DARK_SOUL` instead of
`RSC_X_GOVI` — the latter silently requires Night to collect when
physically in a liveside level (an engine quirk the standalone's `fill.py`
already guards against via `_liveside_ok()`), and AP places Dark Soul items
with no location-category restriction at all, so this was a real,
previously-unhandled correctness gap. See `generate_output()`'s Dark Soul
retyping block in `__init__.py`.

`ap_patcher.py` / `apply_ap_seed.py` / the portable `.apshadowman` JSON is now
the **only** AP patching path — `worlds/shadowman/patcher.py` (the old
`game_dir`/hybrid local-patch path) and the `GameDir` option were both removed
2026-07-21.

Tier 1 (cosmetic) and Tier 2 (EXE-patch knobs) are done, except
`soul_threshold_mode`, which stays OFF by default because enabling it desyncs
`access_rules.py` from the patched EXE (needs an `access_rules.py` change,
not made — ask before touching that file). Of Tier 3: `entrance_mode` is done
for `deadside_only` (`cross_hub` not ported), `piston_combos` is done,
`shuffle_prisms` is not ported.

`Insanity` was relabeled `Cadeaux Key Items` — it was never a "graded tier"
gap to begin with. Soul/Govi locations have never been gated by it (on or
off). As of 2026-07-21 (later session, prompted by user feedback that
cadeaux should be "optional as a location"), the option now gates whether
cadeaux locations exist as AP locations **at all**: off excludes all ~657 of
them from the AP location pool entirely — same treatment as barrel, no
checks, no hints, stays vanilla; on includes them all with no item-type
restriction. This replaces the earlier (2026-07-20 → 2026-07-21-morning)
design where cadeaux locations were unconditionally real AP checks and the
option only toggled an `item_rule` (Cadeaux-only vs any item) — that design
didn't match "optional," since off still surfaced ~657 checks/hints. Barrels
stay permanently excluded from the AP location pool (`_SKIP_CATS`) regardless
of any option, so there is no AP equivalent of the standalone's Tier 3
(weapon/lore/bonus/barrel scope) — adding one would mean ~2,085 more
locations, a scope decision, not a missing port.

A real bug was found and fixed 2026-07-21: the AP world's `fill.py` was
missing the `UNVERIFIED_LOCS` filter entirely, letting 13 confirmed-phantom
cadeaux locations become live checks. Live cadeaux count is now 653 (was 657,
see below); true count is 666 pending in-game re-audit of the remaining
flagged `data/locations.csv` rows (4 more, in `t4ndgad`, are resolved — no
in-game entrance, correctly excluded).

Console output for entrance-rando and piston-combo-rando is now non-spoiling:
both `ap_patcher.py` and `patcher.py` call `apply_unified_shuffle(...,
verbose=False)` with a plain route-count summary printed instead, and
`dark_engine_patch.py`'s `apply_dark_engine_patch()` /
`extract_and_patch_journal()` default to `verbose=False` (only the CLI
diagnostic `__main__` block still passes `verbose=True`). Full spoiler
details remain in the `spoiler_seed_<seed>.txt` file either way.

Two more real bugs were found and fixed 2026-07-21 (late night), both from a
single pasted `ap_patcher.py` log showing 7 "placements not written to
files" validation errors:

1. Boss/true_form (`FIXED_SOUL_LOCS`) locations were being queued into
   `progression_placement` in `generate_output()` even though they're
   pre-filled, auto-collected checks with no patchable RSC record — every
   one produced a validation error (3 of the 7). Fixed by skipping that
   category up front, matching the standalone's own behavior.
2. 4 `salvage` cadeaux rows (`0x552A`/`0x5602`/`0x5692`/`0x56DA`) were
   `is_tracked=FALSE` with a "coords need verification" note but
   `is_verified=TRUE`, so `CHECKABLE_LOCS` kept them live and both patchers
   failed to write them (the other 4 of the 7). A blanket "treat
   `is_tracked=FALSE` like `is_verified=False`" fix was tried first and
   reverted the same session — `is_tracked=FALSE` covers 79% of the CSV,
   including 198 real, currently-working non-barrel locations, so a blanket
   version would have silently broken far more than it fixed. Fixed
   correctly instead by flipping `is_verified=FALSE` on just those 4 rows in
   `data/locations.csv` (regenerated `extracted_locations.py` in both
   repos) — reuses the existing, already-correct `UNVERIFIED_LOCS` path with
   zero blast radius elsewhere. This is why the live cadeaux count above
   dropped from 657 to 653.

A third real bug was found and fixed 2026-07-21 (later still): `ap_patcher.py`
was running `audit_govi_patches()`/`verify_patch()` on every `quest.rsc` patch
pass (ported unchanged from the standalone's `patcher.py`), printing "[!]
MISMATCH" lines for every soul whose `save_idx` AP intentionally reassigned via
`_soul_identity_map()` — expected AP behavior (see the Dark Soul retype fix
above), not a real patching failure, but confusing/spoiler-adjacent noise in
the console log. Removed both calls from `ap_patcher.py`'s patch loop only
(functions still defined, just unwired) — the standalone's own `patcher.py`
never reassigns soul identity this way, so its equivalent call stays as a
genuine correctness check.

A fourth real bug was found and fixed 2026-07-21 (still later), then
superseded by a better fix the same night. The game validates the GLOBAL
count of `$darksoul` lines across all of `levels.txt`, and the sum of
`$cadeaux N` values, against hardcoded constants (120, 666) at launch —
confirmed via an actual in-game crash dialog: "Total Dark Souls in levels is
not 120. Its: 95". It does not care where souls/cadeaux physically ended up,
only that the file's declared totals are exactly right.

The first attempt tried to keep `patchers/levels_txt_patcher.py`'s declared
totals pinned to 120/666 no matter what AP placement did (restoring stripped
vanilla `$darksoul` lines for any soul identity that never got real AP
placement data — e.g. because it was sent to another player's game, or its
native slot received a foreign item instead). This worked for souls in
testing, but real in-game testing then turned up `cadeaux total: vanilla=666
patched=750` — the cadeaux side has its own separate failure mode (the
"donor" identity-swap logic isn't as dormant in real seeds as its own code
comment assumed, minting extra cadeaux-labeled locations). Chasing each of
these individually is a losing game: in an AP multiworld, the true number of
souls/cadeaux physically present in Shadow Man's world is not a meaningful
invariant at all — some of Shadow Man's own items get sent to other players'
games, and Shadow Man's own slots can hold other players' items instead — so
forcing the hints file to always claim exactly 120/666 means periodically
lying about locations that no longer hold what the file says they hold.

Fixed properly instead by disabling the EXE-side check itself. Ghidra
disassembly of `FUN_14031c100` (the levels.txt parser) pinpointed the two
conditional jumps — `CMP R14B,0x78` / `JNZ` (souls vs 120) and
`CMP EDX,0x29A` / `JNZ` (cadeaux vs 666, `EDX` being `DAT_1407edde8` — the
same address `cadeaux_patch.py` already documented as the Fogometers-door
total). Initial offsets computed via the codebase's usual `VA - 0x140000000`
convention (`0x31E27A`/`0x31E286`) turned out wrong for this region of the
file — confirmed by a real crash after patching ("Its: 91" with WARNING logs
showing unexpected bytes at those offsets). A byte-pattern search script run
against the real exe found the true offsets are 0xC04 earlier:
**`0x31D67A`** (souls JNZ) and **`0x31D686`** (cadeaux JNZ) — corrected in
`cadeaux_patch.py`. (Note: the pre-existing, never-applied
`CADEAU_TOTAL_OFFSETS["levelstxt_validation"] = 0x31E282` entry is likely
wrong by the same delta if it's ever wired up.) New function
`patch_levels_txt_launch_validator()` in `cadeaux_patch.py` NOPs both 6-byte
`JNZ` instructions (idempotent, verifies vanilla bytes before patching), wired
unconditionally into `ap_patcher.py`'s EXE-patch step — not the standalone's,
since the standalone never has foreign items and shouldn't need it. With the
crash disabled, the earlier "restore stripped soul line" workaround in
`levels_txt_patcher.py` was reverted — it's no longer needed, and it was
itself misleading (pointing the tracker at a soul that isn't really there).
Orphaned soul IDs are now simply omitted; the hints file only ever describes
what's actually true for that seed.

A second real bug (found via real testing after the EXE-check removal:
`cadeaux total: vanilla=666 patched=742/750`) was root-caused and fixed the
same day: `generate_output()`'s cadeaux donor-backfill loop
(`_cadeaux_identity_map()`'s `assignment` dict, meant to relabel a donor
slot's `category` from `"cadeaux"` to `"barrel"` once its physical cadeaux
identity is donated to a displaced target) was gated behind
`if donor.loc_key not in progression_placement`. That guard was always
False — every donor is a `category=="cadeaux"` `CHECKABLE_LOCS` entry, this
map is only ever non-empty when insanity is on (all cadeaux locations are
AP locations then), and the main per-location loop above already writes an
entry for every AP location, donors included. So the backfill never fired:
every donation left the donor slot still labeled `"cadeaux"`, so
`levels_txt_patcher.py`'s per-level `$cadeaux` recount (driven by that
category label) counted both the donor and the target it gave its identity
to — a guaranteed +1 overcount per donation. Fixed by making the backfill
overwrite unconditional.

A third real bug, reported 2026-07-21 (user testing, not a crash dialog this
time): connecting to the AP server while `client.py` was sitting at the main
menu let other players' items get memory-injected immediately — into
player/inventory structures that don't exist until a save is loaded — and
starting a New Game afterward crashed. `_inject_item()` only ever checked
`_get_process()` (is the exe running), never whether a real game session was
active. First attempt gated on `current_level` being non-`None` (i.e. "have
we observed a live level number yet"), but the user caught the flaw before
it shipped: the main menu's background demo drives real level-loading code
for its attract-mode gameplay, so a live level number can't distinguish "the
menu's demo is currently showing level N" from "the player is really in
level N" — the gate would have unlocked injection during the menu demo,
same crash risk. Fixed properly using dedicated menu-state addresses instead
of level state: `TITLE_SCREEN_1..5_RVA` (`0xF5F17C`, `0xF9C1C4`, `0xF970B0`,
`0xF970B8`, `0xF9738C`), sourced from the community LiveSplit ASL
autosplitter for this game (bropacman v1.5 / Winning117) rather than
independently Ghidra-confirmed — the autosplitter's own 5-way-agreement
reset condition was kept as-is (anti-false-positive design) rather than
trusting one address. `_read_is_at_title_screen()` returns `True`/`False`/
`None` (ambiguous read); `_inject_item()` and `_replay_all_received_items()`
(covers manual `/inject` too) both re-read this fresh at call time — no
reliance on a cached poll value — and fail closed on anything but a
confirmed `False`. `_poll_live_memory` tracks `_confirmed_in_game`
transitions in both directions and auto-fires a replay the moment the
player leaves the title screen, and re-arms the gate on the way back to it
— which also closes a gap the `current_level`-only approach couldn't have:
quitting an in-progress, AP-connected game back to the main menu mid-session
and staying connected (`current_level` is intentionally never cleared, so
that case would have stayed "in game" and kept injecting). `/status` now
reports the live gate state too.

A fourth real bug, found via real playtesting the same day: a cross-game
Dark Soul landing on a Swamp (`swampday`/`swampnit`) location didn't get
protected from the Night-locked-Govi engine quirk the way the other 5
liveside regions already were — `LIVESIDE_REGIONS` in the AP world's
`fill.py` was missing `"Louisiana Swampland"`, even though the standalone's
canonical `fill.py` (source of truth per CLAUDE.md) already had it — silent
drift, every other entry matched. Added it, now matches exactly.

A fifth: `RSC_X_POIGNE` (an AP item from another player's game) was firing
its injection (`GiveItem(0x13)`, confirmed live via the console log) but
granting nothing in-game. Root cause, per the user: Poigne isn't a plain
possession-flag item like Baton/Calabash/etc. — vanilla's gad-power tier
system has 4 flag slots (`GAD_1..4_RVA`), the 3 real Gad Temples each set
one, and Poigne sets the 4th directly, independent of the temples.
`POIGNE_RVA` is a real flag too, but it's cosmetic/tracking only (why
self-found detection worked off it) — it's not what enables the ability.
Re-mapped `RSC_X_POIGNE` from `("give_item", 0x13)` to `("gad", 4)` and
generalized method `"gad"`'s dispatch (both `_inject_item` and
`_replay_all_received_items`) to add `arg` (not always `1`) to the
cumulative `gad_powers_received` counter before calling `inject_gad_power`,
which already clamps the actual in-memory tier to 4 regardless of how high
the cumulative count climbs — so Poigne stacking with real temple powers in
any order is harmless.

Also, `/status` was reporting "Soul count: 0" / "Gad powers: 0" even with a
real higher live level, and never showed Cadeaux at all. `last_soul_count`
only updates from a save-file parse and `gad_powers_received` only from AP
items processed by the current client session — neither reflects the
already-correct live-memory values the poll loop tracks separately
(`_live_soul_count`, logged correctly the whole time as "Live soul counter:
X -> Y"). `/status` now does a fresh live-memory read of
`SOUL_COUNT_RVA`/`GAD_LEVEL_RVA`/`CADEAUX_COUNT_RVA` when the game is
running, falling back to the session-tracked values only if that read
fails.

A sixth, and the most severe of this session's real-testing finds: entrance
shuffle's portal gating was fundamentally broken, caught by the user reading
an actual playthrough log (t3swmgad's own locations appearing in an early
sphere that shouldn't have had anywhere near enough souls for its SL6-ish
gate). `DEADSIDE_PORTAL_GATE` (access_rules.py) mapped each of the 9
Deadside portal cutscene files to a gate under a "physical position,
independent of destination" theory, and `make_portal_rule()` used
`_gate_sl_only()` (soul threshold only, no `GATE_DEPENDENCIES` ancestor-
chain walk) on the reasoning that the chain only models "you must have
already passed earlier gates on the way to the VANILLA destination," which
stops applying once that destination is shuffled. The user caught both
halves of this as wrong: the ancestor chain isn't about the destination at
all, it's the physical walk through Marrow Gates to reach a portal's own
location, unaffected by where its cutscene points afterward — and the gate
IDs in the table didn't even match the portal's real vanilla gate to begin
with (`LE_Cage.cut`, i.e. the Cageways portal, was mapped to
`GATE_DEADSIDE_PATH_3` instead of `GATE_DEADSIDE_CAGEWAYS`). Confirmed via
this seed's actual gate_remap/thresholds: the shuffled Cageways→Temple of
Blood portal only required 6 souls under the old code, vs. 34 once fixed
(walking the real chain up through `GATE_DEADSIDE_CAGEWAYS`) — many spheres
too early, not a rounding difference.

Fixed by remapping every `DEADSIDE_PORTAL_GATE` entry to the same gate id
each portal already uses in the vanilla (`entrance_shuffle=None`)
`gate_connections` list in regions.py — proven correct, unaffected by
shuffle either way — and switching `make_portal_rule()` (both the
single-gate and route-list branches) from `_gate_sl_only()` to `R.gate()`,
so the full ancestor chain gets walked exactly like the vanilla list already
does. Temple of Prophecy (`LE_Gad2.cut`) needed a route-list entry instead
of a single gate id, since its vanilla connection is a hand-built
"PATH_7, or (CAGEWAYS and PLAYROOMS and PATH_6)" OR-condition, not a single
named gate. `_gate_sl_only()` is left defined (now unused) for reference.

This exact same bug — identical `DEADSIDE_PORTAL_GATE` table,
`_gate_sl_only`-based portal rules in regions.py's `_make_routes_rule` —
was present in the standalone repo too, confirmed by diff and fixed there
identically. Both repos verified via a direct import of the fixed
`access_rules.py` against this seed's real `gate_remap`/
`soul_thresholds_precomputed`.

**REVERTED (2026-07-22): the levels.txt launch-validator EXE patch.** User
report from real play: the game now launched fine, but opening the in-game
inventory menu started raising exceptions. NOPing the two JNZ checks in
`FUN_14031c100` stopped the crash, but evidently there was more inside that
function than the Ghidra read caught — skipping past the crash call also
skips whatever sits between it and wherever execution was actually meant to
land, and something in there turned out to matter for the inventory menu.
`patch_levels_txt_launch_validator()`'s call site in `ap_patcher.py` is
commented out (function itself left defined, in case a fuller disassembly
justifies revisiting this later). To avoid reintroducing the original
120/666-mismatch crash without the EXE patch, `ap_patcher.py`'s levels.txt
step no longer calls `patch_levels_txt()` (the placement-accurate rewrite)
at all — only `strip_levels_txt()` runs, which isn't placement-aware and
never touches the `$darksoul`/`$cadeaux` counts, so they stay exactly
vanilla and the native check passes on its own. Net effect: AP seeds no
longer get accurate per-seed map-level hints (`patch_tracker=true` is
currently a no-op for AP, stripped hints are written regardless) until this
is revisited — the standalone's own `patcher.py`/`patch_levels_txt()` call
is untouched and unaffected, since it never had this problem to begin with.

**FIXED (2026-07-22): AP-side local-apply output landed in the wrong,
non-seed-specific folder; spoiler log was missing soul-threshold-mode
values; starting-item picker reworked to multi-select.** Three related
fixes from one user report, all in the standalone repo (`apply_ap_seed.py`
/ `ap_patcher.py` / `ap_gui.py`, consumed by the AP world only indirectly
via the `.apshadowman` file):
1. `apply_ap_seed.py`'s `--output-dir` default was a single fixed
   `<game-dir>/randomizer_output` folder shared by every seed, unlike
   `ap_patcher.py`'s own seed-specific `work_path`
   (`_randomizer_work_<seed>`) — so each new seed's spoiler log /
   `object_map.csv` / `soul_thresholds.json` silently overwrote the last
   one's. Default changed to `<game-dir>/_randomizer_work_<seed>` to match.
2. The spoiler log's SL-threshold section (`_spoiler_gate_section`, written
   at Step 4) always displayed souls translated via `VANILLA_SL_THRESHOLDS`,
   even when `soul_threshold_mode` (Tier 2) was active and the real in-game
   per-tier soul counts differed — because `write_spoiler_log()` runs before
   Step 6e computes `sl_thresholds_result`. This data was already being
   written to `soul_thresholds.json`, just never appended to the `.txt`
   spoiler log. Added `_spoiler_soul_thresholds_section()`, appended right
   after `sl_thresholds_result` is computed. Also added the missing
   `patch_tracker` line to the spoiler log header (present in `config`,
   never printed).
3. `ap_gui.py`'s Starting Item picker was a single-select dropdown plus 4
   separate "All X" bundle checkboxes, mirroring the standalone GUI's
   single-item model — but AP's `start_inventory_from_pool` genuinely
   supports picking several items at once. Reworked to one `<select
   multiple>` (fixed size, so it stays compact); Retractor/Accumulator/Gad
   Power Upgrade options now always grant their full stackable count (5/3/3)
   when picked, which is what the removed bundle checkboxes did — so the
   bundle checkboxes and their HTML/JS are gone entirely.
   `buildStartInventoryPool()` and `importYaml()`'s restore logic reworked
   to read/write `selectedOptions` directly instead of unpacking bundles.

Remaining known gaps are tracked in sections A–F below; full session-by-session
history is in the Changelog.

## Changelog

> **STATUS UPDATE (same day):** Tier 1 completed. Sections A and the cosmetic
> half of C are resolved — current standalone modules copied in with relative
> imports (enemy/music/sfx + new ambient/sky + extracted_enemy_locations),
> ENEMY_DIFFICULTY/ENEMY_SOUND_SETS appended to constants.py, patcher Step 6d
> (ambients) + Step 9.5 (enemies-sfx, sky, sfx swap-log tuple) updated, and 6
> new options added (enemy_mix_movement, enemy_uncap_counts, shuffle_ambients,
> ambient_mode, shuffle_enemies_sfx, shuffle_sky) plus EnemyMode `difficulty`
> restored as default. NOT yet generation-tested. Tiers 2–3 remain.
>
> **STATUS UPDATE (2026-07-15, later same day):** Tier 2 done. The correct,
> current standalone repo was found at a separate mount point (the repo
> previously mounted as `Archipelago/shadow-man-remastered-randomizer - Non AP`
> is a stale/partial copy missing `soul_threshold_patch.py`, `health_patch.py`,
> `death_penalty_patch.py`, `cadeaux_patch.py`, `dark_engine_patch.py`,
> `rsc_utils.py`, `gui.py`, `randomizers/`, `patchers/`, and its own `CLAUDE.md`
> — do not use it as a source again). Copied the 4 Tier-2 patch modules
> unchanged (stdlib-only, no import fixes needed, same as `gad_pickup_patch.py`).
> Added 6 options (`starting_health`, `altar_health_grant`,
> `altar_cadeaux_required`, `fogometers_cadeaux_required`, `death_penalty`,
> `soul_threshold_mode`) and wired them into `generate_output()` and
> `patcher.py` Step 6e/7. All use AP-native Range/Choice `random` support.
> **`soul_threshold_mode` defaults OFF and should stay off**: AP's
> `access_rules.py` `_SOUL_THRESHOLDS` still assumes vanilla thresholds, so
> enabling it desyncs logic from the patched EXE — could produce seeds AP
> thinks are legal but aren't beatable in-game (or trivially easy). Fixing
> that needs an `access_rules.py` change, which was NOT made (hard rule: ask
> before touching access_rules.py). `altar_cadeaux_required` /
> `fogometers_cadeaux_required` are logic-safe — `cadeaux_666` in
> `access_rules.py` is currently a no-op. NOT execution-tested against a real
> `thoth_x64.exe` (needs `game_dir` + an actual generation run) — the sandbox's
> bash view of the mounted AP repo went stale mid-edit (see session notes
> quirks section) so a bash-side import/compile check wasn't reliable either;
> verified by re-reading every edited file with the Read tool instead. Tier 3
> remains (dark_engine_patch.py / piston_combos, entrance_mode, open_gates,
> graded insanity, shuffle_prisms — all logic-affecting, deliberately deferred).
>
> **STATUS UPDATE (2026-07-21):** `entrance_mode` **DONE** (deadside_only
> only — cross_hub still not ported). `EntranceMode` Choice option added;
> `generate_early()` computes a 9-portal bijection via `self.random`;
> `regions.py`'s `create_regions()` builds the shuffled marrow→spoke
> connections from it (falls back to the fixed vanilla list when off);
> `access_rules.py` gained `make_portal_rule()`/`_gate_sl_only()`/
> `DEADSIDE_PORTAL_GATE`. Physical `.cut`-file patching (`ExitLevelPos`
> rewrites) lives in `ap_patcher.py` (standalone repo), reusing
> `randomizers/entrance_randomizer.py` directly rather than duplicating it.
> Serialized into the `.apshadowman` JSON as a top-level `entrance_shuffle`
> dict. Verified via a 30-seed Fill-based harness (full region reachability +
> 0 unfilled locations).
>
> `piston_combos` **DONE.** `PistonCombos` Toggle option added (simpler than
> the standalone's 3-state off/on/random — AP-native `random:` YAML already
> covers the random case for a Toggle). `dark_engine_patch.py` copied
> unchanged into `ap_patcher.py`'s import (same repo, reused not duplicated,
> like `entrance_randomizer.py` above). When on, Jack's Schematic
> (`RSC_X_JACKS_SCHEMATIC`) is built as `progression` instead of its normal
> `useful` classification (`create_items()`), and `access_rules.py`'s
> `pistons()` gained a `require_schematic` param — threaded through
> `BoundR`/`make_location_rule()` for the `gate_expr`-string call sites
> (`as4dkeng` barrels + Legion) and passed explicitly at the direct
> `completion_condition` call site in `set_rules()`. Verified via a 16-seed
> harness: schematic correctly required for completion when on (blocked
> without it, unblocked with it), unaffected when off.
>
> Both features exposed in `ap_gui.py`'s YAML tab (previously disabled
> stubs). Neither touched `worlds/shadowman/patcher.py` (the AP world's own
> "hybrid game_dir" immediate-patch path) at the time — only `ap_patcher.py`
> (the portable-JSON path via `apply_ap_seed.py`) applied the physical
> patches. That was a known, previously-flagged gap affecting every Tier
> 2/3 patch feature ported so far.
>
> Remaining Tier 3 at the time of this entry: `cross_hub` entrance mode,
> graded insanity (1–3, AP is bool-only), `shuffle_prisms`. **Superseded —
> see the 2026-07-21 evening entry below and "Current state" at top: insanity
> isn't a missing-tiers gap, it's a deliberately narrower, correctly-labeled
> feature (cadeaux-only). Only `cross_hub` and `shuffle_prisms` remain.**
>
> **STATUS UPDATE (2026-07-21, later same day): the `game_dir`/hybrid gap
> above is now CLOSED — by removal, not by porting.** `GameDir` option and
> `worlds/shadowman/patcher.py` (1264 lines, the AP world's own local
> patcher, zero other importers) are both gone. There is no longer a second
> patcher implementation to fall behind `ap_patcher.py` — every seed, AP
> website or local generation, goes through the portable `.apshadowman` +
> `apply_ap_seed.py` workflow exclusively. `generate_output()`'s docstring
> in `__init__.py` documents this; `guide_en.md` was rewritten to describe
> the new (only) workflow; the 8 option docstrings that said "only applies
> if game_dir is set" were reworded to point at `apply_ap_seed.py` instead.
> Section B below still lists `game_dir` in the "present and working" list
> — stale, left for history; it no longer exists as an option.
>
> **STATUS UPDATE (2026-07-21, evening): Insanity relabeled + a real
> unverified-location bug fixed.** Auditing what AP's `Insanity` bool
> actually does (prompted by a naming question — does "Full" really match
> the standalone's Tier 2?) found: soul/Govi locations have never been
> gated in this world, on or off, so there's no "tier" to climb on that
> axis — the option only ever toggled the cadeaux item_rule. Barrels remain
> permanently out of the AP location pool (`_SKIP_CATS`) regardless, so
> there is no AP equivalent of the standalone's Tier 3 at all, and adding
> one would mean ~2,085 more locations. Renamed the option (`options.py`)
> and `ap_gui.py`'s control from a 5-option "Insanity Tier" dropdown (with
> two permanently-disabled Tier 1/2 placeholder entries) to a plain
> `Cadeaux Key Items` checkbox with an accurate tooltip and dice-button RNG
> support, matching every other boolean in the tab. `display_name` field
> keeps the YAML key `insanity` for compatibility.
>
> Separately, verifying the real cadeaux count (should be 666, not 670)
> found `worlds/shadowman/fill.py`'s `CHECKABLE_LOCS` never actually
> filtered on `is_verified` at all — no `UNVERIFIED_LOCS` constant existed
> in this world's copy, despite `CLAUDE.md` documenting that architecture
> and the standalone repo's own `fill.py` already having it. Practical
> effect: all 13 `is_verified=FALSE` cadeaux rows (4 in `t4ndgad`, a cut
> sub-zone of Salvage with no in-game entrance, plus 9 flagged
> out-of-bounds/invisible/unconfirmed) were live, fillable AP checks. Ported
> the `UNVERIFIED_LOCS` filter into this world's `fill.py` verbatim from the
> standalone's version, and wired the same suppression logic into
> `ap_patcher.py`'s `validate_final_seed()` (imports `UNVERIFIED_LOCS` from
> the standalone's own `fill.py` — same underlying CSV, so loc_keys match).
> Live cadeaux count is now 657 (670 − 13); reaches the true 666 once the 9
> non-`t4ndgad` flagged rows are re-audited and confirmed real or removed
> from `data/locations.csv` — not done here, left for the user's own review
> since it needs in-game confirmation.
>
> Remaining Tier 3 gap description above (line 73) is now slightly stale:
> "graded insanity (1–3, AP is bool-only)" undersells the actual gap — it's
> not just "no grades," it's that AP's single toggle only ever covered
> standalone's Tier-2 scope (soul + cadeaux) and has no path to Tier 3
> (weapon/lore/bonus/barrel) without first deciding whether barrels should
> ever become real AP locations at all.
>
> **STATUS UPDATE (2026-07-21, later evening): Cadeaux Key Items redesigned
> to actually make cadeaux locations optional.** User feedback: the
> previous design (cadeaux locations always real AP checks, insanity only
> toggling an `item_rule` restriction) didn't match "I want cadeaux to be
> optional as a location for AP" — off still surfaced ~657 checks/hints,
> just restricted to Cadeaux-only rewards. Changed regions.py's `_SKIP_CATS`
> to conditionally include `"cadeaux"` based on the option (threaded through
> `create_regions()`/`_build_sub_regions()`), so off now excludes cadeaux
> locations from the AP location graph entirely — same treatment as
> `"barrel"`. `__init__.py`'s `create_items()` pool-size math updated to
> match (no "Cadeaux" item created when off; `open_location_count` shrinks
> by the cadeaux location count); `set_rules()`'s cadeaux `item_rule` block
> was deleted outright — no longer needed either way (off: locations don't
> exist; on: no restriction, which is what "on" already meant).
> `locations.py`'s own `_SKIP_CATS` (the static full AP-ID registry) is
> unchanged and correctly still includes cadeaux unconditionally — per-seed
> inclusion is regions.py's job, same pattern AP uses generally (a fixed ID
> superset, per-seed subsetting via which Locations actually get built).
> `_cadeaux_identity_map()` and `generate_output()`'s cadeaux fast path
> needed no changes — both degrade to no-ops when no "Cadeaux" item exists
> in the pool. Verified with real end-to-end generation (not just review):
> copied the repo to a scratch dir, ran `Generate.py` with `insanity: false`
> (clean generation, spoiler "Location Count: 157", cross-checked all 657
> cadeaux loc_keys against the spoiler location list — zero present) and
> `insanity: true` (clean generation, "Location Count: 814", the full set).
> Also fixed two now-stale user-facing descriptions of the option that still
> described the old behavior: `options.py`'s `Insanity` class docstring and
> `ap_gui.py`'s Cadeaux Key Items tooltip.
>
> **STATUS UPDATE (2026-07-21, night): added a "Defeat Legion" -> "Victory"
> event location/item.** User noticed other worlds' spoiler playthroughs end
> on an explicit goal line (Majora's Mask Recompiled: `18: { Defeat Majora:
> Victory }`) and Shadow Man had no equivalent — `completion_condition` was
> a bare `R.pistons(...)` state-check with no location/item backing it, so
> nothing ever showed up as a discrete final-sphere entry, even though the
> underlying logic (and the client's own Legion-defeat detection /
> `CLIENT_GOAL` reporting — see client.py's `_goal_watcher_loop`, unrelated
> and already correct) was fine. Added the standard AP event pattern via
> `BaseClasses.Region.add_event()` in `__init__.py`'s `create_regions()`
> method: an event location "Defeat Legion" in `Asylum: Engine Block`
> (the hub Legion is fought from once all five Engine Blocks are powered),
> locked with a "Victory" event item, gated by the same `R.pistons(...)`
> rule `completion_condition` used to call directly. `set_rules()`'s
> `completion_condition` now reads `state.has("Victory", player)` instead —
> single source of truth, so the spoiler-visible event and the actual win
> condition can't drift apart. Verified via real generation: spoiler
> playthrough's final sphere is now `Defeat Legion: Victory` (confirmed for
> both `insanity: false` and `insanity: true` seeds), and the flat Locations
> list / Paths section both show it too, matching the Majora's Mask
> Recompiled reference exactly in structure.
>
> **STATUS UPDATE (2026-07-21, later night): real bug fixed — Dark Souls
> placed in liveside levels now use RSC_X_DARK_SOUL, not RSC_X_GOVI.**
> User flagged a quirk from the standalone tool that AP never accounted
> for: RSC_X_GOVI has an engine-level requirement that it can only be
> collected at Night when physically situated in a liveside level
> (London/Florida/Prison/Queens/Salvage) — independent of whatever
> access_rule the location itself otherwise has. The standalone's own
> `fill.py` already guards this via `_liveside_ok()` (never places a
> `category == "soul"` item in a liveside location unless Night is already
> reachable at that fill step) — confirmed by the CSV's own "NIGHT required
> only for GOVI" notes. AP had no equivalent guard, and unlike the
> standalone, AP places "Dark Soul" items with **zero** location-category
> restriction (any location, any category, per `generate_output()`'s own
> comment) — meaning a Dark Soul could land on, say, a liveside weapon slot
> reachable without Night, get retyped to RSC_X_GOVI, and become silently
> uncollectible until Night despite AP's logic thinking it was already
> reachable. Confirmed with the user (in-game knowledge, not verifiable
> from code/CSV alone) that `RSC_X_DARK_SOUL` — a distinct, real RSC type
> already used natively for a handful of deadside Asylum "Engine Block"
> soul pickups (`as4dkeng`/`as2exper`/`ah2playr`) — does NOT carry this
> Night requirement, even when placed in an actual liveside level. Fixed
> `generate_output()`'s Dark Soul retyping in `__init__.py` to choose
> `RSC_X_DARK_SOUL` instead of `RSC_X_GOVI` whenever the target location's
> `level_region` is in `LIVESIDE_REGIONS` (imported from `.fill`, already
> defined there for this world's own `_liveside_ok()`-equivalent check).
> No `ap_patcher.py` or `client.py` changes needed — `ap_patcher.py`
> already fully supports `RSC_X_DARK_SOUL` as a physical write target
> (its own `DARK_SOUL_TYPES` set, correct marker-FX Y offset), and
> client-side soul detection (`SOUL_COUNT_RVA`, the dark-soul flag array,
> `kexShadowManAIGovi` save-file parsing) is generic to any dark soul
> object, not keyed by RSC name. Verified via real generation: an
> `insanity: true` seed placed 30 Dark Souls across `uground`/`prison`/
> `florida` liveside folders, all correctly written as `RSC_X_DARK_SOUL`;
> cross-checked the same seed's 94 `RSC_X_GOVI` placements against every
> liveside loc_key prefix — zero overlap.

> **STATUS UPDATE (2026-07-21, late night): real bug fixed — boss/true_form
> (`FIXED_SOUL_LOCS`) locations no longer queued for RSC patching.** User
> pasted a real `ap_patcher.py` log showing 7 "placements not written to
> files" validation errors. 3 of the 7 (`prison:enemies.rsc:0x53BE`,
> `florida:resource.rsc:0x4488`, `tenement:enemies.rsc:0x00BC`) were boss
> locations. `pre_fill()` locks a "Dark Soul" item into every
> `FIXED_SOUL_LOCS` entry so AP's fill skips them for placement while still
> counting them toward reachability ("auto-collected fixed checks" — the
> boss's own death is the check, not a physical RSC pickup). But
> `generate_output()`'s per-location loop iterated every AP location with an
> item indiscriminately, including these, and fed them through the same Dark
> Soul retype path as real soul/govi slots — queuing a `progression_placement`
> entry for a loc_key that points at the boss's own enemy actor record (e.g.
> `RSC_X_AVERY_MARX`), which `write_placement_patches()` can never resolve to
> a patchable RSC record. The standalone never has this problem — its own
> placement dict, built by `assumed_fill()` over `CHECKABLE_LOCS` only, never
> includes `FIXED_SOUL_LOCS` in the first place (see `fill.py`'s
> `validate_fill()`, which only mixes in `active_fixed_soul_locs` for the
> reachability simulation, not the placement dict itself). Fixed by skipping
> `raw_loc.raw.category in ("boss", "true_form")` up front in
> `generate_output()`'s loop, before any retyping — matches the standalone's
> behavior exactly (boss vanilla soul drop stays untouched).
>
> The remaining 4 of the 7 errors (`salvage:resource.rsc:0x552A/0x5602/0x5692/
> 0x56DA`, all `category="cadeaux"`) are a separate, likely pre-existing data
> issue: those 4 rows in `data/locations.csv` have `is_tracked=FALSE` with a
> note ("Section 2 RSC - sub_region and coords need verification") — but
> `fill.py`'s `CHECKABLE_LOCS` filter only excludes on `is_verified is False`,
> never checks `is_tracked` at all, so these stayed live and fillable despite
> being flagged unreliable. This affects the standalone equally (shared
> `fill.py`) — not yet fixed, needs a decision on whether `is_tracked=FALSE`
> should exclude the same way `is_verified=False` does, or whether these 4
> offsets just need re-verification in the CSV.
>
> **Tried and reverted same day:** a blanket `UNTRACKED_LOCS` filter
> (excluding every `is_tracked=False` row from `CHECKABLE_LOCS`, mirroring
> `UNVERIFIED_LOCS`) was implemented in both `fill.py` copies, then checked
> against the actual CSV distribution before shipping — `is_tracked=FALSE`
> turns out to span 2423 of 3055 rows (79%): 2215 `barrel` (already excluded
> regardless via `_ALWAYS_EXCLUDE`), but also 161 `cadeaux` and 37 real
> `weapon`/`progression`/`accumulator`/`retractor`/`lore`/`eclipser`/`gad`/
> `bonus` rows that are currently live, working AP checks. `is_tracked`
> is evidently a general "does the engine persist this record's collected
> state" flag (ties to `track_type`'s persistent-vs-volatile-barrel values),
> not a per-row reliability signal the way `is_verified` is — the blanket
> version would have silently pulled 198 real, working locations out of both
> patchers. Reverted before landing.
>
> **Fixed correctly, same session:** flipped `is_verified=FALSE` on just
> those 4 salvage rows in `data/locations.csv` (data-only change, no code
> change) and regenerated `extracted_locations.py` in both repos via
> `tools/generate.py`. This reuses the existing, already-correct
> `UNVERIFIED_LOCS` exclusion path — zero blast radius on anything else.
> Confirmed via a direct `RAW_LOCATIONS`/`fill.py` check in both repos: all
> 4 loc_keys now report `is_verified=False` and are absent from
> `CHECKABLE_LOCS`. Live cadeaux count drops from 657 to 653 as a result.

Ground truth: standalone `patcher.py` CLI (31 settings) + `gui.py` FIELD_DEFAULTS,
diffed against AP `options.py` (13 options) and `generate_output()` wiring.

## A. Wired into AP but BROKEN — crashes generation if enabled

The April-merge copies were never adapted to package context. `patcher.py`
imports them relatively (`from .enemy_randomizer import ...`) so import works,
but *inside* each module the function-level imports are absolute and fail at
call time (ModuleNotFoundError):

| Option | Failure |
|--------|---------|
| `shuffle_enemies`, `shuffle_true_forms` | `from extracted_enemy_locations import ...` — file **not copied** into world folder at all; also `from fill` / `from access_rules` (absolute) |
| `shuffle_music` | `from kpf_handler import ...` — file exists but absolute import won't resolve |
| `shuffle_voices`, `shuffle_weapons_sfx` | same `kpf_handler` absolute import in sfx_randomizer |

No try/except around any of these call sites → hard generation crash.
`gad_pickup_patch.py` is fine (self-contained, stdlib only).

## B. Present and believed working

gate_preset, shuffle_gad_temples, shuffle_weapons, shuffle_lore, shuffle_bonus,
progression_balancing, insanity (bool only — see C), game_dir, death_link,
start_inventory_from_pool.

## C. In standalone, missing from AP entirely

**Cosmetic / easy adds** (module refresh required first, see E):
shuffle_enemies_sfx, shuffle_ambients + ambient_mode, shuffle_sky,
enemy_mix_movement, enemy_uncap_counts, enemy_mode `difficulty` choice
(AP has only full/contextual; standalone default is difficulty).

**EXE-patch knobs** (patch modules never copied, see D):
starting_health, altar_health_grant, altar_cadeaux_required,
fogometers_cadeaux_required, death_penalty, soul_threshold_mode.
~~piston_combos~~ — **DONE (2026-07-21).** See status update at top.

**Logic-affecting** (needs AP region/rule work, not just wiring):
~~entrance_mode~~ — **DONE for deadside_only (2026-07-21)**, cross_hub still
not ported. See "Current state" at top. `shuffle_prisms` also remains.
Graded insanity (1–3) is **not** a gap — see "Current state": `Insanity`
(now `Cadeaux Key Items`) is a deliberately cadeaux-only toggle, not a
truncated tier ladder.

~~open_gates N~~ — **DONE (2026-07-20).** Turned out to need no
access_rules.py changes: gate_preset already resolved into `self.gate_values`
in `generate_early()`, before `create_regions()`, exactly like the
standalone's fill.py-time application. Added `OpenGatesN` (Range, -1 to 6,
default -1 = "use the preset's own baked-in value") to `options.py`;
`generate_early()` now overrides `self.gate_values` with it the same way the
preset-only path always did (see that function's inline comments). Also note
~~`max_sl`~~ was already done in Tier 2 (this line was stale) and
~~`patch_tracker toggle (AP: not exposed)`~~ under Convenience/N-A below is
also stale — both exist in options.py as of the Tier-2 pass.

**Convenience / N-A:**
starting_item + bundles (mostly covered by AP-native start_inventory_from_pool),
patch_tracker toggle (AP: not exposed), settings_string (GUI-only).

## D. Support files in standalone but absent from AP world folder

death_penalty_patch.py, soul_threshold_patch.py, health_patch.py,
cadeaux_patch.py, dark_engine_patch.py, rsc_utils.py,
extracted_enemy_locations.py, randomizers/ (ambient, boss, entrance, sky —
boss_randomizer is new since the April merge), patchers/ (levels_txt_patcher,
loc_english_patcher).

## E. Module drift

All four copied modules DIFFER from standalone current versions
(enemy 294 vs 491 lines, music 165 vs 230, sfx 201 vs 244, gad_pickup 677 vs 661).
Standalone has since reorganized randomizers into a `randomizers/` subpackage.
**Recommendation: don't patch the stale copies — re-copy current standalone
modules, convert their internal imports to package-relative, and copy the
missing data/support files.**

## F. Random support

AP YAML natively supports `random` on Toggle/Choice/Range, so every
standalone `--*-random` flag comes free once the option exists — satisfies
the CLAUDE.md seed-controlled-random rule with no extra code.

## Effort tiers

1. **Fix A + refresh E + cosmetic C** — copy files, fix imports, add ~8
   options + config keys. One focused session; low risk.
2. **EXE-patch knobs** — copy 5 patch modules, add 7 options, wire config.
   One session; mechanical (mirrors standalone `_build_cmd` wiring).
3. **Logic-affecting** — entrance rando needs AP region graph + client
   level-tracking awareness; open_gates/max_sl/graded-insanity need fill.py
   forward-port (the deliberately-unmerged June deltas overlap here).
   Multiple sessions; do after live-memory work stabilizes.
