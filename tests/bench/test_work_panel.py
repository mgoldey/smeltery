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


# ------------------------------------------------------------------------------------------------- run progress


def _running_run(q, out, *, cwd=None, command=None):
    tid = q.submit("run x", command or ["smeltery", "run", "c.toml", "--out", str(out)], cwd=cwd or str(out.parent))
    q.tasks[-1].status = "running"
    return tid


def _checkpoint(out, total, lines, reused=0):
    import json

    out.mkdir(parents=True, exist_ok=True)
    (out / "progress.json").write_text(
        json.dumps({"partial": "calcs.partial.jsonl", "total_scfs": total, "reused": reused})
    )
    (out / "calcs.partial.jsonl").write_text("".join(f'{{"i": {i}}}\n' for i in range(lines)))


def _progress(c, tid):
    return next(t for t in c.get("/api/tasks").json() if t["id"] == tid)["progress"]


def test_a_running_run_reports_scfs_done_of_planned(client_for, tmp_path):
    q = FakeTaskQueue()
    c, _ = client_for(queue=q)
    out = tmp_path / "out"
    _checkpoint(out, total=12, lines=5)
    tid = _running_run(q, out)
    assert _progress(c, tid) == {"done": 5, "total": 12, "reused": 0}
    with (out / "calcs.partial.jsonl").open("a") as f:  # the next poll counts only what was appended
        f.write('{"i": 5}\n{"i": 6}\n{"half')
    assert _progress(c, tid)["done"] == 7  # the half-written line is not counted yet


def test_a_relative_out_resolves_against_the_task_cwd(client_for, tmp_path):
    q = FakeTaskQueue()
    c, _ = client_for(queue=q)
    _checkpoint(tmp_path / "out", total=4, lines=1)
    tid = _running_run(q, tmp_path / "out", cwd=str(tmp_path), command=["smeltery", "run", "c.toml", "--out", "out"])
    assert _progress(c, tid) == {"done": 1, "total": 4, "reused": 0}


def test_no_progress_file_means_no_progress_not_zero(client_for, tmp_path):
    q = FakeTaskQueue()
    c, _ = client_for(queue=q)
    (tmp_path / "out").mkdir()
    tid = _running_run(q, tmp_path / "out")
    other = _running_run(q, tmp_path / "out", command=["sleep", "60"])  # not a run at all
    assert _progress(c, tid) is None
    assert _progress(c, other) is None


def test_a_finished_task_has_no_progress_even_if_files_linger(client_for, tmp_path):
    q = FakeTaskQueue()
    c, _ = client_for(queue=q)
    _checkpoint(tmp_path / "out", total=4, lines=2)
    tid = _running_run(q, tmp_path / "out")
    q.tasks[-1].status = "failed"
    assert _progress(c, tid) is None


def test_a_resumed_run_counts_reused_scfs_as_done(client_for, tmp_path):
    q = FakeTaskQueue()
    c, _ = client_for(queue=q)
    _checkpoint(tmp_path / "out", total=10, lines=6, reused=6)  # six taken from the earlier attempt, none new yet
    tid = _running_run(q, tmp_path / "out")
    assert _progress(c, tid) == {"done": 6, "total": 10, "reused": 6}


def test_a_progress_file_with_no_checkpoint_yet_is_zero_done(client_for, tmp_path):
    import json

    q = FakeTaskQueue()
    c, _ = client_for(queue=q)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "progress.json").write_text(json.dumps({"partial": "calcs.partial.jsonl", "total_scfs": 8}))
    assert _progress(c, _running_run(q, tmp_path / "out")) == {"done": 0, "total": 8, "reused": 0}


def test_a_remedy_ladder_grows_the_plan_instead_of_overflowing_it(client_for, tmp_path):
    q = FakeTaskQueue()
    c, _ = client_for(queue=q)
    _checkpoint(tmp_path / "out", total=2, lines=3)
    assert _progress(c, _running_run(q, tmp_path / "out")) == {"done": 3, "total": 3, "reused": 0}


def test_counting_a_large_checkpoint_is_incremental_and_cheap(tmp_path):
    import time

    from smeltery.bench.progress import RunProgress

    p = tmp_path / "calcs.partial.jsonl"
    p.write_text("".join(f'{{"candidate": "c{i}", "pad": "{"x" * 400}"}}\n' for i in range(5000)))
    rp = RunProgress()
    assert rp.lines(p) == 5000
    t0 = time.perf_counter()
    for _ in range(200):
        assert rp.lines(p) == 5000
    assert (time.perf_counter() - t0) / 200 < 0.005  # an unchanged file costs a stat, not a re-read
    with p.open("a") as f:
        f.write("{}\n")
    assert rp.lines(p) == 5001
    p.write_text("{}\n")  # replaced by a shorter file (--force): counted afresh
    assert rp.lines(p) == 1
