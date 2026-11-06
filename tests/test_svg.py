# SPDX-License-Identifier: GPL-3.0-or-later
"""A sheet as an SVG: millimetres, to scale, by the same walk as a PDF and a printer. Checked by its own numbers, by reading it as XML, and
by rendering it with resvg (a renderer that isn't ours) and measuring the ink."""
import io
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import replace

import pytest

from draftworx.cli import main as cli
from draftworx.core import sheets
from draftworx.core.model import Document, Layer, Linetype, Settings, Style
from draftworx.core.native import dumps
from draftworx.core.shapes import Text
from draftworx.core.svg import svg_of
from draftworx.core.vec import Vec
from driver import line
from test_output import plan_drawing

NS = {"s": "http://www.w3.org/2000/svg"}


def paths(data: bytes) -> list[list[tuple[float, float]]]:
    """Each path's points, for paths of straight pieces, in the page's millimetres with y up."""
    out = []
    for d in re.findall(rb'<path d="([^"]+)" fill="none"', data):
        pts = [(float(x), float(y)) for x, y in re.findall(rb"[ML](-?[\d.]+) (-?[\d.]+)", d)]
        out.append(pts)
    return out


def test_a_metre_at_1_to_50_is_20_millimetres():
    doc = plan_drawing()
    data, notes = svg_of(doc, sheets.sheet_of(doc))
    root = ET.fromstring(data)
    assert (root.get("width"), root.get("height"), root.get("viewBox")) == ("420mm", "297mm", "0 0 420 297") and notes == []
    (metre,) = [p for p in paths(data) if len(p) == 2]
    assert abs(metre[1][0] - metre[0][0]) == pytest.approx(20.0, abs=1e-4) and metre[0][1] == metre[1][1]
    # the room, 10 m by 6 m, is 200 by 120 mm in the middle of the paper
    (room,) = [p for p in paths(data) if len(p) == 4]
    assert room[0] == pytest.approx(((420 - 200) / 2, (297 - 120) / 2), abs=1e-4)


def test_rendered_the_metre_measures_20_millimetres():
    resvg = pytest.importorskip("resvg_py")
    from PIL import Image
    doc = Document()
    with doc.transaction("x") as t:
        t.add(line(0, 0, 1000, 0), Style(weight=50))
        t.set_settings(Settings(paper="A4", annotation_scale=50.0, sheet_x=-2000.0, sheet_y=-5000.0))
    data, _ = svg_of(doc, sheets.sheet_of(doc))
    image = Image.open(io.BytesIO(bytes(resvg.svg_to_bytes(svg_string=data.decode("utf-8"), background="white", dpi=254.0)))).convert("L")
    w, h = image.size
    assert abs(w - 2970) <= 1 and abs(h - 2100) <= 1          # A4 across at 10 pixels to the millimetre
    row = round(h - 100 * 10)
    ink = [x for x in range(w) if image.getpixel((x, row)) < 128]
    assert ink and (ink[-1] + 1 - ink[0]) / 10 == pytest.approx(20.0, abs=0.15) and ink[0] / 10 == pytest.approx(40.0, abs=0.15)


def test_text_is_real_text_any_character_and_upright():
    doc = plan_drawing()
    with doc.transaction("x") as t:
        t.add(Text(Vec(1000.0, 2000.0), 250.0, "Flow → out <A&B>"))
    data, notes = svg_of(doc, sheets.sheet_of(doc))
    (text,) = ET.fromstring(data).iter("{http://www.w3.org/2000/svg}text")
    assert text.text == "Flow → out <A&B>" and notes == []        # unlike a PDF in Helvetica, every character is there
    assert float(text.get("font-size")) == pytest.approx(5 / 0.718, abs=1e-3)
    a, b, c, d, e, f = (float(x) for x in re.findall(r"-?[\d.]+", text.get("transform")))
    # along the baseline, and down the letters, which in the page's y-up group is down too: so it reads the right way up
    assert (a, b, c, d) == (1.0, 0.0, 0.0, -1.0)
    st = doc.settings
    assert (e, f) == pytest.approx(((1000 - st.sheet_x) / 50, (2000 - st.sheet_y) / 50), abs=1e-4)


def test_line_types_weights_and_what_doesnt_print():
    doc = plan_drawing()
    with doc.transaction("x") as t:
        t.put_linetype(Linetype("HIDDEN", "Hidden", (6.35, -3.175)))
        t.put_layer(Layer("Hidden", linetype="HIDDEN", weight=50))
        t.put_layer(Layer("Notes", plot=False))
        t.add(line(0, 3000, 10000, 3000), Style("Hidden"))
        t.add(line(0, 4000, 10000, 4000), Style("Notes"))
        t.set_settings(replace(doc.settings, ltscale=50.0))
    data, _ = svg_of(doc, sheets.sheet_of(doc))
    assert len(paths(data)) == 3
    assert b'stroke-width="0.5" stroke-dasharray="6.35 3.175"' in data      # at 1:50 with a line type scale of 50


def test_the_same_drawing_gives_the_same_bytes():
    doc = plan_drawing()
    assert svg_of(doc, sheets.sheet_of(doc)) == svg_of(doc, sheets.sheet_of(doc))


def test_from_the_command_line(capsys, tmp_path):
    src = tmp_path / "plan.draftworx"
    src.write_bytes(dumps(plan_drawing()))
    code = cli(["svg", str(src), str(tmp_path / "plan.svg")])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and (out["paper"], out["scale"]) == ("A3", "1:50") and (tmp_path / "plan.svg").read_bytes().startswith(b"<?xml")
    code = cli(["svg", str(src), str(tmp_path / "plan.pdf")])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["error"] == "The new file's name has to end in .svg."
