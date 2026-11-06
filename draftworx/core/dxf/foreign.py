# SPDX-License-Identifier: GPL-3.0-or-later
"""What can be drawn of an object that can't be edited here. It's for showing and snapping to: the file's own text is what's kept."""
from __future__ import annotations

import math

from ..curves import Arc, EllipseArc, Line, Polyline, Spline
from ..vec import Affine, Vec, ccw_sweep
from .tags import clean, fnum, inum

_FLIP = Affine(-1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
#: These draw themselves through a block of their own, and a file always holds that block.
THROUGH_A_BLOCK = {"DIMENSION", "ARC_DIMENSION", "LARGE_RADIAL_DIMENSION", "ACAD_TABLE"}
#: Shown only in part, which has to be said.
IN_PART = {"HATCH": "their outlines", "MLINE": "their centre lines", "IMAGE": "their outlines", "WIPEOUT": "their outlines"}


class _Tags:
    """Reading tags in order, for the objects where order is what gives them their meaning."""

    def __init__(self, tags: list):
        self.tags = tags
        self.i = 0

    def code(self) -> int | None:
        return self.tags[self.i][0] if self.i < len(self.tags) else None

    def take(self, code: int, default: str = "0") -> str:
        if self.code() == code:
            self.i += 1
            return self.tags[self.i - 1][1]
        return default

    def point(self, code: int = 10) -> Vec:
        x = fnum(self.take(code))
        return Vec(x, fnum(self.take(code + 10)))

    def find(self, code: int) -> bool:
        while self.i < len(self.tags):
            if self.tags[self.i][0] == code:
                return True
            self.i += 1
        return False


def _arc_angles(start: float, end: float, ccw: bool) -> tuple[float, float]:
    """A file stores a hatch's clockwise arcs with their angles complemented. In radians, as a start and a counter-clockwise sweep."""
    if not ccw:
        start, end = 360.0 - end, 360.0 - start
    a0 = math.radians(start)
    return a0, ccw_sweep(a0, math.radians(end))


def _edge_spline(t: _Tags) -> Spline | None:
    degree = inum(t.take(94, "3"))
    t.take(73)
    t.take(74)
    knot_count, ctrl_count = inum(t.take(95)), inum(t.take(96))
    knots = tuple(fnum(t.take(40)) for _ in range(knot_count) if t.code() == 40)
    ctrl: list[Vec] = []
    weights: list[float] = []
    for _ in range(ctrl_count):
        if t.code() != 10:
            break
        ctrl.append(t.point(10))
        if t.code() == 42:
            weights.append(fnum(t.take(42), 1.0))
    if t.code() == 97:
        for _ in range(inum(t.take(97))):
            if t.code() != 11:
                break
            t.point(11)
    while t.code() in (12, 22, 13, 23):
        t.i += 1
    try:
        return Spline(degree, knots, tuple(ctrl), tuple(weights) if len(weights) == len(ctrl) else ())
    except ValueError:
        return Polyline(tuple(ctrl)) if ctrl else None


def _edge(t: _Tags):
    kind = inum(t.take(72))
    if kind == 1:
        return Line(t.point(10), t.point(11))
    if kind == 2:
        c = t.point(10)
        r = fnum(t.take(40))
        start, end = fnum(t.take(50)), fnum(t.take(51))
        a0, sweep = _arc_angles(start, end, inum(t.take(73, "1")) != 0)
        return Arc(c, r, a0, sweep) if r > 0.0 else None
    if kind == 3:
        c, major = t.point(10), t.point(11)
        ratio = fnum(t.take(40), 1.0)
        start, end = fnum(t.take(50)), fnum(t.take(51))
        a0, sweep = _arc_angles(start, end, inum(t.take(73, "1")) != 0)
        return EllipseArc(c, major, ratio, a0, sweep) if major != (0.0, 0.0) and 0.0 < ratio <= 1.0 else None
    if kind == 4:
        return _edge_spline(t)
    return None


def _chained(edges: list) -> list:
    """A path's edges turned, where a file has them backwards, so each starts where the one before it ends."""
    if len(edges) < 2:
        return edges
    box = None
    for c in edges:
        b = c.bbox()
        box = b if box is None else (min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3]))
    eps = max(box[2] - box[0], box[3] - box[1], 1e-12) * 1e-6

    def meets(p, q) -> bool:
        return abs(p[0] - q[0]) <= eps and abs(p[1] - q[1]) <= eps
    out = list(edges)
    if not meets(out[0].end, out[1].start) and not meets(out[0].end, out[1].end) and (meets(out[0].start, out[1].start) or meets(out[0].start, out[1].end)):
        out[0] = out[0].reversed()
    for i in range(1, len(out)):
        if not meets(out[i - 1].end, out[i].start) and meets(out[i - 1].end, out[i].end):
            out[i] = out[i].reversed()
    return out


