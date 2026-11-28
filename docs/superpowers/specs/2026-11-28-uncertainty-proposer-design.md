# An uncertainty-aware proposer for the active-learning loop

Date: 2026-11-28. Follows 2026-11-20-active-learning-design.md, which left "no surrogate model, acquisition function
or uncertainty model of its own" behind the `Proposer` boundary. This adds one, outside the loop.

## What

`smeltery.al_proposers.UncertaintyProposer(pool, *, seed, acquisition, kappa, xi, generator, exclude, ...)`, a
`Proposer` that chooses the next batch from a candidate pool (and optionally a `generator(rng, k) -> SMILES`).

- **Model**: a Gaussian process with a min/max Tanimoto kernel on Morgan radius-2 count fingerprints (1024 bins),
  standardised targets, unit signal variance. Each observation's noise is its own `uncertainty` (SEM) squared, so a
  noisy score pulls the fit less; a missing SEM takes the median SEM. numpy only; no new dependency.
- **Acquisition** (score is minimised): `lcb` = `mean - kappa*sd` (default `kappa=1`), `ei` = expected improvement over
  the best scored value, `random` = a seeded draw without a model (the control).
- **Batch**: greedy with fantasised outcomes (a pick joins the training set at its predicted mean), so the batch
  spreads out instead of picking n near-duplicates.
- **Cold start**: fewer than two finite scores means no fit; it draws `Random(seed + len(history))` over the
  candidates, the same draw `PoolProposer` makes, so iteration 0 is identical for the same seed.
- **Dedup**: candidates exclude every history SMILES (scored or not), `exclude` (the CLI passes the reference), and
  duplicates and unparseable entries in the pool. The loop still dedups after it, as for any proposer.
- **No RDKit**: `canonical` becomes the stripped string and features become hashed (crc32) SMILES 3-grams. Similarity
  is cruder, but it fits, proposes and dedups. (The loop itself still needs RDKit; this keeps the proposer usable
  and testable on its own.)
- **Determinism**: all randomness is `random.Random(seed + len(history))`; ties break by pool order; no `hash()`.
- **Recording**: `seeds = {"uncertainty": seed}` is recorded by the loop as it is for any proposer. The acquisition,
  `kappa`, `xi` and featurizer are exposed as `params`; `proposer_identity` now records an optional `params` mapping
  in the manifest's proposer identity when a proposer has one (absent for others, so existing identities are
  unchanged).

## Wiring

`smeltery al run --pool FILE --acquisition lcb|ei|random [--kappa K] [--seed S]` builds the proposer (ref
`smeltery.al_proposers:UncertaintyProposer`). `--acquisition` needs `--pool`. `--proposer
smeltery.al_proposers:UncertaintyProposer` also loads, but with an empty pool: it proposes nothing and the loop
stops `exhausted`; use the flag, or construct it in your own module.

One fix to `load_proposer`: it skipped calling a class that had a `propose` attribute (every class does), so
`--proposer module:Class` handed the loop an uninstantiated class. A class is now instantiated, as the docs said.

## Tests and the honesty rule

`tests/test_al_proposers.py` covers protocol, seeds and params, cold start, determinism, dedup and exclusion,
exhaustion, a generator, the no-RDKit path, kernel properties and the CLI wiring. The comparison test runs
`tests/al_synthetic.py`: a 312-molecule pool with an additive structure-activity landscape (scaffold, two
substituents, one interaction, fixed noise), a constant SEM, and a loop driver mimicking `run_loop`'s dedup. It asserts
the model-guided mean best-so-far is below `PoolProposer`'s at every iteration after the shared cold start, and by
more than 0.3 at the end, over 20 seeds. The landscape is additive in fingerprint-visible features, which suits
this model; the result says the proposer works when the structure is learnable, not that it will on the funnel's
real surface. No funnel run is involved.
