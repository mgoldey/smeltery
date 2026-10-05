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
    from .tiers import Tier


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
    p = parent.per_pose[quantity]
    a = analogue.per_pose[quantity]
    if len(p) != len(a):
        raise ValueError(f"{analogue.name}: {len(a)} poses vs parent's {len(p)}; cannot pair")
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
    measurements: dict[str, Measurement], keep: int, z: float = 2.0, floor: float = 0.0,
    *, tier: Tier | None = None, quantity: str | None = None,
) -> CutResult:
    """Cut to `keep` survivors. With `tier` and `quantity`, the floor is the tier's
    own systematic floor and an unmeasured one raises `UnmeasuredFloorError`;
    an explicit `floor=` is then not accepted. The cut's unit is the tier's
    declared unit for `quantity`, and every measurement must carry it."""
    if tier is not None:
        if quantity is None or floor != 0.0:
            raise ValueError("with tier=, pass quantity= and no explicit floor=")
        floor = tier_floor(tier, quantity)
        unit = tier.produces()[quantity]
        wrong = sorted(k for k, v in measurements.items() if v.unit != unit)
        if wrong:
            raise ValueError(f"{wrong} are not in the tier's declared unit {unit!r} for {quantity!r}")
    order = sorted(measurements, key=lambda k: measurements[k].mean)
    groups: list[list[str]] = []
    for name in order:
        if groups and not resolved(measurements[groups[-1][-1]], measurements[name], z, floor):
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
                groups, None, True, z, floor,
                [f"keep={keep} falls inside the unresolved group {g}; refusing to cut it"],
            )
    return CutResult(groups, kept, False, z, floor)
