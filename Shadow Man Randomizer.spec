# -*- mode: python ; coding: utf-8 -*-
import glob
from PyInstaller.utils.hooks import collect_dynamic_libs

# Auto-include all root-level .py files except gui.py (the entry point).
# New patch/helper files are picked up automatically — no manual spec edits needed.
root_py = [(f, '.') for f in glob.glob('*.py') if f != 'gui.py']

# Unique Retractor Keys assembles its exe patch with Keystone and checks it
# with Capstone. Both load a native .dll via ctypes, which PyInstaller's
# import analysis can't see, so bundle the .dlls explicitly (as ap_gui.spec does).
keystone_binaries = collect_dynamic_libs('keystone')
capstone_binaries = collect_dynamic_libs('capstone')

a = Analysis(
    ['gui.py'],
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
