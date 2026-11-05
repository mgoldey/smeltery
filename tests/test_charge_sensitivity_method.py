"""`charge_sensitivity` (smeltery.methods.charge_sensitivity): the measurement repeated with the pocket charged by a
second pdb2pqr force field, and the disagreement reported by `smeltery.charge_sensitivity`.

Data source: the pocket fixture tests/data/pocket_fixture/7LCJ_pocket.pdb charged by `.venv/bin/pdb2pqr30` (3.8.0)
with --ff=AMBER (the primary field) and --ff=CHARMM. The dE_int values are not SCFs: an injected measure computes them
from the field it is handed (the electrostatic potential of the field at a fixed point, times a per-ligand factor), so
a different field gives different numbers and a reused field gives the same ones.
"""

from __future__ import annotations

import dataclasses
import math
import os
import shutil
import tomllib
from pathlib import Path

import numpy as np
import pytest

from smeltery import Candidate, charge_sensitivity
from smeltery.cli import ConfigError, build_plan, load_config, run_plan, validate_config
from smeltery.methods import charge_sensitivity as cs_mod
from smeltery.methods import registry
from smeltery.pocket import load_pocket
from smeltery.record import RunRecord

ROOT = Path(__file__).resolve().parent.parent
POCKET = ROOT / "tests" / "data" / "pocket_fixture" / "7LCJ_pocket.pdb"
PDB2PQR = ROOT / ".venv" / "bin" / "pdb2pqr30"

needs_pdb2pqr = pytest.mark.skipif(
    not PDB2PQR.exists(), reason=f"needs {PDB2PQR} (pdb2pqr 3.8.0, the version the fixture was charged with)"
)

CONFIG = """
campaign = "t"
parent = "benzoic"

[candidates]
benzoic = "OC(=O)c1ccccc1"
4-F = "OC(=O)c1ccc(F)cc1"
4-Cl = "OC(=O)c1ccc(Cl)cc1"
4-Br = "OC(=O)c1ccc(Br)cc1"
4-Me = "Cc1ccc(C(=O)O)cc1"

[poses]
n_poses = 3

[pocket]
file = "pocket.pdb"

[cut]
keep = 1
z = 2.0
floor = 0.0

[charge_sensitivity]
models = ["CHARMM"]
"""

FACTOR = {"benzoic": 1.0, "4-F": 1.3, "4-Cl": 0.6, "4-Br": 1.1, "4-Me": 0.9}
OFFSET = {"benzoic": 0.0, "4-F": -0.4, "4-Cl": 0.3, "4-Br": -0.1, "4-Me": 0.2}
PROBE = np.array([10.0, 10.0, 10.0])  # a fixed point, Angstrom


def potential(field) -> float:
    q = np.array([c.q for c in field])
    x = np.array([c.xyz_ang for c in field])
    return float((q / np.linalg.norm(x - PROBE, axis=1)).sum())


def field_measure(plan):
    """Stand-in for the SCFs: dE_int depends on the field the plan carries, the ligand and the pose."""
    phi = potential(plan.field)
    for c in plan.candidates:
        c.per_pose["dE_int"] = [FACTOR[c.name] * phi * (1 + 0.1 * i) + OFFSET[c.name] * i for i in range(len(c.poses))]


@pytest.fixture
def pdb2pqr_on_path(monkeypatch):
    if not PDB2PQR.exists():
        pytest.skip(f"needs {PDB2PQR} (pdb2pqr 3.8.0)")
    monkeypatch.setenv("PATH", f"{PDB2PQR.parent}{os.pathsep}{os.environ['PATH']}")


@pytest.fixture
def plan(tmp_path, pdb2pqr_on_path):
    shutil.copy(POCKET, tmp_path / "pocket.pdb")
    (tmp_path / "c.toml").write_text(CONFIG)
    return build_plan(load_config(tmp_path / "c.toml"))


@pytest.fixture
def hook(monkeypatch):
    h = registry()["charge_sensitivity"].hook
    assert isinstance(h, cs_mod.ChargeSensitivity)
    monkeypatch.setattr(h, "measure", field_measure)
    return h


def _primary_ddE(rec, name, parent="benzoic"):
    pp = rec.results["per_pose"]
    return float(np.mean(np.array(pp[name]["dE_int"]) - np.array(pp[parent]["dE_int"])))


