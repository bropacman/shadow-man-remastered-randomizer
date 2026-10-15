# -*- mode: python ; coding: utf-8 -*-
# Shadow Man AP Companion (ap_gui.py) build spec.
#
# Mirrors "Shadow Man Randomizer.spec" (gui.py's own build) as closely as
# possible on purpose -- same PyInstaller shape, same blanket "bundle every
# root .py file except the entry point" trick, same shared data folders --
# so the two tools stay easy to maintain side by side. The one thing that
# actually matters here: ap_gui.py's own self-re-invocation trick (see its
# module docstring's "Frozen apply-seed-subprocess mode" section, and
# gui.py's matching _PATCHER_FLAG) needs apply_ap_seed.py (and everything
# IT imports -- ap_patcher.py, every *_patch.py module, rsc_utils.py,
# constants.py, etc.) bundled into this same exe, since the frozen build
# re-invokes itself as a plain apply_ap_seed.py CLI process rather than
# shelling out to a second exe. tools/pyinstaller_shared.py bundles every
# root .py file and the exe_patches/ package for both builds.
import os
import sys

# Folders, native libraries and hidden imports shared with
# "Shadow Man Randomizer.spec" (the standalone exe) live in tools/pyinstaller_shared.py, so both exes always get the same ones.
sys.path.insert(0, os.path.join(SPECPATH, "tools"))
from pyinstaller_shared import datas, binaries, HIDDEN_IMPORTS

a = Analysis(
    ['ap_gui.py'],
    pathex=['.'],
    binaries=binaries(),
    datas=datas('ap_gui.py'),
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
    name='shadow_man_ap_companion',
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
