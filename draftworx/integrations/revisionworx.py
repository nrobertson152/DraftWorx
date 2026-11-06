# SPDX-License-Identifier: GPL-3.0-or-later
"""RevisionWorx, the family's revision control, reached only through its command line.

`revisionworx link` is its interface for other programs (docs/INTEGRATION.md there): each call answers with one JSON object. It says
whether a file is a controlled document and how the file compares with the latest revision, and `publish` checks a file in, checking it
out first if nobody has it, or puts a new file under revision control. Checking out and undoing a check-out aren't part of `link`, so
they use `revisionworx --json doc checkout|cancel`, as FluidWorx does. RevisionWorx's own rules, locks and records apply exactly as they
would by hand. DraftWorx never reads or writes a workspace itself.

Finding the program follows RevisionWorx's reference client (contrib/rwx_client.py): REVISIONWORX_CLI, `revisionworx` on the PATH, the
standalone build where it installs, then `python -m revisionworx`. Without it, none of this shows.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

API_VERSION = 1
TIMEOUT = 120.0         # RevisionWorx waits up to 90 s for another program's change to finish before it says it's busy
_NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}      # no console flashing up on Windows


class RwxError(Exception):
    """RevisionWorx refused, or couldn't be asked. The message is written for the person; `kind` is RevisionWorx's (`not-found`,
    `checked-out`, `busy`, `no-workspace`, `bad-input`, `error`), or `unavailable` when it isn't installed."""

    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.kind = kind


def _standalone() -> list[Path]:
    names = ("revisionworx.exe",) if os.name == "nt" else ("revisionworx",)
    roots: list[Path] = []
    if os.name == "nt":
        roots += [Path(os.environ[v]) for v in ("LOCALAPPDATA", "ProgramFiles", "ProgramFiles(x86)") if os.environ.get(v)]
        roots.append(Path.home() / "Downloads")
    else:
        roots += [Path.home() / "Applications", Path.home() / ".local" / "opt", Path("/opt"), Path.home()]
    out = []
    for root in roots:
        try:
            for folder in sorted(root.glob("RevisionWorx*"), reverse=True):
                out += [folder / n for n in names if (folder / n).is_file()]
        except OSError:
            continue
    return out


def find_command() -> list[str] | None:
    """What runs RevisionWorx here, or None."""
    given = os.environ.get("REVISIONWORX_CLI")
    if given:
        return [given]
    found = shutil.which("revisionworx")
    if found:
        return [found]
    for exe in _standalone():
        return [str(exe)]
    if not getattr(sys, "frozen", False):
        try:
            if importlib.util.find_spec("revisionworx") is not None:
                return [sys.executable, "-m", "revisionworx"]
        except (ImportError, ValueError):
            pass
    return None


def _environment() -> dict:
    """The environment for RevisionWorx: ours, without what a packaged DraftWorx sets for its own libraries."""
    env = dict(os.environ)
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
        for k in ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH"):
            env.pop(k, None)
        orig = env.pop("LD_LIBRARY_PATH_ORIG", None)
        if orig is not None:
            env["LD_LIBRARY_PATH"] = orig
        else:
            env.pop("LD_LIBRARY_PATH", None)
    return env


