# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading what's typed, and showing lengths and angles.

Numbers can be sums (`100/3`, `2*(3+4)`) and can carry units (`25mm`, `1.5in`, `3'4-1/2"`). A bare number is in drawing
units. This is a small parser of its own: nothing typed is ever evaluated as Python.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .vec import Vec, polar

_IN_MM = {"mm": 1.0, "cm": 10.0, "dm": 100.0, "m": 1000.0, "km": 1e6, "um": 1e-3, "in": 25.4, "inch": 25.4, "inches": 25.4,
          "ft": 304.8, "foot": 304.8, "feet": 304.8, "yd": 914.4, "mi": 1609344.0}
UNIT_NAMES = {"mm": "millimetres", "cm": "centimetres", "m": "metres", "in": "inches", "ft": "feet", "none": "units"}


class TypedError(ValueError):
    """What was typed can't be understood. The message says what's wrong, in a few words."""


def unit_factor(unit: str, drawing_unit: str) -> float:
    """How many drawing units one `unit` is. A drawing with no units takes a typed unit at millimetre scale."""
    return _IN_MM[unit] / _IN_MM.get(drawing_unit, 1.0)


_NUMBER = re.compile(r"\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?")
_WORD = re.compile(r"[A-Za-zµ]+")
_FEET = re.compile(r"(\d+(?:\.\d*)?)'[\s-]*(?:(\d+(?:\.\d*)?)?[\s-]*(?:(\d+)/(\d+))?\"?)?")
_INCHES = re.compile(r"(?:(\d+(?:\.\d*)?)[\s-]+)?(?:(\d+)/(\d+))\"|(\d+(?:\.\d*)?)\"")


