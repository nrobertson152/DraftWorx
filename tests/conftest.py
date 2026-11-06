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
    """Nothing a test does may touch the real settings or recovery files: DraftWorx's own, or those of a program it runs, such as
    RevisionWorx's workspaces and check-outs."""
    monkeypatch.setenv("DRAFTWORX_STATE_DIR", str(tmp_path / "state"))
    for var in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "APPDATA", "LOCALAPPDATA"):
        monkeypatch.setenv(var, str(tmp_path / "user" / var.lower()))
    monkeypatch.delenv("REVISIONWORX_WORKSPACE", raising=False)
