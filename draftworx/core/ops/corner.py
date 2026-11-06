# SPDX-License-Identifier: GPL-3.0-or-later
"""Fillets and chamfers: making two things meet, in the corner you pointed at."""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..curves import Arc, Circle, Line, Polyline
from ..intersect import intersect
from ..vec import Vec, tol


@dataclass(frozen=True, slots=True)
class Corner:
    a: object | None    # what the first becomes. None if nothing is left of it.
    b: object | None    # the second
    join: object | None     # the arc or line between them. None for a sharp corner.


_CURVED = (Arc, Circle)


def _offset_curves(c, r: float) -> list:
    """Whatever is r away from this, on either side, for finding centres."""
    if type(c) is Line:
        n = (c.b - c.a).unit().perp() * r
        return [Line(c.a + n, c.b + n), Line(c.a - n, c.b - n)]
    out = [Circle(c.c, c.r + r)]
    if c.r - r > tol(c.c[0], c.c[1], c.r):
        out.append(Circle(c.c, c.r - r))
    return out


def _foot(c, p: Vec) -> Vec:
    """The nearest point on this curve carried on as far as it goes: a whole line, or a whole circle."""
    if type(c) is Line:
        d = (c.b - c.a).unit()
        return c.a + d * (p - c.a).dot(d)
    return c.c + (p - c.c).unit() * c.r


def _position(arc: Arc, p: Vec) -> float:
    """How far round this arc's own direction a point is, from its start."""
    sign = 1.0 if arc.sweep >= 0.0 else -1.0
    return ((math.atan2(p[1] - arc.c[1], p[0] - arc.c[0]) - arc.start_angle) * sign) % math.tau


def _kept(c, t: Vec, pick: Vec, eps: float):
    """What's left of a curve running to t, on the side that was pointed at. If t is off the end, the curve grows to reach it. A circle stays whole."""
    if type(c) is Circle:
        return c
    if type(c) is Line:
        d = c.b - c.a
        u, v = (t - c.a).dot(d) / d.length2(), (pick - c.a).dot(d) / d.length2()
        out = Line(t, c.b) if v > u else Line(c.a, t)
        return out if out.length() > eps else None
    sign = 1.0 if c.sweep >= 0.0 else -1.0
    sweep = abs(c.sweep)
    pos, want = _position(c, t), _position(c, pick)
    if pos <= sweep:
        if want > pos:
            out = Arc(c.c, c.r, c.start_angle + sign * pos, sign * (sweep - pos))
        else:
            out = Arc(c.c, c.r, c.start_angle, sign * pos)
    elif pos - sweep < math.tau - pos:
        out = Arc(c.c, c.r, c.start_angle, sign * pos)                      # past the end
    else:
        back = math.tau - pos
        out = Arc(c.c, c.r, c.start_angle - sign * back, sign * (sweep + back))    # before the start
    return out if out.length() > eps else None


def _minor_arc(c: Vec, r: float, a: Vec, b: Vec) -> Arc:
    a0, a1 = (a - c).angle(), (b - c).angle()
    sweep = math.remainder(a1 - a0, math.tau)
    return Arc(c, r, a0, sweep)


def _parallel(a: Line, pa: Vec, b: Line, pb: Vec, eps: float) -> Corner:
    """Two parallel lines are joined by a half circle at the end you pointed nearer."""
    near_a = a.a if a.a.distance(pa) < a.b.distance(pa) else a.b
    far_a = a.b if near_a is a.a else a.a
    t = _foot(b, near_a)
    gap = near_a.distance(t)
    if gap <= eps:
        raise ValueError("Those two lines lie on top of each other, so there's no corner to make.")
    out = (near_a - far_a).unit()
    c = near_a.midpoint(t)
    a0 = (near_a - c).angle()
    # the half that bulges out past the ends
    middle = c + out * (gap * 0.5)
    arc = Arc(c, gap * 0.5, a0, math.pi if Arc(c, gap * 0.5, a0, math.pi).point_at(0.5).distance(middle) < gap * 0.25 else -math.pi)
    return Corner(a, _kept(b, t, pb, eps), arc)


def fillet(a, pa: Vec, b, pb: Vec, r: float) -> Corner:
    """Round the corner between a and b, on the sides of them that were pointed at. A radius of nothing makes a sharp corner."""
    for c in (a, b):
        if not isinstance(c, (Line, *_CURVED)):
            raise ValueError("Only lines, arcs and circles can be filleted, and the corners of a polyline.")
    if r < 0.0:
        raise ValueError("A radius can't be below zero.")
    box = a.bbox()
    other = b.bbox()
    eps = tol(box[0], box[1], box[2], box[3], other[0], other[1], other[2], other[3]) * 100.0
    if type(a) is Line and type(b) is Line:
        da, db = (a.b - a.a).unit(), (b.b - b.a).unit()
        if abs(da.cross(db)) < 1e-12:
            return _parallel(a, pa, b, pb, eps)
    if r <= eps:
        hits = intersect(a, b, True, True)
        if not hits:
            raise ValueError("Those two never meet.")
        x = min(hits, key=lambda h: h.point.distance(pa) + h.point.distance(pb)).point
        return Corner(_kept(a, x, pa, eps), _kept(b, x, pb, eps), None)
    best = None
    for oa in _offset_curves(a, r):
        for ob in _offset_curves(b, r):
            for h in intersect(oa, ob, True, True):
                ta, tb = _foot(a, h.point), _foot(b, h.point)
                score = ta.distance(pa) + tb.distance(pb)
                if best is None or score < best[0]:
                    best = (score, h.point, ta, tb)
    if best is None:
        raise ValueError(f"A radius of {r:g} can't touch both of those.")
    _, c, ta, tb = best
    return Corner(_kept(a, ta, pa, eps), _kept(b, tb, pb, eps), _minor_arc(c, r, ta, tb))


