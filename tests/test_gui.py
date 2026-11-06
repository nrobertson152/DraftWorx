# SPDX-License-Identifier: GPL-3.0-or-later
"""The real window, driven by real Qt events."""
import math
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QMessageBox, QToolButton  # noqa: E402

from draftworx.core import recovery  # noqa: E402
from draftworx.core.config import Config  # noqa: E402
from draftworx.core.curves import Circle, Line, Polyline  # noqa: E402
from draftworx.core.files import open_drawing  # noqa: E402
from draftworx.core.model import BlockDef, Entity, Layer, Linetype, Style  # noqa: E402
from draftworx.core.shapes import Insert, Solid, Text  # noqa: E402
from draftworx.core.vec import Vec  # noqa: E402
from draftworx.gui import theme as themes  # noqa: E402
from draftworx.gui.palette import Entry, Palette  # noqa: E402
from draftworx.gui.window import MainWindow  # noqa: E402
from driver import line  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication(["draftworx-tests"])


class Hand:
    """Moving the real mouse over the real canvas, in drawing coordinates."""

    def __init__(self, app, window):
        self.app, self.w, self.c = app, window, window.canvas
        self.s = window.session
        self.doc = window.session.doc

    def fit(self, box=(0, 0, 200, 150)):
        self.c.view.fit(box)
        self.c.redraw()
        self.app.processEvents()

    def at(self, x, y) -> QPoint:
        sx, sy = self.c.view.to_screen(x, y)
        return QPoint(round(sx), round(sy))

    def move(self, x, y, mods=Qt.KeyboardModifier.NoModifier):
        QTest.mouseMove(self.c, self.at(x, y))
        self.app.processEvents()
        return self

    def click(self, x, y, mods=Qt.KeyboardModifier.NoModifier):
        self.move(x, y)
        QTest.mouseClick(self.c, Qt.MouseButton.LeftButton, mods, self.at(x, y))
        self.app.processEvents()
        return self

    def type(self, text):
        QTest.keyClicks(self.c, text)
        self.app.processEvents()
        return self

    def key(self, key, mods=Qt.KeyboardModifier.NoModifier):
        QTest.keyClick(self.c, key, mods)
        self.app.processEvents()
        return self

    def enter(self):
        return self.key(Qt.Key.Key_Return)

    def pixel(self, x, y) -> QColor:
        """The colour on screen at a drawing position, from what's actually painted."""
        self.c.redraw()
        self.app.processEvents()
        image = self.c.grab().toImage()
        sx, sy = self.c.view.to_screen(x, y)
        return QColor(image.pixel(round(sx), round(sy)))

    def around(self, x, y, r=2) -> list[QColor]:
        self.c.redraw()
        self.app.processEvents()
        image = self.c.grab().toImage()
        sx, sy = self.c.view.to_screen(x, y)
        return [QColor(image.pixel(round(sx) + i, round(sy) + j)) for i in range(-r, r + 1) for j in range(-r, r + 1)]

    def has_color_near(self, x, y, want: str, r=2) -> bool:
        target = QColor(want)
        return any(abs(c.red() - target.red()) < 60 and abs(c.green() - target.green()) < 60 and abs(c.blue() - target.blue()) < 60
                   for c in self.around(x, y, r))


@pytest.fixture
def hand(app):
    w = MainWindow(Config())
    w.show()
    app.processEvents()
    h = Hand(app, w)
    h.fit()
    yield h
    assert w.session.last_error is None, w.session.last_error
    w.session.doc.forget_history()
    w.session.doc.mark_saved()
    w.close()
    app.processEvents()


def add(hand, *shapes, style=None):
    with hand.doc.transaction("set up") as t:
        return [t.add(s, style) for s in shapes]


