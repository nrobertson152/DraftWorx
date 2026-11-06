# SPDX-License-Identifier: GPL-3.0-or-later
"""Annotation commands: the one Dimension tool.

It reads what you point at. A line measures its length, a circle its diameter, an arc its radius, a second line the angle between them, and two
points the distance between them. Where you put it decides the rest: beside two points measures across or up, along them measures their true
length. What it measures from stays attached, so the dimension follows when that changes.
"""
from __future__ import annotations

import math

from . import command
from .. import dim as dims
from ..curves import Arc, Circle, Line, Polyline
from ..select import pick
from ..session import ENTER, GetDistance, GetPoint, GetText, Keyword, Preview
from ..shapes import Dimension
from ..snap import REAL
from ..vec import Vec, near

_ORIENTATIONS = ("Horizontal", "Vertical", "Aligned")


def _segment_at(s: Polyline, p: Vec) -> int:
    """Which piece of a polyline a point is on."""
    best, found = math.inf, 0
    for i, g in enumerate(s.segments):
        d = g.project(p)[1].distance(p)
        if d < best:
            best, found = d, i
    return found


def straight_at(doc, eid: int, p: Vec) -> tuple[Vec, Vec, tuple] | None:
    """Something straight under the cursor, as its two ends and what each is attached to: a line, or a straight piece of a polyline."""
    e = doc.entities.get(eid)
    if e is None:
        return None
    s = e.shape
    if type(s) is Line:
        return s.a, s.b, ((eid, "start"), (eid, "end"))
    if type(s) is Polyline and s.segments:
        i = _segment_at(s, p)
        if type(s.segments[i]) is Line:
            j = (i + 1) % len(s.pts)
            return s.pts[i], s.pts[j], ((eid, f"vertex:{i}"), (eid, f"vertex:{j}"))
    return None


def round_at(doc, eid: int, p: Vec) -> tuple[Vec, float, bool, tuple] | None:
    """A circle or arc under the cursor: its centre, its radius, whether it's whole, and what a dimension of it is attached to."""
    e = doc.entities.get(eid)
    if e is None:
        return None
    s = e.shape
    if type(s) is Circle:
        return s.c, s.r, True, ((eid, "center"), (eid, "on"))
    if type(s) is Arc:
        return s.c, s.r, False, ((eid, "center"), (eid, "on"))
    if type(s) is Polyline and s.segments:
        g = s.segments[_segment_at(s, p)]
        if type(g) is Arc:
            return g.c, g.r, False, ()       # a polyline's arc has no centre of its own to stay attached to
    return None


def orientation(a: Vec, b: Vec, p: Vec, locked: str | None = None) -> tuple[str, float]:
    """Which way to measure between two points, from where the dimension line goes: beside them measures up, above or below them measures across,
    and anywhere else measures along them. `locked` is a choice that overrides that."""
    if locked == "Horizontal":
        return "linear", 0.0
    if locked == "Vertical":
        return "linear", math.pi / 2.0
    if locked == "Aligned":
        return "aligned", 0.0
    x0, x1 = sorted((a[0], b[0]))
    y0, y1 = sorted((a[1], b[1]))
    within_x = x0 <= p[0] <= x1
    within_y = y0 <= p[1] <= y1
    if within_x and not within_y:
        return "linear", 0.0
    if within_y and not within_x:
        return "linear", math.pi / 2.0
    return "aligned", 0.0


def _between(a: Vec, b: Vec, p: Vec, refs: tuple, style: str, locked: str | None, offset: float | None = None) -> Dimension:
    """A dimension between two points with its line through p, or `offset` from them on p's side."""
    measure, angle = orientation(a, b, p, locked)
    if measure == "aligned" and near(a, b):
        measure, angle = "linear", 0.0
    u = (b - a).unit() if measure == "aligned" else Vec(math.cos(angle), math.sin(angle))
    n = u.perp()
    at = p
    if offset is not None:
        side = 1.0 if (p - a).dot(n) >= 0.0 else -1.0
        at = a + n * (offset * side)
    return Dimension(measure, (a, b), at, angle, style=style, scale=1.0, refs=refs)


def _angle_between(first: tuple, second: tuple, p: Vec, style: str) -> Dimension | None:
    a0, a1, ra = first
    b0, b1, rb = second
    d = Dimension("angular", (a0, a1, b0, b1), p, style=style, refs=ra + rb if ra and rb else ())
    return d if dims.angle_of(d) is not None else None


@command("DIMENSION", "DIM", "DLI", "DAL", "DRA", "DDI", "DAN", group="Annotate",
         help="Measure what you point at: a line, circle, arc, the angle between two lines, or between two points", icon="dimension")
