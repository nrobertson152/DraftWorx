# SPDX-License-Identifier: GPL-3.0-or-later
"""Offsetting: everything a set distance to one side.

A line, a circle and an arc stay what they are, exactly. A polyline stays a polyline, exactly, and that's where the work is. Move each piece over and round each corner
on the outside, keep exactly the parts of that which are at least the distance from all of the original, join them up, and only then let straight sides carry on to
sharp corners where that stays far enough away (docs/ARCHITECTURE.md, ADR-12). An offset of an ellipse or a spline isn't an ellipse or a spline, so it's a spline
fitted to within a stated distance.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from heapq import heappop, heappush

from ..curves import Arc, Circle, EllipseArc, Line, Polyline, Spline, spline_through
from ..intersect import _segment_gap as segment_gap, intersect
from ..spatial import SpatialIndex
from ..vec import Vec, tol


@dataclass(frozen=True, slots=True)
class Offset:
    shapes: tuple
    within: float = 0.0     # how far it can be from the true offset. 0 means it's exact.


def is_left(s, p) -> bool:
    """Is this point to the left of the direction the object runs in? Exactly on it counts as left."""
    t, q = s.project(p)
    d = s.tangent_at(t)
    return d[0] * (p[1] - q[1]) - d[1] * (p[0] - q[0]) >= 0.0


def side_of(s, toward: Vec) -> bool | None:
    """Which side of it a point is on, as an offset sees it: outside a circle or an arc, or to the left of anything else. None for what can't be offset."""
    k = type(s)
    if k is Circle or k is Arc:
        return s.c.distance(toward) >= s.r
    if k is Line or k is Polyline or k is EllipseArc or k is Spline:
        return is_left(s, toward)
    return None


def offset(s, distance: float, toward: Vec, deadline: float | None = None) -> Offset:
    """This object moved `distance` to the side `toward` is on. Raises ValueError if nothing would be left, and TimeoutError if it isn't done by `deadline`
    (a time.monotonic() value), which is for a preview that mustn't hold anything up."""
    if distance <= 0.0:
        raise ValueError("An offset needs a distance above zero.")
    k = type(s)
    if k is Circle or k is Arc:
        outward = side_of(s, toward)
        r = s.r + distance if outward else s.r - distance
        if r <= tol(s.c[0], s.c[1], s.r):
            raise ValueError(f"That's further in than its radius ({s.r:g}).")
        return Offset((Circle(s.c, r) if k is Circle else replace(s, r=r),))
    signed = distance if side_of(s, toward) else -distance
    if k is Line:
        n = (s.b - s.a).unit().perp() * signed
        return Offset((Line(s.a + n, s.b + n),))
    if k is Polyline:
        return Offset(_polyline(s, signed, deadline))
    if k is EllipseArc or k is Spline:
        return _smooth(s, signed)
    raise ValueError("Only lines, arcs, circles, ellipses, splines and polylines can be offset.")


# ------------------------------------------------------------------------------------------- ellipses and splines


def _smooth(s, signed: float) -> Offset:
    box = s.bbox()
    size = max(box[2] - box[0], box[3] - box[1], abs(signed))
    target = size * 1e-5
    far = abs(signed) * (1.0 - 1e-6)

    def at(t: float) -> Vec | None:
        d = s.tangent_at(t)
        if d.length2() == 0.0:
            return None
        return s.point_at(t) + d.unit().perp() * signed

    best = None
    n = 32
    while n <= 1024:
        runs: list[list[tuple[float, Vec]]] = [[]]
        for i in range(n + 1):
            t = i / n
            q = at(t)
            # inside a tight bend the offset folds back on itself, and those points are too close to be part of it
            if q is None or s.project(q)[1].distance(q) < far:
                if runs[-1]:
                    runs.append([])
                continue
            runs[-1].append((t, q))
        shapes: list[Spline] = []
        worst = 0.0
        for run in runs:
            if len(run) < 2:
                continue
            start, end = s.tangent_at(run[0][0]), s.tangent_at(run[-1][0])
            sp = spline_through([q for _, q in run], start, end)
            shapes.append(sp)
            for (t0, _), (t1, _) in zip(run, run[1:]):
                q = at((t0 + t1) * 0.5)
                if q is not None:
                    worst = max(worst, sp.project(q)[1].distance(q))
        if not shapes:
            raise ValueError("There's nothing left at that distance.")
        best = Offset(tuple(shapes), max(worst, 1e-12))
        if worst <= target:
            break
        n *= 2
    return best


