# SPDX-License-Identifier: GPL-3.0-or-later
"""Changing the shape of what's there."""
from __future__ import annotations

import math
import time

from . import command
from .modify import attach_copies, selected
from ..curves import Polyline
from ..model import split_key
from ..ops.corner import chamfer, corner_of_polyline, every_corner, fillet
from ..ops.offset import offset, side_of
from ..ops.pieces import CUTTABLE, Extended, apply_pieces, break_at, crossings, cut_at, extend_at
from ..ops.reshape import explode_shape, join, stretched
from ..select import pick
from ..session import ENTER, GetAngle, GetNumber, GetPick, GetPoint, Keyword, Preview, Typed
from ..shapes import Foreign
from ..units import TypedError, format_length, parse_length
from ..vec import Affine, Vec, near

MOST_COPIES = 100_000


def _hovering(s):
    """A preview that only says what a click would land on."""
    def preview(p, px):
        hits = pick(s.doc, p, px)
        return Preview(hover=hits[0] if hits else None)
    return preview


def _length_text(s, value: float) -> str:
    st = s.doc.settings
    return format_length(value, st.linear_format, st.linear_decimals, st.units)


# ----------------------------------------------------------------------------------------------- trim and extend


def _trim_or_extend(s, extending_first: bool, verb: str):
    known: dict[int, list[float]] = {}
    seen_at = [None]

    def cuts_on(e) -> list[float]:
        # working these out is the slow part, and the mouse moves far more often than the drawing changes
        if seen_at[0] != s.doc_version:
            known.clear()
            seen_at[0] = s.doc_version
        if e.id not in known:
            known[e.id] = crossings(s.doc, e)
        return known[e.id]

    def plan(p, px, shift):
        extending = extending_first != shift
        for i in pick(s.doc, p, px):
            e = s.doc.entities.get(i)
            if e is None:
                continue
            if extending:
                r = extend_at(s.doc, i, p)
                if r is not None:
                    return e, r
            elif isinstance(e.shape, CUTTABLE):
                return e, cut_at(e.shape, cuts_on(e), e.shape.project(p)[0])
        return None

    def preview(p, px):
        found = plan(p, px, s.shift)
        if found is None:
            return Preview()
        r = found[1]
        return Preview(ghosts=(r.added,)) if isinstance(r, Extended) else Preview(doomed=r.removed)

    other = "trim" if extending_first else "extend"
    prompt = f"{verb}: click the {'end to lengthen' if extending_first else 'piece to take out'}, or drag across several. Hold Shift to {other} instead. Enter to finish."
    while True:
        ans = yield GetPick(prompt, preview=preview, enter=True, swipe=True)
        if ans is ENTER:
            return
        found = plan(ans.point, ans.px, ans.shift)
        if found is None:
            if not ans.swiped:
                s.message = "There's nothing here to extend to." if extending_first != ans.shift else "There's nothing here to trim."
            continue
        e, r = found
        if isinstance(r, Extended):
            with s.doc.transaction("Extend") as t:
                t.replace(e.id, r.shape)
        else:
            with s.doc.transaction("Trim") as t:
                apply_pieces(t, e, r.kept)


@command("TRIM", "TR", group="Modify", help="Click the piece in the way to take it out, or drag across several", icon="trim")
def trim(s):
    yield from _trim_or_extend(s, False, "Trim")


@command("EXTEND", "EX", group="Modify", help="Lengthen a line or arc to the next thing in its way", icon="extend")
def extend(s):
    yield from _trim_or_extend(s, True, "Extend")


# ------------------------------------------------------------------------------------------------------- offset


def _add_offsets(s, e, result) -> None:
    with s.doc.transaction("Offset") as t:
        for shape in result.shapes:
            t.add(shape, e.style)
    if result.within:
        s.message = f"An offset of a curve like this isn't one itself. This is a spline within {_length_text(s, result.within)} of it."


QUICK = 0.15     # seconds a preview may take


