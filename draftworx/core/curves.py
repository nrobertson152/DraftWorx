# SPDX-License-Identifier: GPL-3.0-or-later
"""The curves everything else is built from: Line, Arc, Circle, EllipseArc, Spline and Polyline.

Each is an immutable value. A curve runs from t = 0 to t = 1, and every curve answers the same questions in the same
way: `point_at`, `tangent_at`, `project`, `length`, `bbox`, `transformed`, `sub`, `reversed`, `emit`.

Arcs and elliptical arcs have a signed sweep, so a polyline's clockwise bulge is an ordinary arc that runs the way the
polyline runs. `transformed(m).point_at(t) == m.apply(point_at(t))` holds, which is what lets a block's contents be
snapped to exactly, however the block was scaled.

A circle has no start, so its parameter is the one exception: it stays a circle under a turn, with t measured from +X.

`emit` writes a curve to anything with `moveTo`, `lineTo`, `cubicTo` and `closeSubpath`. A QPainterPath has those, so
the core can feed the window without importing Qt. Arcs are written as Beziers of at most 30 degrees, whose error is
about 3e-7 of the radius: still under a pixel when a circle is so far zoomed in that its radius is a million pixels.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from functools import cached_property
from typing import Protocol

from .vec import TAU, Affine, BBox, Vec, bbox_union, norm_angle, polar, tol

_BEZIER_STEP = math.pi / 6.0

# Gauss-Legendre, 8 points on [-1, 1]
_GL = ((-0.9602898564975363, 0.1012285362903763), (-0.7966664774136267, 0.2223810344533745),
       (-0.5255324099163290, 0.3137066458778873), (-0.1834346424956498, 0.3626837833783620),
       (0.1834346424956498, 0.3626837833783620), (0.5255324099163290, 0.3137066458778873),
       (0.7966664774136267, 0.2223810344533745), (0.9602898564975363, 0.1012285362903763))


class PathSink(Protocol):
    def moveTo(self, x: float, y: float) -> None: ...
    def lineTo(self, x: float, y: float) -> None: ...
    def cubicTo(self, x1: float, y1: float, x2: float, y2: float, x: float, y: float) -> None: ...
    def closeSubpath(self) -> None: ...


class PathRecorder:
    """A PathSink that keeps what it's given, for anything that isn't a Qt path: SVG, flattening, tests."""
    __slots__ = ("ops",)

    def __init__(self) -> None:
        self.ops: list[tuple] = []

    def moveTo(self, x, y):
        self.ops.append(("M", x, y))

    def lineTo(self, x, y):
        self.ops.append(("L", x, y))

    def cubicTo(self, x1, y1, x2, y2, x, y):
        self.ops.append(("C", x1, y1, x2, y2, x, y))

    def closeSubpath(self):
        self.ops.append(("Z",))


def _integrate(speed, t0: float, t1: float, pieces: int) -> float:
    total = 0.0
    h = (t1 - t0) / pieces
    for i in range(pieces):
        mid = t0 + (i + 0.5) * h
        for node, weight in _GL:
            total += weight * speed(mid + node * h * 0.5)
    return total * abs(h) * 0.5


@dataclass(frozen=True, slots=True)
class Line:
    a: Vec
    b: Vec

    closed = False
    kind = "line"

    @property
    def start(self) -> Vec:
        return self.a

    @property
    def end(self) -> Vec:
        return self.b

    def point_at(self, t: float) -> Vec:
        if t == 0.0:
            return self.a
        if t == 1.0:
            return self.b
        a, b = self.a, self.b
        return Vec(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)

    def tangent_at(self, t: float = 0.0) -> Vec:
        a, b = self.a, self.b
        return Vec(b[0] - a[0], b[1] - a[1])

    def length(self) -> float:
        a, b = self.a, self.b
        return math.hypot(b[0] - a[0], b[1] - a[1])

    def bbox(self) -> BBox:
        (ax, ay), (bx, by) = self.a, self.b
        return (ax if ax < bx else bx, ay if ay < by else by, ax if ax > bx else bx, ay if ay > by else by)

    def param_of(self, p) -> float:
        """Where the perpendicular from p lands along this line, not limited to the segment."""
        (ax, ay), (bx, by) = self.a, self.b
        dx, dy = bx - ax, by - ay
        n = dx * dx + dy * dy
        return ((p[0] - ax) * dx + (p[1] - ay) * dy) / n if n > 0.0 else 0.0

    def project(self, p) -> tuple[float, Vec]:
        t = self.param_of(p)
        t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
        return t, self.point_at(t)

    def transformed(self, m: Affine) -> "Line":
        return Line(m.apply(self.a), m.apply(self.b))

    def sub(self, t0: float, t1: float) -> "Line":
        return Line(self.point_at(t0), self.point_at(t1))

    def reversed(self) -> "Line":
        return Line(self.b, self.a)

    def emit(self, sink: PathSink, move: bool = True) -> None:
        if move:
            sink.moveTo(self.a[0], self.a[1])
        sink.lineTo(self.b[0], self.b[1])

    def flatten(self, chord: float) -> list[Vec]:
        return [self.a, self.b]


@dataclass(frozen=True, slots=True)
class Arc:
    """Runs from `start` by the signed `sweep`, in radians. At most one full turn."""
    c: Vec
    r: float
    start_angle: float
    sweep: float

    kind = "arc"

    @property
    def closed(self) -> bool:
        return abs(self.sweep) >= TAU

    @property
    def end_angle(self) -> float:
        return self.start_angle + self.sweep

    @property
    def start(self) -> Vec:
        return polar(self.c, self.r, self.start_angle)

    @property
    def end(self) -> Vec:
        return polar(self.c, self.r, self.start_angle + self.sweep)

    def point_at(self, t: float) -> Vec:
        return polar(self.c, self.r, self.start_angle + self.sweep * t)

    def tangent_at(self, t: float = 0.0) -> Vec:
        a = self.start_angle + self.sweep * t
        k = self.r * self.sweep
        return Vec(-k * math.sin(a), k * math.cos(a))

    def length(self) -> float:
        return self.r * abs(self.sweep)

    def param_of_angle(self, angle: float) -> float:
        """How far along the arc this direction from the centre is. Over 1 means it's past the end."""
        if self.sweep > 0.0:
            return norm_angle(angle - self.start_angle) / self.sweep
        if self.sweep < 0.0:
            return norm_angle(self.start_angle - angle) / -self.sweep
        return math.inf

    def bbox(self) -> BBox:
        (cx, cy), r = self.c, self.r
        s, e = self.start, self.end
        x0, x1 = (s[0], e[0]) if s[0] < e[0] else (e[0], s[0])
        y0, y1 = (s[1], e[1]) if s[1] < e[1] else (e[1], s[1])
        if self.param_of_angle(0.0) <= 1.0:
            x1 = cx + r
        if self.param_of_angle(math.pi) <= 1.0:
            x0 = cx - r
        if self.param_of_angle(math.pi / 2.0) <= 1.0:
            y1 = cy + r
        if self.param_of_angle(math.pi * 1.5) <= 1.0:
            y0 = cy - r
        return (x0, y0, x1, y1)

    def project(self, p) -> tuple[float, Vec]:
        dx, dy = p[0] - self.c[0], p[1] - self.c[1]
        if dx == 0.0 and dy == 0.0:
            return 0.0, self.start
        t = self.param_of_angle(math.atan2(dy, dx))
        if t <= 1.0:
            return t, self.point_at(t)
        s, e = self.start, self.end
        if math.hypot(p[0] - s[0], p[1] - s[1]) <= math.hypot(p[0] - e[0], p[1] - e[1]):
            return 0.0, s
        return 1.0, e

    def transformed(self, m: Affine) -> "Arc | EllipseArc":
        if m.is_conformal():
            d = m.apply_vector(polar(Vec(0.0, 0.0), 1.0, self.start_angle))
            return Arc(m.apply(self.c), self.r * d.length(), d.angle(), -self.sweep if m.is_mirrored() else self.sweep)
        return _ellipse_from_conjugate(m.apply(self.c), m.apply_vector(Vec(self.r, 0.0)), m.apply_vector(Vec(0.0, self.r)),
                                       self.start_angle, self.sweep)

    def sub(self, t0: float, t1: float) -> "Arc":
        return Arc(self.c, self.r, self.start_angle + self.sweep * t0, self.sweep * (t1 - t0))

    def reversed(self) -> "Arc":
        return Arc(self.c, self.r, self.start_angle + self.sweep, -self.sweep)

    def ccw(self) -> "Arc":
        """The same points, running counter-clockwise: how DXF stores an arc."""
        return self if self.sweep >= 0.0 else self.reversed()

    def emit(self, sink: PathSink, move: bool = True) -> None:
        (cx, cy), r = self.c, self.r
        _emit_conic(sink, cx, cy, r, 0.0, 0.0, r, self.start_angle, self.sweep, move)

    def flatten(self, chord: float) -> list[Vec]:
        n = _pieces_for_chord(self.r, abs(self.sweep), chord)
        return [self.point_at(i / n) for i in range(n + 1)]


