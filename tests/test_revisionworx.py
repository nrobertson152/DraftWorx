# SPDX-License-Identifier: GPL-3.0-or-later
"""RevisionWorx, through its real command line: whether a drawing is controlled, checking it out and in, someone else's check-out, putting
a drawing under revision control, and the parts list reaching OpenMeridian as a released BOM. These run the real programs when they can
be found, and skip otherwise; CI installs them."""
from pathlib import Path

import pytest

from draftworx.core.native import dumps
from draftworx.core.shapes import Attrib, Insert, Text
from draftworx.core.vec import Vec
from draftworx.integrations.revisionworx import RwxError, Status, find_command, project_for, publish_parts, workspaces_for
from driver import line
from family import controlled, drawing, elsewhere, run, valves, workspace

pytestmark = pytest.mark.skipif(find_command() is None, reason="RevisionWorx isn't installed")


@pytest.fixture
def ws(tmp_path):
    return workspace(tmp_path)


class TestStatus:
    def test_a_drawing_it_doesnt_control(self, ws, tmp_path):
        f = tmp_path / "loose.draftworx"
        f.write_bytes(dumps(drawing()))
        s = ws[3].status(f)
        assert not s.controlled and s.sentence() == "" and s.primary() == "" and s.file_state == "unmanaged"

    def test_a_controlled_drawing_says_what_it_is_and_what_to_do(self, ws):
        f = controlled(ws)
        s = ws[3].status(f)
        assert s.controlled and s.number == "DWG-1" and s.project == "J-2001" and s.file_state == "unchanged"
        assert s.sentence() == f"DWG-1 rev {s.revision} · Work in Progress" and s.headline()[0] == "Available"
        assert s.primary() == "check-out" and s.primary(unsaved=True) == "check-in"

    def test_reading_an_answer_it_doesnt_know_all_of(self):
        s = Status.of({"ok": True, "controlled": True, "document": {"number": "X", "checkout": {"user": "alex", "host": "PC", "mine": False},
                                                                     "latest": {"label": "C", "state_name": "Released"}, "new": 1},
                       "file": {"state": "unchanged"}, "workspace": {"location": "/w"}})
        assert s.theirs and s.sentence() == "X rev C · Released · checked out by alex on PC" and s.primary() == ""


class TestCheckingOutAndIn:
    def test_check_out_change_check_in(self, ws):
        f = controlled(ws)
        rwx = ws[3]
        first = rwx.status(f)
        rwx.check_out(first)
        s = rwx.status(f)
        assert s.mine and s.primary() == "check-in" and s.sentence().endswith("checked out by you")
        f.write_bytes(dumps(drawing(line(0, 0, 100, 0), line(0, 0, 0, 50))))
        assert rwx.status(f).file_state == "modified"
        answer = rwx.check_in(f, "Added a line")
        assert answer["result"] == "revised" and answer["document"]["latest"]["label"] != first.revision
        s = rwx.status(f)
        assert not s.checked_out_by and s.file_state == "unchanged" and s.revision == answer["document"]["latest"]["label"]

    def test_checking_in_without_a_check_out_checks_it_out_first(self, ws):
        f = controlled(ws)
        f.write_bytes(dumps(drawing(line(0, 0, 10, 10))))
        s = ws[3].status(f)
        assert s.file_state == "modified-externally" and s.headline()[0] == "Changed without a check-out" and s.primary() == "check-in"
        assert ws[3].check_in(f, "Changed")["result"] == "revised"

    def test_someone_elses_check_out(self, ws, tmp_path):
        f = controlled(ws)
        doc_id = ws[3].status(f).doc_id
        run("-w", ws[0], "doc", "checkout", doc_id, env=elsewhere(tmp_path))
        s = ws[3].status(f)
        assert s.theirs and not s.mine and s.primary() == "" and "checked out by" in s.sentence()
        with pytest.raises(RwxError) as e:
            ws[3].check_in(f, "Mine")
        assert e.value.kind == "checked-out"
        with pytest.raises(RwxError):
            ws[3].check_out(s)

    def test_undoing_a_check_out_can_put_the_revision_back(self, ws):
        f = controlled(ws)
        before = f.read_bytes()
        rwx = ws[3]
        rwx.check_out(rwx.status(f))
        f.write_bytes(dumps(drawing(line(5, 5, 6, 6))))
        rwx.undo_check_out(rwx.status(f), put_back=True)
        s = rwx.status(f)
        assert f.read_bytes() == before and not s.checked_out_by and s.file_state == "unchanged"

    def test_undoing_it_can_keep_the_file(self, ws):
        f = controlled(ws)
        rwx = ws[3]
        rwx.check_out(rwx.status(f))
        changed = dumps(drawing(line(5, 5, 6, 6)))
        f.write_bytes(changed)
        rwx.undo_check_out(rwx.status(f), put_back=False)
        assert f.read_bytes() == changed and rwx.status(f).file_state == "modified-externally"


