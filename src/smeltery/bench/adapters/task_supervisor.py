"""The process that owns one task, so a task outlives the Bench process that submitted it.

`SubprocessTaskQueue` starts one supervisor per task, in its own session, as

    python -I .../task_supervisor.py DB TASK_ID

and from then on only reads sqlite and sends signals. The supervisor is the task's parent: it alone can reap the exit
code, so it writes the heartbeat, the peak RSS and the final status and returncode to sqlite itself, and it enforces
the memory limit, the timeout and the retries. When it finishes it promotes the next queued task that may start
now, which is what keeps the queue moving while no Bench process is up.

Every pid stored here is stored with its identity (boot id + the start time from /proc/<pid>/stat, field 22), and no
pid is trusted or signalled unless its identity still matches: after a reboot or PID reuse a stored pid can name an
unrelated process.

Standard library only, and run by path rather than with `-m`: importing the `smeltery` package pulls in RDKit and
the rest of the funnel, a cost every task would pay for nothing. That is also why the logging setup shared with
Bench (`setup_logging`, `JsonFormatter`) lives here: a supervisor cannot import a sibling module.

Logging: every lifecycle transition is logged on the `smeltery.bench.tasks` logger with the task id (`task_event`).
A supervisor outlives the Bench that started it and inherits its stderr and its `SMELTERY_BENCH_LOG_LEVEL` /
`SMELTERY_BENCH_LOG_JSON`, so one task's trail reads the same whichever process wrote the line. Never log a
command line, an environment or a token: they can hold secrets.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from pathlib import Path

BUSY_TIMEOUT_S = 30
LOG_LEVEL_ENV = "SMELTERY_BENCH_LOG_LEVEL"
LOG_JSON_ENV = "SMELTERY_BENCH_LOG_JSON"
_log = logging.getLogger("smeltery.bench.tasks")
_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
_GB = 1024**3


def utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------------------------------- logging

_HANDLER_NAME = "smeltery-bench"
_STANDARD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, logger, message, every `extra` field, and `exc_info` as `exc`."""

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        out.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS and not k.startswith("_")})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging(level: str = "info", as_json: bool = False) -> None:
    """Install the one stderr handler on the root logger, replacing any this function installed before."""
    root = logging.getLogger()
    for h in [h for h in root.handlers if h.get_name() == _HANDLER_NAME]:
        root.removeHandler(h)
    handler = logging.StreamHandler()  # stderr
    handler.set_name(_HANDLER_NAME)
    handler.setFormatter(
        JsonFormatter() if as_json else logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level.upper())


def log_env_requested() -> tuple[str, bool] | None:
    """The level and JSON choice a parent Bench exported to its supervisors; None when it exported neither."""
    level, as_json = os.environ.get(LOG_LEVEL_ENV), os.environ.get(LOG_JSON_ENV, "").lower() in ("1", "true", "yes")
    if level is None and not as_json:
        return None
    level = (level or "info").upper()
    return (level if level in logging.getLevelNamesMapping() else "INFO"), as_json


def task_event(task_id: str | None, event: str, *, level: int = logging.INFO, exc_info: bool = False, **fields) -> None:
    """Log one task lifecycle transition: `task <id> <event> k=v ...`, with `task_id`, `event` and the fields as
    structured extras for `--log-json`."""
    detail = "".join(f" {k}={v}" for k, v in fields.items() if v is not None)
    _log.log(
        level,
        "task %s %s%s",
        task_id,
        event,
        detail,
        exc_info=exc_info,
        extra={"task_id": task_id, "event": event, **fields},
    )


@contextlib.contextmanager
def connect(db: str | Path, *, immediate: bool = False) -> Generator[sqlite3.Connection]:
    """Like `_sqlite.open_db` (commit on success, roll back on error, always close), plus `immediate`: take the
    write lock up front (BEGIN IMMEDIATE), so a read-then-write cannot interleave with another process's."""
    con = sqlite3.connect(db, timeout=BUSY_TIMEOUT_S, isolation_level=None)
    try:
        con.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_S * 1000}")
        con.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield con
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")
    finally:
        con.close()


