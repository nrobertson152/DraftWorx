# SPDX-License-Identifier: GPL-3.0-or-later
"""The behaviour docs/SNAPPING.md promises."""
import math
import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from draftworx.core.curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from draftworx.core.model import BlockDef, Document, Entity, Layer, Style
from draftworx.core.shapes import Insert, Node, Text
from draftworx.core.snap import (ALL_KINDS, Candidate, Guide, Kind, Lock, SnapEngine, SnapQuery, Tracked, Tracker, tidy, tidy_step)
from draftworx.core.vec import Vec
from geom_strategies import lines


def line(x0, y0, x1, y1):
    return Line(Vec(float(x0), float(y0)), Vec(float(x1), float(y1)))


def world(*shapes, style=None):
    d = Document()
    with d.transaction("x") as t:
        ids = [t.add(s, style) for s in shapes]
    return d, SnapEngine(d), ids


def query(x, y, px=1.0, **kw):
    return SnapQuery(Vec(float(x), float(y)), px, **kw)


def best(engine, x, y, px=1.0, previous=None, **kw):
    return engine.pick(query(x, y, px, **kw), previous)


def kinds_at(engine, x, y, px=1.0, **kw):
    return [c.kind for c in engine.candidates(query(x, y, px, **kw))]


class TestRealPoints:
    def test_endpoint_at_the_cursor(self):
        _, e, (a,) = world(line(0, 0, 100, 0))
        r = best(e, 2, 3)
        assert r.point == Vec(0, 0)
        assert r.candidate.kind == Kind.ENDPOINT
        assert r.candidate.sources == (a,)
        assert r.candidate.label == "Endpoint"

    def test_midpoint(self):
        _, e, _ = world(line(0, 0, 100, 0))
        r = best(e, 51, 2)
        assert r.point == Vec(50, 0) and r.candidate.kind == Kind.MIDPOINT

    def test_a_clearly_closer_midpoint_beats_an_endpoint(self):
        _, e, _ = world(line(0, 0, 20, 0))
        r = best(e, 9, 0)       # 1 px from the midpoint, 9 from the end
        assert r.candidate.kind == Kind.MIDPOINT

    def test_when_about_equally_close_the_endpoint_wins(self):
        _, e, _ = world(line(0, 0, 20, 0))
        r = best(e, 5.5, 0)       # 5.5 from the end, 4.5 from the middle
        assert r.candidate.kind == Kind.ENDPOINT

    def test_real_points_beat_on_the_line(self):
        _, e, _ = world(line(0, 0, 100, 0))
        r = best(e, 13, 0)      # the cursor is exactly on the line, and 13 px from its end
        assert r.candidate.kind == Kind.ENDPOINT

    def test_on_the_line_when_nothing_real_is_near(self):
        _, e, _ = world(line(0, 0, 100, 0))
        r = best(e, 30, 2)
        assert r.candidate.kind == Kind.ON
        assert r.point == Vec(30, 0)
        assert r.candidate.label == "On"

    def test_nothing_near_is_free(self):
        _, e, _ = world(line(0, 0, 100, 0))
        r = best(e, 30, 50)
        assert r.candidate is None and r.point == Vec(30, 50)

    def test_center_without_touching_the_circle(self):
        _, e, _ = world(Circle(Vec(0, 0), 100))
        r = best(e, 3, 3)
        assert r.candidate.kind == Kind.CENTER and r.point == Vec(0, 0)

    def test_center_of_an_arc_that_is_far_from_it(self):
        _, e, _ = world(Arc(Vec(0, 0), 1000, 0.1, 0.2))
        r = best(e, 3, 3)
        assert r.candidate.kind == Kind.CENTER and r.point == Vec(0, 0)

    def test_quadrants(self):
        _, e, _ = world(Circle(Vec(0, 0), 100))
        for x, y, exp in ((100, 5, (100, 0)), (5, 100, (0, 100)), (-100, 5, (-100, 0)), (5, -100, (0, -100))):
            r = best(e, x, y)
            assert r.candidate.kind == Kind.QUADRANT and r.point == Vec(*exp)

    def test_an_arc_only_offers_quadrants_it_passes(self):
        arc = Arc(Vec(0, 0), 100, 0.2, 0.5)
        _, e, _ = world(arc)
        assert Kind.QUADRANT not in kinds_at(e, 100, 0)
        _, e, _ = world(Arc(Vec(0, 0), 100, -0.2, 0.5))
        assert best(e, 100, 2).candidate.kind == Kind.QUADRANT

    def test_arc_ends_and_middle(self):
        arc = Arc(Vec(0, 0), 100, 0.0, math.pi / 2)
        _, e, _ = world(arc)
        m = arc.point_at(0.5)
        r = best(e, m[0] + 1, m[1])
        assert r.candidate.kind == Kind.MIDPOINT and r.point.distance(m) < 1e-12
        s = best(e, 1, 100)
        assert s.candidate.kind in (Kind.ENDPOINT, Kind.QUADRANT)

    def test_a_circle_has_no_endpoints(self):
        _, e, _ = world(Circle(Vec(0, 0), 100))
        assert Kind.ENDPOINT not in kinds_at(e, 100, 0)
        assert Kind.MIDPOINT not in kinds_at(e, 100, 0)

    def test_ellipse_features(self):
        el = EllipseArc(Vec(0, 0), Vec(100, 0), 0.5)
        _, e, _ = world(el)
        assert best(e, 99, 1).point.distance((100, 0)) < 1e-9
        assert best(e, 1, 49).point.distance((0, 50)) < 1e-9
        assert best(e, 2, 2).candidate.kind == Kind.CENTER

    def test_part_of_an_ellipse(self):
        el = EllipseArc(Vec(0, 0), Vec(100, 0), 0.5, 0.3, 1.0)
        _, e, _ = world(el)
        q = el.start
        r = best(e, q[0] + 1, q[1])
        assert r.candidate.kind == Kind.ENDPOINT and r.point.distance(q) < 1e-9

    def test_spline_ends_middle_and_fit_points(self):
        s = Spline(3, (0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0), (Vec(0, 0), Vec(100, 300), Vec(200, -300), Vec(300, 0)), fit=(Vec(0, 0), Vec(300, 0)))
        _, e, _ = world(s)
        assert best(e, 2, 2).point.distance((0, 0)) < 1e-9
        assert best(e, 298, 2).point.distance((300, 0)) < 1e-9
        m = s.point_at(0.5)
        assert best(e, m[0], m[1] + 1).candidate.kind == Kind.MIDPOINT

    def test_a_point_object(self):
        _, e, _ = world(Node(Vec(5, 5)))
        r = best(e, 6, 6)
        assert r.candidate.kind == Kind.NODE and r.point == Vec(5, 5)

    def test_text_offers_where_it_is_anchored(self):
        _, e, _ = world(Text(Vec(5, 5), 10.0, "Hello"))
        r = best(e, 6, 6)
        assert r.candidate.kind == Kind.INSERTION and r.point == Vec(5, 5)

    def test_polyline_vertices_are_the_stored_numbers(self):
        a = Vec(1 / 3, 2 / 3)
        b = Vec(100 + 1 / 7, 3 / 11)
        c = Vec(100.5, 100.25)
        _, e, _ = world(Polyline((a, b, c), (0.5, 0.0, 0.0)))
        assert best(e, b[0] + 1, b[1] + 1).point == b
        assert best(e, c[0] + 1, c[1]).point == c

    def test_polyline_segment_midpoints_and_arc_centres(self):
        p = Polyline((Vec(0, 0), Vec(100, 0), Vec(100, 100)), (0.0, 1.0, 0.0))
        _, e, _ = world(p)
        assert best(e, 50, 2).point == Vec(50, 0)
        assert best(e, 100, 50).candidate.kind == Kind.CENTER

    def test_center_of_a_closed_polyline(self):
        p = Polyline((Vec(0, 0), Vec(100, 0), Vec(100, 100), Vec(0, 100)), closed=True)
        _, e, _ = world(p)
        r = best(e, 52, 49)
        assert r.candidate.kind == Kind.GEOMETRIC_CENTER and r.point.distance((50, 50)) < 1e-9

    def test_an_open_polyline_has_no_center(self):
        p = Polyline((Vec(0, 0), Vec(100, 0), Vec(100, 100), Vec(0, 100)))
        _, e, _ = world(p)
        assert best(e, 50, 50).candidate is None


