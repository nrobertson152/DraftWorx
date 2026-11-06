# SPDX-License-Identifier: GPL-3.0-or-later
"""Drawing commands."""
from __future__ import annotations

import math

from . import command
from ..curves import Circle, EllipseArc, Line, Polyline
from ..geometry import arc_from_center, arc_through, circle_through
from ..session import ENTER, UNDO, GetDistance, GetPoint, GetText, Keyword
from ..shapes import Node, Text
from ..snap import Tracked
from ..vec import Vec, near


def _default_text_height(s) -> float:
    """A readable size on paper at the drawing's scale. A text style with a fixed height wins."""
    style = s.doc.text_styles.get(s.doc.current.text_style)
    if style is not None and style.height > 0.0:
        return style.height
    return 2.5 * s.doc.settings.annotation_scale


@command("LINE", "L", group="Draw", help="Draw straight lines, one after another", icon="line")
def line(s):
    start = yield GetPoint("Line: where does it start?")
    points = [start]
    made: list[int] = []
    while True:
        a = points[-1]
        options = ("Close", "Undo") if len(points) > 2 else ("Undo",) if len(points) > 1 else ()
        tracked = (Tracked(start, "Start of the line"),) if len(points) > 1 else ()
        ans = yield GetPoint("Line: where to next? Type a length, or press Enter to finish.", options, undoable=True, base=a,
                             preview=lambda p, a=a: [Line(a, p)], tracked=tracked, enter=True)
        if ans is ENTER:
            return
        if ans is UNDO or ans == Keyword("Undo"):
            if not made:
                return      # nothing drawn yet, so stepping back is the same as stopping
            with s.doc.transaction("Undo segment") as t:
                t.remove(made.pop())
            points.pop()
            continue
        if ans == Keyword("Close"):
            ans = start
        if near(ans, a):
            s.message = "That's the same point. A line needs two different ones."
            continue
        with s.doc.transaction("Line") as t:
            made.append(t.add(Line(a, ans)))
        if ans is start:
            return
        points.append(ans)


def _tangent_arc_bulge(direction: Vec, a: Vec, b: Vec) -> float:
    """The bulge of the arc from a to b leaving a in this direction."""
    chord = b - a
    if chord.length2() == 0.0 or direction.length2() == 0.0:
        return 0.0
    half = math.atan2(direction.cross(chord), direction.dot(chord))
    return math.tan(half / 2.0)


@command("PLINE", "PL", "POLYLINE", group="Draw", help="Draw one connected shape of straight and arc segments", icon="polyline")
def pline(s):
    start = yield GetPoint("Polyline: where does it start?")
    pts = [start]
    bulges: list[float] = []
    arc_mode = False

    def shape(closed=False):
        return Polyline(tuple(pts), tuple(bulges) + (0.0,), closed)

    def heading() -> Vec:
        """Which way the polyline is going at its end, so the next arc can leave smoothly."""
        if len(pts) < 2:
            return Vec(1.0, 0.0)
        seg = shape().segments[-1]
        return seg.tangent_at(1.0).unit()

    while True:
        a = pts[-1]
        options = ("Close", "Undo", "Line" if arc_mode else "Arc") if len(pts) > 2 else ("Undo", "Line" if arc_mode else "Arc") if len(pts) > 1 else ("Line" if arc_mode else "Arc",)

        def preview(p, a=a, arc=arc_mode):
            b = _tangent_arc_bulge(heading(), a, p) if arc else 0.0
            return [Polyline(tuple(pts) + (p,), tuple(bulges) + (b, 0.0))]
        extra = (shape(),) if len(pts) > 1 else ()
        word = "arc" if arc_mode else "line"
        ans = yield GetPoint(f"Polyline ({word}): where to next? Press Enter to finish.", options, undoable=True, base=a, preview=preview,
                             tracked=(Tracked(start, "Start of the polyline"),) if len(pts) > 1 else (), extra=extra, enter=True)
        if ans is ENTER:
            break
        if ans is UNDO or ans == Keyword("Undo"):
            if len(pts) == 1:
                return
            pts.pop()
            bulges.pop()
            continue
        if ans == Keyword("Arc"):
            arc_mode = True
            continue
        if ans == Keyword("Line"):
            arc_mode = False
            continue
        if ans == Keyword("Close"):
            if arc_mode:
                bulges.append(_tangent_arc_bulge(heading(), a, start))
            else:
                bulges.append(0.0)
            with s.doc.transaction("Polyline") as t:
                t.add(Polyline(tuple(pts), tuple(bulges), True))
            return
        if near(ans, a):
            s.message = "That's the same point."
            continue
        bulges.append(_tangent_arc_bulge(heading(), a, ans) if arc_mode else 0.0)
        pts.append(ans)
    if len(pts) >= 2:
        with s.doc.transaction("Polyline") as t:
            t.add(Polyline(tuple(pts), tuple(bulges) + (0.0,)))


def _rectangle(a: Vec, b: Vec) -> Polyline:
    return Polyline((a, Vec(b[0], a[1]), b, Vec(a[0], b[1])), closed=True)


@command("RECTANGLE", "REC", "RECT", group="Draw", help="Draw a rectangle from two corners. Type width,height for the second", icon="rectangle")
def rectangle(s):
    a = yield GetPoint("Rectangle: first corner?")
    b = yield GetPoint("Rectangle: opposite corner? Type width,height.", base=a, preview=lambda p: [_rectangle(a, p)])
    if a[0] == b[0] or a[1] == b[1]:
        s.message = "A rectangle needs some width and some height."
        return
    with s.doc.transaction("Rectangle") as t:
        t.add(_rectangle(a, b))


