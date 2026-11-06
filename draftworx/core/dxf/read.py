# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading a DXF file into a drawing, and keeping everything that came with it."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import replace

from .. import color as colors
from .. import dim as dims
from ..curves import Arc, Circle, EllipseArc, Line, Polyline, Spline, spline_through
from ..model import BlockDef, DimStyle, Document, Entity, Layer, Linetype, Settings, Style, TextStyle
from ..native import FormatError, check_block_cycles
from ..rawsource import Raw
from ..hatch import known as known_pattern
from ..shapes import AttDef, Attrib, Dimension, Foreign, Hatch, Insert, MText, Node, PatternLine, Solid, Text
from ..vec import TAU, Vec, ccw_sweep
from .foreign import IN_PART, block_of, curves_of_foreign, hatch_paths
from .tags import Decoded, clean, decode, decode_text, fnum, header_state, inum, pairs

MOST_OBJECTS = 8_000_000
MOST_COORDINATE = 1e100     # further out than anything real, and near where arithmetic starts to run out
UNITS = {0: "none", 1: "in", 2: "ft", 4: "mm", 5: "cm", 6: "m"}
_LUNITS = {1: "scientific", 2: "decimal", 3: "engineering", 4: "architectural", 5: "fractional"}
#: What becomes something you can edit. Everything else is kept as text.
EDITABLE = {"LINE", "ARC", "CIRCLE", "ELLIPSE", "POINT", "LWPOLYLINE", "POLYLINE", "SPLINE", "TEXT", "MTEXT", "SOLID", "TRACE", "INSERT", "ATTDEF", "DIMENSION",
            "HATCH"}
_PLURALS = {"HATCH": "hatches", "3DSOLID": "3D solids", "3DFACE": "3D faces", "ACAD_TABLE": "tables", "MLEADER": "multileaders", "MULTILEADER": "multileaders",
            "ACAD_PROXY_ENTITY": "proxy objects", "ARC_DIMENSION": "arc dimensions", "LARGE_RADIAL_DIMENSION": "large radial dimensions", "XLINE": "construction lines",
            "RAY": "rays", "POLYLINE": "3D polylines and meshes"}
_SINGULARS = {"hatches": "hatch", "3D polylines and meshes": "3D polyline or mesh"}


def _plural(kind: str, n: int) -> str:
    """How many of something, in words a person uses."""
    word = _PLURALS.get(kind, kind.lower().replace("acad_", "").replace("_", " ") + "s")
    if n == 1:
        word = _SINGULARS.get(word, word[:-1] if word.endswith("s") else word)
    return f"{n} {word}"


def _sane(box) -> bool:
    return box is None or all(abs(v) < MOST_COORDINATE for v in box)



def _dimscale_override(tags: list) -> float | None:
    """A dimension's own DIMSCALE, from the overrides AutoCAD keeps in its extended data. The others stay in the file's text."""
    in_acad = in_dstyle = False
    i, n = 0, len(tags)
    while i < n:
        code, value = tags[i]
        if code == 1001:
            in_acad = value.strip() == "ACAD"
            in_dstyle = False
        elif in_acad and code == 1000 and value.strip() == "DSTYLE":
            in_dstyle = True
        elif in_dstyle and code == 1070 and value.strip() == "40" and i + 1 < n and tags[i + 1][0] == 1040:
            return fnum(tags[i + 1][1])
        i += 1
    return None

