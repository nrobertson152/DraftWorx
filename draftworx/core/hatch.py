# SPDX-License-Identifier: GPL-3.0-or-later
"""What a hatch draws: its patterns, the pattern's lines inside its boundary, and a solid fill's outline.

Patterns are given as a pattern file gives them, in millimetres on paper, so a hatch made at the drawing's scale looks the same on every
sheet (ADR-5). A pattern's lines are worked out from its boundary each time they're needed, like a dimension's, so they can't disagree with it.

Inside means inside an odd number of the boundary's loops. Lines are clipped to the loops drawn as fine chords, so where a boundary is curved
a pattern line ends within a few millionths of the hatch's size of it.
"""
from __future__ import annotations

import math
from dataclasses import replace
from functools import lru_cache

from .regions import curve_area, faces, inside_loops, inside_polygon, interior_point, loop_polygon, same_area
from .shapes import Hatch, Node, PatternLine, Solid
from .curves import Line
from .intersect import intersect
from .vec import Vec

MOST_PIECES = 400_000       # of one hatch's pattern. Past this a pattern is too fine to see, and too slow to draw
MOST_LINES = 100_000

#: name: (what it's for, its lines), sizes in millimetres. The names are the ones other programs use, so they recognise them.
PATTERNS: dict[str, tuple[str, tuple[PatternLine, ...]]] = {
    "SOLID": ("Solid fill", ()),
    "ANSI31": ("Diagonal lines: general, iron, brick", (PatternLine(math.radians(45.0), Vec(0.0, 0.0), 0.0, 3.175),)),
    "ANSI32": ("Steel", (PatternLine(math.radians(45.0), Vec(0.0, 0.0), 0.0, 9.525), PatternLine(math.radians(45.0), Vec(4.490128053, 0.0), 0.0, 9.525))),
    "ANSI37": ("Crossed diagonals", (PatternLine(math.radians(45.0), Vec(0.0, 0.0), 0.0, 3.175), PatternLine(math.radians(135.0), Vec(0.0, 0.0), 0.0, 3.175))),
    "LINE": ("Horizontal lines", (PatternLine(0.0, Vec(0.0, 0.0), 0.0, 3.175),)),
    "NET": ("Square grid", (PatternLine(0.0, Vec(0.0, 0.0), 0.0, 3.175), PatternLine(math.radians(90.0), Vec(0.0, 0.0), 0.0, 3.175))),
    "DOTS": ("Dots", (PatternLine(0.0, Vec(0.0, 0.0), 0.79375, 1.5875, (0.0, -1.5875)),)),
    "DASH": ("Dashes", (PatternLine(0.0, Vec(0.0, 0.0), 3.175, 3.175, (3.175, -3.175)),)),
    "SQUARE": ("Squares", (PatternLine(0.0, Vec(0.0, 0.0), 0.0, 3.175, (3.175, -3.175)),
                           PatternLine(math.radians(90.0), Vec(0.0, 0.0), 0.0, 3.175, (3.175, -3.175)))),
    "BRICK": ("Brick courses", (PatternLine(0.0, Vec(0.0, 0.0), 0.0, 6.35), PatternLine(math.radians(90.0), Vec(0.0, 0.0), 0.0, 12.7, (6.35, -6.35)),
                                PatternLine(math.radians(90.0), Vec(6.35, 0.0), 0.0, 12.7, (-6.35, 6.35)))),
    "HONEY": ("Honeycomb", (PatternLine(0.0, Vec(0.0, 0.0), 4.7625, 2.749630645, (3.175, -6.35)),
                            PatternLine(math.radians(120.0), Vec(0.0, 0.0), 4.7625, 2.749630645, (3.175, -6.35)),
                            PatternLine(math.radians(60.0), Vec(0.0, 0.0), 4.7625, 2.749630645, (-6.35, 3.175)))),
    "EARTH": ("Earth", tuple(PatternLine(0.0, Vec(0.0, y), 6.35, 6.35, (6.35, -6.35)) for y in (0.0, 2.38125, 4.7625))
              + tuple(PatternLine(math.radians(90.0), Vec(x, 5.55625), 6.35, 6.35, (6.35, -6.35)) for x in (0.79375, 3.175, 5.55625))),
}
DEFAULT = "ANSI31"


