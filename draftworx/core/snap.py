# SPDX-License-Identifier: GPL-3.0-or-later
"""Finding the point the person meant. See docs/SNAPPING.md for the behaviour this has to keep.

Given where the cursor is, this returns every candidate in order, best first. It uses no clock and no window: the same
drawing, cursor and history give the same answer, every time, so it's tested directly. Every candidate's point is computed
from the geometry, never from the screen. The screen only decides which candidates are near enough to be offered.

The ranking is the heart of it. A candidate's score is its distance from the cursor in pixels, plus a handicap for its
kind. So a point the geometry really has wins whenever it's about as close as anything else, and something worked out
from an alignment only wins where the cursor is clearly on it and clearly not on a real point.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable

from .curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from .intersect import intersect
from .model import ARC_SUB, CENTRE_SUB, Document, Entity, index_key, polyline_entries, split_key
from .shapes import AttDef, Insert, MText, Node, Solid, Text
from .spatial import SpatialIndex
from .vec import IDENTITY, Affine, Vec, near, polar, tol


class Kind(IntEnum):
    ENDPOINT = 0
    INTERSECTION = 1
    NODE = 2
    INSERTION = 3
    CENTER = 4
    GEOMETRIC_CENTER = 5
    MIDPOINT = 6
    QUADRANT = 7
    PERPENDICULAR = 8
    TANGENT = 9
    GUIDES_MEET = 10        # two alignment lines cross
    GUIDE_ON_OBJECT = 11    # an alignment line crosses something real
    GUIDE = 12              # on one alignment line
    ON = 13                 # on an object, nowhere in particular
    GRID = 14
    FREE = 15


LABELS = {Kind.ENDPOINT: "Endpoint", Kind.INTERSECTION: "Intersection", Kind.NODE: "Point", Kind.INSERTION: "Insertion",
          Kind.CENTER: "Center", Kind.GEOMETRIC_CENTER: "Geometric center", Kind.MIDPOINT: "Midpoint", Kind.QUADRANT: "Quadrant",
          Kind.PERPENDICULAR: "Perpendicular", Kind.TANGENT: "Tangent", Kind.GUIDES_MEET: "Alignment", Kind.GUIDE_ON_OBJECT: "Alignment",
          Kind.GUIDE: "Alignment", Kind.ON: "On", Kind.GRID: "Grid", Kind.FREE: ""}

#: What each kind gives away, in pixels. See the module docstring.
HANDICAP = (0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.0, 2.0, 5.0, 5.0, 7.0, 7.0, 10.0, 14.0, 16.0, 99.0)
#: Kinds whose real location is a point the geometry has
REAL = frozenset((Kind.ENDPOINT, Kind.INTERSECTION, Kind.NODE, Kind.INSERTION, Kind.CENTER, Kind.GEOMETRIC_CENTER, Kind.MIDPOINT, Kind.QUADRANT))
#: What hovering can pick up as a point to track from
ACQUIRABLE = REAL | {Kind.PERPENDICULAR, Kind.TANGENT}
ALL_KINDS = frozenset(Kind) - {Kind.GRID, Kind.FREE}

APERTURE = 14.0         # pixels: how far from a point it's offered
GUIDE_REACH = 7.0       # pixels: how far from an alignment line it catches
ON_REACH = 7.0          # pixels: how far from an object counts as on it
STICK = 4.0             # pixels: what the current choice gets as a head start, so it doesn't flicker
STICK_REACH = 1.3       # the current choice stays on offer this much further out than a new one would be
MOST_CURVES = 24        # the nearest curves to look at closely: more only happens when zoomed far out
MOST_ENTRIES = 256      # the nearest index entries to look at
MOST_POINTS = 128       # the nearest points to offer, which is more than anyone will press Tab through
_ANYWHERE_ON_LOCK = HANDICAP[Kind.GUIDE] + GUIDE_REACH     # just being on a locked line loses to anything in reach


def tidy_step(px: float) -> float:
    """The round number, 1, 2 or 5 times a power of ten, that's about one pixel at this zoom."""
    if px <= 0.0 or not math.isfinite(px):
        return 0.0
    e = math.floor(math.log10(px))
    base = 10.0 ** e
    for k in (1.0, 2.0, 5.0, 10.0):
        if base * k >= px:
            return base * k
    return base * 10.0


def tidy(value: float, px: float) -> float:
    """A free position, rounded to what the screen can show. Lengths read as round numbers and nothing looks different."""
    step = tidy_step(px)
    if step == 0.0:
        return value
    # through decimal digits, so 0.1 steps give 123.4, not 123.40000000000001
    return round(round(value / step) * step, -math.floor(math.log10(step)))


