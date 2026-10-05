"""Exactness anchors through the real tiers (RDKit + ferric). Seconds, not minutes.

These are the trivial limits where the machinery must do nothing. If either
one fails, no ΔΔE the funnel reports can be trusted.
"""

import ferric

from smeltery import ANGSTROM_TO_BOHR, Candidate, PointCharge, paired_delta
from smeltery.tiers import FieldInteraction, PairedPoses

ACID = "OC(=O)c1ccccc1"


def _field():
    return [PointCharge(1.0, (3.0, 0.0, 0.0)), PointCharge(-0.5, (0.0, 3.5, 0.0))]


def test_self_pair_gives_exactly_zero_on_every_pose():
    parent = Candidate("parent", ACID)
    twin = Candidate("twin", ACID)  # same molecule, separate candidate
    PairedPoses(n_poses=3).run([parent, twin], {"parent": parent})
    assert [p.coords_ang.tolist() for p in twin.poses] == [p.coords_ang.tolist() for p in parent.poses]
    FieldInteraction().run([parent, twin], {"field": _field()})
    dd = paired_delta(parent, twin, "dE_int")
    assert dd.per_pose == (0.0, 0.0, 0.0), dd.per_pose
    # the anchor must not be vacuous: the field really did something
    assert all(abs(v) > 1e-3 for v in parent.per_pose["dE_int"])


def test_empty_field_gives_exactly_zero_interaction():
    c = Candidate("c", ACID)
    PairedPoses(n_poses=2).run([c], {"parent": c})
    FieldInteraction().run([c], {"field": []})
    assert c.per_pose["dE_int"] == [0.0, 0.0]


def test_analogue_core_is_copied_exactly_from_the_parent_pose():
    parent = Candidate("parent", ACID)
    fluoro = Candidate("4-F", "OC(=O)c1ccc(F)cc1")
    PairedPoses(n_poses=2).run([parent, fluoro], {"parent": parent})
    # every parent coordinate except the replaced para-H appears verbatim in the analogue
    for pp, ap in zip(parent.poses, fluoro.poses, strict=True):
        a_rows = {tuple(r) for r in ap.coords_ang.tolist()}
        missing = [s for s, r in zip(pp.symbols, pp.coords_ang.tolist(), strict=True) if tuple(r) not in a_rows]
        assert missing == ["H"], missing


def test_bohr_factor_is_exactly_the_one_ferric_uses_for_xyz():
    """Point charges (converted here) and atoms (converted by ferric) must share a frame."""
    he = ferric.Molecule.from_xyz_string("1\n\nHe 1.0 0.0 0.0\n")
    assert he.coords_bohr()[0][0] == ANGSTROM_TO_BOHR
