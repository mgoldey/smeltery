"""Lineage of a paired ΔΔE: the endpoint joins the record's own fields and computes no number."""

from __future__ import annotations

import json

import pytest

from bench.conftest import record_path


def test_lineage_numbers_are_the_records_own(run_dir, client_for):
    client, _ = client_for(run_dir)
    rec = json.loads(record_path(run_dir).read_text())
    res = rec["results"]
    body = client.get("/api/runs/0/lineage", params={"candidate": "4-Br"})
    assert body.status_code == 200, body.text
    j = body.json()
    assert (j["candidate"], j["quantity"], j["parent"]) == ("4-Br", "dE_int", "benzoic")
    assert j["mean"] == res["paired_ddE"]["4-Br"]["mean"]
    assert j["sem"] == res["paired_ddE"]["4-Br"]["sem"] and j["n"] == 4
    vals, pvals = res["per_pose"]["4-Br"]["dE_int"], res["per_pose"]["benzoic"]["dE_int"]
    assert [p["value"] for p in j["poses"]] == vals
    assert [p["parent_value"] for p in j["poses"]] == pvals
    for p in j["poses"]:
        assert p["difference"] == p["value"] - p["parent_value"]
    assert j["field"]["n_charges"] == len(res["field"])
    assert j["digest"] == rec["input_digest"] and j["started_utc"] == rec["started_utc"]
    assert j["engine"] == rec["ferric"] and j["calcs"] == res["calcs"]
    steps = {s["stage"]: s for s in j["steps"]}
    assert steps["poses"]["seconds"] == 0.5 and steps["poses"]["kind"] == "pair"
    assert steps["field_interaction"]["kind"] == "measure" and steps["field_interaction"]["seconds"] == 1.5
    assert steps["field_interaction"]["settings"] == {"basis": "sto-3g", "name": "field_interaction"}
    assert [s["stage"] for s in j["steps"]] == ["poses", "pocket", "field_interaction"]


def test_a_run_from_before_provenance_lists_what_it_did_not_record(run_dir, client_for):
    p = record_path(run_dir)
    rec = json.loads(p.read_text())
    rec.pop("smeltery", None)
    rec["results"].pop("config", None)
    rec["results"].pop("calcs", None)
    p.write_text(json.dumps(rec))
    client, _ = client_for(run_dir)
    r = client.get("/api/runs/0/lineage", params={"candidate": "4-Br"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert {"smeltery", "config", "calcs"} <= set(j["missing"])
    assert j["smeltery"] is None and j["config"] is None and j["calcs"] is None


@pytest.mark.parametrize("candidate,status", [("benzoic", 409), ("nope", 404)])
def test_the_parent_and_unknown_candidates_are_refused(run_dir, client_for, candidate, status):
    client, _ = client_for(run_dir)
    assert client.get("/api/runs/0/lineage", params={"candidate": candidate}).status_code == status


def test_an_incomplete_run_is_refused(incomplete_run_dir, client_for):
    client, _ = client_for(incomplete_run_dir)
    assert client.get("/api/runs/0/lineage", params={"candidate": "4-Br"}).status_code == 409
