# SPDX-License-Identifier: GPL-3.0-or-later
import pytest
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from draftworx.core import color as colors
from draftworx.core.curves import Arc, Circle, Line, Polyline
from draftworx.core.model import (BlockDef, Document, EditError, Entity, Layer, Style, CENTRE_SUB, index_key, split_key)
from draftworx.core.shapes import Insert, Node, Text
from draftworx.core.vec import Vec
from geom_strategies import circles, lines, polylines


def line(x0=0.0, y0=0.0, x1=10.0, y1=0.0):
    return Line(Vec(x0, y0), Vec(x1, y1))


def doc_with(*shapes):
    d = Document()
    with d.transaction("set up") as t:
        ids = [t.add(s) for s in shapes]
    return d, ids


class TestTransaction:
    def test_add_is_one_undo_step(self):
        d = Document()
        with d.transaction("Draw") as t:
            a = t.add(line())
            b = t.add(line(1, 1, 2, 2))
        assert set(d.entities) == {a, b}
        assert d.undo_label == "Draw"
        assert d.undo() == "Draw"
        assert d.entities == {}
        assert d.redo() == "Draw"
        assert set(d.entities) == {a, b}

    def test_failure_leaves_nothing_behind(self):
        d, (a,) = doc_with(line())
        before = d.fingerprint()
        with pytest.raises(RuntimeError):
            with d.transaction("Bad") as t:
                t.remove(a)
                t.add(line(5, 5, 6, 6))
                t.put_layer(Layer("New"))
                raise RuntimeError("part way")
        assert d.fingerprint() == before
        assert d.undo_label == "set up"
        assert d.index.query((-1, -1, 11, 1)) == [index_key(a)]
        assert "New" not in d.layers

    def test_failure_notifies_nobody(self):
        d, (a,) = doc_with(line())
        heard = []
        d.on_change(heard.append)
        with pytest.raises(RuntimeError):
            with d.transaction("Bad") as t:
                t.remove(a)
                raise RuntimeError()
        assert heard == []

    def test_empty_change_is_not_a_step(self):
        d = Document()
        with d.transaction("Nothing"):
            pass
        assert not d.can_undo

    def test_setting_something_to_what_it_is_is_not_a_step(self):
        d, (a,) = doc_with(line())
        with d.transaction("Same") as t:
            t.replace(a, line())
            t.put_layer(Layer("0"))
        assert d.undo_label == "set up"

    def test_change_undone_within_itself_is_not_a_step(self):
        d, (a,) = doc_with(line())
        e = d.entities[a]
        with d.transaction("Back and forth") as t:
            t.replace(a, line(1, 1, 2, 2))
            t._record("entity", a, e)
        assert d.undo_label == "set up"

    def test_repeated_change_keeps_the_first_old_value(self):
        d, (a,) = doc_with(line())
        with d.transaction("Twice") as t:
            t.replace(a, line(1, 1, 2, 2))
            t.replace(a, line(3, 3, 4, 4))
        d.undo()
        assert d.entities[a].shape == line()

    def test_add_then_remove_in_one_change_is_nothing(self):
        d = Document()
        with d.transaction("x") as t:
            eid = t.add(line())
            t.remove(eid)
        assert not d.can_undo and not d.entities

    def test_nested_changes_are_refused(self):
        d = Document()
        with d.transaction("a"):
            with pytest.raises(EditError):
                with d.transaction("b"):
                    pass

    def test_undo_during_a_change_is_refused(self):
        d, _ = doc_with(line())
        with d.transaction("a"):
            with pytest.raises(EditError):
                d.undo()
            with pytest.raises(EditError):
                d.redo()

    def test_using_a_finished_transaction_is_refused(self):
        d = Document()
        with d.transaction("a") as t:
            pass
        with pytest.raises(EditError):
            t.add(line())

    def test_a_new_change_clears_redo(self):
        d, _ = doc_with(line())
        d.undo()
        assert d.can_redo
        with d.transaction("Other") as t:
            t.add(line(5, 5, 6, 6))
        assert not d.can_redo
        assert d.redo() is None

    def test_ids_are_never_reused(self):
        d, (a,) = doc_with(line())
        d.undo()
        with d.transaction("Other") as t:
            b = t.add(line())
        assert b != a

    def test_replacing_or_removing_what_is_gone_says_so(self):
        d = Document()
        with d.transaction("x") as t:
            with pytest.raises(EditError):
                t.replace(99, line())
            with pytest.raises(EditError):
                t.remove(99)


