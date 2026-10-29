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
    with pytest.raises(ReductionError, match="pools unlike conditions"):
        r.group(_results("props", DATA))
    # Deliberate pooling is possible, and says so.
    assert len(_red(within=(), strict=False).group(_results("props", DATA))) == 1


def test_a_factor_cannot_be_both_varied_and_fixed():
    with pytest.raises(ReductionError, match="cannot be both"):
        _red(over=("arm",), within=("arm", "cell"))


def test_grouping_buckets_by_within_then_over():
    groups = _red().group(_results("props", DATA))
    assert [g.within.label() for g in groups] == ["cell=c1", "cell=c2"]
    assert all(set(g.arms) == {Condition(arm="a"), Condition(arm="b")} for g in groups)
    assert groups[0].n == 2, "two step values: one per arm"
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
    assert e.detail["favouring_first"] == 2


def test_unpaired_groups_are_counted_not_dropped():
    """The anchor. 4 cells where one arm produced nothing vanished from the headline."""
    import statistics as st

    data = {**DATA, ("c3", "a"): [-30.0, -31.0]}  # no 'b' arm
    e = paired_difference(_red().group(_results("props", data)), lambda vs: st.median(vs))
    assert e.n == 2 and e.detail["unpaired_groups"] == 1


def test_a_difference_with_no_paired_group_is_none_not_zero():
    e = paired_difference(_red().group(_results("props", {("c1", "a"): [1.0]})), lambda vs: vs[0])
    assert e.value is None and e.n == 0
    assert "no group has both arms" in e.detail["reason"]


def test_paired_difference_checks_the_floor_it_is_given():
    import statistics as st

    groups = _red().group(_results("props", DATA))
    with pytest.raises(FloorKindError):
        paired_difference(groups, lambda vs: st.median(vs), floor=Floor(4.7, "kcal/mol", ABSOLUTE, "preps"))
    e = paired_difference(groups, lambda vs: st.median(vs), floor=Floor(0.5, "kcal/mol", DIFFERENCE, "measured"))
    assert e.detail["resolved"] is True  # |-1.25| > 0.5


def test_a_rate_cannot_be_quoted_without_its_denominator():
    """The anchor. 6/324 and 0/59 have overlapping intervals; the bare counts did not show it."""
    pxm = rate(
        _red(within=(), strict=False).group(_results("props", {("c1", "a"): [1.0] * 6 + [0.0] * 318})),
        lambda vs: (int(sum(vs)), len(vs)),
    )
    sbdd = rate(
        _red(within=(), strict=False).group(_results("props", {("c1", "b"): [0.0] * 59})),
        lambda vs: (int(sum(vs)), len(vs)),
    )
    assert pxm.n == 324 and sbdd.n == 59
    assert pxm.has_interval and sbdd.has_interval
    assert sbdd.value == 0.0 and sbdd.hi > pxm.value, (
        "zero events is not evidence of a lower rate: the interval must reach above the other arm"
    )


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
