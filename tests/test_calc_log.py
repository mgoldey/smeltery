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


def _synthetic_partial(out, name, rows):
    """What `--only-candidate NAME` leaves: partial-NAME.json naming a calcs-NAME.jsonl (ferric-free)."""
    from smeltery.cli import calc_file_name

    calcs = None
    if rows is not None:
        f = calc_file_name(name)
        (out / f).write_text("".join(json.dumps(r) + "\n" for r in rows))
        calcs = {"file": f, "n": len(rows)}
    pp = {"dE_int": [0.0, 0.1] if name == "ethanol" else [1.0, 1.1]}
    (out / f"partial-{name}.json").write_text(json.dumps({"results": {"per_pose": {name: pp}, "calcs": calcs}}))


def _row(cand, pose, leg):
    return {
        "candidate": cand,
        "pose": pose,
        "leg": leg,
        "converged": True,
        "iterations": 5,
        "energy_hartree": -1.0,
        "seconds": 0.1,
    }


def test_partial_names_are_per_candidate_and_sanitised():
    from smeltery.cli import calc_file_name

    assert calc_file_name("propanol") == "calcs-propanol.jsonl"
    assert calc_file_name("a b/c") == "calcs-a_b_c.jsonl"


def test_combine_merges_the_partial_logs_instead_of_overwriting(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(TINY)
    out = tmp_path / "out"
    out.mkdir()
    _synthetic_partial(out, "ethanol", [_row("ethanol", 0, "vacuum"), _row("ethanol", 0, "field")])
    _synthetic_partial(out, "propanol", [_row("propanol", 0, "vacuum")])
    assert main(["run", str(cfg), "--out", str(out), "--combine"]) == 0
    assert len(_lines(out / "calcs.jsonl")) == 3
    rec = json.loads(next(out.glob("run-*.json")).read_text())
    assert rec["results"]["calcs"] == {"file": "calcs.jsonl", "n": 3}
    assert (out / "calcs-ethanol.jsonl").is_file() and (out / "calcs-propanol.jsonl").is_file()


def test_combine_without_partial_logs_never_writes_an_empty_log_or_a_false_zero(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(TINY)
    out = tmp_path / "out"
    out.mkdir()
    _synthetic_partial(out, "ethanol", None)
    _synthetic_partial(out, "propanol", None)
    (out / "calcs.jsonl").write_text('{"keep": "me"}\n')
    assert main(["run", str(cfg), "--out", str(out), "--combine"]) == 0
    assert (out / "calcs.jsonl").read_text() == '{"keep": "me"}\n'
    rec = json.loads(next(out.glob("run-*.json")).read_text())
    assert rec["results"]["calcs"] is None


@pytest.mark.needs_ferric
def test_partial_runs_into_one_out_keep_separate_logs(tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text(TINY)
    out = tmp_path / "out"
    for name in ("ethanol", "propanol"):
        assert main(["run", str(cfg), "--out", str(out), "--only-candidate", name]) == 0
    assert len(_lines(out / "calcs-ethanol.jsonl")) == 4 and len(_lines(out / "calcs-propanol.jsonl")) == 4
    assert not (out / "calcs.jsonl").exists()
    assert main(["run", str(cfg), "--out", str(out), "--combine"]) == 0
    assert len(_lines(out / "calcs.jsonl")) == 8
