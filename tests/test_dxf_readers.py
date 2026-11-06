# SPDX-License-Identifier: GPL-3.0-or-later
"""Other programs reading what's written here, because a file that only we and one library can read isn't much of an exchange format.

These run the real programs when they're there, and skip otherwise. CI installs GDAL and LibreCAD on Linux. AutoCAD can't be run in CI, so nothing here
says anything about it.
"""
import json
import math
import os
import shutil
import subprocess

import pytest

from draftworx.core.curves import Arc, Circle, EllipseArc, Polyline
from draftworx.core.dxf import VERSIONS, read_dxf, write_dxf
from draftworx.core.model import BlockDef, Document, Entity, Layer, Style
from draftworx.core.shapes import Dimension, Hatch, Insert, MText, Node, Solid, Text
from draftworx.core.vec import TAU, Affine, Vec
from driver import line


def drawing() -> Document:
    d = Document()
    with d.transaction("x") as t:
        t.put_layer(Layer("Parts", 1))
        t.add(line(0, 0, 100, 0), Style("Parts"))
        t.add(Circle(Vec(50.0, 50.0), 25.0))
        t.add(Arc(Vec(50.0, 50.0), 40.0, 0.5, 2.0))
        t.add(EllipseArc(Vec(150.0, 50.0), Vec(40.0, 10.0), 0.4, 0.0, TAU))
        t.add(Polyline((Vec(200.0, 0.0), Vec(250.0, 0.0), Vec(250.0, 50.0)), (0.0, 0.5, 0.0), True))
        t.add(Text(Vec(10.0, 200.0), 5.0, "Hello é"))
        t.add(MText(Vec(10.0, 180.0), 3.0, "Two\\Plines", 80.0))
        t.add(Solid((Vec(300.0, 0.0), Vec(320.0, 0.0), Vec(320.0, 20.0), Vec(300.0, 20.0))))
        t.add(Node(Vec(5.0, 5.0)))
        t.put_block(BlockDef("Bolt", Vec(0.0, 0.0), (Entity(1000, line(-5, 0, 5, 0)),)))
        t.add(Insert("Bolt", Vec(100.0, 100.0), 2.0, 2.0, math.pi / 2.0))
        t.add(Dimension("linear", (Vec(0.0, 0.0), Vec(100.0, 0.0)), Vec(50.0, -20.0)))
        t.add(Dimension("diameter", (Vec(50.0, 50.0), Vec(50.0, 75.0)), Vec(50.0, 90.0)))
        square = (line(400, 0, 500, 0), line(500, 0, 500, 100), line(500, 100, 400, 100), line(400, 100, 400, 0))
        t.add(Hatch((square,), "SOLID"))
        t.add(Hatch((tuple(c.transformed(Affine.translation(200, 0)) for c in square), (Circle(Vec(650.0, 50.0), 20.0).as_arc().reversed(),)), "ANSI31", 2.0))
    return d


def gdal_features(path, tmp_path, encoding="UTF-8"):
    """GDAL goes by $DWGCODEPAGE whatever the version, and so misreads text in any file from 2007 on, ours or AutoCAD's, unless it's told. A file's own code page isn't what's being tested."""
    out = tmp_path / "out.geojson"
    subprocess.run(["ogr2ogr", "--config", "DXF_ENCODING", encoding, "-f", "GeoJSON", str(out), str(path), "entities"], check=True, capture_output=True, timeout=120)
    return json.loads(out.read_text(encoding="utf-8"))["features"]


needs_gdal = pytest.mark.skipif(shutil.which("ogr2ogr") is None, reason="GDAL isn't installed")
needs_librecad = pytest.mark.skipif(shutil.which("librecad") is None, reason="LibreCAD isn't installed")


