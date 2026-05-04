@echo off
setlocal
cd /d "%~dp0"

echo ====================================================
echo   Shadow Man Remastered Randomizer - Build System
echo ====================================================
echo.

:: 1. Environment Preparation
echo [1/3] Checking dependencies...
:: We ensure pyinstaller and pywebview are present. 
:: 'yaml' is often required if you use config files.
pip install --quiet --upgrade pywebview pyinstaller pyyaml
if errorlevel 1 (
    echo.
    echo ERROR: Dependency installation failed. 
    echo Ensure Python is added to your PATH.
    pause & exit /b 1
)

:: 2. The Build Process
echo [2/3] Building executable from Spec file...
echo.
:: Using --clean ensures we don't use stale data from previous failed builds.
:: We point directly to the .spec file to preserve your custom configuration.
pyinstaller --noconfirm --clean "Shadow Man Randomizer.spec"

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
if exist "dist\shadow_man_randomizer.exe" (
    echo SUCCESS: Executable created at:
    echo   dist\shadow_man_randomizer.exe
    echo.
    echo Note: You can now distribute the single .exe file.
) else (
    echo ERROR: Build finished but the .exe was not found in the dist folder.
)

echo.
pause