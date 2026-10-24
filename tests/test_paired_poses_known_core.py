"""PairedPoses with a caller-supplied scaffold core.

Per docs/superpowers/specs/2026-10-22-paired-poses-known-core-design.md. The point of a known core is
to skip the MCS SEARCH (which cannot finish on scaffold-growth output) without skipping its CHECK, so
most of these tests are about the check: it must accept a symmetry-equivalent relabelling and refuse a
genuine conflict.
"""

from __future__ import annotations

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from smeltery import Candidate
from smeltery.model import Pose
from smeltery.tiers import NoCommonCoreError, PairedPoses

# A symmetric piperidine so the automorphism case is reachable: the two ring CH2 arms either side of
# the N are interchangeable, which is exactly the 14<->36 / 15<->35 swap seen on real output.
PARENT = "C1CCN(C)CC1"
ANALOGUE = "C1CCN(CC)CC1"


def _pose(smiles: str, seed: int = 7) -> Pose:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=seed) == 0
    conf = mol.GetConformer()
    xyz = np.array([list(conf.GetAtomPosition(i)) for i in range(mol.GetNumAtoms())])
    return Pose(tuple(a.GetSymbol() for a in mol.GetAtoms()), xyz)


def _heavy_identity(smiles: str) -> list[tuple[int, int]]:
    n = Chem.MolFromSmiles(smiles).GetNumAtoms()
    return [(i, i) for i in range(n)]


def _run(tier: PairedPoses, parent: Candidate, analogue: Candidate) -> None:
    tier.run([analogue], {"parent": parent})


def test_self_pair_anchor_holds_with_a_known_core():
    """Pairing a molecule with itself must copy every atom, as on the MCS path."""
    parent = Candidate("parent", PARENT, poses=[_pose(PARENT)])
    analogue = Candidate("self", PARENT, poses=[_pose(PARENT)])
    tier = PairedPoses(parent_poses_given=True, n_poses=1, known_cores={"self": _heavy_identity(PARENT)})
    _run(tier, parent, analogue)
    # every mapped atom is an exact copy of the parent's coordinate
    assert np.allclose(analogue.poses[0].coords_ang, parent.poses[0].coords_ang, atol=1e-9)
    assert not tier.unvalidated_cores


def test_known_core_matches_the_mcs_path():
    """With a mapping the MCS would also pick, the poses are identical either way."""
    parent = Candidate("parent", PARENT, poses=[_pose(PARENT)])
    a_mcs = Candidate("a", ANALOGUE, poses=[_pose(ANALOGUE)])
    a_known = Candidate("a", ANALOGUE, poses=[_pose(ANALOGUE)])
    _run(PairedPoses(parent_poses_given=True, n_poses=1), parent, a_mcs)
    core = Chem.MolFromSmiles(PARENT)
    match = Chem.MolFromSmiles(ANALOGUE).GetSubstructMatch(core)
    assert match, "the analogue must contain the parent skeleton for this test to mean anything"
    known = [(int(a), int(p)) for p, a in enumerate(match)]
    _run(
        PairedPoses(parent_poses_given=True, n_poses=1, known_cores={"a": known}),
        parent,
        a_known,
    )
    mapped = [a for a, _ in known]
    got = np.array(a_known.poses[0].coords_ang)[mapped]
    want = np.array(a_mcs.poses[0].coords_ang)[mapped]
    assert np.allclose(got, want, atol=1e-9)


