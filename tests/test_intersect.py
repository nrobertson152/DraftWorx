# SPDX-License-Identifier: GPL-3.0-or-later
import math

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from draftworx.core.curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from draftworx.core.intersect import intersect
from draftworx.core.vec import TAU, Vec, tol
from geom_strategies import angle, arcs, circles, curves, ellipses, lines, point, radius, sweep, unit


def pts(hits):
    return sorted((round(h.point[0], 7), round(h.point[1], 7)) for h in hits)


def tolerance(*curves_):
    v = []
    for c in curves_:
        v.extend(c.bbox())
    return tol(*v) * 1e3


class TestLines:
    def test_cross(self):
        h = intersect(Line(Vec(0, 0), Vec(10, 10)), Line(Vec(0, 10), Vec(10, 0)))
        assert len(h) == 1
        assert h[0].point.distance((5, 5)) < 1e-12
        assert h[0].ta == pytest.approx(0.5) and h[0].tb == pytest.approx(0.5)

    def test_miss(self):
        assert intersect(Line(Vec(0, 0), Vec(1, 0)), Line(Vec(2, -1), Vec(2, 1))) == []

    def test_extended_reaches(self):
        h = intersect(Line(Vec(0, 0), Vec(1, 0)), Line(Vec(2, -1), Vec(2, 1)), extend_a=True)
        assert h[0].point.distance((2, 0)) < 1e-12
        assert h[0].ta == pytest.approx(2.0)
        assert intersect(Line(Vec(0, 0), Vec(1, 0)), Line(Vec(2, 1), Vec(2, 3)), extend_a=True) == []
        assert len(intersect(Line(Vec(0, 0), Vec(1, 0)), Line(Vec(2, 1), Vec(2, 3)), True, True)) == 1

    def test_a_touching_end_gives_the_stored_end_exactly(self):
        end = Vec(0.1 + 0.2, 0.7)
        h = intersect(Line(Vec(0, 0), Vec(1, 0.7 / 0.3)), Line(end, Vec(5, 5)))
        if h:
            assert h[0].point == end or h[0].point.distance(end) < 1e-9
        v = intersect(Line(Vec(0, 0), Vec(1, 1)), Line(Vec(1, 1), Vec(2, 0)))
        assert v[0].point == Vec(1, 1)

    def test_parallel_never_meet(self):
        assert intersect(Line(Vec(0, 0), Vec(1, 0)), Line(Vec(0, 1), Vec(1, 1)), True, True) == []

    def test_overlap_gives_the_shared_ends(self):
        h = intersect(Line(Vec(0, 0), Vec(10, 0)), Line(Vec(4, 0), Vec(20, 0)))
        assert pts(h) == [(4, 0), (10, 0)]

    def test_end_to_end_collinear_touch_once(self):
        h = intersect(Line(Vec(0, 0), Vec(10, 0)), Line(Vec(10, 0), Vec(20, 0)))
        assert pts(h) == [(10, 0)]

    def test_collinear_with_a_gap_miss(self):
        assert intersect(Line(Vec(0, 0), Vec(1, 0)), Line(Vec(3, 0), Vec(4, 0))) == []

    def test_zero_length(self):
        assert pts(intersect(Line(Vec(5, 0), Vec(5, 0)), Line(Vec(0, 0), Vec(10, 0)))) == [(5, 0)]
        assert pts(intersect(Line(Vec(0, 0), Vec(10, 0)), Line(Vec(5, 0), Vec(5, 0)))) == [(5, 0)]
        assert intersect(Line(Vec(5, 1), Vec(5, 1)), Line(Vec(0, 0), Vec(10, 0))) == []
        assert pts(intersect(Line(Vec(5, 0), Vec(5, 0)), Line(Vec(5, 0), Vec(5, 0)))) == [(5, 0)]


