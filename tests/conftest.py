# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared test settings. Property tests are derandomized so CI is repeatable; a failure here is real, never a dice roll."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hypothesis import HealthCheck, settings  # noqa: E402

settings.register_profile("draftworx", derandomize=True, deadline=None, max_examples=200,
                          suppress_health_check=[HealthCheck.too_slow])
settings.load_profile("draftworx")
