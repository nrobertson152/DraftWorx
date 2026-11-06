# SPDX-License-Identifier: GPL-3.0-or-later
"""Hatches: the area round a point, the pattern inside it, following what's round it, files, and the Hatch tool."""
import io
import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from draftworx.core import hatch as hatches
from draftworx.core import regions
from draftworx.core.curves import Arc, Circle, EllipseArc, Line, Polyline
from draftworx.core.dxf.read import read_dxf
from draftworx.core.dxf.write import encode_dxf
from draftworx.core.info import rows_for
from draftworx.core.model import BlockDef, Document, Entity, Settings, Style
from draftworx.core.native import FormatError, dumps, loads
from draftworx.core.ops.reshape import explode_shape
from draftworx.core.select import in_window, pick
from draftworx.core.shapes import Hatch, Insert, Node, PatternLine, Solid
from draftworx.core.vec import Affine, Vec
from driver import Person, line


def V(x, y):
    return Vec(float(x), float(y))


def box(x0, y0, x1, y1):
    return [line(x0, y0, x1, y0), line(x1, y0, x1, y1), line(x1, y1, x0, y1), line(x0, y1, x0, y0)]


def drawing(*shapes):
    doc = Document()
    with doc.transaction("set up") as t:
        ids = [t.add(s) for s in shapes]
    return doc, ids


def area_of(found) -> float:
    assert isinstance(found, regions.Region), getattr(found, "reason", found)
    return found.area


def hatches_in(doc):
    return [e for e in sorted(doc.entities.values(), key=lambda e: e.id) if type(e.shape) is Hatch]


# ------------------------------------------------------------------------------------------------- finding the area


