"""The PLB measurement code: statistics, validation and the run script's geometry. Each test can fail.

Mutation checks done by hand when this file was written (each made at least one test below fail, then reverted):
flipping the sign of the computed ddE, pairing poses by a shifted index, giving each ligand its own jitter instead
of a shared one, dropping the dE-from-energies consistency check, and not refusing an edited record.
"""

from __future__ import annotations

import copy
import importlib.util
import itertools
import json
import math
from pathlib import Path

import numpy as np
import pytest

from smeltery import plb_measure as pm
from smeltery.model import HARTREE_TO_KCAL
from smeltery.record import digest

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------------------------------------ statistics


def test_ranks_kendall_pearson_against_scipy_with_ties():
    stats = pytest.importorskip("scipy.stats")
    rng = np.random.default_rng(3)
    for _ in range(20):
        x = rng.integers(0, 5, size=9).astype(float)
        y = rng.normal(size=9) + 0.5 * x
        assert pm.kendall_tau_b(x, y) == pytest.approx(stats.kendalltau(x, y).statistic, abs=1e-12)
        assert pm.pearson(x, y) == pytest.approx(stats.pearsonr(x, y).statistic, abs=1e-12)
        assert pm.spearman(x, y) == pytest.approx(stats.spearmanr(x, y).statistic, abs=1e-12)
        assert np.allclose(pm._avg_ranks(x), stats.rankdata(x))


def test_ols_matches_polyfit_and_recovers_slope():
    x = np.array([-1.6, -0.9, -0.2, 0.1, 0.5, 0.9, 1.2, 1.9])
    y = 4.5 * x + 1.0 + np.array([0.1, -0.2, 0.05, 0.0, 0.15, -0.1, 0.0, 0.2])
    fit = pm.ols(x, y)
    slope, icpt = np.polyfit(x, y, 1)
    assert fit["slope"] == pytest.approx(slope) and fit["intercept"] == pytest.approx(icpt)
    resid = y - (icpt + slope * x)
    assert fit["rmse"] == pytest.approx(math.sqrt((resid**2).sum() / (len(x) - 2)))
    assert abs(fit["slope"] - 4.5) < 0.3


def test_exact_permutation_p_of_a_perfect_ranking_is_two_over_n_factorial():
    x = np.arange(7, dtype=float)
    null = pm.permutation_null(x, 2.0 * x + 1)
    assert null["exact"] and null["n_permutations"] == math.factorial(7)
    assert null["spearman"]["p_two_sided"] == pytest.approx(2 / math.factorial(7))
    assert null["spearman"]["p_one_sided_positive"] == pytest.approx(1 / math.factorial(7))
    # the sign is not lost: an inverted relationship is NOT a positive-direction hit
    inv = pm.permutation_null(x, -x)
    assert inv["spearman"]["p_one_sided_positive"] == pytest.approx(1.0)
    assert inv["spearman"]["p_two_sided"] == pytest.approx(2 / math.factorial(7))


def test_permutation_null_matches_brute_force_loop():
    rng = np.random.default_rng(1)
    x, y = rng.normal(size=6), rng.normal(size=6)
    obs = pm.spearman(x, y)
    nulls = [pm.spearman(x[list(p)], y) for p in itertools.permutations(range(6))]
    brute_two = np.mean([abs(v) >= abs(obs) - 1e-9 for v in nulls])
    brute_one = np.mean([v >= obs - 1e-9 for v in nulls])
    got = pm.permutation_null(x, y)["spearman"]
    assert got["p_two_sided"] == pytest.approx(brute_two) and got["p_one_sided_positive"] == pytest.approx(brute_one)
    kn = [pm.kendall_tau_b(x[list(p)], y) for p in itertools.permutations(range(6))]
    assert pm.permutation_null(x, y)["kendall"]["p_two_sided"] == pytest.approx(
        np.mean([abs(v) >= abs(pm.kendall_tau_b(x, y)) - 1e-9 for v in kn])
    )


