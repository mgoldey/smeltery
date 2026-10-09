"""PoseBusters gate (#15): impossible poses are rejected by name, and quantum tiers refuse them."""

import json
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("posebusters", reason="needs the posebusters extra: pip install 'smeltery[posebusters]'")

from rdkit import Chem  # noqa: E402
from rdkit.Chem import AllChem  # noqa: E402

from smeltery import Candidate, Pose, PoseGateError, check_candidate_poses, posebusters_check  # noqa: E402
from smeltery.tiers import FieldInteraction, Gfn2  # noqa: E402

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
PEP5 = Path(__file__).parent / "data" / "pocket_pep5.pdb"


def _good_pose(smiles=ASPIRIN, seed=1) -> Pose:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=seed) == 0
    AllChem.MMFFOptimizeMolecule(mol)
    xyz = np.array([list(mol.GetConformer().GetAtomPosition(i)) for i in range(mol.GetNumAtoms())])
    return Pose(tuple(a.GetSymbol() for a in mol.GetAtoms()), xyz)


def _broken(pose: Pose, idx=2, dx=2.0) -> Pose:
    xyz = pose.coords_ang.copy()
    xyz[idx, 0] += dx  # drag a heavy atom off its bonds
    return Pose(pose.symbols, xyz)


def test_valid_pose_passes_with_a_non_empty_pass_list():
    rep = posebusters_check([_good_pose()], ASPIRIN)
    v = rep.verdicts[0]
    assert rep.all_passed and v.passed
    assert len(v.passed_checks) >= 8 and "bond_lengths" in v.passed_checks and not v.failed_checks


def test_broken_pose_is_rejected_and_the_reason_names_the_check():
    rep = posebusters_check([_good_pose(), _broken(_good_pose())], ASPIRIN)
    assert not rep.all_passed
    assert set(rep.failures()) == {1}
    assert "bond_lengths" in rep.failures()[1]


def test_clashing_hydrogen_is_rejected():
    p = _good_pose()
    xyz = p.coords_ang.copy()
    h = next(i for i, s in enumerate(p.symbols) if s == "H")
    xyz[h] = xyz[5]  # onto a ring carbon
    rep = posebusters_check([Pose(p.symbols, xyz)], ASPIRIN)
    assert not rep.all_passed and rep.failures()[0]  # rejected, with named checks


def test_atom_order_mismatch_refuses_instead_of_guessing_bonds():
    p = _good_pose()
    swapped = Pose(tuple(reversed(p.symbols)), p.coords_ang[::-1].copy())
    with pytest.raises(ValueError, match="AddHs"):
        posebusters_check([swapped], ASPIRIN)


@pytest.mark.parametrize("tier", [FieldInteraction(), Gfn2()])
def test_quantum_tier_given_a_failing_pose_raises(tier):
    cand = Candidate("bad", ASPIRIN, [_broken(_good_pose())])
    check_candidate_poses(cand)
    with pytest.raises(PoseGateError, match="bond_lengths"):
        tier.run([cand], {"field": []})


def test_tier_without_report_runs_unless_the_ctx_demands_one():
    cand = Candidate("nr", ASPIRIN, [_good_pose()])
    with pytest.raises(PoseGateError, match="no pose report"):
        FieldInteraction().run([cand], {"field": [], "require_pose_report": True})


def test_report_is_json_serialisable_with_per_pose_pass_fail():
    cand = Candidate("c", ASPIRIN, [_good_pose(), _broken(_good_pose())])
    d = check_candidate_poses(cand).to_dict()
    again = json.loads(json.dumps(d))
    assert [p["passed"] for p in again["poses"]] == [True, False]
    assert again["config"] == "mol" and again["posebusters_version"]
    assert cand.pose_report is not None


def test_receptor_adds_protein_ligand_checks():
    rep = posebusters_check([_good_pose()], ASPIRIN, receptor=str(PEP5))
    assert rep.config == "dock"
    names = set(rep.verdicts[0].passed_checks) | set(rep.verdicts[0].failed_checks)
    assert {"minimum_distance_to_protein", "volume_overlap_with_protein"} <= names
