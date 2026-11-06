# DraftWorx

**A lightweight, offline 2D CAD program that's simple to learn and exceptionally good at drafting.** Windows and Linux. Free and open source (GPL-3.0-or-later).

Not more features than AutoCAD. Just everyday drafting that's easier: snaps that find the point you meant without a settings dialog, typing a length wherever you are,
trimming the bit in the way, one Dimension tool that reads what you point at, and DXF files that come back undamaged.

> **Status: under construction.** The geometry kernel, the drawing model, undo and saving exist and are tested. There's no window yet. See [the roadmap](docs/ROADMAP.md).

Copyright © 2026 Nate Robertson. No accounts, telemetry, advertisements or feature limits.

## Read more

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
