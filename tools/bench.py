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

from draftworx.core.curves import Arc, Circle, Line, Polyline  # noqa: E402
from draftworx.core.model import Document  # noqa: E402
from draftworx.core.native import dumps, loads  # noqa: E402
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
    t, data = timed(lambda: dumps(doc))
    results.append((f"write a file ({len(data) / 1e6:.1f} MB)", t, 20.0 * k))
    t, back = timed(lambda: loads(data))
    results.append(("read a file", t, 30.0 * k))
    t, _ = timed(lambda: doc.fingerprint())
    results.append(("fingerprint", t, 20.0 * k))

    def undo_all():
        eid = next(iter(doc.entities))
        for i in range(1000):
            with doc.transaction("Move") as tx:
                tx.replace(eid, Line(Vec(float(i), 0.0), Vec(float(i), 10.0)))
        for _ in range(1000):
            doc.undo()
    t, _ = timed(undo_all)
    results.append(("one edit and its undo", t / 1000, 0.01))

    failed = False
    print(f"{n} objects")
    for name, seconds, limit in results:
        over = seconds > limit
        failed |= over
        print(f"  {name:38s} {seconds * 1000:10.3f} ms   limit {limit * 1000:8.1f} ms {'OVER' if over else ''}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