def hatch_paths(tags: list, flip: int, shown: list | None = None) -> tuple[list[tuple], _Tags]:
    """A hatch's boundary paths, each a loop of curves end to end, in the drawing's coordinates; and where reading got to. `shown` is given
    the paths as a file draws them: a polyline path as one polyline."""
    t = _Tags(tags)
    if not t.find(91):
        return [], t
    loops: list[tuple] = []
    for _ in range(min(inum(t.take(91)), 100_000)):
        if t.code() != 92:
            break
        flags = inum(t.take(92))
        loop: list = []
        if flags & 2:
            has_bulges = inum(t.take(72)) != 0
            t.take(73)
            pts: list[Vec] = []
            bulges: list[float] = []
            for _ in range(inum(t.take(93))):
                if t.code() != 10:
                    break
                pts.append(t.point(10))
                bulges.append(fnum(t.take(42)) if has_bulges and t.code() == 42 else 0.0)
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts.pop()
                bulges.pop()
            if len(pts) > 1:
                whole = Polyline(tuple(pts), tuple(bulges), True)
                loop = list(whole.segments)
                if shown is not None:
                    shown.append(whole.transformed(_FLIP) if flip < 0 else whole)
        else:
            for _ in range(inum(t.take(93))):
                if t.code() != 72:
                    break
                edge = _edge(t)
                if edge is not None:
                    loop.extend(edge.segments if type(edge) is Polyline else (edge,))
                    if shown is not None:
                        shown.append(edge.transformed(_FLIP) if flip < 0 else edge)
            loop = _chained(loop)
        if t.code() == 97:
            for _ in range(inum(t.take(97))):
                t.take(330)
        if loop:
            loops.append(tuple(c.transformed(_FLIP) for c in loop) if flip < 0 else tuple(loop))
    return loops, t


def _hatch(tags: list, flip: int) -> tuple:
    shown: list = []
    hatch_paths(tags, flip, shown)
    return tuple(shown)


def _points(tags: list, code: int) -> list[Vec]:
    out = []
    x = None
    for c, v in tags:
        if c == code:
            x = fnum(v)
        elif c == code + 10 and x is not None:
            out.append(Vec(x, fnum(v)))
            x = None
    return out


def _outline(pts: list[Vec], closed: bool) -> tuple:
    kept = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
    if closed and len(kept) > 1 and kept[0] == kept[-1]:
        kept.pop()
    return (Polyline(tuple(kept), closed=closed),) if len(kept) > 1 else ()


def _rectangle(d: dict) -> tuple:
    """An image's frame: its corner, and how far its edges go for each of its pixels."""
    p = Vec(fnum(d.get(10, "0")), fnum(d.get(20, "0")))
    u = Vec(fnum(d.get(11, "0")), fnum(d.get(21, "0"))) * fnum(d.get(13, "0"))
    v = Vec(fnum(d.get(12, "0")), fnum(d.get(22, "0"))) * fnum(d.get(23, "0"))
    return _outline([p, p + u, p + u + v, p + v], True)


def block_of(kind: str, d: dict) -> tuple[str, Vec]:
    """The block that draws this, and where. Ordinarily at the origin: a dimension's block is written in the drawing's own coordinates."""
    if kind not in THROUGH_A_BLOCK:
        return "", Vec(0.0, 0.0)
    name = clean(d.get(2, "")).strip()
    if kind == "ACAD_TABLE":
        return name, Vec(fnum(d.get(10, "0")), fnum(d.get(20, "0")))
    return name, Vec(fnum(d.get(12, "0")), fnum(d.get(22, "0")))


def curves_of_foreign(kind: str, tags: list, d: dict, flip: int, vertices: list) -> tuple:
    """The curves to show for an object. Nothing is better than something wrong, so damage here leaves it undrawn."""
    try:
        if kind == "HATCH":
            return _hatch(tags, flip)
        if kind == "LEADER":
            return _outline(_points(tags, 10), False)
        if kind == "MLINE":
            return _outline(_points(tags, 11), bool(inum(d.get(71, "0")) & 2))
        if kind == "3DFACE":
            return _outline([Vec(fnum(d.get(10 + i, "0")), fnum(d.get(20 + i, "0"))) for i in range(4)], True)
        if kind == "IMAGE" or kind == "WIPEOUT":
            return _rectangle(d)
        if kind == "POLYLINE" and inum(d.get(70, "0")) & 8:
            return _outline([Vec(fnum(v.get(10, "0")), fnum(v.get(20, "0"))) for v in vertices], bool(inum(d.get(70, "0")) & 1))
    except (ValueError, ArithmeticError, IndexError):
        pass
    return ()
