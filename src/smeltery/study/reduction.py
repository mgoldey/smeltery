"""A `Reduction`: the node that consumes a step's output ACROSS conditions.

A `Step` maps over conditions independently. The analyses that actually answer a study's question do
not: a per-cell paired difference needs every cell of both arms at once, a mixed model needs every
molecule with its cell label. That is a different shape, and it is the shape with no abstraction --
which is exactly where every error in this project happened:

* pooling two arms' molecules moved a measured difference from -1.21 to -3.24 kcal/mol;
* an ABSOLUTE floor was used on a difference, too conservative by ~sqrt(n);
* "PAINS 6 vs 0" was quoted as a finding, a rate with no denominator and no interval;
* a per-molecule spread was offered as the floor on a difference of aggregates.

Every one is a mis-stated *shape*: what varied, what was held fixed, and over how many things. So a
Reduction declares that shape as data, and the declaration is the safety property:

    Reduction(name="per_cell_difference", consumes="props",
              over=("arm",),      # the factor that VARIES -- the comparison
              within=("cell",))   # the factors held FIXED -- the pairing

From `over`/`within` alone:

* **Pooling is `within=()`** -- visible, refusable, and impossible to do by accident. `strict=True`
  refuses it outright, because pooling across conditions that differ in anything compares mixtures.
* **The floor kind is DERIVED, not chosen.** A reduction with a non-empty `over` produces a
  difference, so `floor_kind` is DIFFERENCE and `Floor.check` refuses an absolute one. Nothing is
  left to a caller's memory.
* **n travels with the estimate.** `Estimate` carries `n` and an interval, so a rate cannot be
  quoted without its denominator.
* `key()` gives a content address -- the digest of the declaration plus the keys of every step
  output consumed -- so a CALLER can cache on it the way `Study.run` caches steps. `run()` does not
  cache on its own; reducing is cheap next to the steps it consumes.
"""

from __future__ import annotations

import math
import statistics as st
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..floor import ABSOLUTE, DIFFERENCE, Floor
from ..record import digest
from .condition import Condition
from .step import StepResult


class ReductionError(ValueError):
    """A reduction that cannot run as declared, or whose declaration contradicts its data."""


#: Below this many measurements an arm's summary is a draw, not a summary, and no difference
#: involving it is resolvable. `smeltery.gui.compare` takes the same position for the same reason.
MIN_PER_ARM = 3


@dataclass(frozen=True)
class Group:
    """One cell of the reduction: the conditions held fixed, and the arms within it.

    `arms` maps each level of the `over` factors to the step values at that level. A group with one
    arm is UNPAIRED -- reported, never dropped, because an arm producing nothing for a group is a
    result about that arm.

    The values are a LIST because several conditions can land in one bucket: with `within=("cell",)`
    and a `seed` factor also present, one cell holds one value per seed. `one()` is the common case
    -- exactly one condition per arm -- and raises rather than silently taking the first, because
    quietly discarding replicates is how a study loses its error bars.
    """

    within: Condition
    arms: dict[Condition, list[Any]]

    @property
    def unpaired(self) -> bool:
        return len(self.arms) < 2

    @property
    def n_values(self) -> int:
        """How many STEP VALUES this group holds across all arms -- typically one per arm.

        Not a denominator: a cell holding 30 molecules in one arm and 1 in the other has
        `n_values == 2`. The counts that matter are per arm, and `paired_difference` reports the
        smallest as `Estimate.n_min_per_group`.
        """
        return sum(len(v) for v in self.arms.values())

    def one(self, arm: Condition) -> Any:
        """The single value for `arm`, or an error naming how many there were."""
        vals = self.arms[arm]
        if len(vals) != 1:
            raise ReductionError(
                f"group {self.within.label()} arm {arm.label()} has {len(vals)} values, not 1: a factor "
                f"varies that is neither in `over` nor `within`, so these are replicates. Add it to "
                f"`within` to keep them apart, or reduce them before this step."
            )
        return vals[0]

    def arm_order(self) -> list[Condition]:
        """Arms in a deterministic order, so `a - b` means the same thing on every run."""
        return sorted(self.arms, key=lambda c: c.label())


