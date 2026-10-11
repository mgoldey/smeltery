"""The screening treatment (issue #28): charges divided by a uniform eps, and its pre-declared analysis.

Mutation checks done by hand when this file was written (each made at least one test below fail, then reverted):
multiplying the charges by eps instead of dividing, ignoring eps, moving the charge positions, taking the unscreened
model's poses instead of the common poses, counting ddE sign flips against the wrong model, accepting a family
without an eps = 1 member, and accepting screened models that do not share one PQR.
"""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import numpy as np
import pytest
from test_plb_measure import _fake_target, _record

from smeltery import plb_measure as pm
from smeltery.model import HARTREE_TO_KCAL
from smeltery.record import digest

ROOT = Path(__file__).resolve().parents[1]


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FIELD = [(0.8, 7.5, 0.9, 0.4), (-0.8, -6.6, 1.9, -1.9), (0.3, 0.9, 7.6, 5.7)]


def test_scale_field_divides_charges_and_never_moves_them():
    run = _script("run_plb_ddE")
    assert run.scale_field(FIELD, 1.0) == FIELD  # the trivial limit: exactly the same numbers
    out = run.scale_field(FIELD, 4.0)
    assert [c[0] for c in out] == pytest.approx([0.2, -0.2, 0.075], abs=1e-15)
    assert [c[1:] for c in out] == [c[1:] for c in FIELD]
    for bad in (0.0, -2.0, float("nan")):
        with pytest.raises(ValueError):
            run.scale_field(FIELD, bad)


@pytest.mark.needs_ferric
def test_screened_scf_anchors_eps_one_exactly_and_eps_infinity_to_vacuum():
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
    field = [(q, x, y, z) for q, x, y, z in FIELD]
    run._init_worker({f"e{e:g}": run.scale_field(field, e) for e in (1.0, 2.0, 1e9)} | {"raw": field})
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
    vac = run.run_task({**base, "kind": "vac"})["energy_hartree"]

    def dE(model):
        r = run.run_task({**base, "kind": "field", "model": model})
        assert r["error"] is None
        return (r["energy_hartree"] - vac) * HARTREE_TO_KCAL

    raw, one, two, inf = dE("raw"), dE("e1"), dE("e2"), dE("e1e+09")
    assert one == pytest.approx(raw, abs=1e-9)  # eps = 1 reproduces the vacuum-field interaction energy
    assert abs(raw) > 0.5  # the field is not inert, so the anchors are not vacuous
    assert abs(inf) < 1e-4  # eps -> infinity: the field vanishes and so does the interaction
    assert 0.2 < two / raw < 0.8 and two != pytest.approx(raw, abs=1e-3)  # a real, graded response


def _screened(eps_by_model, *, nonlinear=None, flip=None, n_by_model=None, same_pqr=True, slope=3.0):
    """A record whose model m is the base model's dE_int divided by eps (first order), with optional distortions."""
    models = tuple(eps_by_model)
    rec, ddg = _record(n_lig=6, n_poses=6, models=models, slope=slope, noise=0.3, n_by_model=n_by_model, seed=4)
    inp, res = rec["inputs"], rec["results"]
    base = next(m for m, e in eps_by_model.items() if e == 1.0) if 1.0 in eps_by_model.values() else models[0]
    base_dE = {n: np.array(v) for n, v in res["dE_int_kcal_mol"][base].items()}
    for m, e in eps_by_model.items():
        n_m = inp["n_poses_by_model"][m]
        new = {}
        for n, v in base_dE.items():
            d = v[:n_m] / e
            if nonlinear and m == nonlinear[0]:
                d = d * nonlinear[1]  # a ligand-independent distortion: every energy of this model, ddE included
            new[n] = d
        if flip and m == flip[0]:
            par = new["L0"]
            new[flip[1]] = 2 * par - new[flip[1]]  # reverse this analogue's ddE about the parent
        res["dE_int_kcal_mol"][m] = {n: v.tolist() for n, v in new.items()}
        res["E_field_hartree"][m] = {
            n: (np.array(res["E_vac_hartree"][n][:n_m]) + v / HARTREE_TO_KCAL).tolist() for n, v in new.items()
        }
        inp["field_provenance"][m]["dielectric_eps"] = e
        if same_pqr:
            inp["field_provenance"][m]["input_sha256"] = "c" * 64
            inp["prep_provenance"][m]["output_sha256"] = "c" * 64
    rec["input_digest"] = digest({"inputs": inp, "tiers": rec["tiers"]})
    return rec, ddg


