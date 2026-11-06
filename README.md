# DraftWorx

**A lightweight, offline 2D CAD program that's simple to learn and exceptionally good at drafting.** Windows and Linux. Free and open source (GPL-3.0-or-later).

Not more features than AutoCAD. Just everyday drafting that's easier: snaps that find the point you meant without a settings dialog, typing a length wherever you are,
trimming the bit in the way, one Dimension tool that reads what you point at, and DXF files that come back undamaged.

> **Status: under construction.** You can draw, snap, select, edit, and open and save DXF without damaging it. There's no trim, dimensions or printing yet, and nobody has used it by hand. See [the roadmap](docs/ROADMAP.md).

![DraftWorx drawing a line, lined up with a corner it picked up by pausing on it](docs/images/screenshot.png)

Copyright © 2026 Nate Robertson. No accounts, telemetry, advertisements or feature limits.

## Try it

```sh
python -m pip install "draftworx @ git+https://github.com/nrobertson152/DraftWorx"
draftworx-gui
```

Type `L` and Enter to draw a line. Type a length and Enter. Pause on a corner to line up with it. Ctrl+K finds anything.

## Read more

- [How snapping behaves](docs/SNAPPING.md)

- [Architecture and decisions](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [What existing CAD programs teach](docs/RESEARCH.md)
- [The file format](docs/FILE_FORMAT.md)
- [Known limitations](docs/KNOWN_LIMITATIONS.md)

## Part of a family

[RevisionWorx](https://github.com/nrobertson152/RevisionWorx) for revision control, [OpenSchematix](https://github.com/nrobertson152/OpenSchematix) for schematics,
[OpenMeridian](https://github.com/nrobertson152/OpenMeridian) for items and BOMs. DraftWorx reaches them through their documented files and command lines, and works without them.

## Development

Python 3.13 and PySide6. See `AGENTS.md` for the rules and commands.
