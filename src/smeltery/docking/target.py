"""`DockingTarget`: receptor + box prepared once, ready to hand to the funnel.

The manual path is: run Meeko, find a box by eye, build a `ctx` dict. This does
it from a PDB in one call, with an optional list of flexible sidechains:

    target = prepare_target("1abc.pdb", workdir, ligand_resname="LIG",
                            flex_residues=["A:45", "A:112"])
    Docking(VinaProvider(), seeds=(1, 2, 3)).run(candidates, target.ctx())

Box choice (first that applies): an explicit `box`, else `ligand_resname`/the
lone HETATM ligand, else the `flex_residues`/`pocket_residues`. With none of
those it raises: a default box in the middle of the protein is a silent wrong
answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .autobox import box_from_ligand, box_from_residues
from .base import Box
from .vina_dock import Receptor, prepare_receptor


@dataclass(frozen=True)
class DockingTarget:
    receptor: Receptor
    box: Box
    source_pdb: str = ""

    def ctx(self) -> dict:
        """The `ctx` keys `Docking.run` needs. Rigid targets pass a bare path."""
        rec = self.receptor if self.receptor.is_flexible else self.receptor.rigid
        return {"receptor": rec, "box": self.box}


def prepare_target(
    pdb_path: str | Path,
    workdir: str | Path,
    *,
    box: Box | None = None,
    ligand_resname: str | None = None,
    pocket_residues: tuple[str, ...] | list[str] = (),
    flex_residues: tuple[str, ...] | list[str] = (),
    padding: float = 8.0,
    use_ligand_box: bool = True,
) -> DockingTarget:
    pdb_path, workdir = Path(pdb_path), Path(workdir)
    if box is None:
        if use_ligand_box and (ligand_resname or _has_het(pdb_path)):
            box = box_from_ligand(pdb_path, ligand_resname, padding)
        elif pocket_residues or flex_residues:
            box = box_from_residues(pdb_path, list(pocket_residues) or list(flex_residues), padding)
        else:
            raise ValueError(
                "no box: pass box=, a co-crystal ligand (ligand_resname=), or "
                "pocket_residues=/flex_residues="
            )
    prepared = prepare_receptor(pdb_path, workdir / pdb_path.stem, tuple(flex_residues))
    rec = prepared if isinstance(prepared, Receptor) else Receptor(prepared)
    return DockingTarget(rec, box, str(pdb_path))


def _has_het(pdb_path: Path) -> bool:
    try:
        box_from_ligand(pdb_path)
    except ValueError as e:
        return "several HETATM" in str(e)  # present but ambiguous: let the caller see the error
    return True
