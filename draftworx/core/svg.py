# SPDX-License-Identifier: GPL-3.0-or-later
"""A sheet as an SVG, at its scale, for Inkscape, a browser, a laser cutter's software or a word processor. No Qt, nothing outside the
standard library.

It's drawn by the same walk of the drawing as a PDF and a printer (`printing.py`, ADR-15), onto a page whose units are millimetres: the
file says `width="420mm" height="297mm"` and its view box is the paper in millimetres, so a program that honours sizes shows and prints it
to scale, and one cutting or plotting it gets real lengths. Curves are Bézier pieces, as in the PDF. Text is real text in Helvetica (or
Arial, which has its widths), so it can be searched and changed; unlike a PDF, every character can be written. The same drawing gives the
same bytes.
"""
from __future__ import annotations

import math
from xml.sax.saxutils import escape, quoteattr

from .. import PROGRAM
from .model import Document
from .printing import Pen, draw, text_origin
from .sheets import OFF_SHEET, Sheet, off_sheet
from .shapes import Text

_CAP = 0.718            # Helvetica's capital height, as a part of its size


def _n(v: float) -> str:
    """A number as short as it can be and still exact to a ten-thousandth of a millimetre (0.1 µm)."""
    text = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _rgb(rgb: int) -> str:
    return f"#{rgb & 0xFFFFFF:06x}"


class _Path:
    """What `curves.emit` writes, as SVG path data, in millimetres with y up (the page's group turns it over)."""

    def __init__(self):
        self.d: list[str] = []

    def moveTo(self, x, y):
        self.d.append(f"M{_n(x)} {_n(y)}")

    def lineTo(self, x, y):
        self.d.append(f"L{_n(x)} {_n(y)}")

    def cubicTo(self, x1, y1, x2, y2, x, y):
        self.d.append(f"C{_n(x1)} {_n(y1)} {_n(x2)} {_n(y2)} {_n(x)} {_n(y)}")

    def closeSubpath(self):
        self.d.append("Z")


class _Content:
    """A Page (printing.Page) that writes SVG elements."""

    def __init__(self):
        self.out: list[str] = []

    @staticmethod
    def _stroke_attrs(pen: Pen) -> str:
        dash = f' stroke-dasharray="{" ".join(_n(d) for d in pen.dashes)}"' if pen.dashes else ""
        return f'stroke="{_rgb(pen.rgb)}" stroke-width="{_n(pen.width)}"{dash}'

    def stroke(self, curve, pen: Pen) -> None:
        p = _Path()
        curve.emit(p)
        self.out.append(f'<path d="{" ".join(p.d)}" fill="none" {self._stroke_attrs(pen)}/>')

    def fill(self, loops, rgb: int) -> None:
        p = _Path()
        for loop in loops:
            first = True
            for c in loop:
                c.emit(p, first)
                first = False
            p.closeSubpath()
        self.out.append(f'<path d="{" ".join(p.d)}" fill="{_rgb(rgb)}" fill-rule="nonzero" stroke="none"/>')

    def dot(self, p, pen: Pen) -> None:
        self.out.append(f'<circle cx="{_n(p[0])}" cy="{_n(p[1])}" r="{_n(max(pen.width, 0.3) / 2.0)}" fill="{_rgb(pen.rgb)}"/>')

    def text(self, t: Text, rgb: int) -> None:
        if not t.text:
            return
        start, rot, h, wf = text_origin(t)
        size = h / _CAP
        if size <= 0.0:
            return
        c, s = math.cos(rot), math.sin(rot)
        sx = -1.0 if t.mirrored_x else 1.0
        sy = -1.0 if t.mirrored_y else 1.0
        shear = math.tan(t.oblique)
        # as the PDF's text matrix, then turned over again: text in SVG is drawn with y down, inside a group whose y is up
        a, b = wf * sx * c, wf * sx * s
        cc, d = -(shear * sy * c - sy * s), -(shear * sy * s + sy * c)
        self.out.append(f'<text transform="matrix({_n(a)} {_n(b)} {_n(cc)} {_n(d)} {_n(start[0])} {_n(start[1])})" font-size="{_n(size)}" '
                        f'fill="{_rgb(rgb)}">{escape(t.text)}</text>')


def svg_of(doc: Document, sheet: Sheet, black: bool = False, title: str = "") -> tuple[bytes, list[str]]:
    """The SVG, the size of the sheet's paper, and what the person should know about it."""
    content = _Content()
    left_out = draw(doc, sheet, content, black)
    w, h = sheet.width, sheet.height
    head = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{_n(w)}mm" height="{_n(h)}mm" viewBox="0 0 {_n(w)} {_n(h)}">',
        f"<title>{escape(title)}</title>" if title else "",
        f"<desc>{escape(PROGRAM)}: the sheet at {escape(_scale(doc))}, in millimetres.</desc>",
        f'<g transform="matrix(1 0 0 -1 0 {_n(h)})" stroke-linecap="butt" stroke-linejoin="round" '
        f'font-family={quoteattr("Helvetica, Arial, Liberation Sans, sans-serif")}>',
    ]
    body = "\n".join(x for x in head if x) + "\n" + "\n".join(content.out) + "\n</g>\n</svg>\n"
    notes = []
    if off_sheet(doc, sheet):
        notes.append(OFF_SHEET)
    if left_out:
        notes.append("Some blocks are placed so many times over that only part of them could be drawn.")
    return body.encode("utf-8"), notes


def _scale(doc: Document) -> str:
    from .sheets import scale_words
    return scale_words(doc.settings.annotation_scale)


__all__ = ["svg_of"]
