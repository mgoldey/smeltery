"""Counterpoise interaction energy (smeltery.pocket.interaction_energy).

Anchors, each able to fail:
* the trivial limit: two closed-shell atoms 50 A apart interact by exactly nothing at HF, and their BSSE vanishes;
  for a polar pair at 50 A the BSSE vanishes so CP and uncorrected agree;
* an independent construction: all five energies of the Cs water dimer against PySCF's ghost-atom CP
  (tests/data/make_water_dimer_cp_pyscf.py), at cc-pVDZ and at STO-3G, where BSSE is three times the CP value;
* the bookkeeping, against a fake engine whose energies are known in closed form (no ferric needed).
"""

from __future__ import annotations

import importlib.util
import re
import types

import pytest

from smeltery import ANGSTROM_TO_BOHR, PointCharge
from smeltery.model import HARTREE_TO_KCAL
from smeltery.pocket import Fragment, compute_interaction_energy
from smeltery.pocket import interaction_energy as ie

WATER_A = Fragment(
    ["O", "H", "H"],
    [(-1.551007, -0.114520, 0.0), (-1.934259, 0.762503, 0.0), (-0.599677, 0.040712, 0.0)],
)
WATER_B = Fragment(
    ["O", "H", "H"],
    [(1.350625, 0.111469, 0.0), (1.680398, -0.373741, -0.758561), (1.680398, -0.373741, 0.758561)],
)

# PySCF 2.13.1 RHF, ghost-atom CP, conv_tol 1e-11 (tests/data/make_water_dimer_cp_pyscf.py), Hartree.
PYSCF_REF = {
    "cc-pvdz": {
        "e_dimer": -152.06253624962358,
        "e_a_dimer_basis": -76.02695155331102,
        "e_b_dimer_basis": -76.02971665131426,
        "e_a_monomer_basis": -76.02660309615551,
        "e_b_monomer_basis": -76.0267103571241,
    },
    "sto-3g": {
        "e_dimer": -149.93537592642605,
        "e_a_dimer_basis": -74.96354382599142,
        "e_b_dimer_basis": -74.96959918715264,
        "e_a_monomer_basis": -74.96340213632374,
        "e_b_monomer_basis": -74.96316006992443,
    },
}


def _shifted(frag: Fragment, dx: float) -> Fragment:
    return Fragment(frag.symbols, [(x + dx, y, z) for x, y, z in frag.coords_angstrom], frag.charge, frag.multiplicity)


# ----------------------------------------------------------------- no engine


def test_fragment_rejects_mismatched_symbols_and_coords():
    with pytest.raises(ValueError, match="2 symbols but 1 coordinates"):
        Fragment(["O", "H"], [(0.0, 0.0, 0.0)])


def test_fragment_rejects_empty_and_ghost_symbols():
    with pytest.raises(ValueError, match="no atoms"):
        Fragment([], [])
    with pytest.raises(ValueError, match="real elements"):
        Fragment(["@O"], [(0.0, 0.0, 0.0)])


def test_dft_requires_a_functional():
    with pytest.raises(ValueError, match="requires xc="):
        compute_interaction_energy(WATER_A, WATER_B, method="dft")


def test_overlapping_fragments_are_refused_before_any_scf():
    """The same atom in both fragments (e.g. a residue fragment that still holds the ligand) is not a dimer."""
    dup = Fragment(["O"], [WATER_A.coords_angstrom[0]])
    with pytest.raises(ValueError, match="fragments overlap"):
        compute_interaction_energy(WATER_A, dup)


