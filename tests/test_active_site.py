"""Pocket embedding, binding energy, prescreen and relaxation (issue #11, migrated from ferric tools/active_site).

Fast tests use water / STO-3G and a handful of point charges. One heavy test reproduces the
campaign's 71-atom, 6458-charge number (~2 x 70 s of SCF): it needs `pdb2pqr30` on PATH and
SMELTERY_RUN_HEAVY=1, and skips with that message otherwise.
"""

import inspect
import os
import shutil
from pathlib import Path

import ferric
import numpy as np
import pytest

from smeltery import ANGSTROM_TO_BOHR, PointCharge
from smeltery import pocket as P
from smeltery.pocket import binding_energy as BE

DATA = Path(__file__).parent / "data" / "pocket_fixture"
POCKET_PDB, LIGAND_XYZ = DATA / "7LCJ_pocket.pdb", DATA / "conf_00_cryo_em.xyz"

WATER = ["O", "H", "H"], [(0.0, 0.0, 0.0), (0.7586, 0.0, 0.5043), (-0.7586, 0.0, 0.5043)]
# Charges 4-5 A from the water, none within the 1.5 A overlap cutoff.
FIELD = P.PocketField([PointCharge(0.5, (0.0, 0.0, 4.0)), PointCharge(-0.5, (4.0, 0.0, 0.0)),
                       PointCharge(0.3, (0.0, 4.5, -1.0))], {"input": "synthetic"})
# One gentle charge for relaxation: with the three-charge FIELD above ferric's optimizer cycles
# (energy alternates by ~4e-4 Ha for 100 steps, converged=False), which is a ferric matter, not ours.
GENTLE = P.PocketField([PointCharge(0.2, (0.0, 0.0, 5.0))])
KW = {"energy_conv": 1e-10, "density_conv": 1e-8}


def _xyz(tmp_path, name="w.xyz", coords=None):
    syms, xyz = WATER[0], coords or WATER[1]
    p = tmp_path / name
    p.write_text("3\nwater\n" + "".join(f"{s} {x} {y} {z}\n" for s, (x, y, z) in zip(syms, xyz)))
    return p


def _pqr(tmp_path, charges):
    lines = [f"ATOM  {i:5d}  X   ION     1    {x:8.3f}{y:8.3f}{z:8.3f} {q:7.4f} 1.0000"
             for i, (q, (x, y, z)) in enumerate(charges, 1)]
    p = tmp_path / "f.pqr"
    p.write_text("\n".join(lines) + "\nTER\nEND\n")
    return p


def _embed(coords=None, pocket=FIELD, basis="sto-3g"):
    return P.embed_ligand_from_coords(*WATER[:1], coords or WATER[1], pocket=pocket, basis=basis)


# ---- acceptance 1: two SCFs reported separately ----------------------------------

def test_binding_energy_reports_two_separate_scf_energies(tmp_path):
    r = P.compute_binding_energy(_xyz(tmp_path), FIELD, basis="sto-3g", min_available_gb=0)
    emb = _embed()
    vac = ferric.run_rhf(emb.mol, emb.basis_set)  # independent vacuum SCF
    fld = ferric.run_rhf(emb.mol, emb.basis_set, point_charges=[c.as_ferric_bohr() for c in FIELD])
    assert r.e_vacuum == pytest.approx(vac.energy, abs=1e-8)
    assert r.e_field == pytest.approx(fld.energy, abs=1e-8)
    assert r.e_field != r.e_vacuum  # negative control: the field is not a no-op
    assert r.delta_e_hartree == r.e_field - r.e_vacuum
    assert r.delta_e_kcal_mol == pytest.approx(r.delta_e_hartree * 627.5094740631)
    assert r.n_pocket_charges == 3
    assert set(r.charges_vacuum) == set(r.charges_field) == {"hirshfeld", "lowdin"}


def test_binding_energy_accepts_a_pqr_path_and_keeps_every_charge(tmp_path):
    pqr = _pqr(tmp_path, [(c.q, c.xyz_ang) for c in FIELD])
    r = P.compute_binding_energy(_xyz(tmp_path), pqr, basis="sto-3g", min_available_gb=0)
    assert r.n_pocket_charges == 3  # no default cutoff
    assert inspect.signature(P.compute_binding_energy).parameters.keys().isdisjoint({"cutoff_ang", "cutoff"})


@pytest.mark.skipif(shutil.which("pdb2pqr30") is None,
                    reason="pdb2pqr30 not installed (pip install pdb2pqr): the 7LCJ binding-energy anchor not run")
@pytest.mark.skipif(os.environ.get("SMELTERY_RUN_HEAVY") != "1",
                    reason="~2.5 min: two SCFs at 71 atoms / 6458 charges. Run with SMELTERY_RUN_HEAVY=1")
