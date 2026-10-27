"""Supermolecular, counterpoise-corrected interaction energy between two QM fragments.

    dE_int = E_AB - E_A(AB basis) - E_B(AB basis)          [counterpoise, Boys-Bernardi]

This is the quantity `compute_binding_energy` is NOT. That one is the ligand's response to a frozen classical pocket
field (one QM molecule, so no supermolecular interaction and no BSSE). Here both partners are QM, in one basis.

In a finite basis each monomer borrows its partner's basis functions inside the dimer, which lowers E_AB for a reason
that has nothing to do with binding: basis-set superposition error (BSSE). It always makes binding look stronger.
Counterpoise (CP) removes it by computing each monomer in the FULL dimer basis, with the partner's centres present as
ghosts (basis functions, no nucleus, no electrons). ferric reads a ghost as an `@`-prefixed symbol in XYZ (`@O`).

Measured in ferric (tools/active_site, July 2026; the code this module ports), RHF, Cs water dimer:

    basis      raw      CP      BSSE   (kcal/mol)
    sto-3g   -5.531  -1.401   +4.130   <- BSSE is 3x the CP value; STO-3G cannot describe an H-bond
    6-31g    -7.487  -6.451   +1.036
    cc-pvdz  -5.787  -3.682   +2.105   (PySCF ghost-atom CP gives -3.682: tests/test_interaction_energy.py)
    def2-svp -5.905  -4.025   +1.881

and for a ligand-residue contact at RHF/STO-3G, BSSE was 86% of the uncorrected value. Uncorrected numbers are not
reported as the answer here; `counterpoise=False` exists to show the size of the correction.

SCOPE. An electronic interaction energy at fixed geometry. NOT a binding free energy: no desolvation, entropy, ligand
strain, pocket relaxation or conformational averaging. Hartree-Fock has no dispersion at all, so an HF number here is
electrostatics + exchange + induction only; for DFT, pass ferric's `dispersion=` through `scf_kwargs` (it reaches all
five SCFs, as it must).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from ..gates import MIN_ATOM_SEPARATION_ANG
from ..model import HARTREE_TO_KCAL, PointCharge
from ._ferric import ferric


@dataclass
class Fragment:
    """One QM fragment: element symbols and coordinates in Angstrom."""

    symbols: list[str]
    coords_angstrom: list[tuple[float, float, float]]
    charge: int = 0
    multiplicity: int = 1

    def __post_init__(self):
        if len(self.symbols) != len(self.coords_angstrom):
            raise ValueError(f"{len(self.symbols)} symbols but {len(self.coords_angstrom)} coordinates")
        if not self.symbols:
            raise ValueError("fragment has no atoms")
        bad = [s for s in self.symbols if not s or s.startswith("@")]
        if bad:
            raise ValueError(f"fragment symbols must be real elements (ghosts are added by the CP code): {bad[:5]}")
        if self.multiplicity < 1:
            raise ValueError(f"multiplicity must be >= 1, got {self.multiplicity}")

    @classmethod
    def from_xyz(cls, path: str | Path, charge: int = 0, multiplicity: int = 1) -> Fragment:
        lines = Path(path).read_text().splitlines()
        n = int(lines[0].split()[0])
        syms, xyz = [], []
        for line in lines[2 : 2 + n]:
            p = line.split()
            syms.append(p[0])
            xyz.append((float(p[1]), float(p[2]), float(p[3])))
        return cls(symbols=syms, coords_angstrom=xyz, charge=charge, multiplicity=multiplicity)

    @property
    def natoms(self) -> int:
        return len(self.symbols)


def _xyz_string(real: Fragment, ghost: Fragment | None) -> str:
    """XYZ in Angstrom with `ghost`'s centres `@`-prefixed (basis only: no nuclear charge, no electrons)."""
    rows = [(s, c) for s, c in zip(real.symbols, real.coords_angstrom)]
    if ghost is not None:
        rows += [("@" + s, c) for s, c in zip(ghost.symbols, ghost.coords_angstrom)]
    lines = [str(len(rows)), "fragment"]
    lines += [f"{s} {x:.10f} {y:.10f} {z:.10f}" for s, (x, y, z) in rows]
    return "\n".join(lines) + "\n"


def min_intermolecular_distance(a: Fragment, b: Fragment) -> float:
    """Shortest A-B atom distance (Angstrom)."""
    return min(math.dist(p, q) for p in a.coords_angstrom for q in b.coords_angstrom)


