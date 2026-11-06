# SPDX-License-Identifier: GPL-3.0-or-later
"""What's under the cursor, and what's in a selection window."""
from __future__ import annotations

import math
from typing import Iterator

from . import dim as dims
from .curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from .model import Document, Entity, split_key
from .shapes import AttDef, Dimension, Foreign, Hatch, Insert, MText, Node, Solid, Text
from .vec import IDENTITY, Affine, BBox, Vec, bbox_contains

_CURVES = (Line, Arc, Circle, EllipseArc, Spline)
PICK_REACH = 6.0        # pixels
MOST_PIECES = 200_000   # blocks in blocks in arrays can multiply past anything a click could afford to look at
_MOST_CELLS = 64        # of a block's array, when looking for text in it


def _placed(c, m: Affine):
    """A curve where a block's copy puts it, or None if that copy is scaled to nothing."""
    if m is IDENTITY:
        return c
    try:
        return c.transformed(m)
    except (ValueError, ArithmeticError):
        return None


def drawn_by(doc: Document, d: Dimension) -> list:
    """What a dimension draws, as shapes, unless a file's block draws it."""
    return [] if d.block and doc.blocks.get(d.block) is not None else dims.drawn(d, doc.dim_style_of(d))


def curves_of(doc: Document, e: Entity, m: Affine = IDENTITY, _depth: int = 0, _left: list | None = None) -> Iterator:
    """Every curve an entity draws, in drawing coordinates, including inside blocks. There's a limit to how many, shared by everything inside it."""
    s = e.shape
    k = type(s)
    parts = ()
    if k in _CURVES:
        parts = (s,)
    elif k is Polyline:
        parts = s.segments
    elif k is Solid:
        parts = s.outline().segments
    elif k is Foreign:
        parts = [seg for c in s.curves for seg in (c.segments if type(c) is Polyline else (c,))]
    elif k is Dimension:
        parts = [seg for item in drawn_by(doc, s) for seg in ((item,) if type(item) in _CURVES else item.outline().segments if type(item) is Solid else ())]
    for c in parts:
        c = _placed(c, m)
        if c is not None:
            yield c
    if (k is Foreign or k is Dimension) and s.block:
        k = Insert
        s = Insert(s.block, s.at if type(s) is Foreign else Vec(0.0, 0.0))
    if k is Insert and _depth < 16:
        d = doc.blocks.get(s.block)
        if d is None:
            return
        left = _left if _left is not None else [MOST_PIECES]
        for col in range(s.columns):
            for row in range(s.rows):
                local = s.matrix(d.base, col, row)
                full = local if m is IDENTITY else local.then(m)
                for inner in d.entities:
                    left[0] -= 1
                    if left[0] < 0:
                        return
                    if inner.style.layer != "0" and doc.is_hidden(inner.style):
                        continue
                    yield from curves_of(doc, inner, full, _depth + 1, left)


def _text_box_distance(box: tuple[Vec, Vec, Vec], p) -> float:
    """How far a point is from a text's box. Zero inside it."""
    o, a, u = box
    la, lu = a.length(), u.length()
    if la == 0.0 or lu == 0.0:
        return math.hypot(p[0] - o[0], p[1] - o[1])
    dx, dy = p[0] - o[0], p[1] - o[1]
    s = (dx * a[0] + dy * a[1]) / la
    t = (dx * u[0] + dy * u[1]) / lu
    ox = 0.0 if 0.0 <= s <= la else min(abs(s), abs(s - la))
    oy = 0.0 if 0.0 <= t <= lu else min(abs(t), abs(t - lu))
    return math.hypot(ox, oy)


