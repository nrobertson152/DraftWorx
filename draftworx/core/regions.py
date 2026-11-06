# SPDX-License-Identifier: GPL-3.0-or-later
"""The area round a point: what a hatch fills when you click inside something.

The curves near the point are cut wherever they cross or touch, and the pieces become the edges of a map. Walking the map with the area
always on the left, and turning as sharply left as possible at every corner, goes once round each area. The area round the point is the
smallest one that holds it. Whatever lies inside it on its own, an island, becomes a hole, and the inside of an island isn't filled. Lines
that only poke into an area don't divide it: the walk goes up one side of them and back down the other, and those pieces are dropped.

Only curves near the point are looked at first. A curve is used whole if any of it is near, so the map is exact for the curves it has. If
the area found reaches past what was looked at, a curve that wasn't looked at could cut it, so more is looked at, until it doesn't.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from .curves import Arc, Circle, Line, Polyline
from .intersect import intersect
from .vec import BBox, Vec, bbox_union, tol

MOST_CURVES = 20_000        # looked at for one area. More than this, and pointing isn't the way to find it
_GROW = 4.0
_GL = ((-0.8611363115940526, 0.3478548451374538), (-0.3399810435848563, 0.6521451548625461),
       (0.3399810435848563, 0.6521451548625461), (0.8611363115940526, 0.3478548451374538))


class TooSlow(Exception):
    """Finding the area took longer than a preview can wait."""


@dataclass(frozen=True, slots=True)
class Region:
    """`loops` are closed boundaries made of curves end to end. The first goes round the outside, counter-clockwise, and the rest are
    holes in it, clockwise. `refs` are the objects whose curves bound it."""
    loops: tuple[tuple, ...]
    refs: tuple[int, ...]
    area: float

    def bbox(self) -> BBox | None:
        box = None
        for loop in self.loops:
            for c in loop:
                box = bbox_union(box, c.bbox())
        return box


@dataclass(frozen=True, slots=True)
class NoRegion:
    """Why there's no area there, said for the person, and where the boundary may be open."""
    reason: str
    gaps: tuple[Vec, ...] = ()


# ------------------------------------------------------------------------------------------------------ small geometry


def curve_area(c) -> float:
    """Half the integral of x dy - y dx along a curve: summed round a closed loop, its signed area, counter-clockwise positive."""
    k = type(c)
    if k is Line:
        return 0.5 * (c.a[0] * c.b[1] - c.b[0] * c.a[1])
    if k is Arc or k is Circle:
        a = c.as_arc() if k is Circle else c
        (cx, cy), r = a.c, a.r
        s, e = a.start, a.end
        return 0.5 * (cx * (e[1] - s[1]) - cy * (e[0] - s[0])) + 0.5 * r * r * a.sweep
    pieces = 24
    total = 0.0
    for i in range(pieces):
        t0, t1 = i / pieces, (i + 1) / pieces
        half, mid = (t1 - t0) * 0.5, (t1 + t0) * 0.5
        for x, w in _GL:
            t = mid + half * x
            p, d = c.point_at(t), c.tangent_at(t)
            total += w * half * (p[0] * d[1] - p[1] * d[0])
    return 0.5 * total


def curvature(c, t: float) -> float:
    """How sharply a curve bends at t, as it runs: positive to the left. What tells apart curves that leave a point in the same direction."""
    k = type(c)
    if k is Line:
        return 0.0
    if k is Arc:
        return (1.0 if c.sweep > 0.0 else -1.0) / c.r if c.r > 0.0 else 0.0
    if k is Circle:
        return 1.0 / c.r if c.r > 0.0 else 0.0
    h = 1e-5
    t0, t1 = (t, t + h) if t + h <= 1.0 else (t - h, t)
    d0, d1 = c.tangent_at(t0), c.tangent_at(t1)
    d = c.tangent_at(t)
    speed = math.hypot(d[0], d[1])
    if speed == 0.0:
        return 0.0
    dd = ((d1[0] - d0[0]) / h, (d1[1] - d0[1]) / h)
    return (d[0] * dd[1] - d[1] * dd[0]) / speed ** 3


