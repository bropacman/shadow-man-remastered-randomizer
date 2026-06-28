"""
levels_txt_patcher.py
=====================
Patches scripts/levels.txt so the in-game map tracker reflects randomized
item locations and their actual access requirements.

After fill() runs, we know exactly where every progression item ended up and
what gate_raw condition guards that slot.  This module rewires the $directive
entries in levels.txt accordingly — moving each item to the correct level
block and writing a condition string derived from:

  • Region entry requirement  (PATHSOFSHADOW + SL gate to reach the level)
  • Sub-region gate           (gate_raw on the destination location)

SL values are read from gate_remap so shuffled gates are reflected correctly.
"""

from __future__ import annotations
import re
from pathlib import Path
from constants import GATE_VANILLA_SL, GATE_E2O_POSITIONS, E2O_MATCH_RADIUS, PRESERVED_SOUL_IDS

# RSC names that count as a cadeaux collectible when placed in any slot.
# Mirrors patcher.py CADEAUX_TYPES — kept local to avoid a circular import.
_CADEAUX_RSC: frozenset[str] = frozenset({
    "RSC_CADEAUX",
    "RSC_X_CADEAUX",
    "RSC_PICKUP_CADEAUX",
})

# Barrel/crate RSC names — a barrel is a cadeaux only when save_idx != 0.
# Mirrors patcher.py BARREL_TYPES + fill.py _PLAIN_BARREL_POOL.
_BARREL_RSC: frozenset[str] = frozenset({
    "RSC_X_BARREL",
    "RSC_X_BARREL_A",
    "RSC_X_BARREL_D",
    "RSC_X_BARREL_L",
    "RSC_EXPLOSIVE_BARREL",
    "RSC_TE_PACKBOX1",
    "RSC_TE_PACKBOX2",
    "RSC_FL_CRATE",
    "RSC_UN_CRATES",
})

# ── Level ID → levels.txt block number ───────────────────────────────────────

LEVEL_TO_NUM: dict[str, int] = {
    "swampday": 0,  "swampnit": 0,
    "tenement": 1,  "ntenemnt": 1,
    "prison":   2,  "nprison":  2,
    "uground":  3,  "nuground": 3,
    "florida":  4,  "nflorida": 4,
    "salvage":  5,  "nsalvage": 5,
    # t4ndgad is intentionally NOT mapped here. It is a cut sub-zone of salvage
    # with 4 RSC_CADEAUX in its resource.rsc (all is_verified=False, inaccessible).
    # Vanilla levels.txt $cadeaux 35 for level 5 appears to count those 4 — giving
    # 31 (salvage) + 4 (t4ndgad) = 35.  We do NOT replicate that here because
    # t4ndgad has no entrance: adding them would make the tracker show 35 expected
    # with only 31 collectable, breaking 100% completion for players.
    # The patched total will be 662 instead of 666 as a result — this is a known
    # vanilla data artifact from a cut level, not a randomizer bug.
    "deadside": 6,
    "wastland": 7,
    "asylum":   8,
    "as2exper": 9,
    "as3schis": 10,
    "as4dkeng": 11,
    "t1tchgad": 12,
    "ah1cagew": 13,
    "ah2playr": 14,
    "t2wlkgad": 15,
    "ah3lavad": 16,
    "t3swmgad": 17,
    "ah4fogom": 18,
}

# ── Region entry requirements ─────────────────────────────────────────────────

# Gate that guards entry into each deadside/asylum level from the hub
_LEVEL_ENTRY_GATE: dict[str, str] = {
    "wastland": "GATE_DEADSIDE_WASTELAND",
    "asylum":   "GATE_DEADSIDE_ASYLUM",
    "as2exper": "GATE_DEADSIDE_ASYLUM",
    "as3schis": "GATE_DEADSIDE_ASYLUM",
    "as4dkeng": "GATE_DEADSIDE_ASYLUM",
    "t1tchgad": "GATE_DEADSIDE_PATH_3",
    "ah1cagew": "GATE_DEADSIDE_CAGEWAYS",
    "ah2playr": "GATE_DEADSIDE_PLAYROOMS",
    "t2wlkgad": "GATE_DEADSIDE_PATH_6",
    "ah3lavad": "GATE_DEADSIDE_LAVADUCTS",
    "t3swmgad": "GATE_DEADSIDE_BLOOD",
    "ah4fogom": "GATE_DEADSIDE_FOGOMETERS",
}