class TestLineAndCircle:
    def test_two_points(self):
        assert pts(intersect(Line(Vec(-10, 0), Vec(10, 0)), Circle(Vec(0, 0), 5))) == [(-5, 0), (5, 0)]

    def test_tangent_is_one_point(self):
        h = intersect(Line(Vec(-10, 5), Vec(10, 5)), Circle(Vec(0, 0), 5))
        assert pts(h) == [(0, 5)]

    def test_nearly_tangent_is_still_one_point(self):
        h = intersect(Line(Vec(-10, 5 - 1e-12), Vec(10, 5 - 1e-12)), Circle(Vec(0, 0), 5))
        assert len(h) == 1

    def test_miss(self):
        assert intersect(Line(Vec(-10, 6), Vec(10, 6)), Circle(Vec(0, 0), 5)) == []

    def test_inside_the_circle_does_not_reach_unless_extended(self):
        l = Line(Vec(-1, 0), Vec(1, 0))
        assert intersect(l, Circle(Vec(0, 0), 5)) == []
        assert pts(intersect(l, Circle(Vec(0, 0), 5), extend_a=True)) == [(-5, 0), (5, 0)]

    def test_arc_limits_it(self):
        a = Arc(Vec(0, 0), 5, 0.0, math.pi / 2)       # first quadrant
        assert pts(intersect(Line(Vec(-10, 3), Vec(10, 3)), a)) == [(4, 3)]
        assert intersect(Line(Vec(-10, -3), Vec(10, -3)), a) == []

    def test_arc_extended_finds_the_rest(self):
        a = Arc(Vec(0, 0), 5, 0.0, math.pi / 2)
        assert pts(intersect(Line(Vec(-10, -3), Vec(10, -3)), a, extend_b=True)) == [(-4, -3), (4, -3)]

    def test_arc_end_counts(self):
        a = Arc(Vec(0, 0), 5, 0.0, math.pi / 2)
        h = intersect(Line(Vec(5, -1), Vec(5, 1)), a)
        assert pts(h) == [(5, 0)]
        assert h[0].tb == 0.0

    def test_clockwise_arc(self):
        a = Arc(Vec(0, 0), 5, math.pi / 2, -math.pi / 2)      # top to right, clockwise
        h = intersect(Line(Vec(-10, 3), Vec(10, 3)), a)
        assert pts(h) == [(4, 3)]
        assert 0 < h[0].tb < 1

    def test_order_of_arguments_gives_the_same_points(self):
        l, c = Line(Vec(-10, 0), Vec(10, 0)), Circle(Vec(0, 0), 5)
        assert pts(intersect(l, c)) == pts(intersect(c, l))
        for h in intersect(c, l):
            assert c.point_at(h.ta).distance(h.point) < 1e-9
            assert l.point_at(h.tb).distance(h.point) < 1e-9


class TestCircles:
    def test_two_points(self):
        assert pts(intersect(Circle(Vec(0, 0), 5), Circle(Vec(6, 0), 5))) == [(3, -4), (3, 4)]

    def test_outside_tangent(self):
        assert pts(intersect(Circle(Vec(0, 0), 5), Circle(Vec(8, 0), 3))) == [(5, 0)]

    def test_inside_tangent(self):
        assert pts(intersect(Circle(Vec(0, 0), 5), Circle(Vec(2, 0), 3))) == [(5, 0)]

    def test_apart_and_nested(self):
        assert intersect(Circle(Vec(0, 0), 1), Circle(Vec(5, 0), 1)) == []
        assert intersect(Circle(Vec(0, 0), 5), Circle(Vec(1, 0), 1)) == []

    def test_concentric_and_same(self):
        assert intersect(Circle(Vec(0, 0), 5), Circle(Vec(0, 0), 4)) == []
        assert intersect(Circle(Vec(0, 0), 5), Circle(Vec(0, 0), 5)) == []

    def test_arcs_of_the_same_circle_share_their_ends(self):
        a = Arc(Vec(0, 0), 5, 0.0, 2.0)
        b = Arc(Vec(0, 0), 5, 1.0, 2.0)
        h = intersect(a, b)
        assert len(h) == 2
        assert sorted(h[i].point.distance(a.end) < 1e-9 or h[i].point.distance(b.start) < 1e-9 for i in range(2)) == [True, True]

    def test_arc_limits(self):
        a = Arc(Vec(0, 0), 5, 0.0, math.pi / 2)
        assert pts(intersect(a, Circle(Vec(6, 0), 5))) == [(3, 4)]


