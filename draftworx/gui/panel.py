# SPDX-License-Identifier: GPL-3.0-or-later
"""The panel beside the drawing. It shows what's relevant to what's selected, and otherwise the layers.

Measurements are always here, without a measure command. Most numbers can be changed by typing over them.
"""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QColorDialog, QComboBox, QFormLayout, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ..core import color as colors
from ..core.info import name_of, rows_for, summarise
from ..core.layers import add_layer, rename_layer, set_layer_flag, unique_layer_name
from ..core.model import WEIGHTS, EditError, Style
from ..core.units import TypedError, format_length
from . import icons

BASIC_COLORS = [(1, "Red"), (2, "Yellow"), (3, "Green"), (4, "Cyan"), (5, "Blue"), (6, "Magenta"), (7, "Black or white"), (8, "Dark gray"), (9, "Light gray")]


def _swatch(rgb: int, size: int = 14) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor(f"#{rgb & 0xffffff:06x}"))
    p = QPainter(pm)
    p.setPen(QColor("#808080"))
    p.drawRect(0, 0, size - 1, size - 1)
    p.end()
    return QIcon(pm)


def _weight_label(w: int) -> str:
    if w == -1:
        return "ByLayer"
    if w == -2:
        return "ByBlock"
    if w == -3:
        return "Default"
    return f"{w / 100:.2f} mm"


class _Field(QLineEdit):
    """A number you can type over. Enter applies it, and Esc or leaving puts it back."""
    finished = Signal()

    def __init__(self, text: str, apply):
        super().__init__(text)
        self._original = text
        self._apply = apply
        self.returnPressed.connect(self._commit)

    def _commit(self) -> None:
        if self.text() == self._original:
            self.finished.emit()
            return
        if self._apply(self.text()):
            self._original = self.text()
        else:
            self.setText(self._original)
        self.finished.emit()

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key.Key_Escape:
            self.setText(self._original)
            self.finished.emit()
            return
        super().keyPressEvent(ev)


