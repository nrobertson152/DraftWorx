# SPDX-License-Identifier: GPL-3.0-or-later
"""Ctrl+K: everything the program can do, by name. Nothing needs to be remembered, and no menu needs to be hunted through."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from ..core.commands import COMMANDS, search


@dataclass(frozen=True, slots=True)
class Entry:
    name: str
    detail: str
    run: Callable[[], None]
    keys: str = ""


class Palette(QDialog):
    """A search box and a list. Enter runs the first match, or the one chosen with the arrow keys."""

    def __init__(self, parent, session, extras: list[Entry]):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("palette")
        self.session = session
        self.extras = extras
        self.chosen: Entry | None = None
        self.setMinimumSize(520, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(6)
        self.box = QLineEdit(self)
        self.box.setPlaceholderText("What do you want to do?")
        self.box.textChanged.connect(self._fill)
        self.box.returnPressed.connect(self._accept_current)
        self.box.installEventFilter(self)
        self.list = QListWidget(self)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.itemClicked.connect(lambda _: self._accept_current())
        lay.addWidget(self.box)
        lay.addWidget(self.list, 1)
        self._fill("")

    def _matches(self, text: str) -> list[Entry]:
        q = text.strip().casefold()
        out: list[Entry] = []
        session = self.session
        if q:
            commands = search(q, 40)
        else:
            commands = sorted(COMMANDS.values(), key=lambda i: (i.group, i.name))
        for info in commands:
            aliases = " ".join(info.aliases)
            out.append(Entry(info.name.capitalize(), info.help, lambda n=info.name: session.run(n), aliases))
        extras = [e for e in self.extras if not q or q in e.name.casefold() or q in e.detail.casefold()]
        return (out + extras) if q else (extras + out)

    def _fill(self, text: str) -> None:
        self.list.clear()
        self._entries = self._matches(text)
        for e in self._entries:
            label = f"{e.name}  —  {e.detail}" if e.detail else e.name
            if e.keys:
                label += f"   ({e.keys})"
            QListWidgetItem(label, self.list)
        if self._entries:
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.box and ev.type() == ev.Type.KeyPress:
            if ev.key() == Qt.Key.Key_Down:
                self.list.setCurrentRow(min(self.list.currentRow() + 1, self.list.count() - 1))
                return True
            if ev.key() == Qt.Key.Key_Up:
                self.list.setCurrentRow(max(self.list.currentRow() - 1, 0))
                return True
            if ev.key() == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, ev)

    def _accept_current(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self._entries):
            self.chosen = self._entries[row]
            self.accept()
