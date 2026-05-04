# Shadowman Remastered (thoth_x64.exe) — Randomizer Engineering Findings

## Overview

The game has two distinct reward systems:
1. **Scripted rewards** (levels.txt parser) — grants items/abilities via script keywords
2. **Object property rewards** (ALTERWEP-style pedestals) — object flags determine what's granted on pickup

For randomizer purposes, the scripted reward system is what controls the "special" grants (Gad abilities, Flambeau, Poigne, etc.), and **it is fully patchable** via the `GrantReward` function.

---

## The Core Grant Functions

### `GrantReward` — `VA 0x14031c020`

This is the main scripted reward dispatcher. Signature:

```
GrantReward(obj_ptr, level_idx, gad_ability_flag, item_type_id)
  rcx = obj_ptr     (rsi from caller — the game object being triggered)
  rdx = level_idx   (r14 from caller — current level index, 0x00..0x12)
  r8  = gad_ability_flag  (0 = no Gad grant; see table below)
  r9  = item_type_id      (0 = no item; see item ID table below)
```

The function appends a `{gad_flag, item_id}` entry to the level's item array (global table at `0x140db1b98`, indexed by `level_idx`), then calls the object-property processor at `0x14031b680` to resolve pickup behavior.

### `AddDarkSoul` — `VA 0x14031bf50`

A near-identical function used exclusively for dark souls. Same layout but calls `0x1401d61f0` (GetSceneID/GetSoulID) to fill in the item_id automatically from the object's scene data instead of a literal. **Dark souls should remain on their own separate randomization path** — don't mix with GrantReward.

---

## Gad Ability Flag Values (r8 in GrantReward)

| Flag value | Ability granted     | `levels.txt` keyword |
|-----------|---------------------|----------------------|
| `0`       | No Gad ability      | (any item-only grant) |
| `4`       | TOUCHGAD            | `touchgad`           |
| `6`       | WALKGAD             | `walkgad`            |
| `7`       | SWIMGAD             | `swimgad`            |
| `edi`     | POIGNE (passthrough) | `poigne`             |

> **Note on POIGNE:** The `poigne` handler passes `edi` through as `r8` rather than a literal. `edi` holds the vortex/ring position index from the calling context (the Gad ring loop). When patching, you'll need to substitute a concrete value or preserve the register state.

---

## Item Type IDs (r9 in GrantReward)

These map directly to the `IDD_` string table built at `0x140011b00` (heap-allocated, 30 entries × 16 bytes):

| ID (hex) | ID (dec) | Name                |
|----------|----------|---------------------|
| `0x01`   | 1        | IDD_ACCUMULATOR     |
| `0x02`   | 2        | IDD_ASSON           |
| `0x03`   | 3        | IDD_BATON           |
| `0x04`   | 4        | IDD_BOOKOFSHADOWS   |
| `0x05`   | 5        | IDD_CADEAUX         |
| `0x06`   | 6        | IDD_CALABASH        |
| `0x07`   | 7        | IDD_DESERTEAGLE     |
| `0x08`   | 8        | IDD_ECLIPSER        |
| `0x09`   | 9        | IDD_ENGINEERSKEY    |
| `0x0a`   | 10       | IDD_ENSEIGNE        |
| `0x0b`   | 11       | IDD_FLAMBEAU        |
| `0x0e`   | 14       | IDD_FLASHLIGHT      |
| `0x0f`   | 15       | IDD_HEALTH          |
| `0x10`   | 16       | IDD_LALUNE          |
| `0x11`   | 17       | IDD_LALAME          |
| `0x12`   | 18       | IDD_MARTEAU         |
| `0x13`   | 19       | IDD_POIGNE          |
| `0x14`   | 20       | IDD_PRISM           |
| `0x15`   | 21       | IDD_PRISONCARD      |
| `0x16`   | 22       | IDD_PROPHECY        |
| `0x17`   | 23       | IDD_RETRACTOR       |
| `0x18`   | 24       | IDD_SCHEMATIC       |
| `0x19`   | 25       | IDD_SHADOWGUN       |
| `0x1a`   | 26       | IDD_SHOTGUN         |
| `0x1b`   | 27       | IDD_SHOTGUNAMMO     |
| `0x1c`   | 28       | IDD_SOLEIL          |
| `0x1d`   | 29       | IDD_TEDDY           |
| `0x1e`   | 30       | IDD_VIOLATOR        |
| `0x1f`   | 31       | IDD_VIOLATORAMMO    |
| `0x230`  | 560      | IDD_SHOTGUN2 (sawed)|

> IDs 0xc (12) and 0xd (13) are absent from the table — they don't exist in the item ID space.

---

## Scripted Reward Call Sites in levels.txt Parser

All located in the large parser function starting around `0x14031c100`. Each keyword match ends with a call to `GrantReward` at `0x14031c020`:

