#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A standalone DraftWorx for this computer's system, built, checked and packed as CI does it: python tools/package.py

It needs the `[dev]` extras, for PyInstaller. The result is dist/DraftWorx-<version>-<commit>-<system>.zip on Windows, or .tar.gz
on Linux, which keeps the programs runnable. Unpacked, it's one folder that needs nothing installed, not even Python, with two
programs in it (tools/package.spec): `draftworx-gui` opens the window, without a console behind it on Windows, and `draftworx` is
the command, which also opens the window when it's given nothing to do.

Before it's packed, both programs run their own self-test, window included, and the command reads, converts and prints a real
drawing, all from a folder of their own. A build that doesn't pass isn't packed.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from draftworx import __version__  # noqa: E402

DIST = ROOT / "dist"
WORK = ROOT / "build" / "package"
DRAWING = ROOT / "tests" / "data" / "Leica_Disto_S910.dxf"      # a real one: lines, points and text


def system() -> str:
    """What it's built for: windows-x64, linux-x64 or linux-arm64."""
    name = {"win32": "windows", "linux": "linux"}.get(sys.platform)
    if name is None:
        raise SystemExit(f"DraftWorx is made for Windows and Linux, and this is {sys.platform}.")
    machine = platform.machine().lower()
    arch = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64"}.get(machine, machine)
    return f"{name}-{arch}"


def commit() -> str:
    """The commit it's built from, so two builds of one version can be told apart, with `-dirty` if tracked files have changed
    since. Empty outside a git checkout."""
    def git(*args: str) -> str | None:
        try:
            r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return r.stdout.strip() if r.returncode == 0 else None
    head = git("rev-parse", "--short=7", "HEAD")
    if not head:
        return ""
    return head + ("-dirty" if git("status", "--porcelain", "--untracked-files=no") else "")


def make_icon(path: Path) -> Path:
    """The window's own icon (MainWindow._apply_icon), drawn by its own code at each size Windows shows, for the programs' files."""
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImage, QPainter

    from draftworx.gui import icons
    from draftworx.gui.theme import DEFAULT_THEME, THEMES

    accent = THEMES[DEFAULT_THEME]["ui"]["accent"]
    images = []
    for size in (256, 128, 64, 48, 32, 24, 16):
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        p = QPainter(image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        icons.paint(p, "polyline", 0, 0, size, accent, accent, 2.2)
        p.end()
        png = QBuffer()
        png.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(png, "PNG")
        images.append(Image.open(io.BytesIO(png.data().data())))
    path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(path, format="ICO", sizes=[im.size for im in images], append_images=images[1:])
    return path


def build(folder: str, icon: Path | None) -> Path:
    env = dict(os.environ, DRAFTWORX_FOLDER=folder)
    if icon is not None:
        env["DRAFTWORX_ICON"] = str(icon)
    try:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                        "--distpath", str(DIST), "--workpath", str(WORK), str(ROOT / "tools" / "package.spec")],
                       cwd=ROOT, env=env, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"PyInstaller couldn't build it (exit code {exc.returncode}). What it said is above.") from exc
    return DIST / folder


