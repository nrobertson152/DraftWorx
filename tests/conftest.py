# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared test settings. Property tests are derandomized so CI is repeatable; a failure here is real, never a dice roll."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from hypothesis import HealthCheck, settings  # noqa: E402

settings.register_profile("draftworx", derandomize=True, deadline=None, max_examples=200,
                          suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
settings.load_profile("draftworx")


@pytest.fixture(autouse=True)
def private_state(tmp_path, monkeypatch):
    """Nothing a test does may touch the real settings or recovery files."""
    monkeypatch.setenv("DRAFTWORX_STATE_DIR", str(tmp_path / "state"))
