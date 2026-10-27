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

import functools
import math
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..gates import BOND_SLACK_ANG, MIN_ATOM_SEPARATION_ANG
from ..model import HARTREE_TO_KCAL, PointCharge
from ._ferric import ferric

#: Point charges within this distance (Angstrom) of any QM atom are dropped, as `embed_ligand` does: a field taken from
#: the same PQR a residue fragment was cut from still holds that residue's own atoms and the neighbours its link H's
#: replaced (one sits 0.24 A from a cap H). Same 1.5 A default as `embedding`.
OVERLAP_CUTOFF_ANG = 1.5
#: A BSSE below this (Hartree) is not round-off: a monomer went UP in energy when given more basis functions, which a
#: variational SCF cannot do. Measured round-off: |BSSE| 1e-11 Ha for two waters 50 A apart.
BSSE_FLOOR_HARTREE = -1e-8
#: Basis-name markers of the bundled sets that carry an ECP (def2-* from Rb, Z=37; aug-cc-pvdz-pp likewise).
_ECP_BASIS_MARKERS = ("def2", "-pp")
_ECP_MIN_Z = 37


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
        x = np.asarray(self.coords_angstrom, dtype=float)
        d = np.linalg.norm(x[:, None] - x[None], axis=2)
        np.fill_diagonal(d, np.inf)
        i, j = np.unravel_index(np.argmin(d), d.shape)
        if d[i, j] < MIN_ATOM_SEPARATION_ANG:
            raise ValueError(
                f"atoms {i} ({self.symbols[i]}) and {j} ({self.symbols[j]}) are {d[i, j]:.3f} A apart: coincident atoms"
            )

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


def _check_not_bonded(a: Fragment, b: Fragment) -> None:
    """Refuse fragments that share a covalent bond (`smeltery.gates` rule: covalent radii + `BOND_SLACK_ANG`).

    A bond between A and B is an uncapped cut: each monomer then has a dangling valence and dE is not an interaction
    energy. Cap the cut (`residue_fragment_from_pqr`) or treat the pair as one molecule.
    """
    from rdkit import Chem

    table = Chem.GetPeriodicTable()
    for i, (si, pi) in enumerate(zip(a.symbols, a.coords_angstrom)):
        for j, (sj, pj) in enumerate(zip(b.symbols, b.coords_angstrom)):
            d = math.dist(pi, pj)
            if d < MIN_ATOM_SEPARATION_ANG:
                raise ValueError(
                    f"fragments overlap: A atom {i} ({si}) and B atom {j} ({sj}) are {d:.3f} A apart "
                    f"(< {MIN_ATOM_SEPARATION_ANG} A). The same atom is probably in both fragments."
                )
            limit = table.GetRcovalent(si) + table.GetRcovalent(sj) + BOND_SLACK_ANG
            if d <= limit:
                raise ValueError(
                    f"fragments are covalently bonded: A atom {i} ({si}) - B atom {j} ({sj}) at {d:.3f} A "
                    f"(bond limit {limit:.2f} A). An uncapped cut is not an interaction energy; cap it first."
                )


@functools.lru_cache(maxsize=1)
def ferric_ghost_has_no_ecp() -> bool:
    """True if the installed ferric gives a ghost atom basis functions only, no ECP.

    ferric before PR #572 (including the 0.1.0rc8 wheel) attached an ECP to ghost centres too, so a counterpoise ghost
    heavy atom changed the Hamiltonian. Probe: He + @I (def2-svp, 1.5 A) must lie BELOW bare He, as any basis-only
    addition does. Measured: +1.865e-3 Ha above with the bug; -3.744e-4 below with the fix. Two tiny SCFs, cached.
    """
    bs = ferric.BasisSet.bundled("def2-svp")
    he = ferric.run_rhf(ferric.Molecule.from_xyz_string("1\nprobe\nHe 0 0 0\n", 0, 1), bs).energy
    he_ghost = ferric.run_rhf(ferric.Molecule.from_xyz_string("2\nprobe\nHe 0 0 0\n@I 0 0 1.5\n", 0, 1), bs).energy
    return he_ghost < he


def _check_ghost_ecp(a: Fragment, b: Fragment, basis: str) -> None:
    if not any(m in basis.lower() for m in _ECP_BASIS_MARKERS):
        return
    from rdkit import Chem

    table = Chem.GetPeriodicTable()
    heavy = sorted({s for s in a.symbols + b.symbols if table.GetAtomicNumber(s) >= _ECP_MIN_Z})
    if heavy and not ferric_ghost_has_no_ecp():
        raise ValueError(
            f"{heavy} carry an ECP in {basis}, and this ferric also attaches the ECP to their GHOSTS (fixed in ferric "
            "PR #572, not in 0.1.0rc8), so every counterpoise term with such a ghost has the wrong Hamiltonian. "
            "Upgrade ferric, or use an all-electron basis for these elements."
        )