class TestIntersections:
    def test_two_lines(self):
        _, e, ids = world(line(0, 0, 100, 100), line(0, 100, 100, 0))
        r = best(e, 52, 49)
        assert r.candidate.kind == Kind.INTERSECTION
        assert r.point.distance((50, 50)) < 1e-12
        assert set(r.candidate.sources) == set(ids)

    def test_line_and_circle(self):
        _, e, _ = world(line(-200, 0, 200, 0), Circle(Vec(0, 0), 100))
        r = best(e, 101, 1)
        assert r.point.distance((100, 0)) < 1e-12

    def test_a_shared_end_is_the_stored_number_not_a_recomputed_one(self):
        a = Vec(1 / 3, 1 / 7)
        _, e, _ = world(Line(a, Vec(100.0, 100.0)), Line(a, Vec(-100.0, 50.0)))
        r = best(e, a[0] + 1, a[1])
        assert r.point == a
        assert r.candidate.kind == Kind.ENDPOINT

    def test_a_crossing_on_a_polyline(self):
        _, e, _ = world(Polyline((Vec(0, 0), Vec(100, 0), Vec(100, 100))), line(50, -50, 50, 50))
        r = best(e, 51, 1)
        assert r.candidate.kind == Kind.INTERSECTION and r.point.distance((50, 0)) < 1e-12

    def test_a_polyline_crossing_itself(self):
        _, e, _ = world(Polyline((Vec(0, 0), Vec(100, 100), Vec(100, 0), Vec(0, 100))))
        r = best(e, 51, 50)
        assert r.candidate.kind == Kind.INTERSECTION and r.point.distance((50, 50)) < 1e-12

    def test_neighbouring_segments_do_not_count_as_crossing(self):
        _, e, _ = world(Polyline((Vec(0, 0), Vec(100, 0), Vec(100, 100))))
        assert Kind.INTERSECTION not in kinds_at(e, 100, 0)

    def test_ellipse_and_line_are_exact(self):
        _, e, _ = world(EllipseArc(Vec(0, 0), Vec(100, 0), 0.5), line(30, -100, 30, 100))
        expected_y = 50 * math.sqrt(1 - 0.09)
        r = best(e, 31, expected_y + 1)
        assert r.candidate.kind == Kind.INTERSECTION
        assert r.point.distance((30, expected_y)) < 1e-9

    def test_arc_and_spline(self):
        s = Spline(3, (0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0), (Vec(-100, -100), Vec(-30, 100), Vec(30, -100), Vec(100, 100)))
        _, e, _ = world(s, line(-200, 0, 200, 0))
        r = best(e, 0, 2)
        assert r.candidate.kind == Kind.INTERSECTION
        assert abs(r.point[1]) < 1e-9

    @pytest.mark.parametrize("px", [1e-6, 1e-3, 1.0, 100.0])
    def test_exact_at_every_zoom(self, px):
        k = px * 100        # the same picture on screen, whatever units it's in
        _, e, _ = world(line(0.1 * k, 0.2 * k, 100.3 * k, 100.4 * k), line(0.1 * k, 100.4 * k, 100.3 * k, 0.2 * k))
        r = best(e, 50.2 * k + 2 * px, 50.3 * k - 3 * px, px)
        assert r.candidate.kind == Kind.INTERSECTION
        assert r.point.distance((50.2 * k, 50.3 * k)) < 1e-9 * max(1.0, k * 100)

    def test_results_are_remembered_between_moves_and_forgotten_on_change(self, monkeypatch):
        d, e, (a, b) = world(line(0, 0, 100, 100), line(0, 100, 100, 0))
        from draftworx.core import snap
        calls = []
        real = snap.intersect
        monkeypatch.setattr(snap, "intersect", lambda *x, **k: (calls.append(1), real(*x, **k))[1])
        for i in range(10):
            best(e, 50 + i * 0.1, 50)
        assert len(calls) == 1
        with d.transaction("Move") as t:
            t.replace(a, line(0, 10, 100, 90))
        r = best(e, 50, 50)
        assert len(calls) == 2
        assert r.candidate.kind == Kind.INTERSECTION