def _direction(v: Vec) -> float:
    """An angle in (-pi, pi], with straight left always pi, so a tie can't straddle where angles wrap round."""
    a = math.atan2(v[1], v[0])
    if a <= -math.pi + 1e-9:
        a += math.tau
    return a


def inside_polygon(pts, p) -> bool:
    """Even-odd: is p inside this closed polygon?"""
    inside = False
    x, y = p[0], p[1]
    n = len(pts)
    j = n - 1
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[j]
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            inside = not inside
        j = i
    return inside


def loop_polygon(loop, chord: float) -> list[Vec]:
    pts: list[Vec] = []
    for c in loop:
        flat = c.flatten(chord)
        pts.extend(flat[:-1] if len(flat) > 1 else flat)
    return pts


# ------------------------------------------------------------------------------------------------------------- the map


class _Map:
    """The curves cut where they meet, as edges between corners. Edge k runs from corner `ends[k][0]` to `ends[k][1]`; half-edge 2k runs that
    way and 2k + 1 back."""

    def __init__(self, items: list[tuple[object, int]], merge: float, deadline: float | None = None):
        self.merge = merge
        self.deadline = deadline
        curves = [c for c, _ in items]
        owners = [o for _, o in items]
        splits = self._cuts(curves)
        self.points: list[Vec] = []
        self._grid: dict[tuple[int, int], list[int]] = {}
        self.pieces: list = []
        self.owners: list[set[int]] = []
        self.ends: list[tuple[int, int]] = []
        seen: dict[tuple, int] = {}
        for i, c in enumerate(curves):
            ts = sorted(set(min(1.0, max(0.0, t)) for t in splits[i]))
            for a, b in zip(ts, ts[1:]):
                if b - a <= 1e-12:
                    continue
                piece = c.sub(a, b) if type(c) is not Line else Line(c.point_at(a), c.point_at(b))
                if piece.length() <= merge:
                    continue
                v0, v1 = self._corner(piece.start), self._corner(piece.end)
                mid = piece.point_at(0.5)
                key = (min(v0, v1), max(v0, v1), round(mid[0] / (merge * 4.0)), round(mid[1] / (merge * 4.0)))
                if key in seen:
                    self.owners[seen[key]].add(owners[i])        # the same edge drawn twice is one edge
                    continue
                seen[key] = len(self.pieces)
                self.pieces.append(piece)
                self.owners.append({owners[i]})
                self.ends.append((v0, v1))
        self._link()

    def _tick(self) -> None:
        if self.deadline is not None and time.monotonic() > self.deadline:
            raise TooSlow()

    def _cuts(self, curves: list) -> list[list[float]]:
        """Where each curve is cut: wherever it crosses or touches another, and where an end of another stops on it."""
        merge = self.merge
        splits = [[0.0, 1.0] for _ in curves]
        boxes = []
        for c in curves:
            b = c.bbox()
            boxes.append((b[0] - merge, b[1] - merge, b[2] + merge, b[3] + merge))
        order = sorted(range(len(curves)), key=lambda i: boxes[i][0])
        active: list[int] = []
        for n, i in enumerate(order):
            if n % 64 == 0:
                self._tick()
            bi = boxes[i]
            active = [j for j in active if boxes[j][2] >= bi[0]]
            a = curves[i]
            for j in active:
                bj = boxes[j]
                if bj[1] > bi[3] or bj[3] < bi[1]:
                    continue
                b = curves[j]
                try:
                    hits = intersect(a, b)
                except (ValueError, ArithmeticError):
                    hits = []
                for h in hits:
                    splits[i].append(h.ta)
                    splits[j].append(h.tb)
                # an end that stops just short of, or just past, another curve still meets it
                for c, other, k in ((a, b, j), (b, a, i)):
                    if c.closed:
                        continue
                    for end in (c.start, c.end):
                        t, q = other.project(end)
                        if 0.0 < t < 1.0 and math.hypot(q[0] - end[0], q[1] - end[1]) <= merge:
                            splits[k].append(t)
            active.append(i)
        return splits

    def _corner(self, p: Vec) -> int:
        """The corner at p: one already there, if it's close enough to be the same point."""
        m = self.merge
        gx, gy = int(math.floor(p[0] / m)), int(math.floor(p[1] / m))
        best, found = m, -1
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for v in self._grid.get((gx + dx, gy + dy), ()):
                    q = self.points[v]
                    d = math.hypot(q[0] - p[0], q[1] - p[1])
                    if d <= best:
                        best, found = d, v
        if found >= 0:
            return found
        self.points.append(p)
        self._grid.setdefault((gx, gy), []).append(len(self.points) - 1)
        return len(self.points) - 1

    def _link(self) -> None:
        """Round each corner, the edges leaving it in counter-clockwise order, and which edge follows which with the area on the left."""
        leaving: dict[int, list[tuple[float, float, int]]] = {}
        for k, piece in enumerate(self.pieces):
            v0, v1 = self.ends[k]
            d0 = piece.tangent_at(0.0)
            d1 = piece.tangent_at(1.0)
            leaving.setdefault(v0, []).append((_direction(d0), curvature(piece, 0.0), 2 * k))
            leaving.setdefault(v1, []).append((_direction((-d1[0], -d1[1])), -curvature(piece, 1.0), 2 * k + 1))
        self.leaving: dict[int, list[int]] = {}
        self.slot: dict[int, int] = {}
        for v, out in leaving.items():
            out.sort(key=lambda e: e[0])
            # leaving in the same direction, the one that bends more to the left comes later, counter-clockwise
            i = 0
            while i < len(out):
                j = i + 1
                while j < len(out) and out[j][0] - out[i][0] <= 1e-9:
                    j += 1
                if j - i > 1:
                    out[i:j] = sorted(out[i:j], key=lambda e: e[1])
                i = j
            order = [e[2] for e in out]
            self.leaving[v] = order
            for n, h in enumerate(order):
                self.slot[h] = n

    def start_of(self, h: int) -> int:
        k, back = divmod(h, 2)
        return self.ends[k][1] if back else self.ends[k][0]

    def end_of(self, h: int) -> int:
        k, back = divmod(h, 2)
        return self.ends[k][0] if back else self.ends[k][1]

    def after(self, h: int) -> int:
        """The half-edge that follows h with the area kept on the left: the sharpest left turn at its end."""
        twin = h ^ 1
        out = self.leaving[self.end_of(h)]
        return out[self.slot[twin] - 1]

    def curve_of(self, h: int):
        k, back = divmod(h, 2)
        c = self.pieces[k]
        return c.reversed() if back else c

    def cycles(self) -> list[list[int]]:
        seen: set[int] = set()
        out: list[list[int]] = []
        for start in range(2 * len(self.pieces)):
            if start in seen:
                continue
            cycle = []
            h = start
            while h not in seen:
                seen.add(h)
                cycle.append(h)
                h = self.after(h)
                if len(cycle) > 4 * len(self.pieces) + 4:
                    break       # can't happen with a consistent map, and a loop that never ends must not hang the program
            out.append(cycle)
        return out

    def components(self) -> list[int]:
        parent = list(range(len(self.points)))

        def root(v: int) -> int:
            while parent[v] != v:
                parent[v] = parent[parent[v]]
                v = parent[v]
            return v
        for v0, v1 in self.ends:
            a, b = root(v0), root(v1)
            if a != b:
                parent[a] = b
        return [root(v) for v in range(len(self.points))]

    def open_ends(self) -> list[Vec]:
        """Corners only one edge reaches: where a boundary may be open."""
        count: dict[int, int] = {}
        for v0, v1 in self.ends:
            count[v0] = count.get(v0, 0) + 1
            count[v1] = count.get(v1, 0) + 1
        return [self.points[v] for v, n in count.items() if n == 1]