def _in_polygon(pts, p) -> bool:
    inside = False
    n = len(pts)
    x, y = p[0], p[1]
    for i in range(n):
        (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            inside = not inside
    return inside


def hatch_distance(h: Hatch, p) -> float:
    """How far a point is from a hatch's area: zero inside it."""
    from .hatch import polygons
    from .regions import inside_polygon
    box = h.bbox()
    if box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]:
        inside = False
        for poly in polygons(h):
            if len(poly) > 2 and inside_polygon(poly, p):
                inside = not inside
        if inside:
            return 0.0
    best = math.inf
    for loop in h.loops:
        for c in loop:
            q = c.project(p)[1]
            best = min(best, math.hypot(q[0] - p[0], q[1] - p[1]))
    return best


def distance_to(doc: Document, e: Entity, p, _depth: int = 0) -> float:
    """How far a point is from what an entity draws."""
    s = e.shape
    k = type(s)
    if k is Hatch:
        return hatch_distance(s, p)
    if k in _CURVES:
        q = s.project(p)[1]
        return math.hypot(q[0] - p[0], q[1] - p[1])
    if k is Polyline:
        q = s.project(p)[1]
        return math.hypot(q[0] - p[0], q[1] - p[1])
    if k is Node:
        return math.hypot(s.p[0] - p[0], s.p[1] - p[1])
    if k is Text or k is MText:
        return _text_box_distance(s.box(), p)
    if k is AttDef:
        return _text_box_distance(s.attrib.text.box(), p)
    if k is Solid:
        if _in_polygon(s.pts, p):
            return 0.0
        q = s.outline().project(p)[1]
        return math.hypot(q[0] - p[0], q[1] - p[1])
    if k is Insert:
        return _insert_distance(doc, s, p, _depth)
    if k is Dimension:
        best = math.inf
        if s.block and doc.blocks.get(s.block) is not None:
            return _insert_distance(doc, Insert(s.block, Vec(0.0, 0.0)), p, _depth)
        for item in drawn_by(doc, s):
            if type(item) is Text:
                best = min(best, _text_box_distance(item.box(), p))
            elif type(item) is Solid:
                best = min(best, 0.0 if _in_polygon(item.pts, p) else distance_to(doc, Entity(0, item), p))
            else:
                q = item.project(p)[1]
                best = min(best, math.hypot(q[0] - p[0], q[1] - p[1]))
        return best
    if k is Foreign:
        best = math.inf
        for c in s.curves:
            q = c.project(p)[1]
            best = min(best, math.hypot(q[0] - p[0], q[1] - p[1]))
        if s.block:
            best = min(best, _insert_distance(doc, Insert(s.block, s.at), p, _depth))
        return best
    return math.inf


def _insert_distance(doc: Document, ins: Insert, p, depth: int) -> float:
    if depth > 16:
        return math.inf
    d = doc.blocks.get(ins.block)
    if d is None:
        return math.hypot(ins.p[0] - p[0], ins.p[1] - p[1])
    best = math.inf
    for c in curves_of(doc, Entity(0, ins)):
        q = c.project(p)[1]
        best = min(best, math.hypot(q[0] - p[0], q[1] - p[1]))
    for a in ins.attribs:
        if not a.invisible:
            best = min(best, _text_box_distance(a.text.box(), p))
    for col in range(min(ins.columns, _MOST_CELLS)):
        for row in range(min(ins.rows, _MOST_CELLS)):
            m = ins.matrix(d.base, col, row)
            for inner in d.entities:
                k = type(inner.shape)
                if k is Text or k is MText or k is Node:
                    moved = inner.shape.transformed(m)
                    best = min(best, distance_to(doc, Entity(0, moved), p))
    return best


def pick(doc: Document, p, px: float, reach: float = PICK_REACH, skip_locked: bool = True, filter=None) -> list[int]:
    """Entities under a point, nearest first. Closest wins, then what's on top."""
    r = reach * px
    window = (p[0] - r, p[1] - r, p[0] + r, p[1] + r)
    seen: set[int] = set()
    found: list[tuple[float, int, int]] = []
    for key in doc.index.query(window):
        eid, _ = split_key(key)
        if eid in seen:
            continue
        seen.add(eid)
        e = doc.entities.get(eid)
        if e is None or doc.is_hidden(e.style) or (skip_locked and doc.is_locked(e.style)):
            continue
        if filter is not None and not filter(e):
            continue
        d = distance_to(doc, e, p)
        if d <= r:
            if type(e.shape) is Hatch:
                d = max(d, r * 0.95)        # anything drawn over a hatch, or near it, wins the click
            found.append((d, -e.order, eid))
    found.sort()
    return [f[2] for f in found]


