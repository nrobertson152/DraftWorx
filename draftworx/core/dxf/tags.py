# SPDX-License-Identifier: GPL-3.0-or-later
"""DXF at its lowest level: pairs of a group code and a value, in ASCII or binary, and the strings inside them."""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass

from ..native import FormatError

BINARY_SENTINEL = b"AutoCAD Binary DXF\r\n\x1a\x00"
_CODEPAGES = {"ANSI_874": "cp874", "ANSI_932": "cp932", "DOS932": "cp932", "ANSI_936": "gbk", "ANSI_949": "cp949", "ANSI_950": "cp950", "ANSI_1250": "cp1250", "ANSI_1251": "cp1251",
              "ANSI_1252": "cp1252", "ANSI_1253": "cp1253", "ANSI_1254": "cp1254", "ANSI_1255": "cp1255", "ANSI_1256": "cp1256", "ANSI_1257": "cp1257", "ANSI_1258": "cp1258"}
_HEADER_VALUE = re.compile(rb"\$(ACADVER|DWGCODEPAGE)[ \t]*\r?\n[ \t]*\d+[ \t]*\r?\n[ \t]*([^\r\n]*)")
_UNICODE_ESCAPE = re.compile(r"(?<!\\)\\U\+([0-9A-Fa-f]{4})")
_ESCAPED_BYTES = re.compile("[\udc80-\udcff]+")
_CARET = re.compile(r"\^([ @-_])")
_PERCENT = {"d": "°", "p": "±", "c": "Ø", "%": "%"}


def codec_for(codepage: str) -> str:
    return _CODEPAGES.get(codepage.strip().upper(), "cp1252")


@dataclass(slots=True)
class Decoded:
    """A file as lines of text, and what's needed to write it back the same."""
    lines: list[str]
    version: str
    codepage: str
    newline: str = "\r\n"
    ended: bool = True      # does the last line end with a newline?
    bom: bool = False
    binary: bool = False


def decode(data: bytes) -> Decoded:
    """Bytes that don't fit the file's encoding are held as escapes, so a stray byte comes back out unchanged."""
    if data.startswith(BINARY_SENTINEL):
        return _binary(data)
    bom = data.startswith(b"\xef\xbb\xbf")
    if bom:
        data = data[3:]
    found = {m.group(1).decode("ascii"): m.group(2).decode("ascii", "replace").strip() for m in _HEADER_VALUE.finditer(data[:100_000])}
    version, codepage = found.get("ACADVER", "AC1009"), found.get("DWGCODEPAGE", "ANSI_1252")
    encoding = "utf-8" if version >= "AC1021" else codec_for(codepage)
    text = data.decode(encoding, "surrogateescape")
    first = text[:4096]
    newline = "\r\n" if "\r\n" in first else ("\r" if "\r" in first and "\n" not in first else "\n")
    if "\r" in text:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    ended = lines[-1] == ""
    if ended:
        lines.pop()
    return Decoded(lines, version, codepage, newline, ended, bom)


def group_type(code: int) -> str:
    """How a group code's value is stored in a binary file."""
    if 310 <= code < 320 or code == 1004:
        return "b"
    if code < 10 or 100 <= code <= 105 or 300 <= code < 370 or 390 <= code < 400 or 410 <= code < 420 or 430 <= code < 440 or 470 <= code < 482 or 999 <= code < 1010:
        return "s"
    if 10 <= code < 60 or 110 <= code < 150 or 210 <= code < 240 or 460 <= code < 470 or 1010 <= code < 1060:
        return "d"
    if 60 <= code < 80 or 170 <= code < 180 or 270 <= code < 290 or 370 <= code < 390 or 400 <= code < 410 or 1060 <= code <= 1070:
        return "h"
    if 90 <= code < 100 or 420 <= code < 430 or 440 <= code < 460 or code == 1071:
        return "l"
    if 160 <= code < 170:
        return "q"
    if 290 <= code < 300:
        return "?"
    return "s"


