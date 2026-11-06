# SPDX-License-Identifier: GPL-3.0-or-later
"""Cleaning up a drawing: what's drawn twice, what has no size, lines that overlap, and names nothing uses.

`plan` works out what would change without changing anything, so it can be shown first. `apply` makes exactly those changes, in one
Transaction, so one undo puts everything back.

Lines are joined only where they lie on one line, overlap or meet end to end, and look the same (layer, colour, line type, weight). What a
dimension or hatch is attached to isn't joined, so nothing lets go. A layer, block, line type or style is unused when nothing in the
drawing names it; one that came from a file and is named or pointed at by its handle anywhere in what the file holds that isn't drawn
here, such as paper space, its header or a dimension style's arrows, is kept, because something there needs it.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

from .curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from .model import BYBLOCK_TYPE, BYLAYER_TYPE, CONTINUOUS, BlockDef, Document, Transaction, block_used_by
from .rawsource import Raw
from .shapes import AttDef, Dimension, Foreign, Hatch, Insert, MText, Text
from .vec import Vec, tol


@dataclass
class Plan:
    duplicates: list[int] = field(default_factory=list)
    empty: list[int] = field(default_factory=list)              # no length, no size or no text
    joined: list[tuple[list[int], Line]] = field(default_factory=list)
    layers: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    linetypes: list[str] = field(default_factory=list)
    text_styles: list[str] = field(default_factory=list)
    dim_styles: list[str] = field(default_factory=list)

    @property
    def gone(self) -> list[int]:
        """Every object that goes, joined ones included."""
        return sorted(set(self.duplicates) | set(self.empty) | {i for ids, _ in self.joined for i in ids})

    def __bool__(self) -> bool:
        return bool(self.duplicates or self.empty or self.joined or self.layers or self.blocks or self.linetypes or self.text_styles or self.dim_styles)

    def sentence(self) -> str:
        """What it would do, in a few words."""
        parts = []
        if self.duplicates:
            parts.append(f"{len(self.duplicates)} drawn twice")
        if self.empty:
            parts.append(f"{len(self.empty)} with no size")
        if self.joined:
            n = sum(len(ids) for ids, _ in self.joined)
            parts.append(f"{n} overlapping lines made into {len(self.joined)}")
        names = []
        for what, items in (("layer", self.layers), ("block", self.blocks), ("line type", self.linetypes), ("text style", self.text_styles),
                            ("dimension style", self.dim_styles)):
            if items:
                names.append(f"{len(items)} {what}{'s' if len(items) != 1 else ''}")
        if names:
            parts.append(", ".join(names) + " that nothing uses")
        return "; ".join(parts) if parts else "nothing to clean up"


def _empty(s) -> bool:
    k = type(s)
    eps = tol(*(s.bbox() if hasattr(s, "bbox") and k not in (Text, MText, AttDef) else (0.0,)))
    if k is Line:
        return s.a.distance(s.b) <= eps
    if k is Arc:
        return s.r <= eps or abs(s.sweep) * s.r <= eps
    if k is Circle:
        return s.r <= eps
    if k is EllipseArc:
        return s.major.length() <= eps or s.ratio * s.major.length() <= eps or abs(s.sweep) <= 1e-12
    if k is Polyline:
        return all(p.distance(s.pts[0]) <= eps for p in s.pts) and not s.width
    if k is Spline:
        return all(p.distance(s.ctrl[0]) <= eps for p in s.ctrl)
    if k is Text:
        return not s.text.strip()
    if k is MText:
        return not "".join(s.lines()).strip()
    return False


def _key(s):
    """What makes two objects the same drawing: a line either way round is one line."""
    k = type(s)
    if k is Line:
        return ("line",) + tuple(sorted((tuple(s.a), tuple(s.b))))
    if k is Arc:
        a = s.ccw()
        return ("arc", tuple(a.c), a.r, round(a.start_angle % math.tau, 12), round(a.sweep, 12))
    return s


def _attached(doc: Document) -> set[int]:
    out: set[int] = set()
    for e in doc.entities.values():
        s = e.shape
        if type(s) is Dimension:
            out.update(r[0] for r in s.refs if r is not None)
        elif type(s) is Hatch:
            out.update(s.refs)
    return out


def _collinear_runs(doc: Document, lines: list, attached: set[int]) -> list[tuple[list[int], Line]]:
    """Lines on one line, with the same look, that overlap or meet: each run as one line from its first end to its last."""
    groups: dict[tuple, list] = {}
    box = doc.extents(visible_only=False) or (0.0, 0.0, 0.0, 0.0)
    eps = tol(*box) * 100.0         # how far apart lines can be and still be one line: the same for every line, so they're grouped alike
    for e in lines:
        if e.id in attached:
            continue
        a, b = e.shape.a, e.shape.b
        d = b - a
        n = d.length()
        if n == 0.0:
            continue
        u = d / n
        if u[0] < 0.0 or (u[0] == 0.0 and u[1] < 0.0):
            u = -u
        normal = u.perp()
        offset = a.dot(normal)
        key = (e.style, round(u[0], 9), round(u[1], 9), round(offset / max(eps, 1e-12)))
        groups.setdefault(key, []).append((e, u, eps))
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        u = members[0][1]
        spans = sorted(((min(e.shape.a.dot(u), e.shape.b.dot(u)), max(e.shape.a.dot(u), e.shape.b.dot(u)), e, eps) for e, _, eps in members),
                       key=lambda x: x[0])
        run = [spans[0]]
        end = spans[0][1]
        for sp in spans[1:]:
            if sp[0] <= end + sp[3]:
                run.append(sp)
                end = max(end, sp[1])
            else:
                if len(run) > 1:
                    out.append(_join(run, u))
                run, end = [sp], sp[1]
        if len(run) > 1:
            out.append(_join(run, u))
    return out


def _join(run, u: Vec) -> tuple[list[int], Line]:
    start = min(run, key=lambda x: x[0])
    finish = max(run, key=lambda x: x[1])
    s, f = start[2].shape, finish[2].shape
    a = s.a if s.a.dot(u) <= s.b.dot(u) else s.b         # an end point that's really there, never one worked out
    b = f.b if f.b.dot(u) >= f.a.dot(u) else f.a
    return sorted(x[2].id for x in run), Line(a, b)


_OWNER = "330"      # the object that owns this one, such as the block it's in: not a use of it


def _values(text: str, out: Counter, dxf: bool = True) -> None:
    """Count every value in an object's text as a DXF file has it, which is how it names a layer or a block or points at one by its handle.
    Who owns it isn't counted: an object inside a block points at that block. Text that isn't DXF has every line counted."""
    lines = text.split("\n")
    if not dxf:
        out.update(x.strip().casefold() for x in lines if x.strip())
        return
    for k in range(0, len(lines) - 1, 2):
        if lines[k].strip() == _OWNER:
            continue
        v = lines[k + 1].strip()
        if v:
            out[v.casefold()] += 1


def _handle(text: str) -> str:
    """The handle in an object's text: code 5 just after its type, or 105 for a dimension style."""
    lines = text.split("\n")
    for k in range(2, min(len(lines), 8) - 1, 2):
        if lines[k].strip() in ("5", "105"):
            return lines[k + 1].strip()
    return ""


