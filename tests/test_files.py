# SPDX-License-Identifier: GPL-3.0-or-later
import json
import os

import pytest

from draftworx.core import recovery
from draftworx.core.config import Config, load_config, save_config, state_dir
from draftworx.core.files import Opened, open_drawing, save_drawing
from draftworx.core.model import Document, Layer
from draftworx.core.native import FormatError
from draftworx.core.safefile import ChangedOnDisk, SaveError
from driver import line


def drawing():
    d = Document()
    with d.transaction("x") as t:
        t.add(line(0, 0, 10, 0))
        t.put_layer(Layer("A"))
    return d


class TestSaveAndOpen:
    def test_round_trip(self, tmp_path):
        p = tmp_path / "a.draftworx"
        doc = drawing()
        o = Opened(doc)
        save_drawing(o, p)
        assert o.path == p and not doc.dirty
        back = open_drawing(p)
        assert back.doc.fingerprint() == doc.fingerprint()
        assert not back.doc.dirty and not back.doc.can_undo

    def test_no_name_says_so(self):
        with pytest.raises(SaveError, match="name"):
            save_drawing(Opened(drawing()))

    def test_a_failed_save_leaves_it_unsaved(self, tmp_path):
        doc = drawing()
        o = Opened(doc)
        with pytest.raises(SaveError):
            save_drawing(o, tmp_path / "missing" / "a.draftworx")
        assert doc.dirty and o.path is None

    def test_keeps_the_previous_version(self, tmp_path):
        p = tmp_path / "a.draftworx"
        doc = drawing()
        o = Opened(doc)
        save_drawing(o, p)
        first = p.read_bytes()
        with doc.transaction("y") as t:
            t.add(line(1, 1, 2, 2))
        save_drawing(o, p)
        assert (tmp_path / "a.draftworx.bak").read_bytes() == first

    def test_someone_elses_change_is_not_overwritten(self, tmp_path):
        p = tmp_path / "a.draftworx"
        o = Opened(drawing())
        save_drawing(o, p)
        theirs = p.read_bytes().replace(b'"next_id"', b' "next_id"')
        p.write_bytes(theirs)
        with o.doc.transaction("mine") as t:
            t.add(line(5, 5, 6, 6))
        with pytest.raises(ChangedOnDisk):
            save_drawing(o, p)
        assert p.read_bytes() == theirs
        assert o.doc.dirty
        save_drawing(o, p, force=True)
        assert p.read_bytes() != theirs

    def test_a_deleted_file_counts_as_changed(self, tmp_path):
        p = tmp_path / "a.draftworx"
        o = Opened(drawing())
        save_drawing(o, p)
        p.unlink()
        with pytest.raises(ChangedOnDisk):
            save_drawing(o, p)

    def test_save_as_elsewhere_does_not_care_about_changes_there(self, tmp_path):
        p, q = tmp_path / "a.draftworx", tmp_path / "b.draftworx"
        o = Opened(drawing())
        save_drawing(o, p)
        q.write_bytes(b"something else")
        save_drawing(o, q)
        assert o.path == q

    def test_undo_back_to_the_save_is_clean_after_saving(self, tmp_path):
        p = tmp_path / "a.draftworx"
        doc = drawing()
        o = Opened(doc)
        save_drawing(o, p)
        with doc.transaction("y") as t:
            t.add(line(1, 1, 2, 2))
        assert doc.dirty
        doc.undo()
        assert not doc.dirty

    def test_what_is_written_is_checked_by_reading_it_back(self, tmp_path, monkeypatch):
        from draftworx.core import files
        p = tmp_path / "a.draftworx"
        o = Opened(drawing())
        save_drawing(o, p)
        before = p.read_bytes()
        with o.doc.transaction("y") as t:
            t.add(line(1, 1, 2, 2))
        # the writer goes wrong: it writes a file that doesn't hold what's in memory
        monkeypatch.setattr(files, "dumps", lambda d, prog: before)
        with pytest.raises(SaveError, match="check"):
            save_drawing(o, p)
        assert p.read_bytes() == before
        assert o.doc.dirty


class TestOpening:
    def test_missing(self, tmp_path):
        with pytest.raises(FormatError, match="isn't there"):
            open_drawing(tmp_path / "nope.draftworx")

    def test_a_folder(self, tmp_path):
        with pytest.raises(FormatError):
            open_drawing(tmp_path)

    def test_garbage(self, tmp_path):
        p = tmp_path / "a.draftworx"
        p.write_bytes(b"\x00\x01garbage")
        with pytest.raises(FormatError):
            open_drawing(p)

    def test_empty(self, tmp_path):
        p = tmp_path / "a.draftworx"
        p.write_bytes(b"")
        with pytest.raises(FormatError):
            open_drawing(p)


