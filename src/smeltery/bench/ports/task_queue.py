"""Port: submit, list, status, cancel, logs."""

from __future__ import annotations

from pathlib import Path
from typing import List, Protocol, runtime_checkable

from smeltery.tasks import Task


@runtime_checkable
class TaskQueue(Protocol):
    def submit(
        self,
        name: str,
        command: List[str],
        *,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        log_dir: str | Path | None = None,
        note: str = "",
    ) -> str:
        """Submit work; return a task id."""
        ...

    def list(self, status: str | None = None) -> List[Task]: ...

    def get(self, task_id: str) -> Task | None: ...

    def status(self, task_id: str) -> str: ...

    def cancel(self, task_id: str) -> bool: ...

    def tail_log(self, task_id: str, lines: int = 40) -> str: ...
