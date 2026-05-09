"""
loc_english_patcher.py
======================
Patches localization/loc_english.txt inside the game KPF so the map tracker
hint panel shows correct (non-misleading) labels for randomized items.

Handles two independent modes (both keyed off patch_tracker=True):

  GAD collapse (always on when shuffle_gad_temples=True)
  -------------------------------------------------------
  All three GAD power keys -> "Gad Power".  After item randomization the
  levels.txt directive (touchgad/walkgad/swimgad) is placed at the destination
  level based on which item was placed there, but the hint text comes from the
  m_obj_* key matching the DIRECTIVE name -- not necessarily the power actually
  in that spot.  Collapsing all three keeps the hint accurate regardless of
  which power ended up where.

  Obscure hints (opt-in, obscure_hints=True in config)
  -----------------------------------------------------
  CURRENTLY DEACTIVATED — see _obscure_overrides() for design notes.

  The intent is to replace every tracked item's label with a tier-based cryptic
  phrase (progression / weapon / lore / darksoul) instead of the item name.
  Items with vanilla m_obj_* keys can be overridden directly.  Items without
  vanilla keys (weapons, key items, Eclipser pieces) fall through to the game's
  m_obj_findthe / i_* inventory lookup — obscuring those requires overriding
  the i_* inventory name, which also affects the inventory screen.  Until a
  cleaner solution is found the feature is stubbed out.
"""

from __future__ import annotations

import re
from pathlib import Path
from constants import GAD_LABEL, HINT_TIERS, DIRECTIVE_HINT_TIER

# -- GAD collapse -------------------------------------------------------------

#: m_obj_* keys that control the GAD hint-panel text.
GAD_KEYS: frozenset = frozenset({
    "m_obj_touchgad",
    "m_obj_walkgad",
    "m_obj_swimgad",
})


def _obscure_overrides():
    """
    (Currently unused — obscure hints mode is deactivated.)

    Design notes for when this is revisited:
      - Items WITH vanilla m_obj_* keys (dark souls, gad, retractors, poigne,
        violator) can be overridden directly; those work cleanly.
      - Items WITHOUT vanilla m_obj_* keys (weapons, key items, Eclipser pieces)
        have their tracker hint sourced from the i_* inventory key via the
        m_obj_findthe "{}" substitution.  Overriding m_obj_findthe with a
        static string does NOT work — the game reads the i_* key directly.
        Overriding i_* does work but also changes the inventory display name.
      - Until a non-invasive path is found, calling this function applies only
        the m_obj_* overrides (partial obscuring, items without vanilla keys
        still show real names).
    """
    return {
        f"m_obj_{directive}": HINT_TIERS[tier]
        for directive, tier in DIRECTIVE_HINT_TIER.items()
    }


# -- Parser / writer ----------------------------------------------------------

# Matches:  [leading_ws] key [ws=ws] "value" [trailing]
# Captures: (leading_ws, key, ws=ws, value, trailing)
_LINE_RE = re.compile(r'^(\s*)(\S+)(\s*=\s*)"([^"]*)"(.*)', re.ASCII)

# Column to align the '=' sign for injected lines (matches the m_obj_* block style)
_INJECT_KEY_WIDTH = 24


# -- Public API ---------------------------------------------------------------

def patch_loc_english(source_path, output_path, overrides, inject_missing=False):
    """
    Read source_path, apply overrides (key -> new value), write output_path.

    Lines that don't match any override key are copied verbatim (including
    comments, blank lines, and column-alignment spacing).

    If inject_missing=True, any override keys that were NOT found in the file
    are injected as new lines immediately after the last existing m_obj_* key.
    This handles items that lack a dedicated m_obj_* entry and would otherwise
    fall back to the game's m_obj_findthe / inventory-name mechanism.

    Returns (replaced, injected) counts.
    """
    text = Path(source_path).read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines(keepends=True)

    matched = set()
    replaced = 0
    out_lines = []
    last_mobj_idx = -1

    for i, line in enumerate(lines):
        m = _LINE_RE.match(line)
        if m:
            key = m.group(2)
            if key in overrides:
                leading, eq_part, trailing = m.group(1), m.group(3), m.group(5)
                eol = ""
                for ch in reversed(line):
                    if ch in "\r\n":
                        eol = ch + eol
                    else:
                        break
                line = f'{leading}{key}{eq_part}"{overrides[key]}"{trailing}{eol or chr(10)}'
                replaced += 1
                matched.add(key)
            # Track injection point: last m_obj_* line that is NOT the
            # findthe/find sentinel — injecting after those may be ignored
            # by the game if it stops reading at the sentinel lines.
            if m.group(2).startswith("m_obj_") and \
                    m.group(2) not in ("m_obj_findthe", "m_obj_find"):
                last_mobj_idx = len(out_lines)
        out_lines.append(line)

    # Inject keys that were not found in the file
    injected = 0
    if inject_missing:
        missing = {k: v for k, v in overrides.items() if k not in matched}
        if missing:
            inject_lines = [
                f"{key:<{_INJECT_KEY_WIDTH}} = \"{value}\"\n"
                for key, value in sorted(missing.items())
            ]
            insert_at = last_mobj_idx + 1 if last_mobj_idx >= 0 else len(out_lines)
            out_lines = out_lines[:insert_at] + inject_lines + out_lines[insert_at:]
            injected = len(inject_lines)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text("".join(out_lines), encoding="utf-8")
    return replaced, injected


def patch_loc_english_for_tracker(
    source_path,
    output_path,
    *,
    shuffle_gad_temples=False,
    obscure_hints=False,
):
    """
    High-level entry point called from patcher.py step 4d.

    Builds the override dict from active config flags and delegates to
    patch_loc_english.  Override priority (highest last, wins):

      1. GAD collapse  (shuffle_gad_temples)
      2. Obscure hints (obscure_hints) -- CURRENTLY DEACTIVATED (no-op)

    If neither flag produces any overrides the file is still written as an
    identical copy so the repack step can always include it.
    """
    overrides = {}

    # 1. GAD collapse -- prevent misleading specific-power labels
    if shuffle_gad_temples:
        for key in GAD_KEYS:
            overrides[key] = GAD_LABEL

    # 2. Obscure hints -- DEACTIVATED
    #    Obscuring items without vanilla m_obj_* keys requires overriding i_*
    #    inventory names, which also changes the inventory screen display.
    #    Re-enable once a cleaner solution is found.
    #
    # if obscure_hints:
    #     overrides.update(_obscure_overrides())

    if not overrides:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(Path(source_path).read_bytes())
        print("  [loc_english] No overrides needed -- copied verbatim")
        return

    replaced, injected = patch_loc_english(
        source_path, output_path, overrides,
        inject_missing=False,
    )

    mode = []
    if shuffle_gad_temples:
        mode.append("GAD collapse")

    parts = [f"{replaced} replaced"]
    if injected:
        parts.append(f"{injected} injected")
    print(f"  [loc_english] Patched -> {Path(output_path).name}  "
          f"({', '.join(parts)} key(s), mode: {', '.join(mode) or 'none'})")
