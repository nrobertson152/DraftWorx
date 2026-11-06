# SPDX-License-Identifier: GPL-3.0-or-later
"""The `.draftworx` format: UTF-8 JSON, one object to a line, the same bytes on every computer. See docs/FILE_FORMAT.md.

Every value is checked on the way in. A file that fails a check is refused with a message saying where, and nothing is
built from it, so a damaged file can't half-load and then be saved over itself.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import replace
from typing import Any

from . import color as colors
from .curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from .model import MOST_SEGMENTS, BlockDef, DimStyle, Document, Entity, Layer, Linetype, Settings, Style, TextStyle, block_used_by
from .rawsource import Raw
from .shapes import AttDef, Attrib, Foreign, Insert, MText, Node, Solid, Text
from .vec import TAU, Vec

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
FORMAT = "DraftWorx drawing"
VERSION = 1
MAX_ID = 1 << 39        # room for a sub-index under it in the spatial index, and more than any drawing will use


class FormatError(Exception):
    """This file can't be read. The message says why, for the person opening it."""


def _pt(p) -> list[float]:
    return [float(p[0]), float(p[1])]


def _encode_text(s: Text) -> dict:
    d: dict[str, Any] = {"p": _pt(s.p), "height": float(s.height), "text": s.text}
    if s.rotation:
        d["rotation"] = float(s.rotation)
    if s.style != "Standard":
        d["style"] = s.style
    if s.halign:
        d["halign"] = s.halign
    if s.valign:
        d["valign"] = s.valign
    if s.width_factor != 1.0:
        d["width_factor"] = float(s.width_factor)
    if s.oblique:
        d["oblique"] = float(s.oblique)
    if s.p2 is not None:
        d["p2"] = _pt(s.p2)
    if s.mirrored_x:
        d["mirrored_x"] = True
    if s.mirrored_y:
        d["mirrored_y"] = True
    return d


def _encode_attrib(a: Attrib) -> dict:
    d: dict[str, Any] = {"tag": a.tag, **_encode_text(a.text)}
    if a.prompt:
        d["prompt"] = a.prompt
    if a.invisible:
        d["invisible"] = True
    if a.constant:
        d["constant"] = True
    return d


def encode_shape(s) -> dict:
    k = type(s)
    if k is Line:
        return {"type": "line", "a": _pt(s.a), "b": _pt(s.b)}
    if k is Arc:
        return {"type": "arc", "c": _pt(s.c), "r": float(s.r), "start": float(s.start_angle), "sweep": float(s.sweep)}
    if k is Circle:
        return {"type": "circle", "c": _pt(s.c), "r": float(s.r)}
    if k is EllipseArc:
        d = {"type": "ellipse", "c": _pt(s.c), "major": _pt(s.major), "ratio": float(s.ratio)}
        if s.start_param or s.sweep != TAU:
            d["start"] = float(s.start_param)
            d["sweep"] = float(s.sweep)
        return d
    if k is Polyline:
        d = {"type": "polyline", "pts": [_pt(p) for p in s.pts]}
        if any(s.bulges):
            d["bulges"] = [float(b) for b in s.bulges]
        if s.closed:
            d["closed"] = True
        if s.width:
            d["width"] = float(s.width)
        if s.widths:
            d["widths"] = [[float(a), float(b)] for a, b in s.widths]
        return d
    if k is Spline:
        d = {"type": "spline", "degree": s.degree, "knots": [float(u) for u in s.knots], "ctrl": [_pt(p) for p in s.ctrl]}
        if s.weights:
            d["weights"] = [float(w) for w in s.weights]
        if s.fit:
            d["fit"] = [_pt(p) for p in s.fit]
        if s.periodic:
            d["periodic"] = True
        return d
    if k is Node:
        return {"type": "node", "p": _pt(s.p)}
    if k is Solid:
        return {"type": "solid", "pts": [_pt(p) for p in s.pts]}
    if k is Text:
        return {"type": "text", **_encode_text(s)}
    if k is MText:
        d = {"type": "mtext", "p": _pt(s.p), "height": float(s.height), "text": s.text}
        if s.width:
            d["width"] = float(s.width)
        if s.rotation:
            d["rotation"] = float(s.rotation)
        if s.attach != 1:
            d["attach"] = s.attach
        if s.style != "Standard":
            d["style"] = s.style
        if s.spacing != 1.0:
            d["spacing"] = float(s.spacing)
        return d
    if k is AttDef:
        return {"type": "attdef", **_encode_attrib(s.attrib)}
    if k is Insert:
        d = {"type": "insert", "block": s.block, "p": _pt(s.p)}
        if s.sx != 1.0:
            d["sx"] = float(s.sx)
        if s.sy != 1.0:
            d["sy"] = float(s.sy)
        if s.rotation:
            d["rotation"] = float(s.rotation)
        if s.attribs:
            d["attribs"] = [_encode_attrib(a) for a in s.attribs]
        if s.columns != 1 or s.rows != 1:
            d["columns"], d["rows"] = s.columns, s.rows
            d["column_spacing"], d["row_spacing"] = float(s.column_spacing), float(s.row_spacing)
        return d
    if k is Foreign:
        d = {"type": "foreign", "dxf_type": s.dxf_type}
        if s.block:
            d["block"] = s.block
        if s.at != (0.0, 0.0):
            d["at"] = _pt(s.at)
        if s.curves:
            d["curves"] = [encode_shape(c) for c in s.curves]
        return d
    raise TypeError(f"no file form for {k.__name__}")