def test_7lcj_binding_energy_reproduces_campaign_value():
    r = P.compute_binding_energy(LIGAND_XYZ, POCKET_PDB)
    assert r.delta_e_kcal_mol == pytest.approx(-17.41, abs=0.05)
    assert r.n_pocket_charges > 6000
    assert r.e_vacuum != r.e_field and r.e_field - r.e_vacuum == r.delta_e_hartree


def test_7lcj_fixture_is_committed_and_has_71_atoms():
    syms, coords = P.embedding.read_xyz(LIGAND_XYZ)
    assert len(syms) == 71 and POCKET_PDB.stat().st_size > 100_000


# ---- acceptance 2: vacuum anchor ---------------------------------------------------

def _vacuum(emb):
    return ferric.run_rhf(emb.mol, emb.basis_set, **KW)


def test_zero_pocket_charges_reproduce_vacuum_bit_identically():
    vac = _vacuum(_embed(pocket=None))
    zero = P.PocketField([PointCharge(0.0, c.xyz_ang) for c in FIELD])
    emb = _embed(pocket=zero)
    assert len(emb.point_charges) == 3  # zero charges really are handed to ferric
    # the same call P.compute_energy(use_field=True) makes, kept explicit here with tight convergence
    zero_run = ferric.run_rhf(emb.mol, emb.basis_set, point_charges=emb.point_charges, **KW)
    assert zero_run.energy == vac.energy  # exact equality, not approx
    # and through the public API, default convergence
    assert P.compute_energy(emb, use_field=True).energy == P.compute_energy(emb, use_field=False).energy
    # negative control: real charges are not bit-identical
    assert ferric.run_rhf(emb.mol, emb.basis_set, point_charges=_embed().point_charges, **KW).energy != vac.energy


def test_empty_field_and_no_field_are_vacuum_and_say_so():
    for pocket in (None, P.PocketField([])):
        r = P.compute_energy(_embed(pocket=pocket))
        assert r.field is False and r.energy == P.compute_energy(_embed(pocket=None), use_field=False).energy
    assert P.compute_energy(_embed()).field is True
    assert P.compute_energy(_embed(), use_field=False).field is False


# ---- acceptance 3: noise floor exposed programmatically ----------------------------

def test_noise_floor_and_no_ranking_are_exposed_as_data_not_only_prose(tmp_path):
    assert P.DDE_NOISE_FLOOR_KCAL_MOL == 4.07 and P.RANKS_ANALOGUES is False
    r = P.compute_binding_energy(_xyz(tmp_path), FIELD, basis="sto-3g", min_available_gb=0)
    assert r.noise_floor_kcal_mol == P.DDE_NOISE_FLOOR_KCAL_MOL == 4.07
    assert r.ranks_analogues is False
    # and the prose still says it (a later edit that trims the docstring should fail here)
    doc = inspect.getdoc(P.compute_binding_energy)
    assert "4.07" in doc and "CANNOT DO: RANK ANALOGUES" in doc and "Do not order two analogues by it" in doc


# ---- loader reuse, overlap filtering, embedding ------------------------------------

def test_there_is_one_pqr_parser_and_it_is_the_loaders():
    assert P.parse_pqr is P.loader.parse_pqr
    for mod in (P.embedding, P.prescreen, P.energy, P.relaxation, BE):
        assert "def parse_pqr" not in inspect.getsource(mod)


def test_overlapping_pocket_charges_are_dropped_and_far_ones_kept():
    near = P.PocketField([PointCharge(1.0, (0.0, 0.0, 1.0)), PointCharge(1.0, (0.0, 0.0, 2.5))])
    emb = _embed(pocket=near)
    assert [c.xyz_ang for c in emb.charges] == [(0.0, 0.0, 2.5)]
    assert _embed(pocket=None).charges is None  # no pocket is not "all filtered"
    # Bohr conversion happens once, at the ferric boundary
    q, x, y, z = emb.point_charges[0]
    assert (q, z) == (1.0, pytest.approx(2.5 * ANGSTROM_TO_BOHR))


def test_embed_from_coords_rejects_a_symbol_coordinate_mismatch():
    with pytest.raises(ValueError, match="3 symbols but 2 coordinate"):
        P.embed_ligand_from_coords(WATER[0], WATER[1][:2], basis="sto-3g")


def test_embed_ligand_from_xyz_file_matches_from_coords(tmp_path):
    a = P.embed_ligand(_xyz(tmp_path), pocket=FIELD, basis="sto-3g")
    b = _embed()
    assert a.symbols == b.symbols and a.charges == b.charges
    assert np.allclose(a.coords_angstrom, b.coords_angstrom)