def filter_point_charges(
    charges: list[PointCharge], fragments: list[Fragment], cutoff_ang: float = OVERLAP_CUTOFF_ANG
) -> tuple[list[PointCharge], list[PointCharge]]:
    """(kept, dropped): charges within `cutoff_ang` of any atom of any fragment are dropped."""
    x = np.asarray([c for f in fragments for c in f.coords_angstrom], dtype=float)
    kept, dropped = [], []
    for pc in charges:
        d = np.linalg.norm(x - np.asarray(pc.xyz_ang), axis=1).min()
        (dropped if d < cutoff_ang else kept).append(pc)
    return kept, dropped


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
    n_point_charges_used: int = 0
    point_charges_dropped: list[PointCharge] = field(default_factory=list)

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
    overlap_cutoff_angstrom: float = OVERLAP_CUTOFF_ANG,
    **scf_kwargs,
) -> InteractionEnergyResult:
    """Supermolecular interaction energy between two QM fragments, counterpoise-corrected by default.

    Five SCFs: the dimer, each monomer in the dimer basis (partner as ghosts), and each monomer in its own basis
    (needed to report BSSE). `counterpoise=False` still runs all five, so BSSE is always reported; the flag only
    selects which dE goes into `delta_e_hartree`.

    `point_charges` (smeltery `PointCharge`s, Angstrom; converted to Bohr here, at the ferric boundary) applies the
    SAME classical field to all five SCFs. A field identical in every term cancels out of dE except through its effect
    on the densities, which is the polarization you want to keep. It is deliberately not configurable per term.
    Charges within `overlap_cutoff_angstrom` of ANY atom of A or B are dropped once, before any SCF, so all five terms
    see the same field (as `embed_ligand` filters). The dropped charges are on the result and named in a warning.

    The dimer's charge is the sum; its multiplicity is `ma + mb - 1` (spins added). That is right for closed + closed
    and for one open shell, NOT for antiferromagnetic coupling. method="rhf" is closed-shell only: ferric's run_rhf
    refuses any term with multiplicity > 1.

    Raises before any SCF if: the fragments overlap or are covalently bonded (an uncapped cut); a heavy element in an
    ECP basis would be a ghost and the installed ferric attaches ECPs to ghosts (`ferric_ghost_has_no_ecp`). Raises
    after the SCFs if any term failed to converge or BSSE < `BSSE_FLOOR_HARTREE` (a monomer rose in a larger basis).
    """
    if method not in ("rhf", "dft"):
        raise ValueError(f"unknown method {method!r}; expected 'rhf' or 'dft'")
    if method == "dft" and xc is None:
        raise ValueError("method='dft' requires xc=<functional name>")
    _check_not_bonded(fragment_a, fragment_b)
    _check_ghost_ecp(fragment_a, fragment_b, basis)

    kept, dropped = filter_point_charges(point_charges or [], [fragment_a, fragment_b], overlap_cutoff_angstrom)
    if dropped:
        warnings.warn(
            f"dropped {len(dropped)} of {len(point_charges)} point charges within {overlap_cutoff_angstrom} A of a QM "
            f"atom (net {sum(c.q for c in dropped):+.4f} e); the same {len(kept)} reach all five SCFs",
            stacklevel=2,
        )
    basis_set = ferric.BasisSet.bundled(basis)
    pcs = [pc.as_ferric_bohr() for pc in kept] if kept else None

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
    bsse = (e_a_own - e_a_cp) + (e_b_own - e_b_cp)
    if bsse < BSSE_FLOOR_HARTREE:
        raise RuntimeError(
            f"BSSE = {bsse:.3e} Ha < {BSSE_FLOOR_HARTREE:.0e}: a monomer went UP in energy in the larger (dimer) basis "
            f"(A {e_a_cp - e_a_own:+.3e}, B {e_b_cp - e_b_own:+.3e} Ha), which a variational SCF cannot do. A "
            "ghost or a term is broken (a ghost carrying an ECP, a different SCF state); dE is not usable."
        )
    return InteractionEnergyResult(
        e_dimer=e_ab,
        e_a_dimer_basis=e_a_cp,
        e_b_dimer_basis=e_b_cp,
        e_a_monomer_basis=e_a_own,
        e_b_monomer_basis=e_b_own,
        delta_e_hartree=de_cp if counterpoise else de_raw,
        delta_e_cp_hartree=de_cp,
        delta_e_uncorrected_hartree=de_raw,
        bsse_hartree=bsse,
        counterpoise=counterpoise,
        basis=basis,
        method=method,
        n_point_charges_used=len(kept),
        point_charges_dropped=dropped,
    )
