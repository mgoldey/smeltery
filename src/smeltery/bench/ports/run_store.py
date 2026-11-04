"""Port: find, read, index runs and their artefacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RunStore(Protocol):
    def list_runs(self, root: str | Path | None = None) -> list[dict[str, Any]]: ...

    def read_run(self, path: str | Path) -> Any:
        """Return a `smeltery.gui.results.Run`."""
        ...

    def find_runs(self, root: str | Path | None = None) -> list[Path]: ...
