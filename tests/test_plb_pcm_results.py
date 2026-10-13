# ruff: noqa: E501  (long table-cell assertions)
"""The committed PCM measurements (issue #28): provenance, the stored gas-phase energies unchanged, the statistics recompute
from the committed per-pose energies (floats to rel 1e-12, never bit for bit), a corrupted copy is refused, and the report
quotes the analysis files.

Nothing here runs quantum chemistry: the SCFs ran once (scripts/run_plb_ddE.py, docs/benchmarks/plb_pcm_plan.md).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import numpy as np
import pytest

from smeltery import plb_measure as pm
from smeltery.benchmark import load_benchmark
from smeltery.model import HARTREE_TO_KCAL
from smeltery.record import digest

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "benchmarks" / "plb" / "results"
REPORT = ROOT / "docs" / "benchmarks" / "plb_pcm.md"
CPU_CAP_HOURS = 24.0

CASES = {
    "tyk2": {
        "n_ligands": 13,
        "poses": {"amber14": 8, "amber14_pcm_water": 4, "amber14_pcm_eps4": 2},
        "solvent": {"amber14_pcm_water": "water", "amber14_pcm_eps4": 4.0},
        "base": RES / "tyk2_sto3g.json",
    },
    "cdk2": {
        "n_ligands": 9,
        "poses": {"amber14": 5, "amber14_pcm_water": 2},
        "solvent": {"amber14_pcm_water": "water"},
        "base": RES / "cdk2_sto3g.json",
    },
}


def _same(a, b, rel=1e-12):
    """Equal structure, floats equal to rel: a recomputed value can differ in the last ulp across platforms."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k], rel) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y, rel) for x, y in zip(a, b, strict=True))
    if isinstance(a, float) or isinstance(b, float):
        return a == pytest.approx(b, rel=rel, abs=1e-15, nan_ok=True)
    return a == b


@pytest.fixture(scope="module", params=sorted(CASES))
def case(request):
    name = request.param
    raw = json.loads((RES / f"{name}_sto3g_pcm.json").read_text())
    stored = json.loads((RES / f"{name}_sto3g_pcm_analysis.json").read_text())
    return {"name": name, **CASES[name], "raw": raw, "stored": stored, "res": pm.validate_results(raw)}


def test_the_results_validate_and_say_what_was_run(case):
    raw, res = case["raw"], case["res"]
    inp = raw["inputs"]
    assert res.target == case["name"] and len(res.ligands) == case["n_ligands"]
    assert res.n_poses_by_model == case["poses"] and res.models == tuple(case["poses"])
    assert res.solvent == case["solvent"]
    assert inp["field_solvent"] == {"amber14": None, **case["solvent"]}
    assert inp["basis"] == "sto-3g" and inp["cutoff_ang"] == 15.0 and inp["seed"] == 1
    assert raw["results"]["failures"] == []
    assert res.ferric["version"] == "0.1.0rc7" and res.ferric["provenance"].startswith("VERIFIED")
    assert re.fullmatch(r"[0-9a-f]{40}", inp["smeltery_commit"])
    assert inp["reused_from_base_result"]["model"] == "amber14"
    # every PCM model uses the gas model's field, byte for byte
    shas = {fp["input_sha256"] for fp in inp["field_provenance"].values()}
    assert len(shas) == 1
    for m in case["solvent"]:
        assert (
            inp["field_provenance"][m]["dielectric_eps"] == 1.0
            and inp["field_provenance"][m]["solvent"] == case["solvent"][m]
        )
        assert inp["pose_indices"][m] == inp["pose_indices"]["amber14"][: case["poses"][m]]


def test_the_gas_phase_model_is_the_merged_result_unchanged(case):
    base = json.loads(case["base"].read_text())
    raw = case["raw"]
    assert raw["results"]["E_vac_hartree"] == base["results"]["E_vac_hartree"]
    assert raw["results"]["E_field_hartree"]["amber14"] == base["results"]["E_field_hartree"]["amber14"]
    assert raw["results"]["dE_int_kcal_mol"]["amber14"] == base["results"]["dE_int_kcal_mol"]["amber14"]
    assert raw["inputs"]["pose_geometry_sha256"] == base["inputs"]["pose_geometry_sha256"]
    assert raw["inputs"]["pivot_ang"] == base["inputs"]["pivot_ang"]


