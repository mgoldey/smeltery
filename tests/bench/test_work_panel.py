"""The task API behind the work panel: list, log, cancel, over the TaskQueue port.

There is no route that submits an arbitrary command (it was remote code execution); tasks come only from the launch
and preview use cases, so these tests put tasks on the queue directly.
"""

from bench.fakes import FakeTaskQueue


def test_there_is_no_route_that_runs_an_arbitrary_command(client_for):
    q = FakeTaskQueue()
    c, _ = client_for(queue=q)
    assert c.post("/api/tasks", json={"command": ["touch", "/tmp/x"]}).status_code == 405
    assert q.submitted == []


def test_list_log_and_cancel(client_for):
    q = FakeTaskQueue()
    c, _ = client_for(queue=q)
    tid = q.submit("sleep", ["sleep", "60"])
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

    q = SubprocessTaskQueue(tmp_path / "q")
    c, _ = client_for(queue=q)
    tid = q.submit("bash", ["bash", "-c", "echo hi; exit 3"])
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        t = next(x for x in c.get("/api/tasks").json() if x["id"] == tid)
        if t["status"] in ("succeeded", "failed"):
            break
        time.sleep(0.1)
    assert (t["status"], t["returncode"]) == ("failed", 3)
    assert "hi" in c.get(f"/api/tasks/{tid}/log").json()["log"]


def test_retries_are_reported_as_max_attempts(client_for):
    q = FakeTaskQueue()
    c, _ = client_for(queue=q)
    q.submit("again", ["true"], retries=2)
    q.submit("once", ["true"])
    assert {t["name"]: t["max_attempts"] for t in c.get("/api/tasks").json()} == {"again": 3, "once": 1}


def test_a_task_carries_its_health_fields_through_the_api(client_for, tmp_path):
    """HPC-004/HPC-002: the panel can only show heartbeat age, OOM and the memory cap if the API carries them."""
    from smeltery.bench.adapters.task_queue import SubprocessTaskQueue

    q = SubprocessTaskQueue(tmp_path / "q")
    c, _ = client_for(queue=q)
    tid = q.submit("py", ["python", "-c", "pass"], memory_limit_gb=4.0)
    body = next(t for t in c.get("/api/tasks").json() if t["id"] == tid)
    for key in ("heartbeat_at", "heartbeat_age_s", "stalled", "peak_rss_gb", "oom", "memory_limit_gb"):
        assert key in body, f"{key} missing from the task the API returns"
    assert body["stalled"] is False and body["oom"] is False
    assert body["memory_limit_gb"] == 4.0


# The four tests that used to live here POSTed to /api/tasks, which ran any argv for anyone who could reach
# the port and is gone (see security.py). A launch is now the only way work enters the queue, so the two of
# them that asserted real behaviour -- a memory cap and a timeout reaching the queue -- assert it on the queue
# itself. The other two only checked Pydantic's `gt=0` on the deleted request model.
def test_a_memory_limit_reaches_the_queue_and_the_api_reports_it(client_for, tmp_path):
    q = FakeTaskQueue()
    c, _ = client_for(queue=q)
    tid = q.submit("py", ["python", "-c", "pass"], memory_limit_gb=4.0)
    assert q.options[-1]["memory_limit_gb"] == 4.0
    body = next(t for t in c.get("/api/tasks").json() if t["id"] == tid)
    assert body["memory_limit_gb"] == 4.0


def test_a_timeout_reaches_the_queue(client_for):
    q = FakeTaskQueue()
    client_for(queue=q)
    q.submit("sleep", ["sleep", "60"], timeout_s=30)
    assert q.options[-1]["timeout_s"] == 30
