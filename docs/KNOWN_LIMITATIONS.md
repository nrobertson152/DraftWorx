# Known limitations

DraftWorx is deliberately small. A simple result mustn't look like an exact one, so here's what it doesn't do.

## Not there yet

This is early. You can draw lines, polylines, rectangles, circles, arcs, ellipses, points and text, select them, drag their handles, and move, copy, rotate, scale, mirror and erase them. Nothing else yet: no DXF, trim, offset, dimensions, hatch, block creation or printing. See `ROADMAP.md`.

## By design

- **2D only.** Z values in a DXF aren't used.
- **No DWG.** DXF is the exchange format.
- **No paper space or viewports**, so one scale per sheet (`ARCHITECTURE.md`, ADR-5). A DXF that has them keeps them, but they aren't shown or editable.
- **No parametric constraints.**

## Display

- Paragraph text shows its words, wrapped to its width. Fonts, colours and sizes set inside the text aren't shown yet.
- SHX fonts can't be read, so text in them is drawn in a plain sans.

## Accuracy

- Coordinates are IEEE doubles. Two points closer than a billionth of a unit (or a trillionth of their size, for large coordinates) count as the same.
- Where curves touch without crossing (tangency) and neither is a line or circle, the contact point is found numerically, to about a millionth of the curves' size.
- Text extents use a fixed table of Helvetica's widths, so they can differ a little from what's drawn, and from another program's fonts.
- NURBS and splines above degree 3 draw as curves matched to a hundred-thousandth of their size. Polynomial cubic splines draw exactly.

## Not verified

- How it feels. It's only been driven by tests and looked at in screenshots. Nobody has drawn with it by hand, on Windows or Linux.
- Native Windows use by hand. CI runs on Windows, offscreen.
- AutoCAD reading files written here: it can't be run in CI.