def test_dE_recomputes_from_the_stored_scf_energies_against_the_alone_leg(case):
    raw, res = case["raw"], case["res"]
    e_vac = np.array([raw["results"]["E_vac_hartree"][n] for n in res.ligands])
    for m in res.models:
        n_m = res.n_poses_by_model[m]
        e_fld = np.array([raw["results"]["E_field_hartree"][m][n] for n in res.ligands])
        if m in res.solvent:
            e_ref = np.array([raw["results"]["E_alone_hartree"][m][n] for n in res.ligands])
            assert not np.allclose(e_ref, e_vac[:, :n_m], rtol=0, atol=1e-6)  # the reference leg is NOT the gas energy
        else:
            e_ref = e_vac[:, :n_m]
        assert np.allclose(res.dE[m], (e_fld - e_ref) * HARTREE_TO_KCAL, rtol=1e-12, atol=1e-9)
        assert np.isfinite(res.dE[m]).all()


def test_cpu_spend_is_inside_the_declared_cap(case):
    raw = case["raw"]
    h = raw["results"]["total_cpu_s_all_tasks_incl_failures_and_reruns"] / 3600
    expected = {"tyk2": 14.26, "cdk2": 5.65}[case["name"]]
    assert h == pytest.approx(expected, abs=0.02)
    assert 8.82 + 5.44 + 5.65 + 0.18 + 0.5 <= CPU_CAP_HOURS  # all stages, the pilot and the reserved tests/mutation CPU


def test_the_solvent_statistics_recompute_to_rel_1e12(case):
    res, stored = case["res"], case["stored"]
    target = load_benchmark().targets[case["name"]]
    names = list(res.analogues)
    x = np.array([stored["exp_ddg"][n] for n in names])
    again = pm.solvent_analysis(res, x, n_boot=stored["n_boot"], seed=stored["seed"] + 700)
    assert _same(again, stored["solvent"])
    for lg in [g for g in target.ligands if g.name in stored["exp_ddg"]]:  # an excluded ligand is not in the file
        assert stored["exp_ddg"][lg.name] == pytest.approx(lg.ddg_kcal_mol, abs=1e-12)
    # point statistics of every model, from the committed energies (bootstrap-free part of analyse)
    an = pm.analyse(res, target, n_boot=20)
    for m in res.models:
        for k, v in stored["models"][m]["point"].items():
            assert an["models"][m]["point"][k] == pytest.approx(v, rel=1e-12, abs=1e-12, nan_ok=True), (m, k)
    assert "charge_model_comparison" not in stored and "screening" not in stored


def test_the_labels_follow_the_declared_thresholds(case):
    for row in case["stored"]["solvent"]["by_model"].values():
        d, ci = row["delta_spearman_vs_gas"], row["delta_spearman_paired_bootstrap_ci"]
        assert row["labels"]["rank_change"] == pm.rank_change_label(d, ci["lo"], ci["hi"])
        assert abs(d) < pm.RANK_SHIFT_THRESHOLD  # the pre-declared outcome of this study: no shift of 0.15
        assert row["labels"]["rank_change"] == "unchanged at the level this study can detect"
        assert row["labels"]["tracks"] == "does not track at the level detectable"
        assert row["spearman_with_gas_ddE"] > 0.99
        assert row["n_ddE_sign_flips_vs_gas"] == 0 and row["n_dE_int_sign_flips_vs_gas"] == 0
        assert row["mean_dE_int_ratio_to_gas"] > 1.0  # PCM made the interaction larger, not smaller


def _refused(raw, mutate, *, fix_digest=False):
    r = copy.deepcopy(raw)
    mutate(r)
    if fix_digest:
        r["input_digest"] = digest({"inputs": r["inputs"], "tiers": r["tiers"]})
    with pytest.raises(pm.PlbResultsError):
        pm.validate_results(r)


