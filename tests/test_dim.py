# SPDX-License-Identifier: GPL-3.0-or-later
"""Dimensions: what they measure and say, what they draw, how they follow what they're attached to, and the one Dimension tool."""
import math
from dataclasses import replace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from draftworx.core import dim as dims
from draftworx.core.curves import Arc, Circle, Line, Polyline
from draftworx.core.grips import grips_of, moved
from draftworx.core.model import DimStyle, Document
from draftworx.core.native import FormatError, dumps, loads
from draftworx.core.ops.reshape import explode_shape
from draftworx.core.select import in_window, pick
from draftworx.core.shapes import Dimension, Solid, Text
from draftworx.core.snap import Kind, SnapEngine, SnapQuery
from draftworx.core.vec import Affine, Vec
from driver import Person, line

STD = DimStyle("Standard")


def V(x, y):
    return Vec(float(x), float(y))


def close(a, b, eps=1e-9):
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= eps


def texts(items):
    return [i for i in items if type(i) is Text]


def lines(items):
    return [i for i in items if type(i) is Line]


def arrows(items):
    return [i for i in items if type(i) is Solid]


# ---------------------------------------------------------------------------------------------- measuring and saying


class TestWhatItSays:
    def test_aligned_and_linear(self):
        a, b = V(0, 0), V(30, 40)
        assert dims.label(Dimension("aligned", (a, b), V(0, 10)), STD) == "50"
        assert dims.label(Dimension("linear", (a, b), V(0, 50)), STD) == "30"
        assert dims.label(Dimension("linear", (a, b), V(50, 0), math.pi / 2), STD) == "40"

    def test_radius_and_diameter(self):
        assert dims.label(Dimension("radius", (V(0, 0), V(10, 0)), V(20, 0)), STD) == "R10"
        assert dims.label(Dimension("diameter", (V(0, 0), V(10, 0)), V(20, 0)), STD) == "Ø20"

    def test_the_angle_is_the_one_its_arc_is_in(self):
        x, y = (V(0, 0), V(10, 0)), (V(0, 0), V(0, 10))
        for at in (V(5, 5), V(-5, 5), V(-5, -5), V(5, -5)):
            assert dims.label(Dimension("angular", x + y, at), STD) == "90°"
        thirty = (V(0, 0), V(10 * math.cos(math.radians(30)), 10 * math.sin(math.radians(30))))
        assert dims.label(Dimension("angular", x + thirty, V(10, 2)), STD) == "30°"
        assert dims.label(Dimension("angular", x + thirty, V(-1, 5)), STD) == "150°"

    def test_three_points_can_measure_the_outside_of_the_angle(self):
        d = Dimension("angular", (V(0, 0), V(10, 0), V(0, 10)), V(5, 5))
        assert dims.label(d, STD) == "90°"
        assert dims.label(replace(d, at=V(-5, -5)), STD) == "270°"

    def test_text_typed_instead(self):
        d = Dimension("aligned", (V(0, 0), V(30, 40)), V(0, 10))
        assert dims.label(replace(d, text="<> mm"), STD) == "50 mm"
        assert dims.label(replace(d, text="ABOUT"), STD) == "ABOUT"
        assert dims.label(replace(d, text=" "), STD) == ""

    def test_the_style_writes_the_number(self):
        d = Dimension("aligned", (V(0, 0), V(12.5, 0)), V(0, 10))
        assert dims.label(d, STD) == "12.5"
        assert dims.label(d, replace(STD, suppress_zeros=0)) == "12.50"
        assert dims.label(d, replace(STD, linear_factor=2.0)) == "25"
        assert dims.label(d, replace(STD, prefix_suffix="<> mm")) == "12.5 mm"
        assert dims.label(d, replace(STD, decimal_sep=",", suppress_zeros=0)) == "12,50"
        assert dims.label(replace(d, pts=(V(0, 0), V(0.5, 0))), replace(STD, suppress_zeros=12)) == ".5"

    def test_parallel_lines_have_no_angle(self):
        d = Dimension("angular", (V(0, 0), V(10, 0), V(0, 5), V(10, 5)), V(5, 2))
        assert dims.angle_of(d) is None and dims.measurement(d) == 0.0

    def test_wrong_point_counts_are_refused(self):
        with pytest.raises(ValueError):
            Dimension("aligned", (V(0, 0),), V(0, 0))
        with pytest.raises(ValueError):
            Dimension("sideways", (V(0, 0), V(1, 0)), V(0, 0))
        with pytest.raises(ValueError):
            Dimension("aligned", (V(0, 0), V(1, 0)), V(0, 0), refs=((1, "start"),))


