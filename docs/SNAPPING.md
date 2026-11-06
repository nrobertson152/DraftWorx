# Snapping

This is what the program promises when you point at something. `tests/test_snap.py` holds it to these. Why it's this way is in `RESEARCH.md`.

## What you get without setting anything

Everything's on. You don't choose snap modes: you point, and the program offers the point you're most likely to mean, says what it is, and marks it.

| What | Where | Marker |
|---|---|---|
| Endpoint | Ends of lines, arcs, polyline vertices | Square |
| Intersection | Where two things cross, including a polyline crossing itself | X |
| Midpoint | Middle of a line, arc or polyline segment | Triangle |
| Center | Of a circle, arc or ellipse. You don't have to touch the curve first | Circle |
| Geometric center | Of a closed polyline, arcs included | Star |
| Quadrant | The 0, 90, 180 and 270 degree points of a circle or arc | Diamond |
| Point, Insertion | Point objects, and where text and blocks are anchored | Crossed circle, double square |
| Perpendicular, Tangent | When there's a point to measure from | Right angle, circle and line |
| On | On an object, nowhere in particular, when nothing above is near | Hourglass |
| Alignment | On an alignment line, or where lines meet each other or an object | Dotted line and X |

Inside a block, its contents count like anything else, exactly, however it's been scaled or stretched.

## How it chooses

Each candidate scores its distance from the cursor in pixels, plus a handicap for its kind. Lowest wins.

| Handicap (px) | Kinds |
|---|---|
| 0 | Endpoint, Intersection, Point, Insertion, Center |
| 1 to 2 | Geometric center, Midpoint, Quadrant |
| 5 | Perpendicular, Tangent |
| 7 | Alignment lines meeting, or crossing an object |
| 10 | On one alignment line |
| 14 | On an object |

What this means:
- **A point the geometry really has beats a worked-out one whenever they're about equally close.** An alignment can't take the place of an endpoint right beside it.
- **A worked-out point still wins where the cursor is clearly on it.** Perpendicular 1 px away beats an endpoint 12 px away.
- **"On" never beats anything real in reach.** It's what's left.
- Points are offered within 14 px, alignment lines catch within 7 px, and "On" within 7 px.

**It doesn't flicker.** The current choice gets a 4 px head start, and a real point stays chosen out to 18 px once it's been chosen. A shaky hand doesn't flip it, and a clear move does.

**Tab goes through everything on offer**, in order, when the wrong thing is chosen.

**It's repeatable.** The same drawing, cursor and history give the same answer, whatever order things were drawn in.

## The point you get is exact

A snap's coordinates come from the geometry, never from the screen. The screen only decides what's near enough to offer. So a shared endpoint is the stored number, an intersection is exact to rounding at any zoom, and a point on a horizontal alignment line has exactly the y of what it's aligned to.

A free point, with nothing to snap to, has no exact answer. It's rounded to the round number (1, 2 or 5 times a power of ten) that's about one pixel at this zoom. Nothing looks different, and your lengths read 125.5 rather than 125.4837261.

## Tracking

There's no tracking mode. **Pause on a point and it's picked up** (0.35 s). Alignment lines then appear when the cursor lines up with it, horizontally, vertically, and along what the point was on (a line's extension, an arc's tangent).

- Where two lines meet, or a line crosses an object, is a candidate, and it's exact.
- **Type a number while on a line**, and the point goes that far along it, from the point it came from. No `FROM` command.
- Pause on it again to drop it. Up to 7 are kept, and the oldest goes. Clicking a point clears them.
- Passing over a point doesn't pick it up.

**From the last point**, polar lines are always offered (every 45 degrees unless you choose otherwise), and horizontal is exactly horizontal.

**Pause on an edge while drawing**, and lines parallel and perpendicular to it are offered from your last point.

## Locks

- **Ortho (F8), or hold Shift:** the point stays on the horizontal or vertical from your last point. Pointing at something real then gives the place on that line in line with it, so "draw along here until it's below that corner" takes no construction lines.
- **Type a length:** the same idea on a circle. **Type an angle (`<30`):** the same on a line. Type both and it's fixed.

## Getting out of the way

Hold Ctrl for no snapping at this moment. F3 turns snapping off until you turn it back on. Individual kinds can be switched off, but you shouldn't need to.

## What it costs

At 100,000 objects on the development machine, a mouse move costs about 0.4 ms at drawing scale, 0.07 ms zoomed in, and 6.5 ms zoomed right out (`tools/bench.py`). Intersections are remembered between moves until the drawing changes.