class ContextPanel(QFrame):
    #: A message for the status line.
    message = Signal(str)
    #: Done typing, so the keyboard should go back to the drawing.
    release_focus = Signal()

    def __init__(self, session, theme, parent=None):
        super().__init__(parent)
        self.setObjectName("panel")
        self.session = session
        self.theme = theme
        self.setMinimumWidth(250)
        self.setMaximumWidth(340)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)
        self.title = QLabel("")
        self.title.setObjectName("panelTitle")
        outer.addWidget(self.title)
        self.sub = QLabel("")
        self.sub.setObjectName("small")
        self.sub.setWordWrap(True)
        outer.addWidget(self.sub)
        self.props = QWidget()
        self.form = QFormLayout(self.props)
        self.form.setContentsMargins(0, 2, 0, 2)
        self.form.setHorizontalSpacing(8)
        self.form.setVerticalSpacing(4)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(self.props)
        self.layers_title = QLabel("LAYERS")
        self.layers_title.setObjectName("sectionTitle")
        head = QHBoxLayout()
        head.addWidget(self.layers_title)
        head.addStretch(1)
        self.add_button = QToolButton()
        self.add_button.setObjectName("tool")
        self.add_button.setToolTip("Add a layer")
        self.add_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.add_button.clicked.connect(self._add_layer)
        head.addWidget(self.add_button)
        outer.addLayout(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, 1)
        self._seen = (-1, -1)
        self.refresh(True)

    def set_theme(self, theme) -> None:
        self.theme = theme
        self.refresh(True)

    def _editing(self) -> bool:
        w = self.focusWidget()
        return isinstance(w, QLineEdit) and self.isAncestorOf(w)

    def refresh(self, force: bool = False) -> None:
        s = self.session
        state = (s.selection_version, s.doc_version)
        if state == self._seen and not force:
            return
        if self._editing() and not force:
            return
        self._seen = state
        self.add_button.setIcon(icons.icon("plus", self.theme.ui["text"], self.theme.ui["accent"], 16))
        self._fill_selection()
        self._fill_layers()

    # ------------------------------------------------------------------------------------------------ the selection

    def _clear_form(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)

    def _fill_selection(self) -> None:
        s = self.session
        doc = s.doc
        self._clear_form()
        ids = sorted(s.selection)
        st = doc.settings
        if not ids:
            box = doc.extents()
            self.title.setText("Drawing")
            n = len(doc.entities)
            size = f"{format_length(box[2] - box[0], st.linear_format, st.linear_decimals, st.units)} x {format_length(box[3] - box[1], st.linear_format, st.linear_decimals, st.units)} {st.units}" if box else "Empty"
            self.sub.setText(f"{n} object{'s' if n != 1 else ''}. {size}.")
            self.props.setVisible(False)
            return
        self.props.setVisible(True)
        info = summarise(doc, ids)
        if len(ids) == 1:
            e = doc.entities[ids[0]]
            self.title.setText(name_of(e.shape))
            self.sub.setText("")
        else:
            self.title.setText(f"{len(ids)} selected")
            self.sub.setText(", ".join(f"{v} {k.lower()}{'s' if v != 1 else ''}" for k, v in sorted(info.kinds.items())))
        self._add_style_rows(ids)
        if len(ids) == 1:
            e = doc.entities[ids[0]]
            for row in rows_for(e.shape, st):
                if row.edit is None:
                    value = QLabel(row.value)
                    value.setObjectName("muted")
                    value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    self.form.addRow(row.label, value)
                else:
                    field = _Field(row.value, lambda text, r=row, eid=e.id: self._apply_edit(eid, r, text))
                    field.finished.connect(self.release_focus.emit)
                    self.form.addRow(row.label, field)
        else:
            if info.length > 0.0:
                self._readout("Total length", format_length(info.length, st.linear_format, st.linear_decimals, st.units))
            if info.area > 0.0:
                self._readout("Total area", format_length(info.area, st.linear_format, st.linear_decimals, st.units))
            if info.box is not None:
                self._readout("Overall size", f"{format_length(info.box[2] - info.box[0], st.linear_format, st.linear_decimals, st.units)} x "
                                              f"{format_length(info.box[3] - info.box[1], st.linear_format, st.linear_decimals, st.units)}")

    def _readout(self, label: str, value: str) -> None:
        v = QLabel(value)
        v.setObjectName("muted")
        v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.form.addRow(label, v)

    def _apply_edit(self, eid: int, row, text: str) -> bool:
        try:
            shape = row.edit(text)
        except (TypedError, ValueError) as exc:
            self.message.emit(str(exc).capitalize() + ".")
            return False
        if shape is None:
            self.message.emit("That doesn't make a valid shape.")
            return False
        try:
            with self.session.doc.transaction(f"Change {row.label.lower()}") as t:
                t.replace(eid, shape)
        except EditError as exc:
            self.message.emit(str(exc))
            return False
        return True

    def _common(self, ids, read):
        """The value they all share, or None if they differ."""
        values = {read(self.session.doc.entities[i].style) for i in ids}
        return values.pop() if len(values) == 1 else None

    def _combo(self, items: list, current, apply, icon_of=None) -> QComboBox:
        box = QComboBox()
        box.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        at = -1
        for i, (label, value) in enumerate(items):
            if icon_of is not None:
                box.addItem(icon_of(value), label, value)
            else:
                box.addItem(label, value)
            if value == current:
                at = i
        if at < 0:
            box.insertItem(0, "(mixed)" if current is None else str(current), current)
            at = 0
        box.setCurrentIndex(at)
        box.activated.connect(lambda i: self._combo_chosen(box, i, apply))
        return box

    def _combo_chosen(self, box: QComboBox, i: int, apply) -> None:
        value = box.itemData(i)
        if value is not None:
            apply(value)
        self.release_focus.emit()

    def _set_style(self, ids, change) -> None:
        doc = self.session.doc
        with doc.transaction("Change style") as t:
            for i in ids:
                e = doc.entities.get(i)
                if e is not None:
                    t.replace(i, style=change(e.style))

    def _add_style_rows(self, ids) -> None:
        doc = self.session.doc
        layer = self._common(ids, lambda st: st.layer)
        self.form.addRow("Layer", self._combo([(v.name, v.name) for v in doc.layers], layer,
                                              lambda v: self._set_style(ids, lambda st: replace_style(st, layer=v))))
        c = self._common(ids, lambda st: st.color)
        items = [("ByLayer", colors.BYLAYER), ("ByBlock", colors.BYBLOCK)] + [(n, i) for i, n in BASIC_COLORS]
        if c is not None and c not in dict((v, 1) for _, v in items):
            items.append((colors.describe(c), c))

        def icon_for(v):
            return _swatch(colors.rgb_of(v, 0xc0c0c0))
        self.form.addRow("Color", self._combo(items, c, lambda v: self._set_style(ids, lambda st: replace_style(st, color=v)), icon_for))
        lt = self._common(ids, lambda st: st.linetype)
        self.form.addRow("Line type", self._combo([(v.name, v.name) for v in doc.linetypes], lt,
                                                  lambda v: self._set_style(ids, lambda st: replace_style(st, linetype=v))))
        w = self._common(ids, lambda st: st.weight)
        weights = [-1, -2, -3] + list(WEIGHTS)
        self.form.addRow("Line weight", self._combo([(_weight_label(v), v) for v in weights], w,
                                                    lambda v: self._set_style(ids, lambda st: replace_style(st, weight=v))))

    # ---------------------------------------------------------------------------------------------------- layers

    def _fill_layers(self) -> None:
        doc = self.session.doc
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        for layer in doc.layers:
            lay.addWidget(self._layer_row(layer))
        lay.addStretch(1)
        self.scroll.setWidget(holder)

    def _small_button(self, icon_name: str, tip: str, action) -> QToolButton:
        u = self.theme.ui
        b = QToolButton()
        b.setObjectName("tool")
        b.setIcon(icons.icon(icon_name, u["muted"], u["accent"], 15))
        b.setIconSize(QSize(15, 15))
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        b.clicked.connect(action)
        return b

    def _layer_row(self, layer) -> QWidget:
        doc = self.session.doc
        u = self.theme.ui
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(2)
        h.addWidget(self._small_button("eye" if layer.on else "eye_off", "Hide this layer" if layer.on else "Show this layer",
                                       lambda: self._flag(layer.name, on=not layer.on)))
        h.addWidget(self._small_button("lock" if layer.locked else "unlock", "Unlock this layer" if layer.locked else "Lock this layer",
                                       lambda: self._flag(layer.name, locked=not layer.locked)))
        swatch = QToolButton()
        swatch.setObjectName("tool")
        swatch.setIcon(_swatch(colors.rgb_of(layer.color, 0)))
        swatch.setToolTip(f"Color: {colors.describe(layer.color)}")
        swatch.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        swatch.clicked.connect(lambda: self._choose_color(layer.name, swatch))
        h.addWidget(swatch)
        current = layer.name.casefold() == doc.current.layer.casefold()
        name = QPushButton(layer.name)
        name.setFlat(True)
        name.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        name.setStyleSheet(f"QPushButton {{ border: none; background: transparent; text-align: left; padding: 3px 4px; font-weight: {'700' if current else '400'}; color: {u['text'] if layer.on else u['faint']}; }}"
                           f"QPushButton:hover {{ background: {u['hover']}; }}")
        name.setToolTip("Current layer: new things go here" if current else "Click to draw on this layer")
        name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        name.clicked.connect(lambda: self._make_current(layer.name))
        name.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        name.customContextMenuRequested.connect(lambda pos: self._layer_menu(layer.name, name.mapToGlobal(pos)))
        h.addWidget(name, 1)
        if current:
            mark = QLabel()
            mark.setPixmap(icons.icon("check", u["text"], u["accent"], 14).pixmap(14, 14))
            mark.setToolTip("Current layer")
            h.addWidget(mark)
        return row

    def _flag(self, name: str, **flags) -> None:
        set_layer_flag(self.session.doc, name, **flags)

    def _make_current(self, name: str) -> None:
        self.session.doc.current.layer = name
        self.refresh(True)
        self.release_focus.emit()

    def _choose_color(self, name: str, anchor) -> None:
        menu = QMenu(self)
        for index, label in BASIC_COLORS:
            a = menu.addAction(_swatch(colors.rgb_of(index, 0)), label)
            a.triggered.connect(lambda _=False, v=index: self._flag(name, color=v))
        menu.addSeparator()
        custom = menu.addAction("Other…")
        custom.triggered.connect(lambda: self._custom_color(name))
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _custom_color(self, name: str) -> None:
        c = QColorDialog.getColor(parent=self)
        if c.isValid():
            self._flag(name, color=colors.true_color(c.red(), c.green(), c.blue()))

    def _layer_menu(self, name: str, at) -> None:
        menu = QMenu(self)
        menu.addAction("Rename…").triggered.connect(lambda: self._rename_layer(name))
        menu.addAction("Delete").triggered.connect(lambda: self._delete_layer(name))
        menu.exec(at)

    def _rename_layer(self, name: str) -> None:
        new, ok = QInputDialog.getText(self, "Rename layer", "New name:", text=name)
        if ok:
            try:
                rename_layer(self.session.doc, name, new)
            except EditError as exc:
                self.message.emit(str(exc))

    def _delete_layer(self, name: str) -> None:
        try:
            with self.session.doc.transaction("Delete layer") as t:
                t.remove_layer(name)
        except EditError as exc:
            self.message.emit(str(exc))

    def _add_layer(self) -> None:
        doc = self.session.doc
        new, ok = QInputDialog.getText(self, "New layer", "Name:", text=unique_layer_name(doc))
        if ok and new.strip():
            try:
                doc.current.layer = add_layer(doc, new)
                self.refresh(True)
            except EditError as exc:
                self.message.emit(str(exc))


def replace_style(st: Style, **values) -> Style:
    return replace(st, **values)
