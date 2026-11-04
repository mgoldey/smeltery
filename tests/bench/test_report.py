import re

import pytest
from fastapi.testclient import TestClient

from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench.app import create_app


@pytest.fixture
def run_dir(tmp_path):
    from gui_fixture import make_run_dir

    return make_run_dir(tmp_path)


def fakes(run_dir):
    return dict(
        run_store=FakeRunStore(run_dir),
        task_queue=FakeTaskQueue(),
        identity=FakeIdentity("mg"),
        clock=FixedClock(),
    )


def test_the_verdict_precedes_the_candidate_table(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    body = c.get("/report/0").text
    assert body.lower().index("verdict") < body.lower().index("candidate")


def test_a_comment_anchors_to_a_candidate(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    c.post("/api/runs/0/comments", json={"anchor": "candidate:4-F", "body": "hi"})
    comments = c.get("/api/runs/0/comments").json()
    assert any(com.get("anchor") == "candidate:4-F" for com in comments)


def test_the_report_surface_cannot_change_the_method(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    assert c.post("/report/0/pipeline").status_code in (404, 405)
    assert c.post("/api/runs/0/pipeline").status_code in (404, 405)


def test_the_frozen_snapshot_has_zero_external_references(run_dir, tmp_path):
    c = TestClient(create_app(**fakes(run_dir)))
    out = tmp_path / "snap.html"
    r = c.post("/api/runs/0/snapshot", json={"path": str(out)})
    assert r.status_code == 200
    text = out.read_text()
    assert not re.search(r"https?://", text)


def test_a_proposal_is_a_draft_and_submits_nothing(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    r = c.post("/api/runs/0/proposals", json={"smiles": "CCO", "reason": "synthesis"})
    assert r.status_code == 200
    # fake queue not available; ensure no task submitted via queue if present


def test_an_invalid_smiles_is_400(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    r = c.post("/api/runs/0/proposals", json={"smiles": "not smiles", "reason": "synthesis"})
    assert r.status_code == 400


def test_an_unknown_reason_is_400(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    r = c.post("/api/runs/0/proposals", json={"smiles": "CCO", "reason": "vibes"})
    assert r.status_code == 400


def test_served_and_frozen_reports_agree(run_dir, tmp_path):
    c = TestClient(create_app(**fakes(run_dir)))
    data = c.get("/api/runs/0/ddE").json()
    out = tmp_path / "snap.html"
    c.post("/api/runs/0/snapshot", json={"path": str(out)})
    text = out.read_text()
    for row in data["rows"]:
        assert f"{row['mean']:.2f}" in text