# ----------------------------------------------------------------------------------------------------- polylines


def _shifted(g, signed: float):
    """One piece moved to the left of its direction. An arc that closes up to nothing becomes a straight line between where its ends went, which is thrown away later."""
    if type(g) is Line:
        n = (g.b - g.a).unit().perp() * signed
        return Line(g.a + n, g.b + n)
    r = g.r - signed if g.sweep > 0.0 else g.r + signed
    if r > tol(g.c[0], g.c[1], g.r):
        return Arc(g.c, r, g.start_angle, g.sweep)
    return Line(g.start + g.tangent_at(0.0).unit().perp() * signed, g.end + g.tangent_at(1.0).unit().perp() * signed)


def _trim_end(g, t: float, p: Vec):
    return Line(g.a, p) if type(g) is Line else g.sub(0.0, t)


def _trim_start(g, t: float, p: Vec):
    return Line(p, g.b) if type(g) is Line else g.sub(t, 1.0)


def _round_join(v: Vec, a: Vec, b: Vec, turn: float):
    """An arc round a corner, going the way the path turns, which is never more than half way round. At exactly half way, the turn says which half."""
    a0 = (a - v).angle()
    sweep = math.remainder((b - v).angle() - a0, math.tau)
    if abs(abs(sweep) - math.pi) < 1e-9:
        sweep = math.copysign(math.pi, turn)
    return Arc(v, v.distance(a), a0, sweep)


def _joined(a, b, v: Vec, ta: Vec, tb: Vec, signed: float, eps: float) -> tuple:
    """Two neighbouring offset pieces, meeting. Returns (a, what goes between, b, whether that's a rounded corner)."""
    ea, sb = a.end, b.start
    if ea.distance(sb) <= eps:
        return a, (), b, False
    cross = ta.unit().cross(tb.unit())
    dot = ta.unit().dot(tb.unit())
    if abs(cross) < 1e-12:
        if dot > 0.0:
            return a, (Line(ea, sb),), b, False
        return a, (_round_join(v, ea, sb, -1.0 if signed > 0.0 else 1.0),), b, True
    if cross * signed > 0.0:
        # the inside of the bend: they overlap, so each stops where they cross
        hits = intersect(a, b)
        if hits:
            h = min(hits, key=lambda h: h.point.distance(v))
            return _trim_end(a, h.ta, h.point), (), _trim_start(b, h.tb, h.point), False
        return a, (), b, False      # too short to meet. Whatever's between them is too close to the corner.
    return a, (_round_join(v, ea, sb, cross),), b, True


def _chain(s: Polyline, segs: list, signed: float, eps: float) -> tuple[list, list[bool], list[int]]:
    """The offset pieces in order, joined where they meet and rounded round each corner on the outside, with whatever loops that makes. Also which of them are those
    rounded corners, and the piece of the original each came from or comes after."""
    n = len(segs)
    raw = [_shifted(g, signed) for g in segs]
    between: list[tuple] = [()] * n
    rounded = [False] * n
    for i in range(n if s.closed else n - 1):
        j = (i + 1) % n
        raw[i], between[i], raw[j], rounded[i] = _joined(raw[i], raw[j], segs[i].end, segs[i].tangent_at(1.0), segs[j].tangent_at(0.0), signed, eps)
    pieces: list = []
    corners: list[bool] = []
    origins: list[int] = []
    for i in range(n):
        if raw[i].length() > eps:
            pieces.append(raw[i])
            corners.append(False)
            origins.append(i)
        for g in between[i]:
            if g.length() > eps:
                pieces.append(g)
                corners.append(rounded[i])
                origins.append(i)
    return pieces, corners, origins


def _edges_of(g, distance: float, eps: float) -> tuple:
    """Everything this far from a piece of the original, which is the only place a point's distance from it can change between too close and far enough. And the
    lines across its ends, where the nearest point on it stops being inside it and becomes an end: something running along exactly the distance from it, as a corner
    going round where the path comes back to the same point does, can only change there."""
    def across(p: Vec, direction: Vec) -> Line:
        n = direction.unit().perp() * distance
        return Line(p - n, p + n)
    caps = (Circle(g.start, distance), Circle(g.end, distance), across(g.start, g.tangent_at(0.0)), across(g.end, g.tangent_at(1.0)))
    if type(g) is Line:
        n = (g.b - g.a).unit().perp() * distance
        return (Line(g.a + n, g.b + n), Line(g.a - n, g.b - n)) + caps
    inner = abs(g.r - distance)
    return (Circle(g.c, g.r + distance),) + ((Circle(g.c, inner),) if inner > eps else ()) + caps


