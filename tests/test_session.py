# SPDX-License-Identifier: GPL-3.0-or-later
"""Using the program, by the same calls the window makes."""
import math

import pytest

from draftworx.core.commands import COMMANDS, find, search
from draftworx.core.curves import Arc, Circle, EllipseArc, Line, Polyline
from draftworx.core.model import Layer, Style
from draftworx.core.session import GetPoint, Session
from draftworx.core.shapes import Insert, Node, Text
from draftworx.core.snap import Kind
from draftworx.core.vec import Vec
from driver import Person, line


class TestLine:
    def test_two_clicks_and_enter(self):
        with Person() as p:
            p.run("LINE").click(0, 0).click(100, 0).enter()
            assert p.shapes() == [line(0, 0, 100, 0)]
            assert not p.s.active

    def test_a_chain(self):
        with Person() as p:
            p.run("LINE").click(0, 0).click(100, 0).click(100, 100).enter()
            assert p.shapes() == [line(0, 0, 100, 0), line(100, 0, 100, 100)]

    def test_each_segment_is_its_own_undo_step_and_stays_if_stopped(self):
        with Person() as p:
            p.run("LINE").click(0, 0).click(100, 0).click(100, 100).key("escape")
            assert len(p.shapes()) == 2
            p.s.undo()
            assert len(p.shapes()) == 1

    def test_a_typed_length_goes_the_way_the_cursor_points(self):
        with Person() as p:
            p.run("LINE").click(0, 0).at(50, 0).type("125.5").enter().enter()
            assert p.shapes() == [line(0, 0, 125.5, 0)]

    def test_typed_length_on_a_polar_line_is_exactly_on_it(self):
        with Person() as p:
            p.run("LINE").click(0, 0).at(70, 71).type("100").enter()
            a = p.shapes()[0]
            assert a.b.distance((100 * math.sqrt(0.5), 100 * math.sqrt(0.5))) < 1e-9

    def test_typed_x_y_is_from_the_last_point(self):
        with Person() as p:
            p.run("LINE").click(10, 10).type("100,50").enter().enter()
            assert p.shapes() == [line(10, 10, 110, 60)]

    def test_hash_is_from_the_origin(self):
        with Person() as p:
            p.run("LINE").click(10, 10).type("#100,50").enter().enter()
            assert p.shapes() == [line(10, 10, 100, 50)]

    def test_typed_first_point_is_from_the_origin(self):
        with Person() as p:
            p.run("LINE").type("5,6").enter().type("10,0").enter().enter()
            assert p.shapes() == [line(5, 6, 15, 6)]

    def test_length_and_angle(self):
        with Person() as p:
            p.run("LINE").click(0, 0).type("100<90").enter().enter()
            assert p.shapes()[0].b.distance((0, 100)) < 1e-9

    def test_held_angle(self):
        with Person() as p:
            p.run("LINE").click(0, 0).type("<30").enter()
            assert p.s.lock_direction is not None
            p.at(200, 5)
            q = p.s.snap.point
            assert math.atan2(q[1], q[0]) == pytest.approx(math.pi / 6)
            p.type("100").enter().enter()
            assert p.shapes()[0].b.distance((100 * math.cos(math.pi / 6), 50)) < 1e-9

    def test_held_length_with_tab(self):
        with Person() as p:
            p.run("LINE").click(0, 0).type("100").key("tab")
            assert p.s.lock_radius == 100.0
            p.at(300, 300)
            assert p.s.snap.point.length() == pytest.approx(100)
            p.click(300, 300).enter()
            assert p.shapes()[0].length() == pytest.approx(100)

    def test_held_length_then_typed_angle_with_tab(self):
        with Person() as p:
            p.run("LINE").click(0, 0).type("100").key("tab").type("<90").key("tab").at(5, 50)
            assert p.s.snap.point.distance((0, 100)) < 1e-9
            p.enter().enter()
            assert p.shapes()[0].b.distance((0, 100)) < 1e-9

    def test_escape_lets_go_of_a_held_value(self):
        with Person() as p:
            p.run("LINE").click(0, 0).type("100").key("tab").key("escape")
            assert p.s.lock_radius is None and p.s.active

    def test_close(self):
        with Person() as p:
            p.run("LINE").click(0, 0).click(100, 0).click(100, 100).type("c").enter()
            assert p.shapes()[-1] == line(100, 100, 0, 0)
            assert not p.s.active

    def test_undo_steps_back_one_segment(self):
        with Person() as p:
            p.run("LINE").click(0, 0).click(100, 0).click(100, 100)
            p.s.undo()
            assert p.shapes() == [line(0, 0, 100, 0)]
            assert p.s.active
            p.click(40, 70).enter()
            assert p.shapes() == [line(0, 0, 100, 0), line(100, 0, 40, 70)]

    def test_the_same_point_twice_is_refused_gently(self):
        with Person() as p:
            p.run("LINE").click(0, 0).click(0, 0)
            assert p.s.message and not p.shapes() and p.s.active

    def test_a_snap_gives_the_exact_point(self):
        a = Vec(1 / 3, 1 / 7)
        with Person(Line(a, Vec(100, 100))) as p:
            p.run("LINE").click(a[0] + 1, a[1] + 1).click(50, 300).enter()
            assert p.new_shapes()[0].a == a

    def test_preview_follows_the_cursor(self):
        with Person() as p:
            p.run("LINE").click(0, 0).at(40, 77)
            assert p.s.ghosts == [Line(Vec(0, 0), p.s.snap.point)]

    def test_the_start_of_the_line_is_offered_for_lining_up(self):
        with Person() as p:
            p.run("LINE").click(0, 0).click(100, 0).click(100, 100).at(2, 150)
            assert p.s.snap.candidate.kind == Kind.GUIDE
            assert p.s.snap.point[0] == 0.0
            assert p.s.snap.guides[0].label == "Vertical"

    def test_the_preview_follows_what_is_typed(self):
        with Person() as p:
            p.run("LINE").click(0, 0).at(50, 0).type("12")
            assert p.s.ghosts == [line(0, 0, 12, 0)]
            p.type("0")
            assert p.s.ghosts == [line(0, 0, 120, 0)]
            p.type(",5")
            assert p.s.ghosts == [line(0, 0, 120, 5)]
            p.key("escape")
            assert p.s.ghosts == [line(0, 0, 50, 0)]

    def test_half_typed_shows_where_the_cursor_is(self):
        with Person() as p:
            p.run("LINE").click(0, 0).at(50, 0).type("12x")
            assert p.s.ghosts == [line(0, 0, 50, 0)]

    def test_a_typed_radius_previews(self):
        with Person() as p:
            p.run("C").click(0, 0).at(50, 3).type("20")
            assert p.s.ghosts[0].r == pytest.approx(20)

    def test_a_typed_diameter_previews_as_half(self):
        with Person() as p:
            p.run("C").click(0, 0).type("d").enter().at(50, 3).type("20")
            assert p.s.ghosts[0].r == pytest.approx(10)

    def test_fields_show_length_and_angle(self):
        with Person() as p:
            p.run("LINE").click(0, 0).at(30, 0)
            f = {x.name: x.text for x in p.s.fields}
            assert f["length"] == "30.00" and f["angle"] == "0°"

    def test_fields_show_what_is_being_typed(self):
        with Person() as p:
            p.run("LINE").click(0, 0).at(30, 0).type("12")
            f = {x.name: x for x in p.s.fields}
            assert f["length"].text == "12" and f["length"].typing

    def test_a_bad_number_says_so_and_stays_put(self):
        with Person() as p:
            p.run("LINE").click(0, 0).at(50, 0).type("12x").enter()
            assert p.s.message and p.s.active and p.s.input == "12x"
            p.key("backspace").enter().enter()
            assert p.shapes()[0].b == Vec(12, 0)

    def test_units_in_what_is_typed(self):
        with Person() as p:
            p.run("LINE").click(0, 0).at(10, 0).type("1in").enter().enter()
            assert p.shapes()[0].b.distance((25.4, 0)) < 1e-9