class TestFindingTheArea:
    def test_a_rectangle_of_lines(self):
        doc, ids = drawing(*box(0, 0, 100, 50))
        found = regions.find(doc, V(30, 20), 10.0)
        assert area_of(found) == pytest.approx(5000.0, abs=1e-9)
        assert found.refs == tuple(ids)
        assert len(found.loops) == 1 and sum(regions.curve_area(c) for c in found.loops[0]) > 0.0     # counter-clockwise

    def test_an_island_is_a_hole_and_its_inside_is_its_own(self):
        doc, ids = drawing(*box(0, 0, 100, 50), Circle(V(30, 25), 10.0))
        outer = regions.find(doc, V(70, 25), 10.0)
        assert area_of(outer) == pytest.approx(5000.0 - math.pi * 100.0, abs=1e-9)
        assert len(outer.loops) == 2 and sum(regions.curve_area(c) for c in outer.loops[1]) < 0.0
        assert area_of(regions.find(doc, V(30, 25), 10.0)) == pytest.approx(math.pi * 100.0, abs=1e-9)

    def test_only_islands_directly_inside_are_holes(self):
        doc, _ = drawing(*box(0, 0, 100, 100), *box(20, 20, 80, 80), *box(40, 40, 60, 60))
        assert area_of(regions.find(doc, V(10, 10), 5.0)) == pytest.approx(100 * 100 - 60 * 60)
        assert area_of(regions.find(doc, V(30, 30), 5.0)) == pytest.approx(60 * 60 - 20 * 20)
        assert area_of(regions.find(doc, V(50, 50), 5.0)) == pytest.approx(20 * 20)

    def test_a_line_poking_in_does_not_divide_it(self):
        doc, _ = drawing(*box(0, 0, 100, 50), line(0, 25, 60, 25), line(70, 10, 90, 10))
        found = regions.find(doc, V(80, 40), 10.0)
        assert area_of(found) == pytest.approx(5000.0)
        assert all(type(c) is Line for loop in found.loops for c in loop)
        assert len(found.loops) == 1

    def test_an_island_joined_to_the_edge_is_still_a_hole(self):
        doc, _ = drawing(*box(0, 0, 100, 50), *box(40, 20, 60, 30), line(60, 25, 100, 25))
        found = regions.find(doc, V(20, 40), 10.0)
        assert area_of(found) == pytest.approx(5000.0 - 200.0)
        assert len(found.loops) == 2

    def test_crossing_and_overshooting_lines_close_an_area(self):
        doc, _ = drawing(line(-10, 0, 110, 0), line(100, -10, 100, 60), line(110, 50, -10, 50), line(0, 60, 0, -10))
        assert area_of(regions.find(doc, V(50, 25), 10.0)) == pytest.approx(5000.0)

    def test_the_same_line_twice_is_one_edge(self):
        doc, _ = drawing(*box(0, 0, 100, 50), *box(0, 0, 100, 50), line(20, 0, 80, 0))
        assert area_of(regions.find(doc, V(50, 25), 10.0)) == pytest.approx(5000.0)

    def test_arcs_and_a_polyline_with_bulges_are_exact(self):
        p = Polyline((V(0, 0), V(100, 0), V(100, 50), V(0, 50)), (0.0, 0.4, 0.0, -0.3), True)
        doc, _ = drawing(p)
        assert area_of(regions.find(doc, V(50, 25), 10.0)) == pytest.approx(p.signed_area(), rel=1e-12)

    def test_touching_circles_are_separate_areas(self):
        doc, _ = drawing(Circle(V(0, 0), 10.0), Circle(V(20, 0), 10.0))
        assert area_of(regions.find(doc, V(-5, 0), 4.0)) == pytest.approx(math.pi * 100.0, abs=1e-9)
        assert area_of(regions.find(doc, V(25, 0), 4.0)) == pytest.approx(math.pi * 100.0, abs=1e-9)

    def test_an_ellipse_bounds_an_area(self):
        doc, _ = drawing(EllipseArc(V(0, 0), V(20, 0), 0.5))
        assert area_of(regions.find(doc, V(1, 1), 4.0)) == pytest.approx(math.pi * 20 * 10, rel=1e-9)

    def test_an_area_much_bigger_than_where_it_looks_first(self):
        doc, _ = drawing(*box(0, 0, 100_000, 50_000))
        assert area_of(regions.find(doc, V(10, 10), 1.0)) == pytest.approx(100_000 * 50_000)

    def test_what_a_block_draws_bounds_an_area(self):
        doc = Document()
        with doc.transaction("set up") as t:
            t.put_block(BlockDef("Room", V(0, 0), tuple(Entity(100 + i, s) for i, s in enumerate(box(0, 0, 10, 10)))))
            iid = t.add(Insert("Room", V(50, 50), 2.0, 2.0))
        found = regions.find(doc, V(55, 55), 1.0)
        assert area_of(found) == pytest.approx(400.0)
        assert found.refs == (iid,)

    def test_an_open_area_says_where_it_is_open(self):
        doc, _ = drawing(line(0, 0, 100, 0), line(100, 0, 100, 50), line(100, 50, 0, 50), line(0, 50, 0, 3))
        found = regions.find(doc, V(50, 25), 10.0)
        assert isinstance(found, regions.NoRegion)
        assert "isn't closed" in found.reason
        assert V(0, 3) in found.gaps and V(0, 0) in found.gaps

    def test_a_gap_can_be_closed(self):
        doc, _ = drawing(line(0, 0, 100, 0), line(100, 0, 100, 50), line(100, 50, 0, 50), line(0, 50, 0, 3))
        assert isinstance(regions.find(doc, V(50, 25), 10.0, gap=2.0), regions.NoRegion)
        assert area_of(regions.find(doc, V(50, 25), 10.0, gap=5.0)) == pytest.approx(5000.0)

    def test_dimensions_and_text_are_not_boundaries(self):
        from draftworx.core.shapes import Dimension, Text
        doc, _ = drawing(*box(0, 0, 100, 50), Text(V(40, 20), 5.0, "ROOM"), Dimension("linear", (V(0, 0), V(100, 0)), V(50, 30)))
        assert area_of(regions.find(doc, V(10, 40), 5.0)) == pytest.approx(5000.0)

    def test_a_preview_can_give_up(self):
        doc, _ = drawing(*[line(i, 0, i + 0.5, 100) for i in range(400)], *[line(0, j / 4, 400, j / 4 + 0.3) for j in range(400)])
        with pytest.raises(regions.TooSlow):
            regions.find(doc, V(200.2, 50.1), 300.0, deadline=0.0)

    @given(st.lists(st.tuples(st.floats(-1e4, 1e4), st.floats(-1e4, 1e4)), min_size=3, max_size=12, unique=True),
           st.floats(1e-3, 1e5))
    @settings(max_examples=60)
    def test_any_convex_shape_is_found_exactly(self, raw, size):
        pts = [V(x, y) for x, y in raw]
        hull = _hull(pts)
        if len(hull) < 3:
            return
        poly = Polyline(tuple(p * (size / 1e4) for p in hull), closed=True)
        a = poly.signed_area()
        if a <= (size * 1e-3) ** 2:
            return
        doc, _ = drawing(poly)
        c = poly.centroid()
        assert area_of(regions.find(doc, c, size * 0.01)) == pytest.approx(a, rel=1e-9)

    @given(st.lists(st.tuples(st.integers(1, 18), st.integers(1, 8), st.floats(0.05, 0.45)), min_size=1, max_size=10))
    @settings(max_examples=60)
    def test_islands_are_taken_out_whatever_their_number(self, cells):
        holes = {(cx, cy): r for cx, cy, r in cells}
        shapes = box(0, 0, 20, 10) + [Circle(V(cx, cy), r) for (cx, cy), r in holes.items()]
        doc, _ = drawing(*shapes)
        free = next((V(x + 0.5, y + 0.5) for x in range(20) for y in range(10) if all(math.hypot(x + 0.5 - cx, y + 0.5 - cy) > 1.0 for cx, cy in holes)), None)
        if free is None:
            return
        want = 200.0 - sum(math.pi * r * r for r in holes.values())
        assert area_of(regions.find(doc, free, 0.5)) == pytest.approx(want, abs=1e-9)


