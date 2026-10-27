"""Link-H-capped residue fragments (smeltery.pocket.residue_fragment) on tests/data/gly3.pqr.

gly3.pqr is a hydrogen-complete PDB2PQR/AMBER-style Gly-Gly-Gly (NH3+ ... COO-), no chain IDs. The capping checks are
against the full tripeptide, not against the code's own tables: every atom of the capped residue must have exactly the
coordination it has in the intact chain (the caps restore what the cut removed, no more), every cap must lie on the
bond it replaces, and nothing may be duplicated.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from smeltery.gates import BOND_SLACK_ANG
from smeltery.pocket import residue_fragment_from_pqr
from smeltery.pocket.residue_fragment import parse_pqr_atoms

GLY3 = Path(__file__).parent / "data" / "gly3.pqr"
Z = {"H": 1, "C": 6, "N": 7, "O": 8, "S": 16}
RCOV = {"H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66, "S": 1.05}


def _coordination(symbols, coords):
    x = np.asarray(coords, dtype=float)
    d = np.linalg.norm(x[:, None] - x[None], axis=2)
    r = np.array([RCOV[s] for s in symbols])
    adj = (d <= r[:, None] + r[None] + BOND_SLACK_ANG) & ~np.eye(len(symbols), dtype=bool)
    return adj.sum(axis=1)


def _full_chain():
    atoms = parse_pqr_atoms(GLY3.read_text())
    return atoms, [a.name[0] for a in atoms], [a.xyz for a in atoms]


def test_middle_residue_gets_two_caps_at_standard_lengths_on_the_cut_bonds():
    rf = residue_fragment_from_pqr(GLY3, 2)
    f = rf.fragment
    assert rf.label == "GLY2"
    assert f.symbols == ["N", "C", "C", "O", "H", "H", "H", "H", "H"]
    assert sorted((c.anchor, c.replaced, c.bond_length_ang) for c in rf.caps) == [
        ("GLY2/C", "GLY3/N", 1.09),
        ("GLY2/N", "GLY1/C", 1.01),
    ]
    atoms, _, _ = _full_chain()
    by = {(a.resnum, a.name): a.xyz for a in atoms}
    for cap, h in zip(rf.caps, f.coords_angstrom[-2:]):
        anchor = f.coords_angstrom[cap.anchor_index]
        res, name = cap.replaced.split("/")
        partner = by[(int(res[3:]), name)]
        assert math.dist(anchor, h) == pytest.approx(cap.bond_length_ang, abs=1e-12)
        # On the original bond vector, pointing at the replaced atom (unit vectors equal).
        u_h = np.subtract(h, anchor) / math.dist(anchor, h)
        u_b = np.subtract(partner, anchor) / math.dist(anchor, partner)
        assert u_h == pytest.approx(u_b, abs=1e-12)


def test_capped_residue_restores_the_coordination_the_chain_had():
    """Valence: each atom of capped GLY2 has as many neighbours as in the intact tripeptide; each H has one."""
    atoms, sym_all, xyz_all = _full_chain()
    cn_all = _coordination(sym_all, xyz_all)
    rf = residue_fragment_from_pqr(GLY3, 2)
    f = rf.fragment
    cn = _coordination(f.symbols, f.coords_angstrom)
    idx = [i for i, a in enumerate(atoms) if a.resnum == 2]
    assert list(cn[: len(idx)]) == list(cn_all[idx])
    assert list(cn_all[idx]) == [3, 4, 3, 1, 1, 1, 1]  # N, CA, C(sp2), O, H, HA2, HA3: independent of the code
    assert all(cn[i] == 1 for i, s in enumerate(f.symbols) if s == "H")
    # Closed shell: C2H5NO, neutral, 32 electrons.
    assert sum(Z[s] for s in f.symbols) - f.charge == 32


def test_bare_residue_has_the_dangling_valences_capping_fixes():
    """The negative control: without caps the N and C are under-coordinated, so the valence test above can fail."""
    rf = residue_fragment_from_pqr(GLY3, 2, cap=False)
    cn = _coordination(rf.fragment.symbols, rf.fragment.coords_angstrom)
    assert list(cn) == [2, 4, 2, 1, 1, 1, 1]
    assert rf.caps == []


def test_no_duplicate_or_coincident_atoms():
    for res in (1, 2, 3):
        f = residue_fragment_from_pqr(GLY3, res).fragment
        x = np.asarray(f.coords_angstrom)
        d = np.linalg.norm(x[:, None] - x[None], axis=2) + np.eye(len(x)) * 99
        assert d.min() > 0.95, f"GLY{res}: two atoms {d.min():.3f} A apart"
        assert len({tuple(np.round(c, 6)) for c in f.coords_angstrom}) == f.natoms


def test_terminal_residues_are_capped_only_where_a_bond_was_cut():
    n_term = residue_fragment_from_pqr(GLY3, 1)
    c_term = residue_fragment_from_pqr(GLY3, 3)
    assert [(c.anchor, c.replaced) for c in n_term.caps] == [("GLY1/C", "GLY2/N")]
    assert [(c.anchor, c.replaced) for c in c_term.caps] == [("GLY3/N", "GLY2/C")]
    # Charges from the PQR: NH3+ terminus +1, COO- terminus -1.
    assert (n_term.fragment.charge, c_term.fragment.charge) == (1, -1)
    assert residue_fragment_from_pqr(GLY3, 2).fragment.charge == 0


def _rewrite(text, resnum, dx=0.0, resname=None, chain=None):
    """Residue `resnum`'s records shifted by `dx` A in x, optionally renamed or given a chain ID."""
    out = []
    for line in text.splitlines():
        f = line.split()
        if line.startswith("ATOM") and int(f[-6]) == resnum:
            x, y, z = float(f[-5]) + dx, float(f[-4]), float(f[-3])
            ch = f" {chain}" if chain else (f" {f[4]}" if len(f) == 11 else "")
            rn = resname or f[3]
            line = f"ATOM  {int(f[1]):5d} {f[2]:<4s} {rn:3s}{ch} {resnum:4d} {x:8.3f}{y:8.3f}{z:8.3f} {f[-2]} {f[-1]}"
        out.append(line)
    return "\n".join(out) + "\n"