def encode_style(style: Style, d: dict) -> dict:
    if style.layer != "0":
        d["layer"] = style.layer
    if style.color != colors.BYLAYER:
        d["color"] = style.color
    if style.linetype != "ByLayer":
        d["linetype"] = style.linetype
    if style.weight != -1:
        d["weight"] = style.weight
    if style.ltscale != 1.0:
        d["ltscale"] = float(style.ltscale)
    return d


def encode_entity(e: Entity) -> dict:
    d: dict[str, Any] = {"id": e.id}
    d.update(encode_shape(e.shape))
    encode_style(e.style, d)
    if e.order != e.id:
        d["order"] = e.order
    src = e.source
    if src is not None:
        d["source"] = _encode_source(src, not isinstance(src, Raw) or (src.shape is not e.shape and src.shape != e.shape),
                                     not isinstance(src, Raw) or (src.style is not e.style and src.style != e.style))
    return d


def _encode_source(source, reshaped: bool = False, restyled: bool = False):
    """The file's own text, and whether the thing is still what it says. Without that, a changed object would come back from a file looking unchanged."""
    if not isinstance(source, Raw):
        return source
    d = {"dxf": source.text}
    if reshaped:
        d["reshaped"] = True
    if restyled:
        d["restyled"] = True
    return d


def _encode_table_source(v) -> dict:
    return _encode_source(v.source, isinstance(v.source, Raw) and v.source.shape != replace(v, source=None))


def encode_layer(v: Layer) -> dict:
    d: dict[str, Any] = {"name": v.name, "color": v.color, "linetype": v.linetype, "weight": v.weight}
    for flag, default in (("on", True), ("frozen", False), ("locked", False), ("plot", True)):
        if getattr(v, flag) != default:
            d[flag] = getattr(v, flag)
    if v.description:
        d["description"] = v.description
    if v.source is not None:
        d["source"] = _encode_table_source(v)
    return d


def encode_linetype(v: Linetype) -> dict:
    d: dict[str, Any] = {"name": v.name, "description": v.description, "pattern": [float(x) for x in v.pattern]}
    if v.source is not None:
        d["source"] = _encode_table_source(v)
    return d


def encode_text_style(v: TextStyle) -> dict:
    d: dict[str, Any] = {"name": v.name, "font": v.font, "height": float(v.height), "width_factor": float(v.width_factor),
                         "oblique": float(v.oblique)}
    if v.source is not None:
        d["source"] = _encode_table_source(v)
    return d


_DIM_FLOATS = ("text_height", "arrow_size", "ext_offset", "ext_beyond", "text_gap", "baseline_step", "scale", "linear_factor",
               "tick_size", "center_mark")
