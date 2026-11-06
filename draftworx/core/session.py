# SPDX-License-Identifier: GPL-3.0-or-later
"""A drawing being worked on: what's selected, what's running, what the pointer and keyboard are doing.

No window here. The window forwards pointer moves, clicks and keys, and paints what this says. So everything a person can do
can also be done by a test, with no Qt, by calling the same methods.

A command is a generator that yields what it needs (`GetPoint`, `GetDistance`...) and is sent the answer:

    def line(s):
        a = yield GetPoint("Where does the line start?")
        b = yield GetPoint("Where does it end?", base=a)
        with s.doc.transaction("Line") as t:
            t.add(Line(a, b))
"""
from __future__ import annotations

import math
import traceback
from dataclasses import dataclass
from typing import Callable, Iterable

from .grips import Grip, grip_at, grips_of
from .model import Document, EditError
from .select import in_window, pick
from .snap import ALL_KINDS, Candidate, Kind, Lock, SnapEngine, SnapQuery, SnapResult, Tracked, Tracker
from .units import TypedError, format_angle, format_length, parse_angle, parse_length, parse_number, parse_point
from .vec import Vec


class _Sentinel:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return self.name


ENTER = _Sentinel("ENTER")      # Enter with nothing typed: "that's all"
UNDO = _Sentinel("UNDO")        # Ctrl+Z in a command that can step back


@dataclass(frozen=True, slots=True)
class Keyword:
    name: str


@dataclass(frozen=True, slots=True)
class Pick:
    point: Vec
    px: float
    shift: bool = False


@dataclass
class Request:
    prompt: str = ""
    keywords: tuple[str, ...] = ()
    undoable: bool = False


@dataclass
class GetPoint(Request):
    base: Vec | None = None
    preview: Callable | None = None
    tracked: tuple[Tracked, ...] = ()
    extra: tuple = ()
    exclude: frozenset = frozenset()
    enter: bool = False                 # Enter means "finished"
    on_release: bool = False            # a drag: letting go is the answer
    ctrl_keeps_snap: bool = False


@dataclass
class GetDistance(GetPoint):
    default: float | None = None
    plain: bool = False                 # a bare number like a scale factor, not a length
    typed_factor: float = 1.0           # what a typed value is multiplied by: 0.5 when a radius is asked for as a diameter
    measure: Callable | None = None     # what a pointed answer means, if not the distance from `base`


@dataclass
class GetAngle(GetPoint):
    default: float | None = None


@dataclass
class GetNumber(Request):
    default: float | None = None


@dataclass
class GetText(Request):
    default: str = ""
    preview: Callable | None = None


@dataclass
class GetPick(Request):
    """Point at something. `preview(point, px)` says what would happen, and returns ghosts to show."""
    preview: Callable | None = None
    enter: bool = False


@dataclass
class GetSelection(Request):
    """Select objects, in the ordinary way, until Enter."""


@dataclass(frozen=True, slots=True)
class Field:
    """A number shown beside the cursor while drawing."""
    name: str
    text: str
    typing: bool = False
    locked: bool = False


@dataclass(slots=True)
class _Down:
    kind: str                       # grip, body, window
    start: Vec
    eid: int = 0
    grip: Grip | None = None
    shift: bool = False
    ctrl: bool = False
    was_selected: bool = False
    base: Vec | None = None
    moved: bool = False


@dataclass(slots=True)
class Drafting:
    """The switches. They belong to the person, not the drawing."""
    snap: bool = True
    tracking: bool = True
    ortho: bool = False
    grid: bool = False
    grid_step: float = 10.0
    polar_step: float = math.pi / 4.0
    kinds: frozenset = ALL_KINDS


OVERRIDES = {"end": Kind.ENDPOINT, "mid": Kind.MIDPOINT, "int": Kind.INTERSECTION, "cen": Kind.CENTER, "qua": Kind.QUADRANT,
             "per": Kind.PERPENDICULAR, "tan": Kind.TANGENT, "nea": Kind.ON, "nod": Kind.NODE, "ins": Kind.INSERTION,
             "gce": Kind.GEOMETRIC_CENTER}
DRAG_START = 5.0        # pixels the pointer moves before a press counts as a drag
MOST_GRIPS = 400        # with more than this selected, grips would just be noise


