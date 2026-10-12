"""Implicit solvation (IEF-PCM) in the PLB runner: the two legs, the cap guard, the no-retry rule, and the loader.

The production design is docs/benchmarks/plb_pcm_plan.md. Nothing here uses a PLB ligand: the SCFs are on a 6-atom
methanol so the whole file runs in about a minute.

Mutation checks (made by hand, each made at least one test below fail, then reverted) are listed in the PR body and
in docs/benchmarks/plb_pcm.md.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pytest
from test_plb_measure import _fake_target, _record

from smeltery import plb_measure as pm
from smeltery.model import HARTREE_TO_KCAL
from smeltery.record import digest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

SYMBOLS = ("C", "O", "H", "H", "H", "H")
COORDS = np.array(
    [
        [0.0, 0.0, 0.0],
        [1.41, 0.0, 0.0],
        [-0.36, 1.02, 0.0],
        [-0.36, -0.51, 0.89],
        [-0.36, -0.51, -0.89],
        [1.74, 0.9, 0.0],
    ]
)
FIELD = [(0.8, 7.5, 0.9, 0.4), (-0.8, -6.6, 1.9, -1.9), (0.3, 0.9, 7.6, 5.7)]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------------------------- the two legs (ferric)


def _spec(**kw):
    return {
        "ligand": "meoh",
        "pose": 0,
        "symbols": SYMBOLS,
        "coords": COORDS,
        "net_charge": 0,
        "basis": "sto-3g",
        "energy_conv": 1e-10,
        "density_conv": 1e-8,
        **kw,
    }


@pytest.mark.needs_ferric
def test_pcm_legs_are_run_rhf_with_solvent_and_with_or_without_the_field():
    import ferric

    run = _script("run_plb_ddE")
    run._init_worker({"m": FIELD})
    mol = ferric.Molecule.from_xyz_string(run.xyz_text(SYMBOLS, COORDS, "x"), charge=0)
    bs = ferric.BasisSet.bundled("sto-3g")
    kw = {"energy_conv": 1e-10, "density_conv": 1e-8}
    alone = run.run_task(_spec(kind="alone", model="m", solvent="water"))
    fld = run.run_task(_spec(kind="field", model="m", solvent="water"))
    vac = run.run_task(_spec(kind="vac"))
    gas_fld = run.run_task(_spec(kind="field", model="m"))
    assert all(r["error"] is None and r["converged"] for r in (alone, fld, vac, gas_fld))
    assert alone["energy_hartree"] == ferric.run_rhf(mol, bs, solvent="water", **kw).energy
    assert fld["energy_hartree"] == ferric.run_rhf(mol, bs, solvent="water", point_charges=FIELD, **kw).energy
    assert alone["solvent"] == fld["solvent"] == "water" and "solvent" not in vac and "solvent" not in gas_fld
    assert alone["model"] == "m" and alone["kind"] == "alone" and isinstance(alone["iterations"], int)
    # the solvent really enters both legs, and the water setting is the dielectric constant 78.4
    assert alone["energy_hartree"] != vac["energy_hartree"] and fld["energy_hartree"] != gas_fld["energy_hartree"]
    assert run.run_task(_spec(kind="alone", model="m", solvent=78.4))["energy_hartree"] == alone["energy_hartree"]
    de_pcm = (fld["energy_hartree"] - alone["energy_hartree"]) * HARTREE_TO_KCAL
    de_gas = (gas_fld["energy_hartree"] - vac["energy_hartree"]) * HARTREE_TO_KCAL
    assert abs(de_pcm) > 0.5 and de_pcm != pytest.approx(de_gas, abs=1e-3)  # PCM changes the interaction energy


@pytest.mark.needs_ferric
def test_an_unknown_solvent_is_a_recorded_failure_not_a_vacuum_fallback():
    run = _script("run_plb_ddE")
    r = run.run_task(_spec(kind="alone", model="m", solvent="not-a-solvent"))
    assert r["error"] and "not-a-solvent" in r["error"] and "energy_hartree" not in r


def test_parse_solvent_takes_a_number_or_a_name():
    run = _script("run_plb_ddE")
    assert run.parse_solvent("4.0") == 4.0 and run.parse_solvent("78.4") == 78.4
    assert run.parse_solvent("water") == "water"


# ---------------------------------------------------------------------------------------------- the runner, end to end


class _Charge:
    def __init__(self, q, x, y, z):
        self.q, self.xyz_ang = q, (x, y, z)

    def as_ferric_bohr(self):
        return (self.q, *self.xyz_ang)


class _Pocket(list):
    provenance = None  # set in __init__


def _pocket_stub(tmp_path):
    """A stand-in for `load_pocket` (the production PQR is not needed): a short list of charges with provenance."""
    pqr = tmp_path / "f.pqr"
    pqr.write_text("REMARK stub\n")
    (tmp_path / "f.pqr.json").write_text(
        json.dumps(
            {
                "output_sha256": "b" * 64,
                "input_sha256": "a" * 64,
                "openmm_version": "8.6.1",
                "force_field_files": {"x.xml": "a" * 64},
                "deleted_residues_min_distance_to_center_ang": {"TPO:A:160": 21.3},
            }
        )
    )

    def load_pocket(path, cutoff_ang, center_ang):
        p = _Pocket(_Charge(*c) for c in FIELD)
        p.provenance = {
            "input_sha256": "b" * 64,
            "cutoff_ang": cutoff_ang,
            "center_ang": list(center_ang),
            "n_charges": len(FIELD),
        }
        return p

    return pqr, load_pocket


def _series(n=6):
    ligs = {}
    for i in range(n):
        shift = np.array([0.0, 0.0, 0.05 * i])
        ligs[f"L{i}"] = {
            "symbols": SYMBOLS,
            "coords": COORDS + shift * np.arange(6)[:, None],
            "heavy_smiles": "CO",
            "net_charge": 0,
        }
    return ligs


@pytest.fixture()
def runner(tmp_path, monkeypatch):
    """The run script with the series and the pocket replaced by stubs; spawned workers import it from SCRIPTS."""
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(SCRIPTS), os.environ.get("PYTHONPATH", "")]))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("run_plb_ddE", None)
    import run_plb_ddE as run  # the importable copy: spawned workers unpickle run_task from this module name

    pqr, load_pocket = _pocket_stub(tmp_path)
    ligs = _series()
    tgt = type("T", (), {"reference": "L0"})()
    monkeypatch.setattr(run, "load_series", lambda plb_dir, target: (None, tgt, copy.deepcopy(ligs)))
    monkeypatch.setattr(run, "basis_gaps", lambda symbols, basis: [])
    monkeypatch.setattr(run, "load_pocket", load_pocket)
    monkeypatch.setattr(run, "git_commit", lambda: "c" * 40)
    run.PQR = pqr
    return run


def _args(runner, tmp_path, *extra):
    return runner.main(
        [
            "run",
            "--plb-dir",
            str(tmp_path),
            "--target",
            "tyk2",
            "--field",
            f"gas={runner.PQR}",
            "--field",
            f"w={runner.PQR}",
            "--out",
            str(tmp_path / "out"),
            "--poses",
            "2",
            "--workers",
            "2",
            *extra,
        ]
    )


@pytest.mark.needs_ferric
def test_runner_assembles_a_solvent_model_against_its_own_alone_leg(runner, tmp_path):
    rc = _args(runner, tmp_path, "--solvent", "w=water", "--run", "vac", "gas", "alone:w", "w", "--max-cpu-hours", "1")
    assert rc == 0
    out = tmp_path / "out"
    recs = [json.loads(ln) for ln in (out / "tasks.jsonl").read_text().splitlines()]
    assert len(recs) == 6 * 2 * 4 and all(r["error"] is None for r in recs)
    assert {(r["kind"], r.get("model")) for r in recs} == {
        ("vac", None),
        ("field", "gas"),
        ("alone", "w"),
        ("field", "w"),
    }
    assert all(r.get("solvent") == "water" for r in recs if r.get("model") == "w")
    assert not any("solvent" in r for r in recs if r["kind"] == "vac" or r.get("model") == "gas")
    result = tmp_path / "res.json"
    assert runner.main(["assemble", "--out", str(out), "--result", str(result)]) == 0
    raw = json.loads(result.read_text())
    # the solvent model is recorded as such, and the gas model is untouched
    assert raw["inputs"]["field_solvent"] == {"gas": None, "w": "water"}
    assert raw["tiers"][0]["solvent_by_model"] == {"gas": None, "w": "water"}
    by = {(r["kind"], r.get("model"), r["ligand"], r["pose"]): r["energy_hartree"] for r in recs}
    for n in raw["results"]["E_vac_hartree"]:
        for k in range(2):
            assert raw["results"]["E_alone_hartree"]["w"][n][k] == by[("alone", "w", n, k)]
            want_pcm = (by[("field", "w", n, k)] - by[("alone", "w", n, k)]) * HARTREE_TO_KCAL
            want_gas = (by[("field", "gas", n, k)] - by[("vac", None, n, k)]) * HARTREE_TO_KCAL
            assert raw["results"]["dE_int_kcal_mol"]["w"][n][k] == pytest.approx(want_pcm, rel=1e-12)
            assert raw["results"]["dE_int_kcal_mol"]["gas"][n][k] == pytest.approx(want_gas, rel=1e-12)
            wrong = (by[("field", "w", n, k)] - by[("vac", None, n, k)]) * HARTREE_TO_KCAL
            assert raw["results"]["dE_int_kcal_mol"]["w"][n][k] != pytest.approx(wrong, rel=1e-3)
    res = pm.validate_results(raw)
    assert res.solvent == {"w": "water"} and res.models == ("gas", "w")
    assert pm.dielectric_family(res) == {}  # a solvent model is not a charge divisor
    ddg = {"L0": 0.0, "L1": -1.0, "L2": 0.4, "L3": 1.1, "L4": -0.3, "L5": 0.8}
    out_an = pm.analyse(res, _fake_target(ddg), n_boot=50)
    assert "charge_model_comparison" not in out_an and "screening" not in out_an
    sol = out_an["solvent"]
    assert sol["gas_model"] == "gas" and list(sol["by_model"]) == ["w"] and sol["by_model"]["w"]["n_poses"] == 2
    assert sol["by_model"]["w"]["solvent"] == "water"


@pytest.mark.needs_ferric
def test_cap_guard_counts_prior_spend_and_the_scfs_in_flight(runner, tmp_path):
    base = ["--solvent", "w=water", "--run", "alone:w"]
    # CPU spent elsewhere already equals the cap: nothing may start
    assert _args(runner, tmp_path, *base, "--max-cpu-hours", "1", "--prior-cpu-hours", "1") == 0
    assert not (tmp_path / "out" / "tasks.jsonl").exists() or (tmp_path / "out" / "tasks.jsonl").read_text() == ""
    # no CPU spent, but one SCF is assumed to cost more than the whole cap: nothing may start
    assert _args(runner, tmp_path, *base, "--max-cpu-hours", "0.01", "--est-scf-cpu-s", "100") == 0
    assert not (tmp_path / "out" / "tasks.jsonl").exists() or (tmp_path / "out" / "tasks.jsonl").read_text() == ""
    # with room for exactly the estimate, work starts
    assert _args(runner, tmp_path, *base, "--max-cpu-hours", "0.01", "--est-scf-cpu-s", "1", "--workers", "1") == 0
    assert (tmp_path / "out" / "tasks.jsonl").read_text() != ""


@pytest.mark.needs_ferric
def test_a_failed_scf_is_not_retried_its_pose_is_skipped_and_the_exit_is_nonzero(runner, tmp_path):
    args = ["--solvent", "w=not-a-solvent", "--run", "alone:w", "w", "--max-cpu-hours", "1", "--workers", "1"]
    assert _args(runner, tmp_path, *args) == 1
    path = tmp_path / "out" / "tasks.jsonl"
    recs = [json.loads(ln) for ln in path.read_text().splitlines()]
    # one SCF per pose failed; the other 7 tasks of each pose were not run
    assert len(recs) == 2 and {r["pose"] for r in recs} == {0, 1} and all(r["error"] for r in recs)
    assert all("not-a-solvent" in r["error"] and "energy_hartree" not in r for r in recs)
    before = path.read_text()
    assert _args(runner, tmp_path, *args) == 1  # a restart does not retry them
    assert path.read_text() == before
    assert (tmp_path / "out" / "meta.json").is_file()  # the run still wrote its provenance


def test_a_solvent_and_a_charge_divisor_on_one_model_are_refused(runner, tmp_path):
    with pytest.raises(SystemExit, match="separate treatments"):
        _args(runner, tmp_path, "--solvent", "w=water", "--eps", "w=2", "--run", "w", "--max-cpu-hours", "1")
    with pytest.raises(SystemExit, match="alone:MODEL"):
        _args(runner, tmp_path, "--run", "alone:w", "--max-cpu-hours", "1")  # w has no solvent: no alone leg exists


# ---------------------------------------------------------------------------------------------- the loader and analysis


def _with_solvent(n_s=None, *, factor=0.5, shift=0.0, flip=None, solvent="water", eps_pqr=True):
    """A record with a gas model "A" and a solvent model "S" whose dE_int is A's scaled by `factor` (+ `shift`)."""
    rec, ddg = _record(n_lig=6, n_poses=6, models=("A",), slope=3.0, noise=0.3, seed=11)
    inp, res = rec["inputs"], rec["results"]
    n_s = n_s or 6
    inp["models"] = ["A", "S"]
    inp["n_poses_by_model"]["S"] = n_s
    inp["pose_indices"]["S"] = list(range(n_s))
    inp["field_solvent"] = {"A": None, "S": solvent}
    for key in ("field_provenance", "prep_provenance"):
        inp[key]["S"] = copy.deepcopy(inp[key]["A"])
    inp["field_provenance"]["A"]["solvent"] = None
    inp["field_provenance"]["S"]["solvent"] = solvent
    dE = {n: np.array(v)[:n_s] * factor + shift for n, v in res["dE_int_kcal_mol"]["A"].items()}
    if flip:
        dE[flip[1]] = 2 * dE["L0"] - dE[flip[1]]
    alone = {n: [-499.0 - 0.1 * i - 0.01 * k for k in range(n_s)] for i, n in enumerate(dE)}
    res["dE_int_kcal_mol"]["S"] = {n: v.tolist() for n, v in dE.items()}
    res["E_alone_hartree"] = {"S": alone}
    res["E_field_hartree"]["S"] = {n: [alone[n][k] + dE[n][k] / HARTREE_TO_KCAL for k in range(n_s)] for n in dE}
    res["min_dist_to_field_ang"]["S"] = copy.deepcopy(res["min_dist_to_field_ang"]["A"])
    res["min_dist_to_field_ang"]["S"] = {n: v[:n_s] for n, v in res["min_dist_to_field_ang"]["S"].items()}
    rec["tiers"][0]["solvent_by_model"] = {"A": None, "S": solvent}
    rec["input_digest"] = digest({"inputs": inp, "tiers": rec["tiers"]})
    return rec, ddg