class TestEllipses:
    e = EllipseArc(Vec(0, 0), Vec(10, 0), 0.5)

    def test_line_through_center(self):
        assert pts(intersect(Line(Vec(-20, 0), Vec(20, 0)), self.e)) == [(-10, 0), (10, 0)]
        assert pts(intersect(Line(Vec(0, -20), Vec(0, 20)), self.e)) == [(0, -5), (0, 5)]

    def test_line_tangent(self):
        assert pts(intersect(Line(Vec(-20, 5), Vec(20, 5)), self.e)) == [(0, 5)]

    def test_line_miss(self):
        assert intersect(Line(Vec(-20, 6), Vec(20, 6)), self.e) == []

    def test_turned_ellipse(self):
        e = EllipseArc(Vec(1, 1), Vec(0, 10), 0.5)    # major axis up
        assert pts(intersect(Line(Vec(1, -50), Vec(1, 50)), e)) == [(1, -9), (1, 11)]

    def test_part_of_an_ellipse(self):
        e = EllipseArc(Vec(0, 0), Vec(10, 0), 0.5, 0.0, math.pi / 2)
        assert pts(intersect(Line(Vec(-20, 0.0), Vec(20, 0.0)), e)) == [(10, 0)]

    def test_part_extended(self):
        e = EllipseArc(Vec(0, 0), Vec(10, 0), 0.5, 0.0, math.pi / 2)
        assert pts(intersect(Line(Vec(-20, 0.0), Vec(20, 0.0)), e, extend_b=True)) == [(-10, 0), (10, 0)]

    def test_circle_through_axis_ends(self):
        assert pts(intersect(self.e, Circle(Vec(0, 0), 10))) == [(-10, 0), (10, 0)]
        assert pts(intersect(self.e, Circle(Vec(0, 0), 5))) == [(0, -5), (0, 5)]

    def test_circle_crossing_at_four_points(self):
        hits = intersect(self.e, Circle(Vec(0, 0), 7))
        assert len(hits) == 4
        for h in hits:
            assert h.point.length() == pytest.approx(7, abs=1e-8)
            x, y = h.point
            assert (x / 10) ** 2 + (y / 5) ** 2 == pytest.approx(1, abs=1e-8)

    def test_two_ellipses_cross_at_four_points(self):
        other = EllipseArc(Vec(0, 0), Vec(0, 10), 0.5)
        hits = intersect(self.e, other)
        assert len(hits) == 4

    def test_tangent_ellipses(self):
        other = EllipseArc(Vec(20, 0), Vec(10, 0), 0.5)
        hits = intersect(self.e, other)
        assert len(hits) == 1
        assert hits[0].point.distance((10, 0)) < 1e-5

    def test_apart(self):
        assert intersect(self.e, Circle(Vec(100, 100), 1)) == []

    def test_same_ellipse_is_not_a_hang(self):
        intersect(self.e, EllipseArc(Vec(0, 0), Vec(10, 0), 0.5))


class TestSplines:
    s = Spline(3, (0, 0, 0, 0, 1, 2, 2, 2, 2), (Vec(0, 0), Vec(1, 3), Vec(4, 3), Vec(6, 0), Vec(9, 1)))

    def test_crossed_by_a_line(self):
        hits = intersect(Line(Vec(4, -5), Vec(4, 10)), self.s)
        assert len(hits) == 1
        assert hits[0].point[0] == pytest.approx(4.0, abs=1e-9)
        assert self.s.point_at(hits[0].tb).distance(hits[0].point) < 1e-9

    def test_a_short_line_can_be_extended(self):
        short = Line(Vec(4, -5), Vec(4, -4))
        assert intersect(short, self.s) == []
        hits = intersect(short, self.s, extend_a=True)
        assert len(hits) == 1 and hits[0].ta > 1

    def test_crossed_by_a_circle(self):
        hits = intersect(self.s, Circle(Vec(4, 2), 1.0))
        assert hits
        for h in hits:
            assert h.point.distance((4, 2)) == pytest.approx(1.0, abs=1e-8)

    def test_two_splines(self):
        other = self.s.transformed(__import__("draftworx.core.vec", fromlist=["Affine"]).Affine.mirror((0, 2), (9, 2)))
        hits = intersect(self.s, other)
        assert hits
        for h in hits:
            assert self.s.point_at(h.ta).distance(other.point_at(h.tb)) < 1e-8


class TestPolylines:
    def test_crossed_at_each_segment(self):
        p = Polyline((Vec(0, 0), Vec(10, 0), Vec(10, 10)))
        hits = intersect(p, Line(Vec(5, -1), Vec(5, 1)))
        assert pts(hits) == [(5, 0)]
        assert hits[0].ta == pytest.approx(0.25)

    def test_a_crossing_exactly_at_a_vertex_is_one_hit(self):
        p = Polyline((Vec(0, 0), Vec(10, 0), Vec(20, 0)))
        assert pts(intersect(p, Line(Vec(10, -1), Vec(10, 1)))) == [(10, 0)]

    def test_arc_segment(self):
        p = Polyline((Vec(-5, 0), Vec(5, 0)), (1.0, 0.0))    # half circle under the chord
        assert pts(intersect(p, Line(Vec(0, -10), Vec(0, -1)))) == [(0, -5)]

    def test_argument_order(self):
        p = Polyline((Vec(0, 0), Vec(10, 0)))
        hits = intersect(Line(Vec(5, -1), Vec(5, 1)), p)
        assert hits[0].tb == pytest.approx(0.5) and hits[0].ta == pytest.approx(0.5)


def through(c1, c2, target):
    """A hit must be at the point we built both curves to pass through."""
    hits = intersect(c1, c2)
    assert hits, f"nothing found between {c1} and {c2}"
    best = min(h.point.distance(target) for h in hits)
    assert best <= tolerance(c1, c2)
    for h in hits:
        assert c1.point_at(h.ta).distance(h.point) <= tolerance(c1, c2)
        assert c2.point_at(h.tb).distance(h.point) <= tolerance(c1, c2)


