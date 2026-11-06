# SPDX-License-Identifier: GPL-3.0-or-later
"""The `draftworx` command. Doing anything with a drawing needs no window and no Qt."""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from . import PROGRAM, __version__
from .core.curves import Arc, Circle, Line, Polyline
from .core.model import Document
from .core.native import dumps, loads
from .core.safefile import write_verified
from .core.vec import Vec


def self_test() -> int:
    """Draw something, save it, read it back and undo it, using the installed program's own code."""
    doc = Document()
    with doc.transaction("Self-test") as t:
        t.add(Line(Vec(0.0, 0.0), Vec(100.0, 0.0)))
        t.add(Circle(Vec(50.0, 50.0), 25.0))
        t.add(Arc(Vec(0.0, 0.0), 10.0, 0.0, 1.5))
        t.add(Polyline((Vec(0.0, 0.0), Vec(1.0, 0.0), Vec(1.0, 1.0)), closed=True))
    before = doc.fingerprint()
    with tempfile.TemporaryDirectory(prefix="draftworx-selftest-") as folder:
        path = Path(folder) / "selftest.draftworx"
        write_verified(path, dumps(doc, PROGRAM), lambda data: loads(data))
        back = loads(path.read_bytes())
    if back.fingerprint() != before:
        print("self-test failed: the drawing read back differs from the one saved", file=sys.stderr)
        return 1
    doc.undo()
    if doc.entities:
        print("self-test failed: undo left something behind", file=sys.stderr)
        return 1
    print(f"{PROGRAM} self-test passed: drawing, saving, reading back and undo all work.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="draftworx", description="DraftWorx: a lightweight, offline 2D CAD program")
    parser.add_argument("--version", action="version", version=f"draftworx {__version__}")
    parser.add_argument("--self-test", action="store_true", help="check this installation works, and exit")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
