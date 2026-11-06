# SPDX-License-Identifier: GPL-3.0-or-later
"""Changes to layers that reach beyond the layer table."""
from __future__ import annotations

from dataclasses import replace

from .model import BlockDef, Document, EditError, Layer, Style


def unique_layer_name(doc: Document, stem: str = "Layer") -> str:
    i = 1
    while f"{stem} {i}" in doc.layers:
        i += 1
    return f"{stem} {i}"


def _renamed(style: Style, old: str, new: str) -> Style | None:
    if style.layer.casefold() != old.casefold():
        return None
    return Style(new, style.color, style.linetype, style.weight, style.ltscale)


def rename_layer(doc: Document, old: str, new: str) -> None:
    """Rename a layer and everything on it, including inside blocks, as one undo step."""
    new = new.strip()
    layer = doc.layers.get(old)
    if layer is None:
        raise EditError(f"There's no layer called {old}.")
    if old.casefold() == "0":
        raise EditError("Layer 0 can't be renamed. Other drawings and blocks rely on it.")
    if not new:
        raise EditError("A layer needs a name.")
    if any(c in new for c in '<>/\\":;?*|=`'):
        raise EditError('A layer name can\'t contain any of these: < > / \\ " : ; ? * | = `')
    if new == layer.name:
        return
    if new.casefold() != old.casefold() and new in doc.layers:
        raise EditError(f"There's already a layer called {new}.")
    with doc.transaction("Rename layer") as t:
        t.put_layer(replace(layer, name=new))
        if new.casefold() != old.casefold():
            t._record("layer", old.casefold(), None)
        for e in list(doc.entities.values()):
            style = _renamed(e.style, old, new)
            if style is not None:
                t.replace(e.id, style=style)
        for b in list(doc.blocks):
            if any(e.style.layer.casefold() == old.casefold() for e in b.entities):
                members = tuple(type(e)(e.id, e.shape, _renamed(e.style, old, new) or e.style, e.order, e.source) for e in b.entities)
                t.put_block(BlockDef(b.name, b.base, members, b.description, b.flags, b.source))
    if doc.current.layer.casefold() == old.casefold():
        doc.current.layer = new


def set_layer_flag(doc: Document, name: str, **flags) -> None:
    layer = doc.layers.get(name)
    if layer is None:
        raise EditError(f"There's no layer called {name}.")
    if flags.get("on") is False and name.casefold() == doc.current.layer.casefold():
        pass        # allowed: drawing on a layer you've hidden is a surprise, but it's said in the status line
    with doc.transaction("Layer") as t:
        t.put_layer(replace(layer, **flags))


def add_layer(doc: Document, name: str | None = None, **values) -> str:
    name = (name or unique_layer_name(doc)).strip()
    if name in doc.layers:
        raise EditError(f"There's already a layer called {name}.")
    with doc.transaction("New layer") as t:
        t.put_layer(Layer(name, **values))
    return name
