import time

import pytest
from fastapi.testclient import TestClient

from bench.fakes import FakeArtifactStore, FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench.app import create_app


def fakes(run_dir=None, queue=None):
    import pathlib
    import tempfile

    if run_dir is None:
        rd = pathlib.Path(tempfile.mkdtemp()) / "out"
        rd.mkdir(parents=True, exist_ok=True)
    else:
        rd = run_dir
    return dict(
        run_store=FakeRunStore(rd if run_dir else rd.parent if False else rd),
        task_queue=queue if queue is not None else FakeTaskQueue(),
        identity=FakeIdentity("mg"),
        clock=FixedClock(),
        artifact_store=FakeArtifactStore(pathlib.Path(tempfile.mkdtemp()) / ".art"),
    )


class BrokenTaskQueue:
    def list(self):
        raise RuntimeError("temporal down")

    def cancel(self, tid):
        raise RuntimeError("temporal down")


@pytest.fixture
def run_dir(tmp_path):
    from gui_fixture import make_run_dir

    return make_run_dir(tmp_path)


@pytest.fixture
def incomplete_run_dir(tmp_path):
    import json

    from gui_fixture import make_run_dir

    rd = make_run_dir(tmp_path)
    (p,) = rd.glob("run-*.json")
    rec = json.loads(p.read_text())
    del rec["results"]["cut"]
    p.write_text(json.dumps(rec))
    return rd


def test_healthz_answers_200():
    c = TestClient(create_app(**fakes()))
    assert c.get("/healthz").status_code == 200


def test_runs_list_is_served_from_the_run_store(run_dir):
    c = TestClient(create_app(**fakes(run_dir=run_dir)))
    r = c.get("/api/runs")
    assert r.status_code == 200 and r.json()[0]["parent"] == "benzoic"


def test_a_dead_task_backend_is_503_while_runs_still_answer(run_dir):
    c = TestClient(create_app(**fakes(run_dir=run_dir, queue=BrokenTaskQueue())))
    assert c.get("/api/tasks").status_code == 503
    assert "reason" in c.get("/api/tasks").json() or c.get("/api/tasks").json().get("error")
    assert c.get("/api/runs").status_code == 200


def test_the_task_failure_is_fast_not_a_hang(run_dir):
    c = TestClient(create_app(**fakes(run_dir=run_dir, queue=BrokenTaskQueue())))
    t0 = time.monotonic()
    c.get("/api/tasks")
    assert time.monotonic() - t0 < 2.0


def test_an_incomplete_record_is_served_as_INCOMPLETE(incomplete_run_dir):
    c = TestClient(create_app(**fakes(run_dir=incomplete_run_dir)))
    assert c.get("/api/runs/0/verdict").json()["state"] == "INCOMPLETE"


def test_unknown_paths_are_404():
    assert TestClient(create_app(**fakes())).get("/nope").status_code == 404


def test_local_stores_live_under_the_runs_root(tmp_path, monkeypatch):
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    root = tmp_path / "root"
    create_app(root=root)
    # trigger store init by touching app state (already initialized)
    base = root / ".smeltery" / "bench"
    assert (base / "comments.sqlite").exists()
    assert (base / "projects.sqlite").exists()
    # none in cwd
    assert not any(p.name.endswith(".sqlite") for p in cwd.iterdir()) if cwd.exists() else True
