"""Pose search behind a `DockingProvider` interface; Vina/Meeko are the optional
`docking` extra (`pip install 'smeltery[docking]'`) and are imported only when a
`VinaProvider` actually docks. Importing this package needs neither.

Pose results carry no charge or multiplicity, because a pose has neither. To hand
one to ferric, go through `pose_to_structure`, which makes you state both.
"""

from __future__ import annotations

from ..model import Pose
from ..structure import Structure
from .affinity import ensemble_score
from .autobox import box_from_coords, box_from_ligand, box_from_residues
from .base import DEFAULT_EXHAUSTIVENESS, Box, DockingProvider, DockResult
from .provider import VinaProvider
from .target import DockingTarget, prepare_target
from .tier import Docking, DockingError
from .united_atom import (
    PoseMismatchError,
    StereochemistryError,
    check_full_pose,
    check_heavy_atom_count,
    parse_smiles_idx_remark,
    restore_hydrogens,
    smiles_from_pdbqt_remark,
)
from .vina_dock import Receptor, prepare_receptor

__all__ = [
    "DEFAULT_EXHAUSTIVENESS",
    "DockingTarget",
    "Receptor",
    "box_from_coords",
    "box_from_ligand",
    "box_from_residues",
    "ensemble_score",
    "prepare_receptor",
    "prepare_target",
    "Box",
    "Docking",
    "DockingError",
    "DockingProvider",
    "DockResult",
    "PoseMismatchError",
    "StereochemistryError",
    "VinaProvider",
    "check_full_pose",
    "check_heavy_atom_count",
    "parse_smiles_idx_remark",
    "pose_to_structure",
    "restore_hydrogens",
    "smiles_from_pdbqt_remark",
]


def pose_to_structure(pose: Pose, charge: int, multiplicity: int, source: str = "<docked pose>") -> Structure:
    """A `smeltery.structure.Structure` for a pose, with charge and multiplicity explicit.

    Neither is optional: a pose carries no charge, and guessing one is how a
    plausible-looking wrong electron count reaches a scorer.
    """
    return Structure(
        tuple(pose.symbols),
        tuple((float(x), float(y), float(z)) for x, y, z in pose.coords_ang),
        charge,
        multiplicity,
        source,
    )
