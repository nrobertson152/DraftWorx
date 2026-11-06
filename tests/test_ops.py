# SPDX-License-Identifier: GPL-3.0-or-later
"""Trim, extend, break, offset, fillet, chamfer, array, join, explode, stretch: the geometry, and the commands that use it."""
import math
from unittest import mock

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from draftworx.core.curves import Arc, Circle, EllipseArc, Line, Polyline, Spline, spline_through
from draftworx.core.intersect import intersect
from draftworx.core.model import BlockDef, Entity, Layer, Style
from draftworx.core.ops.corner import chamfer, corner_of_polyline, every_corner, fillet
from draftworx.core.ops.offset import is_left, offset
from draftworx.core.ops.pieces import break_at, crossings, cut_at, extend_at, is_closed, trim_at
from draftworx.core.ops.reshape import explode_shape, join, stretched
from draftworx.core.shapes import AttDef, Attrib, Foreign, Insert, MText, Node, Solid, Text
from draftworx.core.vec import TAU, Affine, Vec
from driver import Person, line


def V(x, y):
    return Vec(float(x), float(y))


def close(a, b, eps=1e-9):
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= eps


def doc_of(*shapes):
    p = Person(*shapes)
    return p.doc, p.ids


def rectangle(x0, y0, x1, y1):
    return Polyline((V(x0, y0), V(x1, y0), V(x1, y1), V(x0, y1)), closed=True)


# ------------------------------------------------------------------------------------------------------------ sub


class TestPartOfAPolyline:
    def test_straight_pieces(self):
        s = Polyline((V(0, 0), V(10, 0), V(10, 10)))
        part = s.sub(0.25, 0.75)
        assert part.pts == (V(5, 0), V(10, 0), V(10, 5)) and not part.closed

    def test_keeps_arcs_as_arcs(self):
        s = Polyline((V(0, 0), V(10, 0)), (1.0, 0.0))
        part = s.sub(0.0, 0.5)
        arc = s.segments[0]
        assert close(part.end, arc.point_at(0.5))
        assert part.bulges[0] == pytest.approx(math.tan(arc.sweep * 0.5 / 4.0))

    def test_through_the_start_of_a_closed_one(self):
        s = rectangle(0, 0, 10, 10)
        part = s.sub(0.75, 1.25)
        assert part.pts == (V(0, 10), V(0, 0), V(10, 0))

    def test_keeps_widths(self):
        s = Polyline((V(0, 0), V(10, 0), V(20, 0)), widths=((0.0, 2.0), (2.0, 4.0), (0.0, 0.0)))
        part = s.sub(0.25, 0.75)
        assert part.widths[0] == pytest.approx((1.0, 2.0)) and part.widths[1] == pytest.approx((2.0, 3.0))
        assert Polyline((V(0, 0), V(10, 0)), width=3.0).sub(0.1, 0.9).width == 3.0

    @given(st.floats(0.0, 0.99), st.floats(0.01, 1.0))
    def test_it_follows_the_original(self, a, b):
        assume(b - a > 1e-3)
        s = Polyline((V(0, 0), V(10, 0), V(10, 10), V(20, 5)), (0.3, -0.5, 0.0, 0.0))
        part = s.sub(a, b)
        assert close(part.start, s.point_at(a), 1e-6) and close(part.end, s.point_at(b), 1e-6)
        for t in (0.25, 0.5, 0.75):
            q = part.point_at(t)
            assert close(s.project(q)[1], q, 1e-6)


# ----------------------------------------------------------------------------------------------------------- trim


class TestTrimming:
    def crossed(self, *shape):
        return doc_of(line(0, 0, 100, 0), line(30, -10, 30, 10), line(70, -10, 70, 10), *shape)

    def test_the_middle_leaves_two(self):
        doc, ids = self.crossed()
        c = trim_at(doc, ids[0], V(50, 0))
        assert c.removed == (line(30, 0, 70, 0),) and c.kept == (line(0, 0, 30, 0), line(70, 0, 100, 0))

    def test_an_end_leaves_one(self):
        doc, ids = self.crossed()
        c = trim_at(doc, ids[0], V(10, 0))
        assert c.removed == (line(0, 0, 30, 0),) and c.kept == (line(30, 0, 100, 0),)
        assert trim_at(doc, ids[0], V(90, 0)).kept == (line(0, 0, 70, 0),)

    def test_nothing_to_cut_against_takes_the_whole_thing(self):
        doc, ids = doc_of(line(0, 0, 100, 0))
        c = trim_at(doc, ids[0], V(50, 0))
        assert c.removed == (line(0, 0, 100, 0),) and c.kept == ()

    def test_something_touching_at_its_own_end_doesnt_cut_there(self):
        doc, ids = doc_of(line(0, 0, 100, 0), line(100, 0, 100, 50))
        assert crossings(doc, doc.entities[ids[0]]) == []

    def test_a_circle_needs_two_cuts_and_becomes_an_arc(self):
        doc, ids = doc_of(Circle(V(0, 0), 10.0), line(-20, 0, 20, 0))
        c = trim_at(doc, ids[0], V(0, 10))
        assert len(c.removed) == 1 and type(c.kept[0]) is Arc
        assert close(c.kept[0].point_at(0.5), (0, -10))
        assert close(c.removed[0].point_at(0.5), (0, 10))
        other = trim_at(doc, ids[0], V(0, -10))
        assert close(other.kept[0].point_at(0.5), (0, 10))

    def test_a_circle_with_one_cut_goes(self):
        doc, ids = doc_of(Circle(V(0, 0), 10.0), line(0, 0, 20, 0))
        c = trim_at(doc, ids[0], V(0, 10))
        assert c.kept == () and c.removed == (Circle(V(0, 0), 10.0),)

    def test_a_circle_trimmed_across_where_it_starts(self):
        doc, ids = doc_of(Circle(V(0, 0), 10.0), line(0, -20, 0, 20))
        c = trim_at(doc, ids[0], V(10, 0))
        assert close(c.removed[0].point_at(0.5), (10, 0)) and close(c.kept[0].point_at(0.5), (-10, 0))

    def test_an_arc(self):
        doc, ids = doc_of(Arc(V(0, 0), 10.0, 0.0, math.pi), line(0, 0, 0, 20))
        c = trim_at(doc, ids[0], V(7, 7))
        assert len(c.kept) == 1 and close(c.kept[0].start, (0, 10)) and close(c.kept[0].end, (-10, 0))

    def test_a_backward_arc(self):
        doc, ids = doc_of(Arc(V(0, 0), 10.0, math.pi, -math.pi), line(0, 0, 0, 20))
        c = trim_at(doc, ids[0], V(7, 7))
        assert len(c.kept) == 1 and close(c.kept[0].start, (-10, 0)) and close(c.kept[0].end, (0, 10))

    def test_an_ellipse(self):
        e = EllipseArc(V(0, 0), V(20, 0), 0.5, 0.0, TAU)
        doc, ids = doc_of(e, line(-30, 0, 30, 0))
        c = trim_at(doc, ids[0], V(0, 10))
        assert type(c.kept[0]) is EllipseArc
        assert close(c.kept[0].point_at(0.5), (0, -10), 1e-6)

    def test_a_spline(self):
        s = spline_through([V(0, 0), V(30, 20), V(60, -20), V(90, 0)])
        doc, ids = doc_of(s, line(45, -50, 45, 50))
        c = trim_at(doc, ids[0], V(70, s.project(V(70, 0))[1][1]))
        assert type(c.kept[0]) is Spline and close(c.kept[0].start, s.start, 1e-6)
        assert abs(c.kept[0].end[0] - 45.0) < 1e-6

    def test_a_polyline_across_its_corners(self):
        s = Polyline((V(0, 0), V(50, 0), V(50, 50), V(100, 50)))
        doc, ids = doc_of(s, line(25, -5, 25, 5), line(75, 45, 75, 55))
        c = trim_at(doc, ids[0], V(50, 25))
        assert c.removed[0].pts == (V(25, 0), V(50, 0), V(50, 50), V(75, 50))
        assert c.kept == (Polyline((V(0, 0), V(25, 0)), (0.0, 0.0)), Polyline((V(75, 50), V(100, 50)), (0.0, 0.0)))

    def test_a_closed_polyline_opens_up(self):
        s = rectangle(0, 0, 100, 100)
        doc, ids = doc_of(s, line(-5, 50, 105, 50))
        c = trim_at(doc, ids[0], V(50, 100))
        assert len(c.kept) == 1 and not c.kept[0].closed
        assert close(c.kept[0].start, (0, 50)) and close(c.kept[0].end, (100, 50))
        assert V(0, 0) in c.kept[0].pts and V(100, 0) in c.kept[0].pts

    def test_a_polyline_crossing_itself_cuts_itself(self):
        s = Polyline((V(0, 0), V(100, 0), V(100, 50), V(50, 50), V(50, -50)))
        doc, ids = doc_of(s)
        c = trim_at(doc, ids[0], V(10, 0))
        assert close(c.removed[0].end, (50, 0))

    def test_what_a_block_draws_cuts(self):
        doc, ids = doc_of(line(0, 0, 100, 0))
        with doc.transaction("x") as t:
            t.put_block(BlockDef("B", V(0, 0), (Entity(500, line(0, -10, 0, 10)),)))
            t.add(Insert("B", V(50, 0)))
        assert trim_at(doc, ids[0], V(10, 0)).kept == (line(50, 0, 100, 0),)

    def test_what_a_file_holds_cuts(self):
        doc, ids = doc_of(line(0, 0, 100, 0))
        with doc.transaction("x") as t:
            t.add(Foreign("LEADER", curves=(Polyline((V(50, -10), V(50, 10))),)))
        assert trim_at(doc, ids[0], V(10, 0)).kept == (line(50, 0, 100, 0),)

    def test_a_hidden_layer_doesnt_cut(self):
        doc, ids = doc_of(line(0, 0, 100, 0))
        with doc.transaction("x") as t:
            t.put_layer(Layer("Off", on=False))
            t.add(line(50, -10, 50, 10), Style("Off"))
        assert trim_at(doc, ids[0], V(10, 0)).kept == ()

    def test_text_cant_be_trimmed(self):
        doc, ids = doc_of(Text(V(0, 0), 5.0, "x"))
        assert trim_at(doc, ids[0], V(0, 0)) is None

    @given(st.lists(st.tuples(st.integers(-20, 20), st.integers(-20, 20), st.integers(-20, 20), st.integers(-20, 20)), min_size=2, max_size=7),
           st.sampled_from(("line", "circle", "arc")), st.floats(0.02, 0.98))
    @settings(max_examples=200, deadline=None)
    def test_only_the_piece_pointed_at_goes(self, ends, kind, t):
        others = [Line(V(x0 * 5, y0 * 5), V(x1 * 5, y1 * 5)) for x0, y0, x1, y1 in ends if (x0, y0) != (x1, y1)]
        assume(others)
        target = {"line": Line(V(-90, 3), V(95, 7)), "circle": Circle(V(2, -3), 47.0), "arc": Arc(V(-4, 1), 52.0, 0.3, 4.1)}[kind]
        doc, ids = doc_of(target, *others)
        p = target.point_at(t)
        cut = trim_at(doc, ids[0], p)
        removed = cut.removed
        assert removed, "something always goes"
        # what went has the point that was clicked, and nothing crosses it part way along. A circle cut in one place can't be opened there, so all of it goes.
        assert min(g.project(p)[1].distance(p) for g in removed) <= 1e-9
        if kind == "circle" and len(crossings(doc, doc.entities[ids[0]])) < 2:
            assert removed == (target,)
        else:
            for g in removed:
                for o in others:
                    for h in intersect(g, o):
                        assert min(h.point.distance(g.start), h.point.distance(g.end)) <= 1e-6, h.point
        # nothing else changed: what went and what stayed make up the whole of it
        assert sum(g.length() for g in removed + cut.kept) == pytest.approx(target.length(), abs=1e-6)
        for g in cut.kept:
            for u in (0.0, 0.5, 1.0):
                q = g.point_at(u)
                assert target.project(q)[1].distance(q) <= 1e-9

    def test_cuts_are_tidied(self):
        s = line(0, 0, 100, 0)
        c = cut_at(s, [0.5], 0.75)
        assert c.kept == (line(0, 0, 50, 0),)
        assert is_closed(Circle(V(0, 0), 1.0)) and is_closed(Arc(V(0, 0), 1.0, 0.0, TAU)) and not is_closed(s)