# Liveside night-variant levels — enemies here require the Eclipse to appear.
_NIGHT_LEVELS: frozenset[str] = frozenset({
    "swampnit", "ntenemnt", "nprison", "nuground", "nflorida", "nsalvage",
})

# Levels requiring PATHSOFSHADOW to enter
_DEADSIDE_LEVELS: frozenset[str] = frozenset({
    "deadside", "wastland", "asylum", "as2exper", "as3schis", "as4dkeng",
    "t1tchgad", "ah1cagew", "ah2playr", "t2wlkgad", "ah3lavad", "t3swmgad", "ah4fogom",
})

# Extra flags needed beyond PATHSOFSHADOW + SL to enter specific levels
_LEVEL_ENTRY_EXTRA: dict[str, set[str]] = {
    "as2exper": {"ENGKEY"},
    "as3schis": {"ENGKEY"},
    "as4dkeng": {"ENGKEY"},
}

# ── RSC name → levels.txt directive ──────────────────────────────────────────

RSC_TO_DIRECTIVE: dict[str, str | None] = {
    "RSC_X_POIGNE":          "poigne",
    "RSC_X_FLAMBEAU":        "flambeau",
    "RSC_X_MARTEAU":         "marteau",
    "RSC_X_CALABASH":        "calabash",
    "RSC_X_BATON":           "baton",
    "RSC_X_ASSON":           "asson",
    "RSC_X_ENSEIGNE":        "enseigne",
    "RSC_X_ENGINEERS_KEY":   "engineerskey",
    "RSC_X_PRISON_KEY_CARD": "keycard",
    "RSC_X_BOOK_OF_SHADOWS": "bookofshadows",
    "RSC_X_PROPHECY":        "prophecy",
    "RSC_X_FLASHLIGHT":      "flashlight",
    "RSC_X_JACKS_SCHEMATIC": "journal",
    # Eclipser pieces — vanilla order: part1=lalune, part2=lesoleil, part3=lalame
    "RSC_X_ECLIPSER_PART1":  "lalune",
    "RSC_X_ECLIPSER_PART2":  "lesoleil",
    "RSC_X_ECLIPSER_PART3":  "lalame",
    "RSC_X_LALUNE":          "lalune",
    "RSC_X_SOLEIL":          "lesoleil",
    "RSC_X_LALAME":          "lalame",
    # Weapons
    # RSC_Q_VIOLATOR is the loose collectible Violator — maps to $violator2.
    # RSC_X_VIOLATOR is the accumulator-mechanism Violator — maps to $violator.
    # VIO_PLINTH is never triggered in a randomised run so $violator hints won't
    # clear; the loc_english override labels them "(won't clear)" accordingly.
    "RSC_X_VIOLATOR":        "violator",
    "RSC_Q_VIOLATOR":        "violator2",
    "RSC_X_TETEDEMORT":      "tetedemort",
    "RSC_X_MAC10":           "mac10",
    "RSC_X_MP5":             "mp909",
    "RSC_X_SHOTGUN":         "shotgun",
    "RSC_X_SHOTGUN2":        "sawedshotgun",
    "RSC_X_LIGHT_SOUL":      "lightsoul",
    # Dark souls / govi — directive is "darksoul <save_idx>", resolved at patch time
    "RSC_X_DARK_SOUL":       "darksoul",
    "RSC_X_GOVI":            "darksoul",
    # Multi-instance items — condition updated but not moved (first placement wins)
    "RSC_X_ACCUMULATOR":     "accumulator",
    "RSC_X_RETRACT":         "retractor",
    "RSC_X_RETRACT1":        "retractor",
    "RSC_X_RETRACT2":        "retractor",
    # GAD pickups handled separately via GAD_DIRECTIVE below
    "RSC_X_GAD_PICKUP":      None,
}

# Directives superseded in randomized output: no longer in RSC_TO_DIRECTIVE.values()
# but must still be stripped so vanilla leftovers don't pollute the output.
# Note: $violator is now a live directive again (RSC_X_VIOLATOR → "violator") so
# it is NOT in this set — it gets stripped and re-injected through the normal loop.
_STRIP_ONLY_DIRECTIVES: frozenset[str] = frozenset()

# RSC_X_GAD_PICKUP directive is determined by the item's ORIGINAL level
# (which temple it came from), not where it was randomized to
GAD_DIRECTIVE: dict[str, str] = {
    "t1tchgad": "touchgad",
    "t2wlkgad": "walkgad",
    "t3swmgad": "swimgad",
}

# ── gate_raw → flag translation ───────────────────────────────────────────────

