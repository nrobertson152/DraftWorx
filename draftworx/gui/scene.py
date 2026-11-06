# SPDX-License-Identifier: GPL-3.0-or-later
"""Turning the drawing into something Qt can paint quickly.

Geometry goes once into `QPainterPath`s, in drawing coordinates, grouped by how it looks and chunked. Panning and zooming are then only a
new transform, so they cost no Python per object (`docs/ARCHITECTURE.md`, ADR-1). When an object changes, only its chunk is rebuilt. A chunk
outside the window isn't painted at all.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen, QTransform

from ..core import color as colors
from ..core.curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from ..core.model import BYBLOCK_TYPE, BYLAYER_TYPE, WEIGHT_BYBLOCK, WEIGHT_BYLAYER, WEIGHT_DEFAULT, Document, Entity, Style
from ..core.shapes import AttDef, Foreign, Insert, MText, Node, Solid, Text
from ..core.vec import IDENTITY, Affine, BBox, bbox_union

CHUNK = 2000            # things to a chunk: small enough that an edit is quick, big enough that culling stays cheap
MOST_PIECES = 1_000_000 # of one object: blocks in blocks in arrays can multiply past anything memory or patience could hold
MOST_IN_ALL = 5_000_000 # of the whole drawing, for the same reason
DEFAULT_WEIGHT = 25     # hundredths of a millimetre
PX_PER_MM = 96.0 / 25.4
_CURVES = (Line, Arc, Circle, EllipseArc, Spline)
_FONT_ALIASES = {"txt": "", "simplex": "", "romans": "", "romanc": "", "isocp": "", "iso": "", "standard": ""}


@dataclass(frozen=True, slots=True)
class _Look:
    """How something's drawn: `rgb` of -1 means ink, the paper's opposite, like DXF's colour 7."""
    rgb: int
    pattern: tuple[float, ...]
    weight: int
    fill: bool = False


class _Chunk:
    """`path` is smoothed. `crisp` holds exactly horizontal and vertical lines, which smoothing would only smear across two rows of pixels."""
    __slots__ = ("look", "items", "path", "crisp", "points", "box", "dirty")

    def __init__(self, look: _Look):
        self.look = look
        self.items: dict[int, tuple[list, BBox | None]] = {}
        self.path: QPainterPath | None = None
        self.crisp: QPainterPath | None = None
        self.points: list[QPointF] = []
        self.box: BBox | None = None
        self.dirty = True


def _family_of(font_name: str) -> str:
    """The font to draw a text style with. SHX fonts can't be read, so they get the interface's plain sans."""
    base = font_name.rsplit(".", 1)[0].strip()
    if not base or base.casefold() in _FONT_ALIASES or font_name.casefold().endswith(".shx"):
        return "Arial"
    return base


_fonts: dict[str, tuple[QFont, QFontMetricsF]] = {}


def _font(name: str) -> tuple[QFont, QFontMetricsF]:
    """A font at a fixed large size. Text is then scaled, which keeps its shape the same at every zoom."""
    found = _fonts.get(name)
    if found is None:
        f = QFont(_family_of(name))
        f.setStyleHint(QFont.StyleHint.SansSerif)
        f.setPixelSize(100)
        found = _fonts[name] = (f, QFontMetricsF(f))
    return found


