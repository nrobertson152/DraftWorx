# DXF

A DXF you open, change and save stays a DXF, and only what you changed changes.

## What's kept

Reading keeps two things for every object: what it means, and the exact text the file had for it. When saving back to the version the file came from:

| It's... | So what's written is... |
|---|---|
| unchanged | the file's own text, byte for byte |
| only restyled (layer, colour, line type, weight, scale) | the file's own text with those lines changed |
| a changed shape | a new description of it, keeping its handle, its owner, whatever is watching it and its extended data |
| new | a new description, with a handle no other object in the file has |
| not understood | the file's own text, always |

The same goes for everything around the objects. Header variables are only touched when the drawing's own setting changed, and table entries stay in the file's order. A changed layer keeps what we don't model of it, such as its material and plot style. Tables and sections we don't read at all (`CLASSES`, `OBJECTS`, `VPORT`, `THUMBNAILIMAGE`, whatever comes next) go back as they came, as do paper space, line endings, a byte order mark, comments and anything after the end of the file.

So a text DXF nobody changed comes back **byte for byte**, whichever program wrote it. The tests check that on every version and on real files (`tests/data`), and on hundreds of randomly damaged ones: anything that can be read at all has to come back the same.

What keeps it honest is that a thing's text is only used while the thing is still what that text says. A `.draftworx` file holds the text too, along with whether it still applies, so a DXF saved as `.draftworx` and back loses nothing, and a change can't be undone by that trip.

## What can be read

R12 to 2018, text and binary. Files from before R12 and after 2018 are read too, and what isn't recognised in them is kept. Saving a binary DXF writes text, because that's all this program writes, and it says so.

| In the file | Here |
|---|---|
| LINE, ARC, CIRCLE, ELLIPSE, LWPOLYLINE, SPLINE, POINT | The same thing, exactly |
| POLYLINE | A polyline, if flat. A 3D one shows flattened and can't be changed. A mesh isn't shown |
| TEXT, MTEXT, ATTDEF, ATTRIB | Text. Formatting set inside paragraph text isn't shown |
| SOLID, TRACE | A filled shape |
| INSERT, BLOCK | Blocks, nested and in arrays, stretched and mirrored |
| DIMENSION | A dimension that can be changed: linear, aligned, radius, diameter and angular. Until it's changed, it's drawn by the block the file holds for it, as the program that wrote it drew it. Ordinate dimensions, tilted ones, and ones placed away from their block stay as the file had them |
| ACAD_TABLE, ARC_DIMENSION, LARGE_RADIAL_DIMENSION | Shown through the block the file holds for them. Can't be changed |
| HATCH | Shown by its boundaries, not its fill. Can't be changed yet |
| LEADER, 3DFACE, IMAGE, WIPEOUT, MLINE | Shown by their outlines. Can't be changed |
| Anything else: XLINE, RAY, MULTILEADER, 3DSOLID, REGION, MESH, proxies... | Not shown. Kept |
| Something tilted out of the plane | Not shown. Kept |
| Something whose numbers make no sense | Not shown. Kept |
| Paper space | Not shown. Kept |

Opening a file says what's there that can't be changed or isn't shown, in the panel, where it stays while the file is open.

An object that can't be changed here can still be selected, snapped to, erased and given another layer or colour. Moving or copying a selection leaves it where it is and says so.

**Coordinates.** Objects drawn from behind (a negative extrusion, which is what mirroring often leaves) are read to where they really are. Heights are ignored for showing, and said so. An unchanged object keeps its height, and a changed one loses it.

**Text encoding.** UTF-8 from 2007 on. Before that, the code page in `$DWGCODEPAGE`, with `\U+XXXX` for what it can't hold. Bytes that don't fit come back unchanged. For showing, they're read as Windows Latin 1, which is usually what they were.

**A spline given only by points it goes through.** We work out a cubic of our own: spaced by the distance between the points, and smooth at each. It goes through them exactly. Another program's may differ a little between them.

## What's written

A drawing that didn't come from a DXF is written as **2013** (`AC1027`), with what ezdxf's documentation lists as the least a file needs for AutoCAD to accept it, and a view centred on the drawing, so a program opens on what's in it.

**Export as DXF R12** writes a copy for machines and programs that need the oldest kind. It has no ellipses, splines or paragraph text, so they become fine polylines and lines of text, and it says so. A file from another version can be written to any version the same way (`export_dxf`), but it's then a new file: what can't be held by that version is written as what it draws, or left out, and it says which. An opened file keeps its own version when it's saved, and R13 and R14 files stay as they are.

A **dimension** is written as a DIMENSION, with a block of its own that draws it, as a program that doesn't work out dimensions itself needs. Its own size goes where AutoCAD keeps one, as an override of its style's DIMSCALE. Ø, ° and ± are written %%c, %%d and %%p, which every reader understands whatever its fonts. A dimension from a file that's been changed gets a new block, and the one it had stays in the file.

A name a reader would refuse (`/ \ : ; * ? " < > | , = `` ` ``) gets `_` in its place. R12 names are also upper case. A line of plain text longer than the file's version can hold (255 characters before 2007, 2049 after) stops the save with a message rather than being cut short.

## How it's checked

Before a DXF takes the file's name, what's about to be written is read back and compared with the drawing, object by object and in order: the type, where it is, and the blocks. If something had to change form, one object may have become several, so the check is that nothing went missing. A failed check leaves the old file alone and the drawing unsaved.

In CI (`tests/test_dxf.py`, `tests/test_dxf_readers.py`):

- **ezdxf** reads and audits what's written, in every version, with no errors, and its geometry is compared with ours. It also wrote many of the files that are read.
- **Real files** from other programs: AutoCAD, BricsCAD, a Leica laser measure, dxflib, CADMAN. They come back byte for byte.
- **GDAL** and **LibreCAD**, two other programs with their own readers, read what's written, on Linux. GDAL's result is compared with what was drawn. LibreCAD's is only checked for getting to the end of the file, and it's been looked at by hand once.
- **OpenSchematix**, the real one: what it writes opens here, and what's written here opens there.
- **Damage**: random changes to a file's bytes and numbers give either a message or the same bytes back. Blocks in blocks in arrays, small to describe and impossible to draw, draw part of themselves, say so and don't stop the program.

## What hasn't been checked

**AutoCAD.** It can't be run here, so nothing says it reads our files, though a file we didn't change is the file it wrote. Treat anything else as untested until someone has tried it, which is why `.draftworx` stays the safer place for your only copy (`ARCHITECTURE.md`, ADR-6).

## What can go wrong

- **A reference to something erased.** Erasing an object that a group or another object refers to by handle leaves that reference pointing at nothing. Readers usually cope, and AutoCAD's audit repairs it. We don't follow those references.
- **A renamed layer used in paper space.** Paper space is kept as text, so what's there still names the old layer.
- **What a dimension is attached to isn't written to DXF.** A dimension drawn here follows what it measures, in this program and in a `.draftworx` file. In a DXF it's a dimension that measures from where those points were, so after reopening it no longer follows them. Dimensions from a file arrive unattached for the same reason: AutoCAD keeps that in objects this program doesn't read.
- **Other programs may draw a dimension their own way.** LibreCAD works out dimensions itself from the points they measure rather than using the block that draws them, so its arrows, text and rounding follow its own settings.
- **A damaged file that a repair tool would accept** may be refused here. Rather than guess, we say what's wrong and where. A cut-off file is refused unless every section in it is complete, and then we say so.
