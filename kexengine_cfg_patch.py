"""
Edits the player's kexengine.cfg (plain-text bind/seta cvar file) at patch
time, so cvar-backed toggles that the game only reads once at process boot
(like the "I like Dead Side Guns" secret) can be forced on/off without any
EXE or memory patching at all.

Default location:
    %USERPROFILE%\\Saved Games\\Nightdive Studios\\Shadowman EX\\kexengine.cfg
(the same folder Nightdive's KEX engine itself reads at boot and overwrites
with its own live state on exit).

Background (see CLAUDE.md, "Deadside Guns secret (Secret 14) force-on" for
the full investigation): the Deadside Guns secret's real persistent state
is just `seta g_deadsidegunmode "1"` in this file -- confirmed live that
editing it (with the game NOT running) and then launching the game applies
it correctly. Editing it WHILE the game is running has no effect until a
full quit+relaunch (the engine reads this file once at boot, then holds
its own live cvar state in memory and overwrites the file with that live
state on exit) -- so this module is patch-time only, matching every other
patch this project applies before the player ever launches the game.

Imported directly by both patcher.py (standalone) and ap_patcher.py (AP),
not duplicated -- same pattern as dark_engine_patch.py/entrance_randomizer.py.
"""
from pathlib import Path
import re


def kexengine_cfg_path() -> Path:
    """Default kexengine.cfg location for this game (per-user Saved Games)."""
    return Path.home() / "Saved Games" / "Nightdive Studios" / "Shadowman EX" / "kexengine.cfg"


def set_cvar(cfg_path: Path, cvar: str, value, *, dry_run: bool = False) -> bool:
    """
    Set `seta <cvar> "<value>"` in the kexengine.cfg at `cfg_path`, replacing
    an existing line for that cvar if present, or appending a new line if
    not. `value` may be a bool (encoded as "1"/"0") or anything else
    (encoded via str()).

    Returns True if the cvar is now set correctly (including "already was,
    no write needed" and, under dry_run, "would be set"). Returns False if
    the file doesn't exist -- this module never creates the Saved Games
    folder structure blindly, since that would require guessing at engine
    defaults for every other cvar; the player needs to have launched the
    game at least once already -- or on any read/write failure.
    """
    if not cfg_path.exists():
        print(f"  [kexengine.cfg] Not found at {cfg_path} -- skipping "
              f"{cvar} (launch the game at least once first, then re-patch).")
        return False
    try:
        text = cfg_path.read_text()
    except OSError as exc:
        print(f"  [kexengine.cfg] Couldn't read {cfg_path}: {exc}")
        return False

    value_str = "1" if value is True else ("0" if value is False else str(value))
    new_line = f'seta {cvar} "{value_str}"'
    # No trailing \s* here: in MULTILINE mode, $ already matches right
    # before the line's own newline without consuming it. Adding \s* would
    # greedily eat that newline into the match, so substitution would strip
    # it from the file -- caught live: a "value already correct" case was
    # rewriting the file anyway, just to remove its trailing newline.
    pattern = re.compile(rf'^seta {re.escape(cvar)} ".*"$', re.MULTILINE)

    if pattern.search(text):
        new_text = pattern.sub(new_line, text, count=1)
    else:
        sep = "" if (not text or text.endswith("\n")) else "\n"
        new_text = text + sep + new_line + "\n"

    if new_text == text:
        print(f"  [kexengine.cfg] {cvar} already \"{value_str}\" -- no change needed.")
        return True

    if dry_run:
        print(f"  [kexengine.cfg] (dry-run) would set {cvar} = \"{value_str}\"")
        return True

    try:
        cfg_path.write_text(new_text)
    except OSError as exc:
        print(f"  [kexengine.cfg] Couldn't write {cfg_path}: {exc}")
        return False
    print(f"  [kexengine.cfg] Set {cvar} = \"{value_str}\"")
    return True


def apply_deadside_guns_toggle(enabled: bool, *, cfg_path: Path = None, dry_run: bool = False) -> bool:
    """Force the 'I like Dead Side Guns' secret (g_deadsidegunmode) on/off at patch time."""
    return set_cvar(cfg_path or kexengine_cfg_path(), "g_deadsidegunmode", bool(enabled), dry_run=dry_run)
