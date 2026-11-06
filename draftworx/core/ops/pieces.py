# SPDX-License-Identifier: GPL-3.0-or-later
"""Cutting a curve where other things cross it, and making one reach the next thing it would hit.

There's no choosing of cutting edges. Everything that's visible cuts, which is nearly always what was meant, and hovering shows exactly which piece a click would take.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, replace

from ..curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from ..intersect import intersect
from ..model import Document, Entity, split_key
from ..select import curves_of
from ..vec import TAU, BBox, Vec, tol

CUTTABLE = (Line, Arc, Circle, EllipseArc, Spline, Polyline)


def is_closed(s) -> bool:
    k = type(s)
    if k is Circle:
        return True
    if k is Arc or k is EllipseArc:
        return abs(s.sweep) >= TAU - 1e-12
    if k is Polyline or k is Spline:
        return s.closed
    return False


def _size(s) -> float:
    b = s.bbox()
    return tol(b[0], b[1], b[2], b[3])


def _overlaps(a: BBox, b: BBox) -> bool:
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def _others(doc: Document, e: Entity, box: BBox):
    """The visible things that could touch this box, other than the thing itself."""
    seen = {e.id}
    for key in doc.index.query(box):
        i = split_key(key)[0]
        if i in seen:
            continue
        seen.add(i)
        other = doc.entities.get(i)
        if other is not None and not doc.is_hidden(other.style):
            yield other


def _self_crossings(s: Polyline) -> list[float]:
    segs = s.segments
    n = len(segs)
    out: list[float] = []
    boxes = [g.bbox() for g in segs]
    for i in range(n):
        for j in range(i + 1, n):
            if not _overlaps(boxes[i], boxes[j]):
                continue
            next_door = j == i + 1 or (s.closed and i == 0 and j == n - 1)
            for h in intersect(segs[i], segs[j]):
                if next_door:
                    shared = segs[i].end if j == i + 1 else segs[i].start
                    if h.point.distance(shared) <= _size(segs[i]) * 10.0:
                        continue
                out.append((i + h.ta) / n)
                out.append((j + h.tb) / n)
    return out


def crossings(doc: Document, e: Entity) -> list[float]:
    """Where everything else crosses this object, as positions along it, in order and without its own ends."""
    s = e.shape
    box = s.bbox()
    found: list[float] = []
    for other in _others(doc, e, box):
        for c in curves_of(doc, other):
            if _overlaps(c.bbox(), box):
                found.extend(h.ta for h in intersect(s, c))
    if type(s) is Polyline:
        found.extend(_self_crossings(s))
    return tidied(s, found)


def tidied(s, ts: list[float]) -> list[float]:
    eps = _size(s) * 10.0
    closed = is_closed(s)
    start, end = s.point_at(0.0), s.point_at(1.0)
    out: list[float] = []
    for t in sorted(t % 1.0 if closed else t for t in ts):
        p = s.point_at(t)
        if not closed and ((t < 0.5 and p.distance(start) <= eps) or (t >= 0.5 and p.distance(end) <= eps)):
            continue
        if out and p.distance(s.point_at(out[-1])) <= eps:
            continue
        out.append(t)
    if closed and len(out) > 1 and s.point_at(out[0]).distance(s.point_at(out[-1])) <= eps:
        out.pop()
    return out


def _span(s, a: float, b: float) -> tuple:
    """The stretch from a to b. On a closed curve b may run past 1, on through its start."""
    if b <= 1.0 or type(s) is not Spline:
        return (s.sub(a, b),)
    return (s.sub(a, 1.0), s.sub(0.0, b - 1.0))


def _worth_keeping(pieces: tuple, eps: float) -> tuple:
    return tuple(p for p in pieces if p.length() > eps)


@dataclass(frozen=True, slots=True)
class Cut:
    removed: tuple      # what would go
    kept: tuple         # what would be left of it


def cut_at(s, cuts: list[float], t: float) -> Cut:
    """Take away the piece of `s` between the cuts either side of position t. With nothing to cut against, the piece is the whole thing."""
    eps = _size(s) * 10.0
    if not is_closed(s):
        bounds = [0.0, *cuts, 1.0]
        i = min(bisect_right(bounds, t) - 1, len(bounds) - 2)
        a, b = bounds[i], bounds[i + 1]
        kept = (_span(s, 0.0, a) if a > 0.0 else ()) + (_span(s, b, 1.0) if b < 1.0 else ())
        return Cut(_worth_keeping(_span(s, a, b), 0.0), _worth_keeping(kept, eps))
    if len(cuts) < 2:
        return Cut((s,), ())
    i = bisect_right(cuts, t)
    if i == 0 or i == len(cuts):
        # through the start of the curve
        return Cut(_span(s, cuts[-1], cuts[0] + 1.0), _worth_keeping(_span(s, cuts[0], cuts[-1]), eps))
    a, b = cuts[i - 1], cuts[i]
    return Cut(_span(s, a, b), _worth_keeping(_span(s, b, a + 1.0), eps))


def trim_at(doc: Document, entity_id: int, p: Vec) -> Cut | None:
    e = doc.entities.get(entity_id)
    if e is None or not isinstance(e.shape, CUTTABLE):
        return None
    return cut_at(e.shape, crossings(doc, e), e.shape.project(p)[0])


def apply_pieces(t, e: Entity, pieces: tuple) -> list[int]:
    """Inside a transaction: this object becomes these pieces. The first keeps its identity, because it's the same object, shorter."""
    if not pieces:
        t.remove(e.id)
        return []
    t.replace(e.id, pieces[0])
    return [e.id] + [t.add(p, e.style) for p in pieces[1:]]


