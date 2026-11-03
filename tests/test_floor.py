"""`Floor`: the kind travels with the number, so the wrong floor cannot be used silently.

The anchor is `test_an_absolute_floor_is_refused_where_a_difference_floor_belongs`. That mistake was
made on a real result in this project -- a -4.7 kcal/mol absolute prep-sensitivity floor applied to a
-1.21 kcal/mol difference between two generators, declaring it unresolved when the floor that
actually applies is smaller by roughly sqrt(n). It was caught by re-deriving the arithmetic by hand.
These tests are that catch, made automatic.
"""

from __future__ import annotations

import numpy as np
import pytest

from smeltery.floor import ABSOLUTE, DIFFERENCE, Floor, FloorKindError, from_sensitivity
from smeltery.gates import charge_sensitivity
from smeltery.model import Candidate, Pose


def _c(name: str) -> Candidate:
    return Candidate(name, "C", [Pose(("C",), np.zeros((1, 3)))])


def _report(shifts: dict[str, float]):
    """A sensitivity report whose per-candidate shift (b - a) is exactly `shifts[name]`."""
    cands = [_c(n) for n in shifts]
    base = {n: -20.0 for n in shifts}
    return charge_sensitivity(
        cands,
        "dE_int",
        lambda c, q: base[c.name],
        lambda c, q: base[c.name] + shifts[c.name],
    )


def test_an_absolute_floor_is_refused_where_a_difference_floor_belongs():
    """The anchor. A bare float could not have refused this."""
    absolute = Floor(4.7, "kcal/mol", ABSOLUTE, "two pocket preps, n=5")
    with pytest.raises(FloorKindError, match="absolute floor"):
        absolute.check(DIFFERENCE)
    with pytest.raises(FloorKindError):
        absolute.resolves(1.21)
    # and the other way round, so neither kind is privileged
    diff = Floor(0.44, "kcal/mol", DIFFERENCE, "sensitivity sd scaled to 299 and 54")
    with pytest.raises(FloorKindError, match="difference floor"):
        diff.check(ABSOLUTE)


def test_the_error_names_both_kinds_and_the_source():
    """A refusal a reader cannot act on is only an obstacle."""
    f = Floor(4.7, "kcal/mol", ABSOLUTE, "two pocket preps, n=5")
    with pytest.raises(FloorKindError) as e:
        f.check(DIFFERENCE)
    msg = str(e.value)
    assert "absolute" in msg and "difference" in msg
    assert "two pocket preps, n=5" in msg
    assert "4.7" in msg


def test_a_difference_floor_resolves_what_exceeds_it():
    f = Floor(0.44, "kcal/mol", DIFFERENCE, "measured")
    assert f.resolves(-1.21) is True
    assert f.resolves(0.2) is False
    assert f.resolves(-0.44) is False  # equal is not greater


def test_relabelling_is_possible_but_recorded():
    """Using the wrong floor knowingly is a decision; it must leave a trace."""
    f = Floor(4.7, "kcal/mol", ABSOLUTE, "two pocket preps")
    r = f.as_kind(DIFFERENCE, "no difference floor measured yet; conservative")
    assert r.applies_to == DIFFERENCE
    assert r.resolves(5.0) is True
    assert "relabelled absolute->difference" in r.source
    assert "conservative" in r.source
    with pytest.raises(ValueError, match="needs a reason"):
        f.as_kind(DIFFERENCE, "")


def test_a_floor_must_be_sourced_signed_and_known():
    with pytest.raises(ValueError, match="source"):
        Floor(1.0, "kcal/mol", ABSOLUTE, "")
    with pytest.raises(ValueError, match="negative"):
        Floor(-1.0, "kcal/mol", ABSOLUTE, "x")
    with pytest.raises(ValueError, match="applies_to"):
        Floor(1.0, "kcal/mol", "vibes", "x")


# ---------------------------------------------------------------- from a sensitivity report


def test_common_mode_cancels_and_the_spread_does_not():
    """A shift identical on every candidate moves no difference: spread 0, floor 0."""
    rep = _report({"a": 3.0, "b": 3.0, "c": 3.0})
    assert rep.common_mode == pytest.approx(3.0)
    assert rep.shift_sd == pytest.approx(0.0)
    assert rep.floor == pytest.approx(0.0)  # range, also 0
    assert rep.aggregate_difference_floor(10, 10) == pytest.approx(0.0)


def test_the_difference_floor_shrinks_with_group_size_and_the_absolute_one_does_not():
    """The whole point: averaging over molecules buys precision a single value never gets."""
    rep = _report({"a": 0.0, "b": 3.0, "c": 6.0})
    assert rep.shift_sd == pytest.approx(3.0)
    absolute = from_sensitivity(rep)
    assert absolute.applies_to == ABSOLUTE
    assert absolute.value == pytest.approx(6.0)  # range, max - min

    small = from_sensitivity(rep, n_a=3, n_b=3)
    big = from_sensitivity(rep, n_a=299, n_b=54)
    assert small.applies_to == DIFFERENCE
    assert big.value < small.value < absolute.value
    # sd * sqrt(1/299 + 1/54) = 3.0 * 0.1458...
    assert big.value == pytest.approx(3.0 * np.sqrt(1 / 299 + 1 / 54), rel=1e-9)
    assert big.value == pytest.approx(0.4436, abs=0.001)


def test_the_real_case_the_absolute_floor_got_wrong():
    """With a 3 kcal/mol per-molecule spread, a -1.21 group difference RESOLVES at n=299 vs 54,
    though it sits far inside the 4.7 absolute floor that was quoted against it."""
    rep = _report({f"m{i}": v for i, v in enumerate([-3.0, 0.0, 3.0, -3.0, 3.0, 0.0])})
    diff_floor = from_sensitivity(rep, n_a=299, n_b=54)
    assert diff_floor.value < 0.5
    assert diff_floor.resolves(-1.21) is True
    assert Floor(4.7, "kcal/mol", ABSOLUTE, "preps").as_kind(DIFFERENCE, "wrongly").resolves(-1.21) is False


def test_the_difference_floor_states_its_assumption_in_its_source():
    """It assumes independence across candidates, which scaffolds violate; a reader must see that."""
    f = from_sensitivity(_report({"a": 0.0, "b": 2.0, "c": 4.0}), n_a=10, n_b=10)
    assert "independent" in f.source and "LOWER bound" in f.source


def test_n_must_be_given_for_both_groups_or_neither():
    rep = _report({"a": 0.0, "b": 2.0})
    with pytest.raises(ValueError, match="both"):
        from_sensitivity(rep, n_a=5)
    with pytest.raises(ValueError, match="at least one candidate"):
        rep.aggregate_difference_floor(0, 5)


def test_shift_sd_is_nan_for_a_single_candidate_not_zero():
    """One candidate shows no spread; reporting 0.0 would claim a comparison is unlimited."""
    rep = _report({"a": 1.0, "b": 1.0})
    object.__setattr__(rep, "deltas", {"a": 1.0})
    assert np.isnan(rep.shift_sd)


def test_str_carries_the_kind_so_a_pasted_number_is_still_checkable():
    f = Floor(0.44, "kcal/mol", DIFFERENCE, "sensitivity sd, n=12")
    assert "difference" in str(f) and "0.44" in str(f) and "sensitivity sd, n=12" in str(f)
