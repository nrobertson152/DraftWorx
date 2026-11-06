# DraftWorx architecture

## The idea

A program people prefer not because it does more than AutoCAD, but because everyday drafting is easier. It has to win on the moments that happen hundreds of
times a day: pointing at something and getting the point you meant, typing a length, trimming the bit in the way, dimensioning what you're looking at, and
opening someone else's DXF and saving it back without damage.

So the design rests on four commitments:

1. **Snapping is the product.** It's designed first, tested hardest, and not a settings dialog. See `SNAPPING.md`.
2. **The defaults are the design.** A setting is a failure to find the right behaviour. Every dialog has to justify itself.
3. **The drawing can't be damaged by the program.** Immutable values, one way to change anything, atomic and verified saves.
4. **Simple means small underneath too.** Pure Python, standard library below the window, no plugins or scripting until there's a reason.

## Layers

```
draftworx/
  core/            Never imports Qt. Everything testable without a window.
    vec, curves, intersect      geometry: exact, immutable values
    spatial                     the index that keeps queries fast
    shapes, color, model        the drawing and its Transaction (the only way to change it)
    native, safefile            the .draftworx format and how anything is saved
    dxf/                        reading and writing DXF, keeping what isn't understood
    snap                        the snap and tracking engine
    session, commands/          commands as coroutines, selection, typed input
    select, grips, info, layers what's under the cursor, handles, live measurements
    config, recovery, files     settings, unsaved work, opening and saving
    ops/                        trim, extend, offset, fillet, cleanup and the like
    dim                         what a dimension measures, says and draws, and how it follows what it's attached to
    regions, hatch              the area round a point, and what a hatch draws there
    sheets, printing, pdf, svg  the sheet and its border, what a sheet prints, and a PDF or SVG of it with no Qt
    cleanup                     what's drawn twice, has no size, overlaps, or is named and unused
  gui/             PySide6. Thin: it forwards events and paints what the core says.
  integrations/    the family's programs, only through their files and command lines: revisionworx (status, check-out, check-in, BOMs)
  cli              `draftworx`, one JSON object per command for other programs
```

The line under `core` matters most. A command like Trim is a coroutine that asks for what it needs (`yield GetPoint(...)`) and has no idea a window exists. So a
test can run the whole session, pointer moves and all, and assert on the drawing. `gui` only draws what the session reports.

## How a change flows

```
pointer move → snap engine → session: prompt, candidates, preview → canvas repaints the overlay only
click / Enter → the command coroutine resumes → Transaction → Document updates its index → listeners → canvas rebuilds what changed
```

## Decisions

### ADR-1: Pure Python and PySide6, no compiled extensions, no numpy

The directive asks for Python and PySide6, and this keeps installs trivial. The question was whether it can be fast enough, so it was measured before committing.
On this machine, drawing 100,000 random line segments into a 1920x1080 image, in Python 3.13 with PySide6 6.12:

| Approach | Time |
|---|---|
| `drawLines`, QLineF built each frame | 82 ms |
| `drawPath`, one `QPainterPath` in world coordinates under a transform | 24 ms |
| The same, as 240 chunks, whole drawing visible | 24 ms |
| The same, zoomed in 10x, off-screen chunks skipped | 1.9 ms |
| Building those chunks | 51 ms |
| Blitting a cached image | 0.7 ms |

So: geometry goes into paths once, in world coordinates, chunked by style and place, and panning and zooming are just a new transform. A mouse move redraws only
an overlay over a cached image. Building a path from a byte stream, a known trick, turned out slower (75 ms) than plain `moveTo`/`lineTo` (27 ms), so it isn't used.

### ADR-2: Immutable values, one Transaction

Entities are frozen. The Document holds them, and the only way to change one is `with doc.transaction("Move") as t:`. Each change is recorded as
`(table, key, old, new)`. **Undo replays the records backwards, so there's no per-command undo to get wrong.** An exception in a command rolls back everything it
did. Because old and new are shared references, undo history costs little memory. A hypothesis state machine runs random edits, failures, undos and redos, and
checks at every step that the drawing is exactly what it was and that the index matches a fresh build.

Consequences worth knowing:
- Setting something to the value it already has isn't a change, and doesn't make an undo step that visibly does nothing.
- Ids are never reused, even after undo.
- Dirty means "differs from what's saved": undoing back to a save makes the drawing clean again.
- Your current pen (layer, colour) isn't in undo. Going back shouldn't change what you're drawing with.

### ADR-3: Curves have one interface, and their results are exact

`Line`, `Arc`, `Circle`, `EllipseArc`, `Spline`, and `Polyline` as a chain of the first two. Each answers the same questions. Arcs have a signed sweep, so a polyline's
clockwise bulge is an ordinary arc running the polyline's way.

