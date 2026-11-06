# SPDX-License-Identifier: GPL-3.0-or-later
"""What the panel works from: measurements, typed changes, layers, the view. No window needed."""
import math

import pytest

from draftworx.core.curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from draftworx.core.info import name_of, rows_for, summarise
from draftworx.core.layers import add_layer, rename_layer, set_layer_flag, unique_layer_name
from draftworx.core.model import BlockDef, Document, EditError, Entity, Settings, Style
from draftworx.core.select import in_window, pick
from draftworx.core.shapes import Insert, MText, Node, Solid, Text
from draftworx.core.units import TypedError
from draftworx.core.vec import Vec
from draftworx.core.view import View
from driver import line

ST = Settings()


def row(shape, label):
    return next(r for r in rows_for(shape, ST) if r.label == label)


class TestRows:
    def test_line_length_keeps_the_start_and_direction(self):
        s = line(10, 10, 40, 50)
        assert row(s, "Length").value == "50.00"
        new = row(s, "Length").edit("100")
        assert new.a == s.a
        assert new.length() == pytest.approx(100)
        assert (new.b - new.a).unit().distance((s.b - s.a).unit()) < 1e-9

    def test_line_angle_keeps_the_start_and_length(self):
        s = line(0, 0, 100, 0)
        new = row(s, "Angle").edit("90")
        assert new.b.distance((0, 100)) < 1e-9

    def test_line_ends(self):
        s = line(0, 0, 100, 0)
        assert row(s, "Start X").edit("5").a == Vec(5, 0)
        assert row(s, "End Y").edit("7").b == Vec(100, 7)

    def test_a_length_has_to_be_positive(self):
        with pytest.raises(ValueError):
            row(line(0, 0, 1, 0), "Length").edit("0")
        with pytest.raises(ValueError):
            row(line(0, 0, 1, 0), "Length").edit("-1")

    def test_a_bad_number_says_so(self):
        with pytest.raises(TypedError):
            row(line(0, 0, 1, 0), "Length").edit("abc")

    def test_units_and_sums_work_here_too(self):
        assert row(line(0, 0, 1, 0), "Length").edit("1in").length() == pytest.approx(25.4)
        assert row(line(0, 0, 1, 0), "Length").edit("10/4").length() == pytest.approx(2.5)

    def test_circle(self):
        c = Circle(Vec(1, 2), 5)
        assert row(c, "Radius").edit("7").r == 7
        assert row(c, "Diameter").value == "10.00"
        assert row(c, "Diameter").edit("20").r == 10
        assert row(c, "Circumference").edit(str(math.tau * 3)).r == pytest.approx(3)
        assert row(c, "Area").edit(str(math.pi * 16)).r == pytest.approx(4)
        assert row(c, "Center X").edit("9").c == Vec(9, 2)

    def test_arc(self):
        a = Arc(Vec(0, 0), 10, 0.0, math.pi / 2)
        assert row(a, "Start angle").value == "0°"
        assert row(a, "End angle").value == "90°"
        assert row(a, "Radius").edit("20").r == 20
        new = row(a, "End angle").edit("180")
        assert new.sweep == pytest.approx(math.pi)
        new = row(a, "Start angle").edit("45")
        assert new.sweep == pytest.approx(math.pi / 4)
        assert row(a, "Length").edit is None

    def test_a_clockwise_arc_reads_the_same_way_as_the_counter_clockwise_one(self):
        a = Arc(Vec(0, 0), 10, math.pi / 2, -math.pi / 2)
        assert row(a, "Start angle").value == "0°" and row(a, "End angle").value == "90°"

    def test_polyline(self):
        p = Polyline((Vec(0, 0), Vec(10, 0), Vec(10, 10), Vec(0, 10)), closed=True)
        assert row(p, "Vertices").value == "4"
        assert row(p, "Length").value == "40.00"
        assert row(p, "Area").value == "100.00"
        assert not any(r.label == "Area" for r in rows_for(Polyline((Vec(0, 0), Vec(1, 1))), ST))

    def test_text(self):
        t = Text(Vec(0, 0), 2.5, "Hi")
        assert row(t, "Text").edit("Bye").text == "Bye"
        assert row(t, "Height").edit("5").height == 5
        assert row(t, "Rotation").edit("90").rotation == pytest.approx(math.pi / 2)

    def test_paragraph_text(self):
        t = MText(Vec(0, 0), 2.5, "Hi", 10.0)
        assert row(t, "Width").edit("20").width == 20

    def test_a_block(self):
        i = Insert("B", Vec(0, 0))
        assert row(i, "Block").edit is None
        assert row(i, "Scale X").edit("2").sx == 2
        assert row(i, "Position X").edit("5").p == Vec(5, 0)

    def test_other_shapes_have_rows(self):
        assert rows_for(Node(Vec(1, 2)), ST)
        assert rows_for(EllipseArc(Vec(0, 0), Vec(5, 0), 0.5), ST)
        assert rows_for(Spline(1, (0.0, 0.0, 1.0, 1.0), (Vec(0, 0), Vec(1, 1))), ST)
        assert rows_for(Solid((Vec(0, 0), Vec(1, 0), Vec(1, 1))), ST) == []

    def test_names(self):
        assert name_of(line(0, 0, 1, 1)) == "Line"
        assert name_of(Insert("B", Vec(0, 0))) == "Block"