@needs_pdb2pqr
def test_the_analysis_equals_charge_sensitivity_called_by_hand(plan, hook):
    rec, _ = run_plan(plan, measure=field_measure)
    got = rec.results["charge_sensitivity"]
    assert set(got) == {"CHARMM"}
    entry = got["CHARMM"]

    # by hand: the CHARMM pocket from pdb2pqr, the same measure, the same pairing
    charmm = load_pocket(POCKET, ff="CHARMM")
    cands = [Candidate(c.name, c.smiles, list(c.poses)) for c in plan.candidates]
    field_measure(dataclasses.replace(plan, candidates=cands, parent=cands[0], field=charmm))
    by_name = {c.name: c for c in cands}
    analogues = [n for n in FACTOR if n != "benzoic"]
    model_b = {
        n: float(np.mean(np.array(by_name[n].per_pose["dE_int"]) - np.array(by_name["benzoic"].per_pose["dE_int"])))
        for n in analogues
    }
    model_a = {n: _primary_ddE(rec, n) for n in analogues}
    rep = charge_sensitivity(
        [Candidate(n, "") for n in analogues], "dE_int", lambda c, q: model_a[c.name], lambda c, q: model_b[c.name]
    )
    assert entry["sign_flips"] == rep.n_sign_flips
    assert not math.isnan(rep.spearman) and entry["spearman"] == pytest.approx(rep.spearman)
    assert entry["floor"] == pytest.approx(rep.floor)
    assert entry["n"] == rep.n == 4
    for n in analogues:
        row = entry["paired_ddE"][n]
        assert row["primary"] == pytest.approx(model_a[n]) and row["model"] == pytest.approx(model_b[n])
        assert row["n"] == 3
    assert entry["n_dropped"] == 0 and entry["primary_model"] == "AMBER"
    assert entry["field"]["pdb2pqr_ff"] == "CHARMM"

    # non-vacuity: the extra field really is another field, not the primary one reused (a combine's replayed
    # measure, or plan.field passed again, would make every value below equal)
    primary_pp = rec.results["per_pose"]
    assert all(entry["per_pose"][n] != primary_pp[n]["dE_int"] for n in FACTOR)
    assert any(abs(r["delta"]) > 1e-6 for r in entry["paired_ddE"].values())


@needs_pdb2pqr
def test_the_charmm_field_differs_from_the_amber_one_on_the_pocket_fixture(plan):
    hook = cs_mod.ChargeSensitivity()
    fields = hook.extra_fields(plan, {})
    charmm, amber = fields["CHARMM"], plan.field
    assert amber.provenance["pdb2pqr_ff"] == "AMBER" and charmm.provenance["pdb2pqr_ff"] == "CHARMM"
    # built the same way: same file, cutoff, centre, waters; only the force field differs
    same = ("input_sha256", "cutoff_ang", "center_ang", "drop_waters", "pdb2pqr_version")
    assert {k: charmm.provenance[k] for k in same} == {k: amber.provenance[k] for k in same}
    q_a = np.array([c.q for c in amber])
    q_c = np.array([c.q for c in charmm])
    assert len(amber) > 1000 and len(charmm) > 1000
    assert len(q_a) != len(q_c) or not np.allclose(q_a, q_c)
    assert potential(charmm) != pytest.approx(potential(amber), abs=1e-6)


def _summary_rec(_plan, per_pose):
    return RunRecord("t", {}, [], {"per_pose": per_pose, "candidate_order": list(per_pose)})


class _Plan:
    """Only what `summarize` reads from a plan."""

    def __init__(self, names):
        self.candidates = [Candidate(n, "") for n in names]
        self.parent = self.candidates[0]
        self.field = []