# ---------------------------------------------------------------------------------------------------- /proc


def _boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        _log.debug("no boot id: /proc/sys/kernel/random/boot_id is unreadable", exc_info=True)
        return ""


_BOOT_ID = _boot_id()


def proc_identity(pid: int | None) -> str | None:
    """`<boot id>:<start time>` of a live `pid`; None when it is gone, a zombie, or unreadable. Two readings are
    equal only for the same process: a reused pid has a later start time, and a reboot changes the boot id."""
    if not pid:
        return None
    try:
        with open(f"/proc/{pid}/stat") as fh:
            # comm (field 2) can hold spaces and parens: split after the LAST ')'. Then field 3 is rest[0].
            rest = fh.read().rpartition(")")[2].split()
    except OSError:
        return None
    if len(rest) < 20 or rest[0] in ("Z", "X"):
        return None
    return f"{_BOOT_ID}:{rest[19]}"  # field 22, starttime


def same_process(pid: int | None, identity: str | None) -> bool:
    """Whether `pid` is still the process whose identity was recorded. An unrecorded identity never matches."""
    return bool(pid) and identity is not None and proc_identity(pid) == identity


def descendants(pid: int) -> list[int]:
    """Every process under `pid`, walked through /proc. Empty when it cannot be determined."""
    children: dict[int, list[int]] = {}
    try:
        pids = [int(e) for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return []
    for p in pids:
        try:
            with open(f"/proc/{p}/stat") as fh:
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


def rss_bytes(pid: int) -> int | None:
    """Resident memory of `pid` AND its children, which is what a limit must count: smeltery's tasks shell out,
    and a cap that ignored the children would never fire. None when it cannot be read."""
    total, seen = 0, False
    for p in (pid, *descendants(pid)):
        try:
            with open(f"/proc/{p}/statm") as fh:
                pages = int(fh.read().split()[1])
        except (OSError, IndexError, ValueError):
            continue
        total += pages * _PAGE_SIZE
        seen = True
    return total if seen else None


def kill_group(pgid: int, grace: float = 2.0) -> None:
    """SIGTERM the task's process group (the task is its own session leader), then SIGKILL what is left after
    `grace`. The caller must have checked the pid's identity first."""
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        _log.warning("not permitted to signal process group %s", pgid, exc_info=True)
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError):
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        _log.warning("not permitted to SIGKILL process group %s", pgid, exc_info=True)


# ---------------------------------------------------------------------------------------------------- scheduling

#: Popen handles of supervisors this process started, kept so they are reaped (a zombie supervisor would otherwise
#: sit in the process table until this process exits). Only the reaping lives here, never any task state.
_STARTED: list[subprocess.Popen] = []


def spawn_supervisor(db: str | Path, task_id: str) -> subprocess.Popen:
    _STARTED[:] = [p for p in _STARTED if p.poll() is None]
    p = subprocess.Popen(
        [sys.executable, "-I", str(Path(__file__).resolve()), str(db), task_id],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        # Inherited only when `smeltery bench` exported a logging choice, so the supervisor's lines reach the same place
        # Bench's do. Otherwise (a library caller, a test) the supervisor must not hold open a pipe its parent's own
        # caller is reading to EOF: that caller would wait for the task, not just for the submitter.
        stderr=None if log_env_requested() else subprocess.DEVNULL,
        start_new_session=True,  # Ctrl+C on Bench, or Bench exiting, must not reach the task
        close_fds=True,
    )
    _STARTED.append(p)
    return p


def max_concurrent_runs(con: sqlite3.Connection) -> int:
    """The queue's run-slot count, as the queue last stored it in the `settings` table (default 1)."""
    row = con.execute("SELECT value FROM settings WHERE key='max_concurrent_runs'").fetchone()
    try:
        return max(1, int(row[0])) if row else 1
    except (TypeError, ValueError):
        return 1


