"""
Shadow Man Remastered — Archipelago Companion (pywebview GUI)

Two tabs, one window:
  1. "Generate YAML"   — builds a player .yaml for Archipelago's generator
                          (worlds/shadowman/options.py), no game install needed.
  2. "Apply AP Seed"   — wraps apply_ap_seed.py: takes the .apshadowman file
                          produced by AP generation and patches your local
                          Shadow Man Remastered install.

Deliberately a separate tool from gui.py (the standalone single-player
randomizer GUI) rather than a third tab bolted onto it — the two audiences
barely overlap (someone hosting/joining a multiworld doesn't need the
standalone patcher's seed-rolling UI, and vice versa) but they share the
same visual language and several _Api patterns (browse dialogs, subprocess
streaming to a terminal div), copied/trimmed from gui.py rather than
imported, same tradeoff already accepted for ap_patcher.py / save_path_patch.py
(see those modules' docstrings) — this file is small enough that the
duplication cost is low.

Tab 1's field structure (labels, per-field 🎲 rng buttons, enemy-row/hint
pattern, disabled-until-unlocked sub-options) is deliberately copied 1:1 from
gui.py wherever an AP option has a direct standalone analog, right down to
wording, so switching between this tool and gui.py doesn't feel like two
different products. Where AP's options.py doesn't have an equivalent yet
(Starting Item, standalone's graded insanity tiers — see AP_FEATURE_GAP.md
Tier 3), the control is shown greyed out with a tooltip instead of removed,
so the gap is visible rather than silently missing. The 🎲 buttons are real
here, not cosmetic: AP's YAML
format accepts the literal string "random" for any Toggle/Choice/Range
option (see AP_FEATURE_GAP.md section F), so toggling one emits `random` for
that key instead of the widget's current value — AP rolls it at generate
time, same as gui.py's dice buttons roll it at patch time.

Usage (dev):    python ap_gui.py
Usage (built):  shadow_man_ap_companion.exe
"""

import sys

# ── Frozen apply-seed-subprocess mode ────────────────────────────────────────
# Mirrors gui.py's _PATCHER_FLAG trick: when bundled with PyInstaller, this
# same exe re-invokes itself as a plain apply_ap_seed.py CLI process so the
# GUI can subprocess.Popen() it without needing a second bundled exe.
_APPLY_FLAG = "--_run-apply-seed"

if _APPLY_FLAG in sys.argv:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)

    from pathlib import Path
    import runpy

    _base = (
        Path(sys._MEIPASS)
        if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent
    )
    sys.path.insert(0, str(_base))
    sys.argv = ["apply_ap_seed.py"] + [a for a in sys.argv[1:] if a != _APPLY_FLAG]

    try:
        runpy.run_path(str(_base / "apply_ap_seed.py"), run_name="__main__")
    except SystemExit:
        pass
    sys.exit(0)

# ── Normal GUI mode ───────────────────────────────────────────────────────────

import json
import os
import subprocess
import threading
from pathlib import Path

import webview
import yaml

if getattr(sys, 'frozen', False):
    bundle_dir = sys._MEIPASS
    if bundle_dir not in sys.path:
        sys.path.append(bundle_dir)

SCRIPT_DIR   = Path(__file__).resolve().parent
APPLY_SEED   = SCRIPT_DIR / "apply_ap_seed.py"
DEFAULT_GAME_DIR = SCRIPT_DIR.parent
PREFS_FILE   = SCRIPT_DIR / "gui_prefs.json"   # shared with gui.py — same game install either way

# Archipelago checkout used to launch worlds/shadowman/client.py (2026-08-04,
# see CLAUDE.md's "streamlining apply -> launch -> connect" writeup) — a
# SEPARATE path from DEFAULT_GAME_DIR above (that one's the actual Shadow Man
# Remastered install; this is the Python/Archipelago source checkout this
# world's client.py lives in). No UI field for this yet — overridable via a
# plain "ap_dir" key in gui_prefs.json if this ever needs to point somewhere
# else, same storage _load_prefs()/_save_prefs() already use for game_dir.
DEFAULT_AP_DIR = Path(r"C:\Users\jonat\Documents\Archipelago-0.6.7")

# Separate, independent version track from the standalone (gui.py's HTML
# header) -- the two ship on different schedules to different audiences,
# see RELEASING.md. Starting over at 0.x rather than continuing the old
# 1.2.x numbering: this is the first release that includes the AP
# Companion exe at all, and first real-user testing already caught a
# packaging bug (missing keystone/capstone dependency) -- genuinely beta,
# and 0.x says so honestly. Bump to 1.0.0 once it's held up across a few
# real multiworld games.
COMPANION_VERSION = "v0.1.0"


