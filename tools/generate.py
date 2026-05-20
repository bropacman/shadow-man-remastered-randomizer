"""
tools/generate.py
─────────────────
Converts data/locations.csv → extracted_locations.py

Run whenever locations.csv changes:
    python tools/generate.py

Never edit extracted_locations.py by hand.
"""

import csv
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from constants import SOUL_RSC_FILES as VALID_SOURCE_FILES

# ── Paths ────────────────────────────────────────────────────────────────────

ROOT     = Path(__file__).parent.parent
CSV_PATH = ROOT / "data" / "locations.csv"
OUT_PATH = ROOT / "extracted_locations.py"

# ── Constants ─────────────────────────────────────────────────────────────────

REQUIRED_COLUMNS = {
    "level_id", "source_file", "friendly_name", "object", "offset", "category",
    "level_region", "sub_region",
    "instance_id", "is_tracked", "is_verified", "zone", "x", "y", "z", "notes",
}

VALID_CATEGORIES = {
    "barrel", "cadeaux", "soul", "weapon", "actor","other","retractor","accumulator",
    "progression", "lore", "bonus","enemy","true_form","boss","gad","eclipser"
}

# sub_region sentinel meaning "no gate — freely accessible in the level"
NO_GATE_SENTINEL = "N"

# ── Gate expression parser ────────────────────────────────────────────────────
#
# Parses sub_region strings like:
#   GATE_DEADSIDE_ASYLUM
#   GATE_DEADSIDE_PATH_6 | (GAD2_WALK & GATE_DEADSIDE_ASYLUM & BATON)
#   FLAMBEAU & BATON
#   GAD2_WALK & (SL6 | (SL2 & BATON))
#
# GATE_* tokens emit R.gate("GATE_X", state, player)
# All other tokens emit R.<token_lower>(state, player)
#
# Produces a Python expression string for eval() in fill.py's _reachable():
#   R.gate("GATE_DEADSIDE_ASYLUM", state, player)
#   R.gate("GATE_DEADSIDE_PATH_6", state, player) or (R.gad2_walk(state, player) and R.gate("GATE_DEADSIDE_ASYLUM", state, player) and R.baton(state, player))
#   R.flambeau(state, player) and R.baton(state, player)

_TOKEN_RE = re.compile(r'\(|\)|[A-Z0-9_]+|[&|]')


def _tokenize(expr: str) -> list[str]:
    return _TOKEN_RE.findall(expr.upper())


def _parse_expr(tokens: list[str], pos: int) -> tuple[str, int]:
    """Recursive descent parser. Returns (python_expr_str, new_pos)."""
    lhs, pos = _parse_atom(tokens, pos)

    while pos < len(tokens) and tokens[pos] in ("&", "|"):
        op = tokens[pos]
        pos += 1
        rhs, pos = _parse_atom(tokens, pos)
        py_op = "and" if op == "&" else "or"
        lhs = f"{lhs} {py_op} {rhs}"

    return lhs, pos


def _parse_atom(tokens: list[str], pos: int) -> tuple[str, int]:
    if pos >= len(tokens):
        raise ValueError(f"Unexpected end of expression at position {pos}")

    tok = tokens[pos]

    if tok == "(":
        pos += 1  # consume '('
        inner, pos = _parse_expr(tokens, pos)
        if pos >= len(tokens) or tokens[pos] != ")":
            raise ValueError(f"Expected ')' at position {pos}")
        pos += 1  # consume ')'
        return f"({inner})", pos

    if tok not in (")", "&", "|"):
        # GATE_* tokens use the parametric R.gate() call
        if tok.startswith("GATE_"):
            return f'R.gate("{tok}", state, player)', pos + 1
        # All other tokens map to a named R method
        py_name = tok.lower()
        return f"R.{py_name}(state, player)", pos + 1

    raise ValueError(f"Unexpected token '{tok}' at position {pos}")


def parse_gate_expr(raw: str) -> str | None:
    """
    Parse a sub_region gate expression into a Python boolean expression string.

    Returns None if the expression is empty or the no-gate sentinel.
    Raises ValueError on malformed input.
    """
    s = raw.strip()
    if not s or s.upper() == NO_GATE_SENTINEL:
        return None

    tokens = _tokenize(s)
    if not tokens:
        return None

    result, pos = _parse_expr(tokens, 0)
    if pos != len(tokens):
        raise ValueError(
            f"Unexpected trailing token '{tokens[pos]}' in expression '{raw}'"
        )
    return result


