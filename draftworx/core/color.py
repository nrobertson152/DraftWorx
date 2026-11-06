# SPDX-License-Identifier: GPL-3.0-or-later
"""Colours as DXF stores them, kept exactly: an index, ByLayer, ByBlock, or a true colour.

A colour is one integer, so a style stays small and hashable, and a file's colour comes back out as it went in.
"""
from __future__ import annotations

BYBLOCK = 0
BYLAYER = 256
TRUE = 0x1000000
_VALUES = (255.0, 165.0, 127.0, 76.0, 38.0)
_NAMED = ("ff0000", "ffff00", "00ff00", "00ffff", "0000ff", "ff00ff", "ffffff", "808080", "c0c0c0")
_GREYS = ("333333", "505050", "696969", "828282", "bebebe", "ffffff")


def _build() -> list[int]:
    """Indexes 10 to 249 are a regular wheel: 24 hues, 5 brightnesses, each full and half saturation."""
    table = [0] * 256
    for i, h in enumerate(_NAMED, 1):
        table[i] = int(h, 16)
    for i, h in enumerate(_GREYS, 250):
        table[i] = int(h, 16)
    for i in range(10, 250):
        hue = (i // 10 - 1) * 15
        v = _VALUES[(i % 10) // 2]
        low = v * 0.5 if i % 2 else 0.0
        sector, frac = hue // 60, (hue % 60) / 60.0
        rise = low + (v - low) * frac
        fall = low + (v - low) * (1.0 - frac)
        r, g, b = ((v, rise, low), (fall, v, low), (low, v, rise), (low, fall, v), (rise, low, v), (v, low, fall))[sector]
        table[i] = (int(r) << 16) | (int(g) << 8) | int(b)
    return table


ACI = tuple(_build())


def true_color(r: int, g: int, b: int) -> int:
    return TRUE | ((r & 255) << 16) | ((g & 255) << 8) | (b & 255)


def is_true(color: int) -> bool:
    return color >= TRUE


def is_index(color: int) -> bool:
    return 1 <= color <= 255


def rgb_of(color: int, inherited: int = 0xffffff) -> int:
    """0xRRGGBB for showing a colour. ByLayer and ByBlock show as what they inherit."""
    if color >= TRUE:
        return color & 0xffffff
    if 1 <= color <= 255:
        return ACI[color]
    return inherited


def nearest_index(rgb: int) -> int:
    """The closest of the 255 indexes: DXF files carry it beside a true colour, for readers that only know indexes."""
    r, g, b = (rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255
    best, best_d = 7, 1 << 30
    for i in range(1, 255):
        v = ACI[i]
        d = (((v >> 16) & 255) - r) ** 2 + (((v >> 8) & 255) - g) ** 2 + ((v & 255) - b) ** 2
        if d < best_d:
            best, best_d = i, d
    return best


def valid(color) -> bool:
    return isinstance(color, int) and not isinstance(color, bool) and (0 <= color <= 256 or TRUE <= color <= TRUE | 0xffffff)


def describe(color: int) -> str:
    if color == BYLAYER:
        return "ByLayer"
    if color == BYBLOCK:
        return "ByBlock"
    if color >= TRUE:
        return f"#{color & 0xffffff:06x}"
    return {1: "Red", 2: "Yellow", 3: "Green", 4: "Cyan", 5: "Blue", 6: "Magenta", 7: "White"}.get(color, f"Color {color}")
