import pathlib

import pytest
from fastapi.testclient import TestClient

from bench.fakes import FakeTaskQueue, _task
from smeltery.bench.app import create_app


@pytest.fixture
def run_dir(tmp_path):
    from gui_fixture import make_run_dir

    return make_run_dir(tmp_path)


def fakes(queue=None):
    rd = pathlib.Path("/tmp") / "w"
    rd.mkdir(parents=True, exist_ok=True)
    from bench.fakes import FakeIdentity, FakeRunStore, FixedClock

    return dict(
        run_store=FakeRunStore(rd),
        task_queue=queue if queue is not None else FakeTaskQueue(),
        identity=FakeIdentity("mg"),
        clock=FixedClock(),
    )


def test_a_tasks_lifecycle_renders():
    q = FakeTaskQueue(tasks=[_task("a", "succeeded"), _task("b", "running")])
    c = TestClient(create_app(**fakes(q)))
    r = c.get("/work")
    assert r.status_code == 200
    assert "succeeded" in r.text and "running" in r.text
    assert "at most once" in r.text


def test_cancel_cancels():
    q = FakeTaskQueue(tasks=[_task("a", "running")])
    c = TestClient(create_app(**fakes(q)))
    r = c.post("/api/tasks/a/cancel")
    assert r.status_code in (200, 204)


def test_cancelling_an_unknown_task_is_404():
    q = FakeTaskQueue()
    c = TestClient(create_app(**fakes(q)))
    r = c.post("/api/tasks/ghost/cancel")
    assert r.status_code == 404


def test_the_panel_states_tasks_run_at_most_once():
    q = FakeTaskQueue()
    c = TestClient(create_app(**fakes(q)))
    r = c.get("/work")
    assert "at most once" in r.text


def test_an_unreachable_queue_is_reported_and_the_page_still_loads():
    class Broken:
        def list(self):
            raise RuntimeError("down")

        def cancel(self, tid):
            raise RuntimeError("down")

    c = TestClient(create_app(**fakes(Broken())))
    r = c.get("/work")
    assert r.status_code == 200
    assert "down" in r.text or "unreachable" in r.text