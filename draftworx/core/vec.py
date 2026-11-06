# SPDX-License-Identifier: GPL-3.0-or-later
"""Points, affine transforms, angles and the one tolerance policy the whole core uses.

Model coordinates are IEEE doubles in drawing units, and they are never rounded: a point that came from a snap is the
exact point the geometry defines. "Close enough to be the same point" is decided here and only here (`tol`, `near`), so
it doesn't drift between features.
"""
from __future__ import annotations

import math
from typing import NamedTuple, Sequence

TAU = math.tau
#: Lengths closer than this are the same, at ordinary coordinates.
EPS = 1e-9
#: Doubles carry about 16 digits. Keep four in hand, so large coordinates (a site plan in mm) still compare sensibly.
RELATIVE = 1e-12
#: Directions whose sine differs by less than this are parallel.
EPS_ANGLE = 1e-11


def tol(*magnitudes: float) -> float:
    """The length below which two things at these coordinates are the same."""
    m = 0.0
    for v in magnitudes:
        a = abs(v)
        if a > m:
            m = a
    t = m * RELATIVE
    return t if t > EPS else EPS


class Vec(NamedTuple):
    """A point or direction. Immutable, hashable, unpackable, and it writes to JSON as [x, y]."""
    x: float
    y: float

    def __add__(self, o):  # type: ignore[override]
        return Vec(self[0] + o[0], self[1] + o[1])

    def __sub__(self, o):
        return Vec(self[0] - o[0], self[1] - o[1])

    def __mul__(self, k: float):  # type: ignore[override]
        return Vec(self[0] * k, self[1] * k)

    __rmul__ = __mul__

    def __truediv__(self, k: float):
        return Vec(self[0] / k, self[1] / k)

    def __neg__(self):
        return Vec(-self[0], -self[1])

    def dot(self, o) -> float:
        return self[0] * o[0] + self[1] * o[1]

    def cross(self, o) -> float:
        return self[0] * o[1] - self[1] * o[0]

    def length(self) -> float:
        return math.hypot(self[0], self[1])

    def length2(self) -> float:
        return self[0] * self[0] + self[1] * self[1]

    def unit(self) -> "Vec":
        """The same direction with length 1. The zero vector stays zero, so callers must test what they mean."""
        n = math.hypot(self[0], self[1])
        return Vec(self[0] / n, self[1] / n) if n > 0.0 else Vec(0.0, 0.0)

    def perp(self) -> "Vec":
        """Turned 90 degrees counter-clockwise."""
        return Vec(-self[1], self[0])

    def rotated(self, angle: float) -> "Vec":
        c, s = math.cos(angle), math.sin(angle)
        return Vec(self[0] * c - self[1] * s, self[0] * s + self[1] * c)

    def angle(self) -> float:
        """Direction in radians, in [0, tau)."""
        return norm_angle(math.atan2(self[1], self[0]))

    def distance(self, o) -> float:
        return math.hypot(self[0] - o[0], self[1] - o[1])

    def lerp(self, o, t: float) -> "Vec":
        return Vec(self[0] + (o[0] - self[0]) * t, self[1] + (o[1] - self[1]) * t)

    def midpoint(self, o) -> "Vec":
        return Vec((self[0] + o[0]) / 2.0, (self[1] + o[1]) / 2.0)


ORIGIN = Vec(0.0, 0.0)


def near(a: Sequence[float], b: Sequence[float], eps: float | None = None) -> bool:
    """Are these the same point, by the tolerance for where they are?"""
    dx, dy = a[0] - b[0], a[1] - b[1]
    limit = eps if eps is not None else tol(a[0], a[1], b[0], b[1])
    return dx * dx + dy * dy <= limit * limit


def polar(center: Sequence[float], radius: float, angle: float) -> Vec:
    return Vec(center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle))


def norm_angle(a: float) -> float:
    """Into [0, tau)."""
    a = math.fmod(a, TAU)
    if a < 0.0:
        a += TAU
        if a >= TAU:     # a tiny negative value rounds up to tau
            a = 0.0
    return a


def ccw_sweep(start: float, end: float) -> float:
    """The counter-clockwise sweep from start to end, in (0, tau]. Equal angles mean a full turn."""
    s = norm_angle(end - start)
    return s if s > 0.0 else TAU


