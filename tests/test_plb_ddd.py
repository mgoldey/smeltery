# ruff: noqa: E501  (long assertion lines)
"""The distance-dependent dielectric treatment (issue #28): the scaling, the runner, the loader and the analysis.

Design: docs/benchmarks/plb_ddd_plan.md. SCFs here are on a 6-atom methanol (no PLB ligand), so the file runs in about a minute.
"""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest
from test_plb_measure import _fake_target
from test_plb_pcm import (  # noqa: F401  (runner: fixture)
    COORDS,
    FIELD,
    SYMBOLS,
    _args,
    _script,
    _spec,
    _with_solvent,
    runner,
)

from smeltery import plb_measure as pm
from smeltery.model import ANGSTROM_TO_BOHR, HARTREE_TO_KCAL
from smeltery.record import digest

# ---------------------------------------------------------------------------------------------- the scaling


def _bohr(x_ang, y=0.0, z=0.0):
    return (x_ang * ANGSTROM_TO_BOHR, y * ANGSTROM_TO_BOHR, z * ANGSTROM_TO_BOHR)


def test_each_charge_is_divided_by_max_1_slope_times_its_nearest_atom_distance():
    lig = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0]])  # two atoms: the NEAREST one counts
    charges = [(1.0, *_bohr(0.5)), (1.0, *_bohr(2.0)), (-2.0, *_bohr(7.0)), (0.6, *_bohr(5.0, 3.0, 4.0))]
    s1 = pm.distance_dielectric_field(charges, lig, 1.0)
    s4 = pm.distance_dielectric_field(charges, lig, 4.0)
    # distances: 0.5 (floor, eps 1), 2.0, 3.0 (nearest is the atom at 10 A), 5.0 (to (0,0,0): sqrt(25+9+16)... see below)
    d3 = np.sqrt((10 - 7.0) ** 2)
    d4 = min(np.sqrt(5.0**2 + 3.0**2 + 4.0**2), np.sqrt(5.0**2 + 3.0**2 + 4.0**2))
    assert [q for q, *_ in s1] == pytest.approx([1.0 / 1.0, 1.0 / 2.0, -2.0 / d3, 0.6 / d4])
    assert [q for q, *_ in s4] == pytest.approx([1.0 / 2.0, 1.0 / 8.0, -2.0 / (4 * d3), 0.6 / (4 * d4)])


def test_positions_are_untouched_and_the_count_and_order_are_kept():
    lig = np.array([[0.0, 0.0, 0.0]])
    out = pm.distance_dielectric_field(FIELD, lig, 4.0)
    assert [c[1:] for c in out] == [c[1:] for c in FIELD] and len(out) == len(FIELD)


def test_charges_inside_the_floor_distance_are_not_screened():
    lig = np.array([[0.0, 0.0, 0.0]])
    out = pm.distance_dielectric_field([(0.7, *_bohr(0.2))], lig, 4.0)  # 4 * 0.2 = 0.8 < 1
    assert out[0][0] == pytest.approx(0.7)


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_a_bad_slope_is_refused(bad):
    with pytest.raises(ValueError, match="slope"):
        pm.distance_dielectric_field(FIELD, COORDS, bad)


# ---------------------------------------------------------------------------------------------- the worker (ferric)


@pytest.mark.needs_ferric
def test_the_field_leg_with_a_slope_is_run_rhf_in_the_distance_scaled_field():
    import ferric

    run = _script("run_plb_ddE")
    run._init_worker({"m": FIELD})
    mol = ferric.Molecule.from_xyz_string(run.xyz_text(SYMBOLS, COORDS, "x"), charge=0)
    bs = ferric.BasisSet.bundled("sto-3g")
    kw = {"energy_conv": 1e-10, "density_conv": 1e-8}
    plain = run.run_task(_spec(kind="field", model="m"))
    scaled = run.run_task(_spec(kind="field", model="m", ddd_slope=1.0))
    want = ferric.run_rhf(mol, bs, point_charges=pm.distance_dielectric_field(FIELD, COORDS, 1.0), **kw).energy
    assert scaled["error"] is None and scaled["energy_hartree"] == want
    assert scaled["energy_hartree"] != plain["energy_hartree"]
    assert plain["energy_hartree"] == ferric.run_rhf(mol, bs, point_charges=FIELD, **kw).energy  # no slope: unscreened


