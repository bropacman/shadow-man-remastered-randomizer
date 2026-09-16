# Shadow Man Remastered Randomizer — Project Handoff

Two sibling repos, worked together:

- **AP world**: `C:\Users\jonat\Documents\Archipelago\worlds\shadowman` — the Archipelago multiworld integration.
- **Standalone**: `C:\Users\jonat\Documents\shadow-man-remastered-randomizer` — the original local randomizer, and (as `ap_patcher.py`/`ap_gui.py`) the tool that actually patches a player's game once an AP seed is generated.

Both are driven from the same source data (`data/locations.csv`, duplicated as `extracted_locations.py` in each repo — keep them in sync via `tools/generate.py`).

## Most important files

### AP world (`Archipelago/worlds/shadowman/`)

| File | Role |
|---|---|
| `__init__.py` | `ShadowManWorld` — `generate_early`, `create_regions`, `create_items`, `set_rules`, `pre_fill`, `generate_output`, `fill_slot_data` |
| `options.py` | `ShadowManOptions` dataclass, 42 options |
| `regions.py` | Region graph, entrance shuffle wiring, `_SKIP_CATS` (barrels permanently excluded) |
| `access_rules.py` | `R` singleton + `BoundR` per-world override wrapper (gate/pistons/schematic rules — AP generates multiple worlds concurrently, so no global mutation) |
| `fill.py` | `CHECKABLE_LOCS` / `UNVERIFIED_LOCS` / `AP_LOCATIONS` / `FIXED_SOUL_LOCS` — source-of-truth constants only. **Not used for real AP placement** (AP core's own `Fill.py` does that); this file's `assumed_fill()` is only a standalone CLI validation harness. |
| `locations.py` | `location_table` — the real AP location ID registry, built from `CHECKABLE_LOCS` |
| `items.py` | `ShadowManItem` / `item_table` |
| `extracted_locations.py`, `extracted_enemy_locations.py` | Auto-generated from `data/locations.csv` — never hand-edit |
| `constants.py` | `LEVEL_FOLDERS`, height/FX/asset-override tables |
| `guide_en.md` | Player setup guide (rewritten 2026-07-21 for the portable-JSON-only workflow) |
| `AP_FEATURE_GAP.md` | Running audit of standalone-vs-AP feature parity — long, needs a cleanup pass (see below) |

`worlds/shadowman/patcher.py` was **deleted** 2026-07-21 (the old "hybrid game_dir" local-patch path). Don't resurrect it — every seed now goes through the portable `.apshadowman` + `apply_ap_seed.py` workflow exclusively.

### Standalone (`shadow-man-remastered-randomizer/`)

| File | Role |
|---|---|
| `CLAUDE.md` | Project instructions (file integrity protocol, seed-controlled-random rule). **Key Files table is stale** — see cleanup list below. |
| `ap_patcher.py` | Portable-JSON patcher — applies a generated `.apshadowman` seed to a real local game install. This is the only AP patching path now. |
| `apply_ap_seed.py` | CLI entry wrapping `ap_patcher.py` |
| `ap_gui.py` | GUI companion app (Generate YAML / Apply AP Seed tabs). `COMPANION_VERSION = "v1.2.2"` |
| `patcher.py` | Standalone (non-AP) local CLI patcher, uses `assumed_fill()` |
| `gui.py` | Standalone (non-AP) GUI |
| `fill.py` | Standalone's `assumed_fill()` + the **canonical** `CHECKABLE_LOCS`/`UNVERIFIED_LOCS` definitions — AP's copy should always match this one's filtering logic |
| `data/locations.csv` | Master location data (3055 rows). Regenerate `extracted_locations.py` in **both** repos via `tools/generate.py` after any edit. |
| `access_rules.py`, `regions.py`, `constants.py` | Same names/roles as the AP copies — two independent implementations kept conceptually in sync, not shared by import |
| `dark_engine_patch.py` | Piston-combo EXE + journal patch — imported directly by `ap_patcher.py` (not duplicated) |
| `randomizers/entrance_randomizer.py` | Entrance/portal `.cut` shuffle logic — also imported directly by `ap_patcher.py` |
| `randomizers/enemy_randomizer.py`, `ambient_randomizer.py`, `sky_randomizer.py`, `boss_randomizer.py` | Cosmetic/enemy shuffle modules |
| `death_penalty_patch.py`, `soul_threshold_patch.py`, `health_patch.py`, `cadeaux_patch.py` | EXE-patch modules — these ARE duplicated as literal files inside the AP world folder (unlike `dark_engine_patch.py`/`entrance_randomizer.py`, which are imported directly). Asymmetric on purpose but easy to forget. |
| `rsc_utils.py` | Generic RSC record read/write (`build_rsc_record`, `inject_rsc_record`) — used for GAD pickups and marker FX, not cadeaux-specific |
| `kpf_handler.py` | KPF archive extraction/repacking |
| `tools/generate.py` | Regenerates `extracted_locations.py` from `data/locations.csv` |

## What changed this session (2026-07-21)

1. **Insanity relabeled.** AP's `Insanity` toggle only ever gated cadeaux locations (soul locations were never gated, on or off; barrels are permanently excluded regardless). Renamed to `Cadeaux Key Items` in `options.py` and `ap_gui.py` with an accurate docstring/tooltip — no longer implies a "Full"/Tier-3-equivalent scope that doesn't exist.
2. **Real bug fixed: `UNVERIFIED_LOCS` filter was missing from the AP world's `fill.py`.** `CHECKABLE_LOCS` never actually excluded `is_verified=False` locations, despite `CLAUDE.md` documenting that architecture and the standalone repo's own `fill.py` already having it. This let 13 confirmed-phantom cadeaux locations (4 in `t4ndgad`, a cut sub-zone with no in-game entrance, plus 9 flagged out-of-bounds/invisible/unconfirmed) become live, fillable AP checks. Ported the filter; live cadeaux count is now 657 (was 670), true count is 666 once the remaining 9 flagged rows are re-audited.
3. **Real bug fixed: cadeaux-injection normalization was missing from `ap_patcher.py`.** ~434 of the 670 cadeaux locations are physically barrel-shaped RSC records (`RSC_X_BARREL_A/D/L`) that only register as a cadeaux reward because of their `track_type`. The standalone `patcher.py` normalizes these to `RSC_X_CADEAUX` when the location gets shuffled to a different level (so the reward always registers regardless of the destination's `track_type`), plus a `BARREL_RSC_SUBSTITUTIONS` visual-asset fixup. Both blocks were silently dropped when `ap_patcher.py` was ported from `patcher.py` — ported them back in (`write_placement_patches()`).
4. Wired `UNVERIFIED_LOCS` into `ap_patcher.py`'s `validate_final_seed()` as a defensive suppression net, matching the standalone's own pattern.

Both #2 and #3 above are the same failure class: **a whole logic block silently dropped during a port**, undetected because nothing diffed the ported function against its source line-by-line. Worth remembering as a review habit going forward.

## Doc cleanup still needed

1. **`CLAUDE.md` (standalone repo) Key Files table is stale.** Doesn't mention `ap_patcher.py`, `apply_ap_seed.py`, or `ap_gui.py` at all — these are now the primary AP-facing tools and arguably belong at the top of the table, not absent. Also missing: `dark_engine_patch.py`, `randomizers/` subpackage, `rsc_utils.py`, `kpf_handler.py`, `data/locations.csv`.
2. **`AP_FEATURE_GAP.md` has accreted 6+ dated status-update blocks** stacked at the top of the file, making "what's the current state" hard to find without reading the whole history in order. Recommend either consolidating into one current-state summary + compact changelog appendix, or adding a one-line "CURRENT STATE" pointer at the very top.
3. **`AP_FEATURE_GAP.md` line ~73** ("Remaining Tier 3: cross_hub entrance mode, graded insanity (1–3, AP is bool-only), shuffle_prisms") is now stale — insanity isn't a "missing tiers" gap anymore, it's a deliberately narrower, correctly-labeled feature (cadeaux-only). Already superseded by this session's new status block below it, but the old line itself wasn't rewritten.
4. **`CLAUDE.md`'s `is_verified`/`UNVERIFIED_LOCS` architecture description was accurate for the standalone repo but had silently regressed in the AP world's `fill.py`** until today. Worth a short addendum flagging "AP world fill.py can drift from standalone fill.py — diff before trusting either copy matches the other."
5. **`data/locations.csv` — 9 cadeaux rows still need real in-game re-audit** (florida/tenement/salvage ×5/ah1cagew/as2exper, all flagged `is_verified=FALSE` with `?`-style notes). Not resolved this session — needs your own confirmation in-game. True cadeaux count is 666; currently sitting at 657 until these are cleared.
6. **New standing review habit worth writing down somewhere** (`CLAUDE.md` is the natural home): when porting a function from `patcher.py` → `ap_patcher.py`, or from standalone `fill.py` → AP world `fill.py`, diff line-by-line against the source rather than trusting the port is complete — two silent block-drops were found this session alone (`UNVERIFIED_LOCS` filter, cadeaux-injection normalization).

## Still-open tasks (not part of this session's work)

- **Full local playtest**: generate a seed → `apply_ap_seed.py` → actually play it. Can't be done from a sandboxed environment; needs a real game install.
- **Rare hard-preset fill failure**: `gate_preset="hard"` + low `max_gate_sl` occasionally starves Fill of unrestricted filler slots at certain seeds. Reproduced but not caused by any recent work — needs its own investigation.
