# SPDX-License-Identifier: GPL-3.0-or-later
import json
import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from draftworx.core import color as colors
from draftworx.core.curves import Arc, Circle, EllipseArc, Line, Polyline, Spline
from draftworx.core.model import BlockDef, DimStyle, Document, Entity, Layer, Linetype, Settings, Style, TextStyle
from draftworx.core.native import FORMAT, VERSION, FormatError, dumps, loads
from draftworx.core.shapes import AttDef, Attrib, Insert, MText, Node, Solid, Text
from draftworx.core.vec import Vec
from geom_strategies import curves, polylines


def every_shape():
    return [
        Line(Vec(0.1, 0.2), Vec(1 / 3, 2 / 3)),
        Arc(Vec(1, 2), 3.5, 0.25, 1.75),
        Arc(Vec(1, 2), 3.5, 0.25, -1.75),
        Circle(Vec(-1, -2), 7.125),
        EllipseArc(Vec(0, 0), Vec(3, 4), 0.5),
        EllipseArc(Vec(0, 0), Vec(3, 4), 0.5, 1.0, -2.0),
        Polyline((Vec(0, 0), Vec(1, 0), Vec(1, 1)), (0.0, 0.5, 0.0), True, 0.25, ((0.1, 0.2), (0.3, 0.4), (0.5, 0.6))),
        Polyline((Vec(0, 0), Vec(1, 0))),
        Spline(3, (0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0), (Vec(0, 0), Vec(1, 1), Vec(2, 1), Vec(3, 0))),
        Spline(2, (0.0, 0.0, 0.0, 1.0, 1.0, 1.0), (Vec(1, 0), Vec(1, 1), Vec(0, 1)), (1.0, math.sqrt(0.5), 1.0), (Vec(1, 0),), True),
        Node(Vec(9, 9)),
        Solid((Vec(0, 0), Vec(1, 0), Vec(1, 1))),
        Text(Vec(1, 1), 2.5, "Héllo ‘quoted’ \"x\"\n", 0.5, "S1", 1, 2, 0.8, 0.1, Vec(2, 2), True, True),
        MText(Vec(1, 1), 2.5, r"{\fArial;Top}\Pline", 40.0, 0.5, 5, "S1", 1.5),
        AttDef(Attrib("PART", Text(Vec(0, 0), 2.5, "?"), "Part number?", True, True)),
        Insert("B", Vec(5, 5), 2.0, -3.0, 0.5, (Attrib("PART", Text(Vec(5, 5), 2.5, "A-1")),), 2, 3, 10.0, 20.0),
    ]


def full_document():
    d = Document()
    with d.transaction("x") as t:
        t.put_layer(Layer("Walls", 3, "Dashed", 50, False, True, True, False, "the walls"))
        t.put_linetype(Linetype("Dashed", "- - -", (5.0, -2.5, 0.0, -2.5)))
        t.put_text_style(TextStyle("S1", "isocpeur.ttf", 3.5, 0.9, 0.2))
        t.put_dim_style(DimStyle("D1", text_height=3.5, decimals=1, prefix_suffix="<> mm", text_inside_horizontal=True))
        t.put_block(BlockDef("B", Vec(1, 1), (Entity(1000, Line(Vec(0, 0), Vec(1, 1)), Style("Walls", 1)),), "a block", 2))
        for i, s in enumerate(every_shape()):
            t.add(s, Style("Walls" if i % 2 else "0", 1 if i % 3 else colors.true_color(1, 2, 3), "Dashed", 25, 2.0))
        t.set_settings(Settings("in", "architectural", 3, 2, 48.0, 2.5, 3, 1.5))
    d.current.layer = "Walls"
    d.current.color = 3
    return d