class TestSummary:
    def test_totals(self):
        d = Document()
        with d.transaction("x") as t:
            ids = [t.add(line(0, 0, 10, 0)), t.add(Circle(Vec(0, 0), 1)), t.add(Polyline((Vec(0, 0), Vec(2, 0), Vec(2, 2), Vec(0, 2)), closed=True)),
                   t.add(Node(Vec(50, 50))), t.add(EllipseArc(Vec(0, 0), Vec(2, 0), 0.5))]
        s = summarise(d, ids)
        assert s.count == 5
        assert s.kinds["Line"] == 1 and s.kinds["Circle"] == 1
        assert s.area == pytest.approx(math.pi + 4 + math.pi * 2)
        assert s.length > 10 + math.tau + 8
        assert s.box[2] == 50

    def test_missing_ids_are_ignored(self):
        assert summarise(Document(), [99]).count == 0


class TestLayers:
    def test_rename_moves_everything_on_it_as_one_step(self):
        d = Document()
        add_layer(d, "Old")
        with d.transaction("x") as t:
            a = t.add(line(0, 0, 1, 1), Style("OLD", 3))
            t.put_block(BlockDef("B", entities=(Entity(1000, line(0, 0, 1, 1), Style("Old")),)))
        d.current.layer = "Old"
        rename_layer(d, "old", "New")
        assert "New" in d.layers and "Old" not in d.layers
        assert d.entities[a].style == Style("New", 3)
        assert d.blocks.get("B").entities[0].style.layer == "New"
        assert d.current.layer == "New"
        d.undo()
        assert "Old" in d.layers and "New" not in d.layers
        assert d.entities[a].style.layer == "OLD"
        assert d.blocks.get("B").entities[0].style.layer == "Old"

    def test_changing_only_the_case(self):
        d = Document()
        add_layer(d, "wall")
        with d.transaction("x") as t:
            a = t.add(line(0, 0, 1, 1), Style("wall"))
        rename_layer(d, "wall", "WALL")
        assert d.layers.get("wall").name == "WALL"
        assert d.entities[a].style.layer == "WALL"

    @pytest.mark.parametrize("old,new,why", [("0", "x", "can't be renamed"), ("Nope", "x", "no layer"), ("A", "", "needs a name"), ("A", "B", "already"),
                                             ("A", "a/b", "can't contain")])
    def test_refusals(self, old, new, why):
        d = Document()
        add_layer(d, "A")
        add_layer(d, "B")
        with pytest.raises(EditError, match=why):
            rename_layer(d, old, new)

    def test_renaming_to_itself_is_nothing(self):
        d = Document()
        add_layer(d, "A")
        label = d.undo_label
        rename_layer(d, "A", "A")
        assert d.undo_label == label

    def test_flags(self):
        d = Document()
        add_layer(d, "A")
        set_layer_flag(d, "A", on=False, locked=True)
        assert not d.layers.get("A").on and d.layers.get("A").locked
        d.undo()
        assert d.layers.get("A").on
        with pytest.raises(EditError):
            set_layer_flag(d, "Nope", on=False)

    def test_adding(self):
        d = Document()
        assert unique_layer_name(d) == "Layer 1"
        assert add_layer(d) == "Layer 1"
        assert add_layer(d) == "Layer 2"
        with pytest.raises(EditError, match="already"):
            add_layer(d, "layer 1")


