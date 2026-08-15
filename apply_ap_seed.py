"""
apply_ap_seed.py
=================
The local half of Shadow Man Remastered's Archipelago integration.

Archipelago seed generation (whether run on the AP website or locally via
the CLI generator) never needs Shadow Man Remastered installed. The AP
world's generate_output() only ever writes a small *.apshadowman JSON file
into the multiworld output — safe to bundle into the hosting zip, since it's
just placement/config data, no game files. Whoever is actually going to
PLAY that seed runs this script, locally, against their own legally-owned
copy of the game:

    python apply_ap_seed.py path/to/AP_12345_P1_Alice.apshadowman --game-dir "C:/.../Shadow Man Remastered"

This performs the exact same patching (KPF extraction, RSC placement writes,
EXE patches, KPF repack) that ap_patcher.py's run_patcher() always did —
only now it's a separate, explicit step instead of happening automatically
inside AP's own generation process. ap_patcher.py is a ported copy of the
Archipelago world's own patcher.py (see its module docstring) — same
engine, just fed from a JSON file here instead of directly from AP's fill
algorithm in-process.

Output (spoiler log, object_map.csv, soul_thresholds.json, the patched exe,
the mod KPF) lands in --output-dir (default: <game-dir>/_randomizer_work_<seed>
for the logs/JSON, a seed-specific folder matching ap_patcher.py's own
work_path naming so multiple seeds don't overwrite each other's logs; the
mod + exe always go to <game-dir>/mods and <game-dir>/thoth_x64_patched.exe
respectively, same as any other seed).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import namedtuple
from pathlib import Path

from ap_patcher import run_patcher

# Mirrors the fields actually read off a progression_placement value
# anywhere in the local patching pipeline: ap_patcher.py's run_patcher()
# itself only touches .object/.save_idx, but patchers/levels_txt_patcher.py
# (called deeper in the same pipeline, for the in-game hint tracker) also
# reads .level_id (hard requirement — a missing one raises AttributeError,
# caught live 2026-07-20) and .category (soft requirement via getattr, used
# for $cadeaux per-level counts). Deliberately NOT the AP world's full
# RawLocation namedtuple; this file never needs anything else.
PlacementEntry = namedtuple("PlacementEntry", ["object", "save_idx", "level_id", "category"])


def load_patch_data(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    version = data.get("format_version")
    if version != 1:
        raise ValueError(
            f"Unsupported .apshadowman format_version {version!r} "
            f"(this tool understands version 1). Update apply_ap_seed.py, "
            f"or re-generate the seed with a matching AP world version."
        )
    if data.get("game") != "Shadow Man Remastered":
        raise ValueError(
            f"This file is for {data.get('game')!r}, not Shadow Man "
            f"Remastered — wrong .apshadowman file?"
        )
    return data


def build_progression_placement(raw: dict) -> dict:
    """
    level_id/category were added to the .apshadowman schema alongside this
    fix (2026-07-20). Older files generated before that won't have them —
    fall back to deriving level_id from the loc_key prefix (always correct;
    AP's generate_output() only ever builds progression_placement entries
    from the destination slot's own raw_loc, so level_id == loc_key's own
    level segment) and leave category as None (only affects levels.txt's
    $cadeaux per-level counts, degrades gracefully via getattr elsewhere).
    """
    result = {}
    for loc_key, entry in raw.items():
        level_id = entry.get("level_id") or loc_key.split(":")[0]
        result[loc_key] = PlacementEntry(
            object=entry["object"],
            save_idx=entry["save_idx"],
            level_id=level_id,
            category=entry.get("category"),
        )
    return result


def build_config(raw: dict) -> dict:
    """
    JSON round-trips dict keys as strings — soul_thresholds_precomputed (if
    present) needs its SL keys back as ints. ap_patcher.py's Step 6e already
    does this same int(k) conversion defensively (slot_data does the same
    round-trip over the network to the client), so this isn't strictly
    required, but doing it here too keeps the config dict's shape identical
    to what generate_output() originally built, in case anything else ever
    starts relying on it being int-keyed.
    """
    config = dict(raw)
    precomputed = config.get("soul_thresholds_precomputed")
    if precomputed is not None:
        config["soul_thresholds_precomputed"] = {int(k): int(v) for k, v in precomputed.items()}
    return config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply an Archipelago-generated Shadow Man Remastered seed locally."
    )
    parser.add_argument("patch_file", type=Path,
                         help="Path to the .apshadowman file from your AP output.")
    parser.add_argument("--game-dir", type=Path, required=True,
                         help="Path to your Shadow Man Remastered install.")
    parser.add_argument("--output-dir", type=Path, default=None,
                         help="Where to write the spoiler log / object_map.csv / "
                              "soul_thresholds.json. Default: seed-specific folder "
                              "<game-dir>/_randomizer_work_<seed>, matching the work "
                              "folder ap_patcher.py already writes to.")
    args = parser.parse_args()

    if not args.patch_file.exists():
        sys.exit(f"Patch file not found: {args.patch_file}")
    if not args.game_dir.is_dir():
        sys.exit(f"Game directory not found: {args.game_dir}")

    data = load_patch_data(args.patch_file)
    progression_placement = build_progression_placement(data["progression_placement"])
    config = build_config(data["config"])
    gate_remap = data["gate_remap"]
    # Added 2026-07-21 (entrance randomizer, Task 20) — dict[portal_file,
    # dest_portal_file] or None. .get() (not []) for backward compatibility
    # with .apshadowman files written before this key existed.
    entrance_shuffle = data.get("entrance_shuffle")
    seed = data["seed"]
    player = data.get("player", "?")

    # Fixed 2026-07-22: previously defaulted to a single shared
    # <game-dir>/randomizer_output folder, so every seed's spoiler log /
    # object_map.csv / soul_thresholds.json overwrote the last one. Now
    # matches ap_patcher.py's own work_path naming (_randomizer_work_<seed>)
    # so logs land next to the seed's actual work folder and don't collide
    # across seeds.
    output_dir = args.output_dir or (args.game_dir / f"_randomizer_work_{seed}")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Applying Archipelago seed {seed} (player: {player}) to {args.game_dir} ...")
    run_patcher(
        game_dir              = str(args.game_dir),
        seed                  = seed,
        config                = config,
        output_dir            = str(output_dir),
        progression_placement = progression_placement,
        gate_remap            = gate_remap,
        entrance_shuffle      = entrance_shuffle,
    )
    print(f"\nDone. Mod installed to {args.game_dir / 'mods'}, "
          f"patched exe at {args.game_dir / 'thoth_x64_patched.exe'}, "
          f"logs in {output_dir}.")
    print("Launch thoth_x64_patched.exe (not the vanilla exe) to play this seed.")


if __name__ == "__main__":
    main()
