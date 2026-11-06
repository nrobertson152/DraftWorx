# SPDX-License-Identifier: GPL-3.0-or-later
"""RevisionWorx in the window: what it says about the drawing in one sentence, the one next step (Check out or Check in), and the dialogs
for checking in and for putting a drawing under revision control. Everything it does goes through `integrations.revisionworx`."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from ..integrations.revisionworx import RevisionWorx, RwxError, Status


class Asker(QObject):
    """Asks RevisionWorx about a file away from the window's thread, so a slow shared folder never holds up drawing. The answer arrives
    on the window's thread as `answered(question, path, status)`, the status being None when RevisionWorx isn't there or couldn't say.
    Questions are numbered, so an answer that comes back after a newer one was asked for is known to be out of date."""

    answered = Signal(int, str, object)     # which question it answers, the file, and the status

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._rwx: RevisionWorx | None = None
        self._checked = False

    def rwx(self) -> RevisionWorx | None:
        """RevisionWorx, if it's installed and speaks the interface DraftWorx knows. Found once, the first time it's needed."""
        with self._lock:
            if not self._checked:
                self._rwx = RevisionWorx()
                self._checked = True
                if not self._rwx.available():
                    self._rwx = None
            return self._rwx

    @property
    def available(self) -> bool:
        """Known to be there. False until it's been looked for."""
        return self._checked and self._rwx is not None

    def now(self, path: str) -> Status | None:
        """What RevisionWorx says about this file, or None. A drawing that's never been saved isn't asked about."""
        if not path:
            return None
        rwx = self.rwx()
        if rwx is None:
            return None
        try:
            return rwx.status(path)
        except RwxError:
            return None

    def ask(self, question: int, path: str) -> None:
        if not path:
            self.answered.emit(question, path, None)
            return

        def run() -> None:
            status = self.now(path)
            try:
                self.answered.emit(question, path, status)
            except RuntimeError:
                pass            # the window closed while RevisionWorx was being asked
        threading.Thread(target=run, daemon=True).start()


class RevisionChip(QWidget):
    """RevisionWorx's sentence about the drawing, and its one next step as a button. Hidden for a drawing it doesn't control."""

    act = Signal(str)       # "check-in" or "check-out"

    def __init__(self, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.label = QLabel("")
        self.label.setObjectName("small")
        h.addWidget(self.label)
        self.button = QPushButton("")
        self.button.setObjectName("chip")
        self.button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.button.clicked.connect(lambda: self.act.emit(self.action))
        h.addWidget(self.button)
        self.action = ""
        self.hide()

    def show_status(self, s: Status | None, unsaved: bool) -> None:
        if s is None or not s.controlled:
            self.action = ""
            self.hide()
            return
        title, text, tone = s.headline()
        self.label.setText(s.sentence())
        self.label.setToolTip(f"{title}. {text} (RevisionWorx)")
        name = "message" if tone in ("warn", "danger") else "small"
        if self.label.objectName() != name:
            self.label.setObjectName(name)
            self.label.style().unpolish(self.label)
            self.label.style().polish(self.label)
        self.action = s.primary(unsaved)
        self.button.setText({"check-in": "Check in…", "check-out": "Check out"}.get(self.action, ""))
        self.button.setToolTip({"check-in": "Keep your changes in RevisionWorx as a new revision",
                                "check-out": "Check it out in RevisionWorx, so it's yours to change"}.get(self.action, ""))
        self.button.setVisible(bool(self.action))
        self.show()


def _buttons(dialog: QDialog, ok: str) -> tuple[QHBoxLayout, QPushButton]:
    row = QHBoxLayout()
    row.addStretch(1)
    cancel = QPushButton("Cancel")
    cancel.clicked.connect(dialog.reject)
    go = QPushButton(ok)
    go.setObjectName("primary")
    go.setDefault(True)
    go.clicked.connect(dialog.accept)
    row.addWidget(cancel)
    row.addWidget(go)
    return row, go


class CheckInDialog(QDialog):
    """What changed, which RevisionWorx keeps with the revision, and whether the parts list goes too."""

    def __init__(self, parent, name: str, parts: int):
        super().__init__(parent)
        self.setWindowTitle(f"Check in {name}")
        v = QVBoxLayout(self)
        v.addWidget(QLabel("What changed? RevisionWorx keeps this with the revision."))
        self.message = QLineEdit()
        self.message.setPlaceholderText("Moved the pump 200 mm to the left")
        v.addWidget(self.message)
        self.parts = QCheckBox(f"Its parts list too ({parts} part{'s' if parts != 1 else ''}), as a BOM next to it")
        self.parts.setChecked(parts > 0)
        self.parts.setVisible(parts > 0)
        v.addWidget(self.parts)
        row, self.go = _buttons(self, "Check in")
        v.addLayout(row)
        self.message.textChanged.connect(lambda t: self.go.setEnabled(bool(t.strip())))
        self.go.setEnabled(False)

    @property
    def text(self) -> str:
        return self.message.text().strip()


class AddDialog(QDialog):
    """Putting a drawing under revision control: which project, and its number and title."""

    def __init__(self, parent, projects: list[dict], chosen: dict | None, number: str, title: str):
        super().__init__(parent)
        self.setWindowTitle("Add to RevisionWorx")
        v = QVBoxLayout(self)
        form = QFormLayout()
        self.project = QComboBox()
        for p in projects:
            words = " ".join(x for x in (p.get("number", ""), p.get("name", "")) if x)
            self.project.addItem(f"{p['workspace_name']} › {words}" if p.get("workspace_name") else words, p)
        if chosen is not None:
            at = next((i for i, p in enumerate(projects) if p is chosen), -1)
            if at >= 0:
                self.project.setCurrentIndex(at)
        form.addRow("Project", self.project)
        self.number = QLineEdit(number)
        form.addRow("Number", self.number)
        self.title = QLineEdit(title)
        form.addRow("Title", self.title)
        self.message = QLineEdit("First issue")
        form.addRow("Description", self.message)
        v.addLayout(form)
        note = QLabel("A drawing that isn't in the project's folder is copied into it, and DraftWorx goes on with the copy.")
        note.setObjectName("small")
        note.setWordWrap(True)
        v.addWidget(note)
        row, self.go = _buttons(self, "Add")
        v.addLayout(row)

    @property
    def chosen(self) -> dict:
        return self.project.currentData()


__all__ = ["AddDialog", "Asker", "CheckInDialog", "RevisionChip"]
