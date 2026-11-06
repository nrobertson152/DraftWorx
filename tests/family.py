# SPDX-License-Identifier: GPL-3.0-or-later
"""The family's other programs, set up as a person would, for the tests that use them: a RevisionWorx workspace with a project, a drawing
under revision control in it, and someone else's session."""
import json
import os
import subprocess
from pathlib import Path

from draftworx.core.model import BlockDef, Document, Entity
from draftworx.core.native import dumps
from draftworx.core.shapes import AttDef, Attrib, Insert, Text
from draftworx.core.vec import Vec
from draftworx.integrations.revisionworx import RevisionWorx, find_command
from driver import line


def run(*args, env=None) -> str:
    """RevisionWorx's own command line."""
    r = subprocess.run([*find_command(), *[str(a) for a in args]], capture_output=True, text=True, timeout=120, env=env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def drawing(*shapes) -> Document:
    doc = Document()
    with doc.transaction("x") as t:
        for s in shapes or (line(0, 0, 100, 0),):
            t.add(s)
    return doc


def valves() -> Document:
    """Two V-100 valves and a V-200, as blocks with part numbers."""
    doc = Document()
    field = AttDef(Attrib("PART_NO", Text(Vec(0.0, -10.0), 2.5, "V-100"), "Part number?"))
    with doc.transaction("x") as t:
        t.put_block(BlockDef("Valve", Vec(0.0, 0.0), (Entity(1, line(-5, 0, 5, 0)), Entity(2, field))))
        for x, pn in ((0, "V-100"), (50, "V-100"), (100, "V-200")):
            t.add(Insert("Valve", Vec(float(x), 0.0), attribs=(Attrib("PART_NO", Text(Vec(float(x), -10.0), 2.5, pn)),)))
    return doc


def workspace(tmp_path: Path):
    """(workspace folder, project id, project folder, RevisionWorx) for a workspace with one project, J-2001 Pump skid."""
    eng = tmp_path / "eng"
    run("init", eng, "--name", "Engineering")
    (eng / "Projects").mkdir()
    run("-w", eng, "root", "add", "Projects", eng / "Projects")
    project = json.loads(run("-w", eng, "--json", "project", "new", "--name", "Pump skid", "--number", "J-2001"))
    rwx = RevisionWorx()
    assert rwx.available()
    folder = Path(next(p["folder"] for p in rwx.projects(str(eng)) if p["id"] == project["id"]))
    return eng, project["id"], folder, rwx


def controlled(ws, doc=None, name="plan.draftworx") -> Path:
    """A drawing in the project's folder, put under revision control as DWG-1, Plan."""
    eng, project, folder, rwx = ws
    f = folder / "03 Drawings" / name
    f.write_bytes(dumps(doc or drawing()))
    answer = rwx.publish(f, project, "DWG-1", "Plan", "First issue")
    assert answer["result"] == "created" and Path(answer["document"]["path"]) == f
    return f


def elsewhere(tmp_path: Path) -> dict:
    """Another person's session: RevisionWorx keeps check-outs per user and computer, in their own folders."""
    env = dict(os.environ)
    for var in ("XDG_STATE_HOME", "LOCALAPPDATA"):
        env[var] = str(tmp_path / "someone-else")
    return env