def test_symmetry_equivalent_relabelling_is_accepted():
    """A core that differs from the MCS pick only by a ring automorphism must NOT be refused.

    Strict atom-identity comparison rejected 11 of 130 real molecules on exactly this.
    """
    mol = Chem.MolFromSmiles(PARENT)
    core = Chem.MolFromSmiles(PARENT)
    matches = mol.GetSubstructMatches(core, uniquify=False)
    assert len(matches) > 1, "PARENT must be symmetric for this test to exercise anything"
    # a non-identity automorphism of the parent onto itself
    alt = next(m for m in matches if list(m) != list(range(mol.GetNumAtoms())))
    known = [(int(a), int(p)) for p, a in enumerate(alt)]
    parent = Candidate("parent", PARENT, poses=[_pose(PARENT)])
    analogue = Candidate("sym", PARENT, poses=[_pose(PARENT)])
    tier = PairedPoses(parent_poses_given=True, n_poses=1, known_cores={"sym": known})
    _run(tier, parent, analogue)  # must not raise
    assert len(tier.scaffold_maps["sym"]) >= len(known)


def test_element_mismatch_is_refused():
    parent = Candidate("parent", PARENT, poses=[_pose(PARENT)])
    analogue = Candidate("bad", ANALOGUE, poses=[_pose(ANALOGUE)])
    # map an analogue carbon onto the parent's nitrogen
    pmol, amol = Chem.MolFromSmiles(PARENT), Chem.MolFromSmiles(ANALOGUE)
    n_idx = next(a.GetIdx() for a in pmol.GetAtoms() if a.GetSymbol() == "N")
    c_idx = next(a.GetIdx() for a in amol.GetAtoms() if a.GetSymbol() == "C")
    tier = PairedPoses(parent_poses_given=True, n_poses=1, known_cores={"bad": [(c_idx, n_idx)]})
    with pytest.raises(NoCommonCoreError, match="structural correspondence"):
        _run(tier, parent, analogue)


def test_conflicting_mapping_is_refused():
    """A mapping that is structurally fine but disagrees with every MCS match must refuse."""
    pmol = Chem.MolFromSmiles(PARENT)
    n = pmol.GetNumAtoms()
    # rotate the ring mapping onto itself: element- and bond-preserving where it maps, but not an
    # automorphism of the whole core, so it should agree with no MCS match
    known = [(i, (i + 1) % n) for i in range(n)]
    parent = Candidate("parent", PARENT, poses=[_pose(PARENT)])
    analogue = Candidate("conflict", PARENT, poses=[_pose(PARENT)])
    tier = PairedPoses(parent_poses_given=True, n_poses=1, known_cores={"conflict": known})
    with pytest.raises(NoCommonCoreError):
        _run(tier, parent, analogue)


def test_hydrogens_are_extended_from_the_heavy_core():
    """Every mapped heavy atom's hydrogens are mapped too, so the copied core includes them."""
    parent = Candidate("parent", PARENT, poses=[_pose(PARENT)])
    analogue = Candidate("h", PARENT, poses=[_pose(PARENT)])
    heavy = _heavy_identity(PARENT)
    tier = PairedPoses(parent_poses_given=True, n_poses=1, known_cores={"h": heavy})
    _run(tier, parent, analogue)
    mapping = tier.scaffold_maps["h"]
    withh = Chem.AddHs(Chem.MolFromSmiles(PARENT))
    assert len(mapping) == withh.GetNumAtoms(), "a self-core must map every atom, hydrogens included"
    mapped_h = [a for a, _ in mapping if withh.GetAtomWithIdx(a).GetAtomicNum() == 1]
    assert len(mapped_h) == withh.GetNumAtoms() - len(heavy)


def test_scaffold_maps_is_not_read_as_input():
    """`scaffold_maps` is output. A stale value there must not be trusted as a core on the next run."""
    parent = Candidate("parent", PARENT, poses=[_pose(PARENT)])
    analogue = Candidate("a", ANALOGUE, poses=[_pose(ANALOGUE)])
    tier = PairedPoses(parent_poses_given=True, n_poses=1)
    tier.scaffold_maps = {"a": [(0, 99999)]}  # nonsense that would explode if used as a mapping
    _run(tier, parent, analogue)  # goes through the MCS path, ignoring the stale map
    assert tier.scaffold_maps["a"] != [(0, 99999)]
