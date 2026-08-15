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
# shelling out to a second exe. The blanket root_py glob below already
# covers this the same way it does for gui.py -- no separate accounting
# needed, new patch/helper files are picked up automatically here too.
import glob
from PyInstaller.utils.hooks import collect_dynamic_libs

# Auto-include all root-level .py files except ap_gui.py (the entry point).
root_py = [(f, '.') for f in glob.glob('*.py') if f != 'ap_gui.py']

# keystone-engine and capstone each ship a native shared library
# (keystone.dll / capstone.dll on Windows) loaded at runtime via ctypes
# from inside the installed package directory -- not through a normal
# Python import, so PyInstaller's static import analysis can't see it.
# collect_dynamic_libs() finds and bundles the actual .dll; hiddenimports
# alone (below) would only cover the pure-Python wrapper and still fail at
# runtime. Only needed here, not in "Shadow Man Randomizer.spec" -- see
# requirements.txt and RELEASING.md for why.
keystone_binaries = collect_dynamic_libs('keystone')
capstone_binaries = collect_dynamic_libs('capstone')

a = Analysis(
    ['ap_gui.py'],
    pathex=['.'],
    binaries=keystone_binaries + capstone_binaries,
    datas=root_py + [
        ('data', 'data'),
        ('randomizers', 'randomizers'),
        ('patchers', 'patchers'),
        ('assets', 'assets'),
    ],
    hiddenimports=[
        'webview.platforms.winforms',
        'clr',
        'yaml',
        'keystone',
        'capstone',
    ],
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
