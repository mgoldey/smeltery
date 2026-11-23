"""Where Bench keeps its work when no ROOT is given. The adapters are the only place that reads `Path.home()`."""

from __future__ import annotations

import os
from pathlib import Path

ROOT_ENV = "SMELTERY_BENCH_ROOT"


def default_root() -> Path:
    """Where Bench keeps its work when no ROOT is given: `$SMELTERY_BENCH_ROOT`, else the per-user XDG data directory
    (`$XDG_DATA_HOME/smeltery/bench`, or `~/.local/share/smeltery/bench`). Never the current directory, whose run
    glob would walk a `.venv` or `node_modules`."""
    env = os.environ.get(ROOT_ENV)
    if env:
        return Path(env).expanduser()
    data = os.environ.get("XDG_DATA_HOME")
    base = (
        Path(data).expanduser() if data and Path(data).expanduser().is_absolute() else Path.home() / ".local" / "share"
    )
    return base / "smeltery" / "bench"