class _FakeEngine:
    """Stands in for ferric. Energy in closed form from the XYZ it is handed, so every term is known exactly:

    E = -1.0 per real atom - 0.01 per ghost centre - 0.003 if all six real atoms (both waters) are present.
    So E_AB = -6.003, E_A(AB) = E_B(AB) = -3.03, E_A = E_B = -3.0: dE_CP = +0.057, dE_raw = -0.003, BSSE = +0.06.
    """

    def __init__(self, converged: bool = True, ghost_shift: float = -0.01):
        self.converged = converged
        self.ghost_shift = ghost_shift
        self.calls: list[dict] = []
        self.BasisSet = types.SimpleNamespace(bundled=lambda name: name)
        self.Molecule = types.SimpleNamespace(
            from_xyz_string=lambda xyz, charge, mult: types.SimpleNamespace(xyz=xyz, charge=charge, mult=mult)
        )

    def run_rhf(self, mol, basis_set, point_charges=None, **kw):
        rows = mol.xyz.splitlines()[2:]
        n_real = sum(not r.startswith("@") for r in rows)
        ghosts = len(rows) - n_real
        e = -1.0 * n_real + self.ghost_shift * ghosts - (0.003 if n_real == 6 else 0.0)
        self.calls.append({"n_real": n_real, "ghosts": ghosts, "charge": mol.charge, "pcs": point_charges})
        return types.SimpleNamespace(energy=e, converged=self.converged)


def test_bookkeeping_against_closed_form_energies(monkeypatch):
    fake = _FakeEngine()
    monkeypatch.setattr(ie, "ferric", fake)
    r = compute_interaction_energy(WATER_A, WATER_B, basis="x")
    assert r.e_dimer == pytest.approx(-6.003, abs=1e-12)
    assert r.e_a_dimer_basis == pytest.approx(-3.03, abs=1e-12)
    assert r.e_b_dimer_basis == pytest.approx(-3.03, abs=1e-12)
    assert r.e_a_monomer_basis == pytest.approx(-3.0, abs=1e-12)
    assert r.delta_e_hartree == pytest.approx(0.057, abs=1e-12)
    assert r.delta_e_cp_hartree == pytest.approx(0.057, abs=1e-12)
    assert r.delta_e_uncorrected_hartree == pytest.approx(-0.003, abs=1e-12)
    assert r.bsse_hartree == pytest.approx(0.06, abs=1e-12)
    assert r.delta_e_kcal_mol == pytest.approx(0.057 * HARTREE_TO_KCAL, abs=1e-9)
    # Exactly five SCFs: dimer (6 real), two CP monomers (3 real + 3 ghosts), two own-basis monomers.
    assert sorted((c["n_real"], c["ghosts"]) for c in fake.calls) == [(3, 0), (3, 0), (3, 3), (3, 3), (6, 0)]


def test_counterpoise_false_reports_the_uncorrected_value_and_still_reports_bsse(monkeypatch):
    monkeypatch.setattr(ie, "ferric", _FakeEngine())
    r = compute_interaction_energy(WATER_A, WATER_B, counterpoise=False)
    assert r.delta_e_hartree == pytest.approx(-0.003, abs=1e-12)
    assert r.bsse_hartree == pytest.approx(0.06, abs=1e-12)
    assert r.counterpoise is False


def test_ghost_xyz_carries_the_partner_with_the_at_prefix():
    xyz = ie._xyz_string(WATER_A, WATER_B)
    rows = xyz.splitlines()
    assert rows[0] == "6"
    assert [r.split()[0] for r in rows[2:]] == ["O", "H", "H", "@O", "@H", "@H"]
    # Coordinates are written in Angstrom, unchanged (ferric's XYZ reader converts).
    assert [float(v) for v in rows[5].split()[1:]] == pytest.approx(list(WATER_B.coords_angstrom[0]), abs=1e-10)


def test_point_charges_reach_every_scf_in_bohr(monkeypatch):
    """The same field in all five terms, converted once at the ferric boundary (Angstrom -> Bohr)."""
    fake = _FakeEngine()
    monkeypatch.setattr(ie, "ferric", fake)
    pc = PointCharge(-0.5, (10.0, 0.0, 0.0))
    compute_interaction_energy(WATER_A, WATER_B, point_charges=[pc])
    want = [(-0.5, 10.0 * ANGSTROM_TO_BOHR, 0.0, 0.0)]
    assert [c["pcs"] for c in fake.calls] == [want] * 5