_DIM_INTS = ("decimals", "angle_decimals", "suppress_zeros", "text_above", "unit_format")
_DIM_STRS = ("decimal_sep", "prefix_suffix", "text_style")
_DIM_BOOLS = ("text_inside_horizontal", "text_outside_horizontal")


def encode_dim_style(v: DimStyle) -> dict:
    d: dict[str, Any] = {"name": v.name}
    default = DimStyle(v.name)
    for f in _DIM_FLOATS + _DIM_INTS + _DIM_STRS + _DIM_BOOLS:
        if getattr(v, f) != getattr(default, f):
            d[f] = getattr(v, f)
    if v.source is not None:
        d["source"] = _encode_table_source(v)
    return d


def encode_block(v: BlockDef) -> dict:
    d: dict[str, Any] = {"name": v.name, "base": _pt(v.base), "entities": [encode_entity(e) for e in
                                                                           sorted(v.entities, key=lambda e: (e.order, e.id))]}
    if v.description:
        d["description"] = v.description
    if v.flags:
        d["flags"] = v.flags
    if v.source is not None:
        d["source"] = _encode_source(v.source, isinstance(v.source, Raw) and v.source.shape != replace(v, entities=(), source=None))
    return d


def encode_settings(s: Settings) -> dict:
    return {"units": s.units, "linear_format": s.linear_format, "linear_decimals": s.linear_decimals, "angle_decimals": s.angle_decimals,
            "annotation_scale": float(s.annotation_scale), "ltscale": float(s.ltscale), "point_mode": s.point_mode,
            "point_size": float(s.point_size)}


def canonical_state(doc: Document) -> dict:
    """Everything undo restores, in a form that compares equal exactly when the drawings are the same."""
    return {"settings": encode_settings(doc.settings),
            "layers": [encode_layer(v) for v in doc.layers],
            "linetypes": [encode_linetype(v) for v in doc.linetypes],
            "text_styles": [encode_text_style(v) for v in doc.text_styles],
            "dim_styles": [encode_dim_style(v) for v in doc.dim_styles],
            "blocks": [encode_block(v) for v in doc.blocks],
            "entities": [encode_entity(e) for e in sorted(doc.entities.values(), key=lambda e: e.id)]}


def dumps(doc: Document, program: str = "") -> bytes:
    """The whole file. One object to a line, so a text diff between two versions shows what changed."""
    state = canonical_state(doc)
    c = doc.current
    head = {"format": FORMAT, "version": VERSION, "program": program, "next_id": doc._next_id,
            "settings": state["settings"],
            "current": {"layer": c.layer, "color": c.color, "linetype": c.linetype, "weight": c.weight,
                        "text_style": c.text_style, "dim_style": c.dim_style}}
    def dump(v):
        return json.dumps(v, separators=(",", ":"), ensure_ascii=False, allow_nan=False)

    def section(name: str, items: list) -> str:
        return f"{dump(name)}:[\n" + ",\n".join(dump(i) for i in items) + "\n]"
    parts = [dump(head)[:-1]]
    for name in ("layers", "linetypes", "text_styles", "dim_styles", "blocks", "entities"):
        parts.append(section(name, state[name]))
    parts.append(f'"extras":{dump(doc.extras)}')
    text = ",\n".join(parts) + "}\n"
    try:
        return text.encode("utf-8")
    except UnicodeEncodeError:
        # bytes from a DXF that didn't fit its encoding: JSON can spell them, and they come back as they were
        return _LONE_SURROGATE.sub(lambda m: f"\\u{ord(m.group()):04x}", text).encode("utf-8")


# ---------------------------------------------------------------------------------------------------- reading

def _num(d: dict, key: str, ctx: str, default=None) -> float:
    v = d.get(key, default)
    if v is None and default is None:
        raise FormatError(f"{ctx} has no {key}.")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise FormatError(f"{ctx}: {key} should be a number.")
    return float(v)


