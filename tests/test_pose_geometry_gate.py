"""`require_passing_poses` refuses a pose that is not the molecule it is labelled as (the stranded-halogen failure)."""

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from smeltery import Candidate, Pose
from smeltery.gates import PoseGateError, PoseGeometryError, pose_geometry_problems, require_passing_poses

SMILES = "Fc1ccccc1"  # fluorobenzene


def _pose(smiles: str = SMILES) -> Pose:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=7) == 0
    xyz = np.array([list(mol.GetConformer().GetAtomPosition(i)) for i in range(mol.GetNumAtoms())])
    return Pose(tuple(a.GetSymbol() for a in mol.GetAtoms()), xyz)


def _moved(pose: Pose, idx: int, to: np.ndarray) -> Pose:
    x = pose.coords_ang.copy()
    x[idx] = to
    return Pose(pose.symbols, x)


def test_a_sound_pose_passes():
    assert pose_geometry_problems(SMILES, _pose()) == []
    require_passing_poses(Candidate("ok", SMILES, [_pose()]))


def test_a_stranded_halogen_is_refused_before_any_scf():
    p = _pose()
    far = _moved(p, 0, p.coords_ang[1] + np.array([8.2, 0.0, 0.0]))  # F 8.2 A from its carbon, as in bnz-C34-F
    msgs = pose_geometry_problems(SMILES, far)
    assert any("connected pieces" in m and "F0" in m for m in msgs)
    assert any("bonded atoms too far apart" in m for m in msgs)
    with pytest.raises(PoseGeometryError, match="relax_unmapped=True"):
        require_passing_poses(Candidate("bad", SMILES, [p, far]))


def test_the_error_is_a_pose_gate_error_so_existing_handlers_catch_it():
    p = _pose()
    far = _moved(p, 0, p.coords_ang[1] + np.array([5.0, 0, 0]))
    with pytest.raises(PoseGateError):
        require_passing_poses(Candidate("bad", SMILES, [far]))


def test_the_limit_is_the_openbabel_rule_not_a_fitted_number():
    p = _pose()
    c_f = np.linalg.norm(p.coords_ang[0] - p.coords_ang[1])
    direction = (p.coords_ang[0] - p.coords_ang[1]) / c_f
    # C 0.76 + F 0.57 + 0.45 = 1.78 A: 1.70 A passes, 1.90 A is refused
    assert pose_geometry_problems(SMILES, _moved(p, 0, p.coords_ang[1] + 1.70 * direction)) == []
    assert pose_geometry_problems(SMILES, _moved(p, 0, p.coords_ang[1] + 1.90 * direction)) != []


def test_atom_order_is_not_assumed_for_the_connectivity_check():
    p = _pose()
    perm = list(reversed(range(len(p.symbols))))
    shuffled = Pose(tuple(p.symbols[i] for i in perm), p.coords_ang[perm])
    assert pose_geometry_problems(SMILES, shuffled) == []  # a valid pose in another order is not refused
    far = _moved(p, 0, p.coords_ang[1] + np.array([8.0, 0, 0]))
    far_shuffled = Pose(tuple(far.symbols[i] for i in perm), far.coords_ang[perm])
    assert any("connected pieces" in m for m in pose_geometry_problems(SMILES, far_shuffled))


def test_a_missing_or_extra_atom_is_refused():
    p = _pose()
    drop = Pose(p.symbols[:-1], p.coords_ang[:-1])
    assert any("composition" in m for m in pose_geometry_problems(SMILES, drop))


def test_a_multi_fragment_smiles_expects_its_fragments():
    smi = "[Na+].[Cl-]"
    far = Pose(("Na", "Cl"), np.array([[0.0, 0, 0], [6.0, 0, 0]]))
    assert pose_geometry_problems(smi, far) == []  # two fragments, two pieces
    near = Pose(("Na", "Cl"), np.array([[0.0, 0, 0], [2.4, 0, 0]]))
    assert pose_geometry_problems(smi, near) != []  # bonded-looking: one piece where the SMILES has two


def test_atoms_on_top_of_each_other_are_refused():
    p = _pose()
    x = p.coords_ang.copy()
    x[2] = x[1] + 0.1
    assert any("on top of each other" in m for m in pose_geometry_problems(SMILES, Pose(p.symbols, x)))