def _binary(data: bytes) -> Decoded:
    """A binary file as the same pairs of lines an ASCII one has, so nothing after this needs to care. Saving writes ASCII."""
    pos = len(BINARY_SENTINEL)
    n = len(data)
    if n < pos + 2:
        raise FormatError("This binary DXF file is empty.")
    # R13 and later use two bytes for a group code, older files one. The first code is always 0, then SECTION.
    two_byte = data[pos] == 0 and data[pos + 1] == 0
    lines: list[str] = []
    version = ""
    codepage = "ANSI_1252"
    pending = None
    unpack = struct.unpack_from
    try:
        while pos < n:
            if two_byte:
                code = unpack("<H", data, pos)[0]
                pos += 2
            else:
                code = data[pos]
                pos += 1
                if code == 255:
                    code = unpack("<H", data, pos)[0]
                    pos += 2
            kind = group_type(code)
            if kind == "s":
                end = data.find(b"\0", pos)
                if end < 0:
                    raise FormatError("This binary DXF file is cut off.")
                value = data[pos:end].decode("utf-8" if version >= "AC1021" else codec_for(codepage), "surrogateescape")
                pos = end + 1
                if pending == "$ACADVER" and code == 1:
                    version = value.strip()
                elif pending == "$DWGCODEPAGE" and code == 3:
                    codepage = value.strip()
                pending = value if code == 9 else None
            elif kind == "d":
                value = repr(unpack("<d", data, pos)[0])
                pos += 8
            elif kind == "h":
                value = str(unpack("<h", data, pos)[0])
                pos += 2
            elif kind == "l":
                value = str(unpack("<l", data, pos)[0])
                pos += 4
            elif kind == "q":
                value = str(unpack("<q", data, pos)[0])
                pos += 8
            elif kind == "?":
                value = str(data[pos])
                pos += 1
            else:
                size = data[pos]
                value = data[pos + 1:pos + 1 + size].hex().upper()
                pos += 1 + size
            lines.append(str(code))
            lines.append(value)
    except (struct.error, IndexError) as exc:
        raise FormatError("This binary DXF file is cut off or damaged.") from exc
    return Decoded(lines, version or "AC1009", codepage, binary=True)


def pairs(lines: list[str], start: int, end: int) -> list[tuple[int, str]]:
    """The tags of a stretch of lines."""
    stop = start + (end - start) // 2 * 2
    try:
        return list(zip(map(int, lines[start:stop:2]), lines[start + 1:stop:2]))
    except ValueError:
        for i in range(start, stop, 2):
            try:
                int(lines[i])
            except ValueError as exc:
                raise FormatError(f"This DXF file is damaged at line {i + 1}: {lines[i].strip()[:40]!r} should be a group code.") from exc
        raise


def fnum(value: str, default: float = 0.0) -> float:
    """A float, or the default for nonsense. NaN and infinity are nonsense in a drawing, and they mustn't get in."""
    try:
        v = float(value)
    except ValueError:
        return default
    return v if v == v and abs(v) != float("inf") else default


def inum(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except ValueError:
        try:
            return int(float(value))
        except (ValueError, OverflowError):
            return default


def clean(value: str) -> str:
    """A string fit to show. A byte that didn't fit the file's encoding is read as Windows Latin 1, which is usually what it was."""
    if value.isascii():
        return value
    return _ESCAPED_BYTES.sub(lambda m: m.group().encode("utf-8", "surrogateescape").decode("cp1252", "replace"), value)


def _percent_codes(s: str) -> str:
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        if s[i] == "%" and i + 2 < n and s[i + 1] == "%":
            c = s[i + 2].lower()
            if c in _PERCENT:
                out.append(_PERCENT[c])
                i += 3
                continue
            if c in "uo":       # underline and overline switches: not shown, so not part of what it says
                i += 3
                continue
            if c.isdigit() and s[i + 2:i + 5].isdigit():
                code = int(s[i + 2:i + 5])
                out.append(chr(code) if code >= 32 else " ")
                i += 5
                continue
        out.append(s[i])
        i += 1
    return "".join(out)


def _caret(m: re.Match) -> str:
    c = m.group(1)
    if c == " ":
        return "^"
    return "\n" if c == "J" else " "    # a control character: a line break, or nothing that can be shown


def decode_text(value: str) -> str:
    """What a string says, without DXF's escapes."""
    value = clean(value)
    if "\\U+" in value:
        value = _UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), value)
    if "^" in value:
        value = _CARET.sub(_caret, value)
    if "%%" in value:
        value = _percent_codes(value)
    return value


