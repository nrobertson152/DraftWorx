# SPDX-License-Identifier: GPL-3.0-or-later
"""A revision code on a print: a QR code holding the FieldWorx label of the revision the sheet shows, `FWX:R:<number>@<revision>`
(FieldWorx's docs/PROTOCOL.md, section 7). Anyone with FieldWorx on a phone can scan a paper copy and see whether it is still the
released revision, and which one is if it isn't. It holds no web address and nothing else.

The code always goes in the same place: above the right-hand end of the title block, or, on a sheet without a border, in the lower
right corner, 10 mm in from the edges. It never covers the drawing. `blocked` lists what's printed there; the command line then
refuses, and the window prints without the code and says why. The QR code itself comes from fwx_label.py, FieldWorx's encoder,
copied in unchanged so every program in the family draws the same code for the same revision.
"""
from __future__ import annotations

from dataclasses import dataclass

from .curves import Polyline
from .fwx_label import QRError, encode, label_code
from .model import Document
from .shapes import Text, text_width
from .sheets import Sheet, frame_insert, frame_margins
from .vec import Vec

SIZE = 20.0         # mm: the QR code's square on the paper, its quiet zone of 4 modules included
CAPTION = 2.0       # mm: the height of the capitals of the words under it
GAP = 1.5           # mm between the code's area and the border or title block
LEVEL = "Q"         # a quarter of the code can be dirty or torn and it still reads
TITLE_BLOCK = 40.0  # mm: the height of the title block DraftWorx draws (four rows of 10 mm)


class CodeError(ValueError):
    """There's no revision code to print, or nowhere to print it. The message is written for the person."""


@dataclass(frozen=True)
class RevisionCode:
    """A drawing's number and the label of the revision a print shows."""
    number: str
    revision: str

    @classmethod
    def parse(cls, text: str) -> "RevisionCode":
        """`DWG-1@B`: the revision is what follows the last @."""
        number, at, revision = text.strip().rpartition("@")
        code = cls(number.strip(), revision.strip())
        if not at:
            raise CodeError(f"A revision code is the drawing's number and its revision, like DWG-1@B, not {text.strip()!r}.")
        code.text                                   # checks it, as FieldWorx would read it
        return code

    @property
    def text(self) -> str:
        """What the QR code holds."""
        try:
            return label_code("R", f"{self.number}@{self.revision}")
        except QRError as exc:
            raise CodeError(f"{self.number}@{self.revision} can't be a revision code: {exc}.") from None

    @property
    def caption(self) -> str:
        return f"{self.number} rev {self.revision}"


def area(doc: Document, sheet: Sheet) -> tuple[float, float, float, float]:
    """Where the code and its caption go, in millimetres from the paper's lower-left corner: (left, bottom, right, top)."""
    if frame_insert(doc) is not None:
        edge = frame_margins(sheet.paper)[1]                        # the border, as sheets.frame draws it
        right, bottom = sheet.width - edge - GAP, edge + TITLE_BLOCK + GAP
    else:
        right, bottom = sheet.width - 10.0, 10.0
    return (right - SIZE, bottom, right, bottom + SIZE + CAPTION + 1.5)


def blocked(doc: Document, sheet: Sheet) -> list[int]:
    """The objects printed where the code goes. It's only printed where nothing is."""
    from .select import in_window
    x0, y0, x1, y1 = area(doc, sheet)
    k = sheet.per_mm
    window = (sheet.at[0] + x0 * k, sheet.at[1] + y0 * k, sheet.at[0] + x1 * k, sheet.at[1] + y1 * k)
    out = []
    for i in in_window(doc, window, True, px=0.05 * k, skip_locked=False):
        layer = doc.layers.get(doc.entities[i].style.layer)
        if layer is None or (layer.visible and layer.plot):
            out.append(i)
    return out


def why_not(doc: Document, sheet: Sheet) -> str:
    """Why the code can't go on this sheet, or "" when it can."""
    if blocked(doc, sheet):
        where = "above the title block, at the right" if frame_insert(doc) is not None else "in the lower right corner"
        return (f"There's no room for the revision code: something is drawn where it goes, {where}. It needs {SIZE:g} x "
                f"{SIZE + CAPTION + 1.5:g} mm clear.")
    return ""


def draw_code(page, code: RevisionCode, doc: Document, sheet: Sheet) -> None:
    """Draw the code on a page (printing.Page), in black: the QR code's modules as one fill, and the number and revision under it."""
    x0, y0, x1, y1 = area(doc, sheet)
    rows = encode(code.text, LEVEL)
    unit = SIZE / (len(rows) + 8)
    left, top = x0 + 4 * unit, y1 - 4 * unit
    loops = []
    for r, row in enumerate(rows):
        c = 0
        while c < len(row):
            if not row[c]:
                c += 1
                continue
            start = c
            while c < len(row) and row[c]:
                c += 1
            xa, xb = left + start * unit, left + c * unit
            yb, yt = top - (r + 1) * unit, top - r * unit
            loops.append((Polyline((Vec(xa, yb), Vec(xb, yb), Vec(xb, yt), Vec(xa, yt)), closed=True),))
    page.fill(tuple(loops), 0x000000)
    words = code.caption
    height = min(CAPTION, CAPTION * (x1 - x0) / max(text_width(words, CAPTION), 1e-9))
    if height >= 1.0:
        page.text(Text(Vec((x0 + x1) / 2, y0 + 0.6), height, words, halign=1), 0x000000)


__all__ = ["CAPTION", "CodeError", "LEVEL", "RevisionCode", "SIZE", "area", "blocked", "draw_code", "why_not"]