@dataclass(frozen=True, slots=True)
class Circle:
    c: Vec
    r: float

    closed = True
    kind = "circle"

    @property
    def start(self) -> Vec:
        return Vec(self.c[0] + self.r, self.c[1])

    end = start

    def as_arc(self) -> Arc:
        return Arc(self.c, self.r, 0.0, TAU)

    def point_at(self, t: float) -> Vec:
        return polar(self.c, self.r, TAU * t)

    def tangent_at(self, t: float = 0.0) -> Vec:
        return self.as_arc().tangent_at(t)

    def length(self) -> float:
        return TAU * self.r

    def bbox(self) -> BBox:
        (cx, cy), r = self.c, self.r
        return (cx - r, cy - r, cx + r, cy + r)

    def project(self, p) -> tuple[float, Vec]:
        return self.as_arc().project(p)

    def transformed(self, m: Affine) -> "Circle | EllipseArc":
        if m.is_conformal():
            return Circle(m.apply(self.c), self.r * m.apply_vector(Vec(1.0, 0.0)).length())
        return _ellipse_from_conjugate(m.apply(self.c), m.apply_vector(Vec(self.r, 0.0)), m.apply_vector(Vec(0.0, self.r)),
                                       0.0, TAU)

    def sub(self, t0: float, t1: float) -> Arc:
        return self.as_arc().sub(t0, t1)

    def reversed(self) -> Arc:
        return self.as_arc().reversed()

    def emit(self, sink: PathSink, move: bool = True) -> None:
        (cx, cy), r = self.c, self.r
        _emit_conic(sink, cx, cy, r, 0.0, 0.0, r, 0.0, TAU, move)
        sink.closeSubpath()

    def flatten(self, chord: float) -> list[Vec]:
        return self.as_arc().flatten(chord)


