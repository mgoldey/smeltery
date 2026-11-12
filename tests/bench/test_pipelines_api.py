"""Workspace pipelines over HTTP: list, read, save with a stale-write guard, create blank or from a run."""

import json
from pathlib import Path

from bench.conftest import record_path

BLANK = {"campaign": "cdk2", "parent": "a", "candidates": {"a": "CCO", "b": "CCN"}}


def _make(c, name="p1"):
    r = c.post("/api/pipelines", json={"name": name, "blank": BLANK})
    assert r.status_code == 201, r.text
    return r.json()


def test_a_blank_pipeline_is_created_listed_and_read_back(client_for):
    c, _ = client_for()
    made = _make(c)
    assert made["name"] == "p1" and 'campaign = "cdk2"' in made["toml"]
    assert c.get("/api/pipelines").json() == ["p1"]
    got = c.get("/api/pipelines/p1").json()
    assert got["toml"] == made["toml"] and got["sha256"] == made["sha256"]
    assert Path(got["folder"], "config.toml").read_text() == got["toml"]


def test_a_draft_with_errors_is_saved_and_reports_them(client_for):
    c, _ = client_for()
    made = _make(c)
    # a blank pipeline lacks sites and keep, so it already reports errors; saving it must not refuse
    assert made["ok"] is False and any(i["level"] == "error" for i in made["issues"])
    draft = made["toml"] + "\n# draft\n"
    r = c.put("/api/pipelines/p1", json={"toml": draft, "base_sha256": made["sha256"]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and body["issues"]
    assert c.get("/api/pipelines/p1").json()["toml"] == draft


def test_a_second_save_on_a_stale_base_is_409_and_keeps_the_first(client_for):
    c, _ = client_for()
    made = _make(c)
    first = c.put("/api/pipelines/p1", json={"toml": made["toml"] + "# one\n", "base_sha256": made["sha256"]})
    assert first.status_code == 200
    second = c.put("/api/pipelines/p1", json={"toml": made["toml"] + "# two\n", "base_sha256": made["sha256"]})
    assert second.status_code == 409
    assert "changed" in second.json()["reason"]
    assert c.get("/api/pipelines/p1").json()["toml"].endswith("# one\n")


def test_unparseable_toml_is_400_with_its_line_and_is_not_saved(client_for):
    c, _ = client_for()
    made = _make(c)
    r = c.put("/api/pipelines/p1", json={"toml": 'campaign = "x"\nbad = = 1\n', "base_sha256": made["sha256"]})
    assert r.status_code == 400 and "line 2" in r.json()["reason"]
    assert c.get("/api/pipelines/p1").json()["toml"] == made["toml"]


def test_unknown_taken_and_invalid_names(client_for):
    c, _ = client_for()
    assert c.get("/api/pipelines/ghost").status_code == 404
    assert c.put("/api/pipelines/ghost", json={"toml": "", "base_sha256": None}).status_code == 404
    _make(c)
    assert c.post("/api/pipelines", json={"name": "p1", "blank": BLANK}).status_code == 409
    assert c.post("/api/pipelines", json={"name": "Bad Name", "blank": BLANK}).status_code == 400
    assert c.get("/api/pipelines/Bad Name").status_code in (400, 404)


def test_create_needs_exactly_one_source(client_for):
    c, _ = client_for()
    assert c.post("/api/pipelines", json={"name": "p1"}).status_code == 400
    r = c.post("/api/pipelines", json={"name": "p1", "blank": BLANK, "from_run": 0})
    assert r.status_code == 400


def test_from_prebuilt_is_unknown_until_there_are_any(client_for):
    c, _ = client_for()
    r = c.post("/api/pipelines", json={"name": "p1", "from_prebuilt": "anything"})
    assert r.status_code == 400 and "unknown prebuilt pipeline" in r.json()["reason"]
    assert c.get("/api/pipelines").json() == []


def test_from_a_run_without_a_recorded_config_reconstructs_it_with_the_note(client_for, run_dir):
    c, _ = client_for(run_dir)
    r = c.post("/api/pipelines", json={"name": "next", "from_run": 0})
    assert r.status_code == 201, r.text
    body = r.json()
    assert 'campaign = "' in body["toml"] and any("place the sites again" in n for n in body["notes"])


def _record_config(run_dir, toml: str, inputs: dict[str, str]):
    """Give a fixture run dir the config.toml and staged inputs a Task 1.3 run records."""
    (run_dir / "config.toml").write_text(toml)
    info = {}
    for rel, content in inputs.items():
        (run_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (run_dir / rel).write_text(content)
        info["pocket.file"] = {"source": "orig.pdb", "sha256": "0" * 64, "copy": rel}
    p = record_path(run_dir)
    rec = json.loads(p.read_text())
    rec["results"]["config"] = {"file": "config.toml", "sha256": "0" * 64, "inputs": info}
    p.write_text(json.dumps(rec))


def test_from_a_run_with_a_recorded_config_keeps_its_sites(client_for, run_dir):
    toml = (
        'campaign = "benzoic"\nparent = "benzoic"\n\n[candidates]\nbenzoic = "OC(=O)c1ccccc1"\n\n'
        '[pocket]\nfile = "inputs/abc.pdb"\n\n[[pocket.site]]\natom = 3\nfrom_atom = 1\nq = 1.0\ndistance = 3.0\n'
    )
    _record_config(run_dir, toml, {"inputs/abc.pdb": "ATOM\n"})
    c, _ = client_for(run_dir)
    r = c.post("/api/pipelines", json={"name": "next", "from_run": 0})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["toml"] == toml and "[[pocket.site]]" in body["toml"]
    assert Path(body["folder"], "inputs", "abc.pdb").read_text() == "ATOM\n"
    assert not any("place the sites again" in n for n in body["notes"])
    assert c.get("/api/pipelines/next").json()["toml"] == toml


def test_a_recorded_input_that_is_missing_from_the_run_dir_is_409_and_creates_nothing(client_for, run_dir):
    _record_config(run_dir, 'campaign = "x"\n', {"inputs/abc.pdb": "ATOM\n"})
    (run_dir / "inputs" / "abc.pdb").unlink()
    c, _ = client_for(run_dir)
    r = c.post("/api/pipelines", json={"name": "next", "from_run": 0})
    assert r.status_code == 409 and "abc.pdb" in r.json()["reason"]
    assert c.get("/api/pipelines").json() == []


def test_an_unknown_run_is_404(client_for):
    c, _ = client_for()
    assert c.post("/api/pipelines", json={"name": "n", "from_run": 5}).status_code == 404


def test_a_blank_pipeline_with_an_odd_parent_is_a_saved_draft_not_a_500(client_for):
    # Pipeline.minimal does not raise for this input; a draft may be saved with errors, so it is created.
    c, _ = client_for()
    bad = {"campaign": "x", "parent": "zzz", "candidates": {"a": "CCO"}}
    r = c.post("/api/pipelines", json={"name": "p1", "blank": bad})
    assert r.status_code == 201 and r.json()["ok"] is False
    assert c.get("/api/pipelines").json() == ["p1"]


def test_a_save_without_a_base_says_a_base_is_required(client_for):
    c, _ = client_for()
    _make(c)
    r = c.put("/api/pipelines/p1", json={"toml": "a = 1\n"})
    assert r.status_code == 409 and "base_sha256 is required" in r.json()["reason"]


def test_crlf_text_saved_over_http_does_not_make_the_next_save_stale(client_for):
    c, _ = client_for()
    made = _make(c)
    r1 = c.put("/api/pipelines/p1", json={"toml": 'a = "ΔE"\r\n', "base_sha256": made["sha256"]}).json()
    got = c.get("/api/pipelines/p1").json()
    assert got["sha256"] == r1["sha256"] and got["toml"] == 'a = "ΔE"\r\n'
    assert c.put("/api/pipelines/p1", json={"toml": "a = 2\n", "base_sha256": got["sha256"]}).status_code == 200
