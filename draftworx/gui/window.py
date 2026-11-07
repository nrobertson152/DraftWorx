# SPDX-License-Identifier: GPL-3.0-or-later
"""The main window: the drawing in the middle, tools on the left, what's relevant on the right, and one sentence at the bottom saying what to do."""
from __future__ import annotations

import math
import os
import secrets
import time
from pathlib import Path

from contextlib import contextmanager

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QToolButton, QVBoxLayout, QWidget)

from .. import __version__
from ..core import recovery, sheets
from ..core.commands import COMMANDS, find
from ..core.config import Config, save_config
from ..core.dxf import VERSIONS
from ..core.files import Opened, export_dxf, open_drawing, save_drawing
from ..core.model import Document
from ..core.native import FormatError
from ..core.safefile import ChangedOnDisk, SaveError
from ..core.session import Session
from ..core.units import format_length
from . import icons, theme as themes
from .canvas import Canvas
from .palette import Entry, Palette
from .panel import ContextPanel
from ..integrations.revisionworx import RwxError
from .revision import AddDialog, Asker, CheckInDialog, RevisionChip

FILE_FILTER = "Drawings (*.draftworx *.dxf);;DraftWorx drawings (*.draftworx);;DXF files (*.dxf)"
SAVE_FILTER = "DraftWorx drawing (*.draftworx);;DXF file (*.dxf)"
RAIL = (("Draw", ("LINE", "PLINE", "RECTANGLE", "CIRCLE", "ARC", "ELLIPSE", "HATCH")),
        ("Modify", ("MOVE", "COPY", "ROTATE", "SCALE", "MIRROR", "TRIM", "OFFSET", "FILLET", "ERASE")),
        ("Annotate", ("TEXT", "DIMENSION")),
        ("Blocks", ("INSERT", "BLOCK")))
AUTOSAVE_MS = 60_000