@command("OFFSET", "O", group="Modify", help="Make a copy a set distance to one side", icon="offset")
def offset_command(s):
    d = s.memory.get("offset")
    made: dict = {}
    slow: set[int] = set()
    seen_at = [None]

    def offset_of(e, dist, p, quick=False):
        """Worked out once for each object, distance and side: the mouse moves far more often than any of those change. While it's moving, an object that takes
        too long gets no preview rather than holding everything up. None then."""
        if seen_at[0] != s.doc_version or len(made) > 64:
            made.clear()
            slow.clear()
            seen_at[0] = s.doc_version
        key = (e.id, dist, side_of(e.shape, p))
        if key in made:
            return made[key]
        if quick and e.id in slow:
            return None
        try:
            result = offset(e.shape, dist, p, time.monotonic() + QUICK if quick else None)
        except TimeoutError:
            slow.add(e.id)
            return None
        except ValueError as exc:
            result = str(exc)
        made[key] = result
        return result

    def attempt(p, px, quick=False):
        for i in pick(s.doc, p, px):
            e = s.doc.entities.get(i)
            if e is None or type(e.shape) is Foreign:
                continue
            return e, offset_of(e, d, p, quick)
        return None

    def preview(p, px):
        found = attempt(p, px, True) if d else None
        if found is None or not hasattr(found[1], "shapes"):
            hits = pick(s.doc, p, px)
            return Preview(hover=hits[0] if hits else None)
        return Preview(ghosts=found[1].shapes)

    while True:
        prompt = (f"Offset by {_length_text(s, d)}: click an object, on the side it should go. Type another distance, or T to go through a point. Enter to finish." if d
                  else "Offset: type a distance, or click an object and then a point it should go through. Enter to finish.")
        ans = yield GetPick(prompt, ("Through",), preview=preview, enter=True, typed=True)
        if ans is ENTER:
            return
        if ans == Keyword("Through"):
            d = None
            s.memory.pop("offset", None)
            continue
        if isinstance(ans, Typed):
            try:
                value = parse_length(ans.text, s.doc.settings.units)
            except TypedError as exc:
                s.message = str(exc).capitalize() + "."
                continue
            if value <= 0.0:
                s.message = "An offset needs a distance above zero."
                continue
            d = value
            s.memory["offset"] = d
            continue
        if d:
            found = attempt(ans.point, ans.px)
            if found is None:
                s.message = "There's nothing here to offset."
            elif isinstance(found[1], str):
                s.message = found[1]
            else:
                _add_offsets(s, found[0], found[1])
            continue
        hits = [i for i in pick(s.doc, ans.point, ans.px) if type(s.doc.entities[i].shape) is not Foreign]
        if not hits:
            s.message = "There's nothing here to offset."
            continue
        e = s.doc.entities[hits[0]]

        def through(p, e=e):
            result = offset_of(e, e.shape.project(p)[1].distance(p), p, True)
            return list(result.shapes) if hasattr(result, "shapes") else []

        to = yield GetPoint("Offset: what point should it go through?", preview=through, exclude=frozenset({e.id}))
        try:
            _add_offsets(s, e, offset(e.shape, e.shape.project(to)[1].distance(to), to))
        except ValueError as exc:
            s.message = str(exc)


# --------------------------------------------------------------------------------------------- fillet and chamfer


def _segment_index(s: Polyline, p) -> int:
    n = len(s.segments)
    return min(n - 1, int(s.project(p)[0] * n))


def _vertex_between(s: Polyline, i: int, j: int) -> int | None:
    n = len(s.segments)
    if abs(i - j) == 1:
        return max(i, j)
    if s.closed and {i, j} == {0, n - 1}:
        return 0
    return None


