# SPDX-License-Identifier: GPL-3.0-or-later
"""Dimensions: what one measures, what it says, what it draws, and how it follows what it's attached to.

A dimension holds only what it measures and where it goes (`shapes.Dimension`). Its lines, arrowheads and text are worked out here from those and its
style, every time, so what it shows can't disagree with what it measures. Sizes are its style's times its scale, and they're for the sheet (ADR-5).

A dimension attached to something follows it inside the same change (`follow`, called from the Transaction), so one undo puts both back.
"""
from __future__ import annotations

import math
from dataclasses import replace

from .curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from .shapes import Dimension, Insert, Node, Solid, Text, plain_mtext, text_width
from .units import format_length
from .vec import TAU, Vec, tol

ARROW_WIDTH = 1.0 / 3.0         # a closed, filled arrowhead is a third as wide as it's long
_FORMATS = {1: "scientific", 2: "decimal", 3: "engineering", 4: "architectural", 5: "fractional", 6: "decimal"}


# ----------------------------------------------------------------------------------------------- what it measures


def angle_of(d: Dimension) -> tuple[Vec, Vec, Vec, float] | None:
    """An angular dimension's vertex, the arm its arc starts from, the arm it ends at, and the angle swept counter-clockwise between them. Two lines make four
    angles round where they cross, and it's the one `at` is in. Three points make two, the angle and what's left of the turn. None if there's no angle."""
    if len(d.pts) == 4:
        a0, a1, b0, b1 = d.pts
        da, db = a1 - a0, b1 - b0
        if da.length2() == 0.0 or db.length2() == 0.0:
            return None
        da, db = da.unit(), db.unit()
        den = da.cross(db)
        if abs(den) < 1e-12:
            return None
        v = a0 + da * ((b0 - a0).cross(db) / den)
        rays = sorted(((r.angle() % TAU, r) for r in (da, -da, db, -db)), key=lambda x: x[0])
        w = d.at - v
        aim = w.angle() % TAU if w.length2() > 0.0 else rays[0][0]
        for i in range(4):
            a, r1 = rays[i]
            b, r2 = rays[(i + 1) % 4]
            sweep = (b - a) % TAU
            if (aim - a) % TAU <= sweep:
                return v, r1, r2, sweep
        return v, rays[0][1], rays[1][1], (rays[1][0] - rays[0][0]) % TAU
    v, p1, p2 = d.pts
    r1, r2 = p1 - v, p2 - v
    if r1.length2() == 0.0 or r2.length2() == 0.0:
        return None
    r1, r2 = r1.unit(), r2.unit()
    sweep = (r2.angle() - r1.angle()) % TAU
    w = d.at - v
    if w.length2() == 0.0 or (w.angle() - r1.angle()) % TAU <= sweep:
        return v, r1, r2, sweep
    return v, r2, r1, TAU - sweep


def measurement(d: Dimension) -> float:
    """What it measures: a length, or an angle in radians."""
    m = d.measure
    if m == "aligned" or m == "radius":
        return d.pts[0].distance(d.pts[1])
    if m == "diameter":
        return 2.0 * d.pts[0].distance(d.pts[1])
    if m == "linear":
        return abs((d.pts[1] - d.pts[0]).dot(Vec(math.cos(d.angle), math.sin(d.angle))))
    found = angle_of(d)
    return found[3] if found is not None else 0.0


def _number(value: float, style) -> str:
    fmt = _FORMATS.get(style.unit_format, "decimal")
    text = format_length(value, fmt, max(0, min(int(style.decimals), 8)))
    if fmt == "decimal":
        if style.suppress_zeros & 8 and "." in text:
            text = text.rstrip("0").rstrip(".")
        if style.suppress_zeros & 4:
            if text.startswith("0."):
                text = text[1:]
            elif text.startswith("-0."):
                text = "-" + text[2:]
        if text in ("", "-"):
            text = "0"
        if style.decimal_sep and style.decimal_sep != ".":
            text = text.replace(".", style.decimal_sep)
    return text


