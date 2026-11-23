"""ATOM/HETATM record parsing shared by structure preparation and box building. Pure text; no backends."""

from __future__ import annotations

from collections.abc import Iterator
from typing import NamedTuple

from .structure import StructureError, element_from_pdb_atom_name

#: Residue names treated as solvent water. (`pocket.loader` keeps its own set: that list is recorded in field
#: provenance, which run digests pin.)
WATER_RESIDUES = frozenset({"HOH", "WAT", "DOD", "TIP", "TIP3", "TIP3P", "SOL"})


class PdbRecordError(ValueError):
    """An ATOM/HETATM line whose coordinates are not numbers."""


class AtomRecord(NamedTuple):
    record: str  # "ATOM" or "HETATM"
    name: str
    resname: str
    chain: str
    resnum: str
    altloc: str  # "" when blank
    element: str  # upper case; from the element column, else from the atom name
    xyz: tuple[float, float, float]
    line: str


def _element(line: str, name: str) -> str:
    column = line[76:78].strip()
    if column:
        return column.upper()
    try:
        return element_from_pdb_atom_name(name).upper()
    except StructureError:
        return ""


def iter_atoms(text: str, first_model: bool = True) -> Iterator[AtomRecord]:
    """The ATOM/HETATM records of `text`. `first_model` stops at the first ENDMDL (an NMR ensemble or MD dump
    repeats the molecule); altlocs are yielded, the caller chooses a conformer. `PdbRecordError` on bad coordinates."""
    for line in text.splitlines():
        if first_model and line.startswith("ENDMDL"):
            return
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            xyz = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
        except ValueError as e:
            raise PdbRecordError(f"bad coordinates in PDB line: {line!r}") from e
        name = line[12:16].strip()
        yield AtomRecord(
            line[:6].strip(),
            name,
            line[17:20].strip(),
            line[21:22].strip(),
            line[22:26].strip(),
            line[16:17].strip(),
            _element(line, name),
            xyz,
            line,
        )
