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
- Blocks with fields: make a block of what's selected, place copies and fill in their fields, change the values later, define a field (ATTDEF), and bring another drawing in as a block. A field on its own shows its tag.
- The parts list a drawing's blocks make, from fields tagged PART_NO and the like, written as a BOM RevisionWorx reads.
- `draftworx version`, `info`, `parts` and `convert`, for other programs: one JSON object out, like `revisionworx link`.
- Sheets: choose a paper and the drawing's scale (1:50, 2:1, 1/4"=1'), and where the sheet goes, with Sheet. A border and title block with fields comes with it if you want one, and goes with the sheet.
- Templates: a new drawing on ISO A4 to A0, Letter, Tabloid or ANSI C to E, with its border, layers and line types.
- Print and Export PDF, to scale: a 1000 mm line at 1:50 is 20 mm on paper, checked by arithmetic, by another PDF reader and by measuring the printed page. A drawing printed without a sheet is given one round what's drawn. `draftworx pdf` does the same for other programs.
- Export SVG: the sheet in millimetres, to scale, with real text, for Inkscape, browsers and laser cutters' software. `draftworx svg` for other programs.
- Cleanup: what's drawn twice, what has no size, lines that overlap, and names nothing uses, shown first and taken out in one undo step.
- Bottom-aligned text sits on its point, as other programs place it. It was drawn a little low.
- Where two curves cross twice closer together than can be told apart, the same point is found whichever is first.
- RevisionWorx: the status strip says what RevisionWorx says about the drawing, with its one next step as a button (Check out, Check in…). Checking in saves first, and can take the parts list along as a BOM next to the drawing, which OpenMeridian imports once it's released. File ▸ RevisionWorx undoes a check-out and adds a drawing to a project. Someone else's checked-out file isn't saved over.
- DXF as a working format, R12 to 2018, text and binary. A file nobody changed comes back byte for byte, and what's changed is all that changes. What can't be edited yet, such as dimensions and hatches, is shown as far as it can be, kept, and said so. Export as R12 for machines that need it.
