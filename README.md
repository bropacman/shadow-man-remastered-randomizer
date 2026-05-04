<div align="center">

![Shadow Man Remastered Randomizer](assets/SMRR_LOGO.png)

</div>

A standalone randomizer for **Shadow Man Remastered** (Nightdive Studios, 2021).
Randomizes key items, souls, weapons, gad powers, coffin gate thresholds, enemies,
music, and SFX using a custom assumed-fill algorithm that guarantees every seed
is beatable.

---

## Features

### What Gets Randomized
- **Progression items** — Engineers Key, Poigne, Baton, Flambeau, Marteau, Calabash,
  Eclipser parts, Retractors, Accumulators, Prison Key Card
- **Gad powers** — Touch, Walk, and Swim Gad as physical pickups shuffled across
  temple locations (EXE patch applied automatically; disable with `--no-shuffle-gad-temples`)
- **Weapons** — Asson, Shotgun, Sawed-off Shotgun, Enseigne, MP-909, 0.9-SMG, Tête de Mort, Flashlight, Violator
- **Lore items** — Book of Shadows, Prophecy, Jack's Schematic
- **Starting item** — choose a specific item (or let the randomizer pick one) to receive at the Louisiana Swampland church before any other pickup
- **Dark Souls and Govis** — shuffled across all soul, barrel, and cadeaux slots, so they can end up anywhere
- **Coffin gate SL requirements** — coffin gate thresholds reshuffled across deadside
  (in-world ARC ring decorations updated to match)
- **Enemies** — enemy types shuffled with three modes: depth-weighted by tier
  (default), purely random by movement type, or themed by context group
- **Music** — track-to-track global shuffle (optional)
- **SFX** — Shadow Man voice lines and weapon sounds (optional)

### Logic Guarantees
- Assumed-fill algorithm guarantees all seeds are beatable before patching
- Starting item is granted before fill runs, so the algorithm accounts for it during logic
- Coffin gate shuffle ensures starting gates (Wasteland, Asylum, Path 3) stay at SL≤3
- Eclipser lock group prevents circular placement
- Liveside souls correctly require NIGHT (all three Eclipsers) to collect
- Full sphere-by-sphere playthrough simulation written to spoiler log

### Delivery
- Patches are packed into a single `shadowman_randomizer.kpf` mod file
- Installed to the game's `mods/` folder — no original files are modified
- Delete the KPF (or run `--restore`) to instantly return to vanilla

---

## Requirements

- Python 3.11 or newer
- Shadow Man Remastered (Steam or GOG)
- PyYAML (`pip install -r requirements.txt`)

---

## Quick Start

### Just want to play?

