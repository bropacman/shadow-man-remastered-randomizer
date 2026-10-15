# Scope: Life Altar offerings as AP location checks

Status: phase A built 2026-10-15 (`life_altar_checks` option). AP world only. Both in-game
questions answered by the maintainer 2026-10-15 (see Logic and "To verify").

## Goal

Each Cadeaux offering at a Wasteland life altar sends an AP location
check, making Cadeaux matter as a key item. **Purely additive**: the altar
keeps its vanilla behavior (spends Cadeaux, grants health). Shuffling the
health reward into the pool ("Life Upgrade" items, altar grant patched to
0) is a possible later phase, not part of this one.

## What already exists

| Piece | Where | Notes |
|---|---|---|
| Altar flags | exe flag table | `CF_LIFEALTAR00`..`04` = indices **35–39**, `CF_LIFEALTARSEEN` = 40. Read from the CF name-pointer table (file 0xCA1090, stride 8), calibrated against known `CF_CUSTOM00`=279 and `CF_SAVE_LIMIT_HARD`=333. |
| Live flag reads | `client.py` `read_named_flag(pm, base, index)` | Same mechanism as Light Soul / retractor-key detection. No exe patch, no save reading. |
| Altar cost | `cadeaux_patch.py` | One shared value for all 5 altars + the Fogometers door (`FUN_14043f2f0`). Option `altar_cadeaux_required`. |
| Health per altar | `health_patch.py` | One shared value (`MOV R8D` in the same handler). Unchanged by this feature. |
| Cadeaux counting in logic | `access_rules.py` `cadeaux_666()`, `__init__.py` `collect_item()` | Counts the Cadeaux item group incl. bundles. |

Per-altar costs would need new exe work (single shared handler) and are
out of scope.

## Design

### Option

`LifeAltarChecks` (Toggle, default off). Requires Cadeauxsanity; without
it there are no Cadeaux items. Enforced in `ShadowManWorld._validate_options()`
(warn + turn off), and mirrored in `ap_gui.py`'s option-dependency JS, per
the CLAUDE.md rule that GUI and backend rules stay in step.

### Locations: count-based, not per-altar

Five locations, `Deadside - Wasteland - Life Altar Offering 1`..`5`.
Offering N is sent when **N or more** of flags 35–39 are set, regardless
of which physical altar was used. (Maintainer confirmed 2026-10-15: each
altar costs the same Cadeaux and they can be used in any order.) Reasons:

- Logic for "offering N" is simple and order-independent: it needs
  N × altar cost Cadeaux.
- Per-physical-altar locations would need to know which altar maps to
  which flag and each altar's own access, with no gain for the player.

### Location IDs: a fixed reserved range

`location_table` IDs are assigned by position (`BASE_ID + idx`), so any
CSV change (e.g. a verified flag) shifts every later ID. The altar
locations must not ride on that: give them a fixed block independent of
the CSV, e.g. `BASE_ID + 900_000 + n`, registered in `location_name_to_id`
(by friendly name, as of commit 307a79b) always, created per seed only
when the option is on. Same "reserve the superset" pattern as cadeaux IDs.

### Logic

- Region: Wasteland. The life altars share the Baton Altar's requirement
  (maintainer, 2026-10-15). The Baton Altar's CSV row is `wastland`, zone
  24, requirement `N`, so no sub-gate: reaching Deadside - Wasteland is
  enough. Every offering uses the same region access.
- Rule: `count_group("Cadeaux") weighted by bundle size >= N × altar_cost`
  (reuse the weighting `cadeaux_666()` already does).
- Fogometers door: the game already assumes all 5 altars get used (door
  cost = fog_required − 5 × altar cost; `fog >= 5 × altar` is enforced).
  Total-collected logic stays consistent: offering 5 needs 5 × cost, the
  door needs fog_required ≥ that. No change expected; add a test.

### The real decision: Cadeaux classification

Cadeaux are `filler` today. `collect_item()` counts them anyway, but AP's
fill and progression-balancing simulations only collect advancement
items, so a Cadeaux-gated location can never be proven reachable there.
That's why the Fogometers Light Soul location is marked `EXCLUDED` (filler
only). Two options:

| | A: Excluded altars | B: Cadeaux become progression |
|---|---|---|
| Change | altar locations `EXCLUDED`, like the Light Soul | Cadeaux (+ bundles) → `progression_skip_balancing` |
| What altars can hold | filler only | anything |
| "Cadeaux as key item" | weak: checks exist but hold nothing important | real |
| Fill cost | none | hundreds of progression items; bundles help |
| Risk | low | generation time; revisit the Light Soul EXCLUDED workaround |

