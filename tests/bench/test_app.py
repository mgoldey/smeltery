"""The app shell: health, the run list, independent failure, verdicts, composition, and the built UI."""

import time

import pytest
from fastapi.testclient import TestClient

from smeltery.bench.app import BUILD_HINT, Settings, build_app, create_app, main


class BrokenTaskQueue:
    def list(self, status=None, limit=100):
        raise RuntimeError("temporal down")

    def cancel(self, tid):
        raise RuntimeError("temporal down")


def test_healthz_answers_200(client_for):
    c, _ = client_for()
    assert c.get("/healthz").status_code == 200


def test_livez_and_readyz(client_for, tmp_path):
    from smeltery.bench.adapters.task_queue import SubprocessTaskQueue

    (tmp_path / "bench-root").mkdir()
    c, _ = client_for(queue=SubprocessTaskQueue(tmp_path / "q"))
    assert c.get("/livez").json() == {"status": "ok"}
    r = c.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"]["task_queue"]["ok"] is True


def test_readyz_is_503_when_the_task_db_cannot_open(client_for, tmp_path):
    from smeltery.bench.adapters.task_queue import SubprocessTaskQueue

    q = SubprocessTaskQueue(tmp_path / "q")
    q.db.unlink()
    c, _ = client_for(queue=q)
    r = c.get("/readyz")
    assert r.status_code == 503 and r.json()["checks"]["task_queue"]["ok"] is False


def test_runs_list_is_served_from_the_run_store(client_for, run_dir):
    c, _ = client_for(run_dir)
    (row,) = c.get("/api/runs").json()
    assert row["idx"] == 0 and row["parent"] == "benzoic" and row["verdict"]["state"] == "RESOLVED"
    assert len(row["digest"]) == 64


def test_a_dead_task_backend_is_503_while_runs_still_answer(client_for, run_dir):
    c, _ = client_for(run_dir, queue=BrokenTaskQueue())
    r = c.get("/api/tasks")
    assert r.status_code == 503 and "temporal down" in r.json()["reason"]
    assert c.get("/api/runs").status_code == 200


def test_the_task_failure_is_fast_not_a_hang(client_for, run_dir):
    c, _ = client_for(run_dir, queue=BrokenTaskQueue())
    t0 = time.monotonic()
    c.get("/api/tasks")
    assert time.monotonic() - t0 < 2.0


@pytest.mark.parametrize(
    ("fixture", "state", "winner", "missing"),
    [
        ("run_dir", "RESOLVED", "4-F", []),
        ("unranked_run_dir", "UNRANKED", None, []),
        ("incomplete_run_dir", "INCOMPLETE", None, ["cut"]),
    ],
)
def test_verdict_endpoint_for_each_state(request, client_for, fixture, state, winner, missing):
    c, _ = client_for(request.getfixturevalue(fixture))
    v = c.get("/api/runs/0/verdict").json()
    assert (v["state"], v["winner"], v["missing"]) == (state, winner, missing)


def test_an_incomplete_run_is_409_naming_what_it_lacks(client_for, incomplete_run_dir):
    c, _ = client_for(incomplete_run_dir)
    r = c.get("/api/runs/0/ddE")
    assert r.status_code == 409 and "cut" in r.json()["detail"]


def test_unknown_paths_are_404(client_for):
    c, _ = client_for()
    assert c.get("/nope").status_code == 404
    assert c.get("/api/nope").status_code == 404


def test_whoami_is_identity_current(client_for):
    c, _ = client_for(identity="alice")
    assert c.get("/api/whoami").json()["user"] == "alice"


def test_local_stores_live_under_the_runs_root(tmp_path, monkeypatch):
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    root = tmp_path / "root"
    root.mkdir()
    build_app(root)
    base = root / ".smeltery" / "bench"
    assert (base / "comments.sqlite").exists() and (base / "projects.sqlite").exists()
    assert not list(cwd.iterdir())


def test_web_index_is_served_with_spa_fallback(tmp_path, run_dir):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><div id=root></div>")
    (web / "assets" / "app.js").write_text("console.log(1)")
    from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock

    app = create_app(
        run_store=FakeRunStore(run_dir),
        task_queue=FakeTaskQueue(),
        identity=FakeIdentity(),
        clock=FixedClock(),
        settings=Settings(root=tmp_path / "r", web_dir=web),
    )
    c = TestClient(app)
    for path in ("/", "/run/0/results", "/report/0"):
        r = c.get(path)
        assert r.status_code == 200 and "id=root" in r.text, path
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/api/nope").status_code == 404
    assert c.get("/api/runs").status_code == 200


def test_bench_refuses_without_a_built_ui(monkeypatch, tmp_path, capsys):
    import smeltery.bench.app as appmod

    monkeypatch.setattr(appmod, "WEB_DIR", tmp_path / "absent")
    assert main([str(tmp_path)]) == 2
    assert "npm run build" in capsys.readouterr().err
    assert "npm run build" in BUILD_HINT