@dataclass(frozen=True)
class Estimate:
    """A number with everything needed to read it: n, an interval, the method, and what it rests on.

    The typed fields are each a mistake made in this project. `n` is the unit count a rate was quoted
    without. `method` names the estimator, because the choice of estimator IS the result (a median of
    per-group differences and a mean of them differ here by enough to change the sign conclusion).
    `n_min_per_group` is the smallest arm behind the estimate: the published -1.211 was the median of
    11 cells, and that median landed on the one cell whose second arm was a SINGLE molecule, which
    `n=11` concealed. `resolved` is a tri-state -- None means no floor was given, which is a
    different answer from "not resolved".
    """

    value: float | None
    n: int
    lo: float | None = None
    hi: float | None = None
    unit: str = ""
    label: str = ""
    #: How the interval was made: "order statistic", "wilson", "none". Not decoration: a reader
    #: cannot otherwise tell a Wilson interval from a bootstrap from a range.
    method: str = "none"
    coverage: float | None = None
    n_unpaired: int = 0
    n_min_per_group: int | None = None
    spread: tuple[float, float] | None = None
    favouring_first: int | None = None
    arms: tuple[str, ...] = ()
    floor: Floor | None = None
    resolved: bool | None = None
    underpowered: bool = False
    detail: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.lo is None) != (self.hi is None):
            raise ReductionError(
                "an interval needs both ends; a one-sided bound must be stated as one, not left half-set"
            )

    @property
    def has_interval(self) -> bool:
        return self.lo is not None and self.hi is not None

    def excludes(self, x: float = 0.0) -> bool | None:
        """Does the interval exclude `x`? None when there is no interval to ask."""
        if not self.has_interval:
            return None
        return not (self.lo <= x <= self.hi)

    def __str__(self) -> str:
        head = self.label or "estimate"
        if self.value is None:
            s = f"{head}: none (n={self.n})"
            return s + (f", {self.n_unpaired} unpaired" if self.n_unpaired else "")
        s = f"{head} {self.value:+.3f}"
        if self.unit:
            s += f" {self.unit}"
        if self.has_interval:
            cov = f" {100 * self.coverage:.1f}%" if self.coverage else " 95%"
            s += f" [{self.lo:+.3f}, {self.hi:+.3f}]{cov} CI ({self.method})"
        s += f" (n={self.n}"
        if self.n_min_per_group is not None:
            s += f", smallest arm {self.n_min_per_group}"
        if self.n_unpaired:
            s += f", {self.n_unpaired} unpaired"
        s += ")"
        if self.resolved is False:
            s += " -- NOT RESOLVED"
        elif self.resolved is None and self.floor is None:
            s += " -- no floor given, resolution unknown"
        return s


