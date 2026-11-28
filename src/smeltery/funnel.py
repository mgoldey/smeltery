"""Turn per-pose results into rankable measurements, and cut honestly.

The unit is a PAIRED difference against the parent, ΔΔE_i = q(analogue_i) −
q(parent_i) over paired poses, never a total energy (ferric #323: ranking
analogues by total energy is meaningless across formulas). The cut never
orders two candidates their uncertainties can't separate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from .model import Candidate, Measurement

if TYPE_CHECKING:  # keep the funnel importable without RDKit
    from .gates import SensitivityReport
    from .tiers import Tier


#: Fewest paired poses a measurement needs to be ranked after a failed SCF dropped poses (`cut(min_pairs=)`). Two pairs
#: give a standard error from one degree of freedom, which no z-bar can honestly use; three is the smallest count with a
#: spread worth quoting. Declared, not measured: `[cut] min_pairs` overrides it.
DEFAULT_MIN_PAIRS = 3


class IncomparableError(ValueError):
    """Raised when asked to rank quantities that are not comparable."""


def require_same_formula(candidates: list[Candidate], quantity: str) -> None:
    """Refuse to rank a per-molecule TOTAL quantity across differing formulas.

    Total energies scale with electron count. Ranking two molecules of
    different formula by them orders them by size, not by anything physical.
    Use a difference against a common reference (`paired_delta`) instead.
    """
    formulas = {c.formula for c in candidates}
    if len(formulas) > 1:
        raise IncomparableError(
            f"refusing to rank {quantity!r} across differing formulas {sorted(formulas)}; "
            "a total quantity is only comparable between isomers. Rank a paired "
            "difference against a common reference instead."
        )


class UnmeasuredFloorError(ValueError):
    """Raised when asked to cut on a quantity whose tier has not measured its systematic floor."""


def tier_floor(tier: Tier, quantity: str) -> float:
    """The tier's systematic floor for `quantity`; refuse (raise) if it is unmeasured."""
    floor = tier.systematic_floor(quantity)
    if floor is None:
        raise UnmeasuredFloorError(
            f"refusing to cut on {quantity!r}: tier {tier.name!r} has not measured its "
            "systematic floor (systematic_floor() is None). Measure it, or pass an "
            "explicit floor= to cut() without tier=."
        )
    return floor


def paired_delta(parent: Candidate, analogue: Candidate, quantity: str, tier: Tier | None = None) -> Measurement:
    """ΔΔ over paired poses: analogue pose i minus parent pose i.

    With `tier`, the unit is read from `tier.produces()[quantity]`; without it
    the Measurement keeps its default unit.
    """
    if tier is not None and getattr(tier, "is_delta_g", True) is False:
        raise IncomparableError(
            f"refusing to difference {quantity!r} from tier {tier.name!r}: it is not a free "
            "energy (is_delta_g=False), e.g. a rank-only score. Its ordering may be informative; "
            "its differences are not. Rank by it, do not subtract it."
        )
    p = parent.per_pose[quantity]
    a = analogue.per_pose[quantity]
    if len(p) != len(a):
        raise ValueError(f"{analogue.name}: {len(a)} poses vs parent's {len(p)}; cannot pair")
    bad = [i for i, (x, y) in enumerate(zip(a, p, strict=True)) if not (np.isfinite(x) and np.isfinite(y))]
    if bad:  # a failed pose carries NaN; differencing it would spread the NaN into the mean, or hide it
        raise ValueError(
            f"{analogue.name}: {quantity!r} is not finite at pose(s) {bad} (a failed SCF?); drop those poses pairwise "
            "(planning.run_plan does, and records them) rather than difference them"
        )
    samples = np.asarray(a) - np.asarray(p)
    if tier is None:
        return Measurement.from_samples(samples)
    return Measurement.from_samples(samples, tier.produces()[quantity])


def unpaired_delta(parent: Candidate, analogue: Candidate, quantity: str) -> Measurement:
    """The same mean difference without pairing, for comparison.

    Its SEM combines both ensembles' spreads independently. The paired SEM
    is smaller to the extent that pose effects shared by parent and analogue
    cancel, which is the reason the funnel pairs.
    """
    p = np.asarray(parent.per_pose[quantity])
    a = np.asarray(analogue.per_pose[quantity])
    sem = float(np.sqrt(a.var(ddof=1) / a.size + p.var(ddof=1) / p.size))
    return Measurement(float(a.mean() - p.mean()), sem, int(min(a.size, p.size)), ())


