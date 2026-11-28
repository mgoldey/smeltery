"""`GET /runs/{id}/attrition`: where candidates were lost, with the generator's rejections when the run has them."""

from __future__ import annotations

import json

from bench.conftest import record_path, run_id_of


def _add_generation(run_dir):
    p = record_path(run_dir)
    rec = json.loads(p.read_text())
    rec["results"]["funnel"].insert(
        0,
        {
            "stage": "generate",
            "kind": "generate",
            "candidates_in": 10,
            "candidates_out": 4,
            "generator": "fake",
            "rejected_by_reason": {"duplicate": 5, "size": 1},
            "rejected_total": 6,
            "rejected_listed": 1,
            "rejected": [{"index": 2, "reason": "size", "detail": "", "smiles": "C"}],
        },
    )
    p.write_text(json.dumps(rec, indent=2, sort_keys=True))
    return run_dir


def test_a_run_with_no_generation_step_reports_the_funnel_and_no_generation(client_for, run_dir):
    client, _ = client_for(run_dir)
    got = client.get(f"/api/runs/{run_id_of(client)}/attrition")
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["generation"] is None
    assert [s["stage"] for s in body["stages"]][-1] == "cut"


def test_a_generation_step_gives_raw_returned_and_counts_per_reason(client_for, run_dir):
    client, _ = client_for(_add_generation(run_dir))
    body = client.get(f"/api/runs/{run_id_of(client)}/attrition").json()
    assert body["stages"][0] == {
        "stage": "generate",
        "kind": "generate",
        "candidates_in": 10,
        "candidates_out": 4,
        "unranked": False,
    }
    g = body["generation"]
    assert (g["n_raw"], g["n_returned"], g["rejected_total"], g["rejected_listed"]) == (10, 4, 6, 1)
    assert {"reason": "duplicate", "count": 5} in g["by_reason"]
    assert g["rejected"][0]["smiles"] == "C"