def same_angle(a: float, b: float, eps: float = 1e-10) -> bool:
    d = norm_angle(a - b)
    return d <= eps or TAU - d <= eps


class Affine(NamedTuple):
    """x' = a x + c y + e;  y' = b x + d y + f. Same layout as PDF, SVG and Qt's QTransform."""
    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def apply(self, p: Sequence[float]) -> Vec:
        x, y = p[0], p[1]
        return Vec(self[0] * x + self[2] * y + self[4], self[1] * x + self[3] * y + self[5])

    def apply_vector(self, v: Sequence[float]) -> Vec:
        """A direction: moves don't change it."""
        return Vec(self[0] * v[0] + self[2] * v[1], self[1] * v[0] + self[3] * v[1])

    def then(self, o: "Affine") -> "Affine":
        """This transform, followed by o."""
        a, b, c, d, e, f = self
        A, B, C, D, E, F = o
        return Affine(A * a + C * b, B * a + D * b, A * c + C * d, B * c + D * d, A * e + C * f + E, B * e + D * f + F)

    def det(self) -> float:
        return self[0] * self[3] - self[1] * self[2]

    def inverse(self) -> "Affine":
        a, b, c, d, e, f = self
        det = a * d - b * c
        if det == 0.0 or not math.isfinite(det):
            raise ValueError("this transform flattens everything onto a line, so it can't be undone")
        i = 1.0 / det
        return Affine(d * i, -b * i, -c * i, a * i, (c * f - d * e) * i, (b * e - a * f) * i)

    def is_identity(self) -> bool:
        return self == IDENTITY

    def is_conformal(self) -> bool:
        """Does it keep circles as circles: a move, turn, mirror or uniform scale?"""
        a, b, c, d = self[0], self[1], self[2], self[3]
        big = max(abs(a), abs(b), abs(c), abs(d), 1e-300)
        limit = big * big * 1e-12
        return abs(a * c + b * d) <= limit and abs((a * a + b * b) - (c * c + d * d)) <= limit

    def uniform_scale(self) -> float:
        """How much it scales lengths, for a conformal transform (the geometric mean otherwise)."""
        return math.sqrt(abs(self.det()))

    def is_mirrored(self) -> bool:
        return self.det() < 0.0

    @staticmethod
    def translation(dx: float, dy: float) -> "Affine":
        return Affine(1.0, 0.0, 0.0, 1.0, dx, dy)

    @staticmethod
    def rotation(angle: float, about: Sequence[float] = ORIGIN) -> "Affine":
        c, s = math.cos(angle), math.sin(angle)
        x, y = about[0], about[1]
        return Affine(c, s, -s, c, x - c * x + s * y, y - s * x - c * y)

    @staticmethod
    def scaling(sx: float, sy: float | None = None, about: Sequence[float] = ORIGIN) -> "Affine":
        sy = sx if sy is None else sy
        return Affine(sx, 0.0, 0.0, sy, about[0] * (1.0 - sx), about[1] * (1.0 - sy))

    @staticmethod
    def mirror(p: Sequence[float], q: Sequence[float]) -> "Affine":
        """About the line through p and q."""
        dx, dy = q[0] - p[0], q[1] - p[1]
        n2 = dx * dx + dy * dy
        if n2 == 0.0:
            raise ValueError("a mirror line needs two different points")
        a = (dx * dx - dy * dy) / n2
        b = 2.0 * dx * dy / n2
        return Affine(a, b, b, -a,
                      p[0] - a * p[0] - b * p[1],
                      p[1] - b * p[0] + a * p[1])


IDENTITY = Affine()

BBox = tuple[float, float, float, float]


def bbox_union(a: BBox | None, b: BBox | None) -> BBox | None:
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def bbox_of(points) -> BBox | None:
    pts = list(points)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def bbox_overlaps(a: BBox, b: BBox) -> bool:
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def bbox_contains(outer: BBox, inner: BBox) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def bbox_grow(b: BBox, by: float) -> BBox:
    return (b[0] - by, b[1] - by, b[2] + by, b[3] + by)
