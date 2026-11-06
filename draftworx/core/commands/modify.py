# SPDX-License-Identifier: GPL-3.0-or-later
"""Changing what's there."""
from __future__ import annotations

from . import command
from ..grips import Grip, moved
from ..model import with_refs_moved
from ..session import ENTER, GetAngle, GetDistance, GetPoint, GetSelection, Keyword
from ..shapes import Foreign
from ..snap import Tracked
from ..vec import Affine, Vec, near


def selected(s, verb: str):
    """What the command acts on: what's already selected, or else ask."""
    if s.selection:
        return sorted(s.selection)
    while True:
        ids = yield GetSelection(f"{verb}: select objects, then press Enter.")
        if ids:
            return ids
        s.message = "Nothing selected."
        return []


def apply_to_all(s, label: str, ids: list[int], m: Affine, copy: bool = False) -> list[int]:
    """Move or copy these by one transform, as one undo step. Returns the copies' ids. What can't be changed here stays, and the person is told."""
    made: list[int] = []
    stayed = 0
    with s.doc.transaction(label) as t:
        copies: dict[int, int] = {}
        for i in ids:
            e = s.doc.entities.get(i)
            if e is None:
                continue
            if type(e.shape) is Foreign:
                stayed += 1
                continue
            shape = e.shape.transformed(m)
            if copy:
                copies[i] = t.add(shape, e.style)
                made.append(copies[i])
            else:
                t.replace(i, shape)
        attach_copies(t, copies)
    if stayed:
        s.message = (f"{stayed} object{'s' if stayed != 1 else ''} from the file didn't {'copy' if copy else 'change'}: "
                     f"{'they' if stayed != 1 else 'it'} can only be erased or restyled here so far.")
    return made


def attach_copies(t, copies: dict[int, int]) -> None:
    """Copies of dimensions and hatches copied with what they're attached to are attached to the copies, so they follow those."""
    for new in copies.values():
        e = t.doc.entities.get(new)
        moved = with_refs_moved(e.shape, copies) if e is not None else None
        if moved is not None:
            t.replace(new, moved)


def ghosts_of(s, ids: list[int], m: Affine, limit: int = 2000) -> list:
    """What to show while something moves. Past a limit, showing every piece would only slow the moving down."""
    out = []
    for i in ids[:limit]:
        e = s.doc.entities.get(i)
        if e is not None and type(e.shape) is not Foreign:
            out.append(e.shape.transformed(m))
    return out


@command("ERASE", "E", "DEL", group="Modify", help="Remove objects", icon="erase")
def erase(s):
    ids = yield from selected(s, "Erase")
    if not ids:
        return
    with s.doc.transaction("Erase") as t:
        for i in ids:
            t.remove(i)


@command("MOVE", "M", group="Modify", help="Move objects", icon="move")
def move(s):
    ids = yield from selected(s, "Move")
    if not ids:
        return
    keep = frozenset(ids)
    base = yield GetPoint("Move: from which point?", exclude=keep)

    def show(p):
        d = p - base
        return ghosts_of(s, ids, Affine.translation(d[0], d[1]))
    to = yield GetPoint("Move: to where? Type a length, or x,y.", base=base, preview=show, exclude=keep)
    d = to - base
    apply_to_all(s, "Move", ids, Affine.translation(d[0], d[1]))


@command("COPY", "CO", "CP", group="Modify", help="Copy objects, as many times as you like", icon="copy")
def copy(s):
    ids = yield from selected(s, "Copy")
    if not ids:
        return
    base = yield GetPoint("Copy: from which point?")

    def show(p):
        d = p - base
        return ghosts_of(s, ids, Affine.translation(d[0], d[1]))
    while True:
        to = yield GetPoint("Copy: to where? Press Enter to finish.", base=base, preview=show, enter=True)
        if to is ENTER:
            return
        d = to - base
        apply_to_all(s, "Copy", ids, Affine.translation(d[0], d[1]), copy=True)


