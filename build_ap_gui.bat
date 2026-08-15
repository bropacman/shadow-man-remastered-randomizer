@echo off
setlocal
cd /d "%~dp0"

echo ====================================================
echo   Shadow Man Remastered - AP Companion - Build System
echo ====================================================
echo.

:: 1. Environment Preparation
echo [1/3] Checking dependencies...
:: Same toolchain as build.bat (gui.py's own build) -- pywebview/pyinstaller/
:: pyyaml cover both GUIs -- PLUS keystone-engine/capstone, which build.bat
:: does NOT need: ap_gui.py's frozen exe re-invokes itself as
:: apply_ap_seed.py, which pulls in ap_patcher.py -> secret_mode_section_patch.py,
:: the only place in this repo that imports keystone/capstone. See
:: requirements.txt and RELEASING.md.
pip install --quiet --upgrade pywebview pyinstaller pyyaml keystone-engine capstone
if errorlevel 1 (
    echo.
    echo ERROR: Dependency installation failed.
    echo Ensure Python is added to your PATH.
    pause & exit /b 1
)

:: 2. The Build Process
echo [2/3] Building executable from Spec file...
echo.
:: --distpath/--workpath keep this fully separate from both the standalone
:: exe's own build (build.bat -> dist\standalone\) and the .apworld build
:: (build_apworld.bat -> dist\apworld\) -- three independent artifacts from
:: one repo, see RELEASING.md.
pyinstaller --noconfirm --clean --distpath "dist\ap_companion" --workpath "build\ap_companion" "ap_gui.spec"

if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller build failed.
    echo Check the console output above for missing modules or syntax errors.
    pause & exit /b 1
)

:: 3. Cleanup and Verification
echo.
echo [3/3] Build Complete!
echo.
if exist "dist\ap_companion\shadow_man_ap_companion.exe" (
    echo SUCCESS: Executable created at:
    echo   dist\ap_companion\shadow_man_ap_companion.exe
    echo.
    echo Note: You can now distribute the single .exe file.
) else (
    echo ERROR: Build finished but the .exe was not found in dist\ap_companion.
)

echo.
pause