def dimension(s):
    def style_name() -> str:
        return s.doc.current.dim_style if s.doc.dim_styles.get(s.doc.current.dim_style) is not None else "Standard"

    def scale() -> float:
        st = s.doc.dim_styles.get(style_name())
        own = st.scale if st is not None and st.scale > 0.0 else 1.0
        return s.doc.settings.annotation_scale / own

    def scaled(d: Dimension | None) -> Dimension | None:
        return None if d is None else Dimension(d.measure, d.pts, d.at, d.angle, d.text, d.style, scale(), d.text_at, d.refs)

    def under(p: Vec) -> list[int]:
        return pick(s.doc, p, s.px)

    def hover(p):
        c = s.snap.candidate if s.snap is not None else None
        if c is not None and c.kind in REAL:
            return []       # on a point, which is where a dimension between two points starts
        hits = under(s.cursor)
        return Preview(hover=hits[0] if hits else None)

    while True:
        first = yield GetPoint("Dimension: point at a line, circle or arc, or click the first of two points. Enter to finish.", preview=hover, enter=True)
        if first is ENTER:
            return
        c = s.snap.candidate if s.snap is not None else None
        on_a_point = c is not None and c.kind in REAL
        hits = [] if on_a_point else under(s.cursor)
        if hits:
            straight = straight_at(s.doc, hits[0], s.cursor)
            rounded = round_at(s.doc, hits[0], s.cursor) if straight is None else None
            if straight is not None:
                made = yield from _place_between(s, straight[0], straight[1], straight[2], hits[0], style_name, scaled)
            elif rounded is not None:
                made = yield from _place_round(s, rounded, style_name, scaled)
            else:
                s.message = "That can't be measured by pointing at it. Click two points instead."
                continue
        else:
            ref_a = _ref_for(s, first, c)
            second = yield GetPoint("Dimension: the second point?", base=first, preview=lambda p: [Line(first, p)])
            c2 = s.snap.candidate if s.snap is not None else None
            if near(first, second):
                s.message = "That's the same point. A dimension needs two different ones."
                continue
            ref_b = _ref_for(s, second, c2)
            refs = (ref_a, ref_b) if ref_a is not None or ref_b is not None else ()
            made = yield from _place_between(s, first, second, refs, None, style_name, scaled)
        if made is not None:
            with s.doc.transaction("Dimension") as t:
                t.add(made)


def _ref_for(s, p: Vec, c) -> tuple | None:
    """What a point a dimension measures from is attached to: the named point of the one object it was snapped to, if it's exactly that."""
    if c is None or c.kind not in REAL or len(c.sources) != 1:
        return None
    e = s.doc.entities.get(c.sources[0])
    if e is None:
        return None
    which = dims.which_point(e.shape, p)
    return (e.id, which) if which is not None else None


def _place_between(s, a: Vec, b: Vec, refs: tuple, picked: int | None, style_name, scaled):
    """Where the dimension line goes. Pointing at another line instead measures the angle between them."""
    locked: list[str | None] = [None]
    words = [""]

    def other_line(p: Vec):
        """Another straight thing under the pointer, which may be another piece of the same polyline: pointing at it measures the angle."""
        if picked is None:
            return None
        for i in pick(s.doc, s.cursor, s.px):
            found = straight_at(s.doc, i, s.cursor)
            if found is not None and {found[0], found[1]} != {a, b} and abs((found[1] - found[0]).unit().cross((b - a).unit())) > 1e-9:
                return found
        return None

    def make(p: Vec, offset: float | None = None) -> Dimension:
        d = _between(a, b, p, refs, style_name(), locked[0], offset)
        return Dimension(d.measure, d.pts, d.at, d.angle, words[0], d.style, d.scale, None, d.refs)

    def preview(p):
        other = other_line(p)
        if other is not None:
            d = _angle_between((a, b, refs), other, p, style_name())
            return [scaled(d)] if d is not None else []
        return [scaled(make(p))]

    def measure(p) -> float:
        d = make(p)
        u = (b - a).unit() if d.measure == "aligned" else Vec(math.cos(d.angle), math.sin(d.angle))
        return abs((p - a).dot(u.perp()))

    while True:
        ans = yield GetDistance("Dimension: where does it go? Type how far from what it measures, H, V or A to keep it across, up or along, or T for its text.",
                                ("Horizontal", "Vertical", "Aligned", "Text"), base=a.midpoint(b), preview=preview, measure=measure, from_base=False)
        if ans in tuple(Keyword(k) for k in _ORIENTATIONS):
            locked[0] = ans.name
            continue
        if ans == Keyword("Text"):
            typed = yield GetText("Dimension text: type what it says. <> stands for the measurement. Enter on its own keeps the measurement.", default="")
            words[0] = "" if typed is ENTER or typed == "<>" else typed
            continue
        if isinstance(ans, (int, float)):
            other = other_line(s.cursor)
            if other is not None:
                made = yield from _place_angle(s, (a, b, refs), other, style_name, scaled)
                return made
            # pointed or typed, it's how far the dimension line is from what it measures, on the side the pointer is
            return scaled(make(s.snap.point if s.snap is not None else s.cursor, ans))
        return None


def _place_angle(s, first: tuple, second: tuple, style_name, scaled):
    def preview(p):
        d = _angle_between(first, second, p, style_name())
        return [scaled(d)] if d is not None else []
    p = yield GetPoint("Dimension: which of the angles, and where does its arc go?", preview=preview)
    d = _angle_between(first, second, p, style_name())
    if d is None:
        s.message = "Those two are parallel, so there's no angle between them."
    return scaled(d)


def _place_round(s, found: tuple, style_name, scaled):
    c, r, whole, refs = found
    kind = ["diameter" if whole else "radius"]

    def make(p: Vec) -> Dimension:
        direction = (p - c).unit() if not near(p, c) else Vec(1.0, 0.0)
        return Dimension(kind[0], (c, c + direction * r), p, style=style_name(), refs=refs)

    while True:
        ans = yield GetPoint("Dimension: where does it go? R or D for its radius or diameter.", ("Radius", "Diameter"), preview=lambda p: [scaled(make(p))])
        if ans == Keyword("Radius"):
            kind[0] = "radius"
            continue
        if ans == Keyword("Diameter"):
            kind[0] = "diameter"
            continue
        return scaled(make(ans))