class TestDirty:
    def test_undo_back_to_the_save_is_clean(self):
        d, _ = doc_with(line())
        d.mark_saved()
        assert not d.dirty
        with d.transaction("More") as t:
            t.add(line(1, 1, 2, 2))
        assert d.dirty
        d.undo()
        assert not d.dirty
        d.redo()
        assert d.dirty

    def test_undo_past_the_save_is_dirty(self):
        d, _ = doc_with(line())
        d.mark_saved()
        d.undo()
        assert d.dirty

    def test_a_different_change_after_undo_stays_dirty(self):
        d, _ = doc_with(line())
        with d.transaction("A") as t:
            t.add(line(1, 1, 2, 2))
        d.mark_saved()
        d.undo()
        with d.transaction("B") as t:
            t.add(line(3, 3, 4, 4))
        assert d.dirty
        d.undo()
        assert d.dirty      # one step back is the state before the save


class TestIndex:
    def test_entity_is_found_where_it_is(self):
        d, (a,) = doc_with(line())
        assert d.index.query((4, -1, 5, 1)) == [index_key(a)]
        assert d.index.query((20, 20, 21, 21)) == []

    def test_follows_a_change_and_its_undo(self):
        d, (a,) = doc_with(line())
        with d.transaction("Move") as t:
            t.replace(a, line(100, 100, 110, 100))
        assert d.index.query((4, -1, 5, 1)) == []
        assert d.index.query((104, 99, 105, 101)) == [index_key(a)]
        d.undo()
        assert d.index.query((4, -1, 5, 1)) == [index_key(a)]
        assert d.index.query((104, 99, 105, 101)) == []

    def test_polyline_segments_are_each_indexed(self):
        p = Polyline((Vec(0, 0), Vec(100, 0), Vec(100, 100)))
        d, (a,) = doc_with(p)
        assert d.index.query((10, -1, 11, 1)) == [index_key(a, 0)]
        assert d.index.query((99, 50, 101, 51)) == [index_key(a, 1)]
        assert d.index.query((50, 50, 51, 51)) == []
        with d.transaction("x") as t:
            t.remove(a)
        assert len(d.index) == 0

    def test_arc_centre_is_findable_though_outside_the_arc(self):
        arc = Arc(Vec(0, 0), 100, 0.1, 0.2)
        d, (a,) = doc_with(arc)
        assert d.index.query((-1, -1, 1, 1)) == [index_key(a, CENTRE_SUB)]

    def test_centre_of_a_polyline_arc_is_findable_and_goes_with_it(self):
        p = Polyline((Vec(0, 0), Vec(100, 0)), (0.2, 0.0))
        c = p.segments[0].c
        d, (a,) = doc_with(p)
        assert d.index.query((c[0] - 1, c[1] - 1, c[0] + 1, c[1] + 1))
        with d.transaction("x") as t:
            t.remove(a)
        assert len(d.index) == 0

    def test_closed_polyline_centre_is_findable(self):
        p = Polyline((Vec(0, 0), Vec(100, 0), Vec(100, 100), Vec(0, 100)), closed=True)
        d, (a,) = doc_with(p)
        assert d.index.query((49, 49, 51, 51)) == [index_key(a, CENTRE_SUB)]

    def test_key_round_trip(self):
        assert split_key(index_key(123, 45)) == (123, 45)
        assert split_key(index_key(123, CENTRE_SUB)) == (123, CENTRE_SUB)


