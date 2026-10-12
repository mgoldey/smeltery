# ruff: noqa: E501  (report-quoting assertions)
"""The committed tyk2 AMBER-vs-CHARMM36 comparison (issue #29, second system): the AMBER side is the stored run's, bit for bit;
the statistics recompute from the committed per-pose values; a corrupted copy is refused; the numbers quoted in the
report, in the plan's verdict rule and in docs/limits.md are the numbers in the analysis file.

Nothing here runs quantum chemistry.

Mutation checks done by hand when this file was written (each made at least one test below fail, then reverted):
editing one CHARMM36 energy in the results, replacing one stored AMBER energy, swapping the CHARMM36 PQR digest, quoting a
different floor in the report, quoting a different floor in docs/limits.md, changing the verdict rule's factor-2 band, and
making `dielectric_family` treat a model on its own PQR at eps = 1 as a family member.
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
RESULTS = RES / "tyk2_sto3g_charmm36.json"
ANALYSIS = RES / "tyk2_sto3g_charmm36_analysis.json"
MERGED = RES / "tyk2_sto3g.json"
MERGED_ANALYSIS = RES / "tyk2_sto3g_analysis.json"
CDK2_ANALYSIS = RES / "cdk2_sto3g_analysis.json"
REPORT = ROOT / "docs" / "benchmarks" / "plb_tyk2_charmm.md"
LIMITS = ROOT / "docs" / "limits.md"
CPU_CAP_HOURS = 6.0


def _same(a, b, rel=1e-12):
    """Equal structure, floats equal to rel: a recomputed p-value can differ in the last ulp across platforms."""
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k], rel) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_same(x, y, rel) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return a == pytest.approx(b, rel=rel, nan_ok=True)
    return a == b


@pytest.fixture(scope="module")
def raw():
    return json.loads(RESULTS.read_text())


@pytest.fixture(scope="module")
def merged():
    return json.loads(MERGED.read_text())


@pytest.fixture(scope="module")
def res():
    return pm.load_results(RESULTS)


@pytest.fixture(scope="module")
def target():
    return load_benchmark().targets["tyk2"]


@pytest.fixture(scope="module")
def stored():
    return json.loads(ANALYSIS.read_text())


def test_the_record_validates_and_says_what_was_run(res, raw):
    inp = raw["inputs"]
    assert res.target == "tyk2" and res.models == ("amber14", "charmm36") and len(res.analogues) == 12
    assert res.n_poses_by_model == {"amber14": 8, "charmm36": 8}
    assert inp["dropped_poses"] == {} and raw["results"]["failures"] == [] and raw["results"]["dropped_failures"] == []
    assert inp["pose_indices"]["charmm36"] == list(range(8))  # every SCF converged, nothing dropped (plan section 4)
    assert res.ferric["version"] == "0.1.0rc7" and res.ferric["provenance"].startswith("VERIFIED")
    assert re.fullmatch(r"[0-9a-f]{40}", inp["smeltery_commit"])
    assert (
        len(inp["commands"]) == 1
        and "--field charmm36=" in inp["commands"][0]
        and "--run charmm36" in inp["commands"][0]
    )
    assert "amber14=" not in inp["commands"][0]  # the AMBER SCFs were not rerun


def test_the_amber_side_is_the_stored_run_bit_for_bit_and_geometries_are_identical(raw, merged):
    r, m = raw["results"], merged["results"]
    assert r["E_vac_hartree"] == m["E_vac_hartree"]
    assert r["E_field_hartree"]["amber14"] == m["E_field_hartree"]["amber14"]
    assert r["dE_int_kcal_mol"]["amber14"] == m["dE_int_kcal_mol"]["amber14"]
    assert r["min_dist_to_field_ang"]["amber14"] == m["min_dist_to_field_ang"]["amber14"]
    for k in (
        "pose_geometry_sha256",
        "pivot_ang",
        "seed",
        "jitter_deg",
        "jitter_ang",
        "basis",
        "cutoff_ang",
        "n_poses",
    ):
        assert raw["inputs"][k] == merged["inputs"][k], k
    assert raw["inputs"]["field_provenance"]["amber14"] == merged["inputs"]["field_provenance"]["amber14"]
    reused = raw["inputs"]["reused_from_base_result"]
    import hashlib

    assert reused["file_sha256"] == hashlib.sha256(MERGED.read_bytes()).hexdigest()
    assert reused["model"] == "amber14" and reused["ferric"] == merged["ferric"] == raw["ferric"]


def test_the_two_fields_differ_only_in_the_force_field(raw, target):
    fa, fc = (raw["inputs"]["field_provenance"][m] for m in ("amber14", "charmm36"))
    pa, pc = (raw["inputs"]["prep_provenance"][m] for m in ("amber14", "charmm36"))
    manifest_pdb = next(f for f in target.files if f.kind == "protein_pdb")
    assert pa["input_sha256"] == pc["input_sha256"] == manifest_pdb.sha256
    assert fa["input_sha256"] != fc["input_sha256"] and fc["input_sha256"] == pc["output_sha256"]
    assert fa["center_ang"] == fc["center_ang"] and fa["cutoff_ang"] == fc["cutoff_ang"] == 15.0
    assert fa["n_charges"] == fc["n_charges"] == 1013
    assert pc["force_field"] == "charmm36" and list(pc["force_field_files"]) == ["charmm36.xml"]
    assert pa["waters_dropped"] is pc["waters_dropped"] is True
    assert pa["n_waters_dropped_inside_cutoff"] == pc["n_waters_dropped_inside_cutoff"] == 2
    assert pa["deleted_residues_min_distance_to_center_ang"] == pc["deleted_residues_min_distance_to_center_ang"]
    assert pa["histidine_templates_matched"] == {"HID": 6, "HIE": 7}
    assert pc["histidine_templates_matched"] == {"HSD": 6, "HSE": 7}
    assert pa["net_charge_in_cutoff_sphere_e"] == pytest.approx(1.4018, abs=1e-9)
    assert pc["net_charge_in_cutoff_sphere_e"] == pytest.approx(0.895, abs=1e-9)
    assert fc["dielectric_eps"] == 1.0 and fc["scaled_charge_sum_e"] == pytest.approx(0.895, abs=1e-9)  # unscreened
    assert pc["openmm_version"] == "8.6.1"


def test_dE_recomputes_from_the_stored_scf_energies(raw, res):
    e_vac = np.array([raw["results"]["E_vac_hartree"][n] for n in res.ligands])
    for m in res.models:
        e_fld = np.array([raw["results"]["E_field_hartree"][m][n] for n in res.ligands])
        assert np.allclose(res.dE[m], (e_fld - e_vac) * HARTREE_TO_KCAL, rtol=0, atol=1e-6)


def test_cpu_spend_is_inside_the_cap(raw):
    total_h = raw["results"]["total_cpu_s_all_tasks_incl_failures_and_reruns"] / 3600
    assert 2.9 < total_h < 3.2  # 104 new field SCFs only: the AMBER and vacuum energies cost 0 here
    assert total_h <= 5.5 + 0.2  # the run's cap plus SCFs in flight
    assert total_h + 0.06 + 1.0 <= CPU_CAP_HOURS  # plus the pilot and at most one CPU-hour of tests and analysis
    cpu = raw["results"]["task_cpu_s"]
    assert len(cpu) == 312 and sum(v > 0 for v in cpu.values()) == 104  # reused records carry 0
    assert all(k.startswith("field:charmm36:") for k, v in cpu.items() if v > 0)


def test_the_amber_endpoint_of_the_merged_run_is_reproduced(res, target, stored):
    merged = json.loads(MERGED_ANALYSIS.read_text())["models"]["amber14"]
    assert stored["models"]["amber14"]["point"] == pytest.approx(merged["point"], abs=1e-12)
    assert _same(
        stored["models"]["amber14"]["permutation"]["spearman"]["p_two_sided"],
        merged["permutation"]["spearman"]["p_two_sided"],
    )


def test_the_analysis_recomputes_including_the_charge_comparison(res, target, stored):
    an = pm.analyse(res, target, seed=stored["seed"], n_boot=stored["n_boot"])
    assert "screening" not in an and "screening" not in stored  # no eps family here
    assert _same(an["charge_model_comparison"], stored["charge_model_comparison"])
    for m in res.models:
        assert an["models"][m]["point"] == pytest.approx(stored["models"][m]["point"], abs=1e-9, nan_ok=True)
        assert _same(an["models"][m]["pair_resolution"]["summary"], stored["models"][m]["pair_resolution"]["summary"])


def test_the_comparison_matches_an_independent_computation(res, raw):
    """Sign flips, pair flips, Spearman and the floor from the per-pose dE alone, not through plb_measure."""
    names = list(res.analogues)
    assert list(res.ligands[1:]) == names
    ddE = {}
    for m in res.models:
        dE = np.array([raw["results"]["dE_int_kcal_mol"][m][n] for n in res.ligands])
        ddE[m] = np.array([(dE[i + 1] - dE[0]).mean() for i in range(len(names))])
    a, b = ddE["amber14"], ddE["charmm36"]
    cm = json.loads(ANALYSIS.read_text())["charge_model_comparison"]
    assert cm["n"] == 12 and cm["n_pairs"] == 66
    assert cm["n_sign_flips_of_ddE"] == int((np.sign(a) != np.sign(b)).sum()) == 0
    flips = sum(np.sign(a[i] - a[j]) != np.sign(b[i] - b[j]) for i in range(12) for j in range(i + 1, 12))
    assert cm["n_pairs_order_flipped"] == flips == 0
    assert cm["charge_sensitivity_floor_kcal_mol"] == pytest.approx((b - a).max() - (b - a).min(), abs=1e-9)
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    assert cm["spearman_between_models"] == pytest.approx(np.corrcoef(ra, rb)[0, 1], abs=1e-9)
    da, db = (np.array([raw["results"]["dE_int_kcal_mol"][m][n] for n in res.ligands]) for m in ("amber14", "charmm36"))
    assert cm["raw_dE_int_mean_shift_b_minus_a_kcal_mol"] == pytest.approx((db - da).mean(), abs=1e-9)


def _verdict(cm: dict, cdk2_floor: float) -> dict:
    """The plan's section 3 rule, written out: 'agree' needs all four criteria; the floor criterion is a factor-2 band."""
    ratio = cm["charge_sensitivity_floor_kcal_mol"] / cdk2_floor
    return {
        "spearman": cm["spearman_between_models"] >= 0.90,
        "sign_flips": cm["n_sign_flips_of_ddE"] <= 1,
        "pair_flips": cm["n_pairs_order_flipped"] <= 6,
        "floor_within_factor_2": 0.63 <= ratio <= 2.52,
    }


