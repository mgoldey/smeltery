import asyncio
import concurrent.futures
import contextlib
import threading
import time
import uuid
from pathlib import Path

import pytest

from bench.contracts.base import PortContract
from smeltery.bench.adapters.task_queue import SubprocessTaskQueue, TemporalTaskQueue
from smeltery.bench.ports import TaskQueue
from smeltery.tasks import TemporalUnavailable


def wait_until(pred, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.1)
    raise TimeoutError("wait_until timed out")


_adapters = [
    ("SubprocessTaskQueue", lambda tmp: SubprocessTaskQueue(tmp)),
    ("TemporalTaskQueue", lambda tmp: TemporalTaskQueue(task_queue=f"smeltery-tasks-test-{uuid.uuid4().hex[:8]}")),
]


@contextlib.contextmanager
def _worker_for(task_queue: str, host: str = "127.0.0.1:7233"):
    """A Temporal worker for one test's own task queue, on a thread with its own event loop.

    A running server is not enough: a task submitted with no worker polling its queue never starts."""
    from temporalio.client import Client
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    from smeltery.tasks import worker_activities, worker_workflows

    loop = asyncio.new_event_loop()
    ready, stop = threading.Event(), None

    async def main():
        nonlocal stop
        stop = asyncio.Event()
        client = await Client.connect(host)
        async with Worker(
            client,
            task_queue=task_queue,
            workflows=worker_workflows(),
            activities=worker_activities(),
            activity_executor=concurrent.futures.ThreadPoolExecutor(4),
            workflow_runner=UnsandboxedWorkflowRunner(),  # as `smeltery tasks-worker` runs it
        ):
            ready.set()
            await stop.wait()

    th = threading.Thread(target=loop.run_until_complete, args=(main(),), daemon=True)
    th.start()
    if not ready.wait(30):
        raise RuntimeError("the test worker did not start")
    try:
        yield
    finally:
        loop.call_soon_threadsafe(stop.set)
        th.join(30)


class TaskQueueContract(PortContract):
    port = TaskQueue

    @classmethod
    def adapters(cls):
        return _adapters


@pytest.fixture(params=TaskQueueContract.adapters(), ids=[n for n, _ in TaskQueueContract.adapters()])
def q(request, bench_tmp):
    q = request.param[1](bench_tmp)
    if isinstance(q, TemporalTaskQueue):
        try:
            q.list()
        except TemporalUnavailable as e:
            pytest.skip(str(e))
        with _worker_for(q._m.task_queue):
            yield q
        return
    yield q


@pytest.fixture
def q_factory(bench_tmp):
    def factory(tmp):
        # for subprocess, tmp is root
        return SubprocessTaskQueue(tmp)

    return factory


def test_a_submitted_task_runs_and_reports_succeeded(q):
    tid = q.submit("echo", ["bash", "-c", "echo hi"])
    wait_until(lambda: q.status(tid) in ("succeeded", "failed"))
    t = q.get(tid)
    assert t.status == "succeeded" and t.returncode == 0


def test_a_failing_command_reports_failed_with_its_code(q):
    tid = q.submit("boom", ["bash", "-c", "exit 3"])
    wait_until(lambda: q.status(tid) in ("succeeded", "failed"))
    assert q.get(tid).status == "failed" and q.get(tid).returncode == 3


def test_output_is_captured_and_tailable(q):
    tid = q.submit("echo", ["bash", "-c", "echo one; echo two"])
    wait_until(lambda: q.status(tid) in ("succeeded", "failed"))
    assert "two" in q.tail_log(tid, lines=5)


def test_an_unknown_task_id_is_handled_consistently(q):
    assert q.get("ghost") is None
    with pytest.raises(KeyError):
        q.tail_log("ghost")
    with pytest.raises(KeyError):
        q.cancel("ghost")


def test_status_survives_a_new_adapter_instance(q_factory, bench_tmp):
    q1 = q_factory(bench_tmp)
    tid = q1.submit("echo", ["bash", "-c", "echo hi"])
    wait_until(lambda: q1.status(tid) in ("succeeded", "failed"))
    assert q_factory(bench_tmp).get(tid).status == q1.get(tid).status


def test_list_filters_by_status(q):
    tid = q.submit("ok", ["bash", "-c", "true"])
    # its own task only: a shared Temporal namespace may hold other tasks that never finish
    wait_until(lambda: q.status(tid) in ("succeeded", "failed"))
    assert tid in {t.id for t in q.list(status="succeeded")}
    assert all(t.status == "succeeded" for t in q.list(status="succeeded"))


def test_a_successful_task_is_never_observed_as_failed(q):
    tid = q.submit("echo", ["bash", "-c", "echo hi"])
    seen = []
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        s = q.status(tid)
        seen.append(s)
        if s in ("succeeded", "failed", "cancelled"):
            break
    assert "failed" not in seen
    assert seen and seen[-1] == "succeeded"