class TooDense(ValueError):
    """The pattern is so fine for its area that it would be millions of lines."""


def known(name: str) -> str | None:
    """The pattern this name means, in its own spelling, or None."""
    for n in PATTERNS:
        if n.casefold() == name.strip().casefold():
            return n
    return None


def definition(h: Hatch) -> tuple[PatternLine, ...]:
    """A hatch's pattern lines, before its scale and angle. A pattern from a file brings its own."""
    if h.solid:
        return ()
    if h.lines:
        return h.lines
    found = known(h.pattern)
    return PATTERNS[found][1] if found is not None else PATTERNS[DEFAULT][1]


def world_lines(h: Hatch) -> list[tuple[float, Vec, Vec, tuple[float, ...]]]:
    """Each family of lines as it falls in the drawing: (angle, a point on the first line, the step to the next, dashes in drawing units).
    This is also how a DXF file holds them."""
    k = h.scale
    out = []
    for pl in definition(h):
        a = pl.angle + h.angle
        u = Vec(math.cos(a), math.sin(a))
        n = u.perp()
        base = h.origin + pl.base.rotated(h.angle) * k
        step = u * (pl.along * k) + n * (pl.across * k)
        out.append((a, base, step, tuple(d * k for d in pl.dashes)))
    return out


def _chord(h: Hatch) -> float:
    x0, y0, x1, y1 = h.bbox()
    return max(x1 - x0, y1 - y0, 1e-12) * 2e-6


@lru_cache(maxsize=512)
def polygons(h: Hatch) -> tuple[tuple[Vec, ...], ...]:
    """Each loop as fine chords, for telling what's inside."""
    chord = _chord(h)
    return tuple(tuple(loop_polygon(loop, chord)) for loop in h.loops)


def _traced(h: Hatch) -> list[tuple[list[Vec], list]]:
    """Each loop as chords, with the curve each chord stands for, or None where the curve is straight and the chord is exact."""
    chord = _chord(h)
    out = []
    for loop in h.loops:
        pts: list[Vec] = []
        srcs: list = []
        for c in loop:
            flat = c.flatten(chord)
            flat = flat[:-1] if len(flat) > 1 else flat
            pts.extend(flat)
            srcs.extend([None if type(c) is Line else c] * len(flat))
        out.append((pts, srcs))
    return out


def _exact(src, o: Vec, u: Vec, t: float, reach: float) -> float:
    """Where a pattern line really crosses a curved edge, near where it crosses the chord standing for it."""
    probe = Line(o + u * (t - reach), o + u * (t + reach))
    try:
        hits = intersect(probe, src)
    except (ValueError, ArithmeticError):
        return t
    best = t
    gap = math.inf
    for hit in hits:
        tt = (hit.point - o).dot(u)
        if abs(tt - t) < gap:
            best, gap = tt, abs(tt - t)
    return best