def _int(d: dict, key: str, ctx: str, default=None, lo: int | None = None, hi: int | None = None) -> int:
    v = d.get(key, default)
    if v is None or isinstance(v, bool) or not isinstance(v, int):
        raise FormatError(f"{ctx}: {key} should be a whole number.")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise FormatError(f"{ctx}: {key} is out of range.")
    return v


def _str(d: dict, key: str, ctx: str, default=None) -> str:
    v = d.get(key, default)
    if not isinstance(v, str):
        raise FormatError(f"{ctx}: {key} should be text.")
    return v


def _bool(d: dict, key: str, ctx: str, default: bool = False) -> bool:
    v = d.get(key, default)
    if not isinstance(v, bool):
        raise FormatError(f"{ctx}: {key} should be true or false.")
    return v


def _vec(v, ctx: str, key: str) -> Vec:
    if (not isinstance(v, list) or len(v) != 2 or isinstance(v[0], bool) or isinstance(v[1], bool)
            or not isinstance(v[0], (int, float)) or not isinstance(v[1], (int, float))
            or not math.isfinite(v[0]) or not math.isfinite(v[1])):
        raise FormatError(f"{ctx}: {key} should be a point.")
    return Vec(float(v[0]), float(v[1]))


def _pt_of(d: dict, key: str, ctx: str) -> Vec:
    return _vec(d.get(key), ctx, key)


def _points(d: dict, key: str, ctx: str, least: int = 0) -> tuple[Vec, ...]:
    v = d.get(key)
    if not isinstance(v, list) or len(v) < least:
        raise FormatError(f"{ctx}: {key} should be a list of points.")
    return tuple(_vec(p, ctx, key) for p in v)


def _floats(d: dict, key: str, ctx: str) -> tuple[float, ...]:
    v = d.get(key, [])
    if not isinstance(v, list):
        raise FormatError(f"{ctx}: {key} should be a list of numbers.")
    out = []
    for x in v:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
            raise FormatError(f"{ctx}: {key} should be a list of numbers.")
        out.append(float(x))
    return tuple(out)


def _positive(d: dict, key: str, ctx: str) -> float:
    v = _num(d, key, ctx)
    if v <= 0.0:
        raise FormatError(f"{ctx}: {key} should be more than zero.")
    return v


def _sweep(d: dict, ctx: str, default=None) -> float:
    s = _num(d, "sweep", ctx, default)
    if abs(s) > TAU * (1.0 + 1e-9):
        raise FormatError(f"{ctx}: an arc can't sweep more than one turn.")
    return s


def _decode_text(d: dict, ctx: str) -> Text:
    p2 = d.get("p2")
    return Text(_pt_of(d, "p", ctx), _num(d, "height", ctx), _str(d, "text", ctx), _num(d, "rotation", ctx, 0.0),
                _str(d, "style", ctx, "Standard"), _int(d, "halign", ctx, 0, 0, 5), _int(d, "valign", ctx, 0, 0, 3),
                _num(d, "width_factor", ctx, 1.0), _num(d, "oblique", ctx, 0.0),
                _vec(p2, ctx, "p2") if p2 is not None else None, _bool(d, "mirrored_x", ctx), _bool(d, "mirrored_y", ctx))


def _decode_attrib(d: dict, ctx: str) -> Attrib:
    if not isinstance(d, dict):
        raise FormatError(f"{ctx}: an attribute should be an object.")
    return Attrib(_str(d, "tag", ctx), _decode_text(d, ctx), _str(d, "prompt", ctx, ""), _bool(d, "invisible", ctx),
                  _bool(d, "constant", ctx))