@dataclass(frozen=True, slots=True)
class EllipseArc:
    """c + major cos(a) + minor sin(a) with minor = ratio * major turned 90 degrees, for a from `start_param` by `sweep`."""
    c: Vec
    major: Vec
    ratio: float
    start_param: float = 0.0
    sweep: float = TAU

    kind = "ellipse"

    @property
    def closed(self) -> bool:
        return abs(self.sweep) >= TAU

    @property
    def minor(self) -> Vec:
        return Vec(-self.major[1] * self.ratio, self.major[0] * self.ratio)

    def at_param(self, a: float) -> Vec:
        (cx, cy), (mx, my), k = self.c, self.major, self.ratio
        c, s = math.cos(a), math.sin(a)
        return Vec(cx + mx * c - my * k * s, cy + my * c + mx * k * s)

    @property
    def start(self) -> Vec:
        return self.at_param(self.start_param)

    @property
    def end(self) -> Vec:
        return self.at_param(self.start_param + self.sweep)

    def point_at(self, t: float) -> Vec:
        return self.at_param(self.start_param + self.sweep * t)

    def tangent_at(self, t: float = 0.0) -> Vec:
        a = self.start_param + self.sweep * t
        (mx, my), k, sw = self.major, self.ratio, self.sweep
        c, s = math.cos(a), math.sin(a)
        return Vec(sw * (-mx * s - my * k * c), sw * (-my * s + mx * k * c))

    def length(self) -> float:
        def speed(t):
            return self.tangent_at(t).length()
        return _integrate(speed, 0.0, 1.0, max(1, math.ceil(abs(self.sweep) / (math.pi / 4.0))))

    def param_to_t(self, a: float) -> float:
        """How far along the arc ellipse parameter a is. Over 1 means it's past the end."""
        if self.sweep > 0.0:
            return norm_angle(a - self.start_param) / self.sweep
        if self.sweep < 0.0:
            return norm_angle(self.start_param - a) / -self.sweep
        return math.inf

    def bbox(self) -> BBox:
        (mx, my), (nx, ny) = self.major, self.minor
        s, e = self.start, self.end
        x0, x1 = (s[0], e[0]) if s[0] < e[0] else (e[0], s[0])
        y0, y1 = (s[1], e[1]) if s[1] < e[1] else (e[1], s[1])
        for a0 in (math.atan2(nx, mx), math.atan2(ny, my)):
            for a in (a0, a0 + math.pi):
                if self.param_to_t(a) <= 1.0:
                    x, y = self.at_param(a)
                    x0, x1 = min(x0, x), max(x1, x)
                    y0, y1 = min(y0, y), max(y1, y)
        return (x0, y0, x1, y1)

    def project(self, p) -> tuple[float, Vec]:
        a = _closest_ellipse_param(self.major.length(), self.ratio, self.major.unit(), self.c, p)
        t = self.param_to_t(a)
        if t <= 1.0:
            return t, self.point_at(t)
        # The nearest point of the whole ellipse isn't on this part. From inside an ellipse there can be another dip in
        # distance that is, so search along the part rather than assuming it's an end.
        px, py = p[0], p[1]

        def dist(u):
            q = self.point_at(u)
            return math.hypot(q[0] - px, q[1] - py)
        pieces = 32
        best_i = min(range(pieces + 1), key=lambda i: dist(i / pieces))
        lo, hi = max(best_i - 1, 0) / pieces, min(best_i + 1, pieces) / pieces
        phi = 0.6180339887498949
        x1, x2 = hi - (hi - lo) * phi, lo + (hi - lo) * phi
        f1, f2 = dist(x1), dist(x2)
        for _ in range(50):
            if f1 <= f2:
                hi, x2, f2 = x2, x1, f1
                x1 = hi - (hi - lo) * phi
                f1 = dist(x1)
            else:
                lo, x1, f1 = x1, x2, f2
                x2 = lo + (hi - lo) * phi
                f2 = dist(x2)
        best = min((x1, f1), (x2, f2), (best_i / pieces, dist(best_i / pieces)), key=lambda v: v[1])[0]
        return best, self.point_at(best)

    def transformed(self, m: Affine) -> "EllipseArc":
        return _ellipse_from_conjugate(m.apply(self.c), m.apply_vector(self.major), m.apply_vector(self.minor),
                                       self.start_param, self.sweep)

    def sub(self, t0: float, t1: float) -> "EllipseArc":
        return EllipseArc(self.c, self.major, self.ratio, self.start_param + self.sweep * t0, self.sweep * (t1 - t0))

    def reversed(self) -> "EllipseArc":
        return EllipseArc(self.c, self.major, self.ratio, self.start_param + self.sweep, -self.sweep)

    def ccw(self) -> "EllipseArc":
        return self if self.sweep >= 0.0 else self.reversed()

    def emit(self, sink: PathSink, move: bool = True) -> None:
        (cx, cy), (mx, my), (nx, ny) = self.c, self.major, self.minor
        _emit_conic(sink, cx, cy, mx, my, nx, ny, self.start_param, self.sweep, move)
        if self.closed:
            sink.closeSubpath()

    def flatten(self, chord: float) -> list[Vec]:
        n = _pieces_for_chord(self.major.length(), abs(self.sweep), chord)
        return [self.point_at(i / n) for i in range(n + 1)]


