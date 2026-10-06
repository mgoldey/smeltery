"""Candidate generators: designed analogues, isomer enumeration, substitution proposals.

Migrated from ferric's `tools/morph`, `tools/isomers` and
`tools/pipeline/substitution.py`. GENERIC LIBRARY: it contains no molecule; a
campaign supplies its own parent and hypothesis set.

Pairing is NOT here. Analogue pose i is built on parent pose i by
`smeltery.tiers.PairedPoses`, the one pairing implementation; `paired` keeps
only the statistics over its output. Substitution proposals are keyed by
(substituent, SITE), never by substituent alone.
"""

from .design import Analogue, PharmacophoreSpec
from .embed import EmbeddedAnalogue, embed_analogue
from .enumerate import EnumerationReport, enumerate_isomers, enumerate_with_report
from .model import Isomer
from .paired import PairedPose, PairedResult, paired_ddE, pairs_from_candidates
from .structural import bioisostere_swaps, ring_contractions, stereoisomers
from .substitutional import COMMON_SUBSTITUENTS, substituent_scan
from .substitution import (
    EmbeddedProposal, SubstitutionProposal, embed_proposals, propose_substitutions, proposal_name,
    proposals_by_key, relative_descriptors, to_candidates,
)
from .topology import GraphSanityError, assert_graph_is_sane, mol_with_coords

__all__ = [
    "Analogue", "PharmacophoreSpec", "EmbeddedAnalogue", "embed_analogue",
    "EnumerationReport", "enumerate_isomers", "enumerate_with_report", "Isomer",
    "PairedPose", "PairedResult", "paired_ddE", "pairs_from_candidates",
    "bioisostere_swaps", "ring_contractions", "stereoisomers", "COMMON_SUBSTITUENTS", "substituent_scan",
    "EmbeddedProposal", "SubstitutionProposal", "embed_proposals", "propose_substitutions", "proposal_name",
    "proposals_by_key", "relative_descriptors", "to_candidates",
    "GraphSanityError", "assert_graph_is_sane", "mol_with_coords",
]
