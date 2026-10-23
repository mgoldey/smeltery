"""Scaffold-aware pairing: the generator's known scaffold mapping unblocks model poses.

The all-atom MCS search cannot finish on scaffold-growth output, so a model-pose candidate cannot be
paired by search alone. Passing the repair mapping explicitly through `PairedPoses(known_cores=...)`
pairs it, and the mapping is cross-validated against a heavy-atom MCS on the way in (see
docs/superpowers/specs/2026-10-22-paired-poses-known-core-design.md).
"""

import pytest

from smeltery.generate.sbdd_post import FilterConfig, process_samples
from smeltery.tiers import NoCommonCoreError, PairedPoses


def test_the_mcs_search_alone_cannot_pair_a_model_pose(raw_samples, scaffold_spec, parent_candidate):
    """The motivation for `known_cores`: search times out, so the pair is refused."""
    candidates, _ = process_samples(raw_samples, scaffold_spec, FilterConfig(), pose_source="model")
    cand = candidates[0]
    tier = PairedPoses(parent_poses_given=True, analogue_poses_given=True, mcs_timeout_s=2)
    with pytest.raises(NoCommonCoreError, match="timed out"):
        tier.run([cand], {"parent": parent_candidate})


def test_a_known_core_pairs_the_same_candidate(raw_samples, scaffold_spec, parent_candidate):
    candidates, _ = process_samples(raw_samples, scaffold_spec, FilterConfig(), pose_source="model")
    assert len(candidates) == 1 and len(candidates[0].poses) == 1
    cand = candidates[0]
    # The repair mapping: one entry per scaffold atom, (analogue heavy atom, parent heavy atom).
    known = cand.scaffold_map
    assert len(known) == len(scaffold_spec.indices)
    tier = PairedPoses(
        parent_poses_given=True,
        analogue_poses_given=True,
        mcs_timeout_s=2,
        known_cores={cand.name: known},
    )
    tier.run([cand], {"parent": parent_candidate})
    assert len(cand.poses) == len(parent_candidate.poses)
    assert cand.name in tier.scaffold_maps
    # Hydrogens were extended onto the heavy core, so the published map is the larger one.
    assert len(tier.scaffold_maps[cand.name]) >= len(known)
    # It was checked, not merely trusted.
    assert cand.name not in tier.unvalidated_cores


def test_a_known_core_must_be_passed_explicitly(raw_samples, scaffold_spec, parent_candidate):
    """A mapping on the candidate is NOT a side channel into the tier: it must go through known_cores.

    Otherwise a core reaches the pairing unvalidated, which is what the cross-check exists to prevent.
    """
    candidates, _ = process_samples(raw_samples, scaffold_spec, FilterConfig(), pose_source="model")
    cand = candidates[0]
    assert cand.scaffold_map, "the candidate does carry a mapping"
    tier = PairedPoses(parent_poses_given=True, analogue_poses_given=True, mcs_timeout_s=2)
    with pytest.raises(NoCommonCoreError):
        tier.run([cand], {"parent": parent_candidate})
