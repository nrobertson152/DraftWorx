# SPDX-License-Identifier: GPL-3.0-or-later
"""Blocks with attributes: making one, placing one and filling in its fields, changing them, bringing in a drawing as one, and the parts
list they make."""
import io
import json
import math
from decimal import Decimal

import pytest

from draftworx.cli import main as cli
from draftworx.core import blocks
from draftworx.core.curves import Circle
from draftworx.core.dxf.read import read_dxf
from draftworx.core.dxf.write import encode_dxf
from draftworx.core.info import rows_for
from draftworx.core.model import BlockDef, Document, Entity, Layer, Settings, Style
from draftworx.core.native import dumps, loads
from draftworx.core.parts import bom_csv, parts_list
from draftworx.core.shapes import AttDef, Attrib, Dimension, Hatch, Insert, Text
from draftworx.core.vec import Vec
from driver import Person, line


def V(x, y):
    return Vec(float(x), float(y))


def inserts(doc):
    return [e for e in sorted(doc.entities.values(), key=lambda e: e.id) if type(e.shape) is Insert]


def field(tag, value, at=(0, -10), prompt="", constant=False):
    return AttDef(Attrib(tag, Text(V(*at), 2.5, value), prompt, False, constant))


def valve_drawing():
    """A valve symbol with a part number and a tag, made into a block called Valve."""
    p = Person(Circle(V(0, 0), 5.0), line(-5, 0, 5, 0), field("PART_NO", "V-100", (0, -10), "Part number?"), field("TAG", "Y?", (0, 10)))
    p.s.set_selection(p.ids)
    p.run("BLOCK").click(0, 0).type("Valve").enter()
    return p


class TestMakingOne:
    def test_what_was_selected_becomes_a_block_placed_where_it_was(self):
        p = valve_drawing()
        (ins,) = inserts(p.doc)
        assert len(p.doc.entities) == 1 and ins.shape.block == "Valve" and ins.shape.p == V(0, 0)
        d = p.doc.blocks.get("Valve")
        assert d.base == V(0, 0) and len(d.entities) == 4
        # nothing moved: the objects inside are the ones that were drawn, at their own coordinates
        assert Circle(V(0, 0), 5.0) in [e.shape for e in d.entities]
        assert [(a.tag, a.text.text) for a in ins.shape.attribs] == [("PART_NO", "V-100"), ("TAG", "Y?")]

    def test_it_is_one_undo_step(self):
        p = Person(Circle(V(0, 0), 5.0), line(-5, 0, 5, 0))
        before = p.doc.fingerprint()
        p.s.set_selection(p.ids)
        p.run("BLOCK").click(0, 0).enter()
        assert p.doc.blocks.get("Block1") is not None
        p.doc.undo()
        assert p.doc.fingerprint() == before

    def test_a_name_that_is_taken_can_replace_it_everywhere(self):
        p = valve_drawing()
        p.run("INSERT").enter().click(50, 0).enter().enter()
        with p.doc.transaction("x") as t:
            new_ids = [t.add(Circle(V(200, 0), 8.0))]
        p.s.set_selection(new_ids)
        p.run("BLOCK").click(200, 0).type("Valve").enter()
        assert "already a block" in p.s.prompt and "2 times" in p.s.prompt
        p.type("R").enter()
        assert [e.shape for e in p.doc.blocks.get("Valve").entities] == [Circle(V(200, 0), 8.0)]
        assert len(inserts(p.doc)) == 3       # both old copies now show the new contents, and the new one is placed

    def test_names_a_file_would_refuse_are_refused(self):
        assert blocks.problem_with_name("a/b")
        assert blocks.problem_with_name("*U2")
        assert blocks.problem_with_name("  ")
        assert blocks.problem_with_name("Valve 2") is None

    def test_what_came_from_a_file_and_cant_be_changed_stays_out(self):
        import ezdxf
        src = ezdxf.new("R2013")
        m = src.modelspace()
        m.add_line((0, 0), (10, 0))
        m.add_ray((0, 0), (1, 0))
        stream = io.StringIO()
        src.write(stream)
        doc, _ = read_dxf(stream.getvalue().encode())
        p = Person()
        p.s.replace_document(doc)
        p.doc = doc
        p.s.set_selection(list(doc.entities))
        p.run("BLOCK").click(0, 0).type("Part").enter()
        assert "stayed out" in p.s.message
        assert len(doc.blocks.get("Part").entities) == 1

    def test_dimensions_and_hatches_inside_let_go_of_what_they_were_attached_to(self):
        p = Person(*[line(0, 0, 100, 0), line(100, 0, 100, 50), line(100, 50, 0, 50), line(0, 50, 0, 0)])
        p.run("DIM").click(30, 0).click(30, -15).enter()
        p.run("HATCH").click(50, 25).enter()
        p.s.set_selection(list(p.doc.entities))
        p.run("BLOCK").click(0, 0).type("Room").enter()
        inside = [e.shape for e in p.doc.blocks.get("Room").entities]
        assert all(not s.refs for s in inside if type(s) in (Dimension, Hatch))