class _Mentions:
    """What the file a drawing came from says that isn't drawn here: its header, the tables and objects we don't model, paper space, and
    what can't be changed here. Something unused here that's named or pointed at there is kept, because something there needs it."""

    def __init__(self, doc: Document):
        self.counts: Counter = Counter()
        self.records: dict[str, str] = {}
        dxf = doc.extras.get("dxf") if isinstance(doc.extras.get("dxf"), dict) else {}
        texts: list[str] = []

        def collect(v) -> None:
            if isinstance(v, str):
                texts.append(v)
            elif isinstance(v, dict):
                for x in v.values():
                    collect(x)
            elif isinstance(v, (list, tuple)):
                for x in v:
                    collect(x)
        for key, value in doc.extras.items():
            if key != "dxf":
                collect(value)
        for text in texts:
            _values(text, self.counts, dxf=False)
        texts.clear()
        collect(dxf.get("header"))
        collect(dxf.get("sections"))
        collect(dxf.get("paper"))
        collect([item.get("raw") for item in dxf.get("blocks") or () if isinstance(item, dict)])
        tables = dxf.get("tables") if isinstance(dxf.get("tables"), dict) else {}
        for name, text in (tables.get("raw") or {}).items():
            if name == "BLOCK_RECORD":
                # every block's record is in here, and it goes with the block; what points at a record is elsewhere
                self.records = _records(text) if isinstance(text, str) else {}
            else:
                collect(text)
        collect([item.get("raw") for items in (tables.get("entries") or {}).values() if isinstance(items, list)
                 for item in items if isinstance(item, dict)])
        collect(tables.get("heads"))        # a table's head can point at its entries, as a dimension style table does
        collect(tables.get("tails"))
        for text in texts:
            _values(text, self.counts)
        for table in (doc.layers, doc.linetypes, doc.text_styles, doc.dim_styles):
            for v in table:
                if isinstance(v.source, Raw) and v.source.text:
                    _values(v.source.text, self.counts)
        for e in doc.entities.values():
            if type(e.shape) is Foreign and isinstance(e.source, Raw):
                _values(e.source.text, self.counts)
        for b in doc.blocks:
            for e in b.entities:
                if type(e.shape) is Foreign and isinstance(e.source, Raw):
                    _values(e.source.text, self.counts)

    def need(self, v, own: tuple[str, ...] = ()) -> bool:
        """Whether something from the file is named or pointed at by anything else in it. `own` is its own text, which doesn't count."""
        if not isinstance(v.source, Raw):
            return False
        mine: Counter = Counter()
        for text in own:
            _values(text, mine)
        handle = _handle(v.source.text) if v.source.text and not isinstance(v, BlockDef) else self.records.get(v.name.casefold(), "")
        return any(self.counts[x.casefold()] - mine[x.casefold()] > 0 for x in (v.name, handle) if x)


