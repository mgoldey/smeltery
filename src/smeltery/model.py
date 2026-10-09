"""Core data types.

Units are explicit in every field name. Geometry is Ångström at rest
(`coords_ang`); the only Bohr values are point charges handed to ferric, and the
single conversion lives in `ANGSTROM_TO_BOHR`. Several past ferric bugs came from
mixed Å and Bohr crossing a boundary unlabelled.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import numpy as np

# MUST equal the factor ferric applies when it parses XYZ (ferric-core mol.rs:
# 1 / 0.52917721092, CODATA 2010), or point charges and atoms would sit in
# slightly different frames. tests/test_anchors.py measures ferric's factor at
# runtime and fails if the two ever diverge.
ANGSTROM_TO_BOHR = 1.0 / 0.52917721092
HARTREE_TO_KCAL = 627.5094740631


@dataclass(frozen=True)
class Pose:
    """One 3-D placement of a molecule, in Ångström."""

    symbols: tuple[str, ...]
    coords_ang: np.ndarray  # (n_atoms, 3)

    def __post_init__(self) -> None:
        if self.coords_ang.shape != (len(self.symbols), 3):
            raise ValueError(f"coords shape {self.coords_ang.shape} does not match {len(self.symbols)} atoms")

    @property
    def formula(self) -> str:
        """Hill order: C, then H, then the rest alphabetically (H alphabetical if no C)."""
        c = Counter(self.symbols)
        order = (["C", "H"] + sorted(e for e in c if e not in ("C", "H"))) if "C" in c else sorted(c)
        return "".join(f"{el}{c[el] if c[el] > 1 else ''}" for el in order if el in c)

    def to_xyz(self, comment: str = "") -> str:
        lines = [str(len(self.symbols)), comment]
        lines += [f"{s} {x:.10f} {y:.10f} {z:.10f}" for s, (x, y, z) in zip(self.symbols, self.coords_ang, strict=True)]
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class PointCharge:
    """A fixed external charge. Position in Ångström; converted once, at ferric."""

    q: float
    xyz_ang: tuple[float, float, float]

    def as_ferric_bohr(self) -> tuple[float, float, float, float]:
        x, y, z = (v * ANGSTROM_TO_BOHR for v in self.xyz_ang)
        return (self.q, x, y, z)


@dataclass
class Candidate:
    """A molecule moving through the funnel, carrying a pose ensemble.

    `poses[i]` of every candidate is paired with `poses[i]` of the parent.
    Pairing is by index, established by the pose tier. Per-pose results
    live in `per_pose[quantity]`, aligned with `poses`.
    """

    name: str
    smiles: str
    poses: list[Pose] = field(default_factory=list)
    per_pose: dict[str, list[float]] = field(default_factory=dict)
    #: Flexible-receptor sidechain PDBQT text per pose (aligned with `poses`);
    #: empty when docked against a rigid receptor.
    receptor_flex: list[str] = field(default_factory=list)
    #: `gates.PoseReport` from `check_candidate_poses`, or None if the gate has not run.
    pose_report: object | None = None

    @property
    def formula(self) -> str:
        if not self.poses:
            raise ValueError(f"{self.name}: no poses yet, formula unknown")
        return self.poses[0].formula


@dataclass(frozen=True)
class Measurement:
    """A per-candidate estimate with its uncertainty.

    `per_pose` are the samples, in `unit`. `sem` is the standard error of their
    mean. A Measurement with n < 2 has no uncertainty estimate and cannot be
    used to separate candidates.
    """

    mean: float
    sem: float
    n: int
    per_pose: tuple[float, ...]
    unit: str = "kcal/mol"

    @classmethod
    def from_samples(cls, samples: list[float] | np.ndarray, unit: str = "kcal/mol") -> Measurement:
        a = np.asarray(samples, dtype=float)
        if a.ndim != 1 or a.size == 0:
            raise ValueError("need a non-empty 1-D sample")
        sem = float(a.std(ddof=1) / np.sqrt(a.size)) if a.size > 1 else float("nan")
        return cls(float(a.mean()), sem, int(a.size), tuple(float(v) for v in a), unit)
