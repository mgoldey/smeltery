"""B10: shutdown is defined. Bench reports what it leaves running, and with --stop-tasks-on-exit it stops it,
never signalling a pid whose identity no longer matches."""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench import app as bench_app
from smeltery.bench.adapters.task_queue import SubprocessTaskQueue
from smeltery.bench.app import Settings, create_app
from smeltery.bench.ports import StoppableTasks


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:  # a zombie still answers signal 0
        return Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0] != "Z"
    except OSError:
        return False


def _wait(pred, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(0.1)
    raise TimeoutError


def _app(tmp_path, queue, **kw):
    return create_app(
        run_store=FakeRunStore(tmp_path),
        task_queue=queue,
        identity=FakeIdentity("mg"),
        clock=FixedClock(),
        settings=Settings(root=tmp_path / "bench-root", web_dir=tmp_path / "no-web"),
        **kw,
    )


def _running_sleep(queue, name="long"):
    """Submit `sleep 600` under a real supervisor and wait until its process exists."""
    tid = queue.submit(name, ["sleep", "600"])
    _wait(lambda: queue.get(tid).status == "running" and queue.live_tasks()[0]["pid"])
    return tid, queue.live_tasks()[0]["pid"]


@pytest.fixture
def q(tmp_path):
    queue = SubprocessTaskQueue(tmp_path / "q")
    yield queue
    for t in queue.live_tasks():  # whatever a test left behind
        queue.cancel(t["id"])


def test_the_local_queue_offers_the_optional_shutdown_port(q):
    assert isinstance(q, StoppableTasks) and not isinstance(FakeTaskQueue(), StoppableTasks)


def test_by_default_shutdown_logs_the_live_tasks_and_leaves_them_running(tmp_path, q, caplog):
    tid, pid = _running_sleep(q)
    caplog.set_level(logging.INFO, logger="smeltery.bench.app")
    with TestClient(_app(tmp_path, q)):
        pass  # leaving the block is the shutdown
    msg = [r for r in caplog.records if r.name == "smeltery.bench.app" and "shutting down" in r.getMessage()]
    assert len(msg) == 1 and msg[0].levelno == logging.WARNING
    assert tid in msg[0].getMessage() and f"pid {pid}" in msg[0].getMessage()
    assert "--stop-tasks-on-exit" in msg[0].getMessage()  # tells the operator what to do about it
    time.sleep(1.5)  # a supervisor would have noticed by now
    assert _alive(pid) and q.get(tid).status == "running"


def test_stop_tasks_on_exit_terminates_them_and_says_why(tmp_path, q, caplog):
    tid, pid = _running_sleep(q)
    caplog.set_level(logging.INFO)
    with TestClient(_app(tmp_path, q, stop_tasks_on_exit=True)):
        pass
    assert not _alive(pid)
    t = q.get(tid)
    assert t.status == "cancelled" and "--stop-tasks-on-exit" in t.note
    assert any(r.getMessage().startswith("shutting down; stopping 1 live task") for r in caplog.records)
    assert any(getattr(r, "event", None) == "stop" and r.task_id == tid for r in caplog.records)


def test_stopping_cancels_queued_tasks_and_starts_nothing_new(tmp_path):
    started = []

    class _Me:
        pid = os.getpid()

    def spawn(db, task_id):
        started.append(task_id)
        return _Me()

    q = SubprocessTaskQueue(tmp_path / "q", spawn=spawn, max_concurrent_runs=1)
    a = q.submit("a", ["sleep", "600"], capped=True)  # holds the one slot
    b = q.submit("b", ["sleep", "600"], capped=True)
    c = q.submit("c", ["sleep", "600"], capped=True)
    assert started == [a]
    assert sorted(q.stop_live("why")) == sorted([a, b, c])
    assert {q.get(i).status for i in (a, b, c)} == {"cancelled"}
    assert started == [a]  # the queued ones were not promoted into the freed slot


def test_a_reused_pid_is_never_signalled(tmp_path):
    bystander = subprocess.Popen(["sleep", "600"], start_new_session=True)
    try:

        class _Me:
            pid = os.getpid()

        q = SubprocessTaskQueue(tmp_path / "q", spawn=lambda db, tid: _Me())
        tid = q.submit("x", ["sleep", "600"])
        con = sqlite3.connect(q.db)
        with con:  # the row names the bystander's pid, with an identity that is not the bystander's
            con.execute("UPDATE tasks SET pid=?, pid_start='boot:1' WHERE id=?", (bystander.pid, tid))
        con.close()
        assert q.stop_live("why") == [tid]
        time.sleep(0.5)
        assert bystander.poll() is None and _alive(bystander.pid)
        assert q.get(tid).status == "cancelled"
    finally:
        os.killpg(bystander.pid, signal.SIGKILL)
        bystander.wait()


def test_shutdown_with_no_live_tasks_is_quiet_and_safe(tmp_path, q, caplog):
    caplog.set_level(logging.INFO, logger="smeltery.bench.app")
    with TestClient(_app(tmp_path, q, stop_tasks_on_exit=True)):
        pass
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_backend_without_the_port_shuts_down_cleanly(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="smeltery.bench.app")
    with TestClient(_app(tmp_path, FakeTaskQueue(), stop_tasks_on_exit=True)):
        pass
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_failing_backend_cannot_break_shutdown(tmp_path, caplog):
    class Broken(FakeTaskQueue):
        def live_tasks(self):
            raise RuntimeError("db gone")

        def stop_live(self, reason):
            return []

    with TestClient(_app(tmp_path, Broken())):
        pass  # no exception
    rec = [r for r in caplog.records if "could not inspect" in r.getMessage()]
    assert rec and rec[0].exc_info


# ------------------------------------------------------------------------------------------------ the flag


@pytest.mark.parametrize(
    "argv,env,expected",
    [([], None, False), (["--stop-tasks-on-exit"], None, True), ([], "1", True), ([], "no", False)],
)
def test_the_flag_and_the_env_var_reach_build_app(monkeypatch, tmp_path, argv, env, expected):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("x")
    monkeypatch.setattr(bench_app, "WEB_DIR", web)
    monkeypatch.delenv(bench_app.STOP_TASKS_ENV, raising=False)
    monkeypatch.delenv("SMELTERY_BENCH_LOG_LEVEL", raising=False)
    monkeypatch.delenv("SMELTERY_BENCH_LOG_JSON", raising=False)
    if env is not None:
        monkeypatch.setenv(bench_app.STOP_TASKS_ENV, env)
    got = {}
    monkeypatch.setattr(bench_app, "build_app", lambda root, **kw: got.update(kw))
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    try:
        assert bench_app.main([str(tmp_path), *argv]) == 0
    finally:
        root.handlers[:] = before
        root.setLevel(level)
    assert got["stop_tasks_on_exit"] is expected
