"""One whole protein residue as a closed-shell QM `Fragment`, with link-hydrogen caps at every severed bond.

Ported from ferric `tools/active_site/interaction_energy.residue_fragment_from_pqr` (July 2026), generalised:
ferric capped exactly the N(i)-C(i-1) and C(i)-N(i+1) backbone bonds, found by residue NUMBER. That caps a bond that
is not there at a chain break (numbering continues across a gap) and misses one that is (a disulfide). Here the
severed bonds are found from the geometry: every covalent bond between a residue atom and an atom outside it, by the
same rule `smeltery.gates` uses (covalent radii + `BOND_SLACK_ANG`, Open Babel's convention).

CAPPING. Each severed partner is replaced by a hydrogen on the original bond vector at a standard X-H length
(`LINK_BOND_LENGTH_ANG`: C-H 1.09, N-H 1.01, S-H 1.34 A). This is the standard link-atom scheme and an approximation:
the cap is not the neighbouring residue and carries no backbone dipole. ACE/NME caps keep the peptide dipoles at ~6
extra atoms per cut and are not implemented. Note the QM/MM tier (`smeltery.tiers`) places its link H by a scale
factor along the cut bond instead (ferric's `DEFAULT_LINK_SCALE`, 1.09/1.53 for C-C); that form is needed for a QM/MM
gradient's chain rule. For a single-point fragment the fixed X-H length is the standard choice and does not depend on
how long the cut bond happened to be in the structure.

INPUT. A hydrogen-complete PQR (PDB2PQR/AMBER output), not a raw PDB: crystallography does not resolve hydrogens, so a
residue taken straight from a PDB is missing every H it has, and capping it still leaves dangling valences.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from ..gates import BOND_SLACK_ANG, MIN_ATOM_SEPARATION_ANG
from .interaction_energy import Fragment

#: Standard X-H bond length (Angstrom) for a link hydrogen capping a bond severed at atom X.
LINK_BOND_LENGTH_ANG = {"C": 1.09, "N": 1.01, "O": 0.96, "S": 1.34}

#: Residue names whose atom names start with their element (AMBER/PDB2PQR protonation variants, caps, water). Only
#: for these is the element read from the atom name: elsewhere `CL` is chlorine, not carbon.
STANDARD_RESIDUES = frozenset(
    "ALA ARG ASN ASP ASH CYS CYX CYM GLN GLU GLH GLY HIS HID HIE HIP ILE LEU LYS LYN MET PHE PRO SER THR TRP TYR VAL "
    "ACE NME NHE HOH WAT".split()
)

#: Covalent radii (Angstrom; RDKit's table, which `smeltery.gates` reads) for the elements of STANDARD_RESIDUES.
_RCOV = {"H": 0.31, "C": 0.76, "N": 0.71, "O": 0.66, "S": 1.05}
#: Radius assumed for an atom of a non-standard residue when deciding whether it might be bonded: the largest above.
_RCOV_UNKNOWN = max(_RCOV.values())


@dataclass(frozen=True)
class PqrAtom:
    name: str
    resname: str
    chain: str
    resnum: int
    xyz: tuple[float, float, float]

    @property
    def element(self) -> str | None:
        """Element read from the atom name for a standard residue; None for anything else."""
        if self.resname not in STANDARD_RESIDUES:
            return None
        for ch in self.name:
            if ch.isalpha():
                el = ch.upper()
                return el if el in _RCOV else None
        return None

    def label(self) -> str:
        return f"{self.resname}{self.resnum}{':' + self.chain if self.chain else ''}/{self.name}"


def parse_pqr_atoms(text: str) -> list[PqrAtom]:
    """ATOM/HETATM records with names and residue ids.

    PQR is whitespace-delimited and writers differ on whether the chain ID is present (`smeltery.pocket.parse_pqr`
    counts from the end for the same reason): `ATOM serial name resname [chain] resnum x y z q r`. A record with an
    insertion code or glued columns is refused rather than guessed.
    """
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.startswith(("ATOM", "HETATM")):
            continue
        f = line.split()
        try:
            if len(f) == 11:
                name, resname, chain, resnum = f[2], f[3], f[4], int(f[5])
            elif len(f) == 10:
                name, resname, chain, resnum = f[2], f[3], "", int(f[4])
            else:
                raise ValueError(f"expected 10 or 11 fields, got {len(f)}")
            x, y, z = (float(v) for v in f[-5:-2])
        except ValueError as e:
            raise ValueError(f"PQR line {lineno} is not a record this reader understands ({e}): {line!r}") from e
        out.append(PqrAtom(name, resname, chain, resnum, (x, y, z)))
    if not out:
        raise ValueError("no ATOM/HETATM records found")
    return out


@dataclass(frozen=True)
class LinkCap:
    """One link hydrogen: which fragment atom it caps, which atom it replaces, and at what X-H length."""

    anchor_index: int
    anchor: str
    replaced: str
    bond_length_ang: float


@dataclass
class ResidueFragment:
    fragment: Fragment
    label: str
    caps: list[LinkCap] = field(default_factory=list)


def residue_fragment_from_pqr(
    pqr: str | Path,
    resnum: int,
    chain: str | None = None,
    cap: bool = True,
    charge: int | None = None,
) -> ResidueFragment:
    """Residue `resnum` (in `chain`) of a hydrogen-complete PQR as a QM fragment, link-H capped at every severed bond.

    `pqr` is a path or the PQR text. `chain=None` is accepted only when exactly one chain has that residue number.
    `charge` defaults to the PQR's own partial charges summed over the residue and rounded, which is the AMBER
    protonation state PDB2PQR assigned (refused if the sum is not within 0.05 of an integer). The caps are neutral H.

    `cap=False` returns the bare residue with its dangling valences, for diagnostics only: it is not a valid molecule
    and an SCF on it converges to a meaningless energy.

    Refuses: an unknown atom name in the residue (element not C/N/O/S/H), an atom of a non-standard residue within
    bonding distance (a covalent ligand or metal: the partner's element cannot be read from its name), a severed bond
    at a hydrogen, and coincident atoms.
    """
    text = pqr if isinstance(pqr, str) and "\n" in pqr else Path(pqr).read_text()
    atoms = parse_pqr_atoms(text)
    charges = _pqr_charges(text)

    chains = sorted({a.chain for a in atoms if a.resnum == resnum})
    if not chains:
        raise ValueError(
            f"residue {resnum} not found; first residues present: {sorted({a.resnum for a in atoms})[:10]}"
        )
    if chain is None:
        if len(chains) > 1:
            raise ValueError(f"residue {resnum} exists in chains {chains}; pass chain=")
        chain = chains[0]
    sel_idx = [i for i, a in enumerate(atoms) if a.resnum == resnum and a.chain == chain]
    if not sel_idx:
        raise ValueError(f"residue {resnum} not found in chain {chain!r}; chains with it: {chains}")
    sel = [atoms[i] for i in sel_idx]
    label = f"{sel[0].resname}{resnum}{':' + chain if chain else ''}"

    symbols = []
    for a in sel:
        el = a.element
        if el is None:
            raise ValueError(f"cannot read an element for {a.label()} (only C/N/O/S/H of standard residues)")
        symbols.append(el)
    coords = [a.xyz for a in sel]

    q = sum(charges[i] for i in sel_idx)
    if charge is None:
        if abs(q - round(q)) > 0.05:
            raise ValueError(f"{label}: PQR charges sum to {q:.3f}, not an integer; pass charge= explicitly")
        charge = round(q)

    caps: list[LinkCap] = []
    if cap:
        inside = set(sel_idx)
        for k, a in enumerate(sel):
            ea = symbols[k]
            ra = _RCOV[ea]
            for j, b in enumerate(atoms):
                if j in inside:
                    continue
                d = math.dist(a.xyz, b.xyz)
                if d < MIN_ATOM_SEPARATION_ANG:
                    raise ValueError(f"{a.label()} and {b.label()} are {d:.3f} A apart: coincident atoms")
                eb = b.element
                rb = _RCOV[eb] if eb is not None else _RCOV_UNKNOWN
                if d > ra + rb + BOND_SLACK_ANG:
                    continue
                if eb is None:
                    raise ValueError(
                        f"{a.label()} is {d:.2f} A from {b.label()} of a non-standard residue: possibly a covalent "
                        "bond to a partner whose element cannot be read from its name; refusing to cap it"
                    )
                if ea == "H" or eb == "H":
                    raise ValueError(f"{a.label()} - {b.label()} ({d:.2f} A) would be a severed bond at a hydrogen")
                symbols.append("H")
                coords.append(_place_link_atom(a.xyz, b.xyz, LINK_BOND_LENGTH_ANG[ea]))
                caps.append(LinkCap(k, a.label(), b.label(), LINK_BOND_LENGTH_ANG[ea]))

    return ResidueFragment(Fragment(symbols, coords, charge=charge), label, caps)


def _pqr_charges(text: str) -> list[float]:
    return [float(line.split()[-2]) for line in text.splitlines() if line.startswith(("ATOM", "HETATM"))]


def _place_link_atom(anchor, removed, bond_length: float) -> tuple[float, float, float]:
    """A capping H on anchor->removed, `bond_length` Angstrom from the anchor."""
    v = [removed[k] - anchor[k] for k in range(3)]
    norm = math.sqrt(sum(c * c for c in v))
    if norm == 0.0:
        raise ValueError("anchor and removed atom are coincident; cannot place a link atom")
    return tuple(anchor[k] + v[k] / norm * bond_length for k in range(3))
