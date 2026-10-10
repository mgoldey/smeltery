"""ForceField OpenMM path (issue #18): OpenMM + an OpenFF (SMIRNOFF) ligand force field.

The stack is an optional environment, not a pip extra: openff-toolkit has no usable
PyPI release. Tests that need it SKIP with the install route when it is absent (as
in CI); the ones at the top run everywhere. See docs/environments.md.
"""

import copy
import importlib.util
import shutil
import sys

import numpy as np
import pytest
from rdkit import Chem
from rdkit.Chem import AllChem

from smeltery import Candidate, PoseGateError
from smeltery.model import Pose
from smeltery.tiers import (
    KJ_PER_KCAL,
    ForceField,
    ForceFieldBackendMissing,
    ForceFieldTypingError,
    run_checked,
)

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"


def _present(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


_MISSING = [m for m in ("openmm", "openff.toolkit", "openmmforcefields") if not _present(m)]
if not _MISSING and shutil.which("sqm") is None:
    _MISSING.append("sqm (AmberTools, for AM1-BCC charges)")
needs_stack = pytest.mark.skipif(
    bool(_MISSING),
    reason=(
        f"needs the OpenMM/OpenFF environment ({', '.join(_MISSING)} missing): "
        "`micromamba create -n smeltery-ff -f environment-openff.yml`, then run pytest inside it "
        "(docs/environments.md, 'OpenMM force-field path')"
    ),
)


def _cand(smiles=ASPIRIN, seed=1, name="c"):
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=seed) == 0  # raw ETKDG: deliberately NOT relaxed
    xyz = np.array([list(mol.GetConformer().GetAtomPosition(i)) for i in range(mol.GetNumAtoms())])
    return Candidate(name, smiles, [Pose(tuple(a.GetSymbol() for a in mol.GetAtoms()), xyz)])


# ---------------------------------------------------------------- run everywhere


def test_a_missing_stack_fails_loudly_with_the_install_route(monkeypatch):
    monkeypatch.setitem(sys.modules, "openff.toolkit", None)  # None in sys.modules makes the import raise
    with pytest.raises(ForceFieldBackendMissing, match=r"environment-openff\.yml") as exc:
        ForceField(path="openmm")
    assert "docs/environments.md" in str(exc.value)
    assert isinstance(exc.value, ImportError)


def test_the_unit_conversion_constant_is_the_thermochemical_calorie():
    assert KJ_PER_KCAL == 4.184  # 1 kcal = 4.184 kJ, exactly


# ---------------------------------------------------------------- need the stack


@pytest.fixture(scope="module")
def tier():
    # one instance for the module: AM1-BCC charges are cached per SMILES inside it
    return ForceField(path="openmm")


@needs_stack
def test_openmm_unit_object_agrees_with_our_constant():
    from openmm import unit

    assert (1.0 * unit.kilocalorie_per_mole).value_in_unit(unit.kilojoule_per_mole) == pytest.approx(KJ_PER_KCAL)


@needs_stack
def test_never_writes_coordinates_back_and_reports_strain(tier):
    cand = _cand()
    before = copy.deepcopy(cand.poses)
    run_checked(tier, [cand], {})
    for a, b in zip(before, cand.poses, strict=True):
        assert a.symbols == b.symbols and np.array_equal(a.coords_ang, b.coords_ang)
    e, e_rel = cand.per_pose["E_openmm"][0], cand.per_pose["E_openmm_relaxed"][0]
    assert e_rel < e and e - e_rel > 1.0  # a raw embedding is strained; relaxing a copy lowers the energy


@needs_stack
def test_energy_in_kcal_is_the_openmm_kj_energy_divided_by_4_184(tier):
    """Independent check of the unit conversion: build the same system by hand and read kJ/mol."""
    import openmm
    from openff.toolkit import Molecule
    from openmm import app, unit
    from openmmforcefields.generators import SMIRNOFFTemplateGenerator

    cand = _cand()
    tier.run([cand], {})
    off_mol = Molecule.from_rdkit(Chem.AddHs(Chem.MolFromSmiles(ASPIRIN)), allow_undefined_stereo=True)
    off_mol.assign_partial_charges("am1bcc")
    gen = SMIRNOFFTemplateGenerator(molecules=off_mol, forcefield=tier.ligand_ff)
    ff = app.ForceField()
    ff.registerTemplateGenerator(gen.generator)
    system = ff.createSystem(off_mol.to_topology().to_openmm(), nonbondedMethod=app.NoCutoff, constraints=None)
    ctx = openmm.Context(system, openmm.VerletIntegrator(0.001), openmm.Platform.getPlatformByName("Reference"))
    ctx.setPositions(cand.poses[0].coords_ang * 0.1)
    kj = ctx.getState(getEnergy=True).getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)
    assert cand.per_pose["E_openmm"][0] * 4.184 == pytest.approx(kj, rel=1e-6, abs=1e-3)
    assert cand.per_pose["E_openmm"][0] != pytest.approx(kj, rel=1e-3)  # i.e. it really was converted


@needs_stack
def test_the_energy_depends_on_the_geometry_given(tier):
    a, b = _cand(seed=1), _cand(seed=2)
    tier.run([a, b], {})
    assert a.per_pose["E_openmm"][0] != pytest.approx(b.per_pose["E_openmm"][0], abs=1e-3)


@needs_stack
def test_declares_what_it_writes_with_units_and_settings(tier):
    cand = _cand()
    tier.run([cand], {})
    assert tier.produces() == {"E_openmm": "kcal/mol", "E_openmm_relaxed": "kcal/mol"}
    assert set(cand.per_pose) == set(tier.produces())
    assert all(tier.systematic_floor(q) is None for q in tier.produces())
    with pytest.raises(KeyError, match="does not produce"):
        tier.systematic_floor("E_mmff")
    s = tier.settings()
    assert s["energy_unit_native"] == "kJ/mol" and s["ligand_ff"].startswith("openff")
    assert {"openmm", "openff-toolkit", "openmmforcefields"} <= set(s)


@needs_stack
def test_non_convergence_raises_instead_of_reporting_a_half_relaxed_energy():
    with pytest.raises(RuntimeError, match="did not converge"):
        ForceField(path="openmm", max_iters=1).run([_cand()], {})


@needs_stack
def test_a_molecule_the_force_field_cannot_parameterize_is_a_loud_error(tier):
    with pytest.raises(ForceFieldTypingError, match="could not parameterize"):
        tier.run([_cand("C[Se]C")], {})


@needs_stack
def test_atom_order_mismatch_and_the_pose_gate_are_honoured(tier):
    cand = _cand()
    p = cand.poses[0]
    bad = Candidate("b", ASPIRIN, [Pose(tuple(reversed(p.symbols)), p.coords_ang[::-1].copy())])
    with pytest.raises(ValueError, match="atom order"):
        tier.run([bad], {})
    with pytest.raises(PoseGateError, match="no pose report"):
        tier.run([_cand()], {"require_pose_report": True})
