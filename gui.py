"""
Shadow Man Remastered Randomizer — pywebview GUI
Drives patcher.py as a subprocess (dev) or via self-re-launch (frozen exe).

Usage (dev):    python gui.py
Usage (built):  Shadow Man Randomizer.exe
"""

import sys

# ── Frozen patcher-subprocess mode ───────────────────────────────────────────
_PATCHER_FLAG = "--_run-patcher"

if _PATCHER_FLAG in sys.argv:
    # Force UTF-8 stdout so the patcher can print Unicode (✓, ✅ etc.)
    # without hitting the Windows cp1252 codec limit.
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
    sys.argv = ["patcher.py"] + [a for a in sys.argv[1:] if a != _PATCHER_FLAG]

    try:
        runpy.run_path(str(_base / "patcher.py"), run_name="__main__")
    except SystemExit:
        pass
    sys.exit(0)

# ── Normal GUI mode ───────────────────────────────────────────────────────────

import json
import sys
import os
import queue
import random
import subprocess
import threading
from pathlib import Path
from constants import STARTING_ITEM_POOL

import webview
import patcher

if getattr(sys, 'frozen', False):
    # If the app is running as a bundle (exe)
    bundle_dir = sys._MEIPASS
    if bundle_dir not in sys.path:
        sys.path.append(bundle_dir)

