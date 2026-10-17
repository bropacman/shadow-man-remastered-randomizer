# Scope: cross-hub entrance shuffle in the AP world

Status: scoped 2026-10-14, not started. AP world + `ap_patcher.py`.

## Goal

Add `entrance_mode: cross_hub` to the AP world, matching the standalone:
the 9 Deadside portals and the 5 liveside soul gates are shuffled as one
pool of 14. A Deadside portal can lead into a Dark Engine soul-gate room,
and a liveside soul gate can lead to a Deadside region. Each return trip
goes back to the portal you came from.

## Why it's feasible now

When `deadside_only` was ported (2026-07-21), the AP region graph didn't
model the Dark Engine side well enough, so cross-hub was left out
(`apworld/docs/dev/AP_FEATURE_GAP.md` lists it as "not ported"). Today the
graph has what cross-hub needs:

- Each liveside level is its own region, and each has a separate edge into
  its Dark Engine section (`regions.py`, "Liveside → Engine Block
  sections") with the right item rule: Night, plus Prison Key Card, Poigne
  or Gad 3 where needed.
- The 5 Engine Block sections are separate regions
  (`Asylum: Engine Block - London`, etc.).
- Deadside portals already go through one generic loop under
  `deadside_only` (`DEADSIDE_PORTAL_GATE` → `DEADSIDE_PORTAL_REGION`).

Cross-hub is mostly rewiring those two sets of edges from one shuffle
table. The `.cut` patching is done too: the standalone uses
`randomizers/entrance_randomizer.py` for cross-hub today (a v1.2.2 seed
patched 14 outbound + 14 return routes), and `ap_patcher.py` already
imports it.

## What already exists

| Piece | Where | Cross-hub ready? |
|---|---|---|
| Shuffle with constraints | `randomizers/entrance_randomizer.py` `shuffle_unified()` | Yes, standalone only. AP needs its own copy (the apworld can't import repo-root code) |
| `.cut` patching | `apply_unified_shuffle()`, called by `ap_patcher.py` | Yes. `ap_patcher.build_deadside_unified_shuffle()` only builds the Deadside half |
| Region graph | `apworld/regions.py` `create_regions()` | Deadside half only; soul-gate edges are hard-wired |
| Standalone logic | root `regions.py` `build_level_rules()` | Handles cross-hub generally (soul-gate portal = liveside completion) |
| Gate-SL safety | `fill.py` `_shuffle_gates()` | Only adjusts for `deadside_only` |
| Option | `apworld/options.py` `EntranceMode` | `off`, `deadside_only` |

## Design

### Shuffle (AP world, `generate_early()`)

Port `shuffle_unified(mode="cross_hub")` into the apworld, using
`self.random`. Keep **constraint A**: Asylum: Gateways must be behind a
Deadside portal. Reaching liveside requires the Cathedral, which is
inside Asylum, so putting Asylum behind a soul gate is circular.

Drop the standalone's **constraint B** (Salvage's soul gate can't lead to
a Gad temple). It exists because Gad powers can be vanilla in the
standalone. In AP they're always shuffled items, so the logic covers it.

**Decision: also keep Wasteland and Temple of Fire on Deadside portals.**
`_shuffle_gates()` protects these three early regions (Wasteland, Asylum,
Temple of Fire) by pinning whichever gate guards them to a low SL. A
region behind a soul gate has no SL gate to pin. It would sit behind
Night + a liveside item, which can shrink sphere 1 a lot. In the
standalone that's only a retry; in a multiworld it's a failed generation
for everyone. Pinning the three keeps the existing safety logic valid (it
then runs for cross-hub the same way as for `deadside_only`). The other
11 spokes, including all 5 Dark Engine rooms, still shuffle freely.
Revisit after generation measurements if it feels too tame.

### Shuffle format (slot data + `.apshadowman`)

Today `entrance_shuffle` is `{portal_file: dest_portal_file}` (for example
`"LE_Wast.cut": "LE_Gad1.cut"`). That breaks for cross-hub: four soul-gate
portals are all named `soulexit.cut`. New format, used for both modes:
`{"deadside/LE_Wast.cut": "prison/soulexit.cut", ...}`.

Readers must accept both formats: `ap_patcher.py` (old `.apshadowman`
files) and the UT passthrough in `generate_early()` (old slot data). A
key without `/` means `deadside/`.

### Regions (`apworld/regions.py`)

When the mode is `cross_hub`, replace both hard-wired edge sets with one
loop over the shuffle:

- **Source:** a Deadside portal → from Marrow Gates, rule
  `make_portal_rule(DEADSIDE_PORTAL_GATE[file])` (as today). A soul-gate
  portal → from that liveside region, rule = today's `engine_rules[level]`
  (Night + item). Reaching the liveside region already includes the
  retractor/schism rules.
- **Destination:** a Deadside spoke → `DEADSIDE_PORTAL_REGION`. A Dark
  Engine room → its Engine Block section (`DKE_ARRIVAL_TO_REGION`
  equivalent, keyed by soul gate).
- Entrance names say what leads where, e.g.
  `Asylum: Engine Block - London [deadside/LE_Wast.cut portal, GATE_DEADSIDE_WASTELAND]`,
  so the spoiler and UT read well.

The internal edges (Cathedral, Experimentation Rooms, Cageways → Engine
Block via the tram, liveside → night) don't change.

**Safe by construction:** AP models each Engine Block section as
reachable only through its own soul gate. If the sections are actually
connected inside the Dark Engine, the logic is just conservative (it
never claims something reachable that isn't), so cross-hub can't make it
unsafe.

### Gate safety (`apworld/fill.py` `_shuffle_gates()`)

With the three pinned early regions on Deadside portals, extend the
`entrance_shuffle.mode == "deadside_only"` substitutions to cover the
Deadside half of a cross-hub shuffle. A Deadside spoke that lands behind
a soul gate keeps its vanilla `REGION_GATES` entry, as the standalone does
today.

### Patching (`ap_patcher.py`)

Replace `build_deadside_unified_shuffle()` with a builder for the new
format (both modes) that produces a full `UnifiedShuffle`. Then
`apply_unified_shuffle()` and `unified_spoiler_section()` work unchanged.
Update `RELEASING`/notes: older `.apshadowman` files still apply.

### Client

No change expected. The client tracks levels by live level ID and
doesn't read the entrance table. The Unique Retractor Keys patch hooks
schisms, not soul gates. **Check while testing:** the client's
level-entry resync behaves when arriving at a Deadside region from a
liveside soul gate.

## Work items

1. `options.py`: `option_cross_hub = 2`; describe it, including the three
   pinned regions.
2. `__init__.py`: cross-hub shuffle in `generate_early()`; new shuffle
   format in slot data; UT passthrough accepts both formats.
3. `regions.py`: cross-hub edge loop (Deadside portals + soul gates).
4. `fill.py`: gate-safety substitutions for cross-hub's Deadside half.
5. `ap_patcher.py`: build the full shuffle from either format; spoiler.
6. `ap_gui.py`: add `cross_hub` to the entrance dropdown (`node --check`).
7. Tests (`apworld/test/__init__.py`): a `TestCrossHub` class or two
   (different seeds) that generate; a unit test that Asylum, Wasteland and
   Temple of Fire always land on Deadside portals and the shuffle is a
   14-way bijection; old-format slot data still parses.
8. Docs: `apworld/README.md` (drop "not ported"), `guide_en.md`,
   `AP_FEATURE_GAP.md`.

## Verification

- CI world tests run the new classes against real Archipelago (not on the
  maintainer's PC).
- One offline `ap_patcher.py` run on a cross-hub `.apshadowman` against
  the extracted game files: 14 outbound + returns patched, no errors.
- In game, one seed: take a Deadside portal that leads into a Dark
  Engine room and back out; take a liveside soul gate that leads to a
  Deadside region and back out. Both should return to where you entered.

## Open questions

- Pinning Wasteland/Temple of Fire (above): agree, or let them shuffle
  and rely on generation retries?
- Does the cageway tram (Cageways → Engine Block, fixed in both games)
  stay out of the pool? The standalone keeps it fixed; this scope does too.
