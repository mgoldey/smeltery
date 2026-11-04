import pathlib

import pytest
from fastapi.testclient import TestClient

from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench.app import create_app
from smeltery.gui.catalog import RUNGS


@pytest.fixture
def run_dir(tmp_path):
    from gui_fixture import make_run_dir

    return make_run_dir(tmp_path)


def fakes():
    rd = pathlib.Path("/tmp") / "pe"
    rd.mkdir(parents=True, exist_ok=True)
    return dict(
        run_store=FakeRunStore(rd),
        task_queue=FakeTaskQueue(),
        identity=FakeIdentity("mg"),
        clock=FixedClock(),
    )


def test_every_issue_is_returned():
    # build a broken toml with two issues
    toml = 'campaign = "c"\nparent = "p"\ncandidates = {a = "C"}\n'
    c = TestClient(create_app(**fakes()))
    r = c.post("/api/pipeline/check", json={"toml": toml})
    assert r.status_code == 200
    issues = r.json()
    assert len([i for i in issues if i["level"] == "error"]) >= 2


def test_the_palette_groups_methods_by_rung():
    c = TestClient(create_app(**fakes()))
    pal = c.get("/api/catalog").json()
    assert set(pal.keys()) == {r[0] for r in RUNGS}
    assert "ab_initio" in pal


def test_an_unwired_tier_is_listed_with_a_reason_but_not_addable():
    c = TestClient(create_app(**fakes()))
    pal = c.get("/api/catalog").json()
    unwired = [m for rung in pal.values() for m in rung if not m["wired"]]
    assert unwired and all(m["reason"] for m in unwired)
    addable = c.get("/api/pipeline/addable").json()
    assert "qmmm" not in addable


def test_rigour_placeholder():
    c = TestClient(create_app(**fakes()))
    pal = c.get("/api/catalog").json()
    quantities = {m["key"]: m.get("quantity") for rung in pal.values() for m in rung}
    assert quantities.get("designability") != quantities.get("field_dft_interaction")


def test_site_clicking_two_atoms_writes_one_pocket_site():
    toml = open("examples/mwe_benzoic.toml").read()
    c = TestClient(create_app(**fakes()))
    r = c.post("/api/pipeline/site", json={"toml": toml, "atom": 3, "from_atom": 4, "q": 1.0, "distance": 3.0})
    assert r.status_code == 200
    assert "[[pocket.site]]" in r.json()["toml"]
    assert "atom = 3" in r.json()["toml"]


def test_the_returned_toml_checks_clean():
    toml = open("examples/mwe_benzoic.toml").read()
    c = TestClient(create_app(**fakes()))
    once = c.post(
        "/api/pipeline/site", json={"toml": toml, "atom": 3, "from_atom": 4, "q": 1.0, "distance": 3.0}
    ).json()["toml"]
    twice = c.post("/api/pipeline/check", json={"toml": once}).json()
    assert not [i for i in twice if i["level"] == "error"]