def test_the_plans_verdict_rule_gives_the_verdict_the_report_states(stored):
    cdk2 = json.loads(CDK2_ANALYSIS.read_text())["charge_model_comparison"]
    assert (cdk2["n_sign_flips_of_ddE"], cdk2["n_pairs_order_flipped"], cdk2["n"], cdk2["n_pairs"]) == (0, 1, 8, 28)
    assert (
        round(cdk2["spearman_between_models"], 3) == 0.976
        and round(cdk2["charge_sensitivity_floor_kcal_mol"], 2) == 1.26
    )
    v = _verdict(stored["charge_model_comparison"], cdk2["charge_sensitivity_floor_kcal_mol"])
    assert v == {"spearman": True, "sign_flips": True, "pair_flips": True, "floor_within_factor_2": False}
    assert not all(v.values())  # => "the two systems disagree", on the floor only
    doc = REPORT.read_text()
    assert (
        "disagree" in doc
        and "outside the factor-2 band" in doc
        and "agree on the ranking and disagree on the floor" in doc
    )
    # the rule is sensitive: a floor of 1.0 would have been inside the band
    assert _verdict({**stored["charge_model_comparison"], "charge_sensitivity_floor_kcal_mol": 1.0}, 1.26)[
        "floor_within_factor_2"
    ]


