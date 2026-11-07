# SPDX-License-Identifier: GPL-3.0-or-later
"""A sheet as a PDF, at its scale, with no Qt and nothing outside the standard library.

Lines, arcs and curves are vectors: an arc is Bézier pieces of at most 30 degrees (`curves.emit`), so a printed circle is round to a
fraction of a micrometre. Text is Helvetica, one of the fonts every PDF reader has, which is the font DraftWorx measures text with (ADR-10),
so it lands where the drawing says. The same drawing gives the same bytes, so a revision control program can tell that nothing changed.
"""
from __future__ import annotations

import math
import zlib

from .. import PROGRAM
from .model import Document
from .printing import Pen, draw, text_origin
from .shapes import Text
from .sheets import OFF_SHEET, Sheet, off_sheet

PT = 72.0 / 25.4        # points in a millimetre
_CAP = 0.718            # Helvetica's capital height, as a part of its size


def _n(v: float) -> str:
    """A number as short as it can be and still exact to a ten-thousandth of a point (36 nanometres)."""
    text = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


class _Path:
    """What `curves.emit` writes, as PDF path operators, from millimetres to points."""

    def __init__(self, out: list[str]):
        self.out = out

    def moveTo(self, x, y):
        self.out.append(f"{_n(x * PT)} {_n(y * PT)} m")

    def lineTo(self, x, y):
        self.out.append(f"{_n(x * PT)} {_n(y * PT)} l")

    def cubicTo(self, x1, y1, x2, y2, x, y):
        self.out.append(f"{_n(x1 * PT)} {_n(y1 * PT)} {_n(x2 * PT)} {_n(y2 * PT)} {_n(x * PT)} {_n(y * PT)} c")

    def closeSubpath(self):
        self.out.append("h")


def _rgb(rgb: int) -> str:
    return " ".join(_n(((rgb >> s) & 255) / 255.0) for s in (16, 8, 0))


def _winansi(text: str) -> tuple[bytes, int]:
    """Text as the font's encoding can hold it, and how many characters it couldn't."""
    out = bytearray()
    lost = 0
    for ch in text:
        try:
            b = ch.encode("cp1252")
        except UnicodeEncodeError:
            b, lost = b"?", lost + 1
        out += b
    return bytes(out), lost


def _literal(data: bytes) -> str:
    out = []
    for b in data:
        c = chr(b)
        if c in "()\\":
            out.append("\\" + c)
        elif b < 32 or b > 126:
            out.append(f"\\{b:03o}")
        else:
            out.append(c)
    return "(" + "".join(out) + ")"


class _Content:
    """A Page (printing.Page) that writes a PDF content stream."""

    def __init__(self):
        self.out: list[str] = []
        self.path = _Path(self.out)
        self.lost = 0
        self._stroke = self._fill = None
        self._width = self._dashes = None

    def _pen(self, pen: Pen) -> None:
        if self._stroke != pen.rgb:
            self.out.append(f"{_rgb(pen.rgb)} RG")
            self._stroke = pen.rgb
        if self._width != pen.width:
            self.out.append(f"{_n(pen.width * PT)} w")
            self._width = pen.width
        if self._dashes != pen.dashes:
            self.out.append(f"[{' '.join(_n(d * PT) for d in pen.dashes)}] 0 d")
            self._dashes = pen.dashes

    def _ink(self, rgb: int) -> None:
        if self._fill != rgb:
            self.out.append(f"{_rgb(rgb)} rg")
            self._fill = rgb

    def stroke(self, curve, pen: Pen) -> None:
        self._pen(pen)
        curve.emit(self.path)
        self.out.append("S")

    def fill(self, loops, rgb: int) -> None:
        self._ink(rgb)
        for loop in loops:
            first = True
            for c in loop:
                c.emit(self.path, first)
                first = False
            self.out.append("h")
        self.out.append("f")

    def dot(self, p, pen: Pen) -> None:
        self._pen(Pen(pen.rgb, max(pen.width, 0.3), ()))
        x, y = _n(p[0] * PT), _n(p[1] * PT)
        self.out += ["1 J", f"{x} {y} m", f"{x} {y} l", "S", "0 J"]

    def text(self, t: Text, rgb: int) -> None:
        data, lost = _winansi(t.text)
        self.lost += lost
        start, rot, h, wf = text_origin(t)
        size = h / _CAP * PT
        if size <= 0.0 or not data:
            return
        self._ink(rgb)
        c, s = math.cos(rot), math.sin(rot)
        sx = -1.0 if t.mirrored_x else 1.0
        sy = -1.0 if t.mirrored_y else 1.0
        shear = math.tan(t.oblique)
        # x along the text and y up it, sized: [wf*sx 0 shear*sy sy] then turned
        a, b = wf * sx * c, wf * sx * s
        cc, d = shear * sy * c - sy * s, shear * sy * s + sy * c
        self.out.append(f"BT /F1 {_n(size)} Tf {_n(a)} {_n(b)} {_n(cc)} {_n(d)} {_n(start[0] * PT)} {_n(start[1] * PT)} Tm {_literal(data)} Tj ET")


def pdf_of(doc: Document, sheet: Sheet, black: bool = False, title: str = "") -> tuple[bytes, list[str]]:
    """The PDF, one page the size of the sheet's paper, and what the person should know about it."""
    content = _Content()
    content.out += ["1 j", "0 J"]
    left_out = draw(doc, sheet, content, black)
    stream = zlib.compress("\n".join(content.out).encode("latin-1"), 9)
    w, h = sheet.width * PT, sheet.height * PT
    info = f"<< /Producer {_literal(PROGRAM.encode('latin-1', 'replace'))}" + (f" /Title {_literal(_winansi(title)[0])}" if title else "") + " >>"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_n(w)} {_n(h)}] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        None,
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        info,
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        if body is None:
            out += f"{i} 0 obj\n<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n".encode("latin-1") + stream + b"\nendstream\nendobj\n"
        else:
            out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1")
    for o in offsets:
        out += f"{o:010d} 00000 n \n".encode("latin-1")
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin-1")
    notes = []
    if off_sheet(doc, sheet):
        notes.append(OFF_SHEET)
    if content.lost:
        notes.append(f"{content.lost} character{'s' if content.lost != 1 else ''} of text can't be printed in Helvetica and show as ?.")
    if left_out:
        notes.append("Some blocks are placed so many times over that only part of them could be printed.")
    return bytes(out), notes
