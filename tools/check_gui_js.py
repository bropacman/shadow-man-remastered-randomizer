"""
tools/check_gui_js.py
---------------------
Syntax-check the JavaScript embedded in gui.py and ap_gui.py.

Both GUIs keep their whole page in a module-level raw string
`_HTML = r"<!DOCTYPE html>..."`. py_compile can't see a JS syntax error
in there, and one broken brace silently kills every button in the GUI.
This pulls `_HTML` out with `ast` (no import, so no pywebview / window
side effects), extracts each inline <script> block, and runs
`node --check` on it.

Usage:
    python tools/check_gui_js.py            # checks gui.py and ap_gui.py
    python tools/check_gui_js.py gui.py     # just one file

Exit code 1 if any block fails (or node isn't on PATH).
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILES = ["gui.py", "ap_gui.py"]

_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)


def _html_constant(path: Path) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "_HTML" for t in node.targets)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            return node.value.value
    raise SystemExit(f"{path.name}: no top-level `_HTML = <string>` found")


def main() -> int:
    node = shutil.which("node")
    if node is None:
        print("node not found on PATH -- install Node.js to run this check")
        return 1

    files = sys.argv[1:] or DEFAULT_FILES
    failed = 0
    for name in files:
        path = ROOT / name
        blocks = _SCRIPT_RE.findall(_html_constant(path))
        if not blocks:
            print(f"FAIL {name}: no inline <script> blocks found")
            failed += 1
            continue
        for n, js in enumerate(blocks, 1):
            with tempfile.NamedTemporaryFile(
                "w", suffix=".js", delete=False, encoding="utf-8"
            ) as tmp:
                tmp.write(js)
            try:
                res = subprocess.run([node, "--check", tmp.name],
                                     capture_output=True, text=True)
            finally:
                Path(tmp.name).unlink(missing_ok=True)
            label = f"{name} <script> #{n} ({js.count(chr(10))} lines)"
            if res.returncode == 0:
                print(f"ok   {label}")
            else:
                failed += 1
                # node's own stack trace ("at ..." lines) is noise here
                err = "\n".join(l for l in res.stderr.splitlines()
                                if l.strip() and not l.lstrip().startswith("at "))
                print(f"FAIL {label}\n{err}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