class TestLayers:
    def test_hidden_and_locked_follow_changes_and_undo(self):
        d = Document()
        with d.transaction("Layer") as t:
            t.put_layer(Layer("Walls", on=False, locked=True))
        s = Style("WALLS")      # compared without case
        assert d.is_hidden(s) and d.is_locked(s)
        d.undo()
        assert not d.is_hidden(s) and not d.is_locked(s)
        d.redo()
        assert d.is_hidden(s)

    def test_layer_0_cannot_go(self):
        d = Document()
        with d.transaction("x") as t, pytest.raises(EditError, match="always"):
            t.remove_layer("0")

    def test_a_layer_in_use_cannot_go(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_layer(Layer("A"))
            t.add(line(), Style("A"))
        with d.transaction("y") as t, pytest.raises(EditError, match="1 object"):
            t.remove_layer("a")

    def test_a_layer_used_only_in_a_block_cannot_go(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_layer(Layer("A"))
            t.put_block(BlockDef("B", entities=(Entity(9, line(), Style("A")),)))
        with d.transaction("y") as t, pytest.raises(EditError, match="1 object"):
            t.remove_layer("A")

    def test_the_current_layer_cannot_go(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_layer(Layer("A"))
        d.current.layer = "A"
        with d.transaction("y") as t, pytest.raises(EditError, match="current"):
            t.remove_layer("A")

    def test_an_unused_layer_can_go(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_layer(Layer("A"))
        with d.transaction("y") as t:
            t.remove_layer("A")
        assert "A" not in d.layers
        d.undo()
        assert "A" in d.layers

    def test_counts_follow_edits(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_layer(Layer("A"))
            a = t.add(line(), Style("A"))
        assert d.layer_use("A") == 1
        with d.transaction("y") as t:
            t.replace(a, style=Style("0"))
        assert d.layer_use("A") == 0 and d.layer_use("0") == 1
        d.undo()
        assert d.layer_use("A") == 1


class TestBlocks:
    def test_extents_of_an_insert(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_block(BlockDef("B", entities=(Entity(100, line(0, 0, 10, 0)), Entity(101, line(0, 0, 0, 5)))))
            t.add(Insert("B", Vec(100, 100), 2.0, 2.0))
        assert d.extents() == (100, 100, 120, 110)

    def test_base_point_is_where_it_lands(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_block(BlockDef("B", base=Vec(5, 5), entities=(Entity(100, line(5, 5, 15, 5)),)))
            t.add(Insert("B", Vec(0, 0)))
        assert d.extents() == (0, 0, 10, 0)

    def test_a_block_cannot_contain_itself(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_block(BlockDef("A", entities=(Entity(100, Insert("B", Vec(0, 0))),)))
            t.put_block(BlockDef("B"))
        with d.transaction("y") as t, pytest.raises(EditError, match="contain itself"):
            t.put_block(BlockDef("B", entities=(Entity(101, Insert("A", Vec(0, 0))),)))
        with d.transaction("z") as t, pytest.raises(EditError, match="contain itself"):
            t.put_block(BlockDef("C", entities=(Entity(102, Insert("C", Vec(0, 0))),)))

    def test_a_block_in_use_cannot_go(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_block(BlockDef("B"))
            t.add(Insert("B", Vec(0, 0)))
        with d.transaction("y") as t, pytest.raises(EditError, match="1 time"):
            t.remove_block("B")

    def test_placed_copies_are_found_where_a_changed_block_now_reaches(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_block(BlockDef("B", entities=(Entity(100, line(0, 0, 10, 0)),)))
            a = t.add(Insert("B", Vec(0, 0)))
        assert d.index.query((50, -1, 60, 1)) == []
        with d.transaction("y") as t:
            t.put_block(BlockDef("B", entities=(Entity(100, line(0, 0, 100, 0)),)))
        assert d.index.query((50, -1, 60, 1)) == [index_key(a)]
        assert d.extents() == (0, 0, 100, 0)
        d.undo()
        assert d.index.query((50, -1, 60, 1)) == []
        assert d.extents() == (0, 0, 10, 0)

    def test_changing_a_block_changes_its_bounds(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_block(BlockDef("B", entities=(Entity(100, line(0, 0, 10, 0)),)))
        assert d.block_bbox("B") == (0, 0, 10, 0)
        with d.transaction("y") as t:
            t.put_block(BlockDef("B", entities=(Entity(100, line(0, 0, 20, 0)),)))
        assert d.block_bbox("B") == (0, 0, 20, 0)
        d.undo()
        assert d.block_bbox("B") == (0, 0, 10, 0)

    def test_an_insert_of_a_missing_block_is_harmless(self):
        d, (a,) = doc_with(Insert("Nope", Vec(5, 5)))
        assert d.extents() == (5, 5, 5, 5)
        assert d.entities[a].shape.block == "Nope"

    def test_runaway_nesting_is_cut_off(self):
        d = Document()
        d.blocks._put(BlockDef("A", entities=(Entity(1, Insert("B", Vec(0, 0))),)))
        d.blocks._put(BlockDef("B", entities=(Entity(2, Insert("A", Vec(0, 0))),)))
        d.block_bbox("A")       # returns rather than recursing forever


class TestNotify:
    def test_listener_hears_commit_undo_and_redo(self):
        d = Document()
        heard = []
        stop = d.on_change(lambda ch: heard.append(len(ch)))
        with d.transaction("x") as t:
            t.add(line())
            t.add(line())
        d.undo()
        d.redo()
        assert heard == [2, 2, 2]
        stop()
        d.undo()
        assert heard == [2, 2, 2]


class TestEmpty:
    def test_a_new_drawing_has_what_it_needs(self):
        d = Document()
        assert "0" in d.layers and "Standard" in d.text_styles and "Standard" in d.dim_styles
        assert "Continuous" in d.linetypes and "ByLayer" in d.linetypes
        assert d.extents() is None
        assert not d.dirty and not d.can_undo

    def test_colors(self):
        assert colors.rgb_of(1) == 0xff0000
        assert colors.rgb_of(colors.BYLAYER, 0x123456) == 0x123456
        assert colors.rgb_of(colors.true_color(1, 2, 3)) == 0x010203
        assert colors.nearest_index(0xff0000) == 1
        assert 1 <= colors.nearest_index(0x010203) <= 254
        assert colors.valid(0) and colors.valid(256) and colors.valid(colors.TRUE)
        assert not colors.valid(257) and not colors.valid(True) and not colors.valid("1")
        assert colors.describe(256) == "ByLayer" and colors.describe(0) == "ByBlock" and colors.describe(1) == "Red"

    def test_the_colour_wheel_matches_published_values(self):
        known = {10: 0xff0000, 11: 0xff7f7f, 20: 0xff3f00, 21: 0xff9f7f, 23: 0xa56752, 25: 0x7f4f3f, 27: 0x4c2f26, 29: 0x261713,
                 30: 0xff7f00, 50: 0xffff00, 90: 0x00ff00, 109: 0x132617, 119: 0x13261c, 130: 0x00ffff, 170: 0x0000ff, 210: 0xff00ff}
        for i, v in known.items():
            assert colors.ACI[i] == v, i


class EditMachine(RuleBasedStateMachine):
    """Any sequence of edits, undos and redos keeps every promise: undo restores the exact drawing, indexes stay true."""

    def __init__(self):
        super().__init__()
        self.doc = Document()
        self.states = [self.doc.fingerprint()]   # the drawing after each step, as far back as undo can go
        self.position = 0

    def _step(self):
        self.states = self.states[:self.position + 1]
        self.states.append(self.doc.fingerprint())
        self.position += 1

    @rule(shape=st.one_of(lines, circles, polylines), layer=st.sampled_from(["0", "A", "b"]))
    def add(self, shape, layer):
        with self.doc.transaction("Add") as t:
            if layer not in self.doc.layers:
                t.put_layer(Layer(layer))
            t.add(shape, Style(layer))
        self._step()

    @precondition(lambda self: self.doc.entities)
    @rule(data=st.data(), shape=st.one_of(lines, circles))
    def change(self, data, shape):
        eid = data.draw(st.sampled_from(sorted(self.doc.entities)))
        with self.doc.transaction("Change") as t:
            t.replace(eid, shape)
        self._step()

    @precondition(lambda self: self.doc.entities)
    @rule(data=st.data())
    def remove(self, data):
        eid = data.draw(st.sampled_from(sorted(self.doc.entities)))
        with self.doc.transaction("Remove") as t:
            t.remove(eid)
        self._step()

    @precondition(lambda self: self.doc.entities)
    @rule(data=st.data())
    def fail_part_way(self, data):
        eid = data.draw(st.sampled_from(sorted(self.doc.entities)))
        before = self.doc.fingerprint()
        try:
            with self.doc.transaction("Fail") as t:
                t.remove(eid)
                t.add(Node(Vec(1, 1)))
                raise KeyError("boom")
        except KeyError:
            pass
        assert self.doc.fingerprint() == before

    @rule(on=st.booleans())
    def layer_state(self, on):
        with self.doc.transaction("Layer") as t:
            t.put_layer(Layer("A", on=on, locked=not on))
        if self.states[self.position] != self.doc.fingerprint():
            self._step()

    @precondition(lambda self: self.position > 0)
    @rule()
    def undo(self):
        self.doc.undo()
        self.position -= 1

    @precondition(lambda self: self.position < len(self.states) - 1)
    @rule()
    def redo(self):
        self.doc.redo()
        self.position += 1

    @invariant()
    def state_is_what_it_was_at_this_step(self):
        assert self.doc.fingerprint() == self.states[self.position]

    @invariant()
    def index_matches_a_fresh_build(self):
        fresh = Document()
        fresh.blocks = self.doc.blocks
        for e in self.doc.entities.values():
            fresh._index(e)
        window = (-1e7, -1e7, 1e7, 1e7)
        assert self.doc.index.query(window) == fresh.index.query(window)
        assert self.doc._layer_counts == fresh._layer_counts


TestEditMachine = EditMachine.TestCase
EditMachine.TestCase.settings = __import__("hypothesis").settings(max_examples=40, stateful_step_count=30, deadline=None, derandomize=True)


def test_text_measures_the_same_everywhere():
    t = Text(Vec(0, 0), 10.0, "Hello")
    x0, y0, x1, y1 = t.bbox()
    assert (x0, y0) == (0, 0)
    assert y1 == 10.0
    assert 25 < x1 < 40


def test_circle_helper():
    assert Circle(Vec(0, 0), 1).length() > 6
