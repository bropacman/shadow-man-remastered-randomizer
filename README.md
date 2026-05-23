<div align="center">

![Shadow Man Remastered Randomizer](assets/SMRR_LOGO.png)

</div>

A standalone randomizer for **Shadow Man Remastered** (Nightdive Studios, 2021).
Randomizes key items, souls, weapons, gad powers, coffin gate thresholds, entrances,
enemies, music, and SFX using a custom assumed-fill algorithm that guarantees every
seed is beatable.

---

## Features

### What Gets Randomized
- **Progression items** — Engineers Key, Poigne, Baton, Flambeau, Marteau, Calabash,
  Eclipser parts, Retractors, Accumulators, Prison Key Card
- **Gad powers** — Touch, Walk, and Swim Gad as physical pickups shuffled across
  temple locations (EXE patch applied automatically; disable with `--no-shuffle-gad-temples`)
- **Weapons** — Asson, Shotgun, Sawed-off Shotgun, Enseigne, MP-909, 0.9-SMG, Tête de Mort, Flashlight, Violator
- **Lore items** — Book of Shadows, Prophecy, Jack's Schematic
- **Starting item** — choose a specific item to receive at the Louisiana Swampland church before any other pickup, or use random to let the seed pick one reproducibly
- **Dark Souls and Govis** — shuffled across all soul, barrel, and cadeaux slots, so they can end up anywhere
- **Coffin gate SL requirements** — coffin gate thresholds reshuffled across deadside
  (in-world ARC ring decorations updated to match)
- **Enemies** — enemy types shuffled with three modes: depth-weighted by tier
  (default), purely random by movement type, or themed by context group; optional
  cross-movement-type mixing
- **Ambient creatures** — rats, egrets, flies, butterflies, and friendly fish shuffled
  across their spawn slots (three modes: global free-for-all, per-movement-type, or
  per-context-group); purely cosmetic
- **Music** — track-to-track global shuffle (optional)
- **Entrances** — hub portals reshuffled so Deadside levels and Engine Rooms connect in a new order (two modes: Deadside-only or full cross-hub mixing both)
- **SFX** — Shadow Man voice lines, weapon sounds, and enemy SFX (pain, startle, and
  attack sets shuffled within their own pools)
- **Sky textures** — sky layer TGAs shuffled across levels per-filename (horizon swaps
  with horizon, clouds with clouds, etc.); purely cosmetic

### Gameplay Tuning (EXE patches)
- **Life altar cadeaux requirement** — cadeaux cost and minimum required per altar interaction (default 100, configurable 1–133)
- **Fogometers light soul door** — cadeaux required to open the final Fogometers gate (default 666, configurable 5–666)
- **Starting max health** — player max health at game start on a 1–10 scale (default 5 = 5 000 units)
- **Life altar health grant** — health restored per altar interaction on the same 1–10 scale (default 1 = 1 000 units)

> **Note:** Cadeaux counting is not yet fully reliable — some cadeaux may not register correctly in-game depending on how they were placed. Consider lowering the altar cost and Fogometers door values from their defaults until this is resolved.

