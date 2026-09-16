@echo off
REM launch_client.bat -- auto-launch hook for ShadowManOverlay.dll (2026-08-04).
REM
REM The overlay DLL (overlay_dll/src/dllmain.cpp's TryAutoLaunchClient)
REM runs this automatically a few seconds after it's injected into
REM thoth_x64_patched.exe, ONLY if no client.py has connected to the
REM overlay's IPC socket by then. If you already start the client
REM yourself (Archipelago Launcher menu, a shortcut, etc.) before this
REM fires, the DLL sees that connection and never touches this file at
REM all -- safe to leave in place either way.
REM
REM This runs the exact same "Shadow Man Remastered Client" launcher
REM component the Archipelago Launcher's own menu runs (see
REM worlds/shadowman/__init__.py's components.append(...)), using this
REM install's real virtualenv Python -- same effect as picking it from
REM the Launcher menu yourself, just automatic. Update the two paths
REM below if this Archipelago checkout or its venv ever moves.

cd /d "C:\Users\jonat\Documents\Archipelago-0.6.7"
".venv\Scripts\python.exe" "Launcher.py" "Shadow Man Remastered Client"
