"""The committed tyk2 measurement: provenance complete, statistics recompute from the committed per-pose values,
a corrupted copy is refused, and the numbers quoted in the report are the numbers in the analysis file.

Nothing here runs quantum chemistry: the SCFs ran once (scripts/run_plb_ddE.py); their energies are in the results file.

Mutation checks done by hand when this file was written (each made at least one test below fail, then reverted):
editing one screened energy, changing a recorded eps, swapping the field digest, dropping the smeltery commit, quoting
a number the analysis file does not hold, and reading the wrong eps row of the screening table.
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
RESULTS = ROOT / "benchmarks" / "plb" / "results" / "tyk2_sto3g.json"
ANALYSIS = ROOT / "benchmarks" / "plb" / "results" / "tyk2_sto3g_analysis.json"
REPORT = ROOT / "docs" / "benchmarks" / "plb_tyk2.md"
CPU_CAP_HOURS = 20.0
EPS = {"amber14": 1.0, "amber14_eps2": 2.0, "amber14_eps4": 4.0, "amber14_eps8": 8.0}


@pytest.fixture(scope="module")
def raw():
    return json.loads(RESULTS.read_text())


@pytest.fixture(scope="module")
def res():
    return pm.load_results(RESULTS)


@pytest.fixture(scope="module")
def target():
    return load_benchmark().targets["tyk2"]


@pytest.fixture(scope="module")
def stored():
    return json.loads(ANALYSIS.read_text())


def test_the_committed_results_validate_and_say_what_was_run(res, raw):
    inp = raw["inputs"]
    assert res.target == "tyk2" and res.reference == "lig_ejm_42" and res.models == tuple(EPS)
    assert inp["basis"] == "sto-3g" and inp["cutoff_ang"] == 15.0 and inp["seed"] == 1
    assert inp["jitter_deg"] == 15.0 and inp["jitter_ang"] == 0.5 and inp["n_poses"] == 8
    assert inp["excluded_ligands"] == {}  # the plan: every element is covered, every ligand is neutral
    assert len(res.ligands) == 13 and res.ligands[0] == "lig_ejm_42" and len(res.analogues) == 12
    assert inp["dropped_poses"] == {} and raw["results"]["failures"] == [] and raw["results"]["dropped_failures"] == []
    assert res.n_poses_by_model == {"amber14": 8, "amber14_eps2": 4, "amber14_eps4": 4, "amber14_eps8": 4}
    assert inp["pose_indices"]["amber14"] == list(range(8))
    for m in ("amber14_eps2", "amber14_eps4", "amber14_eps8"):
        assert inp["pose_indices"][m] == [0, 1, 2, 3]  # the first 4 kept poses, as the plan declared
    assert res.ferric["version"] == "0.1.0rc7" and res.ferric["provenance"].startswith("VERIFIED")
    assert re.fullmatch(r"[0-9a-f]{40}", inp["smeltery_commit"])
    assert inp["commands"] and all("run_plb_ddE.py" in c for c in inp["commands"])


def test_the_field_is_one_pqr_identified_by_digest_and_screening_is_recorded(raw, target):
    inp = raw["inputs"]
    manifest_pdb = next(f for f in target.files if f.kind == "protein_pdb")
    shas = {fp["input_sha256"] for fp in inp["field_provenance"].values()}
    assert len(shas) == 1  # one PQR, so a screened model is the same field with its charges divided
    qsum = inp["field_provenance"]["amber14"]["scaled_charge_sum_e"]
    for m, e in EPS.items():
        fp, pp = inp["field_provenance"][m], inp["prep_provenance"][m]
        assert fp["dielectric_eps"] == e and inp["field_eps"][m] == e
        assert fp["scaled_charge_sum_e"] == pytest.approx(qsum / e, rel=1e-12)  # charges divided, nothing else
        assert pp["input_sha256"] == manifest_pdb.sha256 and pp["output_sha256"] == fp["input_sha256"]
        assert fp["cutoff_ang"] == 15.0 and fp["n_charges"] == 1013
        assert set(pp["deleted_residues_min_distance_to_center_ang"]) == {"ACE:A:889", "NME:A:1178"}
        assert all(
            d > fp["cutoff_ang"] + pp["margin_ang"] for d in pp["deleted_residues_min_distance_to_center_ang"].values()
        )
        assert pp["waters_dropped"] is True and pp["n_waters_dropped_inside_cutoff"] == 2
        assert "NO protonation is changed" in pp["protonation"] and pp["openmm_version"] == "8.6.1"
        assert pp["force_field"] == "amber14"
    assert len({tuple(fp["center_ang"]) for fp in inp["field_provenance"].values()}) == 1


def test_the_series_is_the_manifests_and_the_plans_checks_hold(raw, target):
    assert len(target.ligands) == 13 and target.reference == "lig_ejm_42"
    assert {lg.name for lg in target.ligands} == set(raw["results"]["E_vac_hartree"])
    counts = raw["inputs"]["ligand_atom_counts"]
    assert min(counts.values()) == 32 and max(counts.values()) == 42 and len(counts) == 13  # counted from the SDF
    assert len(raw["inputs"]["pose_geometry_sha256"]) == 13


def test_dE_recomputes_from_the_stored_scf_energies(raw, res):
    e_vac = np.array([raw["results"]["E_vac_hartree"][n] for n in res.ligands])
    for m in res.models:
        e_fld = np.array([raw["results"]["E_field_hartree"][m][n] for n in res.ligands])
        n_m = res.n_poses_by_model[m]
        assert np.allclose(res.dE[m], (e_fld - e_vac[:, :n_m]) * HARTREE_TO_KCAL, rtol=0, atol=1e-6)
        assert np.isfinite(res.dE[m]).all()


def test_cpu_spend_is_inside_the_declared_caps(raw):
    total_h = raw["results"]["total_cpu_s_all_tasks_incl_failures_and_reruns"] / 3600
    assert 11.0 < total_h < 11.3  # the SCFs: 6.98 (stage 1) + 4.18 (stage 2)
    assert total_h <= 16.5 + 0.2  # the plan's stage-2 cap plus the SCFs in flight when it would have been reached
    assert total_h + 0.14 + 1.0 <= CPU_CAP_HOURS  # plus the pilot and (at most) one CPU-hour of tests and analysis


def test_the_primary_statistics_recompute_exactly(res, target, stored):
    """Everything that does not depend on the bootstrap RNG recomputes to rounding error."""
    an = pm.analyse(res, target, n_boot=50)
    assert "charge_model_comparison" not in an and "charge_model_comparison" not in stored
    for m in res.models:
        a, b = an["models"][m], stored["models"][m]
        for k, v in b["point"].items():
            assert a["point"][k] == pytest.approx(v, abs=1e-9, nan_ok=True), (m, k)
        for k in ("spearman", "kendall", "pearson", "slope"):
            assert a["permutation"][k]["p_two_sided"] == pytest.approx(b["permutation"][k]["p_two_sided"], abs=1e-12)
            assert a["permutation"][k]["p_one_sided_positive"] == pytest.approx(
                b["permutation"][k]["p_one_sided_positive"], abs=1e-12
            )
        assert a["permutation"]["exact"] is False and a["permutation"]["n_permutations"] == 200_000  # MC at n = 12
        assert a["pair_resolution"]["summary"] == b["pair_resolution"]["summary"]
        assert a["pose_noise"] == pytest.approx(b["pose_noise"], abs=1e-9)
        for n, v in b["per_ligand"].items():
            assert a["per_ligand"][n]["per_pose_ddE"] == pytest.approx(v["per_pose_ddE"], abs=1e-9)
    for k in stored["baselines"]:
        assert an["baselines"][k]["point"] == pytest.approx(stored["baselines"][k]["point"], abs=1e-9, nan_ok=True)
    s_a, s_b = an["screening"], stored["screening"]
    assert s_a["eps_values"] == s_b["eps_values"] == [1.0, 2.0, 4.0, 8.0] and s_a["n_poses_common"] == 4
    assert s_a["slope_crosses_1_at_eps"] == s_b["slope_crosses_1_at_eps"] is None
    for e, row in s_b["by_eps"].items():
        got = s_a["by_eps"][e]
        for k, v in row.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                assert got[k] == pytest.approx(v, abs=1e-9, nan_ok=True), (e, k)
        assert got["point"] == pytest.approx(row["point"], abs=1e-9, nan_ok=True)
        assert got["ddE_mean"] == pytest.approx(row["ddE_mean"], abs=1e-9)


def test_the_full_analysis_including_bootstrap_cis_recomputes(res, target, stored):
    an = pm.analyse(res, target, seed=stored["seed"], n_boot=stored["n_boot"])
    for m in res.models:
        for stat, ci in stored["models"][m]["bootstrap_ci_over_ligands"].items():
            got = an["models"][m]["bootstrap_ci_over_ligands"][stat]
            assert got["lo"] == pytest.approx(ci["lo"], abs=1e-9) and got["hi"] == pytest.approx(ci["hi"], abs=1e-9)
        for key in ("pose_bootstrap_ci", "joint_pose_and_ligand_bootstrap_ci"):
            for stat, ci in stored["models"][m][key].items():
                assert an["models"][m][key][stat]["lo"] == pytest.approx(ci["lo"], abs=1e-9)
                assert an["models"][m][key][stat]["hi"] == pytest.approx(ci["hi"], abs=1e-9)
    for e, row in stored["screening"]["by_eps"].items():
        for stat, ci in row["bootstrap_ci_over_ligands"].items():
            assert an["screening"]["by_eps"][e]["bootstrap_ci_over_ligands"][stat] == ci
    assert an["power"] == stored["power"]


def test_the_experimental_ddg_in_the_analysis_is_the_manifests(stored, target):
    for lg in target.ligands:
        assert stored["exp_ddg"][lg.name] == pytest.approx(lg.ddg_kcal_mol, abs=1e-12)
    assert stored["n_ligands_analogues"] == 12 and stored["excluded_ligands"] == {}


def test_the_plans_numbers_are_the_analysis_files(stored):
    """Detectable effect and the answer's headline figures, as the plan and the report state them."""
    pw = stored["power"]
    assert pw["critical_abs_spearman"] == pytest.approx(0.587, abs=5e-4) and pw["n"] == 12
    assert [round(100 * v) for v in pw["power_by_true_pearson_r"].values()] == [14, 33, 68, 86, 98]
    a = stored["models"]["amber14"]
    assert a["permutation"]["n_permutations"] == 200_000 and a["permutation"]["spearman"]["p_two_sided"] > 0.05
    lo, hi = (a["bootstrap_ci_over_ligands"]["spearman"][k] for k in ("lo", "hi"))
    assert lo < 0 < hi  # the plan's criteria for "tracks" fail: the CI contains 0
    assert a["point"]["kendall"] > 0 and a["point"]["spearman"] > 0