def _hull(pts):
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


# ---------------------------------------------------------------------------------------------------------- the pattern


def square_hatch(pattern="ANSI31", scale=1.0, angle=0.0, size=100.0, holes=()):
    loops = (tuple(box(0, 0, size, size)),) + tuple((Circle(c, r),) for c, r in holes)
    return Hatch(loops, pattern, scale, angle)


class TestThePattern:
    def test_lines_are_where_the_pattern_says(self):
        h = square_hatch("LINE", scale=2.0)
        ys = sorted({round(p.a[1], 9) for p in hatches.pieces(h)})
        assert ys == pytest.approx([6.35 * i for i in range(16)])     # inside is from the bottom edge up to, not on, the top one
        assert all(p.a[0] == pytest.approx(0.0, abs=1e-9) and p.b[0] == pytest.approx(100.0, abs=1e-9) for p in hatches.pieces(h))

    def test_diagonals_end_exactly_on_the_boundary(self):
        h = square_hatch("ANSI31", scale=1.0, angle=0.3)
        for p in hatches.pieces(h):
            for q in (p.a, p.b):
                assert min(abs(q[0]), abs(q[0] - 100), abs(q[1]), abs(q[1] - 100)) < 1e-9

    def test_nothing_is_drawn_in_a_hole_and_ends_on_it_are_exact(self):
        h = square_hatch("ANSI37", holes=((V(50, 50), 20.0),))
        assert hatches.pieces(h)
        for p in hatches.pieces(h):
            assert math.hypot(*(p.point_at(0.5) - V(50, 50))) >= 20.0 - 1e-9
            for q in (p.a, p.b):
                d = math.hypot(*(q - V(50, 50)))
                assert d >= 20.0 - 1e-9
                assert d == pytest.approx(20.0, abs=1e-9) or min(abs(q[0]), abs(q[0] - 100), abs(q[1]), abs(q[1] - 100)) < 1e-9

    def test_dots_and_dashes(self):
        dots = hatches.pieces(square_hatch("DOTS", size=20.0))
        assert dots and all(type(p) is Node for p in dots)
        dashes = hatches.pieces(square_hatch("DASH", size=50.0))
        assert dashes and all(type(p) is Line and p.length() <= 3.175 + 1e-9 for p in dashes)

    def test_a_pattern_too_fine_for_its_area_says_so(self):
        with pytest.raises(hatches.TooDense):
            hatches.pieces(square_hatch("ANSI31", scale=1e-5))

    def test_a_solid_fill_draws_no_lines_and_its_area_is_exact(self):
        h = square_hatch("SOLID", holes=((V(50, 50), 20.0),))
        assert hatches.pieces(h) == ()
        assert hatches.area(h) == pytest.approx(100 * 100 - math.pi * 400, rel=1e-12)

    def test_fill_loops_wind_one_way_for_what_is_filled(self):
        h = Hatch((tuple(box(0, 0, 100, 100)), tuple(reversed([c.reversed() for c in box(10, 10, 90, 90)])), tuple(box(20, 20, 80, 80))))
        loops = hatches.fill_loops(h)
        signs = [sum(regions.curve_area(c) for c in loop) > 0 for loop in loops]
        assert signs == [True, False, True]

    def test_solids_for_r12_cover_exactly_a_straight_area(self):
        h = Hatch((tuple(box(0, 0, 100, 50)), tuple(box(20, 10, 40, 30))))
        pieces = hatches.trapezoids(h)
        assert sum(abs(Polyline(s.pts, closed=True).signed_area()) for s in pieces) == pytest.approx(5000 - 400)
        assert all(type(s) is Solid for s in pieces)

    @given(st.floats(0.0, math.tau), st.floats(0.3, 3.0), st.integers(3, 9), st.floats(5.0, 60.0))
    @settings(max_examples=60)
    def test_on_any_line_the_pattern_covers_just_the_inside(self, angle, scale, sides, radius):
        """A regular polygon, any pattern angle and size: along each pattern line, what's drawn is exactly the stretch inside."""
        pts = tuple(V(radius * math.cos(math.tau * i / sides), radius * math.sin(math.tau * i / sides)) for i in range(sides))
        poly = Polyline(pts, closed=True)
        h = Hatch((poly.segments,), "LINE", scale, angle)
        u = V(math.cos(angle), math.sin(angle))
        by_line: dict = {}
        for p in hatches.pieces(h):
            key = round(p.a.dot(u.perp()), 6)
            by_line[key] = by_line.get(key, 0.0) + p.length()
        for level, drawn in by_line.items():
            ts = []
            for seg in poly.segments:
                a, b = seg.a.dot(u.perp()) - level, seg.b.dot(u.perp()) - level
                if (a > 0) != (b > 0):
                    q = seg.a + (seg.b - seg.a) * (a / (a - b))
                    ts.append(q.dot(u))
            assert len(ts) == 2
            assert drawn == pytest.approx(abs(ts[1] - ts[0]), abs=1e-6 * radius)