@dataclass(frozen=True)
class Status:
    """What RevisionWorx says about one file."""
    controlled: bool
    path: str = ""
    workspace: str = ""             # the workspace's folder
    doc_id: str = ""
    number: str = ""
    title: str = ""
    project_id: str = ""
    project: str = ""               # its number, or its name
    revision: str = ""              # the latest revision's label
    state: str = ""                 # and its lifecycle state, as RevisionWorx names it
    released: str = ""              # the released revision's label, if one is
    file_state: str = "unmanaged"   # unchanged, modified, modified-externally, missing, unrevisioned or unmanaged
    checked_out_by: str = ""        # "alex on SHOP-PC", or "" if nobody has it
    mine: bool = False              # checked out to this computer's session: DraftWorx, the RevisionWorx window and its command line

    @classmethod
    def of(cls, answer: dict) -> "Status":
        file = answer.get("file") or {}
        if not answer.get("controlled"):
            return cls(False, str(file.get("path", "")))
        doc = answer.get("document") or {}
        latest = doc.get("latest") or {}
        released = doc.get("released") or {}
        project = doc.get("project") or {}
        co = doc.get("checkout")
        who = f"{co.get('user') or '?'} on {co.get('host') or '?'}" if co else ""
        return cls(True, str(file.get("path", "")), str((answer.get("workspace") or {}).get("location", "")), str(doc.get("id", "")),
                   str(doc.get("number") or ""), str(doc.get("title") or ""), str(project.get("id") or ""),
                   str(project.get("number") or project.get("name") or ""), str(latest.get("label") or ""), str(latest.get("state_name") or ""),
                   str(released.get("label") or ""), str(file.get("state") or ""), who, bool(co and co.get("mine")))

    @property
    def name(self) -> str:
        return self.number or self.title or "This drawing"

    @property
    def theirs(self) -> bool:
        """Checked out by someone else: their working file, which nobody else should write."""
        return bool(self.checked_out_by) and not self.mine

    @property
    def changed(self) -> bool:
        """The file isn't what the latest revision is."""
        return self.file_state in ("modified", "modified-externally", "unrevisioned")

    def headline(self) -> tuple[str, str, str]:
        """What's true and what to do next, as RevisionWorx's own document panel says it: (title, sentence, tone), the tone being ok, info,
        warn, danger or neutral."""
        if not self.controlled:
            return ("", "", "neutral")
        if self.file_state == "missing":
            return ("File missing", "The file isn't where RevisionWorx expects it.", "danger")
        if self.theirs:
            return (f"Checked out by {self.checked_out_by}", "Read-only for you until they check it in. Save a copy to keep your changes.",
                    "warn")
        if self.file_state == "modified-externally":
            return ("Changed without a check-out", "Check it in to keep the changes as a new revision.", "warn")
        if self.mine:
            if self.file_state == "modified":
                return ("Checked out by you · changed", "Check it in to keep your changes as a new revision.", "info")
            return ("Checked out by you", "Change it, save it, then check it in.", "info")
        if self.file_state == "unrevisioned":
            return ("No revision yet", "Check it in to make its first revision.", "neutral")
        return ("Available", "Check it out before you change it.", "ok")

    def primary(self, unsaved: bool = False) -> str:
        """The one obvious next step: "check-in", "check-out", or "" when there's none. `unsaved`: the drawing has changes not saved yet."""
        if not self.controlled or self.theirs or self.file_state == "missing":
            return ""
        if self.mine or self.changed or unsaved:
            return "check-in"
        return "check-out"

    def sentence(self) -> str:
        """RevisionWorx's status line for it (contrib/rwx_client.py `describe`): "DWG-1 rev B · Released · checked out by you"."""
        if not self.controlled:
            return ""
        parts = [f"{self.name} rev {self.revision or '?'}", self.state]
        if self.checked_out_by:
            parts.append("checked out by you" if self.mine else f"checked out by {self.checked_out_by}")
        if self.file_state in ("modified", "modified-externally"):
            parts.append("this file differs from the latest revision")
        elif self.file_state == "missing":
            parts.append("file missing")
        return " · ".join(p for p in parts if p)