def _refused(raw, mutate, *, fix_digest=False):
    r = copy.deepcopy(raw)
    mutate(r)
    if fix_digest:
        r["input_digest"] = digest({"inputs": r["inputs"], "tiers": r["tiers"]})
    with pytest.raises(pm.PlbResultsError):
        pm.validate_results(r)


def test_a_corrupted_copy_of_the_committed_results_is_refused(raw):
    # an edited screened energy, an edited vacuum energy, a swapped field digest, a dropped field, an edited input
    _refused(raw, lambda r: r["results"]["dE_int_kcal_mol"]["amber14_eps4"]["lig_jmc_27"].__setitem__(2, 0.0))
    _refused(
        raw,
        lambda r: r["results"]["E_vac_hartree"]["lig_ejm_31"].__setitem__(
            0, r["results"]["E_vac_hartree"]["lig_ejm_31"][0] + 1e-3
        ),
    )
    _refused(
        raw,
        lambda r: r["inputs"]["field_provenance"]["amber14_eps2"].__setitem__("input_sha256", "0" * 64),
        fix_digest=True,
    )
    _refused(raw, lambda r: r["inputs"].pop("smeltery_commit"), fix_digest=True)
    _refused(raw, lambda r: r["inputs"]["field_provenance"]["amber14_eps2"].__setitem__("dielectric_eps", 3.0))
    _refused(raw, lambda r: r["ferric"].__setitem__("version", None))
    _refused(raw, lambda r: r["results"]["failures"].append({"ligand": "lig_jmc_27", "pose": 0, "error": "SCF"}))


