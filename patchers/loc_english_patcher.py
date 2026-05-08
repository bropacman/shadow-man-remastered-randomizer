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
  Replaces every tracked item's label with a tier-based cryptic phrase instead
  of the item name, similar to Ocarina of Time randomizer's "Way of the Hero"
  / "Foolish Choice" system:

    * Progression / key items  ->  HINT_GOOD  ("Path of Prophecy")
    * Optional weapons         ->  HINT_BAD   ("Lost in the Shadows")

  Items that already have m_obj_* keys in the file get their values replaced
  in-place.  Items whose m_obj_* keys are absent (they presumably fall back to
  the m_obj_findthe / i_* inventory-name mechanism) get new keys injected right
  after the existing m_obj_* block, so the engine finds them first.

  Obscure hints take full precedence and override the GAD collapse, so players
  can't even infer which power is in a temple from the label.
"""

from __future__ import annotations

import re
from pathlib import Path

# -- GAD collapse -------------------------------------------------------------

#: Generic label used for all three GAD powers when tracker patching is active.
GAD_LABEL = "Gad Power"

#: m_obj_* keys that control the GAD hint-panel text.
GAD_KEYS: frozenset = frozenset({
    "m_obj_touchgad",
    "m_obj_walkgad",
    "m_obj_swimgad",
})

# -- Obscure hint tiers -------------------------------------------------------
#
# To add a new tier: add an entry to HINT_TIERS, then reference it by key in
# _DIRECTIVE_TIER below.  The phrase is what the in-game tracker will display.
#
HINT_TIERS: dict[str, str] = {
    "progression":      "Become the Lord of Deadside",
    "lore":             "Ancient Texts",
    "weapon":           "Weapon of the Damned",
    # Planned / not yet active -- uncomment and wire up when ready:
    # "liveside_weapon":  "...",
    # "deadside_weapon":  "...",
    # "govi":             "...",
    # "darksoul":         "...",
}

# Convenience aliases used in _DIRECTIVE_TIER below.
_T = HINT_TIERS

# Directive name -> hint tier key -> resolved phrase.
# Directive names mirror levels_txt_patcher.RSC_TO_DIRECTIVE / GAD_DIRECTIVE.
_DIRECTIVE_TIER: dict[str, str] = {
    # -- Voodoo / key items ---------------------------------------------------
    "poigne":        _T["progression"],
    "flambeau":      _T["progression"],
    "marteau":       _T["progression"],
    "calabash":      _T["progression"],
    "baton":         _T["progression"],
    "asson":         _T["progression"],
    "enseigne":      _T["progression"],
    "engineerskey":  _T["progression"],
    "keycard":       _T["progression"],
    "flashlight":    _T["progression"],
    # -- Lore -----------------------------------------------------------------
    "bookofshadows": _T["lore"],
    "prophecy":      _T["lore"],
    "journal":       _T["lore"],
    # -- L'Eclipser pieces ----------------------------------------------------
    "lalune":        _T["progression"],
    "lesoleil":      _T["progression"],
    "lalame":        _T["progression"],
    # -- GAD powers -----------------------------------------------------------
    "touchgad":      _T["progression"],
    "walkgad":       _T["progression"],
    "swimgad":       _T["progression"],
    # -- Other progression items ----------------------------------------------
    "darksoul":      _T["progression"],   # TODO: split to own tier when govi/soul tracking lands
    "lightsoul":     _T["progression"],
    "accumulator":   _T["progression"],
    "retractor":     _T["progression"],
    # -- Weapons --------------------------------------------------------------
    "violator":      _T["weapon"],
    "tetedemort":    _T["weapon"],
    "mac10":         _T["weapon"],
    "mp909":         _T["weapon"],
    "shotgun":       _T["weapon"],
    "sawedshotgun":  _T["weapon"],
}


def _obscure_overrides():
    """Return the full m_obj_* -> hint-phrase override dict for all directives."""
    return {f"m_obj_{d}": phrase for d, phrase in _DIRECTIVE_TIER.items()}


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
            # Track the last m_obj_* line for injection point
            if m.group(2).startswith("m_obj_"):
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
      2. Obscure hints (obscure_hints) -- overrides GAD collapse entirely

    When obscure_hints=True, inject_missing=True is passed so that items
    without existing m_obj_* keys get new entries injected into the file.

    If neither flag produces any overrides the file is still written as an
    identical copy so the repack step can always include it.
    """
    overrides = {}

    # 1. GAD collapse -- prevent misleading specific-power labels
    if shuffle_gad_temples:
        for key in GAD_KEYS:
            overrides[key] = GAD_LABEL

    # 2. Obscure hints -- replace/inject all item labels with tier-based phrases
    #    (takes full precedence, including over the GAD collapse above)
    if obscure_hints:
        overrides.update(_obscure_overrides())
        # Strip the "Find the {}" / "Find {}" wrapper so the tier phrase stands
        # alone.  Without this the tracker renders "Find the Path of Prophecy".
        overrides["m_obj_findthe"] = "{}"
        overrides["m_obj_find"]    = "{}"

    if not overrides:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(Path(source_path).read_bytes())
        print("  [loc_english] No overrides needed -- copied verbatim")
        return

    replaced, injected = patch_loc_english(
        source_path, output_path, overrides,
        inject_missing=obscure_hints,   # only inject when doing full obscure pass
    )

    mode = []
    if obscure_hints:
        mode.append("obscure hints")
    elif shuffle_gad_temples:
        mode.append("GAD collapse")

    parts = [f"{replaced} replaced"]
    if injected:
        parts.append(f"{injected} injected")
    print(f"  [loc_english] Patched -> {Path(output_path).name}  "
          f"({', '.join(parts)} key(s), mode: {', '.join(mode) or 'none'})")