class TestFromLastPoint:
    def test_perpendicular_to_a_line(self):
        _, e, _ = world(line(0, 0, 100, 0))
        r = best(e, 40, 1, last=Vec(40, 50), polar_step=0.0)
        assert r.candidate.kind == Kind.PERPENDICULAR and r.point == Vec(40, 0)

    def test_perpendicular_must_be_on_the_line(self):
        _, e, _ = world(line(0, 0, 100, 0))
        assert Kind.PERPENDICULAR not in kinds_at(e, 95, 0, last=Vec(150, 50))

    def test_no_perpendicular_without_a_last_point(self):
        _, e, _ = world(line(0, 0, 100, 0))
        assert Kind.PERPENDICULAR not in kinds_at(e, 40, 0)

    def test_perpendicular_to_a_circle_near_and_far(self):
        _, e, _ = world(Circle(Vec(0, 0), 100))
        near_side = best(e, 100, 3, last=Vec(300, 0), polar_step=0.0)
        assert near_side.point.distance((100, 0)) < 1e-9
        offered = {c.kind for c in e.candidates(query(100, 3, last=Vec(300, 0), polar_step=0.0))}
        assert Kind.PERPENDICULAR in offered or near_side.candidate.kind == Kind.QUADRANT

    def test_perpendicular_to_an_ellipse(self):
        el = EllipseArc(Vec(0, 0), Vec(100, 0), 0.5)
        _, e, _ = world(el)
        last = Vec(120, 90)
        t, q = el.project(last)
        offered = [c for c in e.candidates(query(q[0], q[1], last=last, polar_step=0.0)) if c.kind == Kind.PERPENDICULAR]
        assert offered
        p = offered[0].point
        assert (p - last).unit().dot(el.tangent_at(el.project(p)[0]).unit()) == pytest.approx(0, abs=1e-6)

    def test_tangent_to_a_circle(self):
        c = Circle(Vec(0, 0), 100)
        _, e, _ = world(c)
        last = Vec(200, 0)
        # the tangent point: 60 degrees round
        p = Vec(100 * math.cos(math.pi / 3), 100 * math.sin(math.pi / 3))
        r = best(e, p[0] + 2, p[1] + 2, last=last, polar_step=0.0)
        assert r.candidate.kind == Kind.TANGENT
        assert r.point.distance(p) < 1e-9
        assert (r.point - last).dot(r.point - c.c) == pytest.approx(0, abs=1e-6)

    def test_tangent_only_on_the_arc(self):
        _, e, _ = world(Arc(Vec(0, 0), 100, math.pi, 0.5))
        assert Kind.TANGENT not in kinds_at(e, 50, 86.6, last=Vec(200, 0))

    def test_no_tangent_from_inside(self):
        _, e, _ = world(Circle(Vec(0, 0), 100))
        assert Kind.TANGENT not in kinds_at(e, 100, 5, last=Vec(10, 0))

    def test_tangent_to_an_ellipse_is_exact(self):
        el = EllipseArc(Vec(0, 0), Vec(100, 0), 0.5)
        _, e, _ = world(el)
        last = Vec(200, 0)
        # the tangent point from (200, 0): x = a^2/200 = 50, y = b*sqrt(1 - (x/a)^2)
        p = Vec(50, 50 * math.sqrt(0.75))
        offered = [c for c in e.candidates(query(p[0] + 1, p[1] + 1, last=last, polar_step=0.0)) if c.kind == Kind.TANGENT]
        assert offered and offered[0].point.distance(p) < 1e-9

    def test_tangent_to_a_spline(self):
        s = Spline(3, (0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0), (Vec(-100, 0), Vec(-50, 100), Vec(50, 100), Vec(100, 0)))
        _, e, _ = world(s)
        last = Vec(0, 120)
        # from above the top of the arch, a tangent lands on each side of it
        offered = []
        for t in [i / 40 for i in range(41)]:
            q = s.point_at(t)
            offered += [c for c in e.candidates(query(q[0], q[1], last=last, polar_step=0.0)) if c.kind == Kind.TANGENT]
        assert offered
        for c in offered:
            tang = s.tangent_at(s.project(c.point)[0]).unit()
            assert abs((c.point - last).unit().cross(tang)) < 1e-5


