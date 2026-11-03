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
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

TASK_QUEUE = "smeltery-tasks"
WORKFLOW_TYPE = "ShellTaskWorkflow"


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
            return await workflow.execute_activity(
                run_shell_activity,
                spec,
                start_to_close_timeout=timedelta(hours=24),
                retry_policy=RetryPolicy(maximum_attempts=1),
            )

    TEMPORAL_AVAILABLE = True
except Exception:  # pragma: no cover - import guard
    TEMPORAL_AVAILABLE = False
    run_shell_activity = None
    ShellTaskWorkflow = None


def worker_activities():
    return [run_shell_activity] if TEMPORAL_AVAILABLE else []


def worker_workflows():
    return [ShellTaskWorkflow] if TEMPORAL_AVAILABLE else []


# ---------- client manager ----------


class TemporalTaskManager:
    def __init__(self, host: str = "127.0.0.1:7233", namespace: str = "default", task_queue: str = TASK_QUEUE):
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

    async def submit_async(self, name, command, *, cwd=None, env=None, log_dir=None, note=""):
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
        }
        handle = await client.start_workflow(
            WORKFLOW_TYPE,
            spec,
            id=tid,
            task_queue=self.task_queue,
            memo={"name": name or " ".join(command), "command": list(command), "cwd": cwd_path, "log_path": log_path},
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
            run_id=handle.run_id if hasattr(handle, "run_id") else None,
        )

    def submit(self, name, command, **kw):
        return self._run(self.submit_async(name, command, **kw))

    async def list_async(self, status: str | None = None) -> list[Task]:
        client = await self._client_or_connect()
        tasks: list[Task] = []
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
        if task.status == "succeeded":
            try:
                task.returncode = await handle.result()
            except Exception:
                pass
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
        p = Path(t.log_path)
        if not p.exists():
            return ""
        size = p.stat().st_size
        chunk = min(size, max(4096, lines * 256))
        with p.open("rb") as f:
            if size > chunk:
                f.seek(size - chunk)
            data = f.read().decode(errors="replace")
        return "\n".join(data.splitlines()[-lines:])

    def tail_log(self, task_id, lines=40):
        return self._run(self.tail_log_async(task_id, lines=lines))

    async def _to_task(self, client, wf) -> Task:
        try:
            handle = client.get_workflow_handle(wf.id, run_id=wf.run_id)
            desc = await handle.describe()
            task = await self._from_describe(wf.id, desc, run_id=wf.run_id)
            if task.status == "succeeded":
                try:
                    task.returncode = await handle.result()
                except Exception:
                    pass
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