def _corner_command(s, name: str, ask: str, setting: str, read, describe, make):
    """Fillet and chamfer ask the same questions and do the same thing with the answers."""
    def result(a, pa, b, pb, shift):
        return make(a, pa, b, pb, shift, setting_now[0])

    setting_now = [s.memory.get(setting, read(None))]

    def attempt(first_id, first_point, p, px, shift):
        e1 = s.doc.entities.get(first_id)
        for i in pick(s.doc, p, px):
            e2 = s.doc.entities.get(i)
            if e1 is None or e2 is None:
                continue
            try:
                if i == first_id:
                    if type(e1.shape) is not Polyline:
                        continue
                    vertex = _vertex_between(e1.shape, _segment_index(e1.shape, first_point), _segment_index(e1.shape, p))
                    if vertex is None:
                        continue
                    return e1, e2, corner_of_polyline(e1.shape, vertex, lambda a, pa, b, pb: result(a, pa, b, pb, shift))
                if type(e1.shape) is Polyline or type(e2.shape) is Polyline:
                    return e1, e2, "A polyline's corners can be done here, but not one with something else. Use Explode first, or Join to make them one."
                return e1, e2, result(e1.shape, first_point, e2.shape, p, shift)
            except ValueError as exc:
                return e1, e2, str(exc)
        return None

    while True:
        ans = yield GetPick(f"{name}: click the first one, or type {ask} (now {describe(setting_now[0])}). Enter to finish.", preview=_hovering(s), enter=True, typed=True)
        if ans is ENTER:
            return
        if isinstance(ans, Typed):
            try:
                setting_now[0] = read(ans.text)
                s.memory[setting] = setting_now[0]
            except (TypedError, ValueError) as exc:
                s.message = str(exc).capitalize() + "."
            continue
        hits = pick(s.doc, ans.point, ans.px)
        if not hits:
            s.message = "There's nothing here."
            continue
        first_id, first_point = hits[0], ans.point
        first = s.doc.entities[first_id]
        keywords = ("All",) if type(first.shape) is Polyline else ()

        def preview(p, px):
            found = attempt(first_id, first_point, p, px, s.shift)
            if found is None or isinstance(found[2], str):
                return Preview()
            r = found[2]
            if type(r) is Polyline:
                return Preview(ghosts=(r,))
            return Preview(ghosts=tuple(x for x in (r.a, r.b, r.join) if x is not None))

        hint = " Or A for every corner." if keywords else ""
        second = yield GetPick(f"{name}: click the second one. Hold Shift for a sharp corner.{hint}", keywords, preview=preview)
        if second == Keyword("All"):
            try:
                shape, skipped = every_corner(first.shape, lambda a, pa, b, pb: result(a, pa, b, pb, False))
            except ValueError as exc:
                s.message = str(exc)
                continue
            with s.doc.transaction(name) as t:
                t.replace(first_id, shape)
            if skipped:
                s.message = f"{skipped} corner{'s' if skipped != 1 else ''} had no room, and stayed as {'they were' if skipped != 1 else 'it was'}."
            continue
        found = attempt(first_id, first_point, second.point, second.px, second.shift)
        if found is None:
            s.message = "Click a second object, or the next piece of the same polyline."
            continue
        e1, e2, r = found
        if isinstance(r, str):
            s.message = r
            continue
        with s.doc.transaction(name) as t:
            if type(r) is Polyline:
                t.replace(e1.id, r)
                continue
            for e, shape in ((e1, r.a), (e2, r.b)):
                if shape is None:
                    t.remove(e.id)
                else:
                    t.replace(e.id, shape)
            if r.join is not None:
                t.add(r.join, e1.style)


@command("FILLET", "F", group="Modify", help="Round the corner between two lines, or sharpen it", icon="fillet")
def fillet_command(s):
    def read(text):
        if text is None:
            return 0.0
        value = parse_length(text, s.doc.settings.units)
        if value < 0.0:
            raise ValueError("a radius can't be below zero")
        return value

    def make(a, pa, b, pb, shift, r):
        return fillet(a, pa, b, pb, 0.0 if shift else r)

    yield from _corner_command(s, "Fillet", "a radius", "fillet", read, lambda r: _length_text(s, r), make)


@command("CHAMFER", "CHA", group="Modify", help="Cut the corner between two lines with a straight line", icon="chamfer")
def chamfer_command(s):
    def read(text):
        if text is None:
            return (0.0, 0.0)
        parts = [parse_length(x, s.doc.settings.units) for x in text.split(",")]
        if len(parts) > 2 or min(parts) < 0.0:
            raise ValueError("type one distance, or two with a comma between them")
        return (parts[0], parts[-1])

    def make(a, pa, b, pb, shift, d):
        return chamfer(a, pa, b, pb, 0.0, 0.0) if shift else chamfer(a, pa, b, pb, d[0], d[1])

    def describe(d):
        return _length_text(s, d[0]) if d[0] == d[1] else f"{_length_text(s, d[0])}, {_length_text(s, d[1])}"

    yield from _corner_command(s, "Chamfer", "a distance", "chamfer", read, describe, make)


# -------------------------------------------------------------------------------------------------------- array


def _copies(s, label: str, ids: list[int], transforms: list[Affine]) -> int:
    count = 0
    skipped = 0
    with s.doc.transaction(label) as t:
        for m in transforms:
            copies: dict[int, int] = {}
            for i in ids:
                e = s.doc.entities.get(i)
                if e is None:
                    continue
                if type(e.shape) is Foreign:
                    skipped += 1
                    continue
                copies[i] = t.add(e.shape.transformed(m), e.style)
                count += 1
            attach_copies(t, copies)
    if skipped:
        s.message = "What came from a file and can't be changed here wasn't copied."
    return count