### Logic Guarantees
- Assumed-fill algorithm guarantees all seeds are beatable before patching
- Starting item is granted before fill runs, so the algorithm accounts for it during logic
- Coffin gate shuffle uses a pool-based approach (gates share a pool of SL values drawn from vanilla) so distribution stays bounded — no pile-up at max SL
- Safe mode enforces per-region SL caps on early gates so the game is always immediately accessible
- Eclipser lock group prevents circular placement
- Liveside souls correctly require NIGHT (all three Eclipsers) to collect
- Locations flagged `can_softlock = TRUE` in the CSV are never chosen for key item placement at any insanity tier (see [Location Data](#location-data))
- Full sphere-by-sphere playthrough simulation written to spoiler log

### Delivery
- Patches are packed into a single `shadowman_randomizer.kpf` mod file
- Installed to the game's `mods/` folder — no original files are modified
- Delete the KPF (or run `--restore`) to instantly return to vanilla

---

## Requirements

- Python 3.10 or newer
- Shadow Man Remastered (Steam or GOG)
- PyYAML (`pip install -r requirements.txt`)

---

## Quick Start

### Just want to play?

Download **`shadow_man_randomizer.exe`** from the [Releases page](https://github.com/jonathanmanos/shadow-man-remastered-randomizer/releases/latest).
No Python required — double-click and go.

### Running from source

**1. Install Python 3.10 or newer** if you don't have it — grab it from [python.org](https://www.python.org/downloads/). During install, check **"Add Python to PATH"**.

**2. Download the randomizer.** Either:
- Click **Code → Download ZIP** on the [GitHub page](https://github.com/jonathanmanos/shadow-man-remastered-randomizer), then extract it anywhere, or
- If you have Git: `git clone https://github.com/jonathanmanos/shadow-man-remastered-randomizer.git`

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

---

The patcher writes a spoiler log next to itself (`spoiler_seed_<N>.txt`).
Drop the generated `shadowman_randomizer.kpf` in the game's `mods/` folder and play.

### Common recipes

```bash
# Reproduce a specific seed
python patcher.py --game-dir <PATH> --seed 12345

# Disable gad temple shuffle (on by default)
python patcher.py --game-dir <PATH> --no-shuffle-gad-temples

# Shuffle enemies with the difficulty-weighted default
python patcher.py --game-dir <PATH> --shuffle-enemies

# Themed enemy shuffle (deadside / liveside / prison stay separated)
python patcher.py --game-dir <PATH> --shuffle-enemies --enemy-mode contextual

# Let ground/flying/swimming enemies mix freely
python patcher.py --game-dir <PATH> --shuffle-enemies --enemy-mix-movement

# Shuffle ambient creatures (rats, egrets, flies, butterflies, fish)
python patcher.py --game-dir <PATH> --shuffle-ambients

# Shuffle ambient creatures within movement-type pools (ground/flying/swimming stay separate)
python patcher.py --game-dir <PATH> --shuffle-ambients --ambient-mode full

# Shuffle enemy pain/startle/attack SFX within each sound-type pool
python patcher.py --game-dir <PATH> --shuffle-enemies-sfx

# Shuffle sky textures across levels
python patcher.py --game-dir <PATH> --shuffle-sky

# Light shuffle with SL8 cap
python patcher.py --game-dir <PATH> --gate-preset easy

# Standard shuffle with SL8 cap
python patcher.py --game-dir <PATH> --gate-preset medium

# Start with the Engineers Key already in hand
python patcher.py --game-dir <PATH> --starting-item RSC_X_ENGINEERS_KEY

# Let the seed pick a random starting item (reproducible)
python patcher.py --game-dir <PATH> --random-starting-item

# Enable Teddy Bear map tracker hints
python patcher.py --game-dir <PATH> --patch-tracker

# Shuffle deadside portals only
python patcher.py --game-dir <PATH> --entrance-mode deadside_only

# Shuffle all 14 portals (deadside + dark engine) together
python patcher.py --game-dir <PATH> --entrance-mode cross_hub

# Randomize the entrance mode itself per-seed
python patcher.py --game-dir <PATH> --entrance-mode random

# Randomize altar cadeaux cost and Fogometers door requirement per-seed
python patcher.py --game-dir <PATH> --altar-cadeaux-required random --fogometers-cadeaux-required random

# Throw everything in the blender
python patcher.py --game-dir <PATH> \
    --shuffle-enemies --shuffle-true-forms --enemy-mix-movement \
    --shuffle-ambients --shuffle-sky \
    --shuffle-music --shuffle-voices --shuffle-weapons-sfx --shuffle-enemies-sfx \
    --entrance-mode cross_hub --gate-preset easy \
    --patch-tracker

# Restore vanilla
python patcher.py --restore --game-dir <PATH>
```

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
| `--shuffle-progression` / `--no-shuffle-progression` | on | Shuffle key progression items using assumed-fill |
| `--shuffle-weapons` / `--no-shuffle-weapons` | on | Shuffle weapons |
| `--shuffle-lore` / `--no-shuffle-lore` | on | Shuffle lore items |
| `--shuffle-light-soul` | off | Include the Light Soul bonus item in the shuffle pool |
| `--shuffle-gad-temples` / `--no-shuffle-gad-temples` | on | Gad powers as physical pickups (EXE patch) |
| `--starting-item RSC_NAME` | none | Place a specific item at the Louisiana Swampland church at run start (e.g. `RSC_X_ENGINEERS_KEY`) |
| `--random-starting-item` | off | Pick a random starting item using the seed RNG — reproducible for a given seed |
| `--insanity [1\|2\|3\|random]` | off | Place progression items in normally-excluded slots. Tier 1 = soul/govi slots, tier 2 = +cadeaux slots, tier 3 = all slots. Bare `--insanity` defaults to tier 3. Pass `random` to let the seed pick a tier. |
| `--progression-balancing N` | 50 | 0–100, higher = items pushed deeper into the world |

### Coffin gates

| Flag | Default | Description |
|------|---------|-------------|
| `--gate-preset NAME` | none | `open` = all gates free, no shuffle; `easy` = many gates locked, SL7 cap, 6 gates forced open; `medium` = entry gates locked, SL8 cap, 3 gates forced open; `hard` = entry gates locked, no SL cap, 1 gate forced open; `chaos` = no locks, no cap, no safety |
| `--max-sl N` | none | Override the preset's SL cap — cap the highest SL any shuffled gate can receive (0–10) |
| `--open-gates N` | preset | Override the preset's open-gates default — force the first N linear coffin gates to SL0 (order: Marrow → Wasteland → Asylum → Temple of Fire → Cageways → Playrooms; beyond 6 chosen randomly) |

### Gameplay tuning

Several of the options below accept `random` in place of a number — when passed, the value is chosen randomly per-seed using the seed RNG and recorded in the spoiler log so the result is always reproducible.

| Flag | Default | Description |
|------|---------|-------------|
| `--altar-cadeaux-required N` | `100` | Cadeaux required **and** spent per life altar interaction (1–133, vanilla: 100). Max of 133 = ⌊666 ÷ 5⌋. Accepts `random`. |
| `--fogometers-cadeaux-required N` | `666` | Cadeaux required to open the Fogometers light soul door (must be ≥ 5 × altar cost, max 666, vanilla: 666). Accepts `random`. |
| `--starting-health N` | `5` | Starting max health on a scale of 1–10, where each step = 1 000 units (vanilla: 5 = 5 000). Accepts `random`. |
| `--altar-health-grant N` | `1` | Health granted per life altar interaction, on the same 1–10 scale (vanilla: 1 = 1 000). Note: starting health + 5 × grant should not exceed the 10 000 cap. Accepts `random`. |

> **Cadeaux note:** Cadeaux counting is not yet fully reliable in-game. It is recommended to keep the altar cost and Fogometers door values lower than their defaults until this is resolved.

### Map tracker (Teddy Bear hints)

| Flag | Default | Description |
|------|---------|-------------|
| `--patch-tracker` | off | Rewrite `levels.txt` map badges to show randomized item locations. Without this flag all item badges are stripped so no incorrect vanilla hints appear. |

### Entrance randomizer

| Flag | Default | Description |
|------|---------|-------------|
| `--entrance-mode MODE` | `off` | `deadside_only` = shuffles the 9 Deadside levels among themselves (Engine Rooms stay vanilla); `cross_hub` = 14 levels (Deadside levels + Engine Rooms) shuffled together, a Deadside portal may lead to an Engine Room and vice versa; `random` = mode chosen randomly per-seed. Works best with open coffin gate settings and Insanity Tier ≥ 1. |

### Enemies, music, SFX, and cosmetics

| Flag | Default | Description |
|------|---------|-------------|
| `--shuffle-enemies` | off | Randomize enemy types in each level |
| `--enemy-mode MODE` | `difficulty` | `difficulty` = depth-weighted by tier, `full` = random within movement type, `contextual` = shuffle within context-group pools |
| `--enemy-mix-movement` | off | Allow enemies to swap across movement-type pools (ground/flying/swimming mix freely) |
| `--shuffle-true-forms` | off | Shuffle true-form enemy positions with regular enemies |
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
│
├── patchers/
│   ├── gad_pickup_patch.py       ← EXE patch for gad pickup type_id dispatch
│   └── setup_gad_records.py      ← Injects RSC_X_GAD_PICKUP records into temples
│
├── randomizers/
│   ├── entrance_randomizer.py    ← Hub portal shuffle logic (deadside_only / cross_hub)
│   ├── enemy_randomizer.py       ← Enemy type shuffle logic
│   ├── music_randomizer.py       ← Music shuffle logic
│   └── sfx_randomizer.py         ← Voice and weapon SFX shuffle logic
│
├── data/
│   ├── locations.csv             ← Source of truth for all item locations + logic
│   └── enemy_locations.csv       ← Source of truth for all enemy locations
│
├── tools/
│   ├── generate.py               ← Regenerates extracted_locations.py
│   └── generate_enemies.py       ← Regenerates extracted_enemy_locations.py
│
└── docs/                         ← Research notes
```

`extracted_locations.py` and `extracted_enemy_locations.py` are generated from the
CSVs and are not tracked in git — run the scripts in `tools/` to (re)create them.

---

## How the Patcher Works

1. **Extract** — pulls quest/instance/fx/resource/enemies RSC files from KPF archives
2. **Inject** — adds `RSC_X_GAD_PICKUP` records to temple files (if gad shuffle enabled)
3. **Parse** — reads all RSC records from extracted files
4. **Fill** — runs assumed-fill to place progression items logically (entrance-aware variant used when entrance randomizer is enabled)
5. **Gate shuffle** — writes new SL thresholds to `links.e2o` files
6. **Entrance shuffle** — rewrites level exit scripts so hub portals connect to new spokes (if enabled)
7. **Patch RSC** — writes new item names to all RSC files
8. **Patch enemies** — shuffles enemy type names in enemies RSC files (if enabled)
9. **Patch EXE** — writes prison key card position fix (always) + gad pickup dispatch (if enabled) + cadeaux altar/door thresholds + starting health and altar health grant
10. **Update decos** — renames ARC coffin gate decorations to match new SL values
11. **Repack** — packs all modified files into `shadowman_randomizer.kpf`

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

## Coffin Gate Thresholds

Coffin gates use a non-linear threshold scale:

| SL | Souls Required |
|----|---------------|
| 0  | 0 |
| 1  | 1 |
| 2  | 3 |
| 3  | 7 |
| 4  | 15 |
| 5  | 23 |
| 6  | 35 |
| 7  | 51 |
| 8  | 71 |
| 9  | 95 |
| 10 | 120 (locked) |

Gate SL values are shuffled using a pool drawn from vanilla SL values (clamped to the preset's max SL cap), so the overall distribution stays close to vanilla — you won't end up with eight gates all at max SL. Safe mode adds per-region caps on early gates so the game is always immediately accessible. The `--open-gates N` option forces the first N linear coffin gates to SL0 regardless of the shuffle result.

---

## Troubleshooting

**"Game directory not found" / wrong files patched.** Pass the install folder
explicitly with `--game-dir`. The default assumes the script is sitting next to
the game folder; if you cloned somewhere else, that won't be true.

**Game still loads vanilla.** The patcher writes to `mods/shadowman_randomizer.kpf`.
Confirm the file is there and that no other mods are overriding the same RSC files.

**Run failed mid-patch.** A `_randomizer_work_<seed>` folder is left next to the
game so you can inspect it. Safe to delete. Re-run the patcher to start clean.

**Want vanilla back fast.** `python patcher.py --restore --game-dir <PATH>` or
just delete `mods/shadowman_randomizer.kpf`.

---

## Contributing

Bug reports, seed pathology cases, and PRs welcome. When filing an issue, please
include the seed, the exact CLI flags you used, and the spoiler log if the run
completed.

---

## Credits

- Game by Nightdive Studios
- Randomizer by the Shadow Man modding community

---

## Disclaimer

This is an unofficial fan-made tool and is not affiliated with, endorsed by, or sponsored by Nightdive Studios. Shadow Man Remastered is the property of Nightdive Studios.

This tool requires a legitimate purchased copy of Shadow Man Remastered to function — no game assets are distributed. EXE patching is performed locally on the user's own installation. Use at your own risk; verify your game files via Steam if you need to restore a clean install.

This project is released under the [MIT License](LICENSE).
