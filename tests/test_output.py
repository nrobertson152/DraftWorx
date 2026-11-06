# SPDX-License-Identifier: GPL-3.0-or-later
"""What a drawing looks like on paper: its sheet and scale, templates, PDFs and printing, and cleaning it up before it goes out.

Scale is checked three ways: by the arithmetic of what's written in the PDF, by a PDF reader that isn't ours (pypdf), and by rendering the
page with PDFium at a known resolution and measuring the ink."""
import io
import json
import math
import re
import zlib
from dataclasses import replace

import pytest

from draftworx.cli import main as cli
from draftworx.core import cleanup, sheets
from draftworx.core.curves import Line, Polyline
from draftworx.core.dxf.read import read_dxf
from draftworx.core.dxf.write import write_dxf
from draftworx.core.model import BlockDef, Document, Entity, Layer, Linetype, Settings, Style
from draftworx.core.native import dumps, loads
from draftworx.core.pdf import PT, pdf_of
from draftworx.core.printing import on_white, paper_dashes
from draftworx.core.shapes import Dimension, Hatch, Insert, Text
from draftworx.core.vec import Vec
from driver import Person, line


def V(x, y):
    return Vec(float(x), float(y))


def rect(x0, y0, x1, y1):
    return Polyline((V(x0, y0), V(x1, y0), V(x1, y1), V(x0, y1)), closed=True)


def plan_drawing():
    """A 10 m by 6 m room in millimetres, with a metre-long line in it, on A3 across at 1:50."""
    doc = Document()
    with doc.transaction("set up") as t:
        t.add(rect(0, 0, 10000, 6000))
        t.add(line(1000, 1000, 2000, 1000))
        sheets.apply(t, sheets.fitted(doc, "A3", True))
    return doc


def content(data: bytes) -> str:
    """The page's drawing, as a PDF says it."""
    start = data.index(b"stream\n") + len(b"stream\n")
    return zlib.decompress(data[start:data.index(b"\nendstream", start)]).decode("latin-1")


def strokes(text: str) -> list[list[tuple[float, float]]]:
    """Each stroked path's points, in points on the page, for paths of straight pieces."""
    out, path = [], []
    for row in text.split("\n"):
        parts = row.split()
        if parts and parts[-1] == "m":
            path = [(float(parts[0]), float(parts[1]))]
        elif parts and parts[-1] == "l":
            path.append((float(parts[0]), float(parts[1])))
        elif parts == ["S"]:
            out.append(path)
            path = []
    return out


class TestScales:
    def test_written_as_people_write_them(self):
        assert sheets.parse_scale("1:50") == 50 and sheets.parse_scale(" 1 : 100 ") == 100 and sheets.parse_scale("50") == 50
        assert sheets.parse_scale("2:1") == 0.5 and sheets.parse_scale('1/4"=1\'') == 48 and sheets.parse_scale("1/8\" = 1'-0\"") == 96
        assert sheets.parse_scale("banana") is None and sheets.parse_scale("0:1") is None and sheets.parse_scale("-5") is None
        assert [sheets.scale_words(n) for n in (50, 1, 0.5, 2.5)] == ["1:50", "1:1", "2:1", "1:2.5"]

    def test_papers_by_their_names(self):
        assert sheets.paper_named("a3") == "A3" and sheets.paper_named("letter") == "Letter" and sheets.paper_named("ANSI B") == "Tabloid"
        assert sheets.paper_named("ansi d") == "ANSI D" and sheets.paper_named("B5") is None
        assert sheets.size_of("A3", True) == (420.0, 297.0) and sheets.size_of("A3", False) == (297.0, 420.0)

    def test_the_largest_standard_scale_that_fits(self):
        # 10 m by 6 m on A3 across, 20 mm all round: 380 by 257 mm of room needs 1:26.3, and the next one up is 1:50
        assert sheets.fitting_scale((0, 0, 10000, 6000), 420, 297, "mm") == 50
        assert sheets.fitting_scale((0, 0, 3000, 2000), 420, 297, "mm") == 10
        # 40 ft by 30 ft in inches on Tabloid needs 1:38.2, and architects' next is 1/4" = 1'-0"
        assert sheets.fitting_scale((0, 0, 480, 360), 431.8, 279.4, "in") == 48
        # in metres, a 10 m room still fits at 1:50
        assert sheets.fitting_scale((0, 0, 10, 6), 420, 297, "m") == 50