EPS = {"A": 1.0, "A2": 2.0, "A4": 4.0}
NPOSE = {"A": 6, "A2": 3, "A4": 3}


def test_a_first_order_screening_divides_every_statistic_by_eps_and_keeps_every_rank():
    rec, ddg = _screened(EPS, n_by_model=NPOSE)
    res = pm.validate_results(rec)
    assert pm.dielectric_family(res) == EPS
    out = pm.analyse(res, _fake_target(ddg), n_boot=100)
    assert "charge_model_comparison" not in out  # a screened copy is not a second charge model
    sc = out["screening"]
    assert sc["base_model"] == "A" and sc["n_poses_common"] == 3 and sc["eps_values"] == [1.0, 2.0, 4.0]
    r1, r2, r4 = (sc["by_eps"][k] for k in ("1", "2", "4"))
    assert r1["n_poses"] == r2["n_poses"] == r4["n_poses"] == 3
    # the unscreened row is computed on the SAME three poses as the screened ones, not on its own six
    first3 = pm.paired_ddE(res, "A", res.dE["A"][:, :3])
    assert r1["ddE_mean"] == pytest.approx({n: m.mean for n, m in first3.items()})
    assert r2["point"]["slope"] == pytest.approx(r1["point"]["slope"] / 2) and r4["point"]["slope"] == pytest.approx(
        r1["point"]["slope"] / 4
    )
    assert r4["point"]["sd_ratio"] == pytest.approx(r1["point"]["sd_ratio"] / 4)
    assert r4["mean_dE_int_ratio_to_unscreened"] == pytest.approx(0.25)
    assert r4["slope_measured_over_first_order"] == pytest.approx(1.0)
    for r in (r1, r2, r4):
        assert r["spearman_with_unscreened_ddE"] == pytest.approx(1.0)
        assert r["point"]["spearman"] == pytest.approx(r1["point"]["spearman"])
        assert r["n_ddE_sign_flips_vs_unscreened"] == 0 and r["n_dE_int_sign_flips_vs_unscreened"] == 0


def test_the_slope_crossing_is_the_log_log_interpolation_and_none_when_never_bracketed():
    rec, ddg = _screened(EPS, n_by_model=NPOSE, slope=3.0)
    out = pm.analyse(pm.validate_results(rec), _fake_target(ddg), n_boot=50)["screening"]
    s = {float(k): v["point"]["slope"] for k, v in out["by_eps"].items()}
    # first order: slope(eps) = s1 / eps exactly, so slope = 1 at eps = s1
    assert out["slope_crosses_1_at_eps"] == pytest.approx(s[1.0], rel=1e-9)
    # a series whose slope at eps = 1 is already below 1 never crosses from above
    rec2, ddg2 = _screened(EPS, n_by_model=NPOSE, slope=0.5)
    out2 = pm.analyse(pm.validate_results(rec2), _fake_target(ddg2), n_boot=50)["screening"]
    assert out2["by_eps"]["1"]["point"]["slope"] < 1.0 and out2["slope_crosses_1_at_eps"] is None


def test_nonlinearity_and_sign_flips_are_reported_against_the_unscreened_model():
    rec, ddg = _screened(EPS, n_by_model=NPOSE, nonlinear=("A4", 2.0), flip=("A2", "L3"))
    out = pm.analyse(pm.validate_results(rec), _fake_target(ddg), n_boot=50)["screening"]["by_eps"]
    assert out["4"]["slope_measured_over_first_order"] == pytest.approx(2.0)  # twice the first-order expectation
    assert out["2"]["n_ddE_sign_flips_vs_unscreened"] == 1 and out["4"]["n_ddE_sign_flips_vs_unscreened"] == 0
    assert out["2"]["spearman_with_unscreened_ddE"] < 1.0
    assert out["1"]["n_ddE_sign_flips_vs_unscreened"] == 0  # the reference never flips against itself