class TestPolyline:
    def test_straight(self):
        with Person() as p:
            p.run("PL").click(0, 0).click(100, 0).click(100, 100).enter()
            assert p.shapes() == [Polyline((Vec(0, 0), Vec(100, 0), Vec(100, 100)))]

    def test_it_is_one_undo_step(self):
        with Person() as p:
            p.run("PL").click(0, 0).click(100, 0).click(100, 100).enter()
            p.s.undo()
            assert not p.shapes()

    def test_nothing_is_added_until_it_is_finished(self):
        with Person() as p:
            p.run("PL").click(0, 0).click(100, 0)
            assert not p.shapes()
            p.key("escape")
            assert not p.shapes()

    def test_a_single_point_makes_nothing(self):
        with Person() as p:
            p.run("PL").click(0, 0).enter()
            assert not p.shapes()

    def test_close(self):
        with Person() as p:
            p.run("PL").click(0, 0).click(100, 0).click(100, 100).type("c").enter()
            assert p.shapes()[0].closed and len(p.shapes()[0].pts) == 3

    def test_undo_a_vertex(self):
        with Person() as p:
            p.run("PL").click(0, 0).click(100, 0).click(100, 100)
            p.s.undo()
            p.click(0, 90).enter()
            assert p.shapes()[0].pts == (Vec(0, 0), Vec(100, 0), Vec(0, 90))

    def test_an_arc_segment_leaves_smoothly(self):
        with Person() as p:
            p.run("PL").click(0, 0).click(100, 0).type("a").enter().click(100, 100).enter()
            s = p.shapes()[0]
            arc = s.segments[1]
            assert isinstance(arc, Arc)
            # it leaves heading along the straight part
            assert arc.tangent_at(0.0).unit().distance((1, 0)) < 1e-9
            assert arc.end.distance((100, 100)) < 1e-9
            assert arc.r == pytest.approx(50)

    def test_back_to_lines(self):
        with Person() as p:
            p.run("PL").click(0, 0).click(100, 0).type("a").enter().click(100, 100).type("l").enter().click(0, 100).enter()
            assert isinstance(p.shapes()[0].segments[2], Line)

    def test_its_own_earlier_points_can_be_snapped_to(self):
        with Person() as p:
            p.run("PL").click(0, 0).click(200, 0).click(200, 200).at(101, 1)
            assert p.s.snap.point == Vec(100, 0)
            assert p.s.snap.candidate.kind == Kind.MIDPOINT


class TestRectangle:
    def test_typed_size(self):
        with Person() as p:
            p.run("REC").click(10, 20).type("100,50").enter()
            r = p.shapes()[0]
            assert r.closed and r.pts == (Vec(10, 20), Vec(110, 20), Vec(110, 70), Vec(10, 70))
            assert r.signed_area() == pytest.approx(5000)

    def test_two_clicks(self):
        with Person() as p:
            p.run("RECTANGLE").click(0, 0).click(30, 40)
            assert p.shapes()[0].signed_area() == pytest.approx(1200)

    def test_no_area_says_so(self):
        with Person() as p:
            p.run("REC").click(0, 0).click(0, 40)
            assert not p.shapes() and p.s.message


