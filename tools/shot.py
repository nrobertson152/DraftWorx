#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pictures of the real window, for looking at the design: python tools/shot.py OUT_DIR"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from draftworx.core.config import Config  # noqa: E402
from draftworx.core.curves import Arc, Circle, Line, Polyline  # noqa: E402
from draftworx.core.model import Layer, Linetype, Style  # noqa: E402
from draftworx.core.shapes import Text  # noqa: E402
from draftworx.core.vec import Vec  # noqa: E402
from draftworx.gui.window import MainWindow  # noqa: E402


def sample(doc):
    """A small bracket, drawn the way someone would."""
    with doc.transaction("Sample") as t:
        t.put_linetype(Linetype("Center", "Center line", (12.0, -3.0, 3.0, -3.0)))
        t.put_layer(Layer("Outline", 7, "Continuous", 50))
        t.put_layer(Layer("Holes", 5))
        t.put_layer(Layer("Center lines", 1, "Center"))
        t.put_layer(Layer("Notes", 3))
        outline = Style("Outline")
        pts = (Vec(0, 0), Vec(160, 0), Vec(160, 40), Vec(120, 40), Vec(120, 100), Vec(0, 100))
        t.add(Polyline(pts, (0.0, 0.0, 0.0, 0.0, 0.0, 0.0), True), outline)
        for (x, y, r) in ((30, 30, 12), (30, 70, 12), (140, 20, 8)):
            t.add(Circle(Vec(x, y), r), Style("Holes"))
            t.add(Line(Vec(x - r - 6, y), Vec(x + r + 6, y)), Style("Center lines"))
            t.add(Line(Vec(x, y - r - 6), Vec(x, y + r + 6)), Style("Center lines"))
        t.add(Arc(Vec(85, 60), 22, 0.0, math.pi), outline)
        t.add(Line(Vec(63, 60), Vec(107, 60)), outline)
        t.add(Text(Vec(0, -14), 5.0, "BRACKET, 6 mm PLATE"), Style("Notes"))
    doc.forget_history()


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication(["draftworx"])
    app.setFont(QFont("DejaVu Sans"))
    w = MainWindow(Config())
    w.show()
    sample(w.session.doc)
    w.canvas.scene.rebuild()
    w.panel.refresh(True)
    w.canvas.fit()
    app.processEvents()
    c = w.canvas

    def at(x, y):
        return QPoint(*map(round, c.view.to_screen(x, y)))

    def move(x, y):
        QTest.mouseMove(c, at(x, y))
        app.processEvents()

    def click(x, y):
        move(x, y)
        QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(x, y))
        app.processEvents()

    def save(name):
        c._inside = True
        c.update()
        app.processEvents()
        w.grab().save(str(out / name))

    move(200, 150)
    save("01-drawing.png")
    click(0, 50)           # a polyline edge
    move(200, 150)
    save("02-selected.png")
    QTest.keyClick(c, Qt.Key.Key_Escape)
    w.session.run("LINE")
    click(160, 40)
    move(121, 40.5)
    save("03-endpoint-snap.png")
    move(120, 70)
    move(150, 70.5)
    save("04-polar.png")
    w.session.cancel()
    w.session.run("LINE")
    click(165, 10)
    # pause on a corner to track from it, then line up
    move(120.5, 100.5)
    w.session.tick(w.session.now + 0.1)
    w.session.tick(w.session.now + 0.5)
    move(140, 90)
    move(165, 100.5)
    save("05-tracking.png")
    w.session.cancel()
    w.session.run("CIRCLE")
    click(85, 60)
    move(100, 80)
    QTest.keyClicks(c, "22")
    app.processEvents()
    save("06-typing.png")
    w.session.cancel()
    QTest.keyClicks(c, "li")
    app.processEvents()
    move(100, 80)
    save("07-command-typing.png")
    w.session.input = ""
    w.set_theme("modern_dark")
    move(80, 60)
    save("08-dark.png")
    w.set_theme("modern_light")
    w.session.doc.mark_saved()
    w.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