class TestPlacingOne:
    def test_it_asks_for_each_field_with_its_question(self):
        p = valve_drawing()
        p.run("INSERT").enter().click(50, 20)
        assert "Part number?" in p.s.prompt and "V-100" in p.s.prompt
        p.type("V-200").enter()
        assert "TAG?" in p.s.prompt
        p.type("Y1").enter()
        ins = inserts(p.doc)[-1].shape
        assert ins.p == V(50, 20)
        assert [(a.tag, a.text.text, a.text.p) for a in ins.attribs] == [("PART_NO", "V-200", V(50, 10)), ("TAG", "Y1", V(50, 30))]

    def test_sized_and_turned_and_its_fields_go_with_it(self):
        p = valve_drawing()
        p.run("INSERT").type("val").enter().type("S").enter().type("2").enter().type("R").enter().type("90").enter().click(100, 0).enter().enter()
        ins = inserts(p.doc)[-1].shape
        assert (ins.sx, ins.sy) == (2.0, 2.0) and ins.rotation == pytest.approx(math.pi / 2)
        assert ins.attribs[0].text.p == pytest.approx(V(120, 0))
        assert ins.attribs[0].text.rotation == pytest.approx(math.pi / 2)
        assert ins.attribs[0].text.height == pytest.approx(5.0)

    def test_a_drawing_without_blocks_says_how_to_get_one(self):
        with Person() as p:
            p.run("INSERT")
            assert "no blocks" in p.s.message and not p.s.active

    def test_a_name_that_isnt_one_is_asked_again(self):
        p = valve_drawing()
        p.run("INSERT").type("pump").enter()
        assert "no block called pump" in p.s.message and p.s.active
        p.key("escape")

    def test_a_constant_field_is_the_blocks_own(self):
        p = Person(Circle(V(0, 0), 5.0), field("MFR", "Parker", constant=True))
        p.s.set_selection(p.ids)
        p.run("BLOCK").click(0, 0).type("Pump").enter()
        p.run("INSERT").enter().click(40, 0)
        assert not p.s.active
        assert inserts(p.doc)[-1].shape.attribs == ()


class TestChangingFields:
    def test_in_the_panel(self):
        p = valve_drawing()
        (ins,) = inserts(p.doc)
        rows = {r.label: r for r in rows_for(ins.shape, Settings())}
        assert rows["PART_NO"].value == "V-100"
        with p.doc.transaction("Edit") as t:
            t.replace(ins.id, rows["PART_NO"].edit("V-999"))
        assert inserts(p.doc)[0].shape.attribs[0].text.text == "V-999"

    def test_by_pointing(self):
        p = valve_drawing()
        p.run("ATTEDIT").click(5, 0)
        assert "PART_NO" in p.s.prompt
        p.type("V-500").enter().enter().enter()
        assert [a.text.text for a in inserts(p.doc)[0].shape.attribs] == ["V-500", "Y?"]

    def test_defining_a_field(self):
        with Person() as p:
            p.run("ATTDEF").type("part no").enter()
            assert "one word" in p.s.message
            p.type("part_no").enter().enter().type("X-1").enter().click(30, 30)
            (d,) = [e.shape for e in p.doc.entities.values()]
            assert type(d) is AttDef and d.attrib.tag == "PART_NO" and d.attrib.text.text == "X-1" and d.attrib.prompt == "Part no?"
            assert d.shown().text == "PART_NO"      # on its own it shows its tag


