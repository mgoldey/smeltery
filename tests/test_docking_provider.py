"""Acceptance tests for issue #10 (M2-03): `smeltery.docking`.

Each test names the acceptance criterion it verifies. The ported ferric tests
(united-atom restore, element check, cpu plumbing, Meeko order) live in
`test_docking_*.py` beside this file.

Tests that need the optional `docking` extra (vina, meeko) skip with the install
command when it is absent; the guards themselves are exercised on a real
united-atom PDBQT fixture (`tests/data/aspirin_united_atom.pdbqt`, written by
Meeko 0.8.0 from aspirin: 14 atoms out of 21), which needs neither.
"""

from __future__ import annotations

import inspect
import pathlib
import sys
import types

import numpy as np
import pytest

pytest.importorskip("rdkit", reason="docking guards are RDKit-based")

from rdkit import Chem  # noqa: E402
from rdkit.Chem import AllChem  # noqa: E402

from smeltery import Candidate, UnmeasuredFloorError  # noqa: E402
from smeltery.docking import (  # noqa: E402
    DEFAULT_EXHAUSTIVENESS,
    Box,
    Docking,
    DockingError,
    DockingProvider,
    DockResult,
    PoseMismatchError,
    VinaProvider,
    check_full_pose,
    check_heavy_atom_count,
    parse_smiles_idx_remark,
    pose_to_structure,
    restore_hydrogens,
)
from smeltery.docking import vina_dock  # noqa: E402
from smeltery.funnel import tier_floor  # noqa: E402
from smeltery.model import Pose  # noqa: E402

FIXTURE = pathlib.Path(__file__).parent / "data" / "aspirin_united_atom.pdbqt"
ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
# One heavy atom more than aspirin (ethyl ester): a different molecule.
ETHYL_ESTER = "CCC(=O)Oc1ccccc1C(=O)O"


def _need_extra():
    for mod in ("vina", "meeko"):
        pytest.importorskip(
            mod, reason=f"needs the docking extra: pip install 'smeltery[docking]' ({mod} missing)"
        )


def _fixture_pose():
    """Symbols and coordinates of the REAL united-atom pose, as parsed from the PDBQT."""
    text = FIXTURE.read_text()
    models = vina_dock._parse_pdbqt_models("MODEL 1\n" + text + "ENDMDL\n")
    (syms, coords, _score, serials), = models
    return text, syms, coords, serials


def _embedded(smiles):
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=0xF00D) == 0
    AllChem.MMFFOptimizeMolecule(mol)
    return mol


# --- criterion 1: atom-count guard, real united-atom PDBQT ----------------------

def test_fixture_is_a_real_united_atom_pose():
    """Premise: 14 atoms out of 21 in (7 nonpolar H merged), 13 heavy atoms."""
    _, syms, _, _ = _fixture_pose()
    assert len(syms) == 14
    assert sum(s != "H" for s in syms) == 13
    assert _embedded(ASPIRIN).GetNumAtoms() == 21


def test_pose_with_wrong_heavy_atom_count_raises():
    _, syms, _, _ = _fixture_pose()
    with pytest.raises(PoseMismatchError, match="13 heavy atoms but the docked molecule has 14"):
        check_heavy_atom_count(_embedded(ETHYL_ESTER), syms)


def test_matching_heavy_atom_count_passes_united_atom_pose():
    """Negative control: the same fixture against the molecule that made it passes
    the heavy-atom guard (it is only missing hydrogens, which restore puts back)."""
    _, syms, _, _ = _fixture_pose()
    check_heavy_atom_count(_embedded(ASPIRIN), syms)


def test_united_atom_pose_is_refused_as_a_final_pose():
    """The 2591 kcal/mol failure: 14 atoms where 21 went in must not reach a scorer."""
    _, syms, _, _ = _fixture_pose()
    with pytest.raises(PoseMismatchError, match="hydrogens are missing"):
        check_full_pose(_embedded(ASPIRIN), syms)


