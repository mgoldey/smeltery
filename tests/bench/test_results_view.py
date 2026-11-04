import pytest
from fastapi.testclient import TestClient
from gui_fixture import make_run_dir

from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench.app import create_app
from smeltery.gui import results as gui_results


@pytest.fixture
def run_dir(tmp_path):
    return make_run_dir(tmp_path)


def fakes(run_dir):
    return dict(
        run_store=FakeRunStore(run_dir),
        task_queue=FakeTaskQueue(),
        identity=FakeIdentity("mg"),
        clock=FixedClock(),
    )


def test_resolved_matrix_is_served_exactly(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    r = c.get("/api/runs/0/resolved")
    assert r.status_code == 200
    run = gui_results.load_run(str(list(run_dir.glob("run-*.json"))[0]))
    names, matrix = gui_results.resolved_matrix(run)
    assert r.json()["names"] == names
    assert r.json()["matrix"] == matrix


def test_ddE_rows_equal_ddE_table(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    r = c.get("/api/runs/0/ddE")
    assert r.status_code == 200
    run = gui_results.load_run(str(list(run_dir.glob("run-*.json"))[0]))
    assert r.json()["rows"] == gui_results.ddE_table(run)
    assert "floor" in r.json()
    assert "floor_source" in r.json()
    assert "z" in r.json()
    assert "keep" in r.json()
    assert "verdict" in r.json()


def test_no_composite_score_column(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    r = c.get("/api/runs/0/ddE")
    for row in r.json()["rows"]:
        assert "score" not in row
        assert "overall" not in row
        assert "composite" not in row