def _degrees(radians: float, style) -> str:
    decimals = max(0, min(int(style.angle_decimals), 8))
    text = f"{math.degrees(radians):.{decimals}f}"
    if "." in text and style.suppress_zeros & 8:
        text = text.rstrip("0").rstrip(".")
    if style.decimal_sep and style.decimal_sep != ".":
        text = text.replace(".", style.decimal_sep)
    return text + "°"


def measured_text(d: Dimension, style) -> str:
    """The measurement as its style writes it, with R or Ø in front of a radius or diameter. Ø is what a DXF file's %%c means, and every font has it."""
    value = measurement(d)
    if d.measure == "angular":
        return _degrees(value, style)
    shown = {"radius": "R", "diameter": "Ø"}.get(d.measure, "") + _number(value * style.linear_factor, style)
    post = style.prefix_suffix
    if post:
        shown = post.replace("<>", shown) if "<>" in post else shown + post
    return shown


def label(d: Dimension, style) -> str:
    """What it says: the measurement, or what was typed instead, where "<>" stands for the measurement. A single space says nothing, as in a DXF."""
    if not d.text:
        return measured_text(d, style)
    if not d.text.strip():
        return ""
    text = " ".join(plain_mtext(d.text)) if "\\" in d.text or "{" in d.text else d.text
    return text.replace("<>", measured_text(d, style)) if "<>" in text else text


# ----------------------------------------------------------------------------------------------- what it draws


def sizes(d: Dimension, style) -> dict[str, float]:
    """Its style's sizes, in drawing units."""
    k = (style.scale if style.scale > 0.0 else 1.0) * d.scale
    return {"arrow": style.arrow_size * k, "text": style.text_height * k, "offset": style.ext_offset * k, "beyond": style.ext_beyond * k,
            "gap": abs(style.text_gap) * k, "tick": style.tick_size * k, "centre": style.center_mark * k}


def drawn(d: Dimension, style) -> list:
    """Its lines, arcs, arrowheads and text, in drawing units, as shapes."""
    if d.measure in ("linear", "aligned"):
        return _linear(d, style)
    if d.measure == "angular":
        return _angular(d, style)
    return _radial(d, style)


def readable(direction: Vec) -> float:
    """A rotation for text along this direction that reads left to right, or bottom to top."""
    a = math.atan2(direction[1], direction[0])
    if a > math.pi / 2.0 + 1e-9:
        a -= math.pi
    elif a <= -math.pi / 2.0 + 1e-9:
        a += math.pi
    return a


def arrowhead(tip: Vec, back: Vec, size: float) -> Solid:
    """A closed, filled arrowhead with its point at `tip`, its tail along `back` (a unit vector)."""
    base = tip + back * size
    side = back.perp() * (size * ARROW_WIDTH * 0.5)
    return Solid((tip, base + side, base - side))


def _tick(at: Vec, along: Vec, size: float) -> Line:
    slant = (along + along.perp()).unit() * size
    return Line(at - slant, at + slant)


def _extension(p: Vec, q: Vec, offset: float, beyond: float) -> list:
    """From just off the point measured to just past the dimension line."""
    e = q - p
    n = e.length()
    if n <= 0.0:
        return []
    v = e / n
    start = p + v * min(offset, n)
    end = q + v * beyond
    return [Line(start, end)] if start.distance(end) > 0.0 else []


def _text(d: Dimension, style, middle: Vec, rotation: float, height: float, words: str) -> Text:
    return Text(d.text_at if d.text_at is not None else middle, height, words, rotation, style.text_style, halign=4)


def _line_with_room(a: Vec, b: Vec, hole_at: Vec | None, hole: float) -> list:
    """A line, with a gap for text sitting on it."""
    if hole_at is None or hole <= 0.0:
        return [Line(a, b)]
    span = b - a
    n = span.length()
    if n <= 0.0:
        return []
    u = span / n
    t = (hole_at - a).dot(u)
    out = []
    if t - hole > 0.0:
        out.append(Line(a, a + u * (t - hole)))
    if t + hole < n:
        out.append(Line(a + u * (t + hole), b))
    return out


