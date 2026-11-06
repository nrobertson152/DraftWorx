# SPDX-License-Identifier: GPL-3.0-or-later
"""The rules of the codebase, enforced."""
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "draftworx"
STDLIB = set(sys.stdlib_module_names)


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def not_gui():
    return [p for p in PACKAGE.rglob("*.py") if "gui" not in p.relative_to(PACKAGE).parts]


def test_nothing_outside_the_window_imports_anything_but_the_standard_library():
    for path in not_gui():
        extra = imports_of(path) - STDLIB - {"draftworx"}
        assert not extra, f"{path} imports {extra}"


def test_the_core_runs_with_qt_unavailable():
    code = ("import sys\n"
            "class Block:\n"
            "    def find_spec(self, name, path=None, target=None):\n"
            "        if name.split('.')[0] in ('PySide6', 'shiboken6'):\n"
            "            raise ImportError(name)\n"
            "sys.meta_path.insert(0, Block())\n"
            "from draftworx.cli import main\n"
            "sys.exit(main(['--self-test']))\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "passed" in r.stdout


def test_every_source_file_says_its_licence():
    for path in list(PACKAGE.rglob("*.py")) + list((ROOT / "tests").glob("*.py")):
        if path.stat().st_size:
            assert "SPDX-License-Identifier: GPL-3.0-or-later" in path.read_text(encoding="utf-8").split("\n", 1)[0], path


def test_nothing_reaches_for_the_network():
    forbidden = {"socket", "ssl", "http", "urllib", "ftplib", "smtplib", "telnetlib", "requests", "asyncio", "xmlrpc"}
    for path in PACKAGE.rglob("*.py"):
        assert not (imports_of(path) & forbidden), f"{path} reaches for the network"


def test_the_self_test_runs_as_a_command():
    r = subprocess.run([sys.executable, "-m", "draftworx", "--self-test"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
