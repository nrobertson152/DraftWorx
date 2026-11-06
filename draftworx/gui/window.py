# SPDX-License-Identifier: GPL-3.0-or-later
"""The main window: the drawing in the middle, tools on the left, what's relevant on the right, and one sentence at the bottom saying what to do."""
from __future__ import annotations

import math
import os
import secrets
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractSpinBox, QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
                               QPlainTextEdit, QPushButton, QToolButton, QVBoxLayout, QWidget)

from .. import __version__
from ..core import recovery
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

FILE_FILTER = "Drawings (*.draftworx *.dxf);;DraftWorx drawings (*.draftworx);;DXF files (*.dxf)"
SAVE_FILTER = "DraftWorx drawing (*.draftworx);;DXF file (*.dxf)"
RAIL = (("Draw", ("LINE", "PLINE", "RECTANGLE", "CIRCLE", "ARC", "ELLIPSE", "TEXT")),
        ("Modify", ("MOVE", "COPY", "ROTATE", "SCALE", "MIRROR", "ERASE")))
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
        self._action(file, "Open…", self.open_dialog, QKeySequence.StandardKey.Open, "open")
        self._action(file, "Save", self.save, QKeySequence.StandardKey.Save, "save")
        self._action(file, "Save as…", self.save_as, QKeySequence.StandardKey.SaveAs)
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
        for group in ("Draw", "Modify", "Annotate"):
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
                  Entry("Show the whole drawing", "Zoom to fit", lambda: self.canvas.fit(), "Home"),
                  Entry("Select all", "Select everything", self.select_all, "Ctrl+A"), Entry("Undo", "Undo the last thing", self.undo, "Ctrl+Z"),
                  Entry("Redo", "Redo what was undone", self.redo, "Ctrl+Y")]
        for name, t in themes.THEMES.items():
            extras.append(Entry(f"Theme: {t['label']}", "Change how the program looks", lambda n=name: self.set_theme(n)))
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
        self.canvas.set_document(opened.doc)
        self.panel.set_notes(opened.notices)
        self.panel.refresh(True)
        self._state_changed()
        self.canvas.setFocus()

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
        return True

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