def test_a_distance_dielectric_with_a_solvent_or_a_charge_divisor_or_an_unknown_model_is_refused(runner, tmp_path):  # noqa: F811
    with pytest.raises(SystemExit, match="own treatment"):
        _args(runner, tmp_path, "--ddd", "w=1", "--eps", "w=2", "--run", "w", "--max-cpu-hours", "1")
    with pytest.raises(SystemExit, match="own treatment"):
        _args(runner, tmp_path, "--ddd", "w=1", "--solvent", "w=water", "--run", "w", "--max-cpu-hours", "1")
    with pytest.raises(SystemExit, match="not a --field model"):
        _args(runner, tmp_path, "--ddd", "zz=1", "--run", "w", "--max-cpu-hours", "1")
    with pytest.raises(SystemExit, match="positive"):
        _args(runner, tmp_path, "--ddd", "w=0", "--run", "w", "--max-cpu-hours", "1")


@pytest.mark.needs_ferric
def test_the_runner_records_the_slope_and_assembles_a_model_that_loads_and_analyses(runner, tmp_path):  # noqa: F811
    rc = _args(runner, tmp_path, "--ddd", "w=1", "--run", "vac", "gas", "w", "--max-cpu-hours", "1")
    assert rc == 0
    out = tmp_path / "out"
    result = tmp_path / "res.json"
    assert runner.main(["assemble", "--out", str(out), "--result", str(result)]) == 0
    raw = json.loads(result.read_text())
    assert raw["inputs"]["field_distance_dielectric"] == {"gas": None, "w": 1.0}
    assert raw["inputs"]["field_provenance"]["w"]["distance_dielectric_slope"] == 1.0
    res = pm.validate_results(raw)
    assert res.distance_dielectric == {"w": 1.0} and pm.dielectric_family(res) == {}
    recs = [json.loads(ln) for ln in (out / "tasks.jsonl").read_text().splitlines()]
    by = {(r["kind"], r.get("model"), r["ligand"], r["pose"]): r["energy_hartree"] for r in recs}
    n = "L1"
    # the same ligand pose in the screened and the unscreened field gives different energies (the field depends on the pose)
    assert by[("field", "w", n, 0)] != by[("field", "gas", n, 0)]
    ddg = {"L0": 0.0, "L1": -1.0, "L2": 0.4, "L3": 1.1, "L4": -0.3, "L5": 0.8}
    an = pm.analyse(res, _fake_target(ddg), n_boot=50)
    assert "charge_model_comparison" not in an and "screening" not in an and "solvent" not in an
    row = an["distance_dielectric"]["by_model"]["w"]
    assert row["distance_dielectric_slope"] == 1.0 and row["gas_model"] == "gas" and row["solvent"] is None
    for k in range(2):  # dE_int is (E_field - E_vac): the unscreened vacuum leg, as for every gas-phase model
        want = (by[("field", "w", n, k)] - by[("vac", None, n, k)]) * HARTREE_TO_KCAL
        assert raw["results"]["dE_int_kcal_mol"]["w"][n][k] == pytest.approx(want, rel=1e-12)


# ---------------------------------------------------------------------------------------------- the loader and analysis


def _with_ddd(n_s=6, *, factor=0.5, slope=1.0, flip=None):
    """A record with an unscreened model "A" and a distance-dielectric model "D" whose dE_int is A's times `factor`."""
    rec, ddg = _with_solvent(n_s=n_s, factor=factor, solvent="water")
    inp, res = rec["inputs"], rec["results"]
    inp["field_solvent"] = {"A": None, "S": None}
    inp["field_provenance"]["S"]["solvent"] = None
    rec["tiers"][0].pop("solvent_by_model")
    res.pop("E_alone_hartree")
    # "S" is now a gas-phase-style model whose dE_int equals (E_field - E_vac)
    n_vac = len(next(iter(res["E_vac_hartree"].values())))
    for n, v in res["dE_int_kcal_mol"]["S"].items():
        res["E_field_hartree"]["S"][n] = [res["E_vac_hartree"][n][k] + v[k] / HARTREE_TO_KCAL for k in range(len(v))]
    assert n_vac >= n_s
    inp["field_distance_dielectric"] = {"A": None, "S": slope}
    inp["field_provenance"]["A"]["distance_dielectric_slope"] = None
    inp["field_provenance"]["S"]["distance_dielectric_slope"] = slope
    rec["input_digest"] = digest({"inputs": inp, "tiers": rec["tiers"]})
    return rec, ddg