class TestCircle:
    def test_typed_radius(self):
        with Person() as p:
            p.run("C").click(10, 10).type("25").enter()
            assert p.shapes() == [Circle(Vec(10, 10), 25.0)]

    def test_pointed_radius(self):
        with Person() as p:
            p.run("C").click(0, 0).click(30, 40)
            assert p.shapes()[0].r == pytest.approx(50)

    def test_typed_diameter(self):
        with Person() as p:
            p.run("C").click(0, 0).type("d").enter().type("50").enter()
            assert p.shapes()[0].r == 25.0

    def test_pointing_is_always_the_radius(self):
        with Person() as p:
            p.run("C").click(0, 0).type("d").enter().click(30, 40)
            assert p.shapes()[0].r == pytest.approx(50)

    def test_two_points(self):
        with Person() as p:
            p.run("C").type("2").enter().click(0, 0).click(100, 0)
            assert p.shapes()[0] == Circle(Vec(50, 0), 50.0)

    def test_three_points(self):
        with Person() as p:
            p.key("f10").run("C").type("3").enter().click(100, 0).click(0, 100).click(-100, 0)
            c = p.shapes()[0]
            assert c.c.distance((0, 0)) < 1e-9 and c.r == pytest.approx(100)

    def test_three_points_in_a_line(self):
        with Person() as p:
            p.run("C").type("3").enter().click(0, 0).click(50, 0).click(100, 0)
            assert not p.shapes() and p.s.message

    def test_a_negative_radius_says_so(self):
        with Person() as p:
            p.run("C").click(0, 0).type("-5").enter()
            assert not p.shapes() and p.s.message


class TestArc:
    def test_three_points(self):
        with Person() as p:
            p.key("f10").run("A").click(100, 0).click(0, 100).click(-100, 0)
            a = p.shapes()[0]
            assert a.r == pytest.approx(100) and a.sweep > 0
            assert a.start.distance((100, 0)) < 1e-9 and a.end.distance((-100, 0)) < 1e-9

    def test_three_points_clockwise_are_stored_counter_clockwise_with_the_same_shape(self):
        with Person() as p:
            p.key("f10").run("A").click(-100, 0).click(0, 100).click(100, 0)
            a = p.shapes()[0]
            assert a.sweep > 0
            assert a.point_at(0.5).distance((0, 100)) < 1e-9

    def test_from_the_centre(self):
        with Person() as p:
            p.run("A").type("c").enter().click(0, 0).click(100, 0).click(0, 50)
            a = p.shapes()[0]
            assert a.r == pytest.approx(100)
            assert a.end.distance((0, 100)) < 1e-9

    def test_from_the_centre_clockwise(self):
        with Person() as p:
            p.run("A").type("c").enter().click(0, 0).click(0, 100).type("c").enter().click(100, 0)
            a = p.shapes()[0]
            assert a.sweep == pytest.approx(math.pi / 2)
            assert a.point_at(0.5).distance((100 * math.sqrt(0.5), 100 * math.sqrt(0.5))) < 1e-9

    def test_in_a_line(self):
        with Person() as p:
            p.run("A").click(0, 0).click(50, 0).click(100, 0)
            assert not p.shapes() and p.s.message


class TestEllipse:
    def test_pointed(self):
        with Person() as p:
            p.run("EL").click(0, 0).click(100, 0).click(30, 50)
            e = p.shapes()[0]
            assert isinstance(e, EllipseArc)
            assert e.major == Vec(100, 0)
            assert e.ratio == pytest.approx(0.5)

    def test_typed(self):
        with Person() as p:
            p.run("EL").click(0, 0).click(100, 0).type("25").enter()
            assert p.shapes()[0].ratio == pytest.approx(0.25)

    def test_a_longer_second_axis_becomes_the_major_axis(self):
        with Person() as p:
            p.run("EL").click(0, 0).click(50, 0).type("100").enter()
            e = p.shapes()[0]
            assert e.major.length() == pytest.approx(100)
            assert e.ratio == pytest.approx(0.5)
            assert e.at_param(0).distance((0, 100)) < 1e-9


class TestPointAndText:
    def test_points(self):
        with Person() as p:
            p.run("PO").click(100, 200).click(300, 400).enter()
            assert p.shapes() == [Node(Vec(100, 200)), Node(Vec(300, 400))]

    def test_text(self):
        with Person() as p:
            p.run("T").click(5, 5).type("Hello there").enter()
            t = p.shapes()[0]
            assert isinstance(t, Text) and t.text == "Hello there" and t.p == Vec(5, 5)

    def test_text_keeps_its_spaces(self):
        with Person() as p:
            p.run("T").click(5, 5).type("a").key("space").type("b").enter()
            assert p.shapes()[0].text == "a b"

    def test_text_size_follows_the_drawing_scale(self):
        with Person() as p:
            with p.doc.transaction("scale") as t:
                from dataclasses import replace
                t.set_settings(replace(p.doc.settings, annotation_scale=50.0))
            p.run("T").click(5, 5).type("x").enter()
            assert p.shapes()[0].height == 125.0

    def test_empty_text_makes_nothing(self):
        with Person() as p:
            p.run("T").click(5, 5).enter()
            assert not p.shapes() and not p.s.active


