"""PairedPoses with analogue_poses_given: a given pose keeps its new atoms, the core is still an exact copy."""

import numpy as np
import pytest

from smeltery import Candidate, PointCharge, Pose, paired_delta
from smeltery.tiers import FieldInteraction, PairedPoses

ACID = "OC(=O)c1ccccc1"
FLUORO = "OC(=O)c1ccc(F)cc1"


def _default_run():
    parent, fluoro = Candidate("parent", ACID), Candidate("4-F", FLUORO)
    tier = PairedPoses(n_poses=3)
    tier.run([parent, fluoro], {"parent": parent})
    return parent, fluoro, tier.scaffold_maps["4-F"]


def _given_run(parent, given_pose):
    p2 = Candidate("parent", ACID, poses=list(parent.poses))
    a2 = Candidate("4-F", FLUORO, poses=[given_pose])
    tier = PairedPoses(parent_poses_given=True, analogue_poses_given=True)
    tier.run([p2, a2], {"parent": p2})
    return a2, tier.scaffold_maps["4-F"]


def _perturbed(fluoro, mapping, core_shift, new_shift):
    core = {a for a, _ in mapping}
    unmapped = [i for i in range(len(fluoro.poses[0].symbols)) if i not in core]
    xyz = np.array(fluoro.poses[0].coords_ang)
    xyz[sorted(core)] += core_shift
    xyz[unmapped] += new_shift
    return Pose(fluoro.poses[0].symbols, xyz), unmapped


def test_the_flag_is_a_reported_setting_and_defaults_off():
    assert PairedPoses().analogue_poses_given is False
    assert PairedPoses(analogue_poses_given=True).settings()["analogue_poses_given"] is True


def test_a_given_pose_keeps_its_new_atoms_and_copies_the_core_from_the_parent():
    parent, fluoro, mapping = _default_run()
    given, unmapped = _perturbed(fluoro, mapping, core_shift=0.01, new_shift=np.array([0.3, 0.0, 0.0]))
    assert unmapped, "the fluorine must lie outside the common core"
    a2, mapping2 = _given_run(parent, given)
    assert mapping2 == mapping
    pose0 = a2.poses[0].coords_ang
    for a, p in mapping:
        assert np.array_equal(pose0[a], parent.poses[0].coords_ang[p])
    assert np.array_equal(pose0[unmapped], given.coords_ang[unmapped])
    assert len(a2.poses) == len(parent.poses)


def test_the_other_poses_are_built_as_without_the_option():
    parent, fluoro, mapping = _default_run()
    given, _ = _perturbed(fluoro, mapping, core_shift=0.0, new_shift=np.array([0.3, 0.0, 0.0]))
    a2, _ = _given_run(parent, given)
    for i in range(1, len(parent.poses)):
        assert np.array_equal(a2.poses[i].coords_ang, fluoro.poses[i].coords_ang)


def test_a_given_pose_in_another_atom_order_is_refused():
    parent, fluoro, _ = _default_run()
    pose = fluoro.poses[0]
    with pytest.raises(ValueError, match="atom order"):
        _given_run(parent, Pose(tuple(reversed(pose.symbols)), pose.coords_ang))


def test_a_given_pose_whose_core_is_not_on_the_parent_is_refused():
    parent, fluoro, mapping = _default_run()
    given, _ = _perturbed(fluoro, mapping, core_shift=np.array([1.0, 0.0, 0.0]), new_shift=0.0)
    with pytest.raises(ValueError, match="sits"):
        _given_run(parent, given)


def test_without_the_flag_a_given_pose_is_ignored():
    parent, fluoro, _ = _default_run()
    p2 = Candidate("parent", ACID, poses=list(parent.poses))
    junk = Pose(tuple(reversed(fluoro.poses[0].symbols)), fluoro.poses[0].coords_ang)
    a2 = Candidate("4-F", FLUORO, poses=[junk])
    PairedPoses(parent_poses_given=True).run([p2, a2], {"parent": p2})
    assert np.array_equal(a2.poses[0].coords_ang, fluoro.poses[0].coords_ang)


def test_self_pair_with_given_poses_copies_every_coordinate():
    parent = Candidate("parent", ACID)
    PairedPoses(n_poses=3).run([parent], {"parent": parent})
    twin = Candidate("twin", ACID, poses=list(parent.poses))
    PairedPoses(parent_poses_given=True, analogue_poses_given=True).run([parent, twin], {"parent": parent})
    assert [p.coords_ang.tolist() for p in twin.poses] == [p.coords_ang.tolist() for p in parent.poses]


@pytest.mark.needs_ferric
def test_self_pair_with_given_poses_gives_exactly_zero_ddE():
    parent = Candidate("parent", ACID)
    PairedPoses(n_poses=2).run([parent], {"parent": parent})
    twin = Candidate("twin", ACID, poses=list(parent.poses))
    PairedPoses(parent_poses_given=True, analogue_poses_given=True).run([parent, twin], {"parent": parent})
    field = [PointCharge(1.0, (3.0, 0.0, 0.0)), PointCharge(-0.5, (0.0, 3.5, 0.0))]
    FieldInteraction().run([parent, twin], {"field": field})
    assert paired_delta(parent, twin, "dE_int").per_pose == (0.0, 0.0)
    assert all(abs(v) > 1e-3 for v in parent.per_pose["dE_int"])  # not vacuous: the field does something