def promote(db: str | Path, spawn: Callable[[str | Path, str], subprocess.Popen] = spawn_supervisor) -> list[str]:
    """Start every queued task that may start now, oldest first, each under its own supervisor; return their ids.

    Uncapped tasks (previews, ad-hoc commands) always start. Capped ones (launched runs) start only while fewer than
    `max_concurrent_runs` capped tasks are running: a long DFT run holds several times its per-calculation memory,
    so two at once can push the machine into swap.

    Runs under BEGIN IMMEDIATE, so two promoters (a Bench submitting, a finishing supervisor, a Bench starting up)
    cannot both see the same free slot: the second waits for the lock, then sees the first one's task running. The
    supervisor's pid and identity are written in the same transaction as `running`, so a running row always names
    its supervisor.
    """
    with connect(db) as con:  # the common case, nothing queued, must not take the write lock
        if con.execute("SELECT 1 FROM tasks WHERE status='queued' LIMIT 1").fetchone() is None:
            return []
    started: list[str] = []
    with connect(db, immediate=True) as con:
        slots = (
            max_concurrent_runs(con)
            - con.execute("SELECT COUNT(*) FROM tasks WHERE status='running' AND capped=1").fetchone()[0]
        )
        for tid, capped in con.execute("SELECT id, capped FROM tasks WHERE status='queued' ORDER BY rowid").fetchall():
            if capped:
                if slots <= 0:
                    continue
                slots -= 1
            now = utc()
            try:
                p = spawn(db, tid)
            except OSError as e:
                con.execute(
                    "UPDATE tasks SET status='failed', finished_at=?, note=? WHERE id=?",
                    (now, f"could not start the task's supervisor: {e}", tid),
                )
                task_event(tid, "promote_failed", level=logging.ERROR, exc_info=True, error=str(e))
                continue
            con.execute(
                "UPDATE tasks SET status='running', started_at=?, heartbeat_at=?, supervisor_pid=?, "
                "supervisor_start=? WHERE id=?",
                (now, now, p.pid, proc_identity(p.pid), tid),
            )
            started.append(tid)
            task_event(tid, "promote", supervisor_pid=p.pid, capped=bool(capped))
    return started


# ---------------------------------------------------------------------------------------------------- supervising


class _Stop(Exception):
    """The supervisor was told to stop (SIGTERM/SIGINT)."""


@dataclass
class _Job:
    """What one task runs, as stored in its row."""

    command: list[str]
    cwd: str | None
    log_path: Path
    max_attempts: int
    limit_bytes: int | None
    env: dict[str, str]
    backoff_s: float
    interval_s: float
    timeout_s: float | None

    @classmethod
    def from_row(cls, command, cwd, log_path, max_attempts, limit_bytes, spec) -> _Job:
        spec = json.loads(spec or "{}")
        return cls(
            command=json.loads(command),
            cwd=cwd or None,
            log_path=Path(log_path),
            max_attempts=max_attempts or 1,
            limit_bytes=limit_bytes,
            env={**os.environ, **(spec.get("env") or {})},
            backoff_s=float(spec.get("retry_backoff_s", 1.0)),
            interval_s=float(spec.get("sample_interval_s", 1.0)),
            timeout_s=float(spec["timeout_s"]) if spec.get("timeout_s") else None,
        )


