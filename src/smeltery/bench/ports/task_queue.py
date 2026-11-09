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
        retries: int = 0,
        memory_limit_gb: float | None = None,
    ) -> str:
        """Submit work; return a task id. `retries` (0..5) re-runs a failed task; a cancelled one is never retried.

        `memory_limit_gb` caps the task's resident memory: a backend that can enforce it kills the task and reports
        `oom`, rather than letting one SCF take the node down. None means no cap. A backend that cannot sample
        memory ignores it rather than pretending to enforce it.
        """
        ...

    def list(self, status: str | None = None) -> List[Task]: ...

    def get(self, task_id: str) -> Task | None: ...

    def status(self, task_id: str) -> str: ...

    def cancel(self, task_id: str) -> bool: ...

    def tail_log(self, task_id: str, lines: int = 40) -> str: ...

    def ready(self) -> tuple[bool, str]:
        """Whether the queue can take work, with a short detail. Read-only: a probe must not create anything."""
        ...