def test_exact_critical_spearman_for_n9():
    # the textbook exact two-sided 5% critical value for n = 9 is 0.700
    null = pm.permutation_null(np.arange(9.0), np.arange(9.0))
    assert null["spearman_critical_abs_two_sided_0.05"] == pytest.approx(0.7, abs=0.01)


def test_bootstrap_ci_is_seeded_and_brackets_a_strong_trend():
    x = np.linspace(-1.5, 1.2, 9)
    y = 3 * x + np.random.default_rng(0).normal(scale=0.3, size=9)
    a = pm.bootstrap_ci(x, y, "slope", n_boot=2000, seed=5)
    b = pm.bootstrap_ci(x, y, "slope", n_boot=2000, seed=5)
    assert a == b
    assert a["lo"] < 3 < a["hi"]
    assert pm.bootstrap_ci(x, -y, "spearman", n_boot=500, seed=1)["hi"] < 0  # sign is carried through


def test_binomial_two_sided():
    assert pm.binomial_two_sided(9, 9) == pytest.approx(2 / 512)
    assert pm.binomial_two_sided(5, 9) == pytest.approx(1.0)
    assert math.isnan(pm.binomial_two_sided(0, 0))


def test_power_is_monotone_and_small_at_n9():
    pw = pm.power_simulation(9, [0.3, 0.9], n_sim=800, seed=2)
    assert pw["power_by_true_pearson_r"]["0.30"] < 0.3 < pw["power_by_true_pearson_r"]["0.90"]
    assert pw["critical_abs_spearman"] == pytest.approx(0.7, abs=0.01)


# ------------------------------------------------------------------------------------------------ records


def _record(*, n_lig=6, n_poses=5, models=("A", "B"), slope=3.0, seed=0, noise=0.5, ref="L0", n_by_model=None):
    """A valid RunRecord dict whose ddE = slope * (a made-up ddG) + pose noise; returns (record, ddg)."""
    rng = np.random.default_rng(seed)
    names = [ref] + [f"L{i}" for i in range(1, n_lig)]
    ddg = {n: 0.0 if n == ref else float(v) for n, v in zip(names, np.linspace(-1.5, 1.5, n_lig))}
    common = rng.normal(scale=20.0, size=n_poses)  # pose noise shared by every ligand: the pairing cancels it
    dE = {}
    for mi, m in enumerate(models):
        dE[m] = {
            n: [
                float(-30 + slope * ddg[n] * (1 + 0.1 * mi) + common[k] + rng.normal(scale=noise))
                for k in range(n_poses)
            ]
            for n in names
        }
    n_by_model = n_by_model or {m: n_poses for m in models}
    dE = {m: {n: v[: n_by_model[m]] for n, v in d.items()} for m, d in dE.items()}  # a model may have fewer poses
    e_vac = {n: [-500.0 - 0.1 * i - 0.01 * k for k in range(n_poses)] for i, n in enumerate(names)}
    e_fld = {
        m: {n: [e_vac[n][k] + dE[m][n][k] / HARTREE_TO_KCAL for k in range(n_by_model[m])] for n in names}
        for m in models
    }
    sha = "a" * 64
    inputs = {
        "target": "t",
        "reference": ref,
        "pivot_ang": [0.0, 0.0, 0.0],
        "n_poses": n_poses,
        "n_poses_by_model": n_by_model,
        "seed": 1,
        "jitter_deg": 15.0,
        "jitter_ang": 0.5,
        "basis": "sto-3g",
        "cutoff_ang": 15.0,
        "models": list(models),
        "field_provenance": {
            m: {"input_sha256": f"{i}" * 64, "cutoff_ang": 15.0, "center_ang": [0, 0, 0], "n_charges": 10}
            for i, m in enumerate(models)
        },
        "prep_provenance": {
            m: {
                "input_sha256": sha,
                "output_sha256": f"{i}" * 64,
                "openmm_version": "8.6.1",
                "force_field_files": {"x.xml": sha},
                "deleted_residues_min_distance_to_center_ang": {"TPO:A:160": 21.3},
            }
            for i, m in enumerate(models)
        },
        "smeltery_commit": "b" * 40,
        "pose_geometry_sha256": {n: sha for n in names},
    }
    tiers = [{"name": "field_interaction", "basis": "sto-3g"}]
    results = {
        "schema": pm.SCHEMA,
        "dE_int_kcal_mol": dE,
        "E_vac_hartree": e_vac,
        "E_field_hartree": e_fld,
        "min_dist_to_field_ang": {m: {n: [2.5] * n_by_model[m] for n in names} for m in models},
        "failures": [],
        "total_cpu_s_used_in_results": 123.0,
    }
    rec = {
        "campaign": "plb-t-paired-ddE",
        "inputs": inputs,
        "tiers": tiers,
        "results": results,
        "smeltery_version": "0",
        "ferric": {"version": "0.1.0rc7", "provenance": "VERIFIED: index release 0.1.0rc7 (immutable)"},
        "input_digest": digest({"inputs": inputs, "tiers": tiers}),
    }
    return rec, ddg