| levels.txt keyword | Call site VA   | r8 (gad_flag) | r9 (item_id) | Notes |
|-------------------|---------------|----------------|--------------|-------|
| `flambeau`        | `0x14031d53c` | `0`            | `0xb` (11)   | Pure item grant |
| `touchgad`        | `0x14031d57b` | `4`            | `0`          | Pure Gad grant |
| `walkgad`         | `0x14031d5e0` | `6`            | `0`          | Pure Gad grant |
| `swimgad`         | `0x14031d6e0` | `7`            | `0`          | Pure Gad grant |
| `poigne`          | `0x14031d500` | `edi`          | `0`          | Gad, passthrough flag |
| `cadeaux`         | `0x14031c3cf` | —              | —            | **Different path** — calls `0x1401d61f0` (scene/ring handler), NOT GrantReward |
| `darksoul`        | `0x14031c6fd` | —              | —            | Calls AddDarkSoul (`0x14031bf50`), not GrantReward |

---

## Randomizer Patching Strategy

### Direction 1: Scripted Gad ring → Random item instead

Patch the `touchgad`/`walkgad`/`swimgad` call sites to change:
```
r8 = gad_flag  →  r8 = 0
r9 = 0         →  r9 = RANDOM_ITEM_ID
```

At `0x14031d57b` (touchgad example), the bytes before the call are:
```asm
45 33 c9        xor   r9d, r9d       ; r9 = 0 (no item)
45 8d 41 04     lea   r8d, [r9+4]    ; r8 = 4 (TOUCHGAD)
```
To swap to e.g. Flambeau (item 0xb), no Gad:
```asm
45 33 c0        xor   r8d, r8d       ; r8 = 0 (no gad)
41 b9 0b 00 00 00  mov r9d, 0xb      ; r9 = 11 (FLAMBEAU)
```
Then NOP the remaining bytes to maintain instruction alignment.

### Direction 2: Normal item location → Gad ability instead

For a pedestal/script location that currently calls `GrantReward(obj, level, 0, ITEM_ID)`, patch to:
```
r8 = 0         →  r8 = GAD_FLAG  (4/6/7 for Touch/Walk/Swim)
r9 = ITEM_ID   →  r9 = 0
```

### Cadeaux (Gad ring vortex) — Special case

`cadeaux` does **not** go through `GrantReward`. It calls `0x1401d61f0` (identified as a scene-state reader/gad-ring handler) and stores the result as a CF flag. To randomize what the Gad vortex rewards:

1. Intercept at `0x14031c3cf` (after the `cadeaux` match, before `call 0x1401d61f0`)
2. Skip the `0x1401d61f0` call entirely
3. Jump to a stub that calls `GrantReward(rsi, r14, 0, RANDOM_ITEM_ID)` instead

This is slightly more invasive but doable with a code cave.

---

## Object Property Flags (ALTERWEP-style pickup objects)

These are set by the uppercase keyword parser at `0x14031b680` and stored in the object struct:

**At `obj+0x0C` (item flags):**
| Flag | Keyword   | Meaning       |
|------|-----------|---------------|
| `0x01` | ENGKEY  | Engineer's Key|
| `0x02` | CALABASH | Calabash     |
| `0x04` | BATON   | Baton         |
| `0x08` | MARTEAU | Marteau       |
| `0x10` | FLAMBEAU | Flambeau     |
| `0x20` | JACK_JOURNAL | Jack's Journal |
| `0x40` | RETRACTOR | Retractor  |
| `0x80` | KEYCARD | Keycard       |

**At `obj+0x10` (Gad ability flags):**
| Flag | Keyword   | Meaning    |
|------|-----------|------------|
| `0x01` | TOUCHGAD | Touch Gad |
| `0x02` | POIGNE  | La Poigne  |
| `0x04` | WALKGAD | Walk Gad   |
| `0x08` | SWIMGAD | Swim Gad   |

These are the **world object** definitions (read from level data files). For randomizing the walk-up pedestal items, swapping the data flags at load time (or editing the level data directly) is the cleanest approach — no code patching needed.

---

## CF (Campaign Flag) IDs for Gad abilities

These are set when the player successfully receives a Gad ability, used for save state and gate checks:

| CF Flag name     | Numeric ID |
|-----------------|------------|
| CF_GOTTOUCHGAD  | 67 (0x43)  |
| CF_GOTWALKGAD   | 68 (0x44)  |
| CF_GOTSWIMGAD   | 69 (0x45)  |
| CF_GOTPOIGNE    | 70 (0x46)  |

These are stored in the CF global table at `0x140d8abe8` (22 entries, allocated at startup). If a randomized grant swaps a Gad ability to an item location, you'll need to ensure the corresponding CF flag still gets set — otherwise gates that check e.g. `CF_GOTTOUCHGAD` will never open. A post-grant hook that force-sets the relevant CF flag is advisable.

---

## Key Addresses Summary

| Address          | Description                                |
|-----------------|--------------------------------------------|
| `0x14031c020`   | `GrantReward(obj, level_idx, gad_flag, item_id)` |
| `0x14031bf50`   | `AddDarkSoul(obj, level_idx, soul_count)` — separate, don't mix |
| `0x14031b680`   | Object property parser / pickup resolver  |
| `0x140011b00`   | Item string→ID table builder (init only)  |
| `0x140011870`   | CF flag table builder (init only)         |
| `0x140db1b98`   | Global level item array (runtime)         |
| `0x140d8abe8`   | Global CF flag table (runtime)            |
| `0x14064bd18`   | `strcmp` IAT entry (indirect call target) |
| `0x1406015ce`   | Case-insensitive strcmp variant           |
