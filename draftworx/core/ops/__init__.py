# SPDX-License-Identifier: GPL-3.0-or-later
"""Changing the shape of things: trim, extend, break, offset, fillet, chamfer, join, explode, stretch.

These work out what a change would be and change nothing, so the same call gives a command both its preview and its result. A failure is a
ValueError with a sentence a person can read. Whatever can be exact is exact, and what can't be says how close it is.
"""