def _linear(d: Dimension, style) -> list:
    s = sizes(d, style)
    arrow, th, gap = s["arrow"], s["text"], s["gap"]
    p1, p2 = d.pts
    if d.measure == "aligned":
        u = p2 - p1
        u = u.unit() if u.length2() > 0.0 else Vec(1.0, 0.0)
    else:
        u = Vec(math.cos(d.angle), math.sin(d.angle))
    n = u.perp()
    q1 = p1 + n * (d.at - p1).dot(n)
    q2 = p2 + n * (d.at - p2).dot(n)
    out: list = _extension(p1, q1, s["offset"], s["beyond"]) + _extension(p2, q2, s["offset"], s["beyond"])
    words = label(d, style)
    along = q2 - q1 if q2.distance(q1) > 0.0 else u
    rotation = readable(along)
    up = Vec(-math.sin(rotation), math.cos(rotation))
    middle = q1.midpoint(q2)
    on_line = not style.text_above
    hole = text_width(words, th) * 0.5 + gap if on_line and words and d.text_at is None else 0.0
    span = q2 - q1
    length = span.length()
    if length > 0.0:
        w = span / length
        if s["tick"] > 0.0:
            out += _line_with_room(q1, q2, middle, hole) + [_tick(q1, w, s["tick"]), _tick(q2, w, s["tick"])]
        elif length >= 2.5 * arrow:
            out += _line_with_room(q1, q2, middle, hole) + [arrowhead(q1, w, arrow), arrowhead(q2, -w, arrow)]
        else:
            # no room between them: the arrows go outside, pointing in
            out += [Line(q1 - w * (2.0 * arrow), q2 + w * (2.0 * arrow)), arrowhead(q1, -w, arrow), arrowhead(q2, w, arrow)]
    if words:
        out.append(_text(d, style, middle if on_line else middle + up * (gap + th * 0.5), rotation, th, words))
    return out


def _radial(d: Dimension, style) -> list:
    s = sizes(d, style)
    arrow, th, gap = s["arrow"], s["text"], s["gap"]
    c, p = d.pts
    r = c.distance(p)
    v = d.at - c
    if v.length2() == 0.0:
        v = p - c
    direction = v.unit() if v.length2() > 0.0 else Vec(1.0, 0.0)
    edge = c + direction * r
    outside = v.length() > r
    out: list = []
    if d.measure == "diameter":
        far = c - direction * r
        out += [Line(far, edge), arrowhead(edge, -direction, arrow), arrowhead(far, direction, arrow)]
        if outside:
            out.append(Line(edge, d.at))
        middle = d.at if outside else c
    elif outside:
        out += [Line(edge, d.at), arrowhead(edge, direction, arrow)]       # at the curve, pointing in, its tail outside
        middle = d.at
    else:
        out += [Line(c, edge), arrowhead(edge, -direction, arrow)]         # pointing out from the centre
        middle = c.midpoint(edge)
    mark = s["centre"]
    if mark and outside:
        k = abs(mark)
        out += [Line(c - Vec(k, 0.0), c + Vec(k, 0.0)), Line(c - Vec(0.0, k), c + Vec(0.0, k))]
    words = label(d, style)
    if words:
        rotation = readable(direction)
        up = Vec(-math.sin(rotation), math.cos(rotation))
        out.append(_text(d, style, middle + up * (gap + th * 0.5), rotation, th, words))
    return out


def arms_of(d: Dimension, found: tuple[Vec, Vec, Vec, float]) -> tuple[tuple[Vec, Vec], tuple[Vec, Vec]]:
    """The two arms as segments, in the order the arc runs between them."""
    v, r1, _r2, _ = found
    if len(d.pts) == 4:
        a, b = (d.pts[0], d.pts[1]), (d.pts[2], d.pts[3])
        da = a[1] - a[0]
        first_is_a = da.length2() > 0.0 and abs(r1.cross(da.unit())) < 1e-9
        return (a, b) if first_is_a else (b, a)
    p1, p2 = d.pts[1], d.pts[2]
    first_is_p1 = abs(r1.cross((p1 - v).unit())) < 1e-9 and r1.dot(p1 - v) > 0.0
    return ((v, p1), (v, p2)) if first_is_p1 else ((v, p2), (v, p1))