@dataclass(frozen=True, slots=True)
class Guide:
    """An alignment line through `origin`. `label` says what it is, for the tooltip."""
    origin: Vec
    direction: Vec      # unit length
    label: str
    source: str = ""    # what it comes from: "Endpoint", "last point"

    def foot(self, p) -> tuple[float, Vec]:
        (ox, oy), (dx, dy) = self.origin, self.direction
        t = (p[0] - ox) * dx + (p[1] - oy) * dy
        return t, Vec(ox + dx * t, oy + dy * t)

    def line(self) -> Line:
        (ox, oy), (dx, dy) = self.origin, self.direction
        return Line(self.origin, Vec(ox + dx, oy + dy))


@dataclass(frozen=True, slots=True)
class Tracked:
    """A point picked up by hovering. Alignment lines run from it horizontally and vertically, and along what it's on."""
    point: Vec
    label: str
    along: tuple[Vec, ...] = ()     # directions of what it was on, for extension lines


@dataclass(frozen=True, slots=True)
class Lock:
    """What the person has fixed. A line: the point stays on it. A radius: it stays that far from `origin`. Both: it's fixed."""
    origin: Vec
    direction: Vec | None = None
    radius: float | None = None
    label: str = ""


@dataclass(frozen=True, slots=True)
class Candidate:
    point: Vec
    kind: Kind
    distance: float                     # pixels from the cursor
    score: float
    sources: tuple[int, ...] = ()       # the entities it comes from
    guides: tuple[Guide, ...] = ()
    detail: str = ""

    @property
    def label(self) -> str:
        return self.detail or LABELS[self.kind]

    def same_as(self, other: "Candidate") -> bool:
        """The same choice as before? A point on an alignment line moves with the cursor, and it's still the same line."""
        if self.kind != other.kind:
            return False
        if self.guides or other.guides:
            return [(g.origin, g.direction) for g in self.guides] == [(g.origin, g.direction) for g in other.guides]
        if self.kind == Kind.ON:
            return self.sources == other.sources
        return near(self.point, other.point)


@dataclass(frozen=True, slots=True)
class SnapQuery:
    cursor: Vec
    px: float                                   # drawing units per pixel
    last: Vec | None = None                     # the point being measured from, if there is one
    tracked: tuple[Tracked, ...] = ()
    lock: Lock | None = None
    kinds: frozenset = ALL_KINDS
    polar_step: float = math.pi / 4.0           # 0 turns polar lines off
    parallel_to: tuple[tuple[Vec, str], ...] = ()   # directions to offer from `last`: (direction, what it is)
    exclude: frozenset = frozenset()            # ids to ignore, like what's being moved
    extra: tuple[object, ...] = ()              # curves that don't exist yet, like the polyline being drawn
    grid: float = 0.0                           # spacing, when grid snap is on
    grid_origin: Vec = Vec(0.0, 0.0)
    enabled: bool = True
    aperture: float = APERTURE


@dataclass(frozen=True, slots=True)
class SnapResult:
    point: Vec
    candidate: Candidate | None
    alternatives: tuple[Candidate, ...]
    #: Everything to show: alignment lines in use, so you can see why the point is where it is.
    guides: tuple[Guide, ...] = ()


class _Near:
    """A curve near the cursor, with a name that stays the same between mouse moves."""
    __slots__ = ("curve", "owner", "key", "gap", "foot", "t")

    def __init__(self, curve, owner: int, key: tuple):
        self.curve = curve
        self.owner = owner
        self.key = key
        self.gap = math.inf
        self.foot = None
        self.t = 0.0


class _BlockView:
    """A block's contents with their own index, shared by every copy of it."""
    __slots__ = ("definition", "index", "items")

    def __init__(self, doc: Document, definition):
        self.definition = definition
        self.index = SpatialIndex()
        self.items = list(definition.entities)
        for i, e in enumerate(self.items):
            s = e.shape
            if type(s) is Polyline:
                for sub, box in polyline_entries(s):
                    self.index.insert(index_key(i, sub), box)
                continue
            box = doc.shape_bbox(s)
            if box is not None:
                self.index.insert(index_key(i), box)
            k = type(s)
            if k is Arc or k is Circle or k is EllipseArc:
                self.index.insert(index_key(i, CENTRE_SUB), (s.c[0], s.c[1], s.c[0], s.c[1]))
            elif k is Insert:
                self.index.insert(index_key(i, CENTRE_SUB), (s.p[0], s.p[1], s.p[0], s.p[1]))


