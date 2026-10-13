"""Candidate generators: the protocol, the result record, and the closed set of rejection reasons.

A generator proposes molecules and returns them as `Candidate`s for the funnel. Everything it
declines to return is kept as a `Rejection` with a reason from `REASONS`, so a caller can count
what was lost and why. `len(candidates) + len(rejected)` is the number of raw outputs.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from os import PathLike
from typing import Protocol, Sequence

from ..model import Candidate

#: Why a raw output was not returned. Closed on purpose, so counts by reason are comparable between runs.
REASONS: tuple[str, ...] = (
    "scaffold_atom_mismatch",  # a scaffold atom could not be found in the output, or the fit was too loose
    "unsanitizable",  # RDKit could not sanitize the repaired molecule (or the record was unreadable)
    "disconnected",  # more than one fragment
    "scaffold_lost",  # the scaffold is not a substructure of the result
    "forbidden_substructure",  # matched a forbidden SMARTS; `detail` is its label
    "duplicate",  # the same canonical SMILES was already returned
    "size",  # outside the heavy-atom limits
    "atom_order",  # the pose could not be put in the candidate SMILES's atom order
)


class GeneratorError(RuntimeError):
    """A run failed: the interpreter would not start, the runner crashed, or its output was unusable."""


@dataclass(frozen=True)
class Rejection:
    index: int  # position of the raw output, counted across batches from 0
    reason: str
    detail: str = ""
    smiles: str | None = None  # best-effort SMILES of the repaired molecule; None before one exists

    def __post_init__(self) -> None:
        if self.reason not in REASONS:
            raise ValueError(f"unknown rejection reason {self.reason!r}; expected one of {REASONS}")


@dataclass
class GenerationResult:
    candidates: list[Candidate]
    rejected: list[Rejection]
    provenance: dict

    @property
    def n_raw(self) -> int:
        return len(self.candidates) + len(self.rejected)

    def rejection_counts(self) -> dict[str, int]:
        return dict(Counter(r.reason for r in self.rejected))


class CandidateGenerator(Protocol):
    """Proposes analogues of `parent` that keep the heavy atoms listed in `scaffold`."""

    name: str

    def settings(self) -> dict:
        """Everything that shaped the output, JSON-able, for the run record."""
        ...

    def generate(
        self, parent: Candidate, scaffold: Sequence[int], pocket: str | PathLike, *, seed: int
    ) -> GenerationResult:
        """`scaffold` indexes the parent's heavy atoms in SMILES order; `parent.poses[0]` is the bound pose."""
        ...
