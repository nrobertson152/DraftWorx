# SPDX-License-Identifier: GPL-3.0-or-later
"""The handles on a selected object, and what dragging one does."""
from __future__ import annotations

import math
from dataclasses import dataclass

from dataclasses import replace

from . import dim as dims
from .curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from .geometry import arc_through
from .shapes import AttDef, Dimension, Insert, MText, Node, Solid, Text
from .vec import TAU, Affine, Vec


@dataclass(frozen=True, slots=True)
class Grip:
    point: Vec
    role: str
    index: int = 0
    #: What it's measured from while dragging: the other end of a line, the centre of a circle.
    base: Vec | None = None


def grips_of(s, style=None) -> list[Grip]:
    """`style` is a dimension's style, for where its text is."""
    k = type(s)
    if k is Dimension:
        out = [Grip(p, "dimpoint", i) for i, p in enumerate(s.pts)] + [Grip(s.at, "dimline", 0)]
        box = dims.text_box(s, style) if style is not None and not s.block else None
        if s.text_at is not None:
            out.append(Grip(s.text_at, "dimtext", 0))
        elif box is not None:
            o, a, u = box
            out.append(Grip(o + a * 0.5 + u * 0.5, "dimtext", 0))
        return out
    if k is Line:
        return [Grip(s.a, "end", 0, s.b), Grip(s.b, "end", 1, s.a), Grip(s.a.midpoint(s.b), "move", 0, s.a.midpoint(s.b))]
    if k is Arc:
        return [Grip(s.start, "arc", 0, s.end), Grip(s.end, "arc", 1, s.start), Grip(s.point_at(0.5), "arc", 2, s.c), Grip(s.c, "move", 0, s.c)]
    if k is Circle:
        (x, y), r = s.c, s.r
        return [Grip(s.c, "move", 0, s.c), Grip(Vec(x + r, y), "radius", 0, s.c), Grip(Vec(x, y + r), "radius", 1, s.c),
                Grip(Vec(x - r, y), "radius", 2, s.c), Grip(Vec(x, y - r), "radius", 3, s.c)]
    if k is EllipseArc:
        out = [Grip(s.c, "move", 0, s.c), Grip(s.c + s.major, "major", 0, s.c), Grip(s.c - s.major, "major", 1, s.c),
               Grip(s.c + s.minor, "minor", 0, s.c), Grip(s.c - s.minor, "minor", 1, s.c)]
        return out
    if k is Polyline:
        out = [Grip(p, "vertex", i, s.pts[i - 1] if i or s.closed else (s.pts[1] if len(s.pts) > 1 else None)) for i, p in enumerate(s.pts)]
        for i, seg in enumerate(s.segments):
            out.append(Grip(seg.point_at(0.5), "segment", i, None))
        return out
    if k is Spline:
        return [Grip(p, "ctrl", i) for i, p in enumerate(s.ctrl)]
    if k is Node or k is Text or k is MText or k is Insert:
        return [Grip(s.p, "move")]
    if k is AttDef:
        return [Grip(s.attrib.text.p, "move")]
    if k is Solid:
        return [Grip(p, "corner", i) for i, p in enumerate(s.pts)]
    return []


def moved(s, g: Grip, p: Vec):
    """This shape with this grip at p. Returns the shape unchanged if p would make it meaningless."""
    k = type(s)
    role = g.role
    if role == "move":
        d = p - g.point
        return s.transformed(Affine.translation(d[0], d[1]))
    if k is Dimension:
        if role == "dimline":
            if s.measure in ("radius", "diameter") and p != s.pts[0]:
                # its point on the curve goes round with it, as that's where it's drawn
                c = s.pts[0]
                return replace(s, at=p, pts=(c, c + (p - c).unit() * c.distance(s.pts[1])), block="")
            return replace(s, at=p, block="")
        if role == "dimtext":
            return replace(s, text_at=p, block="")
        pts = list(s.pts)
        pts[g.index] = p
        try:
            return replace(s, pts=tuple(pts), block="")     # it no longer sits on what it was attached to there, so it lets go of it
        except ValueError:
            return s
    if k is Line:
        return Line(p, s.b) if g.index == 0 else Line(s.a, p)
    if k is Arc:
        a, b, c = s.start, s.point_at(0.5), s.end
        if g.index == 0:
            a = p
        elif g.index == 1:
            c = p
        else:
            b = p
        new = arc_through(a, b, c)
        if new is None:
            return s
        # keep the direction it ran
        return new if (new.sweep > 0.0) == (s.sweep > 0.0) else new.reversed()
    if k is Circle:
        r = s.c.distance(p)
        return Circle(s.c, r) if r > 0.0 else s
    if k is EllipseArc:
        if role == "major":
            d = p - s.c if g.index == 0 else s.c - p
            if d.length() == 0.0:
                return s
            ratio = min(1.0, s.minor.length() / d.length())
            return EllipseArc(s.c, d, ratio, s.start_param, s.sweep)
        n = s.minor.unit()
        length = abs((p - s.c).dot(n))
        major = s.major.length()
        if length == 0.0:
            return s
        if length <= major:
            return EllipseArc(s.c, s.major, length / major, s.start_param, s.sweep)
        # the minor axis has become the longer one, so the axes swap and the parameter shifts to keep the points put
        return EllipseArc(s.c, n * length, major / length, s.start_param - math.pi / 2.0, s.sweep) if abs(s.sweep) >= TAU else s
    if k is Polyline:
        return _moved_polyline(s, g, p)
    if k is Spline:
        ctrl = list(s.ctrl)
        ctrl[g.index] = p
        return Spline(s.degree, s.knots, tuple(ctrl), s.weights, (), s.periodic)
    if k is Solid:
        pts = list(s.pts)
        pts[g.index] = p
        return Solid(tuple(pts))
    return s


def _moved_polyline(s: Polyline, g: Grip, p: Vec) -> Polyline:
    pts = list(s.pts)
    bulges = list(s.bulges) if s.bulges else [0.0] * len(pts)
    widths = list(s.widths)
    if g.role == "vertex":
        pts[g.index] = p
        return Polyline(tuple(pts), tuple(bulges), s.closed, s.width, tuple(widths))
    i = g.index
    a, b = pts[i], pts[(i + 1) % len(pts)]
    if isinstance(s.segments[i], Line):
        # dragging the middle of a straight segment puts a corner there
        pts.insert(i + 1, p)
        bulges.insert(i + 1, 0.0)
        if widths:
            widths.insert(i + 1, widths[i])
        return Polyline(tuple(pts), tuple(bulges), s.closed, s.width, tuple(widths))
    new = arc_through(a, p, b)
    if new is None:
        bulges[i] = 0.0
    else:
        bulges[i] = math.tan(new.sweep / 4.0)
    return Polyline(tuple(pts), tuple(bulges), s.closed, s.width, tuple(widths))


def grip_at(grips: list[Grip], p, px: float, reach: float = 7.0) -> Grip | None:
    """The grip under a point. The closest, and the earliest if several are at the same place."""
    best = None
    best_d = reach * px
    for g in grips:
        d = math.hypot(g.point[0] - p[0], g.point[1] - p[1])
        if d < best_d or (best is None and d <= best_d):
            best, best_d = g, d
    return best
