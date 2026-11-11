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


def test_retries_pass_through_and_are_reported_as_max_attempts(client_for):
    c, _ = client_for(queue=FakeTaskQueue())
    r = c.post("/api/tasks", json={"command": "true", "retries": 2})
    assert r.status_code == 201 and r.json()["max_attempts"] == 3
    r = c.post("/api/tasks", json={"command": "true"})
    assert r.json()["max_attempts"] == 1


def test_out_of_range_retries_are_a_422(client_for):
    c, _ = client_for(queue=FakeTaskQueue())
    for bad in (-1, 6):
        assert c.post("/api/tasks", json={"command": "true", "retries": bad}).status_code == 422


def test_a_task_carries_its_health_fields_through_the_api(client_for):
    """HPC-004/HPC-002: the panel can only show heartbeat age and OOM if the API carries them."""
    c, _ = client_for()
    r = c.post("/api/tasks", json={"command": ["python", "-c", "print('ok')"]})
    assert r.status_code == 201, r.text
    body = r.json()
    for key in ("heartbeat_at", "heartbeat_age_s", "stalled", "peak_rss_gb", "oom", "memory_limit_gb"):
        assert key in body, f"{key} missing from the task the API returns"
    assert body["stalled"] is False and body["oom"] is False


def test_a_memory_limit_is_passed_to_the_queue(client_for):
    c, ports = client_for()
    r = c.post("/api/tasks", json={"command": ["python", "-c", "pass"], "memory_limit_gb": 4.0})
    assert r.status_code == 201, r.text
    assert r.json()["memory_limit_gb"] == 4.0


def test_a_non_positive_memory_limit_is_refused_by_the_schema(client_for):
    c, _ = client_for()
    r = c.post("/api/tasks", json={"command": ["python", "-c", "pass"], "memory_limit_gb": 0})
    assert r.status_code == 422, r.text


def test_a_timeout_is_passed_to_the_queue(client_for):
    q = FakeTaskQueue()
    c, _ = client_for(queue=q)
    r = c.post("/api/tasks", json={"command": ["sleep", "60"], "timeout_s": 30})
    assert r.status_code == 201, r.text
    assert q.options[-1]["timeout_s"] == 30


def test_a_non_positive_timeout_is_refused_by_the_schema(client_for):
    c, _ = client_for()
    assert c.post("/api/tasks", json={"command": ["true"], "timeout_s": 0}).status_code == 422
