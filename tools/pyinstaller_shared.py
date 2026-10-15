"""
pyinstaller_shared.py
=====================
What both PyInstaller builds bundle, in one place. "Shadow Man Randomizer.spec"
(gui.py, the standalone exe) and ap_gui.spec (the AP Companion) each import
this and add only their own entry point and exe name, so a new folder, native
library or hidden import reaches both exes at once.

Both GUIs run their patcher in-process from the bundle (gui.py -> patcher.py,
ap_gui.py -> apply_ap_seed.py), so every root .py file and every package the
patchers import is shipped as plain data next to the entry point.
"""
import glob

from PyInstaller.utils.hooks import collect_dynamic_libs

# Packages imported by the patchers at runtime, plus data folders.
FOLDERS = ["data", "randomizers", "patchers", "exe_patches", "assets"]

HIDDEN_IMPORTS = [
    "webview.platforms.winforms",
    "clr",
    "yaml",
    "keystone",
    "capstone",
]


def datas(entry_point: str) -> list[tuple[str, str]]:
    """Every root .py except the entry point, plus FOLDERS."""
    root_py = [(f, ".") for f in glob.glob("*.py") if f != entry_point]
    return root_py + [(d, d) for d in FOLDERS]


def binaries() -> list[tuple[str, str]]:
    # Keystone and Capstone load a native .dll via ctypes from inside their
    # package folder. PyInstaller's import analysis can't see that, and
    # without the .dll the exe patches that assemble code (Unique Retractor
    # Keys, the Trap/Bonus secrets poller) fail with "fail to load the
    # dynamic library".
    return collect_dynamic_libs("keystone") + collect_dynamic_libs("capstone")
