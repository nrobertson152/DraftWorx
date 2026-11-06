# SPDX-License-Identifier: GPL-3.0-or-later
"""DXF, checked against ezdxf, an independent program, and against real files written by other programs."""
import difflib
import io
import math
import random
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

ezdxf = pytest.importorskip("ezdxf")

from ezdxf import recover  # noqa: E402
from ezdxf.math import Vec3  # noqa: E402

from draftworx.core.curves import Arc, Circle, EllipseArc, Line, Polyline, Spline, spline_through  # noqa: E402
from draftworx.core.dxf import DEFAULT, VERSIONS, check_written, encode_dxf, read_dxf, write_dxf  # noqa: E402
from draftworx.core.dxf.read import read_strict  # noqa: E402
from draftworx.core.dxf.tags import decode_text, encode_text, fmt_float, group_type, retag  # noqa: E402
from draftworx.core.files import Opened, export_dxf, open_drawing, save_drawing  # noqa: E402
from draftworx.core.layers import rename_layer  # noqa: E402
from draftworx.core.model import BlockDef, Document, Entity, Layer, Linetype, Style  # noqa: E402
from draftworx.core.native import FormatError, dumps, loads  # noqa: E402
from draftworx.core.safefile import SaveError  # noqa: E402
from draftworx.core.select import curves_of, pick  # noqa: E402
from draftworx.core.shapes import AttDef, Attrib, Dimension, Foreign, Insert, MText, Node, Solid, Text  # noqa: E402
from draftworx.core.snap import Kind, SnapEngine, SnapQuery  # noqa: E402
from draftworx.core.vec import TAU, Affine, Vec  # noqa: E402
from driver import Person, line  # noqa: E402
from geom_strategies import coord  # noqa: E402

DATA = Path(__file__).parent / "data"
TEXT_SAMPLES = sorted(p for p in DATA.glob("*.dxf") if not p.read_bytes().startswith(b"AutoCAD Binary"))
BINARY_SAMPLES = sorted(p for p in DATA.glob("*.dxf") if p.read_bytes().startswith(b"AutoCAD Binary"))
EZDXF_VERSIONS = ["R12", "R2000", "R2004", "R2007", "R2010", "R2013", "R2018"]


def encoded(doc) -> bytes:
    ezdxf.options.write_fixed_meta_data_for_testing = True      # so the same drawing is the same bytes, and a failure can be repeated
    s = io.StringIO()
    doc.write(s)
    return s.getvalue().encode("utf-8" if doc.dxfversion >= "AC1021" else "cp1252", "surrogateescape")


def parsed(data: bytes):
    """What ezdxf makes of it, and what its audit says. It raises if the file's structure is broken."""
    doc, auditor = recover.read(io.BytesIO(data))
    return doc, auditor


def assert_clean(data: bytes):
    doc, auditor = parsed(data)
    assert not auditor.errors, [str(e.message) for e in auditor.errors]
    return doc


