"""Port: how far along a launched run is, for the work panel's progress bar."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RunProgressSource(Protocol):
    def of_task(self, command: list[str], cwd: str) -> dict[str, Any] | None:
        """{done, total, reused} SCF counts for the run a task's `command` (run in `cwd`) is writing, or None when it
        is not a run, or keeps no progress (a preview, a tier that does not checkpoint, a run that has finished)."""
        ...