def test_the_report_quotes_the_analysis_files(stored):
    doc = REPORT.read_text()
    cm = stored["charge_model_comparison"]
    cdk2 = json.loads(CDK2_ANALYSIS.read_text())["charge_model_comparison"]
    for text in (
        f"sign flips of ddE {cm['n_sign_flips_of_ddE']}/{cm['n']}",
        f"pair order flipped {cm['n_pairs_order_flipped']}/{cm['n_pairs']}",
        f"Spearman between models {cm['spearman_between_models']:+.3f}",
        f"Kendall {cm['kendall_between_models']:+.3f}",
        f"charge_sensitivity floor {cm['charge_sensitivity_floor_kcal_mol']:.2f} kcal/mol",
        f"{cm['raw_dE_int_mean_shift_b_minus_a_kcal_mol']:+.2f} +/- {cm['raw_dE_int_sd_of_shift_kcal_mol']:.2f}",
        f"raw dE_int Spearman across ligand-pose {cm['raw_dE_int_spearman_across_ligand_pose']:+.3f}",
    ):
        assert text in doc, text
    side = (
        f"| tyk2 | {cm['n']} | 8 | {cm['n_sign_flips_of_ddE']}/{cm['n']} | {cm['n_pairs_order_flipped']}/{cm['n_pairs']} | "
        f"{cm['spearman_between_models']:+.3f} | {cm['charge_sensitivity_floor_kcal_mol']:.2f} |"
    )
    side_cdk2 = (
        f"| cdk2 | {cdk2['n']} | 5 | {cdk2['n_sign_flips_of_ddE']}/{cdk2['n']} | {cdk2['n_pairs_order_flipped']}/{cdk2['n_pairs']} | "
        f"{cdk2['spearman_between_models']:+.3f} | {cdk2['charge_sensitivity_floor_kcal_mol']:.2f} |"
    )
    assert side in doc and side_cdk2 in doc
    ratio = cm["charge_sensitivity_floor_kcal_mol"] / cdk2["charge_sensitivity_floor_kcal_mol"]
    assert f"{ratio:.2f} times cdk2" in doc
    # every per-ligand shift row of the report is the stored delta
    rows = dict(re.findall(r"\| (lig_\w+) \| ([+-]\d\.\d\d) \|", doc.split("### Per-ligand shift", 1)[1]))
    assert sorted(rows) == sorted(cm["deltas_b_minus_a"])
    for n, d in cm["deltas_b_minus_a"].items():
        assert rows[n] == f"{d:+.2f}", n
    mx, mn = max(cm["deltas_b_minus_a"].values()), min(cm["deltas_b_minus_a"].values())
    assert f"{mx:+.2f} minus {mn:+.2f}" in doc
    a, c = stored["models"]["amber14"], stored["models"]["charmm36"]
    assert f"{c['point']['spearman']:+.3f}, p {c['permutation']['spearman']['p_two_sided']:.4f}" in doc
    assert f"{a['point']['spearman']:+.3f}, p {a['permutation']['spearman']['p_two_sided']:.4f}" in doc
    assert f"{a['pose_noise']['mean_paired_sem']:.2f} at 8 poses" in doc
    assert (
        f"| {cm['raw_dE_int_mean_shift_b_minus_a_kcal_mol']:+.2f} +/- {cm['raw_dE_int_sd_of_shift_kcal_mol']:.2f} |"
        in doc
    )
    assert cm["charge_sensitivity_floor_kcal_mol"] > a["pose_noise"]["mean_paired_sem"]  # "larger than the pose SEM"
    assert f"{c['min_dist_to_field_ang']['min']:.2f} A" in doc
    for text in (
        "Answer in four lines",
        "What this does not support",
        "Deviations from the plan",
        "Decisions for the maintainer",
    ):
        assert text in doc