class TestRoundTrip:
    def test_everything_comes_back_exactly(self):
        d = full_document()
        back = loads(dumps(d))
        assert back.fingerprint() == d.fingerprint()
        assert back.current.layer == "Walls" and back.current.color == 3

    def test_saving_twice_gives_identical_bytes(self):
        d = full_document()
        once = dumps(d)
        assert dumps(loads(once)) == once

    def test_order_of_making_does_not_change_the_bytes(self):
        a, b = Document(), Document()
        with a.transaction("x") as t:
            t.put_layer(Layer("A"))
            t.put_layer(Layer("B"))
        with b.transaction("x") as t:
            t.put_layer(Layer("B"))
            t.put_layer(Layer("A"))
        assert dumps(a) == dumps(b)

    def test_undo_then_save_matches_never_having_done_it(self):
        d = full_document()
        once = dumps(d)
        with d.transaction("y") as t:
            t.add(Line(Vec(0, 0), Vec(1, 1)))
            t.put_layer(Layer("Temp"))
        d.undo()
        # the id counter moved on (ids are never reused), but the drawing is the same
        strip = lambda b: [ln for ln in b.decode().split("\n") if not ln.startswith('{"format"')]
        assert strip(dumps(d)) == strip(once)

    def test_ids_continue_after_reload(self):
        d = Document()
        with d.transaction("x") as t:
            t.add(Line(Vec(0, 0), Vec(1, 1)))
            b = t.add(Line(Vec(0, 0), Vec(1, 1)))
            t.remove(b)
        back = loads(dumps(d))
        with back.transaction("y") as t:
            c = t.add(Line(Vec(0, 0), Vec(1, 1)))
        assert c > b

    def test_ids_in_a_block_are_not_reused_either(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_block(BlockDef("B", entities=(Entity(5000, Line(Vec(0, 0), Vec(1, 1))),)))
        back = loads(dumps(d))
        with back.transaction("y") as t:
            assert t.add(Node(Vec(0, 0))) > 5000

    def test_draw_order_survives(self):
        d = Document()
        with d.transaction("x") as t:
            a = t.add(Line(Vec(0, 0), Vec(1, 1)))
            b = t.add(Line(Vec(0, 0), Vec(1, 1)))
            t.replace(a, order=b + 1)
        back = loads(dumps(d))
        assert [e.id for e in back.sorted_entities()] == [b, a]

    @given(st.lists(st.one_of(curves, polylines), max_size=12))
    def test_any_geometry_round_trips_exactly(self, shapes):
        d = Document()
        with d.transaction("x") as t:
            for s in shapes:
                t.add(s)
        back = loads(dumps(d))
        assert back.fingerprint() == d.fingerprint()
        for eid, e in d.entities.items():
            assert back.entities[eid].shape == e.shape

    def test_floats_are_exact_to_the_last_bit(self):
        x = math.nextafter(1 / 3, 1.0)
        d = Document()
        with d.transaction("x") as t:
            a = t.add(Line(Vec(x, -x), Vec(1e-300, 1e300)))
        back = loads(dumps(d)).entities[a].shape
        assert back.a[0] == x and back.a[1] == -x and back.b == Vec(1e-300, 1e300)

    def test_bytes_are_plain_utf8_with_unix_newlines(self):
        data = dumps(full_document())
        assert b"\r" not in data
        assert "Héllo".encode() in data
        assert data.endswith(b"}\n")
        assert json.loads(data)["format"] == FORMAT

    def test_one_object_to_a_line(self):
        d = Document()
        with d.transaction("x") as t:
            t.add(Line(Vec(0, 0), Vec(1, 1)))
            t.add(Line(Vec(2, 2), Vec(3, 3)))
        assert sum(1 for ln in dumps(d).decode().split("\n") if '"type":"line"' in ln) == 2


def base_file(**extra):
    obj = {"format": FORMAT, "version": VERSION, "entities": []}
    obj.update(extra)
    return obj


def load_obj(obj):
    return loads(json.dumps(obj).encode())


BAD_ENTITIES = [
    {"id": 1, "type": "line", "a": [0, 0]},
    {"id": 1, "type": "line", "a": [0, 0], "b": [1]},
    {"id": 1, "type": "line", "a": [0, 0], "b": ["x", 1]},
    {"id": 1, "type": "line", "a": [0, 0], "b": [True, 1]},
    {"id": 1, "type": "line", "a": [0, 0], "b": [1e999, 1]},
    {"id": 1, "type": "circle", "c": [0, 0], "r": -1},
    {"id": 1, "type": "circle", "c": [0, 0], "r": 0},
    {"id": 1, "type": "circle", "c": [0, 0]},
    {"id": 1, "type": "arc", "c": [0, 0], "r": 1, "start": 0, "sweep": 100},
    {"id": 1, "type": "ellipse", "c": [0, 0], "major": [0, 0], "ratio": 0.5},
    {"id": 1, "type": "ellipse", "c": [0, 0], "major": [1, 0], "ratio": 2},
    {"id": 1, "type": "polyline", "pts": []},
    {"id": 1, "type": "polyline", "pts": [[0, 0], [1, 1]], "bulges": [0.1]},
    {"id": 1, "type": "polyline", "pts": [[0, 0], [1, 1]], "widths": [[1]]},
    {"id": 1, "type": "spline", "degree": 3, "knots": [0], "ctrl": [[0, 0], [1, 1]]},
    {"id": 1, "type": "spline", "degree": 0, "knots": [0], "ctrl": [[0, 0], [1, 1]]},
    {"id": 1, "type": "solid", "pts": [[0, 0], [1, 1]]},
    {"id": 1, "type": "text", "p": [0, 0], "height": 1},
    {"id": 1, "type": "text", "p": [0, 0], "height": 1, "text": 7},
    {"id": 1, "type": "text", "p": [0, 0], "height": 1, "text": "a", "halign": 9},
    {"id": 1, "type": "mtext", "p": [0, 0], "height": 1, "text": "a", "attach": 12},
    {"id": 1, "type": "insert", "p": [0, 0]},
    {"id": 1, "type": "insert", "block": "B", "p": [0, 0], "attribs": "x"},
    {"id": 1, "type": "insert", "block": "B", "p": [0, 0], "attribs": [5]},
    {"id": 1, "type": "insert", "block": "B", "p": [0, 0], "columns": 0},
    {"id": 1, "type": "wormhole"},
    {"id": 1},
    {"id": "1", "type": "node", "p": [0, 0]},
    {"id": True, "type": "node", "p": [0, 0]},
    {"id": 0, "type": "node", "p": [0, 0]},
    {"id": -5, "type": "node", "p": [0, 0]},
    {"id": 1 << 60, "type": "node", "p": [0, 0]},
    {"type": "node", "p": [0, 0]},
    {"id": 1, "type": "node", "p": [0, 0], "color": 9999},
    {"id": 1, "type": "node", "p": [0, 0], "layer": 7},
    {"id": 1, "type": "node", "p": [0, 0], "weight": 9999},
    {"id": 1, "type": "node", "p": [0, 0], "order": "first"},
    "not an object",
    5,
    None,
]


class TestRefusals:
    @pytest.mark.parametrize("item", BAD_ENTITIES)
    def test_a_bad_object_is_refused_with_a_message(self, item):
        with pytest.raises(FormatError):
            load_obj(base_file(entities=[item]))

    def test_the_same_id_twice(self):
        n = {"id": 1, "type": "node", "p": [0, 0]}
        with pytest.raises(FormatError, match="twice"):
            load_obj(base_file(entities=[n, n]))

    def test_an_id_shared_with_a_block(self):
        n = {"id": 1, "type": "node", "p": [0, 0]}
        with pytest.raises(FormatError, match="twice"):
            load_obj(base_file(entities=[n], blocks=[{"name": "B", "base": [0, 0], "entities": [n]}]))

    @pytest.mark.parametrize("data", [
        b"", b"not json", b"[]", b"{}", b"\xff\xfe", b'{"format":"something else"}', b'{"format":"DraftWorx drawing"}',
        b'{"format":"DraftWorx drawing","version":"1"}', b'{"format":"DraftWorx drawing","version":0}',
        b'{"format":"DraftWorx drawing","version":true}', b"{" * 100_000, b"[" * 100_000,
        b'{"format":"DraftWorx drawing","version":1,"entities":{}}',
        b'{"format":"DraftWorx drawing","version":1,"layers":[{}]}',
        b'{"format":"DraftWorx drawing","version":1,"layers":[{"name":"A","color":"red"}]}',
        b'{"format":"DraftWorx drawing","version":1,"settings":7}',
        b'{"format":"DraftWorx drawing","version":1,"blocks":[{"name":"B"}]}',
        b'{"format":"DraftWorx drawing","version":1,"blocks":[{"name":"B","base":[0,0],"entities":3}]}',
        b'{"format":"DraftWorx drawing","version":1,"entities":[{"id":1,"type":"node","p":[NaN,0]}]}',
        b'{"format":"DraftWorx drawing","version":1,"entities":[{"id":1,"type":"node","p":[Infinity,0]}]}',
    ])
    def test_bad_files_are_refused_with_a_message(self, data):
        with pytest.raises(FormatError) as e:
            loads(data)
        assert str(e.value).endswith(".")

    def test_a_newer_file_says_so_plainly(self):
        with pytest.raises(FormatError, match="newer DraftWorx"):
            load_obj(base_file(version=VERSION + 1))

    def test_a_truncated_file_is_refused(self):
        data = dumps(full_document())
        for cut in (1, 10, len(data) // 2, len(data) - 3):
            with pytest.raises(FormatError):
                loads(data[:cut])

    def test_block_cycles_in_a_file_are_refused(self):
        a = {"name": "A", "base": [0, 0], "entities": [{"id": 1, "type": "insert", "block": "B", "p": [0, 0]}]}
        b = {"name": "B", "base": [0, 0], "entities": [{"id": 2, "type": "insert", "block": "a", "p": [0, 0]}]}
        with pytest.raises(FormatError, match="contains itself"):
            load_obj(base_file(blocks=[a, b]))

    @given(st.binary(max_size=200))
    def test_garbage_only_ever_raises_format_error(self, data):
        with pytest.raises(FormatError):
            loads(data)

    @given(st.recursive(st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False, allow_infinity=False) | st.text(max_size=5),
                        lambda c: st.lists(c, max_size=3) | st.dictionaries(st.sampled_from(["id", "type", "p", "a", "b", "c", "r", "pts", "name", "text", "height", "block", "base", "entities", "start", "sweep"]), c, max_size=5),
                        max_leaves=20))
    def test_any_json_in_a_section_only_ever_raises_format_error(self, value):
        for key in ("entities", "layers", "blocks", "linetypes", "text_styles", "dim_styles", "settings", "current"):
            try:
                load_obj(base_file(**{key: value}))
            except FormatError:
                pass

    def test_unknown_current_layer_falls_back(self):
        d = load_obj(base_file(current={"layer": "Gone"}))
        assert d.current.layer == "0"

    def test_missing_tables_are_filled_in(self):
        d = load_obj(base_file())
        assert "0" in d.layers and "Standard" in d.text_styles and "Continuous" in d.linetypes

    def test_layer_states_are_active_after_loading(self):
        d = load_obj(base_file(layers=[{"name": "A", "on": False, "locked": True}]))
        assert d.is_hidden(Style("A")) and d.is_locked(Style("A"))
