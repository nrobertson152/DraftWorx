# SPDX-License-Identifier: GPL-3.0-or-later
"""Writing a drawing to DXF. See docs/DXF.md for what's kept and how it's checked.

Two cases, and the difference matters. Saving to the version a file came from goes back through the file's own text: whatever hasn't changed
is written as it was, so a file nobody changed comes back byte for byte, and a file with one line changed differs by that line. Anything else
is a new file made from the drawing, and it can only hold what the drawing understands, which `WriteReport` says.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field, replace

from .. import color as colors
from .. import dim as dims
from ..curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from ..model import BYBLOCK_TYPE, WEIGHT_BYBLOCK, BlockDef, DimStyle, Document, Entity, Layer, Linetype, Style, TextStyle, block_used_by
from ..rawsource import Raw
from ..safefile import SaveError
from ..shapes import AttDef, Attrib, Dimension, Foreign, Insert, MText, Node, Solid, Text, plain_mtext, text_width
from ..vec import Vec
from .tags import code_line, codec_for, encode_text, fmt_float, header_state, inum, pairs, retag

#: What can be chosen when saving. A drawing that didn't come from a DXF gets DEFAULT.
VERSIONS = {"R12": "AC1009", "2000": "AC1015", "2004": "AC1018", "2007": "AC1021", "2010": "AC1024", "2013": "AC1027", "2018": "AC1032"}
DEFAULT = "AC1027"
_UNITS = {"none": 0, "in": 1, "ft": 2, "mm": 4, "cm": 5, "m": 6}
_LUNITS = {"scientific": 1, "decimal": 2, "engineering": 3, "architectural": 4, "fractional": 5}
_CANONICAL = ("HEADER", "CLASSES", "TABLES", "BLOCKS", "ENTITIES", "OBJECTS")
#: What's inside a dimension's own block takes its look from the dimension
_IN_A_DIMENSION = Style("0", colors.BYBLOCK, BYBLOCK_TYPE, WEIGHT_BYBLOCK)
#: A dimension's type in a file, and the subclass that holds the rest of it
_DIM_TYPES = {"linear": (0, "AcDbRotatedDimension"), "aligned": (1, "AcDbAlignedDimension"), "diameter": (3, "AcDbDiametricDimension"),
              "radius": (4, "AcDbRadialDimension")}
_SYMBOLS = (("Ø", "%%c"), ("°", "%%d"), ("±", "%%p"))
_R12_NAME = re.compile(r"[^A-Z0-9_$-]")
_NAME = re.compile(r'[<>/\\":;?*|,=`\x00-\x1f]')


class _Out:
    __slots__ = ("lines",)

    def __init__(self) -> None:
        self.lines: list[str] = []

    def tag(self, code: int, value) -> None:
        self.lines.append(code_line(code))
        self.lines.append(fmt_float(value) if isinstance(value, float) else str(value))

    def pt(self, code: int, p, z: bool = True) -> None:
        self.tag(code, float(p[0]))
        self.tag(code + 10, float(p[1]))
        if z:
            self.tag(code + 20, 0.0)

    def raw(self, text: str) -> None:
        if text:
            self.lines.extend(text.split("\n"))


class _Handles:
    def __init__(self, start: int):
        self.next = start
        self.start = start

    def new(self) -> str:
        self.next += 1
        return f"{self.next:X}"


def _hex_of(raw_text: str) -> str | None:
    """The handle in an object's text. It comes just after the type, as code 5, or 105 for a dimension style."""
    lines = raw_text.split("\n")
    for i in range(2, min(len(lines), 8) - 1, 2):
        c = lines[i].strip()
        if c == "5" or c == "105":
            return lines[i + 1].strip()
    return None


def _kept_parts(raw_text: str) -> tuple[list[str], list[str], bool]:
    """What survives an object being rewritten: who's watching it and who owns it, its extended data, and whether that named an owner."""
    lines = raw_text.split("\n")
    n = len(lines)
    end = 2
    while end + 1 < n and lines[end].strip() != "0":
        end += 2
    if end + 1 >= n:
        end = n
    head: list[str] = []
    has_owner = False
    i = 2
    while i + 1 < end:
        c = lines[i].strip()
        if c == "100":
            break
        if c == "102":
            while i + 1 < end:
                head += lines[i:i + 2]
                closing = lines[i + 1].strip() == "}"
                i += 2
                if closing:
                    break
            continue
        if c == "330":
            head += lines[i:i + 2]
            has_owner = True
        i += 2
    tail: list[str] = []
    while i + 1 < end:
        if lines[i].strip() == "1001":
            tail = lines[i:end]
            break
        i += 2
    return head, tail, has_owner


@dataclass
class WriteReport:
    """What a write had to do to fit the file's version, so a person can be told and the check knows what to expect."""
    version: str
    converted: Counter = field(default_factory=Counter)     # written in another form
    lost: Counter = field(default_factory=Counter)          # couldn't be written at all

    def sentences(self) -> list[str]:
        out = []
        if self.converted:
            listed = ", ".join(f"{n} {k}" for k, n in sorted(self.converted.items()))
            out.append(f"This version of DXF can't hold everything as it is, so these were written in a simpler form: {listed}.")
        if self.lost:
            listed = ", ".join(f"{n} {k}" for k, n in sorted(self.lost.items()))
            out.append(f"These can't be written to this version of DXF and were left out: {listed}.")
        return out


