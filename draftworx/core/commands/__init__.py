# SPDX-License-Identifier: GPL-3.0-or-later
"""Everything a person can ask for, by name. The command search, the typed command line and the menus all read this one list."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True, slots=True)
class CommandInfo:
    name: str
    function: Callable
    aliases: tuple[str, ...]
    group: str
    help: str
    icon: str = ""


COMMANDS: dict[str, CommandInfo] = {}
_LOOKUP: dict[str, CommandInfo] = {}


def command(name: str, *aliases: str, group: str, help: str, icon: str = ""):
    def register(fn: Callable) -> Callable:
        info = CommandInfo(name, fn, aliases, group, help, icon)
        if name in COMMANDS:
            raise ValueError(f"two commands called {name}")
        COMMANDS[name] = info
        for key in (name, *aliases):
            k = key.casefold()
            if k in _LOOKUP:
                raise ValueError(f"{key} is already {_LOOKUP[k].name}")
            _LOOKUP[k] = info
        return fn
    return register


def find(text: str) -> CommandInfo | None:
    """An exact name or alias."""
    return _LOOKUP.get(text.strip().casefold())


def _score(info: CommandInfo, q: str) -> int | None:
    """How well a query matches, lower is better, None if it doesn't. Names first, then words of the description."""
    name = info.name.casefold()
    if name == q:
        return 0
    for a in info.aliases:
        if a.casefold() == q:
            return 1
    if name.startswith(q):
        return 2
    for a in info.aliases:
        if a.casefold().startswith(q):
            return 3
    if q in name:
        return 4
    words = (info.help + " " + info.group).casefold()
    if any(w.startswith(q) for w in words.split()):
        return 5
    # letters in order, so "rct" finds RECTANGLE
    if len(q) >= 2:
        it = iter(name)
        if all(ch in it for ch in q):
            return 6
    return None


def search(text: str, limit: int = 12) -> list[CommandInfo]:
    """Best first. Ties go alphabetically, so the list doesn't shuffle as more is typed."""
    q = text.strip().casefold()
    if not q:
        return []
    scored = [(s, info.name, info) for info in COMMANDS.values() if (s := _score(info, q)) is not None]
    scored.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in scored[:limit]]


from . import draw, edit, modify  # noqa: E402  importing them registers their commands

__all__ = ["COMMANDS", "CommandInfo", "command", "draw", "edit", "find", "modify", "search"]
