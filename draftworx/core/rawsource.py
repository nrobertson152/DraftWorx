# SPDX-License-Identifier: GPL-3.0-or-later
"""What a file said about something, kept word for word so it can go back out the same."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Raw:
    """`text` is the lines of the file. `shape` and `style` are what was read from them: while a thing is still those, it's unchanged and the text is
    what gets written. An entity has both. A table entry or a block has only `shape`, which is its value without its source and, for a block, without its contents.

    Empty text means the file didn't mention it: we filled it in, such as a layer an object is on that its table lacks. If it stays as it is, nothing is written."""
    text: str
    shape: object = None
    style: object = None