class TestDrawing:
    def test_a_line_with_the_mouse(self, hand):
        hand.s.run("LINE")
        hand.click(20, 20).click(120, 75).enter()
        shapes = [e.shape for e in hand.doc.entities.values()]
        assert len(shapes) == 1 and isinstance(shapes[0], Line)
        assert shapes[0].a.distance((20, 20)) < 1.0 and shapes[0].b.distance((120, 75)) < 1.0

    def test_it_is_painted(self, hand):
        add(hand, line(20, 50, 180, 50))
        assert hand.has_color_near(100, 50, hand.w.theme.canvas["ink"])

    def test_typing_starts_a_command_with_nothing_clicked(self, hand):
        hand.type("c").key(Qt.Key.Key_Space)
        assert hand.s.command_name == "CIRCLE"
        hand.click(100, 75).type("25").enter()
        c = next(iter(hand.doc.entities.values())).shape
        assert isinstance(c, Circle) and c.r == 25.0

    def test_a_typed_length_is_exact(self, hand):
        hand.s.run("LINE")
        hand.click(20, 20).move(100, 20.3).type("125.5").enter().enter()
        s = next(iter(hand.doc.entities.values())).shape
        assert s.length() == pytest.approx(125.5)
        assert s.a[1] == s.b[1]

    def test_snapping_to_what_is_there_is_exact(self, hand):
        a = Vec(1 / 3, 100 / 7)
        add(hand, Line(a, Vec(100, 100)))
        hand.s.run("LINE")
        sx, sy = hand.c.view.to_screen(*a)
        hand.click(*hand.c.view.to_world(sx + 3, sy + 2)).click(150, 20).enter()
        new = hand.doc.entities[max(hand.doc.entities)].shape
        assert new.a == a

    def test_tab_does_not_lose_the_keyboard(self, hand):
        hand.s.run("LINE")
        hand.key(Qt.Key.Key_Tab)
        assert hand.c.hasFocus()
        hand.type("5,5").enter()
        assert hand.s.request.base == Vec(5, 5)

    def test_tab_with_a_number_holds_it(self, hand):
        hand.s.run("LINE")
        hand.click(50, 50).type("40").key(Qt.Key.Key_Tab)
        assert hand.s.lock_radius == 40.0

    def test_escape_stops(self, hand):
        hand.s.run("LINE")
        hand.key(Qt.Key.Key_Escape)
        assert not hand.s.active

    def test_right_click_finishes(self, hand):
        hand.s.run("LINE")
        hand.click(20, 20).click(50, 50)
        QTest.mouseClick(hand.c, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, hand.at(60, 60))
        assert not hand.s.active
        assert len(hand.doc.entities) == 1

    def test_function_keys(self, hand):
        hand.key(Qt.Key.Key_F8)
        assert hand.s.drafting.ortho
        hand.key(Qt.Key.Key_F3)
        assert not hand.s.drafting.snap
        hand.key(Qt.Key.Key_F9)
        assert hand.s.drafting.grid
        hand.key(Qt.Key.Key_F10)
        assert not hand.s.drafting.tracking

    def test_status_toggles_follow(self, hand):
        hand.key(Qt.Key.Key_F8)
        assert hand.w.toggles["f8"].isChecked()
        hand.w.toggles["f8"].click()
        assert not hand.s.drafting.ortho

    def test_shift_held_is_noticed_without_moving(self, hand):
        hand.s.run("LINE")
        hand.click(50, 50).move(120, 60)
        before = hand.s.snap.point
        QTest.keyPress(hand.c, Qt.Key.Key_Shift, Qt.KeyboardModifier.ShiftModifier)
        hand.app.processEvents()
        assert hand.s.shift
        assert hand.s.snap.point[1] == 50.0 and hand.s.snap.point != before
        QTest.keyRelease(hand.c, Qt.Key.Key_Shift, Qt.KeyboardModifier.NoModifier)
        assert not hand.s.shift