class _Writer:
    def __init__(self, doc: Document, version: str):
        self.doc = doc
        self.version = version
        self.modern = version >= "AC1012"
        source = doc.extras.get("dxf")
        self.src: dict = source if isinstance(source, dict) and source.get("version") == version else {}
        self.encoding = "utf-8" if version >= "AC1021" else codec_for(self.src.get("codepage", "ANSI_1252"))
        most = self.src.get("max_handle", 0)
        self.handles = _Handles(most if isinstance(most, int) and most > 0 else 0x100)
        self.handled = self.modern or bool(self.src.get("max_handle"))
        self.report = WriteReport(version)
        self.out = _Out()
        self.block_records: dict[str, str] = {}
        self.records: dict[str, tuple[str, str]] = {}
        self.record_head = ""
        self.record_tail = ""
        self.model_owner = "0"
        self.paper_owner = "0"
        self.known_names = {v.name for t in (doc.layers, doc.linetypes, doc.text_styles, doc.dim_styles, doc.blocks) for v in t if isinstance(v.source, Raw)} if self.src else set()
        #: entity id -> (name, record handle) of the block a dimension written afresh gets, which is what other programs draw
        self.dim_blocks: dict[int, tuple[str, str, Dimension]] = {}

    def s(self, value: str) -> str:
        return encode_text(value, self.version, self.encoding)

    def name(self, value: str) -> str:
        """A name as a reader will accept it. One the file itself used is left alone."""
        if value in self.known_names:
            return value
        if not self.modern:
            star = value.startswith("*")
            return ("*" if star else "") + _R12_NAME.sub("_", value[1:].upper() if star else value.upper())
        star = value.startswith("*")
        return self.s(("*" if star else "") + _NAME.sub("_", value[1:] if star else value))

    # ----------------------------------------------------------------------------------------------------- sections

    def write(self) -> bytes:
        self._plan_block_records()
        self._plan_dimension_blocks()
        parts: dict[str, list[str]] = {"TABLES": self._section_of(self._tables), "BLOCKS": self._section_of(self._blocks), "ENTITIES": self._section_of(self._entities),
                                       "OBJECTS": self._section_of(self._objects), "CLASSES": self._section_of(self._classes)}
        parts["HEADER"] = self._section_of(self._header)     # last, because it says how many handles were used
        kept = self.src.get("sections") or {}
        order = list(self.src.get("order") or (_CANONICAL if self.modern else ("HEADER", "TABLES", "BLOCKS", "ENTITIES")))
        for i, name in enumerate(_CANONICAL):
            if name not in order and parts.get(name):
                later = [order.index(n) for n in _CANONICAL[i + 1:] if n in order]
                order.insert(min(later) if later else len(order), name)
        frames = self.src.get("frames") or {}
        lines: list[str] = []
        for name in order:
            body = parts[name] if name in parts else kept.get(name, "").split("\n") if kept.get(name) else []
            frame = frames.get(name)
            lines += (frame[0] if frame else f"  0\nSECTION\n  2\n{name}").split("\n")
            lines += body
            lines += (frame[1] if frame else "  0\nENDSEC").split("\n")
        eof = self.src.get("eof", "  0\nEOF")
        if eof:
            lines += eof.split("\n")
        newline = self.src.get("newline", "\r\n")
        text = newline.join(lines)
        if self.src.get("ended", True):
            text += newline
        data = text.encode(self.encoding, "surrogateescape")
        return b"\xef\xbb\xbf" + data if self.src.get("bom") else data

    def _section_of(self, method) -> list[str]:
        self.out = _Out()
        method()
        return self.out.lines

    def _classes(self) -> None:
        self.out.raw((self.src.get("sections") or {}).get("CLASSES", ""))

    # ------------------------------------------------------------------------------------------------------- header

    def _variables(self, state: dict) -> dict[str, tuple[str, list]]:
        """Each variable the drawing has a say in: the state it comes from, and its tags."""
        c = state["color"]
        box = state["extents"]
        low = [(10, float(box[0])), (20, float(box[1])), (30, 0.0)] if box else None
        high = [(10, float(box[2])), (20, float(box[3])), (30, 0.0)] if box else None
        out: dict[str, tuple[str, list | None]] = {
            "$LUNITS": ("linear_format", [(70, _LUNITS.get(state["linear_format"], 2))]),
            "$LUPREC": ("linear_decimals", [(70, state["linear_decimals"])]),
            "$AUPREC": ("angle_decimals", [(70, state["angle_decimals"])]),
            "$LTSCALE": ("ltscale", [(40, float(state["ltscale"]))]),
            "$DIMSCALE": ("annotation_scale", [(40, float(state["annotation_scale"]))]),
            "$PDMODE": ("point_mode", [(70, state["point_mode"])]),
            "$PDSIZE": ("point_size", [(40, float(state["point_size"]))]),
            "$CLAYER": ("layer", [(8, self.name(state["layer"]))]),
            "$CELTYPE": ("linetype", [(6, self.name(state["linetype"]))]),
            "$CECOLOR": ("color", [(62, c if c <= 256 else colors.nearest_index(c & 0xffffff))]),
            "$TEXTSTYLE": ("text_style", [(7, self.name(state["text_style"]))]),
            "$EXTMIN": ("extents", low),
            "$EXTMAX": ("extents", high),
        }
        if self.modern:
            out["$INSUNITS"] = ("units", [(70, _UNITS.get(state["units"], 0))])
            out["$MEASUREMENT"] = ("units", [(70, 0 if state["units"] in ("in", "ft") else 1)])
            out["$DIMSTYLE"] = ("dim_style", [(2, self.name(state["dim_style"]))])
        if self.version >= "AC1015":
            out["$CELWEIGHT"] = ("weight", [(370, state["weight"])])
        return out

    def _header(self) -> None:
        """What the file said, with only what's changed changed."""
        o = self.out
        state = header_state(self.doc)
        variables = self._variables(state)
        original = self.src.get("header")
        read = self.src.get("read") or {}
        seed = [(5, f"{self.handles.next + 1:X}")] if self.modern else None
        if self.src:
            changed = {name for name, (key, _) in variables.items() if read.get(key) != state[key]}
            if self.handles.next != self.handles.start and seed is not None:
                changed.add("$HANDSEED")
        else:
            changed = set(variables) | {"$HANDSEED"}
        variables["$HANDSEED"] = ("", seed)
        seen: set[str] = set()
        if original:
            lines = original.split("\n")
            i, n = 0, len(lines)
            while i + 1 < n:
                if lines[i].strip() != "9":
                    i += 2
                    continue
                name = lines[i + 1].strip().upper()
                j = i + 2
                while j + 1 < n and lines[j].strip() != "9":
                    j += 2
                seen.add(name)
                if name in changed and variables.get(name, ("", None))[1] is not None:
                    o.lines += lines[i:i + 2]
                    for code, v in variables[name][1]:
                        o.tag(code, v)
                else:
                    o.lines += lines[i:j]
                i = j
        elif not self.src:
            o.tag(9, "$ACADVER")
            o.tag(1, self.version)
            seen.add("$ACADVER")
            if self.modern:
                o.tag(9, "$DWGCODEPAGE")
                o.tag(3, "ANSI_1252")
        for name in sorted(changed):
            if name not in seen and variables.get(name, ("", None))[1] is not None:
                o.tag(9, name)
                for code, v in variables[name][1]:
                    o.tag(code, v)

    # ------------------------------------------------------------------------------------------------------- tables

    def _plan_block_records(self) -> None:
        """Every block needs a record with a handle, and everything in it refers to that. Records from the file keep theirs."""
        table = ((self.src.get("tables") or {}).get("raw") or {}).get("BLOCK_RECORD", "")
        known: dict[str, tuple[str, str]] = {}
        if table:
            # this table was never checked on reading, so anything could be in it
            lines = table.split("\n")
            n = len(lines)
            marks = [k for k in range(0, n - 1, 2) if lines[k].strip() == "0"]
            last = marks[-1] if len(marks) > 1 and lines[marks[-1] + 1].strip() == "ENDTAB" else n
            inner = [k for k in marks[1:] if k < last]
            self.record_head = "\n".join(lines[:inner[0] if inner else last])
            self.record_tail = "\n".join(lines[last:])
            for a, b in zip(inner, inner[1:] + [last]):
                name = handle = ""
                for k in range(a, b - 1, 2):
                    c = lines[k].strip()
                    if c == "2" and not name:
                        name = lines[k + 1].strip()
                    elif c == "5" and not handle:
                        handle = lines[k + 1].strip()
                key = name.casefold() if name else f"?{a}"
                if key in known:
                    key = f"?{a}"
                known[key] = (handle, "\n".join(lines[a:b]))
        self.records = known
        model = known.get("*model_space") or known.get("$model_space")
        paper = known.get("*paper_space") or known.get("$paper_space")
        # a file without these records stays that way, so what's added to it has no owner to name
        fresh = not self.src
        self.model_owner = model[0] if model else (self.handles.new() if fresh and self.modern else "0")
        self.paper_owner = paper[0] if paper else (self.handles.new() if fresh and self.modern else "0")
        for b in self.doc.blocks:
            found = known.get(b.name.casefold())
            if found:
                self.block_records[b.name.casefold()] = found[0]
            elif self._read_as_a_block(b):
                self.block_records[b.name.casefold()] = "0"     # it came without one, and it isn't our job to add one
            else:
                self.block_records[b.name.casefold()] = self.handles.new() if self.modern else "0"

    def _plan_dimension_blocks(self) -> None:
        """A dimension written afresh needs a block of its own that draws it: that's what other programs show. One still drawn by the block it came with
        keeps that."""
        taken = {b.name.casefold() for b in self.doc.blocks} | set(self.records)
        n = 0
        for e in list(self.doc.entities.values()) + [e for b in self.doc.blocks for e in b.entities]:
            s = e.shape
            if type(s) is not Dimension or self._as_is(e) or (s.block and self.doc.blocks.get(s.block) is not None):
                continue
            while True:
                n += 1
                name = f"*D{n}"
                if name.casefold() not in taken:
                    break
            taken.add(name.casefold())
            self.dim_blocks[e.id] = (name, self.handles.new() if self.modern else "0", s)

    def _as_is(self, e: Entity) -> bool:
        """Is this written as the file had it?"""
        src = e.source
        return isinstance(src, Raw) and bool(self.src) and src.shape == e.shape

    def _read_as_a_block(self, b: BlockDef) -> bool:
        return bool(self.src) and isinstance(b.source, Raw) and bool(b.source.text)

    def _tables(self) -> None:
        info = self.src.get("tables") or {}
        order = list(info.get("order") or ())
        if not self.src:
            order = ["VPORT", "LTYPE", "LAYER", "STYLE", "VIEW", "UCS", "APPID", "DIMSTYLE", "BLOCK_RECORD"] if self.modern else ["LTYPE", "LAYER", "STYLE"]
        else:
            # what the file lacked and now has something to hold
            for name in ("LTYPE", "LAYER", "STYLE", "DIMSTYLE"):
                if name not in order:
                    order.append(name)
            if self.modern and "BLOCK_RECORD" not in order and any(not self._read_as_a_block(b) for b in self.doc.blocks):
                order.append("BLOCK_RECORD")
        for name in order:
            self._table(name, info)

    def _table(self, name: str, info: dict) -> None:
        o = self.out
        raw = info.get("raw") or {}
        if name in ("LAYER", "LTYPE", "STYLE", "DIMSTYLE"):
            self._entries(name, info)
        elif name == "BLOCK_RECORD":
            self._block_record_table()
        elif name in raw:
            o.raw(raw[name])
        elif name == "VPORT":
            self._vport_table()
        elif name == "APPID":
            handle = self._table_head("APPID", 1)
            o.tag(0, "APPID")
            o.tag(5, self.handles.new())
            o.tag(330, handle)
            o.tag(100, "AcDbSymbolTableRecord")
            o.tag(100, "AcDbRegAppTableRecord")
            o.tag(2, "ACAD")
            o.tag(70, 0)
            o.tag(0, "ENDTAB")
        else:
            self._table_head(name, 0)
            o.tag(0, "ENDTAB")

    def _table_head(self, name: str, count: int) -> str:
        o = self.out
        o.tag(0, "TABLE")
        o.tag(2, name)
        handle = "0"
        if self.handled:
            handle = self.handles.new()
            o.tag(5, handle)
        if self.modern:
            o.tag(330, "0")
            o.tag(100, "AcDbSymbolTable")
        o.tag(70, count)
        if self.modern and name == "DIMSTYLE":
            o.tag(100, "AcDbDimStyleTable")
            o.tag(71, 0)
        return handle

    def _entries(self, name: str, info: dict) -> None:
        """A table of things the drawing knows: those in the file, in the file's order, and then what's new."""
        o = self.out
        table = {"LAYER": self.doc.layers, "LTYPE": self.doc.linetypes, "STYLE": self.doc.text_styles, "DIMSTYLE": self.doc.dim_styles}[name]
        head = (info.get("heads") or {}).get(name)
        recorded = (info.get("entries") or {}).get(name) or ()
        wanted: list = []
        done: set[str] = set()
        for item in recorded:
            if isinstance(item, dict):
                wanted.append(item)
            else:
                v = table._d.get(item)
                if v is not None:
                    wanted.append(v)
                    done.add(item)
        for v in table:
            if v.name.casefold() not in done:
                if not self.modern and name == "LTYPE" and v.name.casefold() in ("bylayer", "byblock"):
                    continue
                wanted.append(v)
        fresh_handle = None
        count = 0
        body = _Out()
        self.out = body
        for item in wanted:
            if isinstance(item, dict):
                body.raw(item["raw"])
                count += 1
                continue
            if self._as_read(item):
                if item.source.text:
                    body.raw(item.source.text)
                    count += 1
                continue
            if self._patchable(name, item):
                body.raw(self._patched_layer(item))
                count += 1
                continue
            if head is None and fresh_handle is None and self.handled:
                fresh_handle = self.handles.new()
            self._fresh_entry(name, item, fresh_handle or "0")
            count += 1
        self.out = o
        if head is not None:
            if count != (info.get("counts") or {}).get(name):
                head = retag(head, {70: count}, common_only=False)
            o.raw(head)
        elif count == 0 and self.src:
            return      # the file has no such table, and still has nothing to put in one
        else:
            o.lines += self._table_head_lines(name, count, fresh_handle)
        o.lines += body.lines
        if head is not None:
            o.raw((info.get("tails") or {}).get(name, ""))
        else:
            o.tag(0, "ENDTAB")

    def _table_head_lines(self, name: str, count: int, handle: str | None) -> list[str]:
        o = _Out()
        o.tag(0, "TABLE")
        o.tag(2, name)
        if self.handled:
            o.tag(5, handle or self.handles.new())
        if self.modern:
            o.tag(330, "0")
            o.tag(100, "AcDbSymbolTable")
        o.tag(70, count)
        if self.modern and name == "DIMSTYLE":
            o.tag(100, "AcDbDimStyleTable")
            o.tag(71, 0)
        return o.lines

    def _as_read(self, v) -> bool:
        src = v.source
        return isinstance(src, Raw) and bool(self.src) and src.shape == replace(v, source=None)

    def _patchable(self, name: str, v) -> bool:
        return name == "LAYER" and isinstance(v.source, Raw) and bool(v.source.text) and bool(self.src)

    def _patched_layer(self, v: Layer) -> str:
        """A changed layer keeps what we don't model of it, such as its material and plot style."""
        text = v.source.text
        lines = text.split("\n")
        flags = inum(dict(reversed(pairs(lines, 0, len(lines)))).get(70, "0"))
        c = v.color if v.color <= 255 else colors.nearest_index(v.color & 0xffffff)
        changes: dict = {2: self.s(v.name), 70: (flags & ~5) | (1 if v.frozen else 0) | (4 if v.locked else 0), 62: c if v.on else -c, 6: self.name(v.linetype),
                         420: (v.color & 0xffffff) if v.color > 255 and self.version >= "AC1018" else None}
        if self.version >= "AC1015":
            changes[290] = 1 if v.plot else 0
            changes[370] = v.weight if v.weight != -1 else -3
        return retag(text, changes, common_only=False)

    def _fresh_entry(self, name: str, v, owner: str) -> None:
        {"LAYER": self._layer_entry, "LTYPE": self._ltype_entry, "STYLE": self._style_entry, "DIMSTYLE": self._dim_entry}[name](v, owner)

    def _entry_head(self, kind: str, subclass: str, v, name: str, owner: str) -> None:
        o = self.out
        o.tag(0, kind)
        if self.handled:
            handle = _hex_of(v.source.text) if isinstance(v.source, Raw) and v.source.text and self.src else None
            o.tag(105 if kind == "DIMSTYLE" else 5, handle or self.handles.new())
        if self.modern:
            o.tag(330, owner)
            o.tag(100, "AcDbSymbolTableRecord")
            o.tag(100, subclass)
        o.tag(2, self.name(name))

    def _layer_entry(self, v: Layer, owner: str) -> None:
        o = self.out
        self._entry_head("LAYER", "AcDbLayerTableRecord", v, v.name, owner)
        o.tag(70, (1 if v.frozen else 0) | (4 if v.locked else 0))
        c = v.color if v.color <= 255 else colors.nearest_index(v.color & 0xffffff)
        o.tag(62, c if v.on else -c)
        if v.color > 255 and self.version >= "AC1018":
            o.tag(420, v.color & 0xffffff)
        o.tag(6, self.name(v.linetype))
        if self.version >= "AC1015":
            o.tag(290, 1 if v.plot else 0)
            o.tag(370, v.weight if v.weight != -1 else -3)

    def _ltype_entry(self, v: Linetype, owner: str) -> None:
        o = self.out
        self._entry_head("LTYPE", "AcDbLinetypeTableRecord", v, v.name, owner)
        o.tag(70, 0)
        o.tag(3, self.s(v.description))
        o.tag(72, 65)
        o.tag(73, len(v.pattern))
        o.tag(40, float(sum(abs(x) for x in v.pattern)))
        for x in v.pattern:
            o.tag(49, float(x))
            if self.modern:
                o.tag(74, 0)

    def _style_entry(self, v: TextStyle, owner: str) -> None:
        o = self.out
        self._entry_head("STYLE", "AcDbTextStyleTableRecord", v, v.name, owner)
        o.tag(70, 0)
        o.tag(40, float(v.height))
        o.tag(41, float(v.width_factor))
        o.tag(50, math.degrees(v.oblique))
        o.tag(71, 0)
        o.tag(42, 2.5)
        if not self.modern:
            o.tag(3, "txt")
        else:
            o.tag(3, self.s(v.font if "." in v.font else f"{v.font.lower()}.ttf"))
        o.tag(4, "")

    def _dim_entry(self, v: DimStyle, owner: str) -> None:
        o = self.out
        self._entry_head("DIMSTYLE", "AcDbDimStyleTableRecord", v, v.name, owner)
        o.tag(70, 0)
        for code, value in ((3, self.s(v.prefix_suffix)), (40, float(v.scale)), (41, float(v.arrow_size)), (42, float(v.ext_offset)), (43, float(v.baseline_step)),
                            (44, float(v.ext_beyond)), (73, 1 if v.text_inside_horizontal else 0), (74, 1 if v.text_outside_horizontal else 0), (77, v.text_above), (78, v.suppress_zeros),
                            (140, float(v.text_height)), (141, float(v.center_mark)), (142, float(v.tick_size)), (144, float(v.linear_factor)), (147, float(v.text_gap))):
            o.tag(code, value)
        if self.modern:
            for code, value in ((179, v.angle_decimals), (271, v.decimals), (277, v.unit_format)):
                o.tag(code, value)

    def _vport_table(self) -> None:
        """Where a program opens this drawing: on what's in it, not on an empty corner far away from it."""
        o = self.out
        box = self.doc.extents() or (0.0, 0.0, 100.0, 100.0)
        cx, cy = (box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5
        height = max(box[3] - box[1], (box[2] - box[0]) * 0.625, 1e-6) * 1.1
        owner = self._table_head("VPORT", 1)
        o.tag(0, "VPORT")
        o.tag(5, self.handles.new())
        o.tag(330, owner)
        o.tag(100, "AcDbSymbolTableRecord")
        o.tag(100, "AcDbViewportTableRecord")
        o.tag(2, "*Active")
        o.tag(70, 0)
        for code, value in ((10, 0.0), (20, 0.0), (11, 1.0), (21, 1.0), (12, float(cx)), (22, float(cy)), (13, 0.0), (23, 0.0), (14, 1.0), (24, 1.0), (15, 1.0), (25, 1.0),
                            (16, 0.0), (26, 0.0), (36, 1.0), (17, 0.0), (27, 0.0), (37, 0.0), (40, float(height)), (41, 1.6), (42, 50.0), (43, 0.0), (44, 0.0), (50, 0.0), (51, 0.0),
                            (71, 0), (72, 1000), (73, 1), (74, 3), (75, 0), (76, 0), (77, 0), (78, 0)):
            o.tag(code, value)
        o.tag(0, "ENDTAB")

    def _record(self, name: str, handle: str, owner: str) -> None:
        o = self.out
        o.tag(0, "BLOCK_RECORD")
        o.tag(5, handle)
        o.tag(330, owner)
        o.tag(100, "AcDbSymbolTableRecord")
        o.tag(100, "AcDbBlockTableRecord")
        o.tag(2, self.name(name))

    def _block_record_table(self) -> None:
        o = self.out
        known = self.records
        read_as_blocks = {item["block"] for item in self.src.get("blocks") or () if isinstance(item, dict) and "block" in item}
        gone = read_as_blocks - {b.name.casefold() for b in self.doc.blocks}
        new_names = [b for b in self.doc.blocks if b.name.casefold() not in known and not self._read_as_a_block(b)]
        wanted = [text for key, (_, text) in known.items() if key not in gone]
        owner = "0"
        added = len(new_names) + len(self.dim_blocks)
        if self.record_head:
            head = self.record_head
            if gone or added:
                head = retag(head, {70: len(wanted) + added}, common_only=False)
            o.raw(head)
            found = _hex_of(head)
            owner = found if found else "0"
        else:
            count = len(wanted) + added + (0 if self.src else 2)
            owner = self._table_head("BLOCK_RECORD", count)
        for text in wanted:
            o.raw(text)
        if not self.src:
            self._record("*Model_Space", self.model_owner, owner)
            self._record("*Paper_Space", self.paper_owner, owner)
        for b in new_names:
            self._record(b.name, self.block_records[b.name.casefold()], owner)
        for name, handle, _ in self.dim_blocks.values():
            self._record(name, handle, owner)
        if self.record_tail:
            o.raw(self.record_tail)
        elif not self.record_head:
            o.tag(0, "ENDTAB")

    # ------------------------------------------------------------------------------------------------------- blocks

    def _block_frame(self, name: str, owner: str, base=(0.0, 0.0), flags: int = 0, description: str = "", handle: str | None = None) -> tuple[str, str]:
        """A BLOCK heading and its ENDBLK, for a block with nothing from a file to use."""
        head, tail = _Out(), _Out()
        head.tag(0, "BLOCK")
        if self.handled:
            head.tag(5, handle or self.handles.new())
        if self.modern:
            if owner != "0":
                head.tag(330, owner)
            head.tag(100, "AcDbEntity")
        head.tag(8, "0")
        if self.modern:
            head.tag(100, "AcDbBlockBegin")
        head.tag(2, self.name(name))
        head.tag(70, flags)
        head.pt(10, base)
        head.tag(3, self.name(name))
        head.tag(1, "")
        if description and self.modern:
            head.tag(4, self.s(description))
        tail.tag(0, "ENDBLK")
        if self.handled:
            tail.tag(5, self.handles.new())
        if self.modern:
            if owner != "0":
                tail.tag(330, owner)
            tail.tag(100, "AcDbEntity")
        tail.tag(8, "0")
        if self.modern:
            tail.tag(100, "AcDbBlockEnd")
        return "\n".join(head.lines), "\n".join(tail.lines)

    def _blocks(self) -> None:
        o = self.out
        items = self.src.get("blocks")
        done: set[str] = set()
        if items is not None:
            for item in items:
                if "raw" in item:
                    o.raw(item["raw"])
                else:
                    b = self.doc.blocks._d.get(item["block"])
                    if b is not None:
                        done.add(item["block"])
                        self._block(b)
        elif not self.src and self.modern:
            for name, owner in (("*Model_Space", self.model_owner), ("*Paper_Space", self.paper_owner)):
                head, tail = self._block_frame(name, owner)
                o.raw(head)
                o.raw(tail)
        for b in self.doc.blocks:
            if b.name.casefold() not in done:
                self._block(b)
        for name, record, d in self.dim_blocks.values():
            head, tail = self._block_frame(name, record, flags=1)
            o.raw(head)
            for item in dims.drawn(d, self.doc.dim_style_of(d)):
                if type(item) is Text:
                    item = replace(item, text=_with_symbols(item.text))
                self._fresh(Entity(0, item, _IN_A_DIMENSION), record)
            o.raw(tail)

    def _block(self, b: BlockDef) -> None:
        o = self.out
        owner = self.block_records.get(b.name.casefold(), "0")
        src = b.source
        if isinstance(src, Raw) and self.src and src.text:
            head, tail = _split_block(src.text)
            if src.shape != replace(b, entities=(), source=None):
                head = self._block_frame(b.name, owner, b.base, b.flags, b.description, _hex_of(head))[0]
        else:
            head, tail = self._block_frame(b.name, owner, b.base, b.flags, b.description)
        o.raw(head)
        for e in sorted(b.entities, key=lambda e: (e.order, e.id)):
            self._entity(e, owner)
        o.raw(tail)

    # ----------------------------------------------------------------------------------------------------- entities

    def _entities(self) -> None:
        o = self.out
        paper = self.src.get("paper") or ()
        i, n = 0, len(paper)
        for e in self.doc.sorted_entities():
            while i < n and paper[i][0] < e.order:
                o.raw(paper[i][1])
                i += 1
            self._entity(e, self.model_owner)
        while i < n:
            o.raw(paper[i][1])
            i += 1

    def _entity(self, e: Entity, owner: str) -> None:
        src = e.source
        if self._as_is(e):
            if src.style == e.style:
                self.out.raw(src.text)
            elif src.text:
                self.out.raw(self._restyled(src.text, src.style, e.style))
            return
        if type(e.shape) is Foreign:
            self._explode(e, owner)
            return
        self._fresh(e, owner)

    def _restyled(self, text: str, old: Style | None, new: Style) -> str:
        """Only how it looks has changed, so only those lines do. Everything else about it, understood or not, stays."""
        changes: dict = {}
        if old is None or old.layer != new.layer:
            changes[8] = self.name(new.layer)
        if old is None or old.linetype != new.linetype:
            changes[6] = None if new.linetype.casefold() == "bylayer" else self.name(new.linetype)
        if old is None or old.color != new.color:
            c = new.color
            if colors.is_true(c):
                changes[62] = colors.nearest_index(c & 0xffffff)
                changes[420] = c & 0xffffff if self.version >= "AC1018" else None
            else:
                changes[62] = None if c == colors.BYLAYER else c
                changes[420] = None
            changes[430] = None
        if (old is None or old.weight != new.weight) and self.version >= "AC1015":
            changes[370] = None if new.weight == -1 else new.weight
        if old is None or old.ltscale != new.ltscale:
            changes[48] = None if new.ltscale == 1.0 else float(new.ltscale)
        return retag(text, changes) if changes else text

    def _explode(self, e: Entity, owner: str) -> None:
        """Something that can't be written as itself to this file. What it draws can be, and that's better than nothing."""
        s = e.shape
        if not s.block and not s.curves:
            self.report.lost[f"{s.dxf_type.lower()} object(s)"] += 1
            return
        self.report.converted[f"{s.dxf_type.lower()} object(s), as plain geometry"] += 1
        if s.block:
            self._fresh(Entity(e.id, Insert(s.block, s.at), e.style, e.order), owner)
        for c in s.curves:
            self._fresh(Entity(e.id, c, e.style, e.order), owner)

    def _begin(self, kind: str, e: Entity, owner: str) -> list[str]:
        """The start of any object, up to its own subclass. Returns the extended data that goes at its end."""
        o = self.out
        src = e.source
        head: list[str] = []
        tail: list[str] = []
        has_owner = False
        handle = None
        if isinstance(src, Raw) and self.src and src.text:
            head, tail, has_owner = _kept_parts(src.text)
            handle = _hex_of(src.text)
        o.tag(0, kind)
        if self.handled:
            o.tag(5, handle or self.handles.new())
        if self.modern:
            o.lines += head
            if owner != "0" and not has_owner:
                o.tag(330, owner)
            o.tag(100, "AcDbEntity")
        self._style_tags(e.style)
        return tail

    def _child(self, kind: str, style: Style, owner: str) -> None:
        o = self.out
        o.tag(0, kind)
        if self.handled:
            o.tag(5, self.handles.new())
        if self.modern:
            if owner != "0":
                o.tag(330, owner)
            o.tag(100, "AcDbEntity")
        self._style_tags(style)

    def _style_tags(self, style: Style) -> None:
        o = self.out
        o.tag(8, self.name(style.layer))
        if style.linetype.casefold() != "bylayer":
            o.tag(6, self.name(style.linetype))
        c = style.color
        if colors.is_true(c):
            o.tag(62, colors.nearest_index(c & 0xffffff))
            if self.version >= "AC1018":
                o.tag(420, c & 0xffffff)
        elif c != colors.BYLAYER:
            o.tag(62, c)
        if self.version >= "AC1015" and style.weight != -1:
            o.tag(370, style.weight)
        if style.ltscale != 1.0:
            o.tag(48, float(style.ltscale))

    def _sub(self, name: str) -> None:
        if self.modern:
            self.out.tag(100, name)

    def _fresh(self, e: Entity, owner: str) -> None:
        o = self.out
        s = e.shape
        k = type(s)
        sub = self._sub
        if k is Line:
            tail = self._begin("LINE", e, owner)
            sub("AcDbLine")
            o.pt(10, s.a)
            o.pt(11, s.b)
        elif k is Circle:
            tail = self._begin("CIRCLE", e, owner)
            sub("AcDbCircle")
            o.pt(10, s.c)
            o.tag(40, float(s.r))
        elif k is Arc:
            a = s.ccw()
            tail = self._begin("ARC", e, owner)
            sub("AcDbCircle")
            o.pt(10, a.c)
            o.tag(40, float(a.r))
            sub("AcDbArc")
            o.tag(50, math.degrees(a.start_angle) % 360.0)
            o.tag(51, math.degrees(a.start_angle + a.sweep) % 360.0)
        elif k is EllipseArc or k is Spline:
            if not self.modern:
                self._flattened(e, owner)
                return
            tail = self._ellipse(e, owner) if k is EllipseArc else self._spline(e, owner)
        elif k is Polyline:
            self._polyline(e, owner)
            return
        elif k is Node:
            tail = self._begin("POINT", e, owner)
            sub("AcDbPoint")
            o.pt(10, s.p)
        elif k is Solid:
            tail = self._begin("SOLID", e, owner)
            sub("AcDbTrace")
            pts = s.pts
            o.pt(10, pts[0])
            o.pt(11, pts[1])
            o.pt(12, pts[3] if len(pts) == 4 else pts[2])    # a file's order for a solid's corners is a Z, not round the outline
            o.pt(13, pts[2])
        elif k is Text:
            tail = self._begin("TEXT", e, owner)
            sub("AcDbText")
            self._text_tags(s)
            sub("AcDbText")
            if s.valign:
                o.tag(73, s.valign)
        elif k is MText:
            if not self.modern:
                self._mtext_as_lines(e, owner)
                return
            tail = self._mtext(e, owner)
        elif k is AttDef:
            tail = self._begin("ATTDEF", e, owner)
            sub("AcDbText")
            self._text_tags(s.attrib.text)
            sub("AcDbAttributeDefinition")
            o.tag(3, self.s(s.attrib.prompt))
            o.tag(2, self.name(s.attrib.tag))
            o.tag(70, (1 if s.attrib.invisible else 0) | (2 if s.attrib.constant else 0))
            if s.attrib.text.valign:
                o.tag(74, s.attrib.text.valign)
        elif k is Dimension:
            tail = self._dimension(e, owner)
        elif k is Insert:
            tail = self._begin("INSERT", e, owner)
            sub("AcDbMInsertBlock" if s.columns > 1 or s.rows > 1 else "AcDbBlockReference")
            if s.attribs:
                o.tag(66, 1)
            o.tag(2, self.name(s.block))
            o.pt(10, s.p)
            o.tag(41, float(s.sx))
            o.tag(42, float(s.sy))
            o.tag(43, 1.0)
            if s.rotation:
                o.tag(50, math.degrees(s.rotation) % 360.0)
            if s.columns != 1:
                o.tag(70, s.columns)
            if s.rows != 1:
                o.tag(71, s.rows)
            if s.column_spacing:
                o.tag(44, float(s.column_spacing))
            if s.row_spacing:
                o.tag(45, float(s.row_spacing))
            o.lines += tail
            tail = []
            for a in s.attribs:
                self._attrib(a, e.style, owner)
            if s.attribs:
                self._child("SEQEND", e.style, owner)
        else:
            raise TypeError(f"no DXF form for {k.__name__}")
        o.lines += tail

    def _dimension(self, e: Entity, owner: str) -> list[str]:
        """A DIMENSION, pointing at the block that draws it. Which point goes under which code depends on its type, as DXF has it."""
        o = self.out
        d = e.shape
        planned = self.dim_blocks.get(e.id)
        block = planned[0] if planned is not None else d.block
        words = next((item for item in dims.drawn(d, self.doc.dim_style_of(d)) if type(item) is Text), None)
        middle = d.text_at if d.text_at is not None else (words.p if words is not None else d.at)
        tail = self._begin("DIMENSION", e, owner)
        self._sub("AcDbDimension")
        o.tag(2, self.name(block))
        if d.measure == "angular":
            kind, subclass = (2, "AcDb2LineAngularDimension") if len(d.pts) == 4 else (5, "AcDb3PointAngularDimension")
            first = d.pts[3] if len(d.pts) == 4 else d.at
        elif d.measure in ("linear", "aligned"):
            kind, subclass = _DIM_TYPES[d.measure]
            u = (d.pts[1] - d.pts[0]).unit() if d.measure == "aligned" and d.pts[1] != d.pts[0] else Vec(math.cos(d.angle), math.sin(d.angle))
            n = u.perp()
            first = d.pts[1] + n * (d.at - d.pts[1]).dot(n)      # where the second extension line meets the dimension line
        else:
            kind, subclass = _DIM_TYPES[d.measure]
            c, r = d.pts[0], d.pts[0].distance(d.pts[1])
            w = d.at - c if d.at != c else d.pts[1] - c
            edge = c + (w.unit() if w.length2() > 0.0 else Vec(1.0, 0.0)) * r
            first = c if d.measure == "radius" else c * 2.0 - edge
        o.pt(10, first)
        o.pt(11, middle)
        o.tag(70, kind | 32 | (128 if d.text_at is not None else 0))
        if self.version >= "AC1015":
            o.tag(71, 5)
        if d.text:
            o.tag(1, self.s(_with_symbols(d.text)))
        o.tag(3, self.name(d.style))
        if d.measure in ("linear", "aligned"):
            self._sub("AcDbAlignedDimension")
            o.pt(13, d.pts[0])
            o.pt(14, d.pts[1])
            if d.measure == "linear":
                o.tag(50, math.degrees(d.angle) % 360.0)
                self._sub(subclass)
        elif d.measure == "angular":
            self._sub(subclass)
            if len(d.pts) == 4:
                o.pt(13, d.pts[0])
                o.pt(14, d.pts[1])
                o.pt(15, d.pts[2])
                o.pt(16, d.at)
            else:
                o.pt(13, d.pts[1])
                o.pt(14, d.pts[2])
                o.pt(15, d.pts[0])
        else:
            self._sub(subclass)
            o.pt(15, edge)
            o.tag(40, 0.0)
        if d.scale != 1.0 and not any(line.strip() == "ACAD" for line in tail[1::2]):
            # its own size, as an override of its style's, which is where AutoCAD keeps one
            style = self.doc.dim_style_of(d)
            own = style.scale if style.scale > 0.0 else 1.0
            tail = ["1001", "ACAD", "1000", "DSTYLE", "1002", "{", "1070", "40", "1040", fmt_float(own * d.scale), "1002", "}"] + tail
        return tail

    def _ellipse(self, e: Entity, owner: str) -> list[str]:
        o = self.out
        a = e.shape.ccw()
        tail = self._begin("ELLIPSE", e, owner)
        self._sub("AcDbEllipse")
        o.pt(10, a.c)
        o.pt(11, a.major)
        o.tag(210, 0.0)
        o.tag(220, 0.0)
        o.tag(230, 1.0)
        o.tag(40, float(a.ratio))
        o.tag(41, float(a.start_param))
        o.tag(42, float(a.start_param + a.sweep))
        return tail

    def _spline(self, e: Entity, owner: str) -> list[str]:
        o = self.out
        s = e.shape
        tail = self._begin("SPLINE", e, owner)
        self._sub("AcDbSpline")
        o.tag(210, 0.0)
        o.tag(220, 0.0)
        o.tag(230, 1.0)
        o.tag(70, (3 if s.periodic else 0) | (4 if s.weights else 0) | 8)
        o.tag(71, s.degree)
        o.tag(72, len(s.knots))
        o.tag(73, len(s.ctrl))
        o.tag(74, len(s.fit))
        for u in s.knots:
            o.tag(40, float(u))
        for w in s.weights:
            o.tag(41, float(w))
        for p in s.ctrl:
            o.pt(10, p)
        for p in s.fit:
            o.pt(11, p)
        return tail

    def _mtext(self, e: Entity, owner: str) -> list[str]:
        o = self.out
        s = e.shape
        tail = self._begin("MTEXT", e, owner)
        self._sub("AcDbMText")
        o.pt(10, s.p)
        o.tag(40, float(s.height))
        o.tag(41, float(s.width))
        o.tag(71, s.attach)
        o.tag(72, 1)
        self._long_text(s.text)
        if s.style != "Standard":
            o.tag(7, self.name(s.style))
        if s.rotation:
            o.tag(11, math.cos(s.rotation))
            o.tag(21, math.sin(s.rotation))
            o.tag(31, 0.0)
        if s.spacing != 1.0:
            o.tag(44, float(s.spacing))
        return tail

    def _flattened(self, e: Entity, owner: str) -> None:
        s = e.shape
        box = s.bbox()
        pts = s.flatten(max(box[2] - box[0], box[3] - box[1], 1e-9) * 1e-4)
        closed = bool(s.closed)
        if closed and len(pts) > 1:
            pts = pts[:-1]
        self.report.converted[("ellipses" if type(s) is EllipseArc else "splines") + ", as fine polylines"] += 1
        self._polyline(Entity(e.id, Polyline(tuple(pts), closed=closed), e.style, e.order), owner)

    def _mtext_as_lines(self, e: Entity, owner: str) -> None:
        s = e.shape
        self.report.converted["paragraph text, as lines of text"] += 1
        along = Vec(math.cos(s.rotation), math.sin(s.rotation))
        up = along.perp()
        row = 0
        for line in plain_mtext(s.text):
            for piece in _wrapped(line, s.width, s.height):
                t = Text(s.p - up * (s.height + row * s.height * 1.667 * s.spacing), s.height, piece, s.rotation, s.style)
                self._fresh(Entity(e.id, t, e.style, e.order), owner)
                row += 1

    def _polyline(self, e: Entity, owner: str) -> None:
        o = self.out
        s = e.shape
        if self.version >= "AC1014":
            tail = self._begin("LWPOLYLINE", e, owner)
            self._sub("AcDbPolyline")
            o.tag(90, len(s.pts))
            o.tag(70, 1 if s.closed else 0)
            if s.width:
                o.tag(43, float(s.width))
            for i, p in enumerate(s.pts):
                o.tag(10, float(p[0]))
                o.tag(20, float(p[1]))
                if s.widths:
                    o.tag(40, float(s.widths[i][0]))
                    o.tag(41, float(s.widths[i][1]))
                b = s.bulge(i)
                if b:
                    o.tag(42, float(b))
            o.lines += tail
            return
        tail = self._begin("POLYLINE", e, owner)
        self._sub("AcDb2dPolyline")
        o.tag(66, 1)
        o.pt(10, (0.0, 0.0))
        o.tag(70, 1 if s.closed else 0)
        if s.width:
            o.tag(40, float(s.width))
            o.tag(41, float(s.width))
        o.lines += tail
        for i, p in enumerate(s.pts):
            self._child("VERTEX", e.style, owner)
            self._sub("AcDbVertex")
            self._sub("AcDb2dVertex")
            o.pt(10, p)
            if s.widths:
                o.tag(40, float(s.widths[i][0]))
                o.tag(41, float(s.widths[i][1]))
            b = s.bulge(i)
            if b:
                o.tag(42, float(b))
        self._child("SEQEND", e.style, owner)

    def _attrib(self, a: Attrib, style: Style, owner: str) -> None:
        o = self.out
        self._child("ATTRIB", style, owner)
        self._sub("AcDbText")
        self._text_tags(a.text)
        self._sub("AcDbAttribute")
        o.tag(2, self.name(a.tag))
        o.tag(70, (1 if a.invisible else 0) | (2 if a.constant else 0))
        if a.text.valign:
            o.tag(74, a.text.valign)

    def _text_tags(self, s: Text) -> None:
        o = self.out
        words = self.s(s.text)
        limit = 2049 if self.version >= "AC1021" else 255
        if len(words.encode(self.encoding, "replace")) > limit:
            raise SaveError(f"A line of text is longer than this version of DXF can hold ({limit} characters): \"{s.text[:30]}...\". Shorten it, or use paragraph text.")
        # a reader that doesn't know about alignment still gets it in about the right place
        o.pt(10, s.box()[0] if s.halign in (1, 2, 4) or s.valign else s.p)
        o.tag(40, float(s.height))
        o.tag(1, words)
        if s.rotation:
            o.tag(50, math.degrees(s.rotation) % 360.0)
        if s.width_factor != 1.0:
            o.tag(41, float(s.width_factor))
        if s.oblique:
            o.tag(51, math.degrees(s.oblique))
        if s.style != "Standard":
            o.tag(7, self.name(s.style))
        flags = (2 if s.mirrored_x else 0) | (4 if s.mirrored_y else 0)
        if flags:
            o.tag(71, flags)
        if s.halign:
            o.tag(72, s.halign)
        if s.halign or s.valign:
            o.pt(11, s.p2 if s.p2 is not None else s.p)

    def _long_text(self, text: str) -> None:
        """Paragraph text is held in groups of up to 250 characters, as code 3s and then a code 1."""
        o = self.out
        t = self.s(text.replace("\r\n", "\\P").replace("\n", "\\P"))
        size = 250 if t.isascii() or self.version >= "AC1021" else 120
        while len(t) > size:
            cut = size
            # never split an escape: \U+XXXX, \P or a caret and what follows it
            while cut > size - 16 and ("\\" in t[cut - 7:cut] or t[cut - 1] == "^"):
                cut -= 1
            o.tag(3, t[:cut])
            t = t[cut:]
        o.tag(1, t)

    # ------------------------------------------------------------------------------------------------------ objects

    def _objects(self) -> None:
        o = self.out
        if self.src:
            o.raw((self.src.get("sections") or {}).get("OBJECTS", ""))
            return
        if not self.modern:
            return
        root, group = self.handles.new(), self.handles.new()
        o.tag(0, "DICTIONARY")
        o.tag(5, root)
        o.tag(330, "0")
        o.tag(100, "AcDbDictionary")
        o.tag(281, 1)
        o.tag(3, "ACAD_GROUP")
        o.tag(350, group)
        o.tag(0, "DICTIONARY")
        o.tag(5, group)
        o.tag(330, root)
        o.tag(100, "AcDbDictionary")
        o.tag(281, 1)



def _with_symbols(text: str) -> str:
    """Ø, ° and ± as DXF spells them, which every program reading one understands, whatever its fonts."""
    for ch, code in _SYMBOLS:
        text = text.replace(ch, code)
    return text

def _wrapped(line: str, width: float, height: float) -> list[str]:
    """A line of paragraph text broken at its width, and in any case where one line of plain text would be too long for the file."""
    out: list[str] = []
    current = ""
    for word in line.split(" "):
        while len(word) > 240:
            if current:
                out.append(current)
                current = ""
            out.append(word[:240])
            word = word[240:]
        test = f"{current} {word}" if current else word
        if current and (len(test) > 240 or (width > 0.0 and text_width(test, height) > width)):
            out.append(current)
            current = word
        else:
            current = test
    out.append(current)
    return out


def _split_block(text: str) -> tuple[str, str]:
    """A block's kept text: its heading, and its ENDBLK. Nothing but those two has a group code 0 in it."""
    lines = text.split("\n")
    for i in range(2, len(lines) - 1, 2):
        if lines[i].strip() == "0":
            return "\n".join(lines[:i]), "\n".join(lines[i:])
    return text, ""


def chosen_version(doc: Document, version: str | None) -> str:
    source = doc.extras.get("dxf") if isinstance(doc.extras.get("dxf"), dict) else None
    if version is not None:
        return version
    return source.get("version", DEFAULT) if source else DEFAULT


def encode_dxf(doc: Document, version: str | None = None) -> tuple[bytes, WriteReport]:
    """The bytes of a DXF file, and what had to change to fit. `version` is like "AC1027": by default the version the drawing came from, or 2013."""
    w = _Writer(doc, chosen_version(doc, version))
    return w.write(), w.report


def write_dxf(doc: Document, version: str | None = None) -> bytes:
    return encode_dxf(doc, version)[0]


def _close(a, b, limit: float) -> bool:
    if a is None or b is None:
        return a is b
    return all(abs(x - y) <= limit for x, y in zip(a, b))


def check_written(data: bytes, doc: Document, report: WriteReport | None = None) -> None:
    """Read back what's about to take the file's name, and raise if it doesn't hold what's in the drawing. An object at a time, in order, when nothing
    had to change form. When something did, one object may have become several, so all that can be asked is that nothing went missing."""
    from .read import read_dxf
    back, _ = read_dxf(data)
    want, have = doc.sorted_entities(), back.sorted_entities()
    if report is not None and (report.converted or report.lost):
        if len(have) < len(want) - sum(report.lost.values()):
            raise ValueError(f"{len(want)} objects went in and only {len(have)} came back")
        return
    box = doc.extents(visible_only=False)
    size = max((abs(v) for v in box), default=1.0) if box else 1.0
    limit = 1e-9 + size * 1e-9
    if len(want) != len(have):
        raise ValueError(f"{len(want)} objects went in and {len(have)} came back")
    for a, b in zip(want, have):
        if type(a.shape) is not type(b.shape):
            raise ValueError(f"a {type(a.shape).__name__} came back as a {type(b.shape).__name__}")
        p, q = doc.shape_bbox(a.shape), back.shape_bbox(b.shape)
        if not _close(p, q, limit):
            raise ValueError(f"a {type(a.shape).__name__} at {p} came back at {q}")
    # a dimension written afresh brings a block of its own
    drawing_dims = {block_used_by(e.shape).casefold() for e in back.entities.values() if type(e.shape) is Dimension}
    drawing_dims |= {block_used_by(e.shape).casefold() for b in back.blocks for e in b.entities if type(e.shape) is Dimension}
    went, came = {b.name.casefold() for b in doc.blocks}, {b.name.casefold() for b in back.blocks}
    if not went <= came or not (came - went) <= drawing_dims:
        raise ValueError("the blocks that came back aren't the ones that went in")
    for b in doc.blocks:
        if len(b.entities) != len(back.blocks[b.name].entities):
            raise ValueError(f"block {b.name} came back with a different number of objects")
