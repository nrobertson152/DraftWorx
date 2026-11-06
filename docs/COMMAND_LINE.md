# The `draftworx` command, for other programs

RevisionWorx, OpenSchematix, OpenMeridian and anything else can use a drawing without the window, and without importing DraftWorx:
`draftworx <command> ...` prints **exactly one JSON object** on stdout with `"ok": true` or `"ok": false`, and exits 0 or 2. It's the same
shape as `revisionworx link`. Nothing here needs Qt, the network or an account.

A refusal changes nothing, says why in `"error"`, written for a person, and says what sort of refusal it is in `"kind"`: `bad-input`,
`not-readable`, `exists` or `error`. New fields may be added to answers at any time, so ignore fields you don't know. `api` (from `version`)
only goes up for a change that would break a program using this.

| Command | Does | Answer, besides `ok` |
|---|---|---|
| `version` | this interface's version and the program's | `api`, `version` |
| `info DRAWING` | what's in a `.draftworx` or DXF drawing | `path`, `format`, `dxf_version`, `units`, `scale`, `objects`, `kinds` (how many of each), `layers`, `blocks` (`name`, `placed`), `placed_blocks`, `extents`, `notices` |
| `parts DRAWING [--csv FILE] [--number N] [--description D] [--revision R]` | the parts list the drawing's blocks make | `number`, `parts` (`number`, `description`, `quantity`, `unit`, `revision`, `references`, `blocks`), `problems` (`where`, `what`), `csv` |
| `convert DRAWING OUTPUT [--dxf-version V] [--overwrite]` | write it as DXF or `.draftworx`, checked before it takes the name | `path`, `notes` (what had to be simplified to fit) |
| `pdf DRAWING OUTPUT.pdf [--paper P] [--upright \| --across] [--scale S] [--black] [--overwrite]` | the drawing's sheet as a PDF, to scale | `path`, `paper`, `landscape`, `scale` (`1:50`), `sheet` (its box in the drawing), `notes` |

**PDFs.** With none of `--paper`, `--upright`, `--across` or `--scale`, a drawing that has a sheet is printed on it, exactly as the window
prints it. Otherwise, or when it has no sheet, a sheet is fitted round what's drawn: on `--paper` (or its own paper, or A3, or Tabloid for
a drawing in inches or feet), across unless `--upright`, at `--scale` (`1:50`, `2:1`, `1/4"=1'`) or the largest standard scale that fits. Its
border, if it has one, goes with it. The drawing itself isn't changed. `--black` prints everything in black. The same drawing gives the
same PDF, so a revision control program can tell nothing changed.

**Parts lists.** A placed block is a part when it has an attribute tagged `PART_NO` (or `PARTNO`, `PART_NUMBER`, `PART`, `PN`, `P/N`, `ITEM_NO`).
It may also have `DESCRIPTION`, `QTY`, `UNIT`, `REV`, and `TAG` or `REF` naming this one (`Y1`). Each copy counts its `QTY`, or 1, once
for each place an array puts it. `--csv` writes the list as a BOM RevisionWorx reads (`docs/bom` in RevisionWorx): an item row for the
drawing's own number (`--number`, or the file's name) and each part, then a line for each part under it, numbered 10, 20, 30. Quantities are
plain decimals. Text a spreadsheet would run as a formula gets a `'` in front, which RevisionWorx takes off again. Blocks inside blocks
aren't looked inside: an assembly is a block with a part number of its own.

**Example**

```sh
$ draftworx parts panel.dxf --number PANEL-12 --csv panel-bom.csv
{"csv": "panel-bom.csv", "number": "PANEL-12", "ok": true, "parts": [{"blocks": ["Valve"], "description": "", "number": "V-100", "quantity": "2", ...}], "problems": []}
$ revisionworx link publish panel-bom.csv --project J-2001 --number PANEL-12-BOM --title "Panel 12 parts" -m "From the drawing"
```
