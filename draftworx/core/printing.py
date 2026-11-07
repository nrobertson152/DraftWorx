# SPDX-License-Identifier: GPL-3.0-or-later
"""What a sheet prints: every object on a layer that prints, as strokes, fills, dots and text, in millimetres on the paper.

The drawing is turned into paper millimetres by one transform, the sheet's, so a length on paper is exactly the drawing's length divided
by its scale. Line weights are millimetres on paper, as they're defined; line types' dashes are the drawing's own, divided by the scale.
A PDF (`pdf.py`) and the window's printing both draw from this, so they can't differ in what they show.

Colours print as they are, on white paper: colour 7 is black, and one too pale to see on white is darkened, as the screen does. Or all in
black, as most drawings are printed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Protocol

from . import color as colors
from . import dim as dims
from .curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from .model import BYBLOCK_TYPE, BYLAYER_TYPE, WEIGHT_BYBLOCK, WEIGHT_BYLAYER, WEIGHT_DEFAULT, Document, Style, split_key
from .ops.reshape import inside
from .shapes import AttDef, Dimension, Foreign, Hatch, Insert, MText, Node, Solid, Text, text_width
from .sheets import Sheet
from .vec import Affine, Vec

_CURVES = (Line, Arc, Circle, EllipseArc, Spline)
DEFAULT_WEIGHT = 0.25       # mm, as AutoCAD prints a default line
THINNEST = 0.05             # mm, for a weight of 0: the thinnest line a printer makes
MOST_PIECES = 5_000_000     # what one print draws, for a drawing that describes more than can be drawn


@dataclass(frozen=True, slots=True)
class Pen:
    rgb: int                        # 0xRRGGBB
    width: float                    # mm
    dashes: tuple[float, ...] = ()  # mm on paper, a dash then a gap; empty is solid


class Page(Protocol):
    def stroke(self, curve, pen: Pen) -> None: ...
    def fill(self, loops: tuple[tuple, ...], rgb: int) -> None: ...
    def dot(self, p: Vec, pen: Pen) -> None: ...
    def text(self, t: Text, rgb: int) -> None: ...


def on_white(rgb: int) -> int:
    """A colour as it should print on white: its own, unless it's too pale to see, when it's darkened toward black just enough."""
    def lum(c: int) -> float:
        out = []
        for shift in (16, 8, 0):
            x = ((c >> shift) & 255) / 255.0
            out.append(x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    if (1.05) / (lum(rgb) + 0.05) >= 2.6:
        return rgb
    for step in range(1, 11):
        k = 1.0 - step / 10.0
        c = (int(((rgb >> 16) & 255) * k) << 16) | (int(((rgb >> 8) & 255) * k) << 8) | int((rgb & 255) * k)
        if 1.05 / (lum(c) + 0.05) >= 2.6:
            return c
    return 0


def text_origin(t: Text) -> tuple[Vec, float, float, float]:
    """Where a line of text's baseline starts, which way it runs, and its height and width factor once fitted: what drawing it needs.
    The same reckoning as the window's, with Helvetica's widths (ADR-10)."""
    length = text_width(t.text, t.height, t.width_factor)
    rot, h, wf = t.rotation, t.height, t.width_factor
    if t.halign in (3, 5) and t.p2 is not None:
        d = t.p2 - t.p
        if d.length() > 0.0 and length > 0.0:
            fit = d.length() / length
            rot, wf, length = d.angle(), wf * fit, d.length()
            if t.halign == 3:
                h *= fit
        return t.p, rot, h, wf
    along = Vec(math.cos(rot), math.sin(rot))
    up = along.perp()
    dx = {0: 0.0, 1: -0.5, 2: -1.0, 4: -0.5}.get(t.halign, 0.0) * length
    dy = t.baseline_shift()
    return t.p + along * dx + up * dy, rot, h, wf


def paper_dashes(pattern: tuple[float, ...], k: float, dot: float) -> tuple[float, ...]:
    """A line type's pattern (a dash positive, a gap negative, a dot 0) as millimetres on paper, a dash then a gap and so on, as a PDF or
    a printer takes it: two dashes or two gaps in a row are one, a dot is as long as the line is wide, and one that starts with a gap
    starts with a dash of nothing. Empty if it would draw nothing, which prints as a solid line."""
    out: list[float] = []
    for x in pattern:
        gap = x < 0.0
        length = -x * k if gap else (x * k if x > 0.0 else dot)
        if gap == (len(out) % 2 == 1):
            out.append(length)
        elif out:
            out[-1] += length
        else:
            out += [0.0, length]
    if len(out) % 2:
        out.append(0.0)
    if not any(out[0::2]) or not any(out[1::2]) or not all(math.isfinite(x) for x in out):
        return ()
    return tuple(out)


class _Walk:
    def __init__(self, doc: Document, page: Page, sheet: Sheet, black: bool):
        self.doc, self.page, self.black = doc, page, black
        self.to_paper = Affine.translation(-sheet.at[0], -sheet.at[1]).then(Affine.scaling(1.0 / sheet.per_mm))
        self.per_mm = sheet.per_mm
        self.left = MOST_PIECES

    # how something looks, on paper
    def rgb(self, style: Style) -> int:
        if self.black:
            return 0
        layer = self.doc.layers.get(style.layer)
        c = style.color
        if c == colors.BYLAYER or c == colors.BYBLOCK:
            c = layer.color if layer is not None else 7
        if c == 7 or c == 255 or c < 0:
            return 0
        return on_white(colors.rgb_of(c, 0))

    def pen(self, style: Style, scale: float = 1.0) -> Pen:
        layer = self.doc.layers.get(style.layer)
        w = style.weight
        if w in (WEIGHT_BYLAYER, WEIGHT_BYBLOCK):
            w = layer.weight if layer is not None else WEIGHT_DEFAULT
        width = DEFAULT_WEIGHT if w == WEIGHT_DEFAULT or w < 0 else max(w / 100.0, THINNEST)
        lt = style.linetype
        if lt.casefold() in (BYLAYER_TYPE.casefold(), BYBLOCK_TYPE.casefold()):
            lt = layer.linetype if layer is not None else "Continuous"
        kind = self.doc.linetypes.get(lt)
        dashes: tuple[float, ...] = ()
        if kind is not None and kind.pattern:
            dashes = paper_dashes(kind.pattern, style.ltscale * self.doc.settings.ltscale * scale / self.per_mm, width)
        return Pen(self.rgb(style), width, dashes)

    def printed(self, style: Style) -> bool:
        layer = self.doc.layers.get(style.layer)
        return layer is None or (layer.visible and layer.plot)

    # what it draws
    def shape(self, s, style: Style, parent: Style | None, m: Affine, depth: int) -> None:
        effective = inside(style, parent) if parent is not None else style
        if parent is not None and style.layer != "0" and not self.printed(style):
            return
        self.left -= 1
        if self.left < 0:
            return
        k = type(s)
        if k in _CURVES or k is Polyline:
            self.curve(s, effective, m)
        elif k is Node:
            self.page.dot(m.apply(s.p), self.pen(effective))
        elif k is Solid:
            self.page.fill(((Polyline(tuple(m.apply(p) for p in s.pts), closed=True),),), self.rgb(effective))
        elif k is Text:
            self.text(s, effective, m)
        elif k is MText:
            for t in mtext_lines(s):
                self.text(t, effective, m)
        elif k is AttDef:
            if depth == 0:
                self.text(s.shown(), effective, m)
        elif k is Insert:
            self.insert(s, effective, m, depth)
        elif k is Dimension:
            if s.block and self.doc.blocks.get(s.block) is not None:
                self.insert(Insert(s.block, Vec(0.0, 0.0)), effective, m, depth)
            else:
                for item in dims.drawn(s, self.doc.dim_style_of(s)):
                    self.shape(item, effective, None, m, depth)
        elif k is Hatch:
            self.hatch(s, effective, m)
        elif k is Foreign:
            for c in s.curves:
                self.curve(c, effective, m)
            if s.block:
                self.insert(Insert(s.block, s.at), effective, m, depth)

    def curve(self, c, style: Style, m: Affine) -> None:
        if type(c) is Polyline and (c.width or c.widths):
            self.wide(c, style, m)
            return
        try:
            placed = c.transformed(m)
        except (ValueError, ArithmeticError):
            return
        self.page.stroke(placed, self.pen(style, m.uniform_scale() * self.per_mm))

    def wide(self, s: Polyline, style: Style, m: Affine) -> None:
        """A polyline with a width of its own covers an area: each piece filled, tapering where its width changes."""
        rgb = self.rgb(style)
        n = len(s.pts)
        box = s.bbox()
        chord = max(box[2] - box[0], box[3] - box[1], 1e-9) * 1e-3
        for i, seg in enumerate(s.segments):
            w0, w1 = s.widths[i % n] if s.widths else (s.width, s.width)
            pts = seg.flatten(chord)
            count = len(pts) - 1
            if count < 1 or (w0 <= 0.0 and w1 <= 0.0):
                continue
            left, right = [], []
            for j, p in enumerate(pts):
                a, b = pts[max(j - 1, 0)], pts[min(j + 1, count)]
                d = (b - a).unit()
                half = (w0 + (w1 - w0) * j / count) * 0.5
                left.append(p + d.perp() * half)
                right.append(p - d.perp() * half)
            outline = Polyline(tuple(m.apply(q) for q in left + right[::-1]), closed=True)
            self.page.fill(((outline,),), rgb)
        if s.widths and not all(a > 0.0 or b > 0.0 for a, b in s.widths):
            self.curve(replace(s, width=0.0, widths=()), style, m)

    def text(self, t: Text, style: Style, m: Affine) -> None:
        if not t.text.strip():
            return
        try:
            placed = t.transformed(m)
        except (ValueError, ArithmeticError):
            return
        self.page.text(placed, self.rgb(style))

    def insert(self, s: Insert, style: Style, m: Affine, depth: int) -> None:
        if depth >= 16:
            return
        d = self.doc.blocks.get(s.block)
        if d is None:
            return
        for a in s.attribs:
            if not a.invisible:
                self.text(a.text, style, m)
        for col in range(s.columns):
            for row in range(s.rows):
                full = s.matrix(d.base, col, row).then(m)
                for inner in d.entities:
                    if self.left < 0:
                        return
                    if type(inner.shape) is AttDef:
                        continue
                    self.shape(inner.shape, inner.style, style, full, depth + 1)

    def hatch(self, h: Hatch, style: Style, m: Affine) -> None:
        from .hatch import TooDense, fill_loops, pieces
        if h.solid:
            loops = tuple(tuple(c.transformed(m) for c in loop) for loop in fill_loops(h))
            self.page.fill(loops, self.rgb(style))
            return
        try:
            drawn = pieces(h)
        except TooDense:
            drawn = tuple(c for loop in h.loops for c in loop)
        pen = replace(self.pen(style), dashes=())
        for piece in drawn:
            if type(piece) is Node:
                self.page.dot(m.apply(piece.p), pen)
            else:
                self.page.stroke(piece.transformed(m), pen)


def mtext_lines(t: MText) -> list[Text]:
    """Paragraph text as the lines it's drawn in, wrapped to its width by Helvetica's widths."""
    lines: list[str] = []
    for line in t.lines():
        if t.width > 0.0 and text_width(line, t.height) > t.width:
            cur = ""
            for word in line.split(" "):
                test = f"{cur} {word}" if cur else word
                if text_width(test, t.height) <= t.width or not cur:
                    cur = test
                else:
                    lines.append(cur)
                    cur = word
            lines.append(cur)
        else:
            lines.append(line)
    step = t.height * 1.667 * t.spacing
    total = t.height + step * (len(lines) - 1)
    row, col = (t.attach - 1) // 3, (t.attach - 1) % 3
    top = {0: 0.0, 1: total * 0.5, 2: total}[row]
    along = Vec(math.cos(t.rotation), math.sin(t.rotation))
    up = along.perp()
    out = []
    for i, line in enumerate(lines):
        y = top - t.height - step * i
        x = {0: 0.0, 1: -0.5, 2: -1.0}[col] * text_width(line, t.height)
        out.append(Text(t.p + along * x + up * y, t.height, line, t.rotation, t.style))
    return out


def draw(doc: Document, sheet: Sheet, page: Page, black: bool = False) -> int:
    """Draw everything on the sheet onto the page. Solid fills first, so they never hide what's over them. Returns how many objects were
    left out because the drawing describes more than can be drawn (blocks in blocks in arrays)."""
    w = _Walk(doc, page, sheet, black)
    found: set[int] = set()
    for key in doc.index.query(sheet.box()):
        found.add(split_key(key)[0])
    chosen = [e for e in doc.sorted_entities() if e.id in found and w.printed(e.style)]
    for e in chosen:
        if type(e.shape) is Hatch and e.shape.solid:
            w.shape(e.shape, e.style, None, w.to_paper, 0)
    for e in chosen:
        if not (type(e.shape) is Hatch and e.shape.solid):
            w.shape(e.shape, e.style, None, w.to_paper, 0)
    return max(0, -w.left)


__all__ = ["DEFAULT_WEIGHT", "Page", "Pen", "draw", "mtext_lines", "on_white", "paper_dashes", "text_origin"]
