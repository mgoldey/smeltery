"""B09: Bench leaves a trail. Every task lifecycle transition is logged with the task id, `--log-json` emits one JSON
object per line, and an exception that is swallowed into a response is logged with its traceback."""

from __future__ import annotations

import json
import logging
import os
import signal
import sqlite3
import time

import pytest

from smeltery.bench import app as bench_app
from smeltery.bench.adapters import task_supervisor as sup
from smeltery.bench.adapters.task_queue import SubprocessTaskQueue
from smeltery.bench.adapters.task_supervisor import Supervisor

TASKS = "smeltery.bench.tasks"


class _Me:
    """A spawn that 'starts' the supervisor as this very process, so a test can run it in-process and caplog sees it."""

    pid = os.getpid()


def _spawn_in_process(db, task_id):
    return _Me()


@pytest.fixture
def q(tmp_path):
    queue = SubprocessTaskQueue(tmp_path / "q", spawn=_spawn_in_process)
    queue.retry_backoff_s = 0.01
    queue.sample_interval_s = 0.05
    return queue


@pytest.fixture
def run_supervisor(q):
    """Run one task's supervisor in this process (restoring the signal handlers it installs)."""
    old = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)

    def go(tid):
        try:
            return Supervisor(q.db, tid).run()
        finally:
            signal.signal(signal.SIGTERM, old[0])
            signal.signal(signal.SIGINT, old[1])

    return go


def events(caplog, tid=None):
    return [r for r in caplog.records if r.name == TASKS and (tid is None or r.task_id == tid)]


def kinds(caplog, tid):
    return [r.event for r in events(caplog, tid)]


@pytest.fixture(autouse=True)
def _tasks_logger_visible(caplog):
    caplog.set_level(logging.DEBUG, logger=TASKS)


def test_a_submitted_task_leaves_submit_promote_start_and_exit_lines(q, run_supervisor, caplog):
    tid = q.submit("hello", ["bash", "-c", "exit 0"])
    run_supervisor(tid)
    assert kinds(caplog, tid) == ["submit", "promote", "start", "attempt", "exit"]
    exit_ = [r for r in events(caplog, tid) if r.event == "exit"][0]
    assert exit_.status == "succeeded" and exit_.returncode == 0 and exit_.levelno == logging.INFO
    assert all(tid in r.getMessage() for r in events(caplog, tid))  # the id is in every human-readable line too


def test_a_failing_task_is_a_warning_and_a_retry_is_logged(q, run_supervisor, caplog):
    tid = q.submit("flaky", ["bash", "-c", "exit 3"], retries=1)
    run_supervisor(tid)
    seen = kinds(caplog, tid)
    assert seen.count("retry") == 1 and seen[-1] == "exit"
    retry = [r for r in events(caplog, tid) if r.event == "retry"][0]
    assert retry.attempt == 1 and retry.max_attempts == 2 and retry.returncode == 3
    last = events(caplog, tid)[-1]
    assert last.status == "failed" and last.returncode == 3 and last.levelno == logging.WARNING


def test_a_timeout_is_logged(q, run_supervisor, caplog):
    tid = q.submit("slow", ["sleep", "30"], timeout_s=0.3)
    run_supervisor(tid)
    seen = kinds(caplog, tid)
    assert "timeout" in seen and seen[-1] == "exit"
    assert [r for r in events(caplog, tid) if r.event == "timeout"][0].levelno == logging.WARNING


def test_an_oom_is_logged(q, run_supervisor, caplog):
    tid = q.submit("hog", ["sleep", "30"], memory_limit_gb=1e-9)  # one byte: any process is over it
    run_supervisor(tid)
    seen = kinds(caplog, tid)
    assert "oom" in seen and seen[-1] == "exit"
    assert q.get(tid).oom is True


def test_a_cancel_is_logged(tmp_path, caplog):
    queue = SubprocessTaskQueue(tmp_path / "q", spawn=_spawn_in_process, max_concurrent_runs=1)
    first = queue.submit("a", ["sleep", "30"], capped=True)  # takes the only slot, and is never supervised here
    second = queue.submit("b", ["sleep", "30"], capped=True)  # waits as queued
    assert queue.get(second).status == "queued"
    queue.cancel(second)
    cancel = [r for r in events(caplog, second) if r.event == "cancel"]
    assert len(cancel) == 1 and cancel[0].was == "queued"
    assert "promote" in kinds(caplog, first) and "promote" not in kinds(caplog, second)


def test_a_supervisor_that_cannot_be_started_is_logged_with_its_traceback(tmp_path, caplog):
    def refuse(db, task_id):
        raise OSError("no fork for you")

    queue = SubprocessTaskQueue(tmp_path / "q", spawn=refuse)
    tid = queue.submit("x", ["true"])
    rec = [r for r in events(caplog, tid) if r.event == "promote_failed"][0]
    assert rec.levelno == logging.ERROR and rec.exc_info and rec.exc_info[0] is OSError
    assert queue.get(tid).status == "failed"


def test_a_lost_supervisor_is_logged(tmp_path, caplog):
    queue = SubprocessTaskQueue(tmp_path / "q", spawn=_spawn_in_process)
    tid = queue.submit("x", ["true"])  # 'running' under this pid
    con = sqlite3.connect(queue.db)
    with con:
        con.execute("UPDATE tasks SET supervisor_pid=?, supervisor_start='gone' WHERE id=?", (os.getpid(), tid))
    con.close()
    queue.get(tid)
    assert "supervisor_lost" in kinds(caplog, tid)