class SnapEngine:
    """Remembers intersections between moves, and forgets them whenever the drawing changes."""

    def __init__(self, doc: Document):
        self.doc = doc
        self._crossings: dict[tuple, list[Vec]] = {}
        self._views: dict[str, _BlockView] = {}
        doc.on_change(self._forget)

    def _forget(self, changes=()) -> None:
        self._crossings.clear()
        if any(c.table == "block" for c in changes):
            self._views.clear()     # a block's size feeds into every block that places it

    def view_of(self, name: str) -> _BlockView | None:
        key = name.casefold()
        d = self.doc.blocks.get(name)
        if d is None:
            return None
        v = self._views.get(key)
        if v is None or v.definition is not d:
            v = self._views[key] = _BlockView(self.doc, d)
        return v

    # ------------------------------------------------------------------------------------------ what's near

    def _gather(self, q: SnapQuery, reach: float) -> tuple[list[_Near], list[tuple[Vec, Kind, int]]]:
        cx, cy = q.cursor
        window = (cx - reach, cy - reach, cx + reach, cy + reach)
        curves: list[_Near] = []
        points: list[tuple[Vec, Kind, int]] = []
        keys = self.doc.index.query(window)
        if len(keys) > MOST_ENTRIES:
            keys = self._nearest_keys(keys, cx, cy)
        doc = self.doc
        entities = doc.entities
        exclude = q.exclude
        hidden = doc._hidden_layers
        for key in keys:
            eid, sub = split_key(key)
            if eid in exclude:
                continue
            e = entities.get(eid)
            if e is None or e.style.layer.casefold() in hidden:
                continue
            self._entry(e, sub, eid, (eid, sub), IDENTITY, window, curves, points, 0)
        for i, c in enumerate(q.extra):
            self._extra(c, i, curves, points)
        return curves, points

    def _nearest_keys(self, keys: list[int], cx: float, cy: float) -> list[int]:
        """Zoomed far out, thousands of things can be under the aperture. Take the closest, by a stable rule."""
        def rank(key):
            eid, sub = split_key(key)
            e = self.doc.entities.get(eid)
            if e is None:
                return (math.inf, key)
            box = self.doc.shape_bbox(e.shape) if type(e.shape) is not Polyline or sub >= ARC_SUB or not e.shape.segments \
                else e.shape.segments[sub].bbox()
            if box is None:
                return (math.inf, key)
            dx = (box[0] + box[2]) * 0.5 - cx
            dy = (box[1] + box[3]) * 0.5 - cy
            return (dx * dx + dy * dy, key)
        return sorted(keys, key=rank)[:MOST_ENTRIES]

    def _extra(self, c, i: int, curves: list[_Near], points: list) -> None:
        key = (-1, i)
        if type(c) is Polyline:
            for j, seg in enumerate(c.segments):
                self._curve_entry(seg, 0, key + (j,), curves, points, ends=(c.pts[j], c.pts[(j + 1) % len(c.pts)]))
            return
        self._curve_entry(c, 0, key, curves, points)

    def _entry(self, e: Entity, sub: int, owner: int, name: tuple, m: Affine, window, curves, points, depth: int) -> None:
        s = e.shape
        k = type(s)
        moved = m is not IDENTITY
        if sub >= ARC_SUB and sub != CENTRE_SUB:
            c = s.segments[sub - ARC_SUB].c
            points.append((m.apply(c) if moved else c, Kind.CENTER, owner))
            return
        if sub == CENTRE_SUB:
            if k is Polyline:
                c = s.centroid()
                if c is not None:
                    points.append((m.apply(c) if moved else c, Kind.GEOMETRIC_CENTER, owner))
            elif k is Insert:
                points.append((m.apply(s.p) if moved else s.p, Kind.INSERTION, owner))
            else:
                points.append((m.apply(s.c) if moved else s.c, Kind.CENTER, owner))
            return
        if k is Line or k is Arc or k is Circle or k is EllipseArc or k is Spline:
            self._curve_entry(s.transformed(m) if moved else s, owner, name, curves, points)
        elif k is Polyline:
            segs = s.segments
            if not segs:
                points.append((m.apply(s.pts[0]) if moved else s.pts[0], Kind.NODE, owner))
                return
            seg = segs[sub]
            a, b = s.pts[sub], s.pts[(sub + 1) % len(s.pts)]
            if moved:
                self._curve_entry(seg.transformed(m), owner, name, curves, points, ends=(m.apply(a), m.apply(b)))
            else:
                self._curve_entry(seg, owner, name, curves, points, ends=(a, b))
        elif k is Node:
            points.append((m.apply(s.p) if moved else s.p, Kind.NODE, owner))
        elif k is Insert:
            self._insert(s, owner, name, m, window, curves, points, depth)
        elif k is Text or k is MText:
            points.append((m.apply(s.p) if moved else s.p, Kind.INSERTION, owner))
        elif k is AttDef:
            p = s.attrib.text.p
            points.append((m.apply(p) if moved else p, Kind.INSERTION, owner))
        elif k is Solid:
            for p in s.pts:
                points.append((m.apply(p) if moved else p, Kind.ENDPOINT, owner))

    def _insert(self, ins: Insert, owner: int, name: tuple, m: Affine, window, curves, points, depth: int) -> None:
        if depth > 16:
            return
        view = self.view_of(ins.block)
        if view is None:
            return
        base = view.definition.base
        cells = ins.columns * ins.rows
        for col in range(ins.columns if cells <= 400 else 1):
            for row in range(ins.rows if cells <= 400 else 1):
                local = ins.matrix(base, col, row)
                full = local.then(m) if m is not IDENTITY else local
                try:
                    back = full.inverse()
                except ValueError:
                    continue        # a block scaled to nothing has nothing to snap to
                corners = [back.apply((x, y)) for x in (window[0], window[2]) for y in (window[1], window[3])]
                xs, ys = [c[0] for c in corners], [c[1] for c in corners]
                for key in view.index.query((min(xs), min(ys), max(xs), max(ys))):
                    i, sub = split_key(key)
                    e = view.items[i]
                    if e.style.layer != "0" and e.style.layer.casefold() in self.doc._hidden_layers:
                        continue
                    self._entry(e, sub, owner, name + (col, row, i, sub), full, window, curves, points, depth + 1)

    def _curve_entry(self, c, owner: int, name: tuple, curves: list[_Near], points: list, ends=None) -> None:
        curves.append(_Near(c, owner, name))
        k = type(c)
        if k is Line:
            a, b = ends if ends is not None else (c.a, c.b)
            points.append((a, Kind.ENDPOINT, owner))
            points.append((b, Kind.ENDPOINT, owner))
            points.append((Vec((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5), Kind.MIDPOINT, owner))
        elif k is Arc:
            a, b = ends if ends is not None else (c.start, c.end)
            if not c.closed:
                points.append((a, Kind.ENDPOINT, owner))
                points.append((b, Kind.ENDPOINT, owner))
                points.append((c.point_at(0.5), Kind.MIDPOINT, owner))
            for ang in (0.0, math.pi / 2.0, math.pi, math.pi * 1.5):
                if c.param_of_angle(ang) <= 1.0:
                    points.append((polar(c.c, c.r, ang), Kind.QUADRANT, owner))
        elif k is Circle:
            (x, y), r = c.c, c.r
            points.append((Vec(x + r, y), Kind.QUADRANT, owner))
            points.append((Vec(x, y + r), Kind.QUADRANT, owner))
            points.append((Vec(x - r, y), Kind.QUADRANT, owner))
            points.append((Vec(x, y - r), Kind.QUADRANT, owner))
        elif k is EllipseArc:
            if not c.closed:
                points.append((c.start, Kind.ENDPOINT, owner))
                points.append((c.end, Kind.ENDPOINT, owner))
                points.append((c.point_at(0.5), Kind.MIDPOINT, owner))
            for ang in (0.0, math.pi / 2.0, math.pi, math.pi * 1.5):
                if c.param_to_t(ang) <= 1.0:
                    points.append((c.at_param(ang), Kind.QUADRANT, owner))
        elif k is Spline:
            if not c.closed:
                points.append((c.start, Kind.ENDPOINT, owner))
                points.append((c.end, Kind.ENDPOINT, owner))
            points.append((c.point_at(0.5), Kind.MIDPOINT, owner))
            for p in c.fit:
                points.append((p, Kind.NODE, owner))

    # --------------------------------------------------------------------------------------------- crossings

    def _crossing_points(self, a: _Near, b: _Near) -> list[Vec]:
        key = (a.key, b.key) if a.key <= b.key else (b.key, a.key)
        found = self._crossings.get(key)
        if found is None:
            if len(self._crossings) > 20_000:
                self._crossings.clear()
            found = [h.point for h in intersect(a.curve, b.curve)]
            self._crossings[key] = found
        return found

    # ------------------------------------------------------------------------------------------- candidates

    def candidates(self, q: SnapQuery) -> list[Candidate]:
        """Everything on offer at this cursor, best first."""
        if not q.enabled:
            return []
        px = q.px
        reach = q.aperture * px
        cx, cy = q.cursor
        curves, points = self._gather(q, reach)
        kinds = q.kinds
        out: list[tuple[Vec, Kind, tuple[int, ...]]] = []

        # how close the cursor is to each curve, and only the closest few get looked at closely
        for n in curves:
            n.t, n.foot = n.curve.project(q.cursor)
            n.gap = math.hypot(n.foot[0] - cx, n.foot[1] - cy)
        curves.sort(key=lambda n: (n.gap, n.key))
        touching = [n for n in curves if n.gap <= reach][:MOST_CURVES]

        limit2 = reach * reach
        close = []
        for p, kind, owner in points:
            if kind in kinds:
                dx, dy = p[0] - cx, p[1] - cy
                d2 = dx * dx + dy * dy
                if d2 <= limit2:
                    close.append((d2, kind, p, owner))
        if len(close) > MOST_POINTS:
            close = heapq.nsmallest(MOST_POINTS, close, key=lambda f: (f[0], f[1], f[2], f[3]))
        for _, kind, p, owner in close:
            out.append((p, kind, (owner,) if owner else ()))

        if Kind.INTERSECTION in kinds:
            self._add_crossings(touching, cx, cy, limit2, out)
        if q.last is not None:
            if Kind.PERPENDICULAR in kinds:
                self._add_perpendiculars(touching, q.last, q.cursor, reach, out)
            if Kind.TANGENT in kinds:
                self._add_tangents(touching, q.last, q.cursor, reach, out)
        if Kind.ON in kinds:
            on_reach = ON_REACH * px
            for n in touching:
                if n.gap <= on_reach:
                    out.append((n.foot, Kind.ON, (n.owner,) if n.owner else ()))

        result = _rank(_merge(out), q.cursor, px)
        guides = self._guides(q)
        if guides and q.lock is None:
            result.extend(self._guide_candidates(guides, touching, q))
            result.sort(key=_order)
        if q.lock is not None:
            result = self._apply_lock(q, result, touching)
        return result

    def _add_crossings(self, touching: list[_Near], cx, cy, limit2, out) -> None:
        for i, a in enumerate(touching):
            for b in touching[i + 1:]:
                if a.key[:-1] == b.key[:-1] and _adjacent(a.key, b.key):
                    continue        # neighbours in a polyline only meet at the vertex they share
                for p in self._crossing_points(a, b):
                    dx, dy = p[0] - cx, p[1] - cy
                    if dx * dx + dy * dy <= limit2:
                        out.append((p, Kind.INTERSECTION, tuple(x for x in (a.owner, b.owner) if x)))

    def _add_perpendiculars(self, touching, last: Vec, cursor: Vec, reach: float, out) -> None:
        for n in touching:
            c = n.curve
            k = type(c)
            found: list[Vec] = []
            if k is Line:
                t = c.param_of(last)
                if 0.0 <= t <= 1.0:
                    found.append(c.point_at(t))
            elif k is Arc or k is Circle:
                d = last - c.c
                if d.length2() > 0.0:
                    u = d.unit()
                    for s in (1.0, -1.0):
                        p = Vec(c.c[0] + u[0] * c.r * s, c.c[1] + u[1] * c.r * s)
                        if k is Circle or c.param_of_angle(math.atan2(u[1] * s, u[0] * s)) <= 1.0:
                            found.append(p)
            else:
                f = _solve_near(c, n.t, lambda q, d: (q[0] - last[0]) * d[0] + (q[1] - last[1]) * d[1])
                if f is not None:
                    found.append(f)
            owner = (n.owner,) if n.owner else ()
            for p in found:
                if math.hypot(p[0] - cursor[0], p[1] - cursor[1]) <= reach and not near(p, last):
                    out.append((p, Kind.PERPENDICULAR, owner))

    def _add_tangents(self, touching, last: Vec, cursor: Vec, reach: float, out) -> None:
        for n in touching:
            c = n.curve
            k = type(c)
            found: list[Vec] = []
            if k is Arc or k is Circle:
                found = _circle_tangents(c.c, c.r, last)
                if k is Arc:
                    found = [p for p in found if c.param_of_angle(math.atan2(p[1] - c.c[1], p[0] - c.c[0])) <= 1.0]
            elif k is EllipseArc:
                # An ellipse is a stretched circle, and a stretch keeps tangency.
                a = c.major.length()
                ux, uy = c.major[0] / a, c.major[1] / a
                b = a * c.ratio
                to_local = Affine(ux / a, -uy / b, uy / a, ux / b, 0.0, 0.0)
                d = last - c.c
                local = to_local.apply(d)
                for p in _circle_tangents(Vec(0.0, 0.0), 1.0, local):
                    ang = math.atan2(p[1], p[0])
                    if c.param_to_t(ang) <= 1.0:
                        found.append(c.at_param(ang))
            elif k is Spline:
                f = _solve_near(c, n.t, lambda q, d: (q[0] - last[0]) * d[1] - (q[1] - last[1]) * d[0])
                if f is not None:
                    found.append(f)
            owner = (n.owner,) if n.owner else ()
            for p in found:
                if math.hypot(p[0] - cursor[0], p[1] - cursor[1]) <= reach:
                    out.append((p, Kind.TANGENT, owner))

    # ---------------------------------------------------------------------------------------------- guides

    def _guides(self, q: SnapQuery) -> list[Guide]:
        out: list[Guide] = []
        if Kind.GUIDE not in q.kinds:
            return out
        seen: set = set()

        def add(origin: Vec, d: Vec, label: str, source: str) -> None:
            n = d.unit()
            # a line doesn't care which way it runs, so one line for both directions
            if n[1] < 0.0 or (n[1] == 0.0 and n[0] < 0.0):
                n = -n
            key = (origin, round(n[0], 12), round(n[1], 12))
            if key not in seen:
                seen.add(key)
                out.append(Guide(origin, n, label, source))
        for t in q.tracked:
            add(t.point, Vec(1.0, 0.0), "Horizontal", t.label)
            add(t.point, Vec(0.0, 1.0), "Vertical", t.label)
            for d in t.along:
                add(t.point, d, "Extension", t.label)
        if q.last is not None:
            if q.polar_step > 0.0:
                n = max(1, round(math.pi / q.polar_step))
                for i in range(n):
                    a = i * math.pi / n
                    add(q.last, _exact_direction(a), _angle_label(a), "last point")
            for d, what in q.parallel_to:
                add(q.last, d, f"Parallel to {what}", "last point")
                add(q.last, d.perp(), f"Perpendicular to {what}", "last point")
        return out

    def _guide_candidates(self, guides: list[Guide], touching: list[_Near], q: SnapQuery) -> list[Candidate]:
        px = q.px
        cursor = q.cursor
        reach = GUIDE_REACH * px
        minimum = 8.0 * px
        active: list[tuple[Guide, float, Vec, float]] = []
        for g in guides:
            t, foot = g.foot(cursor)
            gap = math.hypot(foot[0] - cursor[0], foot[1] - cursor[1])
            if gap <= reach and abs(t) >= minimum:
                active.append((g, t, foot, gap))
        out: list[Candidate] = []
        if not active:
            return out
        limit = q.aperture * px
        kinds = q.kinds
        if Kind.GUIDES_MEET in kinds:
            for i, (g, _, _, _) in enumerate(active):
                for (h, _, _, _) in active[i + 1:]:
                    if g.origin == h.origin:
                        continue
                    hit = intersect(g.line(), h.line(), True, True)
                    if hit and hit[0].point.distance(cursor) <= limit:
                        p = hit[0].point
                        d = p.distance(cursor) / px
                        out.append(Candidate(p, Kind.GUIDES_MEET, d, d + HANDICAP[Kind.GUIDES_MEET], (), (g, h)))
        if Kind.GUIDE_ON_OBJECT in kinds:
            for g, _, _, _ in active[:8]:
                for n in touching[:12]:
                    for hit in intersect(g.line(), n.curve, True, False):
                        if hit.point.distance(cursor) <= limit:
                            d = hit.point.distance(cursor) / px
                            out.append(Candidate(hit.point, Kind.GUIDE_ON_OBJECT, d, d + HANDICAP[Kind.GUIDE_ON_OBJECT],
                                                 (n.owner,) if n.owner else (), (g,)))
        for g, t, _, gap in active:
            # along the line, a free position is tidied like any other, and the point stays exactly on the line
            along = tidy(t, px)
            p = Vec(g.origin[0] + g.direction[0] * along, g.origin[1] + g.direction[1] * along)
            d = gap / px
            out.append(Candidate(p, Kind.GUIDE, d, d + HANDICAP[Kind.GUIDE], (), (g,)))
        return out

    # ----------------------------------------------------------------------------------------------- locks

    def _apply_lock(self, q: SnapQuery, offers: list[Candidate], touching: list[_Near]) -> list[Candidate]:
        """When something's fixed, what's offered is what's consistent with it. A real point that isn't on the locked line
        offers the place on the line that's in line with it: that's what pointing at it while locked means."""
        lock = q.lock
        px = q.px
        cursor = q.cursor
        out: list[Candidate] = []
        if lock.direction is not None and lock.radius is not None:
            p = lock.origin + lock.direction * lock.radius
            return [Candidate(p, Kind.FREE, p.distance(cursor) / px, 0.0, (), (), lock.label)]
        reach = q.aperture * px
        if lock.direction is not None:
            g = Guide(lock.origin, lock.direction, lock.label or "Locked", "last point")
            for n in touching[:12]:
                for hit in intersect(g.line(), n.curve, True, False):
                    if hit.point.distance(cursor) <= reach:
                        d = hit.point.distance(cursor) / px
                        out.append(Candidate(hit.point, Kind.GUIDE_ON_OBJECT, d, d + HANDICAP[Kind.GUIDE_ON_OBJECT],
                                             (n.owner,) if n.owner else (), (g,)))
            for c in offers:
                if c.kind in REAL:
                    t, foot = g.foot(c.point)
                    if near(foot, lock.origin):
                        continue
                    out.append(Candidate(foot, c.kind, c.distance, c.score + 3.0, c.sources, (g,), f"In line with {c.label.lower()}"))
            t, _ = g.foot(cursor)
            along = tidy(t, px)
            p = Vec(lock.origin[0] + lock.direction[0] * along, lock.origin[1] + lock.direction[1] * along)
            out.append(Candidate(p, Kind.GUIDE, 0.0, _ANYWHERE_ON_LOCK, (), (g,), lock.label))
        else:
            r = lock.radius
            ring = Circle(lock.origin, r)
            for n in touching[:12]:
                for hit in intersect(ring, n.curve):
                    if hit.point.distance(cursor) <= reach:
                        d = hit.point.distance(cursor) / px
                        out.append(Candidate(hit.point, Kind.GUIDE_ON_OBJECT, d, d + HANDICAP[Kind.GUIDE_ON_OBJECT],
                                             (n.owner,) if n.owner else ()))
            for c in offers:
                if c.kind in REAL:
                    d = c.point - lock.origin
                    if d.length2() == 0.0:
                        continue
                    foot = lock.origin + d.unit() * r
                    out.append(Candidate(foot, c.kind, c.distance, c.score + 3.0, c.sources, (), f"Toward {c.label.lower()}"))
            d = cursor - lock.origin
            u = d.unit() if d.length2() > 0.0 else Vec(1.0, 0.0)
            out.append(Candidate(lock.origin + u * r, Kind.GUIDE, 0.0, _ANYWHERE_ON_LOCK, (), (), lock.label))
        out.sort(key=_order)
        return out

    # --------------------------------------------------------------------------------------------- the pick

    def pick(self, q: SnapQuery, previous: Candidate | None = None) -> SnapResult:
        """Choose. The candidate chosen last time gets a small head start, so it doesn't flicker as the mouse shakes."""
        offers = self.candidates(q)
        chosen: Candidate | None = None
        if offers:
            chosen = offers[0]
            if previous is not None:
                for c in offers:
                    if c.same_as(previous):
                        if c is not chosen and c.score - STICK <= chosen.score:
                            chosen = c
                        break
                else:
                    held = self._still_within_stick(q, previous)
                    if held is not None and held.score - STICK <= chosen.score:
                        chosen = held
        elif previous is not None:
            chosen = self._still_within_stick(q, previous)
        if chosen is not None:
            return SnapResult(chosen.point, chosen, tuple(offers), chosen.guides)
        return SnapResult(self.free_point(q), None, (), ())

    def _still_within_stick(self, q: SnapQuery, previous: Candidate) -> Candidate | None:
        """A real point stays chosen a little beyond where it would first be offered, which is what makes it feel magnetic."""
        if previous.kind not in REAL or previous.guides:
            return None
        d = math.hypot(previous.point[0] - q.cursor[0], previous.point[1] - q.cursor[1]) / q.px
        if d > q.aperture * STICK_REACH or previous.kind not in q.kinds:
            return None
        for eid in previous.sources:
            if eid not in self.doc.entities or eid in q.exclude:
                return None
        return Candidate(previous.point, previous.kind, d, d + HANDICAP[previous.kind], previous.sources, (), previous.detail)

    def _curves_at(self, p: Vec, sources: tuple[int, ...]) -> list:
        """The curves passing through a point, from the things it came from."""
        e = tol(p[0], p[1]) * 4.0
        window = (p[0] - e, p[1] - e, p[0] + e, p[1] + e)
        curves: list[_Near] = []
        for key in self.doc.index.query(window):
            eid, sub = split_key(key)
            if sources and eid not in sources:
                continue
            entity = self.doc.entities.get(eid)
            if entity is not None:
                self._entry(entity, sub, eid, (eid, sub), IDENTITY, window, curves, [], 0)
        return [n.curve for n in curves]

    def directions_at(self, p: Vec, sources: tuple[int, ...]) -> tuple[Vec, ...]:
        """Which ways the things ending at a point are heading, for extension lines. An arc's is its tangent there."""
        out: list[Vec] = []
        for c in self._curves_at(p, sources):
            if type(c) in (Line, Arc) and not getattr(c, "closed", False):
                if near(c.start, p):
                    d = c.tangent_at(0.0).unit()
                elif near(c.end, p):
                    d = c.tangent_at(1.0).unit()
                else:
                    continue
                if d != (0.0, 0.0) and not any(abs(d.cross(o)) < 1e-9 for o in out):
                    out.append(d)
        return tuple(out)

    def direction_on(self, p: Vec, sources: tuple[int, ...]) -> Vec | None:
        """Which way something runs at a point on it."""
        for c in self._curves_at(p, sources):
            t, q = c.project(p)
            if near(q, p, tol(p[0], p[1]) * 8.0):
                d = c.tangent_at(t).unit()
                if d != (0.0, 0.0):
                    return d
        return None

    def free_point(self, q: SnapQuery) -> Vec:
        """Where the point goes if nothing's offered. Grid if it's on, otherwise tidied to what the screen can show."""
        x, y = q.cursor
        if q.grid > 0.0:
            (ox, oy), g = q.grid_origin, q.grid
            return Vec(ox + round((x - ox) / g) * g, oy + round((y - oy) / g) * g)
        return Vec(tidy(x, q.px), tidy(y, q.px))


def _adjacent(a: tuple, b: tuple) -> bool:
    """Are these two segments of one polyline that share a vertex?"""
    return isinstance(a[-1], int) and isinstance(b[-1], int) and abs(a[-1] - b[-1]) <= 1


def _exact_direction(a: float) -> Vec:
    """Horizontal and vertical get exact components, so a horizontal line is exactly horizontal."""
    q = a / (math.pi / 2.0)
    if abs(q - round(q)) < 1e-12:
        return ((Vec(1.0, 0.0), Vec(0.0, 1.0), Vec(-1.0, 0.0), Vec(0.0, -1.0))[round(q) % 4])
    return Vec(math.cos(a), math.sin(a))


def _angle_label(a: float) -> str:
    deg = math.degrees(a)
    if abs(deg) < 1e-9:
        return "Horizontal"
    if abs(deg - 90.0) < 1e-9:
        return "Vertical"
    return f"{deg:g}°"


def _circle_tangents(c: Vec, r: float, p: Vec) -> list[Vec]:
    d = Vec(p[0] - c[0], p[1] - c[1])
    dist = d.length()
    if dist <= r + tol(p[0], p[1], c[0], c[1]):
        return []
    a = math.atan2(d[1], d[0])
    off = math.acos(r / dist)
    return [polar(c, r, a + off), polar(c, r, a - off)]


def _solve_near(c, t0: float, f) -> Vec | None:
    """Where f(point, tangent) = 0 on a curve near t0, for the cases with no closed form. A secant search from a bracket."""
    h = 0.02
    lo, hi = max(0.0, t0 - h), min(1.0, t0 + h)
    fl, fh = f(c.point_at(lo), c.tangent_at(lo)), f(c.point_at(hi), c.tangent_at(hi))
    if fl == 0.0:
        return c.point_at(lo)
    if fh == 0.0:
        return c.point_at(hi)
    if fl * fh > 0.0:
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        fm = f(c.point_at(mid), c.tangent_at(mid))
        if fm == 0.0 or hi - lo < 1e-14:
            lo = hi = mid
            break
        if fl * fm < 0.0:
            hi, fh = mid, fm
        else:
            lo, fl = mid, fm
    return c.point_at(0.5 * (lo + hi))


def _merge(found: Iterable[tuple[Vec, Kind, tuple[int, ...]]]) -> list[tuple[Vec, Kind, tuple[int, ...]]]:
    """The same point found several ways is one candidate: the best kind's, with everything it came from. An endpoint's
    stored number beats the same point computed as an intersection."""
    ordered = sorted(found, key=lambda f: f[1])
    out: list[list] = []
    cells: dict[tuple[int, int], list[list]] = {}
    if not ordered:
        return []
    biggest = max(max(abs(f[0][0]), abs(f[0][1])) for f in ordered)
    size = tol(biggest) * 2.0
    for p, kind, owners in ordered:
        ix, iy = math.floor(p[0] / size), math.floor(p[1] / size)
        for i in (ix - 1, ix, ix + 1):
            for j in (iy - 1, iy, iy + 1):
                for entry in cells.get((i, j), ()):
                    if near(p, entry[0]):
                        if owners and not set(owners) <= set(entry[2]):
                            entry[2] = tuple(sorted(set(entry[2]) | set(owners)))
                        break
                else:
                    continue
                break
            else:
                continue
            break
        else:
            entry = [p, kind, owners]
            out.append(entry)
            cells.setdefault((ix, iy), []).append(entry)
    return [(e[0], e[1], e[2]) for e in out]


def _rank(found, cursor: Vec, px: float) -> list[Candidate]:
    out = []
    for p, kind, owners in found:
        d = math.hypot(p[0] - cursor[0], p[1] - cursor[1]) / px
        out.append(Candidate(p, kind, d, d + HANDICAP[kind], owners))
    out.sort(key=_order)
    return out


def _order(c: Candidate):
    return (c.score, c.kind, c.point[0], c.point[1], c.sources)


class Tracker:
    """What hovering has picked up. There's no tracking mode to switch on: pause over a point and alignment lines come from it.

    Time is passed in, not read from a clock, so behaviour is exactly repeatable. Pausing on a point picks it up; pausing on
    it again later drops it. Pausing on an edge, when there's a point to measure from, offers lines parallel and perpendicular to it.
    """
    DWELL = 0.35        # seconds
    MOST = 7

    def __init__(self) -> None:
        self.points: list[Tracked] = []
        self.directions: list[tuple[Vec, str]] = []
        self._hover: Candidate | None = None
        self._since = 0.0
        self._acted = False

    def clear(self) -> None:
        self.points.clear()
        self.directions.clear()
        self._hover = None
        self._acted = False

    def hover(self, candidate: Candidate | None, now: float, engine: "SnapEngine", has_last: bool) -> bool:
        """The cursor is over this candidate at this moment. Returns True if what's tracked changed."""
        usable = candidate is not None and (candidate.kind in ACQUIRABLE or (candidate.kind == Kind.ON and has_last))
        if not usable:
            self._hover = None
            self._acted = False
            return False
        if self._hover is None or not candidate.same_as(self._hover):
            self._hover = candidate
            self._since = now
            self._acted = False
            return False
        if self._acted or now - self._since < self.DWELL:
            return False
        self._acted = True
        if candidate.kind == Kind.ON:
            return self._toggle_direction(candidate, engine)
        return self._toggle_point(candidate, engine)

    def _toggle_point(self, c: Candidate, engine: "SnapEngine") -> bool:
        for i, t in enumerate(self.points):
            if near(t.point, c.point):
                del self.points[i]
                return True
        self.points.append(Tracked(c.point, c.label, engine.directions_at(c.point, c.sources)))
        del self.points[:-self.MOST]
        return True

    def _toggle_direction(self, c: Candidate, engine: "SnapEngine") -> bool:
        d = engine.direction_on(c.point, c.sources)
        if d is None:
            return False
        for i, (have, _) in enumerate(self.directions):
            if abs(have.cross(d)) < 1e-9:
                del self.directions[i]
                return True
        self.directions.append((d, "that edge"))
        del self.directions[:-self.MOST]
        return True
