"""One line per SCF in OUT/calcs.jsonl, and the log of a run that failed on an SCF."""

import json

import pytest

from smeltery.cli import main

TINY = """
campaign = "tiny"
parent = "ethanol"

[candidates]
ethanol = "CCO"
propanol = "CCCO"

[poses]
n_poses = 2
relax_unmapped = true

[[pocket.site]]
q = 1.0
atom = 2
from_atom = 1
distance = 3.0

[cut]
keep = 1
z = 2.0
floor = 0.0
"""


def _lines(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


@pytest.mark.needs_ferric
def test_every_scf_is_logged_with_its_leg_iterations_and_time(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(TINY)
    out = tmp_path / "out"
    assert main(["run", str(cfg), "--out", str(out)]) == 0
    rows = _lines(out / "calcs.jsonl")
    assert len(rows) == 2 * 2 * 2  # candidates x poses x (vacuum, field)
    assert {r["leg"] for r in rows} == {"vacuum", "field"}
    for r in rows:
        assert set(r) == {"candidate", "pose", "leg", "converged", "iterations", "energy_hartree", "seconds"}
        assert r["converged"] is True and r["iterations"] > 0 and r["seconds"] >= 0
    rec = json.loads(next(out.glob("run-*.json")).read_text())
    assert rec["results"]["calcs"] == {"file": "calcs.jsonl", "n": len(rows)}


@pytest.mark.needs_ferric
def test_a_failed_scf_leaves_its_log(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(TINY + "\n[field_interaction]\nmax_iter = 1\n")
    out = tmp_path / "out"
    assert main(["run", str(cfg), "--out", str(out)]) == 3
    rows = _lines(out / "calcs.jsonl")
    assert {r["leg"] for r in rows} == {"vacuum", "field"}  # both legs are logged before the tier raises
    assert any(r["converged"] is False for r in rows)
