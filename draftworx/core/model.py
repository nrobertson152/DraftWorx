# SPDX-License-Identifier: GPL-3.0-or-later
"""The drawing: entities, layers, styles, blocks, and the one way of changing any of them.

Every value here is immutable, and every change goes through a Transaction, which records (table, key, old, new). Undo
and redo just replay those records in the other direction, so there is no command-specific undo code that could be
wrong, and a command that fails part way leaves nothing behind. The spatial index and layer caches are updated in the
same single place, so they can't disagree with the entities.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Iterator

from . import color as colors
from . import dim as dims
from .curves import Arc, Circle, EllipseArc, Polyline
from .shapes import Dimension, Foreign, Hatch, Insert, Shape
from .spatial import SpatialIndex
from .vec import BBox, Vec, bbox_union

BYLAYER_TYPE = "ByLayer"
BYBLOCK_TYPE = "ByBlock"
CONTINUOUS = "Continuous"
#: Line weight in hundredths of a millimetre, or one of these
WEIGHT_BYLAYER, WEIGHT_BYBLOCK, WEIGHT_DEFAULT = -1, -2, -3
WEIGHTS = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211)


class EditError(Exception):
    """A change that can't be made. The message is written for the person drawing."""


@dataclass(frozen=True, slots=True)
class Style:
    layer: str = "0"
    color: int = colors.BYLAYER
    linetype: str = BYLAYER_TYPE
    weight: int = WEIGHT_BYLAYER
    ltscale: float = 1.0


DEFAULT_STYLE = Style()


@dataclass(frozen=True, slots=True)
class Layer:
    name: str
    color: int = 7
    linetype: str = CONTINUOUS
    weight: int = WEIGHT_DEFAULT
    on: bool = True
    frozen: bool = False
    locked: bool = False
    plot: bool = True
    description: str = ""
    source: object = None

    @property
    def visible(self) -> bool:
        return self.on and not self.frozen


@dataclass(frozen=True, slots=True)
class Linetype:
    """`pattern`: positive is a dash, negative a gap, 0 a dot, in drawing units. Empty is continuous."""
    name: str
    description: str = ""
    pattern: tuple[float, ...] = ()
    source: object = None


@dataclass(frozen=True, slots=True)
class TextStyle:
    name: str
    font: str = "Arial"
    height: float = 0.0
    width_factor: float = 1.0
    oblique: float = 0.0
    source: object = None


@dataclass(frozen=True, slots=True)
class DimStyle:
    """The settings a dimension is drawn with. Named as DXF names them, for anyone who knows them, and kept to the ones
    that change what a dimension looks like. Others in a file are kept as they were (see `source`)."""
    name: str
    text_height: float = 2.5        # DIMTXT
    arrow_size: float = 2.5         # DIMASZ
    ext_offset: float = 0.625       # DIMEXO
    ext_beyond: float = 1.25        # DIMEXE
    text_gap: float = 0.625         # DIMGAP
    baseline_step: float = 3.75     # DIMDLI
    scale: float = 1.0              # DIMSCALE
    linear_factor: float = 1.0      # DIMLFAC
    decimals: int = 2               # DIMDEC
    angle_decimals: int = 0         # DIMADEC
    suppress_zeros: int = 8         # DIMZIN
    decimal_sep: str = "."          # DIMDSEP
    text_above: int = 1             # DIMTAD
    text_inside_horizontal: bool = False  # DIMTIH
    text_outside_horizontal: bool = False  # DIMTOH
    unit_format: int = 2            # DIMLUNIT
    prefix_suffix: str = ""         # DIMPOST
    text_style: str = "Standard"    # DIMTXSTY
    tick_size: float = 0.0          # DIMTSZ: above 0, ticks replace arrows
    center_mark: float = 2.5        # DIMCEN
    source: object = None


@dataclass(frozen=True, slots=True)
class Entity:
    id: int
    shape: Shape
    style: Style = DEFAULT_STYLE
    order: int = 0
    #: What a file said about this entity beyond the model, to be written back while the entity is unchanged.
    source: object = None


