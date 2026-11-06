# SPDX-License-Identifier: GPL-3.0-or-later
"""Where the program keeps its own things, and what it remembers between runs. A damaged settings file is ignored: losing a
remembered theme isn't worth a message, and it must never stop the program starting."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .safefile import SaveError, write_verified


def state_dir() -> Path:
    override = os.environ.get("DRAFTWORX_STATE_DIR")
    if override:
        return Path(override)
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "DraftWorx"
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "draftworx"


@dataclass
class Config:
    theme: str = "modern_light"
    recent: list[str] = field(default_factory=list)
    show_weights: bool = True
    show_grid: bool = True
    polar_degrees: float = 45.0

    def remember(self, path: str) -> None:
        self.recent = [path] + [p for p in self.recent if p != path]
        del self.recent[12:]


def load_config() -> Config:
    try:
        data = json.loads((state_dir() / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Config()
    c = Config()
    if isinstance(data, dict):
        if isinstance(data.get("theme"), str):
            c.theme = data["theme"]
        if isinstance(data.get("recent"), list):
            c.recent = [p for p in data["recent"] if isinstance(p, str)][:12]
        if isinstance(data.get("show_weights"), bool):
            c.show_weights = data["show_weights"]
        if isinstance(data.get("show_grid"), bool):
            c.show_grid = data["show_grid"]
        if isinstance(data.get("polar_degrees"), (int, float)) and not isinstance(data.get("polar_degrees"), bool) and data["polar_degrees"] >= 0:
            c.polar_degrees = float(data["polar_degrees"])
    return c


def save_config(c: Config) -> None:
    """Best effort. A read-only home folder shouldn't get in anyone's way."""
    try:
        folder = state_dir()
        folder.mkdir(parents=True, exist_ok=True)
        write_verified(folder / "settings.json", json.dumps(asdict(c), indent=1).encode("utf-8"))
    except (OSError, SaveError):
        pass
