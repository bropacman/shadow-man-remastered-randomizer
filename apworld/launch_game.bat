@echo off
REM launch_game.bat -- single entry point for playing an applied AP seed
REM (2026-08-04). Use this instead of double-clicking thoth_x64_patched.exe
REM directly -- point a desktop shortcut, or a Steam launch-options wrapper,
REM at this file instead.
REM
REM Starts the AP client first, THEN launches the game -- the correct
REM order, since client.py is the one that injects ShadowManOverlay.dll
REM into the game process once it attaches (see CLAUDE.md's 2026-08-04
REM "in-game connect/console panel" writeup for why this ordering matters
REM and why the DLL alone can't bootstrap this on its own).
REM
REM Safe to run even if client.py is already open from a previous session:
REM it has its own single-instance guard (a named Windows mutex, checked
REM at startup in client.py's launch()) -- a redundant launch here just
REM prints a warning and exits immediately instead of running two copies
REM side by side.

start "" cmd /c ""%~dp0launch_client.bat""

REM Give the client a moment's head start so it's already polling for the
REM game process by the time it appears. Not strictly required -- client.py
REM retries every second regardless -- just avoids a guaranteed one-poll
REM delay on the very first attach.
timeout /t 2 /nobreak >nul

REM Filled in 2026-08-04 from ap_gui.py's saved game_dir (gui_prefs.json).
set GAME_EXE=C:\Program Files (x86)\Steam2\steamapps\common\Shadow Man Remastered\thoth_x64_patched.exe

if not exist "%GAME_EXE%" (
    echo [launch_game.bat] GAME_EXE is not set correctly -- edit this file
    echo and point it at your real thoth_x64_patched.exe path.
    pause
    exit /b 1
)

start "" "%GAME_EXE%"