def decode_shape(d: dict, ctx: str):
    t = d.get("type")
    try:
        if t == "line":
            return Line(_pt_of(d, "a", ctx), _pt_of(d, "b", ctx))
        if t == "arc":
            return Arc(_pt_of(d, "c", ctx), _positive(d, "r", ctx), _num(d, "start", ctx), _sweep(d, ctx))
        if t == "circle":
            return Circle(_pt_of(d, "c", ctx), _positive(d, "r", ctx))
        if t == "ellipse":
            major = _pt_of(d, "major", ctx)
            ratio = _num(d, "ratio", ctx)
            if major == (0.0, 0.0) or not 0.0 < ratio <= 1.0:
                raise FormatError(f"{ctx}: this ellipse has no size.")
            return EllipseArc(_pt_of(d, "c", ctx), major, ratio, _num(d, "start", ctx, 0.0), _sweep(d, ctx, TAU))
        if t == "polyline":
            widths = d.get("widths", [])
            if not isinstance(widths, list) or not all(isinstance(w, list) and len(w) == 2 for w in widths):
                raise FormatError(f"{ctx}: widths should be a list of pairs.")
            pairs = tuple((float(_num({"w": w[0]}, "w", ctx)), float(_num({"w": w[1]}, "w", ctx))) for w in widths)
            pts = _points(d, "pts", ctx, 1)
            if len(pts) > MOST_SEGMENTS:
                raise FormatError(f"{ctx}: a polyline can have at most {MOST_SEGMENTS} points.")
            bulges = _floats(d, "bulges", ctx)
            return Polyline(pts, bulges, _bool(d, "closed", ctx), _num(d, "width", ctx, 0.0), pairs)
        if t == "spline":
            return Spline(_int(d, "degree", ctx, None, 1, 25), _floats(d, "knots", ctx), _points(d, "ctrl", ctx, 2), _floats(d, "weights", ctx),
                          _points(d, "fit", ctx) if "fit" in d else (), _bool(d, "periodic", ctx))
        if t == "node":
            return Node(_pt_of(d, "p", ctx))
        if t == "solid":
            return Solid(_points(d, "pts", ctx, 3))
        if t == "text":
            return _decode_text(d, ctx)
        if t == "mtext":
            return MText(_pt_of(d, "p", ctx), _num(d, "height", ctx), _str(d, "text", ctx), _num(d, "width", ctx, 0.0),
                         _num(d, "rotation", ctx, 0.0), _int(d, "attach", ctx, 1, 1, 9), _str(d, "style", ctx, "Standard"),
                         _num(d, "spacing", ctx, 1.0))
        if t == "attdef":
            return AttDef(_decode_attrib(d, ctx))
        if t == "insert":
            attribs = d.get("attribs", [])
            if not isinstance(attribs, list):
                raise FormatError(f"{ctx}: attribs should be a list.")
            return Insert(_str(d, "block", ctx), _pt_of(d, "p", ctx), _num(d, "sx", ctx, 1.0), _num(d, "sy", ctx, 1.0),
                          _num(d, "rotation", ctx, 0.0), tuple(_decode_attrib(a, ctx) for a in attribs),
                          _int(d, "columns", ctx, 1, 1, 100_000), _int(d, "rows", ctx, 1, 1, 100_000),
                          _num(d, "column_spacing", ctx, 0.0), _num(d, "row_spacing", ctx, 0.0))
        if t == "foreign":
            curves = d.get("curves", [])
            if not isinstance(curves, list) or not all(isinstance(c, dict) and c.get("type") in ("line", "arc", "circle", "ellipse", "polyline", "spline") for c in curves):
                raise FormatError(f"{ctx}: curves should be a list of curves.")
            return Foreign(_str(d, "dxf_type", ctx), _str(d, "block", ctx, ""), _pt_of(d, "at", ctx) if "at" in d else Vec(0.0, 0.0), tuple(decode_shape(c, ctx) for c in curves))
    except (TypeError, ValueError) as exc:
        raise FormatError(f"{ctx}: {exc}") from exc
    raise FormatError(f"{ctx}: there's no object type called {t!r}. A newer DraftWorx may have written this file.")


def _decode_style(d: dict, ctx: str, interned: dict) -> Style:
    color = d.get("color", colors.BYLAYER)
    if not colors.valid(color):
        raise FormatError(f"{ctx}: color is out of range.")
    key = (d.get("layer", "0"), color, d.get("linetype", "ByLayer"), d.get("weight", -1), d.get("ltscale", 1.0))
    found = interned.get(key)
    if found is not None:
        return found
    style = Style(_str(d, "layer", ctx, "0"), color, _str(d, "linetype", ctx, "ByLayer"), _int(d, "weight", ctx, -1, -3, 211),
                  _num(d, "ltscale", ctx, 1.0))
    interned[key] = style
    return style


