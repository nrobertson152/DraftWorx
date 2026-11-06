# SPDX-License-Identifier: GPL-3.0-or-later
"""Blocks and their attributes: make one from what's selected, place one and fill in its fields, define a field, and change the values a
copy was given."""
from __future__ import annotations

import math
from dataclasses import replace

from . import command
from .. import blocks
from ..model import EditError
from ..select import pick
from ..session import ENTER, GetNumber, GetPick, GetPoint, GetText, Keyword, Pick, Preview
from ..shapes import AttDef, Attrib, Insert, Text
from .draw import _default_text_height
from .modify import selected


def _named(doc, text: str):
    """The block this names: exactly, or the one name it's the start of."""
    t = text.strip().casefold()
    found = doc.blocks.get(text.strip())
    if found is not None:
        return found
    starts = [b for b in doc.blocks if not b.anonymous and b.name.casefold().startswith(t)]
    return starts[0] if len(starts) == 1 else None


def placeable(doc) -> list[str]:
    return [b.name for b in doc.blocks if not b.anonymous]


@command("BLOCK", "B", group="Blocks", help="Make a block of what's selected, to place again and again", icon="block")
def block_command(s):
    ids = yield from selected(s, "Block")
    if not ids:
        return
    s.highlights = tuple(ids)
    base = yield GetPoint("Block: its base point, the point it's placed by.")
    default = blocks.free_name(s.doc)
    replacing = False
    while True:
        name = yield GetText(f"Block: what's it called? Enter for {default}.", default=default)
        if name is ENTER:
            name = default
        name = name.strip()
        problem = blocks.problem_with_name(name)
        if problem is not None:
            s.message = problem
            continue
        existing = s.doc.blocks.get(name)
        if existing is None:
            break
        uses = s.doc.block_use(name)
        answer = yield GetText(f"Block: {existing.name} is already a block, placed {uses} time{'s' if uses != 1 else ''}. Type R to replace it "
                               "everywhere, or type another name.", default="")
        if answer is ENTER or not answer.strip():
            continue
        if answer.strip().casefold() in ("r", "replace"):
            name = existing.name
            replacing = True
            break
        default = answer.strip()
    with s.doc.transaction(f"Block {name}") as t:
        made = blocks.make(t, ids, base, name, replacing)
    s.set_selection(())
    s.memory["insert_block"] = made.block.name
    if made.left_out:
        s.message = f"Block {made.block.name} made. {made.left_out} object{'s' if made.left_out != 1 else ''} from a file that can't be changed here stayed out of it."
    else:
        s.message = f"Block {made.block.name} made, and placed where its objects were."


def place(s, name: str):
    """Place a copy of a block: where, its size and turn, then its fields."""
    block = s.doc.blocks.get(name)
    if block is None:
        s.message = f"There's no block called {name}."
        return
    scale, turn = [1.0], [0.0]

    def make(p) -> Insert:
        ins = Insert(block.name, p, scale[0], scale[0], turn[0])
        return Insert(block.name, p, scale[0], scale[0], turn[0], blocks.placed_attribs(block, ins))
    while True:
        p = yield GetPoint(f"Insert {block.name}: where does it go? S to size it, R to turn it.", ("Scale", "Rotate"), preview=lambda p: [make(p)])
        if p == Keyword("Scale"):
            k = yield GetNumber("Insert: what size, as a multiple of the block's own?", default=scale[0])
            if k == 0.0:
                s.message = "A block can't be placed at no size."
            else:
                scale[0] = k
            continue
        if p == Keyword("Rotate"):
            turn[0] = math.radians((yield GetNumber("Insert: turned by how many degrees?", default=math.degrees(turn[0]))))
            continue
        break
    values: dict[str, str] = {}
    for d in blocks.attribs_of(block):
        a = d.attrib
        if a.constant:
            continue
        ask = a.prompt.strip() or f"{a.tag}?"
        typed = yield GetText(f"Insert {block.name}: {ask} Enter for {a.text.text!r}." if a.text.text else f"Insert {block.name}: {ask}",
                              default=a.text.text)
        values[a.tag.casefold()] = a.text.text if typed is ENTER else typed
    ins = Insert(block.name, p, scale[0], scale[0], turn[0])
    with s.doc.transaction(f"Insert {block.name}") as t:
        t.add(Insert(block.name, p, scale[0], scale[0], turn[0], blocks.placed_attribs(block, ins, values)))
    s.memory["insert_block"] = block.name