class MainWindow(QMainWindow):
    def __init__(self, config: Config | None = None, path: str | None = None):
        super().__init__()
        self.config = config or Config()
        self.theme = themes.resolve(self.config.theme)
        QApplication.instance().setStyleSheet(themes.stylesheet(self.theme))
        self.session = Session()
        self.session.drafting.polar_step = math.radians(self.config.polar_degrees)
        self.opened = Opened(self.session.doc)
        self.session_id = f"{os.getpid()}-{secrets.token_hex(6)}"
        self._saved_at_autosave = -1
        self.revision_status = None         # what RevisionWorx last said about the drawing's file (integrations/revisionworx.py)
        self._asker = Asker(self)
        self._asker.answered.connect(self._revision_answered)
        self._asked_at = 0.0
        self._question = 0
        self.canvas = Canvas(self.session, self.theme, self.config)
        self.panel = ContextPanel(self.session, self.theme)
        self.setWindowTitle("DraftWorx")
        self._build()
        self._apply_icon()
        self.resize(1280, 800)
        self.canvas.state_changed.connect(self._state_changed)
        self.canvas.context_menu.connect(self._context_menu)
        self.panel.message.connect(self._say)
        self.panel.release_focus.connect(self.canvas.setFocus)
        QApplication.instance().installEventFilter(self)
        self._autosave_timer = QTimer(self)
        self._autosave_timer.setInterval(AUTOSAVE_MS)
        self._autosave_timer.timeout.connect(self._autosave)
        self._autosave_timer.start()
        self._state_changed()
        self.canvas.setFocus()
        if path:
            self.open_path(path)
        self.refresh_revision()

    # ---------------------------------------------------------------------------------------------- building it

    def _apply_icon(self) -> None:
        pm = QPixmap(64, 64)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        icons.paint(p, "polyline", 0, 0, 64, self.theme.ui["accent"], self.theme.ui["accent"], 2.2)
        p.end()
        self.setWindowIcon(QIcon(pm))

    def _icon(self, name: str, size: int = 20) -> QIcon:
        return icons.icon(name, self.theme.ui["text"], self.theme.ui["accent"], size)

    def _build(self) -> None:
        self._build_menus()
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        middle = QHBoxLayout()
        middle.setContentsMargins(0, 0, 0, 0)
        middle.setSpacing(0)
        self.rail = self._build_rail()
        middle.addWidget(self.rail)
        middle.addWidget(self.canvas, 1)
        middle.addWidget(self.panel)
        outer.addLayout(middle, 1)
        outer.addWidget(self._build_status())
        self.setCentralWidget(central)

    def _tool_button(self, name: str, tip: str, action) -> QToolButton:
        b = QToolButton()
        b.setObjectName("tool")
        b.setIcon(self._icon(name))
        b.setIconSize(QSize(20, 20))
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(action)
        b.setProperty("icon_name", name)
        return b

    def _build_rail(self) -> QFrame:
        rail = QFrame()
        rail.setObjectName("rail")
        rail.setFixedWidth(42)
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(5, 6, 5, 6)
        lay.setSpacing(2)
        lay.addWidget(self._tool_button("search", "All commands (Ctrl+K): find anything by name", self.show_palette))
        for group, names in RAIL:
            rule = QFrame()
            rule.setObjectName("railRule")
            lay.addWidget(rule)
            for name in names:
                info = COMMANDS[name]
                keys = info.aliases[0] if info.aliases else ""
                tip = f"{name.capitalize()} ({keys}): {info.help}" if keys else f"{name.capitalize()}: {info.help}"
                lay.addWidget(self._tool_button(info.icon or "more", tip, lambda _=False, n=name: self._run(n)))
        lay.addStretch(1)
        lay.addWidget(self._tool_button("fit", "Show the whole drawing (Home)", lambda: self.canvas.fit()))
        return rail

    def _build_status(self) -> QFrame:
        strip = QFrame()
        strip.setObjectName("statusStrip")
        h = QHBoxLayout(strip)
        h.setContentsMargins(10, 4, 8, 4)
        h.setSpacing(8)
        self.prompt = QLabel("")
        self.prompt.setObjectName("prompt")
        h.addWidget(self.prompt)
        self.options_row = QHBoxLayout()
        self.options_row.setSpacing(4)
        h.addLayout(self.options_row)
        self.message = QLabel("")
        self.message.setObjectName("message")
        h.addWidget(self.message)
        h.addStretch(1)
        self.revision = RevisionChip()
        self.revision.act.connect(lambda what: self.check_in() if what == "check-in" else self.check_out())
        h.addWidget(self.revision)
        self.coords = QLabel("")
        self.coords.setObjectName("small")
        self.coords.setMinimumWidth(150)
        self.coords.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(self.coords)
        self.toggles = {}
        for key, label, tip in (("f3", "Snap", "Snap to points (F3). Hold Ctrl for no snapping at this moment"),
                                ("f10", "Align", "Alignment lines from points you pause on, and from your last point (F10)"),
                                ("f8", "Ortho", "Keep to horizontal and vertical (F8). Hold Shift to do the opposite for a moment"),
                                ("f9", "Grid", "Snap to the grid (F9)")):
            b = QToolButton()
            b.setObjectName("toggle")
            b.setText(label)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, k=key: self._toggle(k))
            h.addWidget(b)
            self.toggles[key] = b
        self.polar = QComboBox()
        self.polar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.polar.setToolTip("How often alignment lines are offered from your last point")
        for deg in (90, 45, 30, 15, 10, 5):
            self.polar.addItem(f"{deg}°", deg)
        at = self.polar.findData(int(self.config.polar_degrees))
        self.polar.setCurrentIndex(at if at >= 0 else 1)
        self.polar.activated.connect(self._polar_changed)
        h.addWidget(self.polar)
        return strip

    def _build_menus(self) -> None:
        bar = self.menuBar()
        file = bar.addMenu("&File")
        self.actions_by_name: dict[str, QAction] = {}
        self._action(file, "New", self.new_drawing, QKeySequence.StandardKey.New, "new")
        templates = file.addMenu("New from template")
        for name in sheets.TEMPLATES:
            a = templates.addAction(name)
            a.triggered.connect(lambda _=False, n=name: self.new_from_template(n))
        self._action(file, "Open…", self.open_dialog, QKeySequence.StandardKey.Open, "open")
        self._action(file, "Save", self.save, QKeySequence.StandardKey.Save, "save")
        self._action(file, "Save as…", self.save_as, QKeySequence.StandardKey.SaveAs)
        file.addSeparator()
        self._action(file, "Insert a drawing…", self.insert_drawing_dialog, None, "insert")
        file.addSeparator()
        self._action(file, "Print…", self.print_dialog, QKeySequence.StandardKey.Print, "print")
        self._action(file, "Export PDF…", self.export_pdf_dialog, QKeySequence("Ctrl+Shift+P"), "pdf")
        self._action(file, "Export SVG…", self.export_svg_dialog)
        file.addSeparator()
        self.revision_menu = file.addMenu("RevisionWorx")
        self._action(self.revision_menu, "Check out", self.check_out)
        self._action(self.revision_menu, "Check in…", self.check_in)
        self._action(self.revision_menu, "Undo check-out…", self.undo_check_out)
        self._action(self.revision_menu, "Add to RevisionWorx…", self.add_to_revisionworx)
        self._action(self.revision_menu, "Show in RevisionWorx", self.show_in_revisionworx)
        self.revision_menu.menuAction().setVisible(False)
        file.addSeparator()
        self._action(file, "Exit", self.close, QKeySequence.StandardKey.Quit)
        edit = bar.addMenu("&Edit")
        self._action(edit, "Undo", self.undo, QKeySequence.StandardKey.Undo, "undo")
        self._action(edit, "Redo", self.redo, QKeySequence("Ctrl+Y"), "redo")
        extra_redo = QAction(self)
        extra_redo.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        extra_redo.triggered.connect(self.redo)
        self.addAction(extra_redo)
        edit.addSeparator()
        self._action(edit, "Select all", self.select_all, QKeySequence.StandardKey.SelectAll)
        self._action(edit, "Find a command…", self.show_palette, QKeySequence("Ctrl+K"), "search")
        view = bar.addMenu("&View")
        self._action(view, "Show the whole drawing", lambda: self.canvas.fit(), QKeySequence("Home"), "fit")
        self._action(view, "Zoom in", lambda: self.canvas.zoom(1.5), QKeySequence("Ctrl+="))
        self._action(view, "Zoom out", lambda: self.canvas.zoom(1 / 1.5), QKeySequence("Ctrl+-"))
        view.addSeparator()
        self.panel_action = self._action(view, "Panel", self._toggle_panel, QKeySequence("Ctrl+1"))
        self.panel_action.setCheckable(True)
        self.panel_action.setChecked(True)
        self.grid_action = self._action(view, "Grid lines", self._toggle_grid)
        self.grid_action.setCheckable(True)
        self.grid_action.setChecked(self.config.show_grid)
        self.weight_action = self._action(view, "Line weights", self._toggle_weights)
        self.weight_action.setCheckable(True)
        self.weight_action.setChecked(self.config.show_weights)
        themes_menu = view.addMenu("Theme")
        for name, t in themes.THEMES.items():
            a = themes_menu.addAction(t["label"])
            a.triggered.connect(lambda _=False, n=name: self.set_theme(n))
        groups: dict[str, list] = {}
        for info in COMMANDS.values():
            groups.setdefault(info.group, []).append(info)
        for group in ("Draw", "Modify", "Annotate", "Blocks", "Output"):
            if group not in groups:
                continue
            menu = bar.addMenu(group)
            for info in groups[group]:
                keys = f"\t{info.aliases[0]}" if info.aliases else ""
                a = menu.addAction(info.name.capitalize() + keys)
                a.setToolTip(info.help)
                a.triggered.connect(lambda _=False, n=info.name: self._run(n))
        help_menu = bar.addMenu("&Help")
        self._action(help_menu, "About DraftWorx", self._about)

    def _action(self, menu, label: str, fn, keys=None, icon_name: str | None = None) -> QAction:
        a = QAction(label, self)
        if keys is not None:
            a.setShortcut(keys)
        if icon_name:
            a.setIcon(self._icon(icon_name, 16))
        a.triggered.connect(lambda _=False: fn())
        menu.addAction(a)
        self.actions_by_name[label] = a
        return a

    # ------------------------------------------------------------------------------------------- keeping it current

    def _say(self, text: str) -> None:
        self.session.message = text
        self._state_changed()

    def _state_changed(self) -> None:
        s = self.session
        self.prompt.setText(s.prompt)
        self.message.setText(s.message)
        self.revision.show_status(self.revision_status, s.doc.dirty)
        st = s.doc.settings
        p = s.snap.point if s.snap is not None else s.cursor
        self.coords.setText(f"{format_length(p[0], st.linear_format, st.linear_decimals, st.units)}, "
                            f"{format_length(p[1], st.linear_format, st.linear_decimals, st.units)}")
        d = s.drafting
        for key, on in (("f3", d.snap), ("f10", d.tracking), ("f8", d.ortho), ("f9", d.grid)):
            self.toggles[key].setChecked(on)
        self._show_options(s.options)
        self.panel.refresh()
        self._title()
        self.actions_by_name["Undo"].setText(f"Undo {s.doc.undo_label}" if s.doc.can_undo else "Undo")
        self.actions_by_name["Undo"].setEnabled(s.doc.can_undo or s.active)
        self.actions_by_name["Redo"].setText(f"Redo {s.doc.redo_label}" if s.doc.can_redo else "Redo")
        self.actions_by_name["Redo"].setEnabled(s.doc.can_redo)

    def _show_options(self, options: tuple[str, ...]) -> None:
        if getattr(self, "_shown_options", None) == options:
            return
        self._shown_options = options
        while self.options_row.count():
            w = self.options_row.takeAt(0).widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for name in options:
            b = QPushButton(name)
            b.setObjectName("chip")
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, n=name: self._choose_option(n))
            self.options_row.addWidget(b)

    def _choose_option(self, name: str) -> None:
        s = self.session
        s.input = name
        s.key("enter")
        self.canvas.update()
        self._state_changed()

    def _title(self) -> None:
        name = self.opened.path.name if self.opened.path else "Untitled"
        self.setWindowTitle(f"{'*' if self.session.doc.dirty else ''}{name} - DraftWorx")

    def _run(self, name: str) -> None:
        self.session.run(name)
        self.canvas.setFocus()
        self.canvas.update()
        self._state_changed()

    def _toggle(self, key: str) -> None:
        self.session.key(key)
        self.canvas.update()
        self._state_changed()

    def _polar_changed(self, i: int) -> None:
        deg = self.polar.itemData(i)
        self.config.polar_degrees = float(deg)
        self.session.drafting.polar_step = math.radians(deg)
        save_config(self.config)
        self.canvas.setFocus()

    def _toggle_panel(self) -> None:
        self.panel.setVisible(not self.panel.isVisible())
        self.panel_action.setChecked(self.panel.isVisible())

    def _toggle_grid(self) -> None:
        self.config.show_grid = not self.config.show_grid
        save_config(self.config)
        self.canvas.redraw()

    def _toggle_weights(self) -> None:
        self.config.show_weights = not self.config.show_weights
        save_config(self.config)
        self.canvas.redraw()

    def set_theme(self, name: str) -> None:
        self.config.theme = name
        save_config(self.config)
        self.theme = themes.resolve(name)
        QApplication.instance().setStyleSheet(themes.stylesheet(self.theme))
        self.canvas.set_theme(self.theme)
        self.panel.set_theme(self.theme)
        for b in self.rail.findChildren(QToolButton):
            name_ = b.property("icon_name")
            if name_:
                b.setIcon(self._icon(name_))

    def eventFilter(self, obj, ev) -> bool:
        """Typing always reaches the drawing, wherever the keyboard focus happens to have got to, unless it's somewhere you can type."""
        if ev.type() == QEvent.Type.KeyPress and QApplication.activeWindow() is self and QApplication.activeModalWidget() is None and not QApplication.activePopupWidget():
            focus = QApplication.focusWidget()
            if focus is not self.canvas and not isinstance(focus, (QLineEdit, QPlainTextEdit, QAbstractSpinBox)) \
                    and not (isinstance(focus, QComboBox) and focus.isEditable()) and isinstance(obj, QWidget) and obj is focus:
                if not (ev.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)) or ev.key() in (Qt.Key.Key_Shift, Qt.Key.Key_Control):
                    if ev.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                        self.session.key("tab", shift=ev.key() == Qt.Key.Key_Backtab)
                        self.canvas.update()
                        return True
                    if self.canvas.handle_key(ev):
                        return True
        return super().eventFilter(obj, ev)

    # ---------------------------------------------------------------------------------------------------- edit

    def undo(self) -> None:
        self.session.undo()
        self.canvas.redraw()
        self._state_changed()

    def redo(self) -> None:
        self.session.redo()
        self.canvas.redraw()
        self._state_changed()

    def select_all(self) -> None:
        doc = self.session.doc
        self.session.set_selection(i for i, e in doc.entities.items() if not doc.is_hidden(e.style) and not doc.is_locked(e.style))
        self.canvas._after_event()

    def show_palette(self) -> None:
        extras = [Entry("Save", "Save the drawing", self.save, "Ctrl+S"), Entry("Open", "Open a drawing", self.open_dialog, "Ctrl+O"),
                  Entry("New", "Start a new drawing", self.new_drawing, "Ctrl+N"), Entry("Save as", "Save under another name", self.save_as),
                  Entry("Export as DXF R12", "A copy in the oldest kind of DXF, for machines and programs that need it", self.export_r12),
                  Entry("Insert a drawing", "Bring another drawing in as a block, and place it", self.insert_drawing_dialog),
                  Entry("Print", "Print the sheet, to scale", self.print_dialog, "Ctrl+P"),
                  Entry("Export PDF", "The sheet as a PDF, to scale", self.export_pdf_dialog, "Ctrl+Shift+P"),
                  Entry("Export SVG", "The sheet as an SVG in millimetres, for Inkscape, a browser or a laser cutter", self.export_svg_dialog),
                  Entry("Show the whole drawing", "Zoom to fit", lambda: self.canvas.fit(), "Home"),
                  Entry("Select all", "Select everything", self.select_all, "Ctrl+A"), Entry("Undo", "Undo the last thing", self.undo, "Ctrl+Z"),
                  Entry("Redo", "Redo what was undone", self.redo, "Ctrl+Y")]
        for name, t in themes.THEMES.items():
            extras.append(Entry(f"Theme: {t['label']}", "Change how the program looks", lambda n=name: self.set_theme(n)))
        for name in sheets.TEMPLATES:
            extras.append(Entry(f"New: {name}", "A new drawing with a border and title block", lambda n=name: self.new_from_template(n)))
        for label, help_, fn in (("Check out", "Check the drawing out in RevisionWorx, so it's yours to change", self.check_out),
                                 ("Check in…", "Keep your changes in RevisionWorx as a new revision", self.check_in),
                                 ("Undo check-out…", "Give the check-out up in RevisionWorx", self.undo_check_out),
                                 ("Add to RevisionWorx…", "Put the drawing under revision control in a project", self.add_to_revisionworx),
                                 ("Show in RevisionWorx", "Show the drawing's document in RevisionWorx", self.show_in_revisionworx)):
            if self.revision_menu.menuAction().isVisible() and self.actions_by_name[label].isEnabled():
                extras.append(Entry(label.rstrip("…"), help_, fn))
        dialog = Palette(self, self.session, extras)
        c = self.mapToGlobal(self.rect().center())
        dialog.move(c.x() - 260, self.mapToGlobal(self.rect().topLeft()).y() + 60)
        dialog.exec()
        if dialog.chosen is not None:
            dialog.chosen.run()
        self.canvas.setFocus()
        self.canvas.update()
        self._state_changed()

    def _context_menu(self, at) -> None:
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        s = self.session
        if s.selection:
            for name in ("MOVE", "COPY", "ROTATE", "SCALE", "MIRROR", "ERASE"):
                info = find(name)
                if info is not None:
                    menu.addAction(self._icon(info.icon, 16), name.capitalize()).triggered.connect(lambda _=False, n=name: self._run(n))
        else:
            if s.last_command:
                menu.addAction(f"Repeat {s.last_command.capitalize()}").triggered.connect(lambda: self._run(s.last_command))
            menu.addAction("Show the whole drawing").triggered.connect(lambda: self.canvas.fit())
            menu.addAction("Find a command…").triggered.connect(self.show_palette)
        menu.exec(at)

    def _about(self) -> None:
        QMessageBox.about(self, "About DraftWorx",
                          f"<b>DraftWorx {__version__}</b><br>A lightweight, offline 2D CAD program.<br><br>"
                          "Copyright © 2026 Nate Robertson. GPL-3.0-or-later.<br>No accounts, telemetry, advertisements or feature limits.<br><br>"
                          "Uses PySide6 (Qt 6), LGPL-3.0.")

    # --------------------------------------------------------------------------------------------------- files

    def _confirm_discard(self) -> bool:
        """True if it's fine to throw the current drawing away."""
        if not self.session.doc.dirty:
            return True
        name = self.opened.path.name if self.opened.path else "This drawing"
        box = QMessageBox(self)
        box.setWindowTitle("DraftWorx")
        box.setText(f"{name} has changes that haven't been saved.")
        save = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        drop = box.addButton("Don't save", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save)
        box.exec()
        clicked = box.clickedButton()
        if clicked is save:
            return self.save()
        return clicked is drop

    def new_drawing(self) -> None:
        if not self._confirm_discard():
            return
        self._adopt(Opened(Document()))

    def _adopt(self, opened: Opened) -> None:
        recovery.forget(self.session_id)
        self.opened = opened
        self.revision_status = None
        self.canvas.set_document(opened.doc)
        self.panel.set_notes(opened.notices)
        self.panel.refresh(True)
        self._state_changed()
        self.canvas.setFocus()
        self.refresh_revision()

    def open_dialog(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open a drawing", "", FILE_FILTER)
        if path:
            self.open_path(path)

    def open_path(self, path: str) -> bool:
        try:
            opened = open_drawing(path)
        except FormatError as exc:
            QMessageBox.warning(self, "DraftWorx", str(exc))
            return False
        self._adopt(opened)
        self.config.remember(str(path))
        save_config(self.config)
        if self.canvas.scene.cut_short:
            self._say("Some blocks here are placed so many times over that only part of them can be drawn. They're all still in the file.")
        return True

    def save(self) -> bool:
        if self.opened.path is None:
            return self.save_as()
        return self._save_to(None)

    def save_as(self) -> bool:
        suggested = str(self.opened.path) if self.opened.path else "Untitled.draftworx"
        path, chosen = QFileDialog.getSaveFileName(self, "Save the drawing", suggested, SAVE_FILTER, "DXF file (*.dxf)" if suggested.casefold().endswith(".dxf") else "")
        if not path:
            return False
        if not Path(path).suffix:
            path += ".dxf" if "dxf" in chosen else ".draftworx"
        return self._save_to(path)

    def new_from_template(self, name: str) -> None:
        if not self._confirm_discard():
            return
        self._adopt(Opened(sheets.from_template(name)))
        self.canvas.fit()
        self._say(f"A new drawing on {name}. Draw at real size, and set the scale with Sheet.")

    def _sheet_for_output(self) -> bool:
        """A drawing printed without a sheet gets one: where its border is, or round what's drawn, on the usual paper, at the largest scale that
        fits. Its own undo step."""
        doc = self.session.doc
        if sheets.sheet_of(doc) is not None:
            return True
        if sheets.drawn(doc) is None and sheets.frame_insert(doc) is None:
            self._say("There's nothing to print yet.")
            return False
        with doc.transaction("Sheet") as t:
            sheets.apply(t, sheets.first_sheet(doc))
        self.canvas.redraw()
        st = doc.settings
        self._say(f"Fitted on {st.paper} across at {sheets.scale_words(st.annotation_scale)}. Change it with Sheet.")
        return True

    def export_pdf_dialog(self) -> bool:
        return self._export_sheet_dialog(".pdf")

    def export_svg_dialog(self) -> bool:
        return self._export_sheet_dialog(".svg")

    def _export_sheet_dialog(self, suffix: str) -> bool:
        if not self._sheet_for_output():
            return False
        kind = suffix[1:].upper()
        suggested = str(self.opened.path.with_suffix(suffix)) if self.opened.path else f"Untitled{suffix}"
        path, _ = QFileDialog.getSaveFileName(self, f"Export {kind}", suggested, f"{kind} (*{suffix})")
        if not path:
            return False
        if not Path(path).suffix:
            path += suffix
        return self.export_pdf(path) if suffix == ".pdf" else self.export_svg(path)

    def export_pdf(self, path: str, black: bool = False) -> bool:
        from ..core.pdf import pdf_of
        return self._export_sheet(path, pdf_of, black)

    def export_svg(self, path: str, black: bool = False) -> bool:
        """The sheet as an SVG in millimetres, to scale, for Inkscape, a browser or a laser cutter's software."""
        from ..core.svg import svg_of
        return self._export_sheet(path, svg_of, black)

    def _export_sheet(self, path: str, make, black: bool) -> bool:
        from ..core.safefile import write_verified
        doc = self.session.doc
        data, notes = make(doc, sheets.sheet_of(doc), black, self.opened.path.stem if self.opened.path else "")

        def verify(back: bytes) -> None:
            if back != data:
                raise ValueError("it read back differently")
        try:
            write_verified(Path(path), data, verify)
        except SaveError as exc:
            QMessageBox.warning(self, "DraftWorx", str(exc))
            return False
        self._say(" ".join([f"Exported {Path(path).name}.", *notes]))
        return True

    def print_dialog(self) -> bool:
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        from .printing import page_layout, print_sheet
        if not self._sheet_for_output():
            return False
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageLayout(page_layout(sheets.sheet_of(self.session.doc)))
        dialog = QPrintDialog(printer, self)
        if dialog.exec() != QPrintDialog.DialogCode.Accepted:
            return False
        try:
            left = print_sheet(self.session.doc, printer, printer.colorMode() == QPrinter.ColorMode.GrayScale)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "DraftWorx", str(exc))
            return False
        doc = self.session.doc
        self._say(" ".join(["Sent to the printer."] + ([sheets.OFF_SHEET] if sheets.off_sheet(doc, sheets.sheet_of(doc)) else [])
                           + (["Some blocks were placed so many times over that only part of them could be printed."] if left else [])))
        return True

    def insert_drawing_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Insert a drawing", "", FILE_FILTER)
        if path:
            self.insert_drawing(path)

    def insert_drawing(self, path: str) -> bool:
        """Another drawing, as a block named after it, then placed like any block."""
        from ..core import blocks
        from ..core.commands.blocks import place
        try:
            other = open_drawing(path).doc
        except FormatError as exc:
            QMessageBox.warning(self, "DraftWorx", str(exc))
            return False
        doc = self.session.doc
        stem = Path(path).stem
        name = stem if blocks.problem_with_name(stem) is None and stem not in doc.blocks else blocks.free_name(doc, stem)
        brought = blocks.bring_in(doc, other, name)
        with doc.transaction(f"Bring in {Path(path).name}") as t:
            blocks.add(t, brought)
        notes = []
        if brought.left_out:
            notes.append(f"{brought.left_out} object{'s' if brought.left_out != 1 else ''} from that file that can't be changed here were left out.")
        if brought.kept:
            notes.append(f"This drawing's own {', '.join(brought.kept[:5])} {'were' if len(brought.kept) != 1 else 'was'} kept, rather than the file's.")
        self.session.run_generator(place(self.session, name), "Insert")
        self.session.message = " ".join(notes)
        self.canvas.setFocus()
        self.canvas.update()
        self._state_changed()
        return True

    def export_r12(self) -> bool:
        """A copy for something that only reads the old format. What it had to simplify is said, and the drawing carries on as it was."""
        suggested = str(self.opened.path.with_name(self.opened.path.stem + " R12.dxf")) if self.opened.path else "Untitled R12.dxf"
        path, _ = QFileDialog.getSaveFileName(self, "Export as DXF R12", suggested, "DXF file (*.dxf)")
        if not path:
            return False
        if not Path(path).suffix:
            path += ".dxf"
        try:
            notes = export_dxf(self.session.doc, path, VERSIONS["R12"])
        except SaveError as exc:
            QMessageBox.warning(self, "DraftWorx", str(exc))
            return False
        self._say(" ".join([f"Exported {Path(path).name}.", *notes]))
        return True

    def _save_to(self, path: str | None, force: bool = False) -> bool:
        target = Path(path) if path else self.opened.path
        if target is not None and target.exists() and not self._may_write(target):
            return False
        try:
            save_drawing(self.opened, path, force)
        except ChangedOnDisk as exc:
            box = QMessageBox(self)
            box.setWindowTitle("DraftWorx")
            box.setText(str(exc))
            over = box.addButton("Overwrite it", QMessageBox.ButtonRole.DestructiveRole)
            copy = box.addButton("Save a copy…", QMessageBox.ButtonRole.AcceptRole)
            box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(copy)
            box.exec()
            if box.clickedButton() is over:
                return self._save_to(path, True)
            if box.clickedButton() is copy:
                return self.save_as()
            return False
        except SaveError as exc:
            QMessageBox.warning(self, "DraftWorx", str(exc))
            return False
        recovery.forget(self.session_id)
        if self.opened.path is not None:
            self.config.remember(str(self.opened.path))
            save_config(self.config)
        self._say(" ".join([f"Saved {self.opened.path.name}.", *self.opened.notices]))
        self.refresh_revision()
        return True

    # ------------------------------------------------------------------------------------------------- RevisionWorx

    def refresh_revision(self, wait: bool = False) -> None:
        """Ask RevisionWorx about the drawing's file again: in the background, or now. Only the newest question's answer counts."""
        self._asked_at = time.monotonic()
        self._question += 1
        path = str(self.opened.path) if self.opened.path is not None else ""
        if wait:
            self._revision_answered(self._question, path, self._asker.now(path))
        else:
            self._asker.ask(self._question, path)

    def _revision_answered(self, question: int, path: str, status) -> None:
        here = str(self.opened.path) if self.opened.path is not None else ""
        if question != self._question or path != here:
            return                  # out of date: a newer question was asked, or it's about a drawing that isn't open any more
        self.revision_status = status
        available = self._asker.available
        self.revision_menu.menuAction().setVisible(available)
        s = status
        controlled = s is not None and s.controlled
        for label, on in (("Check out", controlled and not s.checked_out_by), ("Check in…", controlled and not s.theirs),
                          ("Undo check-out…", controlled and s.mine), ("Add to RevisionWorx…", available and not controlled),
                          ("Show in RevisionWorx", controlled)):
            self.actions_by_name[label].setEnabled(bool(on))
        self.revision.show_status(status, self.session.doc.dirty)

    @contextmanager
    def _waiting(self):
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()

    def _rwx(self):
        rwx = self._asker.rwx()
        if rwx is None:
            QMessageBox.information(self, "DraftWorx", "RevisionWorx isn't installed, or DraftWorx can't find it. Put it on the PATH, or set "
                                    "REVISIONWORX_CLI to its revisionworx program.")
        return rwx

    def _refused(self, exc: Exception) -> bool:
        QMessageBox.warning(self, "DraftWorx", f"RevisionWorx: {exc}")
        self.refresh_revision(wait=True)
        return False

    def _may_write(self, target: Path) -> bool:
        """Whether saving over this file is all right: not if it's someone else's checked-out working file, which would lose their work."""
        s = self._asker.now(str(target)) if self._asker.rwx() is not None else None
        if s is None and self.revision_status is not None and self.opened.path is not None and target == self.opened.path:
            s = self.revision_status        # RevisionWorx didn't answer: what it said last
        if s is None or not s.theirs:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("DraftWorx")
        box.setText(f"{s.name} is checked out by {s.checked_out_by}. Saving here would overwrite the file they're working on.")
        copy = box.addButton("Save a copy…", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(copy)
        box.exec()
        if box.clickedButton() is copy:
            return self.save_as() and False
        return False

    def check_out(self) -> bool:
        self.refresh_revision(wait=True)
        s = self.revision_status
        rwx = self._rwx()
        if rwx is None or s is None or not s.controlled:
            return False
        if s.checked_out_by:
            self._say(f"{s.name} is already checked out by {'you' if s.mine else s.checked_out_by}.")
            return s.mine
        try:
            with self._waiting():
                rwx.check_out(s)
        except RwxError as exc:
            return self._refused(exc)
        self.refresh_revision(wait=True)
        self._say(f"Checked out {s.name}. Change it, save it, then check it in.")
        return True

    def check_in(self, message: str | None = None, parts: bool | None = None) -> bool:
        """Keep the drawing as a new revision: saved first if it has changes, checked out first if nobody has it. Its parts list goes with
        it as a BOM if asked. `message` and `parts` stand in for the dialog."""
        from ..core.parts import parts_list
        from ..integrations.revisionworx import publish_parts
        self.refresh_revision(wait=True)
        s = self.revision_status
        if s is None or not s.controlled:
            return self.add_to_revisionworx()
        rwx = self._rwx()
        if rwx is None:
            return False
        if s.theirs:
            QMessageBox.information(self, "DraftWorx", f"{s.name} is checked out by {s.checked_out_by}. It can be checked in once they have.")
            return False
        doc = self.session.doc
        count = len(parts_list(doc)[0])
        if message is None:
            dialog = CheckInDialog(self, s.name, count)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            message, parts = dialog.text, dialog.parts.isChecked()
        if doc.dirty and not self.save():
            return False
        notes = []
        try:
            with self._waiting():
                answer = rwx.check_in(self.opened.path, message)
                label = ((answer.get("document") or {}).get("latest") or {}).get("label", "?")
                if answer.get("result") == "unchanged":
                    after = rwx.status(self.opened.path)
                    if after.mine:
                        rwx.undo_check_out(after, False)       # nothing to keep, so it isn't left checked out
                    notes.append(f"Nothing had changed since revision {label}, so there's no new one, and it's checked back in.")
                else:
                    notes.append(f"Checked in {s.name} as revision {label}.")
                if parts and count:
                    bom = publish_parts(rwx, rwx.status(self.opened.path), doc, message)
                    if bom is not None:
                        d = bom.get("document") or {}
                        what = "unchanged" if bom.get("result") == "unchanged" else f"revision {(d.get('latest') or {}).get('label', '?')}"
                        notes.append(f"Its parts list is {d.get('number', 'a BOM')}, {what}.")
        except RwxError as exc:
            return self._refused(exc)
        self.refresh_revision(wait=True)
        self._say(" ".join(notes))
        return True

    def undo_check_out(self, put_back: bool | None = None) -> bool:
        self.refresh_revision(wait=True)
        s = self.revision_status
        rwx = self._rwx()
        if rwx is None or s is None or not s.mine:
            return False
        if put_back is None:
            box = QMessageBox(self)
            box.setWindowTitle("DraftWorx")
            box.setText(f"Give up the check-out of {s.name}?")
            box.setInformativeText(f"Keep the file as it is, or put revision {s.revision} back in it, losing what's changed since"
                                   + (", saved or not." if self.session.doc.dirty else "."))
            keep = box.addButton("Keep the file as it is", QMessageBox.ButtonRole.AcceptRole)
            back = box.addButton(f"Put revision {s.revision} back", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(keep)
            box.exec()
            if box.clickedButton() not in (keep, back):
                return False
            put_back = box.clickedButton() is back
        try:
            with self._waiting():
                rwx.undo_check_out(s, put_back)
        except RwxError as exc:
            return self._refused(exc)
        if put_back:
            path = self.opened.path
            try:
                self._adopt(open_drawing(path))
            except FormatError as exc:
                QMessageBox.warning(self, "DraftWorx", str(exc))
                return False
            self.refresh_revision(wait=True)
            self._say(f"{s.name} is checked back in, and revision {s.revision} is back.")
        else:
            self.refresh_revision(wait=True)
            self._say(f"{s.name} is checked back in. The file stays as it is.")
        return True

    def add_to_revisionworx(self, choice: tuple[dict, str, str, str] | None = None) -> bool:
        """Put the drawing under revision control in a project. `choice` (project, number, title, description) stands in for the dialog."""
        rwx = self._rwx()
        if rwx is None:
            return False
        from ..integrations.revisionworx import workspaces_for
        try:
            with self._waiting():
                found = []
                for ws in workspaces_for(rwx, self.opened.path):
                    for p in rwx.projects(ws["location"]):
                        if p.get("mode", "managed") == "managed" and not p.get("archived"):
                            found.append({**p, "workspace": ws["location"], "workspace_name": ws["name"]})
        except RwxError as exc:
            return self._refused(exc)
        if not found:
            QMessageBox.information(self, "DraftWorx", "RevisionWorx has no project to put it in yet. Make one in RevisionWorx first.")
            return False
        if len({p["workspace"] for p in found}) < 2:
            found = [{**p, "workspace_name": ""} for p in found]
        if choice is None:
            from ..integrations.revisionworx import project_for
            fields = sheets.title_block(self.session.doc)
            stem = self.opened.path.stem if self.opened.path else ""
            dialog = AddDialog(self, found, project_for(found, self.opened.path) if self.opened.path else None,
                               fields.get("DRAWING_NO", stem), fields.get("TITLE", stem))
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            choice = (dialog.chosen, dialog.number.text().strip(), dialog.title.text().strip(), dialog.message.text().strip())
        project, number, title, message = choice
        if (self.opened.path is None or self.session.doc.dirty) and not self.save():
            return False
        try:
            with self._waiting():
                answer = rwx.publish(self.opened.path, project["id"], number, title, message or "First issue", workspace=project.get("workspace", ""))
        except RwxError as exc:
            return self._refused(exc)
        d = answer.get("document") or {}
        label = (d.get("latest") or {}).get("label", "?")
        where = d.get("path", "")
        if where and Path(where).resolve() != self.opened.path.resolve():
            if not self.open_path(where):
                return False
            self.refresh_revision(wait=True)
            self._say(f"Added as {d.get('number') or title} revision {label}. This is its copy in the project's folder.")
        else:
            self.refresh_revision(wait=True)
            self._say(f"Added as {d.get('number') or title} revision {label}.")
        return True

    def show_in_revisionworx(self) -> bool:
        rwx = self._rwx()
        if rwx is None or self.opened.path is None:
            return False
        try:
            rwx.show(self.opened.path)
        except RwxError as exc:
            return self._refused(exc)
        return True

    def changeEvent(self, ev) -> None:
        """Coming back to the window, RevisionWorx may have been used in the meantime: what it says is asked again."""
        super().changeEvent(ev)
        if ev.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            if time.monotonic() - self._asked_at > 3.0:
                self.refresh_revision()

    def _autosave(self) -> None:
        doc = self.session.doc
        if doc.dirty and doc.undo_label is not None:
            state = doc._undo[-1].serial
            if state != self._saved_at_autosave and recovery.autosave(doc, self.session_id, self.opened.path):
                self._saved_at_autosave = state

    def offer_recovery(self) -> None:
        """Called on starting. Unsaved work left by a crash is offered, never silently dropped or silently loaded."""
        for found in recovery.leftovers():
            box = QMessageBox(self)
            box.setWindowTitle("DraftWorx")
            box.setText(f"{found.name} wasn't saved the last time DraftWorx closed. Do you want to recover it?")
            rec = box.addButton("Recover", QMessageBox.ButtonRole.AcceptRole)
            drop = box.addButton("Discard it", QMessageBox.ButtonRole.DestructiveRole)
            box.addButton("Decide later", QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(rec)
            box.exec()
            if box.clickedButton() is rec:
                try:
                    doc = recovery.read(found)
                except FormatError as exc:
                    QMessageBox.warning(self, "DraftWorx", str(exc))
                    continue
                self._adopt(Opened(doc, None))
                # it's not saved anywhere yet, so it must count as unsaved work
                doc._saved_at = -1
                recovery.discard(found)
                self._say(f"Recovered. Save it to keep it. It was {found.name}.")
                self._state_changed()
                return
            if box.clickedButton() is drop:
                recovery.discard(found)

    def closeEvent(self, ev) -> None:
        if self.session.active:
            self.session.cancel()
        if not self._confirm_discard():
            ev.ignore()
            return
        recovery.forget(self.session_id)
        save_config(self.config)
        QApplication.instance().removeEventFilter(self)
        ev.accept()