class TestSheet:
    def test_it_is_the_paper_times_the_scale(self):
        doc = Document()
        with doc.transaction("x") as t:
            t.set_settings(Settings(paper="A3", annotation_scale=50.0, sheet_x=100.0, sheet_y=200.0))
        s = sheets.sheet_of(doc)
        assert s.box() == (100.0, 200.0, 100.0 + 21000.0, 200.0 + 14850.0) and s.to_paper(V(100 + 50, 200 + 100)) == (1.0, 2.0)
        with doc.transaction("x") as t:
            t.set_settings(Settings(units="in", paper="Letter", landscape=False, annotation_scale=48.0))
        s = sheets.sheet_of(doc)
        assert s.box()[2] == pytest.approx(8.5 * 48) and s.box()[3] == pytest.approx(11 * 48)

    def test_fitted_round_what_is_drawn_and_not_its_own_border(self):
        doc = plan_drawing()
        st = doc.settings
        box = sheets.sheet_of(doc).box()
        assert st.paper == "A3" and st.landscape and st.annotation_scale == 50
        assert ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) == (5000.0, 3000.0)
        with doc.transaction("x") as t:
            sheets.place_frame(t, sheets.sheet_of(doc), True)
        again = sheets.fitted(doc, "A3", True)
        assert again.annotation_scale == 50 and (again.sheet_x, again.sheet_y) == (st.sheet_x, st.sheet_y)

    def test_with_nothing_drawn_it_keeps_its_scale_and_place(self):
        doc = Document()
        with doc.transaction("x") as t:
            t.set_settings(Settings(paper="A3", annotation_scale=20.0, sheet_x=500.0, sheet_y=500.0))
        st = sheets.fitted(doc, "A1", True)
        assert st.annotation_scale == 20 and st.sheet_x + 841 * 20 / 2 == 500 + 420 * 20 / 2

    def test_saved_and_opened(self):
        doc = plan_drawing()
        back = loads(dumps(doc))
        assert back.settings == doc.settings and back.fingerprint() == doc.fingerprint()
        # an older DraftWorx would drop the sheet without a word, so a drawing with one is a version it refuses
        assert b'"version":4' in dumps(doc).split(b"\n")[0]
        # and one without writes nothing about it, so it's the version it was
        plain = Document()
        with plain.transaction("x") as t:
            t.add(line(0, 0, 10, 0))
        assert b"paper" not in dumps(plain) and b'"version":1' in dumps(plain) and loads(dumps(plain)).settings.paper == ""

    def test_an_older_file_opens_without_a_sheet(self):
        old = (b'{"format":"DraftWorx drawing","version":3,"program":"","next_id":2,"settings":{"units":"mm","annotation_scale":50.0},'
               b'"current":{},"layers":[],"linetypes":[],"text_styles":[],"dim_styles":[],"blocks":[],'
               b'"entities":[{"id":1,"type":"line","a":[0.0,0.0],"b":[1.0,0.0]}],"extras":{}}')
        doc = loads(old)
        assert sheets.sheet_of(doc) is None and doc.settings.annotation_scale == 50

    @pytest.mark.parametrize("version", [None, "AC1009"])
    def test_a_dxf_brings_its_sheet_back_by_its_border(self, version):
        doc = sheets.from_template("ISO A3 across")
        with doc.transaction("x") as t:
            t.add(rect(0, 0, 10000, 6000))
            sheets.apply(t, sheets.fitted(doc, "A3", True, 50.0))
        data = write_dxf(doc, version)
        back, _ = read_dxf(data)
        assert sheets.sheet_of(back) is None        # DXF has nowhere to say it
        st = sheets.first_sheet(back)
        assert (st.paper, st.landscape, st.annotation_scale, st.sheet_x, st.sheet_y) == ("A3", True, 50.0, doc.settings.sheet_x, doc.settings.sheet_y)
        with back.transaction("Sheet") as t:
            sheets.apply(t, st)
        assert write_dxf(back) == data              # and finding it changes nothing in the file

    def test_a_change_of_scale_keeps_line_types_their_size_on_paper(self):
        doc = plan_drawing()
        with doc.transaction("x") as t:
            sheets.apply(t, sheets.fitted(doc, "A3", True, 100.0))
        # it started at 1:1 with a line type scale of 1, and its sheet at 1:50 made that 50
        assert doc.settings.ltscale == 100 and doc.settings.annotation_scale == 100


