# SPDX-License-Identifier: GPL-3.0-or-later
"""Revision codes on prints (core/revcode.py): FieldWorx's `FWX:R:<number>@<revision>` as a QR code above the title block, so a phone
can say whether a paper copy is still the released revision. The code is read back from the printed page by zxing-cpp, the decoder
FieldWorx's app uses, after PDFium or resvg has rendered it: renderers and a decoder that share nothing with DraftWorx."""
import hashlib
import io
import json
from pathlib import Path

import pytest

from draftworx.cli import main as cli
from draftworx.core import fwx_label, revcode, sheets
from draftworx.core.curves import Polyline
from draftworx.core.model import Document
from draftworx.core.native import dumps
from draftworx.core.pdf import pdf_of
from draftworx.core.revcode import CodeError, RevisionCode
from draftworx.core.svg import svg_of
from draftworx.core.vec import Vec
from driver import line

zxingcpp = pytest.importorskip("zxingcpp")
DPI = 200


def drawn_on(template: str, *lines) -> Document:
    """A drawing on a template, with lines given in millimetres on its paper: (x0, y0, x1, y1)."""
    doc = sheets.from_template(template)
    k = sheets.sheet_of(doc).per_mm
    if lines:
        with doc.transaction("x") as t:
            for x0, y0, x1, y1 in lines:
                t.add(line(x0 * k, y0 * k, x1 * k, y1 * k))
    return doc


def read_pdf(data: bytes):
    pdfium = pytest.importorskip("pypdfium2")
    page = pdfium.PdfDocument(data)[0]
    image = page.render(scale=DPI / 72, grayscale=True).to_pil()
    return image, zxingcpp.read_barcodes(image)


def centre_mm(found, image) -> tuple[float, float]:
    """Where a code read off a page is, in millimetres from the paper's lower-left corner."""
    p = found.position
    xs = [c.x for c in (p.top_left, p.top_right, p.bottom_left, p.bottom_right)]
    ys = [c.y for c in (p.top_left, p.top_right, p.bottom_left, p.bottom_right)]
    k = 25.4 / DPI
    return sum(xs) / 4 * k, (image.size[1] - sum(ys) / 4) * k