@dataclass
class CutResult:
    """Outcome of one stage's cut, lower value is better.

    `groups` are runs of candidates whose neighbours are not resolved from
    each other; within a group there is NO order. `survivors` is set only
    when the `keep` boundary falls cleanly between two groups. Otherwise
    `unranked_at_boundary` is True and nothing is dropped, so the stage
    reports that it couldn't decide rather than deciding arbitrarily.
    """

    groups: list[list[str]]
    survivors: list[str] | None
    unranked_at_boundary: bool
    z: float
    floor: float
    notes: list[str] = field(default_factory=list)
    #: Candidates left out of the ranking because too few paired poses survived (`cut(min_pairs=...)`): name -> why.
    #: While any exist the stage claims no survivors: a candidate that cannot be measured cannot be called worse.
    unrankable: dict[str, str] = field(default_factory=dict)


def resolved(a: Measurement, b: Measurement, z: float, floor: float) -> bool:
    """Is the difference between two measurements resolved?

    Resolved means |Δmean| > z·sqrt(sem_a² + sem_b²) AND |Δmean| > floor. The
    floor is a systematic error the SEM can't see, e.g. the charge-model
    sensitivity. A measurement without an SEM (n < 2) never resolves.
    """
    if not (np.isfinite(a.sem) and np.isfinite(b.sem)):
        return False
    d = abs(a.mean - b.mean)
    return d > z * np.hypot(a.sem, b.sem) and d > floor


def cut(
    measurements: dict[str, Measurement],
    keep: int,
    z: float = 2.0,
    floor: float = 0.0,
    *,
    tier: Tier | None = None,
    quantity: str | None = None,
    sensitivity: SensitivityReport | None = None,
    min_pairs: int | None = None,
) -> CutResult:
    """Cut to `keep` survivors. With `tier` and `quantity`, the floor is the tier's
    own systematic floor and an unmeasured one raises `UnmeasuredFloorError`;
    an explicit `floor=` is then not accepted. The cut's unit is the tier's
    declared unit for `quantity`, and every measurement must carry it.

    `sensitivity` (from `gates.charge_sensitivity`) raises the floor to
    max(base floor, sensitivity.floor), where the base floor is the tier's
    systematic floor (with tier=) or `floor=`. We take the MAX, not a
    quadrature sum: the tier's floor may already include charge-model error, so
    adding the two in quadrature could double-count, and max never lets the cut
    resolve a pair that either source says is unresolved. With tier=, the
    report must be for the same `quantity`.

    `min_pairs`: a measurement from fewer paired poses than this (a partial result after failed SCFs) is not
    ranked. It is listed in `CutResult.unrankable` with its n, and the cut then returns no survivors and
    `unranked_at_boundary=True`: the stage cannot say a candidate it could not measure is not among the best. None
    (the default) ranks every measurement, as before."""
    unrankable: dict[str, str] = {}
    if min_pairs is not None:
        unrankable = {
            k: f"{v.n} paired pose(s) survived, fewer than the {min_pairs} required to rank"
            for k, v in measurements.items()
            if v.n < min_pairs
        }
        measurements = {k: v for k, v in measurements.items() if k not in unrankable}
    if tier is not None:
        if quantity is None or floor != 0.0:
            raise ValueError("with tier=, pass quantity= and no explicit floor=")
        floor = tier_floor(tier, quantity)
        unit = tier.produces()[quantity]
        wrong = sorted(k for k, v in measurements.items() if v.unit != unit)
        if wrong:
            raise ValueError(f"{wrong} are not in the tier's declared unit {unit!r} for {quantity!r}")
    if sensitivity is not None:
        if tier is not None and sensitivity.quantity != quantity:
            raise ValueError(f"sensitivity report is for {sensitivity.quantity!r}, not {quantity!r}")
        floor = max(floor, sensitivity.floor)
    if unrankable:
        done = cut(measurements, keep, z, floor, min_pairs=None) if measurements else None
        groups = done.groups if done is not None else []
        return CutResult(
            groups,
            None,
            True,
            z,
            floor,
            [f"{k}: {why}; no survivors are claimed" for k, why in unrankable.items()] + (done.notes if done else []),
            unrankable,
        )
    order = sorted(measurements, key=lambda k: measurements[k].mean)
    groups: list[list[str]] = []
    for name in order:
        # Against EVERY member, not just the last one. Non-significance does not chain: with means
        # 0.00/0.25/0.50/0.75 at sem 0.10 each adjacent pair is unresolved while the ends are resolved,
        # and comparing neighbours alone swept all four into one group and called the cut UNRANKED --
        # discarding a distinction the statistics do support. A group now means what CutResult says it
        # means: no member is resolved from any other, so there is genuinely no order inside it.
        if groups and not any(resolved(measurements[other], measurements[name], z, floor) for other in groups[-1]):
            groups[-1].append(name)
        else:
            groups.append([name])
    kept: list[str] = []
    for g in groups:
        if len(kept) + len(g) <= keep:
            kept += g
        else:
            if len(kept) == keep:
                return CutResult(groups, kept, False, z, floor)
            return CutResult(
                groups,
                None,
                True,
                z,
                floor,
                [f"keep={keep} falls inside the unresolved group {g}; refusing to cut it"],
            )
    return CutResult(groups, kept, False, z, floor)
