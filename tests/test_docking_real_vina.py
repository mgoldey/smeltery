"""Real Vina + Meeko integration: no fakes, no synthetic receptor.

Skips (with the install command) unless the `docking` extra is installed; CI's
`docking` job installs it and fails if any of these skip.

Fixture: `tests/data/pocket_pep5.pdb` is a folded Ser-Leu-Phe-Asn-Thr pentapeptide
(RDKit ETKDG + MMFF, seed 7), and `pocket_pep5.pdbqt` is that PDB run through
`mk_prepare_receptor.py --read_pdb pocket_pep5.pdb -o pocket_pep5 -p` (Meeko
0.8.0). Both are committed so a Meeko change cannot silently alter the
docking inputs; a second test re-prepares the PDB to catch CLI drift.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil

import numpy as np
import pytest

from smeltery.docking import Box

_MISSING = [m for m in ("vina", "meeko", "rdkit") if importlib.util.find_spec(m) is None]
# A skipif mark, not a module-level importorskip: the tests stay collected (and
# counted against MIN_TESTS) in the plain `test` job, and skip there by name.
pytestmark = pytest.mark.skipif(
    bool(_MISSING),
    reason=f"needs the docking extra: pip install 'smeltery[docking]' ({', '.join(_MISSING)} missing)",
)

DATA = pathlib.Path(__file__).parent / "data"
RECEPTOR_PDB = DATA / "pocket_pep5.pdb"
RECEPTOR_PDBQT = DATA / "pocket_pep5.pdbqt"
# Centroid of the pentapeptide; the box covers the whole 14 x 7 x 9 A peptide.
BOX = Box((0.4, -0.1, -0.1), (22.0, 16.0, 16.0))

LIGANDS = {
    "ethanol": ("CCO", 9),
    "aspirin": ("CC(=O)Oc1ccccc1C(=O)O", 21),
}


def _embedded(smiles):
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=0xF00D) == 0
    AllChem.MMFFOptimizeMolecule(mol)
    return mol


@pytest.mark.parametrize("name", sorted(LIGANDS))
def test_real_vina_poses_have_all_atoms(name):
    from smeltery.docking import VinaProvider

    smiles, n_atoms = LIGANDS[name]
    mol = _embedded(smiles)
    assert mol.GetNumAtoms() == n_atoms
    res = VinaProvider(n_poses=3).dock(mol, RECEPTOR_PDBQT, BOX, seed=7)
    assert res.ok, res.error
    assert 1 <= len(res.poses) <= 3
    for pose in res.poses:
        # United-atom PDBQT drops nonpolar H; the provider must restore them.
        assert len(pose.symbols) == n_atoms
        assert np.asarray(pose.coords_ang).shape == (n_atoms, 3)
        assert np.isfinite(np.asarray(pose.coords_ang)).all()
    assert len(res.scores) == len(res.poses)
    assert np.isfinite(res.scores).all()
    assert res.scores == sorted(res.scores)
    # A pocket made of real residues must give a binding (negative) score.
    assert res.scores[0] < 0.0


def test_real_vina_is_seed_reproducible():
    from smeltery.docking import VinaProvider

    mol = _embedded("CCO")
    prov = VinaProvider(n_poses=2)
    a = prov.dock(mol, RECEPTOR_PDBQT, BOX, seed=11)
    b = prov.dock(mol, RECEPTOR_PDBQT, BOX, seed=11)
    assert a.ok and b.ok
    assert a.scores == b.scores


def test_prepare_receptor_matches_committed_fixture(tmp_path):
    """The Meeko CLI shell-out still works and yields the same atom set."""
    from smeltery.docking import vina_dock

    if shutil.which("mk_prepare_receptor.py") is None:
        pytest.skip("mk_prepare_receptor.py not on PATH (run through `uv run`)")
    out = vina_dock.prepare_receptor(RECEPTOR_PDB, tmp_path / "rec")
    assert out.is_file()

    def atoms(p):
        return [ln[12:16].strip() + ln[17:20] for ln in p.read_text().splitlines() if ln.startswith("ATOM")]

    assert atoms(out) == atoms(RECEPTOR_PDBQT)
    assert len(atoms(out)) > 0
