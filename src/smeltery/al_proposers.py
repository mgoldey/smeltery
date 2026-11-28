"""An uncertainty-aware proposer for the active-learning loop (docs/active-learning.md, spec
2026-11-28-uncertainty-proposer-design.md).

`UncertaintyProposer` fits a Gaussian process with a Tanimoto (min/max) kernel over Morgan count fingerprints to the
scored history, weighting each observation by its own SEM, and picks the unscored candidates with the best
acquisition value: a lower confidence bound (`lcb`, the default) or expected improvement (`ei`) for the paired ΔΔE,
which is minimised. `random` draws without a model, the same family of draw as `PoolProposer`.

Dependencies: numpy only (a core dependency). RDKit supplies the fingerprints and canonical SMILES when it imports;
without it the features are hashed character 3-grams of the SMILES string and canonicalisation is the identity.
That is a weaker similarity, but the proposer still fits, proposes and dedups -- it never fails on import.

Determinism: every random choice comes from `random.Random(seed + len(history))`, and string hashing uses crc32
(never the per-process `hash()`), so one seed and one history give one batch. The seed is exposed as
`seeds = {"uncertainty": seed}`, which the loop records; the acquisition and its parameters are exposed as `params`.
"""

from __future__ import annotations

import math
import random
import zlib
from collections.abc import Callable, Iterable, Sequence

import numpy as np

from .al import ScoredCandidate

ACQUISITIONS = ("lcb", "ei", "random")
_N_BITS = 1024
#: A generator is called as `generator(rng, k)` and returns up to `k` SMILES; it may repeat itself or return junk.
Generator = Callable[[random.Random, int], Iterable[str]]


def _rdkit():
    try:
        from rdkit import Chem  # noqa: PLC0415
        from rdkit.Chem import rdFingerprintGenerator  # noqa: PLC0415

        return Chem, rdFingerprintGenerator
    except Exception:  # noqa: BLE001 - any import failure means "no RDKit"; the fallback features take over
        return None


def canonical(smiles: str) -> str | None:
    """RDKit canonical SMILES; the string itself (stripped) when RDKit is absent; None when it does not parse."""
    rd = _rdkit()
    s = smiles.strip()
    if not s:
        return None
    if rd is None:
        return s
    mol = rd[0].MolFromSmiles(s)
    return None if mol is None else rd[0].MolToSmiles(mol)


def featurize(smiles: Sequence[str], *, use_rdkit: bool | None = None) -> np.ndarray:
    """(n, 1024) non-negative count features. Morgan radius-2 counts with RDKit; hashed SMILES 3-grams without."""
    rd = _rdkit() if use_rdkit is not False else None
    out = np.zeros((len(smiles), _N_BITS), dtype=np.float64)
    gen = rd[1].GetMorganGenerator(radius=2, fpSize=_N_BITS) if rd is not None else None
    for i, smi in enumerate(smiles):
        mol = rd[0].MolFromSmiles(smi) if rd is not None else None
        if gen is not None and mol is not None:
            out[i] = gen.GetCountFingerprintAsNumPy(mol)
            continue
        for j in range(max(1, len(smi) - 2)):
            out[i, zlib.crc32(smi[j : j + 3].encode()) % _N_BITS] += 1.0
    return out


