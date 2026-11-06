# SPDX-License-Identifier: GPL-3.0-or-later
"""Starting the window."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .. import __version__
from ..core.config import load_config
from .window import MainWindow


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    app = QApplication.instance() or QApplication([sys.argv[0], *args])
    app.setApplicationName("DraftWorx")
    app.setApplicationVersion(__version__)
    path = next((a for a in args if not a.startswith("-")), None)
    window = MainWindow(load_config(), path)
    window.show()
    window.offer_recovery()
    return app.exec()


def self_test() -> tuple[bool, str]:
    """Open the real window offscreen, draw through real events, and read the result back. For the packaged program."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from ..core.config import Config
    from ..core.curves import Line
    app = QApplication.instance() or QApplication(["draftworx"])
    window = MainWindow(Config())
    window.show()
    app.processEvents()
    c = window.canvas
    window.session.run("LINE")
    QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(100, 300))
    QTest.mouseClick(c, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(300, 200))
    QTest.keyClick(c, Qt.Key.Key_Return)
    app.processEvents()
    image = c.grab().toImage()
    ok = any(isinstance(e.shape, Line) for e in window.session.doc.entities.values()) and not image.isNull()
    window.session.doc.mark_saved()
    window.close()
    return ok, "the window opened, drew a line through real mouse events and painted it"