class TestPdf:
    def test_a_metre_at_1_to_50_is_20_millimetres(self):
        doc = plan_drawing()
        data, notes = pdf_of(doc, sheets.sheet_of(doc))
        assert data.startswith(b"%PDF-1.4") and notes == []
        assert b"/MediaBox [0 0 1190.5512 841.8898]" in data          # 420 by 297 mm
        paths = strokes(content(data))
        (metre,) = [p for p in paths if len(p) == 2]
        (x0, y0), (x1, y1) = metre
        assert math.hypot(x1 - x0, y1 - y0) == pytest.approx(20 * PT, abs=2e-4)
        # and where it is: 1000 mm from the room's corner is 20 mm, the room being centred on the paper
        (room,) = [p for p in paths if len(p) == 4]
        assert (x0 - room[0][0], y0 - room[0][1]) == pytest.approx((20 * PT, 20 * PT), abs=2e-4)
        assert room[0] == pytest.approx(((420 - 200) / 2 * PT, (297 - 120) / 2 * PT), abs=2e-4)

    def test_another_reader_agrees(self):
        pypdf = pytest.importorskip("pypdf")
        doc = plan_drawing()
        with doc.transaction("x") as t:
            t.add(Text(V(500, 500), 250.0, "ROOM 1 Ø25 ±0.1 45°"))
        data, _ = pdf_of(doc, sheets.sheet_of(doc), title="Plan")
        reader = pypdf.PdfReader(io.BytesIO(data), strict=True)
        (page,) = reader.pages
        assert float(page.mediabox.width) / PT == pytest.approx(420, abs=1e-3)
        assert float(page.mediabox.height) / PT == pytest.approx(297, abs=1e-3)
        assert "ROOM 1 Ø25 ±0.1 45°" in page.extract_text()
        assert reader.metadata.title == "Plan"

    def test_rendered_the_metre_measures_20_millimetres(self):
        pdfium = pytest.importorskip("pypdfium2")
        doc = Document()
        with doc.transaction("x") as t:
            t.add(line(0, 0, 1000, 0), Style(weight=50))
            t.set_settings(Settings(paper="A4", annotation_scale=50.0, sheet_x=-2000.0, sheet_y=-5000.0))
        data, _ = pdf_of(doc, sheets.sheet_of(doc))
        page = pdfium.PdfDocument(data)[0]
        image = page.render(scale=10 / PT).to_pil().convert("L")      # 10 pixels to the millimetre
        w, h = image.size
        assert abs(w - 2970) <= 1 and abs(h - 2100) <= 1        # A4 across, give or take PDFium rounding the last pixel up
        row = round(h - 100 * 10)       # 5000 mm up from the sheet's corner is 100 mm on paper, and an image counts down
        ink = [x for x in range(w) if image.getpixel((x, row)) < 128]
        assert ink and (ink[-1] + 1 - ink[0]) / 10 == pytest.approx(20.0, abs=0.15)
        assert ink[0] / 10 == pytest.approx(40.0, abs=0.15)

    def test_the_same_drawing_gives_the_same_bytes(self):
        doc = plan_drawing()
        assert pdf_of(doc, sheets.sheet_of(doc)) == pdf_of(loads(dumps(doc)), sheets.sheet_of(doc))

    def test_what_doesnt_print_doesnt(self):
        doc = plan_drawing()
        with doc.transaction("x") as t:
            t.put_layer(Layer("Notes", plot=False))
            t.put_layer(Layer("Off", on=False))
            t.add(line(100, 100, 300, 100), Style("Notes"))
            t.add(line(100, 200, 300, 200), Style("Off"))
            t.add(line(30000, 0, 31000, 0))     # off the sheet
        assert len(strokes(content(pdf_of(doc, sheets.sheet_of(doc))[0]))) == 2

    def test_weights_and_line_types_are_millimetres_on_paper(self):
        doc = plan_drawing()
        with doc.transaction("x") as t:
            t.put_linetype(Linetype("HIDDEN", "Hidden", (6.35, -3.175)))
            t.put_layer(Layer("Hidden", linetype="HIDDEN", weight=50))
            t.add(line(0, 3000, 10000, 3000), Style("Hidden"))
            t.set_settings(replace(doc.settings, ltscale=50.0))
        text = content(pdf_of(doc, sheets.sheet_of(doc))[0])
        assert "1.4173 w" in text and "[18 9] 0 d" in text            # 0.5 mm, and the line type at 1:50 with a scale of 50
        assert "0.7087 w" in text                                       # anything else: 0.25 mm, as most programs print it

    def test_line_types_dots_and_odd_patterns(self):
        assert paper_dashes((12.7, -6.35, 0.0, -6.35), 1.0, 0.25) == (12.7, 6.35, 0.25, 6.35)
        assert paper_dashes((-2.0, 5.0), 1.0, 0.25) == (0.0, 2.0, 5.0, 0.0)
        assert paper_dashes((5.0, 3.0, -2.0), 2.0, 0.25) == (16.0, 4.0)
        assert paper_dashes((5.0,), 1.0, 0.25) == () and paper_dashes((), 1.0, 0.25) == ()

    def test_colours_print_on_white_unless_its_all_black(self):
        doc = plan_drawing()
        with doc.transaction("x") as t:
            t.put_layer(Layer("Yellow", 2))
            t.put_layer(Layer("Red", 1))
            t.add(line(0, 100, 100, 100), Style("Yellow"))
            t.add(line(0, 200, 100, 200), Style("Red"))
        text = content(pdf_of(doc, sheets.sheet_of(doc))[0])
        assert "1 0 0 RG" in text and "0 0 0 RG" in text and "1 1 0 RG" not in text
        assert on_white(0xFFFF00) != 0xFFFF00 and on_white(0xFF0000) == 0xFF0000
        black = content(pdf_of(doc, sheets.sheet_of(doc), black=True)[0])
        assert set(re.findall(r"([\d.]+ [\d.]+ [\d.]+) RG", black)) == {"0 0 0"}

    def test_fills_go_under_lines(self):
        doc = plan_drawing()
        with doc.transaction("x") as t:
            t.add(line(0, 3000, 10000, 3000))
            t.add(Hatch(((rect(1000, 1000, 9000, 5000),),), "SOLID"))
        text = content(pdf_of(doc, sheets.sheet_of(doc))[0])
        assert text.index("\nf") < text.index("\nS")

    def test_text_lands_where_the_drawing_puts_it(self):
        doc = plan_drawing()
        st = doc.settings
        with doc.transaction("x") as t:
            t.add(Text(V(1000, 2000), 250.0, "Left"))
            t.add(Text(V(1000, 4000), 250.0, "Bottom", valign=1))
        text = content(pdf_of(doc, sheets.sheet_of(doc))[0])
        rows = re.findall(r"BT /F1 (\S+) Tf \S+ \S+ \S+ \S+ (\S+) (\S+) Tm \((\w+)\) Tj ET", text)
        found = {word: (float(size), float(x), float(y)) for size, x, y, word in rows}
        # 250 mm at 1:50 is 5 mm, which is Helvetica's capitals at 5 / 0.718 mm
        assert found["Left"][0] == pytest.approx(5 / 0.718 * PT, abs=1e-3)
        assert found["Left"][1:] == pytest.approx(((1000 - st.sheet_x) / 50 * PT, (2000 - st.sheet_y) / 50 * PT), abs=1e-3)
        # bottom-aligned text has its lowest letters at the point, so its baseline is a descender's depth (207/718 of the height) above it
        assert found["Bottom"][2] == pytest.approx((4000 + 250 * 207 / 718 - st.sheet_y) / 50 * PT, abs=1e-3)

    def test_what_is_off_the_sheet_is_said(self):
        doc = plan_drawing()
        with doc.transaction("x") as t:
            t.put_layer(Layer("Construction", plot=False))
            t.add(line(-50000, 0, 50000, 0), Style("Construction"))     # doesn't print, so it doesn't count
        assert pdf_of(doc, sheets.sheet_of(doc))[1] == [] and sheets.fitted(doc, "A3", True).annotation_scale == 50
        with doc.transaction("x") as t:
            t.add(line(0, 0, 30000, 0))
        assert pdf_of(doc, sheets.sheet_of(doc))[1] == [sheets.OFF_SHEET]

    def test_what_helvetica_cant_print_is_said(self):
        doc = plan_drawing()
        with doc.transaction("x") as t:
            t.add(Text(V(1000, 2000), 250.0, "Flow → out"))
        data, notes = pdf_of(doc, sheets.sheet_of(doc))
        assert notes == ["1 character of text can't be printed in Helvetica and show as ?."] and b"Flow ? out" in zlib.decompress(
            data[data.index(b"stream\n") + 7:data.index(b"\nendstream")])

    def test_blocks_print_scaled_and_turned(self):
        doc = plan_drawing()
        with doc.transaction("x") as t:
            t.put_block(BlockDef("Bar", V(0, 0), (Entity(1, line(0, 0, 100, 0)),)))
            t.add(Insert("Bar", V(5000, 5000), 10.0, 10.0, math.pi / 2))
        paths = [p for p in strokes(content(pdf_of(doc, sheets.sheet_of(doc))[0])) if len(p) == 2]
        bar = [p for p in paths if abs(p[0][0] - p[1][0]) < 1e-3]
        (((x0, y0), (x1, y1)),) = bar
        assert abs(y1 - y0) == pytest.approx(1000 / 50 * PT, abs=2e-4)