class TestTheTrimCommand:
    def setup(self):
        return Person(line(0, 0, 100, 0), line(30, -10, 30, 10), line(70, -10, 70, 10))

    def test_hovering_shows_what_would_go_and_changes_nothing(self):
        with self.setup() as p:
            before = p.doc.fingerprint()
            p.s.run("TRIM")
            p.at(50, 0)
            assert p.s.doomed == [line(30, 0, 70, 0)]
            assert p.doc.fingerprint() == before
            p.at(10, 0)
            assert p.s.doomed == [line(0, 0, 30, 0)]
            p.at(50, 50)
            assert p.s.doomed == []

    def test_clicking_takes_it_and_the_rest_stays_itself(self):
        with self.setup() as p:
            p.doc.transaction  # the first piece keeps the id
            first = p.ids[0]
            p.s.run("TRIM")
            p.click(50, 0)
            assert p.doc.entities[first].shape == line(0, 0, 30, 0)
            assert sum(1 for e in p.doc.entities.values() if e.shape == line(70, 0, 100, 0)) == 1
            assert p.s.request is not None
            p.enter()
            assert p.s.request is None

    def test_one_undo_for_one_click(self):
        with self.setup() as p:
            before = p.doc.fingerprint()
            p.s.run("TRIM")
            p.click(50, 0)
            p.enter()
            p.s.undo()
            assert p.doc.fingerprint() == before

    def test_a_new_piece_keeps_the_look(self):
        with Person() as p:
            with p.doc.transaction("x") as t:
                t.put_layer(Layer("A", 3))
                t.add(line(0, 0, 100, 0), Style("A", 1))
                t.add(line(30, -10, 30, 10))
                t.add(line(70, -10, 70, 10))
            p.s.run("TRIM")
            p.click(50, 0)
            assert all(e.style == Style("A", 1) for e in p.doc.entities.values() if e.shape.a[1] == 0.0 and e.shape.b[1] == 0.0)

    def test_dragging_across_takes_each_piece(self):
        with Person(line(0, 0, 100, 0), line(0, 10, 100, 10), line(0, 20, 100, 20), line(50, -10, 50, 30)) as p:
            p.s.run("TRIM")
            p.drag(25, -2, 25, 22)
            kept = sorted((e.shape.a, e.shape.b) for e in p.doc.entities.values() if e.shape.a[1] == e.shape.b[1])
            assert kept == [(V(50, 0), V(100, 0)), (V(50, 10), V(100, 10)), (V(50, 20), V(100, 20))]

    def test_a_drag_over_nothing_says_nothing(self):
        with self.setup() as p:
            p.s.run("TRIM")
            p.press(0, 50)
            assert p.s.message == "There's nothing here to trim."
            p.at(50, 50)
            p.release(100, 50)
            assert p.s.message == "" and len(p.doc.entities) == 3

    def test_shift_extends_instead(self):
        with Person(line(0, 0, 40, 0), line(100, -10, 100, 10)) as p:
            p.s.run("TRIM")
            p.at(35, 0, shift=True)
            assert p.s.ghosts == [line(40, 0, 100, 0)] and p.s.doomed == []
            p.click(35, 0, shift=True)
            assert p.doc.entities[p.ids[0]].shape == line(0, 0, 100, 0)

    def test_the_extend_command_trims_with_shift(self):
        with Person(line(0, 0, 40, 0), line(100, -10, 100, 10), line(20, -5, 20, 5)) as p:
            p.s.run("EXTEND")
            p.click(35, 0)
            assert p.doc.entities[p.ids[0]].shape == line(0, 0, 100, 0)
            p.click(5, 0, shift=True)
            assert p.doc.entities[p.ids[0]].shape == line(20, 0, 100, 0)

    def test_a_locked_layer_is_left_alone(self):
        with Person() as p:
            with p.doc.transaction("x") as t:
                t.put_layer(Layer("L", locked=True))
                i = t.add(line(0, 0, 100, 0), Style("L"))
            p.s.run("TRIM")
            p.click(50, 0)
            assert p.doc.entities[i].shape == line(0, 0, 100, 0)


# --------------------------------------------------------------------------------------------------------- extend


class TestExtending:
    def test_a_line_reaches_the_next_thing(self):
        doc, ids = doc_of(line(0, 0, 40, 0), line(100, -10, 100, 10), line(200, -10, 200, 10))
        r = extend_at(doc, ids[0], V(35, 0))
        assert r.shape == line(0, 0, 100, 0) and r.added == line(40, 0, 100, 0)

    def test_the_end_you_point_at(self):
        doc, ids = doc_of(line(0, 0, 40, 0), line(100, -10, 100, 10), line(-50, -10, -50, 10))
        assert extend_at(doc, ids[0], V(5, 0)).shape == line(-50, 0, 40, 0)
        assert extend_at(doc, ids[0], V(35, 0)).shape == line(0, 0, 100, 0)

    def test_nothing_in_the_way(self):
        doc, ids = doc_of(line(0, 0, 40, 0), line(100, 10, 100, 50))
        assert extend_at(doc, ids[0], V(35, 0)) is None

    def test_to_a_circle(self):
        doc, ids = doc_of(line(0, 0, 40, 0), Circle(V(100, 0), 10.0))
        assert extend_at(doc, ids[0], V(35, 0)).shape == line(0, 0, 90, 0)

    def test_an_arc_going_on_round(self):
        doc, ids = doc_of(Arc(V(0, 0), 10.0, 0.0, math.pi / 2.0), line(-5, 8, -20, 8))
        r = extend_at(doc, ids[0], V(0, 10))
        assert type(r.shape) is Arc and r.shape.start_angle == 0.0
        assert close(r.shape.end, (-6.0, 8.0), 1e-9) and close(r.added.start, (0, 10), 1e-9)

    def test_an_arc_going_back_from_its_start(self):
        doc, ids = doc_of(Arc(V(0, 0), 10.0, 0.0, math.pi / 2.0), line(5, -8, 20, -8))
        r = extend_at(doc, ids[0], V(10, 0))
        assert close(r.shape.start, (6.0, -8.0), 1e-9) and close(r.shape.end, (0, 10), 1e-9)

    def test_a_backward_arc(self):
        doc, ids = doc_of(Arc(V(0, 0), 10.0, math.pi / 2.0, -math.pi / 2.0), line(5, -8, 20, -8))
        r = extend_at(doc, ids[0], V(10, 0))
        assert r.shape.sweep < 0.0 and close(r.shape.end, (6.0, -8.0), 1e-9) and close(r.shape.start, (0, 10), 1e-9)

    def test_an_elliptical_arc(self):
        e = EllipseArc(V(0, 0), V(20, 0), 0.5, 0.0, math.pi / 2.0)
        doc, ids = doc_of(e, line(-30, 5, -5, 5))
        r = extend_at(doc, ids[0], V(0, 10))
        assert type(r.shape) is EllipseArc and abs(r.shape.end[1] - 5.0) < 1e-9 and r.shape.end[0] < 0.0

    def test_the_end_of_a_polyline(self):
        s = Polyline((V(0, 0), V(40, 0), V(40, 40)))
        doc, ids = doc_of(s, line(30, 100, 50, 100))
        assert extend_at(doc, ids[0], V(40, 35)).shape.pts == (V(0, 0), V(40, 0), V(40, 100))

    def test_the_start_of_a_polyline_with_an_arc(self):
        s = Polyline((V(10, 0), V(0, 10), V(0, 50)), (math.tan(math.pi / 8.0), 0.0, 0.0))
        doc, ids = doc_of(s, line(5, -20, 5, -5))
        r = extend_at(doc, ids[0], V(10, 0))
        assert r is not None and r.shape.pts[1:] == s.pts[1:]
        assert r.shape.pts[0][0] == pytest.approx(5.0)
        assert abs(r.shape.segments[0].sweep) > abs(s.segments[0].sweep)

    def test_closed_things_have_no_ends(self):
        doc, ids = doc_of(Circle(V(0, 0), 5.0), rectangle(0, 0, 1, 1), line(100, 0, 100, 1))
        assert extend_at(doc, ids[0], V(5, 0)) is None and extend_at(doc, ids[1], V(0, 0)) is None


