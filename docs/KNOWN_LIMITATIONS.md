# Known limitations

DraftWorx is deliberately small. A simple result mustn't look like an exact one, so here's what it doesn't do.

## Not there yet

This is early. You can draw lines, polylines, rectangles, circles, arcs, ellipses, points and text, select them, drag their handles, move, copy, rotate, scale, mirror and erase them, trim, extend, offset, fillet, chamfer, array, break, join, explode and stretch them, match one's look to another's, dimension and hatch them, make and place blocks with fields to fill in, and open and save DXF as well as `.draftworx`. Nothing else yet: no printing. See `ROADMAP.md`.

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
- What it's attached to is kept in `.draftworx` files only, not in DXF (`DXF.md`). Copied together with what it measures, a copy is attached to the copies.
- Dimension styles from a file are used as far as their sizes, rounding, units and text placement go. Their arrowhead blocks, colours, text boxes and fit options aren't: arrows are filled, or ticks.
- When there's no room between the arrows they go outside, but the text stays in the middle, where it can overlap them.

## Hatches

- A hatch fills the area round where you click: the smallest area closed round it by lines, arcs, circles, ellipses, splines, polylines, solids and what blocks draw. Text, dimensions, points and other hatches aren't boundaries, so text inside an area is hatched over.
- An island inside the area is left out, and what's inside an island isn't hatched by the same click (AutoCAD's "outer" style). Click inside it to hatch it too.
- An area has to be closed. Gaps up to a width you give (G) can be closed with straight bridges; otherwise the program shows where it's open.
- Finding an area by clicking stops past 20,000 curves round the point, and says so.
- A hatch follows the objects round it when they're moved or stretched, as long as they still close round an area bounded by mostly the same objects. Trim, break or erase one of them, or move the hatch on its own, and it lets go and stays as it is.
- There are a dozen patterns built in, named as other programs name them. Pattern files (`.pat`) can't be loaded, but a pattern a DXF file defines is kept and drawn. Gradient fills from a file are kept and shown by their outlines, and can't be changed.
- A mirrored hatch keeps its pattern the way it was, as AutoCAD does by default. Its boundary is mirrored.
- Where a boundary is curved, a pattern's lines end exactly on it. A solid fill's edge is drawn as fine chords, as every curve is.
- Hatches aren't snapped to; the objects round them are.

## Blocks

- Block makes a block of what's selected, with the point you pick as its base, and puts a copy where they were. Insert places a copy, sized and turned, and asks for each field (attribute). A copy's fields can be changed in the panel or with Change values (ATTEDIT).
- There's no block editor. To change what a block holds, explode a copy, change it, and make a block of the same name: it replaces the block everywhere it's placed.
- Insert a drawing brings another drawing in as a block named after it. Its layers, styles and blocks come with it, except where this drawing has its own of the same name, which win. What's in it that can't be changed here, such as a 3D solid from a DXF, can't be moved to another drawing either, so it's left out, and the program says how many.
- Fields are text. AutoCAD's fields that fill themselves in (`%<\AcVar ...>%`) and dynamic blocks' parameters are kept in a DXF but not worked out.
- A parts list counts placed blocks with a part number field, one level deep (`COMMAND_LINE.md`). It doesn't look inside blocks inside blocks.

## DXF

What's kept, what's shown and what isn't are in `DXF.md`. The short version: nothing in a file is lost by opening and saving it, but tables, leaders, gradient fills and 3D objects from a file can't be changed yet, some are shown only by their outlines, and some aren't shown at all. The program says which when it opens the file.

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
