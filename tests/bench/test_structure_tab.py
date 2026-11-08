import hashlib
import pathlib

import pytest
from fastapi.testclient import TestClient

from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench.app import create_app


@pytest.fixture
def pdb_path(tmp_path):
    p = tmp_path / "t.pdb"
    p.write_text(
        "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\n"
        "HETATM  100  O   HOH A  99       1.000   1.000   1.000  1.00  0.00           O\n"
        "HETATM  101  C   LIG A 100       2.000   2.000   2.000  1.00  0.00           C\n"
    )
    return p


def fakes():
    rd = pathlib.Path("/tmp") / "st"
    rd.mkdir(parents=True, exist_ok=True)
    return dict(
        run_store=FakeRunStore(rd),
        task_queue=FakeTaskQueue(),
        identity=FakeIdentity("mg"),
        clock=FixedClock(),
    )


def test_a_local_pdb_reports_its_sha256(pdb_path):
    c = TestClient(create_app(**fakes()))
    r = c.post("/api/structure", json={"source": str(pdb_path)})
    assert r.status_code == 200
    assert r.json()["sha256"] == hashlib.sha256(pdb_path.read_bytes()).hexdigest()


def test_what_was_dropped_is_listed(pdb_path):
    c = TestClient(create_app(**fakes()))
    r = c.post("/api/structure", json={"source": str(pdb_path)})
    assert "HOH" in r.text


def test_the_preview_carries_the_validation_summary(pdb_path):
    c = TestClient(create_app(**fakes()))
    j = c.post("/api/structure", json={"source": str(pdb_path)}).json()
    assert j["chains"] == {"A": 1} and j["n_hetatm"] == 2
    assert j["missing_residues"] is None and j["possible_gaps"] == [] and j["warnings"] == []


def test_network_source_without_permission_is_400():
    c = TestClient(create_app(**fakes()))
    r = c.post("/api/structure", json={"source": "1ABC"})
    assert r.status_code == 400
    assert "network" in r.text.lower()


def test_a_missing_file_is_400_naming_it(tmp_path):
    c = TestClient(create_app(**fakes()))
    missing = str(tmp_path / "nope.pdb")
    r = c.post("/api/structure", json={"source": missing})
    assert r.status_code == 400
    assert missing in r.text


def test_a_dropped_file_is_described_by_its_text(client_for):
    """Data source: the committed e2e PDB, which has an EXPDTA record, a ligand (LIG) and two waters (HOH)."""
    import hashlib
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / "ui" / "e2e" / "data" / "site.pdb").read_text()
    c, _ = client_for()
    r = c.post("/api/structure/text", json={"name": "site.pdb", "text": text})
    assert r.status_code == 200
    body = r.json()
    assert body["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert body["origin"] == "EXPERIMENTAL" and body["dropped"] == ["LIG", "HOH"]
    assert c.get(body["file_url"]).text == text


def test_text_that_is_not_a_pdb_is_400(client_for):
    c, _ = client_for()
    r = c.post("/api/structure/text", json={"name": "notes.txt", "text": "hello"})
    assert r.status_code == 400 and "PDB" in r.json()["detail"]
