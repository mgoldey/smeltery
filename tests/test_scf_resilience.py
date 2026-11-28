"""Per-pose SCF resilience: a bounded remedy ladder, then an explicit partial result.

Every SCF here is `tests/fake_ferric.py` (deterministic energies; "never" fails to converge, "needs:<kwarg>" converges
only once a rung passes that kwarg); no quantum chemistry runs.
"""

from __future__ import annotations

import json
import math
import sys
from types import SimpleNamespace

import fake_ferric
import numpy as np
import pytest

from smeltery import Candidate, Measurement, Pose
from smeltery.cli import ConfigError, main, validate_config
from smeltery.funnel import DEFAULT_MIN_PAIRS, cut, paired_delta
from smeltery.tiers import DEFAULT_SCF_LADDER, MAX_REMEDY_RUNGS, FieldInteraction, check_remedies

CONFIG = """
campaign = "scf-resilience"
parent = "ethanol"

[candidates]
ethanol = "CCO"
propanol = "CCCO"
butanol = "CCCCO"

[poses]
n_poses = 4
relax_unmapped = true

[[pocket.site]]
q = 1.0
atom = 2
from_atom = 1
distance = 3.0

[field_interaction]
on_scf_failure = "drop"
remedies = [{{ level_shift = 0.5 }}, {{ diis = "adiis", max_iter = 300 }}]
{extra}
[cut]
keep = 1
z = 2.0
floor = 0.0
{cut_extra}
"""


