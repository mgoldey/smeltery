"""Pocket field, ligand embedding and the field-vs-vacuum binding energy.

Migrated from ferric's `tools/active_site` (issue #11). The loader
(`loader.py`, from #3) is the single PQR/PDB reader: positions are Angstrom
`PointCharge`s and become Bohr only at the ferric boundary. There is NO default
distance cutoff (truncation is not monotone: 10 A was worse than 12 A, and the
net charge wandered between +0.02 and +3.55 e), so none is added here.

`compute_binding_energy` does NOT rank analogues; see `DDE_NOISE_FLOOR_KCAL_MOL`.

`compute_interaction_energy` is the other route: both partners QM (e.g. the ligand and a link-H-capped residue from
`residue_fragment_from_pqr`), counterpoise-corrected for BSSE.
"""

from .binding_energy import (
    DDE_NOISE_FLOOR_KCAL_MOL,
    RANKS_ANALOGUES,
    BindingEnergyResult,
    check_available_memory,
    compute_binding_energy,
)
from .embedding import EmbeddedLigand, embed_ligand, embed_ligand_from_coords
from .energy import EnergyResult, compute_alpha_atomic, compute_charges, compute_energy
from .interaction_energy import Fragment, InteractionEnergyResult, compute_interaction_energy
from .loader import (
    PDB2PQR,
    Pdb2PqrUnavailableError,
    PocketField,
    check_reference_pose,
    file_digest,
    load_pocket,
    min_distance_to_field_ang,
    parse_pqr,
    pdb2pqr_version,
)
from .prescreen import (
    BatchPrescreenEntry,
    PrescreenResult,
    batch_prescreen,
    pocket_field_at_atoms,
    prescreen_pose,
)
from .relaxation import RelaxedPose, RelaxedPoseQmmm, relax_pose_in_pocket, relax_pose_in_pocket_field
from .residue_fragment import LinkCap, ResidueFragment, residue_fragment_from_pqr

__all__ = [
    "BatchPrescreenEntry",
    "BindingEnergyResult",
    "DDE_NOISE_FLOOR_KCAL_MOL",
    "EmbeddedLigand",
    "EnergyResult",
    "Fragment",
    "InteractionEnergyResult",
    "LinkCap",
    "PDB2PQR",
    "Pdb2PqrUnavailableError",
    "PocketField",
    "check_reference_pose",
    "PrescreenResult",
    "RANKS_ANALOGUES",
    "RelaxedPose",
    "RelaxedPoseQmmm",
    "ResidueFragment",
    "batch_prescreen",
    "check_available_memory",
    "compute_alpha_atomic",
    "compute_binding_energy",
    "compute_charges",
    "compute_energy",
    "compute_interaction_energy",
    "embed_ligand",
    "embed_ligand_from_coords",
    "file_digest",
    "load_pocket",
    "min_distance_to_field_ang",
    "parse_pqr",
    "pdb2pqr_version",
    "pocket_field_at_atoms",
    "prescreen_pose",
    "relax_pose_in_pocket",
    "relax_pose_in_pocket_field",
    "residue_fragment_from_pqr",
]
