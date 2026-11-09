"""Task queue adapters."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path

from smeltery.bench.adapters._sqlite import is_duplicate_column, open_db
from smeltery.tasks import MAX_RETRIES, Task, TemporalTaskManager

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _kill_group(pgid: int, grace: float = 2.0) -> None:
    """SIGTERM the task's process group (it is its own session leader), then SIGKILL what is left after `grace`."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError):
        os.killpg(pgid, signal.SIGKILL)


_LIVE_WAITERS: set[str] = set()  # task ids whose waiter thread is alive in this process


_GB = 1024**3


def _age_s(iso: str) -> float:
    """Seconds since an ISO-8601 UTC stamp as `_utc()` writes it. Unparseable reads as 0: a bad stamp must not
    make a healthy task look stalled."""
    from datetime import datetime, timezone

    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return 0.0
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - t).total_seconds()


def _rss_bytes(pid: int) -> int | None:
    """Resident memory of `pid` AND its children, which is what a limit must count: smeltery's tasks shell out,
    and a cap that ignored the children would never fire. None when it cannot be read (the process is gone, or
    this is not a /proc system)."""
    total, seen = 0, False
    for p in (pid, *_descendants(pid)):
        try:
            with open(f"/proc/{p}/statm") as fh:
                pages = int(fh.read().split()[1])
        except (OSError, IndexError, ValueError):
            continue
        total += pages * _PAGE_SIZE
        seen = True
    return total if seen else None