class Rules:
    def __init__(self, tmp_path, monkeypatch):
        self.dir = tmp_path / "fake"
        self.dir.mkdir()
        self.rules: dict[str, str] = {}
        monkeypatch.setenv("FAKE_FERRIC", str(self.dir / "cfg.json"))
        monkeypatch.setitem(sys.modules, "ferric", fake_ferric.make())
        self.save()

    def set(self, label: str, leg: str, rule: str) -> None:
        self.rules[f"{label}|{leg}"] = rule
        self.save()

    def save(self) -> None:
        (self.dir / "cfg.json").write_text(json.dumps({"rules": self.rules, "dir": str(self.dir)}))

    def calls(self, label: str, leg: str) -> list[dict]:
        p = self.dir / "calls.jsonl"
        rows = [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []
        return [r for r in rows if r["label"] == label and r["leg"] == leg]


@pytest.fixture
def engine(tmp_path, monkeypatch):
    return Rules(tmp_path, monkeypatch)


def _run(tmp_path, engine_unused=None, extra="", cut_extra=""):
    cfg = tmp_path / "c.toml"
    cfg.write_text(CONFIG.format(extra=extra, cut_extra=cut_extra))
    out = tmp_path / "out"
    rc = main(["run", str(cfg), "--out", str(out)])
    recs = list(out.glob("run-*.json"))
    return rc, (json.loads(recs[0].read_text()) if recs else None), out


# ---------------------------------------------------------------------------------------------- the ladder


def test_the_default_ladder_uses_only_knobs_the_config_accepts_and_stays_bounded():
    assert check_remedies(list(DEFAULT_SCF_LADDER)) == [dict(r) for r in DEFAULT_SCF_LADDER]
    assert len(DEFAULT_SCF_LADDER) <= MAX_REMEDY_RUNGS
    for rung in DEFAULT_SCF_LADDER:  # no rung touches a threshold or the occupations
        assert not {"energy_conv", "density_conv", "smearing"} & set(rung)
    with pytest.raises(ValueError, match="at most 5"):
        check_remedies([{"max_iter": 100 + i} for i in range(MAX_REMEDY_RUNGS + 1)])
    with pytest.raises(ValueError, match="guess = 'core' is not one of"):
        check_remedies([{"guess": "core"}])
    with pytest.raises(ValueError, match="unknown override 'damping'"):  # ferric's run_rhf has no damping knob
        check_remedies([{"damping": 0.5}])
    assert check_remedies([{"guess": "hcore"}]) == [{"guess": "hcore"}]


def test_on_scf_failure_is_validated_and_absent_from_settings_unless_set():
    with pytest.raises(ValueError, match="on_scf_failure"):
        FieldInteraction(on_scf_failure="retry")
    assert "on_scf_failure" not in FieldInteraction().settings()  # ordinary configs keep their digests
    assert FieldInteraction(on_scf_failure="drop").settings()["on_scf_failure"] == "drop"


def _cands():
    def pose(i):
        return Pose(("H", "H"), np.array([[0.0, 0, 0], [0.7 + 0.1 * i, 0, 0]]))

    return [Candidate(n, "[H][H]", [pose(i) for i in range(3)]) for n in ("A", "B")]


def _field():
    return [SimpleNamespace(q=1.0, as_ferric_bohr=lambda: (1.0, 5.0, 0.0, 0.0))]


def _tier(**kw):
    return FieldInteraction(remedies=[{"level_shift": 0.5}, {"diis": "adiis"}], **kw)


def test_a_leg_climbs_the_ladder_once_and_a_converging_rung_rescues_it(engine):
    engine.set("A pose 1", "field", "needs:level_shift")
    cands = _cands()
    tier = _tier(on_scf_failure="drop")
    tier.run(cands, {"field": _field()})
    assert tier.failures == []
    assert [len(engine.calls("A pose 1", "field"))] == [2]  # rung 0 failed, rung 1 converged: no third SCF
    assert cands[0].per_pose["scf_rung"] == [0.0, 1.0, 0.0]


def test_a_pose_that_fails_every_rung_is_recorded_not_retried_and_the_rest_goes_on(engine):
    engine.set("A pose 1", "field", "never")
    cands = _cands()
    tier = _tier(on_scf_failure="drop")
    tier.run(cands, {"field": _field()})
    assert len(engine.calls("A pose 1", "field")) == 3  # rung 0 and the two remedies, never a fourth
    (f,) = tier.failures
    assert (f["candidate"], f["pose"], f["leg"], f["rungs_tried"]) == ("A", 1, "field", [0, 1, 2])
    assert "did not converge on any of 3 rung(s)" in f["reason"]
    a = cands[0].per_pose["dE_int"]
    assert math.isnan(a[1]) and not math.isnan(a[0]) and not math.isnan(a[2])
    assert all(not math.isnan(v) for v in cands[1].per_pose["dE_int"])  # B was measured in full


def test_a_failed_vacuum_leg_skips_the_field_leg(engine):
    engine.set("B pose 2", "vacuum", "never")
    tier = _tier(on_scf_failure="drop")
    tier.run(_cands(), {"field": _field()})
    assert engine.calls("B pose 2", "field") == []  # its value would not be reported: no hours spent on it
    assert [(f["candidate"], f["pose"], f["leg"]) for f in tier.failures] == [("B", 2, "vacuum")]


def test_the_default_still_stops_the_tier_on_a_failed_pose(engine):
    engine.set("A pose 1", "field", "never")
    with pytest.raises(RuntimeError, match="A pose 1: SCF did not converge"):
        _tier().run(_cands(), {"field": _field()})


# -------------------------------------------------------------------------------------------- the pairing


def test_paired_delta_refuses_a_failed_pose_instead_of_differencing_it():
    p = Candidate("P", "C", per_pose={"q": [1.0, 2.0, 3.0]})
    a = Candidate("A", "C", per_pose={"q": [1.5, float("nan"), 3.5]})
    with pytest.raises(ValueError, match=r"not finite at pose\(s\) \[1\]"):
        paired_delta(p, a, "q")


def test_cut_does_not_rank_a_candidate_with_too_few_pairs_and_claims_no_survivors():
    m = {
        "A": Measurement.from_samples([0.0, 0.1, 0.2, 0.1]),
        "B": Measurement.from_samples([5.0, 5.1, 4.9, 5.0]),
        "C": Measurement.from_samples([-9.0, -9.1]),  # two pairs: a one-dof SEM
    }
    ranked = cut(m, keep=1)
    assert ranked.survivors == ["C"] and not ranked.unrankable  # unchanged without min_pairs
    res = cut(m, keep=1, min_pairs=3)
    assert res.unranked_at_boundary and res.survivors is None
    assert list(res.unrankable) == ["C"] and "2 paired pose(s)" in res.unrankable["C"]
    assert [g for g in res.groups] == [["A"], ["B"]]  # the rest is still ranked, and C is named, not dropped
    assert any("C" in n for n in res.notes)


# ---------------------------------------------------------------------------------- a whole plain run


def test_a_plain_run_pairs_over_the_poses_that_converged_for_both_legs(engine, tmp_path, capsys):
    engine.set("propanol pose 1", "field", "never")  # an analogue fails at pose 1
    engine.set("ethanol pose 3", "vacuum", "never")  # the parent fails at pose 3: dropped for every analogue
    rc, rec, out = _run(tmp_path)
    assert rc == 0
    res = rec["results"]
    assert res["pose_index"] == {"ethanol": [0, 1, 2], "propanol": [0, 2], "butanol": [0, 1, 2]}
    assert res["paired_ddE"]["propanol"]["n"] == 2 and res["paired_ddE"]["butanol"]["n"] == 3
    assert res["pairing"]["requested_poses"] == {"ethanol": 4, "propanol": 4, "butanol": 4}
    assert res["pairing"]["n_pairs"] == {"propanol": 2, "butanol": 3}
    assert res["pairing"]["min_pairs"] == DEFAULT_MIN_PAIRS
    why = {(d["candidate"], d["pose"], d["leg"]): d for d in res["dropped_poses"]}
    assert set(why) == {("propanol", 1, "field"), ("ethanol", 3, "vacuum")}
    assert why[("ethanol", 3, "vacuum")]["dropped_with"] == ["ethanol", "propanol", "butanol"]
    assert "did not converge" in why[("propanol", 1, "field")]["reason"]
    fp = {(x["analogue"], x["pose"]) for x in res["pairing"]["failed_pairs"]}
    assert fp == {("propanol", 1), ("propanol", 3), ("butanol", 3)}  # never silent, per pair
    # propanol has 2 pairs, fewer than the default 3: not ranked, and the cut says so
    assert res["cut"]["unranked"] is True and res["cut"]["survivors"] is None
    assert list(res["cut_unrankable"]) == ["propanol"]
    shown = capsys.readouterr().out
    assert "dropped: propanol pose 1 (field)" in shown and "UNRANKED" in shown
    assert not (out / "calcs.partial.jsonl").exists()


def test_a_declared_min_pairs_of_two_ranks_the_two_pair_analogue_with_its_n_reported(engine, tmp_path):
    engine.set("propanol pose 1", "field", "never")
    engine.set("ethanol pose 3", "vacuum", "never")
    rc, rec, _ = _run(tmp_path, cut_extra="min_pairs = 2")
    assert rc == 0
    res = rec["results"]
    assert res["paired_ddE"]["propanol"]["n"] == 2 and "cut_unrankable" not in res
    assert res["pairing"]["min_pairs"] == 2
    assert res["cut"]["groups"] and any("propanol" in g for g in res["cut"]["groups"])  # ranked, with its n = 2 shown


def test_a_run_without_failures_records_no_partial_machinery(engine, tmp_path):
    rc, rec, _ = _run(tmp_path)
    assert rc == 0
    for k in ("pairing", "dropped_poses", "pose_index", "cut_unrankable"):
        assert k not in rec["results"]
    assert rec["results"]["paired_ddE"]["propanol"]["n"] == 4


def test_every_pose_of_a_candidate_failing_is_a_stage_error_not_an_empty_result(engine, tmp_path, capsys):
    for i in range(4):
        engine.set(f"butanol pose {i}", "vacuum", "never")
    rc, rec, _ = _run(tmp_path)
    assert rc == 3 and rec is None
    assert "every pose of butanol was dropped" in capsys.readouterr().err


def test_a_ladder_rescued_pose_is_kept_and_its_rung_recorded(engine, tmp_path):
    engine.set("propanol pose 2", "field", "needs:level_shift")
    rc, rec, _ = _run(tmp_path)
    assert rc == 0
    assert "dropped_poses" not in rec["results"]
    assert rec["results"]["paired_ddE"]["propanol"]["n"] == 4
    assert rec["results"]["per_pose"]["propanol"]["scf_rung"] == [0.0, 0.0, 1.0, 0.0]


def test_config_validation_of_min_pairs_and_the_failure_policy():
    base = {
        "campaign": "x",
        "parent": "p",
        "candidates": {"p": "CCO", "a": "CCCO"},
        "pocket": {"site": [{"q": 1.0, "atom": 0, "from_atom": 1, "distance": 3.0}]},
        "cut": {"keep": 1, "floor": 0.0, "min_pairs": 1},
    }
    with pytest.raises(ConfigError, match="min_pairs"):
        validate_config(base)
    base["cut"]["min_pairs"] = 3
    validate_config(base)
    base["field_interaction"] = {"on_scf_failure": "drop", "remedies": list(DEFAULT_SCF_LADDER)}
    validate_config(base)