def test_a_valid_solvent_record_loads_and_the_reference_leg_is_the_alone_energy():
    rec, ddg = _with_solvent()
    res = pm.validate_results(rec)
    assert res.solvent == {"S": "water"} and res.models == ("A", "S")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["results"].pop("E_alone_hartree"),  # no alone leg at all
        lambda r: r["results"]["E_alone_hartree"]["S"]["L2"].__setitem__(1, -480.0),  # an edited alone energy
        # dE_int computed against the gas-phase vacuum energy instead of the alone energy
        lambda r: r["results"]["E_alone_hartree"].__setitem__(
            "S", {n: list(v[:6]) for n, v in r["results"]["E_vac_hartree"].items()}
        ),
        lambda r: r["inputs"]["field_solvent"].__setitem__("S", None),  # provenance says gas, energies say solvent
        lambda r: r["inputs"]["field_provenance"]["S"].__setitem__("solvent", "dmso"),
        lambda r: r["inputs"]["field_solvent"].__setitem__("Z", 4.0),  # a solvent for a model that was never run
    ],
)
def test_a_corrupted_solvent_record_is_refused(mutate):
    rec, _ = _with_solvent()
    mutate(rec)
    with pytest.raises(pm.PlbResultsError):
        pm.validate_results(rec)


@pytest.mark.parametrize("bad", [1.0, 0.5, float("inf"), float("nan"), True, ""])
def test_a_solvent_that_is_not_a_name_or_a_dielectric_above_one_is_refused(bad):
    rec, _ = _with_solvent(solvent=bad)
    with pytest.raises(pm.PlbResultsError):
        pm.validate_results(rec)