@dataclass(frozen=True, slots=True)
class BlockDef:
    name: str
    base: Vec = Vec(0.0, 0.0)
    entities: tuple[Entity, ...] = ()
    description: str = ""
    #: From a file: a reference to another drawing, an anonymous block and so on. Written back as it was.
    flags: int = 0
    source: object = None

    @property
    def anonymous(self) -> bool:
        return self.name.startswith("*")


@dataclass(frozen=True, slots=True)
class Settings:
    """The drawing's own settings. Part of undo, like anything else that changes what the drawing looks like."""
    units: str = "mm"               # what one drawing unit is
    linear_format: str = "decimal"  # decimal, engineering, architectural, fractional, scientific
    linear_decimals: int = 2
    angle_decimals: int = 1
    annotation_scale: float = 1.0   # the drawing's print scale, 1:n
    ltscale: float = 1.0
    point_mode: int = 0
    point_size: float = 0.0
    paper: str = ""                 # the sheet it's printed on ("A3"), or none chosen yet (sheets.py)
    landscape: bool = True
    sheet_x: float = 0.0            # where the sheet's lower-left corner is, in the drawing
    sheet_y: float = 0.0


@dataclass(slots=True)
class Current:
    """What the next thing drawn gets. Saved with the drawing but not part of undo: going back shouldn't change your pen."""
    layer: str = "0"
    color: int = colors.BYLAYER
    linetype: str = BYLAYER_TYPE
    weight: int = WEIGHT_BYLAYER
    text_style: str = "Standard"
    dim_style: str = "Standard"

    def style(self) -> Style:
        return Style(self.layer, self.color, self.linetype, self.weight)


class NameTable:
    """A table of named things. Names keep their spelling but compare without case, as they do in DXF."""
    __slots__ = ("_d",)

    def __init__(self) -> None:
        self._d: dict = {}

    def get(self, name: str):
        return self._d.get(name.casefold())

    def __contains__(self, name: str) -> bool:
        return name.casefold() in self._d

    def __getitem__(self, name: str):
        return self._d[name.casefold()]

    def __iter__(self) -> Iterator:
        """Sorted, so what's listed or saved never depends on the order things were made or undone."""
        return iter(sorted(self._d.values(), key=lambda v: v.name.casefold()))

    def __len__(self) -> int:
        return len(self._d)

    def _put(self, value) -> None:
        self._d[value.name.casefold()] = value

    def _drop(self, name: str) -> None:
        self._d.pop(name.casefold(), None)


# Each curve is indexed on its own, so a polyline with a thousand segments costs a thousand small boxes, not one big one.
_SUB = 1 << 24
CENTRE_SUB = _SUB - 1       # the point a curve has away from itself: the centre of an arc, an insertion point
ARC_SUB = 1 << 23           # ARC_SUB + i: the centre of a polyline's arc segment i, which may be far from the arc
MOST_SEGMENTS = ARC_SUB - 1


def block_used_by(shape: Shape) -> str:
    """The block this shape draws, or an empty string."""
    k = type(shape)
    return shape.block if k is Insert or k is Foreign or k is Dimension else ""


def with_refs_moved(shape: Shape, copies: dict[int, int]) -> Shape | None:
    """A copy of a dimension or hatch attached to the copies of what the original was attached to, when those were copied with it. None
    if there's nothing to change."""
    k = type(shape)
    if k is Dimension and shape.refs and any(r is not None and r[0] in copies for r in shape.refs):
        return replace(shape, refs=tuple((copies[r[0]], r[1]) if r is not None and r[0] in copies else r for r in shape.refs))
    if k is Hatch and shape.refs and any(r in copies for r in shape.refs):
        return replace(shape, refs=tuple(copies.get(r, r) for r in shape.refs))
    return None


def index_key(entity_id: int, sub: int = 0) -> int:
    return entity_id * _SUB + sub


def split_key(key: int) -> tuple[int, int]:
    return key // _SUB, key % _SUB


def polyline_entries(s: Polyline) -> list[tuple[int, BBox]]:
    """What the index holds for a polyline: each segment, the centre of each arc segment, and the centre of a closed one."""
    segs = s.segments
    out: list[tuple[int, BBox]] = [(i, seg.bbox()) for i, seg in enumerate(segs)] if segs else [(0, s.bbox())]
    if s.bulges:
        for i, seg in enumerate(segs):
            if type(seg) is Arc:
                out.append((ARC_SUB + i, (seg.c[0], seg.c[1], seg.c[0], seg.c[1])))
    if s.closed and (len(s.pts) > 2 or s.bulges):
        c = s.centroid()
        if c is not None:
            out.append((CENTRE_SUB, (c[0], c[1], c[0], c[1])))
    return out