def tanimoto_kernel(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Min/max Tanimoto kernel on count vectors (positive semi-definite); 1 on the diagonal for non-empty rows."""
    num = np.minimum(a[:, None, :], b[None, :, :]).sum(-1)
    den = np.maximum(a[:, None, :], b[None, :, :]).sum(-1)
    return np.where(den > 0, num / np.where(den > 0, den, 1.0), 1.0)


def _phi(z: np.ndarray) -> np.ndarray:
    return np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi)


def _Phi(z: np.ndarray) -> np.ndarray:  # noqa: N802 - the standard normal CDF
    return 0.5 * (1.0 + np.vectorize(math.erf)(z / math.sqrt(2.0)))


class UncertaintyProposer:
    """Propose the unscored candidates a Tanimoto-kernel GP expects to be best, trading mean against uncertainty.

    `pool` is a fixed list of SMILES; `generator` (optional) adds `generator_k` fresh SMILES per call. With no scored
    history (cold start) it draws at random, deterministically by seed. `kappa` is the LCB exploration weight (the
    score is `mean - kappa * sd`, lower is better); `xi` the EI margin. `exclude` is extra SMILES never proposed
    (e.g. the reference), beyond everything already in the history.
    """

    def __init__(
        self,
        pool: Sequence[str] = (),
        *,
        seed: int = 0,
        acquisition: str = "lcb",
        kappa: float = 1.0,
        xi: float = 0.0,
        generator: Generator | None = None,
        generator_k: int = 64,
        exclude: Sequence[str] = (),
        max_candidates: int = 2000,
    ) -> None:
        if acquisition not in ACQUISITIONS:
            raise ValueError(f"acquisition must be one of {ACQUISITIONS}, got {acquisition!r}")
        self.seed = int(seed)
        self.acquisition = acquisition
        self.kappa = float(kappa)
        self.xi = float(xi)
        self.generator = generator
        self.generator_k = int(generator_k)
        self.max_candidates = int(max_candidates)
        self.seeds = {"uncertainty": self.seed}
        self.params = {"acquisition": acquisition, "kappa": self.kappa, "xi": self.xi, "featurizer": self._featurizer()}
        self._pool = self._dedup(pool)
        self._exclude = {c for c in (canonical(s) for s in exclude) if c is not None}
        self._train_x = np.zeros((0, _N_BITS))
        self._train_y = np.zeros(0)
        self._train_noise = np.zeros(0)

    @staticmethod
    def _featurizer() -> str:
        return "morgan2-counts" if _rdkit() is not None else "smiles-3gram-hash"

    @staticmethod
    def _dedup(smiles: Iterable[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for s in smiles:
            c = canonical(s)
            if c is not None and c not in seen:
                seen.add(c)
                out.append(c)
        return out

    # ---- the Proposer protocol --------------------------------------------------------------------------------

    def fit(self, history: list[ScoredCandidate]) -> None:
        """Condition the GP on the finite-scored history. Rows without a finite score are ignored here (they are
        still excluded from proposals by `propose`)."""
        rows = [c for c in history if c.score is not None and math.isfinite(c.score)]
        if not rows:
            self._train_x, self._train_y, self._train_noise = np.zeros((0, _N_BITS)), np.zeros(0), np.zeros(0)
            return
        self._train_x = featurize([c.smiles for c in rows])
        self._train_y = np.array([c.score for c in rows], dtype=float)
        sems = np.array(
            [c.uncertainty if c.uncertainty is not None and math.isfinite(c.uncertainty) else np.nan for c in rows]
        )
        fallback = float(np.nanmedian(sems)) if np.isfinite(sems).any() else 0.0
        self._train_noise = np.where(np.isfinite(sems), sems, fallback) ** 2

    def propose(self, history: list[ScoredCandidate], n: int) -> list[str]:
        n = max(0, int(n))
        rng = random.Random(self.seed + len(history))
        seen = {c.smiles for c in history} | self._exclude
        cands = [s for s in self._pool if s not in seen]
        if self.generator is not None:
            cands = cands + [s for s in self._dedup(self.generator(rng, self.generator_k)) if s not in seen]
            cands = list(dict.fromkeys(cands))
        if len(cands) > self.max_candidates:
            cands = rng.sample(cands, self.max_candidates)
        if n == 0 or not cands:
            return []
        self.fit(history)  # `propose` is correct without a prior `fit`; the loop calls both on the same history
        if self.acquisition == "random" or len(self._train_y) < 2:
            rng.shuffle(cands)  # cold start (or the random baseline): a seeded draw, no model
            return cands[:n]
        return self._select(cands, n)

    # ---- the model --------------------------------------------------------------------------------------------

    def _posterior(self, x: np.ndarray, y: np.ndarray, noise: np.ndarray, xs: np.ndarray, m: float, s: float):
        """Standardised GP posterior mean and sd at `xs` (unit signal variance, per-point noise, small jitter)."""
        k = tanimoto_kernel(x, x) + np.diag(noise / (s * s) + 1e-6)
        ks = tanimoto_kernel(xs, x)
        sol = np.linalg.solve(k, np.column_stack([(y - m) / s, ks.T]))
        mean = ks @ sol[:, 0]
        var = np.clip(1.0 - np.einsum("ij,ji->i", ks, sol[:, 1:]), 1e-12, None)
        return m + s * mean, s * np.sqrt(var)

    def _acquire(self, mu: np.ndarray, sd: np.ndarray, best: float) -> np.ndarray:
        """Higher is better for both acquisitions."""
        if self.acquisition == "lcb":
            return -(mu - self.kappa * sd)
        z = (best - self.xi - mu) / sd
        return sd * (z * _Phi(z) + _phi(z))

    def _select(self, cands: list[str], n: int) -> list[str]:
        x, y, noise = self._train_x, self._train_y, self._train_noise
        m = float(y.mean())
        s = float(y.std()) or 1.0
        xs = featurize(cands)
        best = float(y.min())
        picked: list[int] = []
        # Greedy batch with fantasised outcomes (kriging believer): a pick joins the training set at its predicted
        # mean, which leaves the mean alone and shrinks the variance around it, so a batch spreads out.
        fx, fy, fn = x, y, noise
        for _ in range(min(n, len(cands))):
            mu, sd = self._posterior(fx, fy, fn, xs, m, s)
            acq = self._acquire(mu, sd, best)
            acq[picked] = -np.inf
            j = int(np.argmax(acq))  # ties go to the lowest index: the pool order is the (deterministic) tie-break
            picked.append(j)
            fx = np.vstack([fx, xs[j : j + 1]])
            fy = np.append(fy, mu[j])
            fn = np.append(fn, 0.0)
        return [cands[j] for j in picked]