def test_dimer_charge_is_the_sum(monkeypatch):
    fake = _FakeEngine()
    monkeypatch.setattr(ie, "ferric", fake)
    a = Fragment(WATER_A.symbols, WATER_A.coords_angstrom, charge=1)
    b = Fragment(WATER_B.symbols, WATER_B.coords_angstrom, charge=-1)
    compute_interaction_energy(a, b)
    by_kind = {(c["n_real"], c["ghosts"], c["charge"]) for c in fake.calls}
    assert (6, 0, 0) in by_kind and (3, 3, 1) in by_kind and (3, 3, -1) in by_kind


def test_point_charges_on_a_qm_atom_are_dropped_once_for_all_five_terms(monkeypatch):
    """Charges 1.0 A from A's O and from B's O are inside the 1.5 A cutoff and must reach NO term (the filter runs
    against A AND B); one 2.0 A from A's O reaches all five."""
    fake = _FakeEngine()
    monkeypatch.setattr(ie, "ferric", fake)
    ax, ay, az = WATER_A.coords_angstrom[0]
    bx, by, bz = WATER_B.coords_angstrom[0]
    near_a = PointCharge(-0.4, (ax, ay, az - 1.0))
    near_b = PointCharge(0.2, (bx, by + 1.0, bz))
    far = PointCharge(0.3, (ax, ay, az - 2.0))
    with pytest.warns(UserWarning, match="dropped 2 of 3 point charges"):
        r = compute_interaction_energy(WATER_A, WATER_B, point_charges=[near_a, near_b, far])
    assert [c["pcs"] for c in fake.calls] == [[far.as_ferric_bohr()]] * 5
    assert r.point_charges_dropped == [near_a, near_b] and r.n_point_charges_used == 1


def test_bonded_fragments_are_refused():
    """An O-H bond (0.97 A) split between A and B is an uncapped cut, not an interaction."""
    a = Fragment(["O", "H"], WATER_A.coords_angstrom[:2])
    b = Fragment(["H"], WATER_A.coords_angstrom[2:])
    with pytest.raises(ValueError, match="covalently bonded"):
        compute_interaction_energy(a, b)


def test_coincident_atoms_within_one_fragment_are_refused():
    with pytest.raises(ValueError, match="coincident atoms"):
        Fragment(["H", "H"], [(0.0, 0.0, 0.0), (0.1, 0.0, 0.0)])


def test_negative_bsse_is_refused(monkeypatch):
    """Ghosts that RAISE a monomer's energy (BSSE -0.06 Ha here) mean a broken ghost or term, not a result."""
    monkeypatch.setattr(ie, "ferric", _FakeEngine(ghost_shift=+0.01))
    with pytest.raises(RuntimeError, match="BSSE = -6.000e-02 Ha"):
        compute_interaction_energy(WATER_A, WATER_B)


HI = Fragment(["H", "I"], [(0.0, 0.0, 0.0), (0.0, 0.0, 1.61)])
HE = Fragment(["He"], [(0.0, 0.0, 4.5)])


def test_ecp_ghost_refused_when_ferric_attaches_ecps_to_ghosts(monkeypatch):
    monkeypatch.setattr(ie, "ferric", _FakeEngine())
    monkeypatch.setattr(ie, "ferric_ghost_has_no_ecp", lambda: False)
    with pytest.raises(ValueError, match=r"\['I'\] carry an ECP in def2-svp.*PR #572"):
        compute_interaction_energy(HI, HE, basis="def2-svp")