def collect_gate_tokens(raw: str) -> set[str]:
    """Return all identifier tokens (lowercase) in a gate expression."""
    s = raw.strip()
    if not s or s.upper() == NO_GATE_SENTINEL:
        return set()
    return {tok.lower() for tok in _tokenize(s) if tok not in ("(", ")", "&", "|")}


# ── Validation ────────────────────────────────────────────────────────────────

def parse_offset(raw: str) -> int:
    raw = raw.strip()
    if raw.startswith(("0x", "0X")):
        return int(raw, 16)
    return int(raw)


def validate(rows: list[dict]) -> list[str]:
    errors = []
    seen_keys: set[str] = set()

    for i, row in enumerate(rows, start=2):
        level_id    = row["level_id"].strip()
        source_file = row["source_file"].strip()
        offset_raw  = row["offset"].strip()
        sub_region  = row["sub_region"].strip()
        category    = row["category"].strip()

        # Offset
        try:
            offset = parse_offset(offset_raw)
        except ValueError:
            errors.append(f"Row {i}: invalid offset '{offset_raw}'")
            continue

        # Unique loc_key
        loc_key = f"{level_id}:{source_file}:0x{offset:04X}"
        if loc_key in seen_keys:
            errors.append(f"Row {i}: duplicate loc_key '{loc_key}'")
        seen_keys.add(loc_key)

        # Category
        if category and category not in VALID_CATEGORIES:
            errors.append(
                f"Row {i} ({loc_key}): unknown category '{category}' "
                f"— expected one of {sorted(VALID_CATEGORIES)}"
            )

        # Source file
        if source_file not in VALID_SOURCE_FILES:
            errors.append(
                f"Row {i} ({loc_key}): unknown source_file '{source_file}' "
                f"— expected one of {sorted(VALID_SOURCE_FILES)}"
            )

        # Gate expression
        if sub_region and sub_region.upper() != NO_GATE_SENTINEL:
            try:
                parse_gate_expr(sub_region)
            except ValueError as e:
                errors.append(f"Row {i} ({loc_key}): bad gate expression '{sub_region}': {e}")

    return errors


# ── Code generation ───────────────────────────────────────────────────────────

HEADER = '''\
# extracted_locations.py
# AUTO-GENERATED by tools/generate.py — DO NOT EDIT MANUALLY.
# Source of truth: data/locations.csv
# Re-generate:     python tools/generate.py

from __future__ import annotations
from typing import NamedTuple, Optional


class RawLocation(NamedTuple):
    """
    One randomisable slot in Shadow Man Remastered.

    Patcher fields  — used by the patcher to place items:
        level_id, source_file, offset

    Display fields  — human-readable info:
        friendly_name, object, category

    Logic fields    — used by regions.py / access_rules.py:
        level_region  : top-level logical region (shared unlock condition)
        gate_expr     : Python boolean expression string for sub-region access,
                        or None if freely accessible within the level.
                        Uses R.gate("GATE_X", state, player) for GATE_* tokens
                        and R.<rule>(state, player) for all other tokens.
                        e.g. \'R.gate("GATE_DEADSIDE_PATH_6", state, player) or
                             (R.gad2_walk(state, player) and R.baton(state, player))\'
        gate_raw      : Original CSV value, for debugging

    Tracking fields — for human navigation/debugging only, ignored by logic:
        instance_id, is_tracked, is_verified, zone, x, y, z, notes

    loc_key is derived at runtime as f"{level_id}:{source_file}:0x{offset:04X}"
    and is never stored in the CSV to avoid drift.
    """
    # ── patcher ──────────────────────────────────────────────────────────────
    level_id:      str
    source_file:   str
    offset:        int

    # ── display ──────────────────────────────────────────────────────────────
    friendly_name: Optional[str]
    object:        str
    category:      str

    # ── logic ────────────────────────────────────────────────────────────────
    level_region:  str
    gate_expr:     Optional[str]   # None = freely accessible within the level
    gate_raw:      Optional[str]   # Original CSV value, for debugging

    # ── tracking / debug metadata ────────────────────────────────────────────
    instance_id:   Optional[int]
    is_tracked:    Optional[bool]
    is_verified:   Optional[bool]
    zone:          Optional[str]
    x:             Optional[float]
    y:             Optional[float]
    z:             Optional[float]
    notes:         Optional[str]

    @property
    def loc_key(self) -> str:
        """Unique patcher key — never stored in CSV, always derived."""
        return f"{self.level_id}:{self.source_file}:0x{self.offset:04X}"


RAW_LOCATIONS: list[RawLocation] = [
'''