@dataclass(frozen=True, slots=True)
class Change:
    table: str
    key: object
    old: object
    new: object


@dataclass(frozen=True, slots=True)
class Step:
    """One undoable thing the person did."""
    label: str
    changes: tuple[Change, ...]
    serial: int


class Transaction:
    """Group changes into one undo step. A `with` block that raises leaves the drawing exactly as it was."""

    def __init__(self, doc: "Document", label: str):
        self.doc = doc
        self.label = label
        self._changes: dict[tuple[str, object], Change] = {}
        self._open = False

    def __enter__(self) -> "Transaction":
        if self.doc._transaction is not None:
            raise EditError("another change is still in progress")
        self.doc._transaction = self
        self._open = True
        return self

    def __exit__(self, kind, value, tb) -> bool:
        if kind is None:
            try:
                self.doc._follow_up(self)
            except BaseException:
                self._open = False
                self.doc._transaction = None
                self.doc._apply_all(_reversed(list(self._changes.values())))
                raise
        self._open = False
        self.doc._transaction = None
        if kind is not None:
            self.doc._apply_all(_reversed(list(self._changes.values())))
            return False
        self.doc._commit(self.label, self._changes)
        return False

    def _record(self, table: str, key, new) -> None:
        if not self._open:
            raise EditError("this change has already finished")
        old = self.doc._get(table, key)
        if old is new or old == new:      # setting something to what it already is isn't a change, so it isn't an undo step
            return
        self.doc._set(table, key, old, new)
        prior = self._changes.get((table, key))
        if prior is not None:
            old = prior.old
        if old is new or old == new:
            self._changes.pop((table, key), None)
        else:
            self._changes[(table, key)] = Change(table, key, old, new)

    # ---- entities

    def add(self, shape: Shape, style: Style | None = None, source: object = None) -> int:
        doc = self.doc
        eid = doc._next_id
        doc._next_id += 1
        doc._check_shape(shape)
        self._record("entity", eid, Entity(eid, shape, style or doc.current.style(), eid, source))
        return eid

    def put(self, entity: Entity) -> None:
        """Add an entity whose id is already allotted, as loading or an undo-free import does."""
        self.doc._check_shape(entity.shape)
        if entity.id >= self.doc._next_id:
            self.doc._next_id = entity.id + 1
        self._record("entity", entity.id, entity)

    def replace(self, eid: int, shape: Shape | None = None, style: Style | None = None, order: int | None = None) -> None:
        e = self.doc.entities.get(eid)
        if e is None:
            raise EditError("that object no longer exists")
        if shape is not None:
            self.doc._check_shape(shape)
        new = Entity(eid, shape if shape is not None else e.shape, style if style is not None else e.style,
                     order if order is not None else e.order, e.source)
        self._record("entity", eid, new)

    def remove(self, eid: int) -> None:
        if eid not in self.doc.entities:
            raise EditError("that object no longer exists")
        self._record("entity", eid, None)

    # ---- tables

    def put_layer(self, layer: Layer) -> None:
        self._record("layer", layer.name.casefold(), layer)

    def remove_layer(self, name: str) -> None:
        if name.casefold() == "0":
            raise EditError("Layer 0 is always there. Other drawings and blocks rely on it.")
        if name.casefold() == self.doc.current.layer.casefold():
            raise EditError(f"Layer {name} is the current layer. Make another layer current first.")
        count = self.doc.layer_use(name)
        if count:
            raise EditError(f"Layer {name} still has {count} object{'s' if count != 1 else ''} on it.")
        self._record("layer", name.casefold(), None)

    def put_linetype(self, value: Linetype) -> None:
        self._record("linetype", value.name.casefold(), value)

    def put_text_style(self, value: TextStyle) -> None:
        self._record("textstyle", value.name.casefold(), value)

    def put_dim_style(self, value: DimStyle) -> None:
        self._record("dimstyle", value.name.casefold(), value)

    def remove_linetype(self, name: str) -> None:
        if name.casefold() in (BYLAYER_TYPE.casefold(), BYBLOCK_TYPE.casefold(), CONTINUOUS.casefold()):
            raise EditError(f"The {name} line type is always there.")
        self._record("linetype", name.casefold(), None)

    def remove_text_style(self, name: str) -> None:
        if name.casefold() == "standard":
            raise EditError("The Standard text style is always there.")
        self._record("textstyle", name.casefold(), None)

    def remove_dim_style(self, name: str) -> None:
        if name.casefold() == "standard":
            raise EditError("The Standard dimension style is always there.")
        self._record("dimstyle", name.casefold(), None)

    def put_block(self, value: BlockDef) -> None:
        if not value.name.strip():
            raise EditError("A block needs a name.")
        for e in value.entities:
            self.doc._check_shape(e.shape, inside=value.name)
            if e.id >= self.doc._next_id:
                self.doc._next_id = e.id + 1
        self._record("block", value.name.casefold(), value)

    def remove_block(self, name: str) -> None:
        count = self.doc.block_use(name)
        if count:
            raise EditError(f"Block {name} is still used {count} time{'s' if count != 1 else ''}.")
        self._record("block", name.casefold(), None)

    def set_settings(self, settings: Settings) -> None:
        self._record("settings", "settings", settings)