class TestModify:
    def test_move(self):
        with Person(line(0, 0, 10, 0)) as p:
            p.s.set_selection(p.ids)
            p.run("M").click(0, 0).type("100,50").enter()
            assert p.shapes() == [line(100, 50, 110, 50)]

    def test_move_asks_for_a_selection_if_there_is_none(self):
        with Person(line(0, 0, 10, 0), line(0, 50, 10, 50)) as p:
            p.run("MOVE")
            assert p.s.prompt.startswith("Move: select")
            p.click(5, 0).enter()
            p.click(0, 0).click(0, 100)
            assert p.shapes() == [line(0, 100, 10, 100), line(0, 50, 10, 50)]

    def test_nothing_selected_stops(self):
        with Person(line(0, 0, 10, 0)) as p:
            p.run("MOVE").enter()
            assert not p.s.active and p.s.message

    def test_it_does_not_snap_to_itself_while_moving(self):
        with Person(line(0, 0, 100, 0), line(0, 50, 100, 50)) as p:
            p.s.set_selection([p.ids[0]])
            p.run("M").click(0, 0).at(100, 3)
            assert p.s.snap.candidate is None or p.s.snap.candidate.kind != Kind.ENDPOINT

    def test_preview_shows_it_where_it_would_go(self):
        with Person(line(0, 0, 10, 0)) as p:
            p.s.set_selection(p.ids)
            p.run("M").click(0, 0).at(50, 40)
            q = p.s.snap.point
            assert p.s.ghosts == [Line(q, q + Vec(10, 0))]

    def test_copy_many(self):
        with Person(line(0, 0, 10, 0)) as p:
            p.s.set_selection(p.ids)
            p.run("CO").click(0, 0).click(0, 100).click(0, 200).enter()
            assert len(p.shapes()) == 3
            assert p.shapes()[2] == line(0, 200, 10, 200)
            p.s.undo()
            assert len(p.shapes()) == 2

    def test_a_copy_keeps_its_style(self):
        with Person() as p:
            with p.doc.transaction("x") as t:
                t.put_layer(Layer("A"))
                a = t.add(line(0, 0, 10, 0), Style("A", 3))
            p.s.set_selection([a])
            p.run("CO").click(0, 0).click(0, 100).enter()
            assert p.doc.entities[max(p.doc.entities)].style == Style("A", 3)

    def test_rotate_typed(self):
        with Person(line(0, 0, 10, 0)) as p:
            p.s.set_selection(p.ids)
            p.run("RO").click(0, 0).type("90").enter()
            assert p.shapes()[0].b.distance((0, 10)) < 1e-9

    def test_rotate_pointed(self):
        with Person(line(0, 0, 10, 0)) as p:
            p.s.set_selection(p.ids)
            p.run("RO").click(0, 0).click(0, 100)
            assert p.shapes()[0].b.distance((0, 10)) < 1e-9

    def test_rotate_copy(self):
        with Person(line(0, 0, 10, 0)) as p:
            p.s.set_selection(p.ids)
            p.run("RO").click(0, 0).type("c").enter().type("90").enter()
            assert len(p.shapes()) == 2 and p.shapes()[0] == line(0, 0, 10, 0)

    def test_scale(self):
        with Person(line(0, 0, 10, 0), Circle(Vec(0, 0), 5)) as p:
            p.s.set_selection(p.ids)
            p.run("SC").click(0, 0).type("2").enter()
            assert p.shapes()[0] == line(0, 0, 20, 0)
            assert p.shapes()[1].r == 10.0

    def test_scale_is_a_number_not_a_length(self):
        with Person(line(0, 0, 10, 0)) as p:
            p.s.set_selection(p.ids)
            p.run("SC").click(0, 0).type("1/2").enter()
            assert p.shapes()[0] == line(0, 0, 5, 0)

    def test_scale_by_zero_says_so(self):
        with Person(line(0, 0, 10, 0)) as p:
            p.s.set_selection(p.ids)
            p.run("SC").click(0, 0).type("0").enter()
            assert p.shapes()[0] == line(0, 0, 10, 0) and p.s.message

    def test_mirror_keeps_the_original_by_default(self):
        with Person(line(10, 0, 20, 0)) as p:
            p.s.set_selection(p.ids)
            p.run("MI").click(0, -100).click(0, 100)
            assert p.shapes() == [line(10, 0, 20, 0), line(-10, 0, -20, 0)]

    def test_mirror_can_replace(self):
        with Person(line(10, 0, 20, 0)) as p:
            p.s.set_selection(p.ids)
            p.run("MI").click(0, -100).type("r").enter().click(0, 100)
            assert p.shapes() == [line(-10, 0, -20, 0)]

    def test_mirror_an_arc_keeps_its_points(self):
        arc = Arc(Vec(10, 0), 5, 0.0, math.pi / 2)
        with Person(arc) as p:
            p.s.set_selection(p.ids)
            p.run("MI").click(0, -100).type("r").enter().click(0, 100)
            m = p.shapes()[0]
            assert m.start.distance((-15, 0)) < 1e-9
            assert m.end.distance((-10, 5)) < 1e-9

    def test_erase(self):
        with Person(line(0, 0, 10, 0)) as p:
            p.s.set_selection(p.ids)
            p.run("E")
            assert not p.shapes()
            p.s.undo()
            assert p.shapes()

    def test_several_in_one_undo_step(self):
        with Person(line(0, 0, 10, 0), line(0, 5, 10, 5)) as p:
            p.s.set_selection(p.ids)
            p.run("M").click(0, 0).type("0,100").enter()
            p.s.undo()
            assert p.shapes() == [line(0, 0, 10, 0), line(0, 5, 10, 5)]

    def test_selection_stays_after_a_move(self):
        with Person(line(0, 0, 10, 0)) as p:
            p.s.set_selection(p.ids)
            p.run("M").click(0, 0).click(0, 100)
            assert p.s.selection == set(p.ids)