def _angular(d: Dimension, style) -> list:
    s = sizes(d, style)
    arrow, th, gap = s["arrow"], s["text"], s["gap"]
    found = angle_of(d)
    words = label(d, style)
    if found is None:
        return [_text(d, style, d.at, 0.0, th, words)] if words else []
    v, r1, r2, sweep = found
    radius = v.distance(d.at)
    if radius <= 0.0:
        radius = arrow * 4.0
    start = r1.angle()
    out: list = []
    for (e0, e1), r in zip(arms_of(d, found), (r1, r2)):
        ends = sorted(((e0 - v).dot(r), (e1 - v).dot(r)))
        if ends[0] <= radius <= ends[1]:
            continue        # the arc reaches the arm itself
        arc_end = v + r * radius
        if radius > ends[1]:
            out.append(Line(v + r * (ends[1] + s["offset"]), arc_end + r * s["beyond"]))
        else:
            out.append(Line(v + r * (ends[0] - s["offset"]), arc_end - r * s["beyond"]))
    out.append(Arc(v, radius, start, sweep))
    s1, s2 = v + r1 * radius, v + r2 * radius
    step = arrow / radius
    room = radius * sweep >= 2.5 * arrow
    for tip, toward in ((s1, start + (step if room else -step)), (s2, start + sweep + (-step if room else step))):
        tail = v + Vec(math.cos(toward), math.sin(toward)) * radius - tip
        if tail.length2() > 0.0:
            out.append(arrowhead(tip, tail.unit(), arrow))
    if words:
        mid = start + sweep * 0.5
        out_dir = Vec(math.cos(mid), math.sin(mid))
        rotation = readable(out_dir.perp())
        out.append(_text(d, style, v + out_dir * (radius + gap + th * 0.5), rotation, th, words))
    return out


def text_box(d: Dimension, style) -> tuple[Vec, Vec, Vec] | None:
    """Where its text is, as (corner, along, up), for picking it and moving it."""
    for item in drawn(d, style):
        if type(item) is Text:
            return item.box()
    return None


# ------------------------------------------------------------------------------------------- what it's attached to


def point_of(s, which: str, d: Dimension | None = None, i: int = 0) -> Vec | None:
    """The point of an object that a dimension is attached to, by name. None if it hasn't got one by that name any more.

    The names: start, end, mid, center, point (of a point or a block), vertex:n and mid:n (of a polyline), and on (a point on a circle or arc, in the
    direction the dimension's point was from its centre, for radius and diameter dimensions)."""
    k = type(s)
    try:
        if which == "center":
            return s.c if k is Arc or k is Circle or k is EllipseArc else None
        if which == "start" or which == "end":
            if k is Line:
                return s.a if which == "start" else s.b
            if k is Arc or k is EllipseArc or k is Spline or k is Polyline:
                return s.start if which == "start" else s.end
            return None
        if which == "mid":
            return s.point_at(0.5) if k is Line or k is Arc else None
        if which == "point":
            return s.p if k is Node or k is Insert else None
        if which.startswith("vertex:"):
            j = int(which[7:])
            return s.pts[j] if k is Polyline and 0 <= j < len(s.pts) else None
        if which.startswith("mid:"):
            j = int(which[4:])
            if k is not Polyline:
                return None
            segments = s.segments
            return segments[j].point_at(0.5) if 0 <= j < len(segments) else None
        if which == "on" and d is not None and (k is Arc or k is Circle):
            direction = d.pts[i] - d.pts[0]
            return s.c + direction.unit() * s.r if direction.length2() > 0.0 else None
    except (ValueError, IndexError, ArithmeticError):
        return None
    return None


def which_point(s, p: Vec) -> str | None:
    """Which of an object's named points this is, if any: what a dimension measuring from it can stay attached to."""
    k = type(s)
    names: list[str] = []
    if k is Line:
        names = ["start", "end", "mid"]
    elif k is Arc:
        names = ["center", "start", "end", "mid"]
    elif k is Circle:
        names = ["center"]
    elif k is EllipseArc:
        names = ["center"] if s.closed else ["center", "start", "end"]
    elif k is Node or k is Insert:
        names = ["point"]
    elif k is Polyline:
        names = [f"vertex:{j}" for j in range(len(s.pts))] + [f"mid:{j}" for j in range(len(s.segments))]
    elif k is Spline and not s.closed:
        names = ["start", "end"]
    eps = tol(p[0], p[1]) * 100.0
    for name in names:
        q = point_of(s, name)
        if q is not None and abs(q[0] - p[0]) <= eps and abs(q[1] - p[1]) <= eps:
            return name
    return None


