"""Workspace pipelines over HTTP: list, read, save with a stale-write guard, create blank or from a run."""

import json
import tomllib
from pathlib import Path

from bench.conftest import record_path, run_id_of

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
    r = c.post("/api/pipelines", json={"name": "p1", "blank": BLANK, "from_run": "deadbeefcafe-000000"})
    assert r.status_code == 400


def test_from_prebuilt_is_unknown_until_there_are_any(client_for):
    c, _ = client_for()
    r = c.post("/api/pipelines", json={"name": "p1", "from_prebuilt": "anything"})
    assert r.status_code == 400 and "unknown prebuilt pipeline" in r.json()["reason"]
    assert c.get("/api/pipelines").json() == []


def test_from_a_run_without_a_recorded_config_reconstructs_it_with_the_note(client_for, run_dir):
    c, _ = client_for(run_dir)
    r = c.post("/api/pipelines", json={"name": "next", "from_run": run_id_of(c)})
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
    r = c.post("/api/pipelines", json={"name": "next", "from_run": run_id_of(c)})
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
    r = c.post("/api/pipelines", json={"name": "next", "from_run": run_id_of(c)})
    assert r.status_code == 409 and "abc.pdb" in r.json()["reason"]
    assert c.get("/api/pipelines").json() == []


def test_an_unknown_run_is_404(client_for):
    c, _ = client_for()
    assert c.post("/api/pipelines", json={"name": "n", "from_run": "deadbeefcafe-999999"}).status_code == 404


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


# ---- next_round: one click from a decision to the next run ------------------------------------------------------

ROUND1 = """\
# round 1: the benzoic MWE with a halogen series
campaign = "benzoic"
parent = "benzoic"

[candidates]
benzoic = "OC(=O)c1ccccc1"   # the parent every ddE is measured against
4-F = "OC(=O)c1ccc(F)cc1"
4-Cl = "OC(=O)c1ccc(Cl)cc1"
4-Br = "OC(=O)c1ccc(Br)cc1"

[[pocket.site]]
q = 1.0
atom = 0
from_atom = 1
distance = 3.0

[cut]
keep = 1
z = 2.0
floor = 0.0
"""


def _round1(run_dir):
    """The fixture run with ROUND1 recorded as the config `smeltery run` staged (no copied inputs)."""
    _record_config(run_dir, ROUND1, {})
    return run_dir


def _decide(c, rid, candidate, status="reject", reason="liability"):
    r = c.post(f"/api/runs/{rid}/decisions", json={"candidate": candidate, "status": status, "reason": reason})
    assert r.status_code == 200, r.text
    return r.json()


def _next(c, name="round2", **spec):
    return c.post("/api/pipelines", json={"name": name, "next_round": {"run": spec.pop("run"), **spec}})


def test_a_next_round_points_at_a_decisions_file_inside_the_new_folder_and_plans(client_for, run_dir, monkeypatch):
    from smeltery import cli

    c, _ = client_for(_round1(run_dir))
    rid = run_id_of(c)
    _decide(c, rid, "4-Cl")
    r = _next(c, run=rid)
    assert r.status_code == 201, r.text
    body = r.json()
    folder = Path(body["folder"])
    carried = tomllib.loads(body["toml"])["decisions"]["file"]
    assert "/" not in carried and (folder / carried).is_file()  # inside the new pipeline folder, not the run's
    copied = json.loads((folder / carried).read_text())
    assert [(d["candidate"], d["status"]) for d in copied["decisions"]] == [("4-Cl", "reject")]
    assert "# the parent every ddE is measured against" in body["toml"]  # the comments are the documentation
    monkeypatch.setattr(cli, "available_memory_gb", lambda: 64.0)
    assert cli.main(["run", str(folder / "config.toml"), "--plan"]) == 0


def test_a_rejected_candidate_stays_in_candidates_and_the_notes_name_it(client_for, run_dir):
    c, _ = client_for(_round1(run_dir))
    rid = run_id_of(c)
    _decide(c, rid, "4-Cl")
    body = _next(c, run=rid).json()
    assert '4-Cl = "OC(=O)c1ccc(Cl)cc1"' in body["toml"]  # the engine drops it; we never rewrite the chemistry
    assert any("4-Cl" in n and "rejected" in n for n in body["notes"])
    assert any("new input digest" in n for n in body["notes"])