class TestSelecting:
    def test_click_selects(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.click(50, 1)
            assert p.s.selection == set(p.ids)

    def test_click_replaces(self):
        with Person(line(0, 0, 100, 0), line(0, 50, 100, 50)) as p:
            p.click(50, 0).click(50, 50)
            assert p.s.selection == {p.ids[1]}

    def test_shift_adds_and_removes(self):
        with Person(line(0, 0, 100, 0), line(0, 50, 100, 50)) as p:
            p.click(50, 0).click(50, 50, shift=True)
            assert p.s.selection == set(p.ids)
            p.click(50, 0, shift=True)
            assert p.s.selection == {p.ids[1]}

    def test_click_on_nothing_clears(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.click(50, 0).click(50, 500)
            assert not p.s.selection

    def test_shift_click_on_nothing_keeps(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.click(50, 0).click(50, 500, shift=True)
            assert p.s.selection == set(p.ids)

    def test_window_left_to_right_needs_it_all_inside(self):
        with Person(line(0, 0, 100, 0), line(0, 50, 1000, 50)) as p:
            p.drag(-10, -10, 200, 100)
            assert p.s.selection == {p.ids[0]}

    def test_window_right_to_left_also_takes_what_it_touches(self):
        with Person(line(0, 0, 100, 0), line(0, 50, 1000, 50), line(500, 500, 600, 600)) as p:
            p.at(200, 100).drag(200, 100, -10, -10)
            assert p.s.selection == {p.ids[0], p.ids[1]}

    def test_the_window_is_shown_while_dragging(self):
        with Person() as p:
            p.press(0, 0).at(50, 50)
            assert p.s.window == (Vec(0, 0), Vec(50, 50), False)
            p.at(-50, 50)
            assert p.s.window[2] is True
            p.release(-50, 50)
            assert p.s.window is None

    def test_shift_window_removes(self):
        with Person(line(0, 0, 100, 0), line(0, 50, 100, 50)) as p:
            p.s.set_selection(p.ids)
            p.drag(-10, -10, 200, 20, shift=True)
            assert p.s.selection == {p.ids[1]}

    def test_a_locked_layer_cannot_be_selected(self):
        with Person() as p:
            with p.doc.transaction("x") as t:
                t.put_layer(Layer("L", locked=True))
                t.add(line(0, 0, 100, 0), Style("L"))
            p.click(50, 0)
            assert not p.s.selection
            p.drag(-10, -10, 200, 200)
            assert not p.s.selection

    def test_a_hidden_layer_cannot_be_selected(self):
        with Person() as p:
            with p.doc.transaction("x") as t:
                t.put_layer(Layer("L", on=False))
                t.add(line(0, 0, 100, 0), Style("L"))
            p.click(50, 0)
            assert not p.s.selection

    def test_tab_steps_through_what_is_under_the_cursor(self):
        with Person(line(0, 0, 100, 0), line(50, -50, 50, 50)) as p:
            p.at(50, 0)
            first = p.s.hover
            p.key("tab")
            second = p.s.hover
            assert first != second
            p.click(50, 0)
            assert p.s.selection == {second}

    def test_hover_shows_what_a_click_would_take(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.at(50, 1)
            assert p.s.hover == p.ids[0]
            p.at(50, 50)
            assert p.s.hover is None

    def test_clicking_text_anywhere_on_it(self):
        with Person(Text(Vec(0, 0), 10.0, "Hello")) as p:
            p.click(10, 5)
            assert p.s.selection == set(p.ids)

    def test_clicking_a_block_on_what_it_draws(self):
        from draftworx.core.model import BlockDef, Entity
        with Person() as p:
            with p.doc.transaction("x") as t:
                t.put_block(BlockDef("B", entities=(Entity(1000, line(0, 0, 100, 0)),)))
                a = t.add(Insert("B", Vec(0, 0), 2.0, 2.0))
            p.click(150, 1)
            assert p.s.selection == {a}
            p.click(150, 50)
            assert not p.s.selection

    def test_window_on_a_block_uses_its_real_shape(self):
        from draftworx.core.model import BlockDef, Entity
        with Person() as p:
            with p.doc.transaction("x") as t:
                t.put_block(BlockDef("B", entities=(Entity(1000, line(0, 0, 100, 100)),)))
                a = t.add(Insert("B", Vec(0, 0)))
            # crossing the empty corner of its box doesn't touch the diagonal
            p.at(100, 10).drag(100, 10, 80, 0)
            assert not p.s.selection
            p.at(60, 40).drag(60, 40, 40, 60)
            assert p.s.selection == {a}

    def test_selection_during_a_command_adds_on_click(self):
        with Person(line(0, 0, 100, 0), line(0, 50, 100, 50)) as p:
            p.run("MOVE").click(50, 0).click(50, 50)
            assert p.s.selection == set(p.ids)
            p.click(50, 0, shift=True)
            assert p.s.selection == {p.ids[1]}

    def test_selection_during_a_command_can_use_a_window(self):
        with Person(line(0, 0, 100, 0), line(0, 50, 100, 50)) as p:
            p.run("MOVE").drag(-10, -10, 200, 200).enter()
            assert "from which point" in p.s.prompt
            assert p.s.selection == set(p.ids)

    def test_delete(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.click(50, 0).key("delete")
            assert not p.shapes() and not p.s.selection
            p.s.undo()
            assert p.shapes()

    def test_escape_clears(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.click(50, 0).key("escape")
            assert not p.s.selection

    def test_undo_of_a_creation_drops_it_from_the_selection(self):
        with Person() as p:
            p.run("L").click(0, 0).click(100, 0).enter()
            p.click(50, 0)
            assert p.s.selection
            p.s.undo()
            assert not p.s.selection and not p.s.grips


class TestDragging:
    def test_drag_moves_what_is_under_it(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.drag(40, 0, 40, 50)
            assert p.s.selection == set(p.ids)
            assert p.shapes()[0].a[1] == pytest.approx(50)

    def test_dragging_by_a_snap_point_moves_exactly_from_it(self):
        a = Vec(1 / 3, 1 / 7)
        target = Vec(1000 / 3, 500 / 7)
        with Person(Line(a, a + Vec(100, 0)), Node(target)) as p:
            # pressed near the end of the line, and let go near the point object
            p.press(a[0] + 2, a[1] + 1)
            p.at(100, 50).at(target[0] + 1, target[1] + 1).release(target[0] + 1, target[1] + 1)
            assert p.shapes()[0].a == target or p.shapes()[0].a.distance(target) < 1e-12

    def test_a_small_wobble_is_a_click_not_a_drag(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.drag(40, 0, 42, 1)
            assert p.shapes()[0] == line(0, 0, 100, 0)
            assert p.s.selection == set(p.ids)
            assert not p.s.active

    def test_ctrl_drag_copies(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.drag(40, 0, 40, 50, ctrl=True)
            assert len(p.shapes()) == 2
            assert p.shapes()[0] == line(0, 0, 100, 0)

    def test_escape_during_a_drag_cancels_it(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.press(40, 0).at(40, 20).at(40, 40)
            assert p.s.active
            p.key("escape")
            assert not p.s.active
            p.release(40, 40)
            assert p.shapes()[0] == line(0, 0, 100, 0)

    def test_a_typed_length_during_a_drag(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.press(25, 0).at(25, 20).at(25, 40).type("25").enter()
            assert p.shapes()[0].a.distance((0, 25)) < 1e-6

    def test_drag_is_one_undo_step(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.drag(40, 0, 40, 50)
            p.s.undo()
            assert p.shapes()[0] == line(0, 0, 100, 0)

    def test_dragging_several(self):
        with Person(line(0, 0, 100, 0), line(0, 50, 100, 50)) as p:
            p.s.set_selection(p.ids)
            p.drag(40, 0, 40, 25)
            assert p.shapes()[1].a[1] == pytest.approx(75)

    def test_a_click_on_one_of_several_selected_selects_just_it(self):
        with Person(line(0, 0, 100, 0), line(0, 50, 100, 50)) as p:
            p.s.set_selection(p.ids)
            p.click(40, 0)
            assert p.s.selection == {p.ids[0]}


class TestGrips:
    def test_a_selected_line_has_grips(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.click(50, 1)
            assert [g.point for _, g in p.s.grips] == [Vec(0, 0), Vec(100, 0), Vec(50, 0)]

    def test_dragging_an_end(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.click(50, 1).drag(100, 0, 150, 50)
            assert p.shapes()[0] == line(0, 0, 150, 50)

    def test_dragging_an_end_is_one_undo_step(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.click(50, 1).drag(100, 0, 150, 50)
            p.s.undo()
            assert p.shapes()[0] == line(0, 0, 100, 0)

    def test_dragging_the_middle_moves_it_all(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.click(30, 1).drag(50, 0, 50, 50)
            assert p.shapes()[0] == line(0, 50, 100, 50)

    def test_click_click_also_works(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.click(50, 1).click(100, 0)
            assert p.s.active
            p.click(150, 50)
            assert p.shapes()[0] == line(0, 0, 150, 50)

    def test_typed_length_sets_the_length(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.click(50, 1).click(100, 0).at(200, 1).type("250").enter()
            assert p.shapes()[0].length() == pytest.approx(250)
            assert p.shapes()[0].a == Vec(0, 0)

    def test_it_snaps_to_other_things_but_not_itself(self):
        with Person(line(0, 0, 100, 0), Node(Vec(300, 300))) as p:
            p.click(50, 1).drag(100, 0, 301, 301)
            assert p.shapes()[0].b == Vec(300, 300)

    def test_lines_up_with_where_it_was(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.click(50, 1).press(100, 0).at(103, 30).at(102, 150)
            assert p.s.snap.point[0] == 100.0

    def test_circle_radius(self):
        with Person(Circle(Vec(0, 0), 50)) as p:
            p.click(50, 0).drag(50, 0, 80, 0)
            assert p.shapes()[0].r == pytest.approx(80)

    def test_circle_centre(self):
        with Person(Circle(Vec(0, 0), 50)) as p:
            p.click(50, 0).drag(0, 0, 10, 20)
            assert p.shapes()[0].c == Vec(10, 20) and p.shapes()[0].r == 50

    def test_arc_through_a_new_middle(self):
        with Person(Arc(Vec(0, 0), 100, 0.0, math.pi)) as p:
            m = p.shapes()[0].point_at(0.5)
            p.click(m[0], m[1]).drag(m[0], m[1], 0, 50)
            a = p.shapes()[0]
            assert a.start.distance((100, 0)) < 1e-9 and a.end.distance((-100, 0)) < 1e-9
            assert a.point_at(0.5).distance((0, 50)) < 1e-6

    def test_polyline_vertex(self):
        pl = Polyline((Vec(0, 0), Vec(100, 0), Vec(100, 100)))
        with Person(pl) as p:
            p.click(50, 1).drag(100, 0, 150, -50)
            assert p.shapes()[0].pts[1] == Vec(150, -50)

    def test_polyline_segment_middle_adds_a_corner(self):
        pl = Polyline((Vec(0, 0), Vec(100, 0)))
        with Person(pl) as p:
            p.click(25, 1).drag(50, 0, 50, 50)
            assert p.shapes()[0].pts == (Vec(0, 0), Vec(50, 50), Vec(100, 0))

    def test_polyline_arc_middle_changes_its_bulge(self):
        pl = Polyline((Vec(0, 0), Vec(100, 0)), (1.0, 0.0))
        with Person(pl) as p:
            m = p.shapes()[0].segments[0].point_at(0.5)
            p.click(m[0], m[1]).drag(m[0], m[1], 50, -20)
            s = p.shapes()[0]
            assert s.pts == (Vec(0, 0), Vec(100, 0))
            assert s.segments[0].point_at(0.5).distance((50, -20)) < 1e-6

    def test_text_moves_by_its_grip(self):
        with Person(Text(Vec(0, 0), 10.0, "Hello")) as p:
            p.click(10, 5).drag(0, 0, 50, 30)
            assert p.shapes()[0].p == Vec(50, 30)

    def test_no_grips_for_a_huge_selection(self):
        lines = [line(0, i * 10, 100, i * 10) for i in range(500)]
        with Person(*lines) as p:
            p.s.set_selection(p.ids)
            assert p.s.grips == []

    def test_no_grips_while_a_command_runs(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.click(50, 1)
            p.run("MOVE")
            assert p.s.grips == []
            p.key("escape")
            assert p.s.grips


class TestLocks:
    def test_ortho(self):
        with Person() as p:
            p.key("f8").run("L").click(0, 0).at(100, 30)
            assert p.s.snap.point == Vec(100, 0)
            p.at(30, 100)
            assert p.s.snap.point == Vec(0, 100)

    def test_shift_holds_ortho_for_a_moment(self):
        with Person() as p:
            p.run("L").click(0, 0).at(100, 30, shift=True)
            assert p.s.snap.point == Vec(100, 0)
            p.at(100, 30)
            assert p.s.snap.point != Vec(100, 0)

    def test_shift_lifts_ortho_for_a_moment(self):
        with Person() as p:
            p.key("f8").run("L").click(0, 0).at(100, 30, shift=True)
            assert p.s.snap.point != Vec(100, 0)

    def test_ortho_lines_up_with_a_point_elsewhere(self):
        with Person(Node(Vec(100, 60))) as p:
            p.key("f8").run("L").click(0, 0).at(101, 61)
            assert p.s.snap.point == Vec(100, 0)
            assert "In line with" in p.s.snap.candidate.label

    def test_a_typed_length_with_ortho_goes_straight(self):
        with Person() as p:
            p.key("f8").run("L").click(0, 0).at(100, 30).type("40").enter().enter()
            assert p.shapes()[0].b == Vec(40, 0)

    def test_a_typed_length_goes_the_way_the_cursor_is(self):
        with Person() as p:
            p.key("f8").run("L").click(0, 0).at(-100, 30).type("40").enter().enter()
            assert p.shapes()[0].b == Vec(-40, 0)

    def test_ctrl_suspends_snapping(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.run("L").at(2, 2, ctrl=True)
            assert p.s.snap.candidate is None
            p.at(2, 2)
            assert p.s.snap.point == Vec(0, 0)

    def test_f3_turns_snapping_off(self):
        with Person(line(0, 0, 100, 0)) as p:
            p.key("f3").run("L").at(2, 2)
            assert p.s.snap.candidate is None
            assert p.s.drafting.snap is False

    def test_f9_grid(self):
        with Person() as p:
            p.key("f9").run("L").at(13, 27)
            assert p.s.snap.point == Vec(10, 30)

    def test_f10_alignment_lines(self):
        with Person() as p:
            p.key("f10").run("L").click(0, 0).at(100, 2)
            assert p.s.snap.candidate is None


class TestTracking:
    def test_pause_then_line_up_then_type_a_distance(self):
        with Person(Node(Vec(100, 100))) as p:
            p.run("L").at(101, 101).wait(0.4)
            assert p.s.tracked[0].point == Vec(100, 100)
            p.at(200, 102)
            assert p.s.snap.candidate.kind == Kind.GUIDE
            assert p.s.snap.point[1] == 100.0
            p.type("30").enter()
            assert p.s.request.base == Vec(130, 100)

    def test_tracked_points_clear_after_a_pick(self):
        with Person(Node(Vec(100, 100))) as p:
            p.run("L").at(101, 101).wait(0.4)
            assert p.s.tracked
            p.click(0, 0)
            assert not p.s.tracked

    def test_two_tracked_points_give_a_corner(self):
        with Person(Node(Vec(100, 0)), Node(Vec(0, 100))) as p:
            p.run("L").at(101, 1).wait(0.4).at(1, 101).wait(0.4).at(101, 102)
            assert p.s.snap.candidate.kind == Kind.GUIDES_MEET
            assert p.s.snap.point == Vec(100, 100)

    def test_keeping_still_on_a_point_does_not_flip_it(self):
        with Person(Node(Vec(100, 100))) as p:
            p.run("L").at(101, 101).wait(0.4).wait(5.0).wait(5.0)
            assert len(p.s.tracked) == 1

    def test_while_typing_nothing_is_picked_up(self):
        with Person(Node(Vec(100, 100))) as p:
            p.run("L").at(101, 101).type("1").wait(0.4)
            assert not p.s.tracked

    def test_a_tracked_edge_gives_parallel_lines(self):
        with Person(line(0, 0, 300, 100)) as p:
            p.run("L").click(0, 200).at(120, 40).wait(0.4)
            assert p.s.tracker.directions
            u = Vec(3, 1).unit()
            q = Vec(0, 200) + u * 200
            p.at(q[0], q[1] + 1)
            assert p.s.snap.guides and p.s.snap.guides[0].label == "Parallel to that edge"


class TestChoosingWithTab:
    def test_tab_goes_through_the_candidates(self):
        with Person(line(0, 0, 20, 0), line(5, -10, 5, 10)) as p:
            p.run("L").at(5, 1)
            first = p.s.snap.candidate
            seen = [first.point]
            for _ in range(len(p.s.snap.alternatives) - 1):
                p.key("tab")
                seen.append(p.s.snap.candidate.point)
            assert len({tuple(q) for q in seen}) == len(p.s.snap.alternatives)

    def test_the_choice_is_what_gets_clicked(self):
        with Person(line(0, 0, 20, 0), line(5, -10, 5, 10)) as p:
            p.run("L").at(5, 1)
            p.key("tab")
            chosen = p.s.snap.point
            p.press(5, 1).release(5, 1)
            p.click(100, 100).enter()
            assert p.new_shapes()[0].a == chosen

    def test_moving_away_forgets_the_choice(self):
        with Person(line(0, 0, 20, 0), line(5, -10, 5, 10)) as p:
            p.run("L").at(5, 1)
            first = p.s.snap.candidate
            p.key("tab")
            p.at(5.5, 1)
            assert not p.s.snap.candidate.same_as(first)
            p.at(100, 100).at(5, 1)
            assert p.s.snap.candidate.same_as(first)

    def test_shift_tab_goes_back(self):
        with Person(line(0, 0, 20, 0), line(5, -10, 5, 10)) as p:
            p.run("L").at(5, 1)
            first = p.s.snap.candidate
            p.key("tab").key("tab", shift=True)
            assert p.s.snap.candidate.same_as(first)


class TestOverrides:
    def test_one_kind_for_one_point(self):
        with Person(line(0, 0, 20, 0)) as p:
            p.run("L").type("mid").enter().at(1, 1)
            assert p.s.snap.candidate.kind == Kind.MIDPOINT
            p.press(1, 1).release(1, 1)
            p.at(1, 1)
            assert p.s.snap.candidate.kind == Kind.ENDPOINT

    def test_none_for_one_point(self):
        with Person(line(0, 0, 20, 0)) as p:
            p.run("L").type("non").enter().at(1, 1)
            assert p.s.snap.candidate is None


class TestCommandLine:
    def test_type_anywhere(self):
        with Person() as p:
            p.type("l").enter()
            assert p.s.command_name == "LINE"

    def test_space_works_too(self):
        with Person() as p:
            p.type("c").key("space")
            assert p.s.command_name == "CIRCLE"

    def test_any_case(self):
        with Person() as p:
            p.type("Line").enter()
            assert p.s.command_name == "LINE"

    def test_part_of_a_name(self):
        with Person() as p:
            p.type("rect").enter()
            assert p.s.command_name == "RECTANGLE"
            p.key("escape")
            p.type("ellip").enter()
            assert p.s.command_name == "ELLIPSE"

    def test_enter_repeats_the_last_command(self):
        with Person() as p:
            p.run("L").click(0, 0).click(10, 0).enter()
            p.enter()
            assert p.s.command_name == "LINE"

    def test_unknown_says_so(self):
        with Person() as p:
            p.type("zzzzqqq").enter()
            assert "zzzzqqq" in p.s.message and not p.s.active

    def test_search_ranks_names_first(self):
        assert search("li")[0].name == "LINE"
        assert search("copy")[0].name == "COPY"
        assert search("turn")[0].name == "ROTATE"
        assert search("") == []

    def test_every_command_has_a_name_help_and_group(self):
        for info in COMMANDS.values():
            assert info.name == info.name.upper()
            assert info.help and info.group
            assert find(info.name) is info
            for a in info.aliases:
                assert find(a) is info


class TestUndo:
    def test_a_command_that_cannot_step_back_just_stops(self):
        with Person(line(0, 0, 10, 0)) as p:
            p.run("C").click(0, 0)
            p.s.undo()
            assert not p.s.active
            assert p.shapes() == [line(0, 0, 10, 0)]

    def test_idle_undo_and_redo(self):
        with Person() as p:
            p.run("L").click(0, 0).click(10, 0).enter()
            p.s.undo()
            assert not p.shapes()
            p.s.redo()
            assert p.shapes()

    def test_undo_with_nothing_says_so(self):
        with Person() as p:
            p.s.undo()
            assert p.s.message == "Nothing to undo."
            p.s.redo()
            assert p.s.message == "Nothing to redo."


class TestFailure:
    def test_a_failing_command_shows_a_message_and_leaves_the_drawing_alone(self, capsys):
        s = Session()

        def bad(session):
            yield GetPoint("x")
            with session.doc.transaction("Bad") as t:
                t.add(line(0, 0, 1, 1))
                raise RuntimeError("boom")
        s.run_generator(bad(s))
        s.press(0, 0, 1.0)
        assert not s.active
        assert not s.doc.entities
        assert "boom" in s.message
        assert isinstance(s.last_error, RuntimeError)

    def test_a_preview_that_cannot_be_drawn_does_not_stop_anything(self):
        s = Session()

        def show(p):
            raise ZeroDivisionError()

        def cmd(session):
            yield GetPoint("x", preview=show)
        s.run_generator(cmd(s))
        s.move(5, 5, 1.0)
        assert s.active and s.ghosts == []

    def test_replacing_the_document_starts_clean(self):
        with Person(line(0, 0, 10, 0)) as p:
            p.click(5, 0)
            from draftworx.core.model import Document
            p.s.replace_document(Document())
            assert not p.s.selection and not p.s.active and p.s.doc is not p.doc
            old_version = p.s.doc_version
            with p.doc.transaction("x") as t:
                t.add(line(0, 0, 1, 1))
            assert p.s.doc_version == old_version


class TestPrompts:
    def test_says_what_to_do(self):
        with Person(line(0, 0, 10, 0)) as p:
            assert "Select something" in p.s.prompt
            p.click(5, 0)
            assert "1 selected" in p.s.prompt
            p.run("L")
            assert p.s.prompt.startswith("Line:")
            p.click(0, 50).click(0, 90)
            assert "Undo" in p.s.options
