<div align="center">

![Shadow Man Remastered Randomizer](assets/SMRR_LOGO.png)

</div>

**Version: v1.1.7**

A standalone randomizer for **Shadow Man Remastered** (Nightdive Studios, 2021).
Randomizes key items, souls, weapons, gad powers, coffin gate thresholds, entrances,
enemies, music, and SFX using a custom assumed-fill algorithm.

---

## Features

### Gameplay
- **Progression items** — Engineers Key, Poigne, Baton, Flambeau, Marteau, Calabash, Eclipser parts, Retractors, Accumulators, Prison Key Card; shuffled across all eligible locations using an assumed-fill algorithm that guarantees every seed is beatable
- **Gad powers** — Touch, Walk, and Swim Gad as physical pickups shuffled across temple locations (EXE patch applied automatically)
- **Weapons** — Asson, Shotgun, Sawed-off Shotgun, Enseigne, MP-909, 0.9-SMG, Tête de Mort, Flashlight, Violator
- **Lore items** — Book of Shadows, Prophecy, Jack's Schematic
- **Dark Engine piston combinations** — the six dark engine piston combos can be randomized per-seed; new values are patched directly into the in-game Jack's Schematic journal page, making it a required progression item
- **Dark Souls and Govis** — shuffled across all soul, barrel, and cadeaux slots
- **Starting item** — choose a specific item to receive at the Louisiana Swampland church before any other pickup, or randomize per-seed
- **Starting bundles** — grant all Accumulators, all Retractors, or all Eclipsers at seed start, removing them from the shuffle pool

### Coffin Gates
- **Gate SL requirements** — coffin gate thresholds reshuffled across Deadside; in-world ARC ring decorations updated to match
- **Gate presets** — open, easy, medium, hard, or chaos; controls which gates are locked and whether an SL cap applies
- **Soul level thresholds** — the soul counts required to reach SL1–SL10 can be randomized via three modes: `progressive` (geometric ramp), `balanced` (even spacing), or `random` (fully random); SL0 is always 0, SL10 is always 120

### Entrance Randomizer
- **Deadside-only** — the 9 Deadside portals reshuffled among themselves; Engine Rooms stay vanilla
- **Cross-hub** — all 14 portals (Deadside + Engine Rooms) shuffled together; a Deadside portal may lead to an Engine Room and vice versa

### Gameplay Tuning (EXE patches)
- **Life altar cadeaux requirement** — cadeaux cost per altar interaction (default 100, configurable 1–133)
- **Fogometers light soul door** — cadeaux required to open the final Fogometers gate (default 666, configurable 5–666)
- **Starting max health** — player max health at game start on a 0.5–10 scale in 0.5 steps (vanilla: 5)
- **Life altar health grant** — health restored per altar interaction on the same 0.5–10 scale (vanilla: 1)
- **Death penalty** — reduces max health by a configurable step (0.5–10, in 0.5 increments) on each death, floored so the player always retains at least one step; 0 = disabled
- **Insanity** — allows progression items to be placed in normally-excluded slots; tier 1 = soul/govi slots, tier 2 = +cadeaux, tier 3 = all slots

> **Note:** Cadeaux counting is not yet fully reliable — some cadeaux may not register correctly in-game. Consider lowering the altar cost and Fogometers door values from their defaults until this is resolved.

### Enemies
- **Enemy shuffle** — enemy types reshuffled with three modes: depth-weighted by difficulty tier (default), purely random within movement type, or themed by context group
- **True forms** — optional; true-form enemies (which drop Dark Souls) shuffled alongside regular enemies
- **Movement mixing** — optional cross-movement-type mixing (ground/flying/swimming can swap freely)
- **Uncap counts** — each slot independently samples from the pool with replacement so any type can appear 0 or many times

### Cosmetics
- **Music** — track-to-track global shuffle
- **Voice lines** — Shadow Man generic voice lines shuffled
- **Weapon SFX** — fire and reload sounds shuffled within each weapon category
- **Enemy SFX** — pain, startle, and attack sets shuffled within their own pools
- **Ambient creatures** — rats, egrets, flies, butterflies, and fish shuffled across spawn slots (global, per-movement-type, or per-context-group)
- **Sky textures** — sky layer TGAs shuffled across levels per-filename (horizon swaps with horizon, clouds with clouds, etc.)