class TestFiles:
    def test_saved_and_opened(self):
        p = valve_drawing()
        back = loads(dumps(p.doc))
        assert back.fingerprint() == p.doc.fingerprint()

    @pytest.mark.parametrize("version", ["AC1009", "AC1015", "AC1027"])
    def test_dxf_keeps_the_fields_and_ezdxf_reads_them(self, version):
        from ezdxf import recover
        p = valve_drawing()
        p.run("INSERT").enter().click(50, 0).type("V-200").enter().type("Y1").enter()
        data, _ = encode_dxf(p.doc, version)
        doc, auditor = recover.read(io.BytesIO(data))
        assert not auditor.errors
        got = sorted(tuple((a.dxf.tag, a.dxf.text) for a in i.attribs) for i in doc.modelspace().query("INSERT"))
        assert got == [(("PART_NO", "V-100"), ("TAG", "Y?")), (("PART_NO", "V-200"), ("TAG", "Y1"))]
        back, _ = read_dxf(data)
        assert sorted(tuple(a.text.text for a in e.shape.attribs) for e in inserts(back)) == [("V-100", "Y?"), ("V-200", "Y1")]


class TestBringingInADrawing:
    def other(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_layer(Layer("Frame", 3))
            t.put_block(BlockDef("Bolt", V(0, 0), (Entity(500, line(-1, 0, 1, 0)),)))
            t.put_block(BlockDef("*U1", V(0, 0), (Entity(501, Circle(V(0, 0), 1.0)),)))
            t.add(line(0, 0, 100, 0), Style("Frame"))
            t.add(Insert("Bolt", V(10, 10)))
            t.add(Insert("*U1", V(20, 20)))
        return d

    def test_it_comes_with_what_it_uses_and_this_drawings_own_win(self):
        target = Document()
        with target.transaction("x") as t:
            t.put_block(BlockDef("Bolt", V(0, 0), (Entity(1, Circle(V(0, 0), 3.0)),)))
            t.put_block(BlockDef("*U1", V(0, 0), (Entity(2, line(0, 0, 1, 1)),)))
        brought = blocks.bring_in(target, self.other(), "Plate")
        with target.transaction("Bring in") as t:
            blocks.add(t, brought)
        assert brought.kept == ("Bolt",)
        assert target.layers.get("Frame") is not None and target.layers.get("Frame").color == 3
        plate = target.blocks.get("Plate")
        names = sorted(s.block for s in (e.shape for e in plate.entities) if type(s) is Insert)
        assert names == ["*U1_1", "Bolt"]
        assert [e.shape for e in target.blocks.get("*U1").entities] == [line(0, 0, 1, 1)]     # this drawing's own, untouched
        assert [e.shape for e in target.blocks.get("*U1_1").entities] == [Circle(V(0, 0), 1.0)]
        ids = [e.id for b in target.blocks for e in b.entities]
        assert len(ids) == len(set(ids))
        assert loads(dumps(target)).fingerprint() == target.fingerprint()

    def test_placed_like_any_block(self):
        p = Person()
        brought = blocks.bring_in(p.doc, self.other(), "Plate")
        with p.doc.transaction("Bring in") as t:
            blocks.add(t, brought)
        p.run("INSERT").type("Plate").enter().click(1000, 1000)
        assert inserts(p.doc)[0].shape == Insert("Plate", V(1000, 1000))


class TestPartsList:
    def drawing(self):
        p = valve_drawing()
        p.run("INSERT").enter().click(50, 0).type("V-100").enter().type("Y2").enter()
        p.run("INSERT").enter().click(100, 0).type("V-200").enter().type("Y3").enter()
        return p

    def test_placed_blocks_with_part_numbers_counted(self):
        p = self.drawing()
        parts, problems = parts_list(p.doc)
        assert not problems
        assert [(x.number, x.quantity, x.references) for x in parts] == [("V-100", Decimal(2), ("Y?", "Y2")), ("V-200", Decimal(1), ("Y3",))]

    def test_quantities_and_arrays(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_block(BlockDef("Bolt", V(0, 0), (Entity(1, line(0, 0, 1, 0)),)))
            qty = Attrib("QTY", Text(V(0, 0), 1, "2.5"))
            pn = Attrib("PN", Text(V(0, 0), 1, "B-1"))
            t.add(Insert("Bolt", V(0, 0), attribs=(pn, qty)))
            t.add(Insert("Bolt", V(0, 0), columns=3, rows=2, column_spacing=10, row_spacing=10, attribs=(pn,)))
            t.add(Insert("Bolt", V(0, 0), attribs=(pn, Attrib("QTY", Text(V(0, 0), 1, "lots")))))
        parts, problems = parts_list(d)
        assert parts[0].quantity == Decimal("8.5")
        assert len(problems) == 1 and "lots" in problems[0].what

    def test_written_as_revisionworxs_bom_and_read_by_it(self):
        bom = pytest.importorskip("revisionworx.bom")
        p = self.drawing()
        parts, _ = parts_list(p.doc)
        data = bom_csv(parts, "VALVE-PANEL", "Valve panel", "B")
        structure = bom.structure_from_csv(data)
        root = next(x.part_id for x in structure.parts if x.number == "VALVE-PANEL")
        lines = sorted((structure.info(ln.child_id).number, ln.quantity, sorted(ln.reference_designators)) for ln in structure.children(root))
        assert lines == [("V-100", Decimal(2), ["Y2", "Y?"]), ("V-200", Decimal(1), ["Y3"])]
        assert structure.info(root).revision == "B" and structure.top_level_ids == (root,)

    def test_a_spreadsheet_formula_is_kept_as_text(self):
        bom = pytest.importorskip("revisionworx.bom")
        d = Document()
        with d.transaction("x") as t:
            t.put_block(BlockDef("X", V(0, 0), (Entity(1, line(0, 0, 1, 0)),)))
            t.add(Insert("X", V(0, 0), attribs=(Attrib("PN", Text(V(0, 0), 1, "=1+1")),)))
        parts, _ = parts_list(d)
        data = bom_csv(parts, "TOP")
        assert b"'=1+1" in data
        structure = bom.structure_from_csv(data)
        assert "=1+1" in {x.number for x in structure.parts}


class TestCommandLine:
    def run(self, capsys, *args):
        code = cli(list(args))
        out = capsys.readouterr().out
        assert out.count("\n") == 1
        return code, json.loads(out)

    def test_version(self, capsys):
        code, out = self.run(capsys, "version")
        assert code == 0 and out["ok"] and out["api"] == 1

    def test_info_and_parts(self, capsys, tmp_path):
        p = TestPartsList().drawing()
        path = tmp_path / "panel.draftworx"
        path.write_bytes(dumps(p.doc))
        code, out = self.run(capsys, "info", str(path))
        assert code == 0 and out["kinds"] == {"Block": 3} and out["blocks"] == [{"name": "Valve", "placed": 3}]
        code, out = self.run(capsys, "parts", str(path), "--csv", str(tmp_path / "bom.csv"), "--number", "PANEL-1")
        assert code == 0 and [x["number"] for x in out["parts"]] == ["V-100", "V-200"] and out["parts"][0]["quantity"] == "2"
        assert (tmp_path / "bom.csv").read_bytes().startswith(b"parent_number,")

    def test_convert(self, capsys, tmp_path):
        p = valve_drawing()
        src = tmp_path / "a.draftworx"
        src.write_bytes(dumps(p.doc))
        code, out = self.run(capsys, "convert", str(src), str(tmp_path / "a.dxf"), "--dxf-version", "R12")
        assert code == 0 and out["ok"] and any("simpler" in n for n in out["notes"]) is False
        assert read_dxf((tmp_path / "a.dxf").read_bytes())[0].extras["dxf"]["version"] == "AC1009"
        code, out = self.run(capsys, "convert", str(src), str(tmp_path / "a.dxf"))
        assert code == 2 and out["kind"] == "exists"

    def test_a_refusal_is_one_answer_too(self, capsys, tmp_path):
        code, out = self.run(capsys, "info", str(tmp_path / "missing.dxf"))
        assert code == 2 and not out["ok"] and out["kind"] == "not-readable"
        code, out = self.run(capsys, "parts")
        assert code == 2 and out["kind"] == "bad-input"