Intersections are closed form where a closed form exists (line, circle, and line with ellipse). Otherwise they're bracketed on a polygon and polished by Newton on the
true curves, so the answer lies on both curves to rounding, not on an approximation. Where curves touch without crossing, the plain equations can't say where, so
that case finds where their directions agree, which is well-conditioned. One tolerance policy (`vec.tol`) scales with coordinate size.

### ADR-4: The index is a loose quadtree that divides only where needed

An object goes in a cell at least its own size, chosen by its centre, and a cell's reach is twice its size, so an object is never split or stored twice. A cell divides only when it
holds more than 16 objects, so a tight cluster of fine detail gets deep cells there and nowhere else. Insert and remove are exact, nothing can go stale, and results come back sorted so
they never depend on insertion order. A polyline is indexed by segment, so a long one doesn't become one big box. Long diagonal lines are the weak case for any box-based index, and it's accepted.

*Tried first:* a stack of hash grids, one per power of two of object size. It's simpler, but a query walked every point object in the drawing (3.7 ms at 100,000 objects), because
zero-size objects landed in cells far smaller than the query window. The quadtree takes 0.03 ms for the same query.

### ADR-5: One drawing, one scale, no paper space

You draw at real size. A drawing has one scale (say 1:50) and a sheet. Text and dimensions are sized for the sheet, and the program works out their model size. Print
is the sheet.

*Given up:* several details at different scales on one sheet, and viewports. That's a real limit for some work, and it's stated in `KNOWN_LIMITATIONS.md`.

*Why:* paper space, viewports and annotative scales are the largest source of confusion in this kind of program (`RESEARCH.md`), and people commonly draw at 1:1 and scale
by a factor anyway. A DXF that has layouts keeps them: they're preserved and written back, and the program says they aren't shown.

Multi-scale sheets can come later as "details": a block placed at another scale, which is simple and already expressible. That's the path to look at if this limit bites,
not viewports.

*As built:* the sheet is a paper and a corner in the drawing's settings (`sheets.py`); its size in the drawing is the paper's times the scale, so it can't disagree with
either. A border and title block is an ordinary block, placed at the scale, with fields; it goes with the sheet when the sheet moves or the scale changes, and so do line
types, so their dashes keep their size on paper. A drawing printed without a sheet is given one first, round what's drawn at the largest standard scale that fits, as its
own undo step, and the program says what it chose.

### ADR-6: `.draftworx` is the home format, and DXF is a working format

New drawings save as `.draftworx`: UTF-8 JSON, one object per line, the same bytes on every computer, so a text diff shows what changed. Writing it is simple enough to
trust with someone's only copy. DXF validity in AutoCAD can't be tested from here, so it isn't the only copy by default.

But a DXF you open, edit and save stays a DXF. No conversion step, no "export". Entities you didn't change go back out as they came in, and anything we don't understand is
kept. See `DXF.md`. *Challenged and kept:* DXF as the only format would be simpler to explain, but a subtle validity problem would then threaten the only copy of someone's
work, and parsing it is several times slower.

### ADR-7: Share formats and looks with the family, not code

OpenSchematix's window is **Tkinter**, not PySide6 as the directive assumed. What can be shared is the design: colour tokens, themes, icon language, command names and
keys. OpenMeridian already carries those into PySide6, so DraftWorx does the same (`gui/theme.py`, `gui/icons.py`). No shared library: each program has its own release
cycle, and a shared library would couple them for the sake of a few hundred lines.

OpenSchematix's DXF code is a deliberately small R12 writer and exploding reader, which is right for it. DraftWorx needs the opposite: keep blocks and dimensions alive and
preserve what it can't read. So it's new code, and the two are tested against each other's files.

Other programs are reached the way the family already does it: `revisionworx link` (one JSON object per command), documented file formats (`.osym`), and DraftWorx's own
`draftworx` command for theirs. If RevisionWorx isn't installed, the integration simply isn't there.

### ADR-8: The window paints; it doesn't use QGraphicsView

QGraphicsView costs a Python object per item, and its hit-testing and selection would duplicate the core's. A plain widget with a cached image, an overlay and chunked paths is
less code and much faster at this scale. Raster, not OpenGL: it works under remote desktop and in CI, and the measurements above say it's enough.

### ADR-9: Horizontal and vertical lines aren't smoothed

Smoothing a 1-pixel line that falls between two rows of pixels gives a 2-pixel grey smear, which is most of what a drawing is made of. So a chunk has two paths: exactly horizontal and vertical lines go in one drawn without smoothing, and everything else in one drawn with it. Sharp where it can be sharp, smooth where it's curved or diagonal. The cost is none in time and two paths per chunk.