def test_a_dielectric_constant_is_a_valid_solvent():
    rec, _ = _with_solvent(solvent=4.0)
    assert pm.validate_results(rec).solvent == {"S": 4.0}


def test_solvent_models_are_neither_a_screening_family_nor_a_second_charge_model():
    rec, ddg = _with_solvent()
    res = pm.validate_results(rec)
    assert pm.dielectric_family(res) == {}
    out = pm.analyse(res, _fake_target(ddg), n_boot=60)
    assert "charge_model_comparison" not in out and "screening" not in out and "solvent" in out
    assert set(out["models"]) == {"A", "S"}


def test_solvent_analysis_compares_on_the_common_poses_and_reports_the_planted_change():
    rec, ddg = _with_solvent(n_s=3, factor=0.5)
    res = pm.validate_results(rec)
    x = np.array([ddg[n] for n in res.analogues])
    sol = pm.solvent_analysis(res, x, n_boot=200, seed=3)
    row = sol["by_model"]["S"]
    assert row["n_poses"] == 3 and row["pose_indices"] == [0, 1, 2] and row["gas_model"] == "A"
    first3 = pm.paired_ddE(res, "A", res.dE["A"][:, :3])  # the gas value is on the SAME three poses, not its own six
    assert row["gas_ddE_mean"] == pytest.approx({n: m.mean for n, m in first3.items()})
    assert row["point"]["slope"] == pytest.approx(row["gas_point"]["slope"] * 0.5)
    assert row["mean_dE_int_ratio_to_gas"] == pytest.approx(0.5)
    assert row["sd_ratio_over_gas_sd_ratio"] == pytest.approx(0.5) and row["labels"]["spread"] == "narrows the spread"
    assert row["spearman_with_gas_ddE"] == pytest.approx(1.0) and row["delta_spearman_vs_gas"] == pytest.approx(0.0)
    assert row["n_ddE_sign_flips_vs_gas"] == 0 and row["n_dE_int_sign_flips_vs_gas"] == 0
    assert row["labels"]["rank_change"] == "unchanged at the level this study can detect"
    assert row["n_dE_int_values"] == 6 * 3