class _Reader:
    def __init__(self, d: Decoded):
        self.d = d
        self.lines = d.lines
        self.doc = Document()
        self.next_id = 1
        self.shown = Counter()      # kept as text, and drawn
        self.unseen = Counter()     # kept as text, and not drawn
        self.bad = 0
        self.raised = 0
        self.paper: list[list] = []
        self.styles: dict = {}
        self.order: list[str] = []
        self.frames: dict[str, list[str]] = {}
        self.eof = "  0\nEOF"
        self.no_eof = False

    # ------------------------------------------------------------------------------------------------- structure

    def sections(self) -> dict[str, tuple[int, int]]:
        """Where each section is. A section that's never closed means the file was cut off, and nothing is read from it."""
        lines = self.lines
        n = len(lines)
        i = 0
        out: dict[str, tuple[int, int]] = {}
        between: list[str] = []
        ended = False
        while i + 1 < n:
            code = lines[i].strip()
            value = lines[i + 1].strip()
            if code == "0" and value == "SECTION":
                if i + 3 >= n or lines[i + 2].strip() != "2":
                    raise FormatError("This DXF file is damaged: a section has no name.")
                name = lines[i + 3].strip().upper()
                start = i + 4
                j = start
                while j + 1 < n and not ("ENDSEC" in lines[j + 1] and lines[j].strip() == "0" and lines[j + 1].strip() == "ENDSEC"):
                    j += 2
                if j + 1 >= n:
                    raise FormatError(f"This DXF file is cut off: its {name} section never ends. The file hasn't been changed.")
                if name in out:
                    raise FormatError(f"This DXF file is damaged: it has two {name} sections.")
                if name in ("TABLES", "BLOCKS", "ENTITIES"):
                    # a comment before the first thing in a section belongs with its opening
                    while start < j and lines[start].strip() != "0":
                        start += 2
                out[name] = (start, j)
                self.order.append(name)
                self.frames[name] = ["\n".join(between + lines[i:start]), "\n".join(lines[j:j + 2])]
                between = []
                i = j + 2
            elif code == "0" and value == "EOF":
                self.eof = "\n".join(between + lines[i:n])
                ended = True
                break
            elif code == "999":
                between += lines[i:i + 2]
                i += 2
            else:
                raise FormatError(f"This isn't a DXF file, or it's damaged at line {i + 1} ({lines[i].strip()[:40]!r}).")
        if not out:
            raise FormatError("This isn't a DXF file: it has no sections.")
        if not ended:
            # every section was closed, so nothing has been lost, and some programs do leave this off
            self.no_eof = True
            self.eof = "\n".join(between)
        return out

    def raw(self, start: int, end: int) -> str:
        return "\n".join(self.lines[start:end])

    def spans(self, start: int, end: int):
        """Each thing in a stretch of a file: (its type, first line, line after its last)."""
        lines = self.lines
        here = None
        kind = ""
        for i in range(start, end, 2):
            c = lines[i]
            if c == "  0" or c.strip() == "0":
                if here is not None:
                    yield kind, here, i
                here = i
                kind = lines[i + 1].strip() if i + 1 < end else ""
        if here is not None:
            yield kind, here, end

    def most_handle(self, seed: int) -> int:
        """Every handle in the file, because a new one mustn't match anything, and not every program keeps its seed right."""
        lines = self.lines
        top = seed
        for i in range(0, len(lines) - 1, 2):
            c = lines[i]
            if c == "  5" or c == "105" or c == "5" or c == "  105":
                try:
                    v = int(lines[i + 1], 16)
                except ValueError:
                    continue
                if v > top:
                    top = v
        return top

    # ---------------------------------------------------------------------------------------------------- header

    def header(self, start: int, end: int) -> dict[str, list[tuple[int, str]]]:
        out: dict[str, list[tuple[int, str]]] = {}
        name = None
        for code, value in pairs(self.lines, start, end):
            if code == 9:
                name = value.strip().upper()
                out[name] = []
            elif name is not None:
                out[name].append((code, value))
        return out

    def apply_header(self, h: dict) -> None:
        doc = self.doc

        def first(name: str, default: str = "") -> str:
            v = h.get(name)
            return v[0][1] if v else default

        units = UNITS.get(inum(first("$INSUNITS", "-1"), -1))
        if units is None:
            # older files don't say, except whether they're metric or imperial
            units = "none" if "$MEASUREMENT" not in h else ("in" if inum(first("$MEASUREMENT")) == 0 else "mm")
        scale = fnum(first("$DIMSCALE", "1"), 1.0)
        doc.settings = Settings(units, _LUNITS.get(inum(first("$LUNITS", "2")), "decimal"), max(0, min(15, inum(first("$LUPREC", "4"), 4))),
                                max(0, min(15, inum(first("$AUPREC", "0")))), scale if scale > 0.0 else 1.0, fnum(first("$LTSCALE", "1"), 1.0) or 1.0,
                                inum(first("$PDMODE")), fnum(first("$PDSIZE")))
        c = doc.current
        c.layer = clean(first("$CLAYER", "0")).strip() or "0"
        color = inum(first("$CECOLOR", "256"), 256)
        c.color = color if colors.valid(color) else colors.BYLAYER
        c.linetype = clean(first("$CELTYPE", "ByLayer")).strip() or "ByLayer"
        w = inum(first("$CELWEIGHT", "-1"), -1)
        c.weight = w if -3 <= w <= 211 else -1
        c.text_style = clean(first("$TEXTSTYLE", "Standard")).strip() or "Standard"
        c.dim_style = clean(first("$DIMSTYLE", "Standard")).strip() or "Standard"

    # ---------------------------------------------------------------------------------------------------- tables

    def tables(self, start: int, end: int) -> dict:
        """The tables we understand become the drawing's tables. The rest stay as text."""
        lines = self.lines
        out: dict = {"order": [], "raw": {}, "heads": {}, "tails": {}, "entries": {}, "counts": {}}

        def keep(a: int, b: int) -> None:
            """What a reader can't use, such as a second table of the same name or records outside any table, still isn't ours to drop."""
            if b > a:
                out["order"].append(f"raw:{len(out['order'])}")
                out["raw"][out["order"][-1]] = self.raw(a, b)
        i = start
        stray = start
        while i + 1 < end:
            if lines[i].strip() == "0" and lines[i + 1].strip() == "TABLE":
                keep(stray, i)
                name = lines[i + 3].strip().upper() if i + 3 < end else ""
                j = i + 2
                while j + 1 < end and not (lines[j].strip() == "0" and lines[j + 1].strip() == "ENDTAB"):
                    j += 2
                stop = min(j + 2, end)
                if name in out["order"] or not name:
                    keep(i, stop)
                else:
                    out["order"].append(name)
                    self._table(name, i, j, stop, out)
                i = stray = stop
            else:
                i += 2
        keep(stray, end)
        return out

    def _table(self, name: str, start: int, end: int, stop: int, out: dict) -> None:
        readers = {"LAYER": (self._layer, self.doc.layers), "LTYPE": (self._linetype, self.doc.linetypes),
                   "STYLE": (self._text_style, self.doc.text_styles), "DIMSTYLE": (self._dim_style, self.doc.dim_styles)}
        if name not in readers:
            out["raw"][name] = self.raw(start, stop)
            return
        reader, table = readers[name]
        entries: list = []
        seen: set[str] = set()
        head_end = start
        count = 0
        for kind, a, b in self.spans(start, end):
            if a == start:
                head_end = b
                continue
            count += 1
            text = self.raw(a, b)
            value = None
            if kind == name:
                try:
                    value = reader(pairs(self.lines, a, b))
                except (ValueError, TypeError):
                    self.bad += 1
            key = value.name.casefold() if value is not None else None
            if value is None or key in seen:
                entries.append({"raw": text})
                continue
            seen.add(key)
            table._put(replace(value, source=Raw(text, value)))
            entries.append(key)
        out["heads"][name] = self.raw(start, head_end)
        out["tails"][name] = self.raw(end, stop)
        out["entries"][name] = entries
        out["counts"][name] = count

    def _layer(self, tags) -> Layer | None:
        d = dict(reversed(tags))
        name = clean(d.get(2, "")).strip()
        if not name:
            return None
        flags = inum(d.get(70, "0"))
        color = inum(d.get(62, "7"), 7)
        on = color >= 0
        color = abs(color) or 7
        if color > 255:
            color = 7
        if 420 in d:
            color = colors.TRUE | (inum(d[420]) & 0xffffff)
        w = inum(d.get(370, "-3"), -3)
        return Layer(name, color, clean(d.get(6, "Continuous")).strip() or "Continuous", w if -3 <= w <= 211 else -3, on, bool(flags & 1), bool(flags & 4), inum(d.get(290, "1"), 1) != 0)

    def _linetype(self, tags) -> Linetype | None:
        name = next((clean(v).strip() for c, v in tags if c == 2), "")
        if not name:
            return None
        return Linetype(name, next((decode_text(v) for c, v in tags if c == 3), ""), tuple(fnum(v) for c, v in tags if c == 49))

    def _text_style(self, tags) -> TextStyle | None:
        d = dict(reversed(tags))
        name = clean(d.get(2, "")).strip()
        if not name or inum(d.get(70, "0")) & 1:      # flag 1 is a shape file, not a text style
            return None
        return TextStyle(name, clean(d.get(3, "")) or "Arial", fnum(d.get(40, "0")), fnum(d.get(41, "1"), 1.0) or 1.0, math.radians(fnum(d.get(50, "0"))))

    def _dim_style(self, tags) -> DimStyle | None:
        d = dict(reversed(tags))
        name = clean(d.get(2, "")).strip()
        if not name:
            return None
        base = DimStyle(name)
        return replace(base, text_height=fnum(d.get(140, ""), base.text_height), arrow_size=fnum(d.get(41, ""), base.arrow_size),
                       ext_offset=fnum(d.get(42, ""), base.ext_offset), ext_beyond=fnum(d.get(44, ""), base.ext_beyond),
                       text_gap=fnum(d.get(147, ""), base.text_gap), baseline_step=fnum(d.get(43, ""), base.baseline_step),
                       scale=fnum(d.get(40, ""), base.scale), linear_factor=fnum(d.get(144, ""), base.linear_factor),
                       decimals=inum(d.get(271, ""), base.decimals), angle_decimals=inum(d.get(179, ""), base.angle_decimals),
                       suppress_zeros=inum(d.get(78, ""), base.suppress_zeros), text_above=inum(d.get(77, ""), base.text_above),
                       text_inside_horizontal=inum(d.get(73, "0")) != 0, text_outside_horizontal=inum(d.get(74, "0")) != 0,
                       unit_format=inum(d.get(277, ""), base.unit_format), prefix_suffix=clean(d.get(3, "")), tick_size=fnum(d.get(142, ""), base.tick_size),
                       center_mark=fnum(d.get(141, ""), base.center_mark))

    # ---------------------------------------------------------------------------------------------------- objects

    def _style(self, d: dict) -> Style:
        key = (d.get(8), d.get(62), d.get(6), d.get(370), d.get(48), d.get(420))
        found = self.styles.get(key)
        if found is not None:
            return found
        c = inum(d.get(62, "256"), 256)
        if 420 in d:
            c = colors.TRUE | (inum(d[420]) & 0xffffff)
        elif not 0 <= c <= 256:
            c = abs(c) if abs(c) <= 255 else colors.BYLAYER
        w = inum(d.get(370, "-1"), -1)
        found = self.styles[key] = Style(clean(d.get(8, "0")).strip() or "0", c, clean(d.get(6, "ByLayer")).strip() or "ByLayer", w if -3 <= w <= 211 else -1, fnum(d.get(48, "1"), 1.0) or 1.0)
        if found.layer not in self.doc.layers:
            self.doc.layers._put(Layer(found.layer))
        return found

    def _flipped(self, d: dict) -> int:
        """Which way an object's own coordinates face. +1 is up, -1 is seen from behind, as a mirrored object often is, and 0 is tilted out of the plane."""
        if 230 not in d and 210 not in d and 220 not in d:
            return 1
        x, y, z = fnum(d.get(210, "0")), fnum(d.get(220, "0")), fnum(d.get(230, "1"), 1.0)
        if abs(x) > 1e-9 or abs(y) > 1e-9 or abs(abs(z) - 1.0) > 1e-9:
            return 0
        return 1 if z > 0.0 else -1

    def _vec(self, d: dict, a: int = 10, sign: float = 1.0) -> Vec:
        return Vec(sign * fnum(d.get(a, "0")), fnum(d.get(a + 10, "0")))

    def shape(self, kind: str, tags: list, d: dict):
        """What an object is, or None if it isn't something to edit here. Raises if its numbers make no sense."""
        # in the drawing's own coordinates, whichever way they face
        if kind == "LINE":
            return Line(self._vec(d), self._vec(d, 11))
        if kind == "POINT":
            return Node(self._vec(d))
        if kind == "SPLINE":
            return self._spline(tags, d)
        if kind == "MTEXT":
            return None if self._flipped(d) == 0 else self._mtext(tags, d)
        if kind == "DIMENSION":
            return self._dimension(tags, d)
        if kind == "HATCH":
            return self._hatch(tags, d)
        flip = self._flipped(d)
        if flip == 0:
            return None
        if kind == "ELLIPSE":
            ratio = fnum(d.get(40, "1"), 1.0)
            major = self._vec(d, 11)
            if major == (0.0, 0.0) or not 0.0 < ratio <= 1.0:
                raise ValueError("no size")
            s, e = fnum(d.get(41, "0")), fnum(d.get(42, str(TAU)), TAU)
            sweep = ccw_sweep(s, e)
            # seen from behind, its minor axis points the other way, which reverses its own angle
            return EllipseArc(self._vec(d), major, ratio, -s, -sweep) if flip < 0 else EllipseArc(self._vec(d), major, ratio, s, sweep)
        # the rest have their own coordinates
        sign = float(flip)
        if kind == "CIRCLE":
            r = fnum(d.get(40, "0"))
            if r <= 0.0:
                raise ValueError("no size")
            return Circle(self._vec(d, 10, sign), r)
        if kind == "ARC":
            r = fnum(d.get(40, "0"))
            if r <= 0.0:
                raise ValueError("no size")
            a0, a1 = math.radians(fnum(d.get(50, "0"))), math.radians(fnum(d.get(51, "0")))
            sweep = ccw_sweep(a0, a1)
            if flip < 0:
                return Arc(self._vec(d, 10, sign), r, math.pi - a1, sweep)
            return Arc(self._vec(d), r, a0, sweep)
        if kind == "LWPOLYLINE":
            return self._lwpolyline(tags, d, sign)
        if kind == "SOLID" or kind == "TRACE":
            third = self._vec(d, 12, sign)
            fourth = self._vec(d, 13, sign) if 13 in d else third
            pts = [self._vec(d, 10, sign), self._vec(d, 11, sign), fourth, third]    # a file's order for its corners is a Z
            if fourth == third:
                pts.pop()
            return Solid(tuple(pts))
        if kind == "TEXT":
            return self._text(d, sign, 73)
        if kind == "ATTDEF":
            flags = inum(d.get(70, "0"))
            return AttDef(Attrib(clean(d.get(2, "")).strip(), self._text(d, sign, 74), decode_text(d.get(3, "")), bool(flags & 1), bool(flags & 2)))
        return None

    def _dimension(self, tags: list, d: dict) -> Dimension | None:
        """A dimension, as one that can be changed. Its block still draws it while it's unchanged. None, so it stays as the file had it, for an ordinate
        dimension, one that isn't flat, or one placed somewhere other than where its block is."""
        if self._flipped(d) != 1 or fnum(d.get(12, "0")) or fnum(d.get(22, "0")):
            return None
        flags = inum(d.get(70, "0"))
        kind = flags & 7
        style = clean(d.get(3, "Standard")).strip() or "Standard"
        text = decode_text(d.get(1, ""))
        text = "" if text == "<>" else text
        block = clean(d.get(2, "")).strip()

        def p(code: int) -> Vec:
            return self._vec(d, code)
        text_at = p(11) if flags & 128 and 11 in d else None
        if kind == 0 or kind == 1:
            measure = "linear" if kind == 0 else "aligned"
            shape = Dimension(measure, (p(13), p(14)), p(10), math.radians(fnum(d.get(50, "0"))) if kind == 0 else 0.0, text, style, 1.0, text_at)
        elif kind == 2:
            shape = Dimension("angular", (p(13), p(14), p(15), p(10)), p(16), 0.0, text, style, 1.0, text_at)
        elif kind == 5:
            shape = Dimension("angular", (p(15), p(13), p(14)), p(10), 0.0, text, style, 1.0, text_at)
        elif kind == 4 or kind == 3:
            c, edge = (p(10), p(15)) if kind == 4 else (p(10).midpoint(p(15)), p(15))
            shape = Dimension("radius" if kind == 4 else "diameter", (c, edge), edge, 0.0, text, style, 1.0, text_at)
        else:
            return None
        override = _dimscale_override(tags)
        if override is not None and override > 0.0:
            own = self.doc.dim_styles.get(style)
            base = own.scale if own is not None and own.scale > 0.0 else 1.0
            shape = replace(shape, scale=override / base)
        if shape.measure in ("radius", "diameter") and 11 in d:
            shape = replace(shape, at=self._radial_at(shape, p(11)))
        return replace(shape, block=block)

    def _hatch(self, tags: list, d: dict) -> Hatch | None:
        """A hatch that can be changed: its boundary paths, its pattern as the file defines it, and the points it was made from. None, so it
        stays as the file had it, for a gradient, one tilted out of the plane, or a pattern the file doesn't define."""
        flip = self._flipped(d)
        if flip == 0:
            return None
        loops, t = hatch_paths(tags, flip)
        if not loops:
            return None
        solid = bool(inum(d.get(70, "0")) & 1)
        name = clean(d.get(2, "")).strip()
        angle, scale = 0.0, 1.0
        world: list[tuple[float, Vec, Vec, tuple[float, ...]]] = []
        seeds: list[Vec] = []
        while t.code() is not None:
            code = t.code()
            if code == 52:
                angle = math.radians(fnum(t.take(52)))
            elif code == 41:
                scale = fnum(t.take(41), 1.0)
            elif code == 78:
                for _ in range(min(inum(t.take(78)), 10_000)):
                    if t.code() != 53:
                        break
                    a = math.radians(fnum(t.take(53)))
                    base = Vec(fnum(t.take(43)), fnum(t.take(44)))
                    step = Vec(fnum(t.take(45)), fnum(t.take(46)))
                    dashes = tuple(fnum(t.take(49)) for _ in range(min(inum(t.take(79)), 1000)) if t.code() == 49)
                    world.append((a, base, step, dashes))
            elif code == 98:
                for _ in range(min(inum(t.take(98)), 10_000)):
                    if t.code() != 10:
                        break
                    seeds.append(t.point(10))
            elif code == 450:
                if inum(t.take(450)) == 1:
                    return None         # a gradient
            elif code == 1001:
                break
            else:
                t.i += 1
        if flip < 0:
            angle = math.pi - angle
            world = [(math.pi - a, Vec(-b[0], b[1]), Vec(-st[0], st[1]), ds) for a, b, st, ds in world]
            seeds = [Vec(-p[0], p[1]) for p in seeds]
        if not (scale > 0.0 and math.isfinite(scale)):
            scale = 1.0
        if solid:
            return Hatch(tuple(loops), "SOLID", scale, angle, Vec(0.0, 0.0), tuple(seeds))
        if not world and known_pattern(name) is None:
            return None
        lines = []
        for a, base, step, dashes in world:
            u = Vec(math.cos(a), math.sin(a))
            lines.append(PatternLine(a - angle, base.rotated(-angle) * (1.0 / scale), step.dot(u) / scale, step.dot(u.perp()) / scale,
                                     tuple(x / scale for x in dashes)))
        return Hatch(tuple(loops), name or "USER", scale, angle % math.tau, Vec(0.0, 0.0), tuple(seeds), (), tuple(lines))

    def _radial_at(self, d: Dimension, middle: Vec) -> Vec:
        """Where a radius or diameter goes, from where its text's middle is: what a file holds. Outside the curve its line runs out to just under the
        text. Inside, all that matters is which way it points, which is toward its point on the curve."""
        c, edge = d.pts
        r = c.distance(edge)
        toward = (edge - c).unit() if edge != c else Vec(1.0, 0.0)
        if d.text_at is not None or c.distance(middle) <= r:
            return middle if d.text_at is not None and c.distance(middle) > r else c + toward * (r * 0.5)
        sizes = dims.sizes(d, self.doc.dim_style_of(d))
        rotation = dims.readable(toward)
        up = Vec(-math.sin(rotation), math.cos(rotation))
        return middle - up * (sizes["gap"] + sizes["text"] * 0.5)

    def _text(self, d: dict, sign: float, valign_code: int) -> Text:
        h = fnum(d.get(40, "0"))
        if h <= 0.0:
            h = 1.0
        halign, valign = inum(d.get(72, "0")), inum(d.get(valign_code, "0"))
        if not 0 <= halign <= 5:
            halign = 0
        if not 0 <= valign <= 3:
            valign = 0
        p = self._vec(d, 10, sign)
        p2 = None
        if (halign or valign) and 11 in d:
            align = self._vec(d, 11, sign)
            if halign == 3 or halign == 5:
                p2 = align
            else:
                p = align
        rot = math.radians(fnum(d.get(50, "0")))
        if sign < 0.0:
            rot = math.pi - rot
        flags = inum(d.get(71, "0"))
        wf = fnum(d.get(41, "1"), 1.0)
        return Text(p, h, decode_text(d.get(1, "")).replace("\n", " "), rot, clean(d.get(7, "Standard")).strip() or "Standard", halign, valign, wf if wf > 0.0 else 1.0,
                    math.radians(fnum(d.get(51, "0"))), p2, bool(flags & 2) != (sign < 0.0), bool(flags & 4))

    def _mtext(self, tags, d: dict) -> MText:
        """Its position and direction are in the drawing's own coordinates. Only its first subclass is the text: what can follow it repeats the same codes."""
        chunks: list[str] = []
        last = ""
        for c, v in tags:
            if c == 3:
                chunks.append(v)
            elif c == 1:
                last = v
                break
        text = decode_text("".join(chunks) + last).replace("\n", "\\P")
        h = fnum(d.get(40, "1"), 1.0)
        rot = math.radians(fnum(d.get(50, "0")))
        if 11 in d:
            x, y = fnum(d.get(11, "1")), fnum(d.get(21, "0"))
            if x or y:
                rot = math.atan2(y, x)
        attach = inum(d.get(71, "1"), 1)
        spacing = fnum(d.get(44, "1"), 1.0)
        return MText(self._vec(d), h if h > 0.0 else 1.0, text, fnum(d.get(41, "0")), rot, attach if 1 <= attach <= 9 else 1,
                     clean(d.get(7, "Standard")).strip() or "Standard", spacing if spacing > 0.0 else 1.0)

    def _lwpolyline(self, tags, d: dict, sign: float) -> Polyline:
        pts: list[Vec] = []
        bulges: list[float] = []
        widths: list[tuple[float, float]] = []
        x = None
        for c, v in tags:
            if c == 10:
                x = fnum(v)
            elif c == 20:
                if x is not None:
                    pts.append(Vec(sign * x, fnum(v)))
                    bulges.append(0.0)
                    widths.append((0.0, 0.0))
                    x = None
            elif c == 42:
                if bulges:
                    bulges[-1] = sign * fnum(v)
            elif c == 40:
                if widths:
                    widths[-1] = (fnum(v), widths[-1][1])
            elif c == 41:
                if widths:
                    widths[-1] = (widths[-1][0], fnum(v))
        if not pts:
            raise ValueError("no points")
        vary = any(a or b for a, b in widths)
        return Polyline(tuple(pts), tuple(bulges), bool(inum(d.get(70, "0")) & 1), fnum(d.get(43, "0")), tuple(widths) if vary else ())

    def _spline(self, tags, d: dict) -> Spline:
        knots = tuple(fnum(v) for c, v in tags if c == 40)
        weights = tuple(fnum(v) for c, v in tags if c == 41)
        ctrl: list[Vec] = []
        fit: list[Vec] = []
        x = None
        target = ctrl
        for c, v in tags:
            if c == 10 or c == 11:
                x = fnum(v)
                target = ctrl if c == 10 else fit
            elif (c == 20 or c == 21) and x is not None:
                target.append(Vec(x, fnum(v)))
                x = None
        flags = inum(d.get(70, "0"))
        degree = inum(d.get(71, "3"), 3)
        if not 1 <= degree <= 25:
            raise ValueError("no such degree")
        if not ctrl and fit:
            # a file may give only the points it goes through, and leave the rest to the reader
            start = self._vec(d, 12) if 12 in d else None
            end = self._vec(d, 13) if 13 in d else None
            if flags & 1 and fit[0] != fit[-1]:
                fit.append(fit[0])
            return spline_through(fit, start, end)
        return Spline(degree, knots, tuple(ctrl), weights if flags & 4 and len(weights) == len(ctrl) else (), tuple(fit), bool(flags & 2))

    def _polyline(self, vertices: list[dict], d: dict):
        """An old-style polyline: a heading, then a VERTEX for each point. None if it isn't flat."""
        flags = inum(d.get(70, "0"))
        if flags & (8 | 16 | 64):       # a 3D polyline, a mesh, a polyface
            return None
        flip = self._flipped(d)
        if flip == 0:
            return None
        sign = float(flip)
        pts: list[Vec] = []
        bulges: list[float] = []
        widths: list[tuple[float, float]] = []
        for vd in vertices:
            if inum(vd.get(70, "0")) & 16:      # a spline's control point, which isn't on the curve
                continue
            pts.append(self._vec(vd, 10, sign))
            bulges.append(sign * fnum(vd.get(42, "0")))
            widths.append((fnum(vd.get(40, d.get(40, "0"))), fnum(vd.get(41, d.get(41, "0")))))
        if not pts:
            raise ValueError("no points")
        vary = any(a != widths[0][0] or b != widths[0][0] for a, b in widths)
        return Polyline(tuple(pts), tuple(bulges), bool(flags & 1), widths[0][0] if not vary else 0.0, tuple(widths) if vary else ())

    def _insert(self, d: dict, attribs: list):
        flip = self._flipped(d)
        if flip == 0:
            return None
        name = clean(d.get(2, "")).strip()
        if not name:
            raise ValueError("no block")
        p = self._vec(d, 10, float(flip))
        sx, sy = fnum(d.get(41, "1"), 1.0), fnum(d.get(42, "1"), 1.0)
        rot = math.radians(fnum(d.get(50, "0")))
        cols, rows = max(1, inum(d.get(70, "1"), 1)), max(1, inum(d.get(71, "1"), 1))
        cs, rs = fnum(d.get(44, "0")), fnum(d.get(45, "0"))
        if flip < 0:
            sx, rot, cs = -sx, -rot, -cs
        return Insert(name, p, sx, sy, rot, tuple(attribs), min(cols, 100_000), min(rows, 100_000), cs, rs)

    def _attribs(self, members: list) -> list[Attrib]:
        out = []
        for _, a, b in members:
            tags = pairs(self.lines, a, b)
            ad = dict(reversed(tags))
            flip = self._flipped(ad)
            if flip == 0:
                continue
            flags = inum(ad.get(70, "0"))
            out.append(Attrib(clean(ad.get(2, "")).strip(), self._text(ad, float(flip), 74), "", bool(flags & 1), bool(flags & 2)))
        return out

    def entities(self, start: int, end: int, into: list[Entity] | None) -> None:
        """Read a stretch of objects. `into` is a block's list, or None for the drawing."""
        spans = list(self.spans(start, end))
        n = len(spans)
        i = 0
        doc = self.doc
        lines = self.lines
        while i < n:
            kind, a, b = spans[i]
            i += 1
            tags = pairs(lines, a, b)
            d = dict(reversed(tags))
            members: list[tuple[str, int, int]] = []
            # these own the objects that follow them, up to a SEQEND, which a damaged file may not have
            if kind == "POLYLINE" or (kind == "INSERT" and inum(d.get(66, "0")) == 1):
                want = "VERTEX" if kind == "POLYLINE" else "ATTRIB"
                while i < n:
                    k2, a2, b2 = spans[i]
                    if k2 == want:
                        members.append((k2, a2, b2))
                        b = b2
                        i += 1
                    elif k2 == "SEQEND":
                        b = b2
                        i += 1
                        break
                    else:
                        break
            text = self.raw(a, b)
            if into is None and d.get(67, "").strip() == "1":
                self.paper.append([self.next_id, text])
                self.next_id += 1
                continue
            style = self._style(d)
            shape = self._read_shape(kind, tags, d, members)
            eid = self.next_id
            self.next_id += 1
            e = Entity(eid, shape, style, eid, Raw(text, shape, style))
            if into is not None:
                into.append(e)
            else:
                doc._set("entity", eid, None, e)
            if eid > MOST_OBJECTS:
                raise FormatError(f"This DXF file has more than {MOST_OBJECTS} objects, which is more than can be opened.")

    def _read_shape(self, kind: str, tags, d: dict, members: list):
        vertices = None
        try:
            if kind == "POLYLINE":
                vertices = [dict(reversed(pairs(self.lines, a, b))) for k, a, b in members]
                shape = self._polyline(vertices, d)
            elif kind == "INSERT":
                shape = self._insert(d, self._attribs(members))
            elif kind in EDITABLE:
                shape = self.shape(kind, tags, d)
            else:
                return self._foreign(kind, tags, d, members)
            if shape is None:
                return self._foreign(kind, tags, d, members, vertices)
            if type(shape) is not Insert:
                if not _sane(self.doc.shape_bbox(shape)):
                    raise ValueError("too far out")
            elif not _sane((shape.p[0], shape.p[1], shape.sx, shape.sy, shape.column_spacing, shape.row_spacing)):
                raise ValueError("too far out")
        except (ValueError, TypeError, ArithmeticError):
            self.bad += 1
            return Foreign(kind)
        if (30 in d and fnum(d[30]) != 0.0) or (31 in d and fnum(d[31]) != 0.0) or (38 in d and fnum(d[38]) != 0.0):
            self.raised += 1
        return shape

    def _foreign(self, kind: str, tags, d: dict, members: list, vertices: list | None = None) -> Foreign:
        if vertices is None:
            vertices = [dict(reversed(pairs(self.lines, a, b))) for k, a, b in members] if kind == "POLYLINE" else []
        curves = curves_of_foreign(kind, tags, d, self._flipped(d), vertices)
        block, at = block_of(kind, d)
        s = Foreign(kind, block, at, curves)
        if not _sane(s.bbox()) or not _sane(at):
            s = Foreign(kind)
        (self.shown if s.curves or s.block else self.unseen)[kind] += 1
        return s

    # ---------------------------------------------------------------------------------------------------- blocks

    def blocks(self, start: int, end: int) -> list:
        """Blocks become the drawing's blocks. Those of paper space and model space stay as text, along with anything else that isn't one."""
        spans = list(self.spans(start, end))
        items: list = []
        seen: set[str] = set()
        i = 0
        while i < len(spans):
            kind, a, b = spans[i]
            if kind != "BLOCK":
                items.append({"raw": self.raw(a, b)})
                i += 1
                continue
            j = i + 1
            while j < len(spans) and spans[j][0] != "ENDBLK":
                j += 1
            closed = j < len(spans)
            stop = spans[j][2] if closed else end
            head = dict(reversed(pairs(self.lines, a, b)))
            name = clean(head.get(2, "")).strip()
            key = name.casefold()
            if not name or key in seen or key.startswith(("*paper_space", "$paper_space", "*model_space", "$model_space")):
                items.append({"raw": self.raw(a, stop)})
            else:
                seen.add(key)
                members: list[Entity] = []
                self.entities(b, spans[j][1] if closed else end, members)
                flags = inum(head.get(70, "0"))
                value = BlockDef(name, self._vec(head), (), decode_text(head.get(4, "")), flags)
                text = self.raw(a, b) + ("\n" + self.raw(spans[j][1], stop) if closed else "")
                self.doc.blocks._put(replace(value, entities=tuple(members), source=Raw(text, value)))
                items.append({"block": key})
            i = j + 1
        return items


