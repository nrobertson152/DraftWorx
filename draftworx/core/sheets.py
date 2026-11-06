# SPDX-License-Identifier: GPL-3.0-or-later
"""Sheets: the paper a drawing is printed on, at the drawing's scale, and the border and title block that go on it (ADR-5).

You draw at real size. The sheet is a rectangle in the drawing, the paper's size times the scale, and printing prints exactly that
rectangle onto that paper. So a 1:50 drawing on A3 is a 21 m by 14.85 m rectangle, and a line 1000 mm long is 20 mm on the paper.

A border with a title block is an ordinary block, placed on the sheet at the drawing's scale, with fields to fill in. Templates are
drawings that start with one.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .curves import Polyline
from .model import BlockDef, Document, Entity, Layer, Linetype, Settings, Style
from .shapes import AttDef, Attrib, Insert, Text
from .units import unit_factor
from .vec import BBox, Vec, bbox_union

#: name: (width, height) in millimetres, upright
PAPERS: dict[str, tuple[float, float]] = {
    "A4": (210.0, 297.0), "A3": (297.0, 420.0), "A2": (420.0, 594.0), "A1": (594.0, 841.0), "A0": (841.0, 1189.0),
    "Letter": (215.9, 279.4), "Legal": (215.9, 355.6), "Tabloid": (279.4, 431.8),
    "ANSI C": (431.8, 558.8), "ANSI D": (558.8, 863.6), "ANSI E": (863.6, 1117.6),
}
_ALIASES = {"ANSI A": "Letter", "ANSI B": "Tabloid", "LEDGER": "Tabloid", "C": "ANSI C", "D": "ANSI D", "E": "ANSI E"}
#: 1:n, as ISO 5455 and architects use them; below 1 is an enlargement (0.5 is 2:1)
METRIC = (0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 1250, 2000, 2500, 5000, 10000)
#: 1:n for drawings in inches and feet: 1/4" = 1'-0" is 1:48
IMPERIAL = (0.25, 0.5, 1, 2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 120, 192, 240, 480, 600, 1200, 2400, 4800)
FRAME_LAYER = "Sheet"


def paper_named(text: str) -> str | None:
    """The paper this names, in its own spelling."""
    t = text.strip().upper().replace("  ", " ")
    t = _ALIASES.get(t, t)
    for name in PAPERS:
        if name.upper() == t.upper():
            return name
    return None


def per_mm(settings: Settings) -> float:
    """Drawing units for each millimetre on the paper: the drawing's scale, in its own units."""
    k = settings.annotation_scale if settings.annotation_scale > 0.0 else 1.0
    return k * unit_factor("mm", settings.units)


def scale_words(n: float) -> str:
    """1:50, or 2:1 for an enlargement."""
    if n <= 0.0:
        n = 1.0
    if n < 1.0:
        inverse = 1.0 / n
        return f"{inverse:g}:1"
    return f"1:{n:g}"


def parse_scale(text: str) -> float | None:
    """1:50, 50, 2:1, or 1/4"=1'-0" style. The n of 1:n, or None."""
    t = text.strip().replace(" ", "")
    if not t:
        return None
    try:
        if ":" in t:
            a, b = t.split(":", 1)
            a, b = float(a), float(b)
            return b / a if a > 0.0 and b > 0.0 else None
        if "=" in t:
            from .units import parse_length
            paper, real = t.split("=", 1)
            p, r = parse_length(paper, "in"), parse_length(real, "in")
            return r / p if p > 0.0 and r > 0.0 else None
        n = float(t)
        return n if n > 0.0 else None
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class Sheet:
    paper: str
    width: float        # millimetres across, as it's turned
    height: float       # millimetres up
    at: Vec             # its lower-left corner, in the drawing
    per_mm: float       # drawing units for each millimetre on it

    def box(self) -> BBox:
        x, y = self.at
        return (x, y, x + self.width * self.per_mm, y + self.height * self.per_mm)

    def outline(self) -> Polyline:
        x0, y0, x1, y1 = self.box()
        return Polyline((Vec(x0, y0), Vec(x1, y0), Vec(x1, y1), Vec(x0, y1)), closed=True)

    def to_paper(self, p) -> tuple[float, float]:
        """A drawing point, as millimetres from the paper's lower-left corner."""
        return ((p[0] - self.at[0]) / self.per_mm, (p[1] - self.at[1]) / self.per_mm)


