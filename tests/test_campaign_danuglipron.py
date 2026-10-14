"""The danuglipron campaign page (issue #35): its quoted numbers equal the committed run records, not retyped.

Records come from `experiments/danuglipron/out/` and `benchmarks/`; numbers that exist only in the frozen prose log
(`RESULTS.md`) are asserted as quoted text from it and marked "(prose)" in the page's source column.
"""

import json
import math
import re
from pathlib import Path

import pytest

from smeltery.pocket import DDE_NOISE_FLOOR_KCAL_MOL

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "docs" / "campaign_danuglipron.md").read_text()
LOG = (ROOT / "experiments" / "danuglipron" / "RESULTS.md").read_text()
OUT = ROOT / "experiments" / "danuglipron" / "out"
LIMITS = (ROOT / "docs" / "limits.md").read_text()


def _json(p):
    return json.loads(Path(p).read_text())


def test_the_strain_row_equals_the_free_scan_record():
    d = _json(OUT / "arm_a_free.json")
    ranked = sorted(d["conformers"], key=lambda c: c["rel_kcal_mol"])
    bound = next(c for c in ranked if c["label"] == "conf_00_cryo_em")
    assert d["method"] == "GFN2-xTB" and d["n_considered"] == 20 and d["n_converged"] == 20
    rank = 1 + ranked.index(bound)
    assert rank == 3 and f"{bound['rel_kcal_mol']:.2f}" == "2.28" and f"{d['spread_kcal_mol']:.2f}" == "10.87"
    assert "**2.28 kcal/mol** above the free minimum, rank 3 of 20, spread 10.87" in PAGE


def test_the_negative_control_rows_equal_the_fit_record():
    f = _json(OUT / "fit_and_rank.json")
    by = {c["label"]: c for c in f["candidates"]}
    par, nc2, nc1, h1c = by["parent"], by["NC2-decyano"], by["NC1-methyl-ester"], by["H1c-azetidine-for-piperidine"]
    assert f["gate_passed"] is False and f["n_conformers_per_analogue"] == 100 and f["n_pocket_charges"] == 6458
    gap = nc2["fit_kcal_mol"] - par["fit_kcal_mol"]
    bar = 2 * math.hypot(nc2["fit_sem_kcal_mol"], par["fit_sem_kcal_mol"])  # the 2-sigma bar of the pair
    assert f"{gap:.2f}" == "-18.51" and f"{bar:.2f}" == "13.46"
    assert gap < 0 and abs(gap) > bar  # NC2 scores significantly BETTER: the refutation
    assert f"{par['fit_kcal_mol']:.2f}" == "-126.07" and f"{nc2['fit_kcal_mol']:.2f}" == "-144.57"
    assert f"{nc1['fit_kcal_mol']:.2f}" == "-22.33" and f"{nc1['fit_kcal_mol'] - par['fit_kcal_mol']:.2f}" == "103.74"
    assert f"{h1c['fit_kcal_mol'] - par['fit_kcal_mol']:.2f}" == "-33.74"
    assert (par["n_poses_scored"], nc2["n_poses_scored"], nc1["n_poses_scored"]) == (91, 100, 100)
    for s in ("-126.07", "-144.57", "gap -18.51", "bar 13.46", "-22.33", "103.74", "33.74", "parent 91, NC2 100"):
        assert s in PAGE, s
    assert "18.51 kcal/mol BETTER than the parent against a 2-sigma bar of 13.46" in PAGE.replace("\n   ", " ")


def test_the_redock_row_equals_the_record():
    r = _json(OUT / "redock_validation.json")
    assert f"{r['best_rmsd']:.2f}" == f"{r['top1_rmsd']:.2f}" == "0.95"
    assert (r["n_poses"], r["exhaustiveness"], r["verdict"]) == (20, 32, "PASS")
    assert "**0.95 A** best-of-20" in PAGE and "exhaustiveness 32, 20 poses" in PAGE