def _cleaned(m: _Map, cycle: list[int]) -> list[list[int]]:
    """A walk round an area, without what it went along both ways: lines poking in, and the bridges out to islands joined to the edge.
    What's left falls apart into closed loops."""
    present = set(cycle)
    keep = [h for h in cycle if (h ^ 1) not in present]
    if not keep:
        return []
    # cut where something was dropped, then join the pieces end to start
    runs: list[list[int]] = []
    run: list[int] = []
    for h in cycle:
        if (h ^ 1) in present:
            if run:
                runs.append(run)
                run = []
        else:
            run.append(h)
    if run:
        if runs and m.end_of(run[-1]) == m.start_of(runs[0][0]) and (cycle[0] ^ 1) not in present:
            runs[0] = run + runs[0]
        else:
            runs.append(run)
    loops: list[list[int]] = []
    while runs:
        loop = runs.pop(0)
        guard = 0
        while m.end_of(loop[-1]) != m.start_of(loop[0]) and guard < 100_000:
            guard += 1
            nxt = next((r for r in runs if m.start_of(r[0]) == m.end_of(loop[-1])), None)
            if nxt is None:
                break
            runs.remove(nxt)
            loop.extend(nxt)
        if m.end_of(loop[-1]) == m.start_of(loop[0]):
            loops.append(loop)
    return loops


