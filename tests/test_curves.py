# SPDX-License-Identifier: GPL-3.0-or-later
import math

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from draftworx.core.curves import Arc, Circle, EllipseArc, Line, PathRecorder, Polyline, Spline, bulge_arc
from draftworx.core.vec import TAU, Affine, Vec, ccw_sweep, near, norm_angle, polar, tol
from geom_strategies import angle, arcs, circles, coord, curves, ellipses, lines, point, polylines, splines, unit

affines = st.one_of(
    st.builds(Affine.translation, coord, coord),
    st.builds(lambda a, p: Affine.rotation(a, p), angle, point),
    st.builds(lambda k, p: Affine.scaling(k, None, p), st.floats(min_value=0.01, max_value=50.0), point),
    st.builds(lambda p, q: Affine.mirror(p, q), point, point.map(lambda v: v + Vec(1.0, 0.5))),
    st.builds(lambda sx, sy, a: Affine.scaling(sx, sy).then(Affine.rotation(a)),
              st.floats(min_value=0.1, max_value=20.0), st.floats(min_value=0.1, max_value=20.0), angle),
)


def scale_of(*pts):
    return tol(*[v for p in pts for v in p]) * 1e3


def bezier_points(rec: PathRecorder, n=8):
    """Sample what a curve wrote to a path, including the Bezier interiors."""
    out = []
    cur = None
    for op in rec.ops:
        if op[0] in "ML":
            cur = Vec(op[1], op[2])
            out.append(cur)
        elif op[0] == "C":
            p1, p2, p3 = Vec(op[1], op[2]), Vec(op[3], op[4]), Vec(op[5], op[6])
            for i in range(1, n + 1):
                t = i / n
                u = 1 - t
                out.append(cur * (u * u * u) + p1 * (3 * u * u * t) + p2 * (3 * u * t * t) + p3 * (t * t * t))
            cur = p3
    return out


class TestVec:
    def test_operators_are_vector_operators_not_tuple_ones(self):
        assert Vec(1, 2) + Vec(3, 4) == Vec(4, 6)
        assert Vec(1, 2) * 2 == Vec(2, 4)
        assert 2 * Vec(1, 2) == Vec(2, 4)
        assert -Vec(1, 2) == Vec(-1, -2)
        assert Vec(4, 2) / 2 == Vec(2, 1)

    def test_angles_land_in_range(self):
        assert norm_angle(-1e-20) == 0.0 or 0.0 <= norm_angle(-1e-20) < TAU
        for a in (-100.0, -TAU, 0.0, TAU, 7.0, 1e6):
            assert 0.0 <= norm_angle(a) < TAU
        assert ccw_sweep(1.0, 1.0) == TAU
        assert ccw_sweep(0.0, math.pi) == pytest.approx(math.pi)
        assert ccw_sweep(math.pi, 0.0) == pytest.approx(math.pi)

    def test_tolerance_grows_with_coordinates(self):
        assert tol(0.0) == 1e-9
        assert tol(1e9) == pytest.approx(1e-3)
        assert near((1e9, 0.0), (1e9 + 1e-4, 0.0))
        assert not near((1.0, 0.0), (1.0 + 1e-6, 0.0))


class TestAffine:
    @given(affines, point)
    def test_inverse_round_trips(self, m, p):
        back = m.inverse().apply(m.apply(p))
        assert back.distance(p) <= 1e-6 * (1 + p.length())

    @given(affines, affines, point)
    def test_then_composes_in_order(self, m, n, p):
        assert m.then(n).apply(p).distance(n.apply(m.apply(p))) <= 1e-6 * (1 + n.apply(m.apply(p)).length())

    def test_rotation_about_a_point(self):
        m = Affine.rotation(math.pi / 2, (10, 0))
        assert m.apply((11, 0)).distance((10, 1)) < 1e-12

    def test_mirror_about_a_line(self):
        m = Affine.mirror((0, 1), (1, 1))
        assert m.apply((5, 3)).distance((5, -1)) < 1e-12
        assert m.is_mirrored() and m.is_conformal()

    def test_a_stretch_is_not_conformal(self):
        assert not Affine.scaling(2, 3).is_conformal()
        assert Affine.scaling(2, 2).is_conformal()

    def test_a_flattening_transform_says_so(self):
        with pytest.raises(ValueError):
            Affine(1, 1, 1, 1).inverse()


