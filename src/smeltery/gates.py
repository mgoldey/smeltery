"""Gates: measurements of how far a ranking can be trusted, not fixes for it.

`charge_sensitivity` scores the same candidates, on the same geometries, under
two independent charge models. A ranking that does not survive the swap is not
a ranking. The gate MEASURES that instability and derives a floor for `cut`;
it cannot correct a systematic error both models share.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import numpy as np

from .model import Candidate, Pose

# A model maps (candidate, quantity) to that candidate's scalar value, e.g. the
# paired ΔΔE of the candidate re-scored with one set of point charges.
ChargeModel = Callable[[Candidate, str], float]


def _ranks(x: np.ndarray) -> np.ndarray:
    """1-based ranks, ties given their average rank."""
    order = np.argsort(x, kind="stable")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(1, len(x) + 1)
    for v in np.unique(x):
        tied = x == v
        if tied.sum() > 1:
            ranks[tied] = ranks[tied].mean()
    return ranks


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    """Spearman rank correlation (Pearson on average ranks); nan if either side is constant."""
    ra, rb = _ranks(np.asarray(a, dtype=float)), _ranks(np.asarray(b, dtype=float))
    if np.ptp(ra) == 0 or np.ptp(rb) == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


@dataclass(frozen=True)
class SensitivityReport:
    """How much the charge model moves the scores.

    `deltas[name]` = value_b - value_a. `floor` is max(delta) - min(delta): the
    largest amount by which swapping models can change the gap between ANY two
    candidates, so two candidates closer than `floor` may change order under
    the swap. It is exactly 0.0 when the models agree everywhere.
    """

    quantity: str
    names: tuple[str, ...]
    values_a: tuple[float, ...]
    values_b: tuple[float, ...]
    n_sign_flips: int
    spearman: float
    deltas: dict[str, float]
    floor: float

    @property
    def n(self) -> int:
        return len(self.names)


def charge_sensitivity(
    candidates: Sequence[Candidate],
    quantity: str,
    model_a: ChargeModel,
    model_b: ChargeModel,
) -> SensitivityReport:
    """Score every candidate under both models and report the disagreement."""
    if len(candidates) < 2:
        raise ValueError("need at least 2 candidates to measure sensitivity")
    names = tuple(c.name for c in candidates)
    if len(set(names)) != len(names):
        raise ValueError("candidate names must be unique")
    a = np.array([model_a(c, quantity) for c in candidates], dtype=float)
    b = np.array([model_b(c, quantity) for c in candidates], dtype=float)
    d = b - a
    return SensitivityReport(
        quantity=quantity,
        names=names,
        values_a=tuple(float(v) for v in a),
        values_b=tuple(float(v) for v in b),
        n_sign_flips=int((np.sign(a) != np.sign(b)).sum()),
        spearman=spearman(a, b),
        deltas={n: float(v) for n, v in zip(names, d, strict=True)},
        floor=float(d.max() - d.min()),
    )


# ---------------------------------------------------------------- pose validity

_INSTALL_HINT = (
    "install the posebusters extra: `pip install 'smeltery[posebusters]'` (or `uv sync --extra posebusters`)"
)


class PoseGateError(RuntimeError):
    """Raised when a quantum tier is handed a pose that failed (or never had) its validity check."""


@dataclass(frozen=True)
class PoseVerdict:
    """One pose: which PoseBusters checks passed and which failed."""

    passed_checks: tuple[str, ...]
    failed_checks: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failed_checks and bool(self.passed_checks)  # no checks run is not a pass


@dataclass(frozen=True)
class PoseReport:
    """Per-pose PoseBusters verdicts, with the config they were produced under."""

    verdicts: tuple[PoseVerdict, ...]
    config: str
    posebusters_version: str
    receptor: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def all_passed(self) -> bool:
        return bool(self.verdicts) and all(v.passed for v in self.verdicts)

    def failures(self) -> dict[int, tuple[str, ...]]:
        """{pose index: failing check names} for every pose that failed."""
        return {i: v.failed_checks for i, v in enumerate(self.verdicts) if not v.passed}

    def to_dict(self) -> dict:
        """JSON-able, for `RunRecord.results`: per-pose pass/fail plus check names."""
        return {
            "config": self.config,
            "posebusters_version": self.posebusters_version,
            "receptor": self.receptor,
            "all_passed": self.all_passed,
            "poses": [
                {"passed": v.passed, "passed_checks": list(v.passed_checks), "failed_checks": list(v.failed_checks)}
                for v in self.verdicts
            ],
        }


def _mol_for_pose(smiles: str, pose: Pose):
    """RDKit mol with the SMILES' bond orders and the pose's coordinates.

    Topology comes from the SMILES, never from the coordinates: perceiving bonds
    from distances is what let chemically impossible docked poses through (carbons
    of degree 5-6 with `usable=True`). The pose must be in `AddHs(smiles)` atom
    order, which is how every pose builder in this package emits them.
    """
    from rdkit import Chem
    from rdkit.Geometry import Point3D

    parsed = Chem.MolFromSmiles(smiles)
    if parsed is None:
        raise ValueError(f"unparseable SMILES: {smiles!r}")
    mol = Chem.AddHs(parsed)
    syms = tuple(a.GetSymbol() for a in mol.GetAtoms())
    if syms != tuple(pose.symbols):
        raise ValueError(
            f"pose atoms {tuple(pose.symbols)} are not in AddHs({smiles!r}) order {syms}; "
            "refusing to assign bonds to a mismatched atom order"
        )
    conf = Chem.Conformer(mol.GetNumAtoms())
    for i, (x, y, z) in enumerate(pose.coords_ang):
        conf.SetAtomPosition(i, Point3D(float(x), float(y), float(z)))
    mol.AddConformer(conf, assignId=True)
    return mol


def _flatness_coords(mol, indices) -> np.ndarray:
    """Coordinates of `indices`, read in one C++ call (same values as PoseBusters' own `_get_coords`)."""
    return np.asarray(mol.GetConformer().GetPositions(), dtype=float)[list(indices)]


def _avoid_point3d_unwind_segfault() -> None:
    """Replace PoseBusters' `flatness._get_coords` (issue #68) with `_flatness_coords`.

    PoseBusters 0.6.5 does `np.array([conf.GetAtomPosition(i) ...])`. NumPy then
    iterates each `Point3D` until RDKit raises the end-of-sequence IndexError from
    C++. RDKit >= 2024.3.2 wheels route every C++ throw through boost's
    stacktrace-from-exception hook, which walks the stack with `_Unwind_Backtrace`;
    on uv's python-build-standalone CPython 3.12.11+/3.13.5+ that walk faults
    (docs/environments.md). `GetPositions()` raises nothing, so the fault is never
    reached. Harmless where it was not faulting: identical coordinates.
    """
    try:
        from posebusters.modules import flatness
    except ImportError:  # posebusters layout changed: nothing to patch
        return
    if hasattr(flatness, "_get_coords"):
        flatness._get_coords = _flatness_coords


def posebusters_check(poses: Sequence[Pose], smiles: str, receptor: str | None = None) -> PoseReport:
    """Run PoseBusters on every pose of one molecule.

    Ligand-only (`config="mol"`: sanitization, connectivity, bond lengths and
    angles, internal clashes, planarity, internal energy) unless `receptor` (a
    protein PDB path) is given, which adds the protein-ligand checks
    (`config="dock"`: minimum distance to protein, volume overlap, ...).
    A pose that fails any check is reported with the check names; nothing is
    repaired. Needs the `posebusters` extra.
    """
    try:
        import posebusters
        from posebusters import PoseBusters
    except ImportError as e:
        raise ImportError(f"posebusters is required for the pose gate -- {_INSTALL_HINT}") from e
    if not poses:
        raise ValueError("no poses to check")
    _avoid_point3d_unwind_segfault()
    mols = [_mol_for_pose(smiles, p) for p in poses]
    config = "dock" if receptor is not None else "mol"
    buster = PoseBusters(config=config)
    df = buster.bust(mols, None, receptor) if receptor is not None else buster.bust(mols)
    verdicts = []
    for _, row in df.iterrows():
        results = {k: bool(v) for k, v in row.items() if isinstance(v, (bool, np.bool_))}
        verdicts.append(
            PoseVerdict(
                tuple(k for k, v in results.items() if v),
                tuple(k for k, v in results.items() if not v),
            )
        )
    if len(verdicts) != len(poses):
        raise RuntimeError(f"PoseBusters returned {len(verdicts)} verdicts for {len(poses)} poses")
    return PoseReport(tuple(verdicts), config, getattr(posebusters, "__version__", "unknown"), receptor)


def check_candidate_poses(cand: Candidate, receptor: str | None = None) -> PoseReport:
    """Run the gate on `cand.poses` and attach the report to the candidate."""
    report = posebusters_check(cand.poses, cand.smiles, receptor)
    cand.pose_report = report
    return report


def require_passing_poses(cand: Candidate, ctx: dict | None = None) -> None:
    """Quantum tiers call this first: raise rather than score a pose that failed its gate.

    A candidate with no report is allowed through unless `ctx["require_pose_report"]`
    is true; a candidate with a report must have every pose pass.
    """
    report = cand.pose_report
    if report is None:
        if ctx and ctx.get("require_pose_report"):
            raise PoseGateError(f"{cand.name}: no pose report, and ctx['require_pose_report'] is set")
        return
    if len(report.verdicts) != len(cand.poses):
        raise PoseGateError(
            f"{cand.name}: pose report covers {len(report.verdicts)} poses, candidate has {len(cand.poses)}"
        )
    bad = report.failures()
    if bad or not report.all_passed:
        detail = "; ".join(f"pose {i}: {', '.join(c) or 'no checks ran'}" for i, c in bad.items())
        raise PoseGateError(f"{cand.name}: refusing to run a quantum tier on poses that failed PoseBusters ({detail})")