Recommendation: ship **A** first (small, safe, gives the checks), then do
**B** as its own change with generation-time measurements at bundle sizes
1, 5, 10 across several seeds. B is what makes Cadeaux feel like a key
item; A alone mostly adds 5 filler checks.

### Client

In the memory watcher loop, each poll: read flags 35–39, count set bits,
send offerings 1..count not yet sent (existing dedupe against
`locations_checked` covers repeats). Only when slot_data says the option
is on. Add the 5 IDs to slot_data's location map so the GUI tab and UT
show them under Wasteland.

### Not affected

Standalone randomizer (no live client; an altar can't hold an item).
Exe patches. Save files. Existing location IDs.

## Work items

1. `options.py`: `LifeAltarChecks`; option group "Cadeaux".
2. `locations.py`: fixed-range altar location IDs + friendly names.
3. `regions.py`: create the 5 locations in Wasteland when enabled; rule as
   above; `EXCLUDED` (phase A).
4. `__init__.py`: `_validate_options()` dependency on Cadeauxsanity;
   slot_data flag + location map entries.
5. `client.py`: flag-count detection in the memory watcher.
6. `ap_gui.py`: checkbox + dependency JS (`node --check` the script).
7. Tests (`apworld/test/__init__.py`): option off → no altar locations;
   on → 5 locations, unique names/IDs, IDs stable across a CSV change;
   rule thresholds; validation turns it off without Cadeauxsanity.
8. Docs: option description, `guide_en.md` mention.

## To verify in game before relying on it

- Using an altar sets the expected `CF_LIFEALTAR0x` flag and the flags
  survive save/reload. Not tested yet; the build assumes it (maintainer,
  2026-10-15). Confirm with `/debug` and the client's flag reads during the
  first test seed: if offerings don't send, check this first.
- ~~Whether any altar is behind a Wasteland sub-gate.~~ Answered: no, same
  as the Baton Altar (`N`).

## Later (phase B / C)

- B: Cadeaux as progression (see table). Client note: an altar check can
  then hold a real item from your own world, which has no physical pickup.
  `client.py` `_inject_item()` skips self-found items (`source_player ==
  self.slot`) for most methods, assuming the game already granted them;
  items from altar locations (`item.location` in `life_altar_ap_ids`) must
  bypass that skip. Phase A is safe without it: altars hold only filler,
  and Cadeaux/Trap-Bonus items are always injected.
- C: shuffle the health reward: patch altar grant to 0, add 5 "Life
  Upgrade" items applied by the client (writes max health).

## Appendix: other save flags that could become checks (post-release)

Full CF flag table: 336 names, pointer table at file 0xCA1090 (stride 8),
index = position. The client currently reads only `CF_GOT_LIGHTSOUL`, the
Legion flags and `CF_CUSTOM00-04`. All of these are flag reads only, no
RSC/exe patching. Grouped by how much logic they'd need:

| Tier | Flags | Logic needed |
|---|---|---|
| Region access only | `CF_BEENTO_LEVEL00..25` (first visit per level) | the level's existing region entrance; low-value "free" checks |
| Existing item rules | `CF_AP_ACCUM0..4_PLACED` (accumulators placed), `CF_RETRACTOR01..05USED` (schisms), `CF_DARKENG_STOPPED1..6` (pistons), `CF_ECLIPSER_0..2` | `x3_accumulator`, retractor rules, `pistons()`, eclipser parts + region; check interplay with Unique Retractor Keys |
| Bosses not already fixed souls | `CF_YORT_DEAD`, `CF_SERAPHDEAD`, `CF_COPTERDEAD`/`CF_COPTER2DEAD`, `CF_NCOPTERDEAD`/`CF_NCOPTER2DEAD` | boss region + weapon assumptions |
| Puzzles / world switches | padlocks (`CF_PRISONPADLOCK*`, `CF_UNDERGROUNDPADLOCKSHOT`, `CF_FL_PADLOCK`, `CF_MO_PADLOCK1/2`), `CF_TENEMENTPOWERON`, `CF_FL_FUSEBOX`, `CF_SLUICEGATE_OPEN`, `CF_PIPEHATCH*`, `CF_WASTELANDWINDMILL`, `CF_ZIGTRIALDONE`, `CF_G1_ZIGLEVEL1..6` | per-flag research: where it is, what it needs (gun, gad power, etc.) |
| Teddy bears | `CF_TED_*` (19, one per level/hub) | find out what sets them first |
| Skip | `CF_HM_*` hint messages, cutscene/dialogue flags, `CF_CUSTOM*`, difficulty/ironman | not player achievements |

Before any of these: confirm in game that each flag flips on the expected
action and survives save/reload, same as the altar flags.