# --------------------------------------------------------------------------------------------------- following the boundary


def hatched(*shapes, at=(50, 25), pattern="ANSI31"):
    p = Person(*shapes)
    p.run("HATCH")
    if pattern != "ANSI31":
        p.type(pattern).enter()
    p.click(*at).enter()
    (h,) = hatches_in(p.doc)
    return p, h.id


class TestFollowing:
    def test_stretching_the_boundary_takes_the_hatch_and_one_undo_puts_both_back(self):
        p, hid = hatched(*box(0, 0, 100, 50))
        before = p.doc.fingerprint()
        with p.doc.transaction("Stretch") as t:
            t.replace(p.ids[0], line(0, 0, 150, 0))
            t.replace(p.ids[1], line(150, 0, 150, 50))
            t.replace(p.ids[2], line(150, 50, 0, 50))
        assert hatches.area(p.doc.entities[hid].shape) == pytest.approx(7500.0)
        p.doc.undo()
        assert p.doc.fingerprint() == before

    def test_moving_an_island_moves_the_hole(self):
        p, hid = hatched(*box(0, 0, 100, 50), Circle(V(30, 25), 10.0), at=(70, 25))
        with p.doc.transaction("Move") as t:
            t.replace(p.ids[4], Circle(V(40, 30), 10.0))
        h = p.doc.entities[hid].shape
        hole = [loop for loop in h.loops if sum(regions.curve_area(c) for c in loop) < 0]
        assert len(hole) == 1 and hole[0][0].bbox() == pytest.approx((30, 20, 50, 40))
        assert h.refs

    def test_moving_the_boundary_alone_takes_the_hatch(self):
        p, hid = hatched(*box(0, 0, 100, 50))
        p.s.set_selection(p.ids)
        p.run("MOVE").click(200, 200).type("@30,40").enter()
        h = p.doc.entities[hid].shape
        assert h.bbox() == pytest.approx((30, 40, 130, 90))
        assert h.refs

    def test_moving_both_keeps_it_attached(self):
        p, hid = hatched(*box(0, 0, 100, 50))
        with p.doc.transaction("Move") as t:
            m = Affine.translation(10, 0)
            for i in [*p.ids, hid]:
                t.replace(i, p.doc.entities[i].shape.transformed(m))
        assert p.doc.entities[hid].shape.refs

    def test_moving_the_hatch_alone_lets_go(self):
        p, hid = hatched(*box(0, 0, 100, 50))
        with p.doc.transaction("Move") as t:
            t.replace(hid, p.doc.entities[hid].shape.transformed(Affine.translation(10, 0)))
        assert p.doc.entities[hid].shape.refs == ()

    def test_erasing_part_of_the_boundary_lets_go_and_leaves_it_as_it_was(self):
        p, hid = hatched(*box(0, 0, 100, 50))
        before = p.doc.entities[hid].shape.loops
        with p.doc.transaction("Erase") as t:
            t.remove(p.ids[0])
        h = p.doc.entities[hid].shape
        assert h.refs == () and h.loops == before

    def test_a_copy_with_its_boundary_follows_the_copy(self):
        p, hid = hatched(*box(0, 0, 100, 50))
        p.s.set_selection([*p.ids, hid])
        p.run("COPY").click(0, 0).type("@0,100").enter().enter()
        copy = hatches_in(p.doc)[1]
        assert set(copy.shape.refs).isdisjoint(p.ids) and len(copy.shape.refs) == 4
        with p.doc.transaction("Stretch") as t:
            for i in copy.shape.refs:
                t.replace(i, p.doc.entities[i].shape.transformed(Affine.scaling(2.0, 1.0, V(0, 100))))
        assert hatches.area(p.doc.entities[copy.id].shape) == pytest.approx(10_000.0)
        assert hatches.area(p.doc.entities[hid].shape) == pytest.approx(5000.0)

    def test_a_property_change_keeps_it_attached(self):
        p, hid = hatched(*box(0, 0, 100, 50))
        h = p.doc.entities[hid].shape
        (pattern,) = [r for r in rows_for(h, Settings()) if r.label == "Pattern"]
        with p.doc.transaction("Pattern") as t:
            t.replace(hid, pattern.edit("net"))
        h = p.doc.entities[hid].shape
        assert h.pattern == "NET" and h.refs