### ADR-10: Text is measured with a fixed table

Layout (extents, dimension text placement) uses Helvetica's published widths, so results don't depend on what fonts a computer has. What's drawn uses a real font, so positions
can differ slightly from a program with a different font. SHX fonts aren't read.

### ADR-11: A DXF is kept as its own text

Reading a DXF into a model and writing the model back out loses whatever the model doesn't hold, and no model holds everything. So every object, table entry and block keeps the exact text it came from, alongside what it means, and the rest of the file is kept as it was. Writing uses that text for as long as the thing is still what the text says, which is a comparison of values and can't go stale: undoing a change makes the same bytes come back, without anything having to remember.

It costs memory, about the size of the file, and it means two descriptions of each object that have to be told apart. In exchange, "we didn't damage your file" becomes something a test can check exactly, on any file, including damaged ones and ones from programs we've never heard of. *Challenged and kept:* parsing unknown things into generic tag lists, as ezdxf does, handles more editing of them but reformats numbers and reorders tags, so a file nobody changed would still come back different.

### ADR-12: An offset is made exact first, and its corners are made sharp afterwards

A polyline's offset is each piece moved over, with every corner on the outside rounded round the vertex. Then each piece keeps exactly the parts that are at least the distance from the whole of the original. That's decided by measuring against the pieces near it, nearest first, cutting it wherever it meets something that's exactly the distance from one of them and testing between the cuts, so it holds even where the path crosses itself, comes back to the same point or has arcs so flat they're nearly lines. Only after that do straight sides carry on to meet in sharp corners, and only where the corner they make stays far enough away, which carrying on mostly does, because it moves away from the corner it goes round.

*Challenged:* making the sharp corners first, as most programs do, and then removing the loops this makes. That's simpler to write, but a corner carried on into the wrong place is then hard to tell from a loop, and a stretch closer than the distance was left behind in random tests. Here random tests check, at many points along every piece, that nothing is closer than the distance and that nothing far enough is missing.

### ADR-13: A dimension holds what it measures, and follows it inside the Transaction

A dimension is a few points and where it goes. Its lines, arrows and text are worked out from those and its style each time they're needed, so what it shows can't disagree with what it measures, and a style change redraws every dimension that uses it. What it measures from can be attached to another object's point by name (an end, a centre, a polyline's corner), and the document keeps an index from each object to the dimensions attached to it.

When a Transaction finishes, any dimension attached to something it changed is updated before the step is recorded, so the move and the dimension's following it are one undo step, and undo can't separate them. A dimension changed in the same step isn't moved again; it lets go of whatever it no longer sits on, which is how moving or copying a dimension on its own leaves it unattached, and moving it with what it measures keeps it attached, without either command knowing about dimensions.

*Challenged:* AutoCAD's way, a separate associativity object watching the geometry through reactors. That's more general (it can watch an intersection), but it's a second place that has to be kept right on every change, undo and file trip. *Given up:* DXF files don't carry the attachment (DXF.md).

### ADR-14: A hatch finds its area with a map of the curves round the click, and follows by matching areas

Clicking inside an area cuts the curves near the click where they meet, and walks the pieces with the area always on the left, turning as sharply left as possible at every corner. Each walk goes round one area. The area round the click is the smallest that holds it, less the islands directly inside it. Walks that go up and back down the same piece are a line poking in, or a bridge to an island, and those pieces are dropped. Curves are used whole if any of them is near, so the map is exact for what it has, and if the area reaches past what was looked at, more is looked at.

A hatch is its loops and its pattern. The pattern's lines are worked out from them each time, like a dimension's, and end exactly on curved edges. When the objects round it change, it becomes the area they now make that's bounded by most nearly the same objects, inside the same Transaction as the change (ADR-13), so it follows a stretch and a move, holes included.

*Challenged:* following the point it was made from, as the first version did. A boundary moved away from that point loses it, and moving a hole changes nothing a point can see. *Challenged:* AutoCAD's associativity, which records which objects make which loop. It's more exact about a loop made of part of an object, and is a second record to keep right on every change, undo and file trip.

### ADR-15: One walk of the drawing prints it, for a PDF and for a printer

`printing.py` turns a sheet into strokes, fills, dots and text in millimetres on the paper, through one transform, and hands them to a page. The PDF writer (`pdf.py`,
standard library only), the SVG writer (`svg.py`, the same) and the window's printer (`gui/printing.py`, Qt) are three pages, so what they print can't differ, and the PDF, which is what's tested, stands for
both. The PDF's numbers are points to a ten-thousandth, so a length on paper is the drawing's divided by its scale to within 36 nanometres; it has no date in it, so the
same drawing gives the same file.