def test_solvent_rank_reversal_is_a_flip_and_a_labelled_shift():
    rec, ddg = _with_solvent(factor=1.0, flip=("S", "L5"))
    res = pm.validate_results(rec)
    x = np.array([ddg[n] for n in res.analogues])
    row = pm.solvent_analysis(res, x, n_boot=200, seed=3)["by_model"]["S"]
    assert row["spearman_with_gas_ddE"] < 1.0 and row["delta_spearman_vs_gas"] < 0
    assert row["n_ddE_sign_flips_vs_gas"] >= 1


def test_several_solvent_models_are_also_compared_on_the_poses_all_of_them_completed():
    rec, ddg = _with_solvent(n_s=4)
    # a second solvent model with only 2 poses
    inp, res_ = rec["inputs"], rec["results"]
    inp["models"].append("S2")
    inp["n_poses_by_model"]["S2"] = 2
    inp["pose_indices"]["S2"] = [0, 1]
    inp["field_solvent"]["S2"] = 4.0
    for key in ("field_provenance", "prep_provenance"):
        inp[key]["S2"] = copy.deepcopy(inp[key]["S"])
    inp["field_provenance"]["S2"]["solvent"] = 4.0
    res_["dE_int_kcal_mol"]["S2"] = {n: v[:2] for n, v in res_["dE_int_kcal_mol"]["S"].items()}
    res_["E_alone_hartree"]["S2"] = {n: v[:2] for n, v in res_["E_alone_hartree"]["S"].items()}
    res_["E_field_hartree"]["S2"] = {n: v[:2] for n, v in res_["E_field_hartree"]["S"].items()}
    res_["min_dist_to_field_ang"]["S2"] = {n: v[:2] for n, v in res_["min_dist_to_field_ang"]["S"].items()}
    rec["input_digest"] = digest({"inputs": inp, "tiers": rec["tiers"]})
    res = pm.validate_results(rec)
    x = np.array([ddg[n] for n in res.analogues])
    sol = pm.solvent_analysis(res, x, n_boot=100, seed=1)
    assert sol["n_poses_common_all"] == 2
    assert sol["by_model"]["S"]["n_poses"] == 4 and sol["by_model"]["S2"]["n_poses"] == 2
    assert sol["on_common_poses_of_all"]["S"]["n_poses"] == 2 and sol["on_common_poses_of_all"]["S2"]["n_poses"] == 2


