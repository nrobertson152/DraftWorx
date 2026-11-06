# SPDX-License-Identifier: GPL-3.0-or-later
"""What the panel says about a selection: live measurements, and the numbers you can change by typing."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, replace
from typing import Callable

from . import dim as dims
from .curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from .geometry import arc_from_center
from .model import Document, Settings
from .shapes import AttDef, Dimension, Foreign, Hatch, Insert, MText, Node, Solid, Text
from .units import format_angle, format_length, parse_angle, parse_length, parse_number
from .vec import TAU, Vec, polar

NAMES = {Line: "Line", Arc: "Arc", Circle: "Circle", EllipseArc: "Ellipse", Spline: "Spline", Polyline: "Polyline", Node: "Point", Solid: "Solid",
         Text: "Text", MText: "Paragraph text", AttDef: "Attribute", Insert: "Block", Hatch: "Hatch"}


def name_of(shape) -> str:
    if type(shape) is Dimension:
        return "Dimension"
    if type(shape) is Foreign:
        return shape.dxf_type.replace("ACAD_", "").replace("_", " ").capitalize()
    return NAMES.get(type(shape), type(shape).__name__)


@dataclass(frozen=True, slots=True)
class Row:
    label: str
    value: str
    #: Given what was typed, the shape it makes. None if this number can't be changed.
    edit: Callable[[str], object] | None = None


def _point_rows(prefix: str, p: Vec, st: Settings, make: Callable[[Vec], object]) -> list[Row]:
    return [Row(f"{prefix} X", _len(p[0], st), lambda t: make(Vec(parse_length(t, st.units), p[1]))),
            Row(f"{prefix} Y", _len(p[1], st), lambda t: make(Vec(p[0], parse_length(t, st.units))))]


def _len(v: float, st: Settings) -> str:
    return format_length(v, st.linear_format, st.linear_decimals, st.units)


def _ang(v: float, st: Settings) -> str:
    return format_angle(v, st.angle_decimals)


def _positive(v: float, what: str) -> float:
    if v <= 0.0:
        raise ValueError(f"{what} has to be more than zero")
    return v


def _tag(t: str) -> str:
    tag = t.strip().upper()
    if not tag or " " in tag:
        raise ValueError("a tag is one word, without spaces")
    return tag


def rows_for(s, st: Settings) -> list[Row]:
    """The numbers for one shape."""
    k = type(s)
    u = st.units
    if k is Line:
        d = s.b - s.a
        length = d.length()
        ang = d.angle() if length > 0.0 else 0.0

        def set_length(t):
            new = _positive(parse_length(t, u), "A length")
            return Line(s.a, polar(s.a, new, ang))

        def set_angle(t):
            return Line(s.a, polar(s.a, length, parse_angle(t)))
        return [Row("Length", _len(length, st), set_length), Row("Angle", _ang(ang, st), set_angle),
                *_point_rows("Start", s.a, st, lambda p: Line(p, s.b)), *_point_rows("End", s.b, st, lambda p: Line(s.a, p))]
    if k is Circle:
        def set_r(t):
            return Circle(s.c, _positive(parse_length(t, u), "A radius"))

        def set_d(t):
            return Circle(s.c, _positive(parse_length(t, u), "A diameter") / 2.0)

        def set_area(t):
            return Circle(s.c, math.sqrt(_positive(parse_number(t), "An area") / math.pi))

        def set_c(t):
            return Circle(s.c, _positive(parse_length(t, u), "A circumference") / TAU)
        return [Row("Radius", _len(s.r, st), set_r), Row("Diameter", _len(s.r * 2.0, st), set_d), Row("Circumference", _len(TAU * s.r, st), set_c),
                Row("Area", _len(math.pi * s.r * s.r, st), set_area), *_point_rows("Center", s.c, st, lambda p: Circle(p, s.r))]
    if k is Arc:
        a = s.ccw()

        def set_r(t):
            return Arc(a.c, _positive(parse_length(t, u), "A radius"), a.start_angle, a.sweep)

        def set_start(t):
            return arc_from_center(a.c, polar(a.c, a.r, parse_angle(t)), a.end)

        def set_end(t):
            return arc_from_center(a.c, a.start, polar(a.c, a.r, parse_angle(t)))
        return [Row("Radius", _len(a.r, st), set_r), Row("Start angle", _ang(a.start_angle, st), set_start),
                Row("End angle", _ang(a.end_angle, st), set_end), Row("Sweep", _ang(a.sweep, st)), Row("Length", _len(a.length(), st)),
                *_point_rows("Center", a.c, st, lambda p: Arc(p, a.r, a.start_angle, a.sweep))]
    if k is EllipseArc:
        return [Row("Major radius", _len(s.major.length(), st)), Row("Minor radius", _len(s.major.length() * s.ratio, st)),
                Row("Length", _len(s.length(), st)), *_point_rows("Center", s.c, st, lambda p: replace(s, c=p))]
    if k is Polyline:
        rows = [Row("Vertices", str(len(s.pts))), Row("Length", _len(s.length(), st))]
        if s.closed:
            rows.append(Row("Area", _len(abs(s.signed_area()), st)))
        return rows
    if k is Spline:
        return [Row("Degree", str(s.degree)), Row("Control points", str(len(s.ctrl))), Row("Length", _len(s.length(), st))]
    if k is Node:
        return _point_rows("Position", s.p, st, lambda p: Node(p))
    if k is Text:
        return [Row("Text", s.text, lambda t: replace(s, text=t)), Row("Height", _len(s.height, st), lambda t: replace(s, height=_positive(parse_length(t, u), "A height"))),
                Row("Rotation", _ang(s.rotation, st), lambda t: replace(s, rotation=parse_angle(t))), *_point_rows("Position", s.p, st, lambda p: replace(s, p=p))]
    if k is MText:
        return [Row("Text", s.text, lambda t: replace(s, text=t)), Row("Height", _len(s.height, st), lambda t: replace(s, height=_positive(parse_length(t, u), "A height"))),
                Row("Width", _len(s.width, st), lambda t: replace(s, width=parse_length(t, u))),
                Row("Rotation", _ang(s.rotation, st), lambda t: replace(s, rotation=parse_angle(t))), *_point_rows("Position", s.p, st, lambda p: replace(s, p=p))]
    if k is Insert:
        def set_value(i: int):
            def change(t):
                a = s.attribs[i]
                return replace(s, attribs=s.attribs[:i] + (replace(a, text=replace(a.text, text=t)),) + s.attribs[i + 1:])
            return change
        fields = [Row(a.tag, a.text.text, set_value(i) if not a.constant else None) for i, a in enumerate(s.attribs)]
        return [Row("Block", s.block), *fields, Row("Scale X", f"{s.sx:g}", lambda t: replace(s, sx=parse_number(t))),
                Row("Scale Y", f"{s.sy:g}", lambda t: replace(s, sy=parse_number(t))),
                Row("Rotation", _ang(s.rotation, st), lambda t: replace(s, rotation=parse_angle(t))), *_point_rows("Position", s.p, st, lambda p: replace(s, p=p))]
    if k is AttDef:
        a = s.attrib
        return [Row("Tag", a.tag, lambda t: replace(s, attrib=replace(a, tag=_tag(t)))),
                Row("Asks", a.prompt, lambda t: replace(s, attrib=replace(a, prompt=t))),
                Row("Value until filled in", a.text.text, lambda t: replace(s, attrib=replace(a, text=replace(a.text, text=t)))),
                Row("Height", _len(a.text.height, st), lambda t: replace(s, attrib=replace(a, text=replace(a.text, height=_positive(parse_length(t, u), "A height")))))]
    if k is Dimension:
        value = dims.measurement(s)
        measured = _ang(value, st) if s.measure == "angular" else _len(value, st)
        return [Row("Measures", f"{measured} ({s.measure})"),
                Row("Text", s.text or "<>", lambda t: replace(s, text="" if t.strip() == "<>" else t, block="")),
                Row("Style", s.style), Row("Attached", "Yes" if any(r is not None for r in s.refs) else "No")]
    if k is Hatch:
        from . import hatch as hatches

        def set_pattern(t):
            found = hatches.known(t)
            if found is None:
                raise ValueError(f"there's no pattern called {t.strip()}. There's {', '.join(hatches.PATTERNS)}")
            return replace(s, pattern=found, lines=())
        rows = [Row("Pattern", s.pattern, set_pattern)]
        if not s.solid:
            rows += [Row("Scale", f"{s.scale:g}", lambda t: replace(s, scale=_positive(parse_number(t), "A scale"))),
                     Row("Angle", _ang(s.angle, st), lambda t: replace(s, angle=parse_angle(t)))]
        rows += [Row("Area", _len(hatches.area(s), st)), Row("Holes", str(max(0, len(s.loops) - 1))),
                 Row("Follows its boundary", "Yes" if s.refs else "No")]
        return rows
    if k is Foreign:
        return [Row("From a file", "Its layer and colour can change. Its shape can't, here.")]
    return []


@dataclass(frozen=True, slots=True)
class Summary:
    count: int
    kinds: dict
    length: float
    area: float
    box: tuple | None


def summarise(doc: Document, ids) -> Summary:
    """Totals for what's selected, so measuring needs no command."""
    kinds: Counter = Counter()
    length = area = 0.0
    box = None
    from .vec import bbox_union
    for i in ids:
        e = doc.entities.get(i)
        if e is None:
            continue
        s = e.shape
        kinds[name_of(s)] += 1
        if hasattr(s, "length"):
            length += s.length()
        if type(s) is Circle:
            area += math.pi * s.r * s.r
        elif type(s) is Polyline and s.closed:
            area += abs(s.signed_area())
        elif type(s) is EllipseArc and s.closed:
            area += math.pi * s.major.length() ** 2 * s.ratio
        elif type(s) is Hatch:
            from .hatch import area as hatch_area
            area += hatch_area(s)
        box = bbox_union(box, doc.shape_bbox(s))
    return Summary(sum(kinds.values()), dict(kinds), length, area, box)