@dataclass
class Reduction:
    """Consume one step's output across conditions, grouped by `within`, compared across `over`.

    `fn(groups, ctx) -> Estimate | dict` does the statistics. It receives the grouping already done,
    so it cannot accidentally pool: the shape was decided by the declaration, not inside the
    function.
    """

    name: str
    version: str
    consumes: str
    fn: Callable[[list[Group], dict], Any]
    over: tuple[str, ...] = ()
    within: tuple[str, ...] = ()
    #: Refuse a declaration that would pool over a factor taking several levels.
    strict: bool = True
    #: Factors deliberately marginalised. A positive declaration naming WHICH, rather than one
    #: boolean that disables the check for every factor at once; it enters the cache key, so a
    #: pooled result and an unpooled one cannot collide.
    pool_over: tuple[str, ...] = ()
    unit: str = ""

    def __post_init__(self) -> None:
        overlap = sorted(set(self.over) & set(self.within))
        if overlap:
            raise ReductionError(
                f"reduction {self.name!r}: {overlap} is both varied (`over`) and held fixed (`within`); "
                "a factor cannot be both"
            )
        clash = sorted(set(self.pool_over) & (set(self.over) | set(self.within)))
        if clash:
            raise ReductionError(
                f"reduction {self.name!r}: {clash} is both pooled over and used as a factor; a factor cannot be both"
            )

    @property
    def floor_kind(self) -> str:
        """DERIVED from the declaration: comparing across `over` yields a difference.

        This is the connection that was missing when a -4.7 kcal/mol ABSOLUTE prep floor was applied
        to a between-arm difference. The reduction's shape decides which floor is admissible, so the
        choice is not left to whoever reads the docstring.
        """
        return DIFFERENCE if self.over else ABSOLUTE

    def check_floor(self, floor: Floor | None) -> Floor | None:
        """Refuse a floor measured for the other shape."""
        return None if floor is None else floor.check(self.floor_kind)

    def group(self, results: list[StepResult]) -> list[Group]:
        """Bucket step values by `within`, then by `over`. Refuses a mis-declared factor."""
        vals = [r for r in results if r.step == self.consumes]
        if not vals:
            steps = sorted({r.step for r in results})
            raise ReductionError(f"reduction {self.name!r} consumes {self.consumes!r}, not produced; have {steps}")
        needed = set(self.over) | set(self.within)
        for r in vals:
            missing = sorted(needed - set(r.condition.fields))
            if missing:
                raise ReductionError(
                    f"reduction {self.name!r} needs factor(s) {missing}, which condition "
                    f"{r.condition.label()} does not fix"
                )
        if self.strict:
            # CARDINALITY, not presence: a factor that is fixed across the data pools nothing, so a
            # single-condition summary must be allowed. And this applies with a non-empty `within`
            # too -- a factor in neither `over` nor `within` that TAKES SEVERAL LEVELS is the real
            # trap, and scoping the check to `within=()` let it through to fail later, or not at all
            # for an estimator that never calls `one()`.
            accounted = set(self.over) | set(self.within) | set(self.pool_over)
            levels: dict[str, set[str]] = {}
            for r in vals:
                for f in set(r.condition.fields) - accounted:
                    levels.setdefault(f, set()).add(r.condition[f])
            pooled = sorted(f for f, lv in levels.items() if len(lv) > 1)
            if pooled:
                raise ReductionError(
                    f"reduction {self.name!r} would pool over {pooled}, which take several levels and "
                    f"are in neither `over` nor `within`: that compares mixtures rather than arms. "
                    f"Add them to `within` to keep them apart, to `over` to compare across them, or "
                    f"to `pool_over` to say the pooling is intended."
                )
        buckets: dict[Condition, dict[Condition, list[Any]]] = {}
        for r in vals:
            w = r.condition.project(self.within)
            o = r.condition.project(self.over)
            buckets.setdefault(w, {}).setdefault(o, []).append(r.value)
        return [Group(w, arms) for w, arms in sorted(buckets.items(), key=lambda kv: kv[0].label())]

    def key(self, results: list[StepResult]) -> str:
        """Content address: the reduction plus the keys of every step output it consumed."""
        consumed = sorted(r.key for r in results if r.step == self.consumes)
        return digest(
            {
                "reduction": self.name,
                "version": self.version,
                "consumes": self.consumes,
                "over": list(self.over),
                "within": list(self.within),
                "pool_over": list(self.pool_over),
                "inputs": consumed,
            }
        )

    def run(self, results: list[StepResult], ctx: dict | None = None) -> Any:
        """Group, then reduce. The grouping is done before `fn` sees anything.

        `ctx` carries the declaration to the estimator -- `floor_kind`, `unit`, `over`, `within` --
        so an estimator reads the shape rather than re-deriving it. Without that the derived
        `floor_kind` would be decorative: `check_floor` would have no caller and an estimator would
        hardcode the kind it assumed.
        """
        shape = {
            "over": self.over,
            "within": self.within,
            "pool_over": self.pool_over,
            "floor_kind": self.floor_kind,
            "unit": self.unit,
        }
        return self.fn(self.group(results), {**dict(ctx or {}), "shape": shape})


