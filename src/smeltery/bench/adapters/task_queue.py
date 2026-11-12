"""Task queue adapters."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from smeltery.bench.adapters import task_supervisor as _sup
from smeltery.bench.adapters._sqlite import add_column, migrate, open_db
from smeltery.bench.adapters.task_supervisor import (
    connect,
    kill_group,
    proc_identity,
    promote,
    same_process,
    spawn_supervisor,
    task_event,
    utc,
)
from smeltery.tasks import MAX_RETRIES, Task, TemporalTaskManager

_GB = 1024**3
_log = logging.getLogger(__name__)
#: `SubprocessTaskQueue` defines `list`, so inside its body the builtin is unreachable by name (as in smeltery.tasks).
_TaskList = list[Task]
_StrList = list[str]
_DictList = list[dict]

#: Columns added after the first schema, with their types. Every one is nullable, so an old row reads back as "the
#: backend cannot say". `owner_pid` is no longer written (the supervisor replaced the in-process waiter) but stays
#: in old databases.
_ADDED_COLUMNS = (
    ("attempt", "INTEGER"),
    ("max_attempts", "INTEGER"),
    ("owner_pid", "INTEGER"),
    ("heartbeat_at", "TEXT"),
    ("peak_rss_bytes", "INTEGER"),
    ("oom", "INTEGER"),
    ("memory_limit_bytes", "INTEGER"),
    ("supervisor_pid", "INTEGER"),  # the process that owns the task (task_supervisor.py) ...
    ("supervisor_start", "TEXT"),  # ... and its identity, so a reused pid is never trusted
    ("pid_start", "TEXT"),  # the identity of `pid`, the current attempt's process (its process group leader)
    ("spec", "TEXT"),  # JSON: what the supervisor needs beyond the columns (env overrides, intervals, timeout)
    ("capped", "INTEGER"),  # 1: a launched run, which waits in `queued` for one of max_concurrent_runs slots
)


def _add_task_columns(con: sqlite3.Connection) -> None:
    for col, kind in _ADDED_COLUMNS:
        add_column(con, "tasks", col, kind)


#: Schema steps; append only (see `_sqlite.migrate`). Version 2 adds `_ADDED_COLUMNS`, which an older database lacks.
_STEPS = (
    """CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, name TEXT, command TEXT, cwd TEXT, status TEXT,
        created_at TEXT, started_at TEXT, finished_at TEXT, returncode INTEGER,
        log_path TEXT, note TEXT, pid INTEGER)""",
    "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)",
    _add_task_columns,
)
SCHEMA_VERSION = len(_STEPS)

#: What `_row_to_task` reads, in order. Named, never `SELECT *`: the column order of a migrated table is not ours.
_TASK_COLUMNS = (
    "id,name,command,cwd,status,created_at,started_at,finished_at,returncode,log_path,note,"
    "attempt,max_attempts,heartbeat_at,peak_rss_bytes,oom,memory_limit_bytes"
)


#: Most tasks listed by default: enough to see what matters, few enough to poll every 1.5 s.
DEFAULT_LIST_LIMIT = 100
_ACTIVE = "('queued','running')"
#: `tail_log` reads backwards in blocks of this size, and never more than `_TAIL_MAX_BYTES` in all, so a 50 MB log
#: (or one giant `\r` progress line) costs the same as a short one.
_TAIL_BLOCK = 64 * 1024
_TAIL_MAX_BYTES = 4 * 1024 * 1024


def read_tail(path: str | Path, lines: int) -> str:
    """The last `lines` lines of a file, reading from the end instead of the whole file."""
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


def _age_s(iso: str) -> float:
    """Seconds since an ISO-8601 UTC stamp as `utc()` writes it. Unparseable reads as 0: a bad stamp must not
    make a healthy task look stalled."""
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        _log.debug("unparseable timestamp %r", iso, exc_info=True)
        return 0.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds()


class SubprocessTaskQueue:
    """Tasks as local processes, state in ROOT/tasks.sqlite.

    Each task runs under its own supervisor process (`task_supervisor.py`), started in a new session, which owns the
    task, writes its heartbeat, peak RSS and exit code, and enforces its memory limit and retries. This class only
    reads sqlite and sends signals, so a task survives the Bench process that submitted it, and a fresh queue on the
    same ROOT reads what the supervisor wrote. Opening a queue reconciles every `running` row whose supervisor is
    gone (the task is `failed` with a note saying so) and starts whatever is queued.

    A task submitted with `capped=True` (a launched run) starts only while fewer than `max_concurrent_runs` capped
    tasks are running, and otherwise waits as `queued`. The scheduling is in sqlite too: a finishing supervisor
    starts the next queued task, and so does opening the queue, so a queued run starts even if the Bench that
    queued it is gone.
    """

    retry_backoff_s = 1.0  # base delay before a retry; doubles per attempt, capped at 30 s
    #: A RUNNING task silent for longer than this reads as `stalled`. Minutes, not seconds: an SCF legitimately
    #: prints nothing for a long time, and calling that dead is the mistake this flag exists to avoid making.
    stall_after_s = 300.0
    #: How often the supervisor reads the task's memory and log. Cheap (two /proc reads), so it can be frequent
    #: enough that a cap fires before the node is in trouble.
    sample_interval_s = 1.0

    def __init__(self, root: str | Path, *, max_concurrent_runs: int = 1, spawn=spawn_supervisor):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "tasks.sqlite"
        self.log_dir = self.root / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._spawn = spawn  # a seam for tests: how a task's supervisor is started
        self._init_db()
        self.max_concurrent_runs = max_concurrent_runs
        self._reconcile()
        promote(self.db, self._spawn)

    def _init_db(self) -> None:
        with open_db(self.db, wal=True) as con:
            migrate(con, _STEPS)

    @property
    def max_concurrent_runs(self) -> int:
        """How many capped tasks (launched runs) may run at once. Stored in the database, because the supervisors
        that start the next queued run read it there."""
        with open_db(self.db) as con:
            return _sup.max_concurrent_runs(con)

    @max_concurrent_runs.setter
    def max_concurrent_runs(self, n: int) -> None:
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"max_concurrent_runs must be a positive integer, got {n!r}")
        with open_db(self.db) as con:
            con.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('max_concurrent_runs', ?)", (str(n),))
        promote(self.db, self._spawn)  # a raised cap frees slots now

    def _row_to_task(self, row) -> Task:
        (
            tid,
            name,
            command,
            cwd,
            status,
            created_at,
            started_at,
            finished_at,
            rc,
            log_path,
            note,
            attempt,
            max_attempts,
            heartbeat_at,
            peak_rss_bytes,
            oom,
            memory_limit_bytes,
        ) = row
        t = Task(
            id=tid,
            name=name,
            command=json.loads(command),
            cwd=cwd or "",
            status=status,
            created_at=created_at,
            started_at=started_at,
            finished_at=finished_at,
            returncode=rc,
            log_path=log_path or "",
            note=note or "",
            attempt=attempt,
            max_attempts=max_attempts,
            heartbeat_at=heartbeat_at,
            peak_rss_gb=(peak_rss_bytes / _GB) if peak_rss_bytes else None,
            oom=bool(oom),
            memory_limit_gb=(memory_limit_bytes / _GB) if memory_limit_bytes else None,
        )
        # Age and stall are derived on READ, not stored: a stored "stalled" would be stale the moment it was
        # written, and the threshold belongs to whoever is asking, not to the row.
        if heartbeat_at:
            t.heartbeat_age_s = max(0.0, round(_age_s(heartbeat_at), 3))
            # Only a running task can be stalled. A finished one is silent because it is done.
            t.stalled = status == "running" and t.heartbeat_age_s >= self.stall_after_s
        return t

    def _reconcile(self, task_id: str | None = None) -> None:
        """Fail every running task (or just `task_id`) whose supervisor is gone, saying why.

        A supervisor writes the task's end before it exits, so a running row with no live supervisor will never be
        finished by anyone. A supervisor pid whose identity no longer matches belongs to another process (a reboot,
        or the pid was reused): it is never signalled. The task's own process is killed only when its identity
        still matches, so the concurrency it held is really released.
        """
        sql = "SELECT id,supervisor_pid,supervisor_start,pid,pid_start FROM tasks WHERE status='running'"
        with open_db(self.db) as con:
            rows = con.execute(*((sql + " AND id=?", (task_id,)) if task_id else (sql,))).fetchall()
        for tid, spid, sstart, pid, pstart in rows:
            if same_process(spid, sstart):
                continue
            if spid is None:
                why = "supervisor lost: the task was started by a Bench without task supervisors"
            elif proc_identity(spid) is None:
                why = "supervisor lost: it exited without recording how the task ended"
            else:
                why = "supervisor lost: its pid now belongs to another process (reboot or pid reuse); not signalled"
            orphan = same_process(pid, pstart)
            if orphan:
                why += "; the task's own process was still running and was killed"
            with connect(self.db, immediate=True) as con:
                changed = con.execute(
                    "UPDATE tasks SET status='failed', finished_at=?, note=? "
                    "WHERE id=? AND status='running' AND supervisor_pid IS ?",
                    (utc(), why, tid, spid),
                ).rowcount
            if changed:
                task_event(
                    tid, "supervisor_lost", level=logging.WARNING, supervisor_pid=spid, orphan_killed=bool(orphan)
                )
            if changed and orphan and pid:
                kill_group(pid)

    def submit(
        self,
        name,
        command,
        *,
        cwd=None,
        env=None,
        log_dir=None,
        note="",
        retries: int = 0,
        memory_limit_gb: float | None = None,
        timeout_s: float | None = None,
        capped: bool = False,
    ):
        if not isinstance(retries, int) or isinstance(retries, bool) or not 0 <= retries <= MAX_RETRIES:
            raise ValueError(f"retries must be an integer from 0 to {MAX_RETRIES}, got {retries!r}")
        if memory_limit_gb is not None and (
            isinstance(memory_limit_gb, bool) or not isinstance(memory_limit_gb, (int, float)) or memory_limit_gb <= 0
        ):
            raise ValueError(f"memory_limit_gb must be a positive number of GB or None, got {memory_limit_gb!r}")
        if timeout_s is not None and (
            isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or timeout_s <= 0
        ):
            raise ValueError(f"timeout_s must be a positive number of seconds or None, got {timeout_s!r}")
        tid = uuid.uuid4().hex[:12]
        log_dir = Path(log_dir) if log_dir else self.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{tid}.log"
        log_path.touch()
        spec = {
            "env": dict(env or {}),  # overrides only: the supervisor inherits the rest of this process's environment
            "retry_backoff_s": self.retry_backoff_s,
            "sample_interval_s": self.sample_interval_s,
            "timeout_s": timeout_s,
        }
        with open_db(self.db) as con:
            con.execute(
                "INSERT INTO tasks (id,name,command,cwd,status,created_at,log_path,note,max_attempts,"
                "memory_limit_bytes,oom,spec,capped) VALUES (?,?,?,?,'queued',?,?,?,?,?,0,?,?)",
                (
                    tid,
                    name or "",
                    json.dumps(list(command)),
                    str(cwd) if cwd else "",
                    utc(),
                    str(log_path),
                    note,
                    retries + 1,
                    int(memory_limit_gb * _GB) if memory_limit_gb else None,
                    json.dumps(spec),
                    int(bool(capped)),
                ),
            )
        task_event(tid, "submit", task_name=name or None, capped=bool(capped), retries=retries)
        promote(self.db, self._spawn)
        return tid

    def _read(self, where: str = "", args: tuple = ()) -> _TaskList:
        with open_db(self.db) as con:
            rows = con.execute(f"SELECT {_TASK_COLUMNS} FROM tasks {where}", args).fetchall()
        return [self._row_to_task(r) for r in rows]

    def list(self, status=None, limit: int | None = DEFAULT_LIST_LIMIT):
        """At most `limit` tasks (None: all), newest first. Queued and running tasks are chosen before finished
        ones, so a long history never pushes live work off the panel; the result is then ordered by creation."""
        self._reconcile()
        promote(self.db, self._spawn)
        where, args = ("WHERE status=?", (status,)) if status else ("", ())
        if limit is None:
            return self._read(f"{where} ORDER BY created_at DESC", args)
        if limit < 1:
            raise ValueError(f"limit must be a positive integer or None, got {limit!r}")
        inner = (
            f"SELECT {_TASK_COLUMNS} FROM tasks {where} ORDER BY (status IN {_ACTIVE}) DESC, created_at DESC LIMIT ?"
        )
        with open_db(self.db) as con:
            rows = con.execute(f"SELECT * FROM ({inner}) ORDER BY created_at DESC", (*args, limit)).fetchall()
        return [self._row_to_task(r) for r in rows]

    def get(self, task_id):
        self._reconcile(task_id)
        promote(self.db, self._spawn)
        found = self._read("WHERE id=?", (task_id,))
        return found[0] if found else None

    def status(self, task_id):
        t = self.get(task_id)
        if t is None:
            raise KeyError(task_id)
        return t.status

    def cancel(self, task_id):
        """Mark the task cancelled, then signal its process group, if the recorded pid is still that process. The
        mark comes first: the supervisor's end-of-task write is guarded by status='running', and a supervisor that
        sees the mark stops the task itself (it is the one that can, if the signal here could not be sent). A queued
        task is never started afterwards: promotion only picks `queued` rows, under the same lock as this write."""
        if self.get(task_id) is None:
            raise KeyError(task_id)
        with connect(self.db, immediate=True) as con:
            row = con.execute("SELECT status,pid,pid_start FROM tasks WHERE id=?", (task_id,)).fetchone()
            changed = con.execute(
                "UPDATE tasks SET status='cancelled', finished_at=? WHERE id=? AND status IN ('queued','running')",
                (utc(), task_id),
            ).rowcount
        if not changed:
            return False
        status, pid, pid_start = row
        task_event(task_id, "cancel", was=status)
        if status == "running" and same_process(pid, pid_start):
            kill_group(pid)
        promote(self.db, self._spawn)
        return True

    def live_tasks(self) -> _DictList:
        self._reconcile()  # a running row whose supervisor is gone is not live
        with open_db(self.db) as con:
            rows = con.execute(
                "SELECT id,name,status,pid,supervisor_pid FROM tasks WHERE status IN ('queued','running') "
                "ORDER BY created_at"
            ).fetchall()
        return [{"id": i, "name": n, "status": st, "pid": pid, "supervisor_pid": spid} for i, n, st, pid, spid in rows]

    def stop_live(self, reason: str) -> _StrList:
        """Cancel every queued task and terminate every running one. The marks are written first and in one
        transaction, so nothing is promoted while the running ones die (a finishing supervisor promotes the next
        queued task). A process group is signalled only while the recorded pid still has the recorded identity: a
        reused pid is never touched."""
        with connect(self.db, immediate=True) as con:
            rows = con.execute(
                "SELECT id,status,pid,pid_start FROM tasks WHERE status IN ('queued','running')"
            ).fetchall()
            con.execute(
                "UPDATE tasks SET status='cancelled', finished_at=?, note=? WHERE status IN ('queued','running')",
                (utc(), reason),
            )
        groups = [pid for _, status, pid, start in rows if status == "running" and pid and same_process(pid, start)]
        for pgid in groups:  # ask them all first, so the grace periods overlap instead of adding up
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, signal.SIGTERM)
        for pgid in groups:
            kill_group(pgid)
        for tid, status, *_ in rows:
            task_event(tid, "stop", was=status, reason=reason)
        return [tid for tid, *_ in rows]

    def ready(self):
        try:
            with contextlib.closing(sqlite3.connect(f"file:{self.db}?mode=rw", uri=True)) as con:
                con.execute("SELECT 1 FROM tasks LIMIT 1")
        except sqlite3.Error as e:
            _log.warning("task queue not ready: %s", e, exc_info=True)
            return False, str(e)
        return True, str(self.db)

    def tail_log(self, task_id, lines=40):
        t = self.get(task_id)
        if t is None:
            raise KeyError(task_id)
        try:
            return read_tail(t.log_path, lines) if t.log_path else ""
        except FileNotFoundError:
            return ""


class TemporalTaskQueue:
    def __init__(
        self, host: str = "127.0.0.1:7233", namespace: str = "default", task_queue: str = "smeltery-tasks"
    ) -> None:  # noqa: E501
        self._m = TemporalTaskManager(host=host, namespace=namespace, task_queue=task_queue)

    def submit(
        self,
        name,
        command,
        *,
        cwd=None,
        env=None,
        log_dir=None,
        note="",
        retries: int = 0,
        memory_limit_gb: float | None = None,
        timeout_s: float | None = None,
        capped: bool = False,  # not used: Temporal's workers bound how much runs at once
    ):
        if timeout_s is not None:
            raise ValueError(
                "timeout_s is not enforced by the Temporal queue. Submit without one, or use the subprocess queue."
            )
        if memory_limit_gb is not None:
            # Refused, not ignored: this backend runs the command on a Temporal worker it does not sample, so
            # accepting a cap would promise an enforcement that never happens. The port says a backend that
            # cannot enforce it must not pretend to.
            raise ValueError(
                "memory_limit_gb is not enforced by the Temporal queue: it does not sample the worker's memory. "
                "Submit without a limit, or use the subprocess queue."
            )
        t = self._m.submit(
            name,
            list(command),
            cwd=str(cwd) if cwd else None,
            env=env,
            log_dir=str(log_dir) if log_dir else None,
            note=note,
            retries=retries,
        )
        return t.id if hasattr(t, "id") else str(t)

    def list(self, status=None, limit: int | None = DEFAULT_LIST_LIMIT):
        tasks = self._m.list(status=status)  # newest first
        return tasks if limit is None else tasks[:limit]

    def get(self, task_id):
        return self._m.get(task_id)

    def status(self, task_id):
        t = self.get(task_id)
        if t is None:
            raise KeyError(task_id)
        return t.status

    def cancel(self, task_id):
        t = self.get(task_id)
        if t is None:
            raise KeyError(task_id)
        return self._m.cancel(task_id)

    def ready(self):
        return True, "temporal: not probed"

    def tail_log(self, task_id, lines=40):
        t = self.get(task_id)
        if t is None:
            raise KeyError(task_id)
        return self._m.tail_log(task_id, lines=lines)
