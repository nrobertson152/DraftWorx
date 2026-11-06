# SPDX-License-Identifier: GPL-3.0-or-later
"""A person at the keyboard, for tests: the same calls the window makes."""
from draftworx.core.curves import Line
from draftworx.core.session import Session
from draftworx.core.vec import Vec


def line(x0, y0, x1, y1):
    return Line(Vec(float(x0), float(y0)), Vec(float(x1), float(y1)))


class Person:
    def __init__(self, *shapes, px=1.0):
        self.s = Session()
        self.doc = self.s.doc
        self.px = px
        self.t = 0.0
        self.ids = []
        if shapes:
            with self.doc.transaction("set up") as t:
                self.ids = [t.add(x) for x in shapes]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        assert self.s.last_error is None, self.s.last_error

    def at(self, x, y, **kw):
        self.t += 0.01
        self.s.move(x, y, self.px, self.t, **kw)
        return self

    def click(self, x, y, **kw):
        self.t += 0.01
        self.s.press(x, y, self.px, self.t, **kw)
        self.s.release(x, y, self.px, self.t, **kw)
        return self

    def drag(self, x0, y0, x1, y1, **kw):
        self.t += 0.01
        self.s.press(x0, y0, self.px, self.t, **kw)
        n = 4
        for i in range(1, n + 1):
            self.t += 0.01
            self.s.move(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n, self.px, self.t, **kw)
        self.t += 0.01
        self.s.release(x1, y1, self.px, self.t, **kw)
        return self

    def press(self, x, y, **kw):
        self.t += 0.01
        self.s.press(x, y, self.px, self.t, **kw)
        return self

    def release(self, x, y, **kw):
        self.t += 0.01
        self.s.release(x, y, self.px, self.t, **kw)
        return self

    def type(self, text):
        for ch in text:
            self.s.type_text(ch)
        return self

    def key(self, name, **kw):
        self.s.key(name, **kw)
        return self

    def enter(self):
        return self.key("enter")

    def run(self, name):
        assert self.s.run(name)
        return self

    def wait(self, seconds, x=None, y=None):
        """Keep still, as the window's timer sees it."""
        self.t += seconds
        self.s.tick(self.t)
        return self

    def shapes(self):
        """What's in the drawing, in the order it was made."""
        return [self.doc.entities[i].shape for i in sorted(self.doc.entities)]

    def new_shapes(self):
        return [self.doc.entities[i].shape for i in sorted(self.doc.entities) if i not in self.ids]
