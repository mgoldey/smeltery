"""PropertyProvider (#24): None never becomes 0.0, and a flat endpoint says so."""

import ast
from pathlib import Path

import pytest

from smeltery.properties import (
    RANK_ONLY_NOTE,
    NetworkNotAllowedError,
    PropertyProvider,
    RdkitAlertProvider,
    assess,
    compare,
)

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
HALOGEN_SCAN = {
    "parent": ASPIRIN,
    "4-F": "CC(=O)Oc1ccc(F)cc1C(=O)O",
    "4-Cl": "CC(=O)Oc1ccc(Cl)cc1C(=O)O",
    "4-Br": "CC(=O)Oc1ccc(Br)cc1C(=O)O",
    "3-F": "CC(=O)Oc1cccc(F)c1C(=O)O",
}


def test_provider_satisfies_the_protocol():
    assert isinstance(RdkitAlertProvider(), PropertyProvider)


def test_unanswerable_molecule_gives_none_not_zero():
    p = RdkitAlertProvider()
    for bad in ("not a smiles", "CC.O"):  # unparseable, multi-fragment
        out = assess(p, bad)
        assert set(out) == set(p.endpoint_notes()) and all(v is None for v in out.values())
        assert not any(v == 0.0 for v in out.values())


def test_a_real_zero_is_still_a_zero():
    assert assess(RdkitAlertProvider(), "CCO")["pains_alerts"] == 0.0


def test_endpoint_left_out_by_a_provider_is_none():
    class Partial(RdkitAlertProvider):
        def predict(self, smiles):
            return {"pains_alerts": 1.0}

    out = assess(Partial(), ASPIRIN)
    assert out["pains_alerts"] == 1.0 and out["brenk_alerts"] is None and out["nih_alerts"] is None


def test_halogen_scan_around_an_unchanged_scaffold_cannot_discriminate():
    rep = compare(RdkitAlertProvider(), HALOGEN_SCAN, "brenk_alerts")
    assert rep.status == "CANNOT_DISCRIMINATE"
    assert len(set(rep.values.values())) == 1 and None not in rep.values.values()
    assert "no information" in rep.note


def test_a_different_motif_is_discriminated():
    rep = compare(RdkitAlertProvider(), {"parent": ASPIRIN, "azo": "c1ccc(N=Nc2ccccc2)cc1"}, "pains_alerts")
    assert rep.status == "DISCRIMINATES"


def test_a_missing_answer_makes_the_comparison_unavailable_not_a_tie():
    rep = compare(RdkitAlertProvider(), {"ok": ASPIRIN, "bad": "not a smiles"}, "brenk_alerts")
    assert rep.status == "UNAVAILABLE" and "bad" in rep.note


def test_network_providers_are_off_by_default():
    class Web(RdkitAlertProvider):
        name, requires_network = "web", True

    with pytest.raises(NetworkNotAllowedError, match="1.6 s"):
        assess(Web(), ASPIRIN)
    assert assess(Web(), ASPIRIN, allow_network=True)["brenk_alerts"] == 1.0
    assert RdkitAlertProvider().requires_network is False


def test_every_endpoint_says_it_is_rank_only():
    notes = RdkitAlertProvider().endpoint_notes()
    assert notes and all(RANK_ONLY_NOTE in n for n in notes.values())


def test_no_code_path_converts_none_to_zero():
    """Static check: properties.py never turns a missing value into 0 / 0.0."""
    src = Path(__file__).parents[1] / "src" / "smeltery" / "properties.py"
    for node in ast.walk(ast.parse(src.read_text())):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            assert not any(isinstance(v, ast.Constant) and v.value in (0, 0.0) for v in node.values), "`x or 0` found"
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get" and len(node.args) == 2:
            assert not (isinstance(node.args[1], ast.Constant) and node.args[1].value in (0, 0.0)), ".get(k, 0) found"