### Logic Guarantees
- Assumed-fill guarantees all seeds are beatable before patching
- Starting item and bundles are granted before fill runs so the algorithm accounts for them during logic
- Coffin gate shuffle uses a pool drawn from vanilla SL values so distribution stays bounded — no pile-up at max SL
- Safe mode enforces per-region SL caps on early gates so the game is always immediately accessible
- Eclipser lock group prevents circular placement
- Liveside souls correctly require NIGHT (all three Eclipsers) to collect
- Locations flagged `can_softlock` in the CSV are never chosen for key item placement at any insanity tier
- Fill uses the randomized SL→souls mapping when soul threshold shuffle is active, so gate logic matches what the player will see in-game
- Full sphere-by-sphere playthrough simulation written to the spoiler log

### Delivery
- Patches are packed into a single `shadowman_randomizer_<seed>.kpf` mod file — the seed number is part of the filename
- Installed to the game's `mods/` folder — no original files are modified
- Delete the KPF (or run `--restore`) to instantly return to vanilla

---

## Quick Start

### Just want to play?

Download **`shadow_man_randomizer.exe`** from the [Releases page](https://github.com/bropacman/shadow-man-remastered-randomizer/releases/latest).
No Python required — double-click and go.

### Running from source

**Requirements:** Python 3.10 or newer, Shadow Man Remastered (Steam or GOG), and PyYAML.

**1. Install Python 3.10 or newer** if you don't have it — grab it from [python.org](https://www.python.org/downloads/). During install, check **"Add Python to PATH"**.

**2. Download the randomizer.** Either:
- Click **Code → Download ZIP** on the [GitHub page](https://github.com/bropacman/shadow-man-remastered-randomizer), then extract it anywhere, or
- If you have Git: `git clone https://github.com/bropacman/shadow-man-remastered-randomizer.git`

**3. Install the one dependency.** Open a terminal in the randomizer folder and run:

```bash
pip install -r requirements.txt
```

> **Tip:** On Windows you can open a terminal in any folder by typing `cmd` into the address bar in File Explorer and pressing Enter.

**4. Launch the randomizer.**

- **GUI (recommended):** double-click **`Launch Randomizer.bat`** — no terminal needed.
- **CLI:** run the patcher directly:

```bash
python patcher.py --game-dir "C:\Program Files (x86)\Steam\steamapps\common\Shadow Man Remastered"
```

### Building the exe yourself

Run `build.bat` from the randomizer folder. It installs PyInstaller automatically and produces `dist/shadow_man_randomizer.exe`.

The GUI covers all available settings and is the recommended way to generate seeds. The CLI is available for scripting or advanced use — run `python patcher.py --help` or see [All Options](#all-options) below.

---

## All Options

### Core

| Flag | Default | Description |
|------|---------|-------------|
| `--game-dir PATH` | parent of script | Shadow Man Remastered install directory |
| `--output-dir PATH` | game dir | Where to write the spoiler log and packed KPF |
| `--seed N` | random | Seed for reproducible results |
| `--config FILE` | none | YAML config file to override defaults |
| `--dry-run` | off | Show what would happen without patching |
| `--restore` | off | Remove randomizer mod, restore vanilla, and exit |
| `--settings-string STR` | none | Base64 settings string exported by the GUI — passed automatically when launching from the GUI, and recorded in the spoiler log for full reproducibility |

### Item shuffle

| Flag | Default | Description |
|------|---------|-------------|
| `--shuffle-weapons` / `--no-shuffle-weapons` | on | Shuffle weapons |
| `--shuffle-weapons-random` | off | Randomly decide per-seed whether to shuffle weapons |
| `--shuffle-lore` / `--no-shuffle-lore` | on | Shuffle lore items |
| `--shuffle-lore-random` | off | Randomly decide per-seed whether to shuffle lore items |
| `--shuffle-light-soul` | off | Include the Light Soul bonus item in the shuffle pool |
| `--shuffle-light-soul-random` | off | Randomly decide per-seed whether to include the Light Soul |
| `--shuffle-gad-temples` / `--no-shuffle-gad-temples` | on | Gad powers as physical pickups (EXE patch) |
| `--shuffle-gad-temples-random` | off | Randomly decide per-seed whether to shuffle gad temples |
| `--shuffle-prisms` / `--no-shuffle-prisms` | off | Shuffle prism items as progression items *(in development)* |
| `--shuffle-prisms-random` | off | Randomly decide per-seed whether to shuffle prisms *(in development)* |
| `--starting-item RSC_NAME` | none | Place a specific item at the Louisiana Swampland church at run start (e.g. `RSC_X_ENGINEERS_KEY`) |
| `--random-starting-item` | off | Pick a random starting item using the seed RNG — reproducible for a given seed |
| `--starting-item-bundles NAME…` | none | Grant one or more full item groups at seed start, removing them from the shuffle pool. Available bundles: `all_accumulators`, `all_retractors`, `all_eclipsers` |
| `--piston-combos` | off | Randomize the six dark engine piston combination values; the new codes are patched into the in-game Jack's Schematic journal page, making it a required progression item |
| `--piston-combos-random` | off | Randomly decide per-seed whether to randomize piston combinations |
| `--insanity [1\|2\|3\|random]` | off | Place progression items in normally-excluded slots. Tier 1 = soul/govi slots, tier 2 = +cadeaux slots, tier 3 = all slots. Bare `--insanity` defaults to tier 3. Pass `random` to let the seed pick a tier. |
| `--progression-balancing N` | 50 | 0–100, higher = items pushed deeper into the world. Accepts `random`. |

### Coffin gates

| Flag | Default | Description |
|------|---------|-------------|
| `--gate-preset NAME` | none | `open` = all gates free, no shuffle; `easy` = many gates locked, SL7 cap, 6 gates forced open; `medium` = entry gates locked, SL8 cap, 3 gates forced open; `hard` = entry gates locked, no SL cap, 1 gate forced open; `chaos` = no locks, no cap, no safety; `random` = preset chosen randomly per-seed |
| `--max-sl N` | none | Override the preset's SL cap — cap the highest SL any shuffled gate can receive (1–10) |
| `--open-gates N` | preset | Override the preset's open-gates default — force the first N linear coffin gates to SL0 (order: Marrow → Wasteland → Asylum → Temple of Fire → Cageways → Playrooms; beyond 6 chosen randomly) |
| `--soul-threshold-mode MODE` | none | Randomize the soul counts required to reach SL1–SL10. `progressive` = geometric ramp, `balanced` = even spacing, `random` = fully random distribution. SL0 stays at 0 and SL10 stays at 120; all intermediate breakpoints are patched into the EXE. Omit the flag to keep vanilla thresholds (1, 3, 7, 15, 23, 35, 51, 71, 95, 120). |
| `--soul-threshold-mode-random` | off | Pick a soul threshold mode randomly per-seed (reproducible for a given seed). |

### Gameplay tuning

Several of the options below accept `random` in place of a number — when passed, the value is chosen randomly per-seed using the seed RNG and recorded in the spoiler log so the result is always reproducible.

| Flag | Default | Description |
|------|---------|-------------|
| `--altar-cadeaux-required N` | `100` | Cadeaux required **and** spent per life altar interaction (1–133, vanilla: 100). Max of 133 = ⌊666 ÷ 5⌋. Accepts `random`. |
| `--fogometers-cadeaux-required N` | `666` | Cadeaux required to open the Fogometers light soul door (must be ≥ 5 × altar cost, max 666, vanilla: 666). Accepts `random`. |
| `--starting-health N` | `5` | Starting max health on a 0.5–10 scale in 0.5 steps (vanilla: 5). Accepts `random`. |
| `--altar-health-grant N` | `1` | Health granted per life altar interaction on a 0.5–10 scale in 0.5 steps (vanilla: 1). Note: starting health + 5 × grant should not exceed the cap of 10. Accepts `random`. |
| `--death-penalty N` | `0` | Reduce max health by N steps on each death, floored at N steps. 0 = disabled. Accepts decimals in 0.5 steps (e.g. `0.5` = −500/death, `1.0` = −1000/death). |
| `--death-penalty-random` | off | Choose a random death-penalty step (0.5–10.0) per-seed rather than disabling it. Reproducible for a given seed. |


### Map tracker (Teddy Bear hints)

| Flag | Default | Description |
|------|---------|-------------|
| `--patch-tracker` / `--no-patch-tracker` | on | Rewrite `levels.txt` map badges to show randomized item locations. Use `--no-patch-tracker` to strip all item badges instead. |

### Entrance randomizer

| Flag | Default | Description |
|------|---------|-------------|
| `--entrance-mode MODE` | `off` | `deadside_only` = shuffles the 9 Deadside levels among themselves (Engine Rooms stay vanilla); `cross_hub` = 14 levels (Deadside levels + Engine Rooms) shuffled together, a Deadside portal may lead to an Engine Room and vice versa; `random` = mode chosen randomly per-seed. Works best with open coffin gate settings and Insanity Tier ≥ 1. |

### Enemies, music, SFX, and cosmetics

| Flag | Default | Description |
|------|---------|-------------|
| `--shuffle-enemies` | off | Randomize enemy types in each level |
| `--shuffle-enemies-random` | off | Decide randomly per-seed whether to shuffle enemies (reproducible for a given seed). |
| `--enemy-mode MODE` | `difficulty` | `difficulty` = depth-weighted by tier, `full` = random within movement type, `contextual` = shuffle within context-group pools; `random` = mode chosen randomly per-seed |
| `--enemy-mix-movement` | off | Allow enemies to swap across movement-type pools (ground/flying/swimming mix freely) |
| `--enemy-mix-movement-random` | off | Decide randomly per-seed whether to mix movement types. |
| `--enemy-uncap-counts` | off | Uncap enemy type counts: each slot independently samples from the pool with replacement, so any type can appear 0 or many times |
| `--enemy-uncap-counts-random` | off | Randomly decide per-seed whether to uncap enemy type counts |
| `--shuffle-true-forms` | off | Shuffle true-form enemy positions with regular enemies |
| `--shuffle-true-forms-random` | off | Decide randomly per-seed whether to shuffle true forms. |
| `--shuffle-ambients` | off | Shuffle friendly/ambient creatures (rats, egrets, flies, butterflies, fish) across spawn slots |
| `--ambient-mode MODE` | `global` | `global` = one free-for-all pool (default), `full` = shuffle within movement type, `contextual` = shuffle within context-group + movement-type pools |
| `--shuffle-music` | off | Shuffle music tracks globally across all levels |
| `--shuffle-voices` | off | Shuffle Shadow Man generic voice lines |
| `--shuffle-weapons-sfx` | off | Shuffle weapon fire/reload sounds within each category |
| `--shuffle-enemies-sfx` | off | Shuffle enemy SFX within each sound-type pool (pain sets swap with pain sets, startle with startle, attack with attack) |
| `--shuffle-sky` | off | Shuffle sky textures across levels (per-filename pool — `000sky.tga` swaps with other `000sky.tga` files across levels, etc.) |

Run `python patcher.py --help` for the authoritative list.

---

## Project Structure

```
shadow-man-remastered-randomizer/
│
├── patcher.py                    ← Main entry point, orchestrates all steps
├── gui.py                        ← pywebview GUI wrapper (runs patcher.py as a subprocess)
├── Launch Randomizer.bat         ← Double-click to open the GUI (no terminal needed)
├── build.bat                     ← Builds a standalone .exe via PyInstaller
├── fill.py                       ← Assumed-fill placement algorithm + simulation (entrance-aware)
├── access_rules.py               ← All logic rules (gates, items, soul levels)
├── regions.py                    ← Region graph and connections
├── locations.py                  ← Location class definitions used by the graph
├── BaseClasses.py                ← Lightweight state/region base classes
├── constants.py                  ← File lists, level folders, EXE item type IDs
├── kpf_handler.py                ← KPF archive extraction and mod packing
│
├── cadeaux_patch.py              ← EXE patch for altar/door cadeaux requirement and cost
├── health_patch.py               ← EXE patch for starting max health and altar health grant
├── dark_engine_patch.py          ← Randomizes dark engine piston combo values; patches in-game journal
├── soul_threshold_patch.py       ← EXE patch for SL1–SL10 soul count requirements
├── death_penalty_patch.py        ← EXE code-cave patch for per-death health reduction
│
├── patchers/
│   ├── gad_pickup_patch.py       ← EXE patch for gad pickup type_id dispatch
│   └── setup_gad_records.py      ← Injects RSC_X_GAD_PICKUP records into temples
│
├── randomizers/
│   ├── entrance_randomizer.py    ← Hub portal shuffle logic (deadside_only / cross_hub)
│   ├── enemy_randomizer.py       ← Enemy type shuffle logic
│   ├── music_randomizer.py       ← Music shuffle logic
│   ├── sfx_randomizer.py         ← Voice and weapon SFX shuffle logic
│   └── sky_randomizer.py         ← Sky texture shuffle logic
│
├── data/
│   ├── locations.csv             ← Source of truth for all item locations + logic
│   └── enemy_locations.csv       ← Source of truth for all enemy locations
│
├── tools/
│   ├── generate.py               ← Regenerates extracted_locations.py
│   └── generate_enemies.py       ← Regenerates extracted_enemy_locations.py
│
└── docs/                         ← Technical documentation
```

`extracted_locations.py` and `extracted_enemy_locations.py` are generated from the
CSVs and are tracked in git so they work out of the box. If you modify the CSVs,
re-run the scripts in `tools/` to regenerate them.

---

## How the Patcher Works

1. **Extract** — pulls quest/instance/fx/resource/enemies RSC files from KPF archives
2. **Inject** — adds `RSC_X_GAD_PICKUP` records to temple files (if gad shuffle enabled)
3. **Parse** — reads all RSC records from extracted files
4. **Fill** — runs assumed-fill to place progression items logically; uses randomized SL thresholds during simulation so gate logic matches what will be patched into the EXE (entrance-aware variant used when entrance randomizer is enabled)
5. **Gate shuffle** — writes new SL thresholds to `links.e2o` files
6. **Entrance shuffle** — rewrites level exit scripts so hub portals connect to new spokes (if enabled)
7. **Patch RSC** — writes new item names to all RSC files
8. **Patch enemies** — shuffles enemy type names in enemies RSC files (if enabled)
9. **Patch EXE** — writes prison key card position fix (always) + gad pickup dispatch (if enabled) + cadeaux altar/door thresholds + starting health and altar health grant + soul level thresholds (if enabled) + death penalty (if enabled)
10. **Patch dark engine** — randomizes piston combo values and patches the Jack's Schematic journal page with the new codes (if enabled)
11. **Update decos** — renames ARC coffin gate decorations to match new SL values
12. **Repack** — packs all modified files into `shadowman_randomizer_<seed>.kpf` so the seed is always identifiable from the filename alone

---

## Location Data

All randomizable locations are defined in `data/locations.csv`. Each row defines:
- Where the item physically lives (`level_id`, `source_file`, `offset`)
- What item is there in vanilla (`object`, `category`)
- What logic gates access to it (`level_region`, `sub_region`)
- Whether placing a key item here could softlock the player (`can_softlock`)

### `can_softlock` flag

Set `can_softlock = TRUE` on any cadeaux location that a player couldn't escape from if a key item were placed there — ledges, one-way drops, tight spots the player can reach but not exit while carrying a heavy item. Locations with this flag are excluded from key item placement at **all** insanity tiers while still receiving a cadeaux normally during the filler phase, so the 666 total count is never affected.

To add or edit locations, modify the CSV then run:
```bash
python tools/generate.py
```

Enemy locations follow the same pattern in `data/enemy_locations.csv` with
additional `context_group` and `movement_type` columns for shuffle pooling.
To regenerate after edits:
```bash
python tools/generate_enemies.py
```

---

## Troubleshooting

**"Game directory not found" / wrong files patched.** Pass the install folder
explicitly with `--game-dir`. The default assumes the script is sitting next to
the game folder; if you cloned somewhere else, that won't be true.

**Game still loads vanilla.** The patcher writes to `mods/shadowman_randomizer_<seed>.kpf`.
Confirm the file is there and that no other mods are overriding the same RSC files.

**Run failed mid-patch.** A `_randomizer_work_<seed>` folder is left next to the
game so you can inspect it. Safe to delete. Re-run the patcher to start clean.

**Want vanilla back fast.** `python patcher.py --restore --game-dir <PATH>` or
just delete the `mods/shadowman_randomizer_*.kpf` file.

---

## Contributing

Bug reports, seed pathology cases, and PRs welcome. When filing an issue, please
include the seed, the exact CLI flags you used, and the spoiler log (`spoiler_log_<seed>.txt`) if one was generated.

Join the discussion or share feedback on the
[GitHub Discussions board](https://github.com/bropacman/shadow-man-remastered-randomizer/discussions).
You can also reach me directly on Discord: **bropacman**

---

## Credits

- Game by Nightdive Studios
- Randomizer by [bropacman](https://github.com/bropacman) and the Shadow Man modding community
- Special thanks to: Momo, karrot250, Tartus, Mtamer01, Embrace Darkshade

---

## Disclaimer

This is an unofficial fan-made tool and is not affiliated with, endorsed by, or sponsored by Nightdive Studios. Shadow Man Remastered is the property of Nightdive Studios.

This tool requires a legitimate purchased copy of Shadow Man Remastered to function — no game assets are distributed. EXE patching is performed locally on the user's own installation. Use at your own risk; verify your game files via Steam if you need to restore a clean install.

This project is released under the [MIT License](LICENSE).