def test_a_pose_missing_on_either_side_is_dropped_from_both():
    names = ["p", "a", "b", "c"]
    primary = {
        "p": {"dE_int": [0.0, 0.0, 0.0]},
        "a": {"dE_int": [1.0, 2.0, 3.0]},
        "b": {"dE_int": [-1.0, -1.0, None]},  # failed under the primary model
        "c": {"dE_int": [5.0, 5.0, 5.0]},
    }
    measured = {
        "CHARMM": {
            "p": [0.5, 0.5, 0.5],
            "a": [1.5, None, 3.5],  # failed under CHARMM
            "b": [-2.0, -2.0, -2.0],
            "c": [4.0, 4.0, 4.0],
        }
    }
    p = _Plan(names)
    out = cs_mod.ChargeSensitivity().summarize(p, _summary_rec(p, primary), measured)["CHARMM"]
    assert out["dropped_poses"] == {"a": [1], "b": [2]} and out["n_dropped"] == 2
    assert out["parent_dropped_poses"] == []
    # a: poses 0 and 2 on both sides; b: poses 0 and 1
    assert out["paired_ddE"]["a"] == {"primary": 2.0, "model": 2.0, "delta": 0.0, "n": 2}
    assert out["paired_ddE"]["b"] == {"primary": -1.0, "model": -2.5, "delta": -1.5, "n": 2}
    assert out["paired_ddE"]["c"]["n"] == 3
    rep = charge_sensitivity(
        [Candidate(n, "") for n in "abc"],
        "dE_int",
        lambda c, q: out["paired_ddE"][c.name]["primary"],
        lambda c, q: out["paired_ddE"][c.name]["model"],
    )
    assert out["sign_flips"] == rep.n_sign_flips and out["spearman"] == pytest.approx(rep.spearman)

    # a parent pose missing under one model drops that pose for every analogue
    measured["CHARMM"]["p"] = [0.5, 0.5, None]
    out = cs_mod.ChargeSensitivity().summarize(p, _summary_rec(p, primary), measured)["CHARMM"]
    # reported once, as the parent's, not once per analogue
    assert out["parent_dropped_poses"] == [2] and out["dropped_poses"] == {"a": [1], "b": [2]}
    assert out["n_dropped"] == 3  # (p, 2), (a, 1), (b, 2)
    assert out["paired_ddE"]["c"]["n"] == 2 and out["paired_ddE"]["a"]["n"] == 1


def test_fewer_than_two_comparable_analogues_is_recorded_not_raised():
    p = _Plan(["p", "a", "b"])
    primary = {"p": {"dE_int": [0.0]}, "a": {"dE_int": [1.0]}, "b": {"dE_int": [2.0]}}
    out = cs_mod.ChargeSensitivity().summarize(
        p, _summary_rec(p, primary), {"CHARMM": {"p": [0.0], "a": [1.0], "b": [None]}}
    )
    entry = out["CHARMM"]
    assert entry["sign_flips"] is None and entry["spearman"] is None and entry["excluded"] == ["b"]
    assert "at least 2" in entry["note"]


@dataclasses.dataclass
class FieldTier:
    """A tier that never runs an SCF: dE_int from the field it is given; one pose of 4-Cl does not converge."""

    name: str = "field_interaction"
    calls: list = dataclasses.field(default_factory=list)  # shared with a copy of the tier

    def settings(self):
        return {}

    def produces(self):
        return {"dE_int": "kcal/mol"}

    def run(self, candidates, ctx):
        phi = potential(ctx["field"])
        self.calc_log = []
        for c in candidates:
            self.calls.append(c.name)
            self.calc_log.append({"candidate": c.name, "pose": 0, "leg": "field", "rung": 0, "converged": True})
            if c.name == "4-Cl" and len(self.calls) % 2 == 0:
                raise RuntimeError(f"{c.name} pose 0: SCF did not converge")
            c.per_pose["dE_int"] = [FACTOR[c.name] * phi for _ in c.poses]


