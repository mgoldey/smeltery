"""Pins a measured limitation: the generator's own model pose is refused by PairedPoses on the real candidate.

On the fixture's one accepted 41-heavy-atom-parent candidate, PairedPoses(parent_poses_given=True,
analogue_poses_given=True) cannot finish its MCS search against the 71-atom parent (the search reaches 54 atoms and
never proves maximality), and, were the search bypassed, the mapped core would sit up to 2.2 A from the parent's
pose 0 because the MCS picks a symmetry-degenerate assignment. Both refusals are correct. This test is to be
updated when PairedPoses can take a known scaffold mapping.
"""

import pytest

from smeltery.generate.sbdd_post import FilterConfig, process_samples
from smeltery.tiers import NoCommonCoreError, PairedPoses


def test_the_real_model_pose_candidate_is_refused_by_the_pairing(raw_samples, scaffold_spec, parent_candidate):
    candidates, _ = process_samples(raw_samples, scaffold_spec, FilterConfig(), pose_source="model")
    assert len(candidates) == 1 and len(candidates[0].poses) == 1
    tier = PairedPoses(parent_poses_given=True, analogue_poses_given=True, mcs_timeout_s=2)
    with pytest.raises(NoCommonCoreError):
        tier.run([parent_candidate, candidates[0]], {"parent": parent_candidate})