def decode_entity(d, where: str, seen: set, interned: dict, source_reader) -> Entity:
    if not isinstance(d, dict):
        raise FormatError(f"{where} should be an object.")
    eid = d.get("id")
    if isinstance(eid, bool) or not isinstance(eid, int) or not 0 < eid < MAX_ID:
        raise FormatError(f"{where} has no valid id.")
    if eid in seen:
        raise FormatError(f"{where}: id {eid} is used twice.")
    seen.add(eid)
    ctx = f"Object {eid}"
    shape = decode_shape(d, ctx)
    style = _decode_style(d, ctx, interned)
    src = d.get("source")
    source = source_reader(src, shape, style) if src is not None and source_reader is not None else src
    return Entity(eid, shape, style, _int(d, "order", ctx, eid), source)


def _list(obj: dict, key: str) -> list:
    v = obj.get(key, [])
    if not isinstance(v, list):
        raise FormatError(f"The file's {key} section should be a list.")
    return v


def _named(item, kind: str) -> dict:
    if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"]:
        raise FormatError(f"A {kind} in this file has no name.")
    return item


def build(obj, source_reader=None) -> Document:
    """A Document from a parsed file. Raises FormatError if anything in it is wrong."""
    if not isinstance(obj, dict) or obj.get("format") != FORMAT:
        raise FormatError("This isn't a DraftWorx drawing.")
    version = obj.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise FormatError("This drawing's format version is missing or wrong.")
    if version > VERSION:
        raise FormatError(f"This drawing was saved by a newer DraftWorx (format {version}; this one reads up to {VERSION}). "
                          "Update DraftWorx to open it. The file hasn't been changed.")
    doc = Document()
    s = obj.get("settings", {})
    if not isinstance(s, dict):
        raise FormatError("The file's settings should be an object.")
    ctx = "Settings"
    doc.settings = Settings(_str(s, "units", ctx, "mm"), _str(s, "linear_format", ctx, "decimal"), _int(s, "linear_decimals", ctx, 2, 0, 15),
                            _int(s, "angle_decimals", ctx, 1, 0, 15), _num(s, "annotation_scale", ctx, 1.0), _num(s, "ltscale", ctx, 1.0),
                            _int(s, "point_mode", ctx, 0), _num(s, "point_size", ctx, 0.0))
    for item in _list(obj, "layers"):
        item = _named(item, "layer")
        ctx = f"Layer {item['name']}"
        if not colors.valid(item.get("color", 7)):
            raise FormatError(f"{ctx}: color is out of range.")
        doc.layers._put(_sourced(Layer(item["name"], item.get("color", 7), _str(item, "linetype", ctx, "Continuous"), _int(item, "weight", ctx, -3, -3, 211),
                                       _bool(item, "on", ctx, True), _bool(item, "frozen", ctx), _bool(item, "locked", ctx), _bool(item, "plot", ctx, True),
                                       _str(item, "description", ctx, "")), item, source_reader))
    for item in _list(obj, "linetypes"):
        item = _named(item, "line type")
        ctx = f"Line type {item['name']}"
        doc.linetypes._put(_sourced(Linetype(item["name"], _str(item, "description", ctx, ""), _floats(item, "pattern", ctx)), item, source_reader))
    for item in _list(obj, "text_styles"):
        item = _named(item, "text style")
        ctx = f"Text style {item['name']}"
        doc.text_styles._put(_sourced(TextStyle(item["name"], _str(item, "font", ctx, "Arial"), _num(item, "height", ctx, 0.0),
                                                _num(item, "width_factor", ctx, 1.0), _num(item, "oblique", ctx, 0.0)), item, source_reader))
    for item in _list(obj, "dim_styles"):
        item = _named(item, "dimension style")
        ctx = f"Dimension style {item['name']}"
        kw: dict[str, Any] = {}
        for f in _DIM_FLOATS:
            if f in item:
                kw[f] = _num(item, f, ctx)
        for f in _DIM_INTS:
            if f in item:
                kw[f] = _int(item, f, ctx)
        for f in _DIM_STRS:
            if f in item:
                kw[f] = _str(item, f, ctx)
        for f in _DIM_BOOLS:
            if f in item:
                kw[f] = _bool(item, f, ctx)
        doc.dim_styles._put(_sourced(DimStyle(item["name"], **kw), item, source_reader))
    seen: set[int] = set()
    interned: dict = {}
    for item in _list(obj, "blocks"):
        item = _named(item, "block")
        ctx = f"Block {item['name']}"
        members = item.get("entities", [])
        if not isinstance(members, list):
            raise FormatError(f"{ctx}: entities should be a list.")
        entities = tuple(decode_entity(e, f"An object in block {item['name']}", seen, interned, source_reader) for e in members)
        head = BlockDef(item["name"], _pt_of(item, "base", ctx), (), _str(item, "description", ctx, ""), _int(item, "flags", ctx, 0))
        doc.blocks._put(replace(_sourced(head, item, source_reader), entities=entities))
    # references can run in any order, so cycles are looked for once every block is known
    check_block_cycles(doc)
    biggest = max(seen, default=0)
    for item in _list(obj, "entities"):
        e = decode_entity(item, "An object", seen, interned, source_reader)
        doc._set("entity", e.id, None, e)
        if e.id > biggest:
            biggest = e.id
    doc._next_id = _int(obj, "next_id", "The file", biggest + 1, 1)
    if doc._next_id <= biggest:
        doc._next_id = biggest + 1
    extras = obj.get("extras")
    doc.extras = extras if isinstance(extras, dict) else {}
    c = obj.get("current", {})
    if isinstance(c, dict):
        ctx = "Current settings"
        doc.current.layer = _str(c, "layer", ctx, "0")
        if doc.current.layer not in doc.layers:
            doc.current.layer = "0"
        col = c.get("color", colors.BYLAYER)
        doc.current.color = col if colors.valid(col) else colors.BYLAYER
        doc.current.linetype = _str(c, "linetype", ctx, "ByLayer")
        doc.current.weight = _int(c, "weight", ctx, -1, -3, 211)
        doc.current.text_style = _str(c, "text_style", ctx, "Standard")
        doc.current.dim_style = _str(c, "dim_style", ctx, "Standard")
    doc._install_missing_required()
    return doc


