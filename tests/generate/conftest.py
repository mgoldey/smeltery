"""Fixtures over the real scaffold-growth data in tests/data/sbdd."""

from pathlib import Path

import numpy as np
import pytest
from rdkit import Chem

from smeltery import Candidate
from smeltery.generate.sbdd_post import build_scaffold, smiles_and_pose

DATA = Path(__file__).resolve().parents[1] / "data" / "sbdd"


@pytest.fixture(scope="session")
def raw_samples():
    """The 16 raw model outputs, unsanitized, exactly as written by the runner."""
    return [m for m in Chem.SDMolSupplier(str(DATA / "grown_raw.sdf"), sanitize=False)]


@pytest.fixture(scope="session")
def parent_candidate():
    mol = next(Chem.SDMolSupplier(str(DATA / "parent.sdf")))
    smiles, pose = smiles_and_pose(mol)
    return Candidate("parent", smiles, poses=[pose])


@pytest.fixture(scope="session")
def scaffold_indices(parent_candidate):
    """The scaffold's atoms as indices into the parent SMILES, found by position."""
    scaffold = next(Chem.SDMolSupplier(str(DATA / "scaffold.sdf")))
    n = Chem.MolFromSmiles(parent_candidate.smiles).GetNumAtoms()
    heavy = parent_candidate.poses[0].coords_ang[:n]
    return [int(np.linalg.norm(heavy - p, axis=1).argmin()) for p in scaffold.GetConformer().GetPositions()]


@pytest.fixture(scope="session")
def scaffold_spec(parent_candidate, scaffold_indices):
    return build_scaffold(parent_candidate.smiles, scaffold_indices, parent_candidate.poses[0])