def test_a_solvent_model_on_another_field_or_with_a_charge_divisor_is_refused_by_the_comparison():
    rec, ddg = _with_solvent()
    rec["inputs"]["prep_provenance"]["S"]["output_sha256"] = "d" * 64
    rec["inputs"]["field_provenance"]["S"]["input_sha256"] = "d" * 64
    rec["input_digest"] = digest({"inputs": rec["inputs"], "tiers": rec["tiers"]})
    res = pm.validate_results(rec)
    with pytest.raises(pm.PlbResultsError, match="field"):
        pm.solvent_analysis(res, np.arange(5.0), n_boot=10, seed=0)
    rec2, _ = _with_solvent()
    rec2["inputs"]["field_provenance"]["S"]["dielectric_eps"] = 2.0
    rec2["input_digest"] = digest({"inputs": rec2["inputs"], "tiers": rec2["tiers"]})
    with pytest.raises(pm.PlbResultsError, match="divide"):
        pm.solvent_analysis(pm.validate_results(rec2), np.arange(5.0), n_boot=10, seed=0)


def test_no_solvent_means_no_solvent_section():
    rec, ddg = _record(n_lig=6, n_poses=5)
    assert "solvent" not in pm.analyse(pm.validate_results(rec), _fake_target(ddg), n_boot=20)