class Session:
    def __init__(self, doc: Document | None = None):
        self.drafting = Drafting()
        self.selection: set[int] = set()
        self.selection_version = 0
        self.doc_version = 0
        self.last_command = ""
        self.message = ""
        self.last_error: BaseException | None = None
        self._gen = None
        self._command_name = ""
        self.request: Request | None = None
        self.input = ""
        self.cursor = Vec(0.0, 0.0)
        self.px = 1.0
        self.now = 0.0
        self.shift = False
        self.ctrl = False
        self.snap: SnapResult | None = None
        self.ghosts: list = []
        self.fields: list[Field] = []
        self.hover: int | None = None
        self.highlights: tuple[int, ...] = ()
        self.window: tuple[Vec, Vec, bool] | None = None
        self.grips: list[tuple[int, Grip]] = []
        self.hover_grip: Grip | None = None
        self.tracker = Tracker()
        self.lock_direction: Vec | None = None
        self.lock_radius: float | None = None
        self._one_shot: frozenset | None = None
        self._chosen: Candidate | None = None
        self._cycle: tuple[Candidate, Vec] | None = None
        self._pick_cycle = 0
        self._down: _Down | None = None
        self._stop_listening = None
        self.doc = doc if doc is not None else Document()
        self._attach(self.doc)

    # ------------------------------------------------------------------------------------------- the drawing

    def _attach(self, doc: Document) -> None:
        self.doc = doc
        self.engine = SnapEngine(doc)
        self._stop_listening = doc.on_change(self._changed)

    def replace_document(self, doc: Document) -> None:
        """Work on a different drawing."""
        self.cancel()
        if self._stop_listening is not None:
            self._stop_listening()
        self.selection.clear()
        self.selection_version += 1
        self.grips = []
        self.hover = None
        self._attach(doc)
        self.doc_version += 1

    def _changed(self, changes) -> None:
        self.doc_version += 1
        gone = {i for i in self.selection if i not in self.doc.entities}
        if gone:
            self.selection -= gone
            self.selection_version += 1
        self._refresh_grips()

    # -------------------------------------------------------------------------------------------- selection

    def set_selection(self, ids: Iterable[int]) -> None:
        new = {i for i in ids if i in self.doc.entities}
        if new != self.selection:
            self.selection = new
            self.selection_version += 1
            self._refresh_grips()

    def _refresh_grips(self) -> None:
        out: list[tuple[int, Grip]] = []
        if self.request is None and len(self.selection) <= MOST_GRIPS:
            for eid in sorted(self.selection):
                e = self.doc.entities.get(eid)
                if e is not None:
                    out.extend((eid, g) for g in grips_of(e.shape))
        self.grips = out

    # --------------------------------------------------------------------------------------------- commands

    @property
    def active(self) -> bool:
        return self._gen is not None

    @property
    def command_name(self) -> str:
        return self._command_name

    def run(self, name: str) -> bool:
        """Start a command by name or alias. Returns False if there's no such command."""
        from .commands import find
        info = find(name)
        if info is None:
            self.message = f"There's no command called {name}."
            return False
        self.run_generator(info.function(self), info.name)
        self.last_command = info.name
        return True

    def run_generator(self, gen, name: str = "") -> None:
        self.cancel()
        self.message = ""
        self._gen = gen
        self._command_name = name
        self.grips = []
        self._down = None
        self.hover = None
        self._send(None, True)

    def cancel(self) -> None:
        """Stop what's running. What it already finished stays; what it hadn't finished was never added."""
        if self._gen is not None:
            gen, self._gen = self._gen, None
            try:
                gen.close()
            except Exception as exc:     # a command's own cleanup must not take the program down
                self._record_error(exc)
        self._end_request()

    def _end_request(self) -> None:
        self.request = None
        self._command_name = ""
        self.input = ""
        self.ghosts = []
        self.fields = []
        self.snap = None
        self.highlights = ()
        self._chosen = None
        self._cycle = None
        self._one_shot = None
        self.lock_direction = None
        self.lock_radius = None
        self.window = None
        self._down = None
        self.tracker.clear()
        self._refresh_grips()

    def _record_error(self, exc: BaseException) -> None:
        self.last_error = exc
        traceback.print_exception(exc)
        self.message = f"That didn't work ({exc}). The drawing is as it was before that step."

    def _send(self, value, first: bool = False) -> None:
        gen = self._gen
        if gen is None:
            return
        try:
            req = next(gen) if first else gen.send(value)
        except StopIteration:
            self._gen = None
            self._end_request()
            self._update()
            return
        except EditError as exc:
            self._gen = None
            self._end_request()
            self.message = str(exc)
            return
        except Exception as exc:
            self._gen = None
            self._end_request()
            self._record_error(exc)
            return
        self.request = req
        self.input = ""
        self._chosen = None
        self._cycle = None
        self._one_shot = None
        self.lock_direction = None
        self.lock_radius = None
        self.tracker.clear()
        self._update()

    def _answer(self, value) -> None:
        self.message = ""
        self._send(value)

    # ------------------------------------------------------------------------------------------ the pointer

    def move(self, x: float, y: float, px: float, now: float = 0.0, shift: bool = False, ctrl: bool = False) -> None:
        self.cursor = Vec(float(x), float(y))
        self.px = float(px)
        self.now = now
        self.shift = shift
        self.ctrl = ctrl
        if self._down is not None:
            self._dragging()
        self._update()

    def tick(self, now: float) -> bool:
        """Time passes with the pointer still. Returns True if something picked up by pausing changed."""
        self.now = now
        req = self.request
        if not isinstance(req, GetPoint) or self.snap is None or self.input:
            return False
        c = self.snap.candidate
        if self._can_track(c) and self.tracker.hover(c, now, self.engine, req.base is not None):
            self._update(track=False)
            return True
        return False

    def press(self, x: float, y: float, px: float, now: float = 0.0, shift: bool = False, ctrl: bool = False) -> None:
        self.move(x, y, px, now, shift, ctrl)
        req = self.request
        if isinstance(req, GetPoint):
            if not req.on_release:
                self._point_chosen()
        elif isinstance(req, GetPick):
            self._answer(Pick(self.cursor, self.px, shift))
        elif isinstance(req, (GetText, GetNumber)):
            pass
        else:
            self._press_to_select(shift, ctrl)

    def release(self, x: float, y: float, px: float, now: float = 0.0, shift: bool = False, ctrl: bool = False) -> None:
        self.move(x, y, px, now, shift, ctrl)
        req = self.request
        if isinstance(req, GetPoint) and req.on_release:
            self._point_chosen()
            return
        down, self._down = self._down, None
        if down is None:
            return
        if down.kind == "window":
            self._finish_window(down)
        elif down.kind == "grip" and not down.moved:
            self._start_grip(down, False)
        elif down.kind == "body" and not down.moved and not down.shift and self.request is None:
            if down.was_selected and len(self.selection) > 1:
                self.set_selection({down.eid})
        self._update()

    def right_click(self) -> bool:
        """In a command it's Enter. Returns False if it's idle, when the window should show a menu instead."""
        if self.request is None:
            return False
        self.key("enter")
        return True

    def _press_to_select(self, shift: bool, ctrl: bool) -> None:
        idle = self.request is None
        if idle and self.grips and not shift:
            g = grip_at([g for _, g in self.grips], self.cursor, self.px)
            if g is not None:
                eid = next(i for i, h in self.grips if h is g)
                self._down = _Down("grip", self.cursor, eid, g, shift, ctrl)
                return
        hits = pick(self.doc, self.cursor, self.px)
        if hits:
            eid = hits[self._pick_cycle % len(hits)]
            self._pick_cycle = 0
            was = eid in self.selection
            if shift:
                self.set_selection(self.selection ^ {eid})
                self._down = None
                return
            if not was:
                self.set_selection({eid} if idle else self.selection | {eid})
            self._down = _Down("body", self.cursor, eid, None, shift, ctrl, was, self._grab_point(eid))
        else:
            self._down = _Down("window", self.cursor, 0, None, shift, ctrl)

    def _grab_point(self, eid: int) -> Vec:
        """Where a drag takes hold: the snap point at the press, so dragging by a corner moves exactly from that corner."""
        q = SnapQuery(self.cursor, self.px, kinds=self.drafting.kinds - {Kind.ON, Kind.GUIDE}, polar_step=0.0)
        r = self.engine.pick(q)
        return r.point if r.candidate is not None else self.cursor

    def _dragging(self) -> None:
        down = self._down
        far = math.hypot(self.cursor[0] - down.start[0], self.cursor[1] - down.start[1]) / self.px >= DRAG_START
        if down.kind == "window":
            if far or down.moved:
                down.moved = True
                self.window = (down.start, self.cursor, self.cursor[0] < down.start[0])
        elif far and not down.moved:
            down.moved = True
            if down.kind == "grip":
                self._start_grip(down, True)
            elif down.kind == "body" and self.request is None:
                self._start_drag(down)

    def _finish_window(self, down: _Down) -> None:
        self.window = None
        if not down.moved:
            if self.request is None and not down.shift:
                self.set_selection(())
            return
        (ax, ay), (bx, by) = down.start, self.cursor
        box = (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
        ids = in_window(self.doc, box, bx < ax, self.px)
        if down.shift:
            self.set_selection(self.selection - set(ids))
        elif self.request is None:
            self.set_selection(ids)
        else:
            self.set_selection(self.selection | set(ids))

    def _start_grip(self, down: _Down, dragging: bool) -> None:
        from .commands.modify import grip_edit
        self._down = None
        self.run_generator(grip_edit(self, down.eid, down.grip, dragging), "Grip")

    def _start_drag(self, down: _Down) -> None:
        from .commands.modify import drag_move
        self._down = None
        ids = sorted(self.selection)
        self.run_generator(drag_move(self, ids, down.base if down.base is not None else down.start, down.ctrl), "Drag")

    # ------------------------------------------------------------------------------------------- what to show

    def _can_track(self, c: Candidate | None) -> bool:
        return (self.drafting.tracking and self.drafting.snap and c is not None and not c.guides
                and self.lock_direction is None and self.lock_radius is None and not self.input)

    def _lock_for(self, req: GetPoint) -> Lock | None:
        base = req.base
        if base is None:
            return None
        direction = self.lock_direction
        label = "Angle held" if direction is not None else ""
        if direction is None and self.drafting.ortho != self.shift:
            d = self.cursor - base
            direction = Vec(1.0, 0.0) if abs(d[0]) >= abs(d[1]) else Vec(0.0, 1.0)
            label = "Horizontal" if direction[1] == 0.0 else "Vertical"
        if direction is None and self.lock_radius is None:
            return None
        if direction is not None and (self.cursor - base).dot(direction) < 0.0:
            direction = -direction
        return Lock(base, direction, self.lock_radius, label or "Length held")

    def _query(self, req: GetPoint) -> SnapQuery:
        d = self.drafting
        suspended = self.ctrl and not req.ctrl_keeps_snap
        tracked = tuple(self.tracker.points) + req.tracked if d.tracking else ()
        return SnapQuery(self.cursor, self.px, req.base, tracked, self._lock_for(req),
                         self._one_shot if self._one_shot is not None else d.kinds,
                         d.polar_step if d.tracking and self._one_shot is None else 0.0,
                         tuple(self.tracker.directions) if d.tracking else (), req.exclude, req.extra,
                         d.grid_step if d.grid else 0.0, enabled=d.snap and not suspended)

    def _update(self, track: bool = True) -> None:
        req = self.request
        if isinstance(req, GetPoint):
            self.hover = None
            self.hover_grip = None
            self._update_point(req, track)
        elif isinstance(req, GetPick):
            self.hover = None
            self.snap = None
            out = req.preview(self.cursor, self.px) if req.preview is not None else None
            self.ghosts = list(out) if out else []
        elif isinstance(req, GetText):
            self.ghosts = list(req.preview(self.input)) if req.preview is not None else []
            self.fields = [Field("text", self.input, True)]
        elif isinstance(req, GetNumber):
            self.fields = [Field("value", self.input, True)]
        else:
            self.snap = None
            self.ghosts = []
            self.fields = []
            if self._down is None or self._down.kind != "window":
                self._update_hover()

    def _update_hover(self) -> None:
        self.hover_grip = None
        if self.request is None and self.grips:
            self.hover_grip = grip_at([g for _, g in self.grips], self.cursor, self.px)
        hits = pick(self.doc, self.cursor, self.px)
        self.hover = hits[self._pick_cycle % len(hits)] if hits else None

    def _update_point(self, req: GetPoint, track: bool) -> None:
        q = self._query(req)
        result = self.engine.pick(q, self._chosen)
        if self._cycle is not None:
            pinned, where = self._cycle
            if math.hypot(where[0] - self.cursor[0], where[1] - self.cursor[1]) / self.px > 8.0:
                self._cycle = None
            else:
                for c in result.alternatives:
                    if c.same_as(pinned):
                        result = SnapResult(c.point, c, result.alternatives, c.guides)
                        break
        self.snap = result
        self._chosen = result.candidate
        c = result.candidate
        if track and self._can_track(c) and self.tracker.hover(c, self.now, self.engine, req.base is not None):
            result = self.engine.pick(self._query(req), self._chosen)
            self.snap = result
        self.highlights = result.candidate.sources if result.candidate is not None else ()
        try:
            at = self._typed_preview(req) or result.point
            self.ghosts = list(req.preview(at)) if req.preview is not None else []
        except (ValueError, ZeroDivisionError):
            self.ghosts = []
        self.fields = self._fields_for(req, result.point)

    def _typed_preview(self, req: GetPoint) -> Vec | None:
        """While a number is being typed, show what it would make, not where the cursor happens to be."""
        text = self.input.strip()
        if not text or req.preview is None or req.base is None or self.snap is None or isinstance(req, GetAngle):
            return None
        try:
            if isinstance(req, GetDistance):
                d = self.snap.point - req.base
                if d.length2() == 0.0 or req.plain or req.measure is not None:
                    return None
                return req.base + d.unit() * (parse_length(text, self.doc.settings.units) * req.typed_factor)
            typed = parse_point(text, req.base, self.doc.settings.units)
        except TypedError:
            return None
        if typed.point is not None:
            return typed.point
        if typed.length is not None:
            return self._point_at_length(req, typed.length, quiet=True)
        return None

    def _fields_for(self, req: GetPoint, p: Vec) -> list[Field]:
        s = self.doc.settings
        out: list[Field] = []
        typing = self.input
        if isinstance(req, GetAngle):
            if req.base is not None:
                out.append(Field("angle", typing or format_angle((p - req.base).angle(), s.angle_decimals), bool(typing)))
            elif typing:
                out.append(Field("angle", typing, True))
            return out
        if req.base is None:
            if typing:
                out.append(Field("point", typing, True))
            else:
                out.append(Field("x", format_length(p[0], s.linear_format, s.linear_decimals, s.units)))
                out.append(Field("y", format_length(p[1], s.linear_format, s.linear_decimals, s.units)))
            return out
        d = p - req.base
        length = d.length()
        shown = format_length(length, s.linear_format, s.linear_decimals, s.units)
        if self.lock_radius is not None:
            shown = format_length(self.lock_radius, s.linear_format, s.linear_decimals, s.units)
        out.append(Field("length", typing if typing and not typing.startswith("<") else shown, bool(typing) and not typing.startswith("<"),
                         self.lock_radius is not None))
        if not isinstance(req, GetDistance):
            angle = format_angle(d.angle() if length > 0.0 else 0.0, s.angle_decimals)
            if self.lock_direction is not None:
                angle = format_angle(self.lock_direction.angle(), s.angle_decimals)
            out.append(Field("angle", typing if typing.startswith("<") else angle, typing.startswith("<"), self.lock_direction is not None))
        return out

    # --------------------------------------------------------------------------------------------- answering

    def _point_chosen(self) -> None:
        req = self.request
        p = self.snap.point if self.snap is not None else self.cursor
        if isinstance(req, GetAngle):
            if req.base is None:
                self.message = "Type an angle."
                return
            self._answer((p - req.base).angle())
        elif isinstance(req, GetDistance):
            if req.base is None:
                self.message = "Type a value."
                return
            self._answer(req.measure(p) if req.measure is not None else req.base.distance(p))
        else:
            self._answer(p)

    def _match_keyword(self, text: str) -> str | None:
        req = self.request
        t = text.casefold()
        if not req or not req.keywords or not t:
            return None
        for k in req.keywords:
            if k.casefold() == t:
                return k
        for k in req.keywords:
            if k.casefold().startswith(t):
                return k
        return None

    def _submit(self) -> None:
        """Enter or Space."""
        req = self.request
        text = self.input.strip()
        if req is None:
            self._submit_command(text)
            return
        if isinstance(req, GetText):
            self._answer(self.input if self.input else (req.default or ENTER))
            return
        if isinstance(req, GetSelection):
            if text:
                k = self._match_keyword(text)
                if k is not None:
                    self._answer(Keyword(k))
                else:
                    self.message = f"{text} isn't one of the options here."
                return
            self._answer(sorted(self.selection))
            return
        if not text:
            self._submit_empty(req)
            return
        k = self._match_keyword(text)
        if k is not None:
            self._answer(Keyword(k))
            return
        if isinstance(req, GetPoint):
            self._submit_typed_point(req, text)
        elif isinstance(req, GetNumber):
            try:
                self._answer(parse_number(text))
            except TypedError as exc:
                self.message = str(exc).capitalize() + "."
        else:
            self.message = f"{text} isn't one of the options here."

    def _submit_empty(self, req: Request) -> None:
        if isinstance(req, GetPoint):
            if self.lock_radius is not None or self.lock_direction is not None:
                self._point_chosen()
            elif isinstance(req, (GetDistance, GetAngle)) and req.default is not None:
                self._answer(req.default)
            elif req.enter:
                self._answer(ENTER)
            else:
                self.message = "Pick a point, or press Esc to stop."
        elif isinstance(req, GetNumber):
            if req.default is not None:
                self._answer(req.default)
            else:
                self.message = "Type a number, or press Esc to stop."
        elif isinstance(req, GetPick):
            if req.enter:
                self._answer(ENTER)
            else:
                self.message = "Point at something, or press Esc to stop."

    def _submit_typed_point(self, req: GetPoint, text: str) -> None:
        units = self.doc.settings.units
        low = text.casefold()
        if low in ("non", "none"):
            self._one_shot = frozenset()
            self.input = ""
            self._update()
            return
        if low in OVERRIDES:
            self._one_shot = frozenset({OVERRIDES[low]})
            self.message = f"Next point: {low} only."
            self.input = ""
            self._update()
            return
        try:
            if isinstance(req, GetAngle):
                self._answer(parse_angle(text))
                return
            if isinstance(req, GetDistance):
                self._answer((parse_number(text) if req.plain else parse_length(text, units)) * req.typed_factor)
                return
            typed = parse_point(text, req.base, units)
        except TypedError as exc:
            self.message = str(exc).capitalize() + "."
            return
        if typed.point is not None:
            self._answer(typed.point)
        elif typed.angle is not None:
            self.lock_direction = Vec(math.cos(typed.angle), math.sin(typed.angle))
            self.input = ""
            self._update()
        else:
            p = self._point_at_length(req, typed.length)
            if p is not None:
                self._answer(p)

    def _point_at_length(self, req: GetPoint, length: float, quiet: bool = False) -> Vec | None:
        """A bare number at a point prompt: that far along an alignment line if the cursor's on one, otherwise from the last point."""
        result = self.snap
        c = result.candidate if result is not None else None
        if c is not None and len(c.guides) == 1 and c.kind == Kind.GUIDE:
            g = c.guides[0]
            t, _ = g.foot(self.cursor)
            k = length if t >= 0.0 else -length
            return Vec(g.origin[0] + g.direction[0] * k, g.origin[1] + g.direction[1] * k)
        if req.base is None:
            if not quiet:
                self.message = "There's no point to measure from yet. Type x,y."
            return None
        p = result.point if result is not None else self.cursor
        d = p - req.base
        if d.length2() == 0.0:
            if not quiet:
                self.message = "Point the cursor the way you want to go, then type the length."
            return None
        u = d.unit()
        return Vec(req.base[0] + u[0] * length, req.base[1] + u[1] * length)

    def _submit_command(self, text: str) -> None:
        if not text:
            if self.last_command:
                self.run(self.last_command)
            return
        from .commands import find, search
        self.input = ""
        info = find(text)
        if info is None:
            found = search(text)
            info = found[0] if found else None
        if info is None:
            self.message = f"There's no command called {text}."
            return
        self.run(info.name)

    # -------------------------------------------------------------------------------------------- the keyboard

    def type_text(self, text: str) -> None:
        """Printable characters. There's no field to click: typing always goes where it makes sense."""
        self.message = ""
        self.input += text
        self._update()

    def key(self, name: str, shift: bool = False, ctrl: bool = False) -> None:
        if name == "enter":
            self._submit()
        elif name == "space":
            if isinstance(self.request, GetText):
                self.type_text(" ")
            else:
                self._submit()
        elif name == "backspace":
            self.input = self.input[:-1]
            self._update()
        elif name == "escape":
            self._escape()
        elif name == "tab":
            self._tab(shift)
        elif name == "delete":
            self._delete()
        elif name == "f3":
            self.drafting.snap = not self.drafting.snap
            self.message = f"Snapping {'on' if self.drafting.snap else 'off'}."
            self._update()
        elif name == "f8":
            self.drafting.ortho = not self.drafting.ortho
            self.message = f"Ortho {'on' if self.drafting.ortho else 'off'}."
            self._update()
        elif name == "f9":
            self.drafting.grid = not self.drafting.grid
            self.message = f"Grid snap {'on' if self.drafting.grid else 'off'}."
            self._update()
        elif name == "f10":
            self.drafting.tracking = not self.drafting.tracking
            self.message = f"Alignment lines {'on' if self.drafting.tracking else 'off'}."
            self._update()

    def _escape(self) -> None:
        if self.input:
            self.input = ""
            self.message = ""
        elif self.lock_radius is not None or self.lock_direction is not None:
            self.lock_radius = None
            self.lock_direction = None
        elif self._gen is not None:
            self.cancel()
        else:
            self.set_selection(())
            self.window = None
            self._down = None
            self.tracker.clear()
            self.message = ""
        self._update()

    def _tab(self, backwards: bool) -> None:
        req = self.request
        if isinstance(req, GetPoint) and self.input.strip():
            self._lock_typed(req)
            return
        if isinstance(req, GetPoint):
            offers = self.snap.alternatives if self.snap is not None else ()
            if len(offers) < 2:
                return
            now_at = next((i for i, c in enumerate(offers) if self.snap.candidate is not None and c.same_as(self.snap.candidate)), 0)
            step = -1 if backwards else 1
            c = offers[(now_at + step) % len(offers)]
            self._cycle = (c, self.cursor)
            self._chosen = c
            self._update(track=False)
        elif req is None or isinstance(req, GetSelection):
            self._pick_cycle += 1
            self._update_hover()

    def _lock_typed(self, req: GetPoint) -> None:
        """Tab with a number typed holds it, and the point keeps following the cursor in the other direction."""
        text = self.input.strip()
        try:
            if isinstance(req, (GetAngle, GetDistance)):
                self._submit()
                return
            if req.base is None:
                self.message = "There's no point to measure from yet. Type x,y."
                return
            typed = parse_point(text, req.base, self.doc.settings.units)
        except TypedError as exc:
            self.message = str(exc).capitalize() + "."
            return
        if typed.length is not None:
            if typed.length <= 0.0:
                self.message = "A held length has to be more than zero."
                return
            self.lock_radius = typed.length
        elif typed.angle is not None:
            self.lock_direction = Vec(math.cos(typed.angle), math.sin(typed.angle))
        else:
            self._answer(typed.point)
            return
        self.input = ""
        self._update()

    def _delete(self) -> None:
        if self.request is None and self.selection:
            ids = sorted(self.selection)
            with self.doc.transaction("Erase") as t:
                for i in ids:
                    t.remove(i)
            self.set_selection(())

    def undo(self) -> None:
        """Ctrl+Z. A command that can step back does, and otherwise it stops and the last thing is undone."""
        req = self.request
        if req is not None and req.undoable:
            self._answer(UNDO)
            return
        if self._gen is not None:
            self.cancel()
            return
        label = self.doc.undo()
        self.message = f"Undid: {label}." if label else "Nothing to undo."
        self._update()

    def redo(self) -> None:
        if self._gen is not None:
            self.cancel()
        label = self.doc.redo()
        self.message = f"Redid: {label}." if label else "Nothing to redo."
        self._update()

    # --------------------------------------------------------------------------------------------- what to say

    @property
    def prompt(self) -> str:
        req = self.request
        if req is not None:
            return req.prompt
        if self.selection:
            n = len(self.selection)
            return f"{n} selected. Drag to move, press Delete to erase, or type a command."
        return "Select something, or type a command."

    @property
    def options(self) -> tuple[str, ...]:
        return self.request.keywords if self.request is not None else ()

    @property
    def tracked(self) -> list[Tracked]:
        return list(self.tracker.points) + (list(self.request.tracked) if isinstance(self.request, GetPoint) else [])