def _load_prefs() -> dict:
    try:
        return json.load(open(PREFS_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _save_prefs(prefs: dict) -> None:
    try:
        json.dump(prefs, open(PREFS_FILE, "w", encoding="utf-8"), indent=2)
    except Exception:
        pass


def _looks_like_install(path: Path) -> bool:
    try:
        return any(path.glob("*.kpf"))
    except OSError:
        return False


_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  :root {
    --bg: #111113; --surface: #1c1c1f; --border: #2a2a2e;
    --accent: #8b1a1a; --accent2: #2d7d46;
    --text: #d8d8d8; --muted: #666; --dim: #444;
    --green: #4caf50; --red: #e57373; --blue: #5b9bd5;
  }
  body {
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 13px; padding: 18px 22px 14px;
    -webkit-user-select: none; user-select: none; line-height: 1.4;
    overflow-x: hidden;
  }
  .header { margin-bottom: 14px; }
  .header h1 { font-size: 17px; font-weight: 700; color: #fff; letter-spacing: -0.01em; }
  .header p  { color: var(--muted); font-size: 11px; margin-top: 2px; }

  .tabs { display: flex; gap: 6px; margin-bottom: 14px; border-bottom: 1px solid var(--border); }
  .tab-btn {
    background: transparent; border: none; border-bottom: 2px solid transparent;
    color: var(--muted); font-size: 13px; font-weight: 600; padding: 9px 4px;
    cursor: pointer; border-radius: 0; margin-right: 18px;
  }
  .tab-btn:hover:not(:disabled) { color: #ccc; filter: none; }
  .tab-btn.active { color: #fff; border-bottom-color: var(--accent2); }
  .tab-panel { display: none; }
  .tab-panel.active { display: block; }

  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 12px 15px;
  }
  .card-title {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.08em; color: var(--muted); margin-bottom: 10px;
  }
  .card-row { display: flex; gap: 10px; margin-bottom: 10px; align-items: flex-start; }
  .card-row .card { flex: 1; margin-bottom: 0; }

  .row { display: flex; align-items: center; gap: 8px; }
  .row + .row { margin-top: 8px; }

  input[type=text], input[type=number], textarea {
    -webkit-user-select: text; user-select: text;
  }
  input[type=text], input[type=number], select, textarea {
    background: #222226; border: 1px solid var(--border);
    border-radius: 5px; color: var(--text);
    font-size: 12px; padding: 5px 9px; outline: none; transition: border-color .15s;
  }
  input[type=text]:focus, input[type=number]:focus, select:focus, textarea:focus { border-color: var(--accent); }
  input.dir-input   { flex: 1; }
  input.name-input  { width: 220px; }
  select { cursor: pointer; }
  select:disabled { opacity: .35; cursor: default; }

  button {
    border: none; border-radius: 5px; cursor: pointer;
    font-size: 12px; font-weight: 500; padding: 5px 13px;
    transition: filter .12s; white-space: nowrap;
  }
  button:hover:not(:disabled) { filter: brightness(1.15); }
  button:active:not(:disabled) { filter: brightness(.9); }
  button:disabled { opacity: .35; cursor: default; }
  .btn-ghost   { background: #28282c; color: #aaa; border: 1px solid #383840; }
  .btn-run     { background: var(--accent2); color: #fff; font-size: 13px; padding: 7px 22px; }
  .btn-launch  { background: #1a3a5c; color: #7ab3e0; border: 1px solid #2a5080; font-size: 13px; padding: 7px 22px; }
  .rng-btn {
    background: transparent; border: 1px solid var(--dim); border-radius: 3px;
    color: var(--dim); cursor: pointer; font-size: 11px; padding: 1px 5px;
    line-height: 1.4; flex-shrink: 0;
  }
  .rng-btn:hover { border-color: var(--muted); color: var(--muted); }
  .rng-btn.rng-active { border-color: #a78bfa; color: #a78bfa; background: rgba(167,139,250,0.1); }
  .rng-btn:disabled { opacity: .35; cursor: not-allowed; }

  .check-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 10px; }
  .check-label { display: flex; align-items: center; gap: 7px; padding: 4px 0; cursor: pointer; color: #ccc; }
  .check-label:hover { color: #fff; }
  input[type=checkbox] { width: 14px; height: 14px; accent-color: #4a7a5a; cursor: pointer; flex-shrink: 0; }
  input[type=checkbox]:disabled { cursor: not-allowed; }

  /* Features not yet ported to the AP world (see AP_FEATURE_GAP.md Tier 3) --
     shown, greyed out, rather than hidden, so it's clear what's coming. */
  .disabled-stub { opacity: 0.42; cursor: not-allowed; }
  .disabled-stub .tip { cursor: help; opacity: 1; }

  .divider { border: none; border-top: 1px solid var(--border); margin: 10px 0; }

  /* Enemy-mode / ambient-mode style rows: label + dropdown + tip + hint */
  .enemy-row { display: flex; align-items: center; gap: 10px; }
  .enemy-row .lbl { color: var(--muted); font-size: 11px; white-space: nowrap; }
  .enemy-row select { flex: 1; min-width: 0; }

  input[type=range] {
    -webkit-appearance: none; appearance: none;
    width: 110px; height: 4px; border-radius: 2px;
    background: #333; outline: none; cursor: pointer;
  }
  input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none; width: 14px; height: 14px;
    border-radius: 50%; background: var(--accent2); cursor: pointer;
  }
  .slider-val { color: var(--text); font-size: 12px; min-width: 24px; text-align: right; }

  .tip {
    position: relative; display: inline-flex;
    align-items: center; margin-left: 4px; cursor: help;
  }
  .tip-icon {
    display: inline-flex; align-items: center; justify-content: center;
    width: 13px; height: 13px; border-radius: 50%;
    background: #2c2c30; color: #666; font-size: 9px; font-weight: 700;
    border: 1px solid #3a3a3e; flex-shrink: 0; line-height: 1;
  }
  .tip:hover .tip-icon { background: #3a3a3e; color: #aaa; }
  .tip-box {
    position: absolute; bottom: calc(100% + 7px); left: 50%;
    transform: translateX(-40%);
    background: #25252a; border: 1px solid #3a3a40; border-radius: 7px;
    padding: 8px 11px; font-size: 11px; color: #bbb; line-height: 1.5;
    width: 230px; z-index: 999; pointer-events: none;
    opacity: 0; transition: opacity .15s;
    white-space: normal; text-align: left;
    font-weight: normal; text-transform: none; letter-spacing: normal; font-style: normal;
  }
  .tip:hover .tip-box { opacity: 1; }
  .tip.anchor-right .tip-box { left: auto; right: 0; transform: none; }

  .terminal {
    background: #0c0c0e; border: 1px solid var(--border); border-radius: 8px;
    font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
    font-size: 11px; height: 260px; overflow-y: auto;
    padding: 10px 13px; white-space: pre-wrap; word-break: break-all;
    color: #a0a0a8; line-height: 1.55; margin-bottom: 8px;
    -webkit-user-select: text; user-select: text; cursor: text;
  }
  .terminal::-webkit-scrollbar { width: 6px; }
  .terminal::-webkit-scrollbar-track { background: transparent; }
  .terminal::-webkit-scrollbar-thumb { background: #333; border-radius: 3px; }
  .status { font-size: 11px; color: var(--muted); min-height: 16px; }
  .status.ok  { color: var(--green); }
  .status.err { color: var(--red);   }
  .hint { color: var(--dim); font-size: 11px; }
  .actions { display: flex; gap: 10px; margin-bottom: 10px; align-items: center; }
  .post-run { display: none; align-items: center; gap: 8px; }
  .post-run.visible { display: flex; }

  #yamlPreview { width: 100%; height: 280px; font-family: 'Cascadia Code','Consolas','Courier New',monospace; font-size: 11.5px; resize: vertical; }
</style>
</head>
<body>

<div class="header">
  <h1>Shadow Man Remastered — Archipelago Companion <span style="font-size:11px;font-weight:400;color:var(--muted);margin-left:6px">__COMPANION_VERSION__</span></h1>
  <p>Build a player YAML for the Archipelago generator, or apply a generated .apshadowman seed to your game.</p>
</div>

<div class="tabs">
  <button class="tab-btn active" id="tabBtnYaml" onclick="showTab('yaml')">1&ensp;·&ensp;Generate YAML</button>
  <button class="tab-btn" id="tabBtnApply" onclick="showTab('apply')">2&ensp;·&ensp;Apply AP Seed</button>
</div>

<!-- ══════════════════════════ TAB 1: Generate YAML ══════════════════════════ -->
<div class="tab-panel active" id="panelYaml">

  <!-- Row 1: Player Name -->
  <!-- Game Directory field removed 2026-07-21 — the "hybrid" immediate-patch
       path (a game_dir YAML option) no longer exists; generation never
       touches your local game files. Use the Apply AP Seed tab (Tab 2)
       after generating, which is where a game directory is actually needed. -->
  <div class="card-row" style="align-items:stretch">
    <div class="card" style="flex:0 0 260px">
      <div class="card-title">Player Name
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Must match the name you use to connect the client.</span></span>
      </div>
      <input type="text" id="playerName" class="name-input" value="Player" oninput="updateYamlPreview()" style="width:100%">
    </div>
    <div class="card" style="flex:1">
      <div class="card-title">Description
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Optional label shown in the AP website's weights list (e.g. "P1 Weights: yourfile.yaml &gt;&gt; this text"). Purely cosmetic.</span></span>
      </div>
      <input type="text" id="playerDescription" class="name-input" value="Shadow Man Remastered Settings" oninput="updateYamlPreview()" style="width:100%">
    </div>
    <div class="card" style="flex:0 0 170px">
      <div class="card-title">Checks
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Estimated number of AP location checks this seed will generate. Only <b>Cadeaux Key Items</b>, <b>Cadeaux Bundle Size</b>, <b>Fog Door Check</b>, and <b>Trap/Bonus Count</b> affect this number &mdash; every other setting only changes what you find where, not how many checks exist. If any of those four are set to &#127922; random, this shows the estimate at their current slider/checkbox value, not the real range &mdash; the actual count may differ once that's rolled.</span></span>
      </div>
      <div id="checkEstimate" style="font-size:26px;font-weight:700;color:var(--accent2);line-height:1.15">&mdash;</div>
      <div id="checkEstimateNote" style="font-size:10px;color:var(--muted);margin-top:2px;min-height:12px"></div>
    </div>
  </div>

  <!-- Row 2: Gameplay+Starting Item (left) | Coffin Gates+Entrance (centre) | Gameplay Tuning (right) -->
  <div style="display:grid;grid-template-columns:minmax(0,0.75fr) minmax(0,0.9fr) minmax(0,1.35fr);gap:10px;margin-bottom:10px;align-items:stretch">

    <!-- Left: Gameplay checkboxes + Starting Item (stub) -->
    <div class="card" style="min-width:200px">
      <div class="card-title">Gameplay — Shuffle</div>
      <div class="check-grid" style="grid-template-columns:1fr">
        <!-- Gad Pickups checkbox removed (2026-08-15): gad temples are
             always shuffled now, no longer a player option -- the "off"
             state was a real correctness bug (see options.py's
             ShadowManOptions comment / CLAUDE.md). Want Gad Powers
             guaranteed from the start instead of shuffled? Use AP's own
             Start Inventory From Pool for "Gad Power" in your YAML. -->
        <label class="check-label">
          <input type="checkbox" id="yShuffleWeapons" checked onchange="updateYamlPreview()">
          Weapons
          <button class="rng-btn" id="yShuffleWeaponsRng" onclick="event.preventDefault();toggleRng('yShuffleWeapons')" title="Randomize per seed">&#127922;</button>
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles weapons (Asson, Shotgun, Enseigne, MP5, T&ecirc;te de Mort, Desert Eagle) across item locations. Uncheck to leave weapons in their vanilla spots.</span></span>
        </label>
        <label class="check-label">
          <input type="checkbox" id="yShuffleLore" checked onchange="updateYamlPreview()">
          Lore
          <button class="rng-btn" id="yShuffleLoreRng" onclick="event.preventDefault();toggleRng('yShuffleLore')" title="Randomize per seed">&#127922;</button>
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles lore items (Book of Shadows, Prophecy, Jack&rsquo;s Schematic) across locations. Uncheck to leave them in vanilla positions.</span></span>
        </label>
        <label class="check-label">
          <input type="checkbox" id="yShuffleBonus" onchange="updateYamlPreview()">
          Light Soul
          <button class="rng-btn" id="yShuffleBonusRng" onclick="event.preventDefault();toggleRng('yShuffleBonus')" title="Randomize per seed">&#127922;</button>
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Includes the Light Soul bonus item (permanent invincibility, vanilla reward for 666 Cadeaux) in the shuffle pool. Off by default as it can affect run balance.</span></span>
        </label>
        <label class="check-label">
          <input type="checkbox" id="yPistonCombos" onchange="updateYamlPreview()">
          Piston Combos
          <button class="rng-btn" id="yPistonCombosRng" onclick="event.preventDefault();toggleRng('yPistonCombos')" title="Randomize per seed">&#127922;</button>
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Randomizes the 6 Dark Engine piston combination values; the in-game journal (Jack&rsquo;s Schematic entry) is rewritten to show the new numbers. When enabled, Jack&rsquo;s Schematic becomes required progression — you must find it to learn the combinations needed to shut the pistons off and reach Legion, the final boss.</span></span>
        </label>
        <label class="check-label">
          <input type="checkbox" id="yDeadsideGuns" onchange="updateYamlPreview()">
          Deadside Guns
          <button class="rng-btn" id="yDeadsideGunsRng" onclick="event.preventDefault();toggleRng('yDeadsideGuns')" title="Randomize per seed">&#127922;</button>
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Forces the vanilla "I like Dead Side Guns" secret on at patch time &mdash; Deadside weapons work on Liveside and vice versa &mdash; without needing to find its hidden in-world unlock trigger. Edits kexengine.cfg; requires having launched the game at least once already.</span></span>
        </label>
      </div>
      <hr class="divider">
      <div class="card-title" style="margin-bottom:8px">
        Starting Item
        <span class="tip" style="vertical-align:middle"><span class="tip-icon">?</span><span class="tip-box">AP-native "Start Inventory From Pool". Ctrl/Cmd-click (or shift-click for a range) to pick several items at once — AP's start_inventory_from_pool genuinely supports multiple items, unlike the standalone randomizer's single-item Starting Item dropdown. Retractor / Accumulator / Gad Power Upgrade each grant the full stackable count (5 / 3 / 3) in one pick — there's no separate "give me just 1" option, matching what the old bundle checkboxes did. Picked items are removed from the shuffle pool and granted at game start via the AP client's memory injection.</span></span>
      </div>
      <div class="row">
        <select id="ySiItem" onchange="updateYamlPreview()" multiple size="8" style="flex:1;min-width:0">
          <option value="Engineers Key">Engineers Key</option>
          <option value="Baton">Baton</option>
          <option value="Flashlight">Flashlight</option>
          <option value="Poigne">Poigne</option>
          <option value="Calabash">Calabash</option>
          <option value="Flambeau">Flambeau</option>
          <option value="Marteau">Marteau</option>
          <option value="Prison Key Card">Prison Key Card</option>
          <option value="Retractor">Retractor (all 5)</option>
          <option value="Accumulator">Accumulator (all 3)</option>
          <option value="Gad Power">Gad Power Upgrade (all 3)</option>
          <option value="Asson">Asson</option>
          <option value="Shotgun">Shotgun</option>
          <option value="Sawed-off Shotgun">Sawed-off Shotgun</option>
          <option value="MP-909">MP-909</option>
          <option value="Enseigne">Enseigne</option>
          <option value="Tete de Mort">T&ecirc;te De Mort</option>
          <option value="Violator">Violator</option>
          <option value="Book of Shadows">Book of Shadows</option>
          <option value="Book of Prophecy">Book of Prophecy</option>
          <option value="Jacks Schematic">Jack&rsquo;s Schematic</option>
          <option value="Light Soul">Light Soul</option>
          <option value="La Lune">Eclipser (La Lune)</option>
          <option value="La Lame">Eclipser (La Lame)</option>
          <option value="Le Soleil">Eclipser (Le Soleil)</option>
        </select>
      </div>
      <div class="row" style="margin-top:6px">
        <button type="button" class="btn-ghost" onclick="clearStartingItems()" style="width:100%;font-size:11px;padding:5px 9px">Clear Selection</button>
      </div>
    </div>

    <!-- Centre column: Coffin Gate Soul Levels + Entrance Randomizer + Health (gameplay tuning, non-AP-specific) -->
    <div class="card">
      <div class="card-title">
        Coffin Gate Soul Levels
        <span class="tip" style="vertical-align:middle">
          <span class="tip-icon">?</span>
          <span class="tip-box">Shuffles the soul level (SL) thresholds on deadside coffin gates. Higher SL gates require more Dark Souls collected before they open.</span>
        </span>
      </div>
      <div style="display:flex;flex-direction:column;gap:7px">
        <div class="row">
          <label style="color:var(--muted);font-size:11px;white-space:nowrap;width:64px">Preset:</label>
          <select id="yGatePreset" onchange="onGatePresetChange()" style="flex:1;min-width:0">
            <option value="story">story — all gates open</option>
            <option value="easy">easy — SL7 cap, 6 open</option>
            <option value="medium">medium — SL8 cap, 3 open</option>
            <option value="hard" selected>hard — no cap, 1 open</option>
            <option value="chaos">chaos — unconstrained</option>
            <option value="random">&#127922; random — rolled per seed</option>
          </select>
        </div>
        <div class="row">
          <label style="color:var(--muted);font-size:11px;white-space:nowrap;width:64px">Max SL:</label>
          <input type="range" id="yMaxGateSl" min="3" max="10" step="1" value="8"
                 oninput="document.getElementById('yMaxGateSlVal').textContent=this.value;updateYamlPreview()">
          <span class="slider-val" id="yMaxGateSlVal">8</span>
          <button class="rng-btn" id="yMaxGateSlRng" onclick="toggleRng('yMaxGateSl')" title="Randomize per seed">&#127922;</button>
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Caps the highest Soul Level a shuffled coffin gate can require. 10 = no cap (top gate needs all 120 souls). Lower values leave more slack, making generation more reliable. Greyed out for the <b>story</b> preset, which ignores it &mdash; all gates are SL0 regardless.</span></span>
        </div>
        <div class="row">
          <label style="color:var(--muted);font-size:11px;white-space:nowrap;width:64px">Open N:</label>
          <select id="yOpenGatesN" style="flex:1;min-width:0" onchange="updateYamlPreview()">
            <option value="-1" selected>preset</option>
            <option value="0">0 — none</option>
            <option value="1">1 — Marrow</option>
            <option value="2">2 — Wasteland</option>
            <option value="3">3 — Asylum</option>
            <option value="4">4 — Temple</option>
            <option value="5">5 — Cageways</option>
            <option value="6">6 — Playrooms</option>
          </select>
          <button class="rng-btn" id="yOpenGatesNRng" onclick="event.preventDefault();toggleRng('yOpenGatesN')" title="Randomize per seed">&#127922;</button>
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Forces the first N linear coffin gates to SL0, regardless of the gate preset. Gates open in sequence: Marrow &rarr; Wasteland &rarr; Asylum &rarr; Temple &rarr; Cageways &rarr; Playrooms. <b>preset</b> leaves the chosen preset's own default alone. Greyed out for the <b>story</b> preset, which already opens every gate.</span></span>
        </div>
        <div class="row">
          <label style="color:var(--muted);font-size:11px;white-space:nowrap">SL Req Shuffle:</label>
          <select id="ySoulThresholdMode" style="flex:1;min-width:0" onchange="updateYamlPreview()">
            <option value="off" selected>Off (vanilla)</option>
            <option value="progressive">Progressive</option>
            <option value="balanced">Balanced</option>
            <option value="full_random">Random</option>
          </select>
          <button class="rng-btn" id="ySoulThresholdModeRng" onclick="event.preventDefault();toggleRng('ySoulThresholdMode')" title="Randomize per seed">&#127922;</button>
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Redistributes the soul counts required for SL1&ndash;SL10 (vanilla: 1, 3, 7, 15, 23, 35, 51, 71, 95, 120). <b>Off</b> keeps vanilla thresholds. AP&rsquo;s own logic always matches whatever this seed's real thresholds are. The &#127922; button randomly picks a mode each seed. <b>Random</b> was briefly removed 2026-08-09 after being blamed for AP generation failures, then re-added the same day once the real cause (Dark Soul provisioning math using vanilla thresholds instead of the seed's real ones, affecting every mode) was found and fixed, plus two rounds of hardening on Random's own threshold spacing.</span></span>
        </div>
      </div>

      <hr class="divider">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin-bottom:6px">
        Entrance Randomizer
        <span class="tip" style="vertical-align:middle"><span class="tip-icon">?</span><span class="tip-box"><b>Deadside Only:</b> shuffles the 9 Deadside portals among themselves — which level a portal leads to is randomized, but each portal keeps its own physical soul-gate requirement. <b>Cross Hub</b> (Deadside levels + Engine Rooms shuffled together, standalone-only) isn&rsquo;t wired into the AP world yet — see AP_FEATURE_GAP.md Tier 3.</span></span>
      </div>
      <div class="row" style="margin-bottom:6px">
        <select id="yEntranceMode" onchange="updateYamlPreview()" style="width:100%;min-width:0">
          <option value="off" selected>Off — vanilla entrances</option>
          <option value="deadside_only">Deadside Only — 9 levels shuffled</option>
          <option value="random">&#127922; random</option>
        </select>
      </div>

      <hr class="divider">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin-bottom:6px">Health</div>
      <div class="row" style="margin-bottom:8px">
        <span style="color:var(--muted);font-size:11px;white-space:nowrap">Starting:</span>
        <input type="range" id="yStartingHealth" min="1" max="10" step="1" value="5"
               oninput="document.getElementById('yStartingHealthVal').textContent=this.value;updateYamlPreview()">
        <span class="slider-val" id="yStartingHealthVal">5</span>
        <button class="rng-btn" id="yStartingHealthRng" onclick="toggleRng('yStartingHealth')" title="Randomize per seed">&#127922;</button>
        <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Starting max health, as a multiple of 1000. Vanilla is 5 (5000 HP). Current health is set to max on spawn.</span></span>
      </div>
      <div class="row" style="margin-bottom:6px">
        <span style="color:var(--muted);font-size:11px;white-space:nowrap">Per altar:</span>
        <input type="range" id="yAltarHealth" min="1" max="10" step="1" value="1"
               oninput="document.getElementById('yAltarHealthVal').textContent=this.value;updateYamlPreview()">
        <span class="slider-val" id="yAltarHealthVal">1</span>
        <button class="rng-btn" id="yAltarHealthRng" onclick="toggleRng('yAltarHealth')" title="Randomize per seed">&#127922;</button>
        <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Health granted per life altar interaction, as a multiple of 1000. Vanilla is 1 (1000 HP per altar, 5 altars total).</span></span>
      </div>
      <div class="hint" style="margin-bottom:6px">start + 5 &times; per-altar &le; 10 (hard cap)</div>
      <div class="row" style="margin-bottom:4px">
        <span style="color:var(--muted);font-size:11px;white-space:nowrap">Per death:</span>
        <input type="range" id="yDeathPenalty" min="0" max="10" step="0.5" value="0" oninput="refreshDeathPenaltyLabel()">
        <span class="slider-val" id="yDeathPenaltyVal">Off</span>
        <button class="rng-btn" id="yDeathPenaltyRng" onclick="toggleRng('yDeathPenalty');refreshDeathPenaltyLabel()" title="Randomize per seed">&#127922;</button>
        <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Reduces max health by step&times;1000 on each death, floored at that amount. Supports half-steps (e.g. 0.5 = &minus;500/death). <b>Off</b> disables the penalty. Applied as a direct EXE patch.</span></span>
      </div>
      <div class="hint" style="margin-bottom:6px">Off = disabled &nbsp;&nbsp; 0.5&ndash;10 = &minus;&frac12; to &minus;10 health bars/death</div>
      <div class="row" style="margin-bottom:4px">
        <span style="color:var(--muted);font-size:11px;white-space:nowrap">Shift-sprint:</span>
        <input type="range" id="ySprintMultiplier" min="0" max="5" step="0.1" value="0" oninput="refreshSprintMultiplierLabel()">
        <span class="slider-val" id="ySprintMultiplierVal">Off</span>
        <button class="rng-btn" id="ySprintMultiplierRng" onclick="toggleRng('ySprintMultiplier');refreshSprintMultiplierLabel()" title="Randomize per seed">&#127922;</button>
        <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Hold Shift to move at this multiple of normal speed, on both land and in water. <b>Off</b> disables Shift-sprint entirely (vanilla movement). Applied as a direct EXE patch.</span></span>
      </div>
      <div class="hint" style="margin-bottom:6px">Off = disabled &nbsp;&nbsp; 1.0&ndash;5.0 = 1x&ndash;5x speed while Shift is held</div>
    </div>

    <!-- Col 3: Archipelago Settings -->
    <div class="card">
      <div class="card-title">Archipelago Settings</div>

      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin-bottom:6px">Multiworld</div>
      <div class="row" style="flex-wrap:wrap;row-gap:6px;margin-bottom:8px">
        <label class="check-label" style="padding:0">
          <input type="checkbox" id="yDeathLink" onchange="syncDeathLinkThresholdDependency()">
          Death Link
          <button class="rng-btn" id="yDeathLinkRng" onclick="event.preventDefault();toggleRng('yDeathLink');syncDeathLinkThresholdDependency()" title="Randomize per seed">&#127922;</button>
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Standard Archipelago Death Link — a death in any linked game kills you here too, and vice versa.</span></span>
        </label>
        <div style="display:flex;align-items:center;gap:8px;margin-left:10px">
          <span style="color:var(--muted);font-size:11px;white-space:nowrap">Every</span>
          <input type="number" id="yDeathLinkThreshold" value="1" min="1" max="5" style="width:50px" oninput="updateYamlPreview()">
          <span style="color:var(--muted);font-size:11px;white-space:nowrap">death(s)</span>
          <button class="rng-btn" id="yDeathLinkThresholdRng" onclick="event.preventDefault();toggleRng('yDeathLinkThreshold')" title="Randomize per seed">&#127922;</button>
          <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">How many of YOUR OWN deaths it takes to send one Death Link. 1 (default) sends every time, same as before this existed. 5 means only every 5th death of yours actually broadcasts &mdash; the other 4 die free. Incoming Death Links (a teammate died) always kill you immediately regardless of this &mdash; it only throttles what you send. Greyed out unless Death Link above is on.</span></span>
        </div>
      </div>

      <hr class="divider">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin-bottom:4px">Cadeaux</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:2px 12px;align-items:start">
        <div>
          <div class="row">
            <span style="color:var(--muted);font-size:11px;white-space:nowrap;margin-right:8px">Altar cost:</span>
            <input type="number" id="yAltarCadeaux" value="100" min="1" max="133" style="width:60px" oninput="syncCadeauxConstraints()">
            <button class="rng-btn" id="yAltarCadeauxRng" onclick="toggleRng('yAltarCadeaux');syncCadeauxConstraints()" title="Randomize per seed">&#127922;</button>
            <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Cadeaux required and spent per life altar interaction. The minimum required and the per-interaction cost are always equal (vanilla: 100, max: 133 = &lfloor;666 &divide; 5&rfloor;). EXE-only &mdash; doesn&rsquo;t affect AP logic.</span></span>
          </div>
          <div id="yAltarCadeauxMsg" style="display:none;font-size:10px;color:var(--red);margin-top:2px"></div>
        </div>
        <div>
          <div class="row">
            <span style="color:var(--muted);font-size:11px;white-space:nowrap;margin-right:8px">Fog door:</span>
            <input type="number" id="yFogCadeaux" value="666" min="5" max="666" style="width:60px" oninput="syncCadeauxConstraints()">
            <button class="rng-btn" id="yFogCadeauxRng" onclick="toggleRng('yFogCadeaux');syncCadeauxConstraints()" title="Randomize per seed">&#127922;</button>
            <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Total Cadeaux required to open the Fogometers door (vanilla: 666). Must be at least 5&times; the altar cost and no more than 666. Also what the Fog Door Check option (below) checks against, when that option is on.</span></span>
          </div>
          <div id="yFogCadeauxMsg" style="display:none;font-size:10px;color:var(--red);margin-top:2px"></div>
        </div>
        <label class="check-label" style="margin-top:6px">
          <input type="checkbox" id="yInsanity" onchange="syncCadeauxGatedDependency();syncCadeauxBundleDependency()">
          Cadeaux Key Items
          <button class="rng-btn" id="yInsanityRng" onclick="event.preventDefault();toggleRng('yInsanity');syncCadeauxGatedDependency();syncCadeauxBundleDependency()" title="Randomize per seed">&#127922;</button>
          <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Off (default): cadeaux (statue/altar) locations are excluded from AP entirely &mdash; same treatment as barrels, no checks, no hints, stays vanilla. On: all ~657 cadeaux locations become real checks with no item-type restriction (any item, including other players&rsquo; key items, can land there). Soul/Govi altar locations are unaffected either way &mdash; they&rsquo;ve always existed as checks eligible for any item. Note: narrower than the standalone tool&rsquo;s graded insanity tiers, which can also open weapon/lore/bonus/barrel slots &mdash; not implemented here.</span></span>
        </label>
        <div class="row" style="margin-top:6px">
          <span style="color:var(--muted);font-size:11px;white-space:nowrap;margin-right:8px">Max bundle:</span>
          <input type="number" id="yCadeauxBundleSize" value="1" min="1" max="50" style="width:55px" oninput="updateYamlPreview()">
          <button class="rng-btn" id="yCadeauxBundleSizeRng" onclick="event.preventDefault();toggleRng('yCadeauxBundleSize')" title="Randomize per seed">&#127922;</button>
          <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Caps how many cadeaux pickups can be grouped into a single AP check, instead of one check per cadeaux. Bundling is now GLOBAL across the whole game (changed 2026-07-28) &mdash; every eligible cadeaux is shuffled together and chunked at this size, so you get AT MOST ONE remainder bundle smaller than this value for the entire seed, not one per region. Tradeoff: a bundle's real check can end up behind a different gate than some of the cadeaux whose value it absorbed, so the reward pacing no longer strictly follows region/gate progression &mdash; doesn't affect completability, just the economy's feel. 1 (default): no bundling, same as before this option existed (~657 individual checks). E.g. a cap of 5 turns ~657 checks into ~131 (130 at x5, 1 remainder). Only meaningful when Cadeaux Key Items above is also on &mdash; greyed out otherwise.</span></span>
        </div>
        <label class="check-label" style="grid-column:1/-1;margin-top:6px">
          <input type="checkbox" id="yCadeauxGatedContent" onchange="updateYamlPreview()">
          Fog Door Check
          <button class="rng-btn" id="yCadeauxGatedContentRng" onclick="event.preventDefault();toggleRng('yCadeauxGatedContent')" title="Randomize per seed">&#127922;</button>
          <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Controls whether the Fogometers Light Soul location (gated behind SL10 + collecting the Fog door&rsquo;s worth of Cadeaux, above) exists as an AP check at all. Off (default): excluded from AP entirely &mdash; stays vanilla, same treatment as barrels/enemy checks. On: it becomes a real check, gated in logic by that Fog door requirement, backed by a precollected-Cadeaux fix so it stays solvable. Requires Cadeaux Key Items above to also be on (greyed out otherwise) &mdash; Cadeaux isn&rsquo;t an AP-tracked item at all unless that&rsquo;s on.</span></span>
        </label>
      </div>

      <hr class="divider">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin-bottom:6px">Trap/Bonus Filler</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:2px 12px;align-items:start">
        <div class="row" style="grid-column:1/-1">
          <span style="color:var(--muted);font-size:11px;white-space:nowrap;margin-right:8px">Count:</span>
          <input type="number" id="yTrapBonusCount" value="0" min="0" max="1000" style="width:55px" oninput="updateYamlPreview()">
          <button class="rng-btn" id="yTrapBonusCountRng" onclick="event.preventDefault();toggleRng('yTrapBonusCount')" title="Randomize per seed">&#127922;</button>
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">How many "Trap/Bonus" filler items are added to the pool. Each applies one random effect, rolled from whichever categories are enabled below: Secrets (a cosmetic secret cvar effect &mdash; Big Head, Wireframe, Disco Lights, etc.), Health (a ~1-minute gradual poison or a full heal x2), Voodoo (drain to 0 or hold at max), or Ammo (drain all 3 tracked guns to 0 or hold at max). 0 (default) adds none. Promotes that many previously-excluded, verified barrel-category locations to real AP checks too, so traps add genuinely new room rather than displacing existing filler.</span></span>
          <span style="color:var(--muted);font-size:11px;white-space:nowrap;margin-left:14px;margin-right:8px">Duration:</span>
          <input type="number" id="yTrapBonusDuration" value="60" min="10" max="300" style="width:55px" oninput="updateYamlPreview()">
          <span style="color:var(--muted);font-size:11px;white-space:nowrap;margin-left:4px">sec</span>
          <button class="rng-btn" id="yTrapBonusDurationRng" onclick="event.preventDefault();toggleRng('yTrapBonusDuration')" title="Randomize per seed">&#127922;</button>
          <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Seconds a temporary trap/hold effect lasts before auto-reverting. Only matters when Mode below actually goes temporary.</span></span>
        </div>
        <div class="row" style="grid-column:1/-1;margin-top:6px">
          <span style="color:var(--muted);font-size:11px;white-space:nowrap;margin-right:8px">Mode:</span>
          <select id="yTrapBonusMode" style="flex:1;min-width:0" onchange="updateYamlPreview()">
            <option value="always_temporary" selected>Always Temporary</option>
            <option value="always_permanent">Always Permanent</option>
            <option value="mixed">Mixed (rolled per pickup)</option>
          </select>
          <button class="rng-btn" id="yTrapBonusModeRng" onclick="event.preventDefault();toggleRng('yTrapBonusMode')" title="Randomize per seed">&#127922;</button>
          <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Only applies to the Secrets, Voodoo Hold, and Ammo Hold sub-effects (Health and Voodoo/Ammo Drain always run their own fixed one-shot duration regardless of this setting). Always Temporary (default): auto-reverts after Trap Duration seconds. Always Permanent: stays on until superseded by a matching drain or another roll of the same effect. Mixed: each pickup independently rolls temporary vs permanent.</span></span>
        </div>
        <label class="check-label" style="margin-top:6px">
          <input type="checkbox" id="yTrapBonusSecretsEnabled" checked onchange="updateYamlPreview()">
          Secrets
          <button class="rng-btn" id="yTrapBonusSecretsEnabledRng" onclick="event.preventDefault();toggleRng('yTrapBonusSecretsEnabled')" title="Randomize per seed">&#127922;</button>
          <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">On (default): a rolled Trap/Bonus can apply a cosmetic secret cvar effect (Big Head, Wireframe, Disco Lights, etc.).</span></span>
        </label>
        <label class="check-label" style="margin-top:6px">
          <input type="checkbox" id="yTrapBonusHealthEnabled" checked onchange="updateYamlPreview()">
          Health (Poison/Heal)
          <button class="rng-btn" id="yTrapBonusHealthEnabledRng" onclick="event.preventDefault();toggleRng('yTrapBonusHealthEnabled')" title="Randomize per seed">&#127922;</button>
          <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">On (default): a rolled Trap/Bonus can apply a ~1-minute gradual poison (drains your whole health pool, then kills you if it reaches 0 before you heal up) or a gradual recovery (heals your full health pool twice over).</span></span>
        </label>
        <label class="check-label">
          <input type="checkbox" id="yTrapBonusVoodooEnabled" checked onchange="updateYamlPreview()">
          Voodoo Power
          <button class="rng-btn" id="yTrapBonusVoodooEnabledRng" onclick="event.preventDefault();toggleRng('yTrapBonusVoodooEnabled')" title="Randomize per seed">&#127922;</button>
          <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">On (default): a rolled Trap/Bonus can instantly drain Voodoo Power to 0, or hold it at max for the Trap Duration (or permanently, per Mode above).</span></span>
        </label>
        <label class="check-label">
          <input type="checkbox" id="yTrapBonusAmmoEnabled" checked onchange="updateYamlPreview()">
          Ammo
          <button class="rng-btn" id="yTrapBonusAmmoEnabledRng" onclick="event.preventDefault();toggleRng('yTrapBonusAmmoEnabled')" title="Randomize per seed">&#127922;</button>
          <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">On (default): a rolled Trap/Bonus can instantly drain all 3 tracked ammo types (Shotgun, Violator, 9mm) to 0, or hold them all at max for the Trap Duration (or permanently, per Mode above).</span></span>
        </label>
      </div>

      <hr class="divider">
      <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin-bottom:6px">Progression</div>
      <div class="row" style="flex-wrap:wrap;row-gap:8px;column-gap:14px">
      <div style="display:flex;align-items:center;gap:8px">
        <span style="color:var(--muted);font-size:11px;white-space:nowrap">Balancing:</span>
        <input type="range" id="yProgBalance" min="0" max="100" value="50" style="width:80px"
               oninput="document.getElementById('yProgBalanceVal').textContent=this.value;updateYamlPreview()">
        <span class="slider-val" id="yProgBalanceVal">50</span>
        <button class="rng-btn" id="yProgBalanceRng" onclick="toggleRng('yProgBalance')" title="Randomize per seed">&#127922;</button>
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Controls how deep into the world progression items tend to be placed. Default 50 is balanced. 0 = items placed early, 100 = items pushed deep.</span></span>
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        <span style="color:var(--muted);font-size:11px;white-space:nowrap">Soul Buffer:</span>
        <select id="ySoulLogicBuffer" style="width:126px" onchange="updateYamlPreview()">
          <option value="off" selected>Off (no slack)</option>
          <option value="hard">Hard (+2 souls)</option>
          <option value="medium">Medium (+4 souls)</option>
          <option value="easy">Easy (+6 souls)</option>
        </select>
        <button class="rng-btn" id="ySoulLogicBufferRng" onclick="event.preventDefault();toggleRng('ySoulLogicBuffer')" title="Randomize per seed">&#127922;</button>
        <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Pads how many souls AP&rsquo;s fill/logic requires be reachable before a soul gate, beyond what the gate actually needs in-game. E.g. <b>Medium</b> means a gate that really only needs 3 souls won&rsquo;t be treated as passable until 7 are placed reachable before it. Doesn&rsquo;t change the real in-game requirement at all &mdash; just leaves AP more slack so a seed doesn&rsquo;t feel razor-tight if you don&rsquo;t grab every single soul on the way. Higher = more forgiving, less "tight." Off (default) is exact prior behavior &mdash; the underlying soul-provisioning bugs that once motivated defaulting this on were found and fixed directly (see SoulThresholdMode's docstring in options.py), so this is back to being a pure optional-extra-slack knob, not a safety net.</span></span>
      </div>
      </div>
      <div class="hint" style="margin-top:6px">0 = items placed early &nbsp;&nbsp; 100 = items pushed deep</div>
    </div>

  </div>

  <!-- Row 3: Enemies + Cosmetic Shuffles -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;align-items:stretch">
    <div class="card" style="flex:1;min-width:280px">
      <div class="card-title">Enemies</div>
      <div class="check-grid" style="margin-bottom:6px">
        <label class="check-label">
          <input type="checkbox" id="yShuffleEnemies" onchange="onEnemiesChange()">
          Shuffle Enemies
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Randomizes enemy types in each level. Use the Enemy Mode dropdown to control how they&rsquo;re assigned.</span></span>
          <button class="rng-btn" id="yShuffleEnemiesRng" onclick="event.preventDefault();toggleRng('yShuffleEnemies');onEnemiesChange()" title="Randomize per seed">&#127922;</button>
        </label>
        <label class="check-label">
          <input type="checkbox" id="yShuffleTrueForms" onchange="updateYamlPreview()">
          Shuffle Trueforms
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">True form boss enemies (which drop Dark Souls) can swap positions with regular enemies. Soul reachability logic is preserved.</span></span>
          <button class="rng-btn" id="yShuffleTrueFormsRng" onclick="event.preventDefault();toggleRng('yShuffleTrueForms')" title="Randomize per seed">&#127922;</button>
        </label>
        <label class="check-label">
          <input type="checkbox" id="yEnemyMixMovement" disabled onchange="updateYamlPreview()">
          Mix Movement Types
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Allows ground, flying, and swimming enemies to swap with each other. Off by default. Ignored unless Shuffle Enemies is also enabled.</span></span>
          <button class="rng-btn" id="yEnemyMixMovementRng" onclick="event.preventDefault();toggleRng('yEnemyMixMovement');onEnemiesChange()" title="Randomize per seed">&#127922;</button>
        </label>
        <label class="check-label">
          <input type="checkbox" id="yEnemyUncapCounts" disabled onchange="updateYamlPreview()">
          Uncap Enemy Counts
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Each slot independently draws a random enemy type with replacement &mdash; some types may appear far more (or less) often than vanilla. Ignored unless Shuffle Enemies is also enabled.</span></span>
          <button class="rng-btn" id="yEnemyUncapCountsRng" onclick="event.preventDefault();toggleRng('yEnemyUncapCounts');onEnemiesChange()" title="Randomize per seed">&#127922;</button>
        </label>
      </div>
      <div class="enemy-row">
        <span class="lbl">Enemy Mode:</span>
        <select id="yEnemyMode" disabled onchange="updateYamlPreview()">
          <option value="difficulty">difficulty &mdash; tier-weighted</option>
          <option value="contextual">contextual &mdash; area pools</option>
          <option value="full">full &mdash; completely random</option>
          <option value="random">&#127922; random mode</option>
        </select>
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box"><b>difficulty:</b> enemies replaced by others of similar difficulty &mdash; same tier, weighted by area depth.<br><b>contextual:</b> shuffled within context groups (deadside/liveside/prison stay separated).<br><b>full:</b> completely random across the whole enemy pool.</span></span>
        <span class="hint" id="yEnemyHint">enable Shuffle Enemies to unlock</span>
      </div>
    </div>

    <div class="card" style="margin-bottom:0;flex:1">
      <div class="card-title">Cosmetic Shuffles</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:2px 8px;margin-bottom:6px">
        <label class="check-label">
          <input type="checkbox" id="yShuffleMusic" onchange="updateYamlPreview()">
          Shuffle Music
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Randomly reassigns music tracks across all levels. No effect on gameplay or logic. Requires KPF repack — applied when you run Apply AP Seed (Tab 2).</span></span>
          <button class="rng-btn" id="yShuffleMusicRng" onclick="event.preventDefault();toggleRng('yShuffleMusic')" title="Randomize per seed">&#127922;</button>
        </label>
        <label class="check-label">
          <input type="checkbox" id="yShuffleVoices" onchange="updateYamlPreview()">
          Shuffle Voice Lines
          <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Shuffles Shadow Man&rsquo;s generic ambient voice lines. Purely cosmetic.</span></span>
          <button class="rng-btn" id="yShuffleVoicesRng" onclick="event.preventDefault();toggleRng('yShuffleVoices')" title="Randomize per seed">&#127922;</button>
        </label>
        <label class="check-label">
          <input type="checkbox" id="yShuffleWeaponsSfx" onchange="updateYamlPreview()">
          Shuffle Weapon SFX
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles weapon fire and reload sounds within each weapon category. Purely cosmetic.</span></span>
          <button class="rng-btn" id="yShuffleWeaponsSfxRng" onclick="event.preventDefault();toggleRng('yShuffleWeaponsSfx')" title="Randomize per seed">&#127922;</button>
        </label>
        <label class="check-label">
          <input type="checkbox" id="yShuffleEnemiesSfx" onchange="updateYamlPreview()">
          Shuffle Enemy SFX
          <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Shuffles enemy pain/startle/attack sounds between enemy types. Purely cosmetic.</span></span>
          <button class="rng-btn" id="yShuffleEnemiesSfxRng" onclick="event.preventDefault();toggleRng('yShuffleEnemiesSfx')" title="Randomize per seed">&#127922;</button>
        </label>
        <label class="check-label">
          <input type="checkbox" id="yShuffleAmbients" onchange="updateYamlPreview()">
          Shuffle Ambients
          <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles ambient creatures (rats, egrets, flies, butterflies, friendly fish) across their spawn slots. Purely cosmetic &mdash; they don&rsquo;t drop items or block progress. Uses the default (global pool) ambient mode.</span></span>
          <button class="rng-btn" id="yShuffleAmbientsRng" onclick="event.preventDefault();toggleRng('yShuffleAmbients')" title="Randomize per seed">&#127922;</button>
        </label>
        <label class="check-label">
          <input type="checkbox" id="yShuffleSky" onchange="updateYamlPreview()">
          Shuffle Sky Textures
          <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Swaps sky textures across levels &mdash; each sky layer (horizon, clouds, hills, sun) is shuffled within its own pool. Purely cosmetic.</span></span>
          <button class="rng-btn" id="yShuffleSkyRng" onclick="event.preventDefault();toggleRng('yShuffleSky')" title="Randomize per seed">&#127922;</button>
        </label>
      </div>
    </div>
  </div>

  <div class="card" style="margin-bottom:10px">
    <div class="card-title" style="display:flex;align-items:center;justify-content:space-between">
      <span>YAML Preview</span>
      <span>
        <button class="btn-ghost" onclick="importYaml()" style="font-size:11px;padding:3px 9px">📂 Import YAML…</button>
        <button class="btn-ghost" onclick="copyYaml()" style="font-size:11px;padding:3px 9px">📋 Copy</button>
        <button class="btn-run" onclick="saveYaml()" style="font-size:11px;padding:3px 12px">💾 Save YAML…</button>
      </span>
    </div>
    <textarea id="yamlPreview" readonly spellcheck="false"></textarea>
    <div class="status" id="yamlStatus"></div>
  </div>

</div>

<!-- ══════════════════════════ TAB 2: Apply AP Seed ══════════════════════════ -->
<div class="tab-panel" id="panelApply">

  <div class="card" style="margin-bottom:10px">
    <div class="card-title">Seed File
      <span class="tip"><span class="tip-icon">?</span><span class="tip-box">The .apshadowman file from your Archipelago output (e.g. AP_12345_P1_Alice.apshadowman) — generated automatically alongside your AP_xxxxx.zip, whether generated on the AP website or a local generator.</span></span>
    </div>
    <div class="row">
      <input type="text" id="patchFile" class="dir-input" placeholder="Path to your .apshadowman file…">
      <button class="btn-ghost" onclick="browsePatchFile()">Browse…</button>
    </div>
  </div>

  <div class="card" style="margin-bottom:10px">
    <div class="card-title">Game Directory</div>
    <div class="row">
      <input type="text" id="applyGameDir" class="dir-input" placeholder="Path to Shadow Man Remastered…">
      <button class="btn-ghost" onclick="browseApplyGameDir()">Browse…</button>
    </div>
  </div>

  <div class="actions">
    <button class="btn-run" id="applyBtn" onclick="applySeed()">Apply Seed</button>
    <span class="status" id="applyStatus"></span>
  </div>

  <div class="terminal" id="applyOutput"></div>

  <!-- Always visible (2026-08-04) -- these don't require having applied a
       seed first, so they double as a quick "just start my game/client"
       shortcut using whatever game dir is already filled in above. -->
  <div class="post-run visible" id="applyPostRun">
    <button class="btn-launch" onclick="launchGame()">▶ Launch Game + Client</button>
    <button class="btn-ghost" onclick="launchGameOnly()">Launch Game Only</button>
    <button class="btn-ghost" onclick="launchClientOnly()">Launch Client Only</button>
    <button class="btn-ghost" onclick="openFolder()">Open Game Folder</button>
  </div>

</div>

<script>
function showTab(name) {
  document.getElementById('panelYaml').classList.toggle('active', name === 'yaml');
  document.getElementById('panelApply').classList.toggle('active', name === 'apply');
  document.getElementById('tabBtnYaml').classList.toggle('active', name === 'yaml');
  document.getElementById('tabBtnApply').classList.toggle('active', name === 'apply');
}

// ── Tab 1: YAML generation ──────────────────────────────────────────────────

// Per-field RNG toggle (🎲 buttons) — mirrors gui.py's toggleRng/isRng, but
// instead of rolling a value client-side, the active state makes buildYaml()
// emit the literal string "random" for that key. AP's YAML format accepts
// "random" for any Toggle/Choice/Range option natively (see
// AP_FEATURE_GAP.md section F), so the generator rolls it at generate time.
function isRng(id) {
  const btn = document.getElementById(id + 'Rng');
  return btn ? btn.classList.contains('rng-active') : false;
}
function toggleRng(id) {
  const btn = document.getElementById(id + 'Rng');
  if (!btn) return;
  const active = btn.classList.toggle('rng-active');
  const el = document.getElementById(id);
  if (el) {
    el.disabled = active;
    el.style.opacity = active ? '0.35' : '';
  }
  const valEl = document.getElementById(id + 'Val');
  if (valEl) valEl.textContent = active ? '?' : (el ? el.value : '');
  updateYamlPreview();
}
// Programmatic version of toggleRng() for YAML import (importYaml() below) —
// sets the rng-active state directly instead of toggling it, and doesn't
// call updateYamlPreview() itself (the importer calls it once at the end,
// after every field has been restored, mirroring gui.py's applyRng()/
// applyConfig() pattern — ap_gui.py didn't have this helper before since
// nothing previously needed to *set* rng state programmatically).
function applyRng(id, active) {
  const btn = document.getElementById(id + 'Rng');
  if (btn) btn.classList.toggle('rng-active', active);
  const el = document.getElementById(id);
  if (el) {
    el.disabled = active;
    el.style.opacity = active ? '0.35' : '';
  }
  const valEl = document.getElementById(id + 'Val');
  if (valEl) valEl.textContent = active ? '?' : (el ? el.value : '');
}

// Each preset's own baked-in Max SL cap (constants.py's GATE_PRESETS,
// AP world copy — story/hard/chaos all carry max_sl: None there, i.e. no
// cap, which the slider represents as its own max value of 10).
const GATE_PRESET_MAX_SL = { story: 10, easy: 7, medium: 8, hard: 10, chaos: 10 };

// Mirrors gui.py's updateGateDesc(): the "story" preset ("all gates open")
// makes max_gate_sl moot (every gate is SL0 regardless — see options.py's
// GatePreset docstring), so grey it out for that one preset only. Every
// other preset (including random) leaves it live, same as gui.py.
//
// Also snaps the Max SL slider itself to that preset's own default cap
// on every preset change (2026-07-31 fix — previously only toggled
// disabled/opacity, so the slider silently stayed wherever it last was
// regardless of preset, even though options.py's MaxGateSL applies "on
// top of the preset's own cap (whichever is lower)" — meaning easy's
// real effective cap was already always <=7 no matter what the slider
// displayed, just not shown that way in the UI).
function onGatePresetChange() {
  const preset = document.getElementById('yGatePreset').value;
  if (!isRng('yMaxGateSl')) {
    const el = document.getElementById('yMaxGateSl');
    el.disabled = (preset === 'story');
    el.style.opacity = (preset === 'story') ? '0.35' : '';
    if (Object.prototype.hasOwnProperty.call(GATE_PRESET_MAX_SL, preset)) {
      el.value = GATE_PRESET_MAX_SL[preset];
      document.getElementById('yMaxGateSlVal').textContent = el.value;
    }
  }
  if (!isRng('yOpenGatesN')) {
    const el = document.getElementById('yOpenGatesN');
    el.disabled = (preset === 'story');
    el.style.opacity = (preset === 'story') ? '0.35' : '';
  }
  updateYamlPreview();
}

function onEnemiesChange() {
  const on = isRng('yShuffleEnemies') || document.getElementById('yShuffleEnemies').checked;
  document.getElementById('yEnemyMode').disabled = !on;
  if (!isRng('yEnemyMixMovement')) document.getElementById('yEnemyMixMovement').disabled = !on;
  if (!isRng('yEnemyUncapCounts')) document.getElementById('yEnemyUncapCounts').disabled = !on;
  document.getElementById('yEnemyHint').textContent = on ? '' : 'enable Shuffle Enemies to unlock';
  updateYamlPreview();
}
function refreshDeathPenaltyLabel() {
  if (!isRng('yDeathPenalty')) {
    const v = parseFloat(document.getElementById('yDeathPenalty').value);
    document.getElementById('yDeathPenaltyVal').textContent = v === 0 ? 'Off' : ('−' + parseFloat(v.toFixed(1)));
  }
  updateYamlPreview();
}
function refreshSprintMultiplierLabel() {
  if (!isRng('ySprintMultiplier')) {
    const v = parseFloat(document.getElementById('ySprintMultiplier').value);
    document.getElementById('ySprintMultiplierVal').textContent = v === 0 ? 'Off' : (parseFloat(v.toFixed(1)) + 'x');
  }
  updateYamlPreview();
}

// Mirrors gui.py's syncCadeauxConstraints() exactly: altar cost 1-133
// (133 = floor(666/5)), fog door must be >= 5x altar cost and <= 666.
function syncCadeauxConstraints() {
  const ALTAR_MAX = 133;
  const FOG_MAX   = 666;
  const altarEl  = document.getElementById('yAltarCadeaux');
  const fogEl    = document.getElementById('yFogCadeaux');
  const altarMsg = document.getElementById('yAltarCadeauxMsg');
  const fogMsg   = document.getElementById('yFogCadeauxMsg');

  if (isRng('yAltarCadeaux') || isRng('yFogCadeaux')) {
    altarMsg.style.display = 'none';
    fogMsg.style.display = 'none';
    updateYamlPreview();
    return;
  }

  let altarVal = parseInt(altarEl.value, 10) || 1;
  if (altarVal > ALTAR_MAX) {
    altarEl.value = ALTAR_MAX;
    altarVal = ALTAR_MAX;
    altarMsg.textContent = `Capped at ${ALTAR_MAX} — altar cost can’t exceed ⌊666 ÷ 5⌋`;
    altarMsg.style.display = 'block';
  } else if (altarVal < 1) {
    altarEl.value = 1;
    altarVal = 1;
    altarMsg.textContent = 'Minimum value is 1';
    altarMsg.style.display = 'block';
  } else {
    altarMsg.style.display = 'none';
  }

  const fogMin = altarVal * 5;
  fogEl.min = fogMin;
  let fogVal = parseInt(fogEl.value, 10) || fogMin;
  if (fogVal < fogMin) {
    fogEl.value = fogMin;
    fogMsg.textContent = `Raised to ${fogMin} — must be ≥ 5 × altar cost (${altarVal})`;
    fogMsg.style.display = 'block';
  } else if (fogVal > FOG_MAX) {
    fogEl.value = FOG_MAX;
    fogMsg.textContent = `Capped at ${FOG_MAX}`;
    fogMsg.style.display = 'block';
  } else {
    fogMsg.style.display = 'none';
  }
  updateYamlPreview();
}

// Cadeaux Gated Content only means anything when Cadeaux Key Items
// (insanity) is also on — Cadeaux isn't an AP-tracked item at all
// otherwise, so the Fogometers Light Soul location's gate can never be
// satisfied and there'd be nothing to turn on. Grey out + force-uncheck
// (and un-randomize) yCadeauxGatedContent whenever yInsanity is
// deterministically off; leave it alone if yInsanity is randomized
// per-seed (its resolved value isn't known here, so we don't second-guess
// it — the backend already treats cadeaux_gated_content as a no-op
// whenever insanity actually resolves off for that seed).
function syncCadeauxGatedDependency() {
  const cgcEl    = document.getElementById('yCadeauxGatedContent');
  const cgcBtn   = document.getElementById('yCadeauxGatedContentRng');
  const insanityOff = !isRng('yInsanity') && !document.getElementById('yInsanity').checked;

  if (insanityOff) {
    applyRng('yCadeauxGatedContent', false);
    cgcEl.checked = false;
  }
  cgcEl.disabled = insanityOff || isRng('yCadeauxGatedContent');
  if (cgcBtn) cgcBtn.disabled = insanityOff;
  updateYamlPreview();
}

// Cadeaux Bundle Size only means anything when Cadeaux Key Items (insanity)
// is also on — cadeaux locations don't exist as AP checks at all otherwise,
// so there's nothing to bundle. Grey out (but don't force a value change —
// 1 is a harmless no-bundling default either way, same reasoning as
// syncDeathLinkThresholdDependency() below) whenever yInsanity is
// deterministically off; leave alone if yInsanity is randomized per-seed,
// same reasoning as syncCadeauxGatedDependency() above.
function syncCadeauxBundleDependency() {
  const cbsEl  = document.getElementById('yCadeauxBundleSize');
  const cbsBtn = document.getElementById('yCadeauxBundleSizeRng');
  const insanityOff = !isRng('yInsanity') && !document.getElementById('yInsanity').checked;

  cbsEl.disabled = insanityOff || isRng('yCadeauxBundleSize');
  if (cbsBtn) cbsBtn.disabled = insanityOff;
  updateYamlPreview();
}

// Death Link Threshold only means anything when Death Link itself is on.
// Grey out (but don't force a value change — 1 is a harmless default either
// way) whenever yDeathLink is deterministically off; leave alone if
// yDeathLink is randomized per-seed, same reasoning as
// syncCadeauxGatedDependency() above.
function syncDeathLinkThresholdDependency() {
  const dltEl  = document.getElementById('yDeathLinkThreshold');
  const dltBtn = document.getElementById('yDeathLinkThresholdRng');
  const deathLinkOff = !isRng('yDeathLink') && !document.getElementById('yDeathLink').checked;

  dltEl.disabled = deathLinkOff || isRng('yDeathLinkThreshold');
  if (dltBtn) dltBtn.disabled = deathLinkOff;
  updateYamlPreview();
}

// ── Starting Item (start_inventory_from_pool) ─────────────────────────────────
// Reworked 2026-07-22: AP's start_inventory_from_pool genuinely supports
// picking several items at once (unlike the standalone randomizer's
// single-item Starting Item dropdown, gui.py, which this used to mirror
// 1:1 via a single-select + separate bundle checkboxes). Now a single
// <select multiple> — compact (fixed size, scrolls) and replaces both the
// old single-pick dropdown and the 4 bundle checkboxes in one control.
//
// The <option value> must be the exact AP item name from items.py's
// item_table. Fixed 2026-07-24: this used to assume "most are RSC names
// used directly" and set most values to raw RSC_X_* strings, which broke
// silently once items.py's 2026-07-22 friendly-name rework (see that file's
// _UNIQUE_ITEM_RSC_NAMES comment) switched every unique item's real
// item_table key to a human-readable name ("Flambeau", "Baton", etc., with
// "Jacks Schematic"/"Tete de Mort" dropping the apostrophe/accent). Every
// option value below now matches item_table exactly. Retractor/Accumulator/
// Gad Power are still AP's stackable names (not the standalone's per-copy
// RSC_X_RETRACT1/2 etc., since AP tracks these as one name + count) and were
// already correct. RSC_X_MAC10 (standalone's "0.9-SMG") has no entry in
// items.py at all, so it's omitted. Violator's item_table key is "Violator"
// (items.py's AP_ITEM_TO_RSC separately remaps it to RSC_Q_VIOLATOR only at
// patch-write time — the item's own name/identity is unaffected).
// No "all_prisms" option: shuffle_prisms/RSC_X_PRISM isn't an AP item.
//
// STACK_QTY: the 3 AP items that are stackable pickups (rather than
// one-shot key items) always grant their full count when picked — this is
// what the old "All Retractors"/"All Accumulators"/"All Gad Pickups" bundle
// checkboxes did, and there's no real use case for starting with a partial
// stack, so folding that into a plain multi-select pick (instead of adding
// a separate quantity control per item, which would fight the "doesn't
// take up lots of space" goal) keeps this compact.
const STACK_QTY = { 'Retractor': 5, 'Accumulator': 3, 'Gad Power': 3 };

// Reads the current picker state into an AP-ready {item_name: count} object.
function buildStartInventoryPool() {
  const items = {};
  for (const opt of document.getElementById('ySiItem').selectedOptions) {
    if (!opt.value) continue;
    items[opt.value] = STACK_QTY[opt.value] || 1;
  }
  return items;
}

// Native <select multiple> has no visible way to deselect everything short
// of Ctrl-click on each already-picked item one at a time -- easy to miss.
// Explicit clear button instead.
function clearStartingItems() {
  const sel = document.getElementById('ySiItem');
  for (const opt of sel.options) opt.selected = false;
  updateYamlPreview();
}

function yamlBool(id)   { return isRng(id) ? 'random' : (document.getElementById(id).checked ? 'true' : 'false'); }
function yamlNum(id)    { return isRng(id) ? 'random' : document.getElementById(id).value; }
// Quoted deliberately (2026-08-06 fix): several <select> option values here
// are literal YAML 1.1 boolean keywords ("off", and in principle "on"/"yes"/
// "no" if ever added) — written unquoted, PyYAML's safe_load() (both this
// GUI's own importYaml()/import_yaml() round-trip AND AP's real generation-
// time YAML loader) parses bare `off` as the Python bool False, not the
// string "off". Choice.from_any() then can't match it to any option name
// (str(False).lower() == "false", not "off") — this was the root cause of
// "Soul Logic Buffer: off doesn't survive save/load," and the identical bug
// silently existed for Entrance Mode's own "off" default too. Quoting keeps
// the value a real YAML string no matter what word it happens to be.
function yamlChoice(id) { return "'" + document.getElementById(id).value + "'"; }  // 'random' lives as a real <option> here
// Same quoting fix, for the rng-btn-driven Choice selects (soul_threshold_mode,
// soul_logic_buffer, trap_bonus_mode) — these don't use yamlChoice() above
// since "random" is represented via the rng button/isRng(), not a literal
// <option>, but need the exact same quoting for their real option values.
function yamlChoiceRng(id) { return isRng(id) ? 'random' : "'" + document.getElementById(id).value + "'"; }
// AP's death_penalty is in "tenths" (10 = -1000 HP/death) — the slider is
// shown in the standalone randomizer's more intuitive x1000-per-step scale
// (0-10, half-steps) and converted here: displayed 0.5 -> AP value 5.
function yamlDeathPenalty() {
  if (isRng('yDeathPenalty')) return 'random';
  return Math.round(parseFloat(document.getElementById('yDeathPenalty').value) * 10);
}
// Same tenths convention as death_penalty: displayed 2.0x -> AP value 20.
function yamlSprintMultiplier() {
  if (isRng('ySprintMultiplier')) return 'random';
  return Math.round(parseFloat(document.getElementById('ySprintMultiplier').value) * 10);
}
function buildYaml() {
  const name = document.getElementById('playerName').value.trim() || 'Player';
  const description = document.getElementById('playerDescription').value.trim();
  const lines = [];
  lines.push('name: ' + name);
  if (description) lines.push('description: ' + description);
  lines.push('game: Shadow Man Remastered');
  lines.push('Shadow Man Remastered:');
  const kv = (k, v) => lines.push('  ' + k + ': ' + v);

  kv('gate_preset', yamlChoice('yGatePreset'));
  kv('max_gate_sl', yamlNum('yMaxGateSl'));
  kv('open_gates_n', yamlNum('yOpenGatesN'));
  kv('shuffle_weapons', yamlBool('yShuffleWeapons'));
  kv('shuffle_lore', yamlBool('yShuffleLore'));
  kv('shuffle_bonus', yamlBool('yShuffleBonus'));
  kv('shuffle_enemies', yamlBool('yShuffleEnemies'));
  kv('enemy_mode', yamlChoice('yEnemyMode'));
  kv('enemy_mix_movement', yamlBool('yEnemyMixMovement'));
  kv('enemy_uncap_counts', yamlBool('yEnemyUncapCounts'));
  kv('shuffle_true_forms', yamlBool('yShuffleTrueForms'));
  kv('shuffle_ambients', yamlBool('yShuffleAmbients'));
  kv('ambient_mode', 'global');  // not exposed in the GUI — left at its default
  kv('shuffle_music', yamlBool('yShuffleMusic'));
  kv('shuffle_voices', yamlBool('yShuffleVoices'));
  kv('shuffle_weapons_sfx', yamlBool('yShuffleWeaponsSfx'));
  kv('shuffle_enemies_sfx', yamlBool('yShuffleEnemiesSfx'));
  kv('shuffle_sky', yamlBool('yShuffleSky'));
  kv('entrance_mode', yamlChoice('yEntranceMode'));
  kv('piston_combos', yamlBool('yPistonCombos'));
  kv('deadside_guns', yamlBool('yDeadsideGuns'));
  kv('progression_balancing', yamlNum('yProgBalance'));
  kv('insanity', yamlBool('yInsanity'));
  kv('cadeaux_bundle_size', yamlNum('yCadeauxBundleSize'));
  kv('starting_health', yamlNum('yStartingHealth'));
  kv('altar_health_grant', yamlNum('yAltarHealth'));
  kv('altar_cadeaux_required', yamlNum('yAltarCadeaux'));
  kv('fogometers_cadeaux_required', yamlNum('yFogCadeaux'));
  kv('cadeaux_gated_content', yamlBool('yCadeauxGatedContent'));
  kv('death_penalty', yamlDeathPenalty());
  kv('sprint_multiplier', yamlSprintMultiplier());
  kv('soul_threshold_mode', yamlChoiceRng('ySoulThresholdMode'));
  kv('soul_logic_buffer', yamlChoiceRng('ySoulLogicBuffer'));
  kv('trap_bonus_count', yamlNum('yTrapBonusCount'));
  kv('trap_bonus_mode', yamlChoiceRng('yTrapBonusMode'));
  kv('trap_bonus_duration', yamlNum('yTrapBonusDuration'));
  kv('trap_bonus_secrets_enabled', yamlBool('yTrapBonusSecretsEnabled'));
  kv('trap_bonus_health_enabled', yamlBool('yTrapBonusHealthEnabled'));
  kv('trap_bonus_voodoo_enabled', yamlBool('yTrapBonusVoodooEnabled'));
  kv('trap_bonus_ammo_enabled', yamlBool('yTrapBonusAmmoEnabled'));
  kv('death_link', yamlBool('yDeathLink'));
  kv('death_link_threshold', yamlNum('yDeathLinkThreshold'));

  const startPool = buildStartInventoryPool();
  const startKeys = Object.keys(startPool);
  if (startKeys.length === 0) {
    kv('start_inventory_from_pool', '{}');
  } else {
    lines.push('  start_inventory_from_pool:');
    startKeys.forEach(k => lines.push('    ' + k + ': ' + startPool[k]));
  }

  return lines.join('\n') + '\n';
}

// ── Live "Checks" readout (2026-08-06) ──────────────────────────────────────
// See _Api.get_check_base_counts()'s own docstring (Python side) for the
// full derivation. Fetched once from the Python backend (pure data-module
// counts, doesn't need a real generation run) and cached — only 4 fields
// (Insanity/Cadeaux Bundle Size/Fog Door Check/Trap Bonus Count) ever
// change the total, so recomputing it live is cheap arithmetic, no new
// API round trip per keystroke.
let CHECK_BASE = null;

async function loadCheckBase() {
  try {
    const r = await window.pywebview.api.get_check_base_counts();
    if (r && r.ok) CHECK_BASE = r;
  } catch (e) {
    // Frozen build missing fill.py, or called before pywebview's ready —
    // leave CHECK_BASE null, updateCheckEstimate() shows "—" for that case.
  }
  updateCheckEstimate();
}

function updateCheckEstimate() {
  const el = document.getElementById('checkEstimate');
  const noteEl = document.getElementById('checkEstimateNote');
  if (!el) return;
  if (!CHECK_BASE) { el.textContent = '—'; if (noteEl) noteEl.textContent = ''; return; }

  const insanityOn      = document.getElementById('yInsanity').checked;
  const bundleSize       = Math.max(1, parseInt(document.getElementById('yCadeauxBundleSize').value, 10) || 1);
  const gatedContentOn   = document.getElementById('yCadeauxGatedContent').checked;
  const trapCount        = Math.max(0, parseInt(document.getElementById('yTrapBonusCount').value, 10) || 0);

  let total = CHECK_BASE.base;
  total += insanityOn ? Math.ceil(CHECK_BASE.cadeaux_total / bundleSize) : 0;
  total += Math.min(trapCount, CHECK_BASE.barrel_candidates);
  if (!gatedContentOn) total -= CHECK_BASE.gated_bonus;

  el.textContent = total.toLocaleString();

  // Reads each field's CURRENT value regardless of its own rng-btn state
  // (simplest, most legible option — computing a real min/max range across
  // 4 independently-randomizable fields would need each slider's own
  // min/max attributes and isn't worth the complexity for a live estimate).
  // Flag it instead so the number is clearly labeled as approximate.
  const varies = ['yInsanity', 'yCadeauxBundleSize', 'yCadeauxGatedContent', 'yTrapBonusCount'].some(isRng);
  if (noteEl) noteEl.textContent = varies ? 'varies — some settings are random' : '';
}

function updateYamlPreview() {
  document.getElementById('yamlPreview').value = buildYaml();
  updateCheckEstimate();
}

async function copyYaml() {
  // navigator.clipboard.writeText() is unreliable inside pywebview's
  // embedded webview (WebView2/CEF) — its promise can reject even when the
  // OS clipboard was actually written, which made this show "Copy failed"
  // on successful copies. Routed through the Python backend instead, which
  // reports what actually happened.
  const ok = await window.pywebview.api.copy_to_clipboard(document.getElementById('yamlPreview').value);
  if (ok) {
    setYamlStatus('Copied to clipboard.', 'ok');
  } else {
    setYamlStatus('Copy failed — select the text and copy manually.', 'err');
  }
}

async function saveYaml() {
  const name = (document.getElementById('playerName').value.trim() || 'Player').replace(/[^\w\-]+/g, '_');
  const result = await window.pywebview.api.save_yaml(document.getElementById('yamlPreview').value, name + '.yaml');
  if (result) setYamlStatus('Saved to ' + result, 'ok');
}

function setYamlStatus(txt, cls) {
  const el = document.getElementById('yamlStatus');
  el.textContent = txt;
  el.className = 'status' + (cls ? ' ' + cls : '');
}

// ── Import YAML ──────────────────────────────────────────────────────────────
// Loads an existing player YAML (hand-written, exported from a previous
// session of this GUI, or edited from the AP website's generator) and
// restores every Tab 1 field from it, so you can tweak a couple of settings
// instead of rebuilding from scratch. Parsing happens Python-side
// (import_yaml() below, via PyYAML — already a project dependency, see
// requirements.txt) rather than in JS, both because there's no YAML parser
// loaded client-side and because the file dialog itself has to go through
// pywebview's API either way.
//
// Two field shapes to restore, matching buildYaml()'s own kv() calls:
//   1. Fields with a real per-field 🎲 rng-btn (every checkbox/slider/number
//      field, including the 6 Cosmetic Shuffles checkboxes — those got their
//      own dice buttons added 2026-07-21, previously the only checkboxes in
//      this tab without one, an inconsistency rather than a deliberate
//      omission) — "random" -> applyRng(id, true); a concrete value ->
//      applyRng(id, false) + set the value.
//   2. Four <select> fields (gate_preset/entrance_mode/enemy_mode/insanity)
//      that have "random" as a literal <option> but NO rng-btn element at
//      all — just set .value directly; toggling a nonexistent Rng button
//      would silently no-op.
// `notes` below stays as a general warnings collector for anything an
// import can't cleanly represent (currently nothing hits it, since every
// GUI-exposed field has a home in shape 1 or 2 — kept for whatever gets
// added next without an rng-btn).
function setRngField(id, val) {
  if (val === undefined) return;
  if (val === 'random') { applyRng(id, true); return; }
  applyRng(id, false);
  const el = document.getElementById(id);
  if (!el) return;
  if (el.type === 'checkbox') el.checked = !!val;
  else {
    el.value = val;
    const valEl = document.getElementById(id + 'Val');
    if (valEl) valEl.textContent = el.value;
  }
}
async function importYaml() {
  const result = await window.pywebview.api.import_yaml();
  if (!result) return;
  if (!result.ok) {
    if (result.error) setYamlStatus('Import failed: ' + result.error, 'err');
    return;  // user cancelled the file dialog, or a real error was already reported above
  }

  const s = result.settings || {};
  const notes = [];

  // Backward-compat for YAML files saved before the 2026-08-06 quoting fix
  // (see yamlChoice()/yamlChoiceRng() above): unquoted `off` was written for
  // entrance_mode/soul_threshold_mode/soul_logic_buffer (the 3 Choice fields
  // whose "off" option is a literal YAML 1.1 boolean keyword), so PyYAML's
  // safe_load() silently parsed it as the Python bool False instead of the
  // string "off" on that older export. Coerce it back so an already-saved
  // file from before this fix still imports its "off" setting correctly.
  ['entrance_mode', 'soul_threshold_mode', 'soul_logic_buffer'].forEach(k => {
    if (s[k] === false) s[k] = 'off';
  });

  if (result.name) document.getElementById('playerName').value = result.name;
  document.getElementById('playerDescription').value = result.description || '';

  // Shape 2: literal-"random"-option selects, no rng-btn to touch.
  if (s.gate_preset    !== undefined) document.getElementById('yGatePreset').value    = s.gate_preset;
  if (s.entrance_mode  !== undefined) document.getElementById('yEntranceMode').value  = s.entrance_mode;
  if (s.enemy_mode     !== undefined) document.getElementById('yEnemyMode').value     = s.enemy_mode;

  // Shape 1: rng-btn fields — sliders/numbers/selects.
  ['max_gate_sl:yMaxGateSl', 'open_gates_n:yOpenGatesN', 'progression_balancing:yProgBalance',
   'starting_health:yStartingHealth', 'altar_health_grant:yAltarHealth',
   'altar_cadeaux_required:yAltarCadeaux', 'fogometers_cadeaux_required:yFogCadeaux',
   'cadeaux_bundle_size:yCadeauxBundleSize',
   'soul_threshold_mode:ySoulThresholdMode', 'soul_logic_buffer:ySoulLogicBuffer',
   'death_link_threshold:yDeathLinkThreshold',
   'trap_bonus_count:yTrapBonusCount', 'trap_bonus_mode:yTrapBonusMode',
   'trap_bonus_duration:yTrapBonusDuration',
  ].forEach(pair => { const [key, id] = pair.split(':'); setRngField(id, s[key]); });

  // Shape 1: rng-btn checkboxes.
  ['shuffle_weapons:yShuffleWeapons',
   'shuffle_lore:yShuffleLore', 'shuffle_bonus:yShuffleBonus', 'shuffle_enemies:yShuffleEnemies',
   'enemy_mix_movement:yEnemyMixMovement', 'enemy_uncap_counts:yEnemyUncapCounts',
   'shuffle_true_forms:yShuffleTrueForms', 'piston_combos:yPistonCombos',
   'deadside_guns:yDeadsideGuns',
   'insanity:yInsanity', 'cadeaux_gated_content:yCadeauxGatedContent',
   'death_link:yDeathLink',
   'shuffle_ambients:yShuffleAmbients', 'shuffle_music:yShuffleMusic',
   'shuffle_voices:yShuffleVoices', 'shuffle_weapons_sfx:yShuffleWeaponsSfx',
   'shuffle_enemies_sfx:yShuffleEnemiesSfx', 'shuffle_sky:yShuffleSky',
   'trap_bonus_secrets_enabled:yTrapBonusSecretsEnabled', 'trap_bonus_health_enabled:yTrapBonusHealthEnabled',
   'trap_bonus_voodoo_enabled:yTrapBonusVoodooEnabled', 'trap_bonus_ammo_enabled:yTrapBonusAmmoEnabled',
  ].forEach(pair => { const [key, id] = pair.split(':'); setRngField(id, s[key]); });

  // death_penalty needs its own handling: AP stores tenths (10 = -1000
  // HP/death), the slider shows the more intuitive x1000-per-step scale —
  // same conversion as yamlDeathPenalty() above, inverted.
  if (s.death_penalty !== undefined) {
    if (s.death_penalty === 'random') { applyRng('yDeathPenalty', true); }
    else { applyRng('yDeathPenalty', false); document.getElementById('yDeathPenalty').value = parseFloat(s.death_penalty) / 10; }
  }

  // sprint_multiplier: same tenths convention as death_penalty above.
  if (s.sprint_multiplier !== undefined) {
    if (s.sprint_multiplier === 'random') { applyRng('ySprintMultiplier', true); }
    else { applyRng('ySprintMultiplier', false); document.getElementById('ySprintMultiplier').value = parseFloat(s.sprint_multiplier) / 10; }
  }

  // start_inventory_from_pool: a nested {item_name: count} mapping, not a
  // scalar — doesn't fit the shape-1/shape-2 patterns above. Reworked
  // 2026-07-22 alongside the picker's move to <select multiple>: just
  // select every option whose value is a key in the pool, since the
  // control can now represent any combination directly (no more
  // bundle-vs-individual unpacking needed).
  {
    const siSelect = document.getElementById('ySiItem');
    for (const opt of siSelect.options) opt.selected = false;
    if (s.start_inventory_from_pool && typeof s.start_inventory_from_pool === 'object') {
      const pool = { ...s.start_inventory_from_pool };
      const known = new Set(Array.from(siSelect.options).map(o => o.value));
      for (const key of Object.keys(pool)) {
        if (known.has(key)) {
          Array.from(siSelect.options).find(o => o.value === key).selected = true;
        } else {
          notes.push(`start_inventory_from_pool item "${key}" isn't in this picker's list — edit the YAML directly to keep it.`);
        }
      }
    }
  }

  // Re-run the side-effect handlers so disabled/greyed-out sub-controls and
  // slider labels stay consistent with whatever we just restored.
  onGatePresetChange();
  onEnemiesChange();
  refreshDeathPenaltyLabel();
  refreshSprintMultiplierLabel();
  syncCadeauxConstraints();
  syncCadeauxGatedDependency();
  syncCadeauxBundleDependency();
  syncDeathLinkThresholdDependency();
  updateYamlPreview();

  setYamlStatus(
    notes.length ? ('Imported — ' + notes.join('; ')) : 'Imported settings from YAML.',
    notes.length ? 'err' : 'ok',
  );
}

// ── Tab 2: Apply AP Seed ────────────────────────────────────────────────────

async function browsePatchFile() {
  const current = document.getElementById('patchFile').value.trim();
  const result = await window.pywebview.api.browse_patch_file(current);
  if (result) document.getElementById('patchFile').value = result;
}

async function browseApplyGameDir() {
  const current = document.getElementById('applyGameDir').value.trim();
  const result = await window.pywebview.api.browse_dir(current);
  if (result) document.getElementById('applyGameDir').value = result;
}

function appendApplyOutput(txt) {
  const el = document.getElementById('applyOutput');
  el.textContent += txt;
  el.scrollTop = el.scrollHeight;
}
function setApplyStatus(txt, cls) {
  const el = document.getElementById('applyStatus');
  el.textContent = txt;
  el.className = 'status' + (cls ? ' ' + cls : '');
}
function setApplyBusy(busy) {
  document.getElementById('applyBtn').disabled = busy;
}

async function applySeed() {
  const patchFile = document.getElementById('patchFile').value.trim();
  const gameDir   = document.getElementById('applyGameDir').value.trim();
  if (!patchFile) { appendApplyOutput('⚠ Select a .apshadowman file first.\n'); return; }
  if (!gameDir)   { appendApplyOutput('⚠ Select your Shadow Man Remastered game directory first.\n'); return; }
  const valid = await window.pywebview.api.validate_dir(gameDir);
  if (!valid) { appendApplyOutput("⚠ That folder doesn't look like a Shadow Man Remastered install (no .kpf files found).\n"); return; }

  document.getElementById('applyOutput').textContent = '';
  // Launch buttons stay visible throughout (2026-08-04) -- no longer hidden
  // here while an apply is in progress.
  setApplyStatus('Applying…'); setApplyBusy(true);
  window.pywebview.api.apply_seed({ patchFile: patchFile, gameDir: gameDir });
}

function onApplyDone(rc) {
  setApplyBusy(false);
  if (rc === 0) {
    setApplyStatus('✓ Done. Mod installed and exe patched.', 'ok');
  } else {
    setApplyStatus('✗ Apply exited with code ' + rc + '.', 'err');
  }
}
function onApplyError(msg) {
  setApplyBusy(false);
  appendApplyOutput('[Error: ' + msg + ']\n');
  setApplyStatus('Error launching apply_ap_seed.', 'err');
}
function launchGame() {
  const gameDir = document.getElementById('applyGameDir').value.trim();
  if (!gameDir) { appendApplyOutput('⚠ Select your Shadow Man Remastered game directory above first.\n'); return; }
  window.pywebview.api.launch_game(gameDir);
}
async function launchGameOnly() {
  const gameDir = document.getElementById('applyGameDir').value.trim();
  if (!gameDir) { appendApplyOutput('⚠ Select your Shadow Man Remastered game directory above first.\n'); return; }
  const ok = await window.pywebview.api.launch_game_only(gameDir);
  if (!ok) appendApplyOutput("⚠ Couldn't find thoth_x64_patched.exe in that folder — opened it instead so you can check.\n");
}
async function launchClientOnly() {
  const ok = await window.pywebview.api.launch_client_only();
  if (!ok) appendApplyOutput("⚠ Couldn't find the Archipelago checkout/venv to start the client from. Check the ap_dir setting.\n");
}
function openFolder() {
  const gameDir = document.getElementById('applyGameDir').value.trim();
  if (!gameDir) { appendApplyOutput('⚠ Select your Shadow Man Remastered game directory above first.\n'); return; }
  window.pywebview.api.open_folder(gameDir);
}

window.addEventListener('pywebviewready', async () => {
  const dir = await window.pywebview.api.get_default_dir();
  if (dir) document.getElementById('applyGameDir').value = dir;
  onGatePresetChange();
  onEnemiesChange();
  syncCadeauxGatedDependency();
  syncCadeauxBundleDependency();
  syncDeathLinkThresholdDependency();
  updateYamlPreview();
  loadCheckBase();
});
</script>
</body>
</html>
"""
_HTML = _HTML.replace("__COMPANION_VERSION__", COMPANION_VERSION)


class _Api:
    def __init__(self):
        self._process: "subprocess.Popen[str] | None" = None
        self._window: "webview.Window | None" = None

    def _set_window(self, w: "webview.Window") -> None:
        self._window = w

    # ── Shared with gui.py's patterns ────────────────────────────────────────

    def get_default_dir(self) -> str:
        saved = _load_prefs().get("game_dir", "")
        if saved and _looks_like_install(Path(saved)):
            return saved
        return str(DEFAULT_GAME_DIR) if _looks_like_install(DEFAULT_GAME_DIR) else ""

    def validate_dir(self, game_dir: str) -> bool:
        return _looks_like_install(Path(game_dir))

    def get_check_base_counts(self) -> dict:
        """
        Powers the live "Checks" readout on the Generate YAML tab (added
        2026-08-06, next to Description). Every GUI setting except Insanity
        ("Cadeaux Key Items"), Cadeaux Bundle Size, Fog Door Check (Cadeaux
        Gated Content), and Trap/Bonus Count only changes WHERE items land,
        never how many real AP locations exist for this seed — so the total
        check count is a small, exact arithmetic formula (computed in JS,
        see updateCheckEstimate()) over those 4 knobs plus the fixed
        constants returned here, pulled straight from this repo's own
        fill.py — the canonical location-data source per CLAUDE.md's Key
        Files table, the same data the AP world's locations.py/regions.py
        are generated from, so these counts can't drift from what a real
        seed actually produces.

        base              = every CHECKABLE_LOCS category except cadeaux/
                             barrel, plus FIXED_SOUL_LOCS (boss/true_form —
                             real, player-facing checks even though Fill
                             pre-locks their item rather than randomizing
                             it). Always exist, regardless of GUI settings.
        cadeaux_total     = CHECKABLE_LOCS rows with category == "cadeaux".
                             Insanity off -> 0 of these become real checks;
                             on -> bundled down to ceil(cadeaux_total /
                             cadeaux_bundle_size) representative checks
                             (regions.py's compute_cadeaux_bundle_
                             representatives()).
        barrel_candidates = CHECKABLE_LOCS "barrel" rows restricted to
                             source_file == "quest.rsc" — the only ones
                             ever eligible for Trap/Bonus promotion (see
                             __init__.py's generate_early()).
                             min(trap_bonus_count, this) become real checks.
        gated_bonus       = 1 — the single "bonus"-category location
                             (Fogometers Light Soul) gated behind
                             CADEAUX_666, already counted inside `base`,
                             subtracted back out when Fog Door Check is off
                             (mirrors fill.py's CADEAUX_666_LOCS in the AP
                             world exactly, not just any row that happens
                             to mention CADEAUX_666 — 2 barrel rows share
                             that same gate text but are barrel, already
                             excluded from `base` entirely, and would be
                             double-subtracted if not filtered to "bonus").

        Returns {"ok": False} (never raises) if fill.py can't be imported
        — e.g. a stripped frozen build missing it — so the JS side can
        hide the readout instead of showing a wrong number.
        """
        try:
            import fill as _fill
        except Exception as exc:
            print(f"  [get_check_base_counts] fill.py unavailable: {exc}")
            return {"ok": False}

        cats: dict = {}
        for loc in _fill.CHECKABLE_LOCS:
            cats[loc.category] = cats.get(loc.category, 0) + 1

        base = sum(v for k, v in cats.items() if k not in ("cadeaux", "barrel"))
        base += len(_fill.FIXED_SOUL_LOCS)

        cadeaux_total = cats.get("cadeaux", 0)
        barrel_candidates = sum(
            1 for loc in _fill.CHECKABLE_LOCS
            if loc.category == "barrel" and loc.source_file == "quest.rsc"
        )
        gated_bonus = sum(
            1 for loc in _fill.CHECKABLE_LOCS
            if loc.category == "bonus" and loc.gate_raw and "CADEAUX_666" in loc.gate_raw
        )

        return {
            "ok": True,
            "base": base,
            "cadeaux_total": cadeaux_total,
            "barrel_candidates": barrel_candidates,
            "gated_bonus": gated_bonus,
        }

    def browse_dir(self, current: str) -> "str | None":
        assert self._window is not None
        result = self._window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=current or str(DEFAULT_GAME_DIR),
        )
        return result[0] if result else None

    def _get_ap_dir(self) -> Path:
        saved = _load_prefs().get("ap_dir", "")
        if saved and (Path(saved) / "Launcher.py").exists():
            return Path(saved)
        return DEFAULT_AP_DIR

    def _launch_ap_client(self) -> bool:
        """
        Best-effort: start the Archipelago Launcher's "Shadow Man
        Remastered Client" component (worlds/shadowman/__init__.py's
        registered Component name).

        Passed as a plain argv list straight to subprocess.Popen -- no
        cmd.exe/shell involved at all, so there's no multi-token quoting
        risk here regardless of spaces in the component name.

        (History, 2026-08-04/05: earlier revisions of this method routed
        through "cmd /k" specifically to keep the console window open
        after the process exited, while diagnosing what turned out to be
        a real, unrelated bug -- Windows multiprocessing's "spawn" start
        method re-injects Launcher.py's own sys.argv into the client.py
        child process it creates, so a positional component name passed
        to Launcher.py on the command line was leaking into client.py's
        own --connect/--password/--nogui argument parser as an
        "unrecognized argument." Two quoting-based fixes were tried and
        both were wrong, since quoting was never the actual problem. Once
        the real cause was found and fixed at the source -- client.py's
        launch() now checks multiprocessing.parent_process() and parses
        against an empty argv when running as a spawned child -- the
        client launches cleanly and the console-babysitting workaround
        here is no longer needed. The window still opens (so you can see
        the client's live connection/log output while playing) but now
        just closes on its own when the client exits, like a normal
        console app, instead of being forced to stay open.)

        client.py has its own single-instance guard (a named Windows
        mutex, checked at the very top of client.py's launch()) -- so
        it's always safe to call this even if a client is already
        running from a previous session; the redundant instance just
        prints a warning and exits immediately instead of running two
        copies side by side.

        Returns True/False so a standalone "Launch Client Only" button
        can tell the difference and show a message — the combined
        "Launch Game + Client" button still ignores the return value and
        degrades silently (never block launching the game itself over
        this), same "degrade gracefully" convention client.py's own
        overlay-DLL injection already follows.
        """
        ap_dir = self._get_ap_dir()
        python_exe = ap_dir / ".venv" / "Scripts" / "python.exe"
        launcher = ap_dir / "Launcher.py"
        if not python_exe.exists() or not launcher.exists():
            return False
        try:
            subprocess.Popen(
                [str(python_exe), str(launcher), "Shadow Man Remastered Client"],
                cwd=str(ap_dir),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            return True
        except OSError:
            return False

    def launch_game(self, game_dir: str) -> None:
        exe = Path(game_dir) / "thoth_x64_patched.exe"
        if exe.exists():
            self._launch_ap_client()
            subprocess.Popen([str(exe)], cwd=game_dir)
        else:
            self.open_folder(game_dir)

    def launch_game_only(self, game_dir: str) -> bool:
        """Game only, no client — e.g. testing the standalone (non-AP)
        randomizer's own patch output, or a player who's launching the AP
        client separately (the in-game F10 panel, once built)."""
        exe = Path(game_dir) / "thoth_x64_patched.exe"
        if exe.exists():
            subprocess.Popen([str(exe)], cwd=game_dir)
            return True
        self.open_folder(game_dir)
        return False

    def launch_client_only(self) -> bool:
        """Client only, no game — e.g. the game's already running from
        elsewhere and just needs a client attached, or reconnecting after
        client.py was closed. Returns False if the Archipelago checkout/
        venv couldn't be found, so the button can surface that instead of
        silently doing nothing."""
        return self._launch_ap_client()

    def open_folder(self, game_dir: str) -> None:
        subprocess.Popen(["explorer", game_dir])

    # ── Tab 1: YAML ──────────────────────────────────────────────────────────

    def save_yaml(self, content: str, suggested_name: str) -> "str | None":
        assert self._window is not None
        result = self._window.create_file_dialog(
            webview.FileDialog.SAVE,
            save_filename=suggested_name,
            file_types=("YAML files (*.yaml)", "All files (*.*)"),
        )
        if not result:
            return None
        path = result if isinstance(result, str) else result[0]
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return path

    def import_yaml(self) -> dict:
        """
        Open a file dialog, parse the selected player YAML, and return its
        name + "Shadow Man Remastered" settings block for importYaml() (JS,
        above) to restore into the Generate YAML tab's form fields.

        Always returns a dict — never raises across the pywebview boundary,
        never returns None (a cancelled dialog is {"ok": False, "error":
        None}, distinct from a real parse failure) — so the JS side has one
        simple shape to branch on.

        Falls back to treating the whole top-level dict as the settings
        block when there's no "Shadow Man Remastered" key, mirroring
        patcher.py's own --config loader (`data.get("Shadow Man Remastered",
        data)`, patcher.py ~line 2631 in the standalone repo) — lets a
        pre-existing config.yaml written for that CLI flag import cleanly
        too, not just a proper multi-game AP player YAML.
        """
        assert self._window is not None
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            directory=str(Path.home()),
            file_types=("YAML files (*.yaml;*.yml)", "All files (*.*)"),
        )
        if not result:
            return {"ok": False, "error": None}  # user cancelled — not an error

        path = result[0] if isinstance(result, (list, tuple)) else result
        try:
            data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except Exception as exc:
            return {"ok": False, "error": f"Couldn't parse {Path(path).name}: {exc}"}

        if not isinstance(data, dict):
            return {"ok": False, "error": f"{Path(path).name} doesn't look like a player YAML."}

        game = data.get("game")
        if game and game != "Shadow Man Remastered":
            return {"ok": False, "error": f"This YAML is for {game!r}, not Shadow Man Remastered."}

        settings = data.get("Shadow Man Remastered")
        if not isinstance(settings, dict):
            settings = {k: v for k, v in data.items() if k not in ("name", "game", "description")}

        return {
            "ok": True,
            "name": data.get("name"),
            "description": data.get("description"),
            "settings": settings,
        }

    def copy_to_clipboard(self, text: str) -> bool:
        """
        navigator.clipboard.writeText() in JS is unreliable inside pywebview's
        embedded webview — its promise can reject even when the OS clipboard
        was actually written (a known WebView2/CEF quirk), which showed
        "Copy failed" on copies that had, in fact, succeeded. Tkinter's
        clipboard is a stdlib, dependency-free, cross-platform way to do this
        from the Python side instead, where success/failure is unambiguous.
        """
        try:
            import tkinter as tk
            r = tk.Tk()
            r.withdraw()
            r.clipboard_clear()
            r.clipboard_append(text)
            r.update()  # flush to the OS clipboard before the window is destroyed
            r.destroy()
            return True
        except Exception as exc:
            print(f"  [copy_to_clipboard] failed: {exc}")
            return False

    # ── Tab 2: Apply AP Seed ─────────────────────────────────────────────────

    def browse_patch_file(self, current: str) -> "str | None":
        assert self._window is not None
        start_dir = str(Path(current).parent) if current else str(Path.home())
        result = self._window.create_file_dialog(
            webview.FileDialog.OPEN,
            directory=start_dir,
            file_types=("Archipelago seed files (*.apshadowman)", "All files (*.*)"),
        )
        return result[0] if result else None

    def apply_seed(self, config: dict) -> None:
        if self._process is not None:
            return
        assert self._window is not None

        patch_file = config.get("patchFile", "").strip()
        game_dir   = config.get("gameDir", "").strip()

        if not patch_file or not Path(patch_file).exists():
            self._window.evaluate_js(f"onApplyError({json.dumps('Patch file not found.')})")
            return
        if not game_dir or not _looks_like_install(Path(game_dir)):
            self._window.evaluate_js(f"onApplyError({json.dumps('Game directory does not look like a Shadow Man Remastered install.')})")
            return

        prefs = _load_prefs()
        if prefs.get("game_dir") != game_dir:
            prefs["game_dir"] = game_dir
            _save_prefs(prefs)

        if getattr(sys, "frozen", False):
            cmd = [sys.executable, _APPLY_FLAG]
        else:
            cmd = [sys.executable, str(APPLY_SEED)]
        cmd += [patch_file, "--game-dir", game_dir]

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=env,
                cwd=str(SCRIPT_DIR),
            )
        except Exception as exc:
            self._window.evaluate_js(f"onApplyError({json.dumps(str(exc))})")
            return

        threading.Thread(target=self._reader_thread, daemon=True).start()

    def _reader_thread(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        assert self._window is not None
        try:
            for line in self._process.stdout:
                self._window.evaluate_js(f"appendApplyOutput({json.dumps(line)})")
        finally:
            self._process.wait()
            rc = self._process.returncode
            self._process = None
            self._window.evaluate_js(f"onApplyDone({rc})")


if __name__ == "__main__":
    api = _Api()
    window = webview.create_window(
        "Shadow Man Remastered — Archipelago Companion",
        html=_HTML,
        js_api=api,
        width=1080,
        height=980,
    )
    api._set_window(window)
    webview.start()