# numbers that exist only in the prose log: (the string the page quotes, a string the log must contain)
PROSE = [
    ("MW 555.6; cLogP 4.89", "MW 555.6"),
    ("cLogP 4.89", "cLogP | 4.89"),
    ("-109.5 against a 41.4", "−109.5 vs a 41.4"),
    ("r = +0.490", "r = +0.490"),
    ("2.23 A (docking bar 2.0 A)", "**2.23 Å**"),
    ("2.70 to 3.70 A", "2.70 Å"),
    ("0.75 to 1.24 A", "0.75-1.24 A"),
    ("+27.43 kcal/mol", "+27.43"),
    ("Spearman -0.261, p 0.35, n = 15", "-0.261   p = 0.35   n = 15"),
    ("|rho| 0.514", "|ρ| = 0.514"),
    ("SELF anchor +0.004", "**+0.004**"),
    ("rho 0.399, SEM ratio 1.21x", "rho 0.399, SEM ratio only 1.21x"),
    ("about 3 orders short", "~3 orders short"),
    ("1%, 32.5x short", "1%, 32.5x short"),
    ("7 to 10x worse", "7-10x worse"),
]


@pytest.mark.parametrize(("quoted", "in_log"), PROSE)
def test_prose_sourced_numbers_are_quoted_from_the_frozen_log(quoted, in_log):
    assert quoted in PAGE, f"the page no longer quotes {quoted!r}"
    assert in_log in LOG, f"RESULTS.md does not contain {in_log!r}"


def test_the_smeltery_rows_equal_the_committed_results():
    assert f"{DDE_NOISE_FLOOR_KCAL_MOL:.2f}" == "4.07" and "4.07 kcal/mol" in PAGE and "| L2 |" in LIMITS
    got = {}
    for name, n, poses in (("tyk2", 12, 8), ("cdk2", 8, 5)):
        a = _json(ROOT / "benchmarks" / "plb" / "results" / f"{name}_sto3g_analysis.json")
        m = a["models"]["amber14"]
        assert (a["n_ligands_analogues"], a["n_poses_by_model"]["amber14"]) == (n, poses)
        got[name] = (m["point"]["spearman"], m["permutation"]["spearman"]["p_two_sided"])
    assert f"{got['tyk2'][0]:+.3f}" == "+0.252" and round(got["tyk2"][1], 2) == 0.43
    assert f"{got['cdk2'][0]:+.3f}" == "+0.690" and round(got["cdk2"][1], 4) == 0.0694
    assert "tyk2 +0.252 (permutation p 0.43); cdk2 +0.690 (p 0.0694)" in PAGE
    assert "PLB tyk2 n = 12 analogues, 8 poses; cdk2 n = 8, 5 poses" in PAGE


def test_the_halogen_row_equals_the_cache_and_all_failures_are_halogens():
    cache = _json(ROOT / "benchmarks" / "danuglipron_halogen" / "results" / "halogen-scf-cache.json")
    failed = [v["error"] for v in cache.values() if v.get("error")]
    names = {e.split(" pose")[0] for e in failed}
    assert len(failed) == 7 and "7 SCFs failed on jittered poses" in PAGE
    assert all(("-F" in n or "-Cl" in n) and "-CH3" not in n for n in names)
    assert "not completed" in PAGE and "issue #6" in PAGE


def test_the_page_states_the_required_conclusions():
    for s in (
        "No candidate is recommended",
        "wrong side",
        "NC2 resolved on the",
        "on-target",
        "not a finding of this work",
        "**6X1A**",
        "**7LCJ**",
        "docs/limits.md",
    ):
        assert s in PAGE, s
    assert re.search(r"measured limits", PAGE)


def test_every_limit_id_the_page_cites_exists_in_the_limits_table():
    for lid in sorted(set(re.findall(r"\b(?:L|U)\d+\b", PAGE))):
        assert f"| {lid} |" in LIMITS, lid
    cited = set(re.findall(r"\bL\d+\b", PAGE))
    assert {"L2", "L8", "L10", "L14", "L16"} <= cited