def test_ecp_ghost_allowed_by_a_fixed_ferric_or_an_all_electron_basis(monkeypatch):
    monkeypatch.setattr(ie, "ferric", _FakeEngine())
    probed = []
    monkeypatch.setattr(ie, "ferric_ghost_has_no_ecp", lambda: probed.append(1) or True)
    compute_interaction_energy(HI, HE, basis="def2-svp")
    assert probed == [1]
    monkeypatch.setattr(ie, "ferric_ghost_has_no_ecp", lambda: pytest.fail("no ECP basis: must not probe"))
    compute_interaction_energy(HI, HE, basis="sto-3g")


@pytest.mark.needs_ferric
def test_ghost_ecp_probe_on_the_installed_ferric():
    """He + @I (def2-svp, 1.5 A), computed here independently of the probe: +1.865e-3 Ha ABOVE He with the bug
    (ferric <= 0.1.0rc8), -3.744e-4 below with ferric PR #572. The probe must read that sign, and the guard must
    refuse exactly when the installed ferric has the bug."""
    import ferric

    bs = ferric.BasisSet.bundled("def2-svp")
    he = ferric.run_rhf(ferric.Molecule.from_xyz_string("1\nx\nHe 0 0 0\n", 0, 1), bs).energy
    ghost = ferric.run_rhf(ferric.Molecule.from_xyz_string("2\nx\nHe 0 0 0\n@I 0 0 1.5\n", 0, 1), bs).energy
    assert abs(ghost - he) > 1e-4  # either way, far from round-off: the probe discriminates
    ie.ferric_ghost_has_no_ecp.cache_clear()
    assert ie.ferric_ghost_has_no_ecp() is (ghost < he)
    if ghost > he:
        with pytest.raises(ValueError, match="PR #572"):
            compute_interaction_energy(HI, HE, basis="def2-svp")


def test_unconverged_term_is_fatal(monkeypatch):
    monkeypatch.setattr(ie, "ferric", _FakeEngine(converged=False))
    with pytest.raises(RuntimeError, match="did not converge for the dimer term"):
        compute_interaction_energy(WATER_A, WATER_B)


# ----------------------------------------------------------------- real ferric


@pytest.mark.needs_ferric
def test_trivial_limit_two_helium_atoms_50_angstrom_apart():
    """Exactness anchor. Closed-shell atoms have no multipoles: at 50 A the HF interaction is zero, and no basis
    function of one reaches the other, so BSSE is zero too. CP, uncorrected and BSSE must all vanish."""
    a = Fragment(["He"], [(0.0, 0.0, 0.0)])
    b = Fragment(["He"], [(50.0, 0.0, 0.0)])
    r = compute_interaction_energy(a, b, basis="cc-pvdz")
    assert abs(r.delta_e_cp_hartree) < 1e-9
    assert abs(r.delta_e_uncorrected_hartree) < 1e-9
    assert abs(r.bsse_hartree) < 1e-9
    assert r.e_a_dimer_basis == pytest.approx(r.e_a_monomer_basis, abs=1e-9)


@pytest.mark.needs_ferric
def test_trivial_limit_water_dimer_pulled_50_angstrom_apart():
    """At 50 A the basis overlap is gone, so BSSE -> 0 and CP == uncorrected; the dipole-dipole tail (~6e-7 Ha for
    two waters, a classical estimate) is all that is left of dE."""
    r = compute_interaction_energy(WATER_A, _shifted(WATER_B, 50.0), basis="cc-pvdz")
    assert abs(r.bsse_hartree) < 1e-9
    assert r.delta_e_cp_hartree == pytest.approx(r.delta_e_uncorrected_hartree, abs=1e-9)
    assert abs(r.delta_e_cp_hartree) < 5e-6


def _assert_matches_pyscf(r, ref, tol):
    for key, want in ref.items():
        assert getattr(r, key) == pytest.approx(want, abs=tol), f"{key}: ferric {getattr(r, key)!r} vs PySCF {want!r}"
    de_cp = ref["e_dimer"] - ref["e_a_dimer_basis"] - ref["e_b_dimer_basis"]
    de_raw = ref["e_dimer"] - ref["e_a_monomer_basis"] - ref["e_b_monomer_basis"]
    assert r.delta_e_cp_hartree == pytest.approx(de_cp, abs=tol)
    assert r.delta_e_uncorrected_hartree == pytest.approx(de_raw, abs=tol)


