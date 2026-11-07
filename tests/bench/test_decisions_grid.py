import json

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


def test_a_decision_is_written_to_the_digest_named_file(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    c.post("/api/runs/0/decisions", json={"candidate": "4-F", "status": "keep", "reason": "cut_survivor", "note": ""})
    assert list(run_dir.glob("decisions-*.json"))


def test_the_user_comes_from_identity_not_the_request(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    c.post(
        "/api/runs/0/decisions",
        json={"candidate": "4-F", "status": "keep", "reason": "cut_survivor", "note": "", "user": "someone-else"},
    )
    data = json.loads(next(run_dir.glob("decisions-*.json")).read_text())
    assert data["decisions"][0]["user"] != "someone-else"


def test_a_tie_choice_without_a_note_is_400_with_the_cores_message(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    r = c.post(
        "/api/runs/0/decisions", json={"candidate": "4-Cl", "status": "keep", "reason": "within_tie_choice", "note": ""}
    )
    assert (
        r.status_code == 400 and "note" in r.json()["error"].lower()
        if "error" in r.json()
        else "note" in r.text.lower()
    )


def test_an_unknown_reason_is_400(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    r = c.post("/api/runs/0/decisions", json={"candidate": "4-F", "status": "keep", "reason": "vibes", "note": ""})
    assert r.status_code == 400


def test_a_bulk_decision_is_written_once_and_is_all_or_nothing(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    ok = {"candidates": ["4-F", "4-Cl"], "status": "keep", "reason": "cut_survivor", "note": ""}
    r = c.post("/api/runs/0/decisions/bulk", json=ok)
    assert r.status_code == 200 and [d["candidate"] for d in r.json()] == ["4-F", "4-Cl"]
    before = next(run_dir.glob("decisions-*.json")).read_text()
    for bad in (
        {**ok, "candidates": ["4-F", "nope"], "status": "reject"},
        {**ok, "reason": "other", "status": "reject"},  # note required, applies to all
    ):
        assert c.post("/api/runs/0/decisions/bulk", json=bad).status_code == 400
    assert next(run_dir.glob("decisions-*.json")).read_text() == before


def test_decisions_export_as_csv(run_dir):
    c = TestClient(create_app(**fakes(run_dir)))
    c.post("/api/runs/0/decisions", json={"candidate": "4-F", "status": "keep", "reason": "cut_survivor", "note": ""})
    r = c.get("/api/runs/0/decisions.csv")
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and ".csv" in r.headers["content-disposition"]
    lines = r.text.splitlines()
    assert lines[0] == "candidate,smiles,status,reason,note,user,time" and lines[1].startswith("4-F,")
    assert "$$$$" in c.get("/api/runs/0/decisions.sdf").text


def test_a_note_with_a_paragraph_break_survives_the_sdf(run_dir):
    from rdkit import Chem

    c = TestClient(create_app(**fakes(run_dir)))
    note = "first para\n\nsecond para"
    c.post("/api/runs/0/decisions", json={"candidate": "4-F", "status": "keep", "reason": "other", "note": note})
    sdf = c.get("/api/runs/0/decisions.sdf").text
    mol = Chem.SDMolSupplier()
    mol.SetData(sdf)
    got = next(iter(mol)).GetProp("note")
    assert "first para" in got and "second para" in got
