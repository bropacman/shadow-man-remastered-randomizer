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
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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

  input[type=text], input[type=number], select {
    background: #222226; border: 1px solid var(--border);
    border-radius: 5px; color: var(--text);
    font-size: 12px; padding: 5px 9px; outline: none; transition: border-color .15s;
  }
  input[type=text]:focus, input[type=number]:focus, select:focus { border-color: var(--accent); }
  input.dir-input   { flex: 1; }
  input.seed-input  { width: 150px; }
  input.maxsl-input { width: 60px; }
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
  .btn-run     { background: var(--accent2); color: #fff; font-size: 13px; padding: 7px 22px; }
  .btn-restore { background: #3a1a1a; color: #e8a0a0; border: 1px solid #522020; font-size: 13px; padding: 7px 22px; }

  /* Checkboxes */
  .check-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2px 20px; }
  .check-label { display: flex; align-items: center; gap: 7px; padding: 4px 0; cursor: pointer; color: #ccc; }
  .check-label:hover { color: #fff; }
  input[type=checkbox] { width: 14px; height: 14px; accent-color: #4a7a5a; cursor: pointer; flex-shrink: 0; }

  /* Cosmetic row */
  .cosm-row { display: flex; gap: 20px; flex-wrap: wrap; }

  /* Divider */
  .divider { border: none; border-top: 1px solid var(--border); margin: 10px 0; }

  /* Enemy mode */
  .enemy-row { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
  .enemy-row .lbl { color: var(--muted); font-size: 11px; white-space: nowrap; }

  /* Slider */
  input[type=range] {
    -webkit-appearance: none; appearance: none;
    width: 160px; height: 4px; border-radius: 2px;
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
  }
  .tip:hover .tip-box { opacity: 1; }

  /* Status / terminal */
  .gate-desc { color: var(--muted); font-size: 11px; font-style: italic; }
  .actions { display: flex; gap: 10px; margin-bottom: 10px; }
  .terminal {
    background: #0c0c0e; border: 1px solid var(--border); border-radius: 8px;
    font-family: 'Cascadia Code', 'Consolas', 'Courier New', monospace;
    font-size: 11px; height: 140px; overflow-y: auto;
    padding: 10px 13px; white-space: pre-wrap; word-break: break-all;
    color: #a0a0a8; line-height: 1.55; margin-bottom: 8px;
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
  <h1>Shadow Man Remastered Randomizer</h1>
  <p>Generates a guaranteed-beatable seed &mdash; drop the .kpf in your mods folder and play.</p>
</div>

<!-- Row 1: Game Dir + Seed -->
<div class="card-row" style="align-items:stretch">
  <div class="card" style="flex:1;min-width:280px;display:flex;flex-direction:column;justify-content:center">
    <div class="card-title">Game Directory</div>
    <div class="row">
      <input type="text" id="gameDir" class="dir-input" placeholder="Path to Shadow Man Remastered&hellip;">
      <button class="btn-ghost" onclick="browseDir()">Browse&hellip;</button>
    </div>
  </div>
  <div class="card" style="flex:1 ;min-width:280px">
    <div class="card-title">Seed</div>
    <div class="row">
      <input type="number" id="seed" class="seed-input" placeholder="random" min="1" max="2147483647">
      <button class="btn-dice" onclick="randomizeSeed()">&#127922;&ensp;Randomize</button>
    </div>
    <div class="hint" style="margin-top:6px">Leave blank for a new random seed each run.</div>
  </div>
</div>

<!-- Row 2: Gameplay+Starting Item (left) | Coffin Gates+Progression (right) -->
<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;align-items:stretch">


  <!-- Left: Gameplay checkboxes + Starting Item -->
  <div class="card" style="flex:1 ;min-width:280px">
    <div class="card-title">Gameplay</div>
    <div class="check-grid">
      <label class="check-label">
        <input type="checkbox" id="shuffleKeyItems" class="default-on" checked>
        Shuffle Key Items
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles key progression items (Engineer&rsquo;s Key, Poign&eacute;, Baton, Flambeau, Marteau, Calabash, Retractors, etc.) using assumed-fill logic that guarantees every seed is beatable.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleGad" class="default-on" checked>
        Shuffle Gad Pickups
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Converts Gad powers (Touch, Walk, Swim) into physical pickups and shuffles them across temple locations. Requires an EXE patch, which is applied automatically.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleWeapons" class="default-on" checked>
        Shuffle Weapons
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles weapons (Asson, Shotgun, Enseigne, MP5, T&ecirc;te de Mort, Desert Eagle) across item locations. Uncheck to leave weapons in their vanilla spots.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleLore" class="default-on" checked>
        Shuffle Lore
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles lore items (Book of Shadows, Prophecy, Jack&rsquo;s Schematic) across locations. Uncheck to leave them in vanilla positions.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleLightSoul">
        Shuffle Light Soul
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Includes the Light Soul bonus item in the shuffle pool. Off by default as it can affect run balance.</span></span>
      </label>
    </div>
    <hr class="divider">
    <div class="card-title" style="margin-bottom:8px">Starting Item</div>
    <div class="row">
      <select id="startingItem">
        <option value="">None</option>
        <option value="random">Random</option>
        <option value="RSC_X_ENGINEERS_KEY">⭐ Engineers Key</option>
        <option value="RSC_X_BATON">Baton</option>
        <option value="RSC_X_CALABASH">Calabash</option>
        <option value="RSC_X_ECLIPSER_PART1">Eclipser Part 1</option>
        <option value="RSC_X_ECLIPSER_PART2">Eclipser Part 2</option>
        <option value="RSC_X_ECLIPSER_PART3">Eclipser Part 3</option>
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
        <option value="RSC_X_VIOLATOR">Violator</option>
      </select>
      <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Places a bonus item at the church in Louisiana Swampland at game start. The selected item is removed from the shuffle pool.</span></span>
    </div>
  </div>

  <!-- Right column: Coffin Gates + Progression stacked -->
  <div style="display:flex;flex-direction:column;gap:10px">

    <div class="card" style="flex:1 ;min-width:280px">
      <div class="card-title">
        Coffin Gate Soul Levels
        <span class="tip" style="vertical-align:middle">
          <span class="tip-icon">?</span>
          <span class="tip-box">Shuffles the soul level (SL) thresholds on deadside coffin gates. Higher SL gates require more Dark Souls collected before they open. Starting gates are always kept at SL&le;3 so the game is immediately playable.</span>
        </span>
      </div>
      <div class="row">
        <select id="gatePreset" onchange="updateGateDesc()">
          <option value="none">none</option>
          <option value="open">open</option>
          <option value="easy">easy</option>
          <option value="medium">medium</option>
          <option value="hard">hard</option>
          <option value="chaos">chaos</option>
        </select>
        <span class="gate-desc" id="gateDesc">default shuffle</span>
      </div>
      <div class="row" style="margin-top:8px">
        <label style="color:var(--muted);font-size:11px;white-space:nowrap">Max SL cap:</label>
        <input type="number" id="maxSl" class="maxsl-input" value="10" min="1" max="10">
        <span class="hint">1&ndash;10, blank = no cap</span>
        <span class="tip">
          <span class="tip-icon">?</span>
          <span class="tip-box">Caps the highest soul level any shuffled gate can receive. Set to 10 to allow all values; lower values make gates more accessible.</span>
        </span>
      </div>
    </div>

    <div class="card" style="flex:1 ;min-width:280px">
      <div class="card-title">Progression</div>
      <div class="row" style="margin-bottom:10px">
        <span style="color:var(--muted);font-size:11px;white-space:nowrap">Insanity Tier</span>
        <select id="insanity" style="flex:1;margin-left:8px">
          <option value="0">Off</option>
          <option value="1">Tier 1 &mdash; Soul &amp; Govi slots</option>
          <option value="2">Tier 2 &mdash; + Cadeaux slots</option>
          <option value="3">Full &mdash; All slots</option>
        </select>
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Controls where key progression items can be placed.<br><b>Tier 1:</b> Soul &amp; Govi pickup slots.<br><b>Tier 2:</b> Also Cadeaux slots.<br><b>Full:</b> Any slot in the game. Wildly random.</span></span>
      </div>
      <div class="row">
        <span style="color:var(--muted);font-size:11px;white-space:nowrap">Balancing:</span>
        <input type="range" id="progBalance" min="0" max="100" value="50"
               oninput="document.getElementById('progBalVal').textContent=this.value">
        <span class="slider-val" id="progBalVal">50</span>
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Controls how deep into the world progression items tend to be placed. Default 50 is balanced. 0 = items placed early, 100 = items pushed deep.</span></span>
      </div>
      <div class="hint" style="margin-top:6px">0 = items placed early &nbsp;&nbsp; 100 = items pushed deep</div>
    </div>

  </div>
</div>

<!-- Row 3: Enemies + Cosmetics -->
<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:10px;align-items:stretch">
  <div class="card" style="flex:1 ;min-width:280px">
    <div class="card-title">Enemies</div>
    <div class="check-grid" style="margin-bottom:10px">
      <label class="check-label">
        <input type="checkbox" id="shuffleEnemies" onchange="onEnemiesChange()">
        Shuffle Enemies
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Randomizes enemy types in each level. Use the Enemy Mode dropdown to control how they are assigned.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleTrueforms">
        Shuffle Trueforms
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles true-form enemy positions into the same pool as regular enemies. Only meaningful when Shuffle Enemies is also enabled.</span></span>
      </label>
    </div>
    <div class="enemy-row">
      <span class="lbl">Enemy Mode:</span>
      <select id="enemyMode" disabled>
        <option value="difficulty">difficulty &mdash; tier-weighted</option>
        <option value="full">full &mdash; random by move type</option>
        <option value="contextual">contextual &mdash; area pools</option>
      </select>
      <span class="tip"><span class="tip-icon">?</span><span class="tip-box"><b>difficulty:</b> enemies replaced by others of a similar tier, weighted by area depth.<br><b>full:</b> fully random within the same movement type (ground/flying/etc).<br><b>contextual:</b> shuffled within context groups (deadside/liveside/prison stay separated).</span></span>
    </div>
    <span class="hint" id="enemyHint" style="margin-left:4px">enable Shuffle Enemies to unlock</span>
  </div>

  <div class="card" style="margin-bottom:0; flex: 1">
    <div class="card-title">Cosmetic Shuffles</div>
    <div class="cosm-row" style="flex-direction:column;gap:2px">
      <label class="check-label">
        <input type="checkbox" id="shuffleMusic">
        Shuffle Music
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Randomly reassigns music tracks across all levels. No effect on gameplay or logic.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleVoices">
        Shuffle Voice Lines
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles Shadow Man&rsquo;s generic ambient voice lines. Purely cosmetic.</span></span>
      </label>
      <label class="check-label">
        <input type="checkbox" id="shuffleWeaponsSfx">
        Shuffle Weapon SFX
        <span class="tip"><span class="tip-icon">?</span><span class="tip-box">Shuffles weapon fire and reload sounds within each weapon category. Purely cosmetic.</span></span>
      </label>
    </div>
    <div class="hint" style="margin-top:10px">No effect on logic or seed beatable-ness.</div>
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
  none:   'default shuffle',
  open:   'all gates free, light soul requirements',
  easy:   'light shuffle, SL7 cap, tight variance',
  medium: 'standard shuffle, SL8 cap',
  hard:   'full shuffle, no SL cap',
  chaos:  'fully unconstrained'
};
function updateGateDesc() {
  document.getElementById('gateDesc').textContent = GATE_DESCS[document.getElementById('gatePreset').value] || '';
}
function onEnemiesChange() {
  const on = document.getElementById('shuffleEnemies').checked;
  document.getElementById('enemyMode').disabled = !on;
  document.getElementById('enemyHint').textContent = on ? '' : 'enable Shuffle Enemies to unlock';
}
function randomizeSeed() {
  document.getElementById('seed').value = Math.floor(Math.random() * 2147483647) + 1;
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
    maxSl:            document.getElementById('maxSl').value.trim(),
    shuffleGad:       document.getElementById('shuffleGad').checked,
    startingItem:     document.getElementById('startingItem').value,
    shuffleEnemies:   document.getElementById('shuffleEnemies').checked,
    shuffleTrueforms: document.getElementById('shuffleTrueforms').checked,
    shuffleMusic:     document.getElementById('shuffleMusic').checked,
    shuffleVoices:    document.getElementById('shuffleVoices').checked,
    shuffleWeaponsSfx:document.getElementById('shuffleWeaponsSfx').checked,
    insanity:         parseInt(document.getElementById('insanity').value),
    shuffleLightSoul: document.getElementById('shuffleLightSoul').checked,
    shuffleKeyItems:  document.getElementById('shuffleKeyItems').checked,
    shuffleWeapons:   document.getElementById('shuffleWeapons').checked,
    shuffleLore:      document.getElementById('shuffleLore').checked,
    enemyMode:        document.getElementById('enemyMode').value.split('—')[0].trim(),
    progBalance:      document.getElementById('progBalance').value,
  };
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
  clearOutput(); setStatus('Running\u2026'); setBusy(true); window.pywebview.api.run_patcher(getConfig());
}
function restoreVanilla() { clearOutput(); setStatus('Restoring vanilla…'); setBusy(true); window.pywebview.api.restore_vanilla(getConfig()); }
function onDone(rc) {
  setBusy(false);
  if (rc === 0) {
    const lines = document.getElementById('output').textContent.split('\n');
    let seed = '', spoiler = '';
    for (const l of lines) {
      const ll = l.toLowerCase();
      if (!seed    && ll.includes('seed')    && /\d/.test(l)) seed    = l.trim();
      if (!spoiler && ll.includes('spoiler'))                  spoiler = l.trim();
    }
    let msg = '✓ Done.';
    if (seed)    msg += '  ' + seed;
    if (spoiler) msg += '  |  ' + spoiler;
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
});
</script>
</body>
</html>
"""


import re as _re

# Lines from patcher.py that are internal debug noise — hidden from the GUI.
# Errors / failures always show through regardless.
_HIDE = [
    _re.compile(r'^\s+\w[\w\s]+:\s+\d+ souls'),   # per-level item summary
    _re.compile(r'Soul audit for'),
    _re.compile(r'\s+0x[0-9A-Fa-f]+:'),            # hex address detail
    _re.compile(r'Verification:\s+\d+ passed'),
    _re.compile(r'\[(QUEST|PICKUPS|FX|INSTANCE|RESOURCE|ENEMIES)\]\s'),
    _re.compile(r'RSC patches:'),
    _re.compile(r'\d+ RSC patches generated'),
    _re.compile(r'Patching RSC items'),
    _re.compile(r'Applying RSC patches'),
    _re.compile(r'Object map:'),
    _re.compile(r'Parsing RSC files'),
    _re.compile(r'Spoiler log written to:'),        # interim line; final "Spoiler log:" kept
    _re.compile(r'Core data KPF:'),
    _re.compile(r'Extracted \d+ file'),
    _re.compile(r'Assumed fill\s*:'),
    _re.compile(r'Soul thresholds:'),
    _re.compile(r'-> planned='),
    _re.compile(r'\[fx\.rsc|instance\.rsc|enemies\.rsc|quest\.rsc|resource\.rsc|pickups\.rsc'),
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
            ("shuffleGad",        "--shuffle-gad-temples"),
            ("shuffleEnemies",    "--shuffle-enemies"),
            ("shuffleTrueforms",  "--shuffle-true-forms"),
            ("shuffleMusic",      "--shuffle-music"),
            ("shuffleVoices",     "--shuffle-voices"),
            ("shuffleWeaponsSfx", "--shuffle-weapons-sfx"),
            ("shuffleLightSoul",  "--shuffle-light-soul"),
        ]
        for key, flag in flag_map:
            if config.get(key):
                cmd.append(flag)

        starting_item = config.get("startingItem", "")
        if starting_item == "random":
            starting_item = random.choice(list(STARTING_ITEM_POOL.values()))
        if starting_item:
            cmd += ["--starting-item", starting_item]

        insanity_tier = int(config.get("insanity", 0))
        if insanity_tier > 0:
            cmd += ["--insanity", str(insanity_tier)]

        if config.get("shuffleEnemies"):
            cmd += ["--enemy-mode", config.get("enemyMode", "difficulty")]

        max_sl = config.get("maxSl", "").strip()
        if max_sl:
            cmd += ["--max-sl", max_sl]

        prog = config.get("progBalance", "50")
        if str(prog) != "50":
            cmd += ["--progression-balancing", str(prog)]

        if not config.get("shuffleKeyItems", True):
            cmd.append("--no-shuffle-progression")
        if not config.get("shuffleWeapons", True):
            cmd.append("--no-shuffle-weapons")
        if not config.get("shuffleLore", True):
            cmd.append("--no-shuffle-lore")

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
        width=820,
        height=900,
    )
    api._set_window(window)
    webview.start()
