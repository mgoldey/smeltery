"""`results.decisions_in` reaches the API, so Provenance can say what a round carried in.

The record has held the decisions file, its digest and the candidates it rejected since `[decisions]` existed
(docs/gui-contract.md section 1), and `RunDetail` did not carry any of it -- so nothing in `ui/src` could render it
and the only trace of what a round had dropped was a comment inside the config text.

Three states have to stay apart, as everywhere else in this app: a run with no `[decisions]` carried nothing in, a
run whose decisions file rejected nobody read a file and excluded no one, and a run that rejected candidates names
them. The first is `null`, not an empty carry.
"""

from __future__ import annotations

import json

import pytest

from bench.conftest import record_path


def _write_decisions_in(run_dir, **kw):
    p = record_path(run_dir)
    rec = json.loads(p.read_text())
    rec["results"]["decisions_in"] = kw
    p.write_text(json.dumps(rec, indent=2, sort_keys=True))
    return run_dir


def test_a_run_with_no_decisions_section_carried_nothing_in(client_for, run_dir):
    client, _ = client_for(run_dir)
    got = client.get("/api/runs/0")
    assert got.status_code == 200, got.text
    # null, not {"file": "", "excluded": []}: an empty carry would claim a file this run never read.
    assert got.json()["decisions_in"] is None


def test_the_carried_file_its_digest_and_the_excluded_candidates_are_served(client_for, run_dir):
    sha = "a" * 64
    out = _write_decisions_in(run_dir, file="decisions-abc123def456.json", sha256=sha, excluded=["4-Cl", "4-Br"])
    client, _ = client_for(out)
    got = client.get("/api/runs/0").json()
    assert got["decisions_in"] == {
        "file": "decisions-abc123def456.json",
        "sha256": sha,
        "excluded": ["4-Cl", "4-Br"],
    }


def test_a_decisions_file_that_rejected_nobody_is_a_carry_with_an_empty_exclusion(client_for, run_dir):
    # Read a file and excluded no one: not the same fact as having no file, and the API must not collapse them.
    out = _write_decisions_in(run_dir, file="d.json", sha256="b" * 64, excluded=[])
    client, _ = client_for(out)
    got = client.get("/api/runs/0").json()["decisions_in"]
    assert got is not None and got["excluded"] == [] and got["file"] == "d.json"


@pytest.mark.parametrize(
    "bad",
    [
        {"file": "d.json"},  # no digest
        {"sha256": "c" * 64, "excluded": []},  # no file
        {"file": 7, "sha256": "c" * 64, "excluded": []},  # not a path
        {"file": "d.json", "sha256": "c" * 64, "excluded": "4-Cl"},  # not a list
    ],
)
def test_a_record_with_the_key_but_not_the_shape_is_reported_as_nothing_carried(client_for, run_dir, bad):
    # An imported record can hold the key without the contract's shape. Inventing the missing half would state a
    # provenance the record does not hold, so it is None -- and the route still answers 200 rather than 500.
    out = _write_decisions_in(run_dir, **bad)
    client, _ = client_for(out)
    got = client.get("/api/runs/0")
    assert got.status_code == 200, got.text
    assert got.json()["decisions_in"] is None


def test_the_api_reports_exactly_what_the_record_holds(client_for, run_dir):
    # The anchor: no re-derivation. Whatever `results.decisions_in` says is what /api/runs/{id} says.
    out = _write_decisions_in(run_dir, file="x/y.json", sha256="d" * 64, excluded=["4-F"])
    client, _ = client_for(out)
    rec = json.loads(record_path(out).read_text())
    assert client.get("/api/runs/0").json()["decisions_in"] == rec["results"]["decisions_in"]
