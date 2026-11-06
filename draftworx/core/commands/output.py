# SPDX-License-Identifier: GPL-3.0-or-later
"""The sheet a drawing prints on, and cleaning a drawing up before it goes out."""
from __future__ import annotations

from dataclasses import replace

from . import command
from .. import cleanup, sheets
from ..session import ENTER, GetPick, Keyword, Pick, Preview, Typed
from ..vec import Vec


def _sheet(st) -> sheets.Sheet:
    w, h = sheets.size_of(st.paper, st.landscape)
    return sheets.Sheet(st.paper, w, h, Vec(st.sheet_x, st.sheet_y), sheets.per_mm(st))


@command("SHEET", "PAGESETUP", "LIMITS", group="Output", help="Choose the paper, the drawing's scale and where the sheet goes", icon="sheet")
def sheet_command(s):
    doc = s.doc
    st = doc.settings if sheets.sheet_of(doc) is not None else sheets.first_sheet(doc, s.memory.get("paper"))
    with_frame = sheets.frame_insert(doc) is not None
    placed = False      # once it's been clicked into place, a new paper or scale keeps it there; until then, it goes round what's drawn
    while True:
        sheet = _sheet(st)
        frame = "" if with_frame else " B adds a border and title block."
        ans = yield GetPick(f"Sheet ({st.paper} {'across' if st.landscape else 'upright'}, {sheets.scale_words(st.annotation_scale)}): click where its "
                            f"middle goes. Type a paper (A4, A3, Letter...) or a scale (1:50). F fits the drawing, T turns it.{frame} Enter to keep it.",
                            ("Fit", "Turn") + (() if with_frame else ("Border",)), preview=lambda p, px, sh=sheet: Preview(ghosts=(sh.outline(),)),
                            enter=True, typed=True)
        if ans is ENTER:
            break
        middle = Vec((sheet.box()[0] + sheet.box()[2]) / 2.0, (sheet.box()[1] + sheet.box()[3]) / 2.0) if placed else None
        if isinstance(ans, Pick):
            w, h = sheets.size_of(st.paper, st.landscape)
            k = sheets.per_mm(st)
            st = replace(st, sheet_x=ans.point[0] - w * k / 2.0, sheet_y=ans.point[1] - h * k / 2.0)
            placed = True
        elif ans == Keyword("Fit"):
            st = replace(sheets.fitted(doc, st.paper, st.landscape), ltscale=st.ltscale)
            placed = False
        elif ans == Keyword("Turn"):
            st = replace(sheets.fitted(doc, st.paper, not st.landscape, st.annotation_scale, middle), ltscale=st.ltscale)
        elif ans == Keyword("Border"):
            with_frame = True
        elif isinstance(ans, Typed):
            paper = sheets.paper_named(ans.text)
            n = sheets.parse_scale(ans.text) if paper is None else None
            if paper is not None:
                st = replace(sheets.fitted(doc, paper, st.landscape, st.annotation_scale, middle), ltscale=st.ltscale)
                s.memory["paper"] = paper
            elif n is not None:
                st = replace(sheets.fitted(doc, st.paper, st.landscape, n, middle), ltscale=st.ltscale)
            else:
                s.message = f"{ans.text.strip()} isn't a paper or a scale. Try A3, Letter or 1:50."
    with doc.transaction("Sheet") as t:
        sheets.apply(t, replace(st, ltscale=doc.settings.ltscale))
        if with_frame and sheets.frame_insert(doc) is None:
            sheets.place_frame(t, sheets.sheet_of(doc), st.landscape)
    s.message = f"The sheet is {st.paper} {'across' if st.landscape else 'upright'}, at {sheets.scale_words(st.annotation_scale)}."


@command("CLEANUP", "OVERKILL", "PURGE", group="Modify", help="Take out what's drawn twice or has no size, join overlapping lines, and drop names nothing uses",
         icon="cleanup")
def cleanup_command(s):
    found = cleanup.plan(s.doc)
    if not found:
        s.message = "There's nothing to clean up."
        return
    doomed = tuple(s.doc.entities[i].shape for i in found.gone if i in s.doc.entities)
    made = tuple(line for _, line in found.joined)
    while True:
        ans = yield GetPick(f"Cleanup: {found.sentence()}. What goes is shown in red. Enter to clean up, Esc to leave it.", ("Clean up",),
                            preview=lambda p, px: Preview(ghosts=made, doomed=doomed), enter=True)
        if ans is ENTER or ans == Keyword("Clean up"):
            break
    with s.doc.transaction("Cleanup") as t:
        cleanup.apply(t, found)
    s.message = f"Cleaned up: {found.sentence()}. One undo puts it all back."
