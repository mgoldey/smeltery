# Task queue semantics

**A task's reported status and exit code are always consistent with what the process did.**

## Requirements

1. States: `queued`, `running`, `succeeded`, `failed`, `cancelled`.
2. `succeeded` means exit code 0; `failed` means a non-zero exit code or a process that vanished
   without one. **A process that exited 0 is never reported `failed`, not even transiently.** A
   reader polling `status()` in a tight loop must see the sequence `running... -> succeeded`, never
   `running -> failed -> succeeded`.
3. When `status()` first reports a terminal state, `get(id).returncode` already holds the exit code
   (None only for `cancelled` or a vanished process). Status and exit code are written together.
4. **A task runs at most once.** A task whose worker died is reported `failed` and is never retried
   on the queue's initiative: an arbitrary shell command is not safe to re-run. Resubmitting is a
   human decision.
5. Status survives the adapter: a new adapter instance over the same root reports the same status,
   exit code, timings and log for an old task.
6. Unknown ids: `get` returns `None`; `tail_log` raises `KeyError`; `cancel` raises `KeyError`.
   Both adapters behave identically.
7. `TemporalTaskQueue.__init__(self, host: str = "127.0.0.1:7233", namespace: str = "default",
   task_queue: str = "smeltery-tasks") -> None`, delegating to `smeltery.tasks.TemporalTaskManager`.
   Its contract run is skipped with a stated reason when no server answers, never silently passed.

## Data source for checks
The process's real exit status (`bash -c "exit 3"` exits 3). Tests poll `status()` repeatedly and
record every value observed.
