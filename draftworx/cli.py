# SPDX-License-Identifier: GPL-3.0-or-later
"""The `draftworx` command. Doing anything with a drawing needs no window and no Qt."""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from . import PROGRAM, __version__
from .core.curves import Arc, Circle, Line, Polyline
from .core.dxf import check_written, read_dxf, write_dxf
from .core.model import Document
from .core.native import dumps, loads
from .core.safefile import write_verified
from .core.vec import Vec


def self_test() -> int:
    """Draw something, save it, read it back and undo it, using the installed program's own code."""
    doc = Document()
    with doc.transaction("Self-test") as t:
        t.add(Line(Vec(0.0, 0.0), Vec(100.0, 0.0)))
        t.add(Circle(Vec(50.0, 50.0), 25.0))
        t.add(Arc(Vec(0.0, 0.0), 10.0, 0.0, 1.5))
        t.add(Polyline((Vec(0.0, 0.0), Vec(1.0, 0.0), Vec(1.0, 1.0)), closed=True))
    before = doc.fingerprint()
    with tempfile.TemporaryDirectory(prefix="draftworx-selftest-") as folder:
        path = Path(folder) / "selftest.draftworx"
        write_verified(path, dumps(doc, PROGRAM), lambda data: loads(data))
        back = loads(path.read_bytes())
    if back.fingerprint() != before:
        print("self-test failed: the drawing read back differs from the one saved", file=sys.stderr)
        return 1
    doc.undo()
    if doc.entities:
        print("self-test failed: undo left something behind", file=sys.stderr)
        return 1
    with doc.transaction("Self-test") as t:
        t.add(Line(Vec(0.0, 0.0), Vec(100.0, 0.0)))
    first = write_dxf(doc)
    back, _ = read_dxf(first)
    check_written(first, doc)
    if write_dxf(back) != first:
        print("self-test failed: a DXF file didn't come back the same", file=sys.stderr)
        return 1
    from .core import sheets
    from .core.pdf import pdf_of
    with doc.transaction("Self-test") as t:
        sheets.apply(t, sheets.fitted(doc, "A4", True, 1.0))
    pdf, _ = pdf_of(doc, sheets.sheet_of(doc))
    if not (pdf.startswith(b"%PDF-1.4") and b"/MediaBox [0 0 841.8898 595.2756]" in pdf and pdf.endswith(b"%%EOF\n")):
        print("self-test failed: a PDF of the drawing didn't come out as it should", file=sys.stderr)
        return 1
    import xml.etree.ElementTree as ET
    from .core.svg import svg_of
    svg, _ = svg_of(doc, sheets.sheet_of(doc))
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        root = None
    if root is None or (root.get("width"), root.get("height")) != ("297mm", "210mm"):
        print("self-test failed: an SVG of the drawing didn't come out as it should", file=sys.stderr)
        return 1
    print(f"{PROGRAM} self-test passed: drawing, saving, reading back, undo, DXF, PDF and SVG all work.")
    try:
        from .gui.app import self_test as window_test
    except ImportError:
        print("The window wasn't tested, because PySide6 isn't available.")
        return 0
    ok, what = window_test()
    if not ok:
        print("self-test failed: the window didn't work", file=sys.stderr)
        return 1
    print(f"Window: {what}.")
    return 0


# ------------------------------------------------------------------------------------------- commands for other programs
#
# `draftworx <command> ...` prints exactly one JSON object on stdout, with "ok": true or false, and exits 0 or 2, as `revisionworx link`
# does. A refusal says why in "error", written for a person, and "kind" says what sort of refusal it is. Nothing here needs Qt.

API_VERSION = 1


class _Refused(Exception):
    def __init__(self, message: str, kind: str = "bad-input"):
        super().__init__(message)
        self.kind = kind


def _answer(obj: dict) -> int:
    import json
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, sort_keys=True) + "\n")
    return 0 if obj.get("ok") else 2


def _open(path: str):
    from .core.files import open_drawing
    from .core.native import FormatError
    try:
        return open_drawing(path)
    except FormatError as exc:
        raise _Refused(str(exc), "not-readable") from exc


