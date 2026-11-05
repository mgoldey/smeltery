"""Prebuilt pipelines over HTTP: the list, and starting a workspace pipeline from one."""

import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from bench.fakes import FakeIdentity, FakeTaskQueue, FixedClock
from smeltery.bench.adapters.run_store import FilesystemRunStore
from smeltery.bench.app import Settings, create_app

NAMES = ["benzoic-pbe", "benzoic-rhf", "docking", "structure"]
PREBUILT = Path(__file__).resolve().parents[2] / "src" / "smeltery" / "prebuilt"


def _tree(folder: Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def _real_client(tmp_path):
    root = tmp_path / "bench-root"
    app = create_app(
        run_store=FilesystemRunStore(root),
        task_queue=FakeTaskQueue(),
        identity=FakeIdentity("mg"),
        clock=FixedClock(),
        settings=Settings(root=root, studies=(), web_dir=tmp_path / "no-web"),
    )
    return TestClient(app), root


def test_the_route_lists_all_four_with_their_two_lines_and_the_example_verdict(client_for):
    c, _ = client_for()
    got = c.get("/api/prebuilt")
    assert got.status_code == 200, got.text
    by = {p["name"]: p for p in got.json()}
    assert sorted(by) == NAMES
    for n in NAMES:
        two = (PREBUILT / n / "about.md").read_text().strip().splitlines()
        assert [by[n]["about"], by[n]["needs"]] == two
    assert {n: by[n]["verdict"]["state"] for n in NAMES} == {
        "benzoic-pbe": "RESOLVED",
        "benzoic-rhf": "RESOLVED",
        "docking": "UNRANKED",
        "structure": "UNRANKED",
    }
    assert "pdb2pqr30" in by["docking"]["needs"]


def test_starting_from_a_prebuilt_copies_it_and_leaves_it_alone(tmp_path):
    c, root = _real_client(tmp_path)
    before = _tree(PREBUILT)
    for n in NAMES:
        r = c.post("/api/pipelines", json={"name": f"my-{n}", "from_prebuilt": n})
        assert r.status_code == 201, r.text
        doc = r.json()
        # R15: the text is the packaged pipeline.toml exactly, with no workspace defaults copied in
        assert doc["toml"].encode() == (PREBUILT / n / "pipeline.toml").read_bytes()
        folder = Path(doc["folder"])
        assert (folder / "config.toml").read_bytes() == (PREBUILT / n / "pipeline.toml").read_bytes()
        for f in (PREBUILT / n).iterdir():
            if f.name not in ("pipeline.toml", "about.md", "run"):
                assert (folder / f.name).read_bytes() == f.read_bytes()
        assert _tree(PREBUILT / n / "run") == _tree(folder / "run")
        # edit the copy: save over it, then change its run directory
        put = c.put(f"/api/pipelines/my-{n}", json={"toml": doc["toml"] + "# edited\n", "base_sha256": doc["sha256"]})
        assert put.status_code == 200, put.text
        for p in (folder / "run").glob("run-*.json"):
            p.write_text("{}")
    assert _tree(PREBUILT) == before


def test_the_example_run_is_listed_as_a_run_under_the_new_pipeline(tmp_path):
    c, root = _real_client(tmp_path)
    assert c.get("/api/runs").json() == []
    doc = c.post("/api/pipelines", json={"name": "pbe", "from_prebuilt": "benzoic-pbe"}).json()
    runs = c.get("/api/runs").json()
    assert len(runs) == 1
    assert runs[0]["path"].startswith(str(Path(doc["folder"]) / "run"))
    assert runs[0]["verdict"]["state"] == "RESOLVED"
    # the copied pipeline is complete: no step wants input, and none is stale or changed
    steps = c.post("/api/pipelines/steps", json={"toml": doc["toml"]}).json()["steps"]
    assert {s["state"] for s in steps} <= {"ready", "off"} and "ready" in {s["state"] for s in steps}


def test_an_unknown_prebuilt_or_a_taken_name_is_refused(tmp_path):
    c, _ = _real_client(tmp_path)
    r = c.post("/api/pipelines", json={"name": "x", "from_prebuilt": "nope"})
    assert r.status_code == 400 and "unknown prebuilt" in r.json()["detail"]
    assert c.post("/api/pipelines", json={"name": "x", "from_prebuilt": "docking"}).status_code == 201
    assert c.post("/api/pipelines", json={"name": "x", "from_prebuilt": "docking"}).status_code == 409