def text_path(t: Text, doc: Document) -> QPainterPath:
    """A line of text as a filled path, in drawing coordinates."""
    style = doc.text_styles.get(t.style)
    font, metrics = _font(style.font if style is not None else "Arial")
    raw = QPainterPath()
    raw.addText(0.0, 0.0, font, t.text)
    cap = metrics.capHeight() or 72.0
    k = t.height / cap
    width = metrics.horizontalAdvance(t.text) * k * t.width_factor
    h = t.height
    rot = t.rotation
    wf = t.width_factor
    if t.halign in (3, 5) and t.p2 is not None:
        d = t.p2 - t.p
        if d.length() > 0.0 and width > 0.0:
            rot = d.angle()
            fit = d.length() / width
            wf *= fit
            if t.halign == 3:
                k *= fit
                h *= fit
            width = d.length()
    dx = {0: 0.0, 1: -0.5, 2: -1.0, 3: 0.0, 4: -0.5, 5: 0.0}.get(t.halign, 0.0) * width
    dy = {0: 0.0, 1: -0.25 * h, 2: -0.5 * h, 3: -h}.get(t.valign if t.halign != 4 else 2, 0.0)
    shear = math.tan(t.oblique)
    m = QTransform()
    m.translate(t.p[0], t.p[1])
    m.rotateRadians(rot)
    m.translate(dx, dy)
    m.scale(-1.0 if t.mirrored_x else 1.0, -1.0 if t.mirrored_y else 1.0)
    # Qt's text is y-down and the drawing's is y-up
    m = QTransform(k * wf, 0.0, shear * k, -k, 0.0, 0.0) * m
    return m.map(raw)


def mtext_paths(t: MText, doc: Document) -> QPainterPath:
    """Paragraph text, wrapped to its width. Inline formatting (fonts and colours inside the string) isn't shown."""
    style = doc.text_styles.get(t.style)
    _, metrics = _font(style.font if style is not None else "Arial")
    cap = metrics.capHeight() or 72.0
    k = t.height / cap
    lines: list[str] = []
    for line in t.lines():
        if t.width > 0.0 and metrics.horizontalAdvance(line) * k > t.width:
            cur = ""
            for word in line.split(" "):
                test = f"{cur} {word}" if cur else word
                if metrics.horizontalAdvance(test) * k <= t.width or not cur:
                    cur = test
                else:
                    lines.append(cur)
                    cur = word
            lines.append(cur)
        else:
            lines.append(line)
    step = t.height * 1.667 * t.spacing
    total = t.height + step * (len(lines) - 1)
    row = (t.attach - 1) // 3
    col = (t.attach - 1) % 3
    top = {0: 0.0, 1: total * 0.5, 2: total}[row]
    out = QPainterPath()
    up = (-math.sin(t.rotation), math.cos(t.rotation))
    along = (math.cos(t.rotation), math.sin(t.rotation))
    for i, line in enumerate(lines):
        y = top - t.height - step * i
        x = {0: 0.0, 1: -0.5, 2: -1.0}[col] * (metrics.horizontalAdvance(line) * k)
        px = t.p[0] + along[0] * x + up[0] * y
        py = t.p[1] + along[1] * x + up[1] * y
        out.addPath(text_path(Text(type(t.p)(px, py), t.height, line, t.rotation, t.style), doc))
    return out


@dataclass(frozen=True, slots=True)
class _Wide:
    """A polyline with a width of its own, in drawing units, which is drawn as the area it covers."""
    line: Polyline

    def transformed(self, m: Affine) -> "_Wide":
        return _Wide(self.line.transformed(m))


def wide_path(s: Polyline) -> QPainterPath:
    """The area a wide polyline covers. A constant width has square ends and mitred corners. A changing width, as an arrowhead has, tapers along each piece."""
    if not s.widths:
        stroker = QPainterPathStroker()
        stroker.setWidth(s.width)
        stroker.setCapStyle(Qt.PenCapStyle.FlatCap)
        stroker.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        line = QPainterPath()
        s.emit(line)
        return stroker.createStroke(line)
    out = QPainterPath()
    out.setFillRule(Qt.FillRule.WindingFill)
    n = len(s.pts)
    box = s.bbox()
    chord = max(box[2] - box[0], box[3] - box[1], 1e-9) * 1e-3
    for i, seg in enumerate(s.segments):
        w0, w1 = s.widths[i % n]
        pts = seg.flatten(chord)
        count = len(pts) - 1
        if count < 1 or (w0 <= 0.0 and w1 <= 0.0):
            continue
        left: list[QPointF] = []
        right: list[QPointF] = []
        for j, p in enumerate(pts):
            a, b = pts[max(j - 1, 0)], pts[min(j + 1, count)]
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy) or 1.0
            half = (w0 + (w1 - w0) * j / count) * 0.5
            nx, ny = -dy / length * half, dx / length * half
            left.append(QPointF(p[0] + nx, p[1] + ny))
            right.append(QPointF(p[0] - nx, p[1] - ny))
        out.moveTo(left[0])
        for q in left[1:] + right[::-1]:
            out.lineTo(q)
        out.closeSubpath()
    return out