# Simple token → flag set (non-gate tokens in gate_raw expressions)
_TOKEN_FLAGS: dict[str, set[str]] = {
    "ENG_KEY":         {"ENGKEY"},
    "POIGNE":          {"POIGNE"},
    "NIGHT":           {"ECLIPSE"},       # user confirmed: NIGHT = ECLIPSE
    "GAD2_WALK":       {"WALKGAD"},
    "GAD3_SWIM":       {"SWIMGAD"},
    "PRISON_KEY_CARD": {"KEYCARD"},
    "BATON":           {"BATON"},
    "FLAMBEAU":        {"FLAMBEAU"},
    "MARTEAU":         {"MARTEAU"},
    "CALABASH":        {"CALABASH"},
    "GAD1_HAND":       {"TOUCHGAD"},
    "TOUCHGAD":        {"TOUCHGAD"},
    "GAD2_WALK":       {"WALKGAD"},
    "WALKGAD":         {"WALKGAD"},
    "GAD3_SWIM":       {"SWIMGAD"},
    "SWIMGAD":         {"SWIMGAD"},
    "X3_ACCUMULATOR":  set(),             # complex count check — omit
}

# Extra ability flags required to physically pass specific gates
_GATE_EXTRA_FLAGS: dict[str, set[str]] = {
    "GATE_FIRE_POIGNE": {"TOUCHGAD"},
}


def _gate_raw_to_flags(gate_raw: str | None, gate_remap: dict[str, int]) -> set[str]:
    """Translate a gate_raw expression into a set of levels.txt flag strings."""
    if not gate_raw:
        return set()
    # OR condition: take the first (primary) branch
    expr = gate_raw.split("|")[0].strip()
    tokens = [t.strip() for t in expr.replace("(", "").replace(")", "").split("&")]
    flags: set[str] = set()
    max_sl = 0
    for tok in tokens:
        tok = tok.strip()
        if tok.startswith("GATE_"):
            sl = gate_remap.get(tok, GATE_VANILLA_SL.get(tok, 0))
            max_sl = max(max_sl, sl)
            flags.update(_GATE_EXTRA_FLAGS.get(tok, set()))
        else:
            flags.update(_TOKEN_FLAGS.get(tok, set()))
    if max_sl:
        flags.add(f"SL{max_sl}")
    return flags


def _level_entry_flags(level_id: str, gate_remap: dict[str, int]) -> set[str]:
    """Flags required to enter a level's region."""
    if level_id not in _DEADSIDE_LEVELS:
        return set()
    flags: set[str] = {"PATHSOFSHADOW"}
    gate = _LEVEL_ENTRY_GATE.get(level_id)
    if gate:
        sl = gate_remap.get(gate, GATE_VANILLA_SL.get(gate, 0))
        if sl:
            flags.add(f"SL{sl}")
    flags.update(_LEVEL_ENTRY_EXTRA.get(level_id, set()))
    return flags


def _order_flags(flags: set[str]) -> list[str]:
    """Order: PATHSOFSHADOW → highest SL only → everything else alphabetical."""
    sl_flags = {f for f in flags if f.startswith("SL")}
    rest = flags - sl_flags - {"PATHSOFSHADOW"}
    ordered: list[str] = []
    if "PATHSOFSHADOW" in flags:
        ordered.append("PATHSOFSHADOW")
    if sl_flags:
        ordered.append(f"SL{max(int(f[2:]) for f in sl_flags)}")
    ordered.extend(sorted(rest))
    return ordered


def _build_condition(level_id: str, gate_raw: str | None, gate_remap: dict[str, int]) -> str:
    """Full levels.txt condition string for an item placed at a given location."""
    flags = _level_entry_flags(level_id, gate_remap)
    flags.update(_gate_raw_to_flags(gate_raw, gate_remap))
    if not flags:
        return "NONE"
    # Order: PATHSOFSHADOW → highest SL only → everything else alphabetical
    sl_flags = {f for f in flags if f.startswith("SL")}
    rest = flags - sl_flags - {"PATHSOFSHADOW"}
    ordered: list[str] = []
    if "PATHSOFSHADOW" in flags:
        ordered.append("PATHSOFSHADOW")
    if sl_flags:
        ordered.append(f"SL{max(int(f[2:]) for f in sl_flags)}")
    ordered.extend(sorted(rest))
    return " ".join(ordered)


# ── levels.txt parser / serializer ───────────────────────────────────────────