def _info(args) -> dict:
    from .core.shapes import Insert
    opened = _open(args.drawing)
    doc = opened.doc
    kinds: dict[str, int] = {}
    from .core.info import name_of
    for e in doc.entities.values():
        kinds[name_of(e.shape)] = kinds.get(name_of(e.shape), 0) + 1
    box = doc.extents()
    source = doc.extras.get("dxf") if isinstance(doc.extras.get("dxf"), dict) else None
    st = doc.settings
    return {"ok": True, "path": str(opened.path), "format": "dxf" if source else "draftworx", "dxf_version": source.get("version") if source else None,
            "units": st.units, "scale": st.annotation_scale, "objects": len(doc.entities), "kinds": kinds,
            "layers": [{"name": v.name, "on": v.on, "frozen": v.frozen, "locked": v.locked} for v in doc.layers],
            "blocks": [{"name": b.name, "placed": doc.block_use(b.name)} for b in doc.blocks if not b.anonymous],
            "placed_blocks": sum(1 for e in doc.entities.values() if type(e.shape) is Insert),
            "extents": list(box) if box is not None else None, "notices": opened.notices}


def _parts(args) -> dict:
    from pathlib import Path as _Path

    from .core.parts import bom_csv, parts_list
    from .core.safefile import SaveError, write_verified
    opened = _open(args.drawing)
    parts, problems = parts_list(opened.doc)
    number = args.number or _Path(args.drawing).stem
    out = {"ok": True, "number": number, "parts": [{"number": p.number, "description": p.description, "quantity": str(p.quantity.normalize()),
                                                  "unit": p.unit, "revision": p.revision, "references": list(p.references), "blocks": list(p.blocks)}
                                                 for p in parts],
           "problems": [{"where": x.where, "what": x.what} for x in problems]}
    if args.csv:
        data = bom_csv(parts, number, args.description or "", args.revision or "")
        def verify(back: bytes) -> None:
            if back != data:
                raise ValueError("it read back differently")
        try:
            write_verified(_Path(args.csv), data, verify)
        except SaveError as exc:
            raise _Refused(str(exc), "error") from exc
        out["csv"] = str(_Path(args.csv))
    return out


def _convert(args) -> dict:
    from pathlib import Path as _Path

    from .core.dxf import VERSIONS
    from .core.files import encode_for
    from .core.safefile import SaveError, write_verified
    opened = _open(args.drawing)
    target = _Path(args.output)
    if target.suffix.casefold() not in (".dxf", ".draftworx"):
        raise _Refused("The new file's name has to end in .dxf or .draftworx.")
    version = None
    if args.dxf_version:
        version = VERSIONS.get(args.dxf_version.upper().replace("R2", "2"), args.dxf_version if args.dxf_version.startswith("AC") else None)
        if version is None:
            raise _Refused(f"There's no DXF version called {args.dxf_version}. There's {', '.join(VERSIONS)}.")
    if target.exists() and not args.overwrite:
        raise _Refused(f"{target.name} is already there. Add --overwrite to replace it.", "exists")
    data, verify, notes = encode_for(opened.doc, target, version)
    try:
        write_verified(target, data, verify)
    except SaveError as exc:
        raise _Refused(str(exc), "error") from exc
    return {"ok": True, "path": str(target), "notes": notes}


def _pdf(args) -> dict:
    from .core.pdf import pdf_of
    return _sheet_file(args, ".pdf", pdf_of)


def _svg(args) -> dict:
    from .core.svg import svg_of
    return _sheet_file(args, ".svg", svg_of)


def _sheet_file(args, suffix: str, make) -> dict:
    """A PDF or SVG of the drawing's sheet: the same sheet, chosen the same way, for either."""
    from pathlib import Path as _Path

    from .core import sheets
    from .core.safefile import SaveError, write_verified
    opened = _open(args.drawing)
    doc = opened.doc
    target = _Path(args.output)
    if target.suffix.casefold() != suffix:
        raise _Refused(f"The new file's name has to end in {suffix}.")
    if target.exists() and not args.overwrite:
        raise _Refused(f"{target.name} is already there. Add --overwrite to replace it.", "exists")
    if args.upright and args.across:
        raise _Refused("The paper can be upright or across, not both.")
    own = sheets.sheet_of(doc)
    paper = sheets.paper_named(args.paper) if args.paper else (own.paper if own else sheets.default_paper(doc))
    if paper is None:
        raise _Refused(f"There's no paper called {args.paper}. There's {', '.join(sheets.PAPERS)}.")
    n = sheets.parse_scale(args.scale) if args.scale else None
    if args.scale and n is None:
        raise _Refused(f"{args.scale} isn't a scale. Try 1:50.")
    asked = args.paper or args.scale or args.upright or args.across
    if own is None and not asked and sheets.frame_insert(doc) is not None:
        with doc.transaction("Sheet") as t:     # a drawing with a border, as a DXF brings one back: the sheet is where it is
            sheets.apply(t, sheets.first_sheet(doc))
    elif own is None or asked:
        if sheets.drawn(doc) is None:
            raise _Refused("There's nothing to print.")
        landscape = False if args.upright else True if args.across else (doc.settings.landscape if own else True)
        # the drawing isn't saved: this only says where the sheet goes for this PDF, and its border goes with it
        with doc.transaction("Sheet") as t:
            sheets.apply(t, sheets.fitted(doc, paper, landscape, n))
    sheet = sheets.sheet_of(doc)
    data, notes = make(doc, sheet, args.black, target.stem)

    def verify(back: bytes) -> None:
        if back != data:
            raise ValueError("it read back differently")
    try:
        write_verified(target, data, verify)
    except SaveError as exc:
        raise _Refused(str(exc), "error") from exc
    return {"ok": True, "path": str(target), "paper": sheet.paper, "landscape": sheet.width > sheet.height,
            "scale": sheets.scale_words(doc.settings.annotation_scale), "sheet": list(sheet.box()), "notes": notes}