def break_at(s, t0: float, t1: float | None = None) -> tuple:
    """Split at one point, or take out what's between two. On something closed the stretch runs from the first point on round in its own direction."""
    if t1 is None:
        if not is_closed(s):
            return _worth_keeping((s.sub(0.0, t0), s.sub(t0, 1.0)), _size(s) * 10.0)
        k = type(s)
        if k is Polyline:
            return (s.sub(t0, t0 + 1.0),)
        if k is Spline:
            return (s.sub(t0, 1.0), s.sub(0.0, t0))
        raise ValueError("Something round can't be split at one point. Give a second point, to take out what's between them.")
    if not is_closed(s):
        a, b = min(t0, t1), max(t0, t1)
        return _worth_keeping((s.sub(0.0, a), s.sub(b, 1.0)), _size(s) * 10.0)
    b = t1 if t1 > t0 else t1 + 1.0
    return _worth_keeping(_span(s, b % 1.0 if b > 1.0 else b, t0 + 1.0 if b <= 1.0 else t0), _size(s) * 10.0)


# ---------------------------------------------------------------------------------------------------- extending


@dataclass(frozen=True, slots=True)
class Extended:
    shape: object       # the whole object, longer
    added: object       # only what's new


def _param_on(e: EllipseArc, p) -> float:
    d = Vec(p[0] - e.c[0], p[1] - e.c[1])
    major, minor = e.major, e.minor
    return math.atan2(d.dot(minor) / minor.length2(), d.dot(major) / major.length2())


def _beyond(piece, at_end: bool, p, eps: float) -> float | None:
    """How far past its end this point is, in the curve's own measure. None if it isn't past that end."""
    if type(piece) is Line:
        along = piece.b - piece.a
        length = along.length()
        u = (Vec(p[0], p[1]) - piece.a).dot(along) / length
        if at_end:
            return u - length if u > length + eps else None
        return -u if u < -eps else None
    start = piece.start_angle if type(piece) is Arc else piece.start_param
    angle = math.atan2(p[1] - piece.c[1], p[0] - piece.c[0]) if type(piece) is Arc else _param_on(piece, p)
    sign = 1.0 if piece.sweep >= 0.0 else -1.0
    sweep = abs(piece.sweep)
    pos = ((angle - start) * sign) % TAU
    limit = eps / max(getattr(piece, "r", 1.0), 1e-12) if type(piece) is Arc else 1e-9
    if pos <= sweep + limit or pos >= TAU - limit:
        return None     # it's on the curve already
    return pos - sweep if at_end else TAU - pos


