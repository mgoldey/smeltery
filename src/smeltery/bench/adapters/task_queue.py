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

from smeltery.bench.adapters._sqlite import open_db
from smeltery.tasks import Task, TemporalTaskManager


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
                    log_path TEXT, note TEXT, pid INTEGER)"""
            )
            con.commit()

    def _row_to_task(self, row) -> Task:
        (tid, name, command, cwd, status, created_at, started_at, finished_at, rc, log_path, note, pid) = row
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
        )
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
                "UPDATE tasks SET status=?, returncode=?, finished_at=? WHERE id=? AND status='running'",
                (new_status, rc, _utc(), tid),
            )
            con.commit()

    def submit(self, name, command, *, cwd=None, env=None, log_dir=None, note=""):
        tid = uuid.uuid4().hex[:12]
        log_dir = Path(log_dir) if log_dir else self.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{tid}.log"
        lf = open(log_path, "wb")
        env_all = dict(os.environ)
        if env:
            env_all.update(env)
        proc = subprocess.Popen(
            list(command),
            cwd=str(cwd) if cwd else None,
            stdout=lf,
            stderr=subprocess.STDOUT,
            env=env_all,
            start_new_session=True,
        )
        lf.close()

        def _wait():
            try:
                rc = proc.wait()
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

        with open_db(self.db) as con:
            con.execute(
                "INSERT INTO tasks (id,name,command,cwd,status,created_at,started_at,log_path,note,pid) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
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
                ),
            )
            con.commit()
        threading.Thread(target=_wait, daemon=True).start()
        return tid

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

    def submit(self, name, command, *, cwd=None, env=None, log_dir=None, note=""):
        t = self._m.submit(
            name,
            list(command),
            cwd=str(cwd) if cwd else None,
            env=env,
            log_dir=str(log_dir) if log_dir else None,
            note=note,
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
