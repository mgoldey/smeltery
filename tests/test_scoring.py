"""ScoringProvider (#21): the type system refuses to difference a rank-only score."""

import numpy as np
import pytest

from smeltery import (
    Candidate, IncomparableError, Pose, Rescoring, Score, ScoringProvider, UnmeasuredFloorError,
    VinaScoreProvider, cut, paired_delta, tier_floor,
)
from smeltery.model import Measurement


class Double:
    """Test-double 'ML' provider: value per pose from a table, optional uncertainty."""

    def __init__(self, table, delta_g, unc=None, name="double"):
        self.name, self.table, self.delta_g, self.unc = name, table, delta_g, unc
        self._i = 0

    def score(self, poses, receptor):
        out = [Score(self.table[self._i + k], "kcal/mol", self.delta_g, self.unc) for k in range(len(poses))]
        self._i += len(poses)
        return out

    def settings(self):
        return {"table": list(self.table)}


def _cand(name, n=3):
    return Candidate(name, "C", [Pose(("H",), np.zeros((1, 3)))] * n)


def _run(provider, names=("parent", "a")):
    cands = [_cand(n) for n in names]
    tier = Rescoring(provider)
    tier.run(cands, {})
    return tier, cands


def test_double_is_a_provider():
    assert isinstance(Double([0.0], True), ScoringProvider)
    assert isinstance(VinaScoreProvider([0.0]), ScoringProvider)


def test_rank_only_provider_cannot_be_differenced_and_message_explains():
    tier, (parent, a) = _run(Double([1, 2, 3, 2, 3, 4], delta_g=False))
    with pytest.raises(IncomparableError, match="not a free energy.*rank-only"):
        paired_delta(parent, a, "rescore", tier)


def test_delta_g_provider_can_be_differenced_and_unrun_tier_is_not_trusted():
    tier, (parent, a) = _run(Double([1, 2, 3, 2, 3, 4], delta_g=True))
    m = paired_delta(parent, a, "rescore", tier)
    assert m.mean == pytest.approx(1.0) and m.unit == "kcal/mol"
    assert Rescoring(Double([], True)).is_delta_g is False


def test_vina_replay_is_rank_only():
    c = _cand("x", 2)
    t = Rescoring(VinaScoreProvider([-7.0, -6.0]), "vina")
    t.run([c], {})
    assert c.per_pose["vina"] == [-7.0, -6.0] and not t.is_delta_g
    with pytest.raises(ValueError, match="2 recorded"):
        VinaScoreProvider([-7.0, -6.0]).score([c.poses[0]], None)


def test_uncertainty_becomes_the_floor_and_creates_ties():
    def groups(unc):
        tier, (p, a) = _run(Double([0, 0, 0, 3, 3.1, 2.9], True, unc))
        ms = {"a": paired_delta(p, a, "rescore", tier), "parent": Measurement.from_samples([0.0, 0.1, -0.1], "kcal/mol")}
        return cut(ms, keep=1, tier=tier, quantity="rescore").groups

    assert groups(0.1) == [["parent"], ["a"]]  # sharp provider ranks
    assert groups(5.0) == [["parent", "a"]]    # noisy provider: a tie


def test_no_uncertainty_means_unmeasured_floor_unless_explicit():
    tier, _ = _run(Double([1, 2, 3, 2, 3, 4], True))
    with pytest.raises(UnmeasuredFloorError):
        tier_floor(tier, "rescore")
    t2 = Rescoring(Double([1, 2, 3], True), floor=0.7)
    assert tier_floor(t2, "rescore") == 0.7


@pytest.mark.parametrize("make", [
    lambda: Double([0, 0, 0, 1, 1, 1], True, 0.01),
    lambda: Double([5, 5, 5, 6, 6, 6], True, 0.01, name="other"),
])
def test_swapping_providers_needs_no_funnel_change(make):
    tier, (p, a) = _run(make())
    assert paired_delta(p, a, "rescore", tier).mean == pytest.approx(1.0)


def test_nan_and_mixed_semantics_rejected():
    with pytest.raises(ValueError):
        Score(float("nan"), "kcal/mol", True)

    class Mixed(Double):
        def score(self, poses, receptor):
            return [Score(1.0, "kcal/mol", True), Score(1.0, "kcal/mol", False)][: len(poses)]

    with pytest.raises(ValueError, match="mixed"):
        Rescoring(Mixed([], True)).run([_cand("x", 2)], {})
