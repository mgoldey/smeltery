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