# --------------------------------------------------------------------------------------------------------- finding it


class _Found:
    """The areas of one map, ready to be asked which one holds a point."""

    def __init__(self, m: _Map, size: float):
        self.m = m
        self.chord = max(m.merge, size * 1e-6)
        comp = m.components()
        areas: dict[int, float] = {}
        for k, piece in enumerate(m.pieces):
            areas[k] = curve_area(piece)
        self.faces: list[tuple[float, list[int], BBox, list[Vec]]] = []       # counter-clockwise: an area's outside
        self.outsides: list[tuple[float, list[int], int, list[Vec]]] = []     # clockwise: a separate group's outer edge
        tiny = (m.merge * 10.0) ** 2
        for cycle in m.cycles():
            a = sum(areas[h // 2] * (-1.0 if h % 2 else 1.0) for h in cycle)
            if a > tiny:
                poly = self._polygon(cycle)
                box = _box(poly)
                self.faces.append((a, cycle, box, poly))
            elif a < -tiny:
                self.outsides.append((a, cycle, comp[m.start_of(cycle[0])], self._polygon(cycle)))
        self.comp = comp

    def _polygon(self, cycle: list[int]) -> list[Vec]:
        pts: list[Vec] = []
        for h in cycle:
            flat = self.m.curve_of(h).flatten(self.chord)
            pts.extend(flat[:-1] if len(flat) > 1 else flat)
        return pts

    def at(self, p: Vec):
        """The outside of the smallest area holding p, or None if p isn't inside anything closed."""
        best = None
        for a, cycle, box, poly in self.faces:
            if box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3] and (best is None or a < best[0]) and inside_polygon(poly, p):
                best = (a, cycle, box, poly)
        return best

    def holes(self, face) -> list[list[int]]:
        """The outer edges of the separate groups inside an area, but not those inside another of them."""
        _, cycle, box, poly = face
        mine = self.comp[self.m.start_of(cycle[0])]
        inside = []
        for a, c, comp, cpoly in self.outsides:
            if comp == mine:
                continue
            q = self.m.points[self.m.start_of(c[0])]
            if box[0] <= q[0] <= box[2] and box[1] <= q[1] <= box[3] and inside_polygon(poly, q):
                inside.append((a, c, cpoly))
        out = []
        for a, c, cpoly in inside:
            q = self.m.points[self.m.start_of(c[0])]
            if not any(o is not c and inside_polygon(opoly, q) for _, o, opoly in inside):
                out.append(c)
        return out


def _box(pts) -> BBox:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _region(found: _Found, face) -> Region | None:
    m = found.m
    loops: list[tuple] = []
    refs: set[int] = set()
    outer_area = 0.0
    hole_area = 0.0
    walks = _cleaned(m, face[1])
    for w in found.holes(face):
        walks.extend(_cleaned(m, w))
    for w in walks:
        curves = tuple(m.curve_of(h) for h in w)
        a = sum(curve_area(c) for c in curves)
        for h in w:
            refs |= m.owners[h // 2]
        if a > 0.0:
            outer_area += a
            loops.insert(0, curves)
        else:
            hole_area += a
            loops.append(curves)
    if not loops or outer_area <= 0.0:
        return None
    refs.discard(0)
    return Region(tuple(loops), tuple(sorted(refs)), outer_area + hole_area)


# --------------------------------------------------------------------------------------------------------- the drawing


def boundary_items(doc, box: BBox, only: frozenset | None = None) -> list[tuple[object, int]]:
    """The curves that can bound an area, from everything shown near `box`: lines, arcs, circles, ellipses, splines, polylines, the edges of
    solids and what blocks draw. Not text, dimensions, points or other hatches."""
    from .model import split_key
    from .select import curves_of
    from .shapes import Dimension, Foreign, Hatch, MText, Node, Text
    skip = (Text, MText, Node, Dimension, Hatch)
    out: list[tuple[object, int]] = []
    seen: set[int] = set()
    ids = (split_key(key)[0] for key in doc.index.query(box)) if only is None else iter(sorted(only))
    for eid in ids:
        if eid in seen:
            continue
        seen.add(eid)
        e = doc.entities.get(eid)
        if e is None or doc.is_hidden(e.style) or type(e.shape) in skip:
            continue
        if type(e.shape) is Foreign and e.shape.dxf_type == "HATCH":
            continue
        for c in curves_of(doc, e):
            if type(c) is Polyline:
                out.extend((g, eid) for g in c.segments)
            else:
                out.append((c, eid))
            if len(out) > MOST_CURVES:
                return out
    return out


def _bridges(items: list[tuple[object, int]], gap: float, merge: float) -> list[tuple[object, int]]:
    """Straight lines across the gaps up to `gap` wide: from each open end to the nearest other open end, or else to the nearest curve."""
    m = _Map(items, merge)
    ends = m.open_ends()
    out: list[tuple[object, int]] = []
    done: set[tuple] = set()
    for p in ends:
        best = None
        for q in ends:
            if q is p:
                continue
            d = math.hypot(q[0] - p[0], q[1] - p[1])
            if merge < d <= gap and (best is None or d < best[0]):
                best = (d, q)
        if best is None:
            for c in m.pieces:
                _, q = c.project(p)
                d = math.hypot(q[0] - p[0], q[1] - p[1])
                if merge < d <= gap and (best is None or d < best[0]):
                    best = (d, q)
        if best is not None:
            key = tuple(sorted((tuple(p), tuple(best[1]))))
            if key not in done:
                done.add(key)
                out.append((Line(p, best[1]), 0))
    return out


def find(doc, seed: Vec, reach: float, only: frozenset | None = None, gap: float = 0.0, deadline: float | None = None) -> Region | NoRegion:
    """The area round `seed`. `reach` is how far round it to look first. `only` limits the boundary to those objects, as a hatch following
    its own boundary does. `gap` closes openings up to that wide. Raises TooSlow past `deadline`."""
    extents = doc.extents()
    if only is not None:
        extents = None
        for i in only:
            e = doc.entities.get(i)
            if e is not None:
                extents = bbox_union(extents, doc.shape_bbox(e.shape))
        if extents is None:
            return NoRegion("What it was bounded by is gone.")
    half = max(reach, tol(seed[0], seed[1]) * 1e3)
    if extents is not None:
        size = max(extents[2] - extents[0], extents[3] - extents[1], 0.0)
        far = max(abs(extents[0] - seed[0]), abs(extents[2] - seed[0]), abs(extents[1] - seed[1]), abs(extents[3] - seed[1]))
    else:
        size = far = 0.0
    for _ in range(40):
        box = (seed[0] - half, seed[1] - half, seed[0] + half, seed[1] + half)
        items = boundary_items(doc, box, only)
        if len(items) > MOST_CURVES:
            return NoRegion("There's too much round that point to find the area by pointing. Zoom in, or close the area with fewer objects.")
        scale = max(half, size, abs(seed[0]), abs(seed[1]), 1e-9)
        merge = max(tol(seed[0], seed[1], scale) * 100.0, half * 1e-9)
        if gap > merge:
            items = items + _bridges(items, gap, merge)
        m = _Map(items, merge, deadline)
        found = _Found(m, half)
        face = found.at(seed)
        whole = extents is None or far <= half
        if face is None:
            if whole:
                # an end inside some closed area isn't where this one is open
                ends = sorted((q for q in m.open_ends() if found.at(q) is None), key=lambda q: math.hypot(q[0] - seed[0], q[1] - seed[1]))
                return NoRegion("That area isn't closed. Close it where it's open, or type G to bridge small gaps.", tuple(ends[:8]))
            half *= _GROW
            continue
        fbox = face[2]
        margin = half * 1e-6
        if fbox[0] > box[0] + margin and fbox[2] < box[2] - margin and fbox[1] > box[1] + margin and fbox[3] < box[3] - margin:
            region = _region(found, face)
            if region is None:
                return NoRegion("There's no area there to fill.")
            return region
        if whole:
            region = _region(found, face)
            return region if region is not None else NoRegion("There's no area there to fill.")
        need = max(abs(fbox[0] - seed[0]), abs(fbox[2] - seed[0]), abs(fbox[1] - seed[1]), abs(fbox[3] - seed[1]))
        half = max(half * 2.0, need * 1.25)
    return NoRegion("That area couldn't be found. Zoom in and try again.")


def faces(doc, only: frozenset) -> list[Region]:
    """Every area that these objects close round, with its holes: what a hatch bounded by them could be now."""
    items = boundary_items(doc, (0.0, 0.0, 0.0, 0.0), only)
    if not items or len(items) > MOST_CURVES:
        return []
    box = None
    for c, _ in items:
        box = bbox_union(box, c.bbox())
    size = max(box[2] - box[0], box[3] - box[1], 1e-9)
    merge = max(tol(box[0], box[1], box[2], box[3], size) * 100.0, size * 1e-9)
    found = _Found(_Map(items, merge), size)
    out = []
    for face in found.faces:
        region = _region(found, face)
        if region is not None:
            out.append(region)
    return out


def inside_loops(loops, p: Vec, chord: float) -> bool:
    """Is p inside an odd number of these loops?"""
    inside = False
    for loop in loops:
        if inside_polygon(loop_polygon(loop, chord), p):
            inside = not inside
    return inside


def interior_point(loops) -> Vec | None:
    """A point well inside the area these loops bound: the middle of the widest stretch across it, halfway up."""
    box = None
    for loop in loops:
        for c in loop:
            box = bbox_union(box, c.bbox())
    if box is None:
        return None
    size = max(box[2] - box[0], box[3] - box[1], 1e-12)
    polys = [loop_polygon(loop, size * 1e-5) for loop in loops]
    best = None
    for frac in (0.5, 0.3, 0.7, 0.15, 0.85, 0.42, 0.58):
        y = box[1] + (box[3] - box[1]) * frac
        xs = []
        for poly in polys:
            n = len(poly)
            for j in range(n):
                (ax, ay), (bx, by) = poly[j], poly[(j + 1) % n]
                if (ay > y) != (by > y):
                    xs.append(ax + (y - ay) * (bx - ax) / (by - ay))
        xs.sort()
        for x0, x1 in zip(xs[0::2], xs[1::2]):
            if best is None or x1 - x0 > best[0]:
                best = (x1 - x0, Vec((x0 + x1) / 2.0, y))
    return best[1] if best is not None else None


def _signature(loops) -> list[tuple[float, BBox]]:
    out = []
    for loop in loops:
        box = None
        total = 0.0
        for c in loop:
            box = bbox_union(box, c.bbox())
            total += curve_area(c)
        out.append((abs(total), box))
    out.sort(key=lambda x: (x[1][0], x[1][1], x[1][2], x[1][3]))
    return out


def same_area(a: tuple, b: tuple, size: float) -> bool:
    """Do two sets of loops bound the same area? Loop by loop, by what each holds and where it is."""
    if len(a) != len(b):
        return False
    eps = max(size, 1e-9) * 1e-8
    for (aa, ba), (ab, bb) in zip(_signature(a), _signature(b)):
        if ba is None or bb is None or abs(aa - ab) > eps * max(size, 1.0) * 10.0 or any(abs(x - y) > eps * 10.0 for x, y in zip(ba, bb)):
            return False
    return True


__all__ = ["MOST_CURVES", "NoRegion", "Region", "TooSlow", "boundary_items", "curve_area", "faces", "find", "inside_loops", "inside_polygon",
           "interior_point", "loop_polygon", "same_area"]
