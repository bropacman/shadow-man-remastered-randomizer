@echo off
setlocal
cd /d "%~dp0"
:: Rebuild everything in dependency order. Extra arguments pass through,
:: e.g.  build_all.bat --only apworld   or   build_all.bat --skip standalone
python build_all.py %*
echo.
pause