def test_returncode_is_set_when_status_first_turns_terminal(q):
    tid = q.submit("boom", ["bash", "-c", "exit 3"])
    deadline = time.monotonic() + 30
    terminal = None
    while time.monotonic() < deadline:
        s = q.status(tid)
        if s in ("succeeded", "failed", "cancelled"):
            terminal = s
            assert q.get(tid).returncode == 3, (s, q.get(tid).returncode)
            break
    assert terminal is not None, "never reached terminal"


def test_cancel_kills_the_whole_process_group(bench_tmp):
    import os

    q = SubprocessTaskQueue(bench_tmp)
    pidfile = bench_tmp / "grandchild.pid"
    tid = q.submit("tree", ["sh", "-c", f"sleep 60 & echo $! > {pidfile}; wait"])
    wait_until(lambda: pidfile.exists() and pidfile.read_text().strip(), timeout=10)
    gc = int(pidfile.read_text())
    assert q.cancel(tid) is True
    assert q.get(tid).status == "cancelled"

    def gone():
        try:
            os.kill(gc, 0)
        except ProcessLookupError:
            return True
        # a killed but not yet reaped child shows as a zombie
        return "Z" in Path(f"/proc/{gc}/stat").read_text().rsplit(")", 1)[1].split()[0]

    wait_until(gone, timeout=5)


# ---- retries (HPC-003): subprocess adapter ----


def _flaky(counter: Path, fail_times: int) -> list[str]:
    """Fails `fail_times` times, then succeeds; the counter file survives across attempts."""
    script = (
        f"n=$(cat {counter} 2>/dev/null || echo 0); n=$((n+1)); echo $n > {counter}; "
        f'echo "run $n"; [ $n -gt {fail_times} ]'
    )
    return ["bash", "-c", script]


def _fast(bench_tmp):
    q = SubprocessTaskQueue(bench_tmp)
    q.retry_backoff_s = 0.05
    return q


def test_a_task_failing_twice_then_succeeding_with_two_retries_succeeds_on_attempt_three(bench_tmp):
    q = _fast(bench_tmp)
    tid = q.submit("flaky", _flaky(bench_tmp / "n", 2), retries=2)
    wait_until(lambda: q.status(tid) in ("succeeded", "failed"))
    t = q.get(tid)
    assert (t.status, t.returncode, t.attempt, t.max_attempts) == ("succeeded", 0, 3, 3)
    log = q.tail_log(tid, lines=50)
    assert "--- attempt 1/3 ---" in log and "--- attempt 3/3 ---" in log and "run 3" in log


def test_retries_are_exhausted_and_the_last_failure_is_reported(bench_tmp):
    q = _fast(bench_tmp)
    tid = q.submit("never", ["bash", "-c", "exit 4"], retries=1)
    wait_until(lambda: q.status(tid) in ("succeeded", "failed"))
    t = q.get(tid)
    assert (t.status, t.returncode, t.attempt, t.max_attempts) == ("failed", 4, 2, 2)


def test_zero_retries_runs_once_with_todays_behaviour(bench_tmp):
    q = _fast(bench_tmp)
    counter = bench_tmp / "n"
    tid = q.submit("once", _flaky(counter, 5))
    wait_until(lambda: q.status(tid) in ("succeeded", "failed"))
    t = q.get(tid)
    assert (t.status, t.attempt, t.max_attempts) == ("failed", 1, 1)
    assert counter.read_text().strip() == "1"
    assert "attempt" not in q.tail_log(tid)


def test_cancel_while_running_is_never_retried(bench_tmp):
    q = _fast(bench_tmp)
    counter = bench_tmp / "n"
    tid = q.submit("slow", ["bash", "-c", f"echo x >> {counter}; sleep 60"], retries=3)
    wait_until(lambda: counter.exists(), timeout=10)
    assert q.cancel(tid) is True
    time.sleep(0.5)
    assert q.get(tid).status == "cancelled"
    assert counter.read_text().count("x") == 1


def test_cancel_during_backoff_stops_the_retry(bench_tmp):
    q = SubprocessTaskQueue(bench_tmp)
    q.retry_backoff_s = 20.0
    counter = bench_tmp / "n"
    tid = q.submit("fail", ["bash", "-c", f"echo x >> {counter}; exit 1"], retries=3)
    # attempt 1 has exited once the waiter clears the pid: the queue is now backing off
    wait_until(lambda: counter.exists() and _pid(q, tid) is None, timeout=10)
    assert q.get(tid).status == "running"
    assert q.cancel(tid) is True
    time.sleep(1.0)
    assert q.get(tid).status == "cancelled"
    assert counter.read_text().count("x") == 1