def _notices(r: _Reader, doc: Document) -> list[str]:
    out: list[str] = []
    in_part = [k for k in r.shown if k in IN_PART]
    if r.shown:
        listed = ", ".join(_plural(k, n) for k, n in sorted(r.shown.items()))
        out.append(f"Shown, but can't be changed here yet: {listed}. They'll be saved back exactly as they were.")
    for k in sorted(in_part):
        out.append(f"{_PLURALS.get(k, k.lower() + 's').capitalize()} are shown by {IN_PART[k]} only.")
    if r.unseen:
        listed = ", ".join(_plural(k, n) for k, n in sorted(r.unseen.items()))
        out.append(f"Not shown, and can't be changed here: {listed}. They'll be saved back exactly as they were.")
    if r.bad:
        out.append(f"{r.bad} object{'s' if r.bad != 1 else ''} made no sense and can't be shown. They'll be saved back exactly as they were.")
    if r.raised:
        out.append(f"{r.raised} object{'s have' if r.raised != 1 else ' has'} a height, and DraftWorx is flat. They're shown from above and keep their heights unless you change them.")
    if r.paper or any(isinstance(item, dict) and _is_paper_with_content(item) for item in doc.extras["dxf"].get("blocks", ())):
        out.append("This file has paper space layouts. They're kept and saved back, but aren't shown here.")
    xrefs = [b.name for b in doc.blocks if b.flags & 4]
    if xrefs:
        out.append(f"This file refers to {len(xrefs)} other drawing{'s' if len(xrefs) != 1 else ''}, which {'aren' if len(xrefs) != 1 else 'isn'}'t shown here: {', '.join(xrefs[:3])}.")
    if r.no_eof:
        out.append("This file has no end marker. Every section in it is complete, so it's probably all there, but check it against whatever it came from.")
    if r.d.binary:
        out.append("This is a binary DXF. Saving it writes the same drawing as text DXF.")
    if r.d.version > "AC1032":
        out.append(f"This file's version ({r.d.version}) is newer than DraftWorx knows. What it doesn't recognise is kept as it was.")
    return out