class TestView:
    def test_round_trip(self):
        v = View(10, 20, 2.5, 800, 600)
        x, y = v.to_screen(33, 44)
        assert v.to_world(x, y).distance((33, 44)) < 1e-9

    def test_y_goes_up(self):
        v = View(0, 0, 1, 800, 600)
        assert v.to_screen(0, 10)[1] < v.to_screen(0, 0)[1]

    def test_matrix_matches(self):
        v = View(10, 20, 2.5, 800, 600)
        a, b, c, d, e, f = v.matrix()
        x, y = 33.0, 44.0
        assert (a * x + c * y + e, b * x + d * y + f) == pytest.approx(v.to_screen(x, y))

    def test_zoom_keeps_the_point_under_the_cursor(self):
        v = View(10, 20, 2.5, 800, 600)
        before = v.to_world(100, 200)
        v.zoom_at(100, 200, 3.0)
        assert v.to_world(100, 200).distance(before) < 1e-9
        assert v.scale == pytest.approx(7.5)

    def test_zoom_has_limits(self):
        v = View()
        for _ in range(400):
            v.zoom_at(0, 0, 10.0)
        assert v.scale <= 1e9
        for _ in range(400):
            v.zoom_at(0, 0, 0.1)
        assert v.scale >= 1e-9

    def test_pan(self):
        v = View(0, 0, 2, 800, 600)
        before = v.to_screen(5, 5)
        v.pan(10, 20)
        after = v.to_screen(5, 5)
        assert after == pytest.approx((before[0] + 10, before[1] + 20))

    def test_fit_shows_it_all(self):
        v = View(width=800, height=600)
        v.fit((100, 100, 300, 500))
        x0, y0, x1, y1 = v.visible()
        assert x0 <= 100 and y0 <= 100 and x1 >= 300 and y1 >= 500

    def test_fit_on_nothing_and_on_a_point(self):
        v = View(width=800, height=600)
        v.fit(None)
        assert v.scale > 0
        v.fit((5, 5, 5, 5))
        assert v.scale > 0 and v.cx == 5
        v.fit((0, 0, 0, 100))
        assert math.isfinite(v.scale)


class TestSelect:
    def world(self, *shapes):
        d = Document()
        with d.transaction("x") as t:
            ids = [t.add(s) for s in shapes]
        return d, ids

    def test_nearest_first(self):
        d, ids = self.world(line(0, 0, 100, 0), line(0, 3, 100, 3))
        assert pick(d, (50, 2), 1.0) == [ids[1], ids[0]]

    def test_on_top_wins_a_tie(self):
        d, ids = self.world(line(0, 0, 100, 0), line(0, 0, 100, 0))
        assert pick(d, (50, 0), 1.0) == [ids[1], ids[0]]

    def test_out_of_reach(self):
        d, _ = self.world(line(0, 0, 100, 0))
        assert pick(d, (50, 10), 1.0) == []
        assert pick(d, (50, 10), 2.0)

    def test_a_filled_solid_from_inside(self):
        d, ids = self.world(Solid((Vec(0, 0), Vec(100, 0), Vec(100, 100), Vec(0, 100))))
        assert pick(d, (50, 50), 1.0) == ids

    def test_filter(self):
        d, ids = self.world(line(0, 0, 100, 0), Circle(Vec(50, 0), 1))
        assert pick(d, (50, 1), 1.0, filter=lambda e: isinstance(e.shape, Line)) == [ids[0]]

    def test_window_and_crossing_on_arcs(self):
        d, ids = self.world(Arc(Vec(0, 0), 100, 0.0, math.pi / 2))
        # the box of the arc's corner, which the arc doesn't reach
        assert in_window(d, (90, 90, 110, 110), True) == []
        # a box the arc passes through
        assert in_window(d, (60, 60, 80, 80), True) == ids
        assert in_window(d, (60, 60, 80, 80), False) == []
        assert in_window(d, (-10, -10, 110, 110), False) == ids

    def test_crossing_a_circle_from_inside_does_not_count(self):
        d, ids = self.world(Circle(Vec(0, 0), 100))
        assert in_window(d, (-10, -10, 10, 10), True) == []
        assert in_window(d, (90, -10, 110, 10), True) == ids

    def test_crossing_text_and_points(self):
        d, ids = self.world(Text(Vec(0, 0), 10.0, "Hello"), Node(Vec(100, 100)))
        assert in_window(d, (5, 2, 8, 4), True) == [ids[0]]
        assert in_window(d, (99, 99, 101, 101), True) == [ids[1]]
        assert in_window(d, (99, 99, 100, 100), False) == [ids[1]]

    def test_polyline_is_listed_once(self):
        d, ids = self.world(Polyline((Vec(0, 0), Vec(10, 0), Vec(10, 10), Vec(0, 10)), closed=True))
        assert in_window(d, (-1, -1, 11, 11), True) == ids
        assert pick(d, (10, 0), 1.0) == ids

    def test_a_missing_block_is_found_by_its_position(self):
        d, ids = self.world(Insert("Nope", Vec(5, 5)))
        assert pick(d, (5, 5), 1.0) == ids
        assert in_window(d, (4, 4, 6, 6), True) == ids