@command("CIRCLE", "C", group="Draw", help="Draw a circle from its centre and radius, or through two or three points", icon="circle")
def circle(s):
    ans = yield GetPoint("Circle: where's the centre?", ("2 points", "3 points"))
    if ans == Keyword("2 points"):
        a = yield GetPoint("Circle: one end of the diameter?")
        b = yield GetPoint("Circle: the other end?", base=a, preview=lambda p: [Circle(a.midpoint(p), a.distance(p) / 2.0)] if not near(a, p) else [])
        if near(a, b):
            s.message = "A circle needs two different points."
            return
        with s.doc.transaction("Circle") as t:
            t.add(Circle(a.midpoint(b), a.distance(b) / 2.0))
        return
    if ans == Keyword("3 points"):
        a = yield GetPoint("Circle: first point on it?")
        b = yield GetPoint("Circle: second point?", base=a)

        def through(p):
            c = circle_through(a, b, p)
            return [c] if c is not None else []
        c = yield GetPoint("Circle: third point?", base=b, preview=through)
        made = circle_through(a, b, c)
        if made is None:
            s.message = "Those three points are in a line, so no circle passes through them."
            return
        with s.doc.transaction("Circle") as t:
            t.add(made)
        return
    center = ans
    prompt = "Circle: what's the radius?"
    option = "Diameter"
    factor = 1.0
    while True:
        def show(p):
            r = center.distance(p)
            return [Circle(center, r)] if r > 0.0 else []
        got = yield GetDistance(prompt, (option,), base=center, preview=show, typed_factor=factor)
        if isinstance(got, Keyword):
            if got.name == "Diameter":
                prompt, option, factor = "Circle: what's the diameter?", "Radius", 0.5
            else:
                prompt, option, factor = "Circle: what's the radius?", "Diameter", 1.0
            continue
        break
    if got <= 0.0:
        s.message = "A circle needs a radius above zero."
        return
    with s.doc.transaction("Circle") as t:
        t.add(Circle(center, got))


@command("ARC", "A", group="Draw", help="Draw an arc through three points, or from its centre", icon="arc")
def arc(s):
    ans = yield GetPoint("Arc: where does it start?", ("Center",))
    if ans == Keyword("Center"):
        c = yield GetPoint("Arc: where's the centre?")
        a = yield GetPoint("Arc: where does it start? (It runs counter-clockwise.)", base=c, preview=lambda p: [Line(c, p)])

        def show(p):
            made = arc_from_center(c, a, p)
            return [made] if made is not None else []
        b = yield GetPoint("Arc: where does it end?", ("Clockwise",), base=c, preview=show)
        if b == Keyword("Clockwise"):
            b = yield GetPoint("Arc: where does it end?", base=c, preview=lambda p: [m.reversed()] if (m := arc_from_center(c, p, a)) else [])
            made = arc_from_center(c, b, a)
        else:
            made = arc_from_center(c, a, b)
        if made is None:
            s.message = "The start can't be at the centre."
            return
        with s.doc.transaction("Arc") as t:
            t.add(made)
        return
    a = ans
    b = yield GetPoint("Arc: a point on the way?", base=a, preview=lambda p: [Line(a, p)])

    def show3(p):
        made = arc_through(a, b, p)
        return [made] if made is not None else [Line(a, p)]
    c = yield GetPoint("Arc: where does it end?", base=b, preview=show3)
    made = arc_through(a, b, c)
    if made is None:
        s.message = "Those three points are in a line, so no arc passes through them."
        return
    with s.doc.transaction("Arc") as t:
        t.add(made.ccw())


@command("ELLIPSE", "EL", group="Draw", help="Draw an ellipse from its centre and two axes", icon="ellipse")
def ellipse(s):
    c = yield GetPoint("Ellipse: where's the centre?")
    a = yield GetPoint("Ellipse: where does one axis end?", base=c, preview=lambda p: [Line(c, p)])
    major = a - c
    if major.length() == 0.0:
        s.message = "An axis needs some length."
        return

    def make(other: float):
        other = abs(other)
        m = major.length()
        if other == 0.0:
            return None
        if other <= m:
            return EllipseArc(c, major, other / m)
        # the second axis is the longer one, and an ellipse stores the longer one as its major axis
        return EllipseArc(c, major.perp().unit() * other, m / other)

    def show(p):
        made = make(abs((p - c).cross(major.unit())))
        return [made] if made is not None else []
    u = major.unit()
    got = yield GetDistance("Ellipse: how long is the other half-axis?", base=c, preview=show, measure=lambda p: abs((p - c).cross(u)))
    made = make(got)
    if made is None:
        s.message = "An axis needs some length."
        return
    with s.doc.transaction("Ellipse") as t:
        t.add(made)


@command("POINT", "PO", group="Draw", help="Place point objects", icon="point")
def point(s):
    while True:
        p = yield GetPoint("Point: where? Press Enter to finish.", enter=True)
        if p is ENTER:
            return
        with s.doc.transaction("Point") as t:
            t.add(Node(p))


@command("TEXT", "T", "DT", group="Annotate", help="Place a line of text", icon="text")
def text(s):
    p = yield GetPoint("Text: where does it start?")
    h = _default_text_height(s)
    style = s.doc.current.text_style

    def show(t):
        return [Text(p, h, t, 0.0, style)] if t else []
    t = yield GetText("Type the text and press Enter.", preview=show)
    if t is ENTER or not t:
        return
    with s.doc.transaction("Text") as tx:
        tx.add(Text(p, h, t, 0.0, style))
