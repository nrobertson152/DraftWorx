# SPDX-License-Identifier: GPL-3.0-or-later
"""Keeping a copy of unsaved work, so a crash or power cut costs at most a minute.

A recovery file is a normal drawing in the program's own folder, beside a small note of where it came from. It's never written
over the person's file. A clean save or a clean close removes it, so one that's still there on starting means something went wrong.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .. import PROGRAM
from .config import state_dir
from .model import Document
from .native import FormatError, dumps, loads
from .safefile import SaveError, write_verified


def _folder() -> Path:
    return state_dir() / "recovery"


def _key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class Found:
    """Unsaved work left by a run that didn't end cleanly."""
    drawing: Path
    original: str       # where it was saved last, or "" if it never was
    saved_at: float
    note: Path

    @property
    def name(self) -> str:
        return Path(self.original).name if self.original else "An unsaved drawing"


def autosave(doc: Document, session_id: str, original: Path | None) -> bool:
    """Keep a copy now. Returns False, quietly, if it couldn't: this must never get in the way of drawing."""
    folder = _folder()
    key = _key(session_id)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        write_verified(folder / f"{key}.draftworx", dumps(doc, PROGRAM))
        note = {"original": str(original) if original else "", "saved_at": time.time(), "pid": os.getpid()}
        write_verified(folder / f"{key}.json", json.dumps(note).encode("utf-8"))
        return True
    except (OSError, SaveError):
        return False


def forget(session_id: str) -> None:
    """The work is safe, so the copy can go."""
    key = _key(session_id)
    for name in (f"{key}.draftworx", f"{key}.json"):
        try:
            (_folder() / name).unlink()
        except OSError:
            pass


def _is_running(pid: int) -> bool:
    """Is another copy of the program still working on this? It isn't left over if it is."""
    if pid == os.getpid():
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def leftovers() -> list[Found]:
    """Work from runs that ended without saving. Newest first. A damaged note or a running program's copy isn't offered."""
    out: list[Found] = []
    try:
        notes = sorted(_folder().glob("*.json"))
    except OSError:
        return out
    for note in notes:
        try:
            data = json.loads(note.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            pid = data.get("pid", 0)
            if isinstance(pid, int) and pid > 0 and _is_running(pid):
                continue
            drawing = note.with_suffix(".draftworx")
            if not drawing.exists():
                continue
            out.append(Found(drawing, str(data.get("original", "")), float(data.get("saved_at", 0.0)), note))
        except (OSError, ValueError, TypeError):
            continue
    out.sort(key=lambda f: -f.saved_at)
    return out


def read(found: Found) -> Document:
    """Raises FormatError if the copy itself is damaged."""
    try:
        return loads(found.drawing.read_bytes())
    except OSError as exc:
        raise FormatError(f"The recovered copy couldn't be read: {exc}.") from exc


def discard(found: Found) -> None:
    for p in (found.drawing, found.note):
        try:
            p.unlink()
        except OSError:
            pass