def _chord(g) -> tuple | None:
    """A straight stand-in for a piece: its ends, and how far the piece can be from the line between them. None if that says nothing useful."""
    if type(g) is Line:
        return g.a, g.b, 0.0
    if abs(g.sweep) > math.pi:
        return None
    return g.start, g.end, g.r * (1.0 - math.cos(g.sweep * 0.5))


class _Original:
    """The path being offset, for asking what's too close to it.

    Its pieces hang off a tree of boxes over runs of neighbours. Neighbours along a path are near each other, so the boxes stay small, and the nearest are looked at
    first. Each that's too close to some of a curve leaves less of it to ask about, in a smaller box, which rules out more of the rest. So a curve is only measured against
    the few pieces that decide where it stops, however many pieces there are."""
    __slots__ = ("segs", "distance", "eps", "least", "reach", "edges", "chords", "size", "boxes")

    def __init__(self, segs: list, distance: float, eps: float):
        self.segs = segs
        self.distance = distance
        self.eps = eps
        self.least = distance - eps * 10.0
        self.reach = distance * distance
        self.edges = [_edges_of(g, distance, eps) for g in segs]
        self.chords = [_chord(g) for g in segs]
        size = 1
        while size < len(segs):
            size *= 2
        self.size = size
        boxes: list = [None] * (2 * size)
        for i, g in enumerate(segs):
            boxes[size + i] = g.bbox()
        for i in range(size - 1, 0, -1):
            a, b = boxes[2 * i], boxes[2 * i + 1]
            boxes[i] = a if b is None or a is None else (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
        self.boxes = boxes

    def _too_close_to(self, g, k: int, limit: float) -> list[tuple[float, float]]:
        """The parts of a curve closer than the distance to one piece, as ranges of its parameter. A curve can only go from one to the other where it meets something
        exactly the distance from the piece, so between two of those a test tells for all of it. It's two tests, at different places: something can touch that
        distance without crossing it, and one test could land just there, as it does for a corner going round where the path crosses itself."""
        h = self.segs[k]
        least = self.least
        ts = [0.0, 1.0]
        for edge in self.edges[k]:
            ts.extend(hit.ta for hit in intersect(g, edge))
        ts.sort()
        parts: list[tuple[float, float]] = []
        for a, b in zip(ts, ts[1:]):
            if b - a <= limit:
                continue
            q = g.point_at(a + (b - a) * 0.375)
            if h.project(q)[1].distance(q) >= least:
                q = g.point_at(a + (b - a) * 0.625)
            if h.project(q)[1].distance(q) < least:
                if parts and a - parts[-1][1] <= limit:
                    parts[-1] = (parts[-1][0], b)
                else:
                    parts.append((a, b))
        return parts

    def far_enough(self, g, near: int = -1) -> list[tuple[float, float]]:
        """The parts of a curve that are at least the distance from all of the original, in order, as ranges of its parameter. `near` is the piece it came from: that
        and its neighbours are what usually cut it short, so they're looked at first."""
        limit = self.eps / g.length()
        reach, boxes, size, chords, least, n = self.reach, self.boxes, self.size, self.chords, self.least, len(self.segs)
        mine = _chord(g)
        left = [(0.0, 1.0)]
        x0, y0, x1, y1 = g.bbox()
        first = [(near + k) % n for k in (2, -2, 1, -1, 0)] if near >= 0 else []
        done: set[int] = set()
        heap = [(0.0, 1)]
        while first or heap:
            i = size + first.pop() if first else heappop(heap)[1]
            b = boxes[i]
            dx = max(b[0] - x1, x0 - b[2], 0.0)
            dy = max(b[1] - y1, y0 - b[3], 0.0)
            if dx * dx + dy * dy >= reach:
                continue        # out of reach, or the box was bigger when this was put on the heap
            if i < size:
                for k in (i + i, i + i + 1):
                    c = boxes[k]
                    if c is not None:
                        dx = max(c[0] - x1, x0 - c[2], 0.0)
                        dy = max(c[1] - y1, y0 - c[3], 0.0)
                        if dx * dx + dy * dy < reach:
                            heappush(heap, (dx * dx + dy * dy, k))
                continue
            k = i - size
            if k in done:
                continue
            done.add(k)
            theirs = chords[k]
            if mine is not None and theirs is not None and segment_gap(mine[0], mine[1], theirs[0], theirs[1])[0] - mine[2] - theirs[2] >= least:
                continue        # nowhere near, as most are: much quicker to tell than where they meet
            close = self._too_close_to(g, k, limit)
            if not close:
                continue
            left = _without(left, close, limit)
            if not left:
                return []
            x0 = y0 = math.inf
            x1 = y1 = -math.inf
            for a, b in left:
                p = _part(g, a, b).bbox()
                x0, y0, x1, y1 = min(x0, p[0]), min(y0, p[1]), max(x1, p[2]), max(y1, p[3])
        return left

    def clear(self, g, near: int = -1) -> bool:
        """Is all of it far enough?"""
        return self.far_enough(g, near) == [(0.0, 1.0)]


def _without(left: list[tuple[float, float]], cut: list[tuple[float, float]], limit: float) -> list[tuple[float, float]]:
    """Ranges with others taken out of them. Anything too short to matter is dropped."""
    out: list[tuple[float, float]] = []
    for a, b in left:
        here = a
        for c0, c1 in cut:
            if c1 <= here or c0 >= b:
                continue
            if c0 - here > limit:
                out.append((here, c0))
            here = max(here, c1)
            if here >= b:
                break
        if b - here > limit:
            out.append((here, b))
    return out


@dataclass(frozen=True, slots=True)
class _Bit:
    """A part of one piece of the path."""
    source: int
    a: float
    b: float
    shape: object


def _part(g, a: float, b: float):
    if a == 0.0 and b == 1.0:
        return g
    if type(g) is Line:
        return Line(g.a if a == 0.0 else g.point_at(a), g.b if b == 1.0 else g.point_at(b))
    return g.sub(a, b)


def _follows(before: _Bit, after: _Bit, eps: float) -> bool:
    return before.b == 1.0 and after.a == 0.0 and before.shape.end.distance(after.shape.start) <= eps


def _in_time(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() > deadline:
        raise TimeoutError("That's taking too long to show.")


def _runs(chain: list, origins: list[int], original: _Original, closed: bool, eps: float, deadline: float | None) -> list[list[_Bit]]:
    """What's far enough, as stretches that run on without a break."""
    runs: list[list[_Bit]] = []
    for i, g in enumerate(chain):
        if not i & 63:
            _in_time(deadline)
        for a, b in original.far_enough(g, origins[i]):
            bit = _Bit(i, a, b, _part(g, a, b))
            if runs and runs[-1][-1].source == i - 1 and _follows(runs[-1][-1], bit, eps):
                runs[-1].append(bit)
            else:
                runs.append([bit])
    if closed and len(runs) > 1:
        first, last = runs[0][0], runs[-1][-1]
        if first.source == 0 and last.source == len(chain) - 1 and _follows(last, first, eps):
            runs[0] = runs.pop() + runs[0]
    return runs


def _stitched(runs: list[list[_Bit]], eps: float) -> list[tuple[list[_Bit], bool]]:
    """Join up the stretches, end to start, because where the path passes close to itself one stops where another starts. Returns each, and whether it closes up."""
    starts = SpatialIndex()
    for i, run in enumerate(runs):
        p = run[0].shape.start
        starts.insert(i, (p[0], p[1], p[0], p[1]))
    out: list[tuple[list[_Bit], bool]] = []
    for i, run in enumerate(runs):
        if i not in starts:
            continue
        starts.remove(i)
        joined = list(run)
        begins = run[0].shape.start
        while begins.distance(joined[-1].shape.end) > eps:
            end = joined[-1].shape.end
            found = starts.query((end[0] - eps, end[1] - eps, end[0] + eps, end[1] + eps))
            if not found:
                break
            starts.remove(found[0])
            joined.extend(runs[found[0]])
        out.append((joined, begins.distance(joined[-1].shape.end) <= eps))
    return out


def _meeting(p, q, near: int, original: _Original, limit: float, spikes: list, index: SpatialIndex) -> tuple | None:
    """Where two straight pieces meet if they carry on: the two of them to that point, or the one of them if they're the same line. One may stop short there if the other
    carries on to it. None if they don't meet, if that's absurdly far, or if carrying on would take them too close to the original or across a corner that's already been
    made like this."""
    if type(p) is not Line or type(q) is not Line:
        return None
    eps = original.eps
    dp, dq = (p.b - p.a).unit(), (q.b - q.a).unit()
    if abs(dp.cross(dq)) < 1e-9:
        if dp.dot(dq) <= 0.0 or abs(dp.cross(q.a - p.b)) > eps:
            return None
        made = (Line(p.a, q.b),)
        carried = [Line(p.b, q.a)]
    else:
        hits = intersect(p, q, True, True)
        if not hits:
            return None
        x = hits[0].point
        past_p, before_q = (x - p.b).dot(dp), (q.a - x).dot(dq)
        if (x - p.a).dot(dp) <= eps or (q.b - x).dot(dq) <= eps or max(abs(past_p), abs(before_q)) > limit:
            return None
        made = (Line(p.a, x), Line(x, q.b))
        carried = []
        if past_p > eps:
            carried.append(Line(p.b, x))
        if before_q > eps:
            carried.append(Line(x, q.a))
        if not carried:
            return None     # they cross, which isn't this
    for c in carried:
        if c.length() > limit or not original.clear(c, near):
            return None
        for k in index.query(c.bbox()):
            if intersect(c, spikes[k]):
                return None
    for c in carried:
        index.insert(len(spikes), c.bbox())
        spikes.append(c)
    return made


def _sharpened(run: list[_Bit], closes: bool, rounded: list[bool], origins: list[int], original: _Original, limit: float, spikes: list,
               index: SpatialIndex) -> list[_Bit]:
    """Straight pieces carry on until they meet, as they would if you drew it by hand, rather than going round the corner. Rounding it first is what makes the distances
    exact, and carrying on can only take it further from the corner."""
    if closes:
        first = next((i for i, b in enumerate(run) if not rounded[b.source]), None)
        if first is None:
            return run
        run = run[first:] + run[:first]
    out: list[_Bit] = []
    i, n = 0, len(run)
    while i < n:
        if not rounded[run[i].source] or not out:
            out.append(run[i])
            i += 1
            continue
        j = i
        while j < n and rounded[run[j].source]:
            j += 1
        wraps = j == n
        if wraps and (not closes or len(out) < 2):
            out.extend(run[i:])
            break
        before, after = out[-1], (out[0] if wraps else run[j])
        made = _meeting(before.shape, after.shape, origins[run[i].source], original, limit, spikes, index)
        if made is None:
            out.extend(run[i:j])
        else:
            out[-1] = replace(before, shape=made[0])
            if wraps:
                if len(made) == 2:
                    out[0] = replace(after, shape=made[1])
                else:
                    out.pop(0)
            else:
                if len(made) == 2:
                    out.append(replace(after, shape=made[1]))
                j += 1
        i = j
    return out


def _as_polyline(bits: list[_Bit], closed: bool, width: float) -> Polyline:
    if closed:
        # a loop starts where the original does, as near as it can
        first = min(range(len(bits)), key=lambda k: (bits[k].source, bits[k].a))
        bits = bits[first:] + bits[:first]
    pts: list[Vec] = []
    bulges: list[float] = []
    for bit in bits:
        g = bit.shape
        pts.append(g.start)
        bulges.append(0.0 if type(g) is Line else math.tan(g.sweep / 4.0))
    if not closed:
        pts.append(bits[-1].shape.end)
        bulges.append(0.0)
    return Polyline(tuple(pts), tuple(bulges), closed, width)


def _polyline(s: Polyline, signed: float, deadline: float | None = None) -> tuple[Polyline, ...]:
    box = s.bbox()
    eps = tol(box[0], box[1], box[2], box[3], signed) * 100.0
    segs = [g for g in s.segments if g.length() > eps]
    if not segs:
        raise ValueError("That polyline has no length to offset.")
    chain, rounded, origins = _chain(s, segs, signed, eps)
    distance = abs(signed)
    original = _Original(segs, distance, eps)
    spikes: list = []
    index = SpatialIndex()
    out = []
    for run, closes in _stitched(_runs(chain, origins, original, s.closed, eps * 10.0, deadline), eps * 10.0):
        _in_time(deadline)
        if closes or sum(b.shape.length() for b in run) > eps * 10.0:
            out.append(_as_polyline(_sharpened(run, closes, rounded, origins, original, distance * 10.0, spikes, index), closes, s.width))
    if not out:
        raise ValueError("There's nothing left at that distance.")
    return tuple(out)