# ---------------------------------------------------------------------------------------------------- drawing


class TestWhatItDraws:
    def test_a_horizontal_one(self):
        d = Dimension("linear", (V(0, 0), V(30, 40)), V(10, 60))
        items = dims.drawn(d, STD)
        ext = [g for g in lines(items) if abs(g.a[0] - g.b[0]) < 1e-12]
        assert len(ext) == 2
        assert {round(g.a[0], 9) for g in ext} == {0.0, 30.0}
        # each starts just off what it measures and runs just past the dimension line
        assert min(g.a[1] for g in ext if g.a[0] == 0.0) == pytest.approx(STD.ext_offset)
        assert max(max(g.a[1], g.b[1]) for g in ext) == pytest.approx(60 + STD.ext_beyond)
        dim_line = [g for g in lines(items) if abs(g.a[1] - 60) < 1e-12 and abs(g.b[1] - 60) < 1e-12]
        assert dim_line and min(g.a[0] for g in dim_line) == pytest.approx(0.0) and max(g.b[0] for g in dim_line) == pytest.approx(30.0)
        tips = sorted(a.pts[0][0] for a in arrows(items))
        assert tips == pytest.approx([0.0, 30.0])
        (t,) = texts(items)
        assert t.text == "30" and t.rotation == pytest.approx(0.0) and t.height == STD.text_height
        assert t.p[0] == pytest.approx(15.0) and t.p[1] > 60.0

    def test_a_vertical_one_reads_from_below(self):
        d = Dimension("linear", (V(0, 0), V(30, 40)), V(50, 10), math.pi / 2)
        (t,) = texts(dims.drawn(d, STD))
        assert t.text == "40" and t.rotation == pytest.approx(math.pi / 2)

    def test_upside_down_text_is_turned_round(self):
        d = Dimension("aligned", (V(30, 40), V(0, 0)), V(0, 10))
        (t,) = texts(dims.drawn(d, STD))
        assert -math.pi / 2 < t.rotation <= math.pi / 2

    @given(st.floats(0.0, 2.0 * math.pi))
    def test_text_along_any_direction_reads_the_right_way_up(self, a):
        direction = Vec(math.cos(a), math.sin(a))
        r = dims.readable(direction)
        assert -math.pi / 2 - 1e-9 < r <= math.pi / 2 + 1e-9
        assert abs(math.sin(r - a)) < 1e-9       # along the same line
        d = Dimension("aligned", (V(0, 0), direction * 50.0), direction.perp() * 10.0)
        (t,) = texts(dims.drawn(d, STD))
        assert -math.pi / 2 - 1e-9 < t.rotation <= math.pi / 2 + 1e-9

    def test_too_narrow_puts_the_arrows_outside(self):
        d = Dimension("linear", (V(0, 0), V(3, 0)), V(0, 10))
        tips = arrows(dims.drawn(d, STD))
        # each arrow's tail is outside the span, pointing in
        assert any(a.pts[0] == V(0, 10) and a.pts[1][0] < 0.0 for a in tips)
        assert any(a.pts[0] == V(3, 10) and a.pts[1][0] > 3.0 for a in tips)

    def test_ticks_instead_of_arrows(self):
        d = Dimension("linear", (V(0, 0), V(30, 0)), V(0, 10))
        items = dims.drawn(d, replace(STD, tick_size=1.0))
        assert not arrows(items) and len(lines(items)) == 5

    def test_text_on_the_line_leaves_a_gap_in_it(self):
        d = Dimension("linear", (V(0, 0), V(60, 0)), V(0, 10))
        items = dims.drawn(d, replace(STD, text_above=0))
        on_line = [g for g in lines(items) if g.a[1] == 10 and g.b[1] == 10]
        assert len(on_line) == 2 and on_line[0].b[0] < 30 < on_line[1].a[0]

    def test_its_scale_and_its_styles_scale_size_it(self):
        d = Dimension("linear", (V(0, 0), V(60, 0)), V(0, 10), scale=2.0)
        (t,) = texts(dims.drawn(d, replace(STD, scale=3.0)))
        assert t.height == pytest.approx(STD.text_height * 6.0)

    def test_a_radius_inside_and_outside(self):
        inside = Dimension("radius", (V(0, 0), V(10, 0)), V(5, 0))
        items = dims.drawn(inside, STD)
        assert any(g.a == V(0, 0) and close(g.b, (10, 0)) for g in lines(items))
        (tip,) = arrows(items)
        assert close(tip.pts[0], (10, 0)) and tip.pts[1][0] < 10.0      # pointing out, its tail toward the centre
        outside = Dimension("radius", (V(0, 0), V(10, 0)), V(0, 25))
        items = dims.drawn(outside, STD)
        (tip,) = arrows(items)
        assert close(tip.pts[0], (0, 10)) and tip.pts[1][1] > 10.0       # pointing in, its tail outside
        assert texts(items)[0].text == "R10"
        assert len(lines(items)) == 3        # the line out to the text, and a centre mark

    def test_a_diameter_has_two_arrows(self):
        d = Dimension("diameter", (V(0, 0), V(10, 0)), V(3, 4))
        items = dims.drawn(d, STD)
        tips = sorted((a.pts[0] for a in arrows(items)), key=lambda p: p[0])
        assert close(tips[0], (-6, -8)) and close(tips[1], (6, 8))
        assert texts(items)[0].text == "Ø20"

    def test_an_angle_draws_an_arc_through_where_it_goes(self):
        d = Dimension("angular", (V(0, 0), V(10, 0), V(0, 0), V(0, 10)), V(20, 20))
        items = dims.drawn(d, STD)
        (arc,) = [i for i in items if type(i) is Arc]
        assert arc.c == V(0, 0) and arc.r == pytest.approx(math.hypot(20, 20)) and arc.sweep == pytest.approx(math.pi / 2)
        assert len(arrows(items)) == 2
        assert len(lines(items)) == 2       # both lines are shorter than the arc's radius, so they're carried out to it
        assert texts(items)[0].text == "90°"

    def test_nothing_to_measure_still_draws_safely(self):
        for d in (Dimension("aligned", (V(1, 1), V(1, 1)), V(1, 1)), Dimension("radius", (V(1, 1), V(1, 1)), V(1, 1)),
                  Dimension("angular", (V(0, 0), V(1, 0), V(0, 1), V(1, 1)), V(1, 1))):
            dims.drawn(d, STD)

    @given(st.tuples(st.floats(-100, 100), st.floats(-100, 100)), st.tuples(st.floats(-100, 100), st.floats(-100, 100)),
           st.tuples(st.floats(-100, 100), st.floats(-100, 100)), st.sampled_from(("linear", "aligned", "radius", "diameter")), st.floats(-4, 4))
    @settings(max_examples=150, deadline=None)
    def test_anything_draws_and_says_what_it_measures(self, a, b, at, measure, angle):
        d = Dimension(measure, (V(*a), V(*b)), V(*at), angle)
        items = dims.drawn(d, STD)
        if not math.isclose(dims.measurement(d), 0.0, abs_tol=1e-9) or measure in ("radius", "diameter"):
            assert texts(items) and texts(items)[0].text == dims.label(d, STD)