@dataclass
class InteractionEnergyResult:
    """Supermolecular interaction energy. All energies in Hartree.

    `bsse_hartree` = (E_A own basis - E_A dimer basis) + (same for B): what the monomers gained purely from the
    partner's basis. It is >= 0 for a sane calculation (each monomer is variationally lower in the larger basis).
    """

    e_dimer: float
    e_a_dimer_basis: float
    e_b_dimer_basis: float
    e_a_monomer_basis: float
    e_b_monomer_basis: float
    delta_e_hartree: float  # the reported value: CP-corrected unless counterpoise=False
    delta_e_cp_hartree: float
    delta_e_uncorrected_hartree: float
    bsse_hartree: float
    counterpoise: bool
    basis: str
    method: str

    @property
    def delta_e_kcal_mol(self) -> float:
        return self.delta_e_hartree * HARTREE_TO_KCAL

    @property
    def bsse_kcal_mol(self) -> float:
        return self.bsse_hartree * HARTREE_TO_KCAL

    @property
    def bsse_fraction(self) -> float:
        """BSSE as a fraction of |uncorrected dE|. Near or above 1: the uncorrected number was mostly basis artifact."""
        denom = abs(self.delta_e_uncorrected_hartree)
        return math.inf if denom == 0.0 else self.bsse_hartree / denom


def compute_interaction_energy(
    fragment_a: Fragment,
    fragment_b: Fragment,
    basis: str = "def2-svp",
    method: str = "rhf",
    xc: str | None = None,
    counterpoise: bool = True,
    point_charges: list[PointCharge] | None = None,
    **scf_kwargs,
) -> InteractionEnergyResult:
    """Supermolecular interaction energy between two QM fragments, counterpoise-corrected by default.

    Five SCFs: the dimer, each monomer in the dimer basis (partner as ghosts), and each monomer in its own basis
    (needed to report BSSE). `counterpoise=False` still runs all five, so BSSE is always reported; the flag only
    selects which dE goes into `delta_e_hartree`.

    `point_charges` (smeltery `PointCharge`s, Angstrom; converted to Bohr here, at the ferric boundary) applies the
    SAME classical field to all five SCFs. A field identical in every term cancels out of dE except through its effect
    on the densities, which is the polarization you want to keep. It is deliberately not configurable per term.

    The dimer's charge is the sum; its multiplicity is `ma + mb - 1` (spins added). That is right for closed + closed
    and for one open shell, NOT for antiferromagnetic coupling; check the combination before passing open shells.

    Raises if the fragments overlap (an A atom within `MIN_ATOM_SEPARATION_ANG` of a B atom: a duplicated atom, e.g. a
    residue fragment that still contains the ligand) or if any of the five SCFs fails to converge.
    """
    if method not in ("rhf", "dft"):
        raise ValueError(f"unknown method {method!r}; expected 'rhf' or 'dft'")
    if method == "dft" and xc is None:
        raise ValueError("method='dft' requires xc=<functional name>")
    d_min = min_intermolecular_distance(fragment_a, fragment_b)
    if d_min < MIN_ATOM_SEPARATION_ANG:
        raise ValueError(
            f"fragments overlap: closest A-B atom pair is {d_min:.3f} A apart (< {MIN_ATOM_SEPARATION_ANG} A). "
            "The same atom is probably in both fragments."
        )

    basis_set = ferric.BasisSet.bundled(basis)
    pcs = [pc.as_ferric_bohr() for pc in point_charges] if point_charges else None

    def run(real: Fragment, ghost: Fragment | None, charge: int, mult: int, label: str) -> float:
        mol = ferric.Molecule.from_xyz_string(_xyz_string(real, ghost), charge, mult)
        if method == "rhf":
            raw = ferric.run_rhf(mol, basis_set, point_charges=pcs, **scf_kwargs)
        else:
            raw = ferric.run_dft(mol, basis_set, functional=xc, point_charges=pcs, **scf_kwargs)
        if not raw.converged:
            raise RuntimeError(
                f"SCF did not converge for the {label} term ({method}/{basis}). An unconverged term makes the "
                "difference meaningless, so this is fatal rather than a warning."
            )
        # RhfResult exposes `.energy`; DftResult exposes `.total_energy` (the split energy.compute_energy handles).
        return raw.energy if hasattr(raw, "energy") else raw.total_energy

    a, b = fragment_a, fragment_b
    e_ab = run(Fragment(a.symbols + b.symbols, a.coords_angstrom + b.coords_angstrom), None,
               a.charge + b.charge, a.multiplicity + b.multiplicity - 1, "dimer")  # fmt: skip
    e_a_cp = run(a, b, a.charge, a.multiplicity, "A in the dimer basis")
    e_b_cp = run(b, a, b.charge, b.multiplicity, "B in the dimer basis")
    e_a_own = run(a, None, a.charge, a.multiplicity, "A in its own basis")
    e_b_own = run(b, None, b.charge, b.multiplicity, "B in its own basis")

    de_cp = e_ab - e_a_cp - e_b_cp
    de_raw = e_ab - e_a_own - e_b_own
    return InteractionEnergyResult(
        e_dimer=e_ab,
        e_a_dimer_basis=e_a_cp,
        e_b_dimer_basis=e_b_cp,
        e_a_monomer_basis=e_a_own,
        e_b_monomer_basis=e_b_own,
        delta_e_hartree=de_cp if counterpoise else de_raw,
        delta_e_cp_hartree=de_cp,
        delta_e_uncorrected_hartree=de_raw,
        bsse_hartree=(e_a_own - e_a_cp) + (e_b_own - e_b_cp),
        counterpoise=counterpoise,
        basis=basis,
        method=method,
    )