def _parse(text: str) -> list[dict]:
    """Parse levels.txt into level block dicts: {num, header, lines}."""
    blocks: list[dict] = []
    current: dict | None = None
    in_block = False
    for line in text.splitlines():
        m = re.match(r'\$level\s+(\d+)', line)
        if m:
            current = {"num": int(m.group(1)), "header": line, "lines": []}
            in_block = False
            blocks.append(current)
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped == "{":
            in_block = True
        elif stripped == "}":
            in_block = False
        elif in_block:
            current["lines"].append(line)
    return blocks


def _serialize(blocks: list[dict]) -> str:
    """Serialize parsed blocks back to levels.txt text."""
    parts: list[str] = []
    for b in blocks:
        parts.append(b["header"])
        parts.append("{")
        parts.extend(b["lines"])
        parts.append("}")
        parts.append("")
    return "\n".join(parts)


def _directive_re(name: str) -> re.Pattern:
    return re.compile(rf'^\s*\${re.escape(name)}\b', re.IGNORECASE)


# ── Public API ────────────────────────────────────────────────────────────────

def _patch_coffingate_lines(blocks: list[dict], gate_remap: dict[str, int]) -> int:
    """
    Rewrite $coffingate SL values in-place across all level blocks.

    Each $coffingate line carries XZ world coordinates which are matched
    against GATE_E2O_POSITIONS (within E2O_MATCH_RADIUS) to identify the
    gate.  The SLx token in the condition string is then replaced with the
    shuffled value from gate_remap.

    Returns the number of lines updated.
    """
    # Build a flat coord→gate_id lookup (level-agnostic for $coffingate lines
    # which appear in the level block that physically contains them)
    coord_to_gate: list[tuple[int, int, str, str]] = [
        (gx, gz, gate_id, level_id)
        for gate_id, (level_id, gx, gz) in GATE_E2O_POSITIONS.items()
    ]

    _coffingate_re = re.compile(
        r'^(\s*\$coffingate\s+")(.*?)(")\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)(.*)',
        re.IGNORECASE,
    )
    _sl_token_re = re.compile(r'\bSL\d+\b')

    updated = 0
    for b in blocks:
        new_lines = []
        for line in b["lines"]:
            m = _coffingate_re.match(line)
            if not m:
                new_lines.append(line)
                continue

            prefix, condition, quote, cx, cy, cz, suffix = m.groups()
            fx, fz = float(cx), float(cz)

            # Find the closest matching gate by XZ distance
            best_gate = None
            best_dist = float("inf")
            for gx, gz, gate_id, _ in coord_to_gate:
                dist = ((fx - gx) ** 2 + (fz - gz) ** 2) ** 0.5
                if dist < best_dist and dist < E2O_MATCH_RADIUS:
                    best_dist = dist
                    best_gate = gate_id

            if best_gate is None:
                new_lines.append(line)
                continue

            new_sl = gate_remap.get(best_gate, GATE_VANILLA_SL.get(best_gate, 0))
            if new_sl == 0:
                new_condition = _sl_token_re.sub("", condition).strip()
                # Collapse any double-spaces left after removing the SL token
                new_condition = re.sub(r'\s{2,}', ' ', new_condition)
                # Empty condition (SL-only gate) → "NONE" so the game doesn't crash
                if not new_condition:
                    new_condition = "NONE"
            else:
                sl_token = f"SL{new_sl}"
                if _sl_token_re.search(condition):
                    new_condition = _sl_token_re.sub(sl_token, condition)
                else:
                    new_condition = f"{condition} {sl_token}".strip()

            new_lines.append(f'{prefix}{new_condition}{quote} {cx} {cy} {cz}{suffix}')
            updated += 1

        b["lines"] = new_lines
    return updated