def chamfer(a: Line, pa: Vec, b: Line, pb: Vec, d1: float, d2: float) -> Corner:
    """Cut the corner between two lines with a straight line, this far back along each."""
    if type(a) is not Line or type(b) is not Line:
        raise ValueError("Only straight lines can be chamfered, and the straight corners of a polyline.")
    box, other = a.bbox(), b.bbox()
    eps = tol(box[0], box[1], box[2], box[3], other[0], other[1], other[2], other[3]) * 100.0
    hits = intersect(a, b, True, True)
    if not hits or abs((a.b - a.a).unit().cross((b.b - b.a).unit())) < 1e-12:
        raise ValueError("Those two lines are parallel, so there's no corner to cut.")
    x = hits[0].point
    if d1 <= eps and d2 <= eps:
        return Corner(_kept(a, x, pa, eps), _kept(b, x, pb, eps), None)

    def back(c: Line, pick: Vec, d: float) -> Vec:
        along = (c.b - c.a).unit()
        side = 1.0 if (pick - x).dot(along) >= 0.0 else -1.0
        return x + along * (side * d)

    ta, tb = back(a, pa, d1), back(b, pb, d2)
    return Corner(_kept(a, ta, pa, eps), _kept(b, tb, pb, eps), Line(ta, tb))


# ---------------------------------------------------------------------------------- corners of a polyline


def _rebuilt(s: Polyline, segs: list, closed: bool) -> Polyline:
    pts: list[Vec] = []
    bulges: list[float] = []
    for g in segs:
        pts.append(g.start)
        bulges.append(0.0 if type(g) is Line else math.tan(g.sweep / 4.0))
    if not closed:
        pts.append(segs[-1].end)
        bulges.append(0.0)
    return Polyline(tuple(pts), tuple(bulges), closed, s.width)


def _on(g, p: Vec, eps: float) -> bool:
    return g.project(p)[1].distance(p) <= eps


def _corner_between(a, b, make, eps: float) -> tuple:
    """(what the first segment becomes, the join, what the second becomes), keeping each segment's far end, and refusing to run off it."""
    c = make(a, a.start, b, b.end)
    ca, cb, join = c.a, c.b, c.join
    if join is None:
        return ca, None, cb
    if not _on(a, join.start, eps) or not _on(b, join.end, eps):
        raise ValueError("That's more than the corner has room for.")
    return ca, join, cb


def corner_of_polyline(s: Polyline, vertex: int, make) -> Polyline:
    """Fillet or chamfer one corner of a polyline. `make(a, pa, b, pb)` is the fillet or chamfer."""
    segs = list(s.segments)
    n = len(segs)
    if n < 2 or (not s.closed and not 0 < vertex < n):
        raise ValueError("That isn't a corner: it's the end of the polyline.")
    i, j = (vertex - 1) % n, vertex % n
    box = s.bbox()
    eps = tol(box[0], box[1], box[2], box[3]) * 100.0
    ca, join, cb = _corner_between(segs[i], segs[j], make, eps)
    replaced: list = []
    for k in range(n):
        g = segs[k]
        if k == i:
            g = ca
        elif k == j:
            g = cb
        if g is not None:
            replaced.append(g)
        if k == i and join is not None:
            replaced.append(join)
    return _rebuilt(s, replaced, s.closed)


def every_corner(s: Polyline, make) -> tuple[Polyline, int]:
    """Fillet or chamfer all the corners there are. Returns the polyline, and how many corners had no room."""
    segs = list(s.segments)
    if len(segs) < 2:
        raise ValueError("A polyline with one piece has no corners.")
    box = s.bbox()
    eps = tol(box[0], box[1], box[2], box[3]) * 100.0
    out: list = [segs[0]]
    skipped = 0
    changed = False
    for j in list(range(1, len(segs))) + ([0] if s.closed else []):
        a = out[-1]
        b = out[0] if j == 0 else segs[j]
        if abs(a.tangent_at(1.0).unit().cross(b.tangent_at(0.0).unit())) < 1e-9:
            if j != 0:
                out.append(b)       # smooth already, so there's no corner here
            continue
        try:
            ca, join, cb = _corner_between(a, b, make, eps)
        except ValueError:
            skipped += 1
            if j != 0:
                out.append(b)
            continue
        changed = True
        if ca is not None:
            out[-1] = ca
        else:
            out.pop()
        if join is not None:
            out.append(join)
        if j == 0:
            out[0] = cb if cb is not None else out[0]
        elif cb is not None:
            out.append(cb)
    return (_rebuilt(s, out, s.closed) if changed else s), skipped
