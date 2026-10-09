"""The docking interface: what a pose-search engine must provide.

A `DockingProvider` takes a ligand with 3-D coordinates, a prepared receptor
and a search box, and returns full-hydrogen `Pose`s with a score per pose. The
funnel and `DockingTier` see only this; Vina is the first implementation, not a
dependency of the funnel. A second engine (Gnina, a fake in a test) needs no
funnel change -- `tests/test_docking_provider.py` proves it with a test double.

Scores are in the engine's own units and are a ranking heuristic. Each
provider says which in `settings()`; nothing here pretends two engines' scores
are comparable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from ..model import Pose

#: Default search effort for every provider. See `vina_dock.dock_ligand` for the
#: measurement: effort above this bought noise, seeds bought spread coverage.
DEFAULT_EXHAUSTIVENESS = 4


@dataclass(frozen=True)
class Box:
    """Axis-aligned search box, Angstrom, in the receptor's frame."""

    center: tuple[float, float, float]
    size: tuple[float, float, float] = (24.0, 24.0, 24.0)


@dataclass
class DockResult:
    """Outcome of one dock call: poses (best first) and one score per pose.

    A search that did not run comes back with `error` set and no poses, never as
    a neutral-looking number. `poses[i]` carries every atom of the input
    molecule, hydrogens included (a provider that cannot guarantee that must
    raise or set `error`, not return a united-atom pose).
    """

    poses: list[Pose] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    error: str | None = None
    #: Per pose, PDBQT text of the flexible receptor residues in that pose ("" if
    #: rigid). Empty list is accepted and means "no flexible receptor".
    flex_receptor: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if len(self.poses) != len(self.scores):
            raise ValueError(
                f"{len(self.poses)} poses but {len(self.scores)} scores"
            )
        if self.flex_receptor and len(self.flex_receptor) != len(self.poses):
            raise ValueError(
                f"{len(self.poses)} poses but {len(self.flex_receptor)} flex-receptor blocks"
            )

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.poses)


@runtime_checkable
class DockingProvider(Protocol):
    name: str

    def dock(
        self,
        mol,
        receptor,
        box: Box,
        seed: int,
        exhaustiveness: int = DEFAULT_EXHAUSTIVENESS,
    ) -> DockResult:
        """Dock an RDKit `mol` (hydrogens, 3-D coordinates) into `receptor`."""
        ...

    def settings(self) -> dict:
        """Native settings, including the exhaustiveness actually used by default."""
        ...

    def score_unit(self) -> str:
        """Unit of `DockResult.scores`."""
        ...
