"""Gates: measurements of how far a ranking can be trusted, not fixes for it.

`charge_sensitivity` scores the same candidates, on the same geometries, under
two independent charge models. A ranking that does not survive the swap is not
a ranking. The gate MEASURES that instability and derives a floor for `cut`;
it cannot correct a systematic error both models share.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np

from .model import Candidate

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