class TestRecovery:
    def test_a_copy_is_offered_after_a_crash(self):
        doc = drawing()
        assert recovery.autosave(doc, "run-1", None)
        # this process wrote it and is still running, but a run never recovers from itself on its next start
        found = recovery.leftovers()
        assert len(found) == 1 and found[0].name == "An unsaved drawing"
        assert recovery.read(found[0]).fingerprint() == doc.fingerprint()

    def test_it_remembers_where_it_came_from(self, tmp_path):
        recovery.autosave(drawing(), "run-1", tmp_path / "bracket.draftworx")
        assert recovery.leftovers()[0].name == "bracket.draftworx"

    def test_a_clean_save_removes_it(self):
        recovery.autosave(drawing(), "run-1", None)
        recovery.forget("run-1")
        assert recovery.leftovers() == []

    def test_discard(self):
        recovery.autosave(drawing(), "run-1", None)
        recovery.discard(recovery.leftovers()[0])
        assert recovery.leftovers() == []
        assert not list((state_dir() / "recovery").iterdir())

    def test_another_running_copys_work_is_left_alone(self, monkeypatch):
        recovery.autosave(drawing(), "run-1", None)
        monkeypatch.setattr(recovery, "_is_running", lambda pid: True)
        assert recovery.leftovers() == []

    def test_a_damaged_note_is_not_offered(self):
        recovery.autosave(drawing(), "run-1", None)
        note = next((state_dir() / "recovery").glob("*.json"))
        note.write_text("{not json")
        assert recovery.leftovers() == []

    def test_a_note_without_its_drawing_is_not_offered(self):
        recovery.autosave(drawing(), "run-1", None)
        next((state_dir() / "recovery").glob("*.draftworx")).unlink()
        assert recovery.leftovers() == []

    def test_a_damaged_copy_says_so(self):
        recovery.autosave(drawing(), "run-1", None)
        next((state_dir() / "recovery").glob("*.draftworx")).write_bytes(b"junk")
        with pytest.raises(FormatError):
            recovery.read(recovery.leftovers()[0])

    def test_newest_first(self):
        recovery.autosave(drawing(), "run-1", None)
        n1 = next((state_dir() / "recovery").glob("*.json"))
        data = json.loads(n1.read_text())
        data["saved_at"] = 1.0
        n1.write_text(json.dumps(data))
        recovery.autosave(drawing(), "run-2", None)
        assert recovery.leftovers()[0].saved_at > 1.0

    def test_failure_to_keep_a_copy_is_quiet(self, monkeypatch, tmp_path):
        blocker = tmp_path / "a-file"
        blocker.write_text("x")
        monkeypatch.setenv("DRAFTWORX_STATE_DIR", str(blocker))
        assert recovery.autosave(drawing(), "run-1", None) is False
        assert recovery.leftovers() == []

    def test_a_dead_process_is_not_running(self):
        assert not recovery._is_running(2 ** 22 + 12345)

    def test_this_process_does_not_count(self):
        assert not recovery._is_running(os.getpid())


class TestConfig:
    def test_defaults_without_a_file(self):
        c = load_config()
        assert c.theme == "modern_light" and c.recent == []

    def test_round_trip(self):
        c = Config(theme="blueprint", show_grid=False, polar_degrees=15.0)
        c.remember("a")
        c.remember("b")
        c.remember("a")
        assert c.recent == ["a", "b"]
        save_config(c)
        assert load_config() == c

    @pytest.mark.parametrize("data", [b"", b"not json", b"[]", b'{"theme": 5, "recent": "x", "show_grid": 1, "polar_degrees": "a"}',
                                      b'{"polar_degrees": -1}', b'{"polar_degrees": true}'])
    def test_a_damaged_file_is_ignored(self, data):
        state_dir().mkdir(parents=True, exist_ok=True)
        (state_dir() / "settings.json").write_bytes(data)
        assert load_config() == Config()

    def test_recent_is_bounded(self):
        c = Config()
        for i in range(30):
            c.remember(str(i))
        assert len(c.recent) == 12

    def test_an_unwritable_folder_is_quiet(self, tmp_path, monkeypatch):
        blocker = tmp_path / "a-file"
        blocker.write_text("x")
        monkeypatch.setenv("DRAFTWORX_STATE_DIR", str(blocker))
        save_config(Config())

    def test_where_it_lives(self, monkeypatch):
        monkeypatch.delenv("DRAFTWORX_STATE_DIR")
        assert "draftworx" in str(state_dir()).lower()