def size_of(paper: str, landscape: bool) -> tuple[float, float]:
    w, h = PAPERS[paper]
    return (max(w, h), min(w, h)) if landscape else (min(w, h), max(w, h))


def sheet_of(doc: Document) -> Sheet | None:
    """The drawing's sheet, or None if it hasn't one yet."""
    st = doc.settings
    if not st.paper or st.paper not in PAPERS:
        return None
    w, h = size_of(st.paper, st.landscape)
    return Sheet(st.paper, w, h, Vec(st.sheet_x, st.sheet_y), per_mm(st))


def fitting_scale(box: BBox, width: float, height: float, units: str, margin: float = 20.0) -> float:
    """The largest standard scale at which `box` fits on this paper, inside a margin in millimetres all round. The n of 1:n."""
    w = max(box[2] - box[0], 1e-12)
    h = max(box[3] - box[1], 1e-12)
    room_w, room_h = max(width - 2 * margin, width * 0.5), max(height - 2 * margin, height * 0.5)
    mm = unit_factor("mm", units)
    need = max(w / (room_w * mm), h / (room_h * mm))
    for n in (IMPERIAL if units in ("in", "ft") else METRIC):
        if n >= need * (1.0 - 1e-9):
            return float(n)
    return float(10 ** math.ceil(math.log10(need)))


def drawn(doc: Document) -> BBox | None:
    """The box round what's drawn and prints: everything on a layer that's shown and printed, but the border and title block, which goes
    wherever the sheet goes."""
    frame_id = frame_insert(doc)
    quiet = {v.name.casefold() for v in doc.layers if not (v.visible and v.plot)}
    box: BBox | None = None
    for e in doc.entities.values():
        if e.id != frame_id and e.style.layer.casefold() not in quiet:
            box = bbox_union(box, doc.shape_bbox(e.shape))
    return box


def off_sheet(doc: Document, sheet: Sheet) -> bool:
    """Whether some of what's drawn is outside the sheet, so it won't print."""
    box = drawn(doc)
    if box is None:
        return False
    x0, y0, x1, y1 = sheet.box()
    return box[0] < x0 or box[1] < y0 or box[2] > x1 or box[3] > y1


OFF_SHEET = "Some of what's drawn is off the sheet, so it isn't printed. Move the sheet or change its scale with Sheet."