def median_ci(values: list[float], level: float = 0.95) -> tuple[float, float, float] | None:
    """Distribution-free CI for a median, from order statistics. Returns (lo, hi, actual coverage).

    The exact partner of a median estimator: coverage is binomial, `1 - 2*P(X <= k-1)` for
    `X ~ Bin(n, 0.5)`, so the attainable levels are discrete and the ACTUAL one is returned rather
    than the one asked for. At the n a study of cells produces (11, say) a percentile bootstrap of a
    median degenerates to exactly this, because resampling can only land the median on an order
    statistic -- so this is computed directly and named honestly instead of dressed as a bootstrap.
    """
    n = len(values)
    if n < 2:
        return None
    s = sorted(values)
    # Attainable coverages are discrete and DECREASE as k grows (k counts inward from each end), so
    # this takes the narrowest interval that still reaches `level` -- never one that undercovers it.
    # At n=11 and level=0.95 that is k=2, giving 98.8%; k=3 would be narrower at 93.5% but would
    # report a 95% interval that is not one. The actual coverage is returned so a reader sees which.
    best = None
    for k in range(1, n // 2 + 1):
        cov = 1.0 - 2.0 * sum(math.comb(n, i) for i in range(k)) / 2**n
        if cov >= level - 1e-12:
            best = (s[k - 1], s[n - k], cov)
        else:
            break
    if best is not None:
        return best
    # No attainable level reaches `level` (small n): the widest interval, with its true coverage.
    return (s[0], s[-1], 1.0 - 2.0 / 2**n)


def paired_difference(
    groups: list[Group],
    value_of: Callable[[Any], float | None],
    *,
    floor: Floor | None = None,
    unit: str = "",
    min_per_arm: int = MIN_PER_ARM,
    level: float = 0.95,
) -> Estimate:
    """The per-group difference of two arms, and the median of those differences, with its interval.

    A difference taken WITHIN each group, so the group's own level cancels, then combined across
    groups. That part is right and is what avoids the pooling confound.

    What the estimate must carry, each from a specific error:

    * an **interval**, from `median_ci`. Returning a bare point estimate and a `resolved` flag is how
      a comparison whose honest interval includes zero gets quoted as a finding;
    * **`n_min_per_group`**, the smallest arm behind it. The published -1.211 was the median of 11
      cells and landed on the one cell whose second arm was a SINGLE molecule; `n=11` hid that;
    * **both halves of `funnel.resolved`** -- the interval must exclude zero AND the estimate must
      clear the floor. An earlier version compared the point estimate to the floor alone, which is
      the defect `gui.compare` records as fixed and this function reintroduced.

    Unpaired groups (an arm produced nothing) and undefined ones (both arms produced, the summary was
    None) are counted separately: they are different results about the arm.
    """
    present = {a for g in groups for a in g.arms}
    if len(present) > 2:
        raise ReductionError(
            f"paired_difference got {len(present)} arms ({sorted(a.label() for a in present)}): a "
            f"difference is between two. Taking the first two and dropping the rest would be the "
            f"mis-stated shape this module exists to refuse. Restrict `over` to two levels."
        )

    diffs: list[float] = []
    smallest: list[int] = []
    unpaired = undefined = 0
    for g in groups:
        if g.unpaired:
            unpaired += 1
            continue
        arms = g.arm_order()
        # `one()` rather than the raw list: a group holding replicates is a mis-declared shape, and
        # silently reducing them here would hide it.
        va, vb = g.one(arms[0]), g.one(arms[1])
        a, b = value_of(va), value_of(vb)
        if a is None or b is None:
            undefined += 1
            continue
        diffs.append(a - b)
        smallest.append(min(_count(va), _count(vb)))
    arm_names = tuple(sorted(a.label() for a in present))
    if not diffs:
        return Estimate(
            None,
            0,
            label="paired difference",
            unit=unit,
            arms=arm_names,
            n_unpaired=unpaired,
            detail={"undefined_groups": undefined, "reason": "no group has both arms"},
        )

    med = st.median(diffs)
    n_min = min(smallest) if smallest else None
    ci = median_ci(diffs, level)
    underpowered = n_min is not None and n_min < min_per_arm
    resolved: bool | None = None
    if floor is not None:
        floor.check(DIFFERENCE)
        excludes_zero = ci is not None and not (ci[0] <= 0.0 <= ci[1])
        resolved = bool(excludes_zero and abs(med) > floor.value and not underpowered)
    return Estimate(
        med,
        len(diffs),
        lo=ci[0] if ci else None,
        hi=ci[1] if ci else None,
        coverage=ci[2] if ci else None,
        unit=unit,
        label="paired difference",
        method="median of per-group differences, order-statistic CI",
        n_unpaired=unpaired,
        n_min_per_group=n_min,
        spread=(min(diffs), max(diffs)),
        favouring_first=sum(1 for d in diffs if d < 0),
        arms=arm_names,
        floor=floor,
        resolved=resolved,
        underpowered=underpowered,
        detail={"undefined_groups": undefined, "differences": diffs},
    )


def _count(step_value: Any) -> int:
    """How many measurements a step value holds, for reporting the smallest arm behind a median."""
    try:
        return len(step_value)
    except TypeError:
        return 1


def _wilson(hits: int, total: int) -> tuple[float, float]:
    """Wilson score interval, in percent. Not degenerate at 0 or n, unlike Wald."""
    p, z = hits / total, 1.96
    d = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / d
    half = z * ((p * (1 - p) / total + z**2 / (4 * total**2)) ** 0.5) / d
    return 100 * max(0.0, centre - half), 100 * min(1.0, centre + half)


def rate_per_arm(
    groups: list[Group], hit_of: Callable[[Any], tuple[int, int]], *, unit: str = "%"
) -> dict[Condition, Estimate]:
    """One Wilson-interval rate PER ARM, because a rate across arms is the mixture, not a comparison.

    `hit_of(value) -> (hits, n)` for one step value. "PAINS 6 vs 0" was 6/324 against 0/59, whose
    intervals overlap almost entirely: at n=59 a 2% rate is undetectable, and zero events is not
    evidence of a lower rate. The point is that each arm keeps its own n and interval -- an earlier
    version of this function summed over arms and returned ONE number for both, which is exactly the
    pooled mixture this module exists to refuse, and the test was written so it never showed.
    """
    acc: dict[Condition, list[int]] = {}
    for g in groups:
        for arm in g.arm_order():
            for value in g.arms[arm]:
                h, n = hit_of(value)
                tally = acc.setdefault(arm, [0, 0])
                tally[0] += h
                tally[1] += n
    out: dict[Condition, Estimate] = {}
    for arm, (hits, total) in sorted(acc.items(), key=lambda kv: kv[0].label()):
        if total == 0:
            out[arm] = Estimate(None, 0, label=f"rate {arm.label()}", unit=unit, method="none")
            continue
        lo, hi = _wilson(hits, total)
        out[arm] = Estimate(
            100 * hits / total,
            total,
            lo=lo,
            hi=hi,
            unit=unit,
            label=f"rate {arm.label()}",
            method="wilson",
            detail={"hits": hits},
        )
    return out


def rate(groups: list[Group], hit_of: Callable[[Any], tuple[int, int]], *, unit: str = "%") -> Estimate:
    """One pooled rate over every arm present. Refuses a multi-arm grouping.

    Pooling arms into a single proportion compares a mixture, so this is allowed only where there is
    nothing to mix: one arm. For several arms use `rate_per_arm`, which keeps each arm's own n and
    interval.
    """
    arms = {a for g in groups for a in g.arms}
    if len(arms) > 1:
        raise ReductionError(
            f"rate() got {len(arms)} arms ({sorted(a.label() for a in arms)}): pooling them gives the "
            f"mixture, not a comparison. Use rate_per_arm() for one interval per arm."
        )
    per = rate_per_arm(groups, hit_of, unit=unit)
    if not per:
        return Estimate(None, 0, label="rate", unit=unit, method="none")
    return next(iter(per.values()))
