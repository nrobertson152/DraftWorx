# SPDX-License-Identifier: GPL-3.0-or-later
"""The parts list a drawing's blocks make: every placed block with a part number among its attributes, counted.

A block says what part it is with an attribute tagged PART_NO (or PARTNO, PART_NUMBER, PART, PN, P/N, ITEM_NO), and may say its
DESCRIPTION, QTY, UNIT, and a TAG or REF that names this one (Y1, M2). The tags are the ones people use; nothing has to be set up.

The list is written as RevisionWorx reads a BOM (`docs/bom` there): an item row for each part, then a line for each, under the drawing's
own number. That's how a parts list reaches OpenMeridian: RevisionWorx keeps it as a controlled document, and OpenMeridian imports a
released one. Quantities are exact decimals, as RevisionWorx wants them.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .model import Document
from .shapes import Insert

NUMBER_TAGS = ("PART_NO", "PARTNO", "PART_NUMBER", "PARTNUMBER", "PART", "PN", "P/N", "ITEM_NO", "ITEMNO")
DESCRIPTION_TAGS = ("DESCRIPTION", "DESC", "TITLE", "NAME")
QUANTITY_TAGS = ("QTY", "QUANTITY", "COUNT")
UNIT_TAGS = ("UNIT", "UOM", "UNITS")
REFERENCE_TAGS = ("TAG", "REF", "REFDES", "REFERENCE", "MARK", "ID")
REVISION_TAGS = ("REV", "REVISION", "PART_REV")
#: What a BOM file of RevisionWorx's has, in its order
COLUMNS = ("parent_number", "find_number", "child_number", "child_revision", "quantity", "unit", "reference_designators", "description",
           "part_unit", "note")


@dataclass(frozen=True, slots=True)
class Part:
    number: str
    description: str
    quantity: Decimal
    unit: str
    revision: str
    references: tuple[str, ...]
    blocks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Problem:
    """A placed block whose fields can't be counted, said for the person."""
    where: str
    what: str


def _field(values: dict[str, str], tags: tuple[str, ...]) -> str:
    for t in tags:
        v = values.get(t)
        if v:
            return v
    return ""


def _quantity(text: str) -> Decimal | None:
    t = text.strip().replace(",", "")
    if not t:
        return Decimal(1)
    try:
        q = Decimal(t)
    except InvalidOperation:
        return None
    return q if q.is_finite() and q > 0 else None


def parts_list(doc: Document) -> tuple[list[Part], list[Problem]]:
    """Every placed block with a part number, counted by part, in part number order. A block placed in an array counts once for each copy.
    Blocks inside other blocks aren't looked inside: a block is a part, and an assembly is a block with its own number."""
    found: dict[str, dict] = {}
    problems: list[Problem] = []
    for e in doc.sorted_entities():
        s = e.shape
        if type(s) is not Insert or doc.is_hidden(e.style):
            continue
        values = {a.tag.strip().upper(): a.text.text.strip() for a in s.attribs}
        definition = doc.blocks.get(s.block)
        if definition is not None:
            for inner in definition.entities:
                if type(inner.shape).__name__ == "AttDef" and inner.shape.attrib.constant:
                    values.setdefault(inner.shape.attrib.tag.strip().upper(), inner.shape.attrib.text.text.strip())
        number = _field(values, NUMBER_TAGS)
        if not number:
            continue
        q = _quantity(_field(values, QUANTITY_TAGS))
        if q is None:
            problems.append(Problem(f"{s.block} at {s.p[0]:g}, {s.p[1]:g}", f"its quantity {_field(values, QUANTITY_TAGS)!r} isn't a number above zero"))
            continue
        q *= s.columns * s.rows
        key = number.casefold()
        row = found.setdefault(key, {"number": number, "description": "", "quantity": Decimal(0), "unit": "", "revision": "", "refs": [], "blocks": []})
        row["quantity"] += q
        for k, tags in (("description", DESCRIPTION_TAGS), ("unit", UNIT_TAGS), ("revision", REVISION_TAGS)):
            v = _field(values, tags)
            if v and not row[k]:
                row[k] = v
            elif v and row[k] and v != row[k]:
                problems.append(Problem(number, f"its {k} is {row[k]!r} in one place and {v!r} in another"))
        ref = _field(values, REFERENCE_TAGS)
        if ref:
            row["refs"].append(ref)
        if s.block not in row["blocks"]:
            row["blocks"].append(s.block)
    parts = [Part(r["number"], r["description"], r["quantity"], r["unit"] or "EA", r["revision"], tuple(r["refs"]), tuple(r["blocks"]))
             for _, r in sorted(found.items())]
    return parts, problems


def _plain(q: Decimal) -> str:
    """A quantity as RevisionWorx reads one: plain digits, no exponent, no trailing zeros."""
    text = format(q.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _cell(text: str) -> str:
    """A spreadsheet would run text starting with = + - @ as a formula: a quote in front keeps it text. The same rule as RevisionWorx's,
    which takes the quote off again when it reads the file."""
    return "'" + text if text.lstrip("'")[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def bom_csv(parts: list[Part], number: str, description: str = "", revision: str = "") -> bytes:
    """The parts list as a RevisionWorx BOM: the drawing's number with its description, an item row for each part, then a line for each
    under the drawing, numbered 10, 20, 30."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\r\n")
    w.writerow(COLUMNS)
    w.writerow(["", "", _cell(number), _cell(revision), "", "", "", _cell(description), "EA", ""])
    for p in parts:
        w.writerow(["", "", _cell(p.number), _cell(p.revision), "", "", "", _cell(p.description), _cell(p.unit), ""])
    for i, p in enumerate(parts, 1):
        w.writerow([_cell(number), str(i * 10), _cell(p.number), _cell(p.revision), _plain(p.quantity), _cell(p.unit), _cell(" ".join(p.references)), "", "", ""])
    return out.getvalue().encode("utf-8")