class TestTemplates:
    def test_a_template_starts_with_a_sheet_and_a_border_to_fill_in(self):
        doc = sheets.from_template("ISO A3 across")
        st = doc.settings
        assert (st.paper, st.landscape, st.units, st.annotation_scale) == ("A3", True, "mm", 1.0)
        frame = doc.entities[sheets.frame_insert(doc)]
        assert frame.style.layer == sheets.FRAME_LAYER and frame.shape.block == "Sheet A3 across"
        fields = {a.tag: a.text.text for a in frame.shape.attribs}
        assert set(fields) == {tag for tag, _ in sheets.FIELDS} and fields["SCALE"] == "1:1"
        assert {"Outlines", "Hidden", "Centre lines", "Dimensions", "Text", "Hatch"} <= {v.name for v in doc.layers}
        assert doc.current.layer == "Outlines" and not doc.dirty and not doc.can_undo

    @pytest.mark.parametrize("name", sorted(sheets.TEMPLATES))
    def test_every_one_fits_its_paper_and_saves(self, name):
        doc = sheets.from_template(name)
        box = sheets.sheet_of(doc).box()
        frame = doc.shape_bbox(doc.entities[sheets.frame_insert(doc)].shape)
        assert box[0] < frame[0] and box[1] < frame[1] and frame[2] < box[2] and frame[3] < box[3]
        assert loads(dumps(doc)).fingerprint() == doc.fingerprint()
        back, _ = read_dxf(write_dxf(doc))
        assert any(type(e.shape) is Insert for e in back.entities.values())

    def test_imperial_line_types_are_the_same_on_paper(self):
        doc = sheets.from_template("Letter across")
        assert doc.settings.units == "in" and doc.linetypes.get("HIDDEN").pattern == pytest.approx((0.25, -0.125))


