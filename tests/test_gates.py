"""Pure-logic tests for the charge-sensitivity gate (no ferric, no RDKit)."""

import json
import math
import pathlib

import pytest

from smeltery import Candidate, Measurement, SensitivityReport, charge_sensitivity, cut, spearman

Q = "ddE"
FIXTURE = json.loads((pathlib.Path(__file__).parent / "data" / "halogen_charge_sensitivity.json").read_text())


def _cands(rows):
    out = []
    for r in rows:
        c = Candidate(r["name"], "")
        c.per_pose["a"], c.per_pose["b"] = r["model_a"], r["model_b"]
        out.append(c)
    return out


def _model(key):
    return lambda c, q: c.per_pose[key]


def test_same_model_twice_is_the_trivial_limit_and_the_models_differ_live():
    cands = _cands(FIXTURE["candidates"])
    same = charge_sensitivity(cands, Q, _model("a"), _model("a"))
    assert same.n_sign_flips == 0
    assert same.spearman == 1.0
    assert same.floor == 0.0
    # non-vacuity: the live pair of models genuinely disagrees, so the values
    # above are not just what the metrics return for anything.
    live = charge_sensitivity(cands, Q, _model("a"), _model("b"))
    assert live.n_sign_flips > 0
    assert live.spearman < 1.0
    assert live.floor > 0.0
    assert live.values_a != live.values_b


def test_halogen_fixture_reproduces_recorded_summary_statistics():
    # The fixture is constructed to match the recorded 4/12 and +0.664; it is not
    # the campaign's raw data (see its PROVENANCE field).
    assert "CONSTRUCTED" in FIXTURE["PROVENANCE"]
    rep = charge_sensitivity(_cands(FIXTURE["candidates"]), FIXTURE["quantity"], _model("a"), _model("b"))
    assert rep.n == FIXTURE["recorded"]["n"] == 12
    assert rep.n_sign_flips == FIXTURE["recorded"]["n_sign_flips"] == 4
    assert rep.spearman == pytest.approx(FIXTURE["recorded"]["spearman"], abs=0.01)


def test_spearman_known_values_and_ties():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert spearman([1, 2, 2, 3], [1, 2, 2, 3]) == pytest.approx(1.0)
    assert math.isnan(spearman([1, 1, 1], [1, 2, 3]))


def _pair_report():
    # x and y are 1.0 apart under model a; the models move candidates by up to 2.5 relative
    rows = [
        {"name": "x", "model_a": -1.0, "model_b": -3.0},
        {"name": "y", "model_a": 0.0, "model_b": 0.0},
        {"name": "z", "model_a": 10.0, "model_b": 10.5},
    ]
    return charge_sensitivity(_cands(rows), Q, _model("a"), _model("b"))


def test_floor_is_the_range_of_per_candidate_deltas():
    rep = _pair_report()
    assert rep.deltas == {"x": -2.0, "y": 0.0, "z": 0.5}
    assert rep.floor == 2.5


def test_cut_returns_tie_group_for_pair_inside_the_sensitivity_floor():
    rep = _pair_report()
    ms = {"x": Measurement(-1.0, 0.01, 10, ()), "y": Measurement(0.0, 0.01, 10, ()),
          "z": Measurement(10.0, 0.01, 10, ())}
    # without the gate x and y are resolved: an order, x before y
    assert cut(ms, keep=1).groups == [["x"], ["y"], ["z"]]
    res = cut(ms, keep=1, sensitivity=rep)
    # |x-y| = 1.0 < floor 2.5: x and y are a tie group, no order; z stays separate
    assert res.groups == [["x", "y"], ["z"]]
    assert res.floor == 2.5
    assert res.unranked_at_boundary and res.survivors is None


class _Tier:
    name = "fake"

    def __init__(self, floor):
        self._floor = floor

    def produces(self):
        return {Q: "kcal/mol"}

    def systematic_floor(self, quantity):
        return self._floor


def test_sensitivity_and_tier_floor_combine_by_max():
    rep = _pair_report()  # floor 2.5
    ms = {"x": Measurement(-1.0, 0.01, 10, ()), "y": Measurement(0.0, 0.01, 10, ())}
    assert cut(ms, keep=1, tier=_Tier(0.5), quantity=Q, sensitivity=rep).floor == 2.5
    assert cut(ms, keep=1, tier=_Tier(4.0), quantity=Q, sensitivity=rep).floor == 4.0
    assert cut(ms, keep=1, floor=1.0, sensitivity=rep).floor == 2.5
    other = SensitivityReport("other", (), (), (), 0, 1.0, {}, 0.0)
    with pytest.raises(ValueError, match="sensitivity report is for"):
        cut(ms, keep=1, tier=_Tier(0.5), quantity=Q, sensitivity=other)
