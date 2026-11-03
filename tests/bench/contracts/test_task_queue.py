import time

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
    ("TemporalTaskQueue", lambda tmp: TemporalTaskQueue()),
]


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
    return q


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
    q.submit("ok", ["bash", "-c", "true"])
    wait_until(lambda: all(t.status in ("succeeded", "failed") for t in q.list()))
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