def run(args: list, where: Path, timeout: float = 300) -> subprocess.CompletedProcess:
    """A built program, run as someone would run it: from a folder of its own, with no Python settings, never showing a window."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["QT_QPA_PLATFORM"] = "offscreen"
    try:
        return subprocess.run([str(a) for a in args], cwd=where, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, stdin=subprocess.DEVNULL, env=env)
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"{Path(args[0]).name} {' '.join(str(a) for a in args[1:])} was still running after {timeout:.0f} s.") from exc


def check(folder: Path) -> list[str]:
    """Use the built programs. What passed, or SystemExit saying what didn't and what the program printed."""
    suffix = ".exe" if sys.platform == "win32" else ""
    command, window = folder / f"draftworx{suffix}", folder / f"draftworx-gui{suffix}"
    passed: list[str] = []

    def expect(ok: bool, what: str, r: subprocess.CompletedProcess | None = None) -> None:
        if not ok:
            said = f" It exited with {r.returncode}.\n--- stdout:\n{r.stdout}\n--- stderr:\n{r.stderr}" if r is not None else ""
            raise SystemExit(f"The built program failed: {what}.{said}")
        passed.append(what)

    everything = list(folder.rglob("*"))
    broken = sorted(str(p.relative_to(folder)) for p in everything if not p.exists())     # a link to something left out
    expect(not broken, "every file in it is there" + (f" (these link to nothing: {broken})" if broken else ""))
    online = sorted(str(p.relative_to(folder)) for p in everything if "network" in p.name.lower())
    expect(not online, "it has no network code: Qt's network module and its plugins are left out" + (f" (found {online})" if online else ""))

    with tempfile.TemporaryDirectory(prefix="draftworx-package-") as tmp:
        here = Path(tmp)
        r = run([command, "--version"], here)
        expect(r.returncode == 0 and r.stdout.strip() == f"draftworx {__version__}", f"`draftworx --version` says {__version__}", r)
        r = run([command, "--self-test"], here)
        expect(r.returncode == 0 and "self-test passed" in r.stdout and "Window:" in r.stdout,
               "`draftworx --self-test` passes, window included", r)
        r = run([window, "--self-test"], here)
        # On Windows this one has no console, so what it prints can go nowhere. Its exit code is what says it passed.
        expect(r.returncode == 0 and ("Window:" in r.stdout or (sys.platform == "win32" and not r.stdout)),
               "`draftworx-gui --self-test` passes", r)

        def answer(*args) -> tuple[dict, subprocess.CompletedProcess]:
            r = run([command, *args], here)
            try:
                return json.loads(r.stdout), r
            except ValueError:
                return {}, r

        obj, r = answer("info", DRAWING)
        expect(r.returncode == 0 and obj.get("ok") is True and obj.get("objects", 0) > 0, f"`draftworx info` reads {DRAWING.name}", r)
        native, pdf, svg = here / "drawing.draftworx", here / "drawing.pdf", here / "drawing.svg"
        obj, r = answer("convert", DRAWING, native)
        expect(r.returncode == 0 and obj.get("ok") is True and native.is_file(), "`draftworx convert` saves it as .draftworx", r)
        obj, r = answer("pdf", native, pdf)
        expect(r.returncode == 0 and obj.get("ok") is True and pdf.read_bytes().startswith(b"%PDF-"), "`draftworx pdf` prints it", r)
        obj, r = answer("svg", native, svg)
        expect(r.returncode == 0 and obj.get("ok") is True and b"<svg" in svg.read_bytes(), "`draftworx svg` draws it", r)
    return passed


def pack(folder: Path) -> Path:
    """A .zip for Windows, and a .tar.gz for Linux, which keeps the programs runnable. Either holds the one folder."""
    kind, ending = ("zip", ".zip") if sys.platform == "win32" else ("gztar", ".tar.gz")
    (DIST / f"{folder.name}{ending}").unlink(missing_ok=True)
    return Path(shutil.make_archive(str(DIST / folder.name), kind, root_dir=DIST, base_dir=folder.name))


def tell_github(archive: Path, size: float, digest: str, tools: str, passed: list[str]) -> None:
    """In GitHub Actions: the archive, for the step that uploads it, and what was made, for the run's summary."""
    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"archive={archive}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        lines = [f"### {archive.name}", "", f"{size:.1f} MB, SHA-256 `{digest}`. Built with {tools}.", "", "Checked before packing:", ""]
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines + [f"- {what}" for what in passed]) + "\n\n")


def main() -> int:
    try:
        import PyInstaller
    except ImportError:
        raise SystemExit('PyInstaller isn\'t installed. It comes with the [dev] extras: python -m pip install -e ".[dev]"') from None
    import PySide6
    folder = "-".join(part for part in ("DraftWorx", __version__, commit(), system()) if part)
    tools = f"Python {platform.python_version()}, PySide6 {PySide6.__version__} and PyInstaller {PyInstaller.__version__}"
    print(f"Building {folder} with {tools}", flush=True)
    icon = make_icon(WORK / "draftworx.ico") if sys.platform == "win32" else None
    built = build(folder, icon)
    print(f"Checking {built.relative_to(ROOT)}", flush=True)
    passed = check(built)
    for what in passed:
        print(f"  passed: {what}")
    archive = pack(built)
    with archive.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    size = archive.stat().st_size / 1e6
    print(f"Made {archive.relative_to(ROOT)}: {size:.1f} MB, SHA-256 {digest}")
    tell_github(archive, size, digest, tools, passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