def test_the_encoder_is_fieldworxs_copied_unchanged():
    # FieldWorx tests fwx_label.py bit for bit against python-qrcode; this copy must be that file, so it draws the same codes.
    # (Line endings aside: git on Windows checks text files out with CRLF.)
    assert fwx_label.FWX_LABEL_VERSION == 1
    assert hashlib.sha256(Path(fwx_label.__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == \
        "56cffdf78cb5f0297cf17f06f8ada7a738f30a2f7e7eb6f65d671109a291c067"


@pytest.mark.parametrize("template", ["ISO A3 across", "ISO A4 upright", "Letter across", "ANSI D across"])
def test_a_print_carries_its_revision_code_above_the_title_block(template):
    doc = drawn_on(template, (100, 100, 150, 120))
    sheet = sheets.sheet_of(doc)
    code = RevisionCode("FRM-200", "B")
    data, notes = pdf_of(doc, sheet, code=code)
    image, found = read_pdf(data)
    assert [f.text for f in found] == ["FWX:R:FRM-200@B"] and found[0].ec_level == "Q"
    x0, y0, x1, y1 = revcode.area(doc, sheet)
    cx, cy = centre_mm(found[0], image)
    assert x0 < cx < x1 and y0 + revcode.CAPTION < cy < y1, "where the area says: above the title block, at the right"
    assert x1 < sheet.width - 10.0 and y0 > 50.0, "inside the border, clear of the title block"
    assert "(FRM-200 rev B) Tj" in content_of(data)
    assert notes == []
    assert pdf_of(doc, sheet, code=code)[0] == data, "the same drawing and code give the same PDF"
    plain, _ = pdf_of(doc, sheet)
    assert read_pdf(plain)[1] == [], "no code unless one is asked for"


def test_a_sheet_without_a_border_has_it_in_the_corner():
    doc = Document()
    with doc.transaction("x") as t:
        t.add(Polyline((Vec(0.0, 0.0), Vec(2000.0, 0.0), Vec(2000.0, 1000.0), Vec(0.0, 1000.0)), closed=True))
        sheets.apply(t, sheets.fitted(doc, "A4", True))
    sheet = sheets.sheet_of(doc)
    assert sheets.frame_insert(doc) is None and revcode.why_not(doc, sheet) == ""
    image, found = read_pdf(pdf_of(doc, sheet, code=RevisionCode("SK-7", "3"))[0])
    cx, cy = centre_mm(found[0], image)
    assert found[0].text == "FWX:R:SK-7@3" and sheet.width - 30 < cx < sheet.width - 10 and 10 < cy < 35


def test_the_svg_carries_it_too():
    resvg = pytest.importorskip("resvg_py")
    from PIL import Image
    doc = drawn_on("ISO A3 across")
    data, _ = svg_of(doc, sheets.sheet_of(doc), code=RevisionCode("FRM-200", "B"))
    image = Image.open(io.BytesIO(bytes(resvg.svg_to_bytes(svg_string=data.decode("utf-8"), background="white", dpi=200.0))))
    assert [f.text for f in zxingcpp.read_barcodes(image.convert("L"))] == ["FWX:R:FRM-200@B"]


def test_it_never_covers_the_drawing():
    doc = drawn_on("ISO A3 across", (380, 60, 400, 70))                       # a line across where the code goes
    sheet = sheets.sheet_of(doc)
    assert revcode.blocked(doc, sheet) and "above the title block, at the right" in revcode.why_not(doc, sheet)
    with pytest.raises(CodeError, match="no room for the revision code"):
        pdf_of(doc, sheet, code=RevisionCode("FRM-200", "B"))
    hidden = drawn_on("ISO A3 across", (380, 60, 400, 70))
    drawn_layer = hidden.entities[revcode.blocked(hidden, sheets.sheet_of(hidden))[0]].style.layer
    with hidden.transaction("x") as t:
        t.put_layer(__import__("dataclasses").replace(hidden.layers.get(drawn_layer), plot=False))
    assert revcode.why_not(hidden, sheets.sheet_of(hidden)) == "", "what doesn't print isn't in the way"
    near = drawn_on("ISO A3 across", (300, 60, 386, 90))                # close, but clear of it
    assert revcode.why_not(near, sheets.sheet_of(near)) == ""


def test_revision_codes_are_checked():
    assert RevisionCode.parse(" FRM-200@B ") == RevisionCode("FRM-200", "B")
    assert RevisionCode.parse("A@B@C").text == "FWX:R:A@B@C", "the revision is what follows the last @"
    for bad in ("FRM-200", "@B", "FRM-200@", "FRM-200@ ", "FRM\n200@B", ""):
        with pytest.raises(CodeError):
            RevisionCode.parse(bad)


def content_of(data: bytes) -> str:
    return __import__("zlib").decompress(data.split(b"stream\n")[1].split(b"\nendstream")[0]).decode("latin-1")


def test_a_long_number_gets_smaller_words_under_its_code():
    doc = drawn_on("ISO A3 across")
    long = RevisionCode("HPU-75-GA-0001-S2", "AB")
    data, _ = pdf_of(doc, sheets.sheet_of(doc), code=long)
    assert [f.text for f in read_pdf(data)[1]] == [long.text]
    caption = next(line for line in content_of(data).splitlines() if "(HPU-75-GA-0001-S2 rev AB) Tj" in line)
    size = float(caption.split("/F1 ")[1].split(" Tf")[0])
    assert size < revcode.CAPTION / 0.718 * 72 / 25.4, "shrunk to the width of the code"
    longer = RevisionCode("HPU-75-GA-0001-SHEET-2-OF-3-PIPING-ISOMETRIC", "AB")
    data, _ = pdf_of(doc, sheets.sheet_of(doc), code=longer)
    assert [f.text for f in read_pdf(data)[1]] == [longer.text]
    assert "rev AB" not in content_of(data), "words too small to read are left out; the title block has them, the code holds them"


class TestCommandLine:
    def run(self, capsys, *args):
        code = cli(list(args))
        out = capsys.readouterr().out
        return code, json.loads(out)

    def test_pdf_and_svg_with_a_revision_code(self, capsys, tmp_path):
        src = tmp_path / "frm.draftworx"
        src.write_bytes(dumps(drawn_on("ISO A3 across", (100, 100, 150, 120))))
        code, out = self.run(capsys, "pdf", str(src), str(tmp_path / "frm.pdf"), "--revision-code", "FRM-200@B")
        assert code == 0 and out["revision_code"] == "FWX:R:FRM-200@B"
        assert [f.text for f in read_pdf((tmp_path / "frm.pdf").read_bytes())[1]] == ["FWX:R:FRM-200@B"]
        code, out = self.run(capsys, "svg", str(src), str(tmp_path / "frm.svg"), "--revision-code", "FRM-200@B")
        assert code == 0 and out["revision_code"] == "FWX:R:FRM-200@B"
        code, out = self.run(capsys, "pdf", str(src), str(tmp_path / "plain.pdf"))
        assert code == 0 and out["revision_code"] == ""

    def test_refusals_write_nothing(self, capsys, tmp_path):
        src = tmp_path / "frm.draftworx"
        src.write_bytes(dumps(drawn_on("ISO A3 across", (380, 60, 400, 70))))
        code, out = self.run(capsys, "pdf", str(src), str(tmp_path / "a.pdf"), "--revision-code", "FRM-200@B")
        assert code == 2 and out["kind"] == "bad-input" and "no room for the revision code" in out["error"]
        code, out = self.run(capsys, "pdf", str(src), str(tmp_path / "a.pdf"), "--revision-code", "FRM-200")
        assert code == 2 and out["kind"] == "bad-input" and "like DWG-1@B" in out["error"]
        assert not (tmp_path / "a.pdf").exists()
