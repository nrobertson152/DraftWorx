# Changelog

## Unreleased

- Geometry kernel: lines, arcs, circles, ellipses, splines and polylines, exact intersections, a spatial index.
- The drawing: immutable entities, layers, styles and blocks, one Transaction for every change, undo and redo.
- The `.draftworx` format, with atomic, verified saves.
- The snap engine: every kind of point ranked by one rule, exact results, tracking by pausing, polar lines and locks.
- The window: a fast canvas, draw and modify commands, typing anywhere, selection, grips, layers, a panel of live measurements you can type over, Ctrl+K to find anything.
- A copy of unsaved work is kept every minute and offered after a crash.
- Changing things: trim and extend by pointing at the piece (or dragging across several), with what would go shown first; offset, exact for lines, arcs, circles and polylines; fillet and chamfer, of two objects or a polyline's corners; array in rows or round a point; break, join, explode, stretch, and match one object's look to another's.
- Circle crossings stay exact when one of the circles is huge, as a polyline piece with a tiny bulge is.
- One Dimension tool that reads what you point at: a line's length across, up or along, a circle's diameter, an arc's radius, the angle between two lines, or between any two points. A dimension follows what it measures, in the same undo step. DXF dimensions open as dimensions that can be changed, and dimensions go out as DXF dimensions with blocks that draw them.
- Hatch: click inside an area and it's filled, solid or with a pattern sized for the sheet. Pointing shows what a click would fill, or where the area is open; small gaps can be bridged. Islands are left out. A hatch follows the objects round it when they're moved or stretched, in the same undo step. DXF hatches open as hatches that can be changed, and hatches go out as DXF hatches, or as lines and solids in R12.
- A dimension or hatch copied with what it's attached to is attached to the copy.
- DXF as a working format, R12 to 2018, text and binary. A file nobody changed comes back byte for byte, and what's changed is all that changes. What can't be edited yet, such as dimensions and hatches, is shown as far as it can be, kept, and said so. Export as R12 for machines that need it.