def encode_text(value: str, version: str, encoding: str) -> str:
    """A string as this file version can hold it. Older versions spell what their code page lacks as \\U+XXXX."""
    if "^" in value:
        value = value.replace("^", "^ ")
    if not value.isprintable():
        value = "".join(" " if ch in "\r\n" else (f"^{chr(ord(ch) + 64)}" if ord(ch) < 32 else ch) for ch in value)
    if value.isascii() or version >= "AC1021":
        return value
    out = []
    for ch in value:
        try:
            ch.encode(encoding)
            out.append(ch)
        except UnicodeEncodeError:
            out.append(f"\\U+{ord(ch):04X}" if ord(ch) <= 0xffff else "?")
    return "".join(out)


def fmt_float(x: float) -> str:
    """As short as reads back exactly, and always in a form every DXF reader accepts."""
    s = repr(float(x))
    if "e" in s:
        m, e = s.split("e")
        if "." not in m:
            m += ".0"
        return f"{m}E{e}"
    return s


def _fmt(value) -> str:
    return fmt_float(value) if isinstance(value, float) else str(value)


def code_line(code: int) -> str:
    return f"{code:3d}" if code < 1000 else str(code)


def _retag_one(lines: list[str], changes: dict, common_only: bool) -> list[str]:
    """One object with some group codes changed. None removes a code. A code that wasn't there goes in after the layer, or at the end."""
    end = len(lines) - len(lines) % 2
    seen_common = False
    for i in range(2, end, 2):
        c = lines[i].strip()
        if c == "1001" or c == "101":
            end = i
            break
        if common_only and c == "100":
            if not seen_common and lines[i + 1].strip() == "AcDbEntity":
                seen_common = True
                continue
            end = i
            break
    left = dict(changes)
    out = lines[:2]
    after_layer = None
    for i in range(2, end, 2):
        try:
            code = int(lines[i])
        except ValueError:
            code = -1
        if code in left:
            value = left.pop(code)
            if value is None:
                continue
            out += [lines[i], _fmt(value)]
        else:
            out += lines[i:i + 2]
        if code == 8:
            after_layer = len(out)
    extra: list[str] = []
    for code in sorted(left):
        if left[code] is not None:
            extra += [code_line(code), _fmt(left[code])]
    where = after_layer if after_layer is not None else len(out)
    return out[:where] + extra + out[where:] + lines[end:]


def retag(text: str, changes: dict, common_only: bool = True) -> str:
    """Change group codes in the text of an object, and of those it owns, leaving every other line as it is.

    `common_only` limits it to the part every object has, before its own subclass, where 8 always means a layer and 62 a colour."""
    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        j = i + 2
        while j + 1 < n and lines[j].strip() != "0":
            j += 2
        if j + 1 >= n:
            j = n
        out += _retag_one(lines[i:j], changes, common_only)
        i = j
    return "\n".join(out)


def header_state(doc) -> dict:
    """What the header says about the drawing. What's the same as when it was read is left as the file had it."""
    st, c = doc.settings, doc.current
    box = doc.extents(visible_only=False)
    return {"units": st.units, "linear_format": st.linear_format, "linear_decimals": st.linear_decimals, "angle_decimals": st.angle_decimals,
            "annotation_scale": st.annotation_scale, "ltscale": st.ltscale, "point_mode": st.point_mode, "point_size": st.point_size,
            "layer": c.layer, "color": c.color, "linetype": c.linetype, "weight": c.weight, "text_style": c.text_style, "dim_style": c.dim_style,
            "extents": list(box) if box is not None else None}