COMMANDS = {
    "version": ("the program's version and this interface's", None),
    "info": ("what's in a drawing: its objects, layers, blocks and extents", _info),
    "parts": ("the parts list its blocks make, and optionally write it as a BOM that RevisionWorx reads", _parts),
    "convert": ("write a drawing as DXF or .draftworx, checked before it takes the name", _convert),
    "pdf": ("the drawing's sheet as a PDF, to scale: its own sheet, or one fitted to what's drawn when it has none or one is asked for", _pdf),
    "svg": ("the drawing's sheet as an SVG in millimetres, to scale, chosen as for pdf", _svg),
}


def run_command(argv: list[str]) -> int:
    """One of the commands for other programs. Always one JSON object out."""
    name = argv[0]
    parser = argparse.ArgumentParser(prog=f"draftworx {name}", description=COMMANDS[name][0])
    if name in ("info", "parts", "convert", "pdf", "svg"):
        parser.add_argument("drawing")
    if name == "parts":
        parser.add_argument("--csv", help="write the parts list here, as a RevisionWorx BOM")
        parser.add_argument("--number", help="the drawing's own part or assembly number (default: its file name)")
        parser.add_argument("--description", default="")
        parser.add_argument("--revision", default="")
    if name in ("pdf", "svg"):
        parser.add_argument("output")
        parser.add_argument("--paper", help="A4, A3, A2, A1, A0, Letter, Legal, Tabloid, ANSI C, ANSI D or ANSI E")
        parser.add_argument("--upright", action="store_true", help="the paper upright")
        parser.add_argument("--across", action="store_true", help="the paper across, as it is unless the drawing's own sheet is upright")
        parser.add_argument("--scale", help="like 1:50, or 2:1; the largest that fits if not given")
        parser.add_argument("--black", action="store_true", help="everything in black, as most drawings are printed")
        parser.add_argument("--overwrite", action="store_true")
    if name == "convert":
        parser.add_argument("output")
        parser.add_argument("--dxf-version", help="R12, 2000, 2004, 2007, 2010, 2013 or 2018")
        parser.add_argument("--overwrite", action="store_true")
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit:
        return _answer({"ok": False, "error": f"Those aren't arguments draftworx {name} takes. Try draftworx {name} --help.", "kind": "bad-input"})
    if name == "version":
        return _answer({"ok": True, "api": API_VERSION, "version": __version__})
    try:
        return _answer(COMMANDS[name][1](args))
    except _Refused as exc:
        return _answer({"ok": False, "error": str(exc), "kind": exc.kind})
    except Exception as exc:        # anything else is still one answer, and says what happened
        return _answer({"ok": False, "error": f"That didn't work: {exc}", "kind": "error"})


def main(argv: list[str] | None = None) -> int:
    given = sys.argv[1:] if argv is None else argv
    if given and given[0] in COMMANDS and not (given[0] == "version" and len(given) > 1):
        return run_command(given)
    parser = argparse.ArgumentParser(prog="draftworx", description="DraftWorx: a lightweight, offline 2D CAD program",
                                     epilog="For other programs: " + "; ".join(f"draftworx {k}: {v[0]}" for k, v in COMMANDS.items())
                                     + ". Each prints one JSON object.")
    parser.add_argument("--version", action="version", version=f"draftworx {__version__}")
    parser.add_argument("--self-test", action="store_true", help="check this installation works, and exit")
    parser.add_argument("drawing", nargs="?", help="a drawing to open")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if args.drawing is not None or argv is None:
        try:
            from .gui.app import main as window
        except ImportError as exc:
            print(f"The window can't start: {exc}", file=sys.stderr)
            return 1
        return window([args.drawing] if args.drawing else [])
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