class RevisionWorx:
    """One RevisionWorx installation."""

    def __init__(self, command: list[str] | None = None, timeout: float = TIMEOUT):
        self.command = command if command is not None else find_command()
        self.timeout = timeout
        self._api: int | None = None

    def available(self) -> bool:
        """Installed, and speaking the interface this was written for."""
        if self.command is None:
            return False
        if self._api is None:
            try:
                self._api = int(self._link("version").get("api", 0))
            except (RwxError, ValueError, TypeError):
                self._api = 0
        return self._api == API_VERSION

    def _call(self, args: list[str]) -> subprocess.CompletedProcess:
        if self.command is None:
            raise RwxError("RevisionWorx isn't installed, or DraftWorx can't find it. Put it on the PATH, or set REVISIONWORX_CLI to its "
                           "revisionworx program.", "unavailable")
        try:
            return subprocess.run([*self.command, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=self.timeout, stdin=subprocess.DEVNULL, env=_environment(), **_NO_WINDOW)
        except FileNotFoundError:
            raise RwxError(f"{self.command[0]} couldn't be run.", "unavailable") from None
        except subprocess.TimeoutExpired:
            raise RwxError("RevisionWorx didn't answer in time. Is the workspace busy, or its folder offline?", "busy") from None
        except OSError as exc:
            raise RwxError(f"RevisionWorx couldn't be run: {exc}", "unavailable") from None

    def _link(self, *args: str, workspace: str = "") -> dict:
        r = self._call([*(["-w", workspace] if workspace else []), "link", *args])
        lines = [x for x in r.stdout.splitlines() if x.strip()]
        try:
            data = json.loads(lines[-1]) if lines else None
        except ValueError:
            data = None
        if not isinstance(data, dict):
            said = (r.stderr or r.stdout or "").strip().splitlines()
            raise RwxError("RevisionWorx gave an answer DraftWorx can't read" + (f": {said[-1]}" if said else "."), "error")
        if not data.get("ok"):
            raise RwxError(str(data.get("error") or "RevisionWorx refused."), str(data.get("kind") or "error"))
        return data

    def _doc(self, workspace: str, *args: str) -> dict:
        """`revisionworx --json -w WORKSPACE doc ...`: one JSON object, or `error: ...` on stderr."""
        r = self._call(["--json", "-w", workspace, "doc", *args])
        if r.returncode != 0:
            said = (r.stderr or r.stdout).strip()
            said = said[len("error: "):] if said.startswith("error: ") else said
            kind = "checked-out" if "checked out" in said else "busy" if "busy" in said else "error"
            raise RwxError(said or f"RevisionWorx stopped with code {r.returncode}.", kind)
        try:
            data = json.loads(r.stdout) if r.stdout.strip() else {}
        except ValueError:
            raise RwxError("RevisionWorx gave an answer DraftWorx can't read.", "error") from None
        return data if isinstance(data, dict) else {}

    # --------------------------------------------------------------------------------------------------- what it does

    def status(self, file: str | os.PathLike) -> Status:
        return Status.of(self._link("status", os.path.abspath(file)))

    def check_out(self, s: Status) -> None:
        self._doc(s.workspace, "checkout", s.doc_id, "--note", "DraftWorx")

    def undo_check_out(self, s: Status, put_back: bool) -> None:
        """Give the check-out up. `put_back` also puts the latest revision back in the file, losing what was changed since."""
        self._doc(s.workspace, "cancel", s.doc_id, *(["--revert"] if put_back else []))

    def check_in(self, file: str | os.PathLike, message: str) -> dict:
        """A new revision of the document this file is, checked out first if nobody has it. `result` is "revised", or "unchanged" when
        the file is what the latest revision already is."""
        return self._link("publish", os.path.abspath(file), "--message", message)

    def publish(self, file: str | os.PathLike, project: str, number: str = "", title: str = "", message: str = "", folder: str = "",
                workspace: str = "") -> dict:
        """A new document in `project`, or a new revision of the one numbered `number` there. A file outside the project's folder is
        copied into it (into `folder`, a subfolder), and never over another; the answer's `document.path` is the file it controls."""
        args = ["publish", os.path.abspath(file), "--project", project]
        for flag, value in (("--number", number), ("--title", title), ("--folder", folder), ("--message", message)):
            if value:
                args += [flag, value]
        return self._link(*args, workspace=workspace)

    def projects(self, workspace: str = "") -> list[dict]:
        return list(self._link("projects", workspace=workspace).get("projects") or [])

    def workspaces(self) -> list[dict]:
        return list(self._link("workspaces").get("workspaces") or [])

    def show(self, file: str | os.PathLike) -> None:
        """Show the document in RevisionWorx's window."""
        self._link("open", "--file", os.path.abspath(file))


def _file_name(text: str) -> str:
    """Text as part of a file name on Windows and Linux."""
    out = "".join("-" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in text).strip(" .")
    return out or "parts"


def publish_parts(rwx: RevisionWorx, s: Status, doc, message: str) -> dict | None:
    """The drawing's parts list (`core.parts`), as a BOM document next to it in its project: numbered as the drawing is, with -BOM, in a
    folder of the project's that's for BOMs if it has one. A new revision of it if it's there already, and none if nothing changed.
    `s` is the drawing's status after it was checked in, so the BOM names the revision it came from. None if there are no parts."""
    import tempfile

    from ..core.parts import bom_csv, parts_list
    parts, _ = parts_list(doc)
    if not parts or not s.controlled:
        return None
    number = f"{s.number}-BOM" if s.number else f"{Path(s.path).stem}-BOM"
    folder = ""
    for p in rwx.projects(s.workspace):
        if p.get("id") == s.project_id and p.get("folder"):
            try:
                found = sorted(x.name for x in Path(p["folder"]).iterdir() if x.is_dir() and "bom" in x.name.casefold())
            except OSError:
                found = []
            folder = found[0] if found else ""
    with tempfile.TemporaryDirectory(prefix="draftworx-bom-") as tmp:
        f = Path(tmp) / f"{_file_name(number)}.csv"
        f.write_bytes(bom_csv(parts, s.number or Path(s.path).stem, s.title, s.revision))
        return rwx.publish(f, s.project_id, number, f"{s.title or s.name} parts list", message, folder, s.workspace)


def workspaces_for(rwx: RevisionWorx, file: str | os.PathLike | None) -> list[dict]:
    """The workspaces a drawing could go in: the one round its folder, if there is one, then those opened in RevisionWorx's window.
    Each is {"location", "name"}."""
    out: list[dict] = []
    seen: set[str] = set()
    if file is not None:
        d = Path(file).resolve().parent
        for folder in (d, *d.parents):
            if (folder / ".revisionworx").is_dir():
                out.append({"location": str(folder), "name": folder.name})
                seen.add(os.path.normcase(str(folder)))
                break
    for w in rwx.workspaces():
        loc = str(w.get("location") or "")
        if loc and not w.get("error") and os.path.normcase(str(Path(loc).resolve())) not in seen:
            out.append({"location": loc, "name": str(w.get("name") or Path(loc).name)})
            seen.add(os.path.normcase(str(Path(loc).resolve())))
    return out


def project_for(projects: list[dict], file: str | os.PathLike) -> dict | None:
    """The project whose folder holds this file, if one does."""
    try:
        here = Path(file).resolve()
    except OSError:
        return None
    best: tuple[int, dict] | None = None
    for p in projects:
        folder = p.get("folder")
        if not folder or p.get("archived"):
            continue
        try:
            root = Path(folder).resolve()
        except OSError:
            continue
        if root == here or root in here.parents:
            depth = len(root.parts)
            if best is None or depth > best[0]:
                best = (depth, p)
    return best[1] if best else None


__all__ = ["API_VERSION", "RevisionWorx", "RwxError", "Status", "find_command", "project_for", "publish_parts", "workspaces_for"]
