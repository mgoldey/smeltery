"""`Reduction`: the shape of a comparison, declared as data.

Every test here is a real error from the gen-compare study, turned into something the type refuses:

* `test_pooling_is_refused_unless_declared` -- pooling moved a measured difference from -1.21 to
  -3.24 kcal/mol.
* `test_the_floor_kind_is_derived_from_the_declaration` -- a -4.7 ABSOLUTE prep floor was applied to
  a between-arm difference, too conservative by ~sqrt(n).
* `test_a_rate_cannot_be_quoted_without_its_denominator` -- "PAINS 6 vs 0" was 6/324 vs 0/59, whose
  intervals overlap almost entirely.
* `test_unpaired_groups_are_counted_not_dropped` -- 4 cells where one arm produced nothing were
  silently excluded from the headline.
"""

from __future__ import annotations

import pytest

from smeltery.floor import ABSOLUTE, DIFFERENCE, Floor, FloorKindError
from smeltery.study import (
    Condition,
    Estimate,
    Reduction,
    ReductionError,
    StepResult,
    paired_difference,
    rate,
    rate_per_arm,
)


def _results(step: str, data: dict[tuple[str, str], list[float]]) -> list[StepResult]:
    """`{(cell, arm): values}` as StepResults, the way a Study.run would return them."""
    return [
        StepResult(step, Condition(cell=cell, arm=arm), f"k{i}", vals, False)
        for i, ((cell, arm), vals) in enumerate(data.items())
    ]


DATA = {
    ("c1", "a"): [-21.0, -20.0, -22.0],
    ("c1", "b"): [-19.0, -20.0],
    ("c2", "a"): [-13.0, -14.0],
    ("c2", "b"): [-12.0, -13.0],
}


def _red(**kw) -> Reduction:
    base = {
        "name": "diff",
        "version": "1",
        "consumes": "props",
        "fn": lambda g, c: g,
        "over": ("arm",),
        "within": ("cell",),
    }
    return Reduction(**{**base, **kw})


# ---------------------------------------------------------------- shape


def test_pooling_is_refused_unless_declared():
    """The anchor. `within=()` while cells vary compares mixtures, not arms."""
    r = _red(within=())
    with pytest.raises(ReductionError, match="would pool over"):
        r.group(_results("props", DATA))
    # Deliberate pooling is possible, and NAMES the factor rather than disabling every check.
    assert len(_red(within=(), pool_over=("cell",)).group(_results("props", DATA))) == 1


def test_a_single_condition_summary_is_not_pooling():
    """Nothing varies, so nothing is mixed. The old check tested field PRESENCE and refused this."""
    r = _red(over=(), within=())
    assert len(r.group([StepResult("props", Condition(arm="a"), "k", [1.0], False)])) == 1


def test_a_factor_in_neither_over_nor_within_is_caught_even_with_a_within():
    """The real trap, which the old `within=()`-only check let through: it surfaced late from
    `one()`, or never at all for an estimator that does not call it."""
    res = [
        StepResult("props", Condition(cell="c1", arm=a, seed=sd), f"k{a}{sd}", [1.0], False)
        for a in ("x", "y")
        for sd in ("100", "200")
    ]
    with pytest.raises(ReductionError, match="would pool over \\['seed'\\]"):
        _red().group(res)
    assert len(_red(pool_over=("seed",)).group(res)) == 1, "declaring it is the way through"


def test_a_factor_cannot_be_both_varied_and_fixed():
    with pytest.raises(ReductionError, match="cannot be both"):
        _red(over=("arm",), within=("arm", "cell"))


def test_grouping_buckets_by_within_then_over():
    groups = _red().group(_results("props", DATA))
    assert [g.within.label() for g in groups] == ["cell=c1", "cell=c2"]
    assert all(set(g.arms) == {Condition(arm="a"), Condition(arm="b")} for g in groups)
    assert groups[0].n_values == 2, "two step values: one per arm, not a denominator"
    assert groups[0].one(Condition(arm="a")) == [-21.0, -20.0, -22.0], "unwrapped, not nested"


def test_a_missing_factor_is_named_rather_than_silently_dropped():
    res = [StepResult("props", Condition(cell="c1"), "k", [1.0], False)]
    with pytest.raises(ReductionError, match="does not fix"):
        _red().group(res)


def test_consuming_a_step_that_was_not_produced_says_what_is_available():
    with pytest.raises(ReductionError, match="have \\['other'\\]"):
        _red().group([StepResult("other", Condition(cell="c", arm="a"), "k", [1.0], False)])