@needs_gdal
@pytest.mark.parametrize("version", [v for k, v in VERSIONS.items() if k != "R12"])
def test_gdal_reads_what_was_drawn(version, tmp_path):
    path = tmp_path / "a.dxf"
    path.write_bytes(write_dxf(drawing(), version))
    features = gdal_features(path, tmp_path, "UTF-8" if version >= "AC1021" else "CP1252")
    by = {}
    for f in features:
        by.setdefault(f["properties"]["SubClasses"].split(":")[-1], []).append(f)
    assert "AcDbLine" in by and by["AcDbLine"][0]["properties"]["Layer"] == "Parts"
    assert by["AcDbLine"][0]["geometry"]["coordinates"][0][:2] == [0.0, 0.0]
    assert by["AcDbLine"][0]["geometry"]["coordinates"][-1][:2] == [100.0, 0.0]
    words = {f["properties"].get("Text") for f in features}
    assert "Hello é" in words
    # dimensions, through the blocks that draw them: a file's %%c is a diameter sign to GDAL
    assert "100" in words and any(w and w.endswith("50") and w[0] in "Ø⌀" for w in words)
    # a circle comes back as many short lines, all the right distance from its centre
    circle = next(f for f in features if "Circle" in f["properties"]["SubClasses"] and "Arc" not in f["properties"]["SubClasses"])
    for x, y, *_ in circle["geometry"]["coordinates"]:
        assert math.hypot(x - 50.0, y - 50.0) == pytest.approx(25.0, abs=0.01)
    # hatches, as areas: their boundaries, holes included
    rings = []
    for f in features:
        if "AcDbHatch" in f["properties"]["SubClasses"]:
            g = f["geometry"]
            polygons = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"] if g["type"] == "MultiPolygon" else []
            rings.append([ring for poly in polygons for ring in poly])
    assert len(rings) == 2
    for found, x0 in zip(sorted(rings, key=lambda r: min(p[0] for p in r[0])), (400.0, 600.0)):
        xs = [p[0] for p in found[0]]
        ys = [p[1] for p in found[0]]
        assert (min(xs), min(ys), max(xs), max(ys)) == pytest.approx((x0, 0.0, x0 + 100.0, 100.0), abs=1e-6)
    assert len(max(rings, key=len)) == 2          # the circle is a hole
    # the block's line, turned a quarter and doubled
    lines = [f["geometry"]["coordinates"] for f in features if f["geometry"]["type"] == "LineString" and len(f["geometry"]["coordinates"]) == 2]
    assert any(a[:2] == pytest.approx([100.0, 90.0], abs=1e-6) and b[:2] == pytest.approx([100.0, 110.0], abs=1e-6) for a, b in lines)


@needs_gdal
def test_gdal_reads_r12(tmp_path):
    path = tmp_path / "a.dxf"
    path.write_bytes(write_dxf(drawing(), "AC1009"))
    features = gdal_features(path, tmp_path, "CP1252")
    assert any(f["properties"]["Layer"] == "PARTS" for f in features)
    assert any(f["properties"].get("Text") == "Hello é" for f in features)


@needs_gdal
def test_gdal_reads_a_file_with_one_thing_changed(tmp_path):
    ezdxf = pytest.importorskip("ezdxf")
    source = ezdxf.new("R2013", setup=True)
    source.modelspace().add_line((0, 0), (10, 0))
    source.modelspace().add_circle((5, 5), 2)
    path = tmp_path / "a.dxf"
    source.saveas(path)
    doc, _ = read_dxf(path.read_bytes())
    with doc.transaction("x") as t:
        t.add(line(0, 0, 0, 10))
    path.write_bytes(write_dxf(doc))
    assert len(gdal_features(path, tmp_path)) == 3


@needs_librecad
@pytest.mark.parametrize("version", VERSIONS.values())
def test_librecad_prints_it(version, tmp_path):
    """LibreCAD reads DXF with a library of its own. All this shows is that it gets to the end and has something to print: a look at the page is by hand."""
    path = tmp_path / "a.dxf"
    path.write_bytes(write_dxf(drawing(), version))
    pdf = tmp_path / "a.pdf"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_RUNTIME_DIR=str(tmp_path))
    result = subprocess.run(["librecad", "dxf2pdf", "-a", "-o", str(pdf), str(path)], capture_output=True, timeout=120, env=env)
    assert pdf.exists() and pdf.stat().st_size > 2000, result.stderr.decode("utf-8", "replace")[-500:]


class TestOpenSchematix:
    """OpenSchematix writes R12 and reads any text DXF. These use the real program when it can be imported."""

    def test_what_it_writes_opens_here(self):
        pytest.importorskip("openschematix")
        from openschematix.dxf import to_dxf
        from openschematix.model import Document as SchematicDocument
        sheet = SchematicDocument()
        data = to_dxf(sheet).encode("cp1252", "replace")
        doc, _ = read_dxf(data)
        assert doc.extras["dxf"]["version"] == "AC1009"
        assert doc.entities        # at least its frame
        assert write_dxf(doc) == data

    @pytest.mark.parametrize("version", [VERSIONS["R12"], VERSIONS["2013"]])
    def test_what_is_written_here_opens_there(self, version):
        pytest.importorskip("openschematix")
        from openschematix.dxf import import_dxf
        report = import_dxf(write_dxf(drawing(), version))
        assert report.counts.get("LINE", 0) >= 1
        assert report.counts.get("CIRCLE", 0) == 1
        assert report.counts.get("TEXT", 0) >= 1
        assert not any(k.startswith(("LINE", "CIRCLE", "ARC", "TEXT")) for k in report.skipped)