def _ghosts_for(s, ids: list[int], transforms: list[Affine], limit: int = 400) -> list:
    out = []
    for m in transforms:
        for i in ids:
            e = s.doc.entities.get(i)
            if e is not None and type(e.shape) is not Foreign:
                out.append(e.shape.transformed(m))
                if len(out) >= limit:
                    return out
    return out


def _count(n) -> int:
    return max(1, int(round(n)))


@command("ARRAY", "AR", group="Modify", help="Copy objects in rows, or round a point", icon="array")
def array(s):
    ids = yield from selected(s, "Array")
    if not ids:
        return
    s.highlights = tuple(ids)
    start = yield GetPoint("Array: pick a point on what you're copying. Or type A to go round a centre.", ("Around",))
    if start == Keyword("Around"):
        centre = yield GetPoint("Array: what point should they go round?")
        n = _count((yield GetNumber("Array: how many in all, counting the original?", default=6.0)))
        if n < 2:
            s.message = "That's only the original."
            return
        if n > MOST_COPIES:
            s.message = f"That's more than {MOST_COPIES} copies."
            return
        total = yield GetAngle("Array: through what angle? Press Enter for a full circle.", base=centre, default=math.tau,
                               preview=lambda p: _ghosts_for(s, ids, [Affine.rotation((p - centre).angle() * k / (n - 1), centre) for k in range(1, n)]))
        # all the way round, the last would land on the first
        step = total / n if abs(abs(total) - math.tau) < 1e-9 else total / (n - 1)
        _copies(s, "Array", ids, [Affine.rotation(step * k, centre) for k in range(1, n)])
        return
    next_at = yield GetPoint("Array: where does the next copy go?", base=start, preview=lambda p: _ghosts_for(s, ids, [Affine.translation(p[0] - start[0], p[1] - start[1])]))
    if near(next_at, start):
        s.message = "That's the same point, so the copies would all be in one place."
        return
    across = next_at - start

    def row_of(count: int, gap: Vec) -> list[Affine]:
        return [Affine.translation(gap[0] * k, gap[1] * k) for k in range(count)]

    columns = _count((yield GetNumber("Array: how many in that direction, counting the original?", default=3.0)))
    if columns > MOST_COPIES:
        s.message = f"That's more than {MOST_COPIES} copies."
        return

    def grid(rows: int, up: Vec) -> list[Affine]:
        out = []
        for r in range(rows):
            for c in range(columns):
                if r or c:
                    out.append(Affine.translation(across[0] * c + up[0] * r, across[1] * c + up[1] * r))
        return out

    up = yield GetPoint("Array: where does the next row go? Press Enter for this one row.", base=start, enter=True,
                        preview=lambda p: _ghosts_for(s, ids, grid(2, p - start)))
    rows = 1
    gap = Vec(0.0, 0.0)
    if up is not ENTER:
        gap = up - start
        rows = _count((yield GetNumber("Array: how many rows, counting this one?", default=2.0)))
    if rows * columns > MOST_COPIES:
        s.message = f"That's more than {MOST_COPIES} copies."
        return
    if rows * columns < 2:
        s.message = "That's only the original."
        return
    _copies(s, "Array", ids, grid(rows, gap))


# -------------------------------------------------------------------------------------------------------- break


@command("BREAK", "BR", group="Modify", help="Split something at a point, or take out what's between two", icon="break")
def break_command(s):
    ans = yield GetPick("Break: click what to break.", preview=_hovering(s))
    hits = [i for i in pick(s.doc, ans.point, ans.px) if isinstance(s.doc.entities[i].shape, CUTTABLE)]
    if not hits:
        s.message = "Only lines, arcs, circles, ellipses, splines and polylines can be broken."
        return
    e = s.doc.entities[hits[0]]
    s.highlights = (e.id,)
    first = yield GetPoint("Break: at what point?")
    t0 = e.shape.project(first)[0]

    def show(p):
        try:
            return list(break_at(e.shape, t0, e.shape.project(p)[0]))
        except ValueError:
            return []

    second = yield GetPoint("Break: click a second point, to take out what's between them. Or press Enter to split it here.", enter=True, preview=show)
    try:
        pieces = break_at(e.shape, t0, None if second is ENTER else e.shape.project(second)[0])
    except ValueError as exc:
        s.message = str(exc)
        return
    with s.doc.transaction("Break") as t:
        apply_pieces(t, e, pieces)