# ---------------------------------------------------------------------------------------------- the declared labels


def test_rank_change_label_follows_the_declared_thresholds():
    f = pm.rank_change_label
    assert f(0.15, 0.01, 0.4) == "improves" and f(0.30, 0.02, 0.5) == "improves"
    assert f(0.15, -0.01, 0.4) == "shift of at least 0.15 not distinguishable from zero"
    assert f(-0.15, -0.4, -0.01) == "worsens" and f(-0.5, -0.9, -0.1) == "worsens"
    assert f(-0.20, -0.4, 0.05) == "shift of at least 0.15 not distinguishable from zero"
    assert (
        f(0.1499, 0.01, 0.4)
        == f(-0.1499, -0.4, -0.01)
        == f(0.0, 0.0, 0.0)
        == "unchanged at the level this study can detect"
    )
    assert f(0.3, None, None) == "shift of at least 0.15 not distinguishable from zero"  # no CI, no claim
    assert pm.RANK_SHIFT_THRESHOLD == 0.15


def test_tracks_label_needs_every_one_of_the_four_criteria():
    f = pm.tracks_label
    assert f(0.7, 0.01, 0.2, 0.5) == "tracks (weakly)"
    assert f(0.7, 0.05, 0.2, 0.5) == "does not track at the level detectable"  # p not below 0.05
    assert f(0.7, 0.01, -0.1, 0.5) == "does not track at the level detectable"  # CI contains 0
    assert f(0.7, 0.01, None, 0.5) == "does not track at the level detectable"
    assert f(0.7, 0.01, 0.2, 0.0) == "does not track at the level detectable"  # tau not positive
    assert f(0.0, 0.01, 0.2, 0.5) == "does not track at the level detectable"
    assert f(-0.8, 0.01, -0.9, -0.6) == "anti-correlated"
    assert f(-0.8, 0.2, -0.9, -0.6) == "does not track at the level detectable"


def test_spread_label_thresholds():
    assert pm.spread_label(0.79) == "narrows the spread" and pm.spread_label(0.8) == "about the same spread"
    assert pm.spread_label(1.25) == "about the same spread" and pm.spread_label(1.26) == "widens the spread"


def test_paired_delta_ci_is_zero_for_identical_models_and_uses_the_same_resample():
    rng = np.random.default_rng(0)
    x = rng.normal(size=12)
    y = x + rng.normal(size=12)
    ci = pm.paired_delta_spearman_ci(x, y, y, n_boot=500, seed=1)
    assert ci["lo"] == ci["hi"] == 0.0  # an unpaired resample would not cancel
    ci2 = pm.paired_delta_spearman_ci(x, y, -y, n_boot=500, seed=1)  # rho(y) - rho(-y) = 2 rho(y), same resamples
    one = pm.bootstrap_ci(x, y, "spearman", n_boot=500, seed=1)
    assert ci2["lo"] == pytest.approx(2 * one["lo"]) and ci2["hi"] == pytest.approx(2 * one["hi"])
    assert pm.paired_delta_spearman_ci(x, y, x, n_boot=300, seed=2) == pm.paired_delta_spearman_ci(
        x, y, x, n_boot=300, seed=2
    )
    assert math.isfinite(ci["n_valid"])


