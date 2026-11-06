# Known limitations

DraftWorx is deliberately small. A simple result mustn't look like an exact one, so here's what it doesn't do.

## Not there yet

This is early. Only the core exists so far: geometry, the drawing, undo and files. There's no window yet. See `ROADMAP.md`.

## By design

- **2D only.** Z values in a DXF aren't used.
- **No DWG.** DXF is the exchange format.
- **No paper space or viewports**, so one scale per sheet (`ARCHITECTURE.md`, ADR-5). A DXF that has them keeps them, but they aren't shown or editable.
- **No parametric constraints.**

## Accuracy

- Coordinates are IEEE doubles. Two points closer than a billionth of a unit (or a trillionth of their size, for large coordinates) count as the same.
- Where curves touch without crossing (tangency) and neither is a line or circle, the contact point is found numerically, to about a millionth of the curves' size.
- Text extents use a fixed table of Helvetica's widths, so they can differ a little from what's drawn, and from another program's fonts.
- NURBS and splines above degree 3 draw as curves matched to a hundred-thousandth of their size. Polynomial cubic splines draw exactly.

## Not verified

- Native Windows use by hand. CI runs on Windows, offscreen.
- AutoCAD reading files written here: it can't be run in CI.