class TestView:
    def wheel(self, hand, x, y, delta, mods=Qt.KeyboardModifier.NoModifier):
        pos = QPointF(hand.at(x, y))
        ev = QWheelEvent(pos, QPointF(hand.c.mapToGlobal(pos.toPoint())), QPoint(0, 0), QPoint(0, delta), Qt.MouseButton.NoButton, mods,
                         Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(hand.c, ev)
        hand.app.processEvents()

    def test_wheel_zooms_about_the_cursor(self, hand):
        before = hand.c.view.scale
        sx, sy = hand.c.view.to_screen(50, 50)
        self.wheel(hand, 50, 50, 240)
        assert hand.c.view.scale == pytest.approx(before * 1.44)
        assert hand.c.view.to_screen(50, 50) == pytest.approx((sx, sy), abs=1.0)

    def test_shift_wheel_pans(self, hand):
        before = hand.c.view.to_screen(50, 50)
        self.wheel(hand, 50, 50, 120, Qt.KeyboardModifier.ShiftModifier)
        after = hand.c.view.to_screen(50, 50)
        assert after[0] != before[0] and after[1] == before[1]

    def test_middle_drag_pans(self, hand):
        before = hand.c.view.to_screen(50, 50)
        QTest.mousePress(hand.c, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, QPoint(100, 100))
        ev_pos = QPoint(140, 130)
        from PySide6.QtGui import QMouseEvent
        move = QMouseEvent(QEvent.Type.MouseMove, QPointF(ev_pos), QPointF(hand.c.mapToGlobal(ev_pos)), Qt.MouseButton.MiddleButton,
                           Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(hand.c, move)
        QTest.mouseRelease(hand.c, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, ev_pos)
        after = hand.c.view.to_screen(50, 50)
        assert after == pytest.approx((before[0] + 40, before[1] + 30))

    def test_fit(self, hand):
        add(hand, line(1000, 1000, 2000, 2000))
        hand.c.fit()
        hand.app.processEvents()
        x0, y0, x1, y1 = hand.c.view.visible()
        assert x0 <= 1000 and x1 >= 2000 and y0 <= 1000 and y1 >= 2000

    def test_resizing_keeps_working(self, hand):
        hand.w.resize(800, 500)
        hand.app.processEvents()
        assert hand.c.view.width == hand.c.width()
        assert not hand.c.grab().isNull()


class TestPainting:
    def test_a_hidden_layer_is_not_painted(self, hand):
        with hand.doc.transaction("x") as t:
            t.put_layer(Layer("Off", on=False))
            t.add(line(20, 50, 180, 50), Style("Off"))
        assert not hand.has_color_near(100, 50, hand.w.theme.canvas["ink"])

    def test_a_colour_is_shown(self, hand):
        add(hand, line(20, 50, 180, 50), style=Style("0", 1))
        assert hand.has_color_near(100, 50, "#ff0000")

    def test_bylayer_colour_is_shown_and_follows_the_layer(self, hand):
        with hand.doc.transaction("x") as t:
            t.put_layer(Layer("Red", 1))
            t.add(line(20, 50, 180, 50), Style("Red"))
        assert hand.has_color_near(100, 50, "#ff0000")
        with hand.doc.transaction("y") as t:
            t.put_layer(Layer("Red", 5))
        assert hand.has_color_near(100, 50, "#0000ff")
        hand.doc.undo()
        assert hand.has_color_near(100, 50, "#ff0000")

    def test_colour_7_is_the_opposite_of_the_paper(self, hand):
        add(hand, line(20, 50, 180, 50), style=Style("0", 7))
        assert hand.has_color_near(100, 50, "#14181d")
        hand.w.set_theme("modern_dark")
        assert hand.has_color_near(100, 50, "#e6e8eb")
        hand.w.set_theme("modern_light")

    def test_a_colour_that_would_vanish_is_darkened_on_screen_only(self, hand):
        add(hand, line(20, 50, 180, 50), style=Style("0", 2))     # yellow on white
        c = min(hand.around(100, 50), key=lambda q: q.lightness())
        assert c.lightness() < 200
        assert next(iter(hand.doc.entities.values())).style.color == 2

    def test_undo_repaints(self, hand):
        add(hand, line(20, 50, 180, 50))
        ink = hand.w.theme.canvas["ink"]
        assert hand.has_color_near(100, 50, ink)
        hand.doc.undo()
        assert not hand.has_color_near(100, 50, ink)

    def test_an_edit_repaints_only_that_but_correctly(self, hand):
        a, b = add(hand, line(20, 50, 180, 50), line(20, 100, 180, 100))
        ink = hand.w.theme.canvas["ink"]
        with hand.doc.transaction("Move") as t:
            t.replace(a, line(20, 25, 180, 25))
        assert hand.has_color_near(100, 25, ink)
        assert not hand.has_color_near(100, 50, ink)
        assert hand.has_color_near(100, 100, ink)

    def test_block_contents_keep_their_own_colours(self, hand):
        with hand.doc.transaction("x") as t:
            t.put_block(BlockDef("B", Vec(0, 0), (Entity(1000, line(0, 0, 100, 0), Style("0", 1)), Entity(1001, line(0, 50, 100, 50), Style("0", 5)))))
            t.add(Insert("B", Vec(50, 20)))
        assert hand.has_color_near(100, 20, "#ff0000")
        assert hand.has_color_near(100, 70, "#0000ff")

    def test_byblock_takes_the_copys_colour(self, hand):
        with hand.doc.transaction("x") as t:
            t.put_block(BlockDef("B", Vec(0, 0), (Entity(1000, line(0, 0, 100, 0), Style("0", 0)),)))
            t.add(Insert("B", Vec(50, 20)), Style("0", 5))
        assert hand.has_color_near(100, 20, "#0000ff")

    def test_text_is_painted(self, hand):
        add(hand, Text(Vec(20, 50), 20.0, "HHHHHHHH"))
        c = hand.around(50, 60, 10)
        assert any(q.lightness() < 100 for q in c)

    def test_dashes_show_gaps(self, hand):
        with hand.doc.transaction("x") as t:
            t.put_linetype(Linetype("Dash", "", (10.0, -10.0)))
            t.add(line(0, 50, 200, 50), Style("0", 1, "Dash"))
        hand.c.redraw()
        hand.app.processEvents()
        image = hand.c.grab().toImage()
        # A one-pixel line sits on whichever row its position rounds to, and that depends on the size of the window, so look at the rows
        # around it and judge the one the line is on. Nothing red at all would mean the line wasn't drawn, which is its own failure.
        middle = round(hand.c.view.to_screen(0, 50)[1])
        left, right = round(hand.c.view.to_screen(0, 0)[0]), round(hand.c.view.to_screen(200, 0)[0])
        rows = [[QColor(image.pixel(x, y)).red() - QColor(image.pixel(x, y)).blue() > 100 for x in range(left, right)] for y in range(middle - 2, middle + 3)]
        reds = max(rows, key=sum)
        assert sum(reds) > 0, "the line wasn't drawn"
        changes = sum(1 for a, b in zip(reds, reds[1:]) if a != b)
        assert changes > 10

    def test_a_selected_thing_is_highlighted(self, hand):
        add(hand, line(20, 50, 180, 50))
        hand.click(100, 50)
        assert hand.s.selection
        assert hand.has_color_near(70, 50, hand.w.theme.canvas["select"])

    def test_every_theme_paints(self, hand):
        add(hand, line(20, 50, 180, 50), Circle(Vec(100, 75), 30))
        for name in themes.THEMES:
            hand.w.set_theme(name)
            hand.app.processEvents()
            assert hand.has_color_near(100, 50, hand.w.theme.canvas["ink"], 3)
        hand.w.set_theme("modern_light")

    def test_very_large_and_very_small_views_do_not_fail(self, hand):
        add(hand, line(0, 0, 1e7, 1e7), Circle(Vec(0, 0), 1e6))
        hand.c.fit()
        hand.app.processEvents()
        hand.c.view.scale = 1e6
        hand.c.redraw()
        hand.app.processEvents()
        assert not hand.c.grab().isNull()

    def test_a_block_that_contains_itself_in_memory_cannot_hang_the_paint(self, hand):
        hand.doc.blocks._put(BlockDef("A", entities=(Entity(1, Insert("B", Vec(0, 0))),)))
        hand.doc.blocks._put(BlockDef("B", entities=(Entity(2, Insert("A", Vec(0, 0))),)))
        add(hand, Insert("A", Vec(0, 0)))
        assert not hand.c.grab().isNull()


class TestPanel:
    def field(self, hand, label: str) -> QLineEdit:
        form = hand.w.panel.form
        for i in range(form.rowCount()):
            item = form.itemAt(i, form.ItemRole.LabelRole)
            if item is not None and item.widget().text() == label:
                return form.itemAt(i, form.ItemRole.FieldRole).widget()
        raise AssertionError(f"no row called {label}")

    def test_nothing_selected_describes_the_drawing(self, hand):
        add(hand, line(0, 0, 100, 0))
        hand.w.panel.refresh(True)
        assert hand.w.panel.title.text() == "Drawing"
        assert "1 object" in hand.w.panel.sub.text()

    def test_a_selection_shows_what_it_is_and_its_numbers(self, hand):
        add(hand, line(20, 50, 120, 50))
        hand.click(70, 50)
        assert hand.w.panel.title.text() == "Line"
        assert self.field(hand, "Length").text() == "100.00"

    def test_typing_a_new_length(self, hand):
        a, = add(hand, line(20, 50, 120, 50))
        hand.click(70, 50)
        f = self.field(hand, "Length")
        f.setFocus()
        f.selectAll()
        QTest.keyClicks(f, "150")
        QTest.keyClick(f, Qt.Key.Key_Return)
        assert hand.doc.entities[a].shape.length() == pytest.approx(150)
        assert hand.c.hasFocus()
        hand.doc.undo()
        assert hand.doc.entities[a].shape.length() == pytest.approx(100)

    def test_a_bad_value_is_put_back_and_says_why(self, hand):
        a, = add(hand, line(20, 50, 120, 50))
        hand.click(70, 50)
        f = self.field(hand, "Length")
        f.setFocus()
        f.selectAll()
        QTest.keyClicks(f, "abc")
        QTest.keyClick(f, Qt.Key.Key_Return)
        assert hand.doc.entities[a].shape.length() == pytest.approx(100)
        assert f.text() == "100.00"
        assert hand.w.session.message

    def test_escape_in_a_field_puts_it_back(self, hand):
        add(hand, line(20, 50, 120, 50))
        hand.click(70, 50)
        f = self.field(hand, "Length")
        f.setFocus()
        QTest.keyClicks(f, "99")
        QTest.keyClick(f, Qt.Key.Key_Escape)
        assert f.text() == "100.00"

    def test_typing_in_a_field_does_not_reach_the_drawing(self, hand):
        add(hand, line(20, 50, 120, 50))
        hand.click(70, 50)
        f = self.field(hand, "Length")
        f.setFocus()
        QTest.keyClicks(f, "l")
        assert hand.s.input == ""

    def test_several_selected_shows_totals(self, hand):
        add(hand, line(20, 50, 120, 50), line(20, 80, 120, 80))
        hand.s.set_selection(hand.doc.entities)
        hand.w.panel.refresh(True)
        assert hand.w.panel.title.text() == "2 selected"
        assert self.field(hand, "Total length").text() == "200.00"

    def test_changing_a_layer_from_the_list(self, hand):
        with hand.doc.transaction("x") as t:
            t.put_layer(Layer("Walls"))
            a = t.add(line(20, 50, 120, 50))
        hand.click(70, 50)
        box = self.field(hand, "Layer")
        assert isinstance(box, QComboBox)
        box.setCurrentIndex(box.findText("Walls"))
        box.activated.emit(box.currentIndex())
        assert hand.doc.entities[a].style.layer == "Walls"
        hand.doc.undo()
        assert hand.doc.entities[a].style.layer == "0"

    def test_a_mixed_selection_says_so_and_can_be_set_for_all(self, hand):
        with hand.doc.transaction("x") as t:
            t.put_layer(Layer("A"))
            t.put_layer(Layer("B"))
            a = t.add(line(20, 50, 120, 50), Style("A"))
            b = t.add(line(20, 80, 120, 80), Style("B"))
        hand.s.set_selection([a, b])
        hand.w.panel.refresh(True)
        box = self.field(hand, "Layer")
        assert box.currentText() == "(mixed)"
        box.setCurrentIndex(box.findText("B"))
        box.activated.emit(box.currentIndex())
        assert hand.doc.entities[a].style.layer == "B"

    def test_choosing_mixed_does_nothing(self, hand):
        with hand.doc.transaction("x") as t:
            t.put_layer(Layer("A"))
            a = t.add(line(20, 50, 120, 50), Style("A"))
            b = t.add(line(20, 80, 120, 80))
        hand.s.set_selection([a, b])
        hand.w.panel.refresh(True)
        box = self.field(hand, "Layer")
        box.setCurrentIndex(0)
        box.activated.emit(0)
        assert hand.doc.entities[a].style.layer == "A"

    def test_layers_are_listed(self, hand):
        with hand.doc.transaction("x") as t:
            t.put_layer(Layer("Walls"))
        hand.w.panel.refresh(True)
        names = [b.text() for b in hand.w.panel.scroll.widget().findChildren(type(hand.w.panel.add_button).__mro__[1])  # QAbstractButton
                 if b.text()]
        assert "Walls" in names and "0" in names

    def test_making_a_layer_current(self, hand):
        with hand.doc.transaction("x") as t:
            t.put_layer(Layer("Walls"))
        hand.w.panel.refresh(True)
        hand.w.panel._make_current("Walls")
        assert hand.doc.current.layer == "Walls"
        hand.s.run("LINE")
        hand.click(20, 20).click(100, 50).enter()
        assert hand.doc.entities[max(hand.doc.entities)].style.layer == "Walls"

    def test_hiding_a_layer_from_its_row(self, hand):
        with hand.doc.transaction("x") as t:
            t.put_layer(Layer("Walls"))
            t.add(line(20, 50, 180, 50), Style("Walls"))
        ink = hand.w.theme.canvas["ink"]
        assert hand.has_color_near(100, 50, ink)
        hand.w.panel._flag("Walls", on=False)
        assert not hand.has_color_near(100, 50, ink)

    def test_deleting_a_layer_in_use_says_why(self, hand):
        with hand.doc.transaction("x") as t:
            t.put_layer(Layer("Walls"))
            t.add(line(20, 50, 180, 50), Style("Walls"))
        hand.w.panel._delete_layer("Walls")
        assert "Walls" in hand.doc.layers and "still has" in hand.s.message


class TestWindow:
    def test_undo_and_redo_actions(self, hand):
        add(hand, line(0, 0, 10, 10))
        hand.w.undo()
        assert not hand.doc.entities
        hand.w.redo()
        assert hand.doc.entities

    def test_undo_says_what_it_will_undo(self, hand):
        add(hand, line(0, 0, 10, 10))
        hand.w._state_changed()
        assert hand.w.actions_by_name["Undo"].text() == "Undo set up"

    def test_select_all(self, hand):
        with hand.doc.transaction("x") as t:
            t.put_layer(Layer("Off", on=False))
            t.put_layer(Layer("Locked", locked=True))
            a = t.add(line(0, 0, 10, 10))
            t.add(line(0, 0, 10, 10), Style("Off"))
            t.add(line(0, 0, 10, 10), Style("Locked"))
        hand.w.select_all()
        assert hand.s.selection == {a}

    def test_prompt_and_options_show(self, hand):
        hand.s.run("LINE")
        hand.click(20, 20).click(100, 50)
        hand.w._state_changed()
        assert hand.w.prompt.text().startswith("Line:")
        assert hand.w.options_row.count() == 1

    def test_clicking_an_option_chooses_it(self, hand):
        hand.s.run("LINE")
        hand.click(20, 20).click(100, 20).click(100, 100)
        hand.w._state_changed()
        hand.w._choose_option("Close")
        assert not hand.s.active
        assert len(hand.doc.entities) == 3

    def test_keys_on_a_button_still_reach_the_drawing(self, hand):
        b = hand.w.rail.findChildren(QToolButton)[1]
        b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        b.setFocus()
        hand.w.activateWindow()
        hand.app.processEvents()
        if QApplication.focusWidget() is b:
            QTest.keyClicks(b, "l")
            assert hand.s.input == "l"

    def test_the_rail_runs_commands(self, hand):
        b = hand.w.rail.findChildren(QToolButton)[1]
        b.click()
        assert hand.s.command_name == "LINE"
        assert hand.c.hasFocus()

    def test_the_status_bar_shows_where_the_cursor_is(self, hand):
        hand.move(50, 50)
        assert "," in hand.w.coords.text()

    def test_polar_step_choice(self, hand):
        at = hand.w.polar.findData(30)
        hand.w.polar.setCurrentIndex(at)
        hand.w._polar_changed(at)
        assert hand.s.drafting.polar_step == pytest.approx(math.pi / 6)

    def test_toggling_the_grid_and_weights(self, hand):
        hand.w._toggle_grid()
        assert hand.w.config.show_grid is False
        hand.w._toggle_weights()
        assert hand.w.config.show_weights is False
        hand.app.processEvents()

    def test_panel_can_be_hidden(self, hand):
        assert hand.w.panel.isVisible()
        hand.w._toggle_panel()
        assert not hand.w.panel.isVisible()

    def test_right_click_with_nothing_running_asks_for_a_menu(self, hand):
        asked = []
        hand.c.context_menu.disconnect()
        hand.c.context_menu.connect(asked.append)
        QTest.mouseClick(hand.c, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, hand.at(50, 50))
        assert asked


class TestPalette:
    def test_everything_is_listed_when_nothing_is_typed(self, hand):
        p = Palette(hand.w, hand.s, [Entry("Save", "Save it", lambda: None)])
        assert p.list.count() > 10

    def test_typing_filters_and_enter_chooses_the_first(self, hand):
        ran = []
        p = Palette(hand.w, hand.s, [Entry("Save", "Save the drawing", lambda: ran.append(1))])
        p.box.setText("sav")
        assert p.list.count() >= 1
        p.box.returnPressed.emit()
        assert p.chosen.name == "Save"
        p.chosen.run()
        assert ran == [1]

    def test_a_command_can_be_run_from_it(self, hand):
        p = Palette(hand.w, hand.s, [])
        p.box.setText("circle")
        p.box.returnPressed.emit()
        p.chosen.run()
        assert hand.s.command_name == "CIRCLE"

    def test_arrow_keys_move_the_choice(self, hand):
        p = Palette(hand.w, hand.s, [])
        p.show()
        p.box.setText("e")
        first = p.list.currentRow()
        QTest.keyClick(p.box, Qt.Key.Key_Down)
        assert p.list.currentRow() == first + 1
        QTest.keyClick(p.box, Qt.Key.Key_Up)
        assert p.list.currentRow() == first
        QTest.keyClick(p.box, Qt.Key.Key_Escape)
        assert p.chosen is None

    def test_no_match_chooses_nothing(self, hand):
        p = Palette(hand.w, hand.s, [])
        p.box.setText("zzzzqqq")
        p.box.returnPressed.emit()
        assert p.chosen is None


class TestFiles:
    def test_save_and_open(self, hand, tmp_path):
        add(hand, line(0, 0, 100, 0))
        p = tmp_path / "a.draftworx"
        assert hand.w._save_to(str(p))
        assert hand.w.windowTitle() == "a.draftworx - DraftWorx"
        assert p.exists()
        fingerprint = hand.doc.fingerprint()
        assert hand.w.open_path(str(p))
        assert hand.w.session.doc is not hand.doc
        assert hand.w.session.doc.fingerprint() == fingerprint
        assert hand.w.canvas.scene.doc is hand.w.session.doc

    def test_the_title_marks_unsaved_changes(self, hand, tmp_path):
        p = tmp_path / "a.draftworx"
        hand.w._save_to(str(p))
        add(hand, line(0, 0, 100, 0))
        hand.w._state_changed()
        assert hand.w.windowTitle().startswith("*")

    def test_a_malformed_file_shows_a_message(self, hand, tmp_path, monkeypatch):
        p = tmp_path / "bad.draftworx"
        p.write_bytes(b"not a drawing")
        said = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: said.append(a[2]))
        doc = hand.w.session.doc
        assert not hand.w.open_path(str(p))
        assert said and hand.w.session.doc is doc

    def test_a_failed_save_shows_a_message_and_stays_unsaved(self, hand, tmp_path, monkeypatch):
        add(hand, line(0, 0, 100, 0))
        said = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: said.append(a[2]))
        assert not hand.w._save_to(str(tmp_path / "missing" / "a.draftworx"))
        assert said and hand.doc.dirty

    def test_a_file_changed_elsewhere_asks_first(self, hand, tmp_path, monkeypatch):
        p = tmp_path / "a.draftworx"
        add(hand, line(0, 0, 100, 0))
        hand.w._save_to(str(p))
        p.write_bytes(p.read_bytes() + b" ")
        add(hand, line(1, 1, 2, 2))

        def cancel(box):
            box.clickedButton = lambda: None
            return 0
        monkeypatch.setattr(QMessageBox, "exec", cancel)
        theirs = p.read_bytes()
        assert not hand.w.save()
        assert p.read_bytes() == theirs

    def test_closing_with_changes_asks(self, app, monkeypatch):
        w = MainWindow(Config())
        w.show()
        with w.session.doc.transaction("x") as t:
            t.add(line(0, 0, 1, 1))
        asked = []

        def cancel(box):
            asked.append(box.text())
            box.clickedButton = lambda: None
            return 0
        monkeypatch.setattr(QMessageBox, "exec", cancel)
        assert not w.close()
        assert asked and w.isVisible()
        w.session.doc.forget_history()
        w.session.doc.mark_saved()
        assert w.close()

    def test_a_new_drawing_after_saving(self, hand, tmp_path):
        add(hand, line(0, 0, 100, 0))
        hand.w._save_to(str(tmp_path / "a.draftworx"))
        hand.w.new_drawing()
        assert not hand.w.session.doc.entities
        assert hand.w.opened.path is None
        assert hand.w.windowTitle() == "Untitled - DraftWorx"


