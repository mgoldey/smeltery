"""A cheap fake scorer and loop driver for proposer tests: no funnel, no quantum chemistry.

The landscape is a hidden structure-activity relationship over a combinatorial pool (scaffold x two substituents):
each substituent contributes a fixed additive effect, one pair interacts, and each molecule carries a fixed
deterministic noise draw; `uncertainty` is a constant SEM. Lower is better, as for paired ddE.
"""

from __future__ import annotations

import zlib

from smeltery.al import PoolProposer, ScoredCandidate, _canonical

SCAFFOLDS = ["c1({a})ccc({b})cc1", "c1({a})ccnc({b})c1", "c1({a})ccc2ccccc2c1", "C1({a})CCC({b})CC1"]
SUBS = ["F", "Cl", "Br", "C", "CC", "O", "OC", "N", "C(F)(F)F", "C#N", "C(=O)O", "S"]
EFFECT = {
    "F": -0.9,
    "Cl": -0.6,
    "Br": -0.3,
    "C": 0.1,
    "CC": 0.2,
    "O": 0.4,
    "OC": 0.1,
    "N": 0.5,
    "C(F)(F)F": -1.2,
    "C#N": -0.2,
    "C(=O)O": 0.8,
    "S": 0.3,
}
SCAFFOLD_EFFECT = [0.0, 0.3, -0.2, 0.6]
SEM = 0.05


def build_pool() -> list[str]:
    pool = []
    for si, scaf in enumerate(SCAFFOLDS):
        for a in SUBS:
            for b in SUBS if "{b}" in scaf else [""]:
                pool.append((scaf.format(a=a, b=b), si, a, b))
    out, seen = [], set()
    for smi, si, a, b in pool:
        c = _canonical(smi)
        if c is not None and c not in seen:
            seen.add(c)
            out.append((c, si, a, b))
    return out


_ENTRIES = build_pool()
POOL = [e[0] for e in _ENTRIES]
_TRUE = {}
for smi, si, a, b in _ENTRIES:
    noise = (zlib.crc32(smi.encode()) % 1000 / 1000.0 - 0.5) * 0.4
    _TRUE[smi] = SCAFFOLD_EFFECT[si] + EFFECT[a] + EFFECT.get(b, 0.0) + (-0.5 if (a, b) == ("F", "F") else 0.0) + noise
BEST = min(_TRUE.values())


def score(smi: str) -> float:
    return _TRUE[smi]


def run_fake_loop(proposer, *, iterations: int, batch: int, reference: str | None = None) -> list[float]:
    """Mimic `al.run_loop`'s bookkeeping (dedup against history, need >= 2 new) with the fake scorer; returns the
    best score so far after each iteration."""
    history: list[ScoredCandidate] = []
    seen = {reference} if reference else set()
    best_curve, best = [], float("inf")
    for it in range(iterations):
        if hasattr(proposer, "fit"):
            proposer.fit(history)
        fresh = []
        for s in proposer.propose(history, batch):
            c = _canonical(s)
            if c is not None and c not in seen and c not in fresh:
                fresh.append(c)
        if len(fresh) < 2:
            break
        for i, c in enumerate(fresh):
            history.append(ScoredCandidate(c, f"c{it:03d}-{i}", score(c), SEM, 4, it))
            seen.add(c)
            best = min(best, score(c))
        best_curve.append(best)
    return best_curve


def compare(seeds, iterations=6, batch=4, acquisitions=("lcb", "ei")):
    from smeltery.al_proposers import UncertaintyProposer

    out = {"pool": [], **{a: [] for a in acquisitions}}
    for s in seeds:
        out["pool"].append(run_fake_loop(PoolProposer(POOL, seed=s), iterations=iterations, batch=batch))
        for a in acquisitions:
            out[a].append(
                run_fake_loop(UncertaintyProposer(POOL, seed=s, acquisition=a), iterations=iterations, batch=batch)
            )
    return out
