"""Scoring providers: a pluggable rescorer at the docking tier's place in the ladder.

A `ScoringProvider` turns poses (plus a receptor) into `Score`s. Vina is one;
an ML rescorer is another, and the funnel does not change to accept it.

The type that matters is `Score.is_delta_g`. Most ML rescorers emit a RANK-ONLY
number: ordering between candidates is informative, differences are not. Such a
score must never be differenced as if it were an energy, so a `Rescoring` tier
whose provider has `is_delta_g=False` is refused by `funnel.paired_delta`
(`IncomparableError`), which is the whole point of declaring the flag.

Uncertainty: a provider that reports a per-pose uncertainty gets it into the
cut as a systematic floor (RMS of the reported values), so a noisy rescorer
yields tie groups where a sharp one ranks. A provider with none has an
unmeasured floor, and the funnel refuses to cut on it, unless the caller
supplies one explicitly.

No ML package is imported here; reference ML providers live behind extras.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .model import Candidate, Pose


@dataclass(frozen=True)
class Score:
    """One pose's score. `uncertainty` is in the same unit as `value`, or None."""

    value: float
    unit: str
    is_delta_g: bool
    uncertainty: float | None = None

    def __post_init__(self) -> None:
        if math.isnan(self.value):
            raise ValueError("NaN score: an unmeasured pose must raise, not score")
        if self.uncertainty is not None and not (self.uncertainty >= 0):
            raise ValueError(f"uncertainty must be >= 0, got {self.uncertainty}")


@runtime_checkable
class ScoringProvider(Protocol):
    name: str

    def score(self, poses: list[Pose], receptor: Any) -> list[Score]:
        """One `Score` per pose, in order. Same unit and `is_delta_g` for all."""
        ...

    def settings(self) -> dict: ...


class VinaScoreProvider:
    """Existing Vina scores, replayed as a provider. Rank-only: `is_delta_g=False`.

    Vina's score is an empirical sum fitted to binding data, not a free energy
    (see `smeltery.docking.vina_dock`), so it declares itself non-delta-G.
    `scores` must align with the poses it will be asked about.
    """

    name = "vina-score"

    def __init__(self, scores: list[float]) -> None:
        self._scores = [float(s) for s in scores]

    def score(self, poses: list[Pose], receptor: Any = None) -> list[Score]:
        if len(poses) != len(self._scores):
            raise ValueError(f"{len(poses)} poses but {len(self._scores)} recorded Vina scores")
        return [Score(s, "kcal/mol", False) for s in self._scores]

    def settings(self) -> dict:
        return {"engine": "vina", "n_scores": len(self._scores)}


class Rescoring:
    """A funnel tier that scores each candidate's existing poses with a `ScoringProvider`.

    Writes `quantity` (default `rescore`) to `Candidate.per_pose`. `ctx['receptor']`
    is passed through to the provider (may be absent).
    """

    name = "rescoring"

    def __init__(self, provider: ScoringProvider, quantity: str = "rescore", floor: float | None = None) -> None:
        self.provider = provider
        self.quantity = quantity
        self._explicit_floor = floor
        self._unit: str | None = None
        self._is_delta_g: bool | None = None
        self._uncertainties: list[float | None] = []

    @property
    def is_delta_g(self) -> bool:
        """False until a run proves otherwise: an unrun tier cannot vouch for its scores."""
        return bool(self._is_delta_g)

    def settings(self) -> dict:
        return {
            "provider": self.provider.name,
            "provider_settings": self.provider.settings(),
            "quantity": self.quantity,
            "unit": self._unit,
            "is_delta_g": self._is_delta_g,
            "explicit_floor": self._explicit_floor,
        }

    def produces(self) -> dict[str, str]:
        return {self.quantity: self._unit or "unitless"}

    def systematic_floor(self, quantity: str) -> float | None:
        if quantity != self.quantity:
            raise KeyError(f"tier {self.name!r} does not produce {quantity!r}; produces {sorted(self.produces())}")
        if self._explicit_floor is not None:
            return self._explicit_floor
        u = self._uncertainties
        if not u or any(x is None for x in u):
            return None  # unmeasured: the funnel refuses to cut on it
        return math.sqrt(sum(x * x for x in u) / len(u))

    def estimate_cost(self, candidates: list[Candidate]) -> dict:
        return {"quantity": "wall_time", "unit": "s", "predicted": None,
                "basis": f"unmeasured: no timing recorded for provider {self.provider.name!r}"}

    def run(self, candidates: list[Candidate], ctx: dict) -> None:
        receptor = ctx.get("receptor")
        for cand in candidates:
            if not cand.poses:
                raise ValueError(f"{cand.name}: no poses to rescore")
            scores = self.provider.score(cand.poses, receptor)
            if len(scores) != len(cand.poses):
                raise ValueError(f"{cand.name}: provider returned {len(scores)} scores for {len(cand.poses)} poses")
            for s in scores:
                self._note(s)
            cand.per_pose[self.quantity] = [s.value for s in scores]
            self._uncertainties += [s.uncertainty for s in scores]

    def _note(self, s: Score) -> None:
        if self._unit is None:
            self._unit, self._is_delta_g = s.unit, s.is_delta_g
        elif (s.unit, s.is_delta_g) != (self._unit, self._is_delta_g):
            raise ValueError(
                f"provider {self.provider.name!r} mixed units/is_delta_g: "
                f"({s.unit!r}, {s.is_delta_g}) vs ({self._unit!r}, {self._is_delta_g})"
            )
