# SPDX-License-Identifier: GPL-3.0-or-later
"""The spatial index: a loose quadtree that only divides where there's something to divide.

An object lives in a cell at least as big as itself, chosen by its centre. A cell's reach is twice its size, which is
what makes "loose" work: an object can stick out of its cell by up to half the cell's size and still be found, so an
object never needs splitting or storing twice. A cell divides only when it holds more than a handful, so a tight cluster
of fine detail in a huge drawing gets deep cells there and nowhere else, and looking near the cursor costs about the same
in a drawing of a hundred objects as in a million.

Inserting and removing are exact, with nothing to rebalance and nothing that can go stale, and results come back sorted,
so what the index returns never depends on the order things were added.
"""
from __future__ import annotations

import math

from .vec import BBox

_CAPACITY = 16
_MAX_DEPTH = 96     # identical points can't be separated, however deep it goes, so they share a cell at this depth


class _Cell:
    __slots__ = ("cx", "cy", "h", "items", "kids", "parent", "depth")

    def __init__(self, cx: float, cy: float, h: float, parent: "_Cell | None", depth: int):
        self.cx, self.cy, self.h = cx, cy, h
        self.items: dict[int, BBox] = {}
        self.kids: list[_Cell] = []
        self.parent = parent
        self.depth = depth


class SpatialIndex:
    __slots__ = ("_root", "_where")

    def __init__(self) -> None:
        self._root = _Cell(0.0, 0.0, 1024.0, None, 0)
        self._where: dict[int, _Cell] = {}

    def __len__(self) -> int:
        return len(self._where)

    def __contains__(self, key: int) -> bool:
        return key in self._where

    def insert(self, key: int, box: BBox) -> None:
        if key in self._where:
            self.remove(key)
        x0, y0, x1, y1 = box
        if not (math.isfinite(x0) and math.isfinite(y0) and math.isfinite(x1) and math.isfinite(y1)):
            raise ValueError("a spatial index can only hold finite positions")
        cx, cy = (x0 + x1) * 0.5, (y0 + y1) * 0.5
        half = max(x1 - x0, y1 - y0) * 0.5
        if not math.isfinite(cx + cy + half):
            raise ValueError("a spatial index can't hold positions this large")
        self._cover(cx, cy, half)
        cell = self._root
        while True:
            h = cell.h * 0.5
            if h < half:
                break               # too big to go any lower
            kid = _child_at(cell, cx, cy)
            if kid is None:
                if len(cell.items) < _CAPACITY or cell.depth >= _MAX_DEPTH:
                    break
                _divide(cell, self._where)
                kid = _child_at(cell, cx, cy)
                if kid is None:
                    kid = _add_child(cell, cx, cy)
            cell = kid
        cell.items[key] = box
        self._where[key] = cell

    def _cover(self, cx: float, cy: float, half: float) -> None:
        """Make the top cell big enough, keeping everything below it."""
        while True:
            root = self._root
            h = root.h
            if abs(cx) < h and abs(cy) < h and half <= h:
                return
            top = _Cell(0.0, 0.0, h * 2.0, None, 0)
            # the old top's quarters become grandchildren of the new top, each under a new cell the size of the old top
            for kid in root.kids:
                sx, sy = (h if kid.cx > 0.0 else -h), (h if kid.cy > 0.0 else -h)
                between = _child_exact(top, sx, sy)
                if between is None:
                    between = _Cell(sx, sy, h, top, 1)
                    top.kids.append(between)
                kid.parent = between
                between.kids.append(kid)
            top.items = root.items
            for key in top.items:
                self._where[key] = top
            self._root = top
            _renumber(top)

    def remove(self, key: int) -> None:
        cell = self._where.pop(key, None)
        if cell is None:
            return
        del cell.items[key]
        while cell.parent is not None and not cell.items and not cell.kids:
            cell.parent.kids.remove(cell)
            cell = cell.parent

    def query(self, window: BBox) -> list[int]:
        """Every key whose box touches the window, sorted."""
        x0, y0, x1, y1 = window
        out: list[int] = []
        append = out.append
        stack = [self._root]
        pop = stack.pop
        while stack:
            cell = pop()
            reach = cell.h + cell.h
            cx, cy = cell.cx, cell.cy
            if cx - reach > x1 or cx + reach < x0 or cy - reach > y1 or cy + reach < y0:
                continue
            items = cell.items
            if items:
                for key, (a, b, c, d) in items.items():
                    if a <= x1 and c >= x0 and b <= y1 and d >= y0:
                        append(key)
            if cell.kids:
                stack.extend(cell.kids)
        out.sort()
        return out

    def bounds(self) -> BBox | None:
        """The box round everything, or None if empty. Walks every object, so call it when the view is fitted, not per frame."""
        box = None
        stack = [self._root]
        while stack:
            cell = stack.pop()
            for (a, b, c, d) in cell.items.values():
                if box is None:
                    box = [a, b, c, d]
                else:
                    if a < box[0]:
                        box[0] = a
                    if b < box[1]:
                        box[1] = b
                    if c > box[2]:
                        box[2] = c
                    if d > box[3]:
                        box[3] = d
            stack.extend(cell.kids)
        return tuple(box) if box is not None else None


def _child_at(cell: _Cell, x: float, y: float) -> _Cell | None:
    east, north = x >= cell.cx, y >= cell.cy
    for kid in cell.kids:
        if (kid.cx >= cell.cx) == east and (kid.cy >= cell.cy) == north:
            return kid
    return None


def _child_exact(cell: _Cell, cx: float, cy: float) -> _Cell | None:
    for kid in cell.kids:
        if kid.cx == cx and kid.cy == cy:
            return kid
    return None


def _add_child(cell: _Cell, x: float, y: float) -> _Cell:
    q = cell.h * 0.5
    kid = _Cell(cell.cx + (q if x >= cell.cx else -q), cell.cy + (q if y >= cell.cy else -q), q, cell, cell.depth + 1)
    cell.kids.append(kid)
    return kid


def _divide(cell: _Cell, where: dict[int, _Cell]) -> None:
    """Move what fits down a level. What's too big to go lower stays."""
    q = cell.h * 0.5
    stay: dict[int, BBox] = {}
    for key, box in cell.items.items():
        half = max(box[2] - box[0], box[3] - box[1]) * 0.5
        if half > q:
            stay[key] = box
            continue
        x, y = (box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5
        kid = _child_at(cell, x, y) or _add_child(cell, x, y)
        kid.items[key] = box
        where[key] = kid
    cell.items = stay


def _renumber(top: _Cell) -> None:
    """Depth only guards against endless division, so after the top grows it's enough that it stays roughly right."""
    for kid in top.kids:
        kid.depth = 1
