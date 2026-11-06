# Known limitations

DraftWorx is deliberately small. A simple result mustn't look like an exact one, so here's what it doesn't do.

## Not there yet

This is early. You can draw lines, polylines, rectangles, circles, arcs, ellipses, points and text, select them, drag their handles, move, copy, rotate, scale, mirror and erase them, trim, extend, offset, fillet, chamfer, array, break, join, explode and stretch them, match one's look to another's, dimension them, and open and save DXF as well as `.draftworx`. Nothing else yet: no hatch, block creation or printing. See `ROADMAP.md`.

## Changing things

- **Trim and extend** have no cutting edges to choose: everything visible cuts, including what's inside blocks and what came from a DXF and can't be changed here. Those cut but can't be trimmed themselves. A circle needs two cuts to open; with one, all of it goes.
- **Offset** of a polyline is exact. Its straight sides carry on until they meet, as you'd draw it by hand, unless that's more than ten times the distance, would come closer to the original than the distance, or would cross another corner made like that. Then the corner is rounded, exactly the distance from it. A corner next to an arc is rounded too. Where the polyline crosses or touches itself, what's left is everything at least the distance from all of it.
- **Offset** of an ellipse or a spline isn't an ellipse or a spline, so it's a spline fitted to the true offset, and the program says how closely.
- While the pointer moves, an offset that takes more than a moment to work out, as one of a polyline with thousands of pieces can, isn't previewed. Clicking still makes it.
- **Fillet** works on lines, arcs and circles and the corners of a polyline. **Chamfer** works on straight lines and a polyline's straight corners. Two different polylines can't be filleted together.
- **Join** joins lines, arcs and open polylines that meet end to end. Ellipses and splines aren't joined.

## Dimensions

- One tool measures lengths (across, up, along or at an angle), radii, diameters and angles. There are no ordinate, arc-length, baseline or chained dimensions, and no tolerances or alternate units.
- A dimension follows what it's attached to: the ends of a line or arc, a polyline's corners, a circle's centre and size. Not where two things cross, which isn't one object's point, and not a point on a curve chosen freely. Moving or copying a dimension on its own lets go, as does dragging one of its points.
- What it's attached to is kept in `.draftworx` files only, not in DXF (`DXF.md`).
- Dimension styles from a file are used as far as their sizes, rounding, units and text placement go. Their arrowhead blocks, colours, text boxes and fit options aren't: arrows are filled, or ticks.
- When there's no room between the arrows they go outside, but the text stays in the middle, where it can overlap them.

## DXF

What's kept, what's shown and what isn't are in `DXF.md`. The short version: nothing in a file is lost by opening and saving it, but dimensions, hatches, tables, leaders and 3D objects from a file can't be changed yet, hatches show only their outlines, and some objects aren't shown at all. The program says which when it opens the file.

## By design

- **2D only.** Heights in a DXF aren't shown. An unchanged object keeps its height, and a changed one loses it.
- **No DWG.** DXF is the exchange format.
- **No paper space or viewports**, so one scale per sheet (`ARCHITECTURE.md`, ADR-5). A DXF that has them keeps them, but they aren't shown or editable.
- **No parametric constraints.**

## Display

- Paragraph text shows its words, wrapped to its width. Fonts, colours and sizes set inside the text aren't shown yet.
- SHX fonts can't be read, so text in them is drawn in a plain sans.
- Line types show their dashes and gaps. Shapes and text inside a line type aren't drawn.
- Blocks placed so many times over that one object would be more than a million pieces, or a drawing more than five million, are drawn in part, and the program says so. They're all still in the file.

## Accuracy

- Coordinates are IEEE doubles. Two points closer than a billionth of a unit (or a trillionth of their size, for large coordinates) count as the same.
- A polyline piece whose bulge is under a hundred-millionth counts as straight. It's that far from straight at most a hundred-millionth of its length.
- Where curves touch without crossing (tangency) and neither is a line or circle, the contact point is found numerically, to about a millionth of the curves' size.
- Text extents use a fixed table of Helvetica's widths, so they can differ a little from what's drawn, and from another program's fonts.
- NURBS and splines above degree 3 draw as curves matched to a hundred-thousandth of their size. Polynomial cubic splines draw exactly.

## Not verified

- How it feels. It's only been driven by tests and looked at in screenshots. Nobody has drawn with it by hand, on Windows or Linux.
- Native Windows use by hand. CI runs on Windows, offscreen.
- AutoCAD reading files written here: it can't be run in CI. ezdxf, GDAL and LibreCAD do read them.