FOOTER = '''\
]

# ── Derived lookups (built once at import time) ───────────────────────────────

# loc_key → RawLocation
LOCATION_TABLE: dict[str, RawLocation] = {loc.loc_key: loc for loc in RAW_LOCATIONS}

# All unique level_regions
LEVEL_REGIONS: set[str] = {loc.level_region for loc in RAW_LOCATIONS}

# level_region → list of locations with no sub-gate (freely accessible once level unlocked)
FREE_LOCATIONS: dict[str, list[RawLocation]] = {}
for _loc in RAW_LOCATIONS:
    if _loc.gate_expr is None:
        FREE_LOCATIONS.setdefault(_loc.level_region, []).append(_loc)

# level_region → gate_raw → list of gated locations
GATED_LOCATIONS: dict[str, dict[str, list[RawLocation]]] = {}
for _loc in RAW_LOCATIONS:
    if _loc.gate_expr is not None:
        _by_gate = GATED_LOCATIONS.setdefault(_loc.level_region, {})
        _by_gate.setdefault(_loc.gate_raw, []).append(_loc)

# level_region → set of unique gate expressions (useful for regions.py scaffolding)
GATES_BY_REGION: dict[str, set[str]] = {}
for _loc in RAW_LOCATIONS:
    if _loc.gate_raw:
        GATES_BY_REGION.setdefault(_loc.level_region, set()).add(_loc.gate_raw)

# All unique rule tokens across all gates (useful for access_rules.py scaffolding)
ALL_RULE_TOKENS: set[str] = set()
for _loc in RAW_LOCATIONS:
    if _loc.gate_raw:
        for _tok in _loc.gate_raw.upper().replace("(", "").replace(")", "").split():
            if _tok not in ("&", "|"):
                ALL_RULE_TOKENS.add(_tok.lower())
'''


def _q(val: str) -> str:
    """Quote a string, escaping backslashes and double-quotes."""
    return '"' + val.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _opt_str(val: str) -> str:
    v = val.strip()
    return "None" if not v else _q(v)


def _opt_numeric(val: str, cast) -> str:
    v = val.strip()
    if not v:
        return "None"
    try:
        return str(cast(v))
    except ValueError:
        return "None"


def _opt_bool(val: str) -> str:
    v = val.strip().upper()
    if v in ("TRUE", "1", "YES"):
        return "True"
    if v in ("FALSE", "0", "NO"):
        return "False"
    return "None"


