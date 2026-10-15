@echo off
setlocal
:: Lives in scripts\; run from the repo root so the .spec files and
:: requirements.txt resolve.
cd /d "%~dp0.."

echo ====================================================
echo   Shadow Man Remastered Randomizer - AP World Build
echo ====================================================
echo.

:: Path to the AP world's own source. Defaults to apworld\ -- this repo's
:: own copy since the 2026-09-16 monorepo merge (see
:: docs\PHASE4_ARCHITECTURE_SCOPING.md). Pass an argument to build from
:: somewhere else instead, e.g. a separate Archipelago checkout during
:: local dev:
::   scripts\build_apworld.bat "D:\Archipelago\worlds\shadowman"
set "AP_SOURCE=%~1"
if "%AP_SOURCE%"=="" set "AP_SOURCE=%CD%\apworld"

if not exist "%AP_SOURCE%\__init__.py" (
    echo ERROR: "%AP_SOURCE%" doesn't look like a worlds\shadowman folder ^(no __init__.py^).
    echo Pass the correct path as an argument, e.g.:
    echo   scripts\build_apworld.bat "C:\path\to\Archipelago\worlds\shadowman"
    pause & exit /b 1
)

echo Source: %AP_SOURCE%
echo.

echo [1/2] Checking for drift against this repo's own shared-file copies...
echo ^(non-fatal -- review any DRIFT lines below, then this continues regardless.
echo  build_all.py runs this same check as a hard BLOCKING gate instead.^)
echo.
python tools\check_apworld_sync.py --ap-dir "%AP_SOURCE%"
echo.

echo [2/2] Packaging .apworld...
python scripts\build_apworld.py --source "%AP_SOURCE%" --output "dist\apworld\shadowman.apworld"

if errorlevel 1 (
    echo.
    echo ERROR: apworld build failed. Check the console output above.
    pause & exit /b 1
)

echo.
echo SUCCESS: dist\apworld\shadowman.apworld
echo.
pause
