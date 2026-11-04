import pathlib

import pytest
from fastapi.testclient import TestClient

from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench.app import create_app


@pytest.fixture
def run_dir(tmp_path):
    from gui_fixture import make_run_dir

    return make_run_dir(tmp_path)


def fakes():
    rd = pathlib.Path("/tmp") / "cp"
    rd.mkdir(parents=True, exist_ok=True)
    return dict(
        run_store=FakeRunStore(rd),
        task_queue=FakeTaskQueue(),
        identity=FakeIdentity("mg"),
        clock=FixedClock(),
    )


def test_dag_rows_are_in_order():
    c = TestClient(create_app(**fakes()))
    r = c.get("/api/studies/bench.study_fixture/dag")
    assert r.status_code == 200
    rows = r.json()
    assert [x["order"] for x in rows] == sorted(x["order"] for x in rows)


def test_cells_report_cached_or_to_run():
    c = TestClient(create_app(**fakes()))
    r = c.get("/api/studies/bench.study_fixture/cells")
    assert r.status_code == 200
    cells = r.json()
    assert all(c["state"] in ("cached", "to_run") for c in cells)


def test_an_unimportable_study_path_is_404_naming_it():
    c = TestClient(create_app(**fakes()))
    r = c.get("/api/studies/nope.module/dag")
    assert r.status_code == 404
    assert "nope.module" in r.text
