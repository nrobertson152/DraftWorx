# Known limitations

DraftWorx is deliberately small. A simple result mustn't look like an exact one, so here's what it doesn't do.

## Not there yet

This is early. You can draw lines, polylines, rectangles, circles, arcs, ellipses, points and text, select them, drag their handles, move, copy, rotate, scale, mirror and erase them, trim, extend, offset, fillet, chamfer, array, break, join, explode and stretch them, match one's look to another's, dimension and hatch them, make and place blocks with fields to fill in, put the drawing on a sheet with a border, print it and make a PDF of it to scale, clean it up, and open and save DXF as well as `.draftworx`. See `ROADMAP.md`.

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

## Sheets and printing

- A drawing has one scale and one sheet (`ARCHITECTURE.md`, ADR-5), on A4 to A0, Letter, Legal, Tabloid or ANSI C to E. There are no other paper sizes, and one sheet is one page: several sheets are several drawings.
- Printing prints the sheet on its paper, to scale. There's no "fit to page", which would look like a scale and not be one. A printer that doesn't have the sheet's paper may shrink or cut it: choose a paper it has with Sheet.
- Text you write keeps its size in the drawing, as in other CAD programs, so it's smaller on paper at a smaller scale. Dimensions and hatch patterns are sized for the sheet, and change with its scale.
- When the scale changes, the line type scale changes with it, so dashes keep their size on paper. There's no setting for it by hand: it comes from a DXF's LTSCALE, or 1. A DXF whose LTSCALE was set for its print scale while its dimension scale (DIMSCALE) was left at 0 or 1 gets dashes that much too long when it's first put on a sheet.
- A DXF keeps the scale (as DIMSCALE and LTSCALE) and the border, which is a block, but has nowhere to say which paper the sheet is or where it goes. Opened again, a drawing with a border gets its sheet back from it, when it's printed or put on a sheet; one without is given a sheet round what's drawn.
- All text prints in Helvetica, which every PDF reader has and which text is measured with. SHX and TrueType fonts named by a style aren't used. A character Helvetica can't show (outside Western European) prints as ?, and the program says how many there are.
- Line weights print as set, and the default weight as 0.25 mm. Colours print as they are, darkened where they'd be too pale to see on white, or everything in black.
- A PDF is one page of vectors and searchable text. It has no layers, links or bookmarks.
- The same drawing gives the same PDF on the same computer. Another computer's zlib may compress it to different bytes that draw the same.
- There's one border and title block design, about ISO 7200's, filled in like any block's fields. To use your own, make a block with the name the program gives its own (`Sheet A3 across`), drawn in millimetres from the paper's lower-left corner, and the program uses yours.
- Templates come with a fixed set of layers and line types. A drawing of your own can be used as a template by saving a copy of it.

## Cleanup

- Cleanup takes out what's drawn twice (the same object, the same look, a line either way round), objects with no size and empty text, and joins straight lines that overlap or meet end to end on one line and look the same. It drops layers, blocks, line types and styles that nothing uses. It shows all of it first, and one undo puts it back.
- It doesn't join arcs or polylines, take out a line lying along a polyline's side, or join lines that are only nearly in line.
- What a dimension or hatch is attached to is never the copy that goes, and isn't joined.
- Layer 0, Defpoints, the current layer and style, and Standard stay. So does anything from a DXF file that the file names or points at elsewhere, such as a layer used in paper space or a dimension style's arrow. Some files from other programs list every style in a way that counts, so those stay.
- A style used only by another style that goes is found by a second cleanup, not the first.

## RevisionWorx

- DraftWorx uses RevisionWorx's command line: `revisionworx link` for what it says about a file, checking in and adding a drawing, and `revisionworx --json doc checkout|cancel` for checking out and undoing it. RevisionWorx 1.0 or later. Its rules, locks and records apply exactly as they would by hand.
- A drawing is checked out when you ask, or at check-in if nobody has it. Changing a drawing doesn't check it out by itself, and saving a drawing that isn't checked out to you makes it "changed without a check-out", as RevisionWorx calls it, until it's checked in. What it doesn't do is save over someone else's checked-out working file: that's refused, and Save a copy offered.
- That refusal needs RevisionWorx's answer. If it doesn't answer when you save, what it said last is used; if it never said anything, the save goes ahead.
- What RevisionWorx says is asked again on opening, saving, checking in or out, and coming back to the window. A change made in RevisionWorx shows when you come back to DraftWorx, not while you're in it.
- The title block's fields aren't filled in from RevisionWorx, or the other way round. Adding a drawing takes its number and title from the title block, if it has one, as the suggestion.
- The parts list goes as a BOM CSV document numbered as the drawing is with `-BOM`, in the project's BOM folder if it has one. It names the drawing's revision. OpenMeridian imports it once it's released in RevisionWorx, not before.
- Tested with the real RevisionWorx and OpenMeridian in CI, on one computer. Not with a workspace in a shared folder between real computers, which RevisionWorx itself hasn't verified yet.

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
