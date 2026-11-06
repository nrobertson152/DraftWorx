# DraftWorx — development workflow

DraftWorx follows the same rules as OpenSchematix, RevisionWorx, FluidWorx and OpenMeridian.

## Roles

- **Director (`nrobertson152`)** has sole authority over releases, release/version tags and licensing, and may override any decision.
- **Developer (Claude)** plans, implements, tests, reviews and merges its own work on branches and PRs. No independent
  reviewer is required. Creating, drafting or publishing GitHub Releases and pushing release/version tags is for the Director only.

GitHub issues, PRs, commits and CI are the record. Post the exact head SHA, commands, versions and pass/fail/skip counts on each PR.
Merge when required CI passes on the exact head. State anything unverified rather than claiming it.

## Product rules

- GPL-3.0-or-later, free and open source.
- Fully offline: no network access, accounts, telemetry, ads or feature limits.
- Python 3.13 and PySide6, on Windows and Linux. Standard library only below `draftworx/gui`; `[dev]` extras are for tests and builds only.
- **`draftworx/core` never imports Qt** (`tests/test_architecture.py` enforces it). A command is a Qt-free coroutine, so it can be tested without a window.
- Every change to a drawing goes through a `Transaction`. There is no other way to change one, and no command-specific undo code.
- A snap result is the exact point the geometry defines. Never round a model coordinate.
- Saves are atomic and verified (`core/safefile.py`). A malformed file shows a message; it never crashes and never half-loads.
- `.draftworx` files must keep opening. Document any format change in `docs/FILE_FORMAT.md`, version it and test it.
- DXF we don't understand is kept, not dropped. An entity the person didn't change goes back out as it came in.
- A simple result must not look like an exact one. Say what the program doesn't do (`docs/KNOWN_LIMITATIONS.md`).
- Simple is the product. Before adding a setting, a dialog or a mode, try to make the default right instead.
- Interface: the same tokens, themes and icon language as OpenSchematix (as OpenMeridian has them), RevisionWorx's status-block
  sentence and one primary action. No colours or styles outside `gui/theme.py`.
- Other programs are reached through their documented files and command lines, never by importing their code.

## Priorities

1. Data loss, corruption and unsafe writes.
2. Wrong geometry: a snap, trim, offset, dimension or DXF value that's wrong without saying so.
3. Crashes and runaway resource use.
4. Platform compatibility.
5. Performance, features, cosmetics.

## Commands

```sh
python -m pip install -e ".[dev]"            # Linux also: sudo apt-get install -y libegl1 libgl1 libopengl0 libxkbcommon-x11-0 libxcb-cursor0
python -m compileall -q draftworx tests
QT_QPA_PLATFORM=offscreen python -m pytest -q -ra
python -m draftworx --self-test
python tools/bench.py                        # measured speed; see docs/ARCHITECTURE.md for what it should stay under
```

Property tests are derandomized, so CI is repeatable. A failure is real.

Add a regression test with every behaviour change, and make sure it fails on the old code. Never weaken, skip or delete tests to make CI
pass, and never bypass CI. Never describe unverified behaviour (native Windows, AutoCAD reading our files) as verified.
