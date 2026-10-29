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
* It caches on the same key scheme: the digest of the reduction plus the keys of every step output
  it consumed. A new cell re-reduces; it does not re-measure.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..floor import ABSOLUTE, DIFFERENCE, Floor
from ..record import digest
from .condition import Condition
from .step import StepResult


class ReductionError(ValueError):
    """A reduction that cannot run as declared, or whose declaration contradicts its data."""


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
    def n(self) -> int:
        """How many step values this group holds, across all arms."""
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
    """A number with the things that make it readable: its n, its interval, and its shape.

    `n` is how many units went in -- the denominator "6 vs 0" was missing. `lo`/`hi` are an interval
    or None when none was computed, and None is honest where a fabricated one is not.
    """

    value: float | None
    n: int
    lo: float | None = None
    hi: float | None = None
    unit: str = ""
    label: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def has_interval(self) -> bool:
        return self.lo is not None and self.hi is not None

    def excludes(self, x: float = 0.0) -> bool | None:
        """Does the interval exclude `x`? None when there is no interval to ask."""
        if not self.has_interval:
            return None
        return not (self.lo <= x <= self.hi)

    def __str__(self) -> str:
        if self.value is None:
            return f"{self.label or 'estimate'}: none (n={self.n})"
        s = f"{self.label or 'estimate'} {self.value:+.3f}"
        if self.unit:
            s += f" {self.unit}"
        if self.has_interval:
            s += f" [{self.lo:+.3f}, {self.hi:+.3f}]"
        return s + f" (n={self.n})"


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
    #: Refuse a declaration that pools across varying factors. Off only for a deliberate pooled
    #: summary, which is then labelled as one.
    strict: bool = True
    unit: str = ""

    def __post_init__(self) -> None:
        overlap = sorted(set(self.over) & set(self.within))
        if overlap:
            raise ReductionError(
                f"reduction {self.name!r}: {overlap} is both varied (`over`) and held fixed (`within`); "
                "a factor cannot be both"
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
        if self.strict and not self.within:
            varying = set()
            for r in vals:
                varying |= set(r.condition.fields) - set(self.over)
            if varying:
                raise ReductionError(
                    f"reduction {self.name!r} holds nothing fixed (`within=()`) while {sorted(varying)} "
                    f"vary: that pools unlike conditions and compares mixtures rather than arms. "
                    f"Declare `within`, or pass strict=False to say the pooling is intended."
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
                "inputs": consumed,
            }
        )

    def run(self, results: list[StepResult], ctx: dict | None = None) -> Any:
        """Group, then reduce. The grouping is done before `fn` sees anything."""
        return self.fn(self.group(results), dict(ctx or {}))


def paired_difference(
    groups: list[Group],
    value_of: Callable[[Any], float | None],
    *,
    floor: Floor | None = None,
    unit: str = "",
) -> Estimate:
    """The per-group difference of two arms, and the median of those differences.

    The estimator the gen-compare study needed: a difference taken WITHIN each group, so the group's
    own level cancels, then combined across groups. Unpaired groups are counted, never dropped.
    `n` is the number of PAIRED groups -- the units the estimate actually rests on, which is what
    "the median landed on the one cell with a single molecule" was hiding.
    """
    import statistics as st

    diffs, unpaired = [], 0
    for g in groups:
        if g.unpaired:
            unpaired += 1
            continue
        arms = g.arm_order()
        # `one()` rather than the raw list: a group holding replicates is a mis-declared shape, and
        # silently reducing them here would hide it.
        a, b = value_of(g.one(arms[0])), value_of(g.one(arms[1]))
        if a is None or b is None:
            unpaired += 1
            continue
        diffs.append(a - b)
    if not diffs:
        return Estimate(
            None,
            0,
            label="paired difference",
            unit=unit,
            detail={"unpaired_groups": unpaired, "reason": "no group has both arms"},
        )
    med = st.median(diffs)
    det = {
        "unpaired_groups": unpaired,
        "paired_groups": len(diffs),
        "range": [min(diffs), max(diffs)],
        "favouring_first": sum(1 for d in diffs if d < 0),
    }
    if floor is not None:
        floor.check(DIFFERENCE)
        det["floor"] = floor.value
        det["resolved"] = abs(med) > floor.value
    return Estimate(med, len(diffs), unit=unit, label="paired difference", detail=det)


def rate(groups: list[Group], hit_of: Callable[[Any], tuple[int, int]], *, unit: str = "%") -> Estimate:
    """A proportion with a Wilson interval, so a rate cannot be quoted without its denominator.

    `hit_of(values) -> (hits, n)`. "PAINS 6 vs 0" was 6/324 against 0/59, whose intervals overlap
    almost entirely -- at n=59 a 2% rate is undetectable, and zero events is not evidence of a lower
    rate. Returning an `Estimate` makes n and the interval travel with the number.
    """
    hits = total = 0
    for g in groups:
        for arm in g.arm_order():
            for value in g.arms[arm]:
                h, n = hit_of(value)
                hits += h
                total += n
    if total == 0:
        return Estimate(None, 0, label="rate", unit=unit)
    p = hits / total
    z = 1.96
    d = 1 + z**2 / total
    centre = (p + z**2 / (2 * total)) / d
    half = z * ((p * (1 - p) / total + z**2 / (4 * total**2)) ** 0.5) / d
    return Estimate(
        100 * p,
        total,
        lo=100 * max(0.0, centre - half),
        hi=100 * min(1.0, centre + half),
        unit=unit,
        label="rate",
        detail={"hits": hits},
    )