@command("INSERT", "I", group="Blocks", help="Place a block, and fill in its fields", icon="insert")
def insert_command(s):
    names = placeable(s.doc)
    if not names:
        s.message = "There are no blocks in this drawing yet. Make one with Block, or bring in a drawing with File, Insert a drawing."
        return
    default = s.memory.get("insert_block")
    if default is None or default not in s.doc.blocks:
        default = names[0]
    while True:
        shown = ", ".join(names[:12]) + (", ..." if len(names) > 12 else "")
        typed = yield GetText(f"Insert: which block? {shown}. Enter for {default}.", default=default)
        found = s.doc.blocks.get(default) if typed is ENTER else _named(s.doc, typed)
        if found is not None:
            break
        s.message = f"There's no block called {typed.strip()}, or more than one starts that way."
    yield from place(s, found.name)


@command("ATTDEF", "ATT", group="Blocks", help="Define a field each copy of a block fills in, like a part number", icon="text")
def attdef_command(s):
    while True:
        tag = yield GetText("Attribute: its tag, a name without spaces, like PART_NO.")
        if tag is ENTER:
            return
        tag = tag.strip().upper()
        if tag and " " not in tag and not blocks.problem_with_name(tag):
            break
        s.message = "A tag is one word, without spaces or any of < > / \\ \" : ; ? * | , = `."
    prompt = yield GetText(f"Attribute: what does placing a block ask? Enter for {tag.replace('_', ' ').capitalize()}?",
                           default=f"{tag.replace('_', ' ').capitalize()}?")
    value = yield GetText("Attribute: its value until it's filled in? Enter for none.", default="")
    value = "" if value is ENTER else value
    height = _default_text_height(s)

    def make(p) -> AttDef:
        return AttDef(Attrib(tag, Text(p, height, value, style=s.doc.current.text_style), "" if prompt is ENTER else prompt))
    p = yield GetPoint("Attribute: where does it go?", preview=lambda p: [make(p)])
    with s.doc.transaction("Attribute") as t:
        t.add(make(p))
    s.message = f"Attribute {tag} made. Select it with what it describes and make a block of them."


@command("ATTEDIT", "ATE", "EATTEDIT", group="Blocks", help="Change the values a placed block was given", icon="text")
def attedit_command(s):
    def with_fields(e) -> bool:
        return type(e.shape) is Insert and any(not a.constant for a in e.shape.attribs)

    def hover(p, px):
        hits = pick(s.doc, p, px, filter=with_fields)
        return Preview(hover=hits[0] if hits else None)
    while True:
        got = yield GetPick("Change values: point at a block that has some. Enter to finish.", preview=hover, enter=True)
        if got is ENTER:
            return
        if not isinstance(got, Pick):
            continue
        hits = pick(s.doc, got.point, got.px, filter=with_fields)
        if not hits:
            s.message = "That isn't a block with values to change."
            continue
        eid = hits[0]
        ins = s.doc.entities[eid].shape
        attribs = list(ins.attribs)
        for i, a in enumerate(attribs):
            if a.constant:
                continue
            typed = yield GetText(f"{a.tag}: {a.prompt or 'its value?'} Enter keeps {a.text.text!r}.", default=a.text.text)
            if typed is not ENTER:
                attribs[i] = replace(a, text=replace(a.text, text=typed))
        try:
            with s.doc.transaction("Change values") as t:
                t.replace(eid, replace(ins, attribs=tuple(attribs)))
        except EditError as exc:
            s.message = str(exc)