def _reversed(changes: Iterable[Change]) -> list[Change]:
    return [Change(c.table, c.key, c.new, c.old) for c in reversed(list(changes))]


class Document:
    def __init__(self) -> None:
        self.entities: dict[int, Entity] = {}
        self.layers = NameTable()
        self.linetypes = NameTable()
        self.text_styles = NameTable()
        self.dim_styles = NameTable()
        self.blocks = NameTable()
        self.settings = Settings()
        self.current = Current()
        self.index = SpatialIndex()
        #: What a source file held beyond the model, kept so it can be written back. See `dxf`.
        self.extras: dict = {}
        self._next_id = 1
        self._transaction: Transaction | None = None
        self._undo: list[Step] = []
        self._redo: list[Step] = []
        self._serial = 0
        self._saved_at = 0
        self._listeners: list[Callable[[tuple[Change, ...]], None]] = []
        self._hidden_layers: set[str] = set()
        self._locked_layers: set[str] = set()
        self._layer_counts: dict[str, int] = {}
        self._block_counts: dict[str, int] = {}
        self._block_bbox: dict[str, tuple[BlockDef, BBox | None]] = {}
        #: entity id -> the dimensions attached to it
        self._dependents: dict[int, set[int]] = {}
        self._install_required()

    def _install_required(self) -> None:
        """What every drawing must have. Part of the starting state, so it isn't an undo step."""
        self.layers._put(Layer("0"))
        for t in (Linetype(BYLAYER_TYPE), Linetype(BYBLOCK_TYPE), Linetype(CONTINUOUS, "Solid line")):
            self.linetypes._put(t)
        self.text_styles._put(TextStyle("Standard"))
        self.dim_styles._put(DimStyle("Standard"))

    def _install_missing_required(self) -> None:
        """After reading a file: whatever it lacked of the above, and the caches for layers it brought."""
        if "0" not in self.layers:
            self.layers._put(Layer("0"))
        for t in (Linetype(BYLAYER_TYPE), Linetype(BYBLOCK_TYPE), Linetype(CONTINUOUS, "Solid line")):
            if t.name not in self.linetypes:
                self.linetypes._put(t)
        if "Standard" not in self.text_styles:
            self.text_styles._put(TextStyle("Standard"))
        if "Standard" not in self.dim_styles:
            self.dim_styles._put(DimStyle("Standard"))
        self._hidden_layers = {k for k, v in self.layers._d.items() if not v.visible}
        self._locked_layers = {k for k, v in self.layers._d.items() if v.locked}

    # ---- history

    def transaction(self, label: str) -> Transaction:
        return Transaction(self, label)

    def undo(self) -> str | None:
        """Undo one step. Returns what was undone, or None if there's nothing to undo."""
        self._refuse_during_change("undo")
        if not self._undo:
            return None
        step = self._undo.pop()
        changes = _reversed(step.changes)
        self._apply_all(changes)
        self._redo.append(step)
        self._notify(tuple(changes))
        return step.label

    def redo(self) -> str | None:
        self._refuse_during_change("redo")
        if not self._redo:
            return None
        step = self._redo.pop()
        self._apply_all(step.changes)
        self._undo.append(step)
        self._notify(step.changes)
        return step.label

    def _refuse_during_change(self, what: str) -> None:
        if self._transaction is not None:
            raise EditError(f"can't {what} in the middle of a change")

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    def mark_saved(self) -> None:
        self._saved_at = self._undo[-1].serial if self._undo else 0

    @property
    def dirty(self) -> bool:
        """Does the drawing differ from what was saved? Undoing back to a save makes it clean again."""
        return (self._undo[-1].serial if self._undo else 0) != self._saved_at

    def forget_history(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._saved_at = 0

    def on_change(self, listener: Callable[[tuple[Change, ...]], None]) -> Callable[[], None]:
        """Call `listener(changes)` after every finished change, undo and redo. Returns a function that stops it."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def _commit(self, label: str, changes: dict) -> None:
        if not changes:
            return
        self._serial += 1
        step = Step(label, tuple(changes.values()), self._serial)
        self._undo.append(step)
        self._redo.clear()
        self._notify(step.changes)

    def _notify(self, changes: tuple[Change, ...]) -> None:
        for listener in list(self._listeners):
            listener(changes)

    def _apply_all(self, changes: Iterable[Change]) -> None:
        for c in changes:
            self._set(c.table, c.key, c.old, c.new)

    # ---- the one place anything changes

    def _get(self, table: str, key):
        if table == "entity":
            return self.entities.get(key)
        if table == "settings":
            return self.settings
        return self._table(table)._d.get(key)

    def _table(self, table: str) -> NameTable:
        return {"layer": self.layers, "linetype": self.linetypes, "textstyle": self.text_styles,
                "dimstyle": self.dim_styles, "block": self.blocks}[table]

    def _set(self, table: str, key, old, new) -> None:
        if table == "entity":
            if old is not None:
                self._unindex(old)
                self._attach(old, False)
                del self.entities[key]
            if new is not None:
                self.entities[key] = new
                self._index(new)
                self._attach(new, True)
        elif table == "settings":
            self.settings = new
        else:
            t = self._table(table)
            if new is None:
                t._d.pop(key, None)
            else:
                t._d[key] = new
            if table == "layer":
                self._hidden_layers.discard(key)
                self._locked_layers.discard(key)
                if new is not None:
                    if not new.visible:
                        self._hidden_layers.add(key)
                    if new.locked:
                        self._locked_layers.add(key)
            elif table == "block":
                self._block_changed(key)
            elif table == "dimstyle":
                self._dim_style_changed(key)

    def _attach(self, e: Entity, on: bool) -> None:
        """Keep track of which dimensions and hatches are attached to what."""
        s = e.shape
        k = type(s)
        if (k is not Dimension and k is not Hatch) or not s.refs:
            return
        for ref in s.refs:
            if ref is None:
                continue
            target = ref[0] if k is Dimension else ref
            if on:
                self._dependents.setdefault(target, set()).add(e.id)
            else:
                found = self._dependents.get(target)
                if found is not None:
                    found.discard(e.id)
                    if not found:
                        del self._dependents[target]

    def attached_to(self, eid: int) -> set[int]:
        """The dimensions and hatches attached to an object."""
        return set(self._dependents.get(eid, ()))

    def _follow_up(self, t: Transaction) -> None:
        """Dimensions and hatches attached to something this change moved follow it, in the same step, so one undo puts both back."""
        changed: set[int] = set()
        for (table, key), c in list(t._changes.items()):
            if table == "entity" and (c.old is None or c.new is None or c.old.shape != c.new.shape):
                changed.add(key)
        if not changed:
            return
        todo: set[int] = set()
        for eid in changed:
            todo |= self._dependents.get(eid, set())
            e = self.entities.get(eid)
            if e is not None and type(e.shape) in (Dimension, Hatch) and e.shape.refs:
                todo.add(eid)

        def shape_of(i: int):
            found = self.entities.get(i)
            return found.shape if found is not None else None
        for did in sorted(todo):
            e = self.entities.get(did)
            if e is None:
                continue
            if type(e.shape) is Dimension:
                followed = dims.follow(e.shape, shape_of, did in changed)
            elif type(e.shape) is Hatch:
                from .hatch import follow
                followed = follow(e.shape, self, did in changed)
            else:
                continue
            if followed is not None:
                t.replace(did, followed)

    def _dim_style_changed(self, key: str) -> None:
        """A dimension style's sizes changed, so what its dimensions draw takes up a different space."""
        for e in self.entities.values():
            if type(e.shape) is Dimension and e.shape.style.casefold() == key:
                box = self.shape_bbox(e.shape)
                if box is not None:
                    self.index.insert(index_key(e.id), box)

    def dim_style_of(self, d: Dimension) -> DimStyle:
        """The style a dimension is drawn with. One that's missing is drawn as Standard."""
        return self.dim_styles.get(d.style) or self.dim_styles.get("Standard") or DimStyle("Standard")

    def dimension_bbox(self, d: Dimension, _depth: int = 0) -> BBox | None:
        box = d.bbox()
        if d.block and self.blocks._d.get(d.block.casefold()) is not None:
            return bbox_union(box, self.block_bbox(d.block, _depth))
        for item in dims.drawn(d, self.dim_style_of(d)):
            box = bbox_union(box, item.bbox())
        return box

    def _block_changed(self, key: str) -> None:
        """A block's size changed, so everything placing it, directly or inside another block, is now somewhere else."""
        affected = {key}
        grew = True
        while grew:
            grew = False
            for k, b in self.blocks._d.items():
                if k not in affected and any(block_used_by(e.shape).casefold() in affected for e in b.entities):
                    affected.add(k)
                    grew = True
        for k in affected:
            self._block_bbox.pop(k, None)
        for e in self.entities.values():
            name = block_used_by(e.shape)
            if name and name.casefold() in affected:
                box = self.shape_bbox(e.shape)
                if box is not None:
                    self.index.insert(index_key(e.id), box)

    def _index(self, e: Entity) -> None:
        s = e.shape
        eid = e.id
        layer = e.style.layer.casefold()
        self._layer_counts[layer] = self._layer_counts.get(layer, 0) + 1
        if type(s) is Polyline:
            for sub, box in polyline_entries(s):
                self.index.insert(index_key(eid, sub), box)
            return
        box = self.shape_bbox(s)
        if box is not None:
            self.index.insert(index_key(eid), box)
        k = type(s)
        if k is Arc or k is EllipseArc or k is Circle:
            c = s.c
            self.index.insert(index_key(eid, CENTRE_SUB), (c[0], c[1], c[0], c[1]))
        elif k is Insert:
            self._block_counts[s.block.casefold()] = self._block_counts.get(s.block.casefold(), 0) + 1
            self.index.insert(index_key(eid, CENTRE_SUB), (s.p[0], s.p[1], s.p[0], s.p[1]))
        elif (k is Foreign or k is Dimension) and s.block:
            self._block_counts[s.block.casefold()] = self._block_counts.get(s.block.casefold(), 0) + 1

    def _unindex(self, e: Entity) -> None:
        s = e.shape
        eid = e.id
        layer = e.style.layer.casefold()
        left = self._layer_counts.get(layer, 0) - 1
        if left > 0:
            self._layer_counts[layer] = left
        else:
            self._layer_counts.pop(layer, None)
        if type(s) is Polyline:
            for sub, _ in polyline_entries(s):
                self.index.remove(index_key(eid, sub))
            return
        self.index.remove(index_key(eid))
        self.index.remove(index_key(eid, CENTRE_SUB))
        if block_used_by(s):
            key = s.block.casefold()
            left = self._block_counts.get(key, 0) - 1
            if left > 0:
                self._block_counts[key] = left
            else:
                self._block_counts.pop(key, None)

    def _check_shape(self, shape: Shape, inside: str | None = None) -> None:
        if isinstance(shape, Insert) and inside is not None and self._would_cycle(shape.block, inside):
            raise EditError(f"Block {inside} can't contain {shape.block}: it would contain itself.")
        if type(shape) is Polyline and len(shape.pts) > MOST_SEGMENTS:
            raise EditError(f"A polyline can have at most {MOST_SEGMENTS} points.")

    def _would_cycle(self, block: str, target: str, seen: set | None = None) -> bool:
        seen = seen if seen is not None else set()
        key = block.casefold()
        if key == target.casefold():
            return True
        if key in seen:
            return False
        seen.add(key)
        d = self.blocks.get(block)
        if d is None:
            return False
        return any(block_used_by(e.shape) and self._would_cycle(e.shape.block, target, seen) for e in d.entities)

    # ---- questions

    def layer_use(self, name: str) -> int:
        return self._layer_counts.get(name.casefold(), 0) + sum(
            1 for b in self.blocks for e in b.entities if e.style.layer.casefold() == name.casefold())

    def block_use(self, name: str) -> int:
        key = name.casefold()
        return self._block_counts.get(key, 0) + sum(1 for b in self.blocks for e in b.entities if block_used_by(e.shape).casefold() == key)

    def layer_of(self, style: Style) -> Layer | None:
        return self.layers.get(style.layer)

    def is_hidden(self, style: Style) -> bool:
        return style.layer.casefold() in self._hidden_layers

    def is_locked(self, style: Style) -> bool:
        return style.layer.casefold() in self._locked_layers

    def shape_bbox(self, s: Shape) -> BBox | None:
        if type(s) is Insert:
            return self.insert_bbox(s)
        if type(s) is Foreign:
            return self.foreign_bbox(s)
        if type(s) is Dimension:
            return self.dimension_bbox(s)
        return s.bbox()

    def foreign_bbox(self, s: Foreign, _depth: int = 0) -> BBox | None:
        box = s.bbox()
        if s.block:
            inner = self.block_bbox(s.block, _depth)
            if inner is not None:
                x, y = s.at
                box = bbox_union(box, (inner[0] + x, inner[1] + y, inner[2] + x, inner[3] + y))
        return box

    def block_bbox(self, name: str, _depth: int = 0) -> BBox | None:
        """The box round a block's contents in its own coordinates. Remembered until the block changes."""
        key = name.casefold()
        d = self.blocks._d.get(key)
        if d is None or _depth > 32:
            return None
        known = self._block_bbox.get(key)
        if known is not None and known[0] is d:
            return known[1]
        box: BBox | None = None
        for e in d.entities:
            s = e.shape
            if type(s) is Insert:
                b = self.insert_bbox(s, _depth + 1)
            elif type(s) is Foreign:
                b = self.foreign_bbox(s, _depth + 1)
            elif type(s) is Dimension:
                b = self.dimension_bbox(s, _depth + 1)
            else:
                b = s.bbox()
            box = bbox_union(box, b)
        self._block_bbox[key] = (d, box)
        return box

    def insert_bbox(self, ins: Insert, _depth: int = 0) -> BBox | None:
        d = self.blocks.get(ins.block)
        inner = self.block_bbox(ins.block, _depth) if d is not None else None
        if inner is None:
            box: BBox | None = (ins.p[0], ins.p[1], ins.p[0], ins.p[1])
        else:
            x0, y0, x1, y1 = inner
            box = None
            for c, r in ((0, 0), (ins.columns - 1, 0), (0, ins.rows - 1), (ins.columns - 1, ins.rows - 1)):
                m = ins.matrix(d.base, c, r)
                for q in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
                    px, py = m.apply(q)
                    box = bbox_union(box, (px, py, px, py))
        for a in ins.attribs:
            box = bbox_union(box, a.text.bbox())
        return box

    def extents(self, visible_only: bool = True) -> BBox | None:
        """The box round the drawing. Hidden layers don't count."""
        box: BBox | None = None
        for e in self.entities.values():
            if visible_only and e.style.layer.casefold() in self._hidden_layers:
                continue
            box = bbox_union(box, self.shape_bbox(e.shape))
        return box

    def sorted_entities(self) -> list[Entity]:
        """In draw order: what's later is on top."""
        return sorted(self.entities.values(), key=lambda e: (e.order, e.id))

    def fingerprint(self) -> str:
        """A digest of everything that undo restores. Two equal fingerprints are the same drawing."""
        from .native import canonical_state
        data = json.dumps(canonical_state(self), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        return hashlib.sha256(data.encode("ascii")).hexdigest()