*Challenged:* printing through Qt for PDFs too (QPdfWriter). It needs Qt for `draftworx pdf`, and its output can't be checked by arithmetic. *Given up:* fonts: text is
Helvetica, which every PDF reader has and which text is measured with (ADR-10); a character it can't show prints as ? and is counted.

### ADR-16: Cleanup works out everything first, and changes it in one Transaction

`cleanup.plan` decides what goes, what's joined and which names are unused, without changing anything, so the command can show it; `cleanup.apply` makes exactly those
changes in one Transaction, so one undo puts all of it back. Joined lines keep ends that were really there, never a worked-out point. A name from a DXF that the file's other
parts name or point at by handle stays, because writing it back without it would leave the file pointing at nothing.

### ADR-17: RevisionWorx through its command line: one sentence and one next step

DraftWorx asks `revisionworx link` (its documented interface, one JSON object a call) what a drawing is, and checks drawings in and
puts them into projects with it. Checking out and undoing that aren't in `link`, so they use `revisionworx --json doc checkout|cancel`, as
FluidWorx does. Nothing imports RevisionWorx, and nothing reads a workspace, so its locks and rules are the only ones there are.

The window shows RevisionWorx's own sentence for the drawing and one button for the next step, as RevisionWorx's document panel does:
Check out when it's available, Check in when it's yours or changed. Asking happens away from the window's thread, so a slow shared folder
can't stall drawing, and the questions are numbered, so an answer that arrives after a newer one was asked for is dropped. A check-in saves
first, and the parts list, if asked, goes as a BOM document beside the drawing, which is how it reaches OpenMeridian.

The one rule DraftWorx adds is about losing work: a file someone else has checked out is their working file, so it isn't saved over.
Everything else (saving without a check-out, checking in) RevisionWorx already handles and records.

*Challenged:* checking out automatically on the first change, as some PDM add-ins do. It leaves checkouts behind from drawings that were
only looked at, and says nothing a button can't. *Challenged:* making the file read-only until it's checked out. RevisionWorx doesn't, and
a drawing opened for a quick measurement would refuse to let you draw a construction line.

## What's measured, and what it should stay under

`tools/bench.py` reports these, and CI runs a quick version. They're from this development machine, not a promise about yours.

| At 100,000 lines | Target |
|---|---|
| An index query near the cursor | under 2 ms (measured 0.03 ms) |
| A snap query at the cursor | under 2 ms (measured 0.4 ms at drawing scale, 0.07 ms zoomed in, 6.5 ms with thousands under the aperture) |
| Read a `.draftworx` | under 3 s (measured 1.8 s) |
| Write a `.draftworx` | under 5 s (measured 0.6 s) |
| Read a DXF of the same | under 5 s (measured 2.8 s, 21 MB) |
| Write a DXF of the same | under 5 s (measured 0.8 s, or 0.3 s for one that hasn't changed) |
| A mouse move, with what it paints | under 16 ms (measured 1 ms, offscreen) |
| Offset a polyline of 10,000 pieces | measured 3.4 s round a ring, 1.8 s along a fine zigzag; an offset that's being previewed gives up after 0.15 s |
| Find the room round a click, among 100,000 lines | measured 56 ms; a hatch being previewed gives up after 0.15 s |
| A PDF of all of it on A0 | measured 1.5 s (3.8 MB) |
| Work out what cleanup would do | measured 0.9 s |
| A pan with everything in view | under 50 ms (measured 30 ms) |
| Repaint zoomed in | measured 13 ms |
| First paint after opening | measured 235 ms |

## Failure modes

| What goes wrong | What happens |
|---|---|
| Crash or power cut during a save | The old file is intact. The new content only takes the name after it's on disk and checked. |
| Disk full, no permission, or a folder that's gone | A message. The old file is intact. |
| What reached the disk differs from what was written | Detected by reading it back. Nothing replaced. |
| The written file wouldn't load back | Detected before it takes the name. A message, and nothing replaced. |
| File changed by someone else since it was opened | The save stops and says so (`ChangedOnDisk`). |
| Read-only file | Respected, even where the operating system would let a rename through. |
| Malformed or truncated file | A message saying where. Nothing is built from it. |
| A DXF that holds what we don't understand | Kept and written back, and we say what it is. |
| A DXF that describes more than can be drawn, such as blocks in blocks in arrays | Drawn in part, with a limit on each object and on the drawing, and said so. Clicking and snapping have limits of their own. |
| Newer format version | Says to update. The file isn't touched. |
| A command fails part way | The whole Transaction rolls back. |
| A crash or power cut while editing | A copy of unsaved work is kept every minute in the program's own folder, and offered on the next start. It's never written over your file. |