def test_a_real_supervisor_process_logs_to_the_inherited_stderr(tmp_path, monkeypatch, capfd):
    monkeypatch.setenv(sup.LOG_LEVEL_ENV, "info")
    monkeypatch.setenv(sup.LOG_JSON_ENV, "1")
    queue = SubprocessTaskQueue(tmp_path / "q")
    tid = queue.submit("real", ["bash", "-c", "exit 0"])
    deadline = time.monotonic() + 60
    while queue.get(tid).status not in ("succeeded", "failed") and time.monotonic() < deadline:
        time.sleep(0.1)
    time.sleep(0.3)  # the supervisor's last line is written just after its row
    lines = [json.loads(ln) for ln in capfd.readouterr().err.splitlines() if ln.startswith("{")]
    mine = [d for d in lines if d.get("task_id") == tid]
    assert {"start", "exit"} <= {d["event"] for d in mine}
    assert all(d["logger"] == TASKS and d["level"] and d["ts"] for d in mine)


# ------------------------------------------------------------------------------------------------ the flags


@pytest.fixture
def clean_logging(monkeypatch):
    monkeypatch.delenv(sup.LOG_LEVEL_ENV, raising=False)  # recorded, so the env main() sets is undone
    monkeypatch.delenv(sup.LOG_JSON_ENV, raising=False)
    root = logging.getLogger()
    before, level = list(root.handlers), root.level
    yield
    root.handlers[:] = before
    root.setLevel(level)


def _serve(monkeypatch, tmp_path, argv, emit):
    web = tmp_path / "web"
    web.mkdir(exist_ok=True)
    (web / "index.html").write_text("<html></html>")
    monkeypatch.setattr(bench_app, "WEB_DIR", web)
    seen = {}

    def fake_run(app, **kw):
        seen.update(kw)
        emit()

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    assert bench_app.main([str(root), *argv]) == 0
    return seen


def test_log_json_emits_parseable_json_lines(monkeypatch, tmp_path, capsys, clean_logging):
    def emit():
        sup.task_event("abc123", "submit", task_name="x", capped=True)
        logging.getLogger("uvicorn.error").warning("a uvicorn line")
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("smeltery.bench.x").error("with a traceback", exc_info=True)

    seen = _serve(monkeypatch, tmp_path, ["--log-json", "--log-level", "debug"], emit)
    err = capsys.readouterr().err.splitlines()
    json_lines = [json.loads(ln) for ln in err if ln.startswith("{")]
    plain = [ln for ln in err if not ln.startswith("{")]
    assert len(json_lines) == 3 and all({"ts", "level", "logger", "message"} <= d.keys() for d in json_lines)
    assert json_lines[0]["task_id"] == "abc123" and json_lines[0]["event"] == "submit" and json_lines[0]["capped"]
    assert "ValueError: boom" in json_lines[2]["exc"]
    assert all(ln.startswith("smeltery bench:") for ln in plain)  # the deliberate startup lines, untouched
    assert seen["log_config"] is None and seen["log_level"] == "debug" and seen["access_log"] is True
    assert os.environ[sup.LOG_JSON_ENV] == "1" and os.environ[sup.LOG_LEVEL_ENV] == "debug"  # for the supervisors


def test_text_is_the_default_and_the_access_log_is_off(monkeypatch, tmp_path, capsys, clean_logging):
    seen = _serve(monkeypatch, tmp_path, [], lambda: sup.task_event("abc123", "submit"))
    err = capsys.readouterr().err
    assert "task abc123 submit" in err and not any(ln.startswith("{") for ln in err.splitlines())
    assert seen["log_level"] == "info" and seen["access_log"] is False


def test_the_environment_sets_the_level_and_json(monkeypatch, tmp_path, capsys, clean_logging):
    monkeypatch.setenv(sup.LOG_LEVEL_ENV, "warning")
    monkeypatch.setenv(sup.LOG_JSON_ENV, "true")
    seen = _serve(monkeypatch, tmp_path, [], lambda: logging.getLogger("smeltery.bench.t").info("below the threshold"))
    assert seen["log_level"] == "warning" and "below the threshold" not in capsys.readouterr().err


def test_a_bad_level_in_the_environment_is_a_usage_error(monkeypatch, tmp_path, capsys, clean_logging):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("x")
    monkeypatch.setattr(bench_app, "WEB_DIR", tmp_path / "web")
    monkeypatch.setenv(sup.LOG_LEVEL_ENV, "loud")
    with pytest.raises(SystemExit) as e:
        bench_app.main([str(tmp_path)])
    assert e.value.code == 2


def test_the_token_is_redacted_from_an_access_log_line():
    rec = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("c", "GET", "/?token=s3cret&x=1", "1.1", 200),
        None,
    )
    assert bench_app._redact_token(rec)
    assert "s3cret" not in rec.getMessage() and "token=***&x=1" in rec.getMessage()


# --------------------------------------------------------------------------- swallowed exceptions are logged


def test_a_dead_task_backend_is_logged_with_its_traceback(client_for, caplog):
    class Broken:
        def list(self, status=None, limit=100):
            raise RuntimeError("temporal down")

    c, _ = client_for(queue=Broken())
    assert c.get("/api/tasks").status_code == 503
    rec = [r for r in caplog.records if "could not list tasks" in r.getMessage()][0]
    assert rec.levelno == logging.WARNING and rec.exc_info and rec.exc_info[0] is RuntimeError