def _sourced(value, item: dict, source_reader):
    src = item.get("source")
    if src is None:
        return value
    return replace(value, source=source_reader(src, value, None) if source_reader is not None else src)


def check_block_cycles(doc: Document) -> None:
    state: dict[str, int] = {}

    def visit(name: str, chain: list[str]) -> None:
        key = name.casefold()
        if state.get(key) == 2:
            return
        if state.get(key) == 1:
            raise FormatError(f"Block {name} contains itself: {' > '.join(chain + [name])}. The file is damaged.")
        d = doc.blocks.get(name)
        if d is None:
            return
        state[key] = 1
        for e in d.entities:
            if block_used_by(e.shape):
                visit(e.shape.block, chain + [d.name])
        state[key] = 2
    for b in doc.blocks:
        visit(b.name, [])


def read_source(src, shape, style):
    """What a file said beyond the model. It came from us, but it's checked like everything else."""
    if isinstance(src, dict) and isinstance(src.get("dxf"), str):
        return Raw(src["dxf"], None if src.get("reshaped") else shape, None if src.get("restyled") else style)
    raise FormatError("What this file keeps from a DXF is damaged.")


def loads(data: bytes, source_reader=read_source) -> Document:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FormatError("This file isn't valid text, so it may be damaged or may not be a DraftWorx drawing.") from exc

    def reject(value):
        raise FormatError(f"This file holds an invalid number ({value}).")
    try:
        obj = json.loads(text, parse_constant=reject)
    except json.JSONDecodeError as exc:
        raise FormatError(f"This file is damaged or incomplete (line {exc.lineno}: {exc.msg}).") from exc
    except RecursionError as exc:
        raise FormatError("This file is nested too deeply to be a DraftWorx drawing.") from exc
    return build(obj, source_reader)