def generate(rows: list[dict]) -> str:
    lines = [HEADER]

    for row in rows:
        level_id      = row["level_id"].strip()
        source_file   = row["source_file"].strip()
        offset        = parse_offset(row["offset"].strip())
        friendly_name = row["friendly_name"].strip()
        obj           = row["object"].strip()
        category      = row["category"].strip()
        level_region  = row["level_region"].strip()
        sub_region    = row["sub_region"].strip()

        gate_expr = parse_gate_expr(sub_region)
        gate_raw  = sub_region if gate_expr is not None else None

        gate_expr_str = "None" if gate_expr is None else _q(gate_expr)
        gate_raw_str  = "None" if gate_raw  is None else _q(gate_raw)
        fname_str     = "None" if not friendly_name else _q(friendly_name)

        instance_str  = _opt_numeric(row["instance_id"], int)
        tracked_str   = _opt_bool(row["is_tracked"])
        verified_str  = _opt_bool(row["is_verified"])
        zone_str      = _opt_str(row["zone"])
        x_str         = _opt_numeric(row["x"], float)
        y_str         = _opt_numeric(row["y"], float)
        z_str         = _opt_numeric(row["z"], float)
        notes_str     = _opt_str(row["notes"])

        lines.append(
            f"    RawLocation("
            f"{_q(level_id)}, {_q(source_file)}, 0x{offset:04X}, "
            f"{fname_str}, {_q(obj)}, {_q(category)}, "
            f"{_q(level_region)}, {gate_expr_str}, {gate_raw_str}, "
            f"{instance_str}, {tracked_str}, {verified_str}, {zone_str}, "
            f"{x_str}, {y_str}, {z_str}, "
            f"{notes_str}),\n"
        )

    lines.append(FOOTER)
    return "".join(lines)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    if not CSV_PATH.exists():
        print(f"ERROR: CSV not found at {CSV_PATH}", file=sys.stderr)
        sys.exit(1)

    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    missing_cols = REQUIRED_COLUMNS - set(reader.fieldnames or [])
    if missing_cols:
        print(f"ERROR: CSV is missing columns: {sorted(missing_cols)}", file=sys.stderr)
        sys.exit(1)

    errors = validate(rows)
    if errors:
        print(f"Found {len(errors)} validation error(s):", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        sys.exit(1)

    output = generate(rows)
    OUT_PATH.write_text(output, encoding="utf-8")
    print(f"✓ Generated {len(rows)} locations → {OUT_PATH.name}")

    # ── Summary ───────────────────────────────────────────────────────────────
    from collections import Counter

    print("\nLocations per level_region:")
    by_region = Counter(row["level_region"] for row in rows)
    for region, count in sorted(by_region.items()):
        print(f"  {count:4d}  {region}")

    all_gates: set[str] = set()
    for row in rows:
        sr = row["sub_region"].strip()
        if sr and sr.upper() != NO_GATE_SENTINEL:
            all_gates.add(sr)

    all_tokens: set[str] = set()
    for gate in all_gates:
        all_tokens |= collect_gate_tokens(gate)

    print(f"\nUnique gate expressions: {len(all_gates)}")
    print(f"\nUnique rule tokens (→ need R.<name> in access_rules.py):")
    for tok in sorted(all_tokens):
        print(f"  {tok}")


# ── Quick self-test ───────────────────────────────────────────────────────────

def _test_parser():
    cases = [
        (
            "GATE_DEADSIDE_ASYLUM",
            'R.gate("GATE_DEADSIDE_ASYLUM", state, player)',
        ),
        (
            "GATE_DEADSIDE_PATH_6 | (GAD2_WALK & GATE_DEADSIDE_ASYLUM & BATON)",
            'R.gate("GATE_DEADSIDE_PATH_6", state, player) or (R.gad2_walk(state, player) and R.gate("GATE_DEADSIDE_ASYLUM", state, player) and R.baton(state, player))',
        ),
        (
            "FLAMBEAU & BATON",
            "R.flambeau(state, player) and R.baton(state, player)",
        ),
        (
            "GATE_DEADSIDE_PLAYROOMS | (GAD2_WALK & GATE_DEADSIDE_ASYLUM & BATON)",
            'R.gate("GATE_DEADSIDE_PLAYROOMS", state, player) or (R.gad2_walk(state, player) and R.gate("GATE_DEADSIDE_ASYLUM", state, player) and R.baton(state, player))',
        ),
        (
            "GATE_DEADSIDE_MYSTERY & POIGNE",
            'R.gate("GATE_DEADSIDE_MYSTERY", state, player) and R.poigne(state, player)',
        ),
        (
            "N",
            None,
        ),
    ]
    all_ok = True
    for raw, expected in cases:
        result = parse_gate_expr(raw)
        ok = result == expected
        if not ok:
            print(f"  FAIL: {raw!r}")
            print(f"    expected: {expected!r}")
            print(f"    got:      {result!r}")
            all_ok = False
    if all_ok:
        print(f"  All {len(cases)} parser tests passed ✓")
    return all_ok


if __name__ == "__main__":
    import sys

    if "--test" in sys.argv:
        print("Running parser self-tests...")
        ok = _test_parser()
        sys.exit(0 if ok else 1)
    main()