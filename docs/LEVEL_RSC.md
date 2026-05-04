# Per-level RSC files (quest, enemies, instance)

Each level folder under `data/levels/` can contain:

| File            | Purpose (game)       | Randomizer status                                   |
|-----------------|----------------------|-----------------------------------------------------|
| **quest.rsc**   | Pickups, souls, items| **Parsed & patched** in `patcher.py`; also in RSC pass |
| **enemies.rsc** | Enemy spawns/state   | **Parser stub** in `rsc_parsers.py`                 |
| **instance.rsc**| Object instances     | **Parser stub** in `rsc_parsers.py`                 |

## RSC pass order (continue from as3schis)

When documenting or reverse-engineering all three RSC types per level, use the **RSC pass** so we don’t redo work. Pass order is **alphabetical by folder name**, starting at **as3schis** (as2exper was the last folder completed before context limits).

- **Script:** `python rsc_parsers.py <path/to/data/levels>`
- **List folders only:** `python rsc_parsers.py --list-only`
- **Start from a different folder:** `python rsc_parsers.py "C:\...\levels" --from as4dkeng`

The script parses **quest.rsc**, **enemies.rsc**, and **instance.rsc** for each level and prints a one-line summary (record counts + header magic) for all three.

Parsers in `rsc_parsers.py` detect header (`Erscv002`, `RSC1`, `RSC2`), scan for `RSC_`/`rsc_`-prefixed names, and optionally read coords (same layout as quest.rsc when magic is `Erscv002`). Extend the parsers as formats are fully reversed.

**Example files:** One example of each (quest.rsc, enemies.rsc, instance.rsc) from the same level is useful to verify headers and record layout and to adjust parsers if the game uses a different structure.

## Level folders (in pass order from as3schis)

From `get_rsc_pass_order()`:

1. as3schis, as4dkeng, asyiggy, asylum, deadside, florida  
2. nflorida, nprison, nsalvage, ntenemnt, nuground, prison, salvage  
3. swampday, swampnit, t1tchgad, t2wlkgad, t3swmgad, t4ndgad, tenement, uground, wastland  

(ah1cagew, ah2playr, ah3lavad, ah4fogom, as2exper are *before* as3schis alphabetically and are not in this pass.)