def strip_levels_txt(source_path: Path, output_path: Path) -> None:
    """
    Read vanilla levels.txt and remove every $directive line whose directive
    is tracked by this module (RSC_TO_DIRECTIVE + GAD_DIRECTIVE values).
    Leaves all other content (level headers, non-item directives) intact.
    Used by default so a randomized install shows no incorrect map badges.
    """
    all_directives: set[str] = set()
    for v in RSC_TO_DIRECTIVE.values():
        if v is not None:
            all_directives.add(v)
    all_directives.update(GAD_DIRECTIVE.values())
    all_directives.update(_STRIP_ONLY_DIRECTIVES)

    # All item directives stay in their original level blocks (removing dark
    # souls entirely crashes the engine's 120-soul count check).  Instead,
    # every matched directive has its condition replaced with "SL10" — which
    # requires collecting all 120 dark souls first, so hints never surface
    # during normal play.  XYZ coordinates (retractor, accumulator, etc.) are
    # preserved intact after the condition token.
    patterns = [_directive_re(d) for d in all_directives]
    patterns.append(_directive_re("coffingate"))
    _cond_re = re.compile(r'^(\s*\$\w+(?:\s+\d+)?\s+)"[^"]*"(.*)', re.IGNORECASE)

    blocks = _parse(source_path.read_text(encoding="utf-8"))
    hidden_total = 0

    for b in blocks:
        new_lines = []
        for line in b["lines"]:
            if any(p.search(line) for p in patterns):
                m = _cond_re.match(line)
                if m:
                    new_lines.append(f'{m.group(1)}"SL10"{m.group(2)}')
                    hidden_total += 1
                # lines that don't match the condition pattern are dropped
            else:
                new_lines.append(line)
        b["lines"] = new_lines

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_serialize(blocks), encoding="utf-8")
    print(f"  [levels_txt] Stripped → {output_path.name}  "
          f"({hidden_total} item directive(s) hidden behind SL10)")