def test_a_chain_break_is_not_capped(tmp_path):
    """Residue 3 moved 6 A away: GLY2-GLY3 is no longer bonded, so GLY2 gets only its N-side cap. ferric's
    number-based rule (resnum + 1 exists -> cap) would have put an H 1.09 A from C pointing at nothing."""
    text = _rewrite(GLY3.read_text(), 3, dx=6.0)
    rf = residue_fragment_from_pqr(text, 2)
    assert [c.replaced for c in rf.caps] == ["GLY1/C"]


def test_a_bond_to_a_nonstandard_residue_is_refused(tmp_path):
    """GLY3 renamed to a ligand: its N is bonded to GLY2's C, but `N` of an unknown residue need not be nitrogen."""
    text = _rewrite(GLY3.read_text(), 3, resname="LIG")
    with pytest.raises(ValueError, match="non-standard residue"):
        residue_fragment_from_pqr(text, 2)


def test_ambiguous_residue_number_needs_a_chain():
    """GLY1 in chain A and a copy 40 A away in chain B: chain=None must refuse rather than merge the two."""
    text = GLY3.read_text()
    for res in (1, 2, 3):
        text = _rewrite(text, res, chain="A")
    copy_b = _rewrite(GLY3.read_text(), 1, dx=40.0, chain="B")
    text += "\n".join(line for line in copy_b.splitlines() if line.startswith("ATOM") and len(line.split()) == 11)
    with pytest.raises(ValueError, match=r"chains \['A', 'B'\]; pass chain="):
        residue_fragment_from_pqr(text + "\n", 1)
    rf = residue_fragment_from_pqr(text + "\n", 1, chain="B")
    assert rf.label == "GLY1:B" and rf.caps == []  # the chain-B copy is isolated: nothing was cut


def test_missing_residue_is_a_clear_error():
    with pytest.raises(ValueError, match="residue 999 not found"):
        residue_fragment_from_pqr(GLY3, 999)


def test_chain_ids_present_or_absent_parse_the_same():
    plain = parse_pqr_atoms(GLY3.read_text())
    chained = parse_pqr_atoms(_rewrite(GLY3.read_text(), 2, chain="A"))
    assert [(a.name, a.resnum, a.xyz) for a in plain] == [(a.name, a.resnum, a.xyz) for a in chained]


@pytest.mark.needs_ferric
def test_capped_residue_and_water_give_a_converged_cp_interaction():
    """End to end: the capped residue is a closed-shell molecule an SCF converges on, and a water H-bonded to its
    carbonyl O interacts attractively with positive BSSE. (STO-3G: a smoke test of the plumbing, not a number.)"""
    from smeltery.pocket import Fragment, compute_interaction_energy

    res = residue_fragment_from_pqr(GLY3, 2).fragment
    o = np.array(res.coords_angstrom[3])
    c = np.array(res.coords_angstrom[2])
    u = (o - c) / np.linalg.norm(o - c)
    h1 = o + 1.9 * u
    w_o = h1 + 0.96 * u
    perp = np.cross(u, [0.0, 0.0, 1.0])
    perp /= np.linalg.norm(perp)
    h2 = w_o + 0.96 * (np.cos(np.radians(75.5)) * u + np.sin(np.radians(75.5)) * perp)
    water = Fragment(["H", "O", "H"], [tuple(h1), tuple(w_o), tuple(h2)])
    r = compute_interaction_energy(res, water, basis="sto-3g")
    assert r.bsse_hartree > 0
    assert r.delta_e_uncorrected_hartree < r.delta_e_cp_hartree
