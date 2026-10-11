"""The committed cdk2 measurement: provenance complete, statistics recompute from the committed per-pose values,
a corrupted copy is refused, and the numbers quoted in the report are the numbers in the analysis file.

Nothing here runs quantum chemistry: the SCFs ran once (scripts/run_plb_ddE.py); their energies are in the results file.
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
RESULTS = ROOT / "benchmarks" / "plb" / "results" / "cdk2_sto3g.json"
ANALYSIS = ROOT / "benchmarks" / "plb" / "results" / "cdk2_sto3g_analysis.json"
REPORT = ROOT / "docs" / "benchmarks" / "plb_cdk2.md"
CPU_CAP_HOURS = 14.0


@pytest.fixture(scope="module")
def raw():
    return json.loads(RESULTS.read_text())


@pytest.fixture(scope="module")
def res():
    return pm.load_results(RESULTS)


@pytest.fixture(scope="module")
def target():
    return load_benchmark().targets["cdk2"]


@pytest.fixture(scope="module")
def stored():
    return json.loads(ANALYSIS.read_text())


def test_the_committed_results_validate_and_say_what_was_run(res, raw):
    inp = raw["inputs"]
    assert res.target == "cdk2" and res.reference == "lig_1h1q" and res.models == ("amber14", "charmm36")
    assert inp["basis"] == "sto-3g" and inp["cutoff_ang"] == 15.0 and inp["seed"] == 1
    assert inp["jitter_deg"] == 15.0 and inp["jitter_ang"] == 0.5 and inp["n_poses"] == 6
    assert inp["excluded_ligands"].keys() == {"lig_17"}  # plan amendment A1
    assert res.ligands[0] == "lig_1h1q" and len(res.ligands) == 9
    assert res.n_poses_by_model["amber14"] == 5  # poses 1 to 5; pose 0 dropped for every ligand (deviation D2)
    assert inp["pose_indices"]["amber14"] == [1, 2, 3, 4, 5] and set(inp["dropped_poses"]) == {"0"}
    assert res.n_poses_by_model["charmm36"] >= 3  # below 3 the plan says #29 is NOT measured
    assert res.ferric["version"] and res.ferric["provenance"].startswith("VERIFIED")
    assert re.fullmatch(r"[0-9a-f]{40}", inp["smeltery_commit"])
    assert inp["commands"] and all("run_plb_ddE.py" in c for c in inp["commands"])


def test_the_field_is_identified_by_digest_and_the_prep_decisions_are_recorded(raw, target):
    inp = raw["inputs"]
    manifest_pdb = next(f for f in target.files if f.kind == "protein_pdb")
    for m, fp in inp["field_provenance"].items():
        pp = inp["prep_provenance"][m]
        assert pp["input_sha256"] == manifest_pdb.sha256  # the PLB protein of the manifest, byte for byte
        assert pp["output_sha256"] == fp["input_sha256"]  # the PQR the SCFs read is the PQR the tool wrote
        assert fp["cutoff_ang"] == 15.0 and fp["n_charges"] == 1046
        assert set(pp["deleted_residues_min_distance_to_center_ang"]) == {
            "ACE:A:-1",
            "ACE:B:174",
            "NME:A:297",
            "NME:B:434",
            "TPO:A:160",
        }
        assert all(
            d > fp["cutoff_ang"] + pp["margin_ang"] for d in pp["deleted_residues_min_distance_to_center_ang"].values()
        )
        assert pp["waters_dropped"] is True and pp["n_waters_dropped_inside_cutoff"] == 1
        assert "NO protonation is changed" in pp["protonation"] and pp["openmm_version"] == "8.6.1"
    ff = {m: inp["prep_provenance"][m]["force_field"] for m in inp["models"]}
    assert ff == {"amber14": "amber14", "charmm36": "charmm36"}
    centres = {tuple(fp["center_ang"]) for fp in inp["field_provenance"].values()}
    assert len(centres) == 1  # one centre, hence one sphere, for every ligand and both models


def test_dE_recomputes_from_the_stored_scf_energies(raw, res):
    e_vac = np.array([raw["results"]["E_vac_hartree"][n] for n in res.ligands])
    for m in res.models:
        e_fld = np.array([raw["results"]["E_field_hartree"][m][n] for n in res.ligands])
        n_m = res.n_poses_by_model[m]
        assert np.allclose(res.dE[m], (e_fld - e_vac[:, :n_m]) * HARTREE_TO_KCAL, rtol=0, atol=1e-6)
        assert np.isfinite(res.dE[m]).all()


def test_cpu_spend_is_inside_the_declared_cap(raw):
    total_h = raw["results"]["total_cpu_s_all_tasks_incl_failures_and_reruns"] / 3600
    assert 0 < total_h <= 12.6 + 0.4  # the plan's stage-2 cap plus the SCFs in flight when it was reached
    assert total_h + 0.3 <= CPU_CAP_HOURS  # plus about 0.3 CPU-h spent on timing, tests and the aborted launch


def test_the_primary_statistics_recompute_exactly(res, target, stored):
    """Everything that does not depend on the bootstrap RNG recomputes to rounding error."""
    an = pm.analyse(res, target, n_boot=50)
    for m in res.models:
        a, b = an["models"][m], stored["models"][m]
        for k, v in b["point"].items():
            assert a["point"][k] == pytest.approx(v, abs=1e-9, nan_ok=True), (m, k)
        for k in ("spearman", "kendall", "pearson", "slope"):
            assert a["permutation"][k]["p_two_sided"] == pytest.approx(b["permutation"][k]["p_two_sided"], abs=1e-12)
            assert a["permutation"][k]["p_one_sided_positive"] == pytest.approx(
                b["permutation"][k]["p_one_sided_positive"], abs=1e-12
            )
        assert a["pair_resolution"]["summary"] == b["pair_resolution"]["summary"]
        assert a["pose_noise"] == pytest.approx(b["pose_noise"], abs=1e-9)
        for n, v in b["per_ligand"].items():
            assert a["per_ligand"][n]["per_pose_ddE"] == pytest.approx(v["per_pose_ddE"], abs=1e-9)
    cm_a, cm_b = an["charge_model_comparison"], stored["charge_model_comparison"]
    for k, v in cm_b.items():
        if isinstance(v, dict) and k in ("deltas_b_minus_a", "values_a", "values_b", "paired_sem_b_over_a"):
            assert cm_a[k] == pytest.approx(v, abs=1e-9, nan_ok=True), k
        elif isinstance(v, (int, float)):
            assert cm_a[k] == pytest.approx(v, abs=1e-9, nan_ok=True), k
    for k in stored["baselines"]:
        assert an["baselines"][k]["point"] == pytest.approx(stored["baselines"][k]["point"], abs=1e-9, nan_ok=True)


def test_the_full_analysis_including_bootstrap_cis_recomputes(res, target, stored):
    an = pm.analyse(res, target, seed=stored["seed"], n_boot=stored["n_boot"])
    for m in res.models:
        for stat, ci in stored["models"][m]["bootstrap_ci_over_ligands"].items():
            got = an["models"][m]["bootstrap_ci_over_ligands"][stat]
            assert got["lo"] == pytest.approx(ci["lo"], abs=0.05) and got["hi"] == pytest.approx(ci["hi"], abs=0.05)
        for key in ("pose_bootstrap_ci", "joint_pose_and_ligand_bootstrap_ci"):
            for stat, ci in stored["models"][m][key].items():
                assert an["models"][m][key][stat]["lo"] == pytest.approx(ci["lo"], abs=0.05)
                assert an["models"][m][key][stat]["hi"] == pytest.approx(ci["hi"], abs=0.05)
    assert an["power"]["critical_abs_spearman"] == pytest.approx(stored["power"]["critical_abs_spearman"])


def test_the_experimental_ddg_in_the_analysis_is_the_manifests(stored, target):
    for lg in target.ligands:
        if lg.name != "lig_17":
            assert stored["exp_ddg"][lg.name] == pytest.approx(lg.ddg_kcal_mol, abs=1e-12)
    assert "lig_17" not in stored["exp_ddg"] and stored["n_ligands_analogues"] == 8


def _refused(raw, mutate, *, fix_digest=False):
    r = copy.deepcopy(raw)
    mutate(r)
    if fix_digest:
        r["input_digest"] = digest({"inputs": r["inputs"], "tiers": r["tiers"]})
    with pytest.raises(pm.PlbResultsError):
        pm.validate_results(r)


def test_a_corrupted_copy_of_the_committed_results_is_refused(raw):
    # a single edited SCF energy, a swapped field digest, a dropped provenance field, an edited input: each is refused
    _refused(raw, lambda r: r["results"]["dE_int_kcal_mol"]["amber14"]["lig_20"].__setitem__(2, 0.0))
    _refused(
        raw,
        lambda r: r["results"]["E_vac_hartree"]["lig_21"].__setitem__(
            0, r["results"]["E_vac_hartree"]["lig_21"][0] + 1e-3
        ),
    )
    _refused(
        raw, lambda r: r["inputs"]["field_provenance"]["amber14"].__setitem__("input_sha256", "0" * 64), fix_digest=True
    )
    _refused(raw, lambda r: r["inputs"].pop("smeltery_commit"), fix_digest=True)
    _refused(raw, lambda r: r["inputs"].__setitem__("seed", 2))  # digest no longer matches
    _refused(raw, lambda r: r["ferric"].__setitem__("version", None))
    _refused(
        raw, lambda r: r["results"]["failures"].append({"ligand": "lig_20", "pose": 0, "error": "SCF did not converge"})
    )


def test_the_report_quotes_the_analysis_file(stored):
    doc = REPORT.read_text()
    a = stored["models"]["amber14"]
    p = a["point"]
    ci = a["bootstrap_ci_over_ligands"]["spearman"]
    for text in (
        f"{p['spearman']:+.3f}",
        f"{ci['lo']:+.2f}",
        f"{ci['hi']:+.2f}",
        f"{a['permutation']['spearman']['p_two_sided']:.4f}",
        f"{p['kendall']:+.3f}",
        f"{p['slope']:+.2f}",
    ):
        assert text in doc, f"the report does not quote {text} from the analysis file"
    cm = stored["charge_model_comparison"]
    for text in (f"{cm['charge_sensitivity_floor_kcal_mol']:.2f}", f"{cm['spearman_between_models']:+.3f}"):
        assert text in doc
    assert "What this does not show" in doc and "Deviations" in doc
