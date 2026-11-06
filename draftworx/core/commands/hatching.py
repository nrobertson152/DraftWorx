# SPDX-License-Identifier: GPL-3.0-or-later
"""Hatching: click inside an area, and it's filled.

Pointing shows the area that a click would fill, and where it's open if it isn't closed. The pattern is sized for the sheet at the
drawing's scale, like text and dimensions (ADR-5), so the default is right on every drawing. A hatch follows the objects round it.
"""
from __future__ import annotations

import math
import time

from . import command
from .. import hatch as hatches
from .. import regions
from ..curves import Circle
from ..select import hatch_distance
from ..session import ENTER, UNDO, GetDistance, GetNumber, GetPick, GetText, Keyword, Pick, Preview, Typed
from ..shapes import Hatch
from ..units import unit_factor
from ..vec import Vec

LOOK_ROUND = 150.0      # pixels round the pointer looked at first
PREVIEW_TIME = 0.15     # seconds an area may take to find while the pointer moves; a click always finds it


def paper_scale(doc) -> float:
    """Drawing units to a millimetre on the sheet, at the drawing's scale."""
    st = doc.settings
    return st.annotation_scale * unit_factor("mm", st.units)


def _already(doc, region: regions.Region, p: Vec) -> bool:
    """Is that area hatched already?"""
    box = region.bbox()
    size = max(box[2] - box[0], box[3] - box[1]) if box else 1.0
    for e in doc.entities.values():
        s = e.shape
        if type(s) is Hatch and not doc.is_hidden(e.style) and hatch_distance(s, p) == 0.0 and regions.same_area(s.loops, region.loops, size):
            return True
    return False


@command("HATCH", "H", "BH", "BHATCH", group="Draw", help="Fill an area with a pattern or solid colour: click inside it", icon="hatch")
def hatch_command(s):
    memory = s.memory
    made: list[int] = []
    last: dict = {}

    def pattern() -> str:
        return memory.get("hatch_pattern", hatches.DEFAULT)

    def scale() -> float:
        return memory.get("hatch_scale", 1.0) * paper_scale(s.doc)

    def area_at(p: Vec, px: float, quick: bool):
        gap = memory.get("hatch_gap", 0.0)
        known = last.get("found")
        if known is not None and last.get("at") == (s.doc_version, gap) and isinstance(known, regions.Region):
            h = Hatch(known.loops)
            if hatch_distance(h, p) == 0.0:
                return known
        deadline = time.monotonic() + PREVIEW_TIME if quick else None
        found = regions.find(s.doc, p, LOOK_ROUND * px, gap=gap, deadline=deadline)
        last["found"], last["at"] = found, (s.doc_version, gap)
        return found

    def make(found: regions.Region, p: Vec) -> Hatch:
        return hatches.made_from(found, pattern(), scale(), math.radians(memory.get("hatch_angle", 0.0)), p)

    def preview(p, px):
        try:
            found = area_at(p, px, True)
        except regions.TooSlow:
            return []
        if isinstance(found, regions.NoRegion):
            return Preview(doomed=tuple(Circle(q, 8.0 * px) for q in found.gaps))
        return Preview(ghosts=(make(found, p),))

    while True:
        name = pattern()
        what = name if name == "SOLID" else f"{name} at {memory.get('hatch_scale', 1.0):g}"
        gap = memory.get("hatch_gap", 0.0)
        if gap:
            what += f", closing gaps up to {gap:g}"
        options = ("Pattern", "Scale", "Angle", "Gap") + (("Undo",) if made else ())
        ans = yield GetPick(f"Hatch ({what}): click inside each area to fill it. Type a pattern's name, or P, S, A or G. Enter to finish.",
                            options, undoable=bool(made), preview=preview, enter=True, typed=True)
        if ans is ENTER:
            return
        if ans is UNDO or ans == Keyword("Undo"):
            if made:
                with s.doc.transaction("Undo hatch") as t:
                    t.remove(made.pop())
            continue
        if ans == Keyword("Pattern"):
            typed = yield GetText(f"Hatch: which pattern? {', '.join(hatches.PATTERNS)}.", default=name)
            if typed is not ENTER:
                ans = Typed(typed)
            else:
                continue
        if isinstance(ans, Typed):
            found_name = hatches.known(ans.text)
            if found_name is None:
                s.message = f"There's no pattern called {ans.text.strip()}. There's {', '.join(hatches.PATTERNS)}."
            else:
                memory["hatch_pattern"] = found_name
                s.message = f"{found_name}: {hatches.PATTERNS[found_name][0]}."
            continue
        if ans == Keyword("Scale"):
            k = yield GetNumber("Hatch: how big is the pattern? 1 is its own size on the sheet, at the drawing's scale.", default=memory.get("hatch_scale", 1.0))
            if k <= 0.0:
                s.message = "A pattern's scale has to be more than zero."
            else:
                memory["hatch_scale"] = k
            continue
        if ans == Keyword("Angle"):
            memory["hatch_angle"] = (yield GetNumber("Hatch: turn the pattern by how many degrees?", default=memory.get("hatch_angle", 0.0)))
            continue
        if ans == Keyword("Gap"):
            g = yield GetDistance("Hatch: close gaps up to how wide? 0 closes none.", default=gap)
            memory["hatch_gap"] = max(0.0, g)
            last.clear()
            continue
        if not isinstance(ans, Pick):
            continue
        found = area_at(ans.point, ans.px, False)
        if isinstance(found, regions.NoRegion):
            s.message = found.reason
            continue
        if _already(s.doc, found, ans.point):
            s.message = "That area is hatched already."
            continue
        h = make(found, ans.point)
        try:
            hatches.pieces(h)
        except hatches.TooDense as exc:
            s.message = str(exc)
            continue
        with s.doc.transaction("Hatch") as t:
            made.append(t.add(h))