# --------------------------------------------------------------------------------------------- following


def doc_with(*shapes):
    doc = Document()
    with doc.transaction("set up") as t:
        ids = [t.add(s) for s in shapes]
    return doc, ids


def attached_line(doc_shapes=()):
    doc, ids = doc_with(line(0, 0, 100, 0), *doc_shapes)
    lid = ids[0]
    with doc.transaction("Dimension") as t:
        did = t.add(Dimension("aligned", (V(0, 0), V(100, 0)), V(50, 20), refs=((lid, "start"), (lid, "end"))))
    return doc, lid, did


class TestFollowing:
    def test_it_follows_a_line_that_moves_in_the_same_step(self):
        doc, lid, did = attached_line()
        steps = len(doc._undo)
        with doc.transaction("Move") as t:
            t.replace(lid, line(10, 5, 110, 5))
        d = doc.entities[did].shape
        assert d.pts == (V(10, 5), V(110, 5)) and close(d.at, (60, 25))
        assert len(doc._undo) == steps + 1
        doc.undo()
        assert doc.entities[did].shape.pts == (V(0, 0), V(100, 0)) and doc.entities[lid].shape == line(0, 0, 100, 0)
        doc.redo()
        assert doc.entities[did].shape.pts == (V(10, 5), V(110, 5))

    def test_turning_the_line_turns_it(self):
        doc, lid, did = attached_line()
        with doc.transaction("Rotate") as t:
            t.replace(lid, doc.entities[lid].shape.transformed(Affine.rotation(math.pi / 2)))
        d = doc.entities[did].shape
        assert close(d.pts[1], (0, 100)) and close(d.at, (-20, 50)) and dims.label(d, STD) == "100"

    def test_lengthening_one_end_changes_what_it_says(self):
        doc, lid, did = attached_line()
        with doc.transaction("Stretch") as t:
            t.replace(lid, line(0, 0, 150, 0))
        d = doc.entities[did].shape
        assert dims.label(d, STD) == "150" and close(d.at, (75, 20))

    def test_deleting_what_its_on_lets_go(self):
        doc, lid, did = attached_line()
        with doc.transaction("Erase") as t:
            t.remove(lid)
        d = doc.entities[did].shape
        assert d.refs == () and d.pts == (V(0, 0), V(100, 0))
        assert not doc.attached_to(lid)
        doc.undo()
        assert doc.entities[did].shape.refs and doc.attached_to(lid) == {did}

    def test_moving_it_on_its_own_lets_go(self):
        doc, lid, did = attached_line()
        with doc.transaction("Move") as t:
            t.replace(did, doc.entities[did].shape.transformed(Affine.translation(0, 30)))
        assert doc.entities[did].shape.refs == ()

    def test_moving_both_together_keeps_it_attached(self):
        doc, lid, did = attached_line()
        m = Affine.translation(5, 7)
        with doc.transaction("Move") as t:
            t.replace(lid, doc.entities[lid].shape.transformed(m))
            t.replace(did, doc.entities[did].shape.transformed(m))
        d = doc.entities[did].shape
        assert d.refs and d.pts == (V(5, 7), V(105, 7))
        with doc.transaction("Move") as t:
            t.replace(lid, line(0, 0, 50, 0))
        assert doc.entities[did].shape.pts == (V(0, 0), V(50, 0))

    def test_a_copy_isnt_attached_to_the_original(self):
        doc, lid, did = attached_line()
        with doc.transaction("Copy") as t:
            copy = t.add(doc.entities[did].shape.transformed(Affine.translation(0, 50)))
        assert doc.entities[copy].shape.refs == () and doc.entities[did].shape.refs

    def test_moving_where_its_line_goes_keeps_it_attached(self):
        doc, lid, did = attached_line()
        d = doc.entities[did].shape
        g = [g for g in grips_of(d, STD) if g.role == "dimline"][0]
        with doc.transaction("Grip") as t:
            t.replace(did, moved(d, g, V(50, 40)))
        assert doc.entities[did].shape.refs and doc.entities[did].shape.at == V(50, 40)

    def test_dragging_a_point_off_lets_go_of_that_one(self):
        doc, lid, did = attached_line()
        d = doc.entities[did].shape
        g = [g for g in grips_of(d, STD) if g.role == "dimpoint" and g.index == 1][0]
        with doc.transaction("Grip") as t:
            t.replace(did, moved(d, g, V(80, 0)))
        assert doc.entities[did].shape.refs == ((lid, "start"), None)

    def test_a_radius_follows_its_circle(self):
        doc, ids = doc_with(Circle(V(0, 0), 10))
        with doc.transaction("Dimension") as t:
            did = t.add(Dimension("radius", (V(0, 0), V(0, 10)), V(0, 25), refs=((ids[0], "center"), (ids[0], "on"))))
        with doc.transaction("Bigger") as t:
            t.replace(ids[0], Circle(V(5, 0), 20))
        d = doc.entities[did].shape
        assert d.pts == (V(5, 0), V(5, 20)) and close(d.at, (5, 35)) and dims.label(d, STD) == "R20"

    def test_an_angle_follows_a_line_that_turns(self):
        doc, ids = doc_with(line(0, 0, 10, 0), line(0, 0, 0, 10))
        a, b = ids
        refs = ((a, "start"), (a, "end"), (b, "start"), (b, "end"))
        with doc.transaction("Dimension") as t:
            did = t.add(Dimension("angular", (V(0, 0), V(10, 0), V(0, 0), V(0, 10)), V(5, 5), refs=refs))
        turned = line(0, 0, 10 * math.cos(math.radians(60)), 10 * math.sin(math.radians(60)))
        with doc.transaction("Rotate") as t:
            t.replace(b, turned)
        d = doc.entities[did].shape
        assert dims.label(d, STD) == "60°"

    def test_a_polylines_corner(self):
        r = Polyline((V(0, 0), V(100, 0), V(100, 50), V(0, 50)), closed=True)
        doc, ids = doc_with(r)
        with doc.transaction("Dimension") as t:
            did = t.add(Dimension("aligned", (V(100, 0), V(100, 50)), V(120, 25), refs=((ids[0], "vertex:1"), (ids[0], "vertex:2"))))
        with doc.transaction("Taller") as t:
            t.replace(ids[0], Polyline((V(0, 0), V(100, 0), V(100, 80), V(0, 80)), closed=True))
        assert dims.label(doc.entities[did].shape, STD) == "80"
        with doc.transaction("Fewer corners") as t:
            t.replace(ids[0], Polyline((V(0, 0), V(100, 0)), closed=False))
        assert doc.entities[did].shape.refs == ((ids[0], "vertex:1"), None)

    def test_named_points(self):
        a = Arc(V(0, 0), 10, 0.0, math.pi / 2)
        assert dims.which_point(a, V(10, 0)) == "start" and dims.which_point(a, V(0, 0)) == "center"
        assert dims.which_point(Circle(V(1, 1), 3), V(1, 1)) == "center"
        assert dims.which_point(line(0, 0, 10, 0), V(5, 0)) == "mid" and dims.which_point(line(0, 0, 10, 0), V(3, 0)) is None
        p = Polyline((V(0, 0), V(10, 0), V(10, 10)))
        assert dims.which_point(p, V(10, 0)) == "vertex:1" and dims.which_point(p, V(10, 5)) == "mid:1"
        assert dims.point_of(p, "vertex:9") is None and dims.point_of(p, "mid:x") is None


