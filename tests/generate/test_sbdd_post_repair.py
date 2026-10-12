"""Scaffold specification and repair of raw DiffSBDD output (pure RDKit; no GPU, no torch)."""

import re

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from smeltery import Pose
from smeltery.generate.sbdd_post import RepairError, _match_scaffold, build_scaffold, repair, smiles_and_pose

#: The one fixture sample that passes the default filters (tests/data/sbdd/README.md).
SURVIVOR = 12


def _pose(smiles: str) -> Pose:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=7) == 0
    return Pose(tuple(a.GetSymbol() for a in mol.GetAtoms()), np.array(mol.GetConformer().GetPositions()))


@pytest.mark.parametrize(
    "indices, message",
    [
        ([], "non-empty list of distinct"),
        ([1, 1], "non-empty list of distinct"),
        ([0, 9], "out of range"),
        ([-1, 0], "out of range"),
        ([0, 1, 2, 3], "nothing to grow"),
        ([0, 3], "one connected piece"),
    ],
)
def test_build_scaffold_rejects_a_bad_index_list(indices, message):
    with pytest.raises(ValueError, match=message):
        build_scaffold("CCCC", indices, _pose("CCCC"))


def test_build_scaffold_rejects_a_pose_in_another_atom_order():
    """Review focus 1: a pose from another tool is a different atom order, and would be mis-placed silently."""
    with pytest.raises(ValueError, match="does not start with the heavy atoms"):
        build_scaffold("CCO", [0, 1], _pose("OCC"))


def test_build_scaffold_keeps_formal_charges():
    """Review focus 2."""
    spec = build_scaffold("CC[NH3+]", [1, 2], _pose("CC[NH3+]"))
    assert [a.GetFormalCharge() for a in spec.kekule.GetAtoms()] == [0, 1]
    assert [a.GetFormalCharge() for a in spec.scaffold.GetAtoms()] == [0, 1]


def test_scaffold_coordinates_are_the_parents(parent_candidate, scaffold_spec, scaffold_indices):
    n = scaffold_spec.parent.GetNumAtoms()
    heavy = parent_candidate.poses[0].coords_ang[:n]
    assert np.array_equal(scaffold_spec.coords, heavy[scaffold_indices])
    assert scaffold_spec.scaffold.GetNumAtoms() == 32
    assert scaffold_spec.indices == tuple(scaffold_indices)


def test_smiles_and_pose_puts_the_pose_in_smiles_atom_order():
    mol = Chem.MolFromSmiles("C[C@H](N)c1ccccc1O")
    AllChem.EmbedMolecule(mol, randomSeed=3)  # heavy atoms only, in input order
    smiles, pose = smiles_and_pose(mol)
    reference = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert pose.symbols == tuple(a.GetSymbol() for a in reference.GetAtoms())
    order = [int(x) for x in re.findall(r"\d+", mol.GetProp("_smilesAtomOutputOrder"))]
    assert np.array_equal(pose.coords_ang[: mol.GetNumAtoms()], mol.GetConformer().GetPositions()[order])


def test_the_parent_fixture_pose_matches_its_smiles(parent_candidate):
    reference = Chem.AddHs(Chem.MolFromSmiles(parent_candidate.smiles))
    assert parent_candidate.poses[0].symbols == tuple(a.GetSymbol() for a in reference.GetAtoms())
    assert len(parent_candidate.poses[0].symbols) == 71


def _scaffold_bonds_present(mol, mapping, spec):
    return sum(
        mol.GetBondBetweenAtoms(mapping[b.GetBeginAtomIdx()], mapping[b.GetEndAtomIdx()]) is not None
        for b in spec.kekule.GetBonds()
    )


def _positions_and_symbols(mol):
    return np.asarray(mol.GetConformer().GetPositions()), [a.GetSymbol() for a in mol.GetAtoms()]