def patch_levels_txt(
    source_path: Path,
    progression_placement: dict,           # loc_key → source RawLocation (item)
    gate_remap: dict[str, int],
    output_path: Path,
    true_form_loc_remap: dict[str, str] | None = None,  # src_loc_key → dst_loc_key
    starting_item_bundles: list | None = None,
    y_adj_map: dict[str, float] | None = None,  # loc_key → y_adjust applied to physical item
    retractor_actual_xyz: dict | None = None,    # level_id → {'retractor'/'accumulator': [(x,y,z,zone),...]}
                                                 # scanned from patched RSC files; consumed (popped) here
) -> None:
    """
    Read vanilla levels.txt, rewire $directive entries to match the randomized
    placement, and write the result to output_path.
    """
    from extracted_locations import LOCATION_TABLE

    blocks = _parse(source_path.read_text(encoding="utf-8"))
    by_num = {b["num"]: b for b in blocks}

    # Reverse map: block number → level_id (for retractor_actual_xyz lookup).
    # LEVEL_TO_NUM iterates in LEVEL_FOLDERS order: day variants always come
    # before night variants (e.g. "swampday" before "swampnit"), so the first
    # entry seen for each block number is always the primary (day) folder —
    # which is where the RSC files actually live.
    _num_to_level: dict[int, str] = {}
    for _lid, _num in LEVEL_TO_NUM.items():
        if _num not in _num_to_level:
            _num_to_level[_num] = _lid

    # Pre-scan: collect soul IDs that actually exist in vanilla levels.txt.
    # The location pool may have more soul-type slots than there are $darksoul
    # entries (e.g. extra govi slots), so we only update IDs already present.
    _soul_id_re = re.compile(r'^\s*\$darksoul\s+(\d+)\b', re.IGNORECASE)
    existing_soul_ids: set[int] = set()
    for b in blocks:
        for line in b["lines"]:
            m = _soul_id_re.match(line)
            if m:
                existing_soul_ids.add(int(m.group(1)))

    # Build: list of (directive, target_level_num, condition, dest, loc_key).
    # Multiple items sharing a base directive (retractor, accumulator, darksoul)
    # each get their own entry so all hints are written — no "last wins" collapse.
    # dest is the RawLocation for the destination slot; used to supply accurate
    # world coordinates for directives that need them (retractor, accumulator).
    # loc_key is carried through so y_adj_map can be applied to the Y coordinate.
    directive_entries: list[tuple[str, int, str, object, object]] = []

    for loc_key, source_loc in progression_placement.items():
        dest = LOCATION_TABLE.get(loc_key)
        if dest is None:
            continue

        rsc_name = source_loc.object
        level_num = LEVEL_TO_NUM.get(dest.level_id)
        if level_num is None:
            continue

        # Resolve directive
        if rsc_name == "RSC_X_GAD_PICKUP":
            directive = GAD_DIRECTIVE.get(source_loc.level_id)
        elif rsc_name in ("RSC_X_DARK_SOUL", "RSC_X_GOVI"):
            # levels.txt format: $darksoul <soul_id> "<condition>"
            # The patcher writes source_loc.save_idx into the destination
            # slot's record (via the "reward" field → INSTANCE_OFF).  The game
            # therefore tracks this soul by the SOURCE soul's ID, not the
            # destination slot's ID.  Use source_loc.save_idx here so the
            # tracker entry matches what the game world reports.
            soul_id = source_loc.save_idx
            if not soul_id or soul_id not in existing_soul_ids:
                continue
            directive = f"darksoul {soul_id}"
        else:
            directive = RSC_TO_DIRECTIVE.get(rsc_name)
        if not directive:
            continue

        # Vanilla never includes PATHSOFSHADOW/SL level-entry flags in any
        # item condition — the tracker is already scoped per level so the
        # player knows which level they're in.  Use sub-region flags only
        # for all item types, matching vanilla format.
        flags = _gate_raw_to_flags(dest.gate_raw, gate_remap)
        ordered = _order_flags(flags)
        condition = " ".join(ordered) if ordered else "NONE"
        directive_entries.append((directive, level_num, condition, dest, loc_key))

    # Bundled items: inject tracker entries pointing to swampday (level 0).
    # Bundled items are excluded from fill, so they never appear in
    # progression_placement. Without this block their vanilla levels.txt entries
    # would remain, showing wrong locations.
    if starting_item_bundles:
        from constants import STARTING_ITEM_BUNDLES as _SIB
        from fill import CHECKABLE_LOCS

        class _SwampDest:
            x, y, z, zone = 15415.0, 1800.0, 6840.0, 5  # first swampday bench

        _bundle_dest = _SwampDest()
        all_bundled_rscs = {r for bk in starting_item_bundles for r in _SIB.get(bk, [])}

        for _loc in CHECKABLE_LOCS:
            _rsc = getattr(_loc, 'object', None)
            if _rsc not in all_bundled_rscs:
                continue
            if _rsc == "RSC_X_GAD_PICKUP":
                _directive = GAD_DIRECTIVE.get(_loc.level_id)
            else:
                _directive = RSC_TO_DIRECTIVE.get(_rsc)
            if not _directive:
                continue
            directive_entries.append((_directive, 0, "NONE", _bundle_dest, None))

    # True form souls — use loc_key_remap from randomize_true_forms.
    # src carries the soul ID (save_idx written to dst slot by enemy randomizer);
    # dst provides the level and access condition.
    if true_form_loc_remap:
        from extracted_enemy_locations import ENEMY_TABLE
        for src_key, dst_key in true_form_loc_remap.items():
            src_rec = ENEMY_TABLE.get(src_key)
            dst_rec = ENEMY_TABLE.get(dst_key)
            if src_rec is None or dst_rec is None:
                continue
            soul_id = src_rec.save_idx
            if not soul_id or soul_id not in existing_soul_ids:
                continue
            level_num = LEVEL_TO_NUM.get(dst_rec.level_id)
            if level_num is None:
                continue
            # EnemyLocation uses sub_region for the raw gate expression
            # ("N" means no requirement, equivalent to None)
            gate_raw = dst_rec.sub_region if dst_rec.sub_region and dst_rec.sub_region != "N" else None
            flags = _gate_raw_to_flags(gate_raw, gate_remap)
            # Night-variant levels are only accessible after the Eclipse
            if dst_rec.level_id in _NIGHT_LEVELS:
                flags.add("ECLIPSE")
            ordered = _order_flags(flags)
            condition = " ".join(ordered) if ordered else "NONE"
            directive_entries.append((f"darksoul {soul_id}", level_num, condition, None, None))

    # Bulk-strip randomizable $darksoul entries up front so vanilla souls that
    # share a block with randomized placements don't produce duplicate "Find X
    # Dark Souls" groups.
    # Boss souls (IDs 9-13) are always preserved — they never move.
    # True form souls are preserved when shuffle_true_forms is off (no remap
    # means they stay at their vanilla locations); stripped and re-injected via
    # true_form_loc_remap when shuffle_true_forms is on.
    from constants import TRUE_FORM_SOUL_IDS
    preserve_ids = set(PRESERVED_SOUL_IDS)
    if not true_form_loc_remap:
        preserve_ids.update(TRUE_FORM_SOUL_IDS)

    _darksoul_id_re = re.compile(r'^\s*\$darksoul\s+(\d+)\b', re.IGNORECASE)
    for b in blocks:
        kept = []
        for line in b["lines"]:
            m = _darksoul_id_re.match(line)
            if m and int(m.group(1)) not in preserve_ids:
                continue   # strip — will be re-injected from randomizer fill
            kept.append(line)
        b["lines"] = kept

    # Strip superseded directives (e.g. $violator → replaced by $violator2).
    # These no longer appear in RSC_TO_DIRECTIVE so the per-entry loop won't
    # touch them, but their vanilla lines must be removed from the output.
    for strip_d in _STRIP_ONLY_DIRECTIVES:
        pat = _directive_re(strip_d)
        for b in blocks:
            b["lines"] = [l for l in b["lines"] if not pat.search(l)]

    # Apply: for each entry, remove the matching vanilla line(s) and inject the
    # updated line into the target block.  Each entry is processed independently
    # so multi-instance directives (retractor, accumulator) all appear.
    #
    # directive_has_coords tracks whether a directive's vanilla lines carried
    # XYZ world coordinates after the condition string.  When True we build the
    # suffix from the DESTINATION slot's actual coordinates (dest.x/y/z/zone)
    # rather than reusing the vanilla coords — which would point to the wrong
    # world position after shuffle and prevent the badge from clearing on pickup.
    written: set[str] = set()
    directive_has_coords: dict[str, bool] = {}

    for directive, target_num, condition, dest, entry_loc_key in directive_entries:
        # darksoul entries already bulk-stripped above; all others strip on
        # first encounter of that directive name.
        pat = _directive_re(directive)
        is_darksoul = directive.startswith("darksoul ")

        if directive not in written:
            has_coords = False
            if not is_darksoul:
                for b in blocks:
                    kept, removed = [], []
                    for line in b["lines"]:
                        (removed if pat.search(line) else kept).append(line)
                    b["lines"] = kept
                    if not has_coords:
                        for line in removed:
                            m = re.search(r'"[^"]*"(.*)', line)
                            if m and m.group(1).strip():
                                has_coords = True
                                break
            directive_has_coords[directive] = has_coords
            written.add(directive)

        # Build the coordinate suffix from the actual destination location.
        # Retractor and accumulator lines include "X Y Z zone" after the
        # condition; the XYZ must exactly match what the game writes into the
        # save manager's spatial lookup array (populated from the live RSC
        # record when the level loads).  We use retractor_actual_xyz — built
        # by scanning the already-patched RSC files — so there is zero chance
        # of a mismatch between the directive and the RSC binary.  This handles
        # both progression items (where y_adj was applied to the RSC) and bundle
        # items (whose positions are computed, not stored in LOCATION_TABLE).
        coords_suffix = ""
        if directive_has_coords.get(directive):
            _dtype = "accumulator" if directive.startswith("accumulator") else "retractor"
            _level_id = (_num_to_level.get(target_num)
                         if dest is None
                         else getattr(dest, 'level_id', _num_to_level.get(target_num)))
            _xyz_list = (retractor_actual_xyz or {}).get(_level_id or "", {}).get(_dtype, [])
            if _xyz_list:
                _ax, _ay, _az, _azone = _xyz_list.pop(0)
                coords_suffix = f" {_ax:.6f} {_ay:.6f} {_az:.6f} {_azone}"
            elif dest is not None:
                # Fallback: dest coords + y_adj (progression items without scan data)
                dx = getattr(dest, 'x', None)
                dy = getattr(dest, 'y', None)
                dz = getattr(dest, 'z', None)
                dzone = getattr(dest, 'zone', None)
                if dx is not None and dy is not None and dz is not None:
                    y_adj = (y_adj_map or {}).get(entry_loc_key, 0.0)
                    zone_str = str(dzone) if dzone is not None else "0"
                    coords_suffix = f" {dx:.6f} {dy + y_adj:.6f} {dz:.6f} {zone_str}"

        new_line = f'    ${directive} "{condition}"{coords_suffix}'
        target = by_num.get(target_num)
        if target is not None:
            target["lines"].append(new_line)
        else:
            print(f"  [levels_txt] WARNING: no block for level {target_num} ({directive})")

    # Group same-directive lines together in each block so the game produces
    # one combined "Find X <item>" entry rather than separate runs.
    # Applies to any directive that can appear multiple times per block.
    _group_res = [
        re.compile(r'^\s*\$darksoul\b',    re.IGNORECASE),
        re.compile(r'^\s*\$retractor\b',   re.IGNORECASE),
        re.compile(r'^\s*\$accumulator\b', re.IGNORECASE),
    ]
    for b in blocks:
        lines = b["lines"]
        for pat in _group_res:
            matched = [l for l in lines if     pat.search(l)]
            rest    = [l for l in lines if not pat.search(l)]
            lines   = matched + rest
        b["lines"] = lines

    cg_updated = _patch_coffingate_lines(blocks, gate_remap)

    # ── Cadeaux counts ────────────────────────────────────────────────────────
    # Count how many cadeaux end up in each level after placement and update
    # the $cadeaux N lines accordingly.
    #
    # A placed item is a cadeaux if its source RawLocation has category="cadeaux".
    # This covers:
    #   • explicit cadeaux RSC types (RSC_CADEAUX, RSC_X_CADEAUX, RSC_PICKUP_CADEAUX)
    #   • persistent barrel cadeaux — both save_idx != 0 AND save_idx == 0 variants
    #     (the engine assigns save slots for the latter at runtime; track_type=0x0002
    #      is the reliable signal, and it is already reflected in the CSV category)
    # Using category directly avoids duplicating the has_drop logic here and
    # correctly handles cross-category placement (e.g. a soul in a cadeaux slot
    # has category="soul" and is NOT counted, which is correct).
    cadeaux_per_level: dict[int, int] = {}
    for loc_key, source_loc in progression_placement.items():
        dest = LOCATION_TABLE.get(loc_key)
        if dest is None:
            continue
        level_num = LEVEL_TO_NUM.get(dest.level_id)
        if level_num is None:
            continue
        if getattr(source_loc, "category", None) == "cadeaux":
            cadeaux_per_level[level_num] = cadeaux_per_level.get(level_num, 0) + 1

    # Unverified cadeaux locations (is_verified=False) are excluded from
    # CHECKABLE_LOCS so fill never touches them — they stay vanilla in the
    # game world.  When we overwrite a level's $cadeaux N line for any fill-
    # placed cadeaux in that level, we must still count these untouched slots
    # so the total isn't short.
    from fill import UNVERIFIED_LOCS, AP_LOCATIONS
    for loc in AP_LOCATIONS:
        if loc.loc_key not in UNVERIFIED_LOCS:
            continue
        if loc.category != "cadeaux":
            continue
        level_num = LEVEL_TO_NUM.get(loc.level_id)
        if level_num is None:
            continue
        # Only add to levels that fill touched (have an entry in cadeaux_per_level).
        # Levels with no fill-placed cadeaux keep their vanilla $cadeaux line as-is,
        # so we don't need to adjust them here.
        if level_num in cadeaux_per_level:
            cadeaux_per_level[level_num] += 1

    _cadeaux_line_re = re.compile(r'^\s*\$cadeaux\s+\d+', re.IGNORECASE)

    # Snapshot vanilla counts BEFORE mutating any block lines.
    # The summary loop below must read from this dict — not from b["lines"] —
    # because the mutation loop overwrites the $cadeaux lines in-place.
    vanilla_per_level: dict[int, int] = {}
    for b in blocks:
        for line in b["lines"]:
            if _cadeaux_line_re.match(line):
                vanilla_per_level[b["num"]] = int(re.search(r'\d+', line).group())
                break

    cadeaux_updated = 0
    for b in blocks:
        new_count = cadeaux_per_level.get(b["num"])
        if new_count is None:
            continue
        new_lines = []
        replaced = False
        for line in b["lines"]:
            if _cadeaux_line_re.match(line) and not replaced:
                new_lines.append(f'    $cadeaux {new_count}')
                replaced = True
                cadeaux_updated += 1
            else:
                new_lines.append(line)
        b["lines"] = new_lines

    missing_cadeaux = 0
    for loc_key, source_loc in progression_placement.items():
        if getattr(source_loc, "category", None) != "cadeaux":
            continue
        dest = LOCATION_TABLE.get(loc_key)
        if dest is None:
            missing_cadeaux += 1
            print(f"  [levels_txt] cadeaux with no LOCATION_TABLE entry: {loc_key}")
    print(f"  [levels_txt] cadeaux missing from count: {missing_cadeaux}")

    vanilla_total = 0
    patched_total = 0
    for b in blocks:
        vanilla_count = vanilla_per_level.get(b["num"], 0)
        vanilla_total += vanilla_count
        new_count = cadeaux_per_level.get(b["num"], vanilla_count)
        patched_total += new_count
        if new_count != vanilla_count:
            print(f"  [levels_txt] level {b['num']}: cadeaux {vanilla_count} → {new_count}")

    print(f"  [levels_txt] cadeaux total: vanilla={vanilla_total} patched={patched_total}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(_serialize(blocks), encoding="utf-8")
    print(f"  [levels_txt] Patched → {output_path.name}  "
          f"({len(directive_entries)} directive(s) updated, "
          f"{cg_updated} coffingate SL(s) updated, "
          f"cadeaux counts: vanilla/stubbed)")