class TestRecovery:
    def test_autosave_keeps_a_copy_and_saving_removes_it(self, hand, tmp_path):
        add(hand, line(0, 0, 100, 0))
        hand.w._autosave()
        found = recovery.leftovers()
        assert len(found) == 1
        assert recovery.read(found[0]).fingerprint() == hand.doc.fingerprint()
        hand.w._save_to(str(tmp_path / "a.draftworx"))
        assert recovery.leftovers() == []

    def test_nothing_is_kept_with_nothing_to_keep(self, hand):
        hand.w._autosave()
        assert recovery.leftovers() == []

    def test_a_copy_left_by_a_crash_is_offered_and_recovered_as_unsaved(self, app, monkeypatch):
        doc_source = MainWindow(Config())
        with doc_source.session.doc.transaction("x") as t:
            t.add(line(0, 0, 100, 0))
        recovery.autosave(doc_source.session.doc, "crashed", Path("bracket.draftworx"))
        wanted = doc_source.session.doc.fingerprint()
        doc_source.session.doc.forget_history()
        doc_source.close()

        w = MainWindow(Config())
        w.show()

        def choose_recover(box):
            recover = next(b for b in box.buttons() if b.text() == "Recover")
            box.clickedButton = lambda: recover
            return 0
        monkeypatch.setattr(QMessageBox, "exec", choose_recover)
        w.offer_recovery()
        assert w.session.doc.fingerprint() == wanted
        assert w.session.doc.dirty
        assert recovery.leftovers() == []
        w.session.doc._saved_at = 0
        w.session.doc.forget_history()
        w.close()

    def test_decide_later_keeps_it(self, app, monkeypatch):
        from draftworx.core.model import Document
        d = Document()
        with d.transaction("x") as t:
            t.add(line(0, 0, 100, 0))
        recovery.autosave(d, "crashed", None)
        w = MainWindow(Config())

        def later(box):
            b = next(b for b in box.buttons() if b.text() == "Decide later")
            box.clickedButton = lambda: b
            return 0
        monkeypatch.setattr(QMessageBox, "exec", later)
        w.offer_recovery()
        assert len(recovery.leftovers()) == 1
        w.close()

    def test_discarding_removes_it(self, app, monkeypatch):
        from draftworx.core.model import Document
        recovery.autosave(Document(), "crashed", None)
        w = MainWindow(Config())

        def drop(box):
            b = next(b for b in box.buttons() if b.text() == "Discard it")
            box.clickedButton = lambda: b
            return 0
        monkeypatch.setattr(QMessageBox, "exec", drop)
        w.offer_recovery()
        assert recovery.leftovers() == []
        w.close()