@needs_pdb2pqr
def test_the_serial_path_measures_through_the_tier_never_through_the_runs_measure(plan, monkeypatch):
    """A combine's measure replays the primary values; re-measuring through it would report zero sensitivity."""
    h = registry()["charge_sensitivity"].hook
    monkeypatch.setattr(h, "measure", None)
    tier = FieldTier()
    plan.tier = tier

    replay_calls = []

    def replay(p):  # what --combine passes: the stored primary values, whatever field p carries
        replay_calls.append(p.field)
        field_measure(dataclasses.replace(p, field=plan.field))

    rec, _ = run_plan(plan, measure=replay)
    assert len(replay_calls) == 1  # the primary measurement only
    entry = rec.results["charge_sensitivity"]["CHARMM"]
    assert len(tier.calls) == sum(len(c.poses) for c in plan.candidates)  # one call per pose
    assert entry["per_pose"]["4-Cl"].count(None) >= 1 and entry["failures"]["4-Cl"]
    assert all(v is not None for v in entry["per_pose"]["4-F"])
    assert entry["per_pose"]["4-F"] != rec.results["per_pose"]["4-F"]["dE_int"]
    # every extra-model SCF row says which pocket it ran in, keeps the tier's rung, and its real pose index
    assert len(entry["calcs"]) == len(tier.calls)
    assert all(r["field_model"] == "CHARMM" and r["rung"] == 0 for r in entry["calcs"])
    assert sorted({r["pose"] for r in entry["calcs"]}) == [0, 1, 2]


def test_extra_models_reads_the_section_without_pdb2pqr():
    h = cs_mod.ChargeSensitivity()
    assert h.extra_models({"charge_sensitivity": {"models": ["CHARMM", "PARSE"]}}) == ["CHARMM", "PARSE"]
    assert h.extra_models({"charge_sensitivity": {}}) == ["CHARMM"]
    assert h.extra_models({}) == []


@needs_pdb2pqr
def test_a_field_that_cannot_be_built_is_recorded_and_the_run_keeps_its_record(plan, hook, monkeypatch):
    """pdb2pqr failing for one model (or the pocket file changing) after every primary SCF has run must not cost the
    record: run_plan returns, and the model's entry carries the error."""
    import subprocess

    import smeltery.pocket as pocket_mod

    plan.cfg["charge_sensitivity"] = {"models": ["CHARMM", "PARSE"]}
    real = pocket_mod.load_pocket

    def flaky(path, **kw):
        if kw.get("ff") == "CHARMM":
            raise subprocess.CalledProcessError(1, ["pdb2pqr30", "--ff=CHARMM"])
        return real(path, **kw)

    monkeypatch.setattr(pocket_mod, "load_pocket", flaky)
    rec, _ = run_plan(plan, measure=field_measure)
    got = rec.results["charge_sensitivity"]
    assert got["CHARMM"]["stage"] == "field" and "CalledProcessError" in got["CHARMM"]["error"]
    assert got["PARSE"]["field"]["pdb2pqr_ff"] == "PARSE" and "error" not in got["PARSE"]
    assert rec.results["paired_ddE"]  # the primary result is intact

    plan.receptor_pdb.write_text(plan.receptor_pdb.read_text() + "REMARK changed\n")
    rec, _ = run_plan(plan, measure=field_measure)
    for m in ("CHARMM", "PARSE"):
        assert "no longer has the digest" in rec.results["charge_sensitivity"][m]["error"]


def test_validation_refuses_what_it_cannot_measure():
    base = tomllib.loads(CONFIG)
    validate_config(base)
    with pytest.raises(ConfigError, match="not pdb2pqr force fields"):
        validate_config({**base, "charge_sensitivity": {"models": ["charmm"]}})
    with pytest.raises(ConfigError, match="compared with itself"):
        validate_config({**base, "charge_sensitivity": {"models": ["AMBER"]}})
    with pytest.raises(ConfigError, match=r"unknown key"):
        validate_config({**base, "charge_sensitivity": {"model": ["CHARMM"]}})
    sites = {k: v for k, v in base.items() if k != "pocket"}
    sites["pocket"] = {"site": [{"q": 1.0, "atom": 0, "from_atom": 1, "distance": 3.0}]}
    with pytest.raises(ConfigError, match="needs \\[pocket\\] file = a .pdb"):
        validate_config(sites)
    validate_config({k: v for k, v in sites.items() if k != "charge_sensitivity"})


def test_a_config_without_the_section_keeps_its_digest():
    from test_methods_registry import DIGESTS_AT_7389ABA

    path = "examples/mwe_benzoic.toml"
    plan = build_plan(load_config(ROOT / path))
    assert RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest == DIGESTS_AT_7389ABA[path]
    assert "charge_sensitivity" not in plan.inputs


@needs_pdb2pqr
def test_the_section_is_an_input(plan):
    assert plan.inputs["charge_sensitivity"] == {"models": ["CHARMM"]}