def _pieces_for_chord(radius: float, sweep: float, chord: float) -> int:
    """Chord segments so a curve of this radius stays within `chord` of the true curve."""
    if radius <= chord or chord <= 0.0:
        return max(1, math.ceil(sweep / (math.pi / 2.0)))
    step = 2.0 * math.acos(1.0 - chord / radius)
    return max(1, min(100_000, math.ceil(sweep / step)))


def _emit_conic(sink, cx, cy, ux, uy, vx, vy, a0, sweep, move) -> None:
    """c + u cos a + v sin a, as Beziers. Exact to about 3e-7 of the radius, and the same under any affine map."""
    n = max(1, math.ceil(abs(sweep) / _BEZIER_STEP - 1e-9))
    da = sweep / n
    k = 4.0 / 3.0 * math.tan(da / 4.0)
    c0, s0 = math.cos(a0), math.sin(a0)
    x0, y0 = cx + ux * c0 + vx * s0, cy + uy * c0 + vy * s0
    if move:
        sink.moveTo(x0, y0)
    cubic = sink.cubicTo
    for i in range(1, n + 1):
        a1 = a0 + da * i
        c1, s1 = math.cos(a1), math.sin(a1)
        x1, y1 = cx + ux * c1 + vx * s1, cy + uy * c1 + vy * s1
        cubic(x0 + k * (-ux * s0 + vx * c0), y0 + k * (-uy * s0 + vy * c0),
              x1 - k * (-ux * s1 + vx * c1), y1 - k * (-uy * s1 + vy * c1), x1, y1)
        x0, y0, c0, s0 = x1, y1, c1, s1


def _ellipse_from_conjugate(c: Vec, u: Vec, v: Vec, start: float, sweep: float) -> EllipseArc:
    """The ellipse c + u cos a + v sin a, with its axes found and the parameter moved to match, so every point stays
    where it was. Handles a mirror: the arc then runs the other way round its own axes."""
    a0 = 0.5 * math.atan2(2.0 * u.dot(v), u.dot(u) - v.dot(v))
    c0, s0 = math.cos(a0), math.sin(a0)
    major = Vec(u[0] * c0 + v[0] * s0, u[1] * c0 + v[1] * s0)
    minor = Vec(-u[0] * s0 + v[0] * c0, -u[1] * s0 + v[1] * c0)
    if minor.length2() > major.length2():
        a0 += math.pi / 2.0
        major, minor = minor, -major
    length = major.length()
    if length == 0.0:
        raise ValueError("this transform flattens the ellipse to a point")
    ratio = min(1.0, minor.length() / length)
    if major.cross(minor) >= 0.0:
        return EllipseArc(c, major, ratio, start - a0, sweep)
    return EllipseArc(c, major, ratio, a0 - start, -sweep)


def _closest_ellipse_param(a: float, ratio: float, axis: Vec, c, p) -> float:
    """The ellipse parameter of the nearest point: a robust start (it can't fail for a point on or near an axis),
    polished by Newton so the answer is exact to rounding."""
    b = a * ratio
    dx, dy = p[0] - c[0], p[1] - c[1]
    x = dx * axis[0] + dy * axis[1]
    y = -dx * axis[1] + dy * axis[0]
    if b >= a * (1.0 - 1e-14):
        return math.atan2(y, x) if (x or y) else 0.0
    px, py = abs(x), abs(y)
    tx = ty = math.sqrt(0.5)
    e2 = a * a - b * b
    for _ in range(6):
        ex = e2 * tx * tx * tx / a
        ey = -e2 * ty * ty * ty / b
        rx, ry = a * tx - ex, b * ty - ey
        qx, qy = px - ex, py - ey
        r = math.hypot(rx, ry)
        q = math.hypot(qx, qy)
        if q == 0.0:
            break
        tx = min(1.0, max(0.0, (qx * r / q + ex) / a))
        ty = min(1.0, max(0.0, (qy * r / q + ey) / b))
        n = math.hypot(tx, ty)
        tx, ty = tx / n, ty / n
    t = math.atan2(ty, tx)
    for _ in range(4):
        st, ct = math.sin(t), math.cos(t)
        f = e2 * ct * st - px * a * st + py * b * ct
        df = e2 * (ct * ct - st * st) - px * a * ct - py * b * st
        if df == 0.0:
            break
        nt = t - f / df
        if not 0.0 <= nt <= math.pi / 2.0:
            break
        t = nt
    ct, st = math.cos(t), math.sin(t)
    return math.atan2(math.copysign(st, y), math.copysign(ct, x))


