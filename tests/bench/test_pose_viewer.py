import json

import pytest
from fastapi.testclient import TestClient

from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench.app import create_app
from smeltery.gui import scenes


@pytest.fixture
def run_dir(tmp_path):
    from gui_fixture import make_run_dir

    return make_run_dir(tmp_path)


@pytest.fixture
def run_dir_no_poses(tmp_path):
    from gui_fixture import make_run_dir

    rd = make_run_dir(tmp_path)
    (p,) = rd.glob("run-*.json")
    rec = json.loads(p.read_text())
    for k in ("per_pose", "units", "poses", "field", "receptor"):
        rec["results"].pop(k, None)
    p.write_text(json.dumps(rec))
    return rd


def fakes(run_dir):
    rd = run_dir
    return dict(
        run_store=FakeRunStore(rd),
        task_queue=FakeTaskQueue(),
        identity=FakeIdentity("mg"),
        clock=FixedClock(),
    )


def test_a_scene_is_returned_for_a_candidate_and_pose(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    s = c.get("/api/runs/0/scene?candidate=4-F&pose=0").json()
    assert isinstance(s, dict)


def test_the_parent_is_shown_muted_alongside(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    s = c.get("/api/runs/0/scene?candidate=4-F&pose=0").json()
    text = json.dumps(s)
    assert str(scenes.PARENT_OPACITY) in text


def test_a_record_without_pose_output_is_409(run_dir_no_poses):
    c = TestClient(create_app(**fakes(run_dir_no_poses)))
    r = c.get("/api/runs/0/scene?candidate=4-F&pose=0")
    assert r.status_code == 409


def test_an_out_of_range_pose_is_400(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    r = c.get("/api/runs/0/scene?candidate=4-F&pose=99")
    assert r.status_code == 400
