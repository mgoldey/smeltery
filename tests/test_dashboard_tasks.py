"""The centralized pipeline surface: run discovery, the dashboard's JSON API, the golden path, and the
Temporal task manager's refusal to pretend.

Four things are pinned here, all of them promises the code makes in prose and nothing proved before:

1. `discover_runs` reports a malformed record instead of dropping it or raising.
2. The dashboard's API answers over a real socket, and a failing task manager becomes a 503, not a traceback.
3. `smeltery golden init` writes a config that `smeltery run --plan` accepts.
4. `TemporalTaskManager` raises `TemporalUnavailable` rather than silently falling back to a private
   implementation -- the claim in `smeltery/tasks.py`'s module docstring.

Ferric-free and server-free: no SCF runs, no Temporal server is contacted. The task manager is either
stubbed (the dashboard tests inject one) or pointed at a closed port with a short deadline.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from gui_fixture import CANDIDATES, PARENT, make_run_dir

from smeltery import tasks as tasks_mod
from smeltery.cli import ConfigError, build_plan, load_config
from smeltery.dashboard import DashboardServer, discover_runs
from smeltery.golden import main as golden_main
from smeltery.record import RunRecord
from smeltery.tasks import Task, TemporalTaskManager, TemporalUnavailable, worker_activities, worker_workflows

# ---------- 1. run discovery ----------


def test_discover_runs_finds_a_contract_shaped_record(tmp_path):
    """A record written to docs/gui-contract.md section 1 is discovered with its summary fields."""
    out = make_run_dir(tmp_path)
    found = discover_runs(tmp_path)
    assert len(found) == 1
    row = found[0]
    assert "error" not in row, row
    assert row["parent"] == PARENT
    assert sorted(row["candidates"]) == sorted(CANDIDATES)
    assert Path(row["path"]).parent == out
    # The cut's verdict travels with the row, so the dashboard can show it without reopening the record.
    assert row["keep"] == 1
    assert row["floor_source"]


def test_discover_runs_reports_a_malformed_record_instead_of_dropping_it(tmp_path):
    """A `run-*.json` that is not a run record becomes a row carrying `error`.

    This is the behaviour the bare `except Exception` in `discover_runs` exists for, and the reason it is
    not a bug: one unreadable file in a shared directory must not blank the dashboard, and must not be
    silently invisible either -- an operator who cannot see the bad record cannot fix it.
    """
    (tmp_path / "run-deadbeef0000.json").write_text("{not json at all")
    (tmp_path / "run-cafebabe0000.json").write_text(json.dumps({"campaign": "x"}))

    found = discover_runs(tmp_path)

    assert len(found) == 2, "both unreadable records must still be reported"
    assert all("error" in r for r in found), found
    assert all(r["error"] for r in found), "the error must carry a reason, not an empty string"
    # Discovery is sorted by path, so the rows are stable across calls.
    assert [Path(r["path"]).name for r in found] == [
        "run-cafebabe0000.json",
        "run-deadbeef0000.json",
    ]


def test_discover_runs_survives_a_mixed_directory_and_honours_limit(tmp_path):
    """One good record beside a broken one: both reported, and `limit` truncates."""
    make_run_dir(tmp_path)
    (tmp_path / "run-000000000bad.json").write_text(")")

    found = discover_runs(tmp_path)
    assert len(found) == 2
    assert sum("error" in r for r in found) == 1
    assert sum("error" not in r for r in found) == 1

    assert len(discover_runs(tmp_path, limit=1)) == 1


def test_discover_runs_on_a_missing_root_is_empty_not_an_error(tmp_path):
    assert discover_runs(tmp_path / "nope") == []


# ---------- 2. the dashboard's JSON API ----------


class StubTaskManager:
    """A task manager with no Temporal behind it.

    `DashboardServer` takes `task_manager` precisely so the HTTP layer can be tested without a server;
    `raises` turns every call into a failure so the handler's 503 path is exercised.
    """

    def __init__(self, tasks=(), *, raises: Exception | None = None):
        self._tasks = list(tasks)
        self._raises = raises
        self.submitted: list[tuple] = []

    def _check(self):
        if self._raises is not None:
            raise self._raises

    def list(self, status=None):
        self._check()
        return [t for t in self._tasks if status is None or t.status == status]

    def get(self, task_id):
        self._check()
        return next((t for t in self._tasks if t.id == task_id), None)

    def submit(self, name, command, *, cwd=None, **kw):
        self._check()
        self.submitted.append((name, command, cwd))
        t = Task(id="stub-1", name=name, command=list(command), cwd=cwd or "", status="queued")
        self._tasks.append(t)
        return t

    def tail_log(self, task_id, lines=40):
        self._check()
        if not any(t.id == task_id for t in self._tasks):
            raise KeyError(task_id)
        return "hello\ndone\n"


def _serving(root, manager):
    """A DashboardServer on an ephemeral port, serving in a background thread."""
    srv = DashboardServer(root, host="127.0.0.1", port=0, task_manager=manager)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}", thread


def _get(url, timeout=10):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(url, payload, timeout=10):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


@pytest.fixture
def dashboard(tmp_path):
    """A served dashboard over a run directory, with a stub task manager, torn down after the test."""
    made = {}

    def start(*, tasks=(), raises=None, with_run=True):
        if with_run:
            made["out"] = make_run_dir(tmp_path)
        manager = StubTaskManager(tasks, raises=raises)
        srv, base, thread = _serving(tmp_path, manager)
        made.update(srv=srv, base=base, thread=thread, manager=manager)
        return base, manager

    yield start

    srv = made.get("srv")
    if srv is not None:
        srv.shutdown()
        srv.server_close()
        made["thread"].join(timeout=5)


def test_dashboard_serves_its_page_and_run_api(dashboard):
    base, _ = dashboard()

    status, body = _get(base + "/")
    assert status == 200
    assert b"smeltery dashboard" in body

    status, body = _get(base + "/api/runs")
    assert status == 200
    rows = json.loads(body)
    assert len(rows) == 1
    assert rows[0]["parent"] == PARENT


def test_dashboard_lists_tasks_from_the_manager(dashboard):
    done = Task(id="t-1", name="echo-test", command=["bash", "-c", "echo hi"], cwd="/tmp", status="succeeded")
    base, _ = dashboard(tasks=[done])

    status, body = _get(base + "/api/tasks")
    assert status == 200
    rows = json.loads(body)
    assert [r["id"] for r in rows] == ["t-1"]
    assert rows[0]["status"] == "succeeded"
    assert rows[0]["command"] == ["bash", "-c", "echo hi"]

    status, body = _get(base + "/api/tasks/t-1/log")
    assert status == 200
    assert "done" in json.loads(body)["log"]


def test_dashboard_turns_an_unreachable_task_backend_into_503(dashboard):
    """Temporal being down must degrade the tasks panel, not the whole page.

    The run list is served from the filesystem and keeps working; only the task calls fail, and they fail
    as a 503 carrying the reason rather than as a traceback or a hang.
    """
    base, _ = dashboard(raises=TemporalUnavailable("cannot connect to Temporal at 127.0.0.1:7233"))

    status, body = _get(base + "/api/tasks")
    assert status == 503
    assert "Temporal" in json.loads(body)["error"]

    status, _ = _get(base + "/api/runs")
    assert status == 200, "run discovery must not depend on the task backend"

    status, body = _post(base + "/api/tasks", {"command": "echo hi"})
    assert status == 503
    assert json.loads(body)["error"]


def test_dashboard_submits_a_task_and_rejects_a_bad_one(dashboard):
    base, manager = dashboard()

    status, body = _post(base + "/api/tasks", {"name": "run", "command": "smeltery run cfg.toml", "cwd": "/tmp"})
    assert status == 201
    assert json.loads(body)["name"] == "run"
    # A string command is split with shlex, so the worker receives argv, never a shell string.
    assert manager.submitted == [("run", ["smeltery", "run", "cfg.toml"], "/tmp")]

    for payload in ({}, {"command": ""}, {"command": []}):
        status, body = _post(base + "/api/tasks", payload)
        assert status == 400, payload
        assert json.loads(body)["error"]

    status, body = _post(base + "/api/tasks", {"command": "echo 'unbalanced"})
    assert status == 400
    assert "invalid command" in json.loads(body)["error"]


def test_dashboard_unknown_paths_are_404(dashboard):
    base, _ = dashboard()
    assert _get(base + "/nope")[0] == 404
    assert _get(base + "/api/tasks/ghost/log")[0] == 404


# ---------- 3. the golden path ----------


def test_golden_init_writes_a_config_that_plan_accepts(tmp_path):
    """`golden init` -> `run --plan` is step 1 -> step 2 of docs/golden-path.md.

    The scaffold deliberately ships with its analogue commented out, so a bare `--plan` must fail with a
    config error (the doc tells the user to add analogues first); adding one must make the plan pass. Both
    halves are asserted, because a scaffold that cannot reach `plan OK` is a broken golden path.
    """
    cfg = tmp_path / "myrun.toml"
    assert golden_main(["init", "--out", str(cfg), "--parent", "lead", "--parent-smiles", "c1ccccc1"]) == 0
    assert cfg.is_file()

    text = cfg.read_text()
    assert 'parent = "lead"' in text
    assert 'lead = "c1ccccc1"' in text

    # As scaffolded: one candidate, which is not a comparison.
    with pytest.raises(ConfigError):
        build_plan(load_config(cfg), out_dir=tmp_path / "out")

    cfg.write_text(text.replace('# analogue = "SMILES"', 'tolyl = "Cc1ccccc1"'))
    plan = build_plan(load_config(cfg), out_dir=tmp_path / "out")
    assert plan.parent.name == "lead"
    assert sorted(c.name for c in plan.candidates) == ["lead", "tolyl"]
    # Poses are embedded for both candidates, which is what makes the plan runnable.
    assert all(c.poses for c in plan.candidates)
    # The plan is reproducible: the same digest the CLI prints at the end of `--plan`.
    digest = RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest
    assert len(digest) == 64


def test_golden_rejects_an_unknown_subcommand(capsys):
    assert golden_main([]) == 2
    assert golden_main(["frobnicate"]) == 2
    assert "init" in capsys.readouterr().err


# ---------- 4. the task manager refuses to pretend ----------


def test_task_manager_raises_when_temporalio_is_absent(monkeypatch):
    """The module docstring's promise: no silent fallback to a private implementation.

    With `temporalio` unimportable, `TEMPORAL_AVAILABLE` is False and every operation must raise
    `TemporalUnavailable`. A fallback here would be worse than an error: tasks would appear to be
    managed while nothing durable recorded them.
    """
    monkeypatch.setattr(tasks_mod, "TEMPORAL_AVAILABLE", False)

    mgr = TemporalTaskManager(host="127.0.0.1:1")
    for call in (
        lambda: mgr.submit("x", ["true"]),
        lambda: mgr.list(),
        lambda: mgr.get("t-1"),
        lambda: mgr.cancel("t-1"),
    ):
        with pytest.raises(TemporalUnavailable):
            call()

    assert worker_activities() == []
    assert worker_workflows() == []


def test_task_manager_raises_when_the_server_is_unreachable():
    """A closed port is `TemporalUnavailable` naming the host, not a bare connection error.

    The port is bound and immediately closed so nothing is listening on it, which keeps the test from
    depending on a port being free by luck or on a real Temporal server being absent.
    """
    if not tasks_mod.TEMPORAL_AVAILABLE:
        pytest.skip("temporalio is not installed; the absent-library path is covered above")

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]

    mgr = TemporalTaskManager(host=f"127.0.0.1:{dead_port}")
    with pytest.raises(TemporalUnavailable) as e:
        mgr.list()
    assert str(dead_port) in str(e.value)


def test_sync_api_refuses_to_be_called_from_a_running_event_loop():
    """`mgr.list()` inside a loop is a deadlock waiting to happen, so it raises instead.

    Checked with `asyncio.get_running_loop()`: `get_event_loop()` warns under a DeprecationWarning (and in
    a future Python raises), and can create a loop as a side effect. The guard must not emit that warning.
    """
    import asyncio
    import warnings

    mgr = TemporalTaskManager(host="127.0.0.1:1")

    async def inside():
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            # The coroutine `list()` builds is never awaited, by design: the guard raises before
            # `asyncio.run` can take it. Closing it keeps that from surfacing as a RuntimeWarning.
            coro = mgr.list_async()
            try:
                # Matched on "use async methods", the half of the message that is smeltery's own.
                # `asyncio.run` raises its own RuntimeError naming a running event loop, so a looser
                # pattern passes even when the guard is deleted -- as a mutation confirmed.
                with pytest.raises(RuntimeError, match="use async methods"):
                    mgr._run(coro)
            finally:
                coro.close()

    asyncio.run(inside())


def test_worker_entrypoints_are_registered_when_temporalio_is_present():
    """`smeltery task-worker` is only useful if these two lists are non-empty."""
    if not tasks_mod.TEMPORAL_AVAILABLE:
        pytest.skip("temporalio is not installed")
    assert [f.__name__ for f in worker_activities()] == ["run_shell_activity"]
    assert [w.__name__ for w in worker_workflows()] == ["ShellTaskWorkflow"]