class TestAdding:
    def test_a_drawing_outside_the_project_is_copied_in(self, ws, tmp_path):
        eng, project, folder, rwx = ws
        f = tmp_path / "bracket.draftworx"
        f.write_bytes(dumps(drawing()))
        answer = rwx.publish(f, project, "DWG-7", "Bracket", "First issue", workspace=str(eng))
        copy = Path(answer["document"]["path"])
        assert folder in copy.parents and copy.read_bytes() == f.read_bytes() and rwx.status(copy).number == "DWG-7"
        assert not rwx.status(f).controlled

    def test_the_workspaces_it_could_go_in(self, ws, tmp_path):
        eng, project, folder, rwx = ws
        inside = folder / "03 Drawings" / "x.draftworx"
        assert [w["location"] for w in workspaces_for(rwx, inside)] == [str(eng)]
        assert workspaces_for(rwx, tmp_path / "x.draftworx") == []       # one RevisionWorx hasn't opened, and that isn't round it

    def test_which_project_a_file_is_in(self, ws, tmp_path):
        eng, project, folder, rwx = ws
        projects = rwx.projects(str(eng))
        assert project_for(projects, folder / "03 Drawings" / "x.draftworx")["id"] == project
        assert project_for(projects, tmp_path / "x.draftworx") is None


class TestPartsList:
    def test_it_goes_as_a_bom_next_to_the_drawing(self, ws):
        f = controlled(ws, valves())
        rwx = ws[3]
        bom = publish_parts(rwx, rwx.status(f), valves(), "First issue")
        d = bom["document"]
        assert bom["result"] == "created" and d["number"] == "DWG-1-BOM" and d["title"] == "Plan parts list"
        assert Path(d["path"]).parent.name == "05 BOM"
        again = publish_parts(rwx, rwx.status(f), valves(), "Same again")
        assert again["result"] == "unchanged"
        more = valves()
        with more.transaction("x") as t:
            t.add(Insert("Valve", Vec(200.0, 0.0), attribs=(Attrib("PART_NO", Text(Vec(200.0, -10.0), 2.5, "V-300")),)))
        assert publish_parts(rwx, rwx.status(f), more, "One more")["result"] == "revised"

    def test_no_parts_no_bom(self, ws):
        f = controlled(ws)
        assert publish_parts(ws[3], ws[3].status(f), drawing(), "x") is None

    def test_openmeridian_imports_it_once_its_released(self, ws):
        meridian = pytest.importorskip("openmeridian.revisionworx")
        eng = ws[0]
        f = controlled(ws, valves())
        rwx = ws[3]
        d = publish_parts(rwx, rwx.status(f), valves(), "First issue")["document"]
        client = meridian.Client(str(eng), cli=find_command())
        with pytest.raises(meridian.RevisionWorxError):
            client.read_bom(d["id"])         # nothing released: OpenMeridian fails closed
        for state in ("in_review", "approved", "released"):
            run("-w", eng, "rev", "transition", d["latest"]["id"], state)
        bom = client.read_bom(d["id"])
        assert bom["root"] == "DWG-1" and bom["source"]["released"]
        assert sorted((x["child"], x["qty"]) for x in bom["lines"]) == [("V-100", 2000), ("V-200", 1000)]     # thousandths