def test_limits_row_l11_quotes_the_analysis_file(stored):
    row = next(ln for ln in LIMITS.read_text().splitlines() if ln.startswith("| L11 |"))
    cm = stored["charge_model_comparison"]
    assert f"{cm['charge_sensitivity_floor_kcal_mol']:.2f} kcal/mol" in row
    assert f"{cm['n_sign_flips_of_ddE']} of {cm['n']} sign flips" in row
    assert f"{cm['n_pairs_order_flipped']} of {cm['n_pairs']} pair-order flips" in row
    assert f"Spearman between models {cm['spearman_between_models']:+.3f}" in row
    assert "tyk2_sto3g_charmm36_analysis.json" in row and "plb_tyk2_charmm.md" in row


def _refused(raw, mutate, *, fix_digest=False):
    r = copy.deepcopy(raw)
    mutate(r)
    if fix_digest:
        r["input_digest"] = digest({"inputs": r["inputs"], "tiers": r["tiers"]})
    with pytest.raises(pm.PlbResultsError):
        pm.validate_results(r)


def test_a_corrupted_copy_is_refused(raw):
    _refused(raw, lambda r: r["results"]["dE_int_kcal_mol"]["charmm36"]["lig_jmc_27"].__setitem__(2, 0.0))
    _refused(raw, lambda r: r["results"]["E_field_hartree"]["amber14"]["lig_ejm_31"].__setitem__(0, -1.0))
    _refused(
        raw,
        lambda r: r["inputs"]["field_provenance"]["charmm36"].__setitem__("input_sha256", "0" * 64),
        fix_digest=True,
    )
    _refused(raw, lambda r: r["inputs"].pop("smeltery_commit"), fix_digest=True)
    _refused(raw, lambda r: r["results"]["failures"].append({"ligand": "lig_jmc_27", "pose": 0, "error": "SCF"}))