@dataclass(frozen=True)
class Spline:
    """A B-spline or NURBS curve, as a DXF file stores it."""
    degree: int
    knots: tuple[float, ...]
    ctrl: tuple[Vec, ...]
    weights: tuple[float, ...] = ()
    fit: tuple[Vec, ...] = ()
    periodic: bool = False

    kind = "spline"

    def __post_init__(self):
        n = len(self.ctrl)
        if self.degree < 1 or n < self.degree + 1:
            raise ValueError(f"a spline of degree {self.degree} needs at least {self.degree + 1} control points")
        if len(self.knots) != n + self.degree + 1:
            raise ValueError(f"a spline with {n} control points of degree {self.degree} needs "
                             f"{n + self.degree + 1} knots, not {len(self.knots)}")
        if any(self.knots[i] > self.knots[i + 1] for i in range(len(self.knots) - 1)):
            raise ValueError("a spline's knots must not decrease")
        if self.knots[self.degree] >= self.knots[n]:
            raise ValueError("a spline's knots must span a range")
        if self.weights and len(self.weights) != n:
            raise ValueError("a spline needs one weight for each control point")
        if self.weights and any(w <= 0.0 or not math.isfinite(w) for w in self.weights):
            raise ValueError("a spline's weights must be positive")

    @property
    def domain(self) -> tuple[float, float]:
        return self.knots[self.degree], self.knots[len(self.ctrl)]

    @cached_property
    def closed(self) -> bool:
        s, e = self.start, self.end
        return self.periodic or math.hypot(s[0] - e[0], s[1] - e[1]) <= tol(s[0], s[1], e[0], e[1])

    @property
    def start(self) -> Vec:
        return self.point_at(0.0)

    @property
    def end(self) -> Vec:
        return self.point_at(1.0)

    def _span(self, u: float) -> int:
        p, n, knots = self.degree, len(self.ctrl), self.knots
        if u >= knots[n]:
            i = n - 1
            while i > p and knots[i] >= knots[i + 1]:
                i -= 1
            return i
        if u <= knots[p]:
            i = p
            while i < n - 1 and knots[i] >= knots[i + 1]:
                i += 1
            return i
        return bisect.bisect_right(knots, u, p, n + 1) - 1

    def _homogeneous(self, u: float, order: int = 0) -> list[tuple[float, float, float]]:
        """de Boor on the homogeneous control points. Returns the point and, with order=1, the derivative."""
        p, knots = self.degree, self.knots
        k = self._span(u)
        w = self.weights
        pts = []
        for j in range(k - p, k + 1):
            cx, cy = self.ctrl[j]
            wj = w[j] if w else 1.0
            pts.append((cx * wj, cy * wj, wj))
        d1 = None
        for r in range(1, p + 1):
            if order == 1 and r == p:
                a, b = pts[p - 1], pts[p]
                den = knots[k + 1] - knots[k]
                d1 = tuple(p * (b[i] - a[i]) / den for i in range(3))
            nxt = list(pts)
            for j in range(p, r - 1, -1):
                i = k - p + j
                den = knots[i + p - r + 1] - knots[i]
                alpha = (u - knots[i]) / den if den > 0.0 else 0.0
                a, b = pts[j - 1], pts[j]
                nxt[j] = (a[0] + (b[0] - a[0]) * alpha, a[1] + (b[1] - a[1]) * alpha, a[2] + (b[2] - a[2]) * alpha)
            pts = nxt
        return [pts[p]] if order == 0 else [pts[p], d1]

    def point_at(self, t: float) -> Vec:
        u0, u1 = self.domain
        h = self._homogeneous(u0 + (u1 - u0) * (0.0 if t < 0.0 else 1.0 if t > 1.0 else t))[0]
        return Vec(h[0] / h[2], h[1] / h[2])

    def tangent_at(self, t: float = 0.0) -> Vec:
        u0, u1 = self.domain
        (x, y, w), (dx, dy, dw) = self._homogeneous(u0 + (u1 - u0) * (0.0 if t < 0.0 else 1.0 if t > 1.0 else t), 1)
        k = u1 - u0
        return Vec(k * (dx * w - x * dw) / (w * w), k * (dy * w - y * dw) / (w * w))

    @cached_property
    def _spans(self) -> list[tuple[float, float]]:
        """The parameter ranges between distinct knots, as t values."""
        u0, u1 = self.domain
        spans = []
        prev = None
        for u in self.knots[self.degree:len(self.ctrl) + 1]:
            if prev is not None and u > prev:
                spans.append(((prev - u0) / (u1 - u0), (u - u0) / (u1 - u0)))
            prev = u
        return spans

    @cached_property
    def _polygon(self) -> list[tuple[float, Vec]]:
        """(t, point) samples within about 1/20000 of the curve's size: for bounds and starting searches."""
        (x0, y0), (x1, y1) = (min(p[0] for p in self.ctrl), min(p[1] for p in self.ctrl)), \
                             (max(p[0] for p in self.ctrl), max(p[1] for p in self.ctrl))
        limit = max(math.hypot(x1 - x0, y1 - y0), 1e-12) * 5e-5
        out = [(0.0, self.point_at(0.0))]

        def divide(t0, p0, t1, p1, depth):
            tm = (t0 + t1) / 2.0
            pm = self.point_at(tm)
            q1, q3 = self.point_at(t0 + (t1 - t0) / 4.0), self.point_at(t0 + (t1 - t0) * 0.75)
            chord = Line(p0, p1)
            worst = max(_dist(chord.project(q)[1], q) for q in (pm, q1, q3))
            if depth > 0 and worst > limit:
                divide(t0, p0, tm, pm, depth - 1)
                divide(tm, pm, t1, p1, depth - 1)
            else:
                out.append((t1, p1))
        for a, b in self._spans:
            divide(a, self.point_at(a), b, self.point_at(b), 8)
        return out

    def length(self) -> float:
        def speed(t):
            return self.tangent_at(t).length()
        return sum(_integrate(speed, a, b, 2) for a, b in self._spans)

    @cached_property
    def _bbox(self) -> BBox:
        xs = [q[1][0] for q in self._polygon]
        ys = [q[1][1] for q in self._polygon]
        margin = max(max(xs) - min(xs), max(ys) - min(ys)) * 1e-4
        return (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)

    def bbox(self) -> BBox:
        return self._bbox

    def project(self, p) -> tuple[float, Vec]:
        """Bracketed from the polygon, narrowed by golden section in the nearest stretch and those beside it (it can't
        be fooled where the curve slows to a stop at repeated control points), then polished by Newton."""
        poly = self._polygon
        best_d, best_i = math.inf, 0
        px, py = p[0], p[1]
        for i in range(len(poly) - 1):
            ax, ay = poly[i][1]
            bx, by = poly[i + 1][1]
            ux, uy = bx - ax, by - ay
            n = ux * ux + uy * uy
            s = ((px - ax) * ux + (py - ay) * uy) / n if n > 0.0 else 0.0
            s = 0.0 if s < 0.0 else 1.0 if s > 1.0 else s
            dx, dy = ax + ux * s - px, ay + uy * s - py
            d = dx * dx + dy * dy
            if d < best_d:
                best_d, best_i = d, i

        def dist(t):
            q = self.point_at(t)
            return math.hypot(q[0] - p[0], q[1] - p[1])
        phi = 0.6180339887498949
        t, now = 0.0, dist(0.0)
        d1 = dist(1.0)
        if d1 < now:
            t, now = 1.0, d1
        for i in range(max(best_i - 1, 0), min(best_i + 2, len(poly) - 1)):
            lo, hi = poly[i][0], poly[i + 1][0]
            x1, x2 = hi - (hi - lo) * phi, lo + (hi - lo) * phi
            f1, f2 = dist(x1), dist(x2)
            for _ in range(24):
                if f1 <= f2:
                    hi, x2, f2 = x2, x1, f1
                    x1 = hi - (hi - lo) * phi
                    f1 = dist(x1)
                else:
                    lo, x1, f1 = x1, x2, f2
                    x2 = lo + (hi - lo) * phi
                    f2 = dist(x2)
            if f1 < now:
                t, now = x1, f1
            if f2 < now:
                t, now = x2, f2
        for _ in range(5):
            q = self.point_at(t)
            d = self.tangent_at(t)
            n = d.dot(d)
            if n == 0.0:
                break
            t2 = min(1.0, max(0.0, t + ((p[0] - q[0]) * d[0] + (p[1] - q[1]) * d[1]) / n))
            d2 = dist(t2)
            if t2 == t or d2 >= now:
                break
            t, now = t2, d2
        return t, self.point_at(t)

    def transformed(self, m: Affine) -> "Spline":
        return Spline(self.degree, self.knots, tuple(m.apply(p) for p in self.ctrl), self.weights,
                      tuple(m.apply(p) for p in self.fit), self.periodic)

    def reversed(self) -> "Spline":
        top = self.knots[-1] + self.knots[0]
        return Spline(self.degree, tuple(top - u for u in reversed(self.knots)), tuple(reversed(self.ctrl)),
                      tuple(reversed(self.weights)), tuple(reversed(self.fit)), self.periodic)

    def _with_knot(self, u: float) -> "Spline":
        """The same curve with one more knot (Boehm)."""
        p, knots = self.degree, self.knots
        k = self._span(u)
        w = self.weights
        homog = [(c[0] * (w[i] if w else 1.0), c[1] * (w[i] if w else 1.0), w[i] if w else 1.0)
                 for i, c in enumerate(self.ctrl)]
        out = homog[:k - p + 1]
        for i in range(k - p + 1, k + 1):
            den = knots[i + p] - knots[i]
            a = (u - knots[i]) / den if den > 0.0 else 0.0
            q0, q1 = homog[i - 1], homog[i]
            out.append(tuple(q0[j] + (q1[j] - q0[j]) * a for j in range(3)))
        out += homog[k:]
        new_knots = knots[:k + 1] + (u,) + knots[k + 1:]
        ctrl = tuple(Vec(q[0] / q[2], q[1] / q[2]) for q in out)
        return Spline(p, new_knots, ctrl, tuple(q[2] for q in out) if w else ())

    def sub(self, t0: float, t1: float) -> "Spline":
        """The part from t0 to t1, exact: a clamped spline of its own."""
        if t1 < t0:
            return self.sub(t1, t0).reversed()
        u0, u1 = self.domain
        ua, ub = u0 + (u1 - u0) * t0, u0 + (u1 - u0) * t1
        s = self
        for u in (ua, ub):
            have = sum(1 for k in s.knots if k == u)
            for _ in range(s.degree - have):
                s = s._with_knot(u)
        p = s.degree
        # with p copies of a knot, the curve passes through the one control point just before them
        ia = bisect.bisect_right(s.knots, ua) - p
        ib = bisect.bisect_left(s.knots, ub)
        ctrl = s.ctrl[ia - 1:ib]
        knots = (ua,) * (p + 1) + s.knots[ia + p:ib] + (ub,) * (p + 1)
        weights = s.weights[ia - 1:ib] if s.weights else ()
        return Spline(p, knots, ctrl, weights)

    def emit(self, sink: PathSink, move: bool = True) -> None:
        """A polynomial cubic span is exactly one Bezier. Anything else (NURBS, higher degree) is divided until a Bezier
        matches it to a hundred-thousandth of the curve's size."""
        exact = not self.weights and self.degree <= 3
        if move:
            q = self.point_at(0.0)
            sink.moveTo(q[0], q[1])
        (x0, y0, x1, y1) = self._bbox
        limit = max(x1 - x0, y1 - y0) * 1e-5

        def piece(t0, p0, d0, t1, p1, d1, depth):
            dt = (t1 - t0) / 3.0
            c1x, c1y = p0[0] + d0[0] * dt, p0[1] + d0[1] * dt
            c2x, c2y = p1[0] - d1[0] * dt, p1[1] - d1[1] * dt
            if not exact and depth > 0:
                tm = (t0 + t1) / 2.0
                pm = self.point_at(tm)
                bx = (p0[0] + p1[0]) * 0.125 + (c1x + c2x) * 0.375
                by = (p0[1] + p1[1]) * 0.125 + (c1y + c2y) * 0.375
                if math.hypot(bx - pm[0], by - pm[1]) > limit:
                    dm = self.tangent_at(tm)
                    piece(t0, p0, d0, tm, pm, dm, depth - 1)
                    piece(tm, pm, dm, t1, p1, d1, depth - 1)
                    return
            sink.cubicTo(c1x, c1y, c2x, c2y, p1[0], p1[1])
        for a, b in self._spans:
            # at a knot the curve may turn sharply, so take each end's tangent from inside this span
            inset = (b - a) * 1e-9
            piece(a, self.point_at(a), self.tangent_at(a + inset), b, self.point_at(b), self.tangent_at(b - inset), 8)

    def flatten(self, chord: float) -> list[Vec]:
        out = [self.point_at(0.0)]

        def divide(t0, p0, t1, p1, depth):
            tm = (t0 + t1) / 2.0
            pm = self.point_at(tm)
            line = Line(p0, p1)
            q1 = self.point_at(t0 + (t1 - t0) / 4.0)
            q3 = self.point_at(t0 + (t1 - t0) * 0.75)
            worst = max(_dist(line.project(q)[1], q) for q in (pm, q1, q3))
            if depth > 0 and worst > chord:
                divide(t0, p0, tm, pm, depth - 1)
                divide(tm, pm, t1, p1, depth - 1)
            else:
                out.append(p1)
        for a, b in self._spans:
            divide(a, self.point_at(a), b, self.point_at(b), 14)
        return out


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def bulge_arc(p: Vec, q: Vec, bulge: float) -> Arc:
    """The arc from p to q with a DXF bulge: the tangent of a quarter of the included angle, positive counter-clockwise."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    h = (1.0 - bulge * bulge) / (4.0 * bulge)
    c = Vec((p[0] + q[0]) / 2.0 - dy * h, (p[1] + q[1]) / 2.0 + dx * h)
    return Arc(c, c.distance(p), math.atan2(p[1] - c[1], p[0] - c[0]), 4.0 * math.atan(bulge))


@dataclass(frozen=True)
class Polyline:
    """Straight and arc segments joined end to end. `bulges` is empty when all are straight."""
    pts: tuple[Vec, ...]
    bulges: tuple[float, ...] = ()
    closed: bool = False
    width: float = 0.0
    #: (start width, end width) for each vertex, when they differ from `width`
    widths: tuple[tuple[float, float], ...] = ()

    kind = "polyline"

    def __post_init__(self):
        if len(self.pts) < 1:
            raise ValueError("a polyline needs at least one point")
        if self.bulges and len(self.bulges) != len(self.pts):
            raise ValueError("a polyline needs one bulge for each point")
        if self.bulges and not any(self.bulges):
            object.__setattr__(self, "bulges", ())      # all straight has one form, so equal polylines compare equal
        if self.widths and len(self.widths) != len(self.pts):
            raise ValueError("a polyline needs one width pair for each point")

    @property
    def start(self) -> Vec:
        return self.pts[0]

    @property
    def end(self) -> Vec:
        return self.pts[0] if self.closed else self.pts[-1]

    def bulge(self, i: int) -> float:
        return self.bulges[i] if self.bulges else 0.0

    @cached_property
    def segments(self) -> tuple[Line | Arc, ...]:
        pts = self.pts
        n = len(pts)
        out: list[Line | Arc] = []
        bulges = self.bulges
        last = n if self.closed else n - 1
        for i in range(last):
            p, q = pts[i], pts[i + 1 if i + 1 < n else 0]
            b = bulges[i] if bulges else 0.0
            if b and (p[0] != q[0] or p[1] != q[1]) and math.isfinite(b):
                out.append(bulge_arc(p, q, b))
            else:
                out.append(Line(p, q))
        return tuple(out)

    def length(self) -> float:
        return sum(s.length() for s in self.segments)

    @cached_property
    def _bbox(self) -> BBox:
        box: BBox | None = None
        for s in self.segments:
            box = bbox_union(box, s.bbox())
        if box is None:
            x, y = self.pts[0]
            return (x, y, x, y)
        return box

    def bbox(self) -> BBox:
        return self._bbox

    def point_at(self, t: float) -> Vec:
        """t runs over the whole polyline: 0 at the start, 1 at the end, in proportion to the number of segments."""
        segs = self.segments
        if not segs:
            return self.pts[0]
        x = t * len(segs)
        i = min(len(segs) - 1, max(0, int(x)))
        return segs[i].point_at(x - i)

    def tangent_at(self, t: float = 0.0) -> Vec:
        segs = self.segments
        if not segs:
            return Vec(0.0, 0.0)
        x = t * len(segs)
        i = min(len(segs) - 1, max(0, int(x)))
        return segs[i].tangent_at(x - i) * float(len(segs))

    def project(self, p) -> tuple[float, Vec]:
        segs = self.segments
        if not segs:
            return 0.0, self.pts[0]
        best = (math.inf, 0.0, self.pts[0])
        for i, s in enumerate(segs):
            t, q = s.project(p)
            d = _dist(q, p)
            if d < best[0]:
                best = (d, (i + t) / len(segs), q)
        return best[1], best[2]

    def transformed(self, m: Affine) -> "Polyline":
        """A polyline can only hold circular arcs. Where a stretch turns them into ellipses they become fine chords,
        within a thousandth of a percent of their radius."""
        if m.is_conformal():
            k = m.uniform_scale()
            mirrored = m.is_mirrored()
            bulges = tuple(-b for b in self.bulges) if mirrored and self.bulges else self.bulges
            return Polyline(tuple(m.apply(p) for p in self.pts), bulges, self.closed, self.width * k,
                            tuple((a * k, b * k) for a, b in self.widths))
        if not any(self.bulges):
            k = m.uniform_scale()
            return Polyline(tuple(m.apply(p) for p in self.pts), (), self.closed, self.width * k,
                            tuple((a * k, b * k) for a, b in self.widths))
        pts: list[Vec] = []
        for s in self.segments:
            if isinstance(s, Line):
                pts.append(s.a)
            else:
                pts.extend(s.flatten(s.r * 1e-5)[:-1])
        if not self.closed:
            pts.append(self.pts[-1])
        return Polyline(tuple(m.apply(p) for p in pts), (), self.closed, self.width * m.uniform_scale())

    def reversed(self) -> "Polyline":
        n = len(self.pts)
        if self.closed:
            pts = (self.pts[0],) + tuple(reversed(self.pts[1:]))
            bulges = tuple(-self.bulge(n - 1 - i) for i in range(n)) if self.bulges else ()
            widths = tuple((self.widths[n - 1 - i][1], self.widths[n - 1 - i][0]) for i in range(n)) if self.widths else ()
            return Polyline(pts, bulges, True, self.width, widths)
        pts = tuple(reversed(self.pts))
        bulges = tuple(-self.bulge(n - 2 - i) for i in range(n - 1)) + (0.0,) if self.bulges else ()
        widths = tuple((self.widths[n - 2 - i][1], self.widths[n - 2 - i][0]) for i in range(n - 1)) + ((0.0, 0.0),) \
            if self.widths else ()
        return Polyline(pts, bulges, False, self.width, widths)

    def emit(self, sink: PathSink, move: bool = True) -> None:
        pts = self.pts
        if move:
            sink.moveTo(pts[0][0], pts[0][1])
        if not self.bulges:
            line = sink.lineTo
            for p in pts[1:]:
                line(p[0], p[1])
        else:
            for s in (self.segments if not self.closed else self.segments[:-1]):
                s.emit(sink, False)
            if self.closed and isinstance(self.segments[-1], Arc):
                self.segments[-1].emit(sink, False)
        if self.closed:
            sink.closeSubpath()

    def flatten(self, chord: float) -> list[Vec]:
        out: list[Vec] = [self.pts[0]]
        for s in self.segments:
            out.extend(s.flatten(chord)[1:])
        return out

    def signed_area(self) -> float:
        """Positive counter-clockwise. Exact, arcs included. Only meaningful when closed."""
        pts = self.pts
        n = len(pts)
        area = 0.0
        x0, y0 = pts[0]
        for i in range(n):
            (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
            area += (ax - x0) * (by - y0) - (bx - x0) * (ay - y0)
        area *= 0.5
        for s in self.segments:
            if isinstance(s, Arc):
                area += 0.5 * s.r * s.r * (s.sweep - math.sin(s.sweep))
        return area

    def centroid(self) -> Vec | None:
        """The centre of area of a closed polyline, arcs included. None if it has no area."""
        pts = self.pts
        n = len(pts)
        x0, y0 = pts[0]
        ax = ay = total = 0.0
        for i in range(n):
            px, py = pts[i][0] - x0, pts[i][1] - y0
            qx, qy = pts[(i + 1) % n][0] - x0, pts[(i + 1) % n][1] - y0
            c = px * qy - qx * py
            total += c
            ax += (px + qx) * c
            ay += (py + qy) * c
        total *= 0.5
        ax /= 6.0
        ay /= 6.0
        for s in self.segments:
            if isinstance(s, Arc) and s.sweep:
                seg = 0.5 * s.r * s.r * (s.sweep - math.sin(s.sweep))
                half = abs(s.sweep) / 2.0
                # the circular segment's own centre lies on the arc's middle radius
                dist = 4.0 * s.r * math.sin(half) ** 3 / (3.0 * (2.0 * half - math.sin(2.0 * half)))
                mx, my = polar(s.c, dist, s.start_angle + s.sweep / 2.0)
                total += seg
                ax += seg * (mx - x0)
                ay += seg * (my - y0)
        if abs(total) <= 1e-14 * max(1.0, abs(ax), abs(ay)):
            return None
        return Vec(x0 + ax / total, y0 + ay / total)


Curve = Line | Arc | Circle | EllipseArc | Spline
