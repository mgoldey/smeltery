"""Task management for smeltery, built on Temporal.

Temporal is the system of record for tasks: this module does not keep its own process table or
status store. It connects to a Temporal server, starts a `ShellTaskWorkflow` for each submitted
task, and reads status back from Temporal. A worker (`smeltery task-worker`) must be running to
execute the tasks; the dashboard and CLI talk to Temporal only.

If Temporal is unreachable, task operations raise `TemporalUnavailable` rather than silently
falling back to a private implementation.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .temporal_defaults import DEFAULT_HOST, DEFAULT_NAMESPACE, DOCKER_COMMAND
from .temporal_defaults import TASKS_QUEUE as TASK_QUEUE

WORKFLOW_TYPE = "ShellTaskWorkflow"

#: `TemporalTaskManager` defines a `list` method, so the builtin is unreachable by name inside that class
#: body. Bound here, where `list` still means the builtin, so return annotations can say what they mean.
_TaskList = list["Task"]


MAX_RETRIES = 5

#: `read_tail` reads backwards in blocks of this size, and never more than `_TAIL_MAX_BYTES` in all, so a 50 MB log
#: (or one giant `\r` progress line) costs the same as a short one.
_TAIL_BLOCK = 64 * 1024
_TAIL_MAX_BYTES = 4 * 1024 * 1024


def read_tail(path: str | Path, lines: int) -> str:
    """The last `lines` whole lines of a file, reading from the end instead of the whole file.

    Lines are counted, not guessed from a byte budget, so long lines still give `lines` of them; past
    `_TAIL_MAX_BYTES` it stops and drops the partial line it stopped inside rather than return a fragment."""
    if lines <= 0:
        return ""
    fd = os.open(path, os.O_RDONLY)
    try:
        end = pos = os.fstat(fd).st_size
        chunks: list[bytes] = []
        newlines = 0
        # lines + 1 newlines mean the first wanted line is complete (a trailing newline ends the last line).
        while pos > 0 and newlines <= lines and end - pos < _TAIL_MAX_BYTES:
            n = min(_TAIL_BLOCK, pos)
            pos -= n
            chunk = os.pread(fd, n, pos)
            chunks.append(chunk)
            newlines += chunk.count(b"\n")
    finally:
        os.close(fd)
    data = b"".join(reversed(chunks))
    text = data.decode(errors="replace").splitlines()
    if pos > 0 and newlines <= lines:  # hit the byte cap inside a line: the first line is partial
        text = text[1:]
    return "\n".join(text[-lines:])


class TemporalUnavailable(RuntimeError):
    """Raised when the Temporal server cannot be reached."""


def _iso(ts) -> str | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts.astimezone(timezone.utc).isoformat()
    return str(ts)


@dataclass
class Task:
    id: str
    name: str
    command: list[str]
    cwd: str
    status: str
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    returncode: int | None = None
    log_path: str = ""
    note: str = ""
    run_id: str | None = None
    attempt: int | None = None  # current attempt, 1-based; None when the backend cannot say
    max_attempts: int | None = None
    #: When the task last showed it was alive. A subprocess speaks no heartbeat protocol, so this is the last time
    #: its log grew: the only liveness a plain command gives for free. None when the backend cannot say.
    heartbeat_at: str | None = None
    #: Seconds since `heartbeat_at`, computed on read so a client need not know the server's clock.
    heartbeat_age_s: float | None = None
    #: True while a RUNNING task has been silent longer than the queue's threshold. Silence is not death -- a long
    #: SCF prints nothing for hours -- so this never changes `status`; it says where to look.
    stalled: bool = False
    #: Largest resident set seen while sampling, in GB. None when nothing sampled it.
    peak_rss_gb: float | None = None
    #: True when the task was killed for exceeding `memory_limit_gb`. A plain failure leaves it False.
    oom: bool = False
    #: The cap this task was submitted with, in GB; None for no cap.
    memory_limit_gb: float | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "command": self.command,
            "cwd": self.cwd,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "returncode": self.returncode,
            "log_path": self.log_path,
            "note": self.note,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "heartbeat_at": self.heartbeat_at,
            "heartbeat_age_s": self.heartbeat_age_s,
            "stalled": self.stalled,
            "peak_rss_gb": self.peak_rss_gb,
            "oom": self.oom,
            "memory_limit_gb": self.memory_limit_gb,
        }


# ---------- Temporal workflow / activity definitions ----------

try:  # temporalio is an optional runtime dependency for environments without it
    from datetime import timedelta

    from temporalio import activity, workflow
    from temporalio.common import RetryPolicy

    @activity.defn(name="run_shell")
    async def run_shell_activity(spec: dict) -> int:
        import asyncio as _asyncio

        command = spec["command"]
        cwd = spec.get("cwd")
        log_path = spec.get("log_path")
        env = dict(os.environ)
        env.update(spec.get("env") or {})
        f = None
        if log_path:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            f = open(log_path, "ab", buffering=0)
        try:
            proc = await _asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                env=env,
                stdout=f if f is not None else _asyncio.subprocess.DEVNULL,
                stderr=_asyncio.subprocess.STDOUT if f is not None else _asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
            rc = await proc.wait()
            return int(rc if rc is not None else 0)
        finally:
            if f is not None:
                f.close()

    @workflow.defn(name=WORKFLOW_TYPE)
    class ShellTaskWorkflow:
        @workflow.run
        async def run(self, spec: dict) -> int:
            # The activity reports failure as a non-zero return code rather than by raising, so Temporal's own
            # RetryPolicy would never fire; retries are a loop here. A cancel during the sleep ends the workflow.
            max_attempts = int(spec.get("retries") or 0) + 1
            rc = 0
            for attempt in range(1, max_attempts + 1):
                rc = await workflow.execute_activity(
                    run_shell_activity,
                    spec,
                    start_to_close_timeout=timedelta(hours=24),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                )
                if rc == 0 or attempt == max_attempts:
                    break
                await workflow.sleep(timedelta(seconds=min(2 ** (attempt - 1), 30)))
                workflow.upsert_memo({"attempt": attempt + 1})
            return rc

    TEMPORAL_AVAILABLE = True
except Exception:  # pragma: no cover - import guard
    # Deliberate: without temporalio there is no activity or workflow to register, and `worker_activities()` /
    # `worker_workflows()` return empty lists rather than these names. Annotated so a type checker reads the
    # shadowing as intended instead of as an incompatible assignment.
    TEMPORAL_AVAILABLE = False
    run_shell_activity: object = None
    ShellTaskWorkflow: object = None


def worker_activities():
    return [run_shell_activity] if TEMPORAL_AVAILABLE else []


def worker_workflows():
    return [ShellTaskWorkflow] if TEMPORAL_AVAILABLE else []


def worker_cli(
    host: str = DEFAULT_HOST,
    namespace: str = DEFAULT_NAMESPACE,
    task_queue: str = TASK_QUEUE,
    concurrency: int = 2,
) -> int:
    """`smeltery task-worker`: run the shell-task worker until Ctrl+C (0); 2 when temporalio is missing or the server
    cannot be reached (said on stderr, with how to start one), as `smeltery temporal-worker` does."""
    if not TEMPORAL_AVAILABLE:
        print("temporalio is not installed", file=sys.stderr)
        return 2
    from temporalio.client import Client
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    async def main() -> int:
        try:
            client = await Client.connect(host, namespace=namespace)
        except Exception as e:  # noqa: BLE001
            print(
                f"cannot reach the Temporal server at {host}: {e}\nstart one with:\n  {DOCKER_COMMAND}", file=sys.stderr
            )
            return 2
        async with Worker(
            client,
            task_queue=task_queue,
            workflows=worker_workflows(),
            activities=worker_activities(),
            workflow_runner=UnsandboxedWorkflowRunner(),
            max_concurrent_activities=concurrency,
        ):
            print(f"smeltery task worker up on {task_queue} (Ctrl+C stops)", flush=True)
            await asyncio.Future()
        return 0

    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        return 0


async def _settle_returncode(task: Task, handle) -> None:
    """A workflow Temporal calls succeeded only ran its command: read the command's return code from the workflow's
    result, and a non-zero one makes the task "failed"."""
    if task.status == "succeeded":
        try:
            task.returncode = await handle.result()
        except Exception:
            pass
        if task.returncode is not None and task.returncode != 0:
            task.status = "failed"


# ---------- client manager ----------


class TemporalTaskManager:
    def __init__(self, host: str = DEFAULT_HOST, namespace: str = DEFAULT_NAMESPACE, task_queue: str = TASK_QUEUE):
        self.host = host
        self.namespace = namespace
        self.task_queue = task_queue
        self._client = None

    async def connect(self):
        if not TEMPORAL_AVAILABLE:
            raise TemporalUnavailable("temporalio is not installed")
        from temporalio.client import Client

        try:
            self._client = await Client.connect(self.host, namespace=self.namespace)
        except Exception as e:  # noqa: BLE001
            raise TemporalUnavailable(f"cannot connect to Temporal at {self.host}: {e}") from e
        return self._client

    async def _client_or_connect(self):
        if self._client is None:
            await self.connect()
        return self._client

    def _run(self, coro):
        # `get_running_loop` and not `get_event_loop`: the question is only ever "are we inside a loop
        # already", and `get_event_loop` both answers it deprecatedly (it warns, and in a future Python
        # raises) and can create a loop as a side effect, which is not wanted in a sync wrapper.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # no running loop: the normal sync path
        else:
            raise RuntimeError("TemporalTaskManager sync API called from a running event loop; use async methods")
        return asyncio.run(coro)

    async def submit_async(self, name, command, *, cwd=None, env=None, log_dir=None, note="", retries: int = 0):
        if not isinstance(retries, int) or isinstance(retries, bool) or not 0 <= retries <= MAX_RETRIES:
            raise ValueError(f"retries must be an integer from 0 to {MAX_RETRIES}, got {retries!r}")
        client = await self._client_or_connect()
        tid = f"smeltery-task-{uuid.uuid4().hex[:12]}"
        cwd_path = str(Path(cwd or ".").resolve())
        if log_dir is None:
            log_dir = Path.cwd() / ".smeltery" / "task-logs"
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = str(log_dir / f"{tid}.log")
        spec = {
            "command": list(command),
            "cwd": cwd_path,
            "env": dict(env or {}),
            "log_path": log_path,
            "retries": retries,
        }
        handle = await client.start_workflow(
            WORKFLOW_TYPE,
            spec,
            id=tid,
            task_queue=self.task_queue,
            memo={
                "name": name or " ".join(command),
                "command": list(command),
                "cwd": cwd_path,
                "log_path": log_path,
                "max_attempts": retries + 1,
            },
        )
        return Task(
            id=tid,
            name=name or " ".join(command),
            command=list(command),
            cwd=cwd_path,
            status="running",
            created_at=_iso(datetime.now(timezone.utc)),
            log_path=log_path,
            note=note,
            max_attempts=retries + 1,
            run_id=handle.run_id if hasattr(handle, "run_id") else None,
        )

    def submit(self, name, command, **kw):
        return self._run(self.submit_async(name, command, **kw))

    # `list[Task]` and not `list[Task]` spelled bare: this class defines a `list` method, which shadows the
    # builtin inside the class body. It works at runtime only because annotations are deferred (PEP 563), and a
    # type checker resolves it to the method. `_TaskList` is the builtin, bound before the shadowing name exists.
    async def list_async(self, status: str | None = None) -> _TaskList:
        client = await self._client_or_connect()
        tasks: _TaskList = []
        async for wf in client.list_workflows(f'WorkflowType = "{WORKFLOW_TYPE}"'):
            t = await self._to_task(client, wf)
            if status and t.status != status:
                continue
            tasks.append(t)
        tasks.sort(key=lambda t: t.created_at or "", reverse=True)
        return tasks

    def list(self, status=None):
        return self._run(self.list_async(status))

    async def get_async(self, task_id: str) -> Task | None:
        client = await self._client_or_connect()
        try:
            handle = client.get_workflow_handle(task_id)
            desc = await handle.describe()
        except Exception:
            return None
        task = await self._from_describe(task_id, desc)
        await _settle_returncode(task, handle)
        return task

    def get(self, task_id):
        return self._run(self.get_async(task_id))

    async def cancel_async(self, task_id: str) -> bool:
        client = await self._client_or_connect()
        try:
            handle = client.get_workflow_handle(task_id)
            await handle.cancel()
            return True
        except Exception:
            return False

    def cancel(self, task_id):
        return self._run(self.cancel_async(task_id))

    async def tail_log_async(self, task_id: str, lines: int = 40) -> str:
        t = await self.get_async(task_id)
        if t is None or not t.log_path:
            return ""
        try:
            return read_tail(t.log_path, lines)
        except FileNotFoundError:
            return ""

    def tail_log(self, task_id, lines=40):
        return self._run(self.tail_log_async(task_id, lines=lines))

    async def _to_task(self, client, wf) -> Task:
        try:
            handle = client.get_workflow_handle(wf.id, run_id=wf.run_id)
            desc = await handle.describe()
            task = await self._from_describe(wf.id, desc, run_id=wf.run_id)
            await _settle_returncode(task, handle)
            return task
        except Exception as e:  # noqa: BLE001
            return Task(id=wf.id, name=wf.id, command=[], cwd="", status=str(wf.status), note=str(e))

    async def _from_describe(self, task_id, desc, run_id=None) -> Task:
        memo_raw = getattr(desc, "memo", None)
        if callable(memo_raw):
            try:
                memo_raw = await memo_raw()
            except Exception:
                memo_raw = None
        memo = memo_raw or {}
        if not isinstance(memo, dict):
            try:
                memo = dict(memo)
            except Exception:
                memo = {}

        # memo values may be payload objects; try .value / data converter
        def _decode(v):
            if hasattr(v, "value"):
                try:
                    v = v.value
                except Exception:
                    pass
            if isinstance(v, (bytes, bytearray)):
                try:
                    v = v.decode()
                except Exception:
                    pass
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except Exception:
                    pass
            return v

        def _m(key, default=None):
            v = memo.get(key, default) if hasattr(memo, "get") else default
            return _decode(v)

        name = _m("name") or task_id
        command = _m("command") or []
        cwd = _m("cwd") or ""
        log_path = _m("log_path") or ""
        max_attempts = _m("max_attempts")
        attempt = _m("attempt", 1 if isinstance(max_attempts, int) else None)
        if not isinstance(command, list):
            command = list(command) if command else []
        status_raw = getattr(desc, "status", None)
        status = getattr(status_raw, "name", str(status_raw)).lower()
        # normalize temporal statuses to our vocabulary
        mapping = {
            "running": "running",
            "completed": "succeeded",
            "failed": "failed",
            "canceled": "cancelled",
            "terminated": "cancelled",
            "timed_out": "failed",
        }
        status = mapping.get(status, status)
        close_time = getattr(desc, "close_time", None)
        start_time = getattr(desc, "start_time", None) or getattr(desc, "execution_time", None)
        # attempt to get return code from result not available in describe; leave None
        return Task(
            id=task_id,
            name=name if isinstance(name, str) else str(name),
            command=command,
            cwd=cwd if isinstance(cwd, str) else str(cwd),
            status=status,
            created_at=_iso(getattr(desc, "create_time", None) or start_time),
            started_at=_iso(start_time),
            finished_at=_iso(close_time),
            returncode=None,
            log_path=log_path if isinstance(log_path, str) else str(log_path),
            attempt=attempt if isinstance(attempt, int) else None,
            max_attempts=max_attempts if isinstance(max_attempts, int) else None,
            run_id=run_id or getattr(desc, "run_id", None),
        )

    # duck-typed helpers used by dashboard/CLI
    def refresh(self):
        return self.list()

    def clear_finished(self):
        return 0


# Backwards/alternative names
TaskManager = TemporalTaskManager
TaskQueue = TemporalTaskManager
TaskStore = TemporalTaskManager