def _longer(piece, at_end: bool, p, by: float) -> tuple:
    """(the piece, longer; only the new part)."""
    if type(piece) is Line:
        q = Vec(p[0], p[1])
        return (Line(piece.a, q), Line(piece.b, q)) if at_end else (Line(q, piece.b), Line(q, piece.a))
    sign = 1.0 if piece.sweep >= 0.0 else -1.0
    sweep = abs(piece.sweep)
    if type(piece) is Arc:
        make = lambda start, turn: Arc(piece.c, piece.r, start, turn)
        begin = piece.start_angle
    else:
        make = lambda start, turn: EllipseArc(piece.c, piece.major, piece.ratio, start, turn)
        begin = piece.start_param
    if at_end:
        return make(begin, sign * (sweep + by)), make(begin + sign * sweep, sign * by)
    return make(begin - sign * by, sign * (sweep + by)), make(begin - sign * by, sign * by)


def _search_box(doc: Document, piece, at_end: bool) -> BBox | None:
    """Where the next thing it could hit might be, without looking at everything."""
    if type(piece) is not Line:
        whole = Circle(piece.c, piece.r) if type(piece) is Arc else EllipseArc(piece.c, piece.major, piece.ratio, 0.0, TAU)
        return whole.bbox()
    box = doc.extents()
    if box is None:
        return None
    end = piece.b if at_end else piece.a
    d = (piece.b - piece.a).unit() * (1.0 if at_end else -1.0)
    # as far along it as the drawing goes
    reach = math.hypot(box[2] - box[0], box[3] - box[1]) + math.hypot(end[0] - (box[0] + box[2]) * 0.5, end[1] - (box[1] + box[3]) * 0.5)
    far = end + d * reach
    return (min(end[0], far[0]), min(end[1], far[1]), max(end[0], far[0]), max(end[1], far[1]))


def extend_at(doc: Document, entity_id: int, p: Vec) -> Extended | None:
    """Make the end nearest this point reach the next thing in its way. None if there's nothing it would reach."""
    e = doc.entities.get(entity_id)
    if e is None:
        return None
    s = e.shape
    k = type(s)
    if k not in (Line, Arc, EllipseArc, Polyline) or is_closed(s):
        return None
    at_end = s.project(p)[0] >= 0.5
    if k is Polyline:
        segs = [g for g in s.segments]
        if not segs:
            return None
        piece = segs[-1] if at_end else segs[0]
    else:
        piece = s
    box = _search_box(doc, piece, at_end)
    if box is None:
        return None
    eps = _size(piece) * 10.0
    best = None
    for other in _others(doc, e, box):
        for c in curves_of(doc, other):
            if not _overlaps(c.bbox(), box):
                continue
            for h in intersect(piece, c, extend_a=True):
                by = _beyond(piece, at_end, h.point, eps)
                if by is not None and (best is None or by < best[0]):
                    best = (by, h.point)
    if best is None:
        return None
    if type(piece) is not Line and abs(piece.sweep) + best[0] >= TAU - 1e-9:
        return None     # it would come all the way round
    longer, added = _longer(piece, at_end, best[1], best[0])
    if k is not Polyline:
        return Extended(longer, added)
    return Extended(_with_end_segment(s, longer, at_end), added)


def _with_end_segment(s: Polyline, g, at_end: bool) -> Polyline:
    """The polyline with its first or last segment replaced."""
    pts = list(s.pts)
    bulges = [s.bulge(i) for i in range(len(pts))]
    bulge = 0.0 if type(g) is Line else math.tan(g.sweep / 4.0)
    if at_end:
        pts[-1] = g.end
        bulges[-2] = bulge
    else:
        pts[0] = g.start
        bulges[0] = bulge
    return replace(s, pts=tuple(pts), bulges=tuple(bulges))