def _segment(a, b, path: QPainterPath, crisp: QPainterPath) -> None:
    """Horizontal and vertical lines go where they'll be drawn sharp."""
    dx, dy = abs(a[0] - b[0]), abs(a[1] - b[1])
    target = crisp if dx <= dy * 1e-6 or dy <= dx * 1e-6 else path
    target.moveTo(a[0], a[1])
    target.lineTo(b[0], b[1])


class Scene:
    def __init__(self, doc: Document, theme):
        self.doc = doc
        self.theme = theme
        self.chunks: dict[_Look, list[_Chunk]] = {}
        self._where: dict[int, list[_Chunk]] = {}
        self._paths: dict[int, tuple[Entity, QPainterPath]] = {}
        self._left = MOST_PIECES
        self._pieces = 0
        self.cut_short = 0      # how many objects have more to them than is being drawn
        self.rebuild()

    # ---------------------------------------------------------------------------- what an entity looks like

    def _effective(self, style: Style, parent: Style | None) -> Style:
        """Inside a block, things on layer 0 and things marked ByBlock take after the block's copy."""
        if parent is None:
            return style
        return Style(parent.layer if style.layer == "0" else style.layer,
                     parent.color if style.color == colors.BYBLOCK else style.color,
                     parent.linetype if style.linetype.casefold() == BYBLOCK_TYPE.casefold() else style.linetype,
                     parent.weight if style.weight == WEIGHT_BYBLOCK else style.weight, style.ltscale)

    def look_of(self, style: Style) -> _Look:
        doc = self.doc
        layer = doc.layers.get(style.layer)
        c = style.color
        if c == colors.BYLAYER or c == colors.BYBLOCK:
            c = layer.color if layer is not None else 7
        rgb = -1 if c == 7 else colors.rgb_of(c, 0)
        lt = style.linetype
        if lt.casefold() in (BYLAYER_TYPE.casefold(), BYBLOCK_TYPE.casefold()):
            lt = layer.linetype if layer is not None else "Continuous"
        kind = doc.linetypes.get(lt)
        scale = style.ltscale * doc.settings.ltscale
        pattern = tuple(abs(x) * scale for x in kind.pattern) if kind is not None and kind.pattern else ()
        w = style.weight
        if w in (WEIGHT_BYLAYER, WEIGHT_BYBLOCK):
            w = layer.weight if layer is not None else WEIGHT_DEFAULT
        if w == WEIGHT_DEFAULT or w < 0:
            w = DEFAULT_WEIGHT
        return _Look(rgb, pattern, w)

    def color_for(self, rgb: int) -> QColor:
        if rgb < 0:
            return QColor(self.theme.canvas["ink"])
        return QColor(self.theme.on_paper(rgb))

    # -------------------------------------------------------------------------------------- keeping up to date

    def rebuild(self) -> None:
        self.chunks.clear()
        self._where.clear()
        self._paths.clear()
        self._pieces = 0
        self.cut_short = 0
        for e in self.doc.sorted_entities():
            self._add(e)

    def set_theme(self, theme) -> None:
        self.theme = theme

    def apply(self, changes) -> None:
        """After a change to the drawing, redo only what it touched."""
        if any(c.table != "entity" for c in changes):
            self.rebuild()      # a layer or block changing can change how anything looks, and that's rare
            return
        for c in changes:
            self._drop(c.key)
            self._paths.pop(c.key, None)
            if c.new is not None:
                self._add(c.new)

    def _drop(self, eid: int) -> None:
        for chunk in self._where.pop(eid, ()):
            chunk.items.pop(eid, None)
            chunk.dirty = True

    def _add(self, e: Entity) -> None:
        if self.doc.is_hidden(e.style):
            return
        pieces: dict[_Look, list[tuple[object, Affine]]] = {}
        allowed = min(MOST_PIECES, MOST_IN_ALL - self._pieces)
        self._left = allowed
        self._walk(e.shape, e.style, None, IDENTITY, pieces, 0)
        self._pieces += allowed - max(self._left, 0)
        if self._left < 0:
            self.cut_short += 1
        box = self.doc.shape_bbox(e.shape)
        placed: list[_Chunk] = []
        for look, items in pieces.items():
            group = self.chunks.setdefault(look, [])
            if not group or len(group[-1].items) >= CHUNK:
                group.append(_Chunk(look))
            chunk = group[-1]
            chunk.items[e.id] = (items, box)
            chunk.dirty = True
            placed.append(chunk)
        self._where[e.id] = placed

    def _walk(self, s, style: Style, parent: Style | None, m: Affine, out: dict, depth: int) -> None:
        """What a shape draws, sorted by how it looks. A block's contents can each look different."""
        effective = self._effective(style, parent)
        k = type(s)
        if k is Foreign:
            # what's known of something from a file: its curves, and the block that draws the rest
            if s.curves:
                look = self.look_of(effective)
                out.setdefault(look, []).extend((c, m) for c in s.curves)
            if s.block:
                self._walk(Insert(s.block, s.at), style, parent, m, out, depth)
            return
        if k is Insert:
            if depth >= 16:
                return
            d = self.doc.blocks.get(s.block)
            if d is None:
                return
            moved = m is not IDENTITY
            look = self.look_of(effective)
            for a in s.attribs:
                if not a.invisible:
                    out.setdefault(_Look(look.rgb, (), 0, True), []).append((a.text, m))
            for col in range(s.columns):
                for row in range(s.rows):
                    local = s.matrix(d.base, col, row)
                    full = local.then(m) if moved else local
                    for inner in d.entities:
                        self._left -= 1
                        if self._left < 0:
                            return
                        if inner.style.layer != "0" and self.doc.is_hidden(inner.style):
                            continue
                        if type(inner.shape) is AttDef:
                            continue        # a definition: each copy shows its own values instead
                        self._walk(inner.shape, inner.style, effective, full, out, depth + 1)
            return
        look = self.look_of(effective)
        if k in (Text, MText, Solid, AttDef):
            look = _Look(look.rgb, (), 0, True)
        elif k is Polyline and (s.width or s.widths):
            out.setdefault(_Look(look.rgb, (), 0, True), []).append((_Wide(s), m))
            if s.widths and not all(a > 0.0 or b > 0.0 for a, b in s.widths):
                out.setdefault(look, []).append((s, m))     # parts of it have no width of their own, so they need the ordinary line too
            return
        out.setdefault(look, []).append((s, m))

    def _build(self, chunk: _Chunk) -> None:
        path = QPainterPath()
        if chunk.look.fill:
            path.setFillRule(Qt.FillRule.WindingFill)   # so two filled things that overlap don't cut a hole in each other
        # a dash pattern has to run on round corners, so patterned lines stay in one path
        crisp = QPainterPath() if not chunk.look.pattern and not chunk.look.fill else None
        points: list[QPointF] = []
        box: BBox | None = None
        for eid in sorted(chunk.items):
            items, b = chunk.items[eid]
            for s, m in items:
                self._emit(s, path, points, m, crisp)
            box = bbox_union(box, b)
        chunk.path = path
        chunk.crisp = crisp if crisp is not None and not crisp.isEmpty() else None
        chunk.points = points
        chunk.box = box
        chunk.dirty = False

    def _emit(self, s, path, points, m: Affine, crisp: QPainterPath | None = None) -> None:
        try:
            self._emit_placed(s, path, points, m, crisp)
        except (ValueError, ArithmeticError):
            pass        # a block's copy scaled to nothing has nothing to draw

    def _emit_placed(self, s, path, points, m: Affine, crisp: QPainterPath | None) -> None:
        k = type(s)
        moved = m is not IDENTITY
        if k is _Wide:
            path.addPath(wide_path((s.transformed(m) if moved else s).line))
        elif k is Line and crisp is not None:
            q = s.transformed(m) if moved else s
            _segment(q.a, q.b, path, crisp)
        elif k is Polyline and crisp is not None:
            q = s.transformed(m) if moved else s
            for seg in q.segments:
                if type(seg) is Line:
                    _segment(seg.a, seg.b, path, crisp)
                else:
                    seg.emit(path)
        elif k in _CURVES or k is Polyline:
            (s.transformed(m) if moved else s).emit(path)
        elif k is Node:
            p = m.apply(s.p) if moved else s.p
            points.append(QPointF(p[0], p[1]))
        elif k is Solid:
            q = s.transformed(m) if moved else s
            path.moveTo(q.pts[0][0], q.pts[0][1])
            for pt in q.pts[1:]:
                path.lineTo(pt[0], pt[1])
            path.closeSubpath()
        elif k is Text:
            path.addPath(text_path(s.transformed(m) if moved else s, self.doc))
        elif k is MText:
            path.addPath(mtext_paths(s.transformed(m) if moved else s, self.doc))
        elif k is AttDef:
            t = s.attrib.text
            path.addPath(text_path((t.transformed(m) if moved else t), self.doc))

    # ------------------------------------------------------------------------------------------------- painting

    def paint(self, p: QPainter, view, show_weights: bool = True) -> None:
        """Everything, under a transform from drawing to window."""
        m11, m12, m21, m22, dx, dy = view.matrix()
        p.setTransform(QTransform(m11, m12, m21, m22, dx, dy))
        x0, y0, x1, y1 = view.visible(4.0)
        scale = view.scale
        for look, group in self.chunks.items():
            if look.fill:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(self.color_for(look.rgb)))
            else:
                p.setPen(self._pen(look, scale, show_weights))
                p.setBrush(Qt.BrushStyle.NoBrush)
            for chunk in group:
                if chunk.dirty:
                    if not chunk.items:
                        continue
                    self._build(chunk)
                b = chunk.box
                if b is None or b[0] > x1 or b[2] < x0 or b[1] > y1 or b[3] < y0:
                    continue
                p.drawPath(chunk.path)
                if chunk.crisp is not None:
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                    p.drawPath(chunk.crisp)
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                if chunk.points:
                    p.drawPoints(chunk.points)
        p.resetTransform()

    def _pen(self, look: _Look, scale: float, show_weights: bool) -> QPen:
        pen = QPen(self.color_for(look.rgb))
        pen.setCosmetic(True)
        w = max(1.0, look.weight * 0.01 * PX_PER_MM) if show_weights else 1.0
        pen.setWidthF(w)
        # square ends, so a thick rectangle's separate sides still meet at its corners
        pen.setCapStyle(Qt.PenCapStyle.SquareCap if w > 1.5 and not look.pattern else Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if look.pattern:
            on_screen = [x * scale for x in look.pattern]
            # dashes too small to see would only read as a fainter solid line, and cost more to draw
            if max(on_screen) >= 3.0:
                dashes = [max(x, 0.5) / w for x in on_screen]
                if len(dashes) % 2:
                    dashes = dashes * 2
                pen.setDashPattern(dashes)
        return pen

    def path_of(self, eid: int) -> QPainterPath | None:
        """One entity's path, for highlighting it."""
        e = self.doc.entities.get(eid)
        if e is None:
            return None
        known = self._paths.get(eid)
        if known is not None and known[0] is e:
            return known[1]
        path = self.shape_path(e.shape, e.style)
        self._paths[eid] = (e, path)
        return path

    def shape_path(self, s, style: Style | None = None) -> QPainterPath:
        """What a shape draws as one path: for highlights, and for what's being drawn."""
        pieces: dict[_Look, list[tuple[object, Affine]]] = {}
        self._walk(s, style or Style(), None, IDENTITY, pieces, 0)
        path = QPainterPath()
        pts: list[QPointF] = []
        for items in pieces.values():
            for piece, m in items:
                self._emit(piece, path, pts, m)
        for q in pts:
            path.addEllipse(q, 0.001, 0.001)
        return path
