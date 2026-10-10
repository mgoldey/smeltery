"""PairedPoses(relax_unmapped=True): relax only the substituent, keep every mapped coordinate exact (issue #17).

RDKit only. The motivation is measured with PoseBusters in tests/test_cli.py and tests/test_docking_cli.py.
"""

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import rdForceFieldHelpers as ffh

from smeltery import Candidate
from smeltery.tiers import PairedPoses, _mol_with_h

PARENT, ANALOGUE = "CCCO", "CCCCO"


def _pair(**kw):
    cands = [Candidate("parent", PARENT), Candidate("analogue", ANALOGUE)]
    tier = PairedPoses(n_poses=3, **kw)
    tier.run(cands, {"parent": cands[0]})
    return cands, tier


def _mmff_energy(smiles, xyz):
    mol = _mol_with_h(smiles)
    conf = Chem.Conformer(mol.GetNumAtoms())
    for i, p in enumerate(xyz):
        conf.SetAtomPosition(i, [float(v) for v in p])
    mol.RemoveAllConformers()
    mol.AddConformer(conf)
    return ffh.MMFFGetMoleculeForceField(mol, ffh.MMFFGetMoleculeProperties(mol)).CalcEnergy()


def test_mapped_atoms_stay_bit_identical_and_only_the_substituent_moves():
    plain = _pair()[0][1]
    (parent, relaxed), tier = _pair(relax_unmapped=True)
    mapped = [a for a, _ in tier.scaffold_maps["analogue"]]
    unmapped = [i for i in range(len(relaxed.poses[0].symbols)) if i not in mapped]
    assert unmapped, "the test needs a substituent"
    for pa, pb in zip(plain.poses, relaxed.poses, strict=True):
        assert np.array_equal(pa.coords_ang[mapped], pb.coords_ang[mapped])  # the core: exact, both ways
        assert not np.array_equal(pa.coords_ang[unmapped], pb.coords_ang[unmapped])  # the substituent: relaxed
    for pose_i, pose in enumerate(relaxed.poses):  # and still an exact copy of the parent's coordinates
        for a, p in tier.scaffold_maps["analogue"]:
            assert np.array_equal(pose.coords_ang[a], parent.poses[pose_i].coords_ang[p])


def test_relaxing_lowers_the_mmff_energy_of_every_pose():
    plain = _pair()[0][1]
    relaxed = _pair(relax_unmapped=True)[0][1]
    for pa, pb in zip(plain.poses, relaxed.poses, strict=True):
        assert _mmff_energy(ANALOGUE, pb.coords_ang) < _mmff_energy(ANALOGUE, pa.coords_ang)


def test_the_self_pair_anchor_survives_relaxation():
    cands = [Candidate("parent", PARENT), Candidate("self", PARENT)]
    PairedPoses(n_poses=3, relax_unmapped=True).run(cands, {"parent": cands[0]})
    for a, b in zip(cands[0].poses, cands[1].poses, strict=True):
        assert np.array_equal(a.coords_ang, b.coords_ang)  # every atom mapped: nothing to relax, nothing moves


def test_an_untypable_substituent_is_refused_not_left_unrelaxed():
    cands = [Candidate("parent", "CCCC"), Candidate("selenide", "CCC[Se]C")]
    with pytest.raises(RuntimeError, match="MMFF94 cannot type"):
        PairedPoses(n_poses=2, relax_unmapped=True).run(cands, {"parent": cands[0]})
    PairedPoses(n_poses=2).run(cands, {"parent": cands[0]})  # off: the old behaviour, no error


def test_the_knob_is_recorded_and_off_by_default():
    assert PairedPoses().settings()["relax_unmapped"] is False
    assert PairedPoses(relax_unmapped=True).settings()["relax_unmapped"] is True
    assert PairedPoses(relax_unmapped=True).settings() != PairedPoses().settings()