# ------------------------------------------------------------------------------------------------------------- the tool


class TestTheTool:
    def test_pointing_shows_what_a_click_would_fill(self):
        with Person(*box(0, 0, 100, 50)) as p:
            p.run("HATCH").at(50, 25)
            assert len(p.s.ghosts) == 1 and type(p.s.ghosts[0]) is Hatch
            p.at(150, 25)
            assert p.s.ghosts == []
            p.key("escape")

    def test_an_open_area_shows_where_it_is_open(self):
        with Person(line(0, 0, 100, 0), line(100, 0, 100, 50), line(100, 50, 0, 50), line(0, 50, 0, 5)) as p:
            p.run("HATCH").at(50, 25)
            assert p.s.doomed and all(type(c) is Circle for c in p.s.doomed)
            p.click(50, 25)
            assert "isn't closed" in p.s.message and not hatches_in(p.doc)
            p.type("G").enter().type("10").enter().click(50, 25).enter()
            assert len(hatches_in(p.doc)) == 1

    def test_sized_for_the_sheet_at_the_drawings_scale(self):
        with Person(*box(0, 0, 10_000, 5000)) as p:
            with p.doc.transaction("Scale") as t:
                t.set_settings(Settings(annotation_scale=50.0))
            p.run("HATCH").click(5000, 2500).enter()
            assert hatches_in(p.doc)[0].shape.scale == pytest.approx(50.0)

    def test_inches_too(self):
        with Person(*box(0, 0, 100, 50)) as p:
            with p.doc.transaction("Units") as t:
                t.set_settings(Settings(units="in"))
            p.run("HATCH").click(50, 25).enter()
            assert hatches_in(p.doc)[0].shape.scale == pytest.approx(1 / 25.4)

    def test_a_pattern_by_name_scale_and_angle(self):
        with Person(*box(0, 0, 100, 50)) as p:
            p.run("HATCH").type("net").enter().type("S").enter().type("2").enter().type("A").enter().type("30").enter().click(50, 25).enter()
            h = hatches_in(p.doc)[0].shape
            assert h.pattern == "NET" and (h.scale, math.degrees(h.angle)) == pytest.approx((2.0, 30.0))
            p.run("HATCH").type("nonsense").enter()
            assert "no pattern" in p.s.message
            p.key("escape")

    def test_twice_in_the_same_area_is_refused(self):
        with Person(*box(0, 0, 100, 50)) as p:
            p.run("HATCH").click(50, 25).click(60, 30)
            assert "already" in p.s.message and len(hatches_in(p.doc)) == 1
            p.enter()

    def test_undo_inside_the_tool_takes_back_the_last(self):
        with Person(*box(0, 0, 100, 50), *box(200, 0, 300, 50)) as p:
            p.run("HATCH").click(50, 25).click(250, 25)
            p.s.undo()
            assert len(hatches_in(p.doc)) == 1
            p.enter()

    def test_a_line_drawn_over_a_hatch_wins_the_click(self):
        with Person(*box(0, 0, 100, 50), line(20, 10, 80, 10)) as p:
            p.run("HATCH").click(50, 30).enter()
            hid = hatches_in(p.doc)[0].id
            assert pick(p.doc, V(50, 10.5), 1.0)[0] == p.ids[4]
            assert pick(p.doc, V(50, 30), 1.0) == [hid]
            assert hid in in_window(p.doc, (-1, -1, 101, 51), False)
            assert hid in in_window(p.doc, (40, 20, 45, 25), True)