def test_self_test(app):
    from draftworx.gui.app import self_test
    ok, what = self_test()
    assert ok and what


def test_opening_a_file_given_on_the_command_line(app, tmp_path):
    from draftworx.core.files import Opened, save_drawing
    from draftworx.core.model import Document
    d = Document()
    with d.transaction("x") as t:
        t.add(line(0, 0, 100, 0))
    p = tmp_path / "a.draftworx"
    save_drawing(Opened(d), p)
    w = MainWindow(Config(), str(p))
    assert w.opened.path == p
    assert len(w.session.doc.entities) == 1
    assert open_drawing(p).doc.fingerprint() == w.session.doc.fingerprint()
    w.close()


DATA = Path(__file__).parent / "data"


def overlapping(a, b):
    return Solid((Vec(a, a), Vec(b, a), Vec(b, b), Vec(a, b)))


class TestFilledThings:
    def test_two_overlapping_solids_dont_cut_a_hole_in_each_other(self, hand):
        add(hand, overlapping(20, 80), overlapping(50, 110))
        ink = hand.w.theme.canvas["ink"]
        assert hand.has_color_near(35, 35, ink) and hand.has_color_near(65, 65, ink) and hand.has_color_near(95, 95, ink)

    def test_a_wide_polyline_is_as_wide_as_it_says(self, hand):
        add(hand, Polyline((Vec(20.0, 50.0), Vec(180.0, 50.0)), width=20.0))
        ink = hand.w.theme.canvas["ink"]
        assert hand.has_color_near(100, 58, ink, 0)
        assert not hand.has_color_near(100, 65, ink, 0)

    def test_a_tapered_polyline_narrows(self, hand):
        add(hand, Polyline((Vec(20.0, 50.0), Vec(180.0, 50.0), Vec(190.0, 50.0)), widths=((30.0, 0.0), (0.0, 0.0), (0.0, 0.0))))
        ink = hand.w.theme.canvas["ink"]
        assert hand.has_color_near(30, 60, ink, 0)
        assert not hand.has_color_near(150, 60, ink, 0)
        # the part with no width still shows as a line
        assert hand.has_color_near(185, 50, ink)

    def test_a_wide_arc_in_a_polyline(self, hand):
        add(hand, Polyline((Vec(50.0, 50.0), Vec(150.0, 50.0)), (1.0, 0.0), width=10.0))
        ink = hand.w.theme.canvas["ink"]
        # a half circle with its bulge to the right of the way it goes: below, here
        assert hand.has_color_near(100, 3, ink, 1) or hand.has_color_near(100, 0, ink, 1)
        assert not hand.has_color_near(100, 50, ink, 1)