class _Parser:
    def __init__(self, text: str, drawing_unit: str, angles: bool):
        self.s = text
        self.i = 0
        self.unit = drawing_unit
        self.angles = angles

    def error(self) -> TypedError:
        return TypedError(f"can't read {self.s!r} as a number")

    def space(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t":
            self.i += 1

    def peek(self) -> str:
        self.space()
        return self.s[self.i] if self.i < len(self.s) else ""

    def expression(self) -> float:
        v = self.term()
        while self.peek() in ("+", "-"):
            op = self.s[self.i]
            self.i += 1
            w = self.term()
            v = v + w if op == "+" else v - w
        return v

    def term(self) -> float:
        v = self.factor()
        while self.peek() in ("*", "/"):
            op = self.s[self.i]
            self.i += 1
            w = self.factor()
            if op == "*":
                v *= w
            else:
                if w == 0.0:
                    raise TypedError("can't divide by zero")
                v /= w
        return v

    def factor(self) -> float:
        c = self.peek()
        if c == "+":
            self.i += 1
            return self.factor()
        if c == "-":
            self.i += 1
            return -self.factor()
        if c == "(":
            self.i += 1
            v = self.expression()
            if self.peek() != ")":
                raise TypedError("a bracket isn't closed")
            self.i += 1
            return v
        return self.primary()

    def primary(self) -> float:
        self.space()
        if not self.angles:
            v = self._imperial()
            if v is not None:
                return v
        m = _NUMBER.match(self.s, self.i)
        if not m:
            raise self.error()
        self.i = m.end()
        v = float(m.group())
        return self._suffixed(v)

    def _imperial(self) -> float | None:
        k = unit_factor("in", self.unit)
        m = _FEET.match(self.s, self.i)
        if m:
            self.i = m.end()
            inches = float(m.group(1)) * 12.0
            if m.group(2):
                inches += float(m.group(2))
            if m.group(3):
                if float(m.group(4)) == 0.0:
                    raise TypedError("can't divide by zero")
                inches += float(m.group(3)) / float(m.group(4))
            return inches * k
        m = _INCHES.match(self.s, self.i)
        if m:
            self.i = m.end()
            if m.group(4):
                return float(m.group(4)) * k
            if float(m.group(3)) == 0.0:
                raise TypedError("can't divide by zero")
            whole = float(m.group(1)) if m.group(1) else 0.0
            return (whole + float(m.group(2)) / float(m.group(3))) * k
        return None

    def _suffixed(self, v: float) -> float:
        self.space()
        if self.i < len(self.s) and self.s[self.i] == "°":
            self.i += 1
            return v
        m = _WORD.match(self.s, self.i)
        if not m:
            return v
        word = m.group().lower()
        if self.angles:
            if word in ("d", "deg", "degrees"):
                self.i = m.end()
                return v
            if word in ("r", "rad", "radians"):
                self.i = m.end()
                return math.degrees(v)
            if word in ("g", "grad", "gon"):
                self.i = m.end()
                return v * 0.9
            raise self.error()
        if word == "µm":
            word = "um"
        if word not in _IN_MM:
            raise TypedError(f"don't know the unit {m.group()!r}")
        self.i = m.end()
        return v * unit_factor(word, self.unit)


def _evaluate(p: _Parser) -> float:
    try:
        v = p.expression()
    except RecursionError as exc:
        raise TypedError("too many brackets") from exc
    if p.peek():
        raise p.error()
    if not math.isfinite(v):
        raise TypedError("that number is too large")
    return v


def parse_length(text: str, drawing_unit: str = "mm") -> float:
    """A length in drawing units."""
    return _evaluate(_Parser(text, drawing_unit, False))


def parse_number(text: str) -> float:
    """A plain number, like a scale factor. Sums are fine, units aren't."""
    return _evaluate(_Parser(text, "mm", True))


def parse_angle(text: str) -> float:
    """An angle in radians. Typed in degrees unless it says otherwise (`1.5r`, `100g`)."""
    return math.radians(parse_number(text))


@dataclass(frozen=True, slots=True)
class TypedPoint:
    """What was typed at a point prompt: exactly one of a place, a length along the current direction, or an angle to hold."""
    point: Vec | None = None
    length: float | None = None
    angle: float | None = None


def _split_top(text: str, sep: str) -> list[str]:
    """Split at a separator outside brackets."""
    depth = 0
    parts, cur = [], []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def parse_point(text: str, last: Vec | None, drawing_unit: str = "mm") -> TypedPoint:
    """`100` length along the current direction. `<30` hold an angle. `10,20` and `50<30` are from the last point if there is one.
    `@` always means from the last point, `#` always means from the origin."""
    s = text.strip()
    if not s:
        raise TypedError("nothing typed")
    absolute = relative = False
    if s[0] == "#":
        absolute = True
        s = s[1:].strip()
    elif s[0] == "@":
        relative = True
        s = s[1:].strip()
    if relative and last is None:
        raise TypedError("there's no last point to measure from")
    origin = Vec(0.0, 0.0) if absolute or last is None else last
    if s.startswith("<"):
        return TypedPoint(angle=parse_angle(s[1:]))
    if not s:
        raise TypedError("nothing after the sign")
    commas = _split_top(s, ",")
    if len(commas) == 2:
        x = parse_length(commas[0], drawing_unit)
        y = parse_length(commas[1], drawing_unit)
        return TypedPoint(point=Vec(origin[0] + x, origin[1] + y))
    if len(commas) > 2:
        raise TypedError("a point has two numbers, x,y")
    polar_parts = _split_top(s, "<")
    if len(polar_parts) == 2:
        return TypedPoint(point=polar(origin, parse_length(polar_parts[0], drawing_unit), parse_angle(polar_parts[1])))
    if absolute or relative:
        raise TypedError("that needs x,y or length<angle")
    return TypedPoint(length=parse_length(s, drawing_unit))


# --------------------------------------------------------------------------------------------- showing numbers

def format_length(value: float, fmt: str = "decimal", decimals: int = 2, drawing_unit: str = "mm") -> str:
    v = value
    if fmt == "scientific":
        return f"{v:.{decimals}e}"
    if fmt in ("architectural", "fractional", "engineering"):
        inches = v * unit_factor(drawing_unit, "in") if drawing_unit in _IN_MM else v
        sign = "-" if inches < 0 else ""
        inches = abs(inches)
        if fmt == "engineering":
            feet = int(inches // 12)
            rest = inches - feet * 12
            shown = f"{rest:.{decimals}f}"
            if float(shown) >= 12.0:
                feet += 1
                shown = f"{0.0:.{decimals}f}"
            return f"{sign}{feet}'-{shown}\""
        denominator = 2 ** max(0, min(decimals, 8))
        total = round(inches * denominator)
        whole_in, numerator = divmod(total, denominator)
        g = math.gcd(numerator, denominator) if numerator else 1
        tail = f"{numerator // g}/{denominator // g}" if numerator else ""
        if fmt == "fractional":
            return sign + (f"{whole_in} {tail}".strip() if whole_in or not tail else tail) + '"'
        feet, rest = divmod(whole_in, 12)
        body = f"{rest}" + (f" {tail}" if tail else "") if rest or not tail else tail
        return f"{sign}{feet}'-{body}\""
    text = f"{v:.{decimals}f}"
    return "0" + text[2:] if text.startswith("-0") and float(text) == 0.0 and decimals == 0 else (text[1:] if float(text) == 0.0 and text.startswith("-") else text)


def format_angle(radians: float, decimals: int = 1) -> str:
    """Degrees, without trailing noise."""
    d = math.degrees(radians) % 360.0
    text = f"{d:.{decimals}f}"
    if float(text) >= 360.0:
        text = f"{0.0:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text + "°"
