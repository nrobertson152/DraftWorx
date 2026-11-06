# The `.draftworx` file

UTF-8 JSON with `\n` line endings. One table entry or object per line, so a text diff between two versions shows what changed. The same drawing gives the same bytes on every computer.

```json
{"format":"DraftWorx drawing","version":1,"program":"DraftWorx 0.1.0","next_id":4,"settings":{...},"current":{...},
"layers":[
{"name":"0","color":7,"linetype":"Continuous","weight":-3}
],
"linetypes":[...],
"text_styles":[...],
"dim_styles":[...],
"blocks":[...],
"entities":[
{"id":1,"type":"line","a":[0.0,0.0],"b":[100.0,0.0]},
{"id":2,"type":"circle","c":[50.0,50.0],"r":25.0,"layer":"Walls","color":1}
],
"extras":{}}
```

Numbers are written as the shortest text that reads back as the same double, so nothing is rounded. NaN and infinity aren't allowed.

## Rules for readers

- `format` must be `"DraftWorx drawing"`. A `version` above what the program knows is refused with a message; the file isn't touched. A drawing is written with the oldest version that can hold it, so one without anything newer still opens in an older DraftWorx.
- Every value is checked. A file that fails a check is refused with a message saying where, and nothing is built from it.
- Table names compare without case. Tables are written sorted, so the order things were made doesn't change the file.
- An `id` is unique across `entities` and all blocks, and is never reused. `next_id` is the next one to give out. It's only ever moved forward on reading.

## Objects

Every object has `id` and `type`. These are optional and default as shown: `layer` ("0"), `color` (256), `linetype` ("ByLayer"), `weight` (-1), `ltscale` (1.0), `order` (the id; later is on top), `source`.

| `type` | Fields |
|---|---|
| `line` | `a`, `b` |
| `arc` | `c`, `r`, `start`, `sweep` (radians, sweep signed, at most one turn) |
| `circle` | `c`, `r` |
| `ellipse` | `c`, `major` (centre to end of the major axis), `ratio` (0 to 1), and optionally `start`, `sweep` (ellipse parameter) |
| `polyline` | `pts`, optionally `bulges` (one per point, DXF's meaning), `closed`, `width`, `widths` (start and end per point) |
| `spline` | `degree`, `knots`, `ctrl`, optionally `weights`, `fit`, `periodic` |
| `node` | `p` |
| `solid` | `pts` (3 or 4, in order round the outline) |
| `text` | `p`, `height`, `text`, optionally `rotation`, `style`, `halign` (0 to 5), `valign` (0 to 3), `width_factor`, `oblique`, `p2`, `mirrored_x`, `mirrored_y` |
| `mtext` | `p`, `height`, `text` (DXF's formatting codes), optionally `width`, `rotation`, `attach` (1 to 9), `style`, `spacing` |
| `attdef` | the fields of `text`, plus `tag`, optionally `prompt`, `invisible`, `constant` |
| `insert` | `block`, `p`, optionally `sx`, `sy`, `rotation`, `attribs` (like `attdef`), `columns`, `rows`, `column_spacing`, `row_spacing` |
| `dimension` | `measure` (`linear`, `aligned`, `radius`, `diameter` or `angular`), `pts`, `at`, optionally `angle` (radians: what a linear one measures along), `text` (`<>` is the measurement), `style`, `scale`, `text_at`, `refs`, `block`. See below |
| `hatch` | `loops` (each a list of `line`, `arc`, `circle`, `ellipse` and `spline` objects, end to end), `pattern` (`SOLID` or a pattern's name), optionally `scale`, `angle` (radians), `origin`, `seeds`, `refs`, `lines`. See below |
| `foreign` | `dxf_type`, optionally `block` and `at` (a block that draws it, and where), and `curves` (lines, arcs, circles, ellipses, polylines or splines to show for it). Something from a DXF that can't be changed here, whose `source` holds what it really is |

**Dimensions** hold what they measure, not what they draw, which is worked out from these and the style. `pts` is two points for `linear` and `aligned`; the centre and a point on the curve for `radius` and `diameter`; and for `angular`, the ends of its two lines (four points), or its vertex and a point on each arm (three). `at` is a point on the dimension line or its arc, or where a radius or diameter's text goes. `refs` has one entry per point: `[id, "which"]` for the object it's attached to and which of its points (`start`, `end`, `mid`, `center`, `point`, `vertex:n`, `mid:n`, or `on` for a point on a circle or arc), or `null`. Something it names that isn't in the file is let go of on reading. `block` is a block from a DXF that drew it there, used while it's unchanged.

**Hatches** fill what's inside an odd number of their `loops`. A pattern's sizes are millimetres on the sheet, multiplied by `scale`, turned by `angle` and started from `origin`. `lines` is the pattern's own definition when it isn't one the program knows, as from a DXF: one `[angle, [x, y], along, across, [dashes]]` per family of lines, as a pattern file has it. `seeds` are the points it was made from, and `refs` the ids of the objects round it that it follows. Something it names that isn't in the file is let go of on reading.

**Settings** are the drawing's own: `units`, `linear_format`, `linear_decimals`, `angle_decimals`, `annotation_scale` (the n of its scale, 1:n), `ltscale`, `point_mode`, `point_size`, and, once it has a sheet (version 4), `paper` (`A4`, `A3`, `A2`, `A1`, `A0`, `Letter`, `Legal`, `Tabloid`, `ANSI C`, `ANSI D` or `ANSI E`), `landscape` and `sheet_at` (the sheet's lower-left corner in the drawing). The sheet is the paper's size times the scale (`sheets.py`). A drawing without a sheet writes none of the three.

**Colours** are one integer: 1 to 255 is a colour index, 256 ByLayer, 0 ByBlock, and `16777216 + 0xRRGGBB` a true colour. **Weights** are in hundredths of a millimetre, or -1 ByLayer, -2 ByBlock, -3 default.

`extras` and `source` hold what a DXF file said beyond the model, so it can be written back (`DXF.md`).

`source` is `{"dxf": "the file's own lines"}`, on an object, a table entry or a block. It can also say `"reshaped": true` or `"restyled": true` if the thing has changed since it was read, so that the old text isn't mistaken for the current one. Without those, the text is written back as it is. Empty text means the file didn't mention it. `extras.dxf` holds the rest of the file: its version, line endings, header, the order of its sections, tables and blocks, and whatever we don't read.

## Versions

**1** First version. The format can gain object types and optional fields without changing version while there's no release, because old files stay readable. After a release, anything an older reader would misread needs a new version and a test that old files still open.

**2** Adds `dimension`. A drawing without one is still written as version 1.

**3** Adds `hatch`. A drawing without one is written as version 2 if it has dimensions, and otherwise as version 1.

**4** Adds the sheet a drawing prints on: `paper`, `landscape` and `sheet_at` in `settings`. An older reader would open the drawing and lose its sheet without saying so, so a drawing with one is version 4. One without is written as before.