def fitted(doc: Document, paper: str, landscape: bool, scale: float | None = None, centre: Vec | None = None) -> Settings:
    """The drawing's settings with a sheet of this paper, centred on what's drawn (or `centre`), at a scale that fits it (or `scale`)."""
    st = doc.settings
    w, h = size_of(paper, landscape)
    box = drawn(doc)
    if box is None:         # nothing drawn: the scale it has, and the sheet where it was, or from the origin
        n = scale if scale is not None else (st.annotation_scale if st.annotation_scale > 0.0 else 1.0)
        k = n * unit_factor("mm", st.units)
        old = sheet_of(doc)
        if centre is None and old is not None:
            centre = Vec((old.box()[0] + old.box()[2]) / 2.0, (old.box()[1] + old.box()[3]) / 2.0)
        box = (0.0, 0.0, w * k, h * k)
    else:
        n = scale if scale is not None else fitting_scale(box, w, h, st.units)
        k = n * unit_factor("mm", st.units)
    c = centre if centre is not None else Vec((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
    return replace(st, paper=paper, landscape=landscape, annotation_scale=n, sheet_x=c[0] - w * k / 2.0, sheet_y=c[1] - h * k / 2.0)


# --------------------------------------------------------------------------------------------- borders and title blocks

FIELDS = (("TITLE", "Title"), ("DRAWING_NO", "Drawing number"), ("REV", "Revision"), ("SHEET", "Sheet"), ("DRAWN", "Drawn"),
          ("CHECKED", "Checked"), ("DATE", "Date"), ("SCALE", "Scale"), ("COMPANY", "Company"), ("PROJECT", "Project"))


def frame_name(paper: str, landscape: bool) -> str:
    return f"Sheet {paper} {'across' if landscape else 'upright'}"


def _cell(out: list, x: float, y: float, w: float, h: float, tag: str, label: str, size: float = 3.5) -> None:
    out.append(Entity(0, Polyline((Vec(x, y), Vec(x + w, y), Vec(x + w, y + h), Vec(x, y + h)), closed=True)))
    out.append(Entity(0, Text(Vec(x + 1.5, y + h - 1.5), 1.8, label, valign=3)))
    out.append(Entity(0, AttDef(Attrib(tag, Text(Vec(x + 2.0, y + 2.5), size, ""), f"{label}?"))))


def frame(paper: str, landscape: bool) -> BlockDef:
    """A border and a title block for this paper, in millimetres, its base point at the paper's lower-left corner. ISO papers have a 20 mm
    filing margin on the left and 10 mm elsewhere; the others half an inch all round. The title block is 180 mm wide (ISO 7200), at the
    bottom right, with a field for each of `FIELDS`."""
    w, h = size_of(paper, landscape)
    iso = paper.startswith("A")
    left, edge = (20.0, 10.0) if iso else (12.7, 12.7)
    x1, y1 = w - edge, h - edge
    out: list[Entity] = [Entity(0, Polyline((Vec(left, edge), Vec(x1, edge), Vec(x1, y1), Vec(left, y1)), closed=True))]
    tw = 180.0 if w - left - edge >= 190.0 else w - left - edge
    x0, row = x1 - tw, 10.0
    _cell(out, x0, edge + 3 * row, tw, row, "TITLE", "Title", 5.0)
    half = tw / 2.0
    _cell(out, x0, edge + 2 * row, half, row, "COMPANY", "Company")
    _cell(out, x0 + half, edge + 2 * row, half, row, "PROJECT", "Project")
    _cell(out, x0, edge + row, tw * 0.5, row, "DRAWING_NO", "Drawing number")
    _cell(out, x0 + tw * 0.5, edge + row, tw * 0.2, row, "REV", "Revision")
    _cell(out, x0 + tw * 0.7, edge + row, tw * 0.3, row, "SHEET", "Sheet")
    quarter = tw / 4.0
    for i, (tag, label) in enumerate((("DRAWN", "Drawn"), ("CHECKED", "Checked"), ("DATE", "Date"), ("SCALE", "Scale"))):
        _cell(out, x0 + i * quarter, edge, quarter, row, tag, label)
    return BlockDef(frame_name(paper, landscape), Vec(0.0, 0.0), tuple(Entity(i + 1, e.shape) for i, e in enumerate(out)),
                    f"Border and title block for {paper} {'across' if landscape else 'upright'}")


def frame_paper(block: str) -> tuple[str, bool] | None:
    """The paper and which way round it is, from a border's block name as `frame_name` makes it, or as an R12 file spells it
    (SHEET_A3_ACROSS)."""
    name = block.replace("_", " ").casefold()
    for paper in PAPERS:
        for landscape in (True, False):
            if name == frame_name(paper, landscape).casefold():
                return paper, landscape
    return None


def frame_insert(doc: Document) -> int | None:
    """The placed border and title block, if there is one: a block named as `frame` names them."""
    for e in doc.sorted_entities():
        if type(e.shape) is Insert and frame_paper(e.shape.block) is not None and doc.blocks.get(e.shape.block) is not None:
            return e.id
    return None


def title_block(doc: Document) -> dict[str, str]:
    """What's filled in on the border's title block, by field (TITLE, DRAWING_NO, ...). Empty without one."""
    i = frame_insert(doc)
    if i is None:
        return {}
    return {a.tag.upper(): a.text.text.strip() for a in doc.entities[i].shape.attribs if a.text.text.strip()}


def first_sheet(doc: Document, paper: str | None = None) -> Settings:
    """A sheet for a drawing that hasn't one. Where its border is, if it has one, as a DXF file brings back a drawing that had a sheet:
    the border's block says the paper, and where it's placed and how big say the rest. Otherwise round what's drawn."""
    i = frame_insert(doc)
    if i is not None:
        ins = doc.entities[i].shape
        found = frame_paper(ins.block)
        k = unit_factor("mm", doc.settings.units)
        if found is not None and ins.sx > 0.0 and ins.sx == ins.sy and ins.rotation == 0.0 and math.isfinite(ins.sx) and ins.columns == ins.rows == 1:
            return replace(doc.settings, paper=found[0], landscape=found[1], annotation_scale=ins.sx / k, sheet_x=ins.p[0], sheet_y=ins.p[1])
    return fitted(doc, paper or default_paper(doc), True)


def default_paper(doc: Document) -> str:
    """The paper a drawing gets when nobody's said: Tabloid for one in inches or feet, A3 for the rest."""
    return "Tabloid" if doc.settings.units in ("in", "ft") else "A3"


def place_frame(t, sheet: Sheet, landscape: bool, keep: dict[str, str] | None = None) -> int:
    """Put the border and title block on the sheet, in this Transaction: replacing the one there, and keeping its fields. SCALE is filled in."""
    from .blocks import placed_attribs
    doc = t.doc
    name = frame_name(sheet.paper, landscape)
    if doc.blocks.get(name) is None:
        b = frame(sheet.paper, landscape)
        base = doc._next_id
        t.put_block(replace(b, entities=tuple(Entity(base + i, e.shape, e.style, base + i) for i, e in enumerate(b.entities))))
    values = dict(keep or {})
    old = frame_insert(doc)
    if old is not None:
        values = {a.tag.casefold(): a.text.text for a in doc.entities[old].shape.attribs} | values
        t.remove(old)
    values["scale"] = scale_words(sheet.per_mm / unit_factor("mm", doc.settings.units))
    if FRAME_LAYER not in doc.layers:
        t.put_layer(Layer(FRAME_LAYER, 7, weight=35))
    ins = Insert(name, sheet.at, sheet.per_mm, sheet.per_mm)
    ins = replace(ins, attribs=placed_attribs(doc.blocks.get(name), ins, values))
    return t.add(ins, Style(FRAME_LAYER))


def apply(t, new: Settings) -> None:
    """Change the drawing's sheet and scale, in this Transaction. Line types keep their size on paper, and a border and title block go with
    the sheet, their fields kept and their scale filled in."""
    doc = t.doc
    old = doc.settings
    if new.annotation_scale != old.annotation_scale and old.annotation_scale > 0.0 and new.annotation_scale > 0.0:
        new = replace(new, ltscale=old.ltscale * new.annotation_scale / old.annotation_scale)
    t.set_settings(new)
    sheet = sheet_of(doc)
    i = frame_insert(doc)
    if sheet is not None and i is not None:
        ins = doc.entities[i].shape
        if frame_paper(ins.block) != (sheet.paper, new.landscape) or ins.sx != sheet.per_mm or ins.sy != sheet.per_mm or ins.p != sheet.at:
            place_frame(t, sheet, new.landscape)        # a border already where the sheet is, as one from a file, is left as it is


# ------------------------------------------------------------------------------------------------------------- templates

TEMPLATES: dict[str, tuple[str, bool, str]] = {}
for _p in ("A4", "A3", "A2", "A1", "A0"):
    for _land in (True, False):
        TEMPLATES[f"ISO {_p} {'across' if _land else 'upright'}"] = (_p, _land, "mm")
for _p in ("Letter", "Tabloid", "ANSI C", "ANSI D", "ANSI E"):
    TEMPLATES[f"{_p} {'across'}"] = (_p, True, "in")


def from_template(name: str) -> Document:
    """A new drawing with a sheet, its border and title block, and the layers and line types drawings usually need."""
    paper, landscape, units = TEMPLATES[name]
    doc = Document()
    k = unit_factor("mm", units)        # the line types' sizes are millimetres on the paper, in the drawing's units
    with doc.transaction("Template") as t:
        t.put_linetype(Linetype("CENTER", "Centre ____ _ ____ _", tuple(x * k for x in (31.75, -6.35, 6.35, -6.35))))
        t.put_linetype(Linetype("HIDDEN", "Hidden __ __ __", tuple(x * k for x in (6.35, -3.175))))
        t.put_linetype(Linetype("PHANTOM", "Phantom ____ _ _ ____", tuple(x * k for x in (31.75, -6.35, 6.35, -6.35, 6.35, -6.35))))
        for layer in (Layer("Outlines", 7, weight=50), Layer("Hidden", 8, "HIDDEN", 25), Layer("Centre lines", 1, "CENTER", 18),
                      Layer("Dimensions", 4, weight=18), Layer("Text", 7, weight=25), Layer("Hatch", 8, weight=13)):
            t.put_layer(layer)
        t.set_settings(Settings(units=units, linear_decimals=2 if units == "mm" else 3, paper=paper, landscape=landscape))
        place_frame(t, sheet_of(doc), landscape)
    doc.current.layer = "Outlines"
    doc.forget_history()
    doc.mark_saved()
    return doc


__all__ = ["FIELDS", "FRAME_LAYER", "IMPERIAL", "METRIC", "OFF_SHEET", "PAPERS", "Sheet", "TEMPLATES", "apply", "default_paper", "drawn", "fitted", "fitting_scale", "frame",
           "first_sheet", "frame_insert", "frame_name", "frame_paper", "from_template", "off_sheet", "paper_named", "parse_scale", "per_mm", "place_frame", "scale_words", "sheet_of", "title_block",
           "size_of"]
