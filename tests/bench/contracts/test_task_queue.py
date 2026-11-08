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
    wait_until(lambda: counter.exists() and q.get(tid).attempt == 1, timeout=10)
    time.sleep(1.0)  # the first attempt has failed; the queue is now backing off
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