SCRIPT_DIR = Path(__file__).resolve().parent
PATCHER    = SCRIPT_DIR / "patcher.py"
DEFAULT_GAME_DIR = SCRIPT_DIR.parent
PREFS_FILE = SCRIPT_DIR / "gui_prefs.json"


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
  /* True equal-width two-column grid; each cell is only as tall as its content */
  .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 10px; align-items: start; }
  .two-col .card { margin-bottom: 0; }

  .row { display: flex; align-items: center; gap: 8px; }
  .row + .row { margin-top: 8px; }

  input[type=text], input[type=number], textarea {
    -webkit-user-select: text; user-select: text;
  }
  input[type=text], input[type=number], select {
    background: #222226; border: 1px solid var(--border);
    border-radius: 5px; color: var(--text);
    font-size: 12px; padding: 5px 9px; outline: none; transition: border-color .15s;
  }
  input[type=text]:focus, input[type=number]:focus, select:focus { border-color: var(--accent); }
  input.dir-input   { flex: 1; }
  input.seed-input  { width: 150px; }
  input.maxsl-input { width: 90px; }  /* kept for reference; slider replaces it */
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
  .btn-dice    { background: #1a2f4a; color: var(--blue); border: 1px solid #243a5e; }
  .rng-btn {
    background: transparent; border: 1px solid var(--dim); border-radius: 3px;
    color: var(--dim); cursor: pointer; font-size: 11px; padding: 1px 5px;
    line-height: 1.4; flex-shrink: 0;
  }
  .rng-btn:hover { border-color: var(--muted); color: var(--muted); }
  .rng-btn.rng-active { border-color: #a78bfa; color: #a78bfa; background: rgba(167,139,250,0.1); }
  .btn-run     { background: var(--accent2); color: #fff; font-size: 13px; padding: 7px 22px; }
  .btn-restore { background: #3a1a1a; color: #e8a0a0; border: 1px solid #522020; font-size: 13px; padding: 7px 22px; }

  /* Checkboxes */
  .check-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 10px; }
  .check-label { display: flex; align-items: center; gap: 7px; padding: 4px 0; cursor: pointer; color: #ccc; }
  .check-label:hover { color: #fff; }
  input[type=checkbox] { width: 14px; height: 14px; accent-color: #4a7a5a; cursor: pointer; flex-shrink: 0; }

  /* Cosmetic row */
  .cosm-row { display: flex; gap: 20px; flex-wrap: wrap; }

  /* Divider */
  .divider { border: none; border-top: 1px solid var(--border); margin: 10px 0; }

  /* Enemy mode */
  .enemy-row { display: flex; align-items: center; gap: 10px; }
  .enemy-row .lbl { color: var(--muted); font-size: 11px; white-space: nowrap; }
  .enemy-row select { flex: 1; min-width: 0; }

  /* Slider */
  input[type=range] {
    -webkit-appearance: none; appearance: none;
    width: 120px; height: 4px; border-radius: 2px;
    background: #333; outline: none; cursor: pointer;
  }
  input[type=range]::-webkit-slider-thumb {
    -webkit-appearance: none; width: 14px; height: 14px;
    border-radius: 50%; background: var(--accent2); cursor: pointer;
  }
  .slider-val { color: var(--text); font-size: 12px; min-width: 24px; text-align: right; }

  /* Tooltip */
  .tip {
    position: relative; display: inline-flex;
    align-items: center; margin-left: 4px; cursor: help;
  }
  .tip-icon {
    display: inline-flex; align-items: center; justify-content: center;
    width: 13px; height: 13px; border-radius: 50%;
    background: #2c2c30; color: #666; font-size: 9px; font-weight: 700;
    border: 1px solid #3a3a3e; flex-shrink: 0; line-height: 1;
    transition: background .12s, color .12s;
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
    /* Reset inherited typography from .card-title (bold, uppercase, tracking) */
    font-weight: normal; text-transform: none; letter-spacing: normal; font-style: normal;
  }
  .tip:hover .tip-box { opacity: 1; }
  /* Anchor tooltip to the right edge — use on tips near the right side of the window */
  .tip.anchor-right .tip-box { left: auto; right: 0; transform: none; }
  /* Anchor tooltip below the icon — use on tips near the top of the window */
  .tip.anchor-bottom .tip-box { bottom: auto; top: calc(100% + 7px); }

  /* Status / terminal */
  .gate-desc { color: var(--muted); font-size: 11px; font-style: italic; }
  .actions { display: flex; gap: 10px; margin-bottom: 10px; }
  .terminal {
    background: #0c0c0e; border: 1px solid var(--border); border-radius: 8px;
    font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
    font-size: 11px; height: 140px; overflow-y: auto;
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
  .btn-launch { background: #1a3a5c; color: #7ab3e0; border: 1px solid #2a5080; font-size: 13px; padding: 7px 22px; }
  .post-run { display: none; align-items: center; gap: 8px; }
  .post-run.visible { display: flex; }
  .progress-bar {
    height: 3px; border-radius: 2px; background: #1e1e22;
    overflow: hidden; margin-bottom: 8px; display: none;
  }
  .progress-bar.active { display: block; }
  .progress-bar::after {
    content: ''; display: block; height: 100%; width: 35%;
    background: var(--accent2); border-radius: 2px;
    animation: bar-slide 1.4s ease-in-out infinite;
  }
  @keyframes bar-slide {
    0%   { margin-left: -35%; }
    100% { margin-left: 135%; }
  }
</style>
</head>
<body>

<div class="header">
  <h1>Shadow Man Remastered Randomizer <span style="font-size:11px;font-weight:400;color:var(--muted);margin-left:6px">v1.1.6</span></h1>
  <p>Generates a guaranteed-beatable seed &mdash; drop the .kpf in your mods folder and play.</p>
</div>

<!-- Row 1: Game Dir + Seed -->
<div class="card-row" style="align-items:stretch">
  <div class="card" style="flex:0 0 300px;display:flex;flex-direction:column;justify-content:center;background:#16161a;border-color:#242428">
    <div class="card-title">
      Game Directory
      <span class="tip anchor-bottom" style="vertical-align:middle"><span class="tip-icon">?</span><span class="tip-box">Point this to your Shadow Man Remastered install folder — the one containing <b>thoth_x64.exe</b> and the game&rsquo;s .kpf files. Use Browse&hellip; or paste the path directly. The randomizer drops its output .kpf into the <b>mods/</b> folder inside that directory and never modifies your original files.</span></span>
    </div>
    <div class="row">
      <input type="text" id="gameDir" class="dir-input" placeholder="Path to Shadow Man Remastered&hellip;">
      <button class="btn-ghost" onclick="browseDir()">Browse&hellip;</button>
    </div>
  </div>
  <div class="card" style="flex:1;background:#16161a;border-color:#242428;display:grid;grid-template-columns:auto 1fr;gap:0 20px;align-items:start">
    <div>
      <div class="card-title">
        Seed
        <span class="tip anchor-bottom" style="vertical-align:middle"><span class="tip-icon">?</span><span class="tip-box">A number that controls everything random in the run. To reproduce a run exactly, you need the same seed <b>and</b> the same settings — changing any option will produce a different result even with the same seed. Leave blank to get a fresh random seed each time you click Run.</span></span>
      </div>
      <div class="row">
        <input type="number" id="seed" class="seed-input" placeholder="random" min="1000000000" max="9999999999">
        <button class="btn-dice" onclick="randomizeSeed()">&#127922;&ensp;Randomize</button>
      </div>
      <div class="hint" style="margin-top:4px">Leave blank for a new random seed each run.</div>
    </div>
    <div>
      <div class="card-title" style="display:flex;align-items:center;gap:6px;margin-bottom:6px">
        Settings String
        <span class="tip anchor-bottom" style="vertical-align:middle"><span class="tip-icon">?</span><span class="tip-box">Encodes all current settings (not the seed or game directory) into a compact string you can save or share. Paste a string and click Import — or press Enter — to restore a saved configuration.</span></span>
        <span style="flex:1"></span>
        <button class="btn-ghost" onclick="exportSettings()" style="white-space:nowrap;font-size:11px;padding:2px 8px">&#128203;&ensp;Copy</button>
        <button class="btn-ghost" onclick="importSettings()" style="white-space:nowrap;font-size:11px;padding:2px 8px">&#8628;&ensp;Import</button>
        <button class="btn-ghost" onclick="if(confirm('Reset all randomizer settings to defaults?')){applyConfig(FIELD_DEFAULTS);document.getElementById('settingsMsg').textContent='reset';setTimeout(()=>document.getElementById('settingsMsg').textContent='',1500)}" style="white-space:nowrap;font-size:11px;padding:2px 8px">&#8635;&ensp;Reset</button>
        <span id="settingsMsg" style="font-size:11px;min-width:50px"></span>
      </div>
      <input type="text" id="settingsString" placeholder="Paste a settings string to restore&hellip;"
             style="width:100%;font-family:'Cascadia Code','Consolas','Courier New',monospace;font-size:11px"
             onkeydown="if(event.key==='Enter')importSettings()">
    </div>
  </div>
</div>

<!-- Row 2: Gameplay+Starting Item (left) | Coffin Gates+Progression (centre) | Gameplay Tuning (right) -->
<div style="display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1.0fr) minmax(0,1.0fr);gap:10px;margin-bottom:10px;align-items:stretch">


  <!-- Left: Gameplay checkboxes + Starting Item -->
  <div class="card" style="flex:1 ;min-width:280px">
    <div class="card-title">Gameplay — Shuffle</div>
    <div class="check-grid">
      <label class="check-label">
        <input type="checkbox" id="shuffleKeyItems" class="default-on" checked>
        Key Items
        <button class="rng-btn" id="shuffleKeyItemsRng" onclick="event.preventDefault();toggleRng('shuffleKeyItems')" title="Randomize per seed">&#127922;</button>
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles key progression items (Engineer&rsquo;s Key, Poign&eacute;, Baton, Flambeau, Marteau, Calabash, Prison Key Card) using assumed-fill logic that guarantees every seed is beatable.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleGad" class="default-on" checked>
        Gad Pickups
        <button class="rng-btn" id="shuffleGadRng" onclick="event.preventDefault();toggleRng('shuffleGad')" title="Randomize per seed">&#127922;</button>
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Converts Gad powers (Touch, Walk, Swim) into physical pickups and shuffles them across temple locations. Requires an EXE patch, which is applied automatically.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleWeapons" class="default-on" checked>
        Weapons
        <button class="rng-btn" id="shuffleWeaponsRng" onclick="event.preventDefault();toggleRng('shuffleWeapons')" title="Randomize per seed">&#127922;</button>
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles weapons (Asson, Shotgun, Enseigne, MP5, T&ecirc;te de Mort, Desert Eagle) across item locations. Uncheck to leave weapons in their vanilla spots.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleLore" class="default-on" checked>
        Lore
        <button class="rng-btn" id="shuffleLoreRng" onclick="event.preventDefault();toggleRng('shuffleLore')" title="Randomize per seed">&#127922;</button>
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles lore items (Book of Shadows, Prophecy, Jack&rsquo;s Schematic) across locations. Uncheck to leave them in vanilla positions.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleRetractors" class="default-on" checked>
        Retractors
        <button class="rng-btn" id="shuffleRetractorsRng" onclick="event.preventDefault();toggleRng('shuffleRetractors')" title="Randomize per seed">&#127922;</button>
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles the 5 Retractor items across locations. Uncheck to leave all Retractors in their vanilla spots.</span></span>
        <span class="tip"><span class="tip-icon" style="color:var(--red)">!</span><span class="tip-box">Turning this off is untested &mdash; not guaranteed to produce a beatable seed.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleAccumulators" class="default-on" checked>
        Accumulators
        <button class="rng-btn" id="shuffleAccumulatorsRng" onclick="event.preventDefault();toggleRng('shuffleAccumulators')" title="Randomize per seed">&#127922;</button>
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles the 3 Accumulator items across locations. Uncheck to leave all Accumulators in their vanilla spots.</span></span>
        <span class="tip"><span class="tip-icon" style="color:var(--red)">!</span><span class="tip-box">Turning this off is untested &mdash; not guaranteed to produce a beatable seed.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleEclipsers" class="default-on" checked>
        Eclipsers
        <button class="rng-btn" id="shuffleEclipsersRng" onclick="event.preventDefault();toggleRng('shuffleEclipsers')" title="Randomize per seed">&#127922;</button>
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles the 3 Eclipser parts across locations. Uncheck to leave all Eclipser parts in their vanilla spots.</span></span>
        <span class="tip"><span class="tip-icon" style="color:var(--red)">!</span><span class="tip-box">Turning this off is untested &mdash; not guaranteed to produce a beatable seed.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleLightSoul">
        Light Soul
        <button class="rng-btn" id="shuffleLightSoulRng" onclick="event.preventDefault();toggleRng('shuffleLightSoul')" title="Randomize per seed">&#127922;</button>
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Includes the Light Soul bonus item in the shuffle pool. Off by default as it can affect run balance.</span></span>
      </label>
      <label class="check-label" style="opacity:0.4;cursor:not-allowed" title="Coming soon">
        <input type="checkbox" id="shufflePrisms" disabled>
        Prisms
        <button class="rng-btn" id="shufflePrismsRng" disabled style="cursor:not-allowed">&#127922;</button>
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Prism shuffle is not yet implemented — coming soon.</span></span>
      </label>
    </div>
    <hr class="divider">
    <div class="card-title" style="margin-bottom:8px">Starting Item</div>
    <div class="row">
      <select id="startingItem">
        <option value="">None</option>
        <option value="random">🎲 random</option>
        <option value="RSC_X_ENGINEERS_KEY">⭐ Engineers Key</option>
        <option value="RSC_X_BATON">Baton</option>
        <option value="RSC_X_CALABASH">Calabash</option>
        <option value="RSC_X_ECLIPSER_PART1">Eclipser</option>
        <option value="RSC_X_FLAMBEAU">Flambeau</option>
        <option value="RSC_X_FLASHLIGHT">Flashlight</option>
        <option value="RSC_X_GAD_PICKUP">Gad Power Upgrade</option>
        <option value="RSC_X_MARTEAU">Marteau</option>
        <option value="RSC_X_POIGNE">Poigne</option>
        <option value="RSC_X_PRISON_KEY_CARD">Prison Key Card</option>
        <option value="RSC_X_RETRACT">Retractor</option>
        <option value="RSC_X_ACCUMULATOR">Accumulator</option>
        <option value="RSC_X_ASSON">Asson</option>
        <option value="RSC_X_BOOK_OF_SHADOWS">Book of Shadows</option>
        <option value="RSC_X_ENSEIGNE">Enseigne</option>
        <option value="RSC_X_JACKS_SCHEMATIC">Jacks Schematic</option>
        <option value="RSC_X_LIGHT_SOUL">Light Soul</option>
        <option value="RSC_X_MAC10">0.9-SMG</option>
        <option value="RSC_X_MP5">MP-909</option>
        <option value="RSC_X_PROPHECY">Book of Prophecy</option>
        <option value="RSC_X_SHOTGUN">Shotgun</option>
        <option value="RSC_X_SHOTGUN2">Sawed-off Shotgun</option>
        <option value="RSC_X_TETEDEMORT">T&ecirc;te De Mort</option>
        <option value="RSC_Q_VIOLATOR">Violator</option>
      </select>
      <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Places a bonus item at the church in Louisiana Swampland at game start. The selected item is removed from the shuffle pool.</span></span>
    </div>
    <hr class="divider">
    <div class="card-title" style="margin-bottom:8px">Teddy Bear Hints</div>
    <label class="check-label">
      <input type="checkbox" id="patchTracker" checked>
      Map Level Hints
      <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Rewrites map level hints to reflect randomized item locations. Off by default (strips all item hints to avoid wrong vanilla locations).</span></span>
    </label>
    <hr class="divider">
    <div class="card-title" style="margin-bottom:6px">Death Penalty</div>
    <div class="row">
      <span style="color:var(--muted);font-size:11px;white-space:nowrap">Per death:</span>
      <input type="range" id="deathPenalty" min="0" max="10" value="0"
             oninput="document.getElementById('deathPenaltyVal').textContent=(this.value==='0'?'Off':'−'+this.value+' step/death')">
      <span class="slider-val" id="deathPenaltyVal">Off</span>
      <button class="rng-btn" id="deathPenaltyRng" onclick="toggleRng('deathPenalty');refreshDeathPenaltyLabel()" title="Randomize per seed">&#127922;</button>
      <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Reduces max health by 1 step per death, floored at that step. <b>Off</b> disables the penalty. <b>1</b> = &minus;1 step/death (mild), <b>10</b> = &minus;10 steps/death (brutal). Applied as a direct EXE patch.</span></span>
    </div>
    <div class="hint" style="margin-top:4px">Off = disabled &nbsp;&nbsp; 10 = &minus;10 steps/death</div>
  </div>

  <!-- Right column: Coffin Gates + Progression stacked -->
  <div style="display:flex;flex-direction:column;gap:10px">

    <div class="card" style="flex:1;min-width:0">
      <div class="card-title">
        Coffin Gate Soul Levels
        <span class="tip" style="vertical-align:middle">
          <span class="tip-icon">?</span>
          <span class="tip-box">Shuffles the soul level (SL) thresholds on deadside coffin gates. Higher SL gates require more Dark Souls collected before they open. Starting gates are always kept at SL&le;3 so the game is immediately playable.</span>
        </span>
      </div>
<div style="display:flex;flex-direction:column;gap:7px">
        <!-- Row: Preset -->
        <div class="row">
          <label style="color:var(--muted);font-size:11px;white-space:nowrap;width:58px">Preset:</label>
          <select id="gatePreset" onchange="updateGateDesc()" style="flex:1;min-width:0">
            <option value="none">none — default coffin gates</option>
            <option value="open">open — all gates free</option>
            <option value="easy">easy — light shuffle, SL7 cap, 6 open</option>
            <option value="medium">medium — standard, SL8 cap, 3 open</option>
            <option value="hard">hard — full shuffle, no SL cap, 1 open</option>
            <option value="chaos">chaos — fully unconstrained</option>
            <option value="random">🎲 random — preset rolled per seed</option>
          </select>
          <span class="tip">
            <span class="tip-icon">?</span>
            <span class="tip-box">Sets the starting configuration for the coffin gate shuffle — controls the SL cap and how many gates start open. You can override either value below with Max SL and Open N.</span>
          </span>
        </div>
        <!-- Row: Max SL -->
        <div class="row">
          <label style="color:var(--muted);font-size:11px;white-space:nowrap;width:58px">Max SL:</label>
          <select id="maxSl" style="flex:1;min-width:0">
            <option value="">preset</option>
            <option value="0">0 — free</option>
            <option value="1">1</option>
            <option value="2">2</option>
            <option value="3">3</option>
            <option value="4">4</option>
            <option value="5">5</option>
            <option value="6">6</option>
            <option value="7">7</option>
            <option value="8">8</option>
            <option value="9">9</option>
            <option value="10">10</option>
          </select>
          <span class="tip">
            <span class="tip-icon">?</span>
            <span class="tip-box">Overrides the preset's SL cap. Choose 0–10 to cap the highest gate level; lower values keep gates more accessible. Leave at <b>preset</b> to use the preset's built-in cap.</span>
          </span>
        </div>
        <!-- Row: Open N -->
        <div class="row">
          <label style="color:var(--muted);font-size:11px;white-space:nowrap;width:58px">Open N:</label>
          <select id="openGatesN" style="flex:1;min-width:0">
            <option value="">preset</option>
            <option value="0">0 — none</option>
            <option value="1">1 — Marrow</option>
            <option value="2">2 — Wasteland</option>
            <option value="3">3 — Asylum</option>
            <option value="4">4 — Temple</option>
            <option value="5">5 — Cageways</option>
            <option value="6">6 — Playrooms</option>
          </select>
          <span class="tip">
            <span class="tip-icon">?</span>
            <span class="tip-box">Forces the first N linear coffin gates to SL0, regardless of the gate preset. Gates are opened in sequence: Marrow → Wasteland → Asylum → Temple of Fire → Cageways → Playrooms. Beyond 6, gates are chosen randomly. Overrides the preset default.</span>
          </span>
        </div>
        <!-- Row: SL threshold mode -->
        <div class="row">
          <label style="color:var(--muted);font-size:11px;white-space:nowrap">SL Req Shuffle:</label>
          <select id="randomizeSoulThresholds" style="flex:1;min-width:0">
            <option value="off">Off (vanilla)</option>
            <option value="progressive">Progressive</option>
            <option value="balanced">Balanced</option>
            <option value="random">Random</option>
          </select>
          <button class="rng-btn" id="randomizeSoulThresholdsRng" onclick="event.preventDefault();toggleRng('randomizeSoulThresholds')" title="Randomize per seed">&#127922;</button>
          <span class="tip">
            <span class="tip-icon">?</span>
            <span class="tip-box">Redistributes the soul counts required for SL1–SL10 gates (vanilla: 1, 3, 7, 15, 23, 35, 51, 71, 95, 120). <b>Progressive:</b> gaps grow larger at higher SLs — early gates are cheap, late gates demand many souls. <b>Balanced:</b> roughly equal spacing throughout. <b>Random:</b> fully random redistribution. SL0 always stays at 0 and SL10 at 120. The 🎲 button randomly picks a mode each seed.</span>
          </span>
        </div>
      </div>
    </div>

    <div class="card" style="flex:1;min-width:0">
      <div class="card-title">
        Entrance Randomizer
        <span class="tip" style="vertical-align:middle"><span class="tip-icon">?</span><span class="tip-box"><b>deadside only:</b> shuffles the 9 Deadside levels among themselves — which level you enter is randomized but the Engine Rooms stay vanilla.<br><b>cross hub:</b> 14 levels (Deadside levels + Engine Rooms) shuffled together; a Deadside portal may lead to an Engine Room and vice versa.</span></span>
      </div>
      <div class="row">
        <select id="entranceMode" onchange="onEntranceModeChange()" style="width:100%;min-width:0">
          <option value="off">Off — vanilla entrances</option>
          <option value="deadside_only">Deadside Only — 9 levels shuffled</option>
          <option value="cross_hub">Cross Hub — Deadside levels &amp; Engine Rooms shuffled</option>
          <option value="random">🎲 random</option>
        </select>
      </div>
      <div id="entranceHint" class="hint" style="margin-top:6px;display:none">
        &#x26A0;&#xFE0F; Works best with Insanity Tier &ge;1.
      </div>
    </div>

  </div>

  <!-- Col 3: Gameplay Tuning -->
  <div class="card">
    <div class="card-title">Gameplay Tuning</div>

    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin-bottom:6px">Health</div>
    <div class="row" style="margin-bottom:8px">
      <span style="color:var(--muted);font-size:11px;white-space:nowrap">Starting:</span>
      <input type="range" id="startingHealth" min="1" max="10" value="5"
             oninput="document.getElementById('startingHealthVal').textContent=this.value">
      <span class="slider-val" id="startingHealthVal">5</span>
      <button class="rng-btn" id="startingHealthRng" onclick="toggleRng('startingHealth')" title="Randomize per seed">&#127922;</button>
      <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Starting max health on a 1&ndash;10 scale. Vanilla is 5. Current health is set to max on spawn.</span></span>
    </div>
    <div class="row" style="margin-bottom:6px">
      <span style="color:var(--muted);font-size:11px;white-space:nowrap">Per altar:</span>
      <input type="range" id="altarHealthGrant" min="1" max="10" value="1"
             oninput="document.getElementById('altarHealthGrantVal').textContent=this.value">
      <span class="slider-val" id="altarHealthGrantVal">1</span>
      <button class="rng-btn" id="altarHealthGrantRng" onclick="toggleRng('altarHealthGrant')" title="Randomize per seed">&#127922;</button>
      <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Health restored per life altar interaction on a 1&ndash;10 scale. Vanilla is 1.</span></span>
    </div>
    <div class="hint" style="margin-bottom:6px">start + 5 &times; per-altar &le; 10 (hard cap)</div>

    <hr class="divider">
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin-bottom:4px">Cadeaux
      <span class="tip"><span class="tip-icon" style="color:var(--red)">!</span><span class="tip-box">Cadeaux counting is not yet fully working &mdash; some cadeaux may not register correctly in-game. Consider lowering these values from their defaults until this is resolved.</span></span>
    </div>
    <div class="row" style="margin-bottom:8px">
      <span style="color:var(--muted);font-size:11px;white-space:nowrap;margin-right:8px">Altar cost:</span>
      <input type="number" id="altarCadeauxRequired" value="100" min="1" max="133" style="width:65px" oninput="syncCadeauxConstraints()">
      <button class="rng-btn" id="altarCadeauxRequiredRng" onclick="toggleRng('altarCadeauxRequired');syncCadeauxConstraints()" title="Randomize per seed">&#127922;</button>
      <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Cadeaux required and spent per life altar interaction. The minimum required and the per-interaction cost are always equal (vanilla: 100, max: 133 = &lfloor;666 &divide; 5&rfloor;).</span></span>
    </div>
    <div id="altarCadeauxMsg" style="display:none;font-size:10px;color:var(--red);margin-top:2px"></div>
    <div class="row" style="margin-top:8px">
      <span style="color:var(--muted);font-size:11px;white-space:nowrap;margin-right:8px">Fog door:</span>
      <input type="number" id="fogometersCadeauxRequired" value="666" min="5" max="666" style="width:65px" oninput="syncCadeauxConstraints()">
      <button class="rng-btn" id="fogometersCadeauxRequiredRng" onclick="toggleRng('fogometersCadeauxRequired');syncCadeauxConstraints()" title="Randomize per seed">&#127922;</button>
      <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Cadeaux required to open the Fogometers light soul door (vanilla: 666). Must be at least 5 &times; the altar cost and no more than 666.</span></span>
    </div>
    <div id="fogometersCadeauxMsg" style="display:none;font-size:10px;color:var(--red);margin-top:2px"></div>

    <hr class="divider">
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);margin-bottom:6px">Progression</div>
    <div class="row" style="margin-bottom:8px">
      <span style="color:var(--muted);font-size:11px;white-space:nowrap">Insanity Tier</span>
      <select id="insanity" style="width:180px;flex:none;margin-left:8px">
        <option value="0">Off</option>
        <option value="1">Tier 1 &mdash; Soul &amp; Govi slots</option>
        <option value="2">Tier 2 &mdash; + Cadeaux slots</option>
        <option value="3">Full &mdash; All slots</option>
        <option value="random">🎲 random tier</option>
      </select>
      <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Controls where key progression items can be placed. By default, key items shuffle among key item slots and dark souls shuffle anywhere.<br><b>Tier 1:</b> Also allows key items in Soul &amp; Govi pickup slots.<br><b>Tier 2:</b> Also allows Cadeaux slots (untested).<br><b>Full:</b> Any slot in the game — wildly random.</span></span>
    </div>
    <div class="row">
      <span style="color:var(--muted);font-size:11px;white-space:nowrap">Balancing:</span>
      <input type="range" id="progBalance" min="0" max="100" value="50"
             oninput="document.getElementById('progBalVal').textContent=this.value">
      <span class="slider-val" id="progBalVal">50</span>
      <button class="rng-btn" id="progBalanceRng" onclick="toggleRng('progBalance')" title="Randomize per seed">&#127922;</button>
      <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Controls how deep into the world progression items tend to be placed. Default 50 is balanced. 0 = items placed early, 100 = items pushed deep.</span></span>
    </div>
    <div class="hint" style="margin-top:6px">0 = items placed early &nbsp;&nbsp; 100 = items pushed deep</div>

  </div>

</div>

<!-- Row 3: Enemies + Cosmetics -->
<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;align-items:stretch">
  <div class="card" style="flex:1 ;min-width:280px">
    <div class="card-title">Enemies</div>
    <div class="check-grid" style="margin-bottom:6px">
      <label class="check-label">
        <input type="checkbox" id="shuffleEnemies" onchange="onEnemiesChange()">
        Shuffle Enemies
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Randomizes enemy types in each level. Use the Enemy Mode dropdown to control how they are assigned.</span></span>
        <button class="rng-btn" id="shuffleEnemiesRng" onclick="event.preventDefault();toggleRng('shuffleEnemies');onEnemiesChange()" title="Randomize per seed">&#127922;</button>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleTrueforms">
        Shuffle Trueforms
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles true-form enemy positions into the same pool as regular enemies. Only meaningful when Shuffle Enemies is also enabled.</span></span>
        <button class="rng-btn" id="shuffleTrueformsRng" onclick="event.preventDefault();toggleRng('shuffleTrueforms')" title="Randomize per seed">&#127922;</button>
      </label>
      <label class="check-label">
        <input type="checkbox" id="enemyMixMovement" disabled>
        Mix Movement Types
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Allows ground, flying, and swimming enemies to swap with each other. Off by default — mixing types can place flying enemies in water areas or vice versa.</span></span>
        <button class="rng-btn" id="enemyMixMovementRng" onclick="event.preventDefault();toggleRng('enemyMixMovement');onEnemiesChange()" title="Randomize per seed">&#127922;</button>
      </label>
      <label class="check-label">
        <input type="checkbox" id="enemyUncapCounts" disabled>
        Uncap Enemy Counts
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Each slot independently picks a random enemy type with replacement — the same type can fill dozens of slots or none at all. Off by default, which preserves vanilla per-type counts.</span></span>
        <button class="rng-btn" id="enemyUncapCountsRng" onclick="event.preventDefault();toggleRng('enemyUncapCounts');onEnemiesChange()" title="Randomize per seed">&#127922;</button>
      </label>
    </div>
    <div class="enemy-row">
      <span class="lbl">Enemy Mode:</span>
      <select id="enemyMode" disabled style="width:180px;flex:none">
        <option value="difficulty">difficulty &mdash; tier-weighted</option>
        <option value="contextual">contextual &mdash; area pools</option>
        <option value="full">full &mdash; completely random</option>
        <option value="random">🎲 random mode</option>
      </select>
      <span class="tip"><span class="tip-icon">?</span><span class="tip-box"><b>difficulty:</b> enemies replaced by others of similar difficulty — same tier, weighted by area depth.<br><b>contextual:</b> shuffled within context groups (deadside/liveside/prison stay separated).<br><b>full:</b> completely random across the whole enemy pool (use Mix Movement Types to also cross ground/flying/etc).</span></span>
      <span class="hint" id="enemyHint">enable Shuffle Enemies to unlock</span>
    </div>
  </div>

  <div class="card" style="margin-bottom:0; flex: 1">
    <div class="card-title">Cosmetic Shuffles</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:2px 8px">
      <label class="check-label">
        <input type="checkbox" id="shuffleMusic">
        Shuffle Music
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Randomly reassigns music tracks across all levels. No effect on gameplay or logic.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleVoices">
        Shuffle Voice Lines
        <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Shuffles Shadow Man&rsquo;s generic ambient voice lines. Purely cosmetic.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleWeaponsSfx">
        Shuffle Weapon SFX
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles weapon fire and reload sounds within each weapon category. Purely cosmetic.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleEnemiesSfx">
        Shuffle Enemy SFX
        <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Shuffles enemy sound effects within type pools — pain sounds trade with other enemies' pain sounds, startle sounds with startle sounds, attack sounds with attack sounds. Ambient creatures and death-by-weapon sounds are left untouched. Purely cosmetic.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleAmbients">
        Shuffle Ambients
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles ambient creatures (rats, egrets, flies, butterflies, friendly fish) across their spawn slots. Any ambient can become any other. Purely cosmetic — they don&rsquo;t drop items or block progress.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleSky">
        Shuffle Sky Textures
        <span class="tip anchor-right"><span class="tip-icon">?</span><span class="tip-box">Swaps sky textures across levels — each sky layer (horizon, clouds, hills, sun) is shuffled within its own pool so only matching layers trade places. Purely cosmetic.</span></span>
      </label>
    </div>
  </div>
</div>

<!-- Actions -->
<div class="actions">
  <button class="btn-run"     id="runBtn"     onclick="runPatcher()">&#9654;&ensp;Run Randomizer</button>
  <button class="btn-restore" id="restoreBtn" onclick="restoreVanilla()">&#8617;&ensp;Restore Vanilla</button>
  <div class="post-run" id="postRun" style="margin-left:auto">
    <button class="btn-launch" onclick="launchGame()">&#9654;&ensp;Launch Game</button>
    <button class="btn-ghost"  onclick="openFolder()">&#128193;&ensp;Open Folder</button>
  </div>
</div>
<div class="progress-bar" id="progressBar"></div>
<div class="terminal" id="output"></div>
<div class="status"   id="status">Ready.</div>

<script>
const GATE_DESCS = {
  none:   'default coffin gates',
  random: '🎲 preset rolled randomly each seed',
  open:   'all gates free — no souls required',
  easy:   'light shuffle, SL7 cap, 6 gates open',
  medium: 'standard shuffle, SL8 cap, 3 gates open',
  hard:   'full shuffle, no SL cap, 1 gate open',
  chaos:  'fully unconstrained — anything goes'
};
function updateGateDesc() {
  const preset = document.getElementById('gatePreset').value;
  const maxSlEl = document.getElementById('maxSl');
  const openGatesEl = document.getElementById('openGatesN');
  if (preset === 'open') {
    maxSlEl.value = '0';
    maxSlEl.disabled = true;
    openGatesEl.value = '';
    openGatesEl.disabled = true;
  } else if (preset === 'random') {
    // Fields stay editable — the resolved preset is unknown until runtime
    maxSlEl.disabled = false;
    openGatesEl.disabled = false;
  } else {
    maxSlEl.disabled = false;
    maxSlEl.value = '';
    openGatesEl.disabled = false;
    // Don't reset openGatesEl.value here — preserve the user's selection
    // when switching between non-"open" presets (e.g. easy → medium).
  }
}
function onEntranceModeChange() {
  const mode = document.getElementById('entranceMode').value;
  document.getElementById('entranceHint').style.display = (mode !== 'off') ? 'block' : 'none';
}
function onEnemiesChange() {
  const rngActive = isRng('shuffleEnemies');
  const on = rngActive || document.getElementById('shuffleEnemies').checked;
  document.getElementById('enemyMode').disabled = !on;
  // Only control each checkbox if it is not itself in random mode
  if (!isRng('enemyMixMovement')) {
    document.getElementById('enemyMixMovement').disabled = !on;
  }
  if (!isRng('enemyUncapCounts')) {
    document.getElementById('enemyUncapCounts').disabled = !on;
  }
  document.getElementById('enemyHint').textContent = on ? '' : 'enable Shuffle Enemies to unlock';
}
// ── Per-field RNG toggle (🎲 buttons) ────────────────────────────────────────
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
}
function applyRng(id, val) {
  // Restore a field's RNG state when importing settings
  const shouldRng = (val === 'random');
  const btn = document.getElementById(id + 'Rng');
  if (btn) btn.classList.toggle('rng-active', shouldRng);
  const el = document.getElementById(id);
  if (el) {
    el.disabled = shouldRng;
    el.style.opacity = shouldRng ? '0.35' : '';
  }
  const valEl = document.getElementById(id + 'Val');
  if (valEl) valEl.textContent = shouldRng ? '?' : (el ? el.value : '');
}

function syncCadeauxConstraints() {
  const ALTAR_MAX = 133; // floor(666 / 5)
  const FOG_MAX   = 666;
  const altarEl   = document.getElementById('altarCadeauxRequired');
  const fogEl     = document.getElementById('fogometersCadeauxRequired');
  const altarMsg  = document.getElementById('altarCadeauxMsg');
  const fogMsg    = document.getElementById('fogometersCadeauxMsg');

  // When either field is randomized, skip constraint enforcement
  if (isRng('altarCadeauxRequired') || isRng('fogometersCadeauxRequired')) {
    altarMsg.style.display = 'none';
    fogMsg.style.display = 'none';
    return;
  }

  let altarVal = parseInt(altarEl.value) || 1;
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
  let fogVal = parseInt(fogEl.value) || fogMin;
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
}
function refreshDeathPenaltyLabel() {
  if (isRng('deathPenalty')) return;
  const v = document.getElementById('deathPenalty').value;
  document.getElementById('deathPenaltyVal').textContent = (v === '0' ? 'Off' : '−' + v + ' step/death');
}
function randomizeSeed() {
  document.getElementById('seed').value = Math.floor(Math.random() * 9000000000) + 1000000000;
}
async function browseDir() {
  const current = document.getElementById('gameDir').value || '';
  const result  = await window.pywebview.api.browse_dir(current);
  if (result) document.getElementById('gameDir').value = result;
}
function getConfig() {
  return {
    gameDir:          document.getElementById('gameDir').value.trim(),
    seed:             document.getElementById('seed').value.trim(),
    gatePreset:       document.getElementById('gatePreset').value,
    maxSl:            document.getElementById('maxSl').value,
    shuffleGad:       isRng('shuffleGad') ? 'random' : document.getElementById('shuffleGad').checked,
    startingItem:     document.getElementById('startingItem').value,
    shuffleEnemies:   isRng('shuffleEnemies') ? 'random' : document.getElementById('shuffleEnemies').checked,
    shuffleTrueforms: isRng('shuffleTrueforms') ? 'random' : document.getElementById('shuffleTrueforms').checked,
    shuffleMusic:     document.getElementById('shuffleMusic').checked,
    shuffleVoices:    document.getElementById('shuffleVoices').checked,
    shuffleWeaponsSfx:document.getElementById('shuffleWeaponsSfx').checked,
    shuffleEnemiesSfx:document.getElementById('shuffleEnemiesSfx').checked,
    shuffleSky:       document.getElementById('shuffleSky').checked,
    patchTracker:        document.getElementById('patchTracker').checked,
    openGatesN:          document.getElementById('openGatesN').value,
    insanity:         document.getElementById('insanity').value,
    shuffleLightSoul: isRng('shuffleLightSoul') ? 'random' : document.getElementById('shuffleLightSoul').checked,
    shuffleKeyItems:  isRng('shuffleKeyItems') ? 'random' : document.getElementById('shuffleKeyItems').checked,
    shuffleWeapons:   isRng('shuffleWeapons') ? 'random' : document.getElementById('shuffleWeapons').checked,
    shuffleLore:      isRng('shuffleLore') ? 'random' : document.getElementById('shuffleLore').checked,
    shufflePrisms:        isRng('shufflePrisms') ? 'random' : document.getElementById('shufflePrisms').checked,
    shuffleRetractors:    isRng('shuffleRetractors') ? 'random' : document.getElementById('shuffleRetractors').checked,
    shuffleAccumulators:  isRng('shuffleAccumulators') ? 'random' : document.getElementById('shuffleAccumulators').checked,
    shuffleEclipsers:     isRng('shuffleEclipsers') ? 'random' : document.getElementById('shuffleEclipsers').checked,
    enemyMode:        document.getElementById('enemyMode').value.split('—')[0].trim(),
    enemyMixMovement: isRng('enemyMixMovement') ? 'random' : document.getElementById('enemyMixMovement').checked,
    enemyUncapCounts: isRng('enemyUncapCounts') ? 'random' : document.getElementById('enemyUncapCounts').checked,
    shuffleAmbients:  document.getElementById('shuffleAmbients').checked,
    progBalance:               isRng('progBalance') ? 'random' : document.getElementById('progBalance').value,
    entranceMode:              document.getElementById('entranceMode').value,
    altarCadeauxRequired:      isRng('altarCadeauxRequired') ? 'random' : document.getElementById('altarCadeauxRequired').value,
    fogometersCadeauxRequired: isRng('fogometersCadeauxRequired') ? 'random' : document.getElementById('fogometersCadeauxRequired').value,
    startingHealth:            isRng('startingHealth') ? 'random' : document.getElementById('startingHealth').value,
    altarHealthGrant:          isRng('altarHealthGrant') ? 'random' : document.getElementById('altarHealthGrant').value,
    randomizeSoulThresholds:   isRng('randomizeSoulThresholds') ? 'random' : document.getElementById('randomizeSoulThresholds').value,
    deathPenalty:              isRng('deathPenalty') ? 'random' : document.getElementById('deathPenalty').value,
  };
}
// ── Settings string export / import ──────────────────────────────────────────
const SETTINGS_OMIT = new Set(['gameDir', 'seed']); // never encoded — machine-specific

// v2 compact format: abbreviated keys + defaults omitted.
// Long key → short code (2–3 chars, all unique).
const FIELD_SHORT = {
  gatePreset:                'gP',
  maxSl:                     'mS',
  shuffleGad:                'sG',
  startingItem:              'sI',
  shuffleEnemies:            'sE',
  shuffleTrueforms:          'sT',
  shuffleMusic:              'sm',
  shuffleVoices:             'sV',
  shuffleWeaponsSfx:         'wS',
  shuffleEnemiesSfx:         'eS',
  shuffleSky:                'sk',
  patchTracker:              'pT',
  openGatesN:                'oG',
  insanity:                  'in',
  shuffleLightSoul:          'sL',
  shuffleKeyItems:           'sK',
  shuffleWeapons:            'sW',
  shuffleLore:               'sLo',
  enemyMode:                 'eM',
  enemyMixMovement:          'eX',
  enemyUncapCounts:          'eU',
  shuffleAmbients:           'sA',
  progBalance:               'pB',
  entranceMode:              'eN',
  altarCadeauxRequired:      'aC',
  fogometersCadeauxRequired: 'fC',
  startingHealth:            'sH',
  altarHealthGrant:          'aH',
  randomizeSoulThresholds:   'rST',
  deathPenalty:              'dP',
  shufflePrisms:             'sPr',
  shuffleRetractors:         'sRt',
  shuffleAccumulators:       'sAc',
  shuffleEclipsers:          'sEc',
};
// Reverse map: short code → long key
const FIELD_LONG = Object.fromEntries(Object.entries(FIELD_SHORT).map(([l, s]) => [s, l]));
// Default values as returned by getConfig() — fields matching these are omitted when encoding.
const FIELD_DEFAULTS = {
  gatePreset:                'none',
  maxSl:                     '',
  shuffleGad:                true,
  startingItem:              '',
  shuffleEnemies:            false,
  shuffleTrueforms:          false,
  shuffleMusic:              false,
  shuffleVoices:             false,
  shuffleWeaponsSfx:         false,
  shuffleEnemiesSfx:         false,
  shuffleSky:                false,
  patchTracker:              true,
  openGatesN:                '',
  insanity:                  '0',
  shuffleLightSoul:          false,
  shuffleKeyItems:           true,
  shuffleWeapons:            true,
  shuffleLore:               true,
  enemyMode:                 'difficulty',
  enemyMixMovement:          false,
  enemyUncapCounts:          false,
  shuffleAmbients:           false,
  progBalance:               '50',
  entranceMode:              'off',
  altarCadeauxRequired:      '100',
  fogometersCadeauxRequired: '666',
  startingHealth:            '5',
  altarHealthGrant:          '1',
  randomizeSoulThresholds:   'off',
  deathPenalty:              '0',
  shufflePrisms:             false,
  shuffleRetractors:         true,
  shuffleAccumulators:       true,
  shuffleEclipsers:          true,
};

function encodeSettings() {
  const cfg = getConfig();
  SETTINGS_OMIT.forEach(k => delete cfg[k]);
  // Build compact object: short keys, defaults omitted.
  const compact = {};
  for (const [longKey, shortKey] of Object.entries(FIELD_SHORT)) {
    const val = cfg[longKey];
    if (val === undefined) continue;
    if (val === FIELD_DEFAULTS[longKey]) continue; // skip defaults
    compact[shortKey] = val;
  }
  return 'v2:' + btoa(JSON.stringify(compact));
}

function exportSettings() {
  const str = encodeSettings();
  const el = document.getElementById('settingsString');
  el.value = str;
  el.select();
  let copied = false;
  try { copied = document.execCommand('copy'); } catch(e) {}
  if (!copied) {
    navigator.clipboard.writeText(str).catch(() => {});
  }
  const msg = document.getElementById('settingsMsg');
  msg.textContent = 'Copied!';
  msg.style.color = 'var(--green)';
  setTimeout(() => { msg.textContent = ''; }, 2000);
}

function importSettings() {
  const raw = document.getElementById('settingsString').value.trim();
  const msg = document.getElementById('settingsMsg');
  if (!raw) {
    msg.textContent = 'No settings string to import';
    msg.style.color = 'var(--red)';
    setTimeout(() => { msg.textContent = ''; msg.style.color = ''; }, 2500);
    return;
  }
  let cfg;
  try {
    if (raw.startsWith('v2:')) {
      // Compact format: expand short keys back to long, fill missing fields from defaults.
      const compact = JSON.parse(atob(raw.slice(3)));
      cfg = { ...FIELD_DEFAULTS };
      for (const [shortKey, val] of Object.entries(compact)) {
        const longKey = FIELD_LONG[shortKey];
        if (longKey) cfg[longKey] = val;
      }
    } else {
      // Legacy v1: full JSON key names — decode as-is.
      cfg = JSON.parse(atob(raw));
    }
  } catch(e) {
    msg.textContent = 'Invalid string';
    msg.style.color = 'var(--red)';
    setTimeout(() => { msg.textContent = ''; }, 2500);
    return;
  }
  applyConfig(cfg);
  msg.textContent = 'Imported!';
  msg.style.color = 'var(--green)';
  setTimeout(() => { msg.textContent = ''; }, 2000);
}

function applyConfig(cfg) {
  function set(id, val) {
    const el = document.getElementById(id);
    if (!el || val === undefined) return;
    if (el.type === 'checkbox') el.checked = !!val;
    else if (el.type === 'range') { el.value = val; el.dispatchEvent(new Event('input')); }
    else el.value = val;
  }
  set('gatePreset',               cfg.gatePreset);
  set('maxSl',                    cfg.maxSl);
  if (cfg.shuffleGad !== undefined) {
    if (cfg.shuffleGad === 'random') { applyRng('shuffleGad', 'random'); }
    else { applyRng('shuffleGad', cfg.shuffleGad); set('shuffleGad', cfg.shuffleGad); }
  }
  set('startingItem',             cfg.startingItem);
  if (cfg.shuffleEnemies !== undefined) {
    if (cfg.shuffleEnemies === 'random') { applyRng('shuffleEnemies', 'random'); }
    else { applyRng('shuffleEnemies', cfg.shuffleEnemies); set('shuffleEnemies', cfg.shuffleEnemies); }
  }
  if (cfg.shuffleTrueforms !== undefined) {
    if (cfg.shuffleTrueforms === 'random') { applyRng('shuffleTrueforms', 'random'); }
    else { applyRng('shuffleTrueforms', cfg.shuffleTrueforms); set('shuffleTrueforms', cfg.shuffleTrueforms); }
  }
  set('shuffleMusic',             cfg.shuffleMusic);
  set('shuffleVoices',            cfg.shuffleVoices);
  set('shuffleWeaponsSfx',        cfg.shuffleWeaponsSfx);
  set('shuffleEnemiesSfx',        cfg.shuffleEnemiesSfx);
  set('shuffleSky',               cfg.shuffleSky);
  set('patchTracker',             cfg.patchTracker);
  set('openGatesN',               cfg.openGatesN);
  set('insanity',                 cfg.insanity);
  if (cfg.shuffleLightSoul !== undefined) {
    if (cfg.shuffleLightSoul === 'random') { applyRng('shuffleLightSoul', 'random'); }
    else { applyRng('shuffleLightSoul', cfg.shuffleLightSoul); set('shuffleLightSoul', cfg.shuffleLightSoul); }
  }
  if (cfg.shuffleKeyItems !== undefined) {
    if (cfg.shuffleKeyItems === 'random') { applyRng('shuffleKeyItems', 'random'); }
    else { applyRng('shuffleKeyItems', cfg.shuffleKeyItems); set('shuffleKeyItems', cfg.shuffleKeyItems); }
  }
  if (cfg.shuffleWeapons !== undefined) {
    if (cfg.shuffleWeapons === 'random') { applyRng('shuffleWeapons', 'random'); }
    else { applyRng('shuffleWeapons', cfg.shuffleWeapons); set('shuffleWeapons', cfg.shuffleWeapons); }
  }
  if (cfg.shuffleLore !== undefined) {
    if (cfg.shuffleLore === 'random') { applyRng('shuffleLore', 'random'); }
    else { applyRng('shuffleLore', cfg.shuffleLore); set('shuffleLore', cfg.shuffleLore); }
  }
  if (cfg.shufflePrisms !== undefined) {
    if (cfg.shufflePrisms === 'random') { applyRng('shufflePrisms', 'random'); }
    else { applyRng('shufflePrisms', cfg.shufflePrisms); set('shufflePrisms', cfg.shufflePrisms); }
  }
  if (cfg.shuffleRetractors !== undefined) {
    if (cfg.shuffleRetractors === 'random') { applyRng('shuffleRetractors', 'random'); }
    else { applyRng('shuffleRetractors', cfg.shuffleRetractors); set('shuffleRetractors', cfg.shuffleRetractors); }
  }
  if (cfg.shuffleAccumulators !== undefined) {
    if (cfg.shuffleAccumulators === 'random') { applyRng('shuffleAccumulators', 'random'); }
    else { applyRng('shuffleAccumulators', cfg.shuffleAccumulators); set('shuffleAccumulators', cfg.shuffleAccumulators); }
  }
  if (cfg.shuffleEclipsers !== undefined) {
    if (cfg.shuffleEclipsers === 'random') { applyRng('shuffleEclipsers', 'random'); }
    else { applyRng('shuffleEclipsers', cfg.shuffleEclipsers); set('shuffleEclipsers', cfg.shuffleEclipsers); }
  }
  if (cfg.enemyMixMovement !== undefined) {
    if (cfg.enemyMixMovement === 'random') { applyRng('enemyMixMovement', 'random'); }
    else { applyRng('enemyMixMovement', cfg.enemyMixMovement); set('enemyMixMovement', cfg.enemyMixMovement); }
  }
  if (cfg.enemyUncapCounts !== undefined) {
    if (cfg.enemyUncapCounts === 'random') { applyRng('enemyUncapCounts', 'random'); }
    else { applyRng('enemyUncapCounts', cfg.enemyUncapCounts); set('enemyUncapCounts', cfg.enemyUncapCounts); }
  }
  set('shuffleAmbients',          cfg.shuffleAmbients);
  set('entranceMode',             cfg.entranceMode);
  // Restore rng-toggle fields (value may be 'random' or a concrete value)
  if (cfg.progBalance !== undefined) {
    if (cfg.progBalance === 'random') { applyRng('progBalance', 'random'); }
    else { applyRng('progBalance', cfg.progBalance); set('progBalance', cfg.progBalance); }
  }
  if (cfg.altarCadeauxRequired !== undefined) {
    if (cfg.altarCadeauxRequired === 'random') { applyRng('altarCadeauxRequired', 'random'); }
    else { applyRng('altarCadeauxRequired', cfg.altarCadeauxRequired); set('altarCadeauxRequired', cfg.altarCadeauxRequired); }
  }
  if (cfg.fogometersCadeauxRequired !== undefined) {
    if (cfg.fogometersCadeauxRequired === 'random') { applyRng('fogometersCadeauxRequired', 'random'); }
    else { applyRng('fogometersCadeauxRequired', cfg.fogometersCadeauxRequired); set('fogometersCadeauxRequired', cfg.fogometersCadeauxRequired); }
  }
  if (cfg.startingHealth !== undefined) {
    if (cfg.startingHealth === 'random') { applyRng('startingHealth', 'random'); }
    else { applyRng('startingHealth', cfg.startingHealth); set('startingHealth', cfg.startingHealth); }
  }
  if (cfg.altarHealthGrant !== undefined) {
    if (cfg.altarHealthGrant === 'random') { applyRng('altarHealthGrant', 'random'); }
    else { applyRng('altarHealthGrant', cfg.altarHealthGrant); set('altarHealthGrant', cfg.altarHealthGrant); }
  }
  if (cfg.randomizeSoulThresholds !== undefined) {
    if (cfg.randomizeSoulThresholds === 'random') { applyRng('randomizeSoulThresholds', 'random'); }
    else { applyRng('randomizeSoulThresholds', cfg.randomizeSoulThresholds); set('randomizeSoulThresholds', cfg.randomizeSoulThresholds); }
  }
  if (cfg.deathPenalty !== undefined) {
    if (String(cfg.deathPenalty) === 'random') { applyRng('deathPenalty', 'random'); }
    else { applyRng('deathPenalty', cfg.deathPenalty); set('deathPenalty', String(cfg.deathPenalty)); }
  }
  // Restore enemy mode — strip the label suffix before setting
  if (cfg.enemyMode) {
    const el = document.getElementById('enemyMode');
    if (el) el.value = cfg.enemyMode.split('—')[0].trim();
  }
  // Re-run side-effect handlers so dependent UI stays consistent
  updateGateDesc();
  onEntranceModeChange();
  onEnemiesChange();
}

function setBusy(busy) {
  document.getElementById('runBtn').disabled     = busy;
  document.getElementById('restoreBtn').disabled = busy;
  document.getElementById('progressBar').className = 'progress-bar' + (busy ? ' active' : '');
  if (busy) document.getElementById('postRun').className = 'post-run';
}
function clearOutput() { document.getElementById('output').textContent = ''; }
function appendOutput(txt) {
  const el = document.getElementById('output');
  el.textContent += txt;
  el.scrollTop = el.scrollHeight;
}
function setStatus(txt, cls) {
  const el = document.getElementById('status');
  el.textContent = txt;
  el.className = 'status' + (cls ? ' ' + cls : '');
}
async function runPatcher() {
  const dir = document.getElementById('gameDir').value.trim();
  if (!dir) { appendOutput('\u26a0 Please select your Shadow Man Remastered game directory first.'); return; }
  const valid = await window.pywebview.api.validate_dir(dir);
  if (!valid) { appendOutput('\u26a0 Directory doesn\'t look like a Shadow Man Remastered install (no .kpf files found). Please select the correct folder.'); return; }
  const cfg = getConfig();
  cfg._settingsStr = encodeSettings();
  clearOutput(); setStatus('Running\u2026'); setBusy(true); window.pywebview.api.run_patcher(cfg);
}
function restoreVanilla() { clearOutput(); setStatus('Restoring vanilla…'); setBusy(true); window.pywebview.api.restore_vanilla(getConfig()); }
function onDone(rc) {
  setBusy(false);
  if (rc === 0) {
    const outputText = document.getElementById('output').textContent;
    const lines = outputText.split('\n');
    let seedLine = '', spoiler = '';
    for (const l of lines) {
      const ll = l.toLowerCase();
      if (!seedLine && ll.startsWith('seed:') && /\d/.test(l)) seedLine = l.trim();
      if (!spoiler  && ll.includes('spoiler'))                  spoiler  = l.trim();
    }
    // Extract the bare seed number and populate the seed field
    const seedNumMatch = seedLine.match(/Seed:\s*(\d+)/i);
    if (seedNumMatch) {
      document.getElementById('seed').value = seedNumMatch[1];
    }
    // Refresh the settings string to capture the current (post-run) state
    document.getElementById('settingsString').value = encodeSettings();

    let msg = '✓ Done.';
    if (seedLine) msg += '  ' + seedLine;
    if (spoiler)  msg += '  |  ' + spoiler;
    setStatus(msg, 'ok');
    document.getElementById('postRun').className = 'post-run visible';
  } else {
    setStatus('✗ Patcher exited with code ' + rc + '.', 'err');
  }
}
function launchGame() { window.pywebview.api.launch_game(document.getElementById('gameDir').value.trim()); }
function openFolder()  { window.pywebview.api.open_folder(document.getElementById('gameDir').value.trim()); }
function onError(msg) {
  setBusy(false);
  appendOutput('[Error: ' + msg + ']\n');
  setStatus('Error launching patcher.', 'err');
}
window.addEventListener('pywebviewready', async () => {
  const dir = await window.pywebview.api.get_default_dir();
  if (dir) document.getElementById('gameDir').value = dir;
  syncCadeauxConstraints();
});
</script>
</body>
</html>
"""


import re as _re

# Only hide lines that would spoil randomized item placements.
# Everything else can show — vague progress descriptions are fine.
_HIDE = [
    _re.compile(r'Soul audit for'),                # header for item-swap detail block
    _re.compile(r'-> planned='),                   # explicit old→new item placement spoiler
    _re.compile(r'removed from pool'),             # reveals starting item before run ends
]

_ALWAYS_SHOW = _re.compile(r'error|fail|warn|exception|traceback', _re.IGNORECASE)

def _should_show(line: str) -> bool:
    """Return True if this patcher output line should be shown to the user."""
    if _ALWAYS_SHOW.search(line):
        return True
    for pat in _HIDE:
        if pat.search(line):
            return False
    return True


class _Api:
    def __init__(self):
        self._process: "subprocess.Popen[str] | None" = None
        self._window: "webview.Window | None" = None

    def _set_window(self, w: "webview.Window") -> None:
        self._window = w

    def get_default_dir(self) -> str:
        saved = _load_prefs().get("game_dir", "")
        if saved and _looks_like_install(Path(saved)):
            return saved
        return str(DEFAULT_GAME_DIR) if _looks_like_install(DEFAULT_GAME_DIR) else ""

    def validate_dir(self, game_dir: str) -> bool:
        return _looks_like_install(Path(game_dir))

    def browse_dir(self, current: str) -> "str | None":
        assert self._window is not None
        result = self._window.create_file_dialog(
            webview.FileDialog.FOLDER,
            directory=current or str(DEFAULT_GAME_DIR),
        )
        return result[0] if result else None

    def launch_game(self, game_dir: str) -> None:
        import subprocess
        from pathlib import Path
        exe = Path(game_dir) / "thoth_x64_patched.exe"
        if exe.exists():
            subprocess.Popen([str(exe)], cwd=game_dir)
        else:
            # Fall back to opening the folder so the user can find it
            self.open_folder(game_dir)

    def open_folder(self, game_dir: str) -> None:
        import subprocess
        subprocess.Popen(["explorer", game_dir])

    def run_patcher(self, config: dict) -> None:
        self._launch(config, restore=False)

    def restore_vanilla(self, config: dict) -> None:
        self._launch(config, restore=True)

    def _build_cmd(self, config: dict, *, restore: bool) -> list:
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, _PATCHER_FLAG]
        else:
            cmd = [sys.executable, str(PATCHER)]

        game_dir = config.get("gameDir", "").strip()
        if not game_dir:
            raise ValueError("No game directory specified.")
        if not _looks_like_install(Path(game_dir)):
            raise ValueError(f"Game directory does not look like a Shadow Man Remastered install: {game_dir}")
        cmd += ["--game-dir", game_dir]
        prefs = _load_prefs()
        if prefs.get("game_dir") != game_dir:
            prefs["game_dir"] = game_dir
            _save_prefs(prefs)

        if restore:
            cmd.append("--restore")
            return cmd

        seed = config.get("seed", "").strip()
        if seed:
            cmd += ["--seed", seed]

        preset = config.get("gatePreset", "none")
        if preset != "none":
            cmd += ["--gate-preset", preset]

        flag_map = [
            ("shuffleMusic",            "--shuffle-music"),
            ("shuffleVoices",           "--shuffle-voices"),
            ("shuffleWeaponsSfx",       "--shuffle-weapons-sfx"),
            ("shuffleEnemiesSfx",       "--shuffle-enemies-sfx"),
            ("shuffleSky",              "--shuffle-sky"),
            ("patchTracker",            "--patch-tracker"),
        ]
        for key, flag in flag_map:
            if config.get(key):
                cmd.append(flag)

        # Handle shuffle-key-items with random support (default-on toggle)
        ski_val = config.get("shuffleKeyItems")
        if ski_val == 'random':
            cmd.append("--shuffle-key-items-random")
        elif ski_val is False or ski_val == False:
            cmd.append("--no-shuffle-progression")
        # True is the default — nothing needed

        # Handle shuffle-gad with random support (default-on toggle)
        gad_val = config.get("shuffleGad")
        if gad_val == 'random':
            cmd.append("--shuffle-gad-temples-random")
        elif gad_val is False or gad_val == False:
            cmd.append("--no-shuffle-gad-temples")
        # True is the default — nothing needed

        # Handle shuffle-weapons with random support (default-on toggle)
        sw_val = config.get("shuffleWeapons")
        if sw_val == 'random':
            cmd.append("--shuffle-weapons-random")
        elif sw_val is False or sw_val == False:
            cmd.append("--no-shuffle-weapons")

        # Handle shuffle-lore with random support (default-on toggle)
        sl_val = config.get("shuffleLore")
        if sl_val == 'random':
            cmd.append("--shuffle-lore-random")
        elif sl_val is False or sl_val == False:
            cmd.append("--no-shuffle-lore")

        # Handle shuffle-light-soul with random support (default-off toggle)
        sls_val = config.get("shuffleLightSoul")
        if sls_val == 'random':
            cmd.append("--shuffle-light-soul-random")
        elif sls_val:
            cmd.append("--shuffle-light-soul")

        # Handle shuffle-prisms with random support (default-off toggle)
        spr_val = config.get("shufflePrisms")
        if spr_val == 'random':
            cmd.append("--shuffle-prisms-random")
        elif spr_val:
            cmd.append("--shuffle-prisms")

        # Handle shuffle-retractors with random support (default-on toggle)
        sret_val = config.get("shuffleRetractors")
        if sret_val == 'random':
            cmd.append("--shuffle-retractors-random")
        elif sret_val is False or sret_val == False:
            cmd.append("--no-shuffle-retractors")

        # Handle shuffle-accumulators with random support (default-on toggle)
        sacc_val = config.get("shuffleAccumulators")
        if sacc_val == 'random':
            cmd.append("--shuffle-accumulators-random")
        elif sacc_val is False or sacc_val == False:
            cmd.append("--no-shuffle-accumulators")

        # Handle shuffle-eclipsers with random support (default-on toggle)
        sec_val = config.get("shuffleEclipsers")
        if sec_val == 'random':
            cmd.append("--shuffle-eclipsers-random")
        elif sec_val is False or sec_val == False:
            cmd.append("--no-shuffle-eclipsers")

        # Handle SL threshold mode with random support
        rst_val = config.get("randomizeSoulThresholds", "off")
        if rst_val == 'random':
            cmd.append("--soul-threshold-mode-random")
        elif rst_val and rst_val != 'off':
            cmd.extend(["--soul-threshold-mode", rst_val])

        # Handle death penalty with random support
        death_penalty = str(config.get("deathPenalty", "0")).strip()
        if death_penalty == "random":
            cmd.append("--death-penalty-random")
        elif death_penalty and death_penalty != "0":
            cmd += ["--death-penalty", death_penalty]

        # Handle shuffle-enemies with random support
        enemies_val = config.get("shuffleEnemies")
        if enemies_val == 'random':
            cmd.append("--shuffle-enemies-random")
        elif enemies_val:
            cmd.append("--shuffle-enemies")

        # Handle shuffle-true-forms with random support
        trueforms_val = config.get("shuffleTrueforms")
        if trueforms_val == 'random':
            cmd.append("--shuffle-true-forms-random")
        elif trueforms_val:
            cmd.append("--shuffle-true-forms")

        starting_item = config.get("startingItem", "")
        if starting_item == "random":
            cmd.append("--random-starting-item")
        elif starting_item:
            cmd += ["--starting-item", starting_item]

        insanity_val = config.get("insanity", 0)
        if str(insanity_val) == "random":
            cmd += ["--insanity", "random"]
        elif int(insanity_val) > 0:
            cmd += ["--insanity", str(int(insanity_val))]

        if enemies_val == 'random' or enemies_val:
            cmd += ["--enemy-mode", config.get("enemyMode", "difficulty")]
            mix_val = config.get("enemyMixMovement")
            if mix_val == 'random':
                cmd.append("--enemy-mix-movement-random")
            elif mix_val:
                cmd.append("--enemy-mix-movement")
            uncap_val = config.get("enemyUncapCounts")
            if uncap_val == 'random':
                cmd.append("--enemy-uncap-counts-random")
            elif uncap_val:
                cmd.append("--enemy-uncap-counts")

        if config.get("shuffleAmbients"):
            cmd.append("--shuffle-ambients")

        max_sl = config.get("maxSl", "").strip()
        if max_sl:
            cmd += ["--max-sl", max_sl]

        open_gates_n = config.get("openGatesN", "").strip()
        if open_gates_n:
            cmd += ["--open-gates", open_gates_n]

        prog = config.get("progBalance", "50")
        if str(prog) != "50":
            cmd += ["--progression-balancing", str(prog)]

        entrance_mode = config.get("entranceMode", "off")
        if entrance_mode and entrance_mode != "off":
            cmd += ["--entrance-mode", entrance_mode]

        altar_cadeaux = str(config.get("altarCadeauxRequired", "100")).strip()
        if altar_cadeaux and altar_cadeaux != "100":
            cmd += ["--altar-cadeaux-required", altar_cadeaux]

        fog_cadeaux = str(config.get("fogometersCadeauxRequired", "666")).strip()
        if fog_cadeaux and fog_cadeaux != "666":
            cmd += ["--fogometers-cadeaux-required", fog_cadeaux]

        starting_health = str(config.get("startingHealth", "5")).strip()
        if starting_health and starting_health != "5":
            cmd += ["--starting-health", starting_health]

        altar_health = str(config.get("altarHealthGrant", "1")).strip()
        if altar_health and altar_health != "1":
            cmd += ["--altar-health-grant", altar_health]

        settings_str = config.get("_settingsStr", "").strip()
        if settings_str:
            cmd += ["--settings-string", settings_str]

        return cmd

    def _launch(self, config: dict, *, restore: bool) -> None:
        if self._process is not None:
            return
        assert self._window is not None

        try:
            cmd = self._build_cmd(config, restore=restore)
        except ValueError as exc:
            self._window.evaluate_js(f"onError({json.dumps(str(exc))})")
            return

        cmd = self._build_cmd(config, restore=restore)
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
            self._window.evaluate_js(f"onError({json.dumps(str(exc))})")
            return

        threading.Thread(target=self._reader_thread, daemon=True).start()

    def _reader_thread(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        assert self._window is not None
        try:
            for line in self._process.stdout:
                if _should_show(line):
                    self._window.evaluate_js(f"appendOutput({json.dumps(line)})")
        finally:
            self._process.wait()
            rc = self._process.returncode
            self._process = None
            if rc == 0:
                note = (
                    "\n"
                    "\u25ba To play: launch thoth_x64_patched.exe from your Shadow Man Remastered\n"
                    "  install folder, or click \"Launch Game\" above.\n"
                )
                self._window.evaluate_js(f"appendOutput({json.dumps(note)})")
            self._window.evaluate_js(f"onDone({rc})")


if __name__ == "__main__":
    api = _Api()
    window = webview.create_window(
        "Shadow Man Remastered Randomizer",
        html=_HTML,
        js_api=api,
        width=1060,
        height=960,
    )
    api._set_window(window)
    webview.start()
