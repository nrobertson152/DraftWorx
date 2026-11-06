# Roadmap

Each stage ends with something that works and is tested on Windows and Linux in CI. Version numbers and releases are the Director's to decide.

| Stage | What | Status |
|---|---|---|
| 0 | Repository, rules, CI, architecture | done |
| 1 | Geometry kernel, document, undo, `.draftworx`, safe saves | done |
| 2 | Snap and tracking engine | done |
| 3 | Window, canvas, commands, typed input, selection and grips, layers, properties, command search, crash recovery | done |
| 4 | DXF read and write | done |
| 5 | Modify: trim, extend, offset, fillet, chamfer, array, break, join, explode, stretch, match | done |
| 6 | Annotation: text, one Dimension tool, associativity, hatch, blocks with attributes | dimensions and hatches done |
| 7 | Print and PDF, sheets and templates, automatic cleanup | |
| 8 | RevisionWorx, OpenSchematix and OpenMeridian | |

## What each stage has to prove

**2, Snap.** Real points beat worked-out points beat tracking. The same inputs always give the same answer. Tab reaches every candidate. Results are exact at any zoom. A
query takes under 2 ms at 100,000 entities.

**3, Window.** You can draw, select, drag grips, undo, save and open by hand, and all of it is driven through real Qt events in tests. Mouse moves stay smooth at 100,000 entities.

**4, DXF.** Reads R12 to 2018, ASCII and binary. An unedited entity comes back byte for byte in the tags that matter, and unknown entities and objects survive. What we write
passes ezdxf's audit and is read correctly by at least one other independent program in CI. AutoCAD can't be run here, and that stays stated.

**5, Modify.** Trim and offset produce geometry that's exactly right, by property tests: nothing on an offset is closer than the distance, nothing that's far enough is left
out, and a trim removes only the piece you pointed at. Offsetting a polyline of 10,000 pieces takes seconds, not minutes.

**6, Annotation.** A dimension follows the geometry it's attached to, inside the same undo step. Dimensions and hatches round-trip through DXF. A block's contents can be snapped to
exactly, including when it's been stretched.

**7, Output.** A PDF at 1:50 measures right on paper. Cleanup previews what it'll change and is one undo step.

**8, Family.** Check-out and revision state come from the real `revisionworx` command in CI. A DXF written here opens in OpenSchematix, and one it writes opens here. A parts list
reaches OpenMeridian through RevisionWorx.

## Deliberately not planned

3D. DWG. Paper space and viewports (see ADR-5). Parametric constraints. Plugins and scripting (a stable `draftworx` command comes first, then we'll see). Cloud anything.

## Possibilities, once the above is solid

Details at another scale on a sheet. Drawing compare. Isometric snap angles. Shared component catalogue with OpenSchematix and OpenMeridian. Read-only guard on drawings that aren't checked out.