class TestBlocks:
    def world_with_block(self, *inserts, members=None):
        d = Document()
        with d.transaction("x") as t:
            t.put_block(BlockDef("B", Vec(10, 10), members or (Entity(1000, line(10, 10, 110, 10)),
                                                               Entity(1001, Circle(Vec(60, 60), 50)))))
            ids = [t.add(i) for i in inserts]
        return d, SnapEngine(d), ids

    def test_the_contents_of_a_placed_block(self):
        _, e, (a,) = self.world_with_block(Insert("B", Vec(500, 500)))
        r = best(e, 601, 501)
        assert r.candidate.kind == Kind.ENDPOINT and r.point == Vec(600, 500)
        assert r.candidate.sources == (a,)

    def test_the_insertion_point_even_if_nothing_is_drawn_there(self):
        members = (Entity(1000, line(1000, 1000, 1100, 1000)),)
        _, e, _ = self.world_with_block(Insert("B", Vec(500, 500)), members=members)
        r = best(e, 501, 501)
        assert r.candidate.kind == Kind.INSERTION and r.point == Vec(500, 500)

    def test_scaled_and_turned(self):
        ins = Insert("B", Vec(500, 500), 2.0, 2.0, math.pi / 2)
        _, e, _ = self.world_with_block(ins)
        r = best(e, 501, 701)      # the far end of the line: 100 long, doubled, pointing up
        assert r.candidate.kind == Kind.ENDPOINT
        assert r.point.distance((500, 700)) < 1e-9

    def test_a_stretch_turns_a_circle_into_an_ellipse_exactly(self):
        ins = Insert("B", Vec(0, 0), 2.0, 1.0)
        _, e, _ = self.world_with_block(ins)
        # block coordinates: circle centred (60, 60) radius 50, base (10, 10)
        center = ins.matrix(Vec(10, 10)).apply((60, 60))
        r = best(e, center[0] + 1, center[1] + 1)
        assert r.candidate.kind == Kind.CENTER and r.point.distance(center) < 1e-9
        far = ins.matrix(Vec(10, 10)).apply((110, 60))
        r = best(e, far[0] + 1, far[1] + 1)
        assert r.point.distance(far) < 1e-9

    def test_mirrored(self):
        ins = Insert("B", Vec(0, 0), -1.0, 1.0)
        _, e, _ = self.world_with_block(ins)
        r = best(e, -101, 1)
        assert r.point.distance((-100, 0)) < 1e-9

    def test_changing_a_nested_block_moves_what_contains_it(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_block(BlockDef("Inner", Vec(0, 0), (Entity(1000, line(0, 0, 10, 0)),)))
            t.put_block(BlockDef("Outer", Vec(0, 0), (Entity(1001, Insert("Inner", Vec(100, 0))),)))
            t.add(Insert("Outer", Vec(1000, 0)))
        e = SnapEngine(d)
        assert best(e, 1111, 1).point == Vec(1110, 0)
        with d.transaction("Edit") as t:
            t.put_block(BlockDef("Inner", Vec(0, 0), (Entity(1002, line(0, 0, 500, 0)),)))
        assert best(e, 1601, 1).point == Vec(1600, 0)
        d.undo()
        assert best(e, 1111, 1).point == Vec(1110, 0)
        assert best(e, 1601, 1).candidate is None

    def test_nested(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_block(BlockDef("Inner", Vec(0, 0), (Entity(1000, line(0, 0, 10, 0)),)))
            t.put_block(BlockDef("Outer", Vec(0, 0), (Entity(1001, Insert("Inner", Vec(100, 0), 2.0, 2.0)),)))
            t.add(Insert("Outer", Vec(1000, 0), 3.0, 3.0))
        e = SnapEngine(d)
        r = best(e, 1361, 1)
        assert r.candidate.kind == Kind.ENDPOINT and r.point.distance((1360, 0)) < 1e-9

    def test_an_array_of_copies(self):
        ins = Insert("B", Vec(0, 0), columns=3, rows=2, column_spacing=1000.0, row_spacing=500.0)
        _, e, _ = self.world_with_block(ins)
        r = best(e, 2100, 500)
        assert r.point.distance((2100, 500)) < 1e-9

    def test_crossing_between_a_block_and_a_line(self):
        d, e, _ = self.world_with_block(Insert("B", Vec(0, 0)), line(50, -50, 50, 50))
        r = best(e, 51, 1)
        assert r.candidate.kind == Kind.INTERSECTION and r.point.distance((50, 0)) < 1e-9

    def test_a_missing_block_is_harmless(self):
        _, e, _ = world(Insert("Nope", Vec(5, 5)))
        r = best(e, 6, 6)
        assert r.candidate.kind == Kind.INSERTION

    def test_changing_a_block_changes_what_copies_offer(self):
        d, e, _ = self.world_with_block(Insert("B", Vec(0, 0)))
        assert best(e, 101, 1).point == Vec(100, 0)
        with d.transaction("Edit") as t:
            t.put_block(BlockDef("B", Vec(10, 10), (Entity(1002, line(10, 10, 210, 10)),)))
        assert best(e, 201, 1).point == Vec(200, 0)

    def test_a_hidden_layer_inside_a_block(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_layer(Layer("Off", on=False))
            t.put_block(BlockDef("B", Vec(0, 0), (Entity(1000, line(0, 0, 100, 0), Style("Off")),)))
            t.add(Insert("B", Vec(0, 0)))
        r = SnapEngine(d).pick(query(99, 1))
        assert r.candidate is None

    def test_a_block_scaled_to_nothing_is_harmless(self):
        d, e, _ = self.world_with_block(Insert("B", Vec(0, 0), 0.0, 0.0))
        best(e, 1, 1)


class TestFilters:
    def test_hidden_layers_are_not_offered(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_layer(Layer("Off", on=False))
            t.add(line(0, 0, 100, 0), Style("Off"))
        assert SnapEngine(d).pick(query(1, 1)).candidate is None

    def test_frozen_layers_are_not_offered(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_layer(Layer("Off", frozen=True))
            t.add(line(0, 0, 100, 0), Style("Off"))
        assert SnapEngine(d).pick(query(1, 1)).candidate is None

    def test_locked_layers_are_offered(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_layer(Layer("L", locked=True))
            t.add(line(0, 0, 100, 0), Style("L"))
        assert SnapEngine(d).pick(query(1, 1)).point == Vec(0, 0)

    def test_what_is_being_moved_is_ignored(self):
        _, e, (a,) = world(line(0, 0, 100, 0))
        assert best(e, 1, 1, exclude=frozenset({a})).candidate is None

    def test_a_kind_can_be_switched_off(self):
        _, e, _ = world(line(0, 0, 100, 0))
        r = best(e, 1, 1, kinds=ALL_KINDS - {Kind.ENDPOINT})
        assert r.candidate.kind == Kind.ON

    def test_snapping_can_be_switched_off(self):
        _, e, _ = world(line(0, 0, 100, 0))
        r = best(e, 1, 1, enabled=False)
        assert r.candidate is None and r.point == Vec(1, 1)

    def test_what_is_being_drawn_can_be_snapped_to(self):
        _, e, _ = world()
        extra = (Polyline((Vec(0, 0), Vec(100, 0), Vec(100, 100))),)
        r = best(e, 99, 1, extra=extra)
        assert r.point == Vec(100, 0) and r.candidate.kind == Kind.ENDPOINT
        assert r.candidate.sources == ()
        assert best(e, 50, 1, extra=extra).point == Vec(50, 0)

    def test_a_loose_curve_being_drawn_can_be_snapped_to(self):
        _, e, _ = world(line(0, 50, 100, 50))
        r = best(e, 50, 49, extra=(line(50, 0, 50, 100),))
        assert r.candidate.kind == Kind.INTERSECTION


class TestChoosing:
    def test_tab_order_reaches_everything_near(self):
        # end, intersection and middle are all close together, and so is the circle's top
        _, e, _ = world(line(0, 0, 20, 0), line(5, -10, 5, 10), Circle(Vec(5, 20), 20))
        offers = e.candidates(query(5, 2))
        kinds = {c.kind for c in offers}
        assert {Kind.ENDPOINT, Kind.INTERSECTION, Kind.MIDPOINT} <= kinds
        assert offers == sorted(offers, key=lambda c: (c.score, c.kind, c.point[0], c.point[1], c.sources))

    def test_the_same_point_found_twice_is_one_candidate(self):
        _, e, ids = world(line(0, 0, 100, 0), line(0, 0, 0, 100))
        offers = [c for c in e.candidates(query(1, 1)) if c.point == Vec(0, 0)]
        assert len(offers) == 1
        assert offers[0].kind == Kind.ENDPOINT
        assert set(offers[0].sources) == set(ids)

    def test_the_choice_does_not_flicker(self):
        # the cursor sits between two endpoints, a little closer to the right-hand one each time
        _, e, _ = world(line(0, 0, 0, 100), line(10, 0, 10, 100))
        first = best(e, 4.5, 1)
        assert first.point == Vec(0, 0)
        held = best(e, 5.5, 1, previous=first.candidate)
        assert held.point == Vec(0, 0), "a move of one pixel shouldn't flip the choice"
        moved = best(e, 8.5, 1, previous=held.candidate)
        assert moved.point == Vec(10, 0), "a clear move should"

    def test_a_point_stays_chosen_a_little_beyond_where_it_was_first_offered(self):
        _, e, _ = world(Node(Vec(0, 0)))
        r = best(e, 13, 0)
        assert r.point == Vec(0, 0)
        assert best(e, 16, 0).candidate is None                         # coming fresh, it's too far
        assert best(e, 16, 0, previous=r.candidate).point == Vec(0, 0)  # already chosen, it holds
        assert best(e, 19, 0, previous=r.candidate).candidate is None   # but not for ever

    def test_a_held_point_lets_go_if_it_no_longer_exists(self):
        d, e, (a,) = world(Node(Vec(0, 0)))
        r = best(e, 1, 0)
        with d.transaction("x") as t:
            t.remove(a)
        assert best(e, 1, 0, previous=r.candidate).candidate is None

    def test_same_inputs_same_answer_whatever_order_things_were_drawn(self):
        r = random.Random(5)
        shapes = [line(r.random() * 100, r.random() * 100, r.random() * 100, r.random() * 100) for _ in range(40)]
        _, e1, _ = world(*shapes)
        shuffled = list(shapes)
        r.shuffle(shuffled)
        _, e2, _ = world(*shuffled)
        for _ in range(100):
            x, y = r.random() * 100, r.random() * 100
            a = [(c.point, c.kind) for c in e1.candidates(query(x, y, 0.5))]
            b = [(c.point, c.kind) for c in e2.candidates(query(x, y, 0.5))]
            assert a == b

    def test_asking_twice_gives_the_same_answer(self):
        _, e, _ = world(line(0, 0, 100, 100), line(0, 100, 100, 0), Circle(Vec(50, 50), 30))
        q = query(52, 52, last=Vec(0, 0))
        assert e.candidates(q) == e.candidates(q)

    @given(st.lists(lines, min_size=1, max_size=12), st.floats(-5000, 5000), st.floats(-5000, 5000),
           st.sampled_from([0.01, 1.0, 50.0]))
    def test_the_best_candidate_is_always_near_enough_and_really_there(self, shapes, x, y, px):
        _, e, _ = world(*shapes)
        offers = e.candidates(query(x, y, px))
        for c in offers:
            assert c.distance <= 14.0 + 1e-6
            assert c.score == pytest.approx(c.distance + {0: 0, 1: 0, 6: 2, 13: 14}[int(c.kind)])
            # every candidate lies on one of the lines
            assert min(s.project(c.point)[1].distance(c.point) for s in shapes) < 1e-6
        assert offers == sorted(offers, key=lambda c: (c.score, c.kind, c.point[0], c.point[1], c.sources))

    def test_zoomed_far_out_stays_bounded(self):
        r = random.Random(6)
        shapes = [line(r.random() * 100, r.random() * 100, r.random() * 100, r.random() * 100) for _ in range(3000)]
        _, e, _ = world(*shapes)
        offers = e.candidates(query(50, 50, 100.0))
        assert offers


class TestGuides:
    def tracked(self, *pts):
        return tuple(Tracked(Vec(float(x), float(y)), "Endpoint") for x, y in pts)

    def test_horizontal_from_a_tracked_point(self):
        _, e, _ = world()
        r = best(e, 100, 3, tracked=self.tracked((0, 0)))
        assert r.candidate.kind == Kind.GUIDE
        assert r.point == Vec(100, 0)
        assert r.guides[0].label == "Horizontal"

    def test_vertical_from_a_tracked_point(self):
        _, e, _ = world()
        r = best(e, 3, 100, tracked=self.tracked((0, 0)))
        assert r.point == Vec(0, 100) and r.guides[0].label == "Vertical"

    def test_the_point_stays_exactly_on_the_line(self):
        _, e, _ = world()
        y = 1 / 3
        r = best(e, 100.123, y + 2, tracked=(Tracked(Vec(0.0, y), "Endpoint"),))
        assert r.point[1] == y

    def test_free_position_along_a_line_is_tidied(self):
        _, e, _ = world()
        r = best(e, 100.37, 0.3, 0.1, tracked=self.tracked((0, 0)))
        assert r.point == Vec(100.4, 0.0)

    def test_two_lines_meeting(self):
        _, e, _ = world()
        r = best(e, 101, 52, tracked=self.tracked((100, 0), (0, 50)))
        assert r.candidate.kind == Kind.GUIDES_MEET
        assert r.point == Vec(100, 50)
        assert len(r.guides) == 2

    def test_a_line_crossing_an_object(self):
        _, e, (a,) = world(line(0, 50, 300, 50))
        r = best(e, 101, 52, tracked=self.tracked((100, 0)))
        assert r.candidate.kind == Kind.GUIDE_ON_OBJECT
        assert r.point == Vec(100, 50)
        assert r.candidate.sources == (a,)

    def test_a_line_crossing_a_circle_is_exact(self):
        _, e, _ = world(Circle(Vec(0, 0), 100))
        p = Vec(60, 80)
        r = best(e, 61, 81, tracked=self.tracked((60, 0)))
        assert r.candidate.kind == Kind.GUIDE_ON_OBJECT
        assert r.point.distance(p) < 1e-9

    def test_extension_of_a_line(self):
        _, e, _ = world()
        d = Vec(1, 1).unit()
        r = best(e, 101, 99, tracked=(Tracked(Vec(0, 0), "Endpoint", (d,)),))
        assert r.candidate.kind == Kind.GUIDE
        assert r.guides[0].label == "Extension"
        assert abs(r.point[0] - r.point[1]) < 1e-9

    def test_a_real_point_beats_a_guide_right_beside_it(self):
        """The SketchUp complaint: an alignment must never take the place of a point that's there."""
        _, e, _ = world(Node(Vec(100, 3)))
        r = best(e, 100, 1, tracked=self.tracked((0, 0)))
        assert r.candidate.kind == Kind.NODE and r.point == Vec(100, 3)

    def test_a_guide_wins_where_the_cursor_is_clearly_on_it_and_far_from_anything_real(self):
        _, e, _ = world(Node(Vec(100, 13)))
        r = best(e, 100, 0.5, tracked=self.tracked((0, 0)))
        assert r.candidate.kind == Kind.GUIDE

    def test_polar_lines_from_the_last_point(self):
        _, e, _ = world()
        r = best(e, 100, 99, last=Vec(0, 0))
        assert r.candidate.kind == Kind.GUIDE
        assert r.guides[0].label == "45°"
        assert r.point[0] == pytest.approx(r.point[1])

    def test_polar_step_changes_the_lines(self):
        _, e, _ = world()
        r = best(e, 100, 58, last=Vec(0, 0), polar_step=math.pi / 6)
        assert r.guides[0].label == "30°"
        assert best(e, 100, 58, last=Vec(0, 0), polar_step=0.0).candidate is None

    def test_horizontal_is_exactly_horizontal(self):
        _, e, _ = world()
        y = 1 / 3
        r = best(e, 100, y + 1, last=Vec(0.0, y))
        assert r.point[1] == y
        assert r.guides[0].label == "Horizontal"

    def test_no_lines_tangle_right_at_the_origin(self):
        _, e, _ = world()
        assert best(e, 2, 2, last=Vec(0, 0)).candidate is None

    def test_parallel_and_perpendicular_to_something(self):
        _, e, _ = world()
        d = Vec(3, 1).unit()
        r = best(e, 300, 101, last=Vec(0, 0), polar_step=0.0, parallel_to=((d, "that edge"),))
        assert r.guides[0].label == "Parallel to that edge"
        p = d.perp()
        r = best(e, p[0] * 200 + 1, p[1] * 200, last=Vec(0, 0), polar_step=0.0, parallel_to=((d, "that edge"),))
        assert r.guides[0].label == "Perpendicular to that edge"

    def test_two_guides_from_one_point_do_not_meet_uselessly(self):
        _, e, _ = world()
        assert Kind.GUIDES_MEET not in kinds_at(e, 5, 5, tracked=self.tracked((0, 0)))

    def test_guide_can_be_switched_off(self):
        _, e, _ = world()
        r = best(e, 100, 3, tracked=self.tracked((0, 0)), kinds=ALL_KINDS - {Kind.GUIDE})
        assert r.candidate is None

    def test_guide_foot(self):
        g = Guide(Vec(1, 1), Vec(1, 0), "x")
        assert g.foot((5, 9)) == (4.0, Vec(5, 1))


class TestLocks:
    def lock_h(self):
        return Lock(Vec(0, 0), Vec(1, 0), None, "Horizontal")

    def test_a_line_lock_keeps_the_point_on_the_line(self):
        _, e, _ = world()
        r = best(e, 100, 40, lock=self.lock_h())
        assert r.point == Vec(100, 0)

    def test_an_object_crossing_the_lock(self):
        _, e, (a,) = world(line(100, -100, 100, 300))
        r = best(e, 101, 3, lock=self.lock_h())
        assert r.candidate.kind == Kind.GUIDE_ON_OBJECT and r.point == Vec(100, 0)

    def test_pointing_at_a_real_point_lines_up_with_it(self):
        _, e, _ = world(Node(Vec(100, 60)))
        r = best(e, 101, 61, lock=self.lock_h())
        assert r.point == Vec(100, 0)
        assert r.candidate.label == "In line with point"
        assert r.candidate.kind == Kind.NODE

    def test_a_radius_lock(self):
        _, e, _ = world()
        r = best(e, 100, 100, lock=Lock(Vec(0, 0), None, 50.0))
        assert r.point.length() == pytest.approx(50)
        assert r.point[0] == pytest.approx(r.point[1])

    def test_an_object_crossing_the_radius_lock(self):
        _, e, _ = world(line(30, -100, 30, 100))
        r = best(e, 31, 41, lock=Lock(Vec(0, 0), None, 50.0))
        assert r.candidate.kind == Kind.GUIDE_ON_OBJECT and r.point.distance((30, 40)) < 1e-9

    def test_pointing_at_a_real_point_with_a_radius_lock(self):
        _, e, _ = world(Node(Vec(300, 400)))
        r = best(e, 301, 401, lock=Lock(Vec(0, 0), None, 50.0))
        assert r.point.distance((30, 40)) < 1e-9
        assert r.candidate.label == "Toward point"

    def test_both_locked_is_one_place(self):
        _, e, _ = world(line(0, 0, 100, 100))
        r = best(e, 500, 500, lock=Lock(Vec(0, 0), Vec(0, 1), 25.0))
        assert r.point == Vec(0, 25)

    def test_the_free_position_on_a_lock_is_tidied(self):
        _, e, _ = world()
        r = best(e, 100.37, 40, 0.1, lock=self.lock_h())
        assert r.point == Vec(100.4, 0)


class TestTidy:
    def test_steps(self):
        assert tidy_step(1.0) == 1.0
        assert tidy_step(1.5) == 2.0
        assert tidy_step(3.0) == 5.0
        assert tidy_step(7.0) == 10.0
        assert tidy_step(0.03) == pytest.approx(0.05)
        assert tidy_step(0.0) == 0.0

    def test_values_read_as_round_numbers(self):
        assert tidy(123.456, 0.1) == 123.5
        assert tidy(123.456, 0.01) == 123.46
        assert tidy(123.456, 1.0) == 123.0
        assert tidy(123.456, 3.0) == 125.0
        assert tidy(-123.456, 0.1) == -123.5

    def test_a_free_point_is_at_most_a_pixel_from_the_cursor(self):
        r = random.Random(7)
        _, e, _ = world()
        for _ in range(500):
            px = 10 ** r.uniform(-6, 4)
            x, y = r.uniform(-1e4, 1e4), r.uniform(-1e4, 1e4)
            p = e.pick(query(x, y, px)).point
            assert abs(p[0] - x) <= px * 2 and abs(p[1] - y) <= px * 2

    def test_a_grid_when_it_is_on(self):
        _, e, _ = world()
        r = best(e, 13, 27, grid=10.0)
        assert r.point == Vec(10, 30)
        assert best(e, 13, 27, grid=10.0, grid_origin=Vec(5, 5)).point == Vec(15, 25)

    def test_a_real_point_beats_the_grid(self):
        _, e, _ = world(Node(Vec(13, 27)))
        assert best(e, 14, 28, grid=10.0).point == Vec(13, 27)


def test_candidate_identity_for_a_moving_point():
    g = Guide(Vec(0, 0), Vec(1, 0), "Horizontal")
    a = Candidate(Vec(10, 0), Kind.GUIDE, 1.0, 11.0, (), (g,))
    b = Candidate(Vec(11, 0), Kind.GUIDE, 1.0, 11.0, (), (g,))
    assert a.same_as(b)
    c = Candidate(Vec(11, 0), Kind.ENDPOINT, 1.0, 1.0)
    assert not a.same_as(c)


class TestTracker:
    def setup_method(self):
        self.tracker = Tracker()

    def hover(self, engine, x, y, now, last=False, **kw):
        c = engine.pick(query(x, y, **kw)).candidate
        return self.tracker.hover(c, now, engine, last)

    def test_a_pause_picks_a_point_up(self):
        _, e, _ = world(line(0, 0, 100, 0))
        assert not self.hover(e, 1, 1, 0.0)
        assert not self.hover(e, 1, 1, 0.2)
        assert self.hover(e, 1, 1, 0.4)
        assert self.tracker.points[0].point == Vec(0, 0)
        assert self.tracker.points[0].label == "Endpoint"

    def test_passing_over_a_point_does_not(self):
        _, e, _ = world(line(0, 0, 100, 0))
        self.hover(e, 1, 1, 0.0)
        self.hover(e, 30, 30, 0.2)       # left before the pause was up
        assert not self.hover(e, 1, 1, 0.4)
        assert not self.tracker.points

    def test_small_movement_on_the_same_point_keeps_the_pause_going(self):
        _, e, _ = world(line(0, 0, 100, 0))
        self.hover(e, 1, 1, 0.0)
        self.hover(e, 2, 1, 0.2)
        assert self.hover(e, 1, 2, 0.4)

    def test_staying_does_not_flip_it_back(self):
        _, e, _ = world(line(0, 0, 100, 0))
        self.hover(e, 1, 1, 0.0)
        self.hover(e, 1, 1, 0.4)
        assert not self.hover(e, 1, 1, 5.0)
        assert len(self.tracker.points) == 1

    def test_a_second_pause_after_leaving_drops_it(self):
        _, e, _ = world(line(0, 0, 100, 0))
        self.hover(e, 1, 1, 0.0)
        self.hover(e, 1, 1, 0.4)
        self.hover(e, 30, 30, 0.5)
        self.hover(e, 1, 1, 0.6)
        assert self.hover(e, 1, 1, 1.0)
        assert not self.tracker.points

    def test_the_oldest_goes_past_the_limit(self):
        _, e, _ = world(*[Node(Vec(i * 100.0, 0.0)) for i in range(9)])
        now = 0.0
        for i in range(9):
            self.hover(e, i * 100.0, 1, now)
            now += 0.4
            self.hover(e, i * 100.0, 1, now)
            now += 0.1
        assert len(self.tracker.points) == Tracker.MOST
        assert self.tracker.points[0].point == Vec(200, 0)

    def test_a_line_end_knows_which_way_the_line_goes(self):
        _, e, _ = world(line(100, 100, 0, 0))
        self.hover(e, 1, 1, 0.0)
        self.hover(e, 1, 1, 0.4)
        along = self.tracker.points[0].along
        assert len(along) == 1
        assert abs(abs(along[0][0]) - math.sqrt(0.5)) < 1e-9

    def test_a_shared_corner_offers_both_directions(self):
        _, e, _ = world(line(0, 0, 100, 0), line(0, 0, 0, 100))
        self.hover(e, 1, 1, 0.0)
        self.hover(e, 1, 1, 0.4)
        assert len(self.tracker.points[0].along) == 2

    def test_an_arc_end_offers_its_tangent(self):
        arc = Arc(Vec(0, 0), 100, 0.0, math.pi / 2)
        _, e, _ = world(arc)
        self.hover(e, 100, -1, 0.0)
        self.hover(e, 100, -1, 0.4)
        assert self.tracker.points[0].along

    def test_a_centre_has_no_direction(self):
        _, e, _ = world(Circle(Vec(0, 0), 100))
        self.hover(e, 1, 1, 0.0)
        self.hover(e, 1, 1, 0.4)
        assert self.tracker.points[0].along == ()

    def test_pausing_on_an_edge_offers_its_direction_when_measuring_from_a_point(self):
        _, e, _ = world(line(0, 0, 300, 100))
        self.hover(e, 60, 20, 0.0, last=True)
        assert self.hover(e, 60, 20, 0.4, last=True)
        d = self.tracker.directions[0][0]
        assert abs(d.cross(Vec(3, 1).unit())) < 1e-9

    def test_pausing_on_an_edge_does_nothing_with_no_point_to_measure_from(self):
        _, e, _ = world(line(0, 0, 300, 100))
        self.hover(e, 60, 20, 0.0, last=False)
        assert not self.hover(e, 60, 20, 0.4, last=False)
        assert not self.tracker.directions

    def test_a_second_pause_on_the_edge_drops_it(self):
        _, e, _ = world(line(0, 0, 300, 100))
        self.hover(e, 60, 20, 0.0, last=True)
        self.hover(e, 60, 20, 0.4, last=True)
        self.hover(e, 500, 500, 0.5, last=True)
        self.hover(e, 60, 20, 0.6, last=True)
        assert self.hover(e, 60, 20, 1.0, last=True)
        assert not self.tracker.directions

    def test_clear(self):
        _, e, _ = world(line(0, 0, 100, 0))
        self.hover(e, 1, 1, 0.0)
        self.hover(e, 1, 1, 0.4)
        self.tracker.clear()
        assert not self.tracker.points

    def test_free_space_resets_the_pause(self):
        _, e, _ = world(line(0, 0, 100, 0))
        self.hover(e, 1, 1, 0.0)
        self.hover(e, 500, 500, 0.3)
        assert not self.hover(e, 1, 1, 0.4)
