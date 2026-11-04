"""The task API behind the work panel: list, submit, log, cancel, over the TaskQueue port."""

from bench.fakes import FakeTaskQueue


def test_submit_splits_a_command_string_into_argv(client_for):
    q = FakeTaskQueue()
    c, _ = client_for(queue=q)
    r = c.post("/api/tasks", json={"name": "run", "command": "smeltery run cfg.toml", "cwd": "/tmp"})
    assert r.status_code == 201 and r.json()["status"] == "queued"
    assert q.submitted == [("run", ["smeltery", "run", "cfg.toml"], "/tmp", "")]


def test_submit_refuses_an_empty_or_unparseable_command(client_for):
    c, _ = client_for()
    assert c.post("/api/tasks", json={"command": []}).status_code == 400
    assert c.post("/api/tasks", json={"command": ""}).status_code == 400
    r = c.post("/api/tasks", json={"command": "echo 'unbalanced"})
    assert r.status_code == 400 and "invalid command" in r.json()["detail"]


def test_list_log_and_cancel(client_for):
    q = FakeTaskQueue()
    c, _ = client_for(queue=q)
    tid = c.post("/api/tasks", json={"command": ["sleep", "60"]}).json()["id"]
    q.logs[tid] = "<b>bold</b>\n"
    assert [t["id"] for t in c.get("/api/tasks").json()] == [tid]
    assert c.get(f"/api/tasks/{tid}/log").json()["log"] == "<b>bold</b>\n"
    assert c.post(f"/api/tasks/{tid}/cancel").json()["status"] == "cancelled"


def test_unknown_task_ids_are_404(client_for):
    c, _ = client_for()
    assert c.get("/api/tasks/ghost/log").status_code == 404
    assert c.post("/api/tasks/ghost/cancel").status_code == 404


def test_a_real_subprocess_queue_runs_and_reports_the_exit_code(client_for, tmp_path):
    import time

    from smeltery.bench.adapters.task_queue import SubprocessTaskQueue

    c, _ = client_for(queue=SubprocessTaskQueue(tmp_path / "q"))
    tid = c.post("/api/tasks", json={"command": ["bash", "-c", "echo hi; exit 3"]}).json()["id"]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        t = next(x for x in c.get("/api/tasks").json() if x["id"] == tid)
        if t["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    assert (t["status"], t["returncode"]) == ("failed", 3)
    assert "hi" in c.get(f"/api/tasks/{tid}/log").json()["log"]
