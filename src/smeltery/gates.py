"""Gates: measurements of how far a ranking can be trusted, not fixes for it.

`charge_sensitivity` scores the same candidates, on the same geometries, under
two independent charge models. A ranking that does not survive the swap is not
a ranking. The gate MEASURES that instability and derives a floor for `cut`;
it cannot correct a systematic error both models share.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from .model import Candidate, Pose

if TYPE_CHECKING:
    from rdkit import Chem

# A model maps (candidate, quantity) to that candidate's scalar value, e.g. the
# paired ΔΔE of the candidate re-scored with one set of point charges.
ChargeModel = Callable[[Candidate, str], float]


def average_ranks(x: np.ndarray) -> np.ndarray:
    """1-based ranks, ties given their average rank."""
    order = np.argsort(x, kind="stable")
    ranks = np.empty(len(x))
    ranks[order] = np.arange(1, len(x) + 1)
    for v in np.unique(x):
        tied = x == v
        if tied.sum() > 1:
            ranks[tied] = ranks[tied].mean()
    return ranks


_ranks = average_ranks  # the older private name


def spearman(a: Sequence[float], b: Sequence[float]) -> float:
    """Spearman rank correlation (Pearson on average ranks); nan if either side is constant."""
    ra, rb = average_ranks(np.asarray(a, dtype=float)), average_ranks(np.asarray(b, dtype=float))
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

    @property
    def common_mode(self) -> float:
        """The mean shift: the part that CANCELS in any difference taken under one model.

        Not a floor on anything. Subtracting it from individual values would be a correction this
        measurement does not license, because the shift is candidate-dependent -- which is exactly
        why `floor` is not zero.
        """
        return float(np.mean(list(self.deltas.values())))

    @property
    def shift_sd(self) -> float:
        """Standard deviation of the per-candidate shifts: the part that does NOT cancel.

        `floor` (the range) bounds how much swapping models can change the gap between any two
        candidates, and is the right floor for RANKING them. This is the input to a floor on a
        difference of AGGREGATES -- see `aggregate_difference_floor`, which is smaller again by
        roughly sqrt(n). nan when fewer than two candidates.
        """
        d = list(self.deltas.values())
        return float(np.std(d, ddof=1)) if len(d) > 1 else float("nan")

    def aggregate_difference_floor(self, n_a: int, n_b: int) -> float:
        """The floor on a difference of MEANS over `n_a` and `n_b` candidates: `shift_sd * sqrt(1/n_a + 1/n_b)`.

        Comparing two groups scored under ONE model (two generators' medians, say), the shared part
        of a model swap cancels and what remains averages down. Quoting `floor`, or even `shift_sd`,
        as the limit on such a difference is too conservative by roughly sqrt(n) and makes group
        comparisons unresolvable however good the data.

        Assumes the per-candidate shifts are independent across candidates. They are not in general
        -- candidates sharing a scaffold sit in the same part of the pocket and move together -- so
        the honest reading is a LOWER bound, with `floor` as the upper one. Say which you used.
        """
        if n_a < 1 or n_b < 1:
            raise ValueError(f"need at least one candidate per group, got n_a={n_a}, n_b={n_b}")
        return float(self.shift_sd * np.sqrt(1.0 / n_a + 1.0 / n_b))


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


class PoseGeometryError(PoseGateError):
    """A pose is not the molecule it is labelled as: wrong atoms, or a bonded atom far from its partner."""


#: Open Babel's bond-perception rule: two atoms are bonded if they are within the sum of their covalent radii plus this
#: slack (Å). A standard convention, not fitted to any data here. A real C-F bond is 1.35 Å against a 1.78 Å limit.
BOND_SLACK_ANG = 0.45
#: Two atoms closer than this (Å) are on top of each other whatever they are.
MIN_ATOM_SEPARATION_ANG = 0.4


def pose_geometry_problems(smiles: str, pose: Pose) -> list[str]:
    """Why `pose` cannot be the molecule `smiles` (with hydrogens added); empty if it can.

    Needs only RDKit and no PoseBusters report, so quantum tiers can always run it. Three checks, cheapest first:
    1. composition: the pose has exactly the elements of the SMILES with H added;
    2. connectivity: bonds perceived from the geometry (`BOND_SLACK_ANG` rule) make as many pieces as the SMILES has
       fragments, so an atom stranded far from the molecule (an isolated open-shell atom no closed-shell SCF can
       converge) is caught whatever the atom order;
    3. when the atoms are in SMILES-with-H order, every graph bond is within the same limit, which also catches a
       stretched bond whose far end happens to land near some other atom.
    It does not judge bond lengths or angles beyond that (PoseBusters does); it is a sanity floor.
    """
    from collections import Counter

    from rdkit import Chem

    try:
        mol = mol_with_h(smiles)
    except ValueError:
        return [f"unparseable SMILES {smiles!r}"]
    want = Counter(a.GetSymbol() for a in mol.GetAtoms())
    have = Counter(pose.symbols)
    if want != have:
        return [f"composition {dict(sorted(have.items()))} does not match the SMILES {dict(sorted(want.items()))}"]
    x = np.asarray(pose.coords_ang, dtype=float)
    if not np.isfinite(x).all():
        return ["non-finite coordinates"]
    table = Chem.GetPeriodicTable()
    rcov = np.array([table.GetRcovalent(s) for s in pose.symbols])
    d = np.linalg.norm(x[:, None, :] - x[None, :, :], axis=2)
    limit = rcov[:, None] + rcov[None, :] + BOND_SLACK_ANG
    n = len(pose.symbols)
    problems: list[str] = []
    close = [(i, j) for i in range(n) for j in range(i + 1, n) if d[i, j] < MIN_ATOM_SEPARATION_ANG]
    if close:
        problems.append(
            "atoms on top of each other: "
            + ", ".join(f"{pose.symbols[i]}{i}-{pose.symbols[j]}{j}" for i, j in close[:5])
        )
    adj = (d <= limit) & ~np.eye(n, dtype=bool)
    seen, pieces = np.zeros(n, dtype=bool), []
    for start in range(n):
        if seen[start]:
            continue
        stack, comp = [start], []
        seen[start] = True
        while stack:
            i = stack.pop()
            comp.append(i)
            for j in np.nonzero(adj[i] & ~seen)[0]:
                seen[j] = True
                stack.append(int(j))
        pieces.append(comp)
    want_pieces = len(Chem.GetMolFrags(mol))
    if len(pieces) != want_pieces:
        small = sorted(pieces, key=len)[: len(pieces) - want_pieces]
        problems.append(
            f"{len(pieces)} connected pieces where the SMILES has {want_pieces}; stranded: "
            + "; ".join(",".join(f"{pose.symbols[i]}{i}" for i in c[:4]) for c in small[:3])
        )
    if tuple(a.GetSymbol() for a in mol.GetAtoms()) == tuple(pose.symbols):
        broken = [
            (b.GetBeginAtomIdx(), b.GetEndAtomIdx())
            for b in mol.GetBonds()
            if d[b.GetBeginAtomIdx(), b.GetEndAtomIdx()] > limit[b.GetBeginAtomIdx(), b.GetEndAtomIdx()]
        ]
        if broken:
            worst = sorted(broken, key=lambda ij: -d[ij])[:3]
            problems.append(
                "bonded atoms too far apart: "
                + ", ".join(f"{pose.symbols[i]}{i}-{pose.symbols[j]}{j} {d[i, j]:.2f} A" for i, j in worst)
                + f" ({len(broken)} bond(s))"
            )
    return problems


def require_sound_geometry(cand: Candidate) -> None:
    """Raise `PoseGeometryError` if any pose of `cand` cannot be the molecule it is labelled as."""
    if not cand.smiles:
        return
    bad = {i: pr for i, p in enumerate(cand.poses) if (pr := pose_geometry_problems(cand.smiles, p))}
    if bad:
        detail = "; ".join(f"pose {i}: {' / '.join(pr)}" for i, pr in list(bad.items())[:4])
        raise PoseGeometryError(
            f"{cand.name}: {len(bad)} of {len(cand.poses)} pose(s) are not this molecule ({detail}). "
            "For poses built by PairedPoses this is the unrelaxed substituent junction: use relax_unmapped=True."
        )


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


class AtomOrderError(ValueError):
    """A pose whose elements are not its SMILES-with-H atom order (`mol_for_poses`). Callers re-raise it in their own
    words; `index` is the pose's position in the list given, `pose_symbols` and `smiles_symbols` the two orders."""

    def __init__(self, index: int, pose_symbols: tuple[str, ...], smiles_symbols: tuple[str, ...]) -> None:
        super().__init__(f"pose {index} atoms {pose_symbols} are not the SMILES-with-H order {smiles_symbols}")
        self.index, self.pose_symbols, self.smiles_symbols = index, pose_symbols, smiles_symbols


def mol_with_h(smiles: str) -> Chem.Mol:
    """`Chem.AddHs(Chem.MolFromSmiles(smiles))`: the atom order every pose in this package is in. An unparseable
    SMILES is a ValueError."""
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        raise ValueError(f"unparseable SMILES: {smiles!r}")
    return Chem.AddHs(mol)


def add_pose_conformer(mol: Chem.Mol, coords_ang, *, set_3d: bool = False) -> None:
    """Add one conformer to `mol` holding `coords_ang` (Angstrom, row k = atom k), with a fresh id."""
    from rdkit import Chem
    from rdkit.Geometry import Point3D

    conf = Chem.Conformer(mol.GetNumAtoms())
    for i, (x, y, z) in enumerate(coords_ang):
        conf.SetAtomPosition(i, Point3D(float(x), float(y), float(z)))
    if set_3d:
        conf.Set3D(True)
    mol.AddConformer(conf, assignId=True)


def mol_for_poses(smiles: str, poses: Sequence[Pose], *, set_3d: bool = False) -> Chem.Mol:
    """RDKit mol with the SMILES' bond orders and one conformer per pose (conformer i = pose i).

    Topology comes from the SMILES, never from the coordinates: perceiving bonds
    from distances is what let chemically impossible docked poses through (carbons
    of degree 5-6 with `usable=True`). Every pose must be in `AddHs(smiles)` atom
    order, which is how every pose builder in this package emits them; one that is
    not is an `AtomOrderError`, raised before its coordinates touch the mol.
    """
    mol = mol_with_h(smiles)
    syms = tuple(a.GetSymbol() for a in mol.GetAtoms())
    for i, pose in enumerate(poses):
        if tuple(pose.symbols) != syms:
            raise AtomOrderError(i, tuple(pose.symbols), syms)
        add_pose_conformer(mol, pose.coords_ang, set_3d=set_3d)
    return mol


def mol_for_pose(smiles: str, pose: Pose, *, set_3d: bool = False) -> Chem.Mol:
    """`mol_for_poses` of one pose."""
    return mol_for_poses(smiles, [pose], set_3d=set_3d)


def _mol_for_pose(smiles: str, pose: Pose):
    """`mol_for_pose`, with the gate's own refusal for a mismatched atom order."""
    try:
        return mol_for_pose(smiles, pose)
    except AtomOrderError as e:
        raise ValueError(
            f"pose atoms {e.pose_symbols} are not in AddHs({smiles!r}) order {e.smiles_symbols}; "
            "refusing to assign bonds to a mismatched atom order"
        ) from None


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
        require_sound_geometry(cand)
        return
    if len(report.verdicts) != len(cand.poses):
        raise PoseGateError(
            f"{cand.name}: pose report covers {len(report.verdicts)} poses, candidate has {len(cand.poses)}"
        )
    bad = report.failures()
    if bad or not report.all_passed:
        detail = "; ".join(f"pose {i}: {', '.join(c) or 'no checks ran'}" for i, c in bad.items())
        raise PoseGateError(f"{cand.name}: refusing to run a quantum tier on poses that failed PoseBusters ({detail})")
    require_sound_geometry(cand)  # after PoseBusters, whose findings are more detailed; before the SCF either way