# ----------------------------------------------------------------------------------------------- join and explode


@command("JOIN", "J", group="Modify", help="Make lines and arcs that meet end to end into one object", icon="join")
def join_command(s):
    ids = yield from selected(s, "Join")
    if not ids:
        return
    items = [(i, s.doc.entities[i].shape) for i in ids if i in s.doc.entities]
    runs, alone = join(items)
    if not runs:
        s.message = "None of those meet end to end. They need to be lines, arcs or open polylines."
        return
    with s.doc.transaction("Join") as t:
        for run in runs:
            t.replace(run.ids[0], run.shape)
            for i in run.ids[1:]:
                t.remove(i)
    s.set_selection([run.ids[0] for run in runs])
    used = sum(len(run.ids) for run in runs)
    missed = len(items) - used
    s.message = f"Joined {used} into {len(runs)}." + (f" {missed} didn't meet anything." if missed else "")


@command("EXPLODE", "X", group="Modify", help="Break a polyline or a block into its parts", icon="explode")
def explode(s):
    ids = yield from selected(s, "Explode")
    if not ids:
        return
    made: list[int] = []
    problems: list[str] = []
    done = 0
    with s.doc.transaction("Explode") as t:
        for i in ids:
            e = s.doc.entities.get(i)
            if e is None:
                continue
            try:
                parts = explode_shape(s.doc, e.shape, e.style)
            except ValueError as exc:
                problems.append(str(exc))
                continue
            t.remove(i)
            made.extend(t.add(shape, style) for shape, style in parts)
            done += 1
    if not done:
        s.message = problems[0] if problems else "Nothing there to take apart."
        return
    s.set_selection(made)
    s.message = f"Took {done} object{'s' if done != 1 else ''} apart into {len(made)}."


# ------------------------------------------------------------------------------------------------------ stretch


@command("STRETCH", "S", group="Modify", help="Pull the ends inside an area, leaving the rest where it is", icon="stretch")
def stretch(s):
    a = yield GetPoint("Stretch: click a corner of the area round the ends to pull.")

    def frame(p):
        return [Polyline((a, Vec(p[0], a[1]), p, Vec(a[0], p[1])), closed=True)]

    b = yield GetPoint("Stretch: click the other corner.", preview=frame)
    box = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
    still = Affine.translation(0.0, 0.0)
    ids: list[int] = []
    seen: set[int] = set()
    for key in s.doc.index.query(box):
        i = split_key(key)[0]
        if i in seen:
            continue
        seen.add(i)
        e = s.doc.entities.get(i)
        if e is None or s.doc.is_hidden(e.style) or s.doc.is_locked(e.style):
            continue
        if stretched(e.shape, box, still) is not None:
            ids.append(i)
    if not ids:
        s.message = "Nothing has an end inside that area."
        return
    s.highlights = tuple(ids)
    base = yield GetPoint("Stretch: pick a point to pull from.")

    def changes(p) -> list:
        m = Affine.translation(p[0] - base[0], p[1] - base[1])
        out = []
        for i in ids:
            shape = stretched(s.doc.entities[i].shape, box, m)
            if shape is not None:
                out.append((i, shape))
        return out

    to = yield GetPoint("Stretch: where to? Type a distance or x,y.", base=base, preview=lambda p: [shape for _, shape in changes(p)])
    with s.doc.transaction("Stretch") as t:
        for i, shape in changes(to):
            t.replace(i, shape)


# -------------------------------------------------------------------------------------------------------- match


@command("MATCH", "MA", group="Modify", help="Make things look like something else: its layer, colour and line type", icon="match")
def match(s):
    ans = yield GetPick("Match: click the object to copy the look of.", preview=_hovering(s))
    hits = pick(s.doc, ans.point, ans.px)
    if not hits:
        s.message = "There's nothing here."
        return
    style = s.doc.entities[hits[0]].style
    s.highlights = (hits[0],)
    while True:
        ans = yield GetPick("Match: click what should look like it, or drag across several. Enter to finish.", preview=_hovering(s), enter=True, swipe=True)
        if ans is ENTER:
            return
        for i in pick(s.doc, ans.point, ans.px):
            if s.doc.entities[i].style != style:
                with s.doc.transaction("Match") as t:
                    t.replace(i, style=style)
                break