@lru_cache(maxsize=128)
def pieces(h: Hatch) -> tuple:
    """The pattern's lines, and dots, inside the hatch, in drawing units. Nothing for a solid fill. Raises TooDense past what can be drawn."""
    if h.solid:
        return ()
    traced = [(pts, srcs) for pts, srcs in _traced(h) if len(pts) >= 2]
    reach = _chord(h) * 50.0
    out: list = []
    for angle, base, step, dashes in world_lines(h):
        u = Vec(math.cos(angle), math.sin(angle))
        n = u.perp()
        gap = step.dot(n)
        if gap < 0.0:
            n, gap = -n, -gap
        if gap <= 0.0 or not math.isfinite(gap):
            continue
        crossings: dict[int, list[float]] = {}
        lo = math.inf
        hi = -math.inf
        for poly, _ in traced:
            for p in poly:
                s = (p - base).dot(n)
                lo, hi = min(lo, s), max(hi, s)
        if (hi - lo) / gap > MOST_LINES:
            raise TooDense(f"The {h.pattern} pattern is too fine for an area this size. Make its scale bigger.")
        count = 0
        for poly, srcs in traced:
            m = len(poly)
            for j in range(m):
                p, q = poly[j], poly[(j + 1) % m]
                sp, sq = (p - base).dot(n), (q - base).dot(n)
                if sp == sq:
                    continue
                a, b = (sp, sq) if sp < sq else (sq, sp)
                first, last = math.ceil(a / gap), math.ceil(b / gap) - 1
                for i in range(first, last + 1):
                    level = i * gap
                    if not (a <= level < b):
                        continue
                    lam = (level - sp) / (sq - sp)
                    x = Vec(p[0] + (q[0] - p[0]) * lam, p[1] + (q[1] - p[1]) * lam)
                    o = base + step * i
                    t = (x - o).dot(u)
                    if srcs[j] is not None:
                        t = _exact(srcs[j], o, u, t, reach)
                    crossings.setdefault(i, []).append(t)
                    count += 1
                    if count > MOST_PIECES:
                        raise TooDense(f"The {h.pattern} pattern is too fine for an area this size. Make its scale bigger.")
        period = sum(abs(d) for d in dashes)
        for i in sorted(crossings):
            ts = sorted(crossings[i])
            o = base + step * i
            for t0, t1 in zip(ts[0::2], ts[1::2]):
                if t1 <= t0:
                    continue
                if not dashes or period <= 0.0:
                    out.append(Line(o + u * t0, o + u * t1))
                else:
                    _dashed(out, o, u, t0, t1, dashes, period)
                if len(out) > MOST_PIECES:
                    raise TooDense(f"The {h.pattern} pattern is too fine for an area this size. Make its scale bigger.")
    return tuple(out)


def _dashed(out: list, o: Vec, u: Vec, t0: float, t1: float, dashes: tuple[float, ...], period: float) -> None:
    starts = []
    acc = 0.0
    for d in dashes:
        starts.append(acc)
        acc += abs(d)
    m = math.floor(t0 / period)
    while m * period < t1:
        origin = m * period
        for d, s in zip(dashes, starts):
            a = origin + s
            if d > 0.0:
                b = a + d
                lo, hi = max(a, t0), min(b, t1)
                if hi > lo:
                    out.append(Line(o + u * lo, o + u * hi))
            elif d == 0.0 and t0 <= a <= t1:
                out.append(Node(o + u * a))
        m += 1
        if len(out) > MOST_PIECES:
            return


def _depths(h: Hatch) -> list[int]:
    """How many other loops each loop is inside."""
    polys = polygons(h)
    out = []
    for i, loop in enumerate(h.loops):
        q = loop[0].point_at(0.5)
        out.append(sum(1 for j, poly in enumerate(polys) if j != i and len(poly) > 2 and inside_polygon(poly, q)))
    return out


def outermost(h: Hatch) -> list[bool]:
    """Which loops aren't inside any other: a file marks those as the hatch's outside."""
    return [d == 0 for d in _depths(h)]


def _reversed(loop: tuple) -> tuple:
    return tuple(c.reversed() for c in reversed(loop))


def fill_loops(h: Hatch) -> tuple[tuple, ...]:
    """The loops turned so that filling what they wind round counter-clockwise fills exactly what's inside an odd number of them: outsides
    counter-clockwise, holes clockwise. Then two hatches that overlap still both show, under the non-zero rule a window and a PDF use."""
    out = []
    for loop, depth in zip(h.loops, _depths(h)):
        ccw = sum(curve_area(c) for c in loop) > 0.0
        out.append(loop if ccw == (depth % 2 == 0) else _reversed(loop))
    return tuple(out)


def area(h: Hatch) -> float:
    """The area filled."""
    return abs(sum(curve_area(c) for loop in fill_loops(h) for c in loop))