class TestSheetCommand:
    def room(self):
        return Person(rect(0, 0, 10000, 6000))

    def test_it_starts_round_the_drawing_and_takes_a_paper_and_a_scale(self):
        with self.room() as p:
            p.run("SHEET")
            assert "A3 across, 1:50" in p.s.prompt
            p.type("A1").enter().type("1:100").enter().enter()
            st = p.doc.settings
            assert (st.paper, st.annotation_scale) == ("A1", 100) and p.doc.undo_label == "Sheet"
            box = sheets.sheet_of(p.doc).box()
            assert ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) == (5000, 3000)
            assert "A1 across, at 1:100" in p.s.message

    def test_turning_it_and_clicking_where_it_goes(self):
        with self.room() as p:
            p.run("SHEET").type("T").enter().click(20000, 30000).enter()
            st = p.doc.settings
            assert not st.landscape and st.annotation_scale == 50
            box = sheets.sheet_of(p.doc).box()
            assert ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) == pytest.approx((20000, 30000))
            assert box[2] - box[0] < box[3] - box[1]

    def test_it_shows_the_sheet_before_its_chosen(self):
        with self.room() as p:
            p.run("SHEET").at(100, 100)
            (ghost,) = p.s.ghosts
            x0, y0, x1, y1 = ghost.bbox()
            assert type(ghost) is Polyline and (x1 - x0, y1 - y0) == (21000, 14850) and ((x0 + x1) / 2, (y0 + y1) / 2) == (5000, 3000)
            assert p.doc.settings.paper == ""       # nothing's changed until it's chosen

    def test_a_border_comes_with_it_and_one_undo_takes_both_back(self):
        with self.room() as p:
            before = p.doc.fingerprint()
            p.run("SHEET").type("B").enter().enter()
            frame = p.doc.entities[sheets.frame_insert(p.doc)].shape
            assert frame.sx == 50 and {a.tag: a.text.text for a in frame.attribs}["SCALE"] == "1:50"
            p.doc.undo()
            assert p.doc.fingerprint() == before and p.doc.settings.paper == ""

    def test_the_border_goes_with_a_new_scale_and_keeps_what_was_filled_in(self):
        with self.room() as p:
            p.run("SHEET").type("B").enter().enter()
            i = sheets.frame_insert(p.doc)
            frame = p.doc.entities[i].shape
            filled = tuple(replace(a, text=replace(a.text, text="Pump house")) if a.tag == "TITLE" else a for a in frame.attribs)
            with p.doc.transaction("fill in") as t:
                t.replace(i, replace(frame, attribs=filled))
            p.run("SHEET").type("1:20").enter().enter()
            frame = p.doc.entities[sheets.frame_insert(p.doc)].shape
            fields = {a.tag: a.text.text for a in frame.attribs}
            assert frame.sx == 20 and fields["SCALE"] == "1:20" and fields["TITLE"] == "Pump house"
            box = sheets.sheet_of(p.doc).box()
            assert frame.p == (box[0], box[1])

    def test_a_new_scale_goes_round_what_is_drawn_unless_the_sheet_was_put_somewhere(self):
        p = Person()
        p.s.replace_document(sheets.from_template("ISO A3 across"))       # its sheet starts at 1:1, at the origin
        p.doc = p.s.doc
        with p.doc.transaction("draw") as t:
            t.add(rect(0, 0, 10000, 6000))
        with p:
            p.run("SHEET").type("1:50").enter().enter()
            box = sheets.sheet_of(p.doc).box()
            assert ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) == (5000, 3000)
            p.run("SHEET").click(20000, 20000).type("1:100").enter().enter()
            box = sheets.sheet_of(p.doc).box()
            assert ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2) == pytest.approx((20000, 20000))

    def test_line_types_keep_their_size_on_paper(self):
        with self.room() as p:
            p.run("SHEET").enter()
            assert p.doc.settings.ltscale == 50
            p.run("SHEET").type("1:100").enter().enter()
            assert p.doc.settings.ltscale == 100

    def test_what_isnt_a_paper_or_a_scale_is_said(self):
        with self.room() as p:
            p.run("SHEET").type("banana").enter()
            assert "banana isn't a paper or a scale" in p.s.message and p.s.active


