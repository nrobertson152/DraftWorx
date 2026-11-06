# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the view is looking: drawing coordinates to window pixels and back. The drawing's y goes up, the window's goes down."""
from __future__ import annotations

from dataclasses import dataclass

from .vec import BBox, Vec

MIN_SCALE = 1e-9
MAX_SCALE = 1e9


@dataclass
class View:
    cx: float = 0.0         # the drawing point at the middle of the window
    cy: float = 0.0
    scale: float = 1.0      # pixels per drawing unit
    width: int = 800
    height: int = 600

    @property
    def px(self) -> float:
        """Drawing units per pixel."""
        return 1.0 / self.scale

    def to_screen(self, x: float, y: float) -> tuple[float, float]:
        return (x - self.cx) * self.scale + self.width * 0.5, (self.cy - y) * self.scale + self.height * 0.5

    def to_world(self, sx: float, sy: float) -> Vec:
        return Vec(self.cx + (sx - self.width * 0.5) / self.scale, self.cy - (sy - self.height * 0.5) / self.scale)

    def matrix(self) -> tuple[float, float, float, float, float, float]:
        """(m11, m12, m21, m22, dx, dy) from drawing to window."""
        s = self.scale
        return (s, 0.0, 0.0, -s, self.width * 0.5 - self.cx * s, self.height * 0.5 + self.cy * s)

    def visible(self, margin_px: float = 0.0) -> BBox:
        (x0, y1), (x1, y0) = self.to_world(-margin_px, -margin_px), self.to_world(self.width + margin_px, self.height + margin_px)
        return (x0, y0, x1, y1)

    def zoom_at(self, sx: float, sy: float, factor: float) -> None:
        """Zoom keeping the drawing point under this pixel where it is."""
        before = self.to_world(sx, sy)
        self.scale = min(MAX_SCALE, max(MIN_SCALE, self.scale * factor))
        after = self.to_world(sx, sy)
        self.cx += before[0] - after[0]
        self.cy += before[1] - after[1]

    def pan(self, dx_px: float, dy_px: float) -> None:
        self.cx -= dx_px / self.scale
        self.cy += dy_px / self.scale

    def fit(self, box: BBox | None, margin: float = 0.06) -> None:
        """Show this box. With nothing to show, a sensible empty sheet."""
        if box is None:
            box = (0.0, 0.0, 297.0, 210.0)
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        if w <= 0.0 and h <= 0.0:
            w = h = max(abs(x0), abs(y0), 1.0) * 0.1 or 1.0
        self.cx, self.cy = (x0 + x1) * 0.5, (y0 + y1) * 0.5
        sx = self.width / w if w > 0.0 else float("inf")
        sy = self.height / h if h > 0.0 else float("inf")
        self.scale = min(MAX_SCALE, max(MIN_SCALE, min(sx, sy) * (1.0 - margin * 2.0)))
