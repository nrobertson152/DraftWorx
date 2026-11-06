# SPDX-License-Identifier: GPL-3.0-or-later
"""Vector icons in the family's stroke language (24 x 24 grid), painted with Qt.
Ops: ("l", x1, y1, ...) polyline, ("p", ...) polygon, ("pf", ...) filled, ("r", x1, y1, x2, y2) rectangle,
("o", cx, cy, r) circle, ("of", ...) filled circle, ("a", cx, cy, r, start, extent) arc. A "!" uses the accent colour."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

ICONS = {
    "line": [("l", 5, 19, 19, 5), ("of!", 5, 19, 1.8), ("of!", 19, 5, 1.8)],
    "polyline": [("l", 4, 18, 9, 7, 15, 15, 20, 6), ("of!", 4, 18, 1.5), ("of!", 9, 7, 1.5), ("of!", 15, 15, 1.5), ("of!", 20, 6, 1.5)],
    "rectangle": [("r", 4, 6, 20, 18), ("of!", 4, 6, 1.6), ("of!", 20, 18, 1.6)],
    "circle": [("o", 12, 12, 8), ("of!", 12, 12, 1.5)],
    "arc": [("a", 12, 13, 8, 0, 180), ("of!", 20, 13, 1.6), ("of!", 4, 13, 1.6)],
    "ellipse": [("e", 12, 12, 9, 5.5), ("of!", 12, 12, 1.5)],
    "point": [("l", 12, 7, 12, 17), ("l", 7, 12, 17, 12), ("of!", 12, 12, 2)],
    "text": [("l", 6, 6, 18, 6), ("l", 12, 6, 12, 19), ("l!", 9, 19, 15, 19)],
    "move": [("l", 12, 3, 12, 21), ("l", 3, 12, 21, 12), ("l!", 9, 6, 12, 3, 15, 6), ("l!", 9, 18, 12, 21, 15, 18), ("l!", 6, 9, 3, 12, 6, 15), ("l!", 18, 9, 21, 12, 18, 15)],
    "copy": [("r", 4, 4, 15, 15), ("r!", 9, 9, 20, 20)],
    "rotate": [("a", 12, 12, 8, 60, 280), ("l!", 16, 19, 16, 14, 21, 14)],
    "scale": [("r", 4, 10, 14, 20), ("l!", 13, 11, 20, 4), ("l!", 15, 4, 20, 4, 20, 9)],
    "mirror": [("l!", 12, 3, 12, 21), ("p", 9, 7, 9, 17, 3, 17), ("p", 15, 7, 15, 17, 21, 17)],
    "erase": [("l", 4, 7, 20, 7), ("l", 9, 7, 9, 4, 15, 4, 15, 7), ("l", 6, 7, 7, 20, 17, 20, 18, 7), ("l!", 10, 11, 10, 16), ("l!", 14, 11, 14, 16)],
    "trim": [("l", 12, 3, 12, 21), ("l", 3, 12, 9, 12), ("l!", 15, 12, 21, 12), ("l!", 16, 9, 20, 15), ("l!", 20, 9, 16, 15)],
    "extend": [("l", 19, 3, 19, 21), ("l", 3, 12, 12, 12), ("l!", 12, 12, 19, 12), ("l!", 15, 9, 18, 12, 15, 15)],
    "offset": [("l", 4, 8, 20, 8), ("l!", 4, 16, 20, 16), ("l", 12, 9, 12, 15)],
    "fillet": [("l", 4, 20, 4, 12), ("a!", 12, 12, 8, 90, 90), ("l", 12, 4, 20, 4)],
    "chamfer": [("l", 4, 20, 4, 11), ("l!", 4, 11, 11, 4), ("l", 11, 4, 20, 4)],
    "array": [("r", 4, 4, 9, 9), ("r!", 15, 4, 20, 9), ("r!", 4, 15, 9, 20), ("r!", 15, 15, 20, 20)],
    "dimension": [("l", 4, 8, 4, 18), ("l", 20, 8, 20, 18), ("l!", 4, 13, 20, 13), ("l!", 7, 10, 4, 13, 7, 16), ("l!", 17, 10, 20, 13, 17, 16)],
    "hatch": [("r", 4, 4, 20, 20), ("l!", 4, 12, 12, 4), ("l!", 4, 20, 20, 4), ("l!", 12, 20, 20, 12)],
    "block": [("p", 12, 3, 20, 7.5, 20, 16.5, 12, 21, 4, 16.5, 4, 7.5), ("l", 4, 7.5, 12, 12, 20, 7.5), ("l!", 12, 12, 12, 21)],
    "measure": [("l", 3, 16, 16, 3, 21, 8, 8, 21, 3, 16), ("l!", 8, 11, 10, 13), ("l!", 11, 8, 13, 10), ("l!", 14, 5, 16, 7)],
    "layers": [("p", 12, 4, 21, 9, 12, 14, 3, 9), ("l!", 3, 13, 12, 18, 21, 13)],
    "search": [("o", 10, 10, 6.5), ("l", 14.8, 14.8, 20.5, 20.5)],
    "undo": [("a", 12, 13, 7, 200, -220), ("l!", 5, 5, 5, 11, 11, 11)],
    "redo": [("a", 12, 13, 7, -20, 220), ("l!", 19, 5, 19, 11, 13, 11)],
    "new": [("p", 6, 3, 14, 3, 19, 8, 19, 21, 6, 21), ("l!", 14, 3, 14, 8, 19, 8)],
    "open": [("l", 3, 19, 3, 6, 9, 6, 11, 9, 20, 9, 20, 12), ("p!", 3, 19, 6, 12, 22, 12, 19, 19)],
    "save": [("p", 4, 4, 17, 4, 20, 7, 20, 20, 4, 20), ("r!", 8, 13, 16, 20), ("l", 8, 4, 8, 9, 15, 9, 15, 4)],
    "fit": [("l", 4, 9, 4, 4, 9, 4), ("l", 15, 4, 20, 4, 20, 9), ("l", 20, 15, 20, 20, 15, 20), ("l", 9, 20, 4, 20, 4, 15), ("r!", 9, 9, 15, 15)],
    "eye": [("e", 12, 12, 9, 5), ("of!", 12, 12, 2.2)],
    "eye_off": [("e", 12, 12, 9, 5), ("l!", 5, 19, 19, 5)],
    "lock": [("r", 6, 11, 18, 20), ("a!", 12, 11, 4, 0, 180)],
    "unlock": [("r", 6, 11, 18, 20), ("a!", 12, 11, 4, 20, 150)],
    "plus": [("l", 12, 5, 12, 19), ("l", 5, 12, 19, 12)],
    "more": [("of", 6, 12, 1.5), ("of", 12, 12, 1.5), ("of", 18, 12, 1.5)],
    "check": [("l!", 6, 12.5, 10.5, 17, 18, 7)],
}


def paint(p: QPainter, name: str, x: float, y: float, size: float, color: str, accent: str, width: float = 1.6) -> None:
    k = size / 24.0
    pen_w = max(1.0, width * k)
    for op in ICONS.get(name, [("r", 4, 4, 20, 20)]):
        kind = op[0].rstrip("!")
        col = QColor(accent if op[0].endswith("!") else color)
        args = op[1:]
        p.setPen(QPen(col, pen_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        pts = [QPointF(x + args[i] * k, y + args[i + 1] * k) for i in range(0, len(args) - 1, 2)] if kind in ("l", "p", "pf") else []
        if kind == "l":
            p.drawPolyline(QPolygonF(pts))
        elif kind in ("p", "pf"):
            if kind == "pf":
                p.setBrush(col)
            p.drawPolygon(QPolygonF(pts))
        elif kind == "r":
            p.drawRoundedRect(QRectF(x + args[0] * k, y + args[1] * k, (args[2] - args[0]) * k, (args[3] - args[1]) * k), 1.2 * k, 1.2 * k)
        elif kind in ("o", "of"):
            if kind == "of":
                p.setBrush(col)
                p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QPointF(x + args[0] * k, y + args[1] * k), args[2] * k, args[2] * k)
        elif kind == "e":
            p.drawEllipse(QPointF(x + args[0] * k, y + args[1] * k), args[2] * k, args[3] * k)
        elif kind == "a":
            cx, cy, r, st, ex = args
            path = QPainterPath()
            rect = QRectF(x + (cx - r) * k, y + (cy - r) * k, 2 * r * k, 2 * r * k)
            path.arcMoveTo(rect, st)
            path.arcTo(rect, st, ex)
            p.drawPath(path)


def icon(name: str, color: str, accent: str, size: int = 20) -> QIcon:
    ic = QIcon()
    for scale in (1, 2):
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.GlobalColor.transparent)
        pm.setDevicePixelRatio(scale)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        paint(p, name, 0, 0, size, color, accent)
        p.end()
        ic.addPixmap(pm)
    return ic
