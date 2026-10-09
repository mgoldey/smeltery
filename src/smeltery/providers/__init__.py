"""Provider interfaces: where a third party plugs in without touching the funnel."""

from .structure import (
    AFDB_ATTRIBUTION,
    AfdbProvider,
    PdbProvider,
    StructureKind,
    StructureProvider,
    StructureResult,
    plddt_from_pdb,
)

__all__ = [
    "AFDB_ATTRIBUTION",
    "AfdbProvider",
    "PdbProvider",
    "StructureKind",
    "StructureProvider",
    "StructureResult",
    "plddt_from_pdb",
]