def test_a_screening_family_with_a_changed_eps_is_refused_by_the_family_check(raw):
    r = copy.deepcopy(raw)
    r["inputs"]["field_provenance"]["amber14"]["dielectric_eps"] = 2.0  # no unscreened member any more
    r["input_digest"] = digest({"inputs": r["inputs"], "tiers": r["tiers"]})
    with pytest.raises(pm.PlbResultsError, match="unscreened"):
        pm.dielectric_family(pm.validate_results(r))


def test_the_report_quotes_the_analysis_file(stored):
    doc = REPORT.read_text()
    a = stored["models"]["amber14"]
    p = a["point"]
    ci = a["bootstrap_ci_over_ligands"]["spearman"]
    sci = a["bootstrap_ci_over_ligands"]["slope"]
    for text in (
        f"{p['spearman']:+.3f}",
        f"{ci['lo']:+.2f}",
        f"{ci['hi']:+.2f}",
        f"{a['permutation']['spearman']['p_two_sided']:.4f}",
        f"{a['permutation']['spearman']['p_one_sided_positive']:.4f}",
        f"{p['kendall']:+.3f}",
        f"{a['permutation']['kendall']['p_two_sided']:.4f}",
        f"{p['slope']:+.2f}",
        f"{sci['lo']:+.2f}",
        f"{sci['hi']:+.2f}",
        f"{p['sd_ratio']:.2f}",
        f"{p['rmse_fit']:.2f}",
        f"{p['mae_raw']:.2f}",
        f"{a['pose_noise']['mean_paired_sem']:.2f}",
        f"{a['pose_noise']['mean_unpaired_sem']:.2f}",
        f"{a['pose_noise']['reliability_of_ddE_ranking']:.2f}",
        f"{a['sign_binomial_two_sided_p']:.3f}",
    ):
        assert text in doc, f"the report does not quote {text} from the analysis file"
    for k, b in stored["baselines"].items():
        assert (
            f"{b['point']['spearman']:+.3f}" in doc and f"{b['permutation']['spearman']['p_two_sided']:.4f}" in doc
        ), k
    ss = a["secondary_same_source_as_parent"]
    assert f"{ss['point']['spearman']:+.3f}" in doc and f"{ss['permutation']['spearman']['p_two_sided']:.4f}" in doc
    s = a["pair_resolution"]["summary"]
    for text in (
        f"{s['0']['n_resolved']} are resolved",
        f"{s['0']['n_resolved_correct_order']} in the experimental order and {s['0']['n_resolved_wrong_order']} not",
        f"{s['n_pairs_correct_sign_all']} of {s['n_pairs']} pairs",
        f"{s['n_pairs_exp_resolved_2sigma']} of the {s['n_pairs']} pairs",
    ):
        assert text in doc, text
    sc = stored["screening"]["by_eps"]
    section = doc.split("### Screening:", 1)[1]
    table = {}
    for ln in section.splitlines():
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if re.fullmatch(r"(1|2|4|8)", cells[0]) and len(cells) == 14:
            table[cells[0]] = cells
    assert sorted(table) == ["1", "2", "4", "8"]
    for e, row in sc.items():  # the reported row is the stored row, column by column (not any matching substring)
        c = table[e]
        assert c[1] == f"{row['point']['spearman']:+.3f}" and c[2] == f"{row['spearman_p_two_sided']:.4f}"
        assert c[4] == f"{row['point']['slope']:+.2f}" and c[6] == f"{row['point']['sd_ratio']:.2f}"
        assert c[7] == f"{row['mean_dE_int_kcal_mol']:+.2f}" and c[8] == f"{row['mean_dE_int_ratio_to_unscreened']:.3f}"
        assert c[11] == f"{row['spearman_with_unscreened_ddE']:+.3f}"
        assert (
            c[12] == f"{row['n_ddE_sign_flips_vs_unscreened']}/12"
            and c[13] == f"{row['n_dE_int_sign_flips_vs_unscreened']}/52"
        )
        assert c[3] == "[{lo:+.2f}, {hi:+.2f}]".format(**row["bootstrap_ci_over_ligands"]["spearman"])
        assert c[5] == "[{lo:+.2f}, {hi:+.2f}]".format(**row["bootstrap_ci_over_ligands"]["slope"])
    for e in ("2", "4", "8"):
        assert f"{sc[e]['slope_measured_over_first_order']:.2f}" in doc
    flips = [sc[e]["n_ddE_sign_flips_vs_unscreened"] for e in ("2", "4", "8")]
    assert flips == [1, 4, 4] and "1, 4 and 4 of 12" in doc
    assert sc["4"]["n_dE_int_sign_flips_vs_unscreened"] == 0 and sc["4"]["n_dE_int_values"] == 52
    shift = max(abs(sc[e]["point"]["spearman"] - sc["1"]["point"]["spearman"]) for e in ("2", "4", "8"))
    assert f"{shift:.2f}" in doc and shift < 0.15  # the plan's label for "rank correlation unchanged"
    for text in ("Answer in four lines", "What this does not show", "Deviations", "Decisions for the maintainer"):
        assert text in doc
