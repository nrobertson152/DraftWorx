# SPDX-License-Identifier: GPL-3.0-or-later
"""The drawing area. It forwards events to the session and paints what the session says.

A cached image holds the drawing, so a mouse move only paints the overlay on top of it: the snap marker, what's being drawn, alignment lines.
"""
from __future__ import annotations

import math
import time

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QPainter, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import QWidget

from ..core.commands import search
from ..core.session import GetPick, GetPoint, Session
from ..core.snap import REAL, Guide, Kind
from ..core.units import format_length
from ..core.view import View
from .scene import Scene

_KEYS = {Qt.Key.Key_Return: "enter", Qt.Key.Key_Enter: "enter", Qt.Key.Key_Escape: "escape", Qt.Key.Key_Space: "space",
         Qt.Key.Key_Backspace: "backspace", Qt.Key.Key_Delete: "delete", Qt.Key.Key_F3: "f3", Qt.Key.Key_F8: "f8", Qt.Key.Key_F9: "f9",
         Qt.Key.Key_F10: "f10"}
MARK = 6.0


class Canvas(QWidget):
    #: Something the rest of the window may want to show has changed.
    state_changed = Signal()
    #: Right-click with nothing running: the window shows a menu at this position.
    context_menu = Signal(object)

    def __init__(self, session: Session, theme, config, parent=None):
        super().__init__(parent)
        self.session = session
        self.theme = theme
        self.config = config
        self.view = View()
        self.scene = Scene(session.doc, theme)
        self._image: QImage | None = None
        self._stale = True
        self._seen_selection = session.selection_version
        self._pan_from: QPointF | None = None
        self._inside = False
        self._fitted = False
        self._stop = session.doc.on_change(self._drawing_changed)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(QSize(200, 150))
        self._font = QFont(theme.family)
        self._font.setPixelSize(12)
        self._metrics = QFontMetricsF(self._font)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # ----------------------------------------------------------------------------------------------- the drawing

    def set_document(self, doc) -> None:
        self._stop()
        self.session.replace_document(doc)
        self._stop = doc.on_change(self._drawing_changed)
        self.scene = Scene(doc, self.theme)
        self.fit()

    def _drawing_changed(self, changes) -> None:
        self.scene.apply(changes)
        self._stale = True
        self.update()

    def set_theme(self, theme) -> None:
        self.theme = theme
        self.scene.set_theme(theme)
        self._font = QFont(theme.family)
        self._font.setPixelSize(12)
        self._metrics = QFontMetricsF(self._font)
        self.redraw()

    def redraw(self) -> None:
        self._stale = True
        self.update()

    # ------------------------------------------------------------------------------------------------- the view

    def fit(self) -> None:
        self.view.width, self.view.height = max(self.width(), 1), max(self.height(), 1)
        self.view.fit(self.session.doc.extents())
        self._fitted = True
        self.redraw()
        self._forward()

    def zoom(self, factor: float, at: QPointF | None = None) -> None:
        c = at if at is not None else QPointF(self.width() / 2.0, self.height() / 2.0)
        self.view.zoom_at(c.x(), c.y(), factor)
        self.redraw()
        self._forward()

    def resizeEvent(self, ev) -> None:
        self.view.width, self.view.height = max(self.width(), 1), max(self.height(), 1)
        if not self._fitted and self.width() > 1:
            self.view.fit(self.session.doc.extents())
            self._fitted = True
        self._stale = True
        super().resizeEvent(ev)

    def _forward(self) -> None:
        """The view changed under a still pointer, so what it's pointing at did too."""
        pos = self.mapFromGlobal(self.cursor().pos())
        w = self.view.to_world(pos.x(), pos.y())
        s = self.session
        s.move(w[0], w[1], self.view.px, time.monotonic(), s.shift, s.ctrl)
        self.state_changed.emit()

    # ------------------------------------------------------------------------------------------------ the pointer

    def _send_move(self, ev) -> None:
        pos = ev.position()
        m = ev.modifiers()
        w = self.view.to_world(pos.x(), pos.y())
        self.session.move(w[0], w[1], self.view.px, time.monotonic(), bool(m & Qt.KeyboardModifier.ShiftModifier),
                          bool(m & Qt.KeyboardModifier.ControlModifier))

    def _after_event(self) -> None:
        s = self.session
        if s.selection_version != self._seen_selection:
            self._seen_selection = s.selection_version
            self._stale = True
        self.update()
        self.state_changed.emit()

    def mouseMoveEvent(self, ev) -> None:
        self._inside = True
        if self._pan_from is not None:
            d = ev.position() - self._pan_from
            self._pan_from = ev.position()
            self.view.pan(d.x(), d.y())
            self._stale = True
        self._send_move(ev)
        self._after_event()

    def mousePressEvent(self, ev) -> None:
        self.setFocus()
        pos = ev.position()
        m = ev.modifiers()
        shift = bool(m & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(m & Qt.KeyboardModifier.ControlModifier)
        w = self.view.to_world(pos.x(), pos.y())
        if ev.button() == Qt.MouseButton.LeftButton:
            self.session.press(w[0], w[1], self.view.px, time.monotonic(), shift, ctrl)
        elif ev.button() == Qt.MouseButton.RightButton:
            if not self.session.right_click():
                self.context_menu.emit(ev.globalPosition().toPoint())
        elif ev.button() == Qt.MouseButton.MiddleButton:
            self._pan_from = pos
        self._after_event()

    def mouseReleaseEvent(self, ev) -> None:
        pos = ev.position()
        m = ev.modifiers()
        w = self.view.to_world(pos.x(), pos.y())
        if ev.button() == Qt.MouseButton.LeftButton:
            self.session.release(w[0], w[1], self.view.px, time.monotonic(), bool(m & Qt.KeyboardModifier.ShiftModifier),
                                 bool(m & Qt.KeyboardModifier.ControlModifier))
        elif ev.button() == Qt.MouseButton.MiddleButton:
            self._pan_from = None
        self._after_event()

    def mouseDoubleClickEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.MiddleButton:
            self.fit()
        else:
            self.mousePressEvent(ev)

    def wheelEvent(self, ev) -> None:
        d = ev.angleDelta().y() or ev.angleDelta().x()
        m = ev.modifiers()
        if m & Qt.KeyboardModifier.ShiftModifier:
            self.view.pan(d / 2.0, 0.0)
        elif m & Qt.KeyboardModifier.ControlModifier:
            self.view.pan(0.0, d / 2.0)
        else:
            self.view.zoom_at(ev.position().x(), ev.position().y(), 1.2 ** (d / 120.0))
        self._stale = True
        self._send_move(ev)
        self._after_event()

    def enterEvent(self, ev) -> None:
        self._inside = True
        self.update()

    def leaveEvent(self, ev) -> None:
        self._inside = False
        self.update()

    def _tick(self) -> None:
        if self.session.tick(time.monotonic()):
            self._after_event()

    # ----------------------------------------------------------------------------------------------- the keyboard

    def event(self, ev) -> bool:
        # Tab means something here, so it mustn't move the focus to another widget
        if ev.type() == QEvent.Type.KeyPress and ev.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            self.session.key("tab", shift=ev.key() == Qt.Key.Key_Backtab)
            self._after_event()
            return True
        return super().event(ev)

    def keyPressEvent(self, ev) -> None:
        self.handle_key(ev)

    def handle_key(self, ev) -> bool:
        """Returns True if it was used. The window also sends keys here that land on buttons and lists, so typing always works."""
        key = ev.key()
        s = self.session
        if key in (Qt.Key.Key_Shift, Qt.Key.Key_Control):
            # the key itself may not be in the modifiers yet, depending on the platform
            m = ev.modifiers()
            s.move(s.cursor[0], s.cursor[1], self.view.px, time.monotonic(), key == Qt.Key.Key_Shift or bool(m & Qt.KeyboardModifier.ShiftModifier),
                   key == Qt.Key.Key_Control or bool(m & Qt.KeyboardModifier.ControlModifier))
            self._after_event()
            return True
        name = _KEYS.get(key)
        if name is not None:
            s.key(name, bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier), bool(ev.modifiers() & Qt.KeyboardModifier.ControlModifier))
            self._after_event()
            return True
        if ev.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
            return False
        text = ev.text()
        if text and text.isprintable():
            s.type_text(text)
            self._after_event()
            return True
        return False

    def keyReleaseEvent(self, ev) -> None:
        key = ev.key()
        if key in (Qt.Key.Key_Shift, Qt.Key.Key_Control):
            s = self.session
            m = ev.modifiers()
            s.move(s.cursor[0], s.cursor[1], self.view.px, time.monotonic(), key != Qt.Key.Key_Shift and bool(m & Qt.KeyboardModifier.ShiftModifier),
                   key != Qt.Key.Key_Control and bool(m & Qt.KeyboardModifier.ControlModifier))
            self._after_event()

    # ------------------------------------------------------------------------------------------------ painting

    def _render(self) -> None:
        dpr = self.devicePixelRatioF()
        w, h = max(1, round(self.width() * dpr)), max(1, round(self.height() * dpr))
        if self._image is None or self._image.width() != w or self._image.height() != h:
            self._image = QImage(w, h, QImage.Format.Format_ARGB32_Premultiplied)
            self._image.setDevicePixelRatio(dpr)
        c = self.theme.canvas
        self._image.fill(QColor(c["paper"]))
        p = QPainter(self._image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.config.show_grid:
            self._paint_grid(p)
        self.scene.paint(p, self.view, self.config.show_weights)
        self._paint_selection(p)
        p.end()
        self._stale = False

    def _paint_grid(self, p: QPainter) -> None:
        """Faint lines at a round spacing for this zoom. It orients the eye. Snapping to it is separate (F9)."""
        c = self.theme.canvas
        v = self.view
        target = 22.0 * v.px
        e = math.floor(math.log10(target)) if target > 0.0 else 0
        base = 10.0 ** e
        step = next((base * k for k in (1.0, 2.0, 5.0, 10.0) if base * k >= target), base * 10.0)
        major = step * 5.0
        x0, y0, x1, y1 = v.visible()
        if not all(map(math.isfinite, (x0, y0, x1, y1))) or (x1 - x0) / step > 400 or (y1 - y0) / step > 400:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        minor = QPen(QColor(c["grid"]))
        strong = QPen(QColor(c["grid_major"]))
        for pen, spacing, skip in ((minor, step, major), (strong, major, None)):
            p.setPen(pen)
            i = math.ceil(x0 / spacing)
            while i * spacing <= x1:
                if skip is None or abs((i * spacing) / skip - round((i * spacing) / skip)) > 1e-9:
                    sx, _ = v.to_screen(i * spacing, 0.0)
                    p.drawLine(QPointF(sx, 0.0), QPointF(sx, v.height))
                i += 1
            j = math.ceil(y0 / spacing)
            while j * spacing <= y1:
                if skip is None or abs((j * spacing) / skip - round((j * spacing) / skip)) > 1e-9:
                    _, sy = v.to_screen(0.0, j * spacing)
                    p.drawLine(QPointF(0.0, sy), QPointF(v.width, sy))
                j += 1
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    def _world_transform(self) -> QTransform:
        return QTransform(*self.view.matrix())

    def _paint_selection(self, p: QPainter) -> None:
        s = self.session
        if not s.selection:
            return
        c = self.theme.canvas
        p.setTransform(self._world_transform())
        pen = QPen(QColor(c["select"]))
        pen.setCosmetic(True)
        pen.setWidthF(2.0)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for eid in s.selection:
            path = self.scene.path_of(eid)
            if path is not None:
                p.drawPath(path)
        p.resetTransform()

    def paintEvent(self, ev) -> None:
        if self._stale or self._image is None:
            self._render()
        p = QPainter(self)
        p.drawImage(0, 0, self._image)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setFont(self._font)
        self._paint_overlay(p)
        p.end()

    def _screen(self, q) -> QPointF:
        x, y = self.view.to_screen(q[0], q[1])
        return QPointF(x, y)

    def _paint_overlay(self, p: QPainter) -> None:
        s = self.session
        c = self.theme.canvas
        if s.hover is not None and s.hover not in s.selection:
            path = self.scene.path_of(s.hover)
            if path is not None:
                p.setTransform(self._world_transform())
                pen = QPen(QColor(c["hover"]))
                pen.setCosmetic(True)
                pen.setWidthF(2.0)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)
                p.resetTransform()
        self._paint_ghosts(p)
        self._paint_grips(p)
        self._paint_tracking(p)
        self._paint_window(p)
        if self._inside:
            self._paint_cursor(p)
        self._paint_snap(p)
        self._paint_fields(p)

    def _paint_ghosts(self, p: QPainter) -> None:
        s = self.session
        if not s.ghosts and not s.doomed:
            return
        p.setTransform(self._world_transform())
        p.setBrush(Qt.BrushStyle.NoBrush)
        for shapes, token, width in ((s.doomed, "remove", 2.5), (s.ghosts, "ghost", 1.5)):
            pen = QPen(QColor(self.theme.canvas[token]))
            pen.setCosmetic(True)
            pen.setWidthF(width)
            p.setPen(pen)
            for shape in shapes:
                p.drawPath(self.scene.shape_path(shape))
        p.resetTransform()

    def _paint_grips(self, p: QPainter) -> None:
        s = self.session
        if not s.grips:
            return
        c = self.theme.canvas
        hot = s.hover_grip
        fill, line = QColor(c["select"]), QColor(c["paper"])
        for _, g in s.grips:
            q = self._screen(g.point)
            r = 5.0 if g is hot else 3.5
            p.setPen(QPen(line, 1.0))
            p.setBrush(QBrush(fill))
            if g.role in ("segment", "move"):
                p.drawPolygon(QPolygonF([q + QPointF(0, -r - 1), q + QPointF(r + 1, 0), q + QPointF(0, r + 1), q + QPointF(-r - 1, 0)]))
            else:
                p.drawRect(QRectF(q.x() - r, q.y() - r, 2 * r, 2 * r))

    def _clip_guide(self, g: Guide) -> tuple[QPointF, QPointF] | None:
        """An alignment line, across the whole window."""
        x0, y0, x1, y1 = self.view.visible(2.0)
        ts = [(cx - g.origin[0]) * g.direction[0] + (cy - g.origin[1]) * g.direction[1] for cx in (x0, x1) for cy in (y0, y1)]
        lo, hi = min(ts), max(ts)
        a = g.origin + g.direction * lo
        b = g.origin + g.direction * hi
        return self._screen(a), self._screen(b)

    def _paint_tracking(self, p: QPainter) -> None:
        s = self.session
        c = self.theme.canvas
        col = QColor(c["guide"])
        result = s.snap
        if result is not None and result.guides:
            pen = QPen(col, 1.0, Qt.PenStyle.DotLine)
            p.setPen(pen)
            for g in result.guides:
                seg = self._clip_guide(g)
                if seg is not None:
                    p.drawLine(*seg)
        p.setPen(QPen(col, 1.5))
        for t in s.tracked:
            q = self._screen(t.point)
            p.drawLine(q + QPointF(-5, 0), q + QPointF(5, 0))
            p.drawLine(q + QPointF(0, -5), q + QPointF(0, 5))

    def _paint_window(self, p: QPainter) -> None:
        win = self.session.window
        if win is None:
            return
        c = self.theme.canvas
        a, b, crossing = win
        qa, qb = self._screen(a), self._screen(b)
        col = QColor(c["band_cross"] if crossing else c["band"])
        fill = QColor(col)
        fill.setAlpha(36)
        pen = QPen(col, 1.0, Qt.PenStyle.DashLine if crossing else Qt.PenStyle.SolidLine)
        p.setPen(pen)
        p.setBrush(QBrush(fill))
        p.drawRect(QRectF(qa, qb).normalized())

    def _cursor_point(self) -> QPointF:
        s = self.session
        r = s.snap
        if r is not None and r.candidate is not None and r.candidate.kind in REAL and not r.candidate.guides:
            return self._screen(r.point)    # the magnet: the crosshair goes where a click would land
        return self._screen(s.cursor)

    def _paint_cursor(self, p: QPainter) -> None:
        q = self._cursor_point()
        col = QColor(self.theme.canvas["crosshair"])
        p.setPen(QPen(col, 1.0))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        p.drawLine(QPointF(0.0, q.y()), QPointF(self.width(), q.y()))
        p.drawLine(QPointF(q.x(), 0.0), QPointF(q.x(), self.height()))
        if not isinstance(self.session.request, (GetPoint,)):
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(q.x() - 4.0, q.y() - 4.0, 8.0, 8.0))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    def _paint_snap(self, p: QPainter) -> None:
        s = self.session
        r = s.snap
        if r is None or r.candidate is None:
            return
        c = r.candidate
        q = self._screen(r.point)
        col = QColor(self.theme.canvas["snap"])
        p.setPen(QPen(col, 2.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        _marker(p, c.kind, q)
        text = self._snap_label(c, r.point)
        self._tag(p, q + QPointF(12.0, -14.0), text)

    def _snap_label(self, c, point) -> str:
        st = self.session.doc.settings
        if c.guides and c.kind in (Kind.GUIDE, Kind.GUIDE_ON_OBJECT):
            g = c.guides[0]
            d = point.distance(g.origin)
            label = c.detail or g.label
            if g.source == "last point":
                return label
            return f"{label} from {g.source.lower()}: {format_length(d, st.linear_format, st.linear_decimals, st.units)}"
        return c.label

    def _tag(self, p: QPainter, at: QPointF, text: str, strong: bool = False) -> QRectF:
        """A small label on the drawing."""
        u = self.theme.ui
        w = self._metrics.horizontalAdvance(text) + 12.0
        h = self._metrics.height() + 6.0
        x = min(max(2.0, at.x()), self.width() - w - 2.0)
        y = min(max(2.0, at.y()), self.height() - h - 2.0)
        rect = QRectF(x, y, w, h)
        p.setPen(QPen(QColor(u["accent"]), 1.5) if strong else Qt.PenStyle.NoPen)
        p.setBrush(QBrush(QColor(u["tooltip_bg"])))
        p.drawRoundedRect(rect, 4.0, 4.0)
        p.setPen(QColor(u["tooltip_fg"]))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        return rect

    def _paint_fields(self, p: QPainter) -> None:
        s = self.session
        base = self._screen(s.cursor) + QPointF(18.0, 18.0)
        x = base.x()
        y = base.y()
        for f in s.fields:
            caret = "|" if f.typing else ""
            lock = " (held)" if f.locked else ""
            r = self._tag(p, QPointF(x, y), f"{f.text}{caret}{lock}", f.typing)
            x = r.right() + 4.0
            y = r.top()
        if s.request is None and s.input:
            self._paint_suggestions(p, base)
        elif isinstance(s.request, (GetPoint, GetPick)) and s.input and not s.fields:
            self._tag(p, base, s.input + "|", True)

    def _paint_suggestions(self, p: QPainter, at: QPointF) -> None:
        s = self.session
        lines = [f"{info.name}  {info.help}" for info in search(s.input, 6)]
        # one left edge for the whole list, even near the side of the window
        widest = max((self._metrics.horizontalAdvance(t) for t in lines), default=0.0) + 12.0
        x = min(at.x(), self.width() - widest - 2.0)
        r = self._tag(p, QPointF(x, at.y()), s.input + "|", True)
        y = r.bottom() + 3.0
        for i, text in enumerate(lines):
            r = self._tag(p, QPointF(x, y), text, i == 0)
            y = r.bottom() + 2.0


def _marker(p: QPainter, kind: Kind, q: QPointF) -> None:
    """A shape for each kind of point, so what's been found can be read at a glance. The shapes are the ones AutoCAD users already know."""
    m = MARK
    x, y = q.x(), q.y()
    if kind == Kind.ENDPOINT:
        p.drawRect(QRectF(x - m, y - m, 2 * m, 2 * m))
    elif kind == Kind.MIDPOINT:
        p.drawPolygon(QPolygonF([QPointF(x, y - m), QPointF(x + m, y + m), QPointF(x - m, y + m)]))
    elif kind == Kind.CENTER:
        p.drawEllipse(q, m, m)
    elif kind == Kind.GEOMETRIC_CENTER:
        p.drawEllipse(q, m, m)
        p.drawLine(QPointF(x - m, y), QPointF(x + m, y))
        p.drawLine(QPointF(x, y - m), QPointF(x, y + m))
    elif kind == Kind.QUADRANT:
        p.drawPolygon(QPolygonF([QPointF(x, y - m), QPointF(x + m, y), QPointF(x, y + m), QPointF(x - m, y)]))
    elif kind in (Kind.INTERSECTION, Kind.GUIDES_MEET):
        p.drawLine(QPointF(x - m, y - m), QPointF(x + m, y + m))
        p.drawLine(QPointF(x - m, y + m), QPointF(x + m, y - m))
    elif kind == Kind.GUIDE_ON_OBJECT:
        p.drawLine(QPointF(x - m, y - m), QPointF(x + m, y + m))
        p.drawLine(QPointF(x - m, y + m), QPointF(x + m, y - m))
        p.drawRect(QRectF(x - m, y - m, 2 * m, 2 * m))
    elif kind == Kind.NODE:
        p.drawEllipse(q, m, m)
        p.drawLine(QPointF(x - m * 0.7, y - m * 0.7), QPointF(x + m * 0.7, y + m * 0.7))
        p.drawLine(QPointF(x - m * 0.7, y + m * 0.7), QPointF(x + m * 0.7, y - m * 0.7))
    elif kind == Kind.INSERTION:
        p.drawRect(QRectF(x - m, y - m, m * 1.4, m * 1.4))
        p.drawRect(QRectF(x - m * 0.4, y - m * 0.4, m * 1.4, m * 1.4))
    elif kind == Kind.PERPENDICULAR:
        p.drawPolyline(QPolygonF([QPointF(x - m, y - m), QPointF(x - m, y + m), QPointF(x + m, y + m)]))
        p.drawPolyline(QPolygonF([QPointF(x - m, y), QPointF(x, y), QPointF(x, y + m)]))
    elif kind == Kind.TANGENT:
        p.drawEllipse(q, m * 0.7, m * 0.7)
        p.drawLine(QPointF(x - m, y - m * 0.7), QPointF(x + m, y - m * 0.7))
    elif kind == Kind.ON:
        p.drawPolygon(QPolygonF([QPointF(x - m, y - m), QPointF(x + m, y - m), QPointF(x - m, y + m), QPointF(x + m, y + m)]))
    elif kind == Kind.GUIDE:
        p.drawLine(QPointF(x - m * 0.7, y - m * 0.7), QPointF(x + m * 0.7, y + m * 0.7))
        p.drawLine(QPointF(x - m * 0.7, y + m * 0.7), QPointF(x + m * 0.7, y - m * 0.7))