class Supervisor:
    """Runs one task's attempts as their parent and records everything about them. Every write that ends the task
    is guarded by status='running', so a cancel that got there first stands."""

    def __init__(self, db: str | Path, task_id: str) -> None:
        self.db, self.tid = str(db), task_id
        self.proc: subprocess.Popen | None = None
        self.deadline: float | None = None  # monotonic; set once the task's limit is known

    def _status(self) -> str | None:
        with connect(self.db) as con:
            row = con.execute("SELECT status FROM tasks WHERE id=?", (self.tid,)).fetchone()
        return row[0] if row else None

    def _finish(self, status: str, rc: int | None, note: str | None = None, **extra: object) -> None:
        sets: list[str] = ["status=?", "returncode=?", "finished_at=?"]
        args: list[object] = [status, rc, utc()]
        if note is not None:
            sets.append("note=?")
            args.append(note)
        for k, v in extra.items():
            sets.append(f"{k}=?")
            args.append(v)
        with connect(self.db) as con:
            changed = con.execute(
                f"UPDATE tasks SET {', '.join(sets)} WHERE id=? AND status='running'", (*args, self.tid)
            ).rowcount
        if changed:
            bad = status == "failed"
            task_event(
                self.tid,
                "exit",
                level=logging.WARNING if bad else logging.INFO,
                status=status,
                returncode=rc,
                note=note,
            )
        else:  # a cancel (or a reconcile) wrote the end first, and it stands
            task_event(self.tid, "exit_ignored", level=logging.DEBUG, status=status, returncode=rc)

    def _end(self, proc: subprocess.Popen) -> tuple[str, None]:
        kill_group(proc.pid)  # still our unreaped child, so its pid cannot have been reused
        proc.wait()
        return "stopped", None

    def run(self) -> int:
        # IMMEDIATE, not a plain read: the promoter that started this process still holds the write lock, and under
        # WAL a plain read would see the row as it was before that transaction (still `queued`) and give up.
        with connect(self.db, immediate=True) as con:
            row = con.execute(
                "SELECT status, supervisor_pid, command, cwd, log_path, max_attempts, memory_limit_bytes, spec "
                "FROM tasks WHERE id=?",
                (self.tid,),
            ).fetchone()
        if row is None or row[0] != "running" or row[1] != os.getpid():
            return 0  # not this process's task: started by mistake, or already resolved

        def _stop(_sig, _frame):
            raise _Stop

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        task_event(self.tid, "start", supervisor_pid=os.getpid())
        job = _Job.from_row(*row[2:])
        if job.timeout_s:  # one wall-clock budget for the whole task, every attempt and backoff included
            self.deadline = time.monotonic() + job.timeout_s
        try:
            self._attempts(job)
        except BaseException as e:  # noqa: BLE001 - whatever happened, the task must not read "running" forever
            why = "supervisor stopped by a signal" if isinstance(e, _Stop) else f"supervisor error: {e!r}"
            task_event(self.tid, "supervisor_error", level=logging.ERROR, exc_info=True, why=why)
            if self.proc is not None and self.proc.poll() is None:
                kill_group(self.proc.pid)
            try:
                self._finish("failed", None, why)
            except Exception:  # noqa: BLE001 - nothing left to do but say so
                task_event(self.tid, "finish_failed", level=logging.ERROR, exc_info=True)
        finally:
            try:
                promote(self.db)
            except Exception:  # noqa: BLE001 - the next promoter (any Bench, any supervisor) starts what is queued
                task_event(self.tid, "promote_failed", level=logging.ERROR, exc_info=True)
        return 0

    def _attempts(self, job: _Job) -> None:
        attempt = 1
        while True:
            outcome, rc = self._one(job, attempt)
            if outcome != "exited":
                return  # cancelled, over the memory limit or out of time: already recorded, never retried
            if rc != 0 and attempt < job.max_attempts and self._status() == "running":
                with connect(self.db) as con:  # no process between attempts: pid NULL says so
                    con.execute(
                        "UPDATE tasks SET pid=NULL, pid_start=NULL WHERE id=? AND status='running'", (self.tid,)
                    )
                delay = min(job.backoff_s * 2 ** (attempt - 1), 30.0)
                task_event(
                    self.tid,
                    "retry",
                    level=logging.WARNING,
                    attempt=attempt,
                    max_attempts=job.max_attempts,
                    returncode=rc,
                    backoff_s=delay,
                )
                if not self._backoff(delay, job):
                    return
                attempt += 1
                continue
            self._finish("succeeded" if rc == 0 else "failed", rc)
            return

    def _backoff(self, seconds: float, job: _Job) -> bool:
        """Wait out the retry delay; False if the task was cancelled or ran out of time meanwhile."""
        end = time.monotonic() + seconds
        while (left := end - time.monotonic()) > 0:
            if self._status() != "running" or self._out_of_time(job):
                return False
            time.sleep(min(0.05, left))
        return self._status() == "running"

    def _out_of_time(self, job: _Job) -> bool:
        """Past the task's wall-clock limit: record why (the caller then kills what is running)."""
        if self.deadline is None or time.monotonic() < self.deadline:
            return False
        task_event(self.tid, "timeout", level=logging.WARNING, timeout_s=job.timeout_s)
        self._finish("failed", None, f"timed out: still running after its {job.timeout_s:g} s wall-clock limit")
        return True

    def _one(self, job: _Job, attempt: int) -> tuple[str, int | None]:
        """Run one attempt to its end: ("exited", rc), or ("stopped", None) when it was ended here (cancelled, over
        the memory limit, or out of time), with the reason already written."""
        with open(job.log_path, "ab") as lf:
            if job.max_attempts > 1:
                lf.write(f"--- attempt {attempt}/{job.max_attempts} ---\n".encode())
                lf.flush()
            self.proc = proc = subprocess.Popen(
                list(job.command),
                cwd=job.cwd,
                stdout=lf,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=job.env,
                start_new_session=True,
            )
        # A task can exit before the first sampling pass, and /proc is gone the moment it does: read once now.
        peak = rss_bytes(proc.pid) or 0
        with connect(self.db) as con:
            changed = con.execute(
                "UPDATE tasks SET pid=?, pid_start=?, attempt=?, "
                "peak_rss_bytes=MAX(COALESCE(peak_rss_bytes,0),?) WHERE id=? AND status='running'",
                (proc.pid, proc_identity(proc.pid), attempt, peak, self.tid),
            ).rowcount
        task_event(self.tid, "attempt", level=logging.DEBUG, attempt=attempt, pid=proc.pid)
        if not changed:  # cancelled while the process was starting
            task_event(self.tid, "cancelled", note="while the process was starting")
            return self._end(proc)
        last_size = -1
        while True:
            wait = job.interval_s
            if self.deadline is not None:  # wake for the deadline, not a whole interval after it
                wait = max(0.0, min(wait, self.deadline - time.monotonic()))
            try:
                return "exited", proc.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                pass
            if self._status() != "running":  # cancelled: Bench signals too, this covers a Bench that could not
                task_event(self.tid, "cancelled", note="the supervisor stopped the task")
                return self._end(proc)
            if self._out_of_time(job):
                return self._end(proc)
            rss, size = rss_bytes(proc.pid), _size(job.log_path, last_size)
            peak = max(peak, rss or 0)
            self._sample(peak, beat=size != last_size)
            last_size = size
            if job.limit_bytes is not None and rss is not None and rss > job.limit_bytes:
                # Terminal BEFORE the kill, so the non-zero exit is not retried: exceeding the cap is a verdict
                # about this command, and running it again would exceed it again.
                note = f"OOM at {rss / _GB:.2f} GB, over the {job.limit_bytes / _GB:.2f} GB limit"
                task_event(self.tid, "oom", level=logging.WARNING, rss_bytes=int(rss), limit_bytes=job.limit_bytes)
                self._finish("failed", None, note, oom=1, peak_rss_bytes=int(rss))
                return self._end(proc)

    def _sample(self, peak: int, beat: bool) -> None:
        """The log's growth is the heartbeat; the RSS of the task and its children is the memory reading."""
        sets: list[str] = ["peak_rss_bytes=MAX(COALESCE(peak_rss_bytes,0),?)"]
        args: list[object] = [int(peak)]
        if beat:
            sets.append("heartbeat_at=?")
            args.append(utc())
        try:  # advisory: a busy database skips one sample, not the task
            with connect(self.db) as con:
                con.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id=?", (*args, self.tid))
        except sqlite3.Error:
            task_event(self.tid, "sample_skipped", level=logging.DEBUG, exc_info=True)  # once a second: not a warning


def _size(path: Path, default: int) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return default


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: task_supervisor.py DB TASK_ID", file=sys.stderr)
        return 2
    if (requested := log_env_requested()) is not None:
        setup_logging(*requested)
    return Supervisor(argv[0], argv[1]).run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