# ---------------------------------------------------------------- the floor connection


def test_the_floor_kind_is_derived_from_the_declaration():
    """The anchor. Comparing across `over` yields a difference, so an absolute floor is refused --
    the connection that was missing when -4.7 kcal/mol was quoted against a -1.21 difference."""
    comparing = _red(over=("arm",), within=("cell",))
    assert comparing.floor_kind == DIFFERENCE
    with pytest.raises(FloorKindError, match="absolute floor"):
        comparing.check_floor(Floor(4.7, "kcal/mol", ABSOLUTE, "two pocket preps"))
    ok = Floor(0.44, "kcal/mol", DIFFERENCE, "sensitivity sd scaled to the groups")
    assert comparing.check_floor(ok) is ok

    summarising = _red(over=(), within=("cell", "arm"))
    assert summarising.floor_kind == ABSOLUTE
    with pytest.raises(FloorKindError):
        summarising.check_floor(ok)


def test_no_floor_is_allowed_and_stays_none():
    assert _red().check_floor(None) is None


# ---------------------------------------------------------------- estimators


def test_paired_difference_takes_the_difference_within_each_group():
    import statistics as st

    groups = _red().group(_results("props", DATA))
    e = paired_difference(groups, lambda vs: st.median(vs), unit="kcal/mol")
    # c1: -21 - -19.5 = -1.5 ; c2: -13.5 - -12.5 = -1.0 ; median -1.25
    assert e.value == pytest.approx(-1.25)
    assert e.n == 2, "n counts PAIRED GROUPS: the units the estimate rests on"
    assert e.favouring_first == 2
    assert e.n_min_per_group == 2, "and the smallest arm behind it is reported, not hidden"


def test_unpaired_groups_are_counted_not_dropped():
    """The anchor. 4 cells where one arm produced nothing vanished from the headline."""
    import statistics as st

    data = {**DATA, ("c3", "a"): [-30.0, -31.0]}  # no 'b' arm
    e = paired_difference(_red().group(_results("props", data)), lambda vs: st.median(vs))
    assert e.n == 2 and e.n_unpaired == 1


def test_a_difference_with_no_paired_group_is_none_not_zero():
    e = paired_difference(_red().group(_results("props", {("c1", "a"): [1.0]})), lambda vs: vs[0])
    assert e.value is None and e.n == 0
    assert "no group has both arms" in e.detail["reason"]


def test_paired_difference_checks_the_floor_it_is_given():
    import statistics as st

    groups = _red().group(_results("props", DATA))
    with pytest.raises(FloorKindError):
        paired_difference(groups, lambda vs: st.median(vs), floor=Floor(4.7, "kcal/mol", ABSOLUTE, "preps"))


#: The real gen-compare per-cell differences: 11 paired cells, median -1.211, and an interval that
#: includes zero. The published headline quoted the point estimate alone.
REAL_DIFFS = [-7.173, -4.890, -2.662, -2.322, -1.673, -1.211, -0.468, -0.343, 0.465, 0.496, 0.611]


def _real_groups():
    """Each cell an arm pair whose medians differ by the measured amount, with enough per arm."""
    res = []
    for i, d in enumerate(REAL_DIFFS):
        res.append(StepResult("props", Condition(cell=f"c{i}", arm="a"), f"a{i}", [d] * 5, False))
        res.append(StepResult("props", Condition(cell=f"c{i}", arm="b"), f"b{i}", [0.0] * 5, False))
    return _red().group(res)


def test_resolved_needs_BOTH_halves_not_just_the_floor():
    """The regression this module shipped with, on the real data. `gui.compare` records the same
    defect as fixed: comparing a point estimate to a floor ignores sampling error entirely."""
    import statistics as st

    e = paired_difference(
        _real_groups(), lambda vs: st.median(vs), floor=Floor(0.44, "kcal/mol", DIFFERENCE, "sensitivity sd")
    )
    assert e.value == pytest.approx(-1.211), "reproduces the published per-cell median"
    assert abs(e.value) > 0.44, "the point estimate clears the floor"
    assert e.has_interval and e.excludes(0.0) is False, "but its interval includes zero"
    assert e.resolved is False, "so it is NOT resolved; the floor alone must not decide"
    assert e.n == 11 and e.n_min_per_group == 5
    assert "NOT RESOLVED" in str(e)