def _records(text: str) -> dict[str, str]:
    """Each block record's handle, by the block's name."""
    out: dict[str, str] = {}
    lines = text.split("\n")
    name = handle = ""
    for k in range(0, len(lines) - 1, 2):
        c, v = lines[k].strip(), lines[k + 1].strip()
        if c == "0":
            if name and handle:
                out.setdefault(name.casefold(), handle)
            name = handle = ""
        elif c == "2" and not name:
            name = v
        elif c == "5" and not handle:
            handle = v
    if name and handle:
        out.setdefault(name.casefold(), handle)
    return out


def plan(doc: Document) -> Plan:
    p = Plan()
    attached = _attached(doc)
    seen: dict[tuple, int] = {}
    lines = []
    # what a dimension or hatch is attached to is never the copy that goes, so it's seen first
    for e in sorted(doc.sorted_entities(), key=lambda e: e.id not in attached):
        s = e.shape
        if type(s) is Foreign:
            continue
        if _empty(s):
            if e.id not in attached:
                p.empty.append(e.id)
            continue
        key = (e.style, _key(s))
        try:
            hash(key)
        except TypeError:
            continue
        if key in seen and e.id not in attached:
            p.duplicates.append(e.id)
            continue
        seen[key] = e.id
        if type(s) is Line:
            lines.append(e)
    p.joined = _collinear_runs(doc, lines, attached)
    gone = set(p.gone)
    top = [e for i, e in doc.entities.items() if i not in gone]
    file = _Mentions(doc) if any(isinstance(v.source, Raw) for table in (doc.layers, doc.linetypes, doc.text_styles, doc.dim_styles)
                                 for v in table) or any(isinstance(b.source, Raw) for b in doc.blocks) else None

    def keep(v, own: tuple[str, ...] = ()) -> bool:
        return file is not None and file.need(v, own)

    # a block is used if anything that stays places it, directly or through another block that's used
    placed = {block_used_by(e.shape).casefold() for e in top if block_used_by(e.shape)}
    grew = True
    while grew:
        grew = False
        for b in doc.blocks:
            if b.name.casefold() in placed:
                for e in b.entities:
                    name = block_used_by(e.shape).casefold()
                    if name and name not in placed:
                        placed.add(name)
                        grew = True
    for b in doc.blocks:
        if b.name.casefold() in placed or b.flags or (b.anonymous and isinstance(b.source, Raw)):
            continue
        own = tuple(e.source.text for e in b.entities if type(e.shape) is Foreign and isinstance(e.source, Raw))
        if not keep(b, own):
            p.blocks.append(b.name)
    going = {name.casefold() for name in p.blocks}
    staying = top + [e for b in doc.blocks if b.name.casefold() not in going for e in b.entities]
    current = doc.current
    used_layers = {e.style.layer.casefold() for e in staying} | {"0", "defpoints", current.layer.casefold()}
    used_types = {e.style.linetype.casefold() for e in staying} | {v.linetype.casefold() for v in doc.layers} | {current.linetype.casefold()}
    used_types |= {BYLAYER_TYPE.casefold(), BYBLOCK_TYPE.casefold(), CONTINUOUS.casefold()}
    used_text = {current.text_style.casefold(), "standard"} | {v.text_style.casefold() for v in doc.dim_styles}
    used_dims = {current.dim_style.casefold(), "standard"}
    for e in staying:
        s = e.shape
        if type(s) is Text:
            used_text.add(s.style.casefold())
        elif type(s) is MText:
            used_text.add(s.style.casefold())
        elif type(s) is AttDef:
            used_text.add(s.attrib.text.style.casefold())
        elif type(s) is Insert:
            used_text.update(a.text.style.casefold() for a in s.attribs)
        elif type(s) is Dimension:
            used_dims.add(s.style.casefold())

    def unused(table, used: set[str]) -> list[str]:
        return [v.name for v in table if v.name.casefold() not in used and not keep(v, (v.source.text,) if isinstance(v.source, Raw) else ())]
    p.layers = unused(doc.layers, used_layers)
    p.linetypes = unused(doc.linetypes, used_types)
    p.text_styles = unused(doc.text_styles, used_text)
    p.dim_styles = unused(doc.dim_styles, used_dims)
    return p


def apply(t: Transaction, p: Plan) -> None:
    """Make the planned changes. The blocks go after what placed them, and layers after what was on them."""
    doc = t.doc
    looks = {ids[0]: doc.entities[ids[0]].style for ids, _ in p.joined if ids[0] in doc.entities}
    for i in p.gone:
        if i in doc.entities:
            t.remove(i)
    for ids, line in p.joined:
        t.add(line, looks.get(ids[0]))
    left = [name for name in p.blocks if name in doc.blocks]
    while left:
        free = [name for name in left if not doc.block_use(name)]      # a block that goes may place one that goes too
        if not free:
            break
        for name in free:
            t.remove_block(name)
        left = [name for name in left if name not in free]
    for name in p.layers:
        if name in doc.layers and not doc.layer_use(name):
            t.remove_layer(name)
    for name in p.linetypes:
        t.remove_linetype(name)
    for name in p.text_styles:
        t.remove_text_style(name)
    for name in p.dim_styles:
        t.remove_dim_style(name)