def test_unknown_method_and_dft_without_functional_raise():
    with pytest.raises(ValueError, match="unknown method"):
        P.compute_energy(_embed(), method="mp2")
    with pytest.raises(ValueError, match="requires xc"):
        P.compute_energy(_embed(), method="dft")


def test_alpha_atomic_without_a_default_auxbasis_asks_for_one():
    emb = _embed(basis="sto-3g")
    with pytest.raises(ValueError, match="pass auxbasis explicitly"):
        P.compute_alpha_atomic(emb, P.compute_energy(emb))


def test_memory_guard():
    P.check_available_memory(0.001)
    if BE._available_gb() is None:
        pytest.skip("no /proc/meminfo: memory guard is a no-op on this platform")
    with pytest.raises(MemoryError):
        P.check_available_memory(1_000_000.0)


# ---- prescreen ---------------------------------------------------------------------

def test_field_at_atoms_matches_hand_coulomb_and_rejects_a_coincident_site():
    one = [PointCharge(2.0, (3.0, 0.0, 0.0))]
    f = P.pocket_field_at_atoms(one, [(0.0, 0.0, 0.0)])
    r = 3.0 * ANGSTROM_TO_BOHR
    assert f[0, 0] == pytest.approx(2.0 / r) and f[0, 1] == pytest.approx(-2.0 / r**2)  # field points away from +q
    assert f[0, 2] == f[0, 3] == 0.0
    with pytest.raises(ValueError, match="coincides"):
        P.pocket_field_at_atoms(one, [(3.0, 0.0, 0.0)])


def test_prescreen_score_is_charge_dot_potential_and_checks_inputs():
    emb = _embed()
    q = np.array([-0.8, 0.4, 0.4])
    res = P.prescreen_pose(emb, q)
    assert res.score == pytest.approx(float(q @ res.field_at_atoms[:, 0]))
    assert res.n_pocket_charges == 3
    assert P.prescreen_pose(emb, np.zeros(3)).score == 0.0  # negative control: zero charges, zero score
    with pytest.raises(ValueError, match="1:1"):
        P.prescreen_pose(emb, [0.0])
    with pytest.raises(ValueError, match="nothing to score"):
        P.prescreen_pose(_embed(pocket=None), q)


def test_batch_prescreen_ranks_ascending_and_puts_failures_last(tmp_path):
    near = _xyz(tmp_path, "near.xyz", [(0.0, 0.0, 1.0), (0.76, 0.0, 1.5), (-0.76, 0.0, 1.5)])
    far = _xyz(tmp_path, "far.xyz")
    bad = tmp_path / "bad.xyz"
    bad.write_text("not an xyz")
    pocket = P.PocketField([PointCharge(1.0, (0.0, 0.0, 3.0))])
    out = P.batch_prescreen(pocket, [bad, far, near], lambda e: [-0.8, 0.4, 0.4], basis="sto-3g")
    assert [e.ligand_xyz.name for e in out][-1] == "bad.xyz" and out[-1].error and out[-1].rank is None
    ok = [e for e in out if e.error is None]
    assert [e.rank for e in ok] == [1, 2] and ok[0].result.score <= ok[1].result.score


# ---- relaxation --------------------------------------------------------------------

def test_relaxation_refuses_a_missing_field_rather_than_falling_back_to_vacuum():
    for pocket in (None, P.PocketField([])):
        with pytest.raises(ValueError, match="nothing to relax"):
            P.relax_pose_in_pocket_field(_embed(pocket=pocket))
        with pytest.raises(ValueError, match="nothing to relax"):
            P.relax_pose_in_pocket(_embed(pocket=pocket))


def test_relax_in_field_lowers_the_energy_and_keeps_atom_order():
    emb = _embed(pocket=GENTLE)
    e0 = P.compute_energy(emb).energy
    r = P.relax_pose_in_pocket_field(emb, max_steps=100)
    assert r.converged and r.symbols == ["O", "H", "H"] and r.n_pocket_charges == 1
    assert r.energy < e0 - 1e-4  # negative control: the starting geometry was not already relaxed
    assert not np.allclose(r.coords_angstrom, emb.coords_angstrom)


def test_qmmm_relax_with_fixed_pocket_agrees_with_fixed_field_relax():
    emb = _embed(pocket=GENTLE)
    a = P.relax_pose_in_pocket_field(emb, max_steps=100)
    b = P.relax_pose_in_pocket(emb, max_steps=100)
    assert b.converged and b.n_pocket_charges == 1 and b.symbols == a.symbols
    assert b.energy == pytest.approx(a.energy, abs=1e-5)