def test_an_underpowered_group_cannot_resolve_anything():
    """A one-measurement arm is a draw, not a summary, however large the difference looks."""
    res = [
        StepResult("props", Condition(cell="c1", arm="a"), "k1", [-30.0] * 8, False),
        StepResult("props", Condition(cell="c1", arm="b"), "k2", [-10.0], False),
        StepResult("props", Condition(cell="c2", arm="a"), "k3", [-30.0] * 8, False),
        StepResult("props", Condition(cell="c2", arm="b"), "k4", [-10.0] * 8, False),
    ]
    e = paired_difference(
        _red().group(res), lambda vs: __import__("statistics").median(vs), floor=Floor(0.5, "kcal/mol", DIFFERENCE, "m")
    )
    assert e.n_min_per_group == 1, "the smallest arm behind the estimate is visible"
    assert e.underpowered is True
    assert e.resolved is False, "a 20 kcal/mol gap still does not resolve on a single draw"


def test_the_estimate_reports_an_interval_and_its_method():
    import statistics as st

    e = paired_difference(_red().group(_results("props", DATA)), lambda vs: st.median(vs))
    assert e.has_interval and e.coverage is not None
    assert "order-statistic" in e.method
    assert e.resolved is None, "no floor given is not the same answer as not resolved"
    assert "no floor given" in str(e)


def test_a_rate_cannot_be_quoted_without_its_denominator():
    """The anchor. 6/324 and 0/59 have overlapping intervals; the bare counts did not show it."""
    groups = _red().group(
        [
            StepResult("props", Condition(cell="c1", arm="a"), "k1", [1.0] * 6 + [0.0] * 318, False),
            StepResult("props", Condition(cell="c1", arm="b"), "k2", [0.0] * 59, False),
        ]
    )
    per = rate_per_arm(groups, lambda vs: (int(sum(vs)), len(vs)))
    pxm, sbdd = per[Condition(arm="a")], per[Condition(arm="b")]
    assert pxm.n == 324 and sbdd.n == 59
    assert pxm.has_interval and sbdd.has_interval and pxm.method == "wilson"
    assert sbdd.value == 0.0 and sbdd.hi > pxm.value, (
        "zero events is not evidence of a lower rate: the interval must reach above the other arm"
    )


def test_rate_refuses_to_pool_two_arms():
    """The defect this shipped with: rate() summed over arms and returned ONE number for both --
    the pooled mixture the module exists to refuse. The old test never passed it a two-arm group."""
    groups = _red().group(
        [
            StepResult("props", Condition(cell="c1", arm="a"), "k1", [1.0] * 6 + [0.0] * 318, False),
            StepResult("props", Condition(cell="c1", arm="b"), "k2", [0.0] * 59, False),
        ]
    )
    with pytest.raises(ReductionError, match="pooling them gives the mixture"):
        rate(groups, lambda vs: (int(sum(vs)), len(vs)))


def test_a_third_arm_is_refused_rather_than_silently_dropped():
    """It took arms[0] and arms[1] and discarded the rest with no record."""
    import statistics as st

    res = [
        StepResult("props", Condition(cell="c1", arm=a), f"k{a}", [v], False)
        for a, v in (("a", -21.0), ("b", -19.0), ("c", -99.0))
    ]
    with pytest.raises(ReductionError, match="3 arms"):
        paired_difference(_red().group(res), lambda vs: st.median(vs))


def test_an_estimate_says_whether_its_interval_excludes_zero():
    assert Estimate(-1.2, 11, lo=-3.4, hi=0.5).excludes(0.0) is False
    assert Estimate(-1.2, 11, lo=-3.4, hi=-0.5).excludes(0.0) is True
    assert Estimate(-1.2, 11).excludes(0.0) is None, "no interval, no answer"


def test_an_estimate_prints_its_n_and_interval():
    s = str(Estimate(-1.21, 11, lo=-3.37, hi=0.47, unit="kcal/mol", label="per-cell diff"))
    assert "-1.210" in s and "n=11" in s and "[-3.370, +0.470]" in s


# ---------------------------------------------------------------- caching


def test_the_key_tracks_the_declaration_and_its_inputs():
    res = _results("props", DATA)
    base = _red().key(res)
    assert _red(within=()).key(res) != base, "a different shape is a different result"
    assert _red(version="2").key(res) != base
    assert _red().key(res) == base
    changed = [*res[:-1], StepResult("props", res[-1].condition, "DIFFERENT", res[-1].value, False)]
    assert _red().key(changed) != base, "an upstream step key change invalidates the reduction"