@pytest.mark.needs_ferric
def test_water_dimer_cc_pvdz_matches_pyscf_counterpoise():
    """Independent construction: PySCF's ghost atoms, integrals and SCF. dE_CP = -3.682 kcal/mol."""
    r = compute_interaction_energy(WATER_A, WATER_B, basis="cc-pvdz")
    _assert_matches_pyscf(r, PYSCF_REF["cc-pvdz"], 1e-9)  # measured 1.5e-11 (pinned build and PyPI 0.1.0rc8)
    assert r.delta_e_kcal_mol == pytest.approx(-3.682, abs=0.002)
    # The uncorrected value is 2.1 kcal/mol away: a test that cannot tell them apart is not a test of CP.
    assert r.delta_e_uncorrected_hartree * HARTREE_TO_KCAL == pytest.approx(-5.787, abs=0.002)


@pytest.mark.needs_ferric
def test_water_dimer_sto3g_bsse_dominates_and_matches_pyscf():
    """At STO-3G the BSSE (+4.13 kcal/mol) is three times the CP interaction (-1.40): uncorrected is mostly artifact."""
    r = compute_interaction_energy(WATER_A, WATER_B, basis="sto-3g")
    _assert_matches_pyscf(r, PYSCF_REF["sto-3g"], 1e-7)  # measured 4.7e-8 (pinned build and PyPI 0.1.0rc8)
    assert r.bsse_kcal_mol == pytest.approx(4.130, abs=0.002)
    assert r.delta_e_kcal_mol == pytest.approx(-1.401, abs=0.002)


@pytest.mark.needs_ferric
@pytest.mark.skipif(importlib.util.find_spec("pyscf") is None, reason="pyscf not installed (reference values pinned)")
def test_water_dimer_against_live_pyscf():
    """Same comparison computed live, when PySCF is present, so the pinned numbers cannot silently go stale."""
    import runpy
    from pathlib import Path

    script = Path(__file__).parent / "data" / "make_water_dimer_cp_pyscf.py"
    ns = runpy.run_path(str(script), run_name="not_main")
    ref = {k: ns["energy"](*args, "cc-pvdz") for k, args in _pyscf_terms(ns).items()}
    r = compute_interaction_energy(WATER_A, WATER_B, basis="cc-pvdz")
    _assert_matches_pyscf(r, ref, 1e-9)


def _pyscf_terms(ns):
    a, b = ns["A"], ns["B"]
    return {
        "e_dimer": (a + b, []),
        "e_a_dimer_basis": (a, b),
        "e_b_dimer_basis": (b, a),
        "e_a_monomer_basis": (a, []),
        "e_b_monomer_basis": (b, []),
    }


@pytest.mark.needs_ferric
def test_dft_branch_runs():
    """ferric's original DFT branch read `.energy` from a DftResult (which has `.total_energy`) and raised on every
    call; nothing covered it. The water dimer must be bound with positive BSSE."""
    r = compute_interaction_energy(WATER_A, WATER_B, basis="sto-3g", method="dft", xc="PBE")
    assert r.delta_e_hartree < 0
    assert r.bsse_hartree > 0
    assert r.method == "dft"


def test_module_docstring_table_quotes_the_pinned_cc_pvdz_value():
    """The docstring's -3.682 is the number the PySCF test pins, not a separate claim."""
    ref = PYSCF_REF["cc-pvdz"]
    de = (ref["e_dimer"] - ref["e_a_dimer_basis"] - ref["e_b_dimer_basis"]) * HARTREE_TO_KCAL
    row = re.search(r"cc-pvdz\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)", ie.__doc__)
    assert float(row.group(2)) == pytest.approx(de, abs=5e-4)
