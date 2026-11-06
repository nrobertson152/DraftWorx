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

- `format` must be `"DraftWorx drawing"`. A `version` above what the program knows is refused with a message; the file isn't touched.
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

**Colours** are one integer: 1 to 255 is a colour index, 256 ByLayer, 0 ByBlock, and `16777216 + 0xRRGGBB` a true colour. **Weights** are in hundredths of a millimetre, or -1 ByLayer, -2 ByBlock, -3 default.

`extras` and `source` hold what a DXF file said beyond the model, so it can be written back (`DXF.md`).

## Versions

**1** First version. The format can gain object types and optional fields without changing version while there's no release, because old files stay readable. After a release, anything an older reader would misread needs a new version and a test that old files still open.