def _is_paper_with_content(item: dict) -> bool:
    """A kept paper space block with something in it. An empty one is in every file, and isn't worth a mention."""
    text = item.get("raw", "")
    lines = text.split("\n")
    if len(lines) < 4 or "paper_space" not in text[:400].casefold():
        return False
    return sum(1 for i in range(0, len(lines) - 1, 2) if lines[i].strip() == "0") > 2


def read_dxf(data: bytes) -> tuple[Document, list[str]]:
    """A drawing from the bytes of a DXF file, and what the person should know about it.

    Raises FormatError if it can't be read, so nothing half-loads. A failure here is a message, whatever caused it."""
    if data.startswith((b"AC10", b"AC1.", b"AC2.", b"MC0.")):
        raise FormatError("This is a DWG file, and DraftWorx doesn't read DWG. Save it as DXF in the program that made it.")
    try:
        return read_strict(data)
    except FormatError:
        raise
    except MemoryError as exc:
        raise FormatError("This DXF file is too large to open on this computer.") from exc
    except Exception as exc:
        raise FormatError(f"This DXF file couldn't be read: it may be damaged ({type(exc).__name__}: {exc}). The file hasn't been changed.") from exc


def read_strict(data: bytes) -> tuple[Document, list[str]]:
    """As `read_dxf`, except that anything other than a FormatError comes out as it is. The tests use this, so a bug isn't mistaken for damage."""
    decoded = decode(data)
    r = _Reader(decoded)
    sections = r.sections()
    doc = r.doc
    seed = 0
    extras: dict = {"version": decoded.version, "codepage": decoded.codepage, "newline": decoded.newline, "ended": decoded.ended, "bom": decoded.bom,
                    "order": r.order, "frames": r.frames, "eof": r.eof}
    doc.extras["dxf"] = extras
    if "HEADER" in sections:
        a, b = sections["HEADER"]
        header = r.header(a, b)
        r.apply_header(header)
        extras["header"] = r.raw(a, b)
        if header.get("$HANDSEED"):
            try:
                seed = int(header["$HANDSEED"][0][1].strip(), 16)
            except ValueError:
                seed = 0
    if "TABLES" in sections:
        extras["tables"] = r.tables(*sections["TABLES"])
    if "BLOCKS" in sections:
        extras["blocks"] = r.blocks(*sections["BLOCKS"])
    check_block_cycles(doc)
    if "ENTITIES" in sections:
        r.entities(*sections["ENTITIES"], None)
    for name, (a, b) in sections.items():
        if name not in ("HEADER", "TABLES", "BLOCKS", "ENTITIES"):
            extras.setdefault("sections", {})[name] = r.raw(a, b)
    extras["paper"] = r.paper
    extras["max_handle"] = r.most_handle(seed)
    doc._next_id = r.next_id
    if doc.current.layer not in doc.layers:
        doc.current.layer = "0"
    if doc.current.text_style not in doc.text_styles:
        doc.current.text_style = "Standard"
    if doc.current.dim_style not in doc.dim_styles:
        doc.current.dim_style = "Standard"
    doc._install_missing_required()
    # What the file didn't say and we filled in, such as a layer an object is on that its table lacks. It stays unsaid unless it's changed.
    for table in (doc.layers, doc.linetypes, doc.text_styles, doc.dim_styles):
        for v in list(table):
            if v.source is None:
                table._put(replace(v, source=Raw("", v)))
    extras["read"] = header_state(doc)
    return doc, _notices(r, doc)
