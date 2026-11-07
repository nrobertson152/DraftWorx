# SPDX-License-Identifier: GPL-3.0-or-later
"""Printing on a printer: what `core.printing` says a sheet prints, painted at its size in millimetres, so a printer prints it to scale."""
from __future__ import annotations

from PySide6.QtCore import QMarginsF, QPointF, QSizeF, Qt
from PySide6.QtGui import QBrush, QColor, QPageLayout, QPageSize, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtPrintSupport import QPrinter

from ..core import sheets
from ..core.model import Document
from ..core.printing import Pen, draw
from ..core.shapes import Text
from .scene import text_path


class _Sink:
    def __init__(self, path: QPainterPath):
        self.path = path

    def moveTo(self, x, y):
        self.path.moveTo(x, y)

    def lineTo(self, x, y):
        self.path.lineTo(x, y)

    def cubicTo(self, x1, y1, x2, y2, x, y):
        self.path.cubicTo(x1, y1, x2, y2, x, y)

    def closeSubpath(self):
        self.path.closeSubpath()


class _QtPage:
    """A printing.Page on a QPainter whose units are millimetres, y up."""

    def __init__(self, painter: QPainter, doc: Document):
        self.p = painter
        self.doc = doc

    def _pen(self, pen: Pen) -> QPen:
        q = QPen(QColor(f"#{pen.rgb:06x}"))
        q.setWidthF(pen.width)
        q.setCapStyle(Qt.PenCapStyle.FlatCap)
        q.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if pen.dashes and pen.width > 0.0:
            q.setDashPattern([max(d, 1e-3) / pen.width for d in pen.dashes])
        return q

    def stroke(self, curve, pen: Pen) -> None:
        path = QPainterPath()
        curve.emit(_Sink(path))
        self.p.setPen(self._pen(pen))
        self.p.setBrush(Qt.BrushStyle.NoBrush)
        self.p.drawPath(path)

    def fill(self, loops, rgb: int) -> None:
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        sink = _Sink(path)
        for loop in loops:
            first = True
            for c in loop:
                c.emit(sink, first)
                first = False
            path.closeSubpath()
        self.p.fillPath(path, QBrush(QColor(f"#{rgb:06x}")))

    def dot(self, p, pen: Pen) -> None:
        r = max(pen.width, 0.3) / 2.0
        self.p.setPen(Qt.PenStyle.NoPen)
        self.p.setBrush(QBrush(QColor(f"#{pen.rgb:06x}")))
        self.p.drawEllipse(QPointF(p[0], p[1]), r, r)

    def text(self, t: Text, rgb: int) -> None:
        self.p.fillPath(text_path(t, self.doc), QBrush(QColor(f"#{rgb:06x}")))


def page_layout(sheet: sheets.Sheet) -> QPageLayout:
    upright = sheets.size_of(sheet.paper, False)
    size = QPageSize(QSizeF(*upright), QPageSize.Unit.Millimeter, sheet.paper)
    orientation = QPageLayout.Orientation.Landscape if sheet.width > sheet.height else QPageLayout.Orientation.Portrait
    return QPageLayout(size, orientation, QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)


def print_sheet(doc: Document, printer: QPrinter, black: bool = False, code=None) -> int:
    """Print the drawing's sheet on this printer, to scale, with the revision code `code` (core.revcode) if given. Returns how many objects
    were left out because there was too much to draw. Nothing goes to the printer if the code has no room (CodeError)."""
    sheet = sheets.sheet_of(doc)
    if sheet is None:
        raise ValueError("The drawing has no sheet yet.")
    if code is not None:
        from ..core.revcode import CodeError, why_not
        problem = why_not(doc, sheet)
        if problem:
            raise CodeError(problem)
    printer.setFullPage(True)
    printer.setPageLayout(page_layout(sheet))
    painter = QPainter()
    if not painter.begin(printer):
        raise OSError("The printer couldn't be started.")
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        k = printer.resolution() / 25.4
        page = printer.pageLayout().fullRectPixels(printer.resolution())
        # millimetres up from the paper's lower-left corner, to the printer's pixels down from its top
        painter.setTransform(QTransform(k, 0.0, 0.0, -k, page.x(), page.y() + sheet.height * k))
        return draw(doc, sheet, _QtPage(painter, doc), black, code)
    finally:
        painter.end()