# ---------------------------------------------------------------------------------------------------------- break


class TestBreaking:
    def test_splitting_a_line(self):
        assert break_at(line(0, 0, 100, 0), 0.25) == (line(0, 0, 25, 0), line(25, 0, 100, 0))

    def test_taking_out_a_stretch(self):
        assert break_at(line(0, 0, 100, 0), 0.25, 0.75) == (line(0, 0, 25, 0), line(75, 0, 100, 0))
        assert break_at(line(0, 0, 100, 0), 0.75, 0.25) == (line(0, 0, 25, 0), line(75, 0, 100, 0))

    def test_a_circle_needs_two_points(self):
        with pytest.raises(ValueError, match="second point"):
            break_at(Circle(V(0, 0), 10.0), 0.25)
        kept = break_at(Circle(V(0, 0), 10.0), 0.0, 0.25)
        assert len(kept) == 1 and close(kept[0].start, (0, 10)) and close(kept[0].end, (10, 0))
        assert kept[0].sweep == pytest.approx(TAU * 0.75)

    def test_a_circle_the_other_way(self):
        kept = break_at(Circle(V(0, 0), 10.0), 0.25, 0.0)
        assert len(kept) == 1 and close(kept[0].start, (10, 0)) and close(kept[0].end, (0, 10))
        assert kept[0].sweep == pytest.approx(TAU * 0.25)

    def test_a_closed_polyline_opens_where_you_say(self):
        s = rectangle(0, 0, 10, 10)
        out = break_at(s, 0.5)
        assert len(out) == 1 and not out[0].closed and out[0].start == V(10, 10) and out[0].end == V(10, 10)
        assert len(out[0].pts) == 5

    def test_a_closed_spline_splits_in_two(self):
        s = Spline(3, (0, 0, 0, 0, 1, 1, 1, 1), (V(0, 0), V(10, 10), V(-10, 10), V(0, 0)))
        assert is_closed(s) and len(break_at(s, 0.5)) == 2

    def test_the_command(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.s.run("BREAK")
            p.click(50, 0)
            p.click(25, 0)
            p.enter()
            assert sorted(e.shape.b[0] for e in p.doc.entities.values()) == [25.0, 100.0]

    def test_the_command_with_two_points(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.s.run("BREAK")
            p.click(50, 0)
            p.click(25, 0)
            p.at(75, 0)
            assert p.s.ghosts == [line(0, 0, 25, 0), line(75, 0, 100, 0)]
            p.click(75, 0)
            assert sorted((e.shape.a, e.shape.b) for e in p.doc.entities.values()) == [(V(0, 0), V(25, 0)), (V(75, 0), V(100, 0))]

    def test_the_command_says_what_cant_be_broken(self):
        with Person(Text(V(0, 0), 5.0, "x")) as p:
            p.s.run("BREAK")
            p.click(1, 1)
            assert "can be broken" in p.s.message


# --------------------------------------------------------------------------------------------------------- offset


def distance_to(s, p):
    return s.project(p)[1].distance(p)


class TestOffsettingSimpleThings:
    def test_a_line_to_either_side(self):
        assert offset(line(0, 0, 100, 0), 5.0, V(50, 20)).shapes == (line(0, 5, 100, 5),)
        assert offset(line(0, 0, 100, 0), 5.0, V(50, -20)).shapes == (line(0, -5, 100, -5),)

    def test_a_circle_in_and_out(self):
        c = Circle(V(0, 0), 10.0)
        assert offset(c, 2.0, V(20, 0)).shapes == (Circle(V(0, 0), 12.0),)
        assert offset(c, 2.0, V(1, 0)).shapes == (Circle(V(0, 0), 8.0),)
        with pytest.raises(ValueError, match="radius"):
            offset(c, 10.0, V(1, 0))

    def test_an_arc_keeps_its_angles(self):
        a = Arc(V(0, 0), 10.0, 0.5, 1.0)
        out = offset(a, 3.0, V(0, 0)).shapes[0]
        assert (out.r, out.start_angle, out.sweep) == (7.0, 0.5, 1.0)
        assert offset(a, 3.0, V(100, 100)).shapes[0].r == 13.0

    def test_nothing_exact_says_so_and_things_exact_dont(self):
        assert offset(line(0, 0, 1, 0), 1.0, V(0, 1)).within == 0.0
        assert offset(rectangle(0, 0, 10, 10), 1.0, V(5, 5)).within == 0.0

    def test_no_distance(self):
        with pytest.raises(ValueError):
            offset(line(0, 0, 1, 0), 0.0, V(0, 1))

    def test_the_side(self):
        assert is_left(line(0, 0, 10, 0), V(5, 1)) and not is_left(line(0, 0, 10, 0), V(5, -1))
        assert is_left(Arc(V(0, 0), 10.0, 0.0, 1.0), V(1, 1))


class TestOffsettingPolylines:
    def test_a_rectangle_inward_and_outward(self):
        r = rectangle(0, 0, 100, 50)
        inner = offset(r, 5.0, V(50, 25)).shapes
        assert len(inner) == 1 and inner[0].closed
        assert set(inner[0].pts) == {V(5, 5), V(95, 5), V(95, 45), V(5, 45)}
        outer = offset(r, 5.0, V(-20, 25)).shapes[0]
        for p, q in zip(sorted(outer.pts), sorted([V(-5, -5), V(105, -5), V(105, 55), V(-5, 55)])):
            assert close(p, q, 1e-9)

    def test_going_clockwise_makes_no_difference(self):
        r = Polyline((V(0, 0), V(0, 50), V(100, 50), V(100, 0)), closed=True)
        inner = offset(r, 5.0, V(50, 25)).shapes[0]
        assert set(inner.pts) == {V(5, 5), V(95, 5), V(95, 45), V(5, 45)}

    def test_an_l_shape_loses_its_short_arm(self):
        s = Polyline((V(0, 0), V(100, 0), V(100, 20), V(50, 20), V(50, 100), V(0, 100)), closed=True)
        out = offset(s, 15.0, V(10, 10)).shapes
        assert len(out) == 1 and set(out[0].pts) == {V(35, 15), V(35, 85), V(15, 85), V(15, 15)}

    def test_too_far_in_is_nothing(self):
        with pytest.raises(ValueError, match="nothing left"):
            offset(rectangle(0, 0, 100, 50), 30.0, V(50, 25))

    def test_a_waist_splits_it_in_two(self):
        s = Polyline((V(0, 0), V(40, 0), V(40, 18), V(60, 18), V(60, 0), V(100, 0), V(100, 50), V(60, 50), V(60, 32), V(40, 32), V(40, 50), V(0, 50)), closed=True)
        out = offset(s, 10.0, V(10, 10)).shapes
        assert len(out) == 2 and all(p.closed for p in out)
        boxes = sorted(p.bbox() for p in out)
        assert boxes[0] == pytest.approx((10.0, 10.0, 30.0, 40.0)) and boxes[1] == pytest.approx((70.0, 10.0, 90.0, 40.0))

    def test_an_open_path(self):
        s = Polyline((V(0, 0), V(100, 0), V(100, 100)))
        left = offset(s, 10.0, V(50, 50)).shapes[0]
        assert left.pts == (V(0, 10), V(90, 10), V(90, 100)) and not left.closed
        right = offset(s, 10.0, V(50, -50)).shapes[0]
        assert right.pts[0] == V(0, -10) and close(right.pts[1], (110, -10), 1e-9) and right.pts[2] == V(110, 100)

    def test_an_open_path_with_a_short_last_piece(self):
        s = Polyline((V(0, 0), V(10, 0), V(10, 1)))
        out = offset(s, 3.0, V(5, 2)).shapes
        assert len(out) == 1
        for t in (0.0, 0.25, 0.5, 0.75, 1.0):
            assert distance_to(s, out[0].point_at(t)) >= 3.0 - 1e-6
        assert out[0].start == V(0, 3) and out[0].end[0] == pytest.approx(10.0 - math.sqrt(5.0))

    def test_arcs_stay_arcs(self):
        # a slot: two straights and two half circles
        s = Polyline((V(0, 0), V(100, 0), V(100, 20), V(0, 20)), (0.0, 1.0, 0.0, 1.0), True)
        outer = offset(s, 5.0, V(50, 100)).shapes[0]
        assert outer.closed and outer.bulges == pytest.approx((0.0, 1.0, 0.0, 1.0))
        assert outer.bbox() == pytest.approx((-15.0, -5.0, 115.0, 25.0))
        inner = offset(s, 5.0, V(50, 10)).shapes[0]
        assert inner.bbox() == pytest.approx((-5.0, 5.0, 105.0, 15.0))

    def test_an_arc_that_closes_up_goes(self):
        # a small bump out of the top: going in further than its radius, it shrinks to nothing, and the two sides of it carry on until they're one line
        s = Polyline((V(0, 0), V(100, 0), V(100, 100), V(60, 100), V(40, 100), V(0, 100)), (0.0, 0.0, 0.0, 1.0, 0.0, 0.0), True)
        out = offset(s, 20.0, V(50, 50)).shapes
        assert len(out) == 1
        assert set(out[0].pts) == {V(20, 20), V(80, 20), V(80, 80), V(20, 80)} and not any(out[0].bulges)

    def test_an_arc_that_closes_up_next_to_something_that_cant_carry_on(self):
        # the same bump with an arc beside it: the corner it stood on stays round, exactly the distance from it
        s = Polyline((V(0, 0), V(100, 0), V(100, 100), V(60, 100), V(40, 100), V(0, 100)), (0.0, 0.0, 0.5, 1.0, 0.0, 0.0), True)
        out = offset(s, 20.0, V(50, 50)).shapes
        assert len(out) == 1
        assert any(type(g) is Arc and close(g.c, (60, 100), 1e-9) and g.r == pytest.approx(20.0) for g in out[0].segments)
        for g in out[0].segments:
            assert distance_to(s, g.point_at(0.5)) >= 20.0 - 1e-6

    def test_a_bump_inward_grows_with_it(self):
        s = Polyline((V(0, 0), V(100, 0), V(100, 100), V(60, 100), V(40, 100), V(0, 100)), (0.0, 0.0, 0.0, -1.0, 0.0, 0.0), True)
        out = offset(s, 20.0, V(50, 50)).shapes
        assert any(type(g) is Arc and close(g.c, (50, 100), 1e-9) and g.r == pytest.approx(30.0) for g in out[0].segments)

    def test_a_corner_with_an_arc_on_the_outside_is_rounded(self):
        s = Polyline((V(0, 0), V(50, 0), V(100, 0)), (0.0, 1.0, 0.0))
        out = offset(s, 10.0, V(25, -50)).shapes[0]
        for t in (0.1, 0.3, 0.5, 0.7, 0.9):
            assert distance_to(s, out.point_at(t)) == pytest.approx(10.0, abs=1e-6)

    def test_a_width_stays(self):
        s = Polyline((V(0, 0), V(100, 0), V(100, 100)), width=2.0)
        assert offset(s, 5.0, V(50, 50)).shapes[0].width == 2.0

    @pytest.mark.parametrize("pts, bulges, closed, d, side", [
        # each of these once left a stretch that was closer than the distance
        (((40, -25), (-50, 95), (60, -70), (-95, -20), (-95, 50), (35, -20), (-15, -85), (-35, -55), (45, -40)),
         (0, 1, 0, 0, 0, 0, 0.6057061422897956, -1, 0), False, 23.19752699581763, (24.49615397009326, -15.479838526991244)),
        (((38.114612582671015, -40.98459411412483), (-81.84028152345712, 81.50992260485708), (-88.92740006151816, 26.026665473482623),
          (-94.5977172364197, 16.08585998330392), (4.53715051688792, -49.01831855753856)),
         (0, 0, -1, 0, 0), True, 39.82298155465408, (15.756425111531808, -47.362225543898305)),
        (((-27.810742444612075, -85.48378835546602), (-60.06271606451801, -46.93685999977188), (-71.85622343916829, -56.110028650330904),
          (45.1144186034729, 6.8310835177584295)),
         (0, -1, 0, -1.410840102578224), True, 10.0, (13.93996980420733, -8.808095692457442)),
        (((-80, -45), (-50, 20), (-70, 90), (-30, -10), (-65, 95), (-70, -25)), (0, 0.09386999725976675, 0, 0, 0, 1), True, 11.363166988257207,
         (-49.694144282251784, 18.276383877908412)),
        # a nearly straight arc, whose crossings with a small circle were a hundredth out
        (((-2, 0), (1, 1), (0, 0)), (5.960464477539063e-08, 0, 0), False, 1.0, (-1.683772346609365, -0.9486833357477944)),
        # the path comes back to a corner it's been round before: the corner going round there runs exactly the distance from the first piece
        (((100, 75), (-50, 75), (75, 75), (-25, 50), (75, 50), (50, -75), (-75, 100), (25, 75), (-100, -75), (0, 0), (-50, -50), (-100, 75), (100, 75), (-50, -75)),
         (-1, 0, 0, 0, -1, 1, 6e-08, 0, -1, -1, 1, 1e-06, 1, 1), False, 5.0, (-28.2218706317464, 12.69580517708565)),
        # a crossing near the end of a nearly straight arc was moved to the end
        (((75, 75), (-100, 75), (-25, -25), (75, 25), (0, 25), (-25, -25), (25, -50), (25, -100), (25, 25), (-75, -75), (-25, 0), (-25, -25), (-25, -75)),
         (-1, 0, 0.76950402532625, 0, 0, -3e-07, 0, -3e-07, -1, 1e-06, 0, 0, 0), False, 1.0, (-26.0, -20.52358904122844)),
        # the path crosses itself at a corner: going round it, the one place it's far enough is the middle, which is where it was tested
        (((-100, 25), (25, -25), (75, 0), (-25, 50), (50, -50), (-25, -100), (-50, 25), (-75, -50), (-50, -25), (25, 25), (50, 75)),
         (-1, 6e-08, 0, -1, -1, 0, -1, -1, 1, 0, -1.156785434505073), True, 1.9790058553623682, (-72.23434427635561, -21.55955205871836)),
    ])
    def test_ones_that_came_out_too_close(self, pts, bulges, closed, d, side):
        s = Polyline(tuple(V(*p) for p in pts), tuple(float(b) for b in bulges), closed)
        for piece in offset(s, d, V(*side)).shapes:
            for g in piece.segments:
                for i in range(33):
                    assert distance_to(s, g.point_at(i / 32)) >= d - 1e-6

    def test_corners_carried_on_never_cross(self):
        # two sharp turns whose corners, carried on, would cross: the second stays round
        s = Polyline((V(50, 60), V(-30, -60), V(-15, 55), V(-90, 70), V(-55, -95), V(-95, -35)))
        out = offset(s, 10.0, V(-19.488286336248922, -46.03520514210537)).shapes
        segs = [g for p in out for g in p.segments]
        for i, a in enumerate(segs):
            for b in segs[i + 1:]:
                for h in intersect(a, b):
                    assert min(h.point.distance(e) for e in (a.start, a.end, b.start, b.end)) <= 1e-6, h.point
        for g in segs:
            assert distance_to(s, g.point_at(0.5)) >= 10.0 - 1e-6

    def test_a_closed_offset_starts_where_the_original_does(self):
        s = Polyline((V(0, 0), V(100, 0), V(100, 20), V(0, 20)), (0.0, 1.0, 0.0, 1.0), True)
        assert offset(s, 5.0, V(50, 100)).shapes[0].pts[0] == V(0, -5)

    def test_an_open_path_that_comes_round_to_itself_closes(self):
        s = Polyline((V(1, -1), V(3, 0), V(0, 4), V(0, 0)))
        out = offset(s, 1.0, V(1.5, 1.0)).shapes
        assert len(out) == 1 and out[0].closed

    @pytest.mark.parametrize("make, d, side", [
        (lambda n: Polyline(tuple(V(i * 0.02, 0.1 * (i % 2)) for i in range(n))), 10.0, V(0, 50)),
        (lambda n: Polyline(tuple(V(100 * math.cos(TAU * i / n), 100 * math.sin(TAU * i / n)) for i in range(n)), closed=True), 10.0, V(500, 0)),
        (lambda n: Polyline(tuple(V(100 * math.cos(TAU * i / n), 100 * math.sin(TAU * i / n)) for i in range(n)), closed=True), 150.0, V(0, 0)),
    ])
    def test_work_grows_with_the_size_not_its_square(self, make, d, side):
        # how many pieces are measured against each other, not how long it takes: a dense zigzag offset well clear of it once measured each piece against
        # every one within reach, and a circle offset further in than its radius did too
        from draftworx.core.ops import offset as module
        counts = []
        for n in (500, 2000):
            calls = [0]
            real = module._Original._too_close_to

            def counted(self, g, k, limit, real=real, calls=calls):
                calls[0] += 1
                return real(self, g, k, limit)
            with mock.patch.object(module._Original, "_too_close_to", counted):
                try:
                    offset(make(n), d, side)
                except ValueError:
                    pass
            counts.append(calls[0])
        assert counts[1] <= counts[0] * 4 * 1.5, counts

    def test_going_back_the_same_way(self):
        s = Polyline((V(0, 0), V(100, 0), V(0, 0.0)))
        out = offset(s, 5.0, V(50, 50)).shapes
        assert out

    @given(st.lists(st.tuples(st.floats(-100, 100), st.floats(-100, 100)), min_size=3, max_size=9),
           st.lists(st.floats(-1.5, 1.5), min_size=9, max_size=9),
           st.floats(0.1, 40.0), st.booleans(), st.booleans(), st.floats(0.0, 1.0))
    @settings(max_examples=250, deadline=None)
    def test_nothing_is_ever_closer_than_the_distance(self, pts, bulges, d, closed, left, at):
        points = [Vec(x, y) for x, y in pts]
        for a, b in zip(points, points[1:] + points[:1]):
            assume(a.distance(b) > 1.0)
        s = Polyline(tuple(points), tuple(bulges[:len(points)]), closed)
        away = s.point_at(at)
        t = s.tangent_at(at).unit().perp()
        try:
            out = offset(s, d, away + t * (1.0 if left else -1.0)).shapes
        except ValueError:
            return
        for piece in out:
            for g in piece.segments:
                for u in (0.0, 0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9, 1.0):
                    q = g.point_at(u)
                    assert distance_to(s, q) >= d - 1e-5 * max(d, 1.0), (q, distance_to(s, q), d)

    @given(st.lists(st.tuples(st.integers(-20, 20), st.integers(-20, 20)), min_size=3, max_size=9),
           st.lists(st.sampled_from((0.0, 0.0, 1.0, -1.0, 0.5)), min_size=9, max_size=9),
           st.sampled_from((1.0, 5.0, 10.0, 12.5)), st.booleans(), st.booleans(), st.floats(0.0, 1.0))
    @settings(max_examples=150, deadline=None)
    def test_nothing_far_enough_is_left_out(self, pts, bulges, d, closed, left, at):
        # Before corners carry on, the result is everything on the offset pieces that's at least the distance from the original. Carrying a corner on can only
        # leave out what's behind it.
        from draftworx.core.ops import offset as module
        points = [Vec(x * 5.0, y * 5.0) for x, y in pts]
        for a, b in zip(points, points[1:] + points[:1]):
            assume(a.distance(b) > 1.0)
        s = Polyline(tuple(points), tuple(bulges[:len(points)]), closed)
        side = s.point_at(at) + s.tangent_at(at).unit().perp() * (1.0 if left else -1.0)
        signed = d if module.is_left(s, side) else -d
        with mock.patch.object(module, "_sharpened", lambda run, *rest: run):
            try:
                out = offset(s, d, side).shapes
            except ValueError:
                out = ()
        segs = list(s.segments)
        for g in segs:
            moved = module._shifted(g, signed)
            for i in range(1, 16):
                q = moved.point_at(i / 16)
                if min(distance_to(h, q) for h in segs if h is not g) > d + 1e-6 and distance_to(s, q) >= d - 1e-9:
                    assert any(distance_to(o, q) <= 1e-6 for o in out), (q, d)

    @given(st.floats(10.0, 500.0), st.floats(10.0, 500.0), st.floats(0.1, 4.0))
    def test_a_rectangle_out_and_back_is_the_same(self, w, h, d):
        r = rectangle(0, 0, w, h)
        out = offset(r, d, V(-1, -1)).shapes[0]
        back = offset(out, d, V(w / 2.0, h / 2.0)).shapes[0]
        tidy = lambda p: (round(p[0], 6), round(p[1], 6))
        assert all(close(p, q, 1e-9) for p, q in zip(sorted(back.pts, key=tidy), sorted(r.pts, key=tidy)))

    @given(st.integers(3, 12), st.floats(20.0, 100.0), st.floats(0.5, 10.0))
    def test_a_regular_polygon_outward_has_the_right_area(self, n, r, d):
        pts = tuple(V(r * math.cos(TAU * i / n), r * math.sin(TAU * i / n)) for i in range(n))
        out = offset(Polyline(pts, closed=True), d, V(1000, 1000)).shapes[0]
        apothem = r * math.cos(math.pi / n)
        grown = (apothem + d) / apothem
        assert abs(out.signed_area()) == pytest.approx(abs(Polyline(pts, closed=True).signed_area()) * grown * grown, rel=1e-6)


class TestOffsettingCurves:
    def test_an_ellipse_is_a_spline_within_what_it_says(self):
        e = EllipseArc(V(0, 0), V(40, 0), 0.5, 0.0, TAU)
        out = offset(e, 5.0, V(100, 0))
        assert out.within > 0.0 and all(type(s) is Spline for s in out.shapes)
        s = out.shapes[0]
        for i in range(40):
            q = s.point_at(i / 40)
            assert distance_to(e, q) == pytest.approx(5.0, abs=max(out.within * 4.0, 1e-3))
        assert out.within < 40.0 * 2e-5

    def test_inside_a_tight_bend_it_folds_and_the_fold_is_left_out(self):
        e = EllipseArc(V(0, 0), V(40, 0), 0.25, 0.0, TAU)
        out = offset(e, 8.0, V(0, 0))
        for s in out.shapes:
            for i in range(21):
                assert distance_to(e, s.point_at(i / 20)) >= 8.0 - 0.01

    def test_too_far_in(self):
        e = EllipseArc(V(0, 0), V(40, 0), 0.25, 0.0, TAU)
        with pytest.raises(ValueError, match="nothing left"):
            offset(e, 50.0, V(0, 0))

    def test_a_spline(self):
        s = spline_through([V(0, 0), V(30, 20), V(60, -20), V(90, 0)])
        out = offset(s, 3.0, V(0, 100))
        for piece in out.shapes:
            for i in range(21):
                assert distance_to(s, piece.point_at(i / 20)) == pytest.approx(3.0, abs=max(out.within * 4.0, 1e-3))


class TestTheOffsetCommand:
    def test_type_hover_click(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.s.run("OFFSET")
            p.type("5")
            p.enter()
            p.at(50, 2)
            assert p.s.ghosts == [line(0, 5, 100, 5)]
            p.at(50, -2)
            assert p.s.ghosts == [line(0, -5, 100, -5)]
            p.click(50, 2)
            assert any(e.shape == line(0, 5, 100, 5) for e in p.doc.entities.values())
            assert p.s.request is not None
            p.enter()

    def test_it_remembers_the_distance(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.s.run("OFFSET")
            p.type("5")
            p.enter()
            p.enter()
            p.s.run("OFFSET")
            p.click(50, 2)
            assert any(e.shape == line(0, 5, 100, 5) for e in p.doc.entities.values())

    def test_through_a_point(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.s.run("OFFSET")
            p.click(50, 0)
            p.at(50, 20)
            assert p.s.ghosts == [line(0, 20, 100, 20)]
            p.click(50, 20)
            assert any(e.shape == line(0, 20, 100, 20) for e in p.doc.entities.values())

    def test_a_bad_distance_says_so(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.s.run("OFFSET")
            p.type("-3")
            p.enter()
            assert "above zero" in p.s.message

    def test_too_far_says_so(self):
        with Person(Circle(V(0, 0), 5.0)) as p:
            p.s.run("OFFSET")
            p.type("10")
            p.enter()
            p.click(4, 0)
            assert "radius" in p.s.message and len(p.doc.entities) == 1

    def test_a_curve_says_how_close(self):
        with Person(EllipseArc(V(0, 0), V(40, 0), 0.5, 0.0, TAU)) as p:
            p.s.run("OFFSET")
            p.type("5")
            p.enter()
            p.click(41, 0)
            assert "spline within" in p.s.message

    def test_a_preview_that_takes_too_long_is_given_up_but_a_click_is_not(self):
        from draftworx.core.commands import edit
        r = Polyline((V(0, 0), V(100, 0), V(100, 50), V(0, 50)), closed=True)
        with Person(r) as p, mock.patch.object(edit, "QUICK", -1.0):
            p.s.run("OFFSET")
            p.type("5")
            p.enter()
            p.at(50, 2)
            assert p.s.ghosts == [] and p.s.hover is not None
            p.at(50, 3)
            assert p.s.ghosts == []
            p.click(50, 2)
            assert len(p.doc.entities) == 2

    def test_each_side_is_worked_out_once(self):
        from draftworx.core.commands import edit
        r = Polyline((V(0, 0), V(100, 0), V(100, 50), V(0, 50)), closed=True)
        with Person(r) as p, mock.patch.object(edit, "offset", wraps=edit.offset) as counted:
            p.s.run("OFFSET")
            p.type("5")
            p.enter()
            for x in (40, 50, 60):
                p.at(x, 2)
            p.at(50, -2)
            p.at(55, -2)
            assert counted.call_count == 2
            p.click(50, 2)
            assert len(p.doc.entities) == 2
            assert all(c.args[3] is not None for c in counted.call_args_list)      # the click used what the preview had worked out

    def test_it_keeps_the_look(self):
        with Person() as p:
            with p.doc.transaction("x") as t:
                t.put_layer(Layer("A"))
                t.add(line(0, 0, 100, 0), Style("A", 2))
            p.s.run("OFFSET")
            p.type("5")
            p.enter()
            p.click(50, 2)
            assert all(e.style == Style("A", 2) for e in p.doc.entities.values())


# ----------------------------------------------------------------------------------------------- fillet and chamfer


def tangent_to(curve, c, r, eps=1e-9):
    """Is a circle at c with radius r tangent to this curve, carried on as far as it goes?"""
    if type(curve) is Line:
        d = (curve.b - curve.a).unit()
        return abs(abs((c - curve.a).cross(d)) - r) <= eps
    gap = curve.c.distance(c)
    return abs(gap - (curve.r + r)) <= eps or abs(gap - abs(curve.r - r)) <= eps


class TestFilleting:
    def test_a_corner(self):
        c = fillet(line(0, 0, 100, 0), V(50, 0), line(0, 0, 0, 100), V(0, 50), 10.0)
        assert close(c.a.a, (10, 0)) and c.a.b == V(100, 0) and close(c.b.a, (0, 10)) and c.b.b == V(0, 100)
        assert close(c.join.c, (10, 10)) and c.join.r == 10.0
        assert close(c.join.point_at(0.5), (10 - 10 * math.sqrt(0.5), 10 - 10 * math.sqrt(0.5)))

    def test_lines_that_stop_short(self):
        c = fillet(line(5, 0, 100, 0), V(50, 0), line(0, 5, 0, 100), V(0, 50), 2.0)
        assert close(c.a.a, (2, 0)) and close(c.b.a, (0, 2))

    def test_lines_that_go_past_each_other_keep_the_side_you_pointed_at(self):
        a, b = line(-50, 0, 50, 0), line(0, -50, 0, 50)
        c = fillet(a, V(25, 0), b, V(0, 25), 5.0)
        assert close(c.a.a, (5, 0)) and c.a.b == V(50, 0) and close(c.b.a, (0, 5)) and c.b.b == V(0, 50)
        c = fillet(a, V(-25, 0), b, V(0, -25), 5.0)
        assert c.a.a == V(-50, 0) and close(c.a.b, (-5, 0)) and c.b.a == V(0, -50) and close(c.b.b, (0, -5))
        assert close(c.join.c, (-5, -5))

    def test_a_sharp_corner_with_no_radius(self):
        c = fillet(line(10, 0, 100, 0), V(50, 0), line(0, 10, 0, 100), V(0, 50), 0.0)
        assert c.a == line(0, 0, 100, 0) and c.b == line(0, 0, 0, 100) and c.join is None

    def test_parallel_lines_get_a_half_circle(self):
        c = fillet(line(0, 0, 100, 0), V(90, 0), line(0, 20, 100, 20), V(90, 20), 3.0)
        assert c.join.r == 10.0 and c.join.c == V(100, 10)
        assert close(c.join.point_at(0.5), (110, 10))

    def test_parallel_lines_of_different_lengths_meet_up(self):
        c = fillet(line(0, 0, 100, 0), V(90, 0), line(0, 20, 60, 20), V(50, 20), 3.0)
        assert c.b == line(0, 20, 100, 20)

    def test_lines_on_top_of_each_other(self):
        with pytest.raises(ValueError, match="on top"):
            fillet(line(0, 0, 100, 0), V(50, 0), line(0, 0, 50, 0), V(25, 0), 3.0)

    def test_a_line_and_a_circle_leaves_the_circle_whole(self):
        circle = Circle(V(50, 30), 10.0)
        c = fillet(line(0, 0, 100, 0), V(20, 0), circle, V(40, 30), 15.0)
        assert c.b is circle
        assert tangent_to(line(0, 0, 100, 0), c.join.c, 15.0) and tangent_to(circle, c.join.c, 15.0)
        assert close(c.a.a, (0, 0)) and c.a.b[1] == 0.0

    def test_a_line_and_an_arc(self):
        arc = Arc(V(50, 0), 20.0, 0.0, math.pi)
        c = fillet(line(0, 40, 100, 40), V(90, 40), arc, V(50 + 20 * math.cos(1.0), 20 * math.sin(1.0)), 15.0)
        assert tangent_to(line(0, 40, 100, 40), c.join.c, 15.0) and tangent_to(arc, c.join.c, 15.0)
        for end in (c.join.start, c.join.end):
            assert abs(end.distance(c.join.c) - 15.0) < 1e-9
        assert close(c.join.start, c.a.start, 1e-9) or close(c.join.start, c.a.end, 1e-9)
        assert close(c.join.end, c.b.start, 1e-9) or close(c.join.end, c.b.end, 1e-9)

    def test_two_arcs(self):
        a, b = Arc(V(0, 0), 20.0, -1.0, 2.0), Arc(V(50, 0), 20.0, math.pi - 1.0, 2.0)
        c = fillet(a, a.point_at(0.8), b, b.point_at(0.2), 10.0)
        assert tangent_to(a, c.join.c, 10.0) and tangent_to(b, c.join.c, 10.0)

    def test_an_arc_that_has_to_grow(self):
        a = Arc(V(0, 0), 20.0, 0.0, 0.2)
        c = fillet(a, a.point_at(0.5), line(-50, 15, 50, 15), V(-20, 15), 2.0)
        assert c.a.sweep != 0.2 and tangent_to(a, c.join.c, 2.0)
        assert close(c.join.start, c.a.end, 1e-9)

    def test_a_radius_too_big_to_reach(self):
        with pytest.raises(ValueError, match="radius"):
            fillet(Circle(V(0, 0), 1.0), V(1, 0), Circle(V(100, 0), 1.0), V(99, 0), 2.0)

    def test_what_it_wont_do(self):
        with pytest.raises(ValueError, match="Only lines"):
            fillet(Polyline((V(0, 0), V(1, 0))), V(0, 0), line(0, 0, 0, 1), V(0, 0), 1.0)

    @given(st.floats(-100, 100), st.floats(-100, 100), st.floats(0.05, 3.09), st.floats(0.05, 20.0))
    def test_two_lines_at_any_angle(self, x, y, angle, r):
        at = V(x, y)
        ua, ub = V(1, 0), Vec(math.cos(angle), math.sin(angle))
        back = r / math.tan(angle / 2.0)
        assume(back < 800.0)
        a, b = Line(at, at + ua * 1000.0), Line(at, at + ub * 1000.0)
        c = fillet(a, at + ua * 900.0, b, at + ub * 900.0, r)
        assert tangent_to(a, c.join.c, r, 1e-6) and tangent_to(b, c.join.c, r, 1e-6)
        assert close(c.a.a, c.join.start, 1e-6) and close(c.b.a, c.join.end, 1e-6)
        assert c.a.length() == pytest.approx(1000.0 - back, abs=1e-6)
        # it's in the corner, not across it
        assert (c.join.point_at(0.5) - at).length() < (c.join.c - at).length()


class TestChamfering:
    def test_a_corner(self):
        c = chamfer(line(0, 0, 100, 0), V(50, 0), line(0, 0, 0, 100), V(0, 50), 10.0, 5.0)
        assert c.a == line(10, 0, 100, 0) and c.b == line(0, 5, 0, 100) and c.join == line(10, 0, 0, 5)

    def test_nothing_is_a_sharp_corner(self):
        c = chamfer(line(5, 0, 100, 0), V(50, 0), line(0, 5, 0, 100), V(0, 50), 0.0, 0.0)
        assert c.a == line(0, 0, 100, 0) and c.join is None

    def test_parallel(self):
        with pytest.raises(ValueError, match="parallel"):
            chamfer(line(0, 0, 100, 0), V(50, 0), line(0, 10, 100, 10), V(50, 10), 1.0, 1.0)

    def test_only_lines(self):
        with pytest.raises(ValueError, match="straight"):
            chamfer(Arc(V(0, 0), 1.0, 0.0, 1.0), V(1, 0), line(0, 0, 1, 1), V(1, 1), 1.0, 1.0)


class TestCornersOfAPolyline:
    def make(self, r):
        return lambda a, pa, b, pb: fillet(a, pa, b, pb, r)

    def test_one_corner(self):
        s = Polyline((V(0, 0), V(100, 0), V(100, 100)))
        out = corner_of_polyline(s, 1, self.make(10.0))
        assert all(close(p, q) for p, q in zip(out.pts, (V(0, 0), V(90, 0), V(100, 10), V(100, 100))))
        assert out.bulges[1] == pytest.approx(math.tan(math.pi / 8.0))
        assert close(out.segments[1].c, (90, 10))

    def test_the_corner_at_the_start_of_a_closed_one(self):
        s = rectangle(0, 0, 100, 100)
        out = corner_of_polyline(s, 0, self.make(10.0))
        assert out.closed and len(out.pts) == 5
        assert abs(out.signed_area()) == pytest.approx(10000.0 - (4.0 - math.pi) * 100.0 / 4.0)

    def test_the_end_isnt_a_corner(self):
        with pytest.raises(ValueError, match="end of the polyline"):
            corner_of_polyline(Polyline((V(0, 0), V(100, 0), V(100, 100))), 0, self.make(10.0))

    def test_no_room(self):
        with pytest.raises(ValueError, match="room"):
            corner_of_polyline(Polyline((V(0, 0), V(5, 0), V(5, 5))), 1, self.make(10.0))

    def test_every_corner(self):
        out, skipped = every_corner(rectangle(0, 0, 100, 50), self.make(10.0))
        assert skipped == 0 and out.closed and len(out.pts) == 8
        assert abs(out.signed_area()) == pytest.approx(5000.0 - (4.0 - math.pi) * 100.0)

    def test_every_corner_says_which_had_no_room(self):
        s = Polyline((V(0, 0), V(100, 0), V(100, 3), V(200, 3), V(200, 100)))
        out, skipped = every_corner(s, self.make(10.0))
        assert skipped == 2

    def test_a_smooth_join_isnt_a_corner(self):
        s = Polyline((V(0, 0), V(100, 0), V(100, 20), V(0, 20)), (0.0, 1.0, 0.0, 0.0))
        out, skipped = every_corner(s, self.make(2.0))
        assert skipped == 0 and out == s

    def test_a_chamfer(self):
        out = corner_of_polyline(Polyline((V(0, 0), V(100, 0), V(100, 100))), 1, lambda a, pa, b, pb: chamfer(a, pa, b, pb, 10.0, 10.0))
        assert out.pts == (V(0, 0), V(90, 0), V(100, 10), V(100, 100)) and all(b == 0.0 for b in out.bulges)


class TestTheCornerCommands:
    def test_fillet_type_a_radius_and_click_twice(self):
        with Person(line(0, 0, 100, 0), line(0, 0, 0, 100)) as p:
            p.s.run("FILLET")
            p.type("10")
            p.enter()
            p.click(50, 0)
            p.at(0, 50)
            assert len(p.s.ghosts) == 3
            p.click(0, 50)
            shapes = [e.shape for e in p.doc.entities.values()]
            assert close(shapes[0].a, (10, 0)) and close(shapes[1].a, (0, 10)) and type(shapes[2]) is Arc
            p.enter()
            p.s.undo()
            assert sorted(e.id for e in p.doc.entities.values()) == sorted(p.ids) and p.doc.entities[p.ids[0]].shape == line(0, 0, 100, 0)

    def test_it_remembers_the_radius(self):
        with Person(line(0, 0, 100, 0), line(0, 0, 0, 100)) as p:
            p.s.run("FILLET")
            p.type("7")
            p.enter()
            p.enter()
            p.s.run("FILLET")
            assert "7" in p.s.prompt

    def test_shift_makes_a_sharp_corner_whatever_the_radius(self):
        with Person(line(10, 0, 100, 0), line(0, 10, 0, 100)) as p:
            p.s.run("FILLET")
            p.type("10")
            p.enter()
            p.click(50, 0)
            p.click(0, 50, shift=True)
            assert p.doc.entities[p.ids[0]].shape == line(0, 0, 100, 0) and len(p.doc.entities) == 2

    def test_a_corner_of_a_polyline(self):
        with Person(Polyline((V(0, 0), V(100, 0), V(100, 100)))) as p:
            p.s.run("FILLET")
            p.type("10")
            p.enter()
            p.click(50, 0)
            p.click(100, 50)
            assert all(close(x, y) for x, y in zip(p.doc.entities[p.ids[0]].shape.pts, (V(0, 0), V(90, 0), V(100, 10), V(100, 100))))

    def test_every_corner_of_a_polyline(self):
        with Person(rectangle(0, 0, 100, 50)) as p:
            p.s.run("FILLET")
            p.type("10")
            p.enter()
            p.click(50, 0)
            p.type("A")
            p.enter()
            assert len(p.doc.entities[p.ids[0]].shape.pts) == 8

    def test_a_polyline_and_something_else_says_what_to_do(self):
        with Person(Polyline((V(0, 0), V(100, 0))), line(100, 0, 100, 100)) as p:
            p.s.run("FILLET")
            p.click(50, 0)
            p.click(100, 50)
            assert "Explode" in p.s.message

    def test_a_radius_with_no_room_says_so(self):
        with Person(Polyline((V(0, 0), V(5, 0), V(5, 5)))) as p:
            p.s.run("FILLET")
            p.type("10")
            p.enter()
            p.click(2, 0)
            p.click(5, 2)
            assert "room" in p.s.message

    def test_chamfer_two_distances(self):
        with Person(line(0, 0, 100, 0), line(0, 0, 0, 100)) as p:
            p.s.run("CHAMFER")
            p.type("10,5")
            p.enter()
            p.click(50, 0)
            p.click(0, 50)
            assert any(e.shape == line(10, 0, 0, 5) for e in p.doc.entities.values())

    def test_a_bad_value_says_so(self):
        with Person(line(0, 0, 100, 0), line(0, 0, 0, 100)) as p:
            p.s.run("CHAMFER")
            p.type("1,2,3")
            p.enter()
            assert "comma" in p.s.message


# -------------------------------------------------------------------------------------------------------- array


class TestArray:
    def test_a_row(self):
        with Person(Circle(V(0, 0), 1.0), px=0.01) as p:
            p.s.set_selection(p.ids)
            p.s.run("ARRAY")
            p.click(0, 0)
            p.at(10, 0)
            assert p.s.ghosts == [Circle(V(10, 0), 1.0)]
            p.click(10, 0)
            p.type("4")
            p.enter()
            p.enter()
            assert sorted(e.shape.c[0] for e in p.doc.entities.values()) == [0.0, 10.0, 20.0, 30.0]

    def test_rows_and_columns_are_one_undo_step(self):
        with Person(Circle(V(0, 0), 1.0), px=0.01) as p:
            before = p.doc.fingerprint()
            p.s.set_selection(p.ids)
            p.s.run("ARRAY")
            p.click(0, 0)
            p.click(10, 0)
            p.type("3")
            p.enter()
            p.click(0, 20)
            p.type("2")
            p.enter()
            assert sorted(e.shape.c for e in p.doc.entities.values()) == sorted(V(x, y) for x in (0, 10, 20) for y in (0, 20))
            p.s.undo()
            assert p.doc.fingerprint() == before

    def test_round_a_point(self):
        with Person(Circle(V(10, 0), 1.0), px=0.01) as p:
            p.s.set_selection(p.ids)
            p.s.run("ARRAY")
            p.type("A")
            p.enter()
            p.click(0, 0)
            p.type("4")
            p.enter()
            p.enter()
            centres = sorted((round(e.shape.c[0], 6), round(e.shape.c[1], 6)) for e in p.doc.entities.values())
            assert centres == [(-10.0, 0.0), (0.0, -10.0), (0.0, 10.0), (10.0, 0.0)]

    def test_through_part_of_a_circle(self):
        with Person(Circle(V(10, 0), 1.0), px=0.01) as p:
            p.s.set_selection(p.ids)
            p.s.run("ARRAY")
            p.type("A")
            p.enter()
            p.click(0, 0)
            p.type("3")
            p.enter()
            p.type("90")
            p.enter()
            centres = sorted((round(e.shape.c[0], 6), round(e.shape.c[1], 6)) for e in p.doc.entities.values())
            assert centres == [(0.0, 10.0), (round(10 * math.cos(math.pi / 4), 6), round(10 * math.sin(math.pi / 4), 6)), (10.0, 0.0)]

    def test_too_many(self):
        with Person(Circle(V(0, 0), 1.0), px=0.01) as p:
            p.s.set_selection(p.ids)
            p.s.run("ARRAY")
            p.click(0, 0)
            p.click(10, 0)
            p.type("1000000")
            p.enter()
            assert "more than" in p.s.message and len(p.doc.entities) == 1


# ------------------------------------------------------------------------------------------------------ join


class TestJoining:
    def test_lines_end_to_end_become_a_polyline(self):
        runs, alone = join([(1, line(0, 0, 10, 0)), (2, line(10, 0, 10, 10)), (3, line(50, 50, 60, 60))])
        assert alone == [3] and runs[0].ids == (1, 2)
        assert runs[0].shape.pts == (V(0, 0), V(10, 0), V(10, 10))

    def test_whichever_way_round_they_were_drawn(self):
        runs, _ = join([(1, line(10, 0, 0, 0)), (2, line(10, 0, 10, 10)), (3, line(10, 20, 10, 10))])
        s = runs[0].shape
        assert set(s.pts) == {V(0, 0), V(10, 0), V(10, 10), V(10, 20)} and len(s.pts) == 4
        assert close(s.segments[0].end, s.segments[1].start) and close(s.segments[1].end, s.segments[2].start)

    def test_a_straight_run_becomes_one_line(self):
        runs, _ = join([(1, line(0, 0, 10, 0)), (2, line(10, 0, 30, 0))])
        assert runs[0].shape == line(0, 0, 30, 0)

    def test_a_closed_loop(self):
        runs, _ = join([(1, line(0, 0, 10, 0)), (2, line(10, 0, 10, 10)), (3, line(10, 10, 0, 0))])
        assert runs[0].shape.closed and len(runs[0].shape.pts) == 3

    def test_arcs_of_one_circle_become_one_arc(self):
        a, b = Arc(V(0, 0), 10.0, 0.0, 1.0), Arc(V(0, 0), 10.0, 1.0, 1.0)
        runs, _ = join([(1, a), (2, b)])
        assert type(runs[0].shape) is Arc and runs[0].shape.sweep == pytest.approx(2.0)

    def test_arcs_all_the_way_round_become_a_circle(self):
        a, b = Arc(V(0, 0), 10.0, 0.0, math.pi), Arc(V(0, 0), 10.0, math.pi, math.pi)
        runs, _ = join([(1, a), (2, b)])
        assert runs[0].shape == Circle(V(0, 0), 10.0)

    def test_an_arc_and_a_line_keep_their_shape(self):
        a = Arc(V(0, 0), 10.0, 0.0, math.pi)
        runs, _ = join([(1, a), (2, Line(a.end, V(-10, -50)))])
        s = runs[0].shape
        assert type(s) is Polyline and s.segments[0].sweep == pytest.approx(math.pi)

    def test_open_polylines_join_and_closed_ones_dont(self):
        runs, alone = join([(1, Polyline((V(0, 0), V(10, 0)))), (2, line(10, 0, 20, 5)), (3, rectangle(0, 0, 1, 1))])
        assert runs[0].ids == (1, 2) and alone == []

    def test_the_command(self):
        with Person(line(0, 0, 10, 0), line(10, 0, 10, 10), line(50, 50, 60, 60)) as p:
            p.s.set_selection(p.ids)
            p.s.run("JOIN")
            assert len(p.doc.entities) == 2 and "1 didn't meet" in p.s.message
            assert type(p.doc.entities[p.ids[0]].shape) is Polyline

    def test_the_command_with_nothing_to_join(self):
        with Person(line(0, 0, 10, 0), line(50, 50, 60, 60)) as p:
            p.s.set_selection(p.ids)
            p.s.run("JOIN")
            assert "None of those" in p.s.message and len(p.doc.entities) == 2


# --------------------------------------------------------------------------------------------------- explode


class TestExploding:
    def test_a_polyline(self):
        doc, _ = doc_of()
        s = Polyline((V(0, 0), V(10, 0), V(10, 10)), (0.5, 0.0, 0.0))
        parts = explode_shape(doc, s, Style("A", 2))
        assert [type(x) for x, _ in parts] == [Arc, Line] and all(st == Style("A", 2) for _, st in parts)

    def test_a_block_comes_out_where_it_was(self):
        doc, _ = doc_of()
        with doc.transaction("x") as t:
            t.put_block(BlockDef("B", V(1, 1), (Entity(500, line(1, 1, 2, 1)),)))
        parts = explode_shape(doc, Insert("B", V(10, 10), 2.0, 2.0, math.pi / 2.0), Style())
        assert len(parts) == 1 and close(parts[0][0].a, (10, 10)) and close(parts[0][0].b, (10, 12))

    def test_a_block_takes_on_its_copys_look(self):
        doc, _ = doc_of()
        with doc.transaction("x") as t:
            t.put_layer(Layer("Inner"))
            t.put_block(BlockDef("B", V(0, 0), (Entity(500, line(0, 0, 1, 1), Style("0", 0)), Entity(501, line(0, 0, 2, 2), Style("Inner", 3)))))
        parts = explode_shape(doc, Insert("B", V(0, 0)), Style("Outer", 5))
        assert parts[0][1] == Style("Outer", 5) and parts[1][1] == Style("Inner", 3)

    def test_a_nested_block_stays_a_block_one_level_down(self):
        doc, _ = doc_of()
        with doc.transaction("x") as t:
            t.put_block(BlockDef("In", V(0, 0), (Entity(500, line(0, 0, 1, 1)),)))
            t.put_block(BlockDef("Out", V(0, 0), (Entity(501, Insert("In", V(5, 5))),)))
        parts = explode_shape(doc, Insert("Out", V(10, 0)), Style())
        assert type(parts[0][0]) is Insert and parts[0][0].p == V(15, 5)

    def test_attributes_become_text_and_definitions_go(self):
        doc, _ = doc_of()
        with doc.transaction("x") as t:
            t.put_block(BlockDef("B", V(0, 0), (Entity(500, AttDef(Attrib("TAG", Text(V(0, 0), 1.0, "?")))),)))
        parts = explode_shape(doc, Insert("B", V(0, 0), attribs=(Attrib("TAG", Text(V(5, 5), 1.0, "value")), Attrib("X", Text(V(0, 0), 1.0, "unseen"), invisible=True))), Style())
        assert [x.text for x, _ in parts] == ["value"]

    def test_an_array_comes_out_as_every_copy(self):
        doc, _ = doc_of()
        with doc.transaction("x") as t:
            t.put_block(BlockDef("B", V(0, 0), (Entity(500, line(0, 0, 1, 0)),)))
        parts = explode_shape(doc, Insert("B", V(0, 0), columns=3, rows=2, column_spacing=10.0, row_spacing=20.0), Style())
        assert sorted(x.a for x, _ in parts) == sorted(V(c * 10, r * 20) for c in range(3) for r in range(2))

    def test_a_huge_array_is_refused(self):
        doc, _ = doc_of()
        with doc.transaction("x") as t:
            t.put_block(BlockDef("B", V(0, 0), (Entity(500, line(0, 0, 1, 0)),)))
        with pytest.raises(ValueError, match="hundred thousand"):
            explode_shape(doc, Insert("B", V(0, 0), columns=1000, rows=1000), Style())

    def test_something_from_a_file_comes_out_as_what_it_draws(self):
        doc, _ = doc_of()
        with doc.transaction("x") as t:
            t.put_block(BlockDef("*D1", V(0, 0), (Entity(500, line(0, 0, 1, 0)),)))
        parts = explode_shape(doc, Foreign("DIMENSION", "*D1", V(5, 5), (line(9, 9, 10, 10),)), Style())
        assert {x for x, _ in parts} == {line(9, 9, 10, 10), line(5, 5, 6, 5)}
        with pytest.raises(ValueError, match="nothing here"):
            explode_shape(doc, Foreign("3DSOLID"), Style())

    def test_paragraph_text_comes_out_as_lines(self):
        doc, _ = doc_of()
        parts = explode_shape(doc, MText(V(0, 0), 2.0, "one\\Ptwo"), Style())
        assert [x.text for x, _ in parts] == ["one", "two"]

    def test_what_is_one_thing_already(self):
        doc, _ = doc_of()
        for s in (line(0, 0, 1, 1), Polyline((V(0, 0), V(1, 1)))):
            with pytest.raises(ValueError):
                explode_shape(doc, s, Style())

    def test_the_command(self):
        with Person(rectangle(0, 0, 10, 10), line(0, 0, 1, 1)) as p:
            p.s.set_selection(p.ids)
            p.s.run("EXPLODE")
            assert len(p.doc.entities) == 5 and len(p.s.selection) == 4
            assert "Took 1 object apart into 4" in p.s.message
            p.s.undo()
            assert len(p.doc.entities) == 2


# --------------------------------------------------------------------------------------------------- stretch


class TestStretching:
    box = (90.0, -10.0, 110.0, 10.0)
    move = Affine.translation(50.0, 0.0)

    def test_the_end_of_a_line_inside(self):
        assert stretched(line(0, 0, 100, 0), self.box, self.move) == line(0, 0, 150, 0)
        assert stretched(line(100, 0, 0, 0), self.box, self.move) == line(150, 0, 0, 0)

    def test_a_line_outside(self):
        assert stretched(line(0, 0, 50, 0), self.box, self.move) is None

    def test_a_line_wholly_inside_moves(self):
        assert stretched(line(95, 0, 105, 0), self.box, self.move) == line(145, 0, 155, 0)

    def test_the_corners_of_a_polyline(self):
        s = rectangle(0, 0, 100, 5)
        out = stretched(s, self.box, self.move)
        assert out.pts == (V(0, 0), V(150, 0), V(150, 5), V(0, 5)) and out.closed

    def test_a_bulge_stays(self):
        s = Polyline((V(0, 0), V(100, 0)), (0.5, 0.0))
        assert stretched(s, self.box, self.move).bulges == (0.5, 0.0)

    def test_an_arc_stays_an_arc_with_the_same_bulge(self):
        a = Arc(V(50, 0), 50.0, math.pi, -math.pi)     # from (0,0) to (100,0), over the top
        out = stretched(a, self.box, self.move)
        assert type(out) is Arc and close(out.start, (0, 0)) and close(out.end, (150, 0))
        assert out.sweep == pytest.approx(a.sweep)

    def test_a_circle_moves_if_its_centre_is_inside(self):
        assert stretched(Circle(V(100, 0), 500.0), self.box, self.move) == Circle(V(150, 0), 500.0)
        assert stretched(Circle(V(0, 0), 100.0), self.box, self.move) is None

    def test_text_and_blocks_move_if_their_position_is_inside(self):
        assert stretched(Text(V(100, 0), 1.0, "x"), self.box, self.move).p == V(150, 0)
        assert stretched(Insert("B", V(100, 0)), self.box, self.move).p == V(150, 0)
        assert stretched(Insert("B", V(0, 0)), self.box, self.move) is None

    def test_a_solid(self):
        out = stretched(Solid((V(0, 0), V(100, 0), V(100, 5))), self.box, self.move)
        assert out.pts == (V(0, 0), V(150, 0), V(150, 5))

    def test_a_spline(self):
        s = Spline(3, (0, 0, 0, 0, 1, 1, 1, 1), (V(0, 0), V(30, 30), V(60, 30), V(100, 0)))
        out = stretched(s, self.box, self.move)
        assert out.ctrl[-1] == V(150, 0) and out.ctrl[0] == V(0, 0)

    def test_what_comes_from_a_file_stays(self):
        assert stretched(Foreign("LEADER", curves=(line(100, 0, 1, 1),)), self.box, self.move) is None

    def test_the_command(self):
        with Person(line(0, 0, 100, 0), line(0, 50, 50, 50)) as p:
            before = p.doc.fingerprint()
            p.s.run("STRETCH")
            p.click(90, -10)
            p.click(110, 10)
            p.click(100, 0)
            p.at(150, 0)
            assert p.s.ghosts == [line(0, 0, 150, 0)]
            p.click(150, 0)
            assert p.doc.entities[p.ids[0]].shape == line(0, 0, 150, 0)
            assert p.doc.entities[p.ids[1]].shape == line(0, 50, 50, 50)
            p.s.undo()
            assert p.doc.fingerprint() == before

    def test_the_command_with_nothing_inside(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.s.run("STRETCH")
            p.click(40, -10)
            p.click(60, 10)
            assert "Nothing has an end" in p.s.message and p.s.request is None


# ------------------------------------------------------------------------------------------------------ match


class TestMatch:
    def test_it_copies_the_look(self):
        with Person() as p:
            with p.doc.transaction("x") as t:
                t.put_layer(Layer("A", 3))
                a = t.add(line(0, 0, 100, 0), Style("A", 2, "ByLayer", 50))
                b = t.add(line(0, 50, 100, 50))
                c = t.add(line(0, 80, 100, 80))
            p.s.run("MATCH")
            p.click(50, 0)
            p.click(50, 50)
            assert p.doc.entities[b].style == p.doc.entities[a].style
            assert p.doc.entities[c].style == Style()
            p.enter()

    def test_dragging_across_several(self):
        with Person() as p:
            with p.doc.transaction("x") as t:
                t.put_layer(Layer("A", 3))
                t.add(line(0, 0, 100, 0), Style("A"))
                ids = [t.add(line(0, y, 100, y)) for y in (10, 20)]
            p.s.run("MATCH")
            p.click(50, 0)
            p.drag(50, 8, 50, 22)
            assert all(p.doc.entities[i].style == Style("A") for i in ids)

    def test_nothing_there(self):
        with Person() as p:
            p.s.run("MATCH")
            p.click(0, 0)
            assert "nothing" in p.s.message and p.s.request is None


def test_every_new_command_can_be_found():
    from draftworx.core.commands import COMMANDS, search
    for name in ("TRIM", "EXTEND", "OFFSET", "FILLET", "CHAMFER", "ARRAY", "BREAK", "JOIN", "EXPLODE", "STRETCH", "MATCH"):
        assert COMMANDS[name].icon and COMMANDS[name].help
    assert any(c.name == "FILLET" for c in search("round"))
    assert any(c.name == "TRIM" for c in search("trim"))


def test_a_node_isnt_something_with_ends():
    assert stretched(Node(V(100, 0)), TestStretching.box, TestStretching.move).p == V(150, 0)
