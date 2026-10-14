"""The committed distance-dielectric measurement (issue #28): provenance, the stored gas-phase energies unchanged, the
statistics recompute from the committed per-pose energies (rel 1e-12, never bit for bit), and the report and limits row
quote the analysis file. Nothing here runs quantum chemistry: the SCFs ran once (docs/benchmarks/plb_ddd_plan.md)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from smeltery import plb_measure as pm
from smeltery.benchmark import load_benchmark
from smeltery.model import HARTREE_TO_KCAL

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "benchmarks" / "plb" / "results"
RAW = json.loads((RES / "tyk2_sto3g_ddd.json").read_text())
STORED = json.loads((RES / "tyk2_sto3g_ddd_analysis.json").read_text())
RESULTS = pm.validate_results(RAW)


def _same(a, b, rel=1e-12):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k], rel) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y, rel) for x, y in zip(a, b, strict=True))
    if isinstance(a, float) or isinstance(b, float):
        return a == pytest.approx(b, rel=rel, abs=1e-15, nan_ok=True)
    return a == b


def test_the_results_say_what_was_run():
    inp = RAW["inputs"]
    assert RESULTS.models == ("amber14", "amber14_ddd1", "amber14_ddd4")
    assert RESULTS.n_poses_by_model == {"amber14": 8, "amber14_ddd1": 4, "amber14_ddd4": 4}
    assert RESULTS.distance_dielectric == {"amber14_ddd1": 1.0, "amber14_ddd4": 4.0} and RESULTS.solvent == {}
    assert inp["basis"] == "sto-3g" and inp["cutoff_ang"] == 15.0 and inp["seed"] == 1
    assert RAW["results"]["failures"] == [] and inp["reused_from_base_result"]["model"] == "amber14"
    assert len({fp["input_sha256"] for fp in inp["field_provenance"].values()}) == 1  # one PQR for all three models
    assert RESULTS.ferric["version"] == "0.1.0rc7"
    assert RAW["results"]["total_cpu_s_all_tasks_incl_failures_and_reruns"] / 3600 == pytest.approx(3.51, abs=0.02)


def test_the_gas_phase_model_is_the_merged_result_unchanged():
    base = json.loads((RES / "tyk2_sto3g.json").read_text())
    assert RAW["results"]["E_vac_hartree"] == base["results"]["E_vac_hartree"]
    assert RAW["results"]["E_field_hartree"]["amber14"] == base["results"]["E_field_hartree"]["amber14"]
    assert RAW["results"]["dE_int_kcal_mol"]["amber14"] == base["results"]["dE_int_kcal_mol"]["amber14"]


def test_dE_recomputes_from_the_stored_scf_energies_against_the_vacuum_leg():
    e_vac = np.array([RAW["results"]["E_vac_hartree"][n] for n in RESULTS.ligands])
    for m in ("amber14_ddd1", "amber14_ddd4"):
        e_fld = np.array([RAW["results"]["E_field_hartree"][m][n] for n in RESULTS.ligands])
        assert np.allclose(RESULTS.dE[m], (e_fld - e_vac[:, :4]) * HARTREE_TO_KCAL, rtol=1e-12, atol=1e-9)


def test_the_statistics_recompute_to_rel_1e12():
    x = np.array([STORED["exp_ddg"][n] for n in RESULTS.analogues])
    again = pm.distance_dielectric_analysis(RESULTS, x, n_boot=STORED["n_boot"], seed=STORED["seed"] + 800)
    assert _same(again, STORED["distance_dielectric"])
    an = pm.analyse(RESULTS, load_benchmark().targets["tyk2"], n_boot=20)
    for m in RESULTS.models:
        for k, v in STORED["models"][m]["point"].items():
            assert an["models"][m]["point"][k] == pytest.approx(v, rel=1e-12, abs=1e-12, nan_ok=True), (m, k)
    assert "screening" not in STORED and "charge_model_comparison" not in STORED


def test_the_labels_follow_the_declared_thresholds():
    for r in STORED["distance_dielectric"]["by_model"].values():
        d = r["delta_spearman_vs_gas"]
        assert abs(d) < pm.RANK_SHIFT_THRESHOLD and r["labels"]["rank_change"].startswith("unchanged")
        assert r["labels"]["rank_change"] == pm.rank_change_label(
            d, r["delta_spearman_paired_bootstrap_ci"]["lo"], r["delta_spearman_paired_bootstrap_ci"]["hi"]
        )


def test_a_corrupted_copy_is_refused():
    bad = copy.deepcopy(RAW)
    bad["inputs"]["field_distance_dielectric"]["amber14_ddd1"] = 2.0
    with pytest.raises(pm.PlbResultsError):
        pm.validate_results(bad)
    bad = copy.deepcopy(RAW)
    bad["results"]["dE_int_kcal_mol"]["amber14_ddd4"]["lig_ejm_31"][0] += 1.0
    with pytest.raises(pm.PlbResultsError):
        pm.validate_results(bad)


def test_the_report_and_the_limits_row_quote_the_analysis_file():
    rep = (ROOT / "docs/benchmarks/plb_ddd.md").read_text()
    lim = (ROOT / "docs/limits.md").read_text()
    rows = STORED["distance_dielectric"]["by_model"]
    for r in rows.values():
        assert f"{r['point']['spearman']:+.3f}" in rep
        assert f"{r['delta_spearman_vs_gas']:+.3f}" in rep and f"{r['delta_spearman_vs_gas']:+.3f}" in lim
        assert f"{r['spearman_with_gas_ddE']:+.3f}" in rep
    assert "tyk2_sto3g_ddd_analysis.json" in lim and "| L16 |" in lim
