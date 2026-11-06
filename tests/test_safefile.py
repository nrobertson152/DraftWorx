# SPDX-License-Identifier: GPL-3.0-or-later
import os
import stat
import sys
from pathlib import Path

import pytest

from draftworx.core import safefile
from draftworx.core.safefile import SaveError, changed_since, stamp_of, write_verified


def only_files(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


class TestWrite:
    def test_new_file(self, tmp_path):
        p = tmp_path / "a.txt"
        stamp = write_verified(p, b"hello")
        assert p.read_bytes() == b"hello"
        assert stamp.size == 5
        assert only_files(tmp_path) == ["a.txt"]

    def test_replaces_atomically_and_leaves_no_temporaries(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_bytes(b"old")
        write_verified(p, b"new")
        assert p.read_bytes() == b"new"
        assert only_files(tmp_path) == ["a.txt"]

    def test_backup_keeps_the_previous_version(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_bytes(b"old")
        write_verified(p, b"new", backup=True)
        assert p.read_bytes() == b"new"
        assert (tmp_path / "a.txt.bak").read_bytes() == b"old"
        assert only_files(tmp_path) == ["a.txt", "a.txt.bak"]

    def test_backup_of_nothing_is_nothing(self, tmp_path):
        write_verified(tmp_path / "a.txt", b"new", backup=True)
        assert only_files(tmp_path) == ["a.txt"]

    def test_a_failed_check_keeps_the_old_file(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_bytes(b"old")

        def verify(data):
            assert data == b"new"
            raise ValueError("no good")
        with pytest.raises(SaveError, match="no good"):
            write_verified(p, b"new", verify, backup=True)
        assert p.read_bytes() == b"old"
        assert only_files(tmp_path) == ["a.txt"]

    def test_verify_sees_what_is_on_disk_not_what_was_handed_in(self, tmp_path, monkeypatch):
        p = tmp_path / "a.txt"
        p.write_bytes(b"old")
        real = safefile._read_all
        monkeypatch.setattr(safefile, "_read_all", lambda path: b"corrupted" if ".tmp" in str(path) else real(path))
        with pytest.raises(SaveError, match="differs"):
            write_verified(p, b"new")
        assert p.read_bytes() == b"old"
        assert only_files(tmp_path) == ["a.txt"]

    def test_a_failed_rename_keeps_the_old_file(self, tmp_path, monkeypatch):
        p = tmp_path / "a.txt"
        p.write_bytes(b"old")

        def refuse(a, b):
            raise OSError(28, "No space left")
        monkeypatch.setattr(os, "replace", refuse)
        with pytest.raises(SaveError, match="disk space"):
            write_verified(p, b"new")
        monkeypatch.undo()
        assert p.read_bytes() == b"old"
        assert only_files(tmp_path) == ["a.txt"]

    def test_a_full_disk_while_writing_keeps_the_old_file(self, tmp_path, monkeypatch):
        p = tmp_path / "a.txt"
        p.write_bytes(b"old")

        def refuse(fd):
            raise OSError(28, "No space left")
        monkeypatch.setattr(os, "fsync", refuse)
        with pytest.raises(SaveError, match="disk space"):
            write_verified(p, b"new")
        monkeypatch.undo()
        assert p.read_bytes() == b"old"
        assert only_files(tmp_path) == ["a.txt"]

    def test_read_only_is_respected(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_bytes(b"old")
        os.chmod(p, stat.S_IREAD)
        try:
            with pytest.raises(SaveError, match="read-only"):
                write_verified(p, b"new")
            assert p.read_bytes() == b"old"
        finally:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)

    def test_a_folder_is_refused(self, tmp_path):
        with pytest.raises(SaveError, match="folder"):
            write_verified(tmp_path, b"x")

    def test_a_missing_folder_says_so(self, tmp_path):
        with pytest.raises(SaveError):
            write_verified(tmp_path / "nope" / "a.txt", b"x")

    @pytest.mark.skipif(sys.platform == "win32", reason="file modes are a POSIX idea")
    def test_keeps_the_file_mode(self, tmp_path):
        p = tmp_path / "a.txt"
        p.write_bytes(b"old")
        os.chmod(p, 0o640)
        write_verified(p, b"new")
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o640

    @pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need privileges on Windows")
    def test_writes_through_a_link(self, tmp_path):
        real = tmp_path / "real.txt"
        real.write_bytes(b"old")
        link = tmp_path / "link.txt"
        link.symlink_to(real)
        write_verified(link, b"new")
        assert link.is_symlink() and real.read_bytes() == b"new"

    def test_a_brief_windows_refusal_is_waited_out(self, tmp_path, monkeypatch):
        p = tmp_path / "a.txt"
        p.write_bytes(b"old")
        real = os.replace
        tries = []

        def busy(a, b):
            tries.append(1)
            if len(tries) < 3:
                raise PermissionError(13, "in use")
            real(a, b)
        monkeypatch.setattr(os, "replace", busy)
        monkeypatch.setattr(safefile.os, "name", "nt")
        monkeypatch.setattr(safefile, "_PAUSE", 0.0)
        # os.name is faked only for the retry decision: nothing else below runs differently that matters here
        monkeypatch.setattr(safefile, "_sync_folder", lambda f: None)
        fake_open = os.open
        monkeypatch.setattr(os, "open", fake_open)
        safefile._patiently(lambda: os.replace(p, tmp_path / "b.txt"))
        assert len(tries) == 3


class TestStamp:
    def test_changed_when_the_bytes_change(self, tmp_path):
        p = tmp_path / "a.txt"
        s = write_verified(p, b"one")
        assert not changed_since(p, s)
        p.write_bytes(b"two")
        assert changed_since(p, s)

    def test_a_touch_with_the_same_bytes_is_not_a_change(self, tmp_path):
        p = tmp_path / "a.txt"
        s = write_verified(p, b"one")
        os.utime(p, (1, 1))
        assert not changed_since(p, s)

    def test_a_deleted_file_counts_as_changed(self, tmp_path):
        p = tmp_path / "a.txt"
        s = write_verified(p, b"one")
        p.unlink()
        assert changed_since(p, s)
        assert stamp_of(p) is None

    def test_a_file_that_appeared_counts_as_changed(self, tmp_path):
        p = tmp_path / "a.txt"
        assert not changed_since(p, None)
        p.write_bytes(b"x")
        assert changed_since(p, None)