def test_a_valid_record_loads():
    rec, _ = _record()
    res = pm.validate_results(rec)
    assert res.ligands[0] == "L0" and res.models == ("A", "B") and res.dE["A"].shape == (6, 5)


def _corrupt(rec, how):
    r = copy.deepcopy(rec)
    inp, res = r["inputs"], r["results"]
    if how == "edited_dE":
        res["dE_int_kcal_mol"]["A"]["L2"][1] += 0.5  # no longer (E_field - E_vac) * 627.5
    elif how == "edited_inputs":
        inp["seed"] = 2  # input_digest no longer matches
    elif how == "missing_key":
        del inp["smeltery_commit"]
    elif how == "bad_commit":
        inp["smeltery_commit"] = "main"
        r["input_digest"] = digest({"inputs": inp, "tiers": r["tiers"]})
    elif how == "pqr_swapped":
        inp["prep_provenance"]["A"]["output_sha256"] = "9" * 64
        r["input_digest"] = digest({"inputs": inp, "tiers": r["tiers"]})
    elif how == "failures":
        res["failures"] = [{"ligand": "L1", "pose": 0, "error": "SCF did not converge"}]
    elif how == "nan":
        res["E_vac_hartree"]["L1"][0] = float("nan")
    elif how == "shape":
        res["dE_int_kcal_mol"]["B"]["L3"].pop()
    elif how == "no_ferric":
        r["ferric"] = {"version": None, "provenance": "UNKNOWN"}
    elif how == "wrong_schema":
        res["schema"] = "other"
    elif how == "short_vacuum":
        for v in res["E_vac_hartree"].values():
            v.pop()  # fewer vacuum energies than a model claims poses
    elif how == "overclaimed_poses":
        inp["n_poses_by_model"]["B"] = 4  # arrays hold 3
        r["input_digest"] = digest({"inputs": inp, "tiers": r["tiers"]})
    elif how == "contact":
        res["min_dist_to_field_ang"]["A"]["L1"][0] = 0.0
    else:
        raise AssertionError(how)
    return r


@pytest.mark.parametrize(
    "how",
    [
        "edited_dE",
        "edited_inputs",
        "missing_key",
        "bad_commit",
        "pqr_swapped",
        "failures",
        "nan",
        "shape",
        "no_ferric",
        "wrong_schema",
        "contact",
        "short_vacuum",
        "overclaimed_poses",
    ],
)
def test_a_corrupted_record_is_refused(how):
    rec, _ = _record(n_by_model={"A": 5, "B": 3})
    pm.validate_results(rec)
    with pytest.raises(pm.PlbResultsError):
        pm.validate_results(_corrupt(rec, how))