def curve_through(kind, p, a, r, t):
    """A curve of this kind that passes through p, a fraction t of the way along it."""
    if kind == "line":
        d = Vec(math.cos(a), math.sin(a)) * r
        return Line(p - d * t, p + d * (1 - t))
    if kind == "arc":
        sw = 1.0 + 4.0 * t
        return Arc(p - Vec(math.cos(a), math.sin(a)) * r, r, a - sw * t, sw)
    if kind == "circle":
        return Circle(p - Vec(math.cos(a), math.sin(a)) * r, r)
    if kind == "ellipse":
        e = EllipseArc(Vec(0.0, 0.0), Vec(math.cos(a), math.sin(a)) * r, 0.6)
        return EllipseArc(p - e.at_param(1.1), e.major, e.ratio)
    knots = (0, 0, 0, 0, 1, 2, 2, 2, 2)
    base = (Vec(-1, -0.2), Vec(-0.5, 0.5), Vec(0, 0), Vec(0.5, -0.5), Vec(1, 0.2))
    c, s = math.cos(a), math.sin(a)
    ctrl = tuple(p + Vec(q[0] * c - q[1] * s, q[0] * s + q[1] * c) * r for q in base)
    spline = Spline(3, tuple(float(k) for k in knots), ctrl)
    return spline.transformed(__import__("draftworx.core.vec", fromlist=["Affine"]).Affine.translation(*(p - spline.point_at(0.5))))


KINDS = ["line", "arc", "circle", "ellipse", "spline"]


@given(st.sampled_from(KINDS), st.sampled_from(KINDS), point, angle, radius, angle, radius, st.floats(min_value=0.1, max_value=0.9))
def test_curves_built_through_a_point_meet_there(k1, k2, p, a1, r1, a2, r2, t):
    assume(0.5 <= r1 / r2 <= 2.0)       # very different sizes are covered by the closed-form tests
    assume(abs(math.sin(a1 - a2)) > 0.2)
    c1, c2 = curve_through(k1, p, a1, r1, t), curve_through(k2, p, a2, r2, t)
    through(c1, c2, p)


@given(curves, curves)
def test_every_hit_is_on_both_curves_and_in_range(a, b):
    hits = intersect(a, b)
    e = tolerance(a, b)
    for h in hits:
        assert 0.0 <= h.ta <= 1.0 and 0.0 <= h.tb <= 1.0
        assert a.point_at(h.ta).distance(h.point) <= e
        assert b.point_at(h.tb).distance(h.point) <= e


@given(curves, curves)
def test_swapping_arguments_finds_the_same_points(a, b):
    # A real crossing is found from either side at the same place. Where curves run along each other or just touch, the
    # answer may legitimately be anywhere along the shared stretch; the tangency and overlap tests cover those.
    p, q = intersect(a, b), intersect(b, a)
    e = tolerance(a, b)
    for h in p:
        da, db = a.tangent_at(h.ta), b.tangent_at(h.tb)
        if da.length() == 0.0 or db.length() == 0.0 or abs(da.unit().cross(db.unit())) < 1e-2:
            continue
        assert q and min(h.point.distance(g.point) for g in q) <= e


@given(lines, arcs)
def test_a_line_and_arc_agree_with_sampling(l, a):
    """If the sampled curves clearly cross, a hit must be reported."""
    n = 200
    side = [l.tangent_at().cross(a.point_at(i / n) - l.a) for i in range(n + 1)]
    changed = [i for i in range(n) if side[i] * side[i + 1] < 0]
    for i in changed:
        q = a.point_at((i + 0.5) / n)
        t = l.param_of(q)
        if 0.02 < t < 0.98:
            assert intersect(l, a), "the arc clearly crosses the line"


@given(ellipses, circles)
def test_numeric_hits_are_true(e, c):
    for h in intersect(e, c):
        assert h.point.distance(c.c) == pytest.approx(c.r, abs=tolerance(e, c))


def test_large_coordinates_stay_exact():
    base = Vec(1e8, 2e8)
    h = intersect(Line(base, base + Vec(10, 10)), Line(base + Vec(0, 10), base + Vec(10, 0)))
    assert h[0].point.distance(base + Vec(5, 5)) < 1e-6


def test_tiny_geometry_still_found():
    h = intersect(Line(Vec(0, 0), Vec(1e-5, 1e-5)), Line(Vec(0, 1e-5), Vec(1e-5, 0)))
    assert h and h[0].point.distance((5e-6, 5e-6)) < 1e-9


def test_full_sweep_arc_has_no_seam():
    a = Arc(Vec(0, 0), 5, 0.0, TAU)
    assert pts(intersect(Line(Vec(4, 0), Vec(6, 0)), a)) == [(5, 0)]


__test__ = {"sweep": sweep, "unit": unit}
del __test__
