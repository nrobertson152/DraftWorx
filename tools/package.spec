# SPDX-License-Identifier: GPL-3.0-or-later
# What goes into the standalone DraftWorx. Build it with `python tools/package.py`, which also checks and packs it.
#
# One folder with two programs in it, as the installed package has them: `draftworx`, the command, and `draftworx-gui`, which on
# Windows opens the window without a console behind it. Both run tools/package_entry.py, the same as `python -m draftworx`, so
# either opens the window when it's given nothing to do, and either can run the self-test. They share everything else.
import os
from pathlib import Path

ROOT = Path(SPECPATH).parent
FOLDER = os.environ.get("DRAFTWORX_FOLDER", "DraftWorx")
ICON = os.environ.get("DRAFTWORX_ICON") or None

# Qt's network module only comes in with PySide6's own hook and with plugins for things DraftWorx never does: TLS, watching the
# network, being shown over VNC and touch input sent over the network. All of it is left out, so the program has nothing to go online with.
analysis = Analysis([str(ROOT / "tools" / "package_entry.py")], pathex=[str(ROOT)], excludes=["PySide6.QtNetwork"])
ONLINE = ("/tls/", "/networkinformation/", "qvnc", "qtuiotouchplugin", "qt6network")


def offline(toc):
    return [e for e in toc if not any(o in e[0].replace("\\", "/").lower() for o in ONLINE)]


analysis.binaries, analysis.datas = offline(analysis.binaries), offline(analysis.datas)     # links to libraries are in datas

code = PYZ(analysis.pure)
programs = [EXE(code, analysis.scripts, name=name, console=console, exclude_binaries=True, icon=ICON, upx=False)
            for name, console in (("draftworx", True), ("draftworx-gui", False))]
COLLECT(*programs, analysis.binaries, analysis.datas, name=FOLDER, upx=False)