def test_load_results_refuses_unreadable_and_malformed_files(tmp_path):
    with pytest.raises(pm.PlbResultsError):
        pm.load_results(tmp_path / "missing.json")
    (tmp_path / "bad.json").write_text("{not json")
    with pytest.raises(pm.PlbResultsError):
        pm.load_results(tmp_path / "bad.json")
    rec, _ = _record()
    (tmp_path / "ok.json").write_text(json.dumps(rec))
    assert pm.load_results(tmp_path / "ok.json").n_poses == 5


# ------------------------------------------------------------------------------------------------ analysis


def test_pairing_is_by_pose_index_and_cancels_shared_pose_noise():
    rec, _ = _record(noise=0.0)
    res = pm.validate_results(rec)
    ddE = pm.paired_ddE(res, "A")
    # noise=0: every pose of ligand i is parent + a constant, so the paired SEM is exactly 0
    assert all(m.sem == pytest.approx(0.0, abs=1e-9) for m in ddE.values())
    # mis-pairing (analogue poses shifted by one index) must show up as pose noise
    shifted = res.dE["A"].copy()
    shifted[1:] = np.roll(shifted[1:], 1, axis=1)
    bad = pm.paired_ddE(res, "A", shifted)
    assert all(m.sem > 1.0 for m in bad.values())


def _fake_target(ddg: dict[str, float], ref="L0"):
    from types import SimpleNamespace

    ligs = [
        SimpleNamespace(name=n, ddg_kcal_mol=v, ddg_sigma_kcal_mol=0.1 if n != ref else 0.0, smiles=s)
        for (n, v), s in zip(ddg.items(), ["CCO", "CCCO", "CCCCO", "c1ccccc1O", "CCN", "CCCl"])
    ]
    return SimpleNamespace(reference=ref, ligands=ligs)


def test_analyse_recovers_a_planted_slope_and_flags_its_sign():
    rec, ddg = _record(n_lig=6, n_poses=6, slope=3.0, noise=0.2)
    res = pm.validate_results(rec)
    out = pm.analyse(res, _fake_target(ddg), n_boot=300)
    a = out["models"]["A"]
    assert a["point"]["spearman"] == pytest.approx(1.0) and a["point"]["slope"] == pytest.approx(3.0, abs=0.4)
    assert a["permutation"]["spearman"]["p_two_sided"] == pytest.approx(2 / math.factorial(5))
    # flip the sign of every computed ddE: the correlation must go to -1, the one-sided p to 1
    flipped = _record(n_lig=6, n_poses=6, slope=-3.0, noise=0.2)[0]
    fa = pm.analyse(pm.validate_results(flipped), _fake_target(ddg), n_boot=300)["models"]["A"]
    assert (
        fa["point"]["spearman"] == pytest.approx(-1.0) and fa["permutation"]["spearman"]["p_one_sided_positive"] == 1.0
    )
    assert fa["point"]["n_sign_agree"] == 0 and a["point"]["n_sign_agree"] == 5


def test_analyse_refuses_a_mismatched_manifest():
    rec, ddg = _record()
    res = pm.validate_results(rec)
    t = _fake_target(ddg)
    t.reference = "L1"
    with pytest.raises(pm.PlbResultsError):
        pm.analyse(res, t, n_boot=10)
    t2 = _fake_target({**ddg, "LX": 0.3})
    t2.ligands = t2.ligands[:-1] + [
        type(t2.ligands[0])(name="LX", ddg_kcal_mol=0.3, ddg_sigma_kcal_mol=0.1, smiles="CC")
    ]
    with pytest.raises(pm.PlbResultsError):
        pm.analyse(res, t2, n_boot=10)


