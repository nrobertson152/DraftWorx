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
    ops/                        trim, extend, offset, fillet, cleanup and the like
    dim, hatch, display         what dimensions and hatches draw
  gui/             PySide6. Thin: it forwards events and paints what the core says.
  integrations/    RevisionWorx, OpenSchematix, OpenMeridian, through their files and command lines
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

### ADR-9: Text is measured with a fixed table

Layout (extents, dimension text placement) uses Helvetica's published widths, so results don't depend on what fonts a computer has. What's drawn uses a real font, so positions
can differ slightly from a program with a different font. SHX fonts aren't read.

## What's measured, and what it should stay under

`tools/bench.py` reports these, and CI runs a quick version. They're from this development machine, not a promise about yours.

| At 100,000 lines | Target |
|---|---|
| An index query near the cursor | under 2 ms (measured 0.03 ms) |
| A snap query at the cursor | under 2 ms |
| Read a `.draftworx` | under 3 s (measured 1.8 s) |
| Write a `.draftworx` | under 5 s (measured 0.6 s) |

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
| Newer format version | Says to update. The file isn't touched. |
| A command fails part way | The whole Transaction rolls back. |
| A crash while editing | Recovery file, offered on next start (planned, see `ROADMAP.md`). |