class TestCleanup:
    def messy(self):
        """Two lines drawn twice (one the other way round), a line with no length, an empty text, three overlapping lines in a row, a
        layer, a line type and a block nothing uses, and a dimension attached to a line that has a copy."""
        return Person(line(0, 0, 100, 0), line(100, 0, 0, 0), line(0, 50, 100, 50), line(0, 50, 100, 50), line(5, 5, 5, 5),
                      Text(V(0, 0), 2.5, "  "), line(0, 100, 60, 100), line(40, 100, 120, 100), line(120, 100, 200, 100),
                      line(0, 200, 50, 200), line(60, 200, 100, 200))

    def test_it_shows_what_goes_and_one_undo_puts_it_all_back(self):
        with self.messy() as p:
            with p.doc.transaction("names") as t:
                t.put_layer(Layer("Unused"))
                t.put_linetype(Linetype("DASHED", "Dashed", (5.0, -2.5)))
                t.put_block(BlockDef("Nothing", V(0, 0), (Entity(1, line(0, 0, 1, 1)),)))
            before = p.doc.fingerprint()
            p.run("CLEANUP").at(0, 0)
            assert "2 drawn twice; 2 with no size; 3 overlapping lines made into 1" in p.s.prompt
            assert "1 layer, 1 block, 1 line type that nothing uses" in p.s.prompt
            assert len(p.s.doomed) == 7 and p.s.ghosts == [line(0, 100, 200, 100)]
            assert p.doc.fingerprint() == before
            p.enter()
            left = sorted((tuple(s.a), tuple(s.b)) for s in p.shapes())
            assert left == [((0, 0), (100, 0)), ((0, 50), (100, 50)), ((0, 100), (200, 100)), ((0, 200), (50, 200)), ((60, 200), (100, 200))]
            assert "Unused" not in p.doc.layers and "DASHED" not in p.doc.linetypes and "Nothing" not in p.doc.blocks
            assert p.doc.undo_label == "Cleanup"
            p.doc.undo()
            assert p.doc.fingerprint() == before

    def test_escape_leaves_it(self):
        with self.messy() as p:
            before = p.doc.fingerprint()
            p.run("CLEANUP").key("escape")
            assert p.doc.fingerprint() == before and not p.s.active

    def test_a_clean_drawing_says_so(self):
        with Person(line(0, 0, 10, 0)) as p:
            p.run("CLEANUP")
            assert p.s.message == "There's nothing to clean up." and not p.s.active

    def test_what_a_dimension_or_hatch_holds_on_to_stays(self):
        p = Person(line(0, 0, 100, 0), line(0, 0, 100, 0), line(100, 0, 200, 0))
        with p.doc.transaction("dim") as t:
            t.add(Dimension("linear", (V(0, 0), V(100, 0)), V(50, -20), refs=((p.ids[1], "start"), (p.ids[1], "end"))))
        found = cleanup.plan(p.doc)
        assert p.ids[1] not in found.gone and p.ids[0] in found.duplicates and not found.joined

    def test_joined_lines_keep_their_real_ends_and_look(self):
        doc = Document()
        a, b = V(0.1, 0.3), V(1000.7, 0.3)
        with doc.transaction("x") as t:
            t.put_layer(Layer("Walls", 3))
            t.add(Line(a, V(500.0, 0.3)), Style("Walls"))
            t.add(Line(V(400.0, 0.3), b), Style("Walls"))
            t.add(Line(V(600.0, 0.3), V(700.0, 0.3)))       # on another layer, so it stays as it is
        found = cleanup.plan(doc)
        ((ids, joined),) = found.joined
        assert joined == Line(a, b) and len(ids) == 2
        with doc.transaction("Cleanup") as t:
            cleanup.apply(t, found)
        styles = sorted(e.style.layer for e in doc.entities.values())
        assert styles == ["0", "Walls"]

    def test_a_block_that_only_a_block_that_goes_uses_goes_too(self):
        doc = Document()
        with doc.transaction("x") as t:
            t.put_layer(Layer("Inner"))
            t.put_block(BlockDef("A", V(0, 0), (Entity(1, line(0, 0, 1, 1), Style("Inner")),)))
            t.put_block(BlockDef("B", V(0, 0), (Entity(2, Insert("A", V(0, 0))),)))
            t.add(line(0, 0, 10, 0))
        found = cleanup.plan(doc)
        assert sorted(found.blocks) == ["A", "B"] and found.layers == ["Inner"]
        with doc.transaction("Cleanup") as t:
            cleanup.apply(t, found)
        assert not list(doc.blocks) and "Inner" not in doc.layers

    def test_what_the_file_names_or_points_at_is_kept(self):
        ezdxf = pytest.importorskip("ezdxf")
        d = ezdxf.new("R2010")
        d.layers.add("Unused")
        d.layers.add("InPaperSpace")
        d.blocks.new("Unused block").add_line((0, 0), (1, 1))
        arrow = d.blocks.new("MyArrow")
        arrow.add_line((-1, 0), (0, 0))
        d.dimstyles.new("Arrows", dxfattribs={"dimblk": "MyArrow"})
        d.header["$DIMSTYLE"] = "Arrows"        # the one new dimensions get, which points at its arrow's block by handle
        d.blocks.new("Named").add_circle((0, 0), 1)
        d.header["$DIMBLK"] = "Named"
        d.paperspace().add_line((0, 0), (10, 10), dxfattribs={"layer": "InPaperSpace"})
        msp = d.modelspace()
        msp.add_line((0, 0), (100, 0))
        stream = io.StringIO()
        d.write(stream)
        doc, _ = read_dxf(stream.getvalue().encode("utf-8"))
        found = cleanup.plan(doc)
        assert found.layers == ["Unused"] and found.blocks == ["Unused block"] and not found.dim_styles
        # kept: a layer used in paper space, a dimension style's arrow, a block the header names, and Defpoints, which dimensions use
        with doc.transaction("Cleanup") as t:
            cleanup.apply(t, found)
        written = write_dxf(doc)
        again = ezdxf.read(io.StringIO(written.decode("utf-8")))
        auditor = again.audit()
        assert not auditor.has_errors and "MyArrow" in again.blocks and "Unused block" not in again.blocks
        assert again.dimstyles.get("Arrows").dxf.dimblk == "MyArrow"


