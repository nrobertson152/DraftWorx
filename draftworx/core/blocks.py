# SPDX-License-Identifier: GPL-3.0-or-later
"""Blocks: making one from what's selected, placing one with its attributes filled in, and bringing in another drawing as one.

A block made here keeps its objects exactly where they were, and its base point is where it's placed from, as in AutoCAD: placing it at
its base point puts everything back where it was, with no arithmetic on any coordinate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .model import BlockDef, Document, Entity, Transaction, block_used_by
from .shapes import AttDef, Attrib, Dimension, Foreign, Hatch, Insert
from .vec import Vec

_BAD = re.compile(r'[<>/\\":;?*|,=`]')


def problem_with_name(name: str) -> str | None:
    """Why a block can't be called this, or None. The characters DXF refuses, and names that start with *, which are the file's own."""
    if not name.strip():
        return "A block needs a name."
    if name.strip().startswith("*"):
        return "A name starting with * is kept for blocks a file makes for itself."
    if _BAD.search(name):
        return "A block's name can't have any of < > / \\ \" : ; ? * | , = ` in it."
    if len(name) > 255:
        return "That name is too long."
    return None


def free_name(doc: Document, stem: str = "Block") -> str:
    """The first of Stem1, Stem2... that isn't taken."""
    stem = _BAD.sub("_", stem.strip()).lstrip("*") or "Block"
    n = 1
    while f"{stem}{n}" in doc.blocks:
        n += 1
    return f"{stem}{n}"


def _alone(shape):
    """A shape as it is inside a block, where there's nothing to stay attached to."""
    if type(shape) is Dimension and (shape.refs or shape.block):
        return replace(shape, refs=(), block="")
    if type(shape) is Hatch and shape.refs:
        return replace(shape, refs=())
    return shape


def attribs_of(block: BlockDef) -> list[AttDef]:
    """The attributes a block asks for, in the order they were made."""
    return [e.shape for e in sorted(block.entities, key=lambda e: (e.order, e.id)) if type(e.shape) is AttDef]


def placed_attribs(block: BlockDef, ins: Insert, values: dict[str, str] | None = None) -> tuple[Attrib, ...]:
    """A copy's attributes: each definition's text, where the copy puts it, with its value. A constant one is part of the block itself."""
    values = values or {}
    m = ins.matrix(block.base)
    out = []
    for d in attribs_of(block):
        a = d.attrib
        if a.constant:
            continue
        value = values.get(a.tag.casefold(), values.get(a.tag, a.text.text))
        out.append(replace(a, text=replace(a.text, text=value)).transformed(m))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Made:
    block: BlockDef
    placed: Insert
    left_out: int


def make(t: Transaction, ids: list[int], base: Vec, name: str, replacing: bool = False) -> Made:
    """Make a block of these objects, with its base point at `base`, and put a copy of it where they were, in this Transaction.

    Attribute definitions among them become the block's, and the copy is given their values. What came from a file and can't be changed
    here stays out of the block, where it was. With `replacing`, a block of that name is redefined, and every copy of it changes."""
    doc = t.doc
    problem = problem_with_name(name)
    if problem is not None:
        from .model import EditError
        raise EditError(problem)
    existing = doc.blocks.get(name)
    if existing is not None and not replacing:
        from .model import EditError
        raise EditError(f"There's already a block called {existing.name}.")
    inside: list[Entity] = []
    left_out = 0
    for i in ids:
        e = doc.entities.get(i)
        if e is None:
            continue
        if type(e.shape) is Foreign:
            left_out += 1
            continue
        if existing is not None and block_used_by(e.shape).casefold() == name.casefold():
            from .model import EditError
            raise EditError(f"Block {existing.name} can't contain a copy of itself.")
        inside.append(Entity(doc._next_id + len(inside), _alone(e.shape), e.style, e.order))
    if not inside:
        from .model import EditError
        raise EditError("There's nothing there that can go in a block.")
    definition = BlockDef(existing.name if existing is not None else name, base, tuple(inside),
                          existing.description if existing is not None else "")
    # the ids inside the block are allotted as the block goes in
    for i in ids:
        if i in doc.entities and type(doc.entities[i].shape) is not Foreign:
            t.remove(i)
    t.put_block(definition)
    placed = Insert(definition.name, base)
    placed = replace(placed, attribs=placed_attribs(definition, placed))
    t.add(placed)
    return Made(definition, placed, left_out)