class TestDxfInTheWindow:
    def write(self, tmp_path, build, version="R2013", name="a.dxf"):
        ezdxf = pytest.importorskip("ezdxf")
        doc = ezdxf.new(version)
        build(doc, doc.modelspace())
        path = tmp_path / name
        doc.saveas(path)
        return path

    def test_it_opens_and_shows_what_it_holds(self, hand, tmp_path):
        def build(doc, m):
            m.add_line((20, 50), (180, 50), dxfattribs={"color": 1})
            m.add_line((20, 100), (180, 100), dxfattribs={"color": 3})
        assert hand.w.open_path(str(self.write(tmp_path, build)))
        hand.fit()
        assert hand.has_color_near(100, 50, hand.c.scene.color_for(0xff0000).name()) and hand.has_color_near(100, 100, hand.c.scene.color_for(0x00ff00).name())

    def test_what_cant_be_edited_still_shows(self, hand, tmp_path):
        def build(doc, m):
            m.add_linear_dim(base=(20, 100), p1=(20, 50), p2=(180, 50), dxfattribs={"color": 1}).render()
            m.add_leader([(20, 20), (60, 20)], dxfattribs={"color": 3})
            h = m.add_hatch(color=5)
            h.paths.add_polyline_path([(100, 10), (180, 10), (180, 40), (100, 40)], is_closed=True)
        assert hand.w.open_path(str(self.write(tmp_path, build)))
        hand.fit()
        color = hand.c.scene.color_for
        assert hand.has_color_near(40, 100, color(0xff0000).name())     # the dimension line, in its block
        assert hand.has_color_near(40, 20, color(0x00ff00).name())      # the leader
        assert hand.has_color_near(140, 10, color(0x0000ff).name())     # the hatch's edge

    def test_clicking_it_selects_it_and_the_panel_names_it(self, hand, tmp_path):
        def build(doc, m):
            m.add_leader([(20, 20), (160, 20)])
        hand.w.open_path(str(self.write(tmp_path, build)))
        hand.fit()
        hand.click(100, 20)
        assert len(hand.w.session.selection) == 1
        assert hand.w.panel.title.text() == "Leader"

    def test_it_says_what_it_found_and_keeps_saying_it(self, hand, tmp_path):
        assert hand.w.panel.notes.isHidden()

        def build(doc, m):
            h = m.add_hatch()
            h.paths.add_polyline_path([(0, 0), (1, 0), (1, 1)], is_closed=True)
            m.add_ray((0, 0), (1, 0))
        hand.w.open_path(str(self.write(tmp_path, build)))
        said = hand.w.panel.notes.text()
        assert "1 hatch" in said and "outlines" in said and "1 ray" in said
        assert not hand.w.panel.notes.isHidden()
        hand.click(100, 100)
        assert hand.w.panel.notes.text() == said
        hand.w.new_drawing()
        assert hand.w.panel.notes.isHidden()

    def test_a_file_that_isnt_one_gets_a_message(self, hand, tmp_path, monkeypatch):
        shown = []
        monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text: shown.append(text))
        path = tmp_path / "a.dxf"
        path.write_bytes(b"not a drawing")
        assert not hand.w.open_path(str(path))
        assert "isn't a DXF" in shown[0]

    def test_saving_as_dxf_by_choosing_it_in_the_dialog(self, hand, tmp_path, monkeypatch):
        add(hand, line(20, 50, 180, 50))
        target = tmp_path / "chosen"
        monkeypatch.setattr("draftworx.gui.window.QFileDialog.getSaveFileName", lambda *a, **k: (str(target), "DXF file (*.dxf)"))
        assert hand.w.save_as()
        assert (tmp_path / "chosen.dxf").read_bytes()[:40].strip().startswith(b"0")
        assert open_drawing(tmp_path / "chosen.dxf").doc.entities

    def test_exporting_r12_leaves_the_drawing_as_it_was(self, hand, tmp_path, monkeypatch):
        add(hand, line(20, 50, 180, 50), Circle(Vec(100.0, 100.0), 20.0))
        monkeypatch.setattr("draftworx.gui.window.QFileDialog.getSaveFileName", lambda *a, **k: (str(tmp_path / "old"), "DXF file (*.dxf)"))
        assert hand.w.export_r12()
        assert b"AC1009" in (tmp_path / "old.dxf").read_bytes()[:200]
        assert hand.w.opened.path is None and hand.doc.dirty
        assert "Exported old.dxf" in hand.w.session.message

    def test_a_save_of_an_opened_file_leaves_the_rest_alone(self, hand, tmp_path):
        def build(doc, m):
            m.add_line((20, 50), (180, 50))
            m.add_ray((0, 0), (1, 0))
        path = self.write(tmp_path, build)
        before = path.read_bytes()
        hand.w.open_path(str(path))
        assert hand.w.save()
        assert path.read_bytes() == before
        with hand.w.session.doc.transaction("x") as t:
            t.add(line(1, 1, 2, 2))
        assert hand.w.save()
        assert b"RAY" in path.read_bytes() and len(open_drawing(path).doc.entities) == 3

    @pytest.mark.parametrize("path", sorted(DATA.glob("*.dxf")), ids=lambda p: p.name)
    def test_every_real_file_opens_and_paints(self, hand, path):
        assert hand.w.open_path(str(path))
        hand.w.canvas.fit()
        hand.c.redraw()
        hand.app.processEvents()
        assert not hand.c.grab().isNull()
        hand.move(10, 10)

    def test_blocks_in_blocks_in_arrays_dont_stop_the_program(self, hand, tmp_path, monkeypatch):
        """A file a few kilobytes long can describe more than any computer can draw: twenty copies of twenty copies, ten levels down."""
        said = []
        monkeypatch.setattr(hand.w, "_say", said.append)

        def build(doc, m):
            b = doc.blocks.new("L0")
            b.add_line((0, 0), (1, 1))
            for level in range(1, 10):
                b = doc.blocks.new(f"L{level}")
                for i in range(20):
                    b.add_blockref(f"L{level - 1}", (i, level))
            big = m.add_blockref("L9", (0, 0))
            big.grid((1000, 1000), (1, 1))
        path = self.write(tmp_path, build)
        start = time.monotonic()
        assert hand.w.open_path(str(path))
        hand.w.canvas.fit()
        hand.c.redraw()
        hand.app.processEvents()
        hand.c.grab()
        hand.move(5, 5)
        hand.click(5, 5)
        hand.key(Qt.Key.Key_Escape)
        assert time.monotonic() - start < 120.0
        assert any("only part" in s for s in said)