def test_a_valid_distance_dielectric_record_loads():
    rec, _ = _with_ddd()
    res = pm.validate_results(rec)
    assert res.distance_dielectric == {"S": 1.0} and res.solvent == {}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda inp: inp["field_distance_dielectric"].update(S=-1.0),
        lambda inp: inp["field_distance_dielectric"].update(S=float("nan")),
        lambda inp: inp["field_distance_dielectric"].update(Q=2.0),
        lambda inp: inp["field_provenance"]["S"].update(distance_dielectric_slope=2.0),
    ],
)
def test_a_corrupted_distance_dielectric_record_is_refused(mutate):
    rec, _ = _with_ddd()
    mutate(rec["inputs"])
    _redigest(rec)
    with pytest.raises(pm.PlbResultsError):
        pm.validate_results(rec)


def test_a_distance_dielectric_model_is_neither_a_screening_family_nor_a_second_charge_model():
    rec, ddg = _with_ddd()
    res = pm.validate_results(rec)
    assert pm.dielectric_family(res) == {}
    out = pm.analyse(res, _fake_target(ddg), n_boot=60)
    assert "charge_model_comparison" not in out and "screening" not in out and "distance_dielectric" in out


def test_the_analysis_compares_with_the_unscreened_model_on_the_poses_both_completed_and_reports_the_planted_change():
    rec, ddg = _with_ddd(n_s=3, factor=0.5)
    res = pm.validate_results(rec)
    x = np.array([ddg[n] for n in res.analogues])
    row = pm.distance_dielectric_analysis(res, x, n_boot=200, seed=3)["by_model"]["S"]
    assert row["n_poses"] == 3 and row["gas_model"] == "A" and row["distance_dielectric_slope"] == 1.0
    assert row["point"]["slope"] == pytest.approx(row["gas_point"]["slope"] * 0.5)
    assert row["mean_dE_int_ratio_to_gas"] == pytest.approx(0.5)
    assert row["delta_spearman_vs_gas"] == pytest.approx(0.0)
    assert row["labels"]["rank_change"] == "unchanged at the level this study can detect"


def _redigest(rec):
    rec["input_digest"] = digest({"inputs": rec["inputs"], "tiers": rec["tiers"]})


def test_a_model_on_another_field_is_refused_at_load_and_a_divisor_is_refused_by_the_comparison():
    rec, ddg = _with_ddd()
    rec["inputs"]["prep_provenance"]["S"]["output_sha256"] = "f" * 64
    _redigest(rec)
    with pytest.raises(pm.PlbResultsError, match="PQR"):  # the loader's own check; the comparison repeats it
        pm.validate_results(rec)
    rec2, _ = _with_ddd()
    rec2["inputs"]["field_provenance"]["S"]["dielectric_eps"] = 2.0
    _redigest(rec2)
    res2 = pm.validate_results(copy.deepcopy(rec2))
    x = np.array([ddg[n] for n in res2.analogues])
    with pytest.raises(pm.PlbResultsError, match="must not also"):
        pm.distance_dielectric_analysis(res2, x, n_boot=20, seed=1)


def test_no_distance_dielectric_means_no_section():
    rec, ddg = _with_solvent()
    res = pm.validate_results(rec)
    assert pm.distance_dielectric_analysis(res, np.zeros(5), n_boot=10, seed=1) == {}
    assert "distance_dielectric" not in pm.analyse(res, _fake_target(ddg), n_boot=40)