class TestEveryCurve:
    @given(curves, unit)
    def test_tangent_matches_a_finite_difference(self, c, t):
        assume(0.01 < t < 0.99)
        if isinstance(c, Spline):
            # the tangent jumps at a knot where the curve has a corner; stay inside a span
            a = next(((p, q) for p, q in c._spans if p < t < q), None)
            assume(a is not None)
            h = min(t - a[0], a[1] - t, 1e-5) / 2
        else:
            h = 1e-6
        assume(h > 1e-9)
        d = c.tangent_at(t)
        f = (c.point_at(t + h) - c.point_at(t - h)) / (2 * h)
        assert d.distance(f) <= 1e-3 * (1.0 + d.length() + f.length())

    @given(curves, unit)
    def test_project_of_a_point_on_the_curve_finds_it(self, c, t):
        p = c.point_at(t)
        _, q = c.project(p)
        assert q.distance(p) <= scale_of(p)

    @given(curves, point)
    def test_project_is_on_the_curve_and_not_beaten_by_samples(self, c, p):
        t, q = c.project(p)
        assert c.point_at(t).distance(q) <= scale_of(q, p) * 10
        best = min(c.point_at(i / 64).distance(p) for i in range(65))
        assert q.distance(p) <= best * (1 + 1e-6) + scale_of(q, p) * 10

    @given(curves, unit)
    def test_bbox_holds_the_curve(self, c, t):
        p = c.point_at(t)
        x0, y0, x1, y1 = c.bbox()
        e = scale_of(p) + 1e-3 * max(x1 - x0, y1 - y0, 1e-9) * (1 if isinstance(c, Spline) else 0)
        assert x0 - e <= p[0] <= x1 + e and y0 - e <= p[1] <= y1 + e

    @given(curves, affines, unit)
    def test_transform_moves_every_point_the_same_way(self, c, m, t):
        moved = c.transformed(m)
        expected = m.apply(c.point_at(t))
        margin = 1e-7 * (1.0 + expected.length() + c.bbox()[2] - c.bbox()[0])
        if isinstance(moved, Circle):
            # a circle has no start of its own, so the point is on it rather than at the same parameter
            assert moved.c.distance(expected) == pytest.approx(moved.r, abs=margin)
        elif isinstance(c, Circle):
            assert moved.project(expected)[1].distance(expected) <= margin
        else:
            assert moved.point_at(t).distance(expected) <= margin

    @given(curves, unit)
    def test_reverse_runs_the_other_way(self, c, t):
        expected = c.point_at(1.0 - t)
        assert c.reversed().point_at(t).distance(expected) <= scale_of(expected) * 100

    @given(curves, unit, unit, unit)
    def test_sub_curve_is_a_part_of_the_curve(self, c, a, b, t):
        lo, hi = min(a, b), max(a, b)
        assume(hi - lo > 0.01)
        part = c.sub(lo, hi)
        expected = c.point_at(lo + (hi - lo) * t)
        assert part.point_at(t).distance(expected) <= 1e-6 * (1.0 + expected.length())

    @given(curves)
    def test_emit_follows_the_curve(self, c):
        rec = PathRecorder()
        c.emit(rec)
        assert rec.ops[0][0] == "M"
        size = max(c.bbox()[2] - c.bbox()[0], c.bbox()[3] - c.bbox()[1], 1e-9)
        sampled = bezier_points(rec)
        for q in sampled[::max(1, len(sampled) // 24)]:
            _, on = c.project(q)
            assert q.distance(on) <= 1e-4 * size + 1e-6

    @given(curves)
    def test_flatten_stays_within_the_chord_limit(self, c):
        size = max(c.bbox()[2] - c.bbox()[0], c.bbox()[3] - c.bbox()[1], 1e-9)
        chord = size * 1e-3
        pts = c.flatten(chord)
        assert pts[0].distance(c.point_at(0.0)) <= scale_of(pts[0])
        for a, b in zip(pts, pts[1:]):
            mid = a.midpoint(b)
            assert mid.distance(c.project(mid)[1]) <= chord * 1.5 + 1e-6


class TestLine:
    def test_ends_are_the_stored_numbers_exactly(self):
        a, b = Vec(0.1, 0.2), Vec(0.3, 0.7)
        assert Line(a, b).point_at(0.0) == a
        assert Line(a, b).point_at(1.0) == b

    def test_zero_length_line_is_harmless(self):
        line = Line(Vec(1, 1), Vec(1, 1))
        assert line.project((5, 5)) == (0.0, Vec(1, 1))
        assert line.length() == 0.0


class TestArc:
    def test_bbox_only_includes_quadrants_it_passes(self):
        box = Arc(Vec(0, 0), 10, 0.1, 0.5).bbox()
        assert box[2] < 10 and box[0] > 0 and box[1] > 0
        box = Arc(Vec(0, 0), 10, 0.0, math.pi).bbox()
        assert box[3] == pytest.approx(10) and box[0] == pytest.approx(-10) and box[1] == pytest.approx(0, abs=1e-9)

    def test_clockwise_arc_bbox(self):
        box = Arc(Vec(0, 0), 10, math.pi / 2, -math.pi).bbox()    # from the top, clockwise through the right, to the bottom
        assert box[2] == pytest.approx(10) and box[0] == pytest.approx(0, abs=1e-9)

    def test_ccw_keeps_the_same_points(self):
        a = Arc(Vec(1, 2), 5, 1.0, -2.0)
        b = a.ccw()
        assert b.sweep > 0
        assert a.start.distance(b.end) < 1e-12 and a.end.distance(b.start) < 1e-12

    def test_stretch_makes_an_ellipse(self):
        e = Arc(Vec(0, 0), 10, 0.0, TAU).transformed(Affine.scaling(2, 1))
        assert isinstance(e, EllipseArc)
        assert e.major.length() == pytest.approx(20)
        assert e.ratio == pytest.approx(0.5)

    def test_mirror_keeps_points_and_reverses_direction(self):
        a = Arc(Vec(0, 0), 5, 0.0, 1.0)
        m = a.transformed(Affine.mirror((0, 0), (0, 1)))
        assert m.sweep < 0
        assert m.start.distance((-5, 0)) < 1e-12

    def test_zero_sweep_is_harmless(self):
        a = Arc(Vec(0, 0), 5, 1.0, 0.0)
        assert a.bbox()[0] == pytest.approx(a.bbox()[2])
        a.project((9, 9))


class TestCircle:
    def test_stretch_makes_an_ellipse_with_axes_found(self):
        e = Circle(Vec(1, 1), 3).transformed(Affine.scaling(1, 2).then(Affine.rotation(0.7)))
        assert isinstance(e, EllipseArc)
        assert e.major.length() == pytest.approx(6)
        assert e.ratio == pytest.approx(0.5)

    def test_a_turn_keeps_it_a_circle(self):
        c = Circle(Vec(1, 1), 3).transformed(Affine.rotation(0.7))
        assert isinstance(c, Circle) and c.r == pytest.approx(3)


class TestEllipse:
    @given(ellipses, point)
    def test_nearest_point_is_perpendicular_when_inside_the_sweep(self, e, p):
        full = EllipseArc(e.c, e.major, e.ratio, 0.0, TAU)
        t, q = full.project(p)
        d = full.tangent_at(t)
        size = p.distance(q)
        assume(size > 1e-6 and d.length() > 1e-6)
        assert abs((p - q).unit().dot(d.unit())) <= 1e-5

    def test_quarter_length_of_a_circle_as_an_ellipse(self):
        e = EllipseArc(Vec(0, 0), Vec(3, 0), 1.0, 0.0, math.pi / 2)
        assert e.length() == pytest.approx(3 * math.pi / 2, rel=1e-9)

    def test_ramanujan_perimeter(self):
        e = EllipseArc(Vec(0, 0), Vec(10, 0), 0.5)
        a, b = 10, 5
        h = ((a - b) / (a + b)) ** 2
        approx = math.pi * (a + b) * (1 + 3 * h / (10 + math.sqrt(4 - 3 * h)))
        assert e.length() == pytest.approx(approx, rel=1e-5)

    def test_mirror_through_conjugate_axes_keeps_points(self):
        e = EllipseArc(Vec(1, 2), Vec(5, 1), 0.4, 0.3, 2.0)
        m = Affine.mirror((0, 0), (1, 3))
        moved = e.transformed(m)
        for t in (0.0, 0.2, 0.5, 1.0):
            assert moved.point_at(t).distance(m.apply(e.point_at(t))) < 1e-9


class TestSpline:
    def clamped(self):
        return Spline(3, (0, 0, 0, 0, 1, 2, 2, 2, 2), (Vec(0, 0), Vec(1, 3), Vec(4, 3), Vec(6, 0), Vec(9, 1)))

    def test_clamped_ends_are_the_end_control_points(self):
        s = self.clamped()
        assert s.point_at(0.0).distance((0, 0)) < 1e-12
        assert s.point_at(1.0).distance((9, 1)) < 1e-12

    def test_bad_splines_are_refused_with_a_reason(self):
        with pytest.raises(ValueError, match="control points"):
            Spline(3, (0,) * 4, (Vec(0, 0),))
        with pytest.raises(ValueError, match="knots"):
            Spline(1, (0, 1), (Vec(0, 0), Vec(1, 1)))
        with pytest.raises(ValueError, match="decrease"):
            Spline(1, (0, 2, 1, 3), (Vec(0, 0), Vec(1, 1)))
        with pytest.raises(ValueError, match="span"):
            Spline(1, (0, 0, 0, 0), (Vec(0, 0), Vec(1, 1)))
        with pytest.raises(ValueError, match="weight"):
            Spline(1, (0, 0, 1, 1), (Vec(0, 0), Vec(1, 1)), (1.0,))
        with pytest.raises(ValueError, match="positive"):
            Spline(1, (0, 0, 1, 1), (Vec(0, 0), Vec(1, 1)), (1.0, 0.0))

    def test_rational_quadratic_is_a_true_circle(self):
        k = math.sqrt(0.5)
        s = Spline(2, (0, 0, 0, 1, 1, 1), (Vec(1, 0), Vec(1, 1), Vec(0, 1)), (1.0, k, 1.0))
        for i in range(21):
            assert s.point_at(i / 20).length() == pytest.approx(1.0, abs=1e-12)

    def test_cubic_emit_is_exact_bezier(self):
        s = self.clamped()
        rec = PathRecorder()
        s.emit(rec)
        for q in bezier_points(rec, 16):
            assert q.distance(s.project(q)[1]) < 1e-7

    def test_length_of_a_straight_spline(self):
        s = Spline(3, (0, 0, 0, 0, 1, 1, 1, 1), (Vec(0, 0), Vec(1, 0), Vec(2, 0), Vec(3, 0)))
        assert s.length() == pytest.approx(3.0)

    def test_unclamped_knots_use_their_domain(self):
        s = Spline(3, (0, 1, 2, 3, 4, 5, 6, 7, 8), (Vec(0, 0), Vec(1, 3), Vec(4, 3), Vec(6, 0), Vec(9, 1)))
        assert s.domain == (3, 5)
        expected = (Vec(0, 0) + Vec(1, 3) * 4 + Vec(4, 3)) / 6
        assert s.point_at(0.0).distance(expected) < 1e-12

    @given(splines)
    def test_a_spline_transformed_keeps_its_weights(self, s):
        assert s.transformed(Affine.translation(1, 1)).weights == s.weights


class TestPolyline:
    def test_bulge_one_is_a_half_circle(self):
        a = bulge_arc(Vec(0, 0), Vec(2, 0), 1.0)
        assert a.r == pytest.approx(1.0) and a.c.distance((1, 0)) < 1e-12
        assert a.sweep == pytest.approx(math.pi)
        assert a.point_at(0.5).distance((1, -1)) < 1e-12        # counter-clockwise from left to right goes under

    def test_negative_bulge_goes_the_other_way(self):
        a = bulge_arc(Vec(0, 0), Vec(2, 0), -1.0)
        assert a.point_at(0.5).distance((1, 1)) < 1e-12

    @given(st.floats(min_value=0.01, max_value=10.0), point, point)
    def test_bulge_arc_meets_both_ends(self, b, p, q):
        assume(p.distance(q) > 1e-3)
        for k in (b, -b):
            a = bulge_arc(p, q, k)
            e = 1e-9 * (1 + a.r + p.length() + q.length())
            assert a.start.distance(p) <= e and a.end.distance(q) <= e

    def test_area_of_a_square_both_ways(self):
        pts = (Vec(0, 0), Vec(10, 0), Vec(10, 10), Vec(0, 10))
        assert Polyline(pts, closed=True).signed_area() == pytest.approx(100)
        assert Polyline(tuple(reversed(pts)), closed=True).signed_area() == pytest.approx(-100)

    def test_area_of_a_circle_from_two_half_circles(self):
        p = Polyline((Vec(-5, 0), Vec(5, 0)), (1.0, 1.0), True)
        assert p.signed_area() == pytest.approx(math.pi * 25)
        assert p.length() == pytest.approx(TAU * 5)

    def test_area_with_an_arc_cut_out(self):
        # a 10 x 10 square whose top edge bows inwards by a half circle
        p = Polyline((Vec(0, 0), Vec(10, 0), Vec(10, 10), Vec(0, 10)), (0, 0, -1.0, 0), True)
        assert p.signed_area() == pytest.approx(100 - math.pi * 25 / 2)

    def test_centroid_of_a_rectangle(self):
        p = Polyline((Vec(2, 2), Vec(12, 2), Vec(12, 6), Vec(2, 6)), closed=True)
        assert p.centroid().distance((7, 4)) < 1e-12

    def test_centroid_of_a_half_disc(self):
        p = Polyline((Vec(-5, 0), Vec(5, 0)), (0.0, 1.0), True)     # flat side down, dome up
        assert p.centroid().distance((0, 4 * 5 / (3 * math.pi))) < 1e-9

    def test_zero_area_has_no_centroid(self):
        assert Polyline((Vec(0, 0), Vec(5, 0)), closed=True).centroid() is None

    @given(polylines)
    def test_a_closed_polyline_area_matches_its_chords(self, p):
        assume(p.closed)
        assume(all(s.length() > 1e-3 for s in p.segments))
        fine = Polyline(tuple(p.flatten(p.bbox()[2] * 0 + 1e-3 * max(p.bbox()[2] - p.bbox()[0], p.bbox()[3] - p.bbox()[1], 1e-3)))[:-1], closed=True)
        size = max(p.bbox()[2] - p.bbox()[0], p.bbox()[3] - p.bbox()[1])
        assert p.signed_area() == pytest.approx(fine.signed_area(), abs=max(1e-6, 1e-2 * size * size))

    @given(polylines, unit)
    def test_reverse(self, p, t):
        assume(all(s.length() > 1e-6 for s in p.segments))
        r = p.reversed()
        assert r.length() == pytest.approx(p.length(), rel=1e-9)
        assert r.signed_area() == pytest.approx(-p.signed_area(), abs=1e-6 * max(1.0, abs(p.signed_area())))
        if not p.closed:
            assert r.point_at(t).distance(p.point_at(1.0 - t)) < 1e-6 * (1 + p.length())

    @given(polylines, affines, unit)
    def test_transform(self, p, m, t):
        assume(all(s.length() > 1e-6 for s in p.segments))
        moved = p.transformed(m)
        if m.is_conformal():
            q = m.apply(p.point_at(t))
            assert moved.point_at(t).distance(q) <= 1e-6 * (1 + q.length())
        else:
            # arcs became chords, so parameters no longer line up; the shape still does
            q = m.apply(p.point_at(t))
            size = max(moved.bbox()[2] - moved.bbox()[0], moved.bbox()[3] - moved.bbox()[1], 1e-6)
            assert q.distance(moved.project(q)[1]) <= 1e-3 * size + 1e-6

    @given(polylines)
    def test_emit_passes_every_vertex(self, p):
        rec = PathRecorder()
        p.emit(rec)
        pts = bezier_points(rec, 2)
        for v in p.pts:
            assert min(v.distance(q) for q in pts) <= 1e-6 * (1 + v.length())

    def test_invalid_are_refused(self):
        with pytest.raises(ValueError):
            Polyline(())
        with pytest.raises(ValueError):
            Polyline((Vec(0, 0), Vec(1, 1)), (0.0,))
        with pytest.raises(ValueError):
            Polyline((Vec(0, 0), Vec(1, 1)), widths=((1.0, 1.0),))

    def test_a_single_point_polyline_is_harmless(self):
        p = Polyline((Vec(3, 4),))
        assert p.segments == ()
        assert p.bbox() == (3, 4, 3, 4)
        assert p.project((0, 0)) == (0.0, Vec(3, 4))
        assert p.point_at(0.5) == Vec(3, 4)
        assert p.length() == 0.0

    def test_vertex_on_a_repeated_point_stays_straight(self):
        p = Polyline((Vec(0, 0), Vec(0, 0), Vec(1, 0)), (0.5, 0.5, 0.0))
        assert isinstance(p.segments[0], Line)


@given(arcs, circles, lines)
def test_strategies_make_valid_geometry(a, c, l):
    assert a.length() > 0 and c.length() > 0 and l.length() > 0


def test_polar():
    assert polar((1, 1), 2, 0).distance((3, 1)) < 1e-12
