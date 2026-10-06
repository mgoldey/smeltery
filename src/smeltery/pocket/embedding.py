"""Embed a ligand (a ferric QM `Molecule`) in an optional pocket's point-charge field.

`embed_ligand` is the unit between "load the pocket once" (`load_pocket`) and
"evaluate an energy" (`compute_energy`): call it once per ligand geometry and
reuse the same `PocketField` across all of them. Pocket charges that sit within
`overlap_cutoff_angstrom` of any ligand atom are dropped (a receptor file that
still holds its own bound ligand would otherwise double-count it).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._ferric import ferric
import numpy as np

from ..model import PointCharge
from .loader import PocketField


@dataclass
class EmbeddedLigand:
    """A QM ligand (Molecule + basis) and the overlap-filtered pocket charges it is evaluated against.

    `charges` are Angstrom `PointCharge`s (the loader's units); `point_charges`
    is the same set as ferric's `(q, x, y, z)` Bohr tuples, the only place the
    unit conversion happens.
    """

    mol: object  # ferric.Molecule
    basis_set: object  # ferric.BasisSet
    basis_name: str
    charges: list[PointCharge] | None  # None: no pocket attached; []: pocket attached, all filtered out
    pocket: PocketField | None
    source_xyz: Path | None
    coords_angstrom: list[tuple[float, float, float]]
    symbols: list[str]

    @property
    def point_charges(self) -> list[tuple[float, float, float, float]] | None:
        return None if self.charges is None else [c.as_ferric_bohr() for c in self.charges]


def read_xyz(path: str | Path) -> tuple[list[str], list[tuple[float, float, float]]]:
    lines = Path(path).read_text().splitlines()
    n = int(lines[0].strip())
    symbols, coords = [], []
    for line in lines[2 : 2 + n]:
        parts = line.split()
        symbols.append(parts[0])
        coords.append((float(parts[1]), float(parts[2]), float(parts[3])))
    if len(symbols) != n:
        raise ValueError(f"{path}: header says {n} atoms but {len(symbols)} rows were read")
    return symbols, coords


def _filter_overlap(pocket, ligand_coords_angstrom, overlap_cutoff_angstrom):
    if pocket is None:
        return None
    lig = np.asarray(ligand_coords_angstrom, dtype=float)
    keep = []
    for c in pocket:
        d2 = np.sum((lig - np.asarray(c.xyz_ang)) ** 2, axis=1)
        if not np.any(d2 < overlap_cutoff_angstrom**2):
            keep.append(c)
    return keep


def embed_ligand(
    ligand_xyz: str | Path,
    pocket: PocketField | None = None,
    basis: str = "def2-svp",
    overlap_cutoff_angstrom: float = 1.5,
) -> EmbeddedLigand:
    """Load a ligand from an xyz file and pair it with `pocket`, re-filtered for overlap with THIS geometry."""
    symbols, coords = read_xyz(ligand_xyz)
    return EmbeddedLigand(
        mol=ferric.Molecule.from_xyz(str(ligand_xyz)),
        basis_set=ferric.BasisSet.bundled(basis),
        basis_name=basis,
        charges=_filter_overlap(pocket, coords, overlap_cutoff_angstrom),
        pocket=pocket,
        source_xyz=Path(ligand_xyz),
        coords_angstrom=coords,
        symbols=symbols,
    )


def embed_ligand_from_coords(
    symbols: list[str],
    coords_angstrom: list[tuple[float, float, float]],
    pocket: PocketField | None = None,
    basis: str = "def2-svp",
    charge: int = 0,
    multiplicity: int = 1,
    overlap_cutoff_angstrom: float = 1.5,
) -> EmbeddedLigand:
    """Same as `embed_ligand`, from in-memory symbols and coordinates (no temp file)."""
    symbols, coords_angstrom = list(symbols), list(coords_angstrom)
    if len(symbols) != len(coords_angstrom):
        # zip() would stop at the shorter one and hand ferric a molecule whose xyz header
        # disagrees with its body (a united-atom pose once did exactly this).
        raise ValueError(
            f"embed_ligand_from_coords: {len(symbols)} symbols but {len(coords_angstrom)} coordinate "
            "rows -- these are per-atom and must match, or the xyz header will disagree with its body"
        )
    xyz = "\n".join(
        [str(len(symbols)), "embed_ligand_from_coords"]
        + [f"{s} {x:.10f} {y:.10f} {z:.10f}" for s, (x, y, z) in zip(symbols, coords_angstrom)]
    ) + "\n"
    return EmbeddedLigand(
        mol=ferric.Molecule.from_xyz_string(xyz, charge, multiplicity),
        basis_set=ferric.BasisSet.bundled(basis),
        basis_name=basis,
        charges=_filter_overlap(pocket, coords_angstrom, overlap_cutoff_angstrom),
        pocket=pocket,
        source_xyz=None,
        coords_angstrom=coords_angstrom,
        symbols=symbols,
    )
