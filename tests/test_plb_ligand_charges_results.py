# ruff: noqa: E501
"""The committed ligand-side charge comparison (issue #29): statistics recompute from the committed per-atom charges and
pocket potentials; the numbers quoted in the report and in docs/limits.md are the numbers in the analysis file; no default
floor was introduced. Nothing here runs quantum chemistry.

Mutation checks done by hand when this file was written (each made at least one test below fail, then reverted):
editing one Hirshfeld charge in the results, editing one stored potential, quoting a different Gasteiger floor in the report,
quoting a different floor in docs/limits.md, replacing the Lowdin set by the Hirshfeld set, and changing one geometry digest in
the stored results.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import numpy as np
import pytest

from smeltery import plb_ligand_charges as lc

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "benchmarks" / "plb" / "results"
RESULTS = RES / "tyk2_sto3g_ligand_charges.json"
ANALYSIS = RES / "tyk2_sto3g_ligand_charges_analysis.json"
STORED = RES / "tyk2_sto3g.json"
REPORT = ROOT / "docs" / "benchmarks" / "plb_ligand_charges.md"
LIMITS = ROOT / "docs" / "limits.md"
CPU_CAP_HOURS = 6.0


@pytest.fixture(scope="module")
def rec():
    return json.loads(RESULTS.read_text())


@pytest.fixture(scope="module")
def an():
    return json.loads(ANALYSIS.read_text())


@pytest.fixture(scope="module")
def stored():
    return json.loads(STORED.read_text())


def test_design_is_what_the_plan_said(rec, an):
    assert rec["models"] == ["lowdin", "hirshfeld", "gasteiger"]
    assert rec["n_poses"] == 8 and len(rec["ligands"]) == 13 and an["n_analogues"] == 12
    assert rec["inputs"]["hirshfeld_proatom"] == "scf" and rec["inputs"]["ferric"]["version"] == "0.1.0rc7"
    assert rec["inputs"]["cpu_s_total"] / 3600 + 0.10 < CPU_CAP_HOURS


def test_geometries_and_field_are_the_stored_runs(rec, stored):
    si = stored["inputs"]
    assert rec["inputs"]["pose_geometry_sha256"] == {n: si["pose_geometry_sha256"][n] for n in rec["ligands"]}
    assert rec["inputs"]["pqr_sha256"] == si["prep_provenance"]["amber14"]["output_sha256"]
    assert rec["inputs"]["n_field_charges"] == si["field_provenance"]["amber14"]["n_charges"]


def test_shapes_and_charge_sums(rec, an):
    for m in rec["models"]:
        for n in rec["ligands"]:
            assert len(rec["charges"][m][n]) == 8
            assert all(len(q) == len(rec["phi_kcal_mol_per_e"][n][0]) for q in rec["charges"][m][n])
    assert max(an["max_abs_charge_sum_error_e"].values()) < 1e-10
    for n in rec["ligands"]:  # Gasteiger is topological: identical at every pose
        assert rec["charges"]["gasteiger"][n][0] == rec["charges"]["gasteiger"][n][7]


def test_analysis_recomputes_from_the_results_file(rec, an, stored):
    again = lc.analyse_record(rec, stored)
    assert json.loads(json.dumps(again)) == an


def test_es_energy_is_q_dot_phi_from_the_file(rec, an):
    n, k = "lig_ejm_48", 3
    q, phi = np.array(rec["charges"]["hirshfeld"][n][k]), np.array(rec["phi_kcal_mol_per_e"][n][k])
    assert lc.es_energies(rec)["hirshfeld"][rec["ligands"].index(n), k] == pytest.approx(float((q * phi).sum()))


def test_a_corrupted_charge_changes_the_analysis(rec, an, stored):
    bad = copy.deepcopy(rec)
    bad["charges"]["hirshfeld"]["lig_jmc_27"][0][0] += 0.5
    assert lc.analyse_record(bad, stored) != an


def test_a_swapped_model_is_detected(rec, an, stored):
    bad = copy.deepcopy(rec)
    bad["charges"]["lowdin"] = bad["charges"]["hirshfeld"]
    assert (
        lc.analyse_record(bad, stored)["pairs"]["lowdin|hirshfeld"]["floor_kcal_mol"]
        == 0.0
        != an["pairs"]["lowdin|hirshfeld"]["floor_kcal_mol"]
    )


def test_verdicts_follow_the_pre_declared_rule(an):
    for p in an["pairs"].values():
        assert p["verdict"] == lc.verdict(p)
        assert p["n"] == 12 and p["n_pairs"] == 66
    assert an["pairs"]["lowdin|hirshfeld"]["verdict"] == "agree"


def _fmt(x, d=2):
    return f"{x:.{d}f}"


def test_report_quotes_the_analysis_file(an):
    text = REPORT.read_text()
    for key, label in (
        ("lowdin|hirshfeld", "Lowdin vs Hirshfeld"),
        ("lowdin|gasteiger", "Lowdin vs Gasteiger"),
        ("hirshfeld|gasteiger", "Hirshfeld vs Gasteiger"),
    ):
        p = an["pairs"][key]
        row = next(ln.strip() for ln in text.splitlines() if ln.strip().startswith(f"| {label} |"))
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert cells[1] == f"{p['n_sign_flips']}/{p['n']}"
        assert cells[2] == f"{p['n_pair_order_flips']}/{p['n_pairs']}"
        assert cells[3].startswith(f"{p['spearman']:+.3f}")
        assert cells[4] == f"{p['kendall']:+.3f}"
        assert cells[5] == _fmt(p["floor_kcal_mol"])
        assert cells[6] == _fmt(p["sd_of_shift_kcal_mol"])
        assert cells[7] == p["verdict"]
        lo, hi = p["spearman_ci95"]["lo"], p["spearman_ci95"]["hi"]
        assert f"[{lo:+.2f}, {hi:+.2f}]" in cells[3] or abs(lo - 1) < 1e-9
    for m, word in (("lowdin", "Lowdin"), ("hirshfeld", "Hirshfeld"), ("gasteiger", "Gasteiger")):
        assert f"{an['vs_qm'][m]['spearman']:+.3f} ({word})" in text
        assert f"{an['mean_paired_sem_kcal_mol'][m]:.2f} ({word})" in text
        assert (
            an["carbonyl_polarity"][m]["n_polarity_correct"]
            == an["carbonyl_polarity"][m]["n_carbonyl_ligand_poses"]
            == 208
        )
    for key in ("lowdin|hirshfeld", "lowdin|gasteiger", "hirshfeld|gasteiger"):
        p = an["pairs"][key]
        assert f"{p['raw_E_es_shift_mean_kcal_mol']:+.2f} +/- {p['raw_E_es_shift_sd_kcal_mol']:.2f}" in text


def test_limits_row_quotes_the_analysis_file(an):
    row = next(ln for ln in LIMITS.read_text().splitlines() if ln.startswith("| L12 |"))
    p = an["pairs"]
    assert (
        f"{p['lowdin|hirshfeld']['floor_kcal_mol']:.2f} kcal/mol (Lowdin vs Hirshfeld, Spearman {p['lowdin|hirshfeld']['spearman']:+.3f})"
        in row
    )
    assert f"{p['lowdin|gasteiger']['floor_kcal_mol']:.2f} (Lowdin vs Gasteiger)" in row
    assert f"{p['hirshfeld|gasteiger']['floor_kcal_mol']:.2f} (Hirshfeld vs Gasteiger)" in row
    assert f"Spearman {p['lowdin|gasteiger']['spearman']:+.3f} for both" in row
    assert p["lowdin|gasteiger"]["spearman"] == p["hirshfeld|gasteiger"]["spearman"]
    assert (
        f"{p['lowdin|gasteiger']['n_pair_order_flips']} of {p['lowdin|gasteiger']['n_pairs']} pair-order flips" in row
    )
    assert "tyk2_sto3g_ligand_charges_analysis.json" in row and "plb_ligand_charges.md" in row


def test_no_default_floor_was_published():
    from smeltery.tiers import FieldInteraction

    assert FieldInteraction().systematic_floor("dE_int") is None
    assert not re.search(
        r"LIGAND_CHARGE\w*FLOOR|CHARGE_SENSITIVITY\w*FLOOR", (ROOT / "src" / "smeltery" / "tiers.py").read_text()
    )