def test_restoring_hydrogens_makes_the_fixture_pass_the_full_pose_guard():
    text, syms, coords, serials = _fixture_pose()
    s2r = parse_smiles_idx_remark(text)
    heavy = [(s, c, k) for s, c, k in zip(syms, coords, serials) if s != "H"]
    out_syms, out_coords = restore_hydrogens(
        ASPIRIN,
        [s for s, _, _ in heavy],
        [c for _, c, _ in heavy],
        rdkit_index_of_heavy=[s2r[k] for _, _, k in heavy],
    )
    assert len(out_syms) == 21
    check_full_pose(_embedded(ASPIRIN), out_syms)


def test_restore_refuses_wrong_heavy_count_on_the_fixture():
    _, syms, coords, _ = _fixture_pose()
    heavy = [(s, c) for s, c in zip(syms, coords) if s != "H"]
    with pytest.raises(ValueError, match="different molecules"):
        restore_hydrogens(ETHYL_ESTER, [s for s, _ in heavy], [c for _, c in heavy])


# --- criterion 2: per-atom element check on the real Meeko map ------------------

def _fixture_heavy_and_map():
    text, syms, coords, serials = _fixture_pose()
    s2r = parse_smiles_idx_remark(text)
    heavy = [(s, c, k) for s, c, k in zip(syms, coords, serials) if s != "H"]
    return (
        [s for s, _, _ in heavy],
        [c for _, c, _ in heavy],
        [s2r[k] for _, _, k in heavy],
    )


def test_reversed_meeko_map_on_real_fixture_raises_element_mismatch():
    syms, coords, mapping = _fixture_heavy_and_map()
    smiles = "CC(=O)Oc1ccccc1C(=O)O"  # Meeko's own SMILES for this fixture
    rev = list(reversed(mapping))
    assert sorted(rev) == sorted(mapping)  # a valid permutation: only the element check can fire
    with pytest.raises(ValueError, match="element mismatch"):
        restore_hydrogens(smiles, syms, coords, rdkit_index_of_heavy=rev)


def test_real_meeko_map_passes_the_element_check():
    """Negative control: the unreversed map is accepted and gives all 21 atoms."""
    syms, coords, mapping = _fixture_heavy_and_map()
    out, _ = restore_hydrogens(ASPIRIN, syms, coords, rdkit_index_of_heavy=mapping)
    assert len(out) == 21


def test_element_check_is_per_atom_not_a_composition_check():
    """A reversed map keeps the multiset of elements identical (same atoms, other
    order), so a formula-level check would pass it. Pin that this is the reason the
    check is per atom."""
    syms, coords, mapping = _fixture_heavy_and_map()
    rev = list(reversed(mapping))
    mol = Chem.AddHs(Chem.MolFromSmiles(ASPIRIN))
    assert sorted(mol.GetAtomWithIdx(i).GetSymbol() for i in rev) == sorted(syms)
    with pytest.raises(ValueError, match="element"):
        restore_hydrogens(ASPIRIN, syms, coords, rdkit_index_of_heavy=rev)


def test_same_element_swap_is_not_an_element_error():
    """DOCUMENTED LIMIT, not a feature: swapping two atoms OF THE SAME element
    passes the element check by construction (the elements agree). The stereo
    guard is what catches it on a chiral molecule; on an achiral one nothing can,
    short of connectivity perception. The issue's phrase 'a same-element
    permutation is caught' therefore holds only where the permutation changes an
    element at some position (the reversed map), which is what ferric's test uses."""
    syms, coords, mapping = _fixture_heavy_and_map()
    # swap two carbons' targets (positions 0 and 1 are both aromatic C in this fixture)
    assert syms[0] == syms[1] == "C"
    swapped = list(mapping)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    out, _ = restore_hydrogens(ASPIRIN, syms, coords, rdkit_index_of_heavy=swapped)
    assert len(out) == 21  # no exception: element check cannot see it


# --- criterion 3: default exhaustiveness 4, recorded in settings() --------------

def test_default_exhaustiveness_is_4_everywhere():
    assert DEFAULT_EXHAUSTIVENESS == 4
    assert vina_dock.DEFAULT_EXHAUSTIVENESS == 4
    assert VinaProvider().exhaustiveness == 4
    assert inspect.signature(vina_dock.dock_ligand).parameters["exhaustiveness"].default == 4
    assert inspect.signature(VinaProvider.dock).parameters["exhaustiveness"].default is None


