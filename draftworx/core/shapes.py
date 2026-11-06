# SPDX-License-Identifier: GPL-3.0-or-later
"""What an entity can be, other than a curve: points, solids, text, block references.

All immutable values, like the curves. Everything that holds positions answers `transformed(m)` and `bbox()`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from .vec import Affine, BBox, Vec, bbox_of, bbox_union

# Helvetica's published widths, in thousandths of the font size. Text is measured with these rather than whichever font
# a computer happens to have, so layout and extents come out the same on Windows and Linux. Capital height is 718.
_WIDTHS = (278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556,
           556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,
           722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556,
           556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334,
           260, 334, 584)
_CAP = 718.0
_DESCENDER = 207.0      # how far below the baseline its lowest letters go


def text_width(s: str, height: float, width_factor: float = 1.0) -> float:
    """How long a line of text comes out, for a capital height of `height`."""
    total = 0
    for ch in s:
        o = ord(ch) - 32
        total += _WIDTHS[o] if 0 <= o < len(_WIDTHS) else 556
    return total / _CAP * height * width_factor


@dataclass(frozen=True, slots=True)
class Node:
    """A point object, what DXF calls POINT."""
    p: Vec
    kind = "node"

    def bbox(self) -> BBox:
        return (self.p[0], self.p[1], self.p[0], self.p[1])

    def transformed(self, m: Affine) -> "Node":
        return Node(m.apply(self.p))


@dataclass(frozen=True, slots=True)
class Solid:
    """A filled triangle or quadrilateral. Corners are in order round the outline (a DXF file's Z order is undone on reading)."""
    pts: tuple[Vec, ...]
    kind = "solid"

    def __post_init__(self):
        if len(self.pts) not in (3, 4):
            raise ValueError("a solid has 3 or 4 corners")

    def bbox(self) -> BBox:
        return bbox_of(self.pts)

    def transformed(self, m: Affine) -> "Solid":
        return Solid(tuple(m.apply(p) for p in self.pts))

    def outline(self) -> Polyline:
        return Polyline(self.pts, closed=True)


# Horizontal: 0 left, 1 centre, 2 right, 3 aligned, 4 middle, 5 fit. Vertical: 0 baseline, 1 bottom, 2 middle, 3 top.
@dataclass(frozen=True, slots=True)
class Text:
    """One line of text. `p` is the point it's anchored at, per its alignment; `p2` is the second point of aligned and fit text."""
    p: Vec
    height: float
    text: str
    rotation: float = 0.0
    style: str = "Standard"
    halign: int = 0
    valign: int = 0
    width_factor: float = 1.0
    oblique: float = 0.0
    p2: Vec | None = None
    mirrored_x: bool = False
    mirrored_y: bool = False

    kind = "text"

    def baseline_shift(self) -> float:
        """How far the baseline is above `p`, by the vertical alignment: on it for baseline; a descender's depth above it for bottom, so
        the lowest letters (Helvetica's, ADR-10) reach `p`; and below it for middle and top, which are of the capitals."""
        h = self.height
        return {0: 0.0, 1: _DESCENDER / _CAP * h, 2: -0.5 * h, 3: -h}.get(2 if self.halign == 4 else self.valign, 0.0)

    def box(self) -> tuple[Vec, Vec, Vec]:
        """(lower-left, along, up) of the box the text occupies, for pick and extents."""
        length = text_width(self.text, self.height, self.width_factor)
        h = self.height
        rot = self.rotation
        if self.halign in (3, 5) and self.p2 is not None:
            d = self.p2 - self.p
            length = d.length() or length
            rot = d.angle() if d.length() > 0.0 else rot
            if self.halign == 3 and length and self.text:
                h = self.height * length / max(text_width(self.text, self.height, self.width_factor), 1e-12)
            start = self.p
            along = Vec(math.cos(rot), math.sin(rot))
            return start, along * length, along.perp() * h
        along = Vec(math.cos(rot), math.sin(rot))
        up = along.perp()
        dx = {0: 0.0, 1: -0.5, 2: -1.0, 4: -0.5}.get(self.halign, 0.0) * length
        start = self.p + along * dx + up * self.baseline_shift()
        return start, along * length, up * h

    def bbox(self) -> BBox:
        o, a, u = self.box()
        return bbox_of((o, o + a, o + u, o + a + u))

    def transformed(self, m: Affine) -> "Text":
        along = m.apply_vector(Vec(math.cos(self.rotation), math.sin(self.rotation)))
        up = m.apply_vector(Vec(-math.sin(self.rotation), math.cos(self.rotation)))
        k = up.length()
        scale_x = along.length() / k if k > 0.0 else 1.0
        return replace(self, p=m.apply(self.p), p2=m.apply(self.p2) if self.p2 is not None else None,
                       height=self.height * k, rotation=along.angle(), width_factor=self.width_factor * scale_x,
                       mirrored_x=self.mirrored_x != m.is_mirrored())


def plain_mtext(raw: str) -> list[str]:
    """The lines of an MTEXT string with its formatting codes removed: what there is to measure, search and read."""
    out: list[str] = []
    cur: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        if ch == "\\" and i + 1 < n:
            code = raw[i + 1]
            if code in "Pp":
                out.append("".join(cur))
                cur = []
                i += 2
            elif code in "\\{}":
                cur.append(code)
                i += 2
            elif code == "~":
                cur.append(" ")
                i += 2
            elif code == "U" and raw[i + 2:i + 3] == "+" and i + 7 <= n:
                try:
                    cur.append(chr(int(raw[i + 3:i + 7], 16)))
                except ValueError:
                    pass
                i += 7
            elif code in "LlOoKkNn":
                i += 2
            elif code == "S":
                j = raw.find(";", i)
                j = n if j < 0 else j
                cur.append(raw[i + 2:j].replace("^", "/").replace("#", "/"))
                i = j + 1
            elif code in "ACFfHQTW":
                j = raw.find(";", i)
                i = n if j < 0 else j + 1
            else:
                cur.append(code)
                i += 2
        elif ch in "{}":
            i += 1
        else:
            cur.append(ch)
            i += 1
    out.append("".join(cur))
    return out


@dataclass(frozen=True, slots=True)
class MText:
    """Paragraph text. `text` is the DXF string, formatting codes and all. `attach` is 1 to 9: top-left to bottom-right."""
    p: Vec
    height: float
    text: str
    width: float = 0.0
    rotation: float = 0.0
    attach: int = 1
    style: str = "Standard"
    spacing: float = 1.0

    kind = "mtext"

    def lines(self) -> list[str]:
        return plain_mtext(self.text)

    def box(self) -> tuple[Vec, Vec, Vec]:
        lines = self.lines()
        widest = max((text_width(s, self.height) for s in lines), default=0.0)
        w = self.width if self.width > 0.0 else widest
        h = self.height * (1.0 + (len(lines) - 1) * 1.667 * self.spacing)
        along = Vec(math.cos(self.rotation), math.sin(self.rotation))
        up = along.perp()
        col = (self.attach - 1) % 3
        row = (self.attach - 1) // 3
        start = self.p + along * (-0.5 * col * w) + up * (-h + 0.5 * row * h)
        return start, along * w, up * h

    def bbox(self) -> BBox:
        o, a, u = self.box()
        return bbox_of((o, o + a, o + u, o + a + u))

    def transformed(self, m: Affine) -> "MText":
        along = m.apply_vector(Vec(math.cos(self.rotation), math.sin(self.rotation)))
        k = along.length()
        return replace(self, p=m.apply(self.p), height=self.height * k, width=self.width * k, rotation=along.angle())


@dataclass(frozen=True, slots=True)
class Attrib:
    """A block attribute's tag and its text, placed like any other text."""
    tag: str
    text: Text
    prompt: str = ""
    invisible: bool = False
    constant: bool = False

    def transformed(self, m: Affine) -> "Attrib":
        return replace(self, text=self.text.transformed(m))


@dataclass(frozen=True, slots=True)
class AttDef:
    """An attribute's definition inside a block: what each insert is asked to fill in."""
    attrib: Attrib
    kind = "attdef"

    def shown(self) -> Text:
        """What's drawn of a definition on its own: its tag, as in AutoCAD. Each placed copy shows its own value instead."""
        return replace(self.attrib.text, text=self.attrib.tag) if self.attrib.tag else self.attrib.text

    def bbox(self) -> BBox:
        return bbox_union(self.attrib.text.bbox(), self.shown().bbox())

    def transformed(self, m: Affine) -> "AttDef":
        return AttDef(self.attrib.transformed(m))


@dataclass(frozen=True, slots=True)
class Insert:
    """A placed block. Scale and rotation are as DXF has them, so a rectangular array and a stretched copy are exact."""
    block: str
    p: Vec
    sx: float = 1.0
    sy: float = 1.0
    rotation: float = 0.0
    attribs: tuple[Attrib, ...] = ()
    columns: int = 1
    rows: int = 1
    column_spacing: float = 0.0
    row_spacing: float = 0.0

    kind = "insert"

    def matrix(self, base: Vec = Vec(0.0, 0.0), column: int = 0, row: int = 0) -> Affine:
        """From block coordinates to where this copy puts them."""
        m = Affine.translation(-base[0] + column * self.column_spacing / (self.sx or 1.0),
                               -base[1] + row * self.row_spacing / (self.sy or 1.0))
        m = m.then(Affine(self.sx, 0.0, 0.0, self.sy, 0.0, 0.0))
        return m.then(Affine.rotation(self.rotation)).then(Affine.translation(self.p[0], self.p[1]))

    def transformed(self, m: Affine) -> "Insert":
        """As far as an insert can follow. Move, turn, mirror and uniform scale are exact. A stretch applied at an angle
        to the block can't be held by the block's two scales, and the caller should explode it first."""
        ax = m.apply_vector(Vec(math.cos(self.rotation), math.sin(self.rotation)))
        k = ax.length() or 1.0
        ky = m.apply_vector(Vec(-math.sin(self.rotation), math.cos(self.rotation))).length() or 1.0
        mirrored = m.is_mirrored()
        return replace(self, p=m.apply(self.p), sx=self.sx * k, sy=(-self.sy if mirrored else self.sy) * ky, rotation=ax.angle(),
                       column_spacing=self.column_spacing * k, row_spacing=(-self.row_spacing if mirrored else self.row_spacing) * ky,
                       attribs=tuple(a.transformed(m) for a in self.attribs))


MEASURES = ("linear", "aligned", "radius", "diameter", "angular")


@dataclass(frozen=True, slots=True)
class Dimension:
    """A measurement shown on the drawing. Its lines, arrows and text are worked out from this each time (`dim.py`), so they always say what it measures.

    `measure` is "linear" (along `angle`), "aligned", "radius", "diameter" or "angular". `pts` is what's measured: two points for linear and aligned; the centre
    and a point on the curve for radius and diameter; for angular, the ends of its two lines (a0, a1, b0, b1), or its vertex and a point on each arm. `at` is
    where it goes: a point on the dimension line, or on its arc, or where a radius or diameter's text is. `text` replaces what it says, with "<>" standing for
    the measurement. `scale` multiplies its style's sizes. `text_at` is where its text was moved to. `refs` has, for each of `pts`, (entity id, which point of
    it) or None: what it follows when that changes. `block` is a block from a file that drew it there, shown while the dimension is still what the file said."""
    measure: str
    pts: tuple[Vec, ...]
    at: Vec
    angle: float = 0.0
    text: str = ""
    style: str = "Standard"
    scale: float = 1.0
    text_at: Vec | None = None
    refs: tuple = ()
    block: str = ""

    kind = "dimension"

    def __post_init__(self):
        if self.measure not in MEASURES:
            raise ValueError(f"there's no {self.measure!r} kind of dimension")
        wanted = {"linear": (2,), "aligned": (2,), "radius": (2,), "diameter": (2,), "angular": (3, 4)}[self.measure]
        if len(self.pts) not in wanted:
            raise ValueError(f"a {self.measure} dimension measures {' or '.join(map(str, wanted))} points, not {len(self.pts)}")
        if self.refs and len(self.refs) != len(self.pts):
            raise ValueError("a dimension says what each of its points is attached to, or nothing")

    def bbox(self) -> BBox:
        """What it measures and where it's drawn. What it draws reaches a little further, by its style's sizes: `Document.shape_bbox` has that.

        `at` can be anywhere along a dimension line, so it's where the dimension line ends that counts. A radius or diameter's point on the curve only
        says how big it is, since it's drawn toward where it goes."""
        extra = (self.text_at,) if self.text_at is not None else ()
        if self.measure in ("radius", "diameter"):
            return bbox_of(self.pts[:1] + (self.at,) + extra)
        if self.measure == "angular":
            return bbox_of(self.pts + (self.at,) + extra)
        p1, p2 = self.pts
        u = p2 - p1 if self.measure == "aligned" else Vec(math.cos(self.angle), math.sin(self.angle))
        if u.length2() == 0.0:
            return bbox_of(self.pts + (self.at,) + extra)
        n = u.unit().perp()
        return bbox_of(self.pts + (p1 + n * (self.at - p1).dot(n), p2 + n * (self.at - p2).dot(n)) + extra)

    def transformed(self, m: Affine) -> "Dimension":
        """Moved with what it measures. Its sizes stay its style's, as a dimension's do when what it measures is scaled, and it's drawn afresh."""
        along = m.apply_vector(Vec(math.cos(self.angle), math.sin(self.angle)))
        return replace(self, pts=tuple(m.apply(p) for p in self.pts), at=m.apply(self.at), text_at=m.apply(self.text_at) if self.text_at is not None else None,
                       angle=along.angle() if along.length2() > 0.0 else self.angle, block="")


@dataclass(frozen=True, slots=True)
class PatternLine:
    """One family of a hatch pattern's lines, as a pattern file has it: lines at `angle` through `base`, each next one `along` further along it
    and `across` to its left, dashed by `dashes` (a dash is positive, a gap negative, a dot 0). Sizes are the pattern's own, before its scale."""
    angle: float
    base: Vec = Vec(0.0, 0.0)
    along: float = 0.0
    across: float = 1.0
    dashes: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class Hatch:
    """An area filled solid or with a pattern. `loops` are closed boundaries, each curves joined end to end, and what's inside an odd number
    of them is filled. `pattern` is "SOLID" or a pattern's name; `lines` is its definition when it isn't one this program knows, as from a
    file. The pattern's sizes are multiplied by `scale`, it's turned by `angle` and its lines start from `origin`.

    `seeds` are the points it was made from, by clicking inside, and `refs` the objects round them that it follows (`hatch.follow`)."""
    loops: tuple[tuple, ...]
    pattern: str = "SOLID"
    scale: float = 1.0
    angle: float = 0.0
    origin: Vec = Vec(0.0, 0.0)
    seeds: tuple[Vec, ...] = ()
    refs: tuple[int, ...] = ()
    lines: tuple[PatternLine, ...] = ()

    kind = "hatch"

    def __post_init__(self):
        if not self.loops or not all(self.loops):
            raise ValueError("a hatch needs a boundary")
        if not (self.scale > 0.0 and math.isfinite(self.scale)):
            raise ValueError("a hatch pattern's scale has to be more than zero")

    @property
    def solid(self) -> bool:
        return self.pattern.casefold() == "solid"

    def bbox(self) -> BBox:
        box = None
        for loop in self.loops:
            for c in loop:
                box = bbox_union(box, c.bbox())
        return box

    def transformed(self, m: Affine) -> "Hatch":
        """Moved, turned and scaled with its boundary. A mirror image keeps its pattern the way it was, as AutoCAD does by default,
        so writing on it isn't mirrored."""
        loops = tuple(tuple(c.transformed(m) for c in loop) for loop in self.loops)
        angle = self.angle
        if not m.is_mirrored():
            turn = m.apply_vector(Vec(1.0, 0.0))
            if turn.length2() > 0.0:
                angle = (self.angle + math.atan2(turn[1], turn[0])) % math.tau
        k = m.uniform_scale()
        return replace(self, loops=loops, scale=self.scale * k if k > 0.0 else self.scale, angle=angle, origin=m.apply(self.origin),
                       seeds=tuple(m.apply(p) for p in self.seeds))


@dataclass(frozen=True, slots=True)
class Foreign:
    """Something a file holds that can't be edited here. It goes back out exactly as it came in, from its entity's `source`.

    What's here is only for showing it and snapping to it: `block` is a block that draws it, as a DXF dimension has, placed at `at`,
    and `curves` is whatever else there is to see of it. It may be neither, and then it isn't shown at all."""
    dxf_type: str
    block: str = ""
    at: Vec = Vec(0.0, 0.0)
    curves: tuple = ()

    kind = "foreign"

    def bbox(self) -> BBox | None:
        box = None
        for c in self.curves:
            box = bbox_union(box, c.bbox())
        return box

    def transformed(self, m: Affine) -> "Foreign":
        raise ValueError(f"A {self.dxf_type.lower()} from a file can't be changed here.")


Curve = Line | Arc | Circle | EllipseArc | Spline
Shape = Curve | Polyline | Node | Solid | Text | MText | AttDef | Insert | Dimension | Hatch | Foreign
CURVES = (Line, Arc, Circle, EllipseArc, Spline)
