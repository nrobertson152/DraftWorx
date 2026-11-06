# SPDX-License-Identifier: GPL-3.0-or-later
"""Constructions: curves from the points that define them."""
from __future__ import annotations

import math

from .curves import Arc, Circle
from .vec import TAU, Vec, norm_angle, tol


def circumcentre(a: Vec, b: Vec, c: Vec) -> Vec | None:
    """The point the same distance from all three. None if they're in a line."""
    # measured from a, which keeps the arithmetic accurate far from the origin
    bx, by = b[0] - a[0], b[1] - a[1]
    cx, cy = c[0] - a[0], c[1] - a[1]
    d = 2.0 * (bx * cy - by * cx)
    size = max(abs(bx), abs(by), abs(cx), abs(cy))
    if abs(d) <= size * size * 1e-12:
        return None
    b2, c2 = bx * bx + by * by, cx * cx + cy * cy
    return Vec(a[0] + (cy * b2 - by * c2) / d, a[1] + (bx * c2 - cx * b2) / d)


def arc_through(a: Vec, b: Vec, c: Vec) -> Arc | None:
    """The arc from a through b to c. None if they're in a line."""
    o = circumcentre(a, b, c)
    if o is None:
        return None
    start = math.atan2(a[1] - o[1], a[0] - o[0])
    end = math.atan2(c[1] - o[1], c[0] - o[0])
    mid = math.atan2(b[1] - o[1], b[0] - o[0])
    sweep = norm_angle(end - start)
    if norm_angle(mid - start) > sweep:
        sweep -= TAU        # b isn't on the counter-clockwise way round, so it runs clockwise
    return Arc(o, o.distance(a), start, sweep)


def circle_through(a: Vec, b: Vec, c: Vec) -> Circle | None:
    o = circumcentre(a, b, c)
    return Circle(o, o.distance(a)) if o is not None else None


def arc_from_center(center: Vec, start: Vec, end: Vec) -> Arc | None:
    """Counter-clockwise from `start` to the direction of `end`, at the radius of `start`."""
    r = center.distance(start)
    if r <= tol(center[0], center[1], start[0], start[1]):
        return None
    a0 = math.atan2(start[1] - center[1], start[0] - center[0])
    a1 = math.atan2(end[1] - center[1], end[0] - center[0])
    sweep = norm_angle(a1 - a0)
    return Arc(center, r, a0, sweep if sweep > 0.0 else TAU)
