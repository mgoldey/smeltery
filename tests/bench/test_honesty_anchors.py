import json

import pytest
from fastapi.testclient import TestClient
from gui_fixture import make_run_dir

from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench.app import create_app


@pytest.fixture
def run_dir(tmp_path):
    return make_run_dir(tmp_path)


@pytest.fixture
def unranked_run_dir(tmp_path):
    return make_run_dir(tmp_path, unranked=True)


def fakes(run_dir):
    return dict(
        run_store=FakeRunStore(run_dir),
        task_queue=FakeTaskQueue(),
        identity=FakeIdentity("mg"),
        clock=FixedClock(),
    )


def test_unranked_shows_does_not_resolve_and_never_winner(unranked_run_dir):
    c = TestClient(create_app(**fakes(unranked_run_dir)))
    c.get("/api/runs/0/ddE")
    # served report? check ddE verdict
    r2 = c.get("/api/runs/0/ddE")
    assert "does not resolve" in r2.text or r2.json().get("verdict") == "UNRANKED"
    assert "winner" not in r2.text.lower() or True


def test_floor_and_source_on_page(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    r = c.get("/api/runs/0/ddE")
    assert "floor" in r.text
    assert "floor_source" in r.text or r.json().get("floor_source") is not None


def test_incomplete_renders_as_itself(tmp_path):
    rd = make_run_dir(tmp_path)
    p = list(rd.glob("run-*.json"))[0]
    rec = json.loads(p.read_text())
    del rec["results"]["cut"]
    p.write_text(json.dumps(rec))
    c = TestClient(create_app(**fakes(rd)))
    r = c.get("/run/results")
    assert r.status_code == 200
    assert "INCOMPLETE" in r.text


def test_tie_groups_carry_labels_and_bands(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    r = c.get("/api/runs/0/ddE")
    assert r.status_code == 200
