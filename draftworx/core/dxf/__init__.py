# SPDX-License-Identifier: GPL-3.0-or-later
"""DXF as a working format. See docs/DXF.md.

Reading keeps two things for every object: what it means, and the exact text the file had for it. An object that's still what it was when read
is written back as that text, so a file nobody changed comes back byte for byte, and one with a line changed differs by that line. Whatever isn't
understood, from objects to whole sections, is kept the same way.
"""
from .read import read_dxf
from .write import DEFAULT, VERSIONS, WriteReport, check_written, encode_dxf, write_dxf

__all__ = ["DEFAULT", "VERSIONS", "WriteReport", "check_written", "encode_dxf", "read_dxf", "write_dxf"]