def test_the_carried_notes_are_written_into_the_config_so_a_reload_still_explains_the_round(client_for, run_dir):
    """`notes` on the response answers one request and is gone on the next GET. The config is the durable record."""
    c, _ = client_for(_round1(run_dir))
    rid = run_id_of(c)
    _decide(c, rid, "4-Cl")
    body = _next(c, run=rid).json()
    assert body["toml"].startswith("# Next round, carried forward by Bench:")
    for note in body["notes"]:
        assert f"#   - {note}" in body["toml"]
    # And a later GET explains the round too: the notes are read back out of the config, so the user who opens
    # this pipeline tomorrow still sees what it carried.
    again = c.get(f"/api/pipelines/{body['name']}").json()
    assert again["notes"] == body["notes"]
    assert "# Next round, carried forward by Bench:" in again["toml"]
    assert "4-Cl" in again["toml"]
    # A pipeline that is not a next round has no notes to invent.
    blank = {"campaign": "c", "parent": "p", "candidates": {}}
    plain = c.post("/api/pipelines", json={"name": "plain", "blank": blank})
    if plain.status_code == 201:
        assert c.get("/api/pipelines/plain").json()["notes"] == []


def test_drop_removes_a_candidate_for_real_and_the_parent_cannot_be_dropped(client_for, run_dir):
    c, _ = client_for(_round1(run_dir))
    rid = run_id_of(c)
    _decide(c, rid, "4-Cl")
    body = _next(c, name="dropped", run=rid, drop=["4-Br"]).json()
    # Gone from the chemistry, not merely rejected. It is still named in the header comment that says what this
    # round carried, so check [candidates] rather than the whole text.
    assert "4-Br" not in tomllib.loads(body["toml"])["candidates"]
    assert '4-F = "OC(=O)c1ccc(F)cc1"' in body["toml"]
    assert any("dropped 1 candidate: 4-Br" in n for n in body["notes"])
    bad = _next(c, name="no-parent", run=rid, drop=["benzoic"])
    assert bad.status_code == 400 and "every analogue is measured against it" in bad.json()["reason"]
    assert "no-parent" not in c.get("/api/pipelines").json()


def test_next_round_with_another_source_is_400_naming_them_all(client_for, run_dir):
    c, _ = client_for(_round1(run_dir))
    rid = run_id_of(c)
    r = c.post("/api/pipelines", json={"name": "p", "next_round": {"run": rid}, "from_run": rid})
    assert r.status_code == 400 and "exactly one of" in r.json()["reason"]
    assert "next_round" in r.json()["reason"]


def test_a_run_with_no_decisions_and_no_proposals_is_400_not_a_silent_copy(client_for, run_dir):
    c, _ = client_for(_round1(run_dir))
    r = _next(c, run=run_id_of(c))
    assert r.status_code == 400 and "no decisions file" in r.json()["reason"]
    assert c.get("/api/pipelines").json() == []


def test_a_proposal_from_another_run_is_400_naming_it(client_for, run_dir):
    c, _ = client_for(_round1(run_dir))
    rid = run_id_of(c)
    _decide(c, rid, "4-Cl")
    r = _next(c, run=rid, add_proposals=["notmine"])
    assert r.status_code == 400 and "notmine" in r.json()["reason"]
    assert c.get("/api/pipelines").json() == []


def test_an_accepted_proposal_is_added_under_candidates(client_for, run_dir):
    c, _ = client_for(_round1(run_dir))
    rid = run_id_of(c)
    made = c.post(
        f"/api/runs/{rid}/proposals",
        json={"smiles": "OC(=O)c1ccc(C(F)(F)F)cc1", "reason": "substitution", "candidate": "4-CF3"},
    )
    assert made.status_code == 201, made.text
    body = _next(c, run=rid, carry_decisions=False, add_proposals=[made.json()["id"]]).json()
    assert '4-CF3 = "OC(=O)c1ccc(C(F)(F)F)cc1"' in body["toml"]
    assert any("added 1 proposed candidate: 4-CF3" in n for n in body["notes"])


def test_a_second_next_round_from_the_same_run_gets_its_own_name(client_for, run_dir):
    c, _ = client_for(_round1(run_dir))
    rid = run_id_of(c)
    _decide(c, rid, "4-Cl")
    first = _next(c, run=rid)
    second = _next(c, run=rid)
    assert first.status_code == 201 and second.status_code == 201, second.text
    assert first.json()["name"] == "round2" and second.json()["name"] == "round2-2"
    assert sorted(c.get("/api/pipelines").json()) == ["round2", "round2-2"]