def everything(version: str):
    """A drawing with one of nearly everything, made by ezdxf."""
    doc = ezdxf.new(version, setup=True)
    doc.layers.add("Walls", color=3, linetype="DASHED")
    doc.layers.add("Off", color=4).off()
    doc.layers.add("Frozen", color=5).freeze()
    doc.layers.add("Locked", color=6).lock()
    m = doc.modelspace()
    m.add_line((1, 2), (11, 7), dxfattribs={"layer": "Walls", "color": 1})
    m.add_line((1, 2, 5), (3, 4, 9))
    m.add_circle((5, 5), 3, dxfattribs={"layer": "Off"})
    m.add_arc((1, 1), 2, 30, 120, dxfattribs={"layer": "Frozen"})
    m.add_text("Hello é ü", height=2.5, dxfattribs={"layer": "Locked", "rotation": 30}).set_placement((1, 2))
    m.add_text("Centred", height=1.5).set_placement((10, 10), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    m.add_point((9, 9))
    m.add_solid([(0, 0), (1, 0), (0, 1), (1, 1)])
    m.add_solid([(10, 0), (11, 0), (10, 1)])
    b = doc.blocks.new("BLK", base_point=(1, 1))
    b.add_line((0, 0), (1, 1))
    b.add_circle((1, 1), 0.5, dxfattribs={"layer": "Walls"})
    b.add_attdef("TAG", (0, 0), dxfattribs={"height": 0.5, "prompt": "What?"})
    n = doc.blocks.new("NESTED")
    n.add_blockref("BLK", (2, 2), dxfattribs={"xscale": 2, "yscale": 3, "rotation": 45})
    m.add_blockref("BLK", (10, 10), dxfattribs={"rotation": 30}).add_attrib("TAG", "value", (10, 10))
    m.add_blockref("NESTED", (20, 20))
    if version == "R12":
        m.add_polyline2d([(0, 0), (5, 5), (9, 1)], close=True)
    else:
        m.add_lwpolyline([(0, 0, 0, 0, 0.5), (5, 0), (5, 5, 1, 2)], close=True)
        m.add_lwpolyline([(0, 20), (5, 20)], dxfattribs={"const_width": 0.5})
        m.add_ellipse((30, 0), major_axis=(4, 1), ratio=0.5, start_param=0.3, end_param=4)
        m.add_ellipse((40, 0), major_axis=(0, 4), ratio=0.25)
        m.add_spline(fit_points=[(0, 0), (1, 1), (3, 0), (5, 5)])
        m.add_open_spline([(0, 30), (1, 31), (3, 30), (5, 35)], degree=3)
        m.add_rational_spline([(0, 40), (1, 41), (3, 40), (5, 45)], weights=[1, 2, 3, 1], degree=3)
        m.add_mtext("Line one\\Pline two", dxfattribs={"char_height": 1.5, "insert": (20, 30), "attachment_point": 5, "width": 30})
        m.add_mtext("x" * 700, dxfattribs={"char_height": 1, "insert": (20, 40)})
        h = m.add_hatch(color=2, dxfattribs={"layer": "Walls"})
        h.paths.add_polyline_path([(0, 0, 0.5), (5, 0), (5, 5)], is_closed=True)
        h.paths.add_edge_path().add_line((10, 10), (20, 10))
        dim = m.add_linear_dim(base=(0, 10), p1=(0, 0), p2=(10, 0))
        dim.render()
        m.add_leader([(0, 0), (5, 5), (10, 5)])
        m.add_3dface([(0, 0, 0), (1, 0, 1), (1, 1, 1), (0, 1, 0)])
        m.add_polyline3d([(0, 0, 0), (1, 1, 1), (2, 0, 2)])
        m.add_xline((0, 0), (1, 1))
        m.add_ray((0, 0), (1, 0))
        doc.layouts.get("Layout1").add_line((0, 0), (10, 10))
        line_with_data = m.add_line((50, 50), (60, 60), dxfattribs={"thickness": 2.5})
        line_with_data.set_xdata("MYAPP", [(1000, "keep me"), (1040, 3.5)])
        doc.appids.add("MYAPP")
        doc.layers.add("Rgb").rgb = (10, 20, 30)
    return doc


def of_type(doc, kind):
    return [e for e in doc.sorted_entities() if type(e.shape) is kind]


def changed_lines(before: bytes, after: bytes) -> list[str]:
    a, b = before.decode("latin-1").split("\n"), after.decode("latin-1").split("\n")
    return [x for x in difflib.ndiff(a, b) if x[0] in "+-"]


# ------------------------------------------------------------------------------------------------- nobody changed anything


class TestAnUnchangedFileComesBackTheSame:
    @pytest.mark.parametrize("version", EZDXF_VERSIONS)
    def test_every_version_byte_for_byte(self, version):
        data = encoded(everything(version))
        doc, _ = read_strict(data)
        assert write_dxf(doc) == data

    @pytest.mark.parametrize("path", TEXT_SAMPLES, ids=lambda p: p.name)
    def test_real_files_byte_for_byte(self, path):
        data = path.read_bytes()
        doc, _ = read_strict(data)
        assert write_dxf(doc) == data

    def test_windows_line_ends(self):
        data = encoded(everything("R2013")).replace(b"\n", b"\r\n")
        assert write_dxf(read_strict(data)[0]) == data

    def test_a_byte_order_mark(self):
        data = b"\xef\xbb\xbf" + encoded(everything("R2013"))
        assert write_dxf(read_strict(data)[0]) == data

    def test_no_newline_at_the_end(self):
        data = encoded(everything("R12")).rstrip(b"\n")
        assert write_dxf(read_strict(data)[0]) == data

    def test_comments_and_odd_spacing(self):
        data = b"999\nmade by hand\n0\nSECTION\n2\nENTITIES\n999\na comment\n0\nLINE\n8\n0\n10\n1.5000\n20\n2\n11\n3\n21\n4\n0\nENDSEC\n999\nthe end\n0\nEOF\n"
        doc, _ = read_strict(data)
        assert of_type(doc, Line)[0].shape == line(1.5, 2, 3, 4)
        assert write_dxf(doc) == data

    def test_junk_after_the_end(self):
        data = encoded(everything("R12")) + b"\x1a\nleft over\n"
        assert write_dxf(read_strict(data)[0]) == data

    def test_through_a_native_file_too(self):
        data = encoded(everything("R2018"))
        doc, _ = read_strict(data)
        again = loads(dumps(doc))
        assert write_dxf(again) == data

    def test_bytes_that_dont_fit_the_encoding(self):
        data = encoded(everything("R2018")).replace(b"Hello", b"H\xe9\xffllo")
        doc, _ = read_strict(data)
        assert write_dxf(doc) == data
        assert write_dxf(loads(dumps(doc))) == data
        text = next(e.shape.text for e in of_type(doc, Text) if e.shape.text.startswith("H"))
        assert text.encode("utf-8")     # fit to show, whatever the file held

    def test_something_nobody_has_heard_of(self):
        data = encoded(everything("R2013"))
        extra = b"  0\nWEIRDTHING\n  5\nFFFF0\n  8\nWalls\n 10\n1.0\n 20\n2.0\n999\nsays what\n"
        data = data.replace(b"  0\nENDSEC\n  0\nSECTION\n  2\nOBJECTS", extra + b"  0\nENDSEC\n  0\nSECTION\n  2\nOBJECTS")
        doc, notes = read_strict(data)
        assert any(e.shape.dxf_type == "WEIRDTHING" for e in of_type(doc, Foreign))
        assert any("weirdthing" in n.lower() for n in notes)
        assert write_dxf(doc) == data

    def test_whole_sections_nobody_has_heard_of(self):
        data = encoded(everything("R2013")).replace(b"  0\nEOF\n", b"  0\nSECTION\n  2\nTHUMBNAILIMAGE\n 90\n4\n310\nABCD\n  0\nENDSEC\n  0\nEOF\n")
        doc, _ = read_strict(data)
        assert write_dxf(doc) == data

    def test_paper_space_is_kept_in_its_place_and_out_of_the_way(self):
        doc = everything("R2013")
        doc.layouts.get("Layout1").add_circle((1, 1), 1)
        data = encoded(doc)
        mine, notes = read_strict(data)
        assert write_dxf(mine) == data
        assert any("paper space" in n for n in notes)
        assert all(e.shape != line(0, 0, 10, 10) for e in mine.entities.values())

    def test_objects_mixed_into_paper_space_keep_their_order(self):
        body = b"  0\nLINE\n  8\n0\n 10\n0\n 20\n0\n 11\n1\n 21\n1\n  0\nLINE\n 67\n1\n  8\n0\n 10\n5\n 20\n5\n 11\n6\n 21\n6\n  0\nCIRCLE\n  8\n0\n 10\n0\n 20\n0\n 40\n1\n"
        data = b"  0\nSECTION\n  2\nENTITIES\n" + body + b"  0\nENDSEC\n  0\nEOF\n"
        doc, _ = read_strict(data)
        assert len(doc.entities) == 2
        assert write_dxf(doc) == data

    def test_records_outside_any_table_and_a_second_table(self):
        table = b"  0\nTABLE\n  2\nLAYER\n 70\n1\n  0\nLAYER\n  2\nA\n 70\n0\n 62\n1\n  6\nCONTINUOUS\n  0\nENDTAB\n"
        stray = b"  0\nLAYER\n  2\nB\n 70\n0\n 62\n2\n  6\nCONTINUOUS\n"
        data = b"  0\nSECTION\n  2\nTABLES\n" + table + stray + table + b"  0\nENDSEC\n  0\nEOF\n"
        doc, _ = read_strict(data)
        assert "A" in doc.layers
        assert write_dxf(doc) == data

    def test_a_repeated_layer_entry_stays(self):
        entry = b"  0\nLAYER\n  2\nA\n 70\n0\n 62\n%d\n  6\nCONTINUOUS\n"
        data = (b"  0\nSECTION\n  2\nTABLES\n  0\nTABLE\n  2\nLAYER\n 70\n2\n" + entry % 1 + entry % 2 + b"  0\nENDTAB\n  0\nENDSEC\n  0\nEOF\n")
        doc, _ = read_strict(data)
        assert doc.layers["A"].color == 1
        assert write_dxf(doc) == data


# ------------------------------------------------------------------------------------------------- someone changed something


def a_line_with_a_handle(doc):
    return next(e for e in of_type(doc, Line) if e.style.layer == "Walls")


class TestOneChangeChangesOneThing:
    def test_moving_a_line_changes_its_numbers_and_the_extents(self):
        data = encoded(everything("R2013"))
        doc, _ = read_strict(data)
        e = a_line_with_a_handle(doc)
        with doc.transaction("x") as t:
            t.replace(e.id, line(1, 2, 12, 7))
        back = write_dxf(doc)
        changes = changed_lines(data, back)
        assert "- 11.0" in changes and "+ 12.0" in changes
        # the object's own lines and nothing else of the file, apart from the tags it now writes differently
        assert len(changes) < 40
        theirs = assert_clean(back)
        moved = [x for x in theirs.modelspace().query("LINE") if x.dxf.layer == "Walls"][0]
        assert tuple(moved.dxf.end)[:2] == (12.0, 7.0)

    def test_undo_takes_it_back_to_the_same_bytes(self):
        data = encoded(everything("R2013"))
        doc, _ = read_strict(data)
        e = a_line_with_a_handle(doc)
        with doc.transaction("x") as t:
            t.replace(e.id, line(1, 2, 12, 7))
        assert write_dxf(doc) != data
        doc.undo()
        assert write_dxf(doc) == data

    @pytest.mark.parametrize("version", EZDXF_VERSIONS)
    def test_adding_something_keeps_every_line_there_was(self, version):
        data = encoded(everything(version))
        doc, _ = read_strict(data)
        with doc.transaction("x") as t:
            t.add(Circle(Vec(100.0, 100.0), 7.0), Style("Walls", 2))
        back = write_dxf(doc)
        gone = [x for x in changed_lines(data, back) if x[0] == "-"]
        # only what the header has to say differently
        assert len(gone) <= 10, gone
        theirs = assert_clean(back)
        circles = [c for c in theirs.modelspace().query("CIRCLE") if c.dxf.radius == 7.0]
        assert len(circles) == 1 and circles[0].dxf.color == 2 and circles[0].dxf.layer == "Walls"

    def test_new_handles_match_nothing_and_the_seed_moves_past_them(self):
        data = encoded(everything("R2013"))
        doc, _ = read_strict(data)
        with doc.transaction("x") as t:
            for i in range(5):
                t.add(line(i, 0, i, 1))
        theirs = assert_clean(write_dxf(doc))
        handles = [e.dxf.handle for e in theirs.modelspace()]
        assert len(handles) == len(set(handles))
        seed = int(theirs.header["$HANDSEED"], 16)
        assert all(int(h, 16) < seed for h in handles)

    def test_a_seed_that_was_wrong_cant_cause_a_clash(self):
        data = encoded(everything("R2013"))
        low = data.split(b"$HANDSEED\n  5\n")[1].split(b"\n")[0]
        data = data.replace(b"$HANDSEED\n  5\n" + low, b"$HANDSEED\n  5\n10")
        doc, _ = read_strict(data)
        with doc.transaction("x") as t:
            t.add(line(0, 0, 1, 1))
        theirs, _ = parsed(write_dxf(doc))
        handles = [e.dxf.handle for e in theirs.modelspace()] + [e.dxf.handle for e in theirs.objects]
        assert len(handles) == len(set(handles))

    def test_erasing_something_takes_only_it(self):
        data = encoded(everything("R2013"))
        doc, _ = read_strict(data)
        e = next(e for e in of_type(doc, Solid))
        with doc.transaction("x") as t:
            t.remove(e.id)
        back = write_dxf(doc)
        added = [x for x in changed_lines(data, back) if x[0] == "+"]
        assert len(added) <= 6     # the extents, if that was what was furthest out
        theirs = assert_clean(back)
        assert len(theirs.modelspace().query("SOLID")) == 1

    def test_a_changed_object_keeps_its_handle_and_its_extended_data(self):
        data = encoded(everything("R2013"))
        doc, _ = read_strict(data)
        before, _ = parsed(data)
        was = next(x for x in before.modelspace().query("LINE") if x.has_xdata("MYAPP"))
        e = next(e for e in of_type(doc, Line) if e.shape == line(50, 50, 60, 60))
        with doc.transaction("x") as t:
            t.replace(e.id, line(50, 50, 70, 70))
        theirs = assert_clean(write_dxf(doc))
        now = theirs.entitydb[was.dxf.handle]
        assert tuple(now.dxf.end)[:2] == (70.0, 70.0)
        assert [tuple(t) for t in now.get_xdata("MYAPP")][-2:] == [(1000, "keep me"), (1040, 3.5)]

    def test_only_a_change_of_look_keeps_everything_else_about_it(self):
        data = encoded(everything("R2013"))
        doc, _ = read_strict(data)
        e = next(e for e in of_type(doc, Line) if e.shape == line(50, 50, 60, 60))
        with doc.transaction("x") as t:
            t.replace(e.id, style=Style("Walls", 3, "DASHED", 50, 2.0))
        back = write_dxf(doc)
        assert len(changed_lines(data, back)) <= 12
        theirs = assert_clean(back)
        now = next(x for x in theirs.modelspace().query("LINE") if x.has_xdata("MYAPP"))
        assert now.dxf.thickness == 2.5
        assert (now.dxf.layer, now.dxf.color, now.dxf.linetype, now.dxf.lineweight, now.dxf.ltscale) == ("Walls", 3, "DASHED", 50, 2.0)
        assert now.get_xdata("MYAPP")

    def test_back_to_bylayer_removes_the_tags(self):
        data = encoded(everything("R2013"))
        doc, _ = read_strict(data)
        e = a_line_with_a_handle(doc)
        with doc.transaction("x") as t:
            t.replace(e.id, style=replace(e.style, color=256))
        theirs = assert_clean(write_dxf(doc))
        assert [x for x in theirs.modelspace().query("LINE") if x.dxf.layer == "Walls"][0].dxf.color == 256

    def test_a_true_colour_replaces_the_old_one(self):
        doc, _ = read_strict(encoded(everything("R2013")))
        e = a_line_with_a_handle(doc)
        with doc.transaction("x") as t:
            t.replace(e.id, style=replace(e.style, color=0x1000000 | 0x102030))
        theirs = assert_clean(write_dxf(doc))
        assert [x for x in theirs.modelspace().query("LINE") if x.dxf.layer == "Walls"][0].rgb == (0x10, 0x20, 0x30)

    def test_changing_a_layer_keeps_what_we_dont_model(self):
        doc_in = everything("R2013")
        data = encoded(doc_in)
        doc, _ = read_strict(data)
        layer = doc.layers["Walls"]
        with doc.transaction("x") as t:
            t.put_layer(replace(layer, color=1, locked=True))
        back = write_dxf(doc)
        assert 3 <= len(changed_lines(data, back)) <= 6
        theirs = assert_clean(back)
        now = theirs.layers.get("Walls")
        assert now.color == 1 and now.is_locked() and now.dxf.linetype == "DASHED"
        assert now.dxf.material_handle == doc_in.layers.get("Walls").dxf.material_handle
        assert now.dxf.handle == doc_in.layers.get("Walls").dxf.handle

    def test_renaming_a_layer_takes_its_objects_along(self):
        doc, _ = read_strict(encoded(everything("R2013")))
        rename_layer(doc, "Walls", "Outer walls")
        theirs = assert_clean(write_dxf(doc))
        assert "Outer walls" in theirs.layers and "Walls" not in theirs.layers
        assert theirs.modelspace().query('LINE[layer=="Outer walls"]')
        assert theirs.blocks.get("BLK").query('CIRCLE[layer=="Outer walls"]')

    def test_adding_and_removing_layers_keeps_the_count_right(self):
        doc, _ = read_strict(encoded(everything("R2013")))
        with doc.transaction("x") as t:
            t.put_layer(Layer("New one", 2))
            t.put_layer(Layer("Another", 3))
        lines = write_dxf(doc).decode("utf-8").split("\n")
        at = next(i for i in range(len(lines) - 3) if lines[i + 1].strip() == "LAYER" and lines[i - 1].strip() == "TABLE")
        count = next(int(lines[i + 1]) for i in range(at, len(lines), 2) if lines[i].strip() == "70")
        assert count == len([x for x in doc.layers if x.source is None or x.source.text])
        assert_clean(write_dxf(doc))

    def test_a_changed_block_keeps_its_heading_and_the_rest(self):
        data = encoded(everything("R2013"))
        doc, _ = read_strict(data)
        b = doc.blocks["BLK"]
        first = next(e for e in b.entities if type(e.shape) is Line)
        moved = replace(first, shape=line(0, 0, 2, 2))
        with doc.transaction("x") as t:
            t.put_block(replace(b, entities=tuple(moved if e is first else e for e in b.entities)))
        back = write_dxf(doc)
        assert len(changed_lines(data, back)) < 30
        theirs = assert_clean(back)
        assert tuple(theirs.blocks.get("BLK").query("LINE")[0].dxf.end)[:2] == (2.0, 2.0)
        assert tuple(theirs.blocks.get("BLK").block.dxf.base_point)[:2] == (1.0, 1.0)

    def test_a_changed_block_base_writes_a_new_heading_with_the_same_handle(self):
        before = everything("R2013")
        doc, _ = read_strict(encoded(before))
        with doc.transaction("x") as t:
            t.put_block(replace(doc.blocks["BLK"], base=Vec(5.0, 6.0)))
        theirs = assert_clean(write_dxf(doc))
        block = theirs.blocks.get("BLK").block
        assert tuple(block.dxf.base_point)[:2] == (5.0, 6.0)
        assert block.dxf.handle == before.blocks.get("BLK").block.dxf.handle

    def test_a_new_block_gets_a_record_and_can_be_placed(self):
        doc, _ = read_strict(encoded(everything("R2013")))
        with doc.transaction("x") as t:
            t.put_block(BlockDef("Fresh", Vec(0.0, 0.0), (Entity(9000, line(0, 0, 1, 1)),)))
            t.add(Insert("Fresh", Vec(70.0, 70.0)))
        theirs = assert_clean(write_dxf(doc))
        assert len(theirs.blocks.get("Fresh").query("LINE")) == 1
        assert theirs.modelspace().query('INSERT[name=="Fresh"]')

    def test_removing_a_block_takes_its_record(self):
        doc, _ = read_strict(encoded(everything("R2013")))
        spare = BlockDef("Spare", Vec(0.0, 0.0))
        with doc.transaction("x") as t:
            t.put_block(spare)
        again, _ = read_strict(write_dxf(doc))
        with again.transaction("x") as t:
            t.remove_block("Spare")
        theirs = assert_clean(write_dxf(again))
        assert "Spare" not in theirs.blocks
        assert all(r.dxf.name != "Spare" for r in theirs.block_records)

    def test_saving_twice_changes_nothing_more(self):
        doc, _ = read_strict(encoded(everything("R2013")))
        with doc.transaction("x") as t:
            t.add(line(0, 0, 1, 1))
        once = write_dxf(doc)
        twice = write_dxf(read_strict(once)[0])
        assert once == twice

    def test_settings_change_only_their_own_lines(self):
        data = encoded(everything("R2013"))
        doc, _ = read_strict(data)
        with doc.transaction("x") as t:
            t.set_settings(replace(doc.settings, ltscale=7.0))
        changes = changed_lines(data, write_dxf(doc))
        assert "+ 7.0" in changes and len(changes) == 2
        assert read_strict(write_dxf(doc))[0].settings.ltscale == 7.0


class TestAChangeSurvivesANativeFile:
    """A .draftworx file holds a DXF's text so it can be written back. What it mustn't do is bring an old version of something back with it."""

    def test_a_moved_object_stays_moved(self):
        doc, _ = read_strict(encoded(everything("R2013")))
        e = a_line_with_a_handle(doc)
        with doc.transaction("x") as t:
            t.replace(e.id, line(1, 2, 12, 7))
        again = loads(dumps(doc))
        theirs = assert_clean(write_dxf(again))
        assert tuple([x for x in theirs.modelspace().query("LINE") if x.dxf.layer == "Walls"][0].dxf.end)[:2] == (12.0, 7.0)
        assert again.fingerprint() == doc.fingerprint()

    def test_a_restyled_object_stays_restyled_and_keeps_its_text(self):
        doc, _ = read_strict(encoded(everything("R2013")))
        e = next(e for e in of_type(doc, Line) if e.shape == line(50, 50, 60, 60))
        with doc.transaction("x") as t:
            t.replace(e.id, style=replace(e.style, color=5))
        theirs = assert_clean(write_dxf(loads(dumps(doc))))
        now = next(x for x in theirs.modelspace().query("LINE") if x.has_xdata("MYAPP"))
        assert now.dxf.color == 5 and now.dxf.thickness == 2.5

    def test_a_changed_layer_and_block_stay_changed(self):
        doc, _ = read_strict(encoded(everything("R2013")))
        with doc.transaction("x") as t:
            t.put_layer(replace(doc.layers["Walls"], color=1))
            t.put_block(replace(doc.blocks["BLK"], base=Vec(5.0, 6.0)))
        theirs = assert_clean(write_dxf(loads(dumps(doc))))
        assert theirs.layers.get("Walls").color == 1
        assert tuple(theirs.blocks.get("BLK").block.dxf.base_point)[:2] == (5.0, 6.0)

    def test_saving_the_native_file_is_verified_with_its_extras(self, tmp_path):
        doc, _ = read_strict(encoded(everything("R2013")))
        path = tmp_path / "a.draftworx"
        save_drawing(Opened(doc), path)
        again = open_drawing(path).doc
        assert again.extras == doc.extras

    def test_damaged_source_in_a_native_file_is_refused(self):
        doc, _ = read_strict(encoded(everything("R12")))
        text = dumps(doc).decode("utf-8")
        assert '"source":{"dxf":' in text
        with pytest.raises(FormatError, match="damaged"):
            loads(text.replace('"source":{"dxf":', '"source":{"dx":', 1).encode("utf-8"))


# ------------------------------------------------------------------------------------------------- reading what it means


def by_kind(version="R2013"):
    doc = everything(version)
    return doc, read_strict(encoded(doc))[0]


def close(a, b, eps=1e-9):
    return math.hypot(a[0] - b[0], a[1] - b[1]) <= eps


class TestWhatItReads:
    def test_lines_arcs_and_circles(self):
        doc = ezdxf.new("R2000")
        m = doc.modelspace()
        m.add_line((1, 2), (3, 4))
        m.add_circle((5, 6), 7)
        a = m.add_arc((1, 1), 2, 350, 20)
        mine, _ = read_strict(encoded(doc))
        assert of_type(mine, Line)[0].shape == line(1, 2, 3, 4)
        assert of_type(mine, Circle)[0].shape == Circle(Vec(5.0, 6.0), 7.0)
        arc = of_type(mine, Arc)[0].shape
        assert close(arc.start, a.start_point) and close(arc.end, a.end_point)
        assert arc.sweep == pytest.approx(math.radians(30.0))

    def test_an_arc_with_equal_angles_is_a_whole_turn(self):
        data = b"  0\nSECTION\n  2\nENTITIES\n  0\nARC\n  8\n0\n 10\n0\n 20\n0\n 40\n1\n 50\n90\n 51\n90\n  0\nENDSEC\n  0\nEOF\n"
        assert of_type(read_strict(data)[0], Arc)[0].shape.sweep == pytest.approx(TAU)

    def test_polylines_with_bulges_and_widths(self):
        doc = ezdxf.new("R2013")
        p = doc.modelspace().add_lwpolyline([(0, 0, 1, 2, 0.5), (5, 0, 0, 0, 0), (5, 5, 0, 0, -1)], close=True)
        mine, _ = read_strict(encoded(doc))
        s = of_type(mine, Polyline)[0].shape
        assert s.closed and s.bulges == (0.5, 0.0, -1.0) and s.widths == ((1.0, 2.0), (0.0, 0.0), (0.0, 0.0))
        for seg, theirs in zip(s.segments, p.virtual_entities()):
            start = theirs.dxf.start if theirs.dxftype() == "LINE" else None
            if start is not None:
                assert close(seg.start, start)

    def test_an_old_polyline_and_its_vertices(self):
        doc = ezdxf.new("R12")
        doc.modelspace().add_polyline2d([(0, 0), (5, 5), (9, 1)], close=True)
        s = of_type(read_strict(encoded(doc))[0], Polyline)[0].shape
        assert s.closed and s.pts == (Vec(0.0, 0.0), Vec(5.0, 5.0), Vec(9.0, 1.0))

    def test_a_polyline_with_no_seqend_is_still_all_there(self):
        data = (b"  0\nSECTION\n  2\nENTITIES\n  0\nPOLYLINE\n  8\n0\n 66\n1\n 70\n0\n  0\nVERTEX\n  8\n0\n 10\n1\n 20\n1\n  0\nVERTEX\n  8\n0\n 10\n2\n 20\n2\n"
                b"  0\nLINE\n  8\n0\n 10\n0\n 20\n0\n 11\n1\n 21\n1\n  0\nENDSEC\n  0\nEOF\n")
        doc, _ = read_strict(data)
        assert len(of_type(doc, Polyline)[0].shape.pts) == 2 and len(of_type(doc, Line)) == 1
        assert write_dxf(doc) == data

    def test_a_spline_is_the_same_curve(self):
        doc = ezdxf.new("R2013")
        s = doc.modelspace().add_rational_spline([(0, 40), (1, 41), (3, 40), (5, 45), (7, 41)], weights=[1, 2, 3, 2, 1], degree=3)
        mine = of_type(read_strict(encoded(doc))[0], Spline)[0].shape
        tool = s.construction_tool()
        for i in range(21):
            p = tool.point(tool.max_t * i / 20)
            q = mine.project((p.x, p.y))[1]
            assert close(p, q, 1e-7)

    def test_a_spline_given_only_by_its_points_goes_through_them(self):
        doc = ezdxf.new("R2013")
        pts = [(0, 0), (1, 2), (4, 1), (6, 5), (8, 4)]
        doc.modelspace().add_spline(fit_points=pts)
        mine = of_type(read_strict(encoded(doc))[0], Spline)[0].shape
        assert mine.fit == tuple(Vec(float(x), float(y)) for x, y in pts)
        assert mine.degree == 3
        for p in pts:
            assert close(mine.project(p)[1], p, 1e-7)

    def test_ellipses(self):
        doc = ezdxf.new("R2013")
        e = doc.modelspace().add_ellipse((30, 0), major_axis=(4, 1), ratio=0.5, start_param=0.3, end_param=4)
        s = of_type(read_strict(encoded(doc))[0], EllipseArc)[0].shape
        assert close(s.start, e.start_point) and close(s.end, e.end_point)

    def test_text_is_where_its_alignment_says(self):
        doc = ezdxf.new("R2013")
        t = doc.modelspace().add_text("x", height=2.0)
        t.set_placement((5, 6), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
        a = doc.modelspace().add_text("y", height=2.0)
        a.set_placement((1, 1), (9, 1), align=ezdxf.enums.TextEntityAlignment.ALIGNED)
        one, two = of_type(read_strict(encoded(doc))[0], Text)
        assert one.shape.p == Vec(5.0, 6.0) and (one.shape.halign, one.shape.valign) == (1, 2)
        assert two.shape.p == Vec(1.0, 1.0) and two.shape.p2 == Vec(9.0, 1.0) and two.shape.halign == 3

    def test_special_characters_in_text(self):
        assert decode_text("20%%d %%p1 %%c5 100%%% %%uword%%u") == "20° ±1 Ø5 100% word"
        assert decode_text("%%176") == "°"
        assert decode_text("caf\\U+00E9") == "café"
        assert decode_text("x^ 2 and y^Iz") == "x^2 and y z"
        assert decode_text("plain") == "plain"

    def test_text_goes_out_as_it_came_in(self):
        for words in ("x^2", "café", "tab\there"):
            out = encode_text(words, "AC1015", "cp1252")
            assert decode_text(out).replace("\t", " ") == words.replace("\t", " ")
        assert encode_text("日本", "AC1015", "cp1252") == "\\U+65E5\\U+672C"
        assert encode_text("日本", "AC1027", "utf-8") == "日本"
        assert "\n" not in encode_text("two\nlines", "AC1027", "utf-8")

    def test_paragraph_text_in_pieces(self):
        doc = ezdxf.new("R2013")
        doc.modelspace().add_mtext("abc" * 300, dxfattribs={"char_height": 1, "insert": (2, 3), "attachment_point": 5, "rotation": 30})
        s = of_type(read_strict(encoded(doc))[0], MText)[0].shape
        assert s.text == "abc" * 300 and s.attach == 5 and s.p == Vec(2.0, 3.0)
        assert s.rotation == pytest.approx(math.radians(30.0))

    def test_paragraph_text_with_columns_reads_only_the_text(self):
        data = (DATA.parent / "data" / "text_alignments.dxf").read_bytes()
        assert read_strict(data)[0].entities

    def test_blocks_attributes_and_nesting(self):
        _, mine = by_kind()
        b = mine.blocks["BLK"]
        assert b.base == Vec(1.0, 1.0) and len(b.entities) == 3
        adef = next(e.shape for e in b.entities if type(e.shape) is AttDef)
        assert adef.attrib.tag == "TAG" and adef.attrib.prompt == "What?"
        ins = next(e.shape for e in of_type(mine, Insert) if e.shape.block == "BLK")
        assert ins.attribs[0].tag == "TAG" and ins.attribs[0].text.text == "value"
        assert ins.rotation == pytest.approx(math.radians(30.0))

    def test_what_a_block_copy_draws_is_where_ezdxf_says(self):
        doc = ezdxf.new("R2013")
        b = doc.blocks.new("B", base_point=(1, 2))
        b.add_line((0, 0), (5, 0))
        n = doc.blocks.new("N")
        n.add_blockref("B", (3, 3), dxfattribs={"xscale": 2, "yscale": 3, "rotation": 45})
        ins = doc.modelspace().add_blockref("N", (10, 20), dxfattribs={"rotation": 30, "xscale": -1})
        mine, _ = read_strict(encoded(doc))
        mine_line = next(curves_of(mine, of_type(mine, Insert)[0]))
        inner = next(ins.virtual_entities())
        theirs = next(inner.virtual_entities())
        assert close(mine_line.a, theirs.dxf.start) and close(mine_line.b, theirs.dxf.end)

    def test_a_block_array(self):
        doc = ezdxf.new("R2013")
        b = doc.blocks.new("B")
        b.add_line((0, 0), (1, 0))
        ins = doc.modelspace().add_blockref("B", (0, 0), dxfattribs={"rotation": 90})
        ins.grid((3, 2), (10, 20))    # 3 rows, 2 columns
        mine, _ = read_strict(encoded(doc))
        s = of_type(mine, Insert)[0].shape
        assert (s.rows, s.columns) == (3, 2)
        mine_lines = sorted((round(c.a[0], 9), round(c.a[1], 9)) for c in curves_of(mine, of_type(mine, Insert)[0]))
        theirs = sorted((round(v.dxf.start.x, 9), round(v.dxf.start.y, 9)) for one in ins.multi_insert() for v in one.virtual_entities())
        assert mine_lines == theirs

    def test_tables_and_settings(self):
        doc = everything("R2013")
        doc.header["$INSUNITS"] = 6
        doc.header["$LTSCALE"] = 2.5
        doc.header["$LUPREC"] = 3
        doc.header["$CLAYER"] = "Walls"
        mine, _ = read_strict(encoded(doc))
        assert mine.settings.units == "m" and mine.settings.ltscale == 2.5 and mine.settings.linear_decimals == 3
        assert mine.current.layer == "Walls"
        assert mine.layers["Walls"].color == 3 and mine.layers["Walls"].linetype == "DASHED"
        assert not mine.layers["Off"].on and mine.layers["Frozen"].frozen and mine.layers["Locked"].locked
        assert mine.layers["Rgb"].color == 0x1000000 | 0x0a141e
        assert mine.is_hidden(Style("Off")) and mine.is_hidden(Style("Frozen")) and mine.is_locked(Style("Locked"))
        dashed = mine.linetypes["DASHED"]
        assert dashed.pattern and dashed.pattern[0] > 0.0 and dashed.pattern[1] < 0.0
        assert "Standard" in mine.text_styles and "Standard" in mine.dim_styles

    def test_a_layer_that_was_never_declared(self):
        data = b"  0\nSECTION\n  2\nENTITIES\n  0\nLINE\n  8\nGhost\n 10\n0\n 20\n0\n 11\n1\n 21\n1\n  0\nENDSEC\n  0\nEOF\n"
        doc, _ = read_strict(data)
        assert "Ghost" in doc.layers
        assert write_dxf(doc) == data
        with doc.transaction("x") as t:
            t.put_layer(replace(doc.layers["Ghost"], color=4))
        theirs, _ = parsed(write_dxf(doc))
        assert theirs.layers.get("Ghost").color == 4

    def test_a_height_is_mentioned_and_kept(self):
        doc = ezdxf.new("R2013")
        doc.modelspace().add_line((1, 2, 5), (3, 4, 9))
        data = encoded(doc)
        mine, notes = read_strict(data)
        assert of_type(mine, Line)[0].shape == line(1, 2, 3, 4)
        assert any("height" in n for n in notes)
        assert write_dxf(mine) == data

    def test_a_dwg_file_gets_told(self):
        with pytest.raises(FormatError, match="DWG"):
            read_dxf(b"AC1027\x00\x00\x00binary stuff")


class TestSeenFromBehind:
    """A mirrored object in a DXF often has a negative extrusion: its own X runs the other way. ezdxf says where it really is."""

    def extruded(self):
        doc = ezdxf.new("R2013")
        doc.modelspace()
        return doc

    def test_circle(self):
        doc = self.extruded()
        doc.modelspace().add_circle((5, 6), 2, dxfattribs={"extrusion": (0, 0, -1)})
        assert of_type(read_strict(encoded(doc))[0], Circle)[0].shape.c == Vec(-5.0, 6.0)

    def test_arc(self):
        doc = self.extruded()
        a = doc.modelspace().add_arc((5, 6), 2, 10, 100, dxfattribs={"extrusion": (0, 0, -1)})
        s = of_type(read_strict(encoded(doc))[0], Arc)[0].shape
        ends = (a.start_point, a.end_point)
        assert any(close(s.start, p) for p in ends) and any(close(s.end, p) for p in ends)
        assert s.sweep == pytest.approx(math.radians(90.0))
        mid = a.ocs().to_wcs(Vec3(5 + 2 * math.cos(math.radians(55)), 6 + 2 * math.sin(math.radians(55))))
        assert close(s.point_at(0.5), mid, 1e-9)

    def test_polyline(self):
        doc = self.extruded()
        p = doc.modelspace().add_lwpolyline([(0, 0, 0, 0, 0.5), (5, 0), (5, 5)], dxfattribs={"extrusion": (0, 0, -1)})
        s = of_type(read_strict(encoded(doc))[0], Polyline)[0].shape
        for mine, theirs in zip(s.pts, p.vertices_in_wcs()):
            assert close(mine, theirs)
        assert s.bulges[0] == -0.5
        mid = next(p.virtual_entities())
        assert close(s.segments[0].point_at(0.5), mid.vertices([(mid.dxf.start_angle + mid.dxf.end_angle) / 2 if mid.dxf.start_angle < mid.dxf.end_angle else (mid.dxf.start_angle + mid.dxf.end_angle + 360) / 2]).__next__(), 1e-9)

    def test_ellipse(self):
        doc = self.extruded()
        e = doc.modelspace().add_ellipse((3, 4), major_axis=(4, 1), ratio=0.5, start_param=0.3, end_param=2.0, dxfattribs={"extrusion": (0, 0, -1)})
        s = of_type(read_strict(encoded(doc))[0], EllipseArc)[0].shape
        assert close(s.start, e.start_point) and close(s.end, e.end_point)
        assert close(s.point_at(0.5), next(e.vertices([1.15])), 1e-9)

    def test_insert(self):
        doc = self.extruded()
        b = doc.blocks.new("B")
        b.add_line((1, 0), (2, 0))
        ins = doc.modelspace().add_blockref("B", (5, 6), dxfattribs={"extrusion": (0, 0, -1), "rotation": 30})
        mine, _ = read_strict(encoded(doc))
        c = next(curves_of(mine, of_type(mine, Insert)[0]))
        theirs = next(ins.virtual_entities())
        assert close(c.a, theirs.dxf.start) and close(c.b, theirs.dxf.end)

    def test_text(self):
        doc = self.extruded()
        t = doc.modelspace().add_text("x", height=2.0, dxfattribs={"extrusion": (0, 0, -1), "rotation": 0})
        t.set_placement((5, 6))
        s = of_type(read_strict(encoded(doc))[0], Text)[0].shape
        assert s.p == Vec(-5.0, 6.0) and s.mirrored_x

    def test_solid(self):
        doc = self.extruded()
        doc.modelspace().add_solid([(1, 1), (2, 1), (1, 2), (2, 2)], dxfattribs={"extrusion": (0, 0, -1)})
        s = of_type(read_strict(encoded(doc))[0], Solid)[0].shape
        assert set(s.pts) == {Vec(-1.0, 1.0), Vec(-2.0, 1.0), Vec(-1.0, 2.0), Vec(-2.0, 2.0)}

    def test_hatch_outline(self):
        doc = self.extruded()
        h = doc.modelspace().add_hatch(dxfattribs={"extrusion": (0, 0, -1)})
        h.paths.add_polyline_path([(1, 1), (3, 1), (3, 3)], is_closed=True)
        s = of_type(read_strict(encoded(doc))[0], Foreign)[0].shape
        assert set(s.curves[0].pts) == {Vec(-1.0, 1.0), Vec(-3.0, 1.0), Vec(-3.0, 3.0)}

    def test_tilted_out_of_the_plane_is_kept_and_said(self):
        doc = self.extruded()
        doc.modelspace().add_circle((5, 6), 2, dxfattribs={"extrusion": (0, 1, 1)})
        data = encoded(doc)
        mine, notes = read_strict(data)
        assert type(next(iter(mine.entities.values())).shape) is Foreign
        assert any("circle" in n for n in notes)
        assert write_dxf(mine) == data


class TestWhatCantBeEdited:
    def test_a_dimension_can_be_changed_and_its_block_draws_it_till_it_is(self):
        _, mine = by_kind()
        dim = of_type(mine, Dimension)[0]
        assert dim.shape.block.startswith("*D")
        assert mine.block_use(dim.shape.block) >= 1
        assert mine.shape_bbox(dim.shape) is not None
        assert any(type(c) is Line for c in curves_of(mine, dim))
        assert dim.shape.transformed(Affine.translation(5.0, 0.0)).block == ""

    def test_a_dimension_placed_away_from_its_block_stays_as_the_file_had_it(self):
        doc = ezdxf.new("R2013")
        doc.modelspace().add_linear_dim(base=(0, 10), p1=(0, 0), p2=(10, 0)).render()
        data = encoded(doc).replace(b"  3\nStandard\n 10\n", b"  3\nStandard\n 12\n100.0\n 22\n0.0\n 10\n", 1)
        mine, _ = read_strict(data)
        dim = of_type(mine, Foreign)[0]
        assert dim.shape.dxf_type == "DIMENSION" and dim.shape.at == (100.0, 0.0)
        assert mine.shape_bbox(dim.shape)[0] >= 99.0
        assert all(min(c.bbox()[0], c.bbox()[2]) >= 99.0 for c in curves_of(mine, dim))

    def test_a_block_a_dimension_uses_cant_be_removed(self):
        _, mine = by_kind()
        dim = of_type(mine, Dimension)[0]
        with pytest.raises(Exception, match="still used"):
            with mine.transaction("x") as t:
                t.remove_block(dim.shape.block)
        with mine.transaction("x") as t:
            t.remove(dim.id)
            t.remove_block(dim.shape.block)

    def test_a_hatch_shows_its_boundary(self):
        doc = ezdxf.new("R2013")
        h = doc.modelspace().add_hatch()
        h.paths.add_polyline_path([(0, 0, 0.5), (5, 0), (5, 5)], is_closed=True)
        e = h.paths.add_edge_path()
        e.add_line((10, 10), (20, 10))
        e.add_arc((15, 10), 5, 0, 180)
        e.add_arc((15, 10), 5, 0, 180, ccw=False)
        e.add_ellipse((15, 20), (4, 0), 0.5, 0, 90)
        e.add_spline(control_points=[(0, 0), (1, 1), (2, 0), (3, 1)], degree=3, knot_values=[0, 0, 0, 0, 1, 1, 1, 1])
        mine, notes = read_strict(encoded(doc))
        s = of_type(mine, Foreign)[0].shape
        kinds = [type(c) for c in s.curves]
        assert kinds == [Polyline, Line, Arc, Arc, EllipseArc, Spline]
        assert s.curves[0].closed and s.curves[0].bulges[0] == 0.5
        assert close(s.curves[2].start, (20, 10)) and close(s.curves[2].end, (10, 10))
        # one stored clockwise covers the same points: only its direction differs
        assert close(s.curves[3].point_at(0.5), (15, 15), 1e-9)
        assert any("outlines" in n for n in notes)

    def test_a_leader_a_3d_polyline_a_face_and_an_image_show_what_they_can(self):
        doc = ezdxf.new("R2013")
        m = doc.modelspace()
        m.add_leader([(0, 0), (5, 5), (10, 5)])
        m.add_polyline3d([(0, 0, 0), (1, 1, 1), (2, 0, 2)])
        m.add_3dface([(0, 0, 0), (1, 0, 1), (1, 1, 1), (0, 1, 0)])
        d = doc.add_image_def("x.png", size_in_pixel=(100, 50))
        m.add_image(d, insert=(10, 10), size_in_units=(20, 10))
        mine, _ = read_strict(encoded(doc))
        shapes = {e.shape.dxf_type: e.shape for e in of_type(mine, Foreign)}
        assert shapes["LEADER"].curves[0].pts == (Vec(0.0, 0.0), Vec(5.0, 5.0), Vec(10.0, 5.0))
        assert shapes["POLYLINE"].curves[0].pts == (Vec(0.0, 0.0), Vec(1.0, 1.0), Vec(2.0, 0.0))
        assert shapes["3DFACE"].curves[0].closed and len(shapes["3DFACE"].curves[0].pts) == 4
        assert mine.shape_bbox(shapes["IMAGE"]) == pytest.approx((10.0, 10.0, 30.0, 20.0))

    def test_what_has_nothing_to_show_is_said_and_doesnt_stretch_the_view(self):
        doc = ezdxf.new("R2013")
        doc.modelspace().add_xline((1e6, 1e6), (1, 1))
        mine, notes = read_strict(encoded(doc))
        assert mine.extents() is None
        assert any("Not shown" in n and "construction line" in n for n in notes)

    def test_nonsense_is_kept_and_counted(self):
        data = (b"  0\nSECTION\n  2\nENTITIES\n  0\nCIRCLE\n  8\n0\n 10\n0\n 20\n0\n 40\n-3\n  0\nSPLINE\n  8\n0\n 70\n8\n 71\n3\n 72\n2\n 73\n1\n 74\n0\n 40\n0\n 40\n0\n 10\n0\n 20\n0\n"
                b"  0\nLINE\n  8\n0\n 10\n1e300\n 20\n0\n 11\n1\n 21\n1\n  0\nENDSEC\n  0\nEOF\n")
        doc, notes = read_strict(data)
        assert all(type(e.shape) is Foreign for e in doc.entities.values()) and len(doc.entities) == 3
        assert any("made no sense" in n for n in notes)
        assert write_dxf(doc) == data

    def test_moving_it_leaves_it_and_says_so(self):
        _, mine = by_kind()
        with Person() as p:
            p.s.replace_document(mine)
            ids = [e.id for e in mine.sorted_entities() if type(e.shape) is Foreign][:2] + [of_type(mine, Line)[0].id]
            before = {i: mine.entities[i].shape for i in ids}
            p.s.set_selection(ids)
            p.s.run("MOVE")
            p.click(0, 0)
            p.click(10, 0)
            assert mine.entities[ids[0]].shape is before[ids[0]]
            assert mine.entities[ids[2]].shape != before[ids[2]]
            assert "2 objects from the file" in p.s.message

    def test_copying_it_leaves_it_and_says_so(self):
        _, mine = by_kind()
        count = len(mine.entities)
        with Person() as p:
            p.s.replace_document(mine)
            ids = [next(e.id for e in of_type(mine, Foreign))]
            p.s.set_selection(ids)
            p.s.run("COPY")
            p.click(0, 0)
            p.click(10, 0)
            assert "from the file" in p.s.message
            p.enter()
            assert len(mine.entities) == count

    def test_erasing_it_works_and_changes_the_file(self):
        doc = ezdxf.new("R2013")
        doc.modelspace().add_leader([(0, 0), (5, 5)])
        mine, _ = read_strict(encoded(doc))
        with mine.transaction("x") as t:
            t.remove(next(iter(mine.entities)))
        assert not assert_clean(write_dxf(mine)).modelspace().query("LEADER")

    def test_changing_its_layer_changes_only_that(self):
        data = encoded(everything("R2013"))
        doc, _ = read_strict(data)
        h = next(e for e in of_type(doc, Foreign) if e.shape.dxf_type == "HATCH")
        with doc.transaction("x") as t:
            t.replace(h.id, style=replace(h.style, layer="0", color=1))
        back = write_dxf(doc)
        assert len(changed_lines(data, back)) <= 4
        theirs = assert_clean(back)
        now = theirs.modelspace().query("HATCH")[0]
        assert now.dxf.layer == "0" and now.dxf.color == 1 and len(now.paths) == 2

    def test_changing_the_layer_of_a_polyline_with_vertices_reaches_them(self):
        doc = ezdxf.new("R12")
        doc.layers.add("B")
        doc.modelspace().add_polyline3d([(0, 0, 0), (1, 1, 1)])
        mine, _ = read_strict(encoded(doc))
        e = next(iter(mine.entities.values()))
        assert type(e.shape) is Foreign
        with mine.transaction("x") as t:
            t.replace(e.id, style=replace(e.style, layer="B"))
        theirs, _ = parsed(write_dxf(mine))
        p = theirs.modelspace().query("POLYLINE")[0]
        assert p.dxf.layer == "B" and all(v.dxf.layer == "B" for v in p.vertices)

    def test_it_can_be_picked_and_snapped_to(self):
        doc = ezdxf.new("R2013")
        doc.modelspace().add_leader([(0, 0), (50, 50), (100, 50)])
        doc.modelspace().add_linear_dim(base=(0, 110), p1=(0, 100), p2=(100, 100)).render()
        mine, _ = read_strict(encoded(doc))
        leader = next(e for e in of_type(mine, Foreign) if e.shape.dxf_type == "LEADER")
        dim = of_type(mine, Dimension)[0]
        assert pick(mine, Vec(25.0, 25.0), 1.0) == [leader.id]
        assert pick(mine, Vec(50.0, 110.0), 1.0) == [dim.id]
        engine = SnapEngine(mine)
        got = engine.pick(SnapQuery(Vec(50.4, 50.4), 0.1))
        assert got.point == Vec(50.0, 50.0) and got.candidate.kind == Kind.ENDPOINT
        inside = engine.pick(SnapQuery(Vec(0.4, 109.6), 0.1))
        assert inside.candidate.kind == Kind.ENDPOINT and close(inside.point, (0.0, 110.0), 0.5)

    def test_the_panel_can_name_it(self):
        from draftworx.core.info import name_of, rows_for
        s = Foreign("ACAD_TABLE")
        assert name_of(s) == "Table" and name_of(Foreign("LEADER")) == "Leader"
        assert rows_for(s, Document().settings)

    def test_it_survives_a_native_file(self):
        _, mine = by_kind()
        again = loads(dumps(mine))
        assert [e.shape for e in of_type(again, Foreign)] == [e.shape for e in of_type(mine, Foreign)]


# ------------------------------------------------------------------------------------------------- files from elsewhere: binary


class TestBinary:
    @pytest.mark.parametrize("version", ["R12", "R2000", "R2013", "R2018"])
    def test_reads_the_same_as_text(self, version, tmp_path):
        doc = everything(version)
        doc.saveas(tmp_path / "a.dxf", fmt="bin")
        data = (tmp_path / "a.dxf").read_bytes()
        assert data.startswith(b"AutoCAD Binary DXF")
        binary, notes = read_strict(data)
        text, _ = read_strict(encoded(doc))
        assert [e.shape for e in binary.sorted_entities()] == pytest.approx([e.shape for e in text.sorted_entities()], abs=0) or \
            [type(e.shape) for e in binary.sorted_entities()] == [type(e.shape) for e in text.sorted_entities()]
        for a, b in zip(binary.sorted_entities(), text.sorted_entities()):
            assert type(a.shape) is type(b.shape)
            box_a, box_b = binary.shape_bbox(a.shape), text.shape_bbox(b.shape)
            assert (box_a is None) == (box_b is None)
            if box_a is not None:
                assert box_a == pytest.approx(box_b, abs=1e-9)
        assert any("binary DXF" in n for n in notes)
        assert_clean(write_dxf(binary))

    @pytest.mark.parametrize("path", BINARY_SAMPLES, ids=lambda p: p.name)
    def test_real_binary_files(self, path):
        doc, _ = read_strict(path.read_bytes())
        again, _ = read_strict(write_dxf(doc))
        assert [type(e.shape) for e in again.sorted_entities()] == [type(e.shape) for e in doc.sorted_entities()]
        theirs = ezdxf.readfile(str(path))
        assert len(doc.entities) == len(theirs.modelspace())

    def test_group_code_types(self):
        assert group_type(310) == "b" and group_type(1004) == "b"
        assert group_type(1) == "s" and group_type(330) == "s" and group_type(1000) == "s" and group_type(999) == "s"
        assert group_type(10) == "d" and group_type(1040) == "d"
        assert group_type(70) == "h" and group_type(1060) == "h" and group_type(1070) == "h"
        assert group_type(90) == "l" and group_type(1071) == "l" and group_type(160) == "q" and group_type(290) == "?"

    def test_cut_off(self):
        data = (DATA / "bin_dxf_r12.dxf").read_bytes()
        for cut in (22, 23, 30, 200, len(data) // 2, len(data) - 3):
            with pytest.raises(FormatError):
                read_strict(data[:cut])


# ------------------------------------------------------------------------------------------------- encodings


class TestEncodings:
    def test_utf8_from_2007(self):
        doc = ezdxf.new("R2013")
        doc.modelspace().add_text("日本語 ñ").set_placement((0, 0))
        mine, _ = read_strict(encoded(doc))
        assert of_type(mine, Text)[0].shape.text == "日本語 ñ"
        with mine.transaction("x") as t:
            t.add(Text(Vec(1.0, 1.0), 1.0, "ещё"))
        theirs = assert_clean(write_dxf(mine))
        assert {x.dxf.text for x in theirs.modelspace().query("TEXT")} == {"日本語 ñ", "ещё"}

    def test_a_code_page_before_2007(self):
        data = (b"  0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\nAC1015\n  9\n$DWGCODEPAGE\n  3\nANSI_1251\n  0\nENDSEC\n"
                b"  0\nSECTION\n  2\nENTITIES\n  0\nTEXT\n  8\n0\n 10\n0\n 20\n0\n 40\n1\n  1\n" + "Привет".encode("cp1251") + b"\n  0\nENDSEC\n  0\nEOF\n")
        doc, _ = read_strict(data)
        assert of_type(doc, Text)[0].shape.text == "Привет"
        assert write_dxf(doc) == data
        with doc.transaction("x") as t:
            t.add(Text(Vec(1.0, 1.0), 1.0, "ещё 日"))
        back = write_dxf(doc)
        assert "ещё".encode("cp1251") in back and b"\\U+65E5" in back
        assert of_type(read_strict(back)[0], Text)[1].shape.text == "ещё 日"

    def test_a_multi_byte_code_page(self):
        data = (b"  0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\nAC1015\n  9\n$DWGCODEPAGE\n  3\nANSI_932\n  0\nENDSEC\n"
                b"  0\nSECTION\n  2\nENTITIES\n  0\nTEXT\n  8\n0\n 10\n0\n 20\n0\n 40\n1\n  1\n" + "日本語".encode("cp932") + b"\n  0\nENDSEC\n  0\nEOF\n")
        doc, _ = read_strict(data)
        assert of_type(doc, Text)[0].shape.text == "日本語"
        assert write_dxf(doc) == data

    def test_an_unknown_code_page_is_read_as_latin(self):
        data = (b"  0\nSECTION\n  2\nHEADER\n  9\n$ACADVER\n  1\nAC1015\n  9\n$DWGCODEPAGE\n  3\nNONSENSE\n  0\nENDSEC\n"
                b"  0\nSECTION\n  2\nENTITIES\n  0\nTEXT\n  8\n0\n 10\n0\n 20\n0\n 40\n1\n  1\ncaf\xe9\n  0\nENDSEC\n  0\nEOF\n")
        doc, _ = read_strict(data)
        assert of_type(doc, Text)[0].shape.text == "café"
        assert write_dxf(doc) == data


# ------------------------------------------------------------------------------------------------- new files


def a_drawing() -> Document:
    d = Document()
    with d.transaction("x") as t:
        t.put_layer(Layer("Red part", 1, weight=50))
        t.put_layer(Layer("Hidden", 2, on=False))
        t.put_layer(Layer("Rgb", 0x1000000 | 0x102030, plot=False, locked=True, frozen=True))
        t.put_linetype(Linetype("Dash", "dashes", (10.0, -5.0, 0.0, -5.0)))
        t.add(line(0, 0, 100, 0), Style("Red part"))
        t.add(line(0, 0, 0, 100), Style("0", 3, "Dash", 35, 2.0))
        t.add(line(0, 0, 10, 10), Style("0", 0x1000000 | 0x405060))
        t.add(Circle(Vec(50.0, 50.0), 25.0))
        t.add(Arc(Vec(50.0, 50.0), 40.0, 0.5, 2.0))
        t.add(Arc(Vec(50.0, 50.0), 45.0, 2.0, -1.5))
        t.add(EllipseArc(Vec(150.0, 50.0), Vec(40.0, 10.0), 0.4, 0.0, TAU))
        t.add(EllipseArc(Vec(250.0, 50.0), Vec(40.0, 10.0), 0.4, 1.0, 2.0))
        t.add(Polyline((Vec(200.0, 0.0), Vec(250.0, 0.0), Vec(250.0, 50.0)), (0.0, 0.5, 0.0), True))
        t.add(Polyline((Vec(0.0, 300.0), Vec(50.0, 300.0)), width=2.0))
        t.add(Polyline((Vec(0.0, 310.0), Vec(50.0, 310.0), Vec(80.0, 310.0)), widths=((1.0, 2.0), (2.0, 0.0), (0.0, 0.0))))
        t.add(spline_through([Vec(0.0, 120.0), Vec(30.0, 150.0), Vec(60.0, 120.0), Vec(90.0, 150.0)]))
        t.add(Text(Vec(10.0, 200.0), 5.0, "Hello é ° 日本 x^2", 0.5))
        t.add(Text(Vec(10.0, 210.0), 5.0, "Centred", 0.0, "Standard", 1, 2))
        t.add(Text(Vec(10.0, 220.0), 5.0, "Fit", 0.0, "Standard", 5, 0, 1.0, 0.0, Vec(60.0, 220.0)))
        t.add(MText(Vec(10.0, 180.0), 3.0, "Para one\\Pand two " + "more " * 80, 80.0, 0.3, 5, "Standard", 1.5))
        t.add(Solid((Vec(300.0, 0.0), Vec(320.0, 0.0), Vec(320.0, 20.0), Vec(300.0, 20.0))))
        t.add(Solid((Vec(330.0, 0.0), Vec(340.0, 0.0), Vec(335.0, 20.0))))
        t.add(Node(Vec(5.0, 5.0)))
        t.put_block(BlockDef("Bolt", Vec(1.0, 1.0), (Entity(1000, Circle(Vec(0.0, 0.0), 3.0)), Entity(1001, line(-5, 0, 5, 0)),
                                                     Entity(1002, AttDef(Attrib("MARK", Text(Vec(0.0, 5.0), 1.0, "?"), "Which?"))))))
        t.add(Insert("Bolt", Vec(100.0, 100.0), 2.0, 2.0, 0.3, (Attrib("MARK", Text(Vec(100.0, 110.0), 1.0, "M6")),)))
        t.add(Insert("Bolt", Vec(200.0, 200.0), 1.0, 1.0, 0.0, (), 3, 2, 10.0, 20.0))
    return d


class TestANewFile:
    @pytest.mark.parametrize("version", [v for k, v in VERSIONS.items() if k != "R12"])
    def test_ezdxf_reads_it_and_audits_it_clean(self, version):
        data, report = encode_dxf(a_drawing(), version)
        assert not report.converted and not report.lost
        theirs = assert_clean(data)
        assert theirs.dxfversion == version
        m = theirs.modelspace()
        assert len(m) == len(a_drawing().entities)
        assert len(m.query("LINE")) == 3 and len(m.query("CIRCLE")) == 1 and len(m.query("ARC")) == 2 and len(m.query("ELLIPSE")) == 2
        assert len(m.query("LWPOLYLINE")) == 3 and len(m.query("SPLINE")) == 1 and len(m.query("TEXT")) == 3 and len(m.query("MTEXT")) == 1
        assert len(m.query("SOLID")) == 2 and len(m.query("POINT")) == 1 and len(m.query("INSERT")) == 2

    def test_the_default_is_2013(self):
        assert b"AC1027" in write_dxf(a_drawing())[:200] and DEFAULT == "AC1027"

    def test_what_ezdxf_sees_is_what_was_drawn(self):
        theirs = assert_clean(write_dxf(a_drawing()))
        m = theirs.modelspace()
        first = m.query("LINE")[0]
        assert (first.dxf.layer, tuple(first.dxf.end)[:2]) == ("Red part", (100.0, 0.0))
        second = m.query("LINE")[1]
        assert (second.dxf.color, second.dxf.linetype, second.dxf.lineweight, second.dxf.ltscale) == (3, "Dash", 35, 2.0)
        assert m.query("LINE")[2].rgb == (0x40, 0x50, 0x60)
        arcs = m.query("ARC")
        assert (arcs[0].dxf.start_angle, arcs[0].dxf.end_angle) == pytest.approx((math.degrees(0.5), math.degrees(2.5)))
        # a clockwise one goes out counter-clockwise, as the same curve
        assert close(arcs[1].start_point, Arc(Vec(50.0, 50.0), 45.0, 2.0, -1.5).end, 1e-9)
        el = m.query("ELLIPSE")[1]
        assert close(el.start_point, EllipseArc(Vec(250.0, 50.0), Vec(40.0, 10.0), 0.4, 1.0, 2.0).start, 1e-9)
        assert close(el.end_point, EllipseArc(Vec(250.0, 50.0), Vec(40.0, 10.0), 0.4, 1.0, 2.0).end, 1e-9)
        assert m.query("LWPOLYLINE")[1].dxf.const_width == 2.0
        assert m.query("LWPOLYLINE")[2].get_points("xyseb")[0] == pytest.approx((0.0, 310.0, 1.0, 2.0, 0.0))
        solid = m.query("SOLID")[0]
        assert list(solid.wcs_vertices())[:4] == [Vec3(300, 0), Vec3(320, 0), Vec3(320, 20), Vec3(300, 20)]
        centre = m.query("TEXT")[1]
        assert centre.get_placement()[1].x == 10.0 and centre.get_placement()[0].name == "MIDDLE_CENTER"
        fit = m.query("TEXT")[2]
        assert fit.get_placement()[0].name == "FIT" and fit.get_placement()[2].x == 60.0
        para = m.query("MTEXT")[0]
        assert para.text.startswith("Para one\\Pand two") and para.dxf.attachment_point == 5 and para.get_rotation() == pytest.approx(math.degrees(0.3))
        assert theirs.layers.get("Red part").color == 1 and theirs.layers.get("Red part").dxf.lineweight == 50
        assert not theirs.layers.get("Hidden").is_on() and theirs.layers.get("Rgb").is_frozen() and theirs.layers.get("Rgb").is_locked()
        assert theirs.layers.get("Rgb").rgb == (0x10, 0x20, 0x30) and not theirs.layers.get("Rgb").dxf.plot
        bolt = theirs.blocks.get("Bolt")
        assert tuple(bolt.block.dxf.base_point)[:2] == (1.0, 1.0) and len(bolt.query("ATTDEF")) == 1
        ins = m.query("INSERT")[0]
        assert ins.dxf.name == "Bolt" and ins.get_attrib("MARK").dxf.text == "M6" and ins.dxf.rotation == pytest.approx(math.degrees(0.3))
        grid = m.query("INSERT")[1]
        assert (grid.dxf.column_count, grid.dxf.row_count, grid.dxf.column_spacing, grid.dxf.row_spacing) == (3, 2, 10.0, 20.0)

    def test_it_reads_back_exactly(self):
        d = a_drawing()
        back, _ = read_strict(write_dxf(d))
        want, have = d.sorted_entities(), back.sorted_entities()
        assert len(want) == len(have)
        for a, b in zip(want, have):
            assert type(a.shape) is type(b.shape)
            assert a.style == b.style
            if type(a.shape) in (Line, Circle, Polyline, Spline, Node, Solid):
                assert a.shape == b.shape
            else:
                assert d.shape_bbox(a.shape) == pytest.approx(back.shape_bbox(b.shape), abs=1e-9)
        assert {x.name for x in d.layers} == {x.name for x in back.layers}
        assert back.layers["Red part"].color == 1 and back.linetypes["Dash"].pattern == (10.0, -5.0, 0.0, -5.0)
        assert back.blocks["Bolt"].base == Vec(1.0, 1.0)

    @given(st.lists(st.tuples(coord, coord, coord, coord), min_size=1, max_size=8))
    @settings(max_examples=50)
    def test_numbers_come_back_exactly(self, items):
        d = Document()
        with d.transaction("x") as t:
            for x0, y0, x1, y1 in items:
                t.add(Line(Vec(x0, y0), Vec(x1, y1)))
        back, _ = read_strict(write_dxf(d))
        assert [e.shape for e in back.sorted_entities()] == [e.shape for e in d.sorted_entities()]

    def test_numbers_every_reader_can_read(self):
        assert fmt_float(1e20) == "1.0E+20" and fmt_float(1.5e-7) == "1.5E-07" and fmt_float(0.1) == "0.1" and fmt_float(3.0) == "3.0"
        assert float(fmt_float(1.2345678901234567e-30)) == 1.2345678901234567e-30

    def test_a_name_a_reader_would_refuse_is_made_fit(self):
        d = Document()
        with d.transaction("x") as t:
            t.put_layer(Layer("a/b:c*d", 1))
            t.add(line(0, 0, 1, 1), Style("a/b:c*d"))
        theirs = assert_clean(write_dxf(d))
        assert theirs.modelspace()[0].dxf.layer == "a_b_c_d" and "a_b_c_d" in theirs.layers

    def test_a_long_line_of_text_is_refused_where_it_wont_fit(self):
        d = Document()
        with d.transaction("x") as t:
            t.add(Text(Vec(0.0, 0.0), 1.0, "x" * 300))
        with pytest.raises(SaveError, match="longer than"):
            write_dxf(d, "AC1015")
        assert_clean(write_dxf(d, "AC1027"))

    def test_long_paragraph_text_isnt_cut_in_the_middle_of_an_escape(self):
        d = Document()
        words = ("日" * 50 + "^" * 50) * 30
        with d.transaction("x") as t:
            t.add(MText(Vec(0.0, 0.0), 1.0, words))
        back, _ = read_strict(write_dxf(d, "AC1015"))
        assert of_type(back, MText)[0].shape.text == words
        back, _ = read_strict(write_dxf(d, "AC1027"))
        assert of_type(back, MText)[0].shape.text == words

    def test_nothing_at_all(self):
        data = write_dxf(Document())
        assert not list(assert_clean(data).modelspace())
        assert not read_strict(data)[0].entities

    def test_it_opens_on_the_drawing(self):
        theirs = assert_clean(write_dxf(a_drawing()))
        v = theirs.viewports.get("*Active")[0]
        box = a_drawing().extents()
        assert box[0] <= v.dxf.center[0] <= box[2] and box[1] <= v.dxf.center[1] <= box[3]
        assert v.dxf.height >= box[3] - box[1]


def all_dimensions() -> Document:
    """One of each kind, and one with its own size and text."""
    doc = Document()
    with doc.transaction("x") as t:
        t.add(Dimension("linear", (Vec(0.0, 0.0), Vec(100.0, 0.0)), Vec(50.0, 20.0)))
        t.add(Dimension("linear", (Vec(100.0, 0.0), Vec(100.0, 40.0)), Vec(120.0, 20.0), math.pi / 2.0))
        t.add(Dimension("aligned", (Vec(0.0, 0.0), Vec(30.0, 40.0)), Vec(-10.0, 30.0), scale=2.0))
        t.add(Dimension("radius", (Vec(200.0, 0.0), Vec(208.0, 6.0)), Vec(224.0, 18.0)))
        t.add(Dimension("diameter", (Vec(300.0, 0.0), Vec(306.0, 8.0)), Vec(303.0, 4.0), text="<> TYP"))
        t.add(Dimension("angular", (Vec(0.0, 0.0), Vec(10.0, 0.0), Vec(0.0, 0.0), Vec(0.0, 10.0)), Vec(20.0, 20.0)))
        t.add(Dimension("angular", (Vec(500.0, 0.0), Vec(510.0, 0.0), Vec(500.0, 10.0)), Vec(505.0, 5.0)))
    return doc


def same_drawing(a: Document, b: Document, eps: float = 1e-9) -> bool:
    """Do two drawings' dimensions draw the same, piece for piece?"""
    from draftworx.core import dim as dims
    for x, y in zip(of_type(a, Dimension), of_type(b, Dimension)):
        p, q = dims.drawn(x.shape, a.dim_style_of(x.shape)), dims.drawn(replace(y.shape, block=""), b.dim_style_of(y.shape))
        if len(p) != len(q):
            return False
        for g, h in zip(p, q):
            if type(g) is not type(h):
                return False
            if type(g) is Text:
                if g.text != h.text or not close(g.p, h.p, eps) or abs(g.rotation - h.rotation) > eps:
                    return False
            elif not all(abs(u - v) <= eps for u, v in zip(g.bbox(), h.bbox())):
                return False
    return True


class TestDimensions:
    @pytest.mark.parametrize("version", ["AC1009", "AC1015", "AC1027"])
    def test_they_go_out_as_dimensions_and_come_back_the_same(self, version):
        doc = all_dimensions()
        data, report = encode_dxf(doc, version)
        assert not report.converted and not report.lost
        check_written(data, doc, report)
        theirs = assert_clean(data)
        from draftworx.core import dim as dims
        written = list(theirs.modelspace().query("DIMENSION"))
        assert len(written) == 7
        for d, e in zip(written, of_type(doc, Dimension)):
            m = d.get_measurement()
            want = dims.measurement(e.shape)
            assert (math.radians(m) if e.shape.measure == "angular" else m) == pytest.approx(want, abs=1e-9)
            assert d.dxf.geometry in theirs.blocks
        back, _ = read_strict(data)
        assert [e.shape.measure for e in of_type(back, Dimension)] == [e.shape.measure for e in of_type(doc, Dimension)]
        assert all(e.shape.block for e in of_type(back, Dimension))
        assert same_drawing(doc, back)
        assert write_dxf(back, version) == data

    def test_its_own_size_goes_as_an_override_of_its_style(self):
        data = write_dxf(all_dimensions())
        theirs = assert_clean(data)
        sizes = [d.override().get("dimscale") for d in theirs.modelspace().query("DIMENSION")]
        assert sizes[2] == 2.0
        back, _ = read_strict(data)
        assert of_type(back, Dimension)[2].shape.scale == 2.0

    def test_symbols_go_as_a_file_spells_them(self):
        data = write_dxf(all_dimensions())
        assert b"%%c" in data and b"%%d" in data
        back, _ = read_strict(data)
        from draftworx.core import dim as dims
        assert dims.label(of_type(back, Dimension)[4].shape, back.dim_style_of(of_type(back, Dimension)[4].shape)) == "Ø20 TYP"

    def test_one_from_a_file_that_was_changed_gets_a_block_of_its_own(self):
        _, mine = by_kind()
        dim = of_type(mine, Dimension)[0]
        old = dim.shape.block
        with mine.transaction("Move") as t:
            t.replace(dim.id, dim.shape.transformed(Affine.translation(0.0, 5.0)))
        data = write_dxf(mine)
        theirs = assert_clean(data)
        written = [d for d in theirs.modelspace().query("DIMENSION")]
        assert len(written) == 1 and written[0].dxf.geometry != old
        assert written[0].dxf.handle == dim.source.text.split("\n")[3].strip()      # the same object, with the same handle
        drawn = theirs.blocks[written[0].dxf.geometry]
        assert {e.dxftype() for e in drawn} >= {"LINE", "SOLID", "TEXT"}
        assert old in theirs.blocks
        check_written(data, mine)

    def test_one_from_a_file_that_wasnt_changed_is_written_as_it_was(self):
        source = encoded(everything("R2013"))
        mine, _ = read_strict(source)
        with mine.transaction("x") as t:
            t.add(line(0, 0, 1, 1))
        changed = changed_lines(source, write_dxf(mine))
        assert not any("DIMENSION" in c for c in changed)


class TestAnotherVersion:
    def test_r12_writes_what_r12_has_and_says_so(self):
        data, report = encode_dxf(a_drawing(), "AC1009")
        theirs = assert_clean(data)
        assert theirs.dxfversion == "AC1009"
        m = theirs.modelspace()
        assert not m.query("ELLIPSE") and not m.query("SPLINE") and not m.query("LWPOLYLINE") and not m.query("MTEXT")
        assert len(m.query("POLYLINE")) == 6
        assert any("ellipses" in s for s in report.sentences()) and any("paragraph" in s for s in report.sentences())
        assert "RED_PART" in theirs.layers and m.query("LINE")[0].dxf.layer == "RED_PART"
        ellipse = EllipseArc(Vec(150.0, 50.0), Vec(40.0, 10.0), 0.4, 0.0, TAU)
        flat = m.query("POLYLINE")[0]
        assert flat.is_closed
        for v in flat.vertices:
            p = v.dxf.location
            assert close(ellipse.project((p.x, p.y))[1], p, 1e-6)
        read_strict(data)
        check_written(data, a_drawing(), report)

    def test_a_file_from_one_version_can_be_written_as_another(self):
        doc, _ = read_strict(encoded(everything("R2018")))
        data, report = encode_dxf(doc, "AC1015")
        theirs = assert_clean(data)
        assert theirs.dxfversion == "AC1015"
        m = theirs.modelspace()
        assert m.query("LINE") and m.query("LWPOLYLINE") and m.query("SPLINE") and m.query("MTEXT")
        # a dimension goes as itself, drawn by the block it came with
        dims_written = m.query("DIMENSION")
        assert dims_written and all(d.dxf.geometry in theirs.blocks for d in dims_written)
        assert not any("dimension" in s for s in report.sentences())
        assert any("xline" in s or "ray" in s for s in report.sentences())
        check_written(data, doc, report)

    def test_r12_from_a_modern_file_explodes_what_it_must(self):
        doc, _ = read_strict(encoded(everything("R2018")))
        data, report = encode_dxf(doc, "AC1009")
        theirs = assert_clean(data)
        assert theirs.dxfversion == "AC1009" and report.converted
        check_written(data, doc, report)

    def test_an_edit_to_an_r12_file_writes_r12(self):
        data = encoded(everything("R12"))
        doc, _ = read_strict(data)
        with doc.transaction("x") as t:
            t.add(EllipseArc(Vec(0.0, 0.0), Vec(5.0, 0.0), 0.5, 0.0, TAU))
            t.add(MText(Vec(1.0, 1.0), 1.0, "two\\Plines"))
            t.add(Polyline((Vec(0.0, 0.0), Vec(1.0, 1.0)), (0.5, 0.0)))
        back, report = encode_dxf(doc)
        theirs = assert_clean(back)
        assert theirs.dxfversion == "AC1009" and report.converted
        check_written(back, doc, report)

    def test_an_edit_to_an_r13_file_writes_what_r13_has(self):
        data = (DATA / "small_r13.dxf").read_bytes()
        doc, _ = read_strict(data)
        assert doc.extras["dxf"]["version"] == "AC1012"
        with doc.transaction("x") as t:
            t.add(Polyline((Vec(0.0, 0.0), Vec(1.0, 1.0), Vec(2.0, 0.0)), (0.5, 0.0, 0.0), True))
        back, report = encode_dxf(doc)
        assert not report.converted
        theirs, _ = parsed(back)
        assert theirs.modelspace().query("POLYLINE") and not theirs.modelspace().query("LWPOLYLINE")
        again, _ = read_strict(back)
        assert of_type(again, Polyline)[-1].shape.bulges[0] == 0.5


# ------------------------------------------------------------------------------------------------- the check, and files that are wrong


class TestTheCheckBeforeSaving:
    def test_it_passes_good_work(self):
        d = a_drawing()
        data, report = encode_dxf(d)
        check_written(data, d, report)
        again, _ = read_strict(data)
        data2, report2 = encode_dxf(again)
        check_written(data2, again, report2)

    def test_it_catches_a_missing_object(self):
        d = a_drawing()
        data, report = encode_dxf(d)
        fewer = data.replace(b"CIRCLE\r\n", b"BROKEN\r\n", 1)
        with pytest.raises(ValueError, match="came back"):
            check_written(fewer, d, report)

    def test_it_catches_a_wrong_number(self):
        d = Document()
        with d.transaction("x") as t:
            t.add(line(0, 0, 100, 0))
            t.add(line(0, 0, 100, 1))
        data, report = encode_dxf(d)
        wrong = data.replace(b"100.0\r\n", b"100.5\r\n")
        with pytest.raises(ValueError, match="came back at"):
            check_written(wrong, d, report)

    def test_it_catches_a_wrong_block(self):
        d = a_drawing()
        data, report = encode_dxf(d)
        with pytest.raises(ValueError):
            check_written(data.replace(b"Bolt", b"Nut!"), d, report)

    def test_a_save_that_fails_it_leaves_the_file_alone(self, tmp_path, monkeypatch):
        path = tmp_path / "a.dxf"
        d = a_drawing()
        save_drawing(Opened(d), path)
        before = path.read_bytes()
        import draftworx.core.dxf.write as write_module
        monkeypatch.setattr(write_module.WriteReport, "sentences", lambda self: [])
        opened = open_drawing(path)
        with opened.doc.transaction("x") as t:
            t.add(line(5, 5, 6, 6))
        real = write_module._Writer.write
        monkeypatch.setattr(write_module._Writer, "write", lambda self: real(self).replace(b"LINE\r\n", b"NOPE\r\n", 1))
        with pytest.raises(SaveError):
            save_drawing(opened, path)
        assert path.read_bytes() == before and opened.doc.dirty


class TestFiles:
    def test_save_and_open(self, tmp_path):
        path = tmp_path / "a.dxf"
        d = a_drawing()
        o = Opened(d)
        save_drawing(o, path)
        assert o.path == path and not d.dirty
        back = open_drawing(path)
        assert len(back.doc.entities) == len(d.entities) and not back.doc.dirty and not back.doc.can_undo
        save_drawing(back, path)
        assert read_strict(path.read_bytes())[0].entities

    def test_an_export_says_what_it_had_to_do_and_leaves_the_drawing_alone(self, tmp_path):
        d = a_drawing()
        notes = export_dxf(d, tmp_path / "a.dxf", "AC1009")
        assert any("simpler form" in n for n in notes)
        assert d.dirty and assert_clean((tmp_path / "a.dxf").read_bytes()).dxfversion == "AC1009"
        assert export_dxf(d, tmp_path / "b.dxf", "AC1015") == []
        assert assert_clean((tmp_path / "b.dxf").read_bytes()).dxfversion == "AC1015"

    def test_opening_tells_the_person(self, tmp_path):
        path = tmp_path / "a.dxf"
        path.write_bytes(encoded(everything("R2013")))
        assert any("can't be changed" in n for n in open_drawing(path).notices)

    def test_a_save_of_an_opened_file_changes_only_the_change(self, tmp_path):
        path = tmp_path / "a.dxf"
        data = encoded(everything("R2013"))
        path.write_bytes(data)
        o = open_drawing(path)
        save_drawing(o, path)
        assert path.read_bytes() == data

    def test_it_wont_overwrite_a_file_someone_else_changed(self, tmp_path):
        from draftworx.core.safefile import ChangedOnDisk
        path = tmp_path / "a.dxf"
        path.write_bytes(encoded(everything("R12")))
        o = open_drawing(path)
        path.write_bytes(encoded(everything("R12")) + b"\n")
        with pytest.raises(ChangedOnDisk):
            save_drawing(o, path)

    def test_a_dxf_saved_as_native_and_back(self, tmp_path):
        data = encoded(everything("R2013"))
        (tmp_path / "a.dxf").write_bytes(data)
        o = open_drawing(tmp_path / "a.dxf")
        save_drawing(o, tmp_path / "a.draftworx")
        o2 = open_drawing(tmp_path / "a.draftworx")
        save_drawing(o2, tmp_path / "b.dxf")
        assert (tmp_path / "b.dxf").read_bytes() == data

    def test_recovering_a_dxf_keeps_what_it_had(self, tmp_path):
        from draftworx.core import recovery
        doc, _ = read_strict(encoded(everything("R2018")))
        with doc.transaction("x") as t:
            t.add(line(0, 0, 1, 1))
        assert recovery.autosave(doc, "s", tmp_path / "a.dxf")
        back = loads((recovery._folder() / f"{recovery._key('s')}.draftworx").read_bytes())
        assert back.extras["dxf"]["version"] == "AC1032"
        assert_clean(write_dxf(back))


class TestDamagedFiles:
    def test_nothing_there(self):
        for data in (b"", b"\n", b"hello", b"0\n", b"  0\nEOF\n", b"\x00\x01\x02\x03" * 100, "ñandú\n".encode("utf-8")):
            with pytest.raises(FormatError):
                read_strict(data)

    def test_a_section_with_no_name(self):
        with pytest.raises(FormatError, match="no name"):
            read_strict(b"  0\nSECTION\n  0\nENDSEC\n  0\nEOF\n")

    def test_two_of_a_section(self):
        s = b"  0\nSECTION\n  2\nENTITIES\n  0\nENDSEC\n"
        with pytest.raises(FormatError, match="two ENTITIES"):
            read_strict(s + s + b"  0\nEOF\n")

    def test_cut_off_in_a_section_is_refused(self):
        data = encoded(everything("R2013"))
        for cut in (len(data) // 3, len(data) // 2, len(data) - 40):
            with pytest.raises(FormatError):
                read_strict(data[:cut])

    def test_no_end_marker_after_a_whole_section_is_said(self):
        data = b"  0\nSECTION\n  2\nENTITIES\n  0\nLINE\n  8\n0\n 10\n0\n 20\n0\n 11\n1\n 21\n1\n  0\nENDSEC\n"
        doc, notes = read_strict(data)
        assert len(doc.entities) == 1 and any("end marker" in n for n in notes)
        assert write_dxf(doc) == data

    def test_a_bad_group_code_says_where(self):
        data = b"  0\nSECTION\n  2\nENTITIES\n  0\nLINE\n  x\n0\n  0\nENDSEC\n  0\nEOF\n"
        with pytest.raises(FormatError, match="line 7"):
            read_strict(data)

    def test_a_block_that_contains_itself(self):
        body = (b"  0\nSECTION\n  2\nBLOCKS\n  0\nBLOCK\n  8\n0\n  2\nA\n 70\n0\n 10\n0\n 20\n0\n  0\nINSERT\n  8\n0\n  2\nB\n 10\n0\n 20\n0\n  0\nENDBLK\n  8\n0\n"
                b"  0\nBLOCK\n  8\n0\n  2\nB\n 70\n0\n 10\n0\n 20\n0\n  0\nINSERT\n  8\n0\n  2\nA\n 10\n0\n 20\n0\n  0\nENDBLK\n  8\n0\n  0\nENDSEC\n  0\nEOF\n")
        with pytest.raises(FormatError, match="contains itself"):
            read_strict(body)

    def test_a_block_with_no_end(self):
        data = b"  0\nSECTION\n  2\nBLOCKS\n  0\nBLOCK\n  8\n0\n  2\nA\n 70\n0\n 10\n0\n 20\n0\n  0\nLINE\n  8\n0\n 10\n0\n 20\n0\n 11\n1\n 21\n1\n  0\nENDSEC\n  0\nEOF\n"
        doc, _ = read_strict(data)
        assert len(doc.blocks["A"].entities) == 1
        assert write_dxf(doc) == data

    @pytest.mark.parametrize("value", [b"nan", b"inf", b"-inf", b"1e999", b"1e308", b"abc", b"", b"0x10"])
    def test_numbers_that_arent(self, value):
        data = b"  0\nSECTION\n  2\nENTITIES\n  0\nCIRCLE\n  8\n0\n 10\n" + value + b"\n 20\n" + value + b"\n 40\n" + value + b"\n  0\nLINE\n  8\n0\n 10\n" + value + b"\n 20\n0\n 11\n1\n 21\n1\n  0\nENDSEC\n  0\nEOF\n"
        doc, _ = read_strict(data)
        assert write_dxf(doc) == data
        assert doc.extents() is None or all(math.isfinite(v) for v in doc.extents())

    def test_a_scale_of_nothing(self):
        doc = ezdxf.new("R2013")
        b = doc.blocks.new("B")
        b.add_ellipse((0, 0), (1, 0), 0.5)
        b.add_circle((0, 0), 1)
        doc.modelspace().add_blockref("B", (1, 1), dxfattribs={"xscale": 0, "yscale": 0})
        mine, _ = read_strict(encoded(doc))
        list(curves_of(mine, of_type(mine, Insert)[0]))
        pick(mine, Vec(1.0, 1.0), 1.0)
        SnapEngine(mine).pick(SnapQuery(Vec(1.0, 1.0), 1.0))

    def test_the_read_turns_a_surprise_into_a_message(self, monkeypatch):
        import draftworx.core.dxf.read as read_module
        monkeypatch.setattr(read_module, "decode", lambda data: 1 / 0)
        with pytest.raises(FormatError, match="couldn't be read"):
            read_dxf(b"  0\nEOF\n")

    @given(st.integers(0, 10_000_000), st.integers(1, 40))
    @settings(max_examples=150)
    def test_damage_anywhere_is_a_message_or_comes_back_the_same(self, seed, hits):
        r = random.Random(seed)
        original = _FUZZ_BASE
        data = bytearray(original)
        for _ in range(hits):
            at = r.randrange(len(data))
            how = r.randrange(4)
            if how == 0:
                data[at] = r.randrange(32, 127)
            elif how == 1:
                data[at] = r.randrange(0, 256)
            elif how == 2:
                del data[at:at + r.randrange(1, 30)]
            else:
                data[at:at] = bytes(r.randrange(0, 256) for _ in range(r.randrange(1, 12)))
        data = bytes(data)
        try:
            doc, _ = read_strict(data)
        except FormatError:
            return
        back = write_dxf(doc)
        # the line ends are normalised to the file's own, and that's the one thing that may differ
        assert back.replace(b"\r\n", b"\n").replace(b"\r", b"\n") == data.replace(b"\r\n", b"\n").replace(b"\r", b"\n") or data.startswith(b"\xef\xbb\xbf")
        for e in doc.sorted_entities():
            doc.shape_bbox(e.shape)
            list(curves_of(doc, e))
        dumps(doc)

    @given(st.integers(0, 10_000_000))
    @settings(max_examples=100)
    def test_numbers_changed_to_anything_are_survived(self, seed):
        r = random.Random(seed)
        lines = _FUZZ_BASE.decode("utf-8", "surrogateescape").split("\n")
        for _ in range(30):
            i = r.randrange(1, len(lines) // 2) * 2 + 1
            lines[i] = r.choice(["0", "-1", "1e300", "-1e300", "1e-300", "nan", "inf", "999999999999999999999", "x", "", "1.5", "32767", "-32768"])
        data = "\n".join(lines).encode("utf-8", "surrogateescape")
        try:
            doc, _ = read_strict(data)
        except FormatError:
            return
        assert write_dxf(doc) == data
        for e in doc.sorted_entities():
            box = doc.shape_bbox(e.shape)
            assert box is None or all(math.isfinite(v) for v in box)
        engine = SnapEngine(doc)
        engine.pick(SnapQuery(Vec(1.0, 1.0), 1.0))
        pick(doc, Vec(1.0, 1.0), 1.0)


_FUZZ_BASE = encoded(everything("R2013"))


class TestSmallPieces:
    def test_changing_tags_leaves_the_rest(self):
        text = "  0\nLINE\n  5\n2A\n100\nAcDbEntity\n  8\nOld\n 62\n1\n100\nAcDbLine\n 62\n99\n 10\n1.0\n1001\nAPP\n1070\n8"
        out = retag(text, {8: "New", 62: None, 6: "Dash"})
        assert out.split("\n") == ["  0", "LINE", "  5", "2A", "100", "AcDbEntity", "  8", "New", "  6", "Dash", "100", "AcDbLine", " 62", "99", " 10", "1.0", "1001", "APP", "1070", "8"]

    def test_changing_tags_of_an_object_and_what_it_owns(self):
        text = "  0\nPOLYLINE\n  8\nA\n  0\nVERTEX\n  8\nA\n 10\n1\n  0\nSEQEND\n  8\nA"
        assert retag(text, {8: "B"}).count("A") == 0

    def test_a_spline_through_points(self):
        pts = [Vec(0.0, 0.0), Vec(1.0, 2.0), Vec(4.0, 1.0), Vec(6.0, 5.0)]
        s = spline_through(pts)
        for p in pts:
            assert close(s.project(p)[1], p, 1e-9)
        assert close(s.start, pts[0]) and close(s.end, pts[-1])
        two = spline_through(pts[:2])
        assert close(two.point_at(0.5), pts[0].midpoint(pts[1]), 1e-9)
        with pytest.raises(ValueError):
            spline_through([Vec(1.0, 1.0), Vec(1.0, 1.0)])

    def test_a_spline_through_points_takes_given_tangents(self):
        pts = [Vec(0.0, 0.0), Vec(5.0, 0.0), Vec(10.0, 0.0)]
        s = spline_through(pts, Vec(0.0, 3.0), Vec(0.0, -2.0))
        t = s.tangent_at(0.0)
        assert t[0] == pytest.approx(0.0, abs=1e-9) and t[1] > 0.0
        t = s.tangent_at(1.0)
        assert t[0] == pytest.approx(0.0, abs=1e-9) and t[1] < 0.0

    @given(st.lists(st.tuples(st.floats(-1000, 1000), st.floats(-1000, 1000)), min_size=2, max_size=20))
    @settings(max_examples=100)
    def test_a_spline_through_any_points_goes_through_them(self, pts):
        points = [Vec(x, y) for x, y in pts]
        kept = [p for i, p in enumerate(points) if i == 0 or p.distance(points[i - 1]) > 1e-3]
        if len(kept) < 2:
            return
        s = spline_through(kept)
        for p in kept:
            assert s.project(p)[1].distance(p) < 1e-4 * max(1.0, max(abs(v) for q in kept for v in q))
