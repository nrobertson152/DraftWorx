# SPDX-License-Identifier: GPL-3.0-or-later
"""`python -m draftworx`: the same as the `draftworx` command."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
