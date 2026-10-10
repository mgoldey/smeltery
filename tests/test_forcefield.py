"""ForceField (MMFF) tier: RDKit only, no ferric (issue #18)."""

import copy

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from smeltery import Candidate, PoseGateError
from smeltery.model import Pose
from smeltery.tiers import ForceField, ForceFieldTypingError, UndeclaredQuantityError, run_checked

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"


def _cand(smiles=ASPIRIN, seed=1, name="c"):
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=seed) == 0  # raw ETKDG: deliberately NOT relaxed
    xyz = np.array([list(mol.GetConformer().GetAtomPosition(i)) for i in range(mol.GetNumAtoms())])
    return Candidate(name, smiles, [Pose(tuple(a.GetSymbol() for a in mol.GetAtoms()), xyz)])


def test_never_writes_coordinates_back_and_reports_strain():
    cand = _cand()
    before = copy.deepcopy(cand.poses)
    run_checked(ForceField(), [cand], {})
    for a, b in zip(before, cand.poses, strict=True):
        assert a.symbols == b.symbols and np.array_equal(a.coords_ang, b.coords_ang)
    e, e_rel = cand.per_pose["E_mmff"][0], cand.per_pose["E_mmff_relaxed"][0]
    assert e_rel < e  # a raw embedding is strained; relaxing a copy lowers the energy
    assert e - e_rel > 1.0


def test_an_already_relaxed_pose_has_near_zero_strain():
    cand = _cand()
    run_checked(ForceField(), [cand], {})
    mol = Chem.AddHs(Chem.MolFromSmiles(ASPIRIN))
    AllChem.EmbedMolecule(mol, randomSeed=1)
    AllChem.MMFFOptimizeMolecule(mol, maxIters=2000)
    xyz = np.array([list(mol.GetConformer().GetAtomPosition(i)) for i in range(mol.GetNumAtoms())])
    relaxed = Candidate("r", ASPIRIN, [Pose(cand.poses[0].symbols, xyz)])
    ForceField().run([relaxed], {})
    assert relaxed.per_pose["E_mmff"][0] - relaxed.per_pose["E_mmff_relaxed"][0] < 0.05


def test_declares_what_it_writes_and_has_no_floor():
    tier = ForceField()
    cand = _cand()
    tier.run([cand], {})
    assert set(cand.per_pose) == set(tier.produces())
    assert all(tier.systematic_floor(q) is None for q in tier.produces())
    with pytest.raises(KeyError, match="does not produce"):
        tier.systematic_floor("dE_int")
    assert tier.settings()["variant"] == "MMFF94"
    assert tier.estimate_cost([cand])["predicted"] is None


def test_non_convergence_raises_instead_of_reporting_a_half_relaxed_energy():
    with pytest.raises(RuntimeError, match="did not converge"):
        ForceField(max_iters=1).run([_cand()], {})


def test_an_untypable_molecule_is_a_loud_error():
    with pytest.raises(ForceFieldTypingError, match="cannot type"):
        ForceField().run([_cand("C[Se]C")], {})


def test_a_pose_whose_atom_order_does_not_match_the_smiles_is_refused():
    cand = _cand()
    p = cand.poses[0]
    cand.poses[0] = Pose(tuple(reversed(p.symbols)), p.coords_ang[::-1].copy())
    with pytest.raises(ValueError, match="atom order"):
        ForceField().run([cand], {})


def test_unknown_paths_and_variants_are_refused_and_the_gate_is_honoured():
    with pytest.raises(ValueError, match="unsupported force-field path"):
        ForceField(path="amber")
    with pytest.raises(ValueError, match="unknown MMFF variant"):
        ForceField(variant="UFF")
    with pytest.raises(PoseGateError, match="no pose report"):
        ForceField().run([_cand()], {"require_pose_report": True})


def test_undeclared_keys_are_still_caught_by_run_checked():
    class Sloppy(ForceField):
        def run(self, candidates, ctx):
            candidates[0].per_pose["oops"] = [0.0]

    with pytest.raises(UndeclaredQuantityError):
        run_checked(Sloppy(), [_cand()], {})
