# SPDX-License-Identifier: GPL-3.0-or-later
"""Random geometry for property tests: coordinates a real drawing could hold, from fine detail to a site plan."""
import math

from hypothesis import strategies as st

from draftworx.core.curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from draftworx.core.vec import TAU, Vec

coord = st.floats(min_value=-5000.0, max_value=5000.0, allow_nan=False, allow_infinity=False, width=64)
point = st.builds(Vec, coord, coord)
radius = st.floats(min_value=0.01, max_value=2000.0, allow_nan=False)
angle = st.floats(min_value=0.0, max_value=TAU - 1e-9, allow_nan=False)
sweep = st.floats(min_value=0.05, max_value=TAU, allow_nan=False)
unit = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


def _line(a, b):
    return Line(a, b)


lines = st.builds(_line, point, point).filter(lambda l: l.length() > 1e-3)
arcs = st.builds(lambda c, r, a, s, ccw: Arc(c, r, a, s if ccw else -s), point, radius, angle, sweep, st.booleans())
circles = st.builds(Circle, point, radius)
ellipses = st.builds(lambda c, m, ma, k, a, s, ccw: EllipseArc(c, Vec(m * math.cos(ma), m * math.sin(ma)), k, a, s if ccw else -s),
                     point, st.floats(min_value=0.1, max_value=2000.0), angle, st.floats(min_value=0.05, max_value=1.0),
                     angle, sweep, st.booleans())


def _spline(pts, degree, rational):
    n = len(pts)
    knots = [0.0] * (degree + 1) + [float(i) for i in range(1, n - degree)] + [float(n - degree)] * (degree + 1)
    weights = tuple(1.0 + (i % 3) * 0.5 for i in range(n)) if rational else ()
    return Spline(degree, tuple(knots), tuple(pts), weights)


splines = st.builds(_spline, st.lists(point, min_size=5, max_size=9), st.sampled_from([2, 3]), st.booleans())
curves = st.one_of(lines, arcs, circles, ellipses, splines)


def _polyline(pts, bulges, closed):
    b = tuple(bulges[i % len(bulges)] for i in range(len(pts)))
    return Polyline(tuple(pts), b, closed)


polylines = st.builds(_polyline, st.lists(point, min_size=2, max_size=8, unique=True),
                      st.lists(st.sampled_from([0.0, 0.0, 0.3, -0.4, 1.0, -1.0]), min_size=1, max_size=4), st.booleans())