# ------------------------------------------------------------------------------------------- in the drawing


class TestInTheDrawing:
    def test_its_text_can_be_clicked(self):
        doc, ids = doc_with(Dimension("linear", (V(0, 0), V(100, 0)), V(0, 20)))
        (t,) = texts(dims.drawn(doc.entities[ids[0]].shape, STD))
        o, a, u = t.box()
        middle = o + a * 0.5 + u * 0.5
        assert pick(doc, middle, 0.01) == ids

    def test_a_window_round_all_of_it(self):
        doc, ids = doc_with(Dimension("linear", (V(0, 0), V(100, 0)), V(0, 20)))
        assert in_window(doc, (-5, -5, 105, 30), False) == ids
        assert in_window(doc, (-5, -5, 105, 20), False) == []      # the text's above the line

    def test_a_style_change_resizes_what_its_box_covers(self):
        doc, ids = doc_with(Dimension("linear", (V(0, 0), V(100, 0)), V(0, 20)))
        before = doc.shape_bbox(doc.entities[ids[0]].shape)
        with doc.transaction("Bigger text") as t:
            t.put_dim_style(replace(doc.dim_styles["Standard"], text_height=10.0))
        after = doc.shape_bbox(doc.entities[ids[0]].shape)
        assert after[3] > before[3] + 5
        assert pick(doc, V(50, 20 + 0.625 + 9), 0.01) == ids

    def test_the_points_it_measures_snap_as_nodes(self):
        doc, ids = doc_with(Dimension("linear", (V(0, 0), V(100, 0)), V(0, 20)))
        engine = SnapEngine(doc)
        found = engine.pick(SnapQuery(V(100.4, 0.3), 0.1))
        assert found.candidate is not None and found.candidate.kind == Kind.NODE and found.point == V(100, 0)

    def test_it_comes_apart_into_what_it_draws(self):
        doc, ids = doc_with(Dimension("linear", (V(0, 0), V(100, 0)), V(0, 20)))
        e = doc.entities[ids[0]]
        parts = explode_shape(doc, e.shape, e.style)
        kinds = {type(p) for p, _ in parts}
        assert kinds == {Line, Solid, Text}

    def test_it_saves_and_reads_back(self):
        doc, lid, did = attached_line()
        with doc.transaction("Text") as t:
            t.replace(did, replace(doc.entities[did].shape, text="<> TYP", scale=2.0, text_at=V(40, 30)))
        data = dumps(doc)
        assert b'"version":2' in data
        back = loads(data)
        assert back.entities[did].shape == doc.entities[did].shape
        assert back.attached_to(lid) == {did}

    def test_a_drawing_without_any_stays_the_old_version(self):
        doc, _ = doc_with(line(0, 0, 1, 1))
        assert b'"version":1' in dumps(doc)

    def test_attached_to_something_not_there_is_let_go_of_on_reading(self):
        doc, lid, did = attached_line()
        data = dumps(doc).replace(f'"refs":[[{lid},"start"],[{lid},"end"]]'.encode(), b'"refs":[[999,"start"],[' + str(lid).encode() + b',"end"]]')
        back = loads(data)
        assert back.entities[did].shape.refs == (None, (lid, "end"))

    @pytest.mark.parametrize("bad", ['"measure":"sideways"', '"pts":[[0,0]]', '"refs":[[0,"start"],null]', '"refs":"start"', '"refs":[[1,"start"]]'])
    def test_damaged_ones_are_refused(self, bad):
        doc, lid, did = attached_line()
        data = dumps(doc).decode()
        e = [ln for ln in data.split("\n") if '"type":"dimension"' in ln][0]
        import json
        obj = json.loads(e.rstrip(","))
        key, value = bad.split(":", 1)
        obj[json.loads(key)] = json.loads(value)
        damaged = data.replace(e.rstrip(","), json.dumps(obj, separators=(",", ":")))
        with pytest.raises(FormatError):
            loads(damaged.encode())