def test_a_malformed_screening_family_is_refused():
    base, ddg = _screened(EPS, n_by_model=NPOSE)
    for mutate in (
        lambda r: r["inputs"]["field_provenance"]["A"].__setitem__("dielectric_eps", 2.0),  # no unscreened member
        lambda r: r["inputs"]["field_provenance"]["A2"].__setitem__("dielectric_eps", 1.0),  # two of them
        lambda r: r["inputs"]["field_provenance"]["A4"].__setitem__("dielectric_eps", 0.0),
        lambda r: r["inputs"]["field_provenance"]["A4"].__setitem__("dielectric_eps", -3.0),
        lambda r: (  # a different PQR, consistently recorded: it loads, but is not a screened copy of the same field
            r["inputs"]["prep_provenance"]["A4"].__setitem__("output_sha256", "d" * 64),
            r["inputs"]["field_provenance"]["A4"].__setitem__("input_sha256", "d" * 64),
        ),
    ):
        r = copy.deepcopy(base)
        mutate(r)
        r["input_digest"] = digest({"inputs": r["inputs"], "tiers": r["tiers"]})
        try:
            res = pm.validate_results(r)
        except pm.PlbResultsError:
            continue  # refused already at load
        with pytest.raises(pm.PlbResultsError):
            pm.dielectric_family(res)


def test_results_without_screening_have_no_screening_section():
    rec, ddg = _record(n_lig=6, n_poses=5)
    out = pm.analyse(pm.validate_results(rec), _fake_target(ddg), n_boot=20)
    assert "screening" not in out and "charge_model_comparison" in out


def test_the_same_source_secondary_uses_only_the_parents_publication():
    from types import SimpleNamespace

    rec, ddg = _record(n_lig=6, n_poses=5, slope=3.0, noise=0.2)
    tgt = _fake_target(ddg)
    for lg in tgt.ligands:  # L0 (parent) to L3 from publication P, L4 and L5 from Q
        lg.measurement = SimpleNamespace(sources=[SimpleNamespace(id="Q" if lg.name in ("L4", "L5") else "P")])
    out = pm.analyse(pm.validate_results(rec), tgt, n_boot=50)["models"]["A"]["secondary_same_source_as_parent"]
    assert out["ligands"] == ["L1", "L2", "L3"] and out["point"]["n"] == 3
    x = np.array([ddg[n] for n in out["ligands"]])
    dde = pm.paired_ddE(pm.validate_results(rec), "A")
    y = np.array([dde[n].mean for n in out["ligands"]])
    assert out["point"]["slope"] == pytest.approx(pm.ols(x, y)["slope"])
    # a target whose ligands all share the parent's publication has no such secondary
    for lg in tgt.ligands:
        lg.measurement = SimpleNamespace(sources=[SimpleNamespace(id="P")])
    assert "secondary_same_source_as_parent" not in pm.analyse(pm.validate_results(rec), tgt, n_boot=50)["models"]["A"]


def test_dE_int_sign_flips_count_ligand_poses_whose_interaction_changes_sign():
    rec, ddg = _screened(EPS, n_by_model=NPOSE, nonlinear=("A4", -1.0))  # every dE_int of A4 reversed
    out = pm.analyse(pm.validate_results(rec), _fake_target(ddg), n_boot=50)["screening"]["by_eps"]
    assert out["4"]["n_dE_int_sign_flips_vs_unscreened"] == 6 * 3 == out["4"]["n_dE_int_values"]
    assert out["2"]["n_dE_int_sign_flips_vs_unscreened"] == 0 and out["1"]["n_dE_int_sign_flips_vs_unscreened"] == 0
    assert out["4"]["mean_dE_int_ratio_to_unscreened"] < 0