Download **`shadow_man_randomizer.exe`** from the [Releases page](https://github.com/jonathanmanos/shadow-man-remastered-randomizer/releases/latest).
No Python required — double-click and go.

### Running from source

**1. Install Python 3.11 or newer** if you don't have it — grab it from [python.org](https://www.python.org/downloads/). During install, check **"Add Python to PATH"**.

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

# Light shuffle with SL8 cap
python patcher.py --game-dir <PATH> --gate-preset easy

# Standard shuffle with SL8 cap
python patcher.py --game-dir <PATH> --gate-preset medium

# Start with the Engineers Key already in hand
python patcher.py --game-dir <PATH> --starting-item RSC_X_ENGINEERS_KEY

# Throw everything in the blender
python patcher.py --game-dir <PATH> \
    --shuffle-enemies --shuffle-true-forms \
    --shuffle-music --shuffle-voices --shuffle-weapons-sfx

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

### Item shuffle

| Flag | Default | Description |
|------|---------|-------------|
| `--shuffle-progression` / `--no-shuffle-progression` | on | Shuffle key progression items using assumed-fill |
| `--shuffle-weapons` / `--no-shuffle-weapons` | on | Shuffle weapons |
| `--shuffle-lore` / `--no-shuffle-lore` | on | Shuffle lore items |
| `--shuffle-light-soul` | off | Include the Light Soul bonus item in the shuffle pool |
| `--shuffle-gad-temples` / `--no-shuffle-gad-temples` | on | Gad powers as physical pickups (EXE patch) |
| `--starting-item RSC_NAME` | none | Place a specific item at the Louisiana Swampland church at run start (e.g. `RSC_X_ENGINEERS_KEY`). Use `--starting-item random` in the GUI to let the randomizer pick one. |
| `--insanity [1\|2\|3]` | off | Place progression items in normally-excluded slots. Tier 1 = soul/govi slots, tier 2 = +cadeaux slots, tier 3 = all slots. Bare `--insanity` defaults to tier 3. |
| `--progression-balancing N` | 50 | 0–100, higher = items pushed deeper into the world |

### Coffin gates

| Flag | Default | Description |
|------|---------|-------------|
| `--gate-preset NAME` | none | `open` = all gates free, no shuffle; `easy` = most gates locked to vanilla, free gates reshuffled with SL7 cap; `medium` = only the three entry gates locked, everything else reshuffled with SL8 cap; `hard` = same locks as medium but no SL cap; `chaos` = no locks, no cap, no safety checks |
| `--max-sl N` | none | Cap the maximum SL any shuffled gate can receive (1–10) |

### Enemies, music, SFX

| Flag | Default | Description |
|------|---------|-------------|
| `--shuffle-enemies` | off | Randomize enemy types in each level |
| `--enemy-mode MODE` | `difficulty` | `difficulty` = depth-weighted by tier, `full` = random within movement type, `contextual` = shuffle within context-group pools |
| `--shuffle-true-forms` | off | Shuffle true-form enemy positions with regular enemies |
| `--shuffle-music` | off | Shuffle music tracks globally across all levels |
| `--shuffle-voices` | off | Shuffle Shadow Man generic voice lines |
| `--shuffle-weapons-sfx` | off | Shuffle weapon fire/reload sounds within each category |

Run `python patcher.py --help` for the authoritative list.

---

## Project Structure

```
shadow-man-remastered-randomizer/
│
├── patcher.py                    ← Main entry point, orchestrates all steps
├── gui.py                        ← Tkinter GUI wrapper (runs patcher.py as a subprocess)
├── Launch Randomizer.bat         ← Double-click to open the GUI (no terminal needed)
├── build.bat                     ← Builds a standalone .exe via PyInstaller
├── fill.py                       ← Assumed-fill placement algorithm + simulation
├── access_rules.py               ← All logic rules (gates, items, soul levels)
├── regions.py                    ← Region graph and connections
├── locations.py                  ← Location class definitions used by the graph
├── BaseClasses.py                ← Lightweight state/region base classes
├── constants.py                  ← File lists, level folders, EXE item type IDs
├── kpf_handler.py                ← KPF archive extraction and mod packing
├── gad_pickup_patch.py           ← EXE patch for gad pickup type_id dispatch
├── setup_gad_records.py          ← Injects RSC_X_GAD_PICKUP records into temples
├── enemy_randomizer.py           ← Enemy type shuffle logic
├── music_randomizer.py           ← Music shuffle logic
├── sfx_randomizer.py             ← Voice and weapon SFX shuffle logic
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
4. **Fill** — runs assumed-fill to place progression items logically
5. **Gate shuffle** — writes new SL thresholds to `links.e2o` files
6. **Patch RSC** — writes new item names to all RSC files
7. **Patch enemies** — shuffles enemy type names in enemies RSC files (if enabled)
8. **Patch EXE** — writes prison key card position fix (always) + gad pickup dispatch (if enabled)
9. **Update decos** — renames ARC coffin gate decorations to match new SL values
10. **Repack** — packs all modified files into `shadowman_randomizer.kpf`

---

## Location Data

All randomizable locations are defined in `data/locations.csv`. Each row defines:
- Where the item physically lives (`level_id`, `source_file`, `offset`)
- What item is there in vanilla (`object`, `category`)
- What logic gates access to it (`level_region`, `sub_region`)

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

Gates SL0 (Marrow), SL10 (Mystery), and Fogometers Interior are always locked
at their vanilla values. All other gates are freely shuffled with the constraint
that the three starting gates (Wasteland, Asylum, Path 3) never exceed SL3.

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

## Known Limitations

- Ambient enemy shuffle (flies, rats, egrets etc.) is catalogued and categorised
  but disabled pending stability testing

---

## Contributing

Bug reports, seed pathology cases, and PRs welcome. When filing an issue, please
include the seed, the exact CLI flags you used, and the spoiler log if the run
completed.

---

## Credits

- Game by Nightdive Studios
- Randomizer by the Shadow Man modding community