def _descendants(pid: int) -> list[int]:
    """Every process under `pid`, walked through /proc. Empty when it cannot be determined."""
    children: dict[int, list[int]] = {}
    try:
        pids = [int(e) for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return []
    for p in pids:
        try:
            with open(f"/proc/{p}/stat") as fh:
                # The comm field can contain spaces and parens, so parse after the LAST ')'.
                rest = fh.read().rpartition(")")[2].split()
            children.setdefault(int(rest[1]), []).append(p)
        except (OSError, IndexError, ValueError):
            continue
    out, stack = [], list(children.get(pid, []))
    while stack:
        p = stack.pop()
        out.append(p)
        stack.extend(children.get(p, []))
    return out


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class SubprocessTaskQueue:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "tasks.sqlite"
        self.log_dir = self.root / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with open_db(self.db, wal=True) as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, name TEXT, command TEXT, cwd TEXT, status TEXT,
                    created_at TEXT, started_at TEXT, finished_at TEXT, returncode INTEGER,
                    log_path TEXT, note TEXT, pid INTEGER, attempt INTEGER, max_attempts INTEGER,
                    owner_pid INTEGER, heartbeat_at TEXT, peak_rss_bytes INTEGER, oom INTEGER,
                    memory_limit_bytes INTEGER)"""
            )
            # Migrate databases created before retries (attempt..owner_pid) or worker health (the rest) existed.
            # Every added column is nullable, so an old row reads back as "the backend cannot say".
            for col in (
                "attempt",
                "max_attempts",
                "owner_pid",
                "heartbeat_at",
                "peak_rss_bytes",
                "oom",
                "memory_limit_bytes",
            ):
                try:
                    kind = "TEXT" if col == "heartbeat_at" else "INTEGER"
                    con.execute(f"ALTER TABLE tasks ADD COLUMN {col} {kind}")
                except sqlite3.OperationalError as e:
                    if not is_duplicate_column(e):
                        raise
            con.commit()

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
            pid,
            attempt,
            max_attempts,
            _owner_pid,
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

    def _refresh(self, task_id: str) -> None:
        with open_db(self.db) as con:
            row = con.execute("SELECT id,status,pid,started_at FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not row:
                return
            tid, status, pid, started_at = row
            if status in ("succeeded", "failed", "cancelled"):
                return
            if pid is None:
                # Between attempts there is no process; only the waiter thread of the submitting process can
                # resume the task. If that process is gone (bench restarted), nothing ever will.
                owner = con.execute("SELECT owner_pid FROM tasks WHERE id=?", (tid,)).fetchone()[0]
                if owner is None or (_pid_alive(owner) and not (owner == os.getpid() and tid not in _LIVE_WAITERS)):
                    return
                con.execute(
                    "UPDATE tasks SET status='failed', finished_at=?, note=? WHERE id=? AND status='running' "
                    "AND pid IS NULL",
                    (_utc(), "bench restarted during retry backoff", tid),
                )
                con.commit()
                return
        # check process
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
        if alive:
            return
        # The owning waiter thread (if any) writes status and returncode together.
        # Give it a brief window so a successful task is never observed as failed.
        for _ in range(50):
            time.sleep(0.01)
            with open_db(self.db) as con:
                row = con.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
            if row and row[0] in ("succeeded", "failed", "cancelled"):
                return
        # re-check liveness
        try:
            os.kill(pid, 0)
            alive = True
        except ProcessLookupError:
            alive = False
        except PermissionError:
            alive = True
        if alive:
            return
        # Reap the exit code if we still can (orphaned / vanished processes).
        rc = None
        try:
            waited_pid, wstatus = os.waitpid(pid, os.WNOHANG)
            if waited_pid == pid:
                if os.WIFEXITED(wstatus):
                    rc = os.WEXITSTATUS(wstatus)
                elif os.WIFSIGNALED(wstatus):
                    rc = -os.WTERMSIG(wstatus)
        except (ChildProcessError, OSError):
            rc = None
        new_status = "succeeded" if rc == 0 else "failed"
        with open_db(self.db) as con:
            con.execute(
                "UPDATE tasks SET status=?, returncode=?, finished_at=? WHERE id=? AND status='running' AND pid=?",
                (new_status, rc, _utc(), tid, pid),  # pid guard: a retry may have replaced the dead process
            )
            con.commit()

    retry_backoff_s = 1.0  # base delay before a retry; doubles per attempt, capped at 30 s
    #: A RUNNING task silent for longer than this reads as `stalled`. Minutes, not seconds: an SCF legitimately
    #: prints nothing for a long time, and calling that dead is the mistake this flag exists to avoid making.
    stall_after_s = 300.0
    #: How often the sampler reads the task's memory and log. Cheap (two /proc reads), so it can be frequent
    #: enough that a cap fires before the node is in trouble.
    sample_interval_s = 1.0

    #: RSS read at spawn, keyed by pid: a task can exit before the first sampling pass, and /proc is gone the
    #: moment it does, so the only reading guaranteed to exist is the one taken as the process starts.
    _spawn_rss: dict[int, int] = {}

    def _spawn(self, command, cwd, env_all, log_path: Path, header: str = ""):
        proc = self._spawn_raw(command, cwd, env_all, log_path, header)
        rss = _rss_bytes(proc.pid)
        if rss is not None:
            self._spawn_rss[proc.pid] = rss
        return proc

    def _spawn_raw(self, command, cwd, env_all, log_path: Path, header: str = ""):
        with open(log_path, "ab") as lf:
            if header:
                lf.write(header.encode())
                lf.flush()
            return subprocess.Popen(
                list(command),
                cwd=str(cwd) if cwd else None,
                stdout=lf,
                stderr=subprocess.STDOUT,
                env=env_all,
                start_new_session=True,
            )

    def _is_running(self, tid: str) -> bool:
        """False once the task is cancelled or otherwise resolved (e.g. by `_refresh`): stop retrying."""
        with open_db(self.db) as con:
            row = con.execute("SELECT status FROM tasks WHERE id=?", (tid,)).fetchone()
        return row is not None and row[0] == "running"

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
    ):
        if not isinstance(retries, int) or isinstance(retries, bool) or not 0 <= retries <= MAX_RETRIES:
            raise ValueError(f"retries must be an integer from 0 to {MAX_RETRIES}, got {retries!r}")
        if memory_limit_gb is not None and (
            isinstance(memory_limit_gb, bool) or not isinstance(memory_limit_gb, (int, float)) or memory_limit_gb <= 0
        ):
            raise ValueError(f"memory_limit_gb must be a positive number of GB or None, got {memory_limit_gb!r}")
        limit_bytes = int(memory_limit_gb * _GB) if memory_limit_gb else None
        max_attempts = retries + 1
        tid = uuid.uuid4().hex[:12]
        log_dir = Path(log_dir) if log_dir else self.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{tid}.log"
        env_all = dict(os.environ)
        if env:
            env_all.update(env)

        def _header(n: int) -> str:
            return f"--- attempt {n}/{max_attempts} ---\n" if max_attempts > 1 else ""

        proc = self._spawn(command, cwd, env_all, log_path, _header(1))

        def _wait(proc):
            attempt = 1
            while True:
                try:
                    rc = proc.wait()
                except Exception:
                    rc = None
                if rc != 0 and attempt < max_attempts and self._is_running(tid):
                    # Between attempts there is no process: pid NULL keeps _refresh from calling the task dead.
                    try:
                        with open_db(self.db) as con:
                            con.execute("UPDATE tasks SET pid=NULL WHERE id=? AND status='running'", (tid,))
                            con.commit()
                    except Exception:
                        pass
                    deadline = time.monotonic() + min(self.retry_backoff_s * 2 ** (attempt - 1), 30.0)
                    while time.monotonic() < deadline and self._is_running(tid):
                        time.sleep(0.05)
                    if not self._is_running(tid):
                        return
                    attempt += 1
                    try:
                        proc = self._spawn(command, cwd, env_all, log_path, _header(attempt))
                        with open_db(self.db) as con:
                            changed = con.execute(
                                "UPDATE tasks SET pid=?, attempt=? WHERE id=? AND status='running'",
                                (proc.pid, attempt, tid),
                            ).rowcount
                            con.commit()
                        if not changed:  # cancelled while the process was starting
                            _kill_group(proc.pid)
                            return
                        continue
                    except Exception:
                        rc = None
                new_status = "succeeded" if rc == 0 else "failed"
                try:
                    with open_db(self.db) as con:
                        con.execute(
                            "UPDATE tasks SET status=?, returncode=?, finished_at=? WHERE id=? AND status NOT IN ('cancelled')",  # noqa: E501
                            (new_status, rc, _utc(), tid),
                        )
                        con.commit()
                except Exception:
                    pass
                return

        with open_db(self.db) as con:
            con.execute(
                "INSERT INTO tasks (id,name,command,cwd,status,created_at,started_at,log_path,note,pid,"
                "attempt,max_attempts,owner_pid,heartbeat_at,memory_limit_bytes,oom) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    tid,
                    name or "",
                    json.dumps(list(command)),
                    str(cwd) if cwd else "",
                    "running",
                    _utc(),
                    _utc(),
                    str(log_path),
                    note,
                    proc.pid,
                    1,
                    max_attempts,
                    os.getpid(),
                    _utc(),  # a task is alive the moment it starts; silence is measured from here
                    limit_bytes,
                    0,
                ),
            )
            con.commit()

        def _run_waiter():
            try:
                _wait(proc)
            finally:
                _LIVE_WAITERS.discard(tid)

        _LIVE_WAITERS.add(tid)
        # One reading before any thread starts. A task can finish inside a sampling interval, and a peak that was
        # never read is indistinguishable from a backend that cannot read one, so every task gets at least this.
        startup_rss = self._spawn_rss.pop(proc.pid, None) or _rss_bytes(proc.pid)
        if startup_rss is not None:
            with contextlib.suppress(Exception):
                with open_db(self.db) as con:
                    con.execute(
                        "UPDATE tasks SET peak_rss_bytes=MAX(COALESCE(peak_rss_bytes,0),?) WHERE id=?",
                        (int(startup_rss), tid),
                    )
                    con.commit()
        threading.Thread(target=_run_waiter, daemon=True).start()
        threading.Thread(target=self._sample, args=(tid, log_path, limit_bytes), daemon=True).start()
        return tid

    def _sample(self, tid: str, log_path: Path, limit_bytes: int | None) -> None:
        """Watch one task: its log's growth is the heartbeat, its RSS is the memory check.

        Runs until the task is no longer running, so a retry's new process is picked up by the same loop (the pid
        is re-read every pass). Everything it writes is advisory except the kill, which is the point of the limit.
        """
        last_size = -1
        first = True
        while True:
            # Sample at once, then on the interval: a short task can finish inside one interval, and a peak that
            # was never read is reported as "nothing sampled it" -- indistinguishable from a backend that cannot.
            if not first:
                time.sleep(self.sample_interval_s)
            first = False
            try:
                with open_db(self.db) as con:
                    row = con.execute("SELECT status,pid,peak_rss_bytes FROM tasks WHERE id=?", (tid,)).fetchone()
                if row is None or row[0] != "running":
                    return  # terminal: the peak written on the last pass stands
                _status, pid, peak = row
                try:
                    size = log_path.stat().st_size
                except OSError:
                    size = last_size
                beat = size != last_size
                last_size = size
                rss = _rss_bytes(pid) if pid else None
                if rss is not None and rss > (peak or 0):
                    peak = rss
                sets, args = [], []
                if beat:
                    sets.append("heartbeat_at=?")
                    args.append(_utc())
                if peak is not None:
                    sets.append("peak_rss_bytes=MAX(COALESCE(peak_rss_bytes,0),?)")
                    args.append(int(peak))
                if sets:
                    with open_db(self.db) as con:
                        con.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", (*args, tid))
                        con.commit()
                if limit_bytes is not None and rss is not None and rss > limit_bytes:
                    self._kill_for_oom(tid, pid, rss, limit_bytes)
                    return
            except Exception:
                # A sampler must never take the task down with it: a failed read is skipped, not fatal.
                continue

    def _kill_for_oom(self, tid: str, pid: int, rss: int, limit_bytes: int) -> None:
        """Kill an over-limit task and record WHY, so "failed" is not the only thing the user is told.

        Marked terminal before the kill, so the waiter thread cannot race it into a retry: exceeding the cap is a
        verdict about this command, and re-running it would exceed the cap again.
        """
        note = f"OOM at {rss / _GB:.2f} GB, over the {limit_bytes / _GB:.2f} GB limit"
        with open_db(self.db) as con:
            con.execute(
                "UPDATE tasks SET status='failed', oom=1, finished_at=?, note=?, peak_rss_bytes=? "
                "WHERE id=? AND status='running'",
                (_utc(), note, int(rss), tid),
            )
            con.commit()
        _kill_group(pid)

    def list(self, status=None):
        with open_db(self.db) as con:
            if status:
                rows = con.execute("SELECT * FROM tasks WHERE status=?", (status,)).fetchall()
            else:
                rows = con.execute("SELECT * FROM tasks").fetchall()
        for r in rows:
            self._refresh(r[0])
        # re-read
        with open_db(self.db) as con:
            if status:
                rows = con.execute("SELECT * FROM tasks WHERE status=?", (status,)).fetchall()
            else:
                rows = con.execute("SELECT * FROM tasks").fetchall()
        return [self._row_to_task(r) for r in rows]

    def get(self, task_id):
        self._refresh(task_id)
        with open_db(self.db) as con:
            row = con.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return None
        return self._row_to_task(row)

    def status(self, task_id):
        t = self.get(task_id)
        if t is None:
            raise KeyError(task_id)
        return t.status

    def cancel(self, task_id):
        t = self.get(task_id)
        if t is None:
            raise KeyError(task_id)
        if t.status in ("succeeded", "failed", "cancelled"):
            return False
        # mark cancelled first: the waiter thread's write is guarded by status NOT IN ('cancelled')
        with open_db(self.db) as con:
            changed = con.execute(
                "UPDATE tasks SET status='cancelled', finished_at=? "
                "WHERE id=? AND status NOT IN ('succeeded','failed','cancelled')",
                (_utc(), task_id),
            ).rowcount
            pid = con.execute("SELECT pid FROM tasks WHERE id=?", (task_id,)).fetchone()
            con.commit()
        if not changed:
            return False
        if pid and pid[0]:
            _kill_group(pid[0])
        return True

    def ready(self):
        try:
            with contextlib.closing(sqlite3.connect(f"file:{self.db}?mode=rw", uri=True)) as con:
                con.execute("SELECT 1 FROM tasks LIMIT 1")
        except sqlite3.Error as e:
            return False, str(e)
        return True, str(self.db)

    def tail_log(self, task_id, lines=40):
        t = self.get(task_id)
        if t is None:
            raise KeyError(task_id)
        p = Path(t.log_path)
        if not p.exists():
            return ""
        data = p.read_text(errors="replace").splitlines()
        return "\n".join(data[-lines:])


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
    ):
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

    def list(self, status=None):
        return self._m.list(status=status)

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