def test_a_corrupted_copy_of_the_committed_results_is_refused(case):
    raw = case["raw"]
    lig = case["res"].analogues[2]
    _refused(raw, lambda r: r["results"]["E_alone_hartree"]["amber14_pcm_water"][lig].__setitem__(1, -1.0))
    _refused(raw, lambda r: r["results"]["dE_int_kcal_mol"]["amber14_pcm_water"][lig].__setitem__(0, 0.0))
    _refused(raw, lambda r: r["results"].pop("E_alone_hartree"))
    _refused(raw, lambda r: r["inputs"]["field_solvent"].__setitem__("amber14_pcm_water", None), fix_digest=True)
    _refused(
        raw,
        lambda r: r["inputs"]["field_provenance"]["amber14_pcm_water"].__setitem__("solvent", "dmso"),
        fix_digest=True,
    )
    _refused(raw, lambda r: r["inputs"].pop("smeltery_commit"), fix_digest=True)
    _refused(raw, lambda r: r["results"]["failures"].append({"ligand": lig, "pose": 0, "error": "SCF"}))


def test_the_report_quotes_the_analysis_files():
    doc = REPORT.read_text()
    t = json.loads((RES / "tyk2_sto3g_pcm_analysis.json").read_text())["solvent"]["by_model"]
    c = json.loads((RES / "cdk2_sto3g_pcm_analysis.json").read_text())["solvent"]["by_model"]
    old = json.loads((RES / "tyk2_sto3g_analysis.json").read_text())["screening"]["by_eps"]
    section = doc.split("### Implicit solvation against the gas-phase model", 1)[1].split("\n## ", 1)[0]
    rows = {
        ln.split("|")[1].strip(): [x.strip() for x in ln.strip().strip("|").split("|")]
        for ln in section.splitlines()
        if ln.startswith("| amber14_pcm")
    }
    for m, r in t.items():
        cells = rows[m]
        ci = r["bootstrap_ci_over_ligands"]
        assert cells[3] == f"{r['point']['spearman']:+.3f}" and cells[4] == f"{r['spearman_p_two_sided']:.4f}"
        assert cells[5] == "[{lo:+.2f}, {hi:+.2f}]".format(**ci["spearman"])
        assert cells[7] == f"{r['point']['slope']:+.2f}" and cells[9] == f"{r['point']['sd_ratio']:.2f}"
        assert cells[10] == f"{r['gas_point']['spearman']:+.3f}" and cells[11] == f"{r['delta_spearman_vs_gas']:+.3f}"
        assert cells[12] == "[{lo:+.2f}, {hi:+.2f}]".format(**r["delta_spearman_paired_bootstrap_ci"])
        assert cells[13] == f"{r['spearman_with_gas_ddE']:+.3f}"
        assert cells[14] == f"{r['mean_dE_int_kcal_mol']:+.2f}" and cells[15] == f"{r['gas_mean_dE_int_kcal_mol']:+.2f}"
        assert cells[16] == f"{r['mean_dE_int_ratio_to_gas']:.3f}"
    w = t["amber14_pcm_water"]
    cw = c["amber14_pcm_water"]
    for text in (
        f"{w['point']['spearman']:+.3f} (permutation p {w['spearman_p_two_sided']:.4f})",
        f"{w['delta_spearman_vs_gas']:+.3f} (paired bootstrap 95% CI [{w['delta_spearman_paired_bootstrap_ci']['lo']:+.2f}, {w['delta_spearman_paired_bootstrap_ci']['hi']:+.2f}]",
        f"{cw['point']['spearman']:+.3f} | {cw['spearman_p_two_sided']:.4f}",
        f"{cw['mean_dE_int_kcal_mol']:+.2f}",
        f"{cw['gas_mean_dE_int_kcal_mol']:+.2f}",
    ):
        assert text in doc, text
    for e, row in old.items():  # the uniform-eps comparison rows are the committed screening rows
        cells = [
            x.strip()
            for ln in doc.splitlines()
            if ln.startswith(f"| field charges / {e} ") or (e == "1" and ln.startswith("| gas phase (eps = 1)"))
            for x in ln.strip().strip("|").split("|")
        ]
        if e in ("1", "2", "4", "8"):
            assert cells[1] == f"{row['point']['spearman']:+.3f}" and cells[2] == f"{row['spearman_p_two_sided']:.4f}"
            assert cells[3] == f"{row['point']['slope']:+.2f}" and cells[4] == f"{row['point']['sd_ratio']:.2f}"
    for text in (
        "Answer in four lines",
        "What the data do not support",
        "Deviations from the plan",
        "Decisions for the maintainer",
    ):
        assert text in doc