def trapezoids(h: Hatch, fineness: float = 5e-5) -> list[Solid]:
    """The filled area as four-sided solids between the heights where the outline turns, for a file that has no hatches (DXF R12). Curved
    edges become chords within `fineness` of the hatch's size."""
    x0, y0, x1, y1 = h.bbox()
    chord = max(x1 - x0, y1 - y0, 1e-12) * fineness
    edges = []
    ys: set[float] = set()
    for poly in (loop_polygon(loop, chord) for loop in h.loops):
        m = len(poly)
        for j in range(m):
            p, q = poly[j], poly[(j + 1) % m]
            ys.add(p[1])
            if p[1] != q[1]:
                edges.append((p, q) if p[1] < q[1] else (q, p))
    levels = sorted(ys)
    edges.sort(key=lambda e: e[0][1])
    out: list[Solid] = []
    active: list = []
    k = 0
    for y0, y1 in zip(levels, levels[1:]):
        while k < len(edges) and edges[k][0][1] <= y0:
            active.append(edges[k])
            k += 1
        active = [e for e in active if e[1][1] > y0]
        mid = (y0 + y1) * 0.5
        xs = []
        for p, q in active:
            if p[1] <= y0 and q[1] >= y1:
                def x_at(y, p=p, q=q):
                    return p[0] + (q[0] - p[0]) * (y - p[1]) / (q[1] - p[1])
                xs.append((x_at(mid), x_at(y0), x_at(y1)))
        xs.sort()
        for (_, a0, a1), (_, b0, b1) in zip(xs[0::2], xs[1::2]):
            pts = (Vec(a0, y0), Vec(b0, y0), Vec(b1, y1), Vec(a1, y1))
            if a0 == b0:
                pts = (Vec(a0, y0), Vec(b1, y1), Vec(a1, y1))
            elif a1 == b1:
                pts = (Vec(a0, y0), Vec(b0, y0), Vec(a1, y1))
            out.append(Solid(pts))
    return out


def made_from(region, pattern: str, scale: float, angle: float, seed: Vec) -> Hatch:
    """A hatch filling an area found by clicking inside it, following the objects round it."""
    found = known(pattern) or pattern
    return Hatch(region.loops, found, scale, angle, Vec(0.0, 0.0), (seed,), region.refs)


def follow(h: Hatch, doc, moved_itself: bool) -> Hatch | None:
    """This hatch, now that what's round it may have changed.

    It becomes the area those same objects now close round: of the areas they make, the one bounded by most nearly the same objects as
    before, then the one holding the point it was made from. So it follows its boundary when that's stretched, and when it's moved, and
    so do its holes. If they no longer close round anything like it, it lets go of them and stays as it was.

    A hatch that was itself changed in the same step isn't changed again: it stays attached if its boundary is where it now is, as when both
    were moved together, and lets go otherwise. None if nothing about it changes."""
    if not h.refs:
        return None
    alive = frozenset(r for r in h.refs if r in doc.entities)
    if not alive:
        return replace(h, refs=())
    x0, y0, x1, y1 = h.bbox()
    size = max(x1 - x0, y1 - y0, 1e-9)
    middle = Vec((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    old = set(h.refs)
    best = None
    for region in faces(doc, alive):
        refs = set(region.refs)
        score = len(refs & old) / len(refs | old)
        box = region.bbox()
        holds = any(inside_loops(region.loops, p, size * 1e-6) for p in h.seeds)
        far = math.hypot((box[0] + box[2]) / 2.0 - middle[0], (box[1] + box[3]) / 2.0 - middle[1])
        key = (score, holds, -far)
        if best is None or key > best[0]:
            best = (key, region)
    if best is None or best[0][0] < 0.5:
        return replace(h, refs=())
    region = best[1]
    if same_area(region.loops, h.loops, size):
        return None if region.refs == h.refs else replace(h, refs=region.refs)
    if moved_itself:
        return replace(h, refs=())
    seeds = tuple(p if inside_loops(region.loops, p, size * 1e-6) else interior_point(region.loops) or p for p in h.seeds)
    return replace(h, loops=region.loops, refs=region.refs, seeds=seeds)