@command("ROTATE", "RO", group="Modify", help="Turn objects about a point", icon="rotate")
def rotate(s):
    ids = yield from selected(s, "Rotate")
    if not ids:
        return
    keep = frozenset(ids)
    base = yield GetPoint("Rotate: about which point?", exclude=keep)
    copying = False
    while True:
        def show(p):
            d = p - base
            return ghosts_of(s, ids, Affine.rotation(d.angle(), base)) if d.length2() > 0.0 else []
        got = yield GetAngle("Rotate: by what angle? Point, or type degrees.", ("Move" if copying else "Copy",), base=base, preview=show, exclude=keep)
        if isinstance(got, Keyword):
            copying = got.name == "Copy"
            continue
        apply_to_all(s, "Rotate", ids, Affine.rotation(got, base), copy=copying)
        return


@command("SCALE", "SC", group="Modify", help="Make objects bigger or smaller about a point", icon="scale")
def scale(s):
    ids = yield from selected(s, "Scale")
    if not ids:
        return
    keep = frozenset(ids)
    base = yield GetPoint("Scale: about which point?", exclude=keep)

    def show(p):
        k = base.distance(p)
        return ghosts_of(s, ids, Affine.scaling(k, None, base)) if k > 0.0 else []
    k = yield GetDistance("Scale: by what factor? Type it, or point: one drawing unit away is full size.", base=base, preview=show, exclude=keep, plain=True)
    if k <= 0.0:
        s.message = "A scale factor has to be more than zero."
        return
    apply_to_all(s, "Scale", ids, Affine.scaling(k, None, base))


@command("MIRROR", "MI", group="Modify", help="Make a mirror image of objects", icon="mirror")
def mirror(s):
    ids = yield from selected(s, "Mirror")
    if not ids:
        return
    a = yield GetPoint("Mirror: first point on the mirror line?")
    keep = True
    while True:
        def show(p):
            return ghosts_of(s, ids, Affine.mirror(a, p)) if not near(a, p) else []
        b = yield GetPoint(f"Mirror: second point on the line? This will {'keep' if keep else 'replace'} the originals.",
                           ("Replace" if keep else "Keep",), base=a, preview=show)
        if isinstance(b, Keyword):
            keep = b.name == "Keep"
            continue
        break
    if near(a, b):
        s.message = "A mirror line needs two different points."
        return
    apply_to_all(s, "Mirror", ids, Affine.mirror(a, b), copy=keep)


def drag_move(s, ids: list[int], base: Vec, copying: bool):
    """Dragging what's selected: pressed on it, letting go is the answer. Dragging by a corner moves exactly from that corner."""
    keep = frozenset(ids)

    def show(p):
        d = p - base
        return ghosts_of(s, ids, Affine.translation(d[0], d[1]))
    word = "Copy" if copying else "Move"
    to = yield GetPoint(f"{word}: let go where you want it. Type a length if you know it.", base=base, preview=show,
                        exclude=frozenset() if copying else keep, on_release=True, ctrl_keeps_snap=True)
    if near(to, base):
        return
    d = to - base
    apply_to_all(s, word, ids, Affine.translation(d[0], d[1]), copy=copying)


def grip_edit(s, eid: int, grip: Grip, dragging: bool):
    """Moving a handle on a selected object. A press and a release first means it follows the cursor until the next click."""
    e = s.doc.entities.get(eid)
    if e is None:
        return
    shape = e.shape

    def show(p):
        new = moved(shape, grip, p)
        return [new]
    verb = "let go" if dragging else "click"
    to = yield GetPoint(f"Move this handle: {verb} where you want it. Type a length if you know it.", base=grip.base if grip.base is not None else grip.point,
                        preview=show, tracked=(Tracked(grip.point, "Where it was"),), exclude=frozenset({eid}), on_release=dragging)
    new = moved(shape, grip, to)
    if new == shape:
        return
    with s.doc.transaction("Edit") as t:
        t.replace(eid, new)
