#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""How fast the things that have to be fast are. Prints numbers, and fails if one is wildly over its target.

The limits are far above what a normal machine takes, because CI machines vary: this catches something going quadratic,
not a few percent. `--quick` uses a tenth of the drawing, and scales the limits to match.
"""
from __future__ import annotations

import argparse
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from draftworx.core import hatch as hatches  # noqa: E402
from draftworx.core import regions  # noqa: E402
from draftworx.core.curves import Arc, Circle, Line, Polyline  # noqa: E402
from draftworx.core.dxf import read_dxf, write_dxf  # noqa: E402
from draftworx.core.model import Document  # noqa: E402
from draftworx.core.native import dumps, loads  # noqa: E402
from draftworx.core.ops.offset import offset  # noqa: E402
from draftworx.core.snap import SnapEngine, SnapQuery  # noqa: E402
from draftworx.core.vec import Vec  # noqa: E402


def drawing(n: int) -> Document:
    """A plan-like drawing: lots of short lines, some arcs and circles and long polylines, spread over 100 m in mm."""
    r = random.Random(1)
    doc = Document()
    size = 100_000.0
    with doc.transaction("Bench") as t:
        for i in range(n):
            x, y = r.random() * size, r.random() * size
            kind = i % 20
            if kind < 16:
                a = r.random() * math.tau
                length = r.random() * 800.0
                t.add(Line(Vec(x, y), Vec(x + length * math.cos(a), y + length * math.sin(a))))
            elif kind == 16:
                t.add(Circle(Vec(x, y), r.random() * 300.0 + 1.0))
            elif kind == 17:
                t.add(Arc(Vec(x, y), r.random() * 300.0 + 1.0, r.random() * 6.0, r.random() * 3.0 + 0.1))
            else:
                pts = tuple(Vec(x + r.random() * 500.0, y + r.random() * 500.0) for _ in range(6))
                t.add(Polyline(pts, closed=kind == 19))
    doc.forget_history()
    return doc


def timed(fn):
    start = time.perf_counter()
    result = fn()
    return time.perf_counter() - start, result


def window_times(doc: Document, k: float) -> list[tuple[str, float, float]]:
    """What a person feels: repainting the whole drawing, and a mouse move. Offscreen, so a real screen adds its own time."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from draftworx.core.config import Config
    from draftworx.core.session import Session
    from draftworx.gui.canvas import Canvas
    from draftworx.gui.theme import resolve
    app = QApplication.instance() or QApplication(["bench"])
    theme = resolve("modern_light")
    session = Session(doc)
    canvas = Canvas(session, theme, Config())
    canvas.resize(1600, 1000)
    canvas.show()
    app.processEvents()
    out = []
    canvas.view.fit(doc.extents())
    canvas.scene.rebuild()

    def first():
        canvas._render()
    t, _ = timed(first)
    out.append(("first paint, everything in view", t, 20.0 * k + 1.0))
    t, _ = timed(lambda: canvas._render())
    out.append(("repaint, everything in view (a pan)", t, 2.0 * k + 0.2))
    canvas.view.zoom_at(800, 500, 30.0)
    t, _ = timed(lambda: canvas._render())
    out.append(("repaint, zoomed in", t, 0.5 * k + 0.1))
    session.run("LINE")
    r = random.Random(3)
    moves = [(r.random() * 100_000.0, r.random() * 100_000.0) for _ in range(200)]

    def move_all():
        for x, y in moves:
            session.move(x, y, canvas.view.px, 0.0)
            canvas.grab()
    t, _ = timed(move_all)
    out.append(("a mouse move, with what it paints", t / len(moves), 0.05 * k + 0.02))
    session.cancel()
    canvas.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("-n", type=int, default=None)
    args = ap.parse_args()
    n = args.n or (10_000 if args.quick else 100_000)
    k = n / 100_000
    results: list[tuple[str, float, float]] = []

    build_time, doc = timed(lambda: drawing(n))
    results.append(("draw everything in one change", build_time, 30.0 * k))
    r = random.Random(2)
    points = [(r.random() * 100_000.0, r.random() * 100_000.0) for _ in range(2000)]

    def queries():
        for x, y in points:
            doc.index.query((x - 50.0, y - 50.0, x + 50.0, y + 50.0))
    t, _ = timed(queries)
    results.append(("index query near the cursor", t / len(points), 0.002))
    engine = SnapEngine(doc)
    snaps = [SnapQuery(Vec(x, y), 25.0, last=Vec(x - 5000.0, y)) for x, y in points[:500]]
    t, _ = timed(lambda: [engine.pick(q) for q in snaps])
    results.append(("snap: a move at drawing scale", t / len(snaps), 0.005))
    close = [SnapQuery(Vec(x, y), 0.5, last=Vec(x - 100.0, y)) for x, y in points[:500]]
    t, _ = timed(lambda: [engine.pick(q) for q in close])
    results.append(("snap: a move zoomed in", t / len(close), 0.005))
    wide = [SnapQuery(Vec(x, y), 400.0, last=Vec(x - 5000.0, y)) for x, y in points[:100]]
    t, _ = timed(lambda: [engine.pick(q) for q in wide])
    results.append(("snap: a move zoomed right out", t / len(wide), 0.05))
    t, data = timed(lambda: dumps(doc))
    results.append((f"write a file ({len(data) / 1e6:.1f} MB)", t, 20.0 * k))
    t, back = timed(lambda: loads(data))
    results.append(("read a file", t, 30.0 * k))
    t, _ = timed(lambda: doc.fingerprint())
    results.append(("fingerprint", t, 20.0 * k))
    t, dxf = timed(lambda: write_dxf(doc))
    results.append((f"write a DXF ({len(dxf) / 1e6:.1f} MB)", t, 30.0 * k))
    t, (from_dxf, _) = timed(lambda: read_dxf(dxf))
    results.append(("read a DXF", t, 60.0 * k))
    t, _ = timed(lambda: write_dxf(from_dxf))
    results.append(("write that DXF back, unchanged", t, 30.0 * k))

    def undo_all():
        eid = next(iter(doc.entities))
        for i in range(1000):
            with doc.transaction("Move") as tx:
                tx.replace(eid, Line(Vec(float(i), 0.0), Vec(float(i), 10.0)))
        for _ in range(1000):
            doc.undo()
    t, _ = timed(undo_all)
    results.append(("one edit and its undo", t / 1000, 0.01))

    m = n // 10
    ring = Polyline(tuple(Vec(100.0 * math.cos(math.tau * i / m), 100.0 * math.sin(math.tau * i / m)) for i in range(m)), closed=True)
    t, _ = timed(lambda: offset(ring, 10.0, Vec(500.0, 0.0)))
    results.append((f"offset a ring of {m} pieces outward", t, 20.0 * k))
    zigzag = Polyline(tuple(Vec(i * 0.02, 0.1 * (i % 2)) for i in range(m)))
    t, _ = timed(lambda: offset(zigzag, 10.0, Vec(0.0, 50.0)))
    results.append((f"offset a fine zigzag of {m}, well clear", t, 20.0 * k))

    side = max(10, int(math.sqrt(n / 4)))
    rooms = Document()
    with rooms.transaction("Rooms") as tx:
        for i in range(side):
            for j in range(side):
                x, y = i * 100.0, j * 100.0
                for a, b in (((x, y), (x + 90, y)), ((x + 90, y), (x + 90, y + 90)), ((x + 90, y + 90), (x, y + 90)), ((x, y + 90), (x, y))):
                    tx.add(Line(Vec(*a), Vec(*b)))
    middle = side // 2 * 100.0 + 45.0
    t, found = timed(lambda: regions.find(rooms, Vec(middle, middle), 150.0))
    assert isinstance(found, regions.Region)
    results.append((f"hatch: find a room among {len(rooms.entities)} lines", t, 0.5))
    t, _ = timed(lambda: hatches.pieces(hatches.made_from(found, "ANSI37", 0.05, 0.3, Vec(middle, middle))))
    results.append(("hatch: a fine pattern across a room", t, 0.5))

    results.extend(window_times(doc, k))

    failed = False
    print(f"{n} objects")
    for name, seconds, limit in results:
        over = seconds > limit
        failed |= over
        print(f"  {name:38s} {seconds * 1000:10.3f} ms   limit {limit * 1000:8.1f} ms {'OVER' if over else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