# ---------------------------------------------------------------------------------------------------- changing it, and files


class TestChangingAndFiles:
    def test_explode(self):
        h = square_hatch("LINE")
        assert all(type(s) is Line for s, _ in explode_shape(Document(), h, Style()))
        assert len(explode_shape(Document(), square_hatch("SOLID"), Style())) == 4

    def test_mirroring_keeps_the_pattern_readable_and_turning_turns_it(self):
        h = square_hatch("ANSI31")
        assert h.transformed(Affine.mirror(V(0, 0), V(0, 1))).angle == 0.0
        assert h.transformed(Affine.rotation(0.5)).angle == pytest.approx(0.5)
        assert h.transformed(Affine.scaling(3.0)).scale == pytest.approx(3.0)

    def test_saved_and_opened(self):
        p, hid = hatched(*box(0, 0, 100, 50), Circle(V(30, 25), 10.0), at=(70, 25))
        data = dumps(p.doc)
        assert b'"version":3' in data.split(b"\n")[0]
        back = loads(data)
        assert back.fingerprint() == p.doc.fingerprint()
        assert back.attached_to(p.ids[0]) == {hid}

    def test_a_drawing_without_hatches_stays_an_older_version(self):
        p = Person(*box(0, 0, 100, 50))
        assert b'"version":1' in dumps(p.doc).split(b"\n")[0]

    def test_a_damaged_hatch_is_refused_with_a_message(self):
        import json
        p, hid = hatched(*box(0, 0, 100, 50))
        lines = dumps(p.doc).decode().split("\n")
        at = next(i for i, x in enumerate(lines) if '"type":"hatch"' in x)
        good = json.loads(lines[at].rstrip(","))
        for change in ({"loops": []}, {"loops": [[{"type": "text"}]]}, {"refs": ["x"]}, {"lines": [[1, 2]]}, {"scale": 0}, {"seeds": [[1]]}):
            bad = dict(good, **change)
            lines[at] = json.dumps(bad, separators=(",", ":")) + ("," if lines[at].endswith(",") else "")
            with pytest.raises(FormatError):
                loads("\n".join(lines).encode())
        assert hid

    @pytest.mark.parametrize("version", ["AC1015", "AC1027", "AC1032"])
    def test_dxf_out_is_read_by_ezdxf_and_draws_the_same_lines(self, version):
        ezdxf = pytest.importorskip("ezdxf")
        from ezdxf import recover
        from ezdxf.render import hatching
        p, hid = hatched(*box(0, 0, 100, 50), Arc(V(100, 25), 25.0, -math.pi / 2, math.pi), Circle(V(30, 25), 10.0),
                         EllipseArc(V(70, 25), V(12, 0), 0.5), at=(50, 45))
        p.run("HATCH").type("SOLID").enter().click(30, 25).type("NET").enter().click(70, 25).enter()
        data, report = encode_dxf(p.doc, version)
        assert not report.sentences()
        doc, auditor = recover.read(io.BytesIO(data))
        assert not auditor.errors
        ours = hatches_in(p.doc)
        theirs = list(doc.modelspace().query("HATCH"))
        assert len(theirs) == 3 and ezdxf is not None
        for a, b in zip(ours, theirs):
            assert bool(b.dxf.solid_fill) == a.shape.solid
            if not a.shape.solid:
                assert sum(1 for _ in hatching.hatch_entity(b)) == len(hatches.pieces(a.shape))
        back, _ = read_dxf(data)
        for a, b in zip(ours, hatches_in(back)):
            assert hatches.area(b.shape) == pytest.approx(hatches.area(a.shape), rel=1e-12)
            assert len(hatches.pieces(b.shape)) == len(hatches.pieces(a.shape))
            assert b.shape.pattern == a.shape.pattern

    def test_r12_gets_the_lines_and_solids_it_draws(self):
        p, _ = hatched(*box(0, 0, 100, 50), *box(200, 0, 300, 50))
        p.run("HATCH").type("SOLID").enter().click(250, 25).enter()
        assert len(hatches_in(p.doc)) == 2
        data, report = encode_dxf(p.doc, "AC1009")
        assert any("hatches" in s for s in report.sentences())
        back, _ = read_dxf(data)
        kinds = {type(e.shape) for e in back.entities.values()}
        assert Line in kinds and Solid in kinds and Hatch not in kinds

    def test_a_dxf_hatch_comes_back_unchanged_and_can_be_changed(self):
        p, hid = hatched(*box(0, 0, 100, 50))
        data, _ = encode_dxf(p.doc, "AC1027")
        doc, _ = read_dxf(data)
        again, _ = encode_dxf(doc, "AC1027")
        assert again == data
        h = hatches_in(doc)[0]
        with doc.transaction("Bigger") as t:
            t.replace(h.id, Hatch(h.shape.loops, h.shape.pattern, h.shape.scale * 2, h.shape.angle, lines=h.shape.lines))
        changed, _ = encode_dxf(doc, "AC1027")
        assert changed != data
        assert len(hatches.pieces(hatches_in(read_dxf(changed)[0])[0].shape)) < len(hatches.pieces(h.shape))

    def test_a_file_s_own_pattern_is_kept(self):
        custom = (PatternLine(0.2, V(0, 0), 1.0, 4.0, (2.0, -1.0)),)
        p = Person()
        with p.doc.transaction("x") as t:
            t.add(Hatch((tuple(box(0, 0, 50, 50)),), "MINE", 2.0, 0.1, lines=custom))
        back, _ = read_dxf(encode_dxf(p.doc, "AC1027")[0])
        h = hatches_in(back)[0].shape
        assert h.pattern == "MINE"
        for (a0, b0, s0, d0), (a1, b1, s1, d1) in zip(hatches.world_lines(h), hatches.world_lines(hatches_in(p.doc)[0].shape), strict=True):
            assert (a0 % math.tau, *b0, *s0, *d0) == pytest.approx((a1 % math.tau, *b1, *s1, *d1), abs=1e-9)