# ------------------------------------------------------------------------------------------- the tool


def dimension_shapes(p):
    return [e.shape for e in p.doc.entities.values() if type(e.shape) is Dimension]


class TestTheDimensionTool:
    def test_a_line_and_where_it_goes(self):
        with Person(line(0, 0, 100, 0), px=0.1) as p:
            p.run("DIM")
            p.click(30, 0)
            p.at(30, 15)
            assert p.s.ghosts and type(p.s.ghosts[0]) is Dimension
            p.click(30, 15)
            (d,) = dimension_shapes(p)
            assert dims.label(d, STD) == "100" and d.refs == ((p.ids[0], "start"), (p.ids[0], "end"))
            assert d.at[1] == pytest.approx(15.0)
            p.enter()

    def test_where_it_goes_isnt_pulled_onto_lines_from_its_middle(self):
        # zoomed out, a wide reach: a polar line at 45 degrees from the middle of what's measured passes right by here, and once caught it
        with Person(line(0, 0, 100, 0)) as p:
            p.run("DIM").click(30, 0).click(33, 18).enter()
            (d,) = dimension_shapes(p)
            assert d.at[1] == pytest.approx(18.0)

    def test_it_follows_the_line_when_that_moves(self):
        with Person(line(0, 0, 100, 0), px=0.1) as p:
            p.run("DIM").click(30, 0).click(30, 15).enter()
            with p.doc.transaction("Move") as t:
                t.replace(p.ids[0], line(0, 0, 120, 0))
            (d,) = dimension_shapes(p)
            assert dims.label(d, STD) == "120"

    def test_where_it_goes_decides_across_up_or_along(self):
        with Person(line(0, 0, 100, 50), px=0.1) as p:
            p.run("DIM").click(60, 30).click(50, 80)
            p.click(60, 30).click(130, 25)
            p.click(60, 30).click(45, 35)
            p.enter()
            said = sorted(dims.label(d, STD) for d in dimension_shapes(p))
            assert said == sorted(["100", "50", f"{math.hypot(100, 50):.2f}".rstrip("0")])

    def test_h_keeps_it_across(self):
        with Person(line(0, 0, 100, 50), px=0.1) as p:
            p.run("DIM").click(60, 30)
            p.type("h").enter()
            p.click(130, 25).enter()
            (d,) = dimension_shapes(p)
            assert d.measure == "linear" and d.angle == 0.0 and dims.label(d, STD) == "100"

    def test_typing_how_far(self):
        with Person(line(0, 0, 100, 0), px=0.1) as p:
            p.run("DIM").click(30, 0)
            p.at(30, -5)
            p.type("12").enter().enter()
            (d,) = dimension_shapes(p)
            assert d.at[1] == pytest.approx(-12.0)

    def test_two_points(self):
        with Person(line(0, 0, 10, 0), line(90, 30, 100, 30), px=0.1) as p:
            p.run("DIM").click(0, 0).click(100, 30).click(50, 50).enter()
            (d,) = dimension_shapes(p)
            assert d.measure == "linear" and dims.label(d, STD) == "100"
            assert d.refs == ((p.ids[0], "start"), (p.ids[1], "end"))

    def test_a_circle_measures_its_diameter_and_an_arc_its_radius(self):
        with Person(Circle(V(0, 0), 10), Arc(V(100, 0), 20, 0.0, math.pi), px=0.1) as p:
            p.run("DIM").click(10 * math.cos(1.0), 10 * math.sin(1.0)).click(20, 20)
            p.click(100 + 20 * math.cos(1.0), 20 * math.sin(1.0)).click(130, 30)
            p.enter()
            said = sorted(dims.label(d, STD) for d in dimension_shapes(p))
            assert said == ["R20", "Ø20"]

    def test_r_measures_a_circles_radius_instead(self):
        with Person(Circle(V(0, 0), 10), px=0.1) as p:
            p.run("DIM").click(10 * math.cos(1.0), 10 * math.sin(1.0))
            p.type("r").enter()
            p.click(20, 20).enter()
            (d,) = dimension_shapes(p)
            assert dims.label(d, STD) == "R10"

    def test_a_second_line_measures_the_angle(self):
        with Person(line(0, 0, 100, 0), line(0, 0, 0, 100), px=0.1) as p:
            p.run("DIM").click(60, 0)
            p.at(0, 60)
            assert p.s.ghosts and p.s.ghosts[0].measure == "angular"
            p.click(0, 60).click(30, 30).enter()
            (d,) = dimension_shapes(p)
            assert dims.label(d, STD) == "90°"

    def test_the_angle_at_a_corner_of_one_polyline(self):
        r = Polyline((V(0, 0), V(100, 0), V(100, 50)))
        with Person(r, px=0.1) as p:
            p.run("DIM").click(60, 0)
            p.at(100, 30)
            assert p.s.ghosts and p.s.ghosts[0].measure == "angular"
            p.click(100, 30).click(90, 10).enter()
            (d,) = dimension_shapes(p)
            assert dims.label(d, STD) == "90°"
            assert d.refs == ((p.ids[0], "vertex:0"), (p.ids[0], "vertex:1"), (p.ids[0], "vertex:1"), (p.ids[0], "vertex:2"))

    def test_something_it_cant_measure_says_so(self):
        with Person(Text(V(0, 0), 5, "HELLO"), px=0.1) as p:
            p.run("DIM").click(5, 2)
            assert "two points" in p.s.message
            p.enter()

    def test_the_drawings_scale_sizes_it(self):
        with Person(line(0, 0, 100, 0), px=0.1) as p:
            with p.doc.transaction("Scale") as t:
                t.set_settings(replace(p.doc.settings, annotation_scale=50.0))
            p.run("DIM").click(30, 0).click(30, 500).enter()
            (d,) = dimension_shapes(p)
            assert d.scale == 50.0

    def test_one_undo_takes_it_away(self):
        with Person(line(0, 0, 100, 0), px=0.1) as p:
            p.run("DIM").click(30, 0).click(30, 15).enter()
            p.doc.undo()
            assert not dimension_shapes(p)