def test_default_exhaustiveness_is_recorded_in_settings():
    assert VinaProvider().settings()["exhaustiveness"] == 4
    assert Docking(VinaProvider()).settings()["exhaustiveness"] == 4
    assert Docking(VinaProvider()).settings()["provider_settings"]["exhaustiveness"] == 4


def test_overridden_exhaustiveness_is_recorded_not_the_default():
    """Negative control: settings() reports what was configured, not a constant."""
    assert VinaProvider(exhaustiveness=16).settings()["exhaustiveness"] == 16
    assert Docking(VinaProvider(), exhaustiveness=8).settings()["exhaustiveness"] == 8


class _RecordingVina:
    last: dict = {}

    def __init__(self, **kw):
        pass

    def set_receptor(self, *a, **k): ...
    def set_ligand_from_string(self, *a, **k): ...
    def compute_vina_maps(self, *a, **k): ...

    def dock(self, **kw):
        type(self).last = kw

    def poses(self, n_poses=1):
        return ""


def test_default_exhaustiveness_reaches_vina(monkeypatch, tmp_path):
    """The recorded value is the value used: Vina.dock sees 4 by default, 12 when asked."""
    mod = types.ModuleType("vina")
    mod.Vina = _RecordingVina
    monkeypatch.setitem(sys.modules, "vina", mod)
    monkeypatch.setattr(vina_dock, "_ligand_pdbqt_from_rdkit", lambda mol: "X")
    rec = tmp_path / "r.pdbqt"
    rec.write_text("ATOM\n")
    mol = _embedded("CCO")
    VinaProvider().dock(mol, rec, Box((0, 0, 0)), seed=1)
    assert _RecordingVina.last["exhaustiveness"] == 4
    VinaProvider().dock(mol, rec, Box((0, 0, 0)), seed=1, exhaustiveness=12)
    assert _RecordingVina.last["exhaustiveness"] == 12


# --- criterion 4: a second engine needs no funnel change ------------------------

class FakeProvider:
    """A second docking engine, written without importing anything from vina_dock."""

    name = "fake"

    def __init__(self):
        self.calls = []

    def settings(self):
        return {"engine": "fake", "exhaustiveness": DEFAULT_EXHAUSTIVENESS}

    def score_unit(self):
        return "au"

    def dock(self, mol, receptor, box, seed, exhaustiveness=DEFAULT_EXHAUSTIVENESS):
        self.calls.append((seed, exhaustiveness, receptor, box))
        syms = tuple(a.GetSymbol() for a in mol.GetAtoms())
        xyz = mol.GetConformer().GetPositions() + float(seed % 7)
        # two poses, deliberately returned worst-first
        return DockResult(poses=[Pose(syms, xyz), Pose(syms, xyz + 1.0)], scores=[-1.0 - seed % 3, -5.0])


def test_test_double_satisfies_the_protocol():
    assert isinstance(FakeProvider(), DockingProvider)
    assert isinstance(VinaProvider(), DockingProvider)


def test_object_missing_dock_is_not_a_provider():
    """Negative control: the protocol check is not vacuous."""
    class NotAProvider:
        name = "x"
    assert not isinstance(NotAProvider(), DockingProvider)


def test_tier_runs_unchanged_over_a_second_engine():
    prov = FakeProvider()
    tier = Docking(prov, seeds=(1, 2))
    cand = Candidate("ethanol", "CCO")
    tier.run([cand], {"receptor": "rec.pdbqt", "box": Box((1.0, 2.0, 3.0))})
    assert [c[0] for c in prov.calls] == [1, 2]  # one dock per seed
    assert all(c[1] == 4 and c[2] == "rec.pdbqt" for c in prov.calls)
    assert len(cand.poses) == 4 == len(cand.per_pose["dock_score"])
    assert cand.per_pose["dock_score"] == sorted(cand.per_pose["dock_score"])  # best first
    assert cand.poses[0].formula == "C2H6O"  # every hydrogen present


