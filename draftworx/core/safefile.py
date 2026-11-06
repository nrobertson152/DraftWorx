# SPDX-License-Identifier: GPL-3.0-or-later
"""Saving without risking what's already there.

The new content goes to a temporary file beside the target, is forced to disk, read back and checked, and only then
takes the target's name in one step. Until that step the old file is untouched, and after it the new file is complete,
so a crash or a full disk at any moment leaves one or the other, never a mix.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_RETRIES = 12
_PAUSE = 0.05


class SaveError(Exception):
    """The file couldn't be saved. The message says why. Whatever was there before is still there."""


class ChangedOnDisk(SaveError):
    """The file changed since it was opened: saving would overwrite someone's work."""


@dataclass(frozen=True, slots=True)
class FileStamp:
    """What a file was when we read or wrote it."""
    size: int
    mtime_ns: int
    sha256: str

    @staticmethod
    def of_bytes(path: Path, data: bytes) -> "FileStamp":
        st = os.stat(path)
        return FileStamp(st.st_size, st.st_mtime_ns, hashlib.sha256(data).hexdigest())


def stamp_of(path: Path) -> FileStamp | None:
    """Its stamp now, or None if there's no such file."""
    try:
        data = _read_all(path)
    except FileNotFoundError:
        return None
    return FileStamp.of_bytes(path, data)


def changed_since(path: Path, known: FileStamp | None) -> bool:
    """Has this file changed since we knew it? A touched file with the same bytes hasn't really."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return known is not None
    if known is None:
        return True
    if st.st_size == known.size and st.st_mtime_ns == known.mtime_ns:
        return False
    return hashlib.sha256(_read_all(path)).hexdigest() != known.sha256


def _read_all(path: Path) -> bytes:
    def read():
        with open(path, "rb") as fh:
            return fh.read()
    return _patiently(read)


def _patiently(action: Callable):
    """Windows briefly refuses files that a virus scanner or indexer has just opened. Give it a moment."""
    for i in range(_RETRIES):
        try:
            return action()
        except PermissionError:
            if os.name != "nt" or i == _RETRIES - 1:
                raise
            time.sleep(_PAUSE)
    raise AssertionError("unreachable")


def _sync_folder(folder: Path) -> None:
    if os.name == "nt":
        return      # Windows can't open a folder this way, and NTFS journals the rename
    try:
        fd = os.open(folder, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _say(exc: OSError, path: Path) -> str:
    if isinstance(exc, PermissionError):
        return f"You don't have permission to save {path.name} there, or another program has it open."
    if exc.errno == 28:
        return f"There isn't enough disk space to save {path.name}."
    return f"{path.name} couldn't be saved: {exc.strerror or exc}."


def write_verified(path: Path | str, data: bytes, verify: Callable[[bytes], None] | None = None, backup: bool = False) -> FileStamp:
    """Replace `path` with `data`. `verify` is handed what actually reached the disk, and raises if it's wrong.

    `backup` keeps the previous version beside it as `<name>.bak`. Raises SaveError with the original intact on any failure.
    """
    target = Path(os.path.realpath(path))
    folder = target.parent
    mode = None
    try:
        st = os.stat(target)
    except FileNotFoundError:
        st = None
    except OSError as exc:
        raise SaveError(_say(exc, target)) from exc
    if st is not None:
        if stat.S_ISDIR(st.st_mode):
            raise SaveError(f"{target.name} is a folder.")
        if not (st.st_mode & stat.S_IWUSR):
            raise SaveError(f"{target.name} is read-only, so it hasn't been changed. Save a copy under another name.")
        mode = stat.S_IMODE(st.st_mode)
    tmp = folder / f".{target.name}.{secrets.token_hex(6)}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o666 if mode is None else mode)
    except OSError as exc:
        raise SaveError(_say(exc, target)) from exc
    done = False
    try:
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            if mode is not None and os.name != "nt":
                os.chmod(tmp, mode)
            back = _read_all(tmp)
        except OSError as exc:
            raise SaveError(_say(exc, target)) from exc
        if back != data:
            raise SaveError(f"{target.name} wasn't saved: what reached the disk differs from what was written. "
                            "The previous file is unchanged. Check the disk or the network connection.")
        if verify is not None:
            try:
                verify(back)
            except Exception as exc:
                raise SaveError(f"{target.name} wasn't saved, because the check of what was written failed ({exc}). "
                                "The previous file is unchanged. This is a bug in DraftWorx: please report it.") from exc
        try:
            if backup and st is not None:
                _keep_backup(target, folder, mode)
            _patiently(lambda: os.replace(tmp, target))
            done = True
            _sync_folder(folder)
            return FileStamp.of_bytes(target, data)
        except OSError as exc:
            raise SaveError(_say(exc, target)) from exc
    finally:
        if not done:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _keep_backup(target: Path, folder: Path, mode: int | None) -> None:
    """A copy, not a rename: the target must keep existing right up to the replace."""
    old = _read_all(target)
    bak = folder / f"{target.name}.bak"
    tmp = folder / f".{target.name}.{secrets.token_hex(6)}.baktmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o666 if mode is None else mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(old)
            fh.flush()
            os.fsync(fh.fileno())
        _patiently(lambda: os.replace(tmp, bak))
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