class TestCommandLine:
    def run(self, capsys, *args):
        code = cli(list(args))
        out = capsys.readouterr().out
        assert out.count("\n") == 1
        return code, json.loads(out)

    def test_a_pdf_of_its_own_sheet_or_one_fitted(self, capsys, tmp_path):
        src = tmp_path / "plan.draftworx"
        src.write_bytes(dumps(plan_drawing()))
        code, out = self.run(capsys, "pdf", str(src), str(tmp_path / "plan.pdf"))
        assert code == 0 and (out["paper"], out["scale"], out["landscape"]) == ("A3", "1:50", True)
        assert (tmp_path / "plan.pdf").read_bytes() == pdf_of(plan_drawing(), sheets.sheet_of(plan_drawing()), title="plan")[0]
        code, out = self.run(capsys, "pdf", str(src), str(tmp_path / "plan.pdf"), "--paper", "A1", "--upright", "--scale", "1:20",
                             "--overwrite")
        assert code == 0 and (out["paper"], out["scale"], out["landscape"]) == ("A1", "1:20", False)
        assert b"/MediaBox [0 0 1683.7795 2383.937]" in (tmp_path / "plan.pdf").read_bytes()
        assert loads(src.read_bytes()).settings == plan_drawing().settings     # the drawing itself isn't changed

    def test_a_drawing_with_no_sheet_gets_one(self, capsys, tmp_path):
        doc = Document()
        with doc.transaction("x") as t:
            t.add(rect(0, 0, 10000, 6000))
        src = tmp_path / "room.draftworx"
        src.write_bytes(dumps(doc))
        code, out = self.run(capsys, "pdf", str(src), str(tmp_path / "room.pdf"))
        assert code == 0 and (out["paper"], out["scale"]) == ("A3", "1:50")

    def test_a_dxf_with_a_border_prints_on_the_sheet_it_shows(self, capsys, tmp_path):
        doc = sheets.from_template("ISO A3 across")
        with doc.transaction("x") as t:
            t.add(rect(0, 0, 3000, 2000))
            sheets.apply(t, sheets.fitted(doc, "A3", True, 50.0))      # 1:20 would fit, and 1:50 is what the border says
        src = tmp_path / "part.dxf"
        src.write_bytes(write_dxf(doc))
        code, out = self.run(capsys, "pdf", str(src), str(tmp_path / "part.pdf"))
        assert code == 0 and (out["paper"], out["scale"]) == ("A3", "1:50") and out["notes"] == []
        assert out["sheet"] == list(sheets.sheet_of(doc).box())

    def test_refusals(self, capsys, tmp_path):
        src = tmp_path / "plan.draftworx"
        src.write_bytes(dumps(plan_drawing()))
        (tmp_path / "there.pdf").write_bytes(b"mine")
        for args, kind in (((str(tmp_path / "there.pdf"),), "exists"), ((str(tmp_path / "a.pdf"), "--paper", "B5"), "bad-input"),
                           ((str(tmp_path / "a.pdf"), "--scale", "big"), "bad-input"), ((str(tmp_path / "a.txt"),), "bad-input"),
                           ((str(tmp_path / "a.pdf"), "--upright", "--across"), "bad-input")):
            code, out = self.run(capsys, "pdf", str(src), *args)
            assert code == 2 and out["kind"] == kind and out["error"]
        assert (tmp_path / "there.pdf").read_bytes() == b"mine"
        empty = tmp_path / "empty.draftworx"
        empty.write_bytes(dumps(Document()))
        code, out = self.run(capsys, "pdf", str(empty), str(tmp_path / "e.pdf"))
        assert code == 2 and out["error"] == "There's nothing to print."
