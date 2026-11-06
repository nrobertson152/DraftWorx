# SPDX-License-Identifier: GPL-3.0-or-later
"""Opening and saving a drawing: which format, and the check that saving won't overwrite someone else's work."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import PROGRAM
from .model import Document
from .native import FormatError, dumps, loads
from .safefile import ChangedOnDisk, FileStamp, SaveError, changed_since, write_verified

MOST_BYTES = 2 * 1024 ** 3      # a limit on what's read into memory, so a wrong file gets a message and not a frozen computer


@dataclass
class Opened:
    """A drawing and where it came from."""
    doc: Document
    path: Path | None = None
    stamp: FileStamp | None = None
    #: Things worth telling the person about what was read, or about the last save.
    notices: list[str] = field(default_factory=list)


def _read(path: Path) -> bytes:
    try:
        size = path.stat().st_size
        if size > MOST_BYTES:
            raise FormatError(f"{path.name} is {size / 1024 ** 3:.1f} GB, too large to open.")
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise FormatError(f"{path.name} isn't there any more.") from exc
    except PermissionError as exc:
        raise FormatError(f"You don't have permission to read {path.name}, or another program has it locked.") from exc
    except OSError as exc:
        raise FormatError(f"{path.name} couldn't be read: {exc.strerror or exc}.") from exc


def open_drawing(path: Path | str) -> Opened:
    """Read a drawing. Raises FormatError with a message for the person opening it."""
    p = Path(path)
    data = _read(p)
    if p.suffix.casefold() == ".dxf":
        from .dxf import read_dxf
        doc, notices = read_dxf(data)
    else:
        doc, notices = loads(data), []
    doc.mark_saved()
    return Opened(doc, p, FileStamp.of_bytes(p, data), notices)


def encode_for(doc: Document, path: Path, version: str | None = None) -> tuple[bytes, object, list[str]]:
    """The bytes for this file name, the check that they read back as the same drawing, and what the person should know about them."""
    if path.suffix.casefold() == ".dxf":
        from .dxf import check_written, encode_dxf
        data, report = encode_dxf(doc, version)
        return data, lambda back: check_written(back, doc, report), report.sentences()
    data = dumps(doc, PROGRAM)
    want = doc.fingerprint()

    def verify(back: bytes) -> None:
        again = loads(back)
        if again.fingerprint() != want or again.extras != doc.extras:
            raise ValueError("the file reads back as a different drawing")
    return data, verify, []


def save_drawing(opened: Opened, path: Path | str | None = None, force: bool = False, backup: bool = True) -> FileStamp:
    """Save it. Raises ChangedOnDisk if the file changed since it was read, unless `force`. Raises SaveError otherwise.
    On any failure, what was there before is still there, and the drawing still counts as unsaved."""
    target = Path(path) if path is not None else opened.path
    if target is None:
        raise SaveError("Choose a name for this drawing first.")
    same_file = opened.path is not None and target == opened.path
    if same_file and not force and changed_since(target, opened.stamp):
        raise ChangedOnDisk(f"{target.name} has changed since you opened it, perhaps on another computer. Saving would overwrite that.")
    data, verify, notes = encode_for(opened.doc, target)
    stamp = write_verified(target, data, verify, backup=backup)
    opened.path = target
    opened.stamp = stamp
    opened.notices = notes
    opened.doc.mark_saved()
    return stamp


def export_dxf(doc: Document, path: Path | str, version: str) -> list[str]:
    """Write a copy in a particular version of DXF, for something that needs that one. The drawing carries on being what it was, and it isn't marked as saved.
    Returns what the person should know about what had to change. Raises SaveError."""
    data, verify, notes = encode_for(doc, Path(path), version)
    write_verified(Path(path), data, verify)
    return notes