def _same(p: Vec, q: Vec) -> bool:
    eps = tol(p[0], p[1], q[0], q[1]) * 100.0
    return abs(p[0] - q[0]) <= eps and abs(p[1] - q[1]) <= eps


def follow(d: Dimension, shape_of, moved_itself: bool) -> Dimension | None:
    """This dimension, now that what it's attached to may have changed. `shape_of(id)` is that object's shape now, or None if it's gone.

    If the dimension itself was changed in the same step (it's new, or it was moved), it isn't moved again: it lets go of whatever it no longer sits on. That's
    how moving a dimension on its own, or copying it, leaves it unattached, while moving it together with what it measures keeps it attached. Something
    deleted is let go of too. None if nothing about it changes."""
    if not d.refs:
        return None
    pts = list(d.pts)
    refs = list(d.refs)
    shifted = False
    for i, ref in enumerate(refs):
        if ref is None:
            continue
        s = shape_of(ref[0])
        p = point_of(s, ref[1], d, i) if s is not None else None
        if p is None:
            refs[i] = None
            continue
        if _same(p, pts[i]):
            continue
        if moved_itself:
            refs[i] = None
        else:
            pts[i] = p
            shifted = True
    kept = tuple(refs) if any(r is not None for r in refs) else ()
    if not shifted:
        return replace(d, refs=kept) if kept != d.refs else None
    return _resettled(d, tuple(pts), kept)


def _resettled(d: Dimension, pts: tuple[Vec, ...], refs: tuple) -> Dimension:
    """Where it goes, kept where it was relative to what it measures, now that that's moved."""
    new = replace(d, pts=pts, refs=refs, block="")
    m = d.measure
    if m == "radius" or m == "diameter":
        c0, c1 = d.pts[0], pts[0]
        r0, r1 = d.pts[0].distance(d.pts[1]), pts[0].distance(pts[1])

        def carry(q):
            if q is None:
                return None
            w = q - c0
            if w.length2() == 0.0:
                return c1
            return c1 + w.unit() * (w.length() - r0 + r1)
        return replace(new, at=carry(d.at), text_at=carry(d.text_at))
    if m == "angular":
        a, b = angle_of(d), angle_of(replace(new, at=d.at))
        if a is None or b is None:
            return new
        v0, v1 = a[0], b[0]
        # turned as much as the arm its arc starts from has turned, so it stays in the same one of the angles
        arm0 = arms_of(d, a)[0]
        if len(pts) == 4:
            j = 0 if (arm0[0], arm0[1]) == (d.pts[0], d.pts[1]) else 2
            before, after = d.pts[j + 1] - d.pts[j], pts[j + 1] - pts[j]
        else:
            j = 1 if arm0[1] == d.pts[1] else 2
            before, after = d.pts[j] - d.pts[0], pts[j] - pts[0]
        turn = after.angle() - before.angle() if before.length2() > 0.0 and after.length2() > 0.0 else 0.0

        def carry(q):
            return None if q is None else v1 + (q - v0).rotated(turn)
        return replace(new, at=carry(d.at), text_at=carry(d.text_at))
    o0 = d.pts[0].midpoint(d.pts[1])
    o1 = pts[0].midpoint(pts[1])
    if m == "aligned":
        u0, u1 = d.pts[1] - d.pts[0], pts[1] - pts[0]
        u0 = u0.unit() if u0.length2() > 0.0 else Vec(1.0, 0.0)
        u1 = u1.unit() if u1.length2() > 0.0 else u0
    else:
        u0 = u1 = Vec(math.cos(d.angle), math.sin(d.angle))

    def carry(q):
        if q is None:
            return None
        w = q - o0
        x, y = w.dot(u0), w.dot(u0.perp())
        return o1 + u1 * x + u1.perp() * y
    return replace(new, at=carry(d.at), text_at=carry(d.text_at))
