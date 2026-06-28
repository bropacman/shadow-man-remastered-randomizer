# -*- mode: python ; coding: utf-8 -*-
import glob

# Auto-include all root-level .py files except gui.py (the entry point).
# New patch/helper files are picked up automatically — no manual spec edits needed.
root_py = [(f, '.') for f in glob.glob('*.py') if f != 'gui.py']

a = Analysis(
    ['gui.py'],
    pathex=['.'],
    binaries=[],
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
