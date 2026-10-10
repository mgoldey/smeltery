"""scripts/chembl_variability.py: its statistics, on synthetic records with hand-worked answers (issue #26)."""

import importlib.util
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("chembl_variability", ROOT / "scripts" / "chembl_variability.py")
cv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cv)


def rec(mol, doc, value, typ="IC50", assay="A1"):
    return {"molecule_chembl_id": mol, "document_chembl_id": doc, "standard_type": typ, "pchembl_value": str(value),
            "assay_chembl_id": assay}  # fmt: skip


def test_the_conversion_factor_is_rt_ln10_at_298_15_k():
    assert cv.to_kcal(1.0) == pytest.approx(1.3643, abs=1e-4)
    assert cv.to_kcal(0.0) == 0.0


def test_pooled_within_sd_matches_a_hand_calculation():
    # groups [6.0, 7.0] (s^2 = 0.5, df 1) and [5.0, 5.0, 8.0] (mean 6, ss = 1+1+4 = 6, df 2): pooled = sqrt((0.5+6)/3)
    out = cv.pooled_within_sd([[6.0, 7.0], [5.0, 5.0, 8.0]])
    assert out["df"] == 3 and out["ss"] == pytest.approx(6.5)
    assert out["sd"] == pytest.approx(math.sqrt(6.5 / 3))
    assert cv.pooled_within_sd([[5.0]])["sd"] is None  # a single value says nothing about spread


def test_replicates_inside_one_document_are_collapsed_not_counted_as_independent():
    records = [rec("M1", "D1", 6.0), rec("M1", "D1", 8.0, assay="A2"), rec("M1", "D2", 7.0)]
    groups = cv.compound_groups(records)
    assert groups[("M1", "IC50")] == {"D1": 7.0, "D2": 7.0}  # D1's median of 6.0 and 8.0


def test_a_compound_needs_two_different_documents_and_types_are_never_mixed():
    records = [
        rec("M1", "D1", 6.0),
        rec("M1", "D1", 9.0, assay="A2"),
        rec("M2", "D1", 6.0, "IC50"),
        rec("M2", "D2", 8.0, "Ki"),
    ]
    s = cv.summarize(records, "IC50")
    assert s["n_compounds_2plus_documents"] == 0  # M1 has one document; M2's IC50 and Ki are not comparable
    assert s["pooled"]["sd"] is None


def test_summary_counts_pairs_identical_pairs_and_the_pooled_sd():
    records = [
        rec("M1", "D1", 6.00), rec("M1", "D2", 7.00),  # one pair, |diff| 1.0
        rec("M2", "D1", 5.00), rec("M2", "D2", 5.00), rec("M2", "D3", 5.002),  # three pairs, all identical within 0.005
    ]  # fmt: skip
    s = cv.summarize(records, "IC50")
    assert s["n_compounds_2plus_documents"] == 2 and s["n_pairs"] == 4 and s["n_identical_pairs"] == 3
    # the four |differences| sort to [0, 0.002, 0.002, 1.0]: the median is (0.002 + 0.002) / 2
    assert s["median_abs_diff_log"] == pytest.approx(0.002)
    # all compounds: ss = 0.5 (M1) + ~0 (M2), df = 1 + 2
    assert s["pooled"]["df"] == 3
    # excluding compounds whose documents all agree: only M1 remains, pooled sd = sqrt(0.5)
    ex = s["pooled_excluding_all_identical_compounds"]
    assert ex["n_compounds"] == 1 and ex["sd"] == pytest.approx(math.sqrt(0.5))
    assert ex["sd_kcal_mol"] == pytest.approx(cv.to_kcal(math.sqrt(0.5)))


def test_inputs_digest_ignores_order_and_changes_with_any_value():
    a = [rec("M1", "D1", 6.0), rec("M2", "D2", 7.0)]
    assert cv.inputs_digest(a) == cv.inputs_digest(list(reversed(a)))
    assert cv.inputs_digest(a) != cv.inputs_digest([rec("M1", "D1", 6.01), rec("M2", "D2", 7.0)])


def test_the_script_refuses_to_touch_the_network_without_the_opt_in(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("SMELTERY_NETWORK", raising=False)
    assert cv.main([str(tmp_path / "o.json")]) == 2
    assert "SMELTERY_NETWORK=1" in capsys.readouterr().err


RESULTS = ROOT / "benchmarks" / "variability" / "results.json"
PLB_MANIFEST = ROOT / "benchmarks" / "plb" / "manifest.json"


def _results():
    import json

    return json.loads(RESULTS.read_text()), json.loads(PLB_MANIFEST.read_text())


def test_the_committed_results_carry_their_provenance_and_licence():
    res, _ = _results()
    p = res["provenance"]
    for key in (
        "chembl_db_version",
        "chembl_release_date",
        "retrieved_utc",
        "filters",
        "licence",
        "attribution",
        "redistribution",
    ):
        assert p.get(key), key
    assert "CC BY-SA" in p["licence"] and "aggregates only" in p["redistribution"]
    assert set(res["targets"]) == set(cv.UNIPROT)  # every PLB target, none missing
    for name, t in res["targets"].items():
        assert (
            t["uniprot"] == cv.UNIPROT[name]
            and t["target_chembl_id"].startswith("CHEMBL")
            and len(t["inputs_sha256"]) == 64
        )


def test_every_committed_statistic_recomputes_from_its_stored_sums():
    """sd = sqrt(ss/df) and the kcal/mol conversion are consistent for every target and type (no hand-edited number)."""
    res, _ = _results()
    for name, t in res["targets"].items():
        for typ, s in t["by_type"].items():
            for key in ("pooled", "pooled_excluding_all_identical_compounds"):
                p = s[key]
                if p["df"]:
                    assert p["sd"] == pytest.approx(math.sqrt(p["ss"] / p["df"]), rel=1e-12), (name, typ, key)
                    assert p["sd_kcal_mol"] == pytest.approx(cv.to_kcal(p["sd"]), rel=1e-12), (name, typ, key)
                else:
                    assert p["sd"] is None, (name, typ, key)
            assert s["n_identical_pairs"] <= s["n_pairs"]


def test_the_plb_types_resolve_and_the_readme_table_is_the_generated_one():
    res, manifest = _results()
    assert set(cv.PLB_TO_CHEMBL_TYPE) >= {t["assay_type"] for t in manifest["targets"].values()}
    readme = (ROOT / "benchmarks" / "variability" / "README.md").read_text()
    assert cv.render_report(res, manifest) in readme, "README table drifted from the committed results"


def test_the_headline_number_is_the_pooled_sd_of_the_plb_own_types():
    res, manifest = _results()
    rows = cv.own_type_rows(res, manifest)
    o = cv.overall(rows)
    assert o["df"] == sum(r["pooled"]["df"] for r in rows) and o["df"] > 500
    assert 0.3 < o["sd"] < 1.0  # a sanity band: independent assays agree to a fraction of a log unit, not a decade
    assert o["diff_sd_kcal_mol"] == pytest.approx(o["sd_kcal_mol"] * math.sqrt(2))