def test_tier_fits_the_tier_contract_and_funnel_refuses_unmeasured_floor():
    tier = Docking(FakeProvider())
    assert tier.produces() == {"dock_score": "au"}
    assert tier.systematic_floor("dock_score") is None
    with pytest.raises(UnmeasuredFloorError):
        tier_floor(tier, "dock_score")  # the funnel's own guard, unmodified
    with pytest.raises(KeyError):
        tier.systematic_floor("not_produced")  # negative control: unknown quantity
    assert tier.estimate_cost([])["predicted"] is None


def test_vina_tier_declares_kcal_per_mol():
    assert Docking(VinaProvider()).produces() == {"dock_score": "kcal/mol"}


def test_tier_without_context_explains_instead_of_keyerror():
    with pytest.raises(DockingError, match="receptor"):
        Docking(FakeProvider()).run([Candidate("e", "CCO")], {})


def test_tier_rejects_multi_fragment_ligand():
    with pytest.raises(DockingError, match="connected"):
        Docking(FakeProvider()).run(
            [Candidate("salt", "CC(=O)[O-].[Na+]")], {"receptor": "r", "box": Box((0, 0, 0))}
        )


def test_tier_needs_a_seed():
    with pytest.raises(ValueError):
        Docking(FakeProvider(), seeds=())


# --- charge and multiplicity stay explicit at the structure boundary -------------

def test_pose_to_structure_requires_charge_and_multiplicity():
    pose = Pose(("O", "H", "H"), np.array([[0.0, 0, 0], [0.96, 0, 0], [-0.24, 0.93, 0]]))
    with pytest.raises(TypeError):
        pose_to_structure(pose)  # type: ignore[call-arg]
    s = pose_to_structure(pose, charge=0, multiplicity=1)
    assert (s.charge, s.multiplicity, len(s.symbols)) == (0, 1, 3)


# --- the optional extra: real Meeko + the extra's import hint --------------------

def test_missing_extra_gives_an_actionable_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "vina", None)  # import raises ImportError
    with pytest.raises(ImportError, match=r"smeltery\[docking\]"):
        vina_dock._require("vina")


def test_importing_the_package_does_not_import_vina_or_meeko():
    import subprocess

    code = (
        "import sys, smeltery.docking; "
        "sys.exit(1 if {'vina','meeko'} & set(sys.modules) else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_real_meeko_writes_the_fixture_shape():
    """With meeko: regenerating aspirin gives the same united-atom shape as the fixture."""
    _need_extra()
    pdbqt = vina_dock._ligand_pdbqt_from_rdkit(_embedded(ASPIRIN))
    models = vina_dock._parse_pdbqt_models("MODEL 1\n" + pdbqt + "ENDMDL\n")
    assert len(models[0][0]) == 14
    assert parse_smiles_idx_remark(pdbqt) == parse_smiles_idx_remark(FIXTURE.read_text())


def test_real_vina_dock_end_to_end(tmp_path):
    """Real Vina + Meeko: dock ethanol into a tiny synthetic receptor; every
    returned pose has all 9 atoms and a score, and the same seed/cpu reproduces."""
    _need_extra()
    rec = tmp_path / "rec.pdbqt"
    lines = []
    k = 1
    for x in (-3.0, 3.0):
        for y in (-3.0, 3.0):
            for z in (-3.0, 3.0):
                lines.append(
                    f"ATOM  {k:5d}  C   ALA A   1    {x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00    +0.000 C "
                )
                k += 1
    rec.write_text("\n".join(lines) + "\n")
    mol = _embedded("CCO")
    prov = VinaProvider(n_poses=3)
    res = prov.dock(mol, rec, Box((0.0, 0.0, 0.0), (12.0, 12.0, 12.0)), seed=7)
    assert res.ok, res.error
    assert all(p.formula == "C2H6O" for p in res.poses)
    assert all(np.isfinite(res.scores))
    assert res.scores == sorted(res.scores)
    res2 = prov.dock(mol, rec, Box((0.0, 0.0, 0.0), (12.0, 12.0, 12.0)), seed=7)
    assert res2.scores == res.scores
