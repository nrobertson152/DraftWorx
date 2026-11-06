# SPDX-License-Identifier: GPL-3.0-or-later
"""Joining things into one, breaking one into its parts, and stretching."""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .. import color as colors
from ..curves import Arc, Circle, EllipseArc, Line, Polyline, Spline, bulge_arc
from ..model import BYBLOCK_TYPE, WEIGHT_BYBLOCK, Document, Style
from ..shapes import AttDef, Foreign, Insert, MText, Node, Solid, Text, plain_mtext
from ..vec import Affine, Vec, tol

# ----------------------------------------------------------------------------------------------------- joining

JOINABLE = (Line, Arc, Polyline)


@dataclass(frozen=True, slots=True)
class Joined:
    ids: tuple[int, ...]    # what went into it, in order
    shape: object           # what it all became


def _pieces_of(s) -> list:
    return list(s.segments) if type(s) is Polyline else [s]


def _one_line(pieces: list, eps: float) -> Line | None:
    """Straight pieces in a row are one line."""
    if not all(type(g) is Line for g in pieces):
        return None
    a, b = pieces[0].a, pieces[-1].b
    if a.distance(b) <= eps:
        return None
    u = (b - a).unit()
    if all(abs((g.b - a).cross(u)) <= eps and (g.b - g.a).dot(u) > 0.0 for g in pieces):
        return Line(a, b)
    return None


def _one_arc(pieces: list, eps: float) -> Arc | Circle | None:
    """Arcs of the same circle, going the same way, are one arc. All the way round, they're a circle."""
    if not all(type(g) is Arc for g in pieces):
        return None
    first = pieces[0]
    sign = first.sweep > 0.0
    for g in pieces:
        if g.c.distance(first.c) > eps or abs(g.r - first.r) > eps or (g.sweep > 0.0) != sign:
            return None
    total = sum(g.sweep for g in pieces)
    if abs(total) >= math.tau - 1e-9:
        return Circle(first.c, first.r)
    return Arc(first.c, first.r, first.start_angle, total)


def join(items: list[tuple[int, object]], eps: float | None = None) -> tuple[list[Joined], list[int]]:
    """Find what meets end to end, and make each run into one object. Returns the runs of two or more, and the ids of what meets nothing."""
    usable = [(i, s) for i, s in items if type(s) in JOINABLE and not (type(s) is Polyline and s.closed)]
    if eps is None:
        extents = [v for _, s in usable for v in s.bbox()]
        eps = tol(*extents) * 100.0 if extents else 1e-9
    runs: list[Joined] = []
    alone: list[int] = []
    left = list(usable)
    while left:
        run = [left.pop(0)]
        pieces = list(_pieces_of(run[0][1]))
        grew = True
        while grew:
            grew = False
            for k, (i, s) in enumerate(left):
                add = _pieces_of(s)
                if not add:
                    continue
                start, end = pieces[0].start, pieces[-1].end
                if add[0].start.distance(end) <= eps:
                    pieces = pieces + add
                elif add[-1].end.distance(end) <= eps:
                    pieces = pieces + [g.reversed() for g in reversed(add)]
                elif add[-1].end.distance(start) <= eps:
                    pieces = add + pieces
                elif add[0].start.distance(start) <= eps:
                    pieces = [g.reversed() for g in reversed(add)] + pieces
                else:
                    continue
                run.append((i, s))
                left.pop(k)
                grew = True
                break
        if len(run) == 1:
            alone.append(run[0][0])
            continue
        closed = pieces[0].start.distance(pieces[-1].end) <= eps
        shape = _one_arc(pieces, eps) or (None if closed else _one_line(pieces, eps))
        if shape is None:
            pts = [g.start for g in pieces] + ([] if closed else [pieces[-1].end])
            bulges = [0.0 if type(g) is Line else math.tan(g.sweep / 4.0) for g in pieces] + ([] if closed else [0.0])
            shape = Polyline(tuple(pts), tuple(bulges), closed)
        runs.append(Joined(tuple(i for i, _ in run), shape))
    return runs, alone


# --------------------------------------------------------------------------------------------------- exploding


def inside(style: Style, parent: Style) -> Style:
    """How something in a block looks once it's out: what was on layer 0, or marked ByBlock, takes after the block's copy."""
    return Style(parent.layer if style.layer == "0" else style.layer,
                 parent.color if style.color == colors.BYBLOCK else style.color,
                 parent.linetype if style.linetype.casefold() == BYBLOCK_TYPE.casefold() else style.linetype,
                 parent.weight if style.weight == WEIGHT_BYBLOCK else style.weight, style.ltscale)