def test_charge_model_comparison_counts_flips_pairs_and_floor():
    rec, ddg = _record(n_lig=6, n_poses=5, slope=3.0, noise=0.0)
    res = pm.validate_results(rec)
    out = pm.analyse(res, _fake_target(ddg), n_boot=100)["charge_model_comparison"]
    # model B is model A with a 10 % larger slope: same order of every ligand, same sign, floor = range of the deltas
    assert (
        out["n"] == 5 and out["n_sign_flips_of_ddE"] == 0 and out["n_pairs_order_flipped"] == 0 and out["n_pairs"] == 10
    )
    d = list(out["deltas_b_minus_a"].values())
    assert out["charge_sensitivity_floor_kcal_mol"] == pytest.approx(max(d) - min(d))
    assert out["spearman_between_models"] == pytest.approx(1.0)
    # reverse model B's ddE (about the parent): every sign flips and every pair order flips
    rev = copy.deepcopy(rec)
    r = rev["results"]
    for n in r["dE_int_kcal_mol"]["B"]:
        par = np.array(r["dE_int_kcal_mol"]["B"]["L0"])
        r["dE_int_kcal_mol"]["B"][n] = (2 * par - np.array(r["dE_int_kcal_mol"]["B"][n])).tolist()
        r["E_field_hartree"]["B"][n] = (
            np.array(r["E_vac_hartree"][n]) + np.array(r["dE_int_kcal_mol"]["B"][n]) / HARTREE_TO_KCAL
        ).tolist()
    r["dE_int_kcal_mol"]["B"]["L0"] = r["dE_int_kcal_mol"]["B"]["L0"]
    o2 = pm.analyse(pm.validate_results(rev), _fake_target(ddg), n_boot=100)["charge_model_comparison"]
    assert o2["n_sign_flips_of_ddE"] == 5 and o2["n_pairs_order_flipped"] == 10
    assert o2["spearman_between_models"] == pytest.approx(-1.0)


def test_pair_resolution_applies_the_floor():
    rec, ddg = _record(n_lig=6, n_poses=6, slope=10.0, noise=0.01)
    res = pm.validate_results(rec)
    out = pm.analyse(res, _fake_target(ddg), n_boot=50)["models"]["A"]["pair_resolution"]["summary"]
    assert out["n_pairs"] == 15
    # ddE spans 10 * 3 kcal/mol with ~zero pose noise: almost every pair clears the 4.07 floor and none is mis-ordered
    assert out["noise_floor_4.07"]["n_resolved"] >= 10 and out["noise_floor_4.07"]["n_resolved_wrong_order"] == 0
    assert (
        out["0"]["n_resolved"] >= out["noise_floor_4.07"]["n_resolved"] >= out["charge_sensitivity"]["n_resolved"] - 15
    )


# ------------------------------------------------------------------------------------------------ run script


def test_shared_jitter_is_one_rigid_motion_of_the_whole_series():
    run = _script("run_plb_ddE")
    rng = np.random.default_rng(0)
    base = {n: rng.normal(size=(7, 3)) * 3 + [5.0, 44.0, 51.0] for n in ("a", "b", "c")}
    poses = run.build_poses(base, base["a"].mean(0), n_poses=4, seed=7, max_deg=15, max_shift=0.5)
    for n, x in base.items():
        assert np.array_equal(poses[n][0], x)  # pose 0 is the PLB pose, untouched
    for k in (1, 2, 3):
        rot, t = run.shared_transform(7, k, 15, 0.5)
        assert np.allclose(rot @ rot.T, np.eye(3)) and np.linalg.det(rot) == pytest.approx(1.0)
        for n in base:
            assert not np.allclose(poses[n][k], base[n])
        # one motion for all ligands: the SAME (R, t) maps each ligand's pose 0 onto its pose k
        for n in base:
            piv = base["a"].mean(0)
            assert np.allclose(poses[n][k], (base[n] - piv) @ rot.T + piv + t)
        # and therefore inter-ligand vectors rotate together (a per-ligand jitter would break this)
        d0 = base["a"][0] - base["b"][0]
        dk = poses["a"][k][0] - poses["b"][k][0]
        assert np.allclose(dk, rot @ d0)
    # deterministic, and pose k does not depend on how many poses were asked for
    again = run.build_poses(base, base["a"].mean(0), n_poses=3, seed=7, max_deg=15, max_shift=0.5)
    assert np.array_equal(again["b"][2], poses["b"][2])
    other = run.build_poses(base, base["a"].mean(0), n_poses=3, seed=8, max_deg=15, max_shift=0.5)
    assert not np.allclose(other["b"][2], poses["b"][2])


