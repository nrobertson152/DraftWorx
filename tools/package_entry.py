# SPDX-License-Identifier: GPL-3.0-or-later
"""What both standalone programs run (tools/package.spec): the same as `python -m draftworx`."""
import sys

from draftworx.cli import main

sys.exit(main())
