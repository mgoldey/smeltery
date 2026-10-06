"""Fast anchors for examples/danuglipron_halogen.py (no SCF: the full run is the example itself)."""

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
from rdkit import Chem

from smeltery import Candidate, Measurement

_SPEC = importlib.util.spec_from_file_location(
    "danuglipron_halogen", Path(__file__).parent.parent / "examples" / "danuglipron_halogen.py")
ex = importlib.util.module_from_spec(_SPEC)
sys.modules["danuglipron_halogen"] = ex
_SPEC.loader.exec_module(ex)


def _data():
    try:
        return ex.find_data_dir()
    except FileNotFoundError:
        pytest.skip("danuglipron test data (ferric checkout) not available")


def test_twelve_distinct_analogues_each_one_substitution():
    smi = ex.analogue_smiles()
    assert len(smi) == 12 and len(set(smi.values())) == 12
    parent_heavy = Chem.MolFromSmiles(ex.PARENT_SMILES).GetNumHeavyAtoms()
    for name, s in smi.items():
        mol = Chem.MolFromSmiles(s)
        assert mol is not None, name
        assert mol.GetNumHeavyAtoms() == parent_heavy + 1, name  # F, Cl and CH3 each add one heavy atom


def test_bound_pose_is_relabelled_without_moving_atoms():
    pose = ex.bound_pose_in_smiles_order(_data() / "conf_00_cryo_em.xyz")
    pmol = Chem.AddHs(Chem.MolFromSmiles(ex.PARENT_SMILES))
    assert pose.symbols == tuple(a.GetSymbol() for a in pmol.GetAtoms())
    for b in pmol.GetBonds():  # every bond of the SMILES topology has a bonded-length distance
        d = np.linalg.norm(pose.coords_ang[b.GetBeginAtomIdx()] - pose.coords_ang[b.GetEndAtomIdx()])
        assert 0.9 < d < 1.9, (b.GetBeginAtomIdx(), b.GetEndAtomIdx(), d)


def test_self_pairing_copies_every_coordinate_and_analogue_core_is_exact():
    bound = ex.bound_pose_in_smiles_order(_data() / "conf_00_cryo_em.xyz")
    parent = Candidate("parent", ex.PARENT_SMILES)
    selfp = Candidate("self", ex.PARENT_SMILES)
    ana = Candidate("F", ex.analogue_smiles()["bzim-C9-F"])
    ex.BoundPosePairedPoses(bound, n_poses=3).run([parent, selfp, ana], {"parent": parent})
    assert len(parent.poses) == 3
    assert not np.array_equal(parent.poses[0].coords_ang, parent.poses[1].coords_ang)  # poses really differ
    for p, s in zip(parent.poses, selfp.poses, strict=True):
        assert p.symbols == s.symbols and np.array_equal(p.coords_ang, s.coords_ang)
    # analogue keeps all parent atoms but the replaced H exactly, so most coordinates are bit-identical
    for p, a in zip(parent.poses, ana.poses, strict=True):
        shared = sum(any(np.array_equal(x, y) for y in p.coords_ang) for x in a.coords_ang)
        assert shared >= len(p.symbols) - 1


def test_campaign_comparison_is_unverified_without_recorded_values(monkeypatch):
    paired = {"A": Measurement.from_samples([1.0, 2.0, 3.0])}
    rows, status = ex.compare_to_campaign(paired)
    assert status.startswith("UNVERIFIED") and rows[0]["recorded"] is None and rows[0]["agrees"] is None
    monkeypatch.setitem(ex.CAMPAIGN_RECORDED, "A", (2.1, 0.5))
    rows, status = ex.compare_to_campaign(paired)
    assert rows[0]["agrees"] is True and status.startswith("AGREES")
    monkeypatch.setitem(ex.CAMPAIGN_RECORDED, "A", (5.0, 0.5))
    assert ex.compare_to_campaign(paired)[0][0]["agrees"] is False