def test_the_model_drops_scaffold_bonds_and_repair_restores_them(raw_samples, scaffold_spec):
    total = scaffold_spec.kekule.GetNumBonds()
    missing_before = 0
    for raw in raw_samples:
        mapping = _match_scaffold(*_positions_and_symbols(raw), scaffold_spec)
        assert mapping is not None
        if _scaffold_bonds_present(raw, mapping, scaffold_spec) < total:
            missing_before += 1
        fixed, fixed_map = repair(raw, scaffold_spec)
        assert _scaffold_bonds_present(fixed, fixed_map, scaffold_spec) == total
    assert missing_before == 12  # measured: only 4 of the 16 raw samples had every scaffold bond


def test_repair_leaves_no_extra_bonds_between_scaffold_atoms(raw_samples, scaffold_spec):
    for raw in raw_samples:
        fixed, mapping = repair(raw, scaffold_spec)
        inside = set(mapping)
        between = sum(1 for b in fixed.GetBonds() if b.GetBeginAtomIdx() in inside and b.GetEndAtomIdx() in inside)
        assert between == scaffold_spec.kekule.GetNumBonds()


def test_repair_does_not_touch_bonds_the_model_placed_outside_the_scaffold(raw_samples, scaffold_spec):
    raw = raw_samples[SURVIVOR]
    fixed, mapping = repair(raw, scaffold_spec)
    inside = set(mapping)

    def outside(mol):
        return sorted(
            (
                min(b.GetBeginAtomIdx(), b.GetEndAtomIdx()),
                max(b.GetBeginAtomIdx(), b.GetEndAtomIdx()),
                str(b.GetBondType()),
            )
            for b in mol.GetBonds()
            if not (b.GetBeginAtomIdx() in inside and b.GetEndAtomIdx() in inside)
        )

    assert outside(fixed) == outside(raw)


def test_repair_fits_the_scaffold_onto_the_parent(raw_samples, scaffold_spec):
    fixed, mapping = repair(raw_samples[SURVIVOR], scaffold_spec)
    pos = fixed.GetConformer().GetPositions()[mapping]
    deviation = np.linalg.norm(pos - scaffold_spec.coords, axis=1)
    assert np.sqrt((deviation**2).mean()) < 0.1
    assert deviation.max() < 0.15


def test_repair_rejects_a_scaffold_atom_with_the_wrong_element(raw_samples, scaffold_spec):
    raw = Chem.RWMol(raw_samples[SURVIVOR])
    pos, _ = _positions_and_symbols(raw)
    raw.GetAtomWithIdx(int(np.linalg.norm(pos - scaffold_spec.coords[0], axis=1).argmin())).SetAtomicNum(15)
    with pytest.raises(RepairError, match="unique same-element"):
        repair(raw.GetMol(), scaffold_spec)


def test_repair_rejects_an_output_missing_a_scaffold_atom(raw_samples, scaffold_spec):
    raw = Chem.Mol(raw_samples[SURVIVOR])
    pos, _ = _positions_and_symbols(raw)
    far = int(np.linalg.norm(pos - scaffold_spec.coords[0], axis=1).argmin())
    raw.GetConformer().SetAtomPosition(far, (50.0, 50.0, 50.0))
    with pytest.raises(RepairError, match="unique same-element"):
        repair(raw, scaffold_spec)


def test_repair_rejects_a_scaffold_that_cannot_be_fitted_rigidly(raw_samples, scaffold_spec):
    raw = Chem.Mol(raw_samples[SURVIVOR])
    mapping = _match_scaffold(*_positions_and_symbols(raw), scaffold_spec)
    conf = raw.GetConformer()
    for k, j in enumerate(mapping):  # alternate +/-0.35 A: each atom stays matchable, the set is no longer rigid
        x, y, z = conf.GetAtomPosition(j)
        conf.SetAtomPosition(j, (x + (0.35 if k % 2 else -0.35), y, z))
    with pytest.raises(RepairError, match="rmsd"):
        repair(raw, scaffold_spec)
