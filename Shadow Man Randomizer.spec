# -*- mode: python ; coding: utf-8 -*-
import os
import sys

# Folders, native libraries and hidden imports shared with ap_gui.spec
# (the AP Companion) live in tools/pyinstaller_shared.py, so both exes always get the same ones.
sys.path.insert(0, os.path.join(SPECPATH, "tools"))
from pyinstaller_shared import datas, binaries, HIDDEN_IMPORTS

a = Analysis(
    ['gui.py'],
    pathex=['.'],
    binaries=binaries(),
    datas=datas('gui.py'),
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='shadow_man_randomizer',
    icon='assets/randomizer.ico',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