@dataclass(frozen=True, slots=True)
class Brought:
    """What bringing in a drawing as a block adds."""
    block: BlockDef
    tables: tuple              # layers, line types, text and dimension styles, and blocks the drawing uses that this one hasn't got
    left_out: int              # objects from a file that can't be changed here, which can't be moved to another drawing either
    kept: tuple[str, ...]      # blocks of the same name already here, which were kept as they are


def bring_in(target: Document, source: Document, name: str) -> Brought:
    """Another drawing as a block called `name`, its base point at its origin. Its layers, styles and blocks come with it, except where
    this drawing has its own of the same name, which win, as they do in AutoCAD. Blocks a file made for itself (named *...) are renamed
    so they can't clash. Nothing is changed yet: `add` puts it in a Transaction."""
    problem = problem_with_name(name)
    if problem is not None:
        raise ValueError(problem)
    next_id = target._next_id
    renamed: dict[str, str] = {}
    taken = {b.name.casefold() for b in target.blocks} | {name.casefold()}
    for b in source.blocks:
        if b.name.startswith("*"):
            n = 1
            while f"{b.name}_{n}".casefold() in taken:
                n += 1
            renamed[b.name.casefold()] = f"{b.name}_{n}"
            taken.add(f"{b.name}_{n}".casefold())

    def moved(shape):
        shape = _alone(shape)
        used = block_used_by(shape)
        if used and used.casefold() in renamed:
            shape = replace(shape, block=renamed[used.casefold()])
        return shape

    left_out = 0

    def copied(entities) -> tuple[Entity, ...]:
        nonlocal next_id, left_out
        out = []
        for e in entities:
            if type(e.shape) is Foreign:
                left_out += 1
                continue
            out.append(Entity(next_id, moved(e.shape), e.style, e.order))
            next_id += 1
        return tuple(out)

    tables: list = []
    kept: list[str] = []
    for b in source.blocks:
        new_name = renamed.get(b.name.casefold(), b.name)
        if new_name.casefold() in {x.name.casefold() for x in target.blocks}:
            kept.append(b.name)
            continue
        tables.append(BlockDef(new_name, b.base, copied(b.entities), b.description))
    block = BlockDef(name, Vec(0.0, 0.0), copied(source.sorted_entities()))
    for table, have in ((source.layers, target.layers), (source.linetypes, target.linetypes), (source.text_styles, target.text_styles),
                        (source.dim_styles, target.dim_styles)):
        for v in table:
            if v.name not in have:
                tables.append(replace(v, source=None))
    return Brought(block, tuple(tables), left_out, tuple(kept))


def add(t: Transaction, brought: Brought) -> None:
    """Put what `bring_in` worked out into the drawing."""
    from .model import DimStyle, Layer, Linetype, TextStyle
    for v in brought.tables:
        k = type(v)
        if k is Layer:
            t.put_layer(v)
        elif k is Linetype:
            t.put_linetype(v)
        elif k is TextStyle:
            t.put_text_style(v)
        elif k is DimStyle:
            t.put_dim_style(v)
    # blocks last, once everything they use is there, and the ones used by others first
    blocks = [v for v in brought.tables if type(v) is BlockDef] + [brought.block]
    done: set[str] = set()
    while blocks:
        progress = False
        for b in list(blocks):
            needs = {block_used_by(e.shape).casefold() for e in b.entities if block_used_by(e.shape)}
            if all(n in done or n in t.doc.blocks for n in needs if n != b.name.casefold()):
                t.put_block(b)
                done.add(b.name.casefold())
                blocks.remove(b)
                progress = True
        if not progress:
            for b in blocks:
                t.put_block(b)
            break