def test_jitter_stays_within_its_declared_bounds():
    run = _script("run_plb_ddE")
    for k in range(1, 200):
        rot, t = run.shared_transform(1, k, 15.0, 0.5)
        angle = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(rot) - 1) / 2))))
        assert angle <= 15.0 + 1e-9 and np.abs(t).max() <= 0.5


def test_min_distance_to_charges():
    run = _script("run_plb_ddE")
    assert run.min_distance_to_charges([[0, 0, 0], [3, 0, 0]], [[0, 4, 0], [3, 0, 2]]) == pytest.approx(2.0)


@pytest.mark.needs_ferric
def test_run_task_equals_the_field_interaction_tier():
    from smeltery import Candidate, Pose
    from smeltery.model import PointCharge
    from smeltery.tiers import FieldInteraction

    run = _script("run_plb_ddE")
    symbols = ("C", "O", "H", "H", "H", "H")
    coords = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.41, 0.0, 0.0],
            [-0.36, 1.02, 0.0],
            [-0.36, -0.51, 0.89],
            [-0.36, -0.51, -0.89],
            [1.74, 0.9, 0.0],
        ]
    )
    charges = [
        PointCharge(0.8, (4.0, 0.5, 0.2)),
        PointCharge(-0.8, (-3.5, 1.0, -1.0)),
        PointCharge(0.3, (0.5, 4.0, 3.0)),
    ]
    cand = Candidate("meoh", "CO", [Pose(symbols, coords)])
    FieldInteraction(basis="sto-3g").run([cand], {"field": charges})
    run._init_worker({"m": [c.as_ferric_bohr() for c in charges]})
    base = {
        "ligand": "meoh",
        "pose": 0,
        "symbols": symbols,
        "coords": coords,
        "net_charge": 0,
        "basis": "sto-3g",
        "energy_conv": 1e-10,
        "density_conv": 1e-8,
    }
    vac = run.run_task({**base, "kind": "vac"})
    fld = run.run_task({**base, "kind": "field", "model": "m"})
    assert vac["error"] is None and fld["error"] is None and fld["model"] == "m"
    dE = (fld["energy_hartree"] - vac["energy_hartree"]) * HARTREE_TO_KCAL
    assert dE == pytest.approx(cand.per_pose["dE_int"][0], abs=1e-9)
    assert abs(dE) > 0.5  # the field is not inert, so the equality is not vacuous


def test_a_model_with_fewer_poses_is_compared_on_the_poses_both_completed():
    rec, ddg = _record(n_lig=6, n_poses=6, slope=3.0, noise=0.2, n_by_model={"A": 6, "B": 3})
    res = pm.validate_results(rec)
    assert res.n_poses_by_model == {"A": 6, "B": 3} and res.dE["A"].shape == (6, 6) and res.dE["B"].shape == (6, 3)
    out = pm.analyse(res, _fake_target(ddg), n_boot=50)
    cm = out["charge_model_comparison"]
    assert cm["n_poses_used"] == 3 and out["n_poses_by_model"] == {"A": 6, "B": 3}
    # A restricted to the first 3 poses, not A's 6, is what B is compared with
    first3 = pm.paired_ddE(res, "A", res.dE["A"][:, :3])
    assert cm["values_a"] == {n: first3[n].mean for n in res.analogues}
    assert out["models"]["B"]["per_ligand"]["L1"]["per_pose_ddE"].__len__() == 3
    assert out["models"]["A"]["per_ligand"]["L1"]["per_pose_ddE"].__len__() == 6