# ------------------------------------------------------------------------------------- folding the stored gas model


BASE = ROOT / "benchmarks" / "plb" / "results" / "tyk2_sto3g.json"


def _pcm_out_dir(tmp_path, *, field_sha=None):
    """The files a PCM `run` leaves (meta, tasks, commands) for the committed tyk2 series, with made-up energies."""
    base = json.loads(BASE.read_text())
    inp = base["inputs"]
    fp = copy.deepcopy(inp["field_provenance"]["amber14"])
    fp["solvent"] = "water"
    if field_sha:
        fp["input_sha256"] = field_sha
    keys = (
        "target",
        "reference",
        "pivot_ang",
        "n_poses",
        "seed",
        "jitter_deg",
        "jitter_ang",
        "basis",
        "cutoff_ang",
        "pose_geometry_sha256",
        "excluded_ligands",
        "ligand_atom_counts",
        "smeltery_commit",
    )
    meta = {k: inp[k] for k in keys}
    meta |= {
        "models": ["pcm"],
        "field_eps": {"pcm": 1.0},
        "field_solvent": {"pcm": "water"},
        "field_provenance": {"pcm": fp},
        "prep_provenance": {"pcm": copy.deepcopy(inp["prep_provenance"]["amber14"])},
    }
    out = tmp_path / "pcm_out"
    out.mkdir()
    (out / "meta.json").write_text(json.dumps(meta))
    (out / "commands.log").write_text("run_plb_ddE.py run ... --solvent pcm=water\n")
    rng = np.random.default_rng(5)
    lines = []
    for n in base["results"]["E_vac_hartree"]:
        for k in range(4):
            e_alone = -900.0 - rng.uniform()
            for kind, e in (("alone", e_alone), ("field", e_alone - 0.02 - 0.001 * rng.uniform())):
                rec = {
                    "kind": kind,
                    "model": "pcm",
                    "ligand": n,
                    "pose": k,
                    "solvent": "water",
                    "energy_hartree": e,
                    "converged": True,
                    "error": None,
                    "cpu_s": 300.0,
                    "wall_s": 400.0,
                }
                if kind == "field":
                    rec["min_dist_to_field_ang"] = 2.5
                lines.append(rec)
    (out / "tasks.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
    return out, base


def test_assemble_folds_the_stored_gas_model_beside_the_solvent_model_unchanged(tmp_path):
    run = _script("run_plb_ddE")
    out, base = _pcm_out_dir(tmp_path)
    result = tmp_path / "r.json"
    argv = ["assemble", "--out", str(out), "--result", str(result), "--base", str(BASE), "--base-model", "amber14"]
    assert run.main([*argv, "--same-field-as-base"]) == 0
    raw = json.loads(result.read_text())
    res = pm.validate_results(raw)
    assert res.models == ("amber14", "pcm") and res.solvent == {"pcm": "water"}
    assert res.n_poses_by_model == {"amber14": 8, "pcm": 4}
    # the gas model enters bit for bit (nothing recomputed), the solvent model is against its own alone leg
    assert raw["results"]["E_vac_hartree"] == base["results"]["E_vac_hartree"]
    assert raw["results"]["dE_int_kcal_mol"]["amber14"] == base["results"]["dE_int_kcal_mol"]["amber14"]
    n = res.analogues[0]
    assert raw["results"]["E_alone_hartree"]["pcm"][n][2] != raw["results"]["E_vac_hartree"][n][2]
    assert raw["inputs"]["field_solvent"] == {"amber14": None, "pcm": "water"}
    assert raw["inputs"]["reused_from_base_result"]["model"] == "amber14"


def test_a_solvent_model_on_a_different_field_is_refused_when_the_same_field_is_demanded(tmp_path):
    run = _script("run_plb_ddE")
    out, _ = _pcm_out_dir(tmp_path, field_sha="9" * 64)
    argv = ["assemble", "--out", str(out), "--result", str(tmp_path / "r.json"), "--base", str(BASE)]
    with pytest.raises(SystemExit, match="input_sha256"):
        run.main([*argv, "--same-field-as-base"])