def test_a_second_force_field_at_eps_one_is_not_a_screening_family(res, raw):
    assert pm.dielectric_family(res) == {}  # AMBER and CHARMM36 are two charge models, each unscreened on its own PQR
    # but a screened model that shares no PQR with an unscreened one is still refused, and so are two unscreened on one PQR
    r = copy.deepcopy(raw)
    r["inputs"]["field_provenance"]["charmm36"]["dielectric_eps"] = 2.0
    r["input_digest"] = digest({"inputs": r["inputs"], "tiers": r["tiers"]})
    with pytest.raises(pm.PlbResultsError):
        pm.dielectric_family(pm.validate_results(r))
    r = copy.deepcopy(raw)
    pa = r["inputs"]["prep_provenance"]
    pa["charmm36"]["output_sha256"] = pa["amber14"]["output_sha256"]
    r["inputs"]["field_provenance"]["charmm36"]["input_sha256"] = pa["amber14"]["output_sha256"]
    r["input_digest"] = digest({"inputs": r["inputs"], "tiers": r["tiers"]})
    with pytest.raises(pm.PlbResultsError, match="unscreened"):
        pm.dielectric_family(pm.validate_results(r))


def test_same_helper_tolerates_an_ulp_but_not_a_real_difference():
    assert _same({"p": [0.14803218069275834]}, {"p": [0.14803218069275842]})
    assert not _same({"p": 0.148}, {"p": 0.149})
    assert not _same({"n": 41}, {"n": 42})
    assert not _same({"a": 1.0}, {"b": 1.0})