def _segment_hits_box(ax, ay, bx, by, box: BBox) -> bool:
    """Liang-Barsky: does this segment touch this box?"""
    x0, y0, x1, y1 = box
    t0, t1 = 0.0, 1.0
    dx, dy = bx - ax, by - ay
    for d, q0, q1 in ((dx, x0 - ax, x1 - ax), (dy, y0 - ay, y1 - ay)):
        if d == 0.0:
            if q0 > 0.0 or q1 < 0.0:
                return False
        else:
            a, b = q0 / d, q1 / d
            if a > b:
                a, b = b, a
            if a > t0:
                t0 = a
            if b < t1:
                t1 = b
            if t0 > t1:
                return False
    return True


def curve_touches_box(c, box: BBox, px: float) -> bool:
    bb = c.bbox()
    if bb[0] > box[2] or bb[2] < box[0] or bb[1] > box[3] or bb[3] < box[1]:
        return False
    if bbox_contains(box, bb):
        return True
    if type(c) is Line:
        return _segment_hits_box(c.a[0], c.a[1], c.b[0], c.b[1], box)
    pts = c.flatten(max(px * 0.25, 1e-9))
    return any(_segment_hits_box(a[0], a[1], b[0], b[1], box) for a, b in zip(pts, pts[1:]))


def _entity_touches_box(doc: Document, e: Entity, box: BBox, px: float) -> bool:
    s = e.shape
    k = type(s)
    if k is Node:
        return box[0] <= s.p[0] <= box[2] and box[1] <= s.p[1] <= box[3]
    if k is Text or k is MText or k is AttDef:
        t = s.attrib.text if k is AttDef else s
        o, a, u = t.box()
        corners = (o, o + a, o + a + u, o + u)
        if any(box[0] <= q[0] <= box[2] and box[1] <= q[1] <= box[3] for q in corners):
            return True
        return any(_segment_hits_box(corners[i][0], corners[i][1], corners[(i + 1) % 4][0], corners[(i + 1) % 4][1], box) for i in range(4)) \
            or _in_polygon(corners, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2))
    if k is Solid and _in_polygon(s.pts, ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)):
        return True
    if k is Hatch:
        middle = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        return hatch_distance(s, middle) == 0.0 or any(curve_touches_box(c, box, px) for loop in s.loops for c in loop)
    if k is Dimension:
        for item in drawn_by(doc, s):
            if type(item) is Text and _entity_touches_box(doc, Entity(0, item), box, px):
                return True
    if k is Insert:
        d = doc.blocks.get(s.block)
        if d is None:
            return box[0] <= s.p[0] <= box[2] and box[1] <= s.p[1] <= box[3]
    return any(curve_touches_box(c, box, px) for c in curves_of(doc, e))


def in_window(doc: Document, box: BBox, crossing: bool, px: float = 1.0, skip_locked: bool = True) -> list[int]:
    """A window selects what's wholly inside it. A crossing window also selects what it touches."""
    out: list[int] = []
    seen: set[int] = set()
    for key in doc.index.query(box):
        eid, _ = split_key(key)
        if eid in seen:
            continue
        seen.add(eid)
        e = doc.entities.get(eid)
        if e is None or doc.is_hidden(e.style) or (skip_locked and doc.is_locked(e.style)):
            continue
        bb = doc.shape_bbox(e.shape)
        if bb is None:
            continue
        if bbox_contains(box, bb) or (crossing and _entity_touches_box(doc, e, box, px)):
            out.append(eid)
    out.sort()
    return out
