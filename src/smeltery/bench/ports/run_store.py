"""Port: find and read runs. Each row carries the run's stable `id` (see `adapters/run_store.run_id`)."""

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

    def read_record(self, path: str | Path) -> dict[str, Any] | None:
        """The record's raw JSON object, or None when it is unreadable or not an object."""
        ...
