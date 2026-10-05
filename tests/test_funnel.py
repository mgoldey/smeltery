"""Pure-logic tests: no ferric, no RDKit. Fast."""

import math

import numpy as np
import pytest

from smeltery import (
    ANGSTROM_TO_BOHR, Candidate, IncomparableError, Measurement, PointCharge, Pose, RunRecord,
    UnmeasuredFloorError, cut, paired_delta, require_same_formula,
)


def m(mean, sem, n=6):
    return Measurement(mean, sem, n, ())


def test_resolved_candidates_are_cut_cleanly():
    res = cut({"a": m(-3.0, 0.1), "b": m(0.0, 0.1), "c": m(3.0, 0.1)}, keep=1)
    assert res.survivors == ["a"]
    assert not res.unranked_at_boundary


def test_unresolved_boundary_is_reported_not_decided():
    # a and b are within each other's noise; keep=1 would have to split them.
    res = cut({"a": m(-1.0, 0.5), "b": m(-0.8, 0.5), "c": m(5.0, 0.1)}, keep=1)
    assert res.survivors is None
    assert res.unranked_at_boundary
    assert ["a", "b"] in res.groups


def test_whole_tie_group_fits_inside_keep():
    res = cut({"a": m(-1.0, 0.5), "b": m(-0.8, 0.5), "c": m(5.0, 0.1)}, keep=2)
    assert res.survivors == ["a", "b"]


def test_systematic_floor_blocks_a_ranking_the_sem_alone_would_allow():
    ms = {"a": m(-1.0, 0.01), "b": m(0.0, 0.01)}
    assert cut(ms, keep=1).survivors == ["a"]
    assert cut(ms, keep=1, floor=2.0).unranked_at_boundary


def test_no_sem_never_resolves():
    res = cut({"a": Measurement(-9.0, math.nan, 1, ()), "b": m(0.0, 0.1)}, keep=1)
    assert res.unranked_at_boundary


def _cand(name, symbols):
    xyz = np.zeros((len(symbols), 3))
    return Candidate(name, "", poses=[Pose(tuple(symbols), xyz)])


class _FakeTier:
    """A structural Tier (Protocol, no base class) with a settable floor and unit."""

    name = "fake"

    def __init__(self, floor, unit="kcal/mol"):
        self._floor, self._unit = floor, unit

    def produces(self):
        return {"q": self._unit}

    def systematic_floor(self, quantity):
        return self._floor


def test_cut_refuses_when_tier_floor_is_unmeasured():
    ms = {"a": m(-1.0, 0.01), "b": m(0.0, 0.01)}
    with pytest.raises(UnmeasuredFloorError) as exc:
        cut(ms, keep=1, tier=_FakeTier(None), quantity="q")
    assert str(exc.value) == (
        "refusing to cut on 'q': tier 'fake' has not measured its systematic floor "
        "(systematic_floor() is None). Measure it, or pass an explicit floor= to cut() without tier=."
    )


def test_cut_takes_its_floor_from_the_tier():
    ms = {"a": m(-1.0, 0.01), "b": m(0.0, 0.01)}
    assert cut(ms, keep=1, tier=_FakeTier(0.5), quantity="q").survivors == ["a"]
    res = cut(ms, keep=1, tier=_FakeTier(2.0), quantity="q")
    assert res.floor == 2.0 and res.unranked_at_boundary


def test_cut_rejects_explicit_floor_alongside_a_tier():
    with pytest.raises(ValueError, match="no explicit floor"):
        cut({"a": m(0.0, 0.1)}, keep=1, floor=1.0, tier=_FakeTier(0.5), quantity="q")


def test_funnel_reads_the_unit_from_the_tier_not_kcal():
    parent, other = Candidate("p", ""), Candidate("o", "")
    parent.per_pose["q"], other.per_pose["q"] = [1.0, 2.0, 3.0], [2.0, 3.0, 5.0]
    dd = paired_delta(parent, other, "q", tier=_FakeTier(0.1, unit="eV"))
    assert dd.unit == "eV"
    with pytest.raises(ValueError, match="declared unit 'eV'"):
        cut({"a": m(0.0, 0.1)}, keep=1, tier=_FakeTier(0.1, unit="eV"), quantity="q")


def test_total_quantity_refused_across_formulas():
    with pytest.raises(IncomparableError, match="differing formulas"):
        require_same_formula([_cand("x", ["C", "H"]), _cand("y", ["C", "F"])], "E_total")


def test_total_quantity_allowed_for_isomers():
    require_same_formula([_cand("x", ["C", "O", "H"]), _cand("y", ["O", "C", "H"])], "E_total")


def test_point_charge_converts_angstrom_to_bohr_once():
    q, x, y, z = PointCharge(1.0, (1.0, 0.0, -2.0)).as_ferric_bohr()
    assert (q, x, y, z) == (1.0, ANGSTROM_TO_BOHR, 0.0, -2.0 * ANGSTROM_TO_BOHR)


def test_run_record_round_trips_and_digest_tracks_inputs():
    r = RunRecord("c", inputs={"a": 1}, tiers=[{"name": "t"}], results={"x": 2.0})
    back = RunRecord.from_json(r.to_json())
    assert back.inputs == r.inputs and back.results == r.results
    assert back.input_digest == r.input_digest
    assert RunRecord("c", inputs={"a": 2}, tiers=[{"name": "t"}], results={}).input_digest != r.input_digest


def test_formula_is_hill_ordered():
    assert _cand("x", ["O", "C", "F", "H", "C", "Cl"]).formula == "C2HClFO"
    assert _cand("y", ["O", "H", "H"]).formula == "H2O"


def test_ferric_identity_is_never_unknown_when_ferric_is_installed():
    from smeltery import ferric_identity

    ident = ferric_identity()
    assert ident["version"], ident
    assert ident["provenance"].startswith(("VERIFIED", "INFERRED")), ident