@pytest.mark.parametrize("bad", [-1, 6, True, "2"])
def test_out_of_range_retries_are_refused(bench_tmp, bad):
    with pytest.raises(ValueError):
        SubprocessTaskQueue(bench_tmp).submit("x", ["true"], retries=bad)


def test_an_old_schema_database_gains_the_attempt_columns(bench_tmp):
    import sqlite3

    root = bench_tmp / "old"
    root.mkdir()
    with contextlib.closing(sqlite3.connect(root / "tasks.sqlite")) as con:
        con.execute(
            """CREATE TABLE tasks (
                id TEXT PRIMARY KEY, name TEXT, command TEXT, cwd TEXT, status TEXT,
                created_at TEXT, started_at TEXT, finished_at TEXT, returncode INTEGER,
                log_path TEXT, note TEXT, pid INTEGER)"""
        )
        con.execute(
            "INSERT INTO tasks (id,name,command,cwd,status,returncode) "
            "VALUES ('old1','o','[\"true\"]','','succeeded',0)"
        )
        con.commit()
    q = SubprocessTaskQueue(root)
    SubprocessTaskQueue(root)  # a second open must tolerate the columns already being there
    t = q.get("old1")
    assert (t.status, t.attempt, t.max_attempts) == ("succeeded", None, None)
    tid = q.submit("new", ["true"])
    wait_until(lambda: q.status(tid) == "succeeded")
    assert q.get(tid).max_attempts == 1


def _pid(q, tid):
    import sqlite3

    with contextlib.closing(sqlite3.connect(q.db)) as con:
        return con.execute("SELECT pid FROM tasks WHERE id=?", (tid,)).fetchone()[0]


def test_a_task_in_backoff_whose_owner_process_died_is_failed_by_a_new_queue(bench_tmp):
    import sqlite3
    import subprocess

    q = SubprocessTaskQueue(bench_tmp)
    q.retry_backoff_s = 30.0
    tid = q.submit("fail", ["bash", "-c", "exit 1"], retries=2)
    wait_until(lambda: _pid(q, tid) is None, timeout=10)
    dead = subprocess.Popen(["true"])
    dead.wait()  # a reaped child's pid: certainly not alive
    with contextlib.closing(sqlite3.connect(q.db)) as con:
        con.execute("UPDATE tasks SET owner_pid=? WHERE id=?", (dead.pid, tid))
        con.commit()
    t = SubprocessTaskQueue(bench_tmp).get(tid)
    assert t.status == "failed" and "bench restarted" in t.note
    assert q.get(tid).status == "failed"


def test_a_task_in_backoff_with_a_live_owner_is_left_running(bench_tmp):
    q = SubprocessTaskQueue(bench_tmp)
    q.retry_backoff_s = 30.0
    tid = q.submit("fail", ["bash", "-c", "exit 1"], retries=2)
    wait_until(lambda: _pid(q, tid) is None, timeout=10)
    assert SubprocessTaskQueue(bench_tmp).get(tid).status == "running"
    q.cancel(tid)


# ---------------------------------------------------------------- HPC-004: heartbeat age


def test_a_running_task_reports_a_heartbeat_that_advances_as_it_writes(bench_tmp):
    """A subprocess has no heartbeat protocol, so its liveness is what it observably does: write output.

    `heartbeat_at` is the last time the task's log grew. A long SCF that prints nothing is a different
    condition from a dead one, and `stalled` is what says which -- see the stall test below.
    """
    q = SubprocessTaskQueue(bench_tmp)
    chatty = "import time,sys\nfor _ in range(40):\n print('tick');sys.stdout.flush();time.sleep(0.1)"
    tid = q.submit("chatty", ["python", "-c", chatty])
    wait_until(lambda: q.get(tid).heartbeat_at is not None)
    first = q.get(tid).heartbeat_at
    wait_until(lambda: (q.get(tid).heartbeat_at or first) > first, timeout=10)
    assert q.get(tid).heartbeat_at > first
    q.cancel(tid)


def test_a_task_is_stalled_once_its_output_is_older_than_the_threshold(bench_tmp):
    # `_utc()` stamps whole seconds, so a sub-second threshold cannot be expressed: age jumps 0 -> 1 -> 2.
    # 4 s is the smallest that leaves room to observe "not yet stalled" first without the test being slow.
    q = SubprocessTaskQueue(bench_tmp)
    q.stall_after_s = 4.0  # the real default is minutes; a test must not wait them out
    tid = q.submit("quiet", ["python", "-c", "import time; print('one', flush=True); time.sleep(60)"])
    wait_until(lambda: q.get(tid).heartbeat_at is not None)
    assert q.get(tid).stalled is False, "a task that just wrote is not stalled"
    wait_until(lambda: q.get(tid).stalled is True, timeout=20)
    t = q.get(tid)
    assert t.status == "running", "stalled describes silence, not death: the task is still running"
    assert t.heartbeat_age_s is not None and t.heartbeat_age_s >= 4.0
    q.cancel(tid)


