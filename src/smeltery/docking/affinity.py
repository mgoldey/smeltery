"""Turning a pose ensemble's scores into one affinity-like number, honestly.

Vina scores are an empirical ranking heuristic. A flexible-ligand dock returns
many poses of one molecule; the best score alone is the noisiest summary of
them. `ensemble_score` pools them with a Boltzmann weight,

    G_ens = -kT ln( sum_i exp(-s_i / kT) )  (+ kT ln N optionally, see below)

which is bounded above by the best pose and rewards a molecule that has many
good poses over one with a single lucky one. It is still a heuristic in the
engine's own units, NOT a binding free energy: it is exactly as accurate as the
scores it pools. Compare it between candidates only through the funnel's paired
machinery; it carries no systematic floor.
"""

from __future__ import annotations

import math

KB_KCAL = 0.0019872041  # kcal/(mol K)


def ensemble_score(scores: list[float], temperature: float = 298.15) -> float:
    """Boltzmann-pooled score (log-sum-exp), same unit as `scores`. Lower is better.

    Not normalised by the pose count: adding a genuinely distinct good pose should
    improve the ensemble score, and adding a bad one should barely move it.
    """
    if not scores:
        raise ValueError("no scores to pool")
    if any(math.isnan(s) for s in scores):
        raise ValueError("NaN score: refusing to pool an unmeasured pose")
    if temperature <= 0:
        raise ValueError("temperature must be positive")
    kt = KB_KCAL * temperature
    m = min(scores)
    return m - kt * math.log(sum(math.exp(-(s - m) / kt) for s in scores))
