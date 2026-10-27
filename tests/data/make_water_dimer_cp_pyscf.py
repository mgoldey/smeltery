"""Regenerate the PySCF counterpoise reference pinned in tests/test_interaction_energy.py (PYSCF_REF).

Boys-Bernardi CP for the Cs water dimer at RHF, monomers in the dimer basis via PySCF ghost atoms ("ghost-O" carrying
the O basis, no nucleus). Independent of ferric: different integral code, SCF and ghost implementation.

Usage (needs pyscf, not a smeltery dependency): python make_water_dimer_cp_pyscf.py
Generated 2026-10-27 with PySCF 2.13.1 (conv_tol 1e-11). Not collected by pytest; the live
test imports `energy`, `A` and `B` from it.
"""

import json

from pyscf import gto, scf

A = [("O", (-1.551007, -0.114520, 0.0)), ("H", (-1.934259, 0.762503, 0.0)), ("H", (-0.599677, 0.040712, 0.0))]
B = [
    ("O", (1.350625, 0.111469, 0.0)),
    ("H", (1.680398, -0.373741, -0.758561)),
    ("H", (1.680398, -0.373741, 0.758561)),
]


def energy(real, ghost, basis):
    atoms = list(real) + [("ghost-" + s, c) for s, c in ghost]
    bas = {s: basis for s, _ in real}
    bas.update({"ghost-" + s: gto.basis.load(basis, s) for s, _ in ghost})
    mol = gto.M(atom=atoms, basis=bas, unit="Angstrom", verbose=0, cart=False)
    mf = scf.RHF(mol)
    mf.conv_tol = 1e-11
    e = mf.kernel()
    assert mf.converged
    return e


if __name__ == "__main__":
    out = {}
    for basis in ("cc-pvdz", "sto-3g"):
        out[basis] = {
            "e_dimer": energy(A + B, [], basis),
            "e_a_dimer_basis": energy(A, B, basis),
            "e_b_dimer_basis": energy(B, A, basis),
            "e_a_monomer_basis": energy(A, [], basis),
            "e_b_monomer_basis": energy(B, [], basis),
        }
    print(json.dumps(out, indent=1))