def test_a_finished_task_is_never_stalled(bench_tmp):
    q = SubprocessTaskQueue(bench_tmp)
    q.stall_after_s = 1.0
    tid = q.submit("quick", ["python", "-c", "print('done')"])
    wait_until(lambda: q.get(tid).status == "succeeded")
    time.sleep(2.5)  # well past the threshold: a terminal task must not drift into "stalled"
    t = q.get(tid)
    assert t.stalled is False and t.status == "succeeded"


# ---------------------------------------------------------------- HPC-002: memory limit and OOM


def test_a_task_over_its_memory_limit_is_killed_and_reported_as_oom(bench_tmp):
    q = SubprocessTaskQueue(bench_tmp)
    q.sample_interval_s = 0.05
    # Allocate steadily until killed. 64 MB is far under any real SCF and far over this limit.
    grow = "a=[]\nimport time\nwhile True:\n a.append(bytearray(4_000_000)); time.sleep(0.02)"
    tid = q.submit("hog", ["python", "-c", grow], memory_limit_gb=0.064)
    wait_until(lambda: q.get(tid).status in ("failed", "succeeded"), timeout=60)
    t = q.get(tid)
    assert t.status == "failed", f"an over-limit task must fail, not {t.status}"
    assert t.oom is True
    assert t.peak_rss_gb is not None and t.peak_rss_gb > 0
    assert "OOM at" in t.note and "GB" in t.note, t.note


def test_a_task_within_its_memory_limit_succeeds_and_records_its_peak(bench_tmp):
    q = SubprocessTaskQueue(bench_tmp)
    q.sample_interval_s = 0.05
    tidy = "x=bytearray(8_000_000); import time; time.sleep(0.4); print('ok')"
    tid = q.submit("tidy", ["python", "-c", tidy], memory_limit_gb=2.0)
    wait_until(lambda: q.get(tid).status == "succeeded", timeout=60)
    t = q.get(tid)
    assert t.oom is False and t.peak_rss_gb is not None and t.peak_rss_gb > 0


def test_no_memory_limit_means_no_cap_and_the_peak_is_still_recorded(bench_tmp):
    q = SubprocessTaskQueue(bench_tmp)
    q.sample_interval_s = 0.05
    tid = q.submit("unbounded", ["python", "-c", "x=bytearray(8_000_000); import time; time.sleep(0.3)"])
    wait_until(lambda: q.get(tid).status == "succeeded", timeout=60)
    t = q.get(tid)
    assert t.oom is False and t.peak_rss_gb is not None


@pytest.mark.parametrize("bad", [-1.0, 0.0, "2", True])
def test_an_invalid_memory_limit_is_refused(bench_tmp, bad):
    q = SubprocessTaskQueue(bench_tmp)
    with pytest.raises(ValueError, match="memory_limit_gb"):
        q.submit("x", ["python", "-c", "pass"], memory_limit_gb=bad)


def test_an_old_schema_database_gains_the_health_columns(bench_tmp):
    import sqlite3

    db = Path(bench_tmp) / "tasks.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, name TEXT, command TEXT, cwd TEXT, status TEXT,"
        " created_at TEXT, started_at TEXT, finished_at TEXT, returncode INTEGER, log_path TEXT, note TEXT,"
        " pid INTEGER)"
    )
    con.commit()
    con.close()
    q = SubprocessTaskQueue(bench_tmp)  # must migrate, not raise
    tid = q.submit("after-migration", ["python", "-c", "print('ok')"])
    wait_until(lambda: q.get(tid).status == "succeeded")
    assert q.get(tid).peak_rss_gb is not None


def test_an_oom_kill_is_never_retried(bench_tmp):
    """Exceeding the cap is a verdict about the command: running it again would exceed it again.

    The waiter thread sees a non-zero exit from the kill and would normally retry, so this guards the
    ordering in `_kill_for_oom` -- terminal first, kill second.
    """
    q = SubprocessTaskQueue(bench_tmp)
    q.sample_interval_s = 0.05
    grow = "a=[]\nimport time\nwhile True:\n a.append(bytearray(4_000_000)); time.sleep(0.02)"
    tid = q.submit("hog-retry", ["python", "-c", grow], retries=3, memory_limit_gb=0.064)
    wait_until(lambda: q.get(tid).oom is True, timeout=60)
    t0 = q.get(tid)
    time.sleep(1.5)  # long enough for a retry to have been spawned and recorded
    t1 = q.get(tid)
    assert t1.status == "failed" and t1.oom is True
    assert t1.attempt == t0.attempt, f"an OOM was retried: attempt went {t0.attempt} -> {t1.attempt}"