def _insert_parts(doc: Document, s: Insert, style: Style) -> list[tuple[object, Style]]:
    d = doc.blocks.get(s.block)
    if d is None:
        raise ValueError(f"The block {s.block} isn't in this drawing, so there's nothing to take apart.")
    if s.columns * s.rows * max(len(d.entities), 1) > 100_000:
        raise ValueError("That's more than a hundred thousand objects. Taking it apart would make the drawing slower, not simpler.")
    out: list[tuple[object, Style]] = []
    for col in range(s.columns):
        for row in range(s.rows):
            m = s.matrix(d.base, col, row)
            for inner in d.entities:
                if type(inner.shape) is AttDef:
                    continue
                if type(inner.shape) is Foreign:
                    for piece, st in explode_shape(doc, inner.shape, inside(inner.style, style)):
                        out.append((piece.transformed(m), st))
                    continue
                out.append((inner.shape.transformed(m), inside(inner.style, style)))
    out.extend((a.text, style) for a in s.attribs if not a.invisible)
    return out


def explode_shape(doc: Document, s, style: Style) -> list[tuple[object, Style]]:
    """What this comes apart into, one level down. Raises ValueError if it's one thing already."""
    k = type(s)
    if k is Polyline:
        if len(s.segments) < 2:
            raise ValueError("That polyline is only one piece.")
        return [(g, style) for g in s.segments]
    if k is Insert:
        return _insert_parts(doc, s, style)
    if k is Foreign:
        out: list[tuple[object, Style]] = [(c, style) for c in s.curves]
        if s.block:
            out.extend(_insert_parts(doc, Insert(s.block, s.at), style))
        if not out:
            raise ValueError(f"There's nothing here that shows what's in that {s.dxf_type.lower()}.")
        return out
    if k is MText:
        along = Vec(math.cos(s.rotation), math.sin(s.rotation))
        up = along.perp()
        return [(Text(s.p - up * (s.height + i * s.height * 1.667 * s.spacing), s.height, line, s.rotation, s.style), style)
                for i, line in enumerate(plain_mtext(s.text))]
    raise ValueError("That's one thing already.")


# --------------------------------------------------------------------------------------------------- stretching


def _in(box, p) -> bool:
    return box[0] <= p[0] <= box[2] and box[1] <= p[1] <= box[3]


def stretched(s, box, m: Affine):
    """This object with whatever of it is inside the box moved. Arcs keep their bulge, so they stay arcs. Something with no ends to pull, such as a circle or text, moves
    if its centre or position is inside, and otherwise stays. Returns None if nothing would change."""
    k = type(s)
    if k is Line:
        a, b = _in(box, s.a), _in(box, s.b)
        if not a and not b:
            return None
        return Line(m.apply(s.a) if a else s.a, m.apply(s.b) if b else s.b)
    if k is Arc:
        a, b = _in(box, s.start), _in(box, s.end)
        if not a and not b:
            return None
        if a and b:
            return s.transformed(m)
        p, q = m.apply(s.start) if a else s.start, m.apply(s.end) if b else s.end
        if abs(s.sweep) >= math.tau - 1e-9 or p == q:
            return s
        return bulge_arc(p, q, math.tan(s.sweep / 4.0))
    if k is Polyline:
        inside_box = [_in(box, p) for p in s.pts]
        if not any(inside_box):
            return None
        return replace(s, pts=tuple(m.apply(p) if hit else p for p, hit in zip(s.pts, inside_box)))
    if k is Spline:
        if not s.ctrl:
            return None
        hits = [_in(box, p) for p in s.ctrl]
        if not any(hits):
            return None
        return replace(s, ctrl=tuple(m.apply(p) if hit else p for p, hit in zip(s.ctrl, hits)), fit=())
    if k is Solid:
        hits = [_in(box, p) for p in s.pts]
        if not any(hits):
            return None
        return Solid(tuple(m.apply(p) if hit else p for p, hit in zip(s.pts, hits)))
    if k is Circle or k is EllipseArc:
        return s.transformed(m) if _in(box, s.c) else None
    if k is Node or k is Text or k is MText or k is Insert:
        return s.transformed(m) if _in(box, s.p) else None
    if k is AttDef:
        return s.transformed(m) if _in(box, s.attrib.text.p) else None
    return None

