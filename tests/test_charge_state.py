"""The tiers must hand ferric the molecule's net charge from its SMILES, not assume a neutral molecule.

`ferric.Molecule.from_xyz_string(xyz, charge=0, multiplicity=1)` defaults to neutral. A singly charged ion
flips the electron-count parity, so ferric refuses it loudly; a DOUBLY charged ion with an even electron count
(oxalate, C2O4 2-) would have been scored as the neutral molecule without any error.
"""

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from smeltery import Candidate, PointCharge, Pose
from smeltery.tiers import FieldInteraction, SurfaceEsp, surface_esp

OXALATE = "[O-]C(=O)C(=O)[O-]"  # C2O4 2-: 44 electrons at charge 0, 46 at charge -2: both even
HYDROXIDE = "[OH-]"  # 9 electrons at charge 0 (odd: ferric refuses), 10 at charge -1


def _cand(smiles, name="c"):
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=1) == 0
    xyz = np.array([list(mol.GetConformer().GetAtomPosition(i)) for i in range(mol.GetNumAtoms())])
    return Candidate(name, smiles, [Pose(tuple(a.GetSymbol() for a in mol.GetAtoms()), xyz)])


@pytest.fixture
def spy(monkeypatch):
    """Record the kwargs of every ferric.Molecule.from_xyz_string call, then defer to the real one."""
    import ferric

    real = ferric.Molecule
    calls = []

    class Proxy:
        @staticmethod
        def from_xyz_string(text, **kw):
            calls.append(kw)
            return real.from_xyz_string(text, **kw)

    monkeypatch.setattr(ferric, "Molecule", Proxy)
    return calls


@pytest.mark.needs_ferric
@pytest.mark.parametrize(("smiles", "charge"), [(OXALATE, -2), (HYDROXIDE, -1), ("[NH4+]", 1), ("CCO", 0)])
def test_field_interaction_passes_the_smiles_net_charge_to_ferric(spy, smiles, charge):
    cand = _cand(smiles)
    FieldInteraction().run([cand], {"field": [PointCharge(0.5, (4.0, 0.0, 0.0))]})
    assert spy and all(kw.get("charge") == charge for kw in spy), spy
    assert np.isfinite(cand.per_pose["dE_int"]).all()  # [OH-] and [NH4+] would be refused as a neutral molecule


@pytest.mark.needs_ferric
def test_a_dianion_is_scored_as_the_dianion_not_as_the_neutral_molecule():
    """The silent case: even electron count either way. The tier must equal a by-hand run at charge -2."""
    import ferric

    cand = _cand(OXALATE)
    field = [PointCharge(1.0, (4.0, 0.0, 0.0))]
    FieldInteraction().run([cand], {"field": field})
    got = cand.per_pose["dE_int"][0]

    mol_xyz = cand.poses[0].to_xyz("x")
    bs = ferric.BasisSet.bundled("sto-3g")
    kw = {"energy_conv": 1e-10, "density_conv": 1e-8}

    def de(charge):
        mol = ferric.Molecule.from_xyz_string(mol_xyz, charge=charge)
        vac = ferric.run_rhf(mol, bs, **kw)
        fld = ferric.run_rhf(mol, bs, point_charges=[field[0].as_ferric_bohr()], **kw)
        return (fld.energy - vac.energy) * 627.5094740631

    assert got == pytest.approx(de(-2), abs=1e-6)
    assert abs(got - de(0)) > 1.0, (
        "the neutral treatment gives (nearly) the same number: the test cannot tell them apart"
    )


@pytest.mark.needs_ferric
def test_surface_esp_builds_its_molecule_at_the_same_charge(spy, monkeypatch):
    """`surface_esp` must not rebuild the molecule neutral after an SCF at another charge."""
    import ferric

    if not hasattr(ferric, "esp_on_surface"):  # the PyPI wheel predates the binding: a stand-in checks the plumbing
        monkeypatch.setattr(
            ferric, "esp_on_surface", lambda mol, bs, res, **kw: (np.zeros((1, 3)), np.zeros(1), 0), raising=False
        )
        stand_in = True
    else:
        stand_in = False
    cand = _cand(HYDROXIDE)
    bs = ferric.BasisSet.bundled("sto-3g")
    mol = ferric.Molecule.from_xyz_string(cand.poses[0].to_xyz("x"), charge=-1)
    res = ferric.run_rhf(mol, bs)
    del spy[:]
    surface_esp(cand.poses[0], res, bs, charge=-1)
    assert spy == [{"charge": -1}], spy
    del spy[:]
    SurfaceEsp(basis="sto-3g").run([cand], {})
    assert spy and all(kw.get("charge") == -1 for kw in spy), (spy, "stand-in" if stand_in else "real binding")
