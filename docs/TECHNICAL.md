# Shadow Man Remastered Randomizer — Technical Reference

This document explains how the randomizer works internally. It is aimed at anyone
who wants to understand the system without reading the source code — contributors,
curious players, or future maintainers.

---

## Table of Contents

1. [KPF Archives — The Mod Layer](#1-kpf-archives)
2. [How Item Randomization Works (RSC patching)](#2-how-item-randomization-works)
3. [Gad Powers — The Physical Pickup System](#3-gad-powers)
4. [Coffin Gate Soul Level Shuffle](#4-coffin-gate-soul-level-shuffle)
5. [From CSV to Logic — How Location Data Flows](#5-from-csv-to-logic)
6. [Logic Edge Cases](#6-logic-edge-cases)
7. [Enemy, Music, and SFX Shuffle](#7-enemy-music-and-sfx-shuffle)
8. [Spoiler Log](#8-spoiler-log)
9. [Recent Fixes](#9-recent-fixes)

---

## 1. KPF Archives

### What KPF files are

Shadow Man Remastered stores all its game data — levels, RSC files, audio,
textures — inside `.kpf` archives. A KPF is structurally identical to a ZIP
file using `ZIP_STORED` (no compression). The base game ships with several
archives named `ShadowManEX0*.kpf` sitting in the root install folder.

### The mod layer

The game natively supports a `mods/` folder. Any `.kpf` placed there is loaded
**before** the base archives, acting as an override layer. Only files that
differ from vanilla need to be present in the mod KPF — everything else falls
through to the originals. This means:

- **Original files are never modified.** The randomizer reads from the base
  KPFs, makes changes in a temporary working directory, and packs only the
  changed files into `mods/shadowman_randomizer.kpf`.
- **Restoration is instant.** Delete (or rename) the mod KPF and the game
  returns to vanilla on next launch, with no file verification needed.
- **Multiple mods are supported.** Load order among mod KPFs is alphabetical;
  `shadowman_randomizer.kpf` is named to sort predictably.

### How the patcher uses KPFs

`kpf_handler.py` provides the full KPF workflow:

1. **Index** — scans all base KPFs and builds an index of every internal path
   to the archive that contains it.
2. **Extract** — pulls the needed files (RSC, links.e2o, EXE, audio) out of
   the appropriate base archive into a temporary working directory.
3. **Modify** — the patcher applies all changes (name swaps, gate writes,
   deco renames, etc.) to the extracted copies.
4. **Repack** — assembles all modified files into `shadowman_randomizer.kpf`
   using `ZIP_STORED` and installs it to `mods/`.

The temporary working directory is cleaned up automatically on success. If the
patcher fails mid-run it is left in place so the partially-modified files can
be inspected.

---

## 2. How Item Randomization Works

### The RSC record format

Every item that exists in the world — souls, weapons, key tools, lore books —
is represented by a record in a binary file called a **quest.rsc**. Each level
folder under the game's `levels/` directory contains one. The RSC format is a
flat array of 72-byte records. Among other fields, each record stores:

- A 30-byte ASCII name string (e.g. `RSC_X_ENGINEERS_KEY`)
- XYZ world coordinates (three 32-bit floats)
- A zone index and instance ID

The game engine reads the name string and uses it to determine what item to
spawn at that position. **The randomizer works entirely by rewriting those name
strings.** No game logic, no scripting, no asset replacement — just swapping
the names in place within the binary records.

### How a seed is applied

When the patcher runs it works in two phases:

**Phase 1 — Fill.** The assumed-fill algorithm (see section 4) decides which
item goes to which location. The output is a `placement` dictionary mapping
each location's unique key (`level_id:source_file:offset`, e.g.
`swampday:quest.rsc:0x0A2E`) to the item that should live there.

**Phase 2 — Patch.** The patcher iterates over the placement dict and for each
entry:
1. Extracts the relevant quest.rsc (or enemies.rsc / instance.rsc) from the
   game's KPF archive.
2. Seeks to the byte offset recorded in the location's entry.
3. Overwrites the name field with the new item's RSC name string, zero-padded
   to 30 bytes.
4. Writes the modified file back out.

All modified files are then repacked into a single `shadowman_randomizer.kpf`
mod archive and installed to the game's `mods/` folder. The game's mod loader
prefers files in `mods/` over the base KPF archives, so vanilla files are never
touched. Deleting the mod KPF instantly restores vanilla.

### Slot types and what can go where

Not every location accepts every item. The randomizer enforces a slot taxonomy
defined in `fill.py` as `SLOT_ACCEPTS`. In brief:

- **Soul slots, cadeaux slots, barrel slots** — only accept soul items (Dark
  Souls and Govis). These are the most numerous locations in the game.
- **Progression, weapon, lore, retractor, accumulator, gad slots** — form a
  shared pool. Any non-soul item can land in any of these slots, and any of
  these slot types can receive any item from the pool (a key tool can appear
  in a weapon slot, a weapon in a progression slot, etc.).

In **insanity mode** these restrictions are relaxed further: tier 1 allows
progression items in soul slots, tier 2 adds cadeaux slots, and tier 3
removes all restrictions entirely.

---

## 3. Gad Powers

### The problem

In vanilla Shadow Man Remastered the three Gad powers (Touch, Walk, Swim) are
granted by the game engine when the player completes each temple's puzzle
sequence. They are not physical items in the world — they are scripted rewards
that the engine fires based on internal game state. This means there is no RSC
record to swap; the randomizer cannot redirect them using the standard
name-swap method.

### The solution — two parts

**Part A: EXE patch (`gad_pickup_patch.py`)**

The patcher makes targeted binary edits to `thoth_x64.exe` to add `RSC_X_GAD_PICKUP`
as a functional item type. In detail:

1. The RSC name string `"RSC_X_GAD_PICKUP"` is written into unused space in the
   executable's `.rdata` section.
2. A spare slot in the game's item dispatch table (the table the engine uses to
   decide what to do when the player picks up any named RSC object) is
   overwritten to point at the new string and assigned a visual model type_id
   (defaulting to the Prophecy book model, which is confirmed safe at runtime).
3. Two small assembly stubs are written into a code cave in `.text`. Each stub
   increments a `gad_level` counter by a different amount (+1 for a Gad
   pickup, +4 for the Poigne slot which is repurposed), calls the game's
   `apply_gad_level` function to immediately grant the corresponding ability,
   then jumps to a tail that plays a voice line and returns to the normal
   pickup flow.
4. The game's existing case handlers for type_id `0x16` (the Prophecy case)
   and `0x13` (Poigne) are redirected via `JMP` to the two stubs.
5. The eight hardcoded writes in the temple Gad-grant function are NOPed out
   so that completing a temple puzzle no longer automatically grants a Gad
   power — the physical pickup is now the only source.

The EXE patch is idempotent: the patcher checks for the `RSC_X_GAD_PICKUP`
string before applying and skips if already present. The patch is applied once
per install, not once per seed.

**Part B: RSC record injection (`setup_gad_records.py`)**

With the EXE patch in place, the engine can handle `RSC_X_GAD_PICKUP` objects —
but those objects need to exist in the quest.rsc files of the three temple levels.
`setup_gad_records.py` is a one-time setup tool that injects a single
`RSC_X_GAD_PICKUP` record into each temple's quest.rsc at surveyed coordinates
(the location of the original temple reward altar).

Injection works by finding the first empty slot in the live record window of
the file (or appending a new record if no empty slot exists), writing the
record, and incrementing the file's record count byte. The resulting file
offset is stable and is hardcoded in `extracted_locations.py` so fill.py can
treat the three Gad slots exactly like any other physical location.

### Gad shuffle disabled

When `--no-shuffle-gad-temples` is passed, the EXE is still patched (the
temple NOPs still apply — vanilla temple completion no longer grants Gad), but
the three Gad pickup slots are excluded from the fill candidate pool. Instead,
the simulation grants each Gad power automatically as the player enters each
temple region, replicating the vanilla progression order within the logic
without requiring the physical pickups to be placed.

---

## 4. Coffin Gate Soul Level Shuffle

### How gates work in vanilla

Deadside is divided by **coffin gates** — barriers that open only when the
player has collected enough Dark Souls. Each gate has an SL (Soul Level)
threshold. The SL→souls-required mapping is non-linear:

| SL | Souls |
|----|-------|
| 0  | 0     |
| 1  | 1     |
| 2  | 3     |
| 3  | 7     |
| 4  | 15    |
| 5  | 23    |
| 6  | 35    |
| 7  | 51    |
| 8  | 71    |
| 9  | 95    |
| 10 | 120   |

The thresholds are stored in `links.e2o` files inside each level's KPF entry.
Each record is 74 bytes; gate records have type `0x0C00` and store the soul
threshold as `SL × 2560` as a 32-bit integer at a fixed offset within the
record.

### How the shuffle works

`_shuffle_gates()` in `fill.py` reassigns SL values across all non-locked
gates before item placement begins:

1. A list of shuffleable gates is built (all gates not in the `locked` set).
2. Each gate is assigned a new SL drawn from a weighted distribution centered
   on its vanilla SL, with a configurable spread (`sl_spread`, default 4).
   Item-based gates (Retractor, Accumulator) get flat random weights instead.
3. A series of safety constraints is applied in priority order:
   - Gates controlling regions with fixed souls or progression slots are
     capped at SL9 (SL10 = 120 souls is unreachable if those slots are
     inaccessible).
   - The three starting gates (Wasteland, Asylum, Path 3) are capped at SL3.
   - Wasteland is further capped at SL2.
   - Path 3 is capped at SL5.
   - Each constraint is enforced by swapping the violating gate's SL with an
     eligible partner gate rather than simply clamping, so the overall
     distribution remains a true permutation.

### How the new values are written

After fill.py produces a `gate_remap` dict (`{gate_id: new_sl}`), the patcher
applies the new values by:

1. Extracting the relevant `links.e2o` files from the game's KPF archives.
2. Scanning each file for gate records matching known gate IDs.
3. Writing the new threshold value (`new_sl × 2560`) into the record at the
   fixed offset.
4. Including the modified files in the repacked `shadowman_randomizer.kpf`.

The ARC decoration records that display the gate's soul count ring in-world
are also updated to match: the patcher renames `RSC_X_COFFIN_GATE_ARC<N>`
records in the level's RSC files to reflect the new SL number, so the visual
ring indicator always matches the actual threshold.

### Gate presets

The preset system (`constants.py` → `GATE_PRESETS`) controls which gates are
locked, whether the shuffle runs at all, and whether a max SL cap is applied:

| Preset | Effect |
|--------|--------|
| `none` | No shuffle; vanilla thresholds everywhere |
| `open` | All gates set to SL0 (free); no shuffle |
| `easy` | Starting gates locked at vanilla SL; rest shuffled with SL7 cap |
| `medium` | Only the three entry gates locked; rest shuffled with SL8 cap |
| `hard` | Same locks as medium; no SL cap |
| `chaos` | No locks, no cap, no safety checks |

The `--max-sl` override (or the GUI Max SL cap selector) clamps the pool of
assignable SL values regardless of preset, and takes effect before any safety
constraints run.

---

## 5. From CSV to Logic

### The CSV as source of truth

Every randomizable location in the game is defined as a row in
`data/locations.csv`. Each row describes one item slot and contains:

| Column | Purpose |
|--------|---------|
| `level_id` | Level folder name (e.g. `swampday`, `asylum`) |
| `source_file` | RSC file within that level (e.g. `quest.rsc`) |
| `offset` | Byte offset of the name field within the file (hex, e.g. `0x002A`) |
| `friendly_name` | Human-readable slot name for spoiler logs |
| `object` | Vanilla RSC name of the item at this location |
| `category` | Slot type: `soul`, `weapon`, `progression`, `cadeaux`, etc. |
| `instance_id` | Unique ID used to deduplicate souls |
| `level_region` | Which named game area this slot belongs to |
| `sub_region` | Gate expression controlling access (see below) |
| `zone`, `x`, `y`, `z` | World coordinates (used by setup tools; not needed by fill) |

To add a new location, add a row to the CSV and re-run `python tools/generate.py`.

### Gate expressions in `sub_region`

The `sub_region` column holds a compact boolean expression describing what the
player must have or have done to reach this slot. Examples:

```
N                              # No gate — freely accessible
GATE_DEADSIDE_ASYLUM           # Must have passed the Asylum coffin gate
FLAMBEAU & BATON               # Must have both Flambeau and Baton
GATE_DEADSIDE_PATH_6 | (GAD2_WALK & GATE_DEADSIDE_ASYLUM & BATON)
```

Tokens starting with `GATE_` refer to coffin gates and are evaluated against
the current gate SL remap. All other tokens map directly to named rule
functions on the `R` object in `access_rules.py` (e.g. `FLAMBEAU` →
`R.flambeau(state, player)`).

### Code generation — `tools/generate.py`

`generate.py` reads the CSV and produces `extracted_locations.py`, a Python
file that is never edited by hand. For each CSV row it emits a `RawLocation`
dataclass instance containing all the row's fields plus a pre-compiled
`gate_expr` — a Python expression string produced by parsing the `sub_region`
column through a small recursive descent parser.

For example, the sub_region `FLAMBEAU & BATON` becomes:

```python
gate_expr = "R.flambeau(state, player) and R.baton(state, player)"
```

This expression is later passed to `eval()` inside `fill.py`'s `_reachable()`
function during placement, with a live `FakeState` and the `R` rule object
in scope. No text parsing happens at fill time — the expression is already
compiled to a string at generation time.

`extracted_locations.py` exports two objects:

- `RAW_LOCATIONS` — a flat list of all `RawLocation` instances.
- `LOCATION_TABLE` — a dict keyed by `loc_key` (`level_id:source_file:0xOFFSET`)
  for fast lookup.

### The fill algorithm

`assumed_fill()` in `fill.py` runs a single forward sweep that places all
items:

**Step 1 — Gates.** If gate shuffle is enabled, `_shuffle_gates()` runs first
and produces a `gate_remap` dict. This is passed to `build_gate_rules()` which
wires up the region graph with the new SL thresholds before any placement
begins.

**Step 2 — Candidate pool.** All checkable locations are collected (filtered
by category and any active exclusions). If gad temple shuffle is off, gad slots
are excluded from the pool.

**Step 3 — Item pool.** The item pool is built from all vanilla items at
checkable locations (souls, progression, weapons, lore, etc. — according to
the active shuffle flags). Items are sorted so progression-critical items are
placed first, souls second, and weapons/lore last.

**Step 4 — Placement sweep.** For each item in the sorted pool:
1. `simulate_playthrough()` is called on the current partial placement to find
   all locations reachable with the items placed so far.
2. The reachable, unfilled slots that accept the item's category are collected
   as candidates.
3. One candidate is chosen using a weighted random pick — weights are based on
   the depth of the slot (which gate controls the region, plus any local logic
   gates), scaled by the `progression_balancing` slider. Higher balancing pushes
   key items toward deeper locations.
4. The item is placed; the slot is removed from the unplaced set.

If no valid slot exists for a non-soul item, a **soulswap** is performed: the
next soul in the queue is moved ahead and placed first to grow the reachable
set, then the blocked item is re-queued. This handles the common case where a
key item is blocked because no reachable progression slots are accessible yet.

**Step 5 — Filler.** Any unfilled barrel and cadeaux slots receive a random
filler item from the remaining pool. Soul slots not consumed by phase 4 are
left at their vanilla values (the patcher skips them).

**Step 6 — Validation.** `validate_fill()` runs a fresh `simulate_playthrough()`
on the completed placement and checks that every checkable location is reachable
and that the end-game goal condition (`R.pistons`) is satisfied. If either check
fails, the seed is flagged (this should not happen under normal operation; the
assumed-fill algorithm is designed to guarantee beatable seeds).

### `access_rules.py` and the `R` object

`access_rules.py` defines a class `R` whose static methods are the named gate
checks referenced by `gate_expr` strings. Each method receives a `FakeState`
(the fill algorithm's lightweight inventory tracker) and a player integer, and
returns a boolean.

Gate-based checks call `R.gate(gate_id, state, player)` which resolves the
current SL for that gate from `_current_gate_sl` (populated by
`set_gate_remap()` at the start of each seed) and compares it against
`state.soul_count` using the `_SOUL_THRESHOLDS` table.

Item checks call `state.has(item_name, player)` or `state.count(item_name, player)`.

The `FakeState` tracks:
- `inv` — a dict of item name → count for all named items
- `soul_count` — total Dark Souls and Govis collected
- `retractor_count` — Retractors collected (counted separately)
- `cadeaux_count` — Cadeaux collected (counted separately)
- `reached_regions` — the set of named game areas currently reachable

The simulation updates these fields as it discovers newly reachable locations
during each sphere pass, and the process repeats until no further progress is
made.

---

## 6. Logic Edge Cases

### The Eclipser lock

The three Eclipser parts (`RSC_X_ECLIPSER_PART1/2/3`) are mutually dependent:
the `NIGHT` condition — which unlocks the Fogometers, the Lavaducts, and all
liveside Dark Soul collection — requires all three to be in the player's
inventory simultaneously.

This creates a potential deadlock: if the fill algorithm tries to place an item
behind a `NIGHT`-gated slot before all three Eclipsers are placed, it could
create a situation where two Eclipsers each require the other to be reachable.

The `GATE_GROUP` dict in `fill.py` handles this. When the algorithm evaluates
a candidate slot whose `gate_expr` contains `r.night(`, it checks whether all
three Eclipsers are already in the current inventory state. If they are not,
the slot is marked as **reserved** and excluded from the candidate list for
that item — even if the region is technically reachable. The Eclipsers are
always placed first (they have the highest placement priority), so by the time
any item needs a NIGHT-gated slot, all three will already be accounted for.

### Liveside Dark Souls

Dark Souls found in liveside regions (London, Texas, Florida, Queens, Mojave)
have an additional logic rule on top of their location's normal gate expression:
they can only be collected after NIGHT is achieved (all three Eclipsers in hand).
In lore terms, Mike needs the Eclipsers active to enter deadside from the liveside
locations; in logic terms, counting a liveside soul before NIGHT is possible
would overcount the player's accessible soul total.

During `simulate_playthrough()`, whenever the simulation encounters a soul slot
in a liveside region, it checks `R.night(state, player)` before marking it as
collected. If NIGHT is not yet achieved, the soul is skipped and the simulation
will pick it up in a later sphere once the Eclipsers are in inventory.

The fill algorithm accounts for this during placement as well: `_liveside_ok()`
prevents any soul item from being placed into a liveside slot unless the
current state already satisfies NIGHT.

---

## 7. Enemy, Music, and SFX Shuffle

These three systems are independent of item placement and run after the main
fill is complete. Each produces a `{internal_kpf_path: local_file_path}` dict
that the patcher merges into the mod KPF alongside the item patches.

### Enemy shuffle (`enemy_randomizer.py`)

Enemies are stored in `enemies.rsc` files (one per level) using the same
72-byte RSC record format as quest.rsc. The randomizer swaps RSC name strings
between enemy records — the slot positions stay fixed, only the type name
changes, so the game spawns a different enemy at each position.

Three modes are available:

- **difficulty** (default) — Enemies are sorted into five tiers by difficulty.
  Each level's depth in the world (measured by its gate requirements) determines
  which tier of enemy it receives. Early areas get tier 1 fodder; late-game
  areas get tier 4–5 threats. Movement type (ground/flying/swimming) is always
  respected — these never mix.
- **full** — Global shuffle within each movement type bucket. No difficulty
  weighting; purely random within the constraint that movement types don't mix.
- **contextual** — Shuffle within each `(context_group, movement_type)` bucket.
  A deadside interior ground enemy only swaps with other deadside interior
  ground enemies. Keeps the feel of each area consistent while still shuffling.

Records marked `enemy_locked` in the CSV are never touched. True form enemies
(which drop Dark Souls) are handled separately — their slots are remapped
alongside the item shuffle so the soul drop follows the enemy.

Enemy locations are defined in `data/enemy_locations.csv` and generated into
`extracted_enemy_locations.py` by `tools/generate_enemies.py`.

### Music shuffle (`music_randomizer.py`)

All music tracks in the game's `audio/music/` KPF folder are extracted and
shuffled globally: track A plays where track B used to play, and so on. Tracks
are bucketed by duration before shuffling so short stings don't land in slots
that expect long looping tracks. The menu theme and a small exclusion list of
ambient one-shots are left in their vanilla slots.

### SFX shuffle (`sfx_randomizer.py`)

Two independent pools are shuffled:

- **Voice lines** — all files under `audio/speech/generic/` are shuffled
  globally among themselves. Mike's generic combat and traversal lines are
  reassigned randomly.
- **Weapon sounds** — fire sounds, reload sounds, and other weapon audio are
  shuffled within curated type buckets defined in `constants.WEAPON_SOUND_SETS`.
  Fire sounds only swap with other fire sounds, reloads with reloads, etc.

---

## 8. Spoiler Log

After every successful run the patcher writes a `spoiler_seed_<N>.txt` file
next to itself (or to `--output-dir` if specified). The spoiler log contains:

- The seed number and all active flags used to generate the run.
- The gate remap table showing the new SL value for every coffin gate.
- A full sphere-by-sphere playthrough simulation: each sphere lists the new
  areas unlocked, the key items found and where they were located, and the
  running soul count and current SL at the start of that sphere.

The sphere simulation is produced by `simulate_playthrough()` in `fill.py`
with `collect_spheres=True`. It replays the exact logic the fill algorithm
used, so the spoiler log is a faithful record of the intended progression order
for that seed — not a post-hoc reconstruction.
