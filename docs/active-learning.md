# Active learning: an external model drives the funnel

`smeltery al run` is a loop. An external model proposes candidate molecules, smeltery scores them through the paired-
ΔΔE funnel, the scores and their uncertainty go back to the model, and it proposes the next batch. smeltery does the
chemistry and the recording; the model does the choosing. Each iteration is an ordinary campaign job
([jobs.md](jobs.md)), so the ceiling, the stage store (reuse by digest) and `cancel` all apply — there is no second
execution path.

```sh
pip install 'smeltery[mcp,attestation,rdkit]'
smeltery al run --loop halogen-scan --target pocket.pdb \
  --reference-smiles "c1ccccc1C(=O)O" \
  --proposer mymodel:proposer \
  --iterations 5 --batch 8 --budget 40 --out runs/halogen-scan
```

## The proposer

The external model is any object with a `propose` method:

```python
class Proposer(Protocol):
    def propose(self, history: list[ScoredCandidate], n: int) -> list[str]: ...  # at most n SMILES
    # optional:
    def fit(self, history: list[ScoredCandidate]) -> None: ...

    seeds: Mapping[str, int]  # recorded when the proposer exposes its own integer seeds
```

`ScoredCandidate` carries the canonical `smiles`, its `name`, the `score` (the paired ΔΔE mean against the reference,
**lower is better**), its `uncertainty` (SEM), `n_poses` and the `iteration` it was scored in. The proposer sees the
whole scored history and returns raw SMILES; the loop canonicalizes them (RDKit), drops the unparseable (a warning,
not a stop) and **dedups against everything already scored, the reference, and within the batch**, so re-suggesting a
molecule costs nothing. The funnel ranks analogues against each other, so a batch needs at least two new candidates;
once the proposer can offer fewer, the loop stops (`exhausted`).

`--proposer module:obj` imports `module` and takes `obj` (a class or zero-arg factory is called). Its identity is
recorded from what is observable, never guessed: the dotted ref, module, class, `module.__version__` when present,
and the sha256 of the module's source file.

### The built-in proposer

For a demo or a test, `--pool FILE` uses the dependency-free `PoolProposer`: it draws unscored SMILES from a fixed
pool (one SMILES per line in `FILE`), deterministically shuffled by `--seed`. No ML.

```sh
smeltery al run --loop demo --target pocket.pdb --reference-smiles "CCO" \
  --pool alcohols.smi --seed 0 --iterations 3 --batch 2 --out runs/demo
```

### The uncertainty-aware proposer

`--acquisition` swaps the random draw for `smeltery.al_proposers.UncertaintyProposer`, which fits a Gaussian process
(Tanimoto kernel on Morgan count fingerprints, each score weighted by its SEM) to the scored history and picks the
unscored pool members with the best acquisition value. It needs only numpy; without RDKit it falls back to hashed
SMILES 3-grams.

```sh
smeltery al run --loop demo --target pocket.pdb --reference-smiles "CCO" \
  --pool candidates.smi --acquisition lcb --seed 0 --iterations 5 --batch 8 --out runs/demo
```

- `--acquisition lcb` (lower confidence bound, `mean - kappa*sd`, `--kappa` default 1), `ei` (expected improvement) or
  `random` (the same proposer without a model, as a control). Scores are minimised.
- The first iteration, with no scored history, is the same seeded draw `PoolProposer` makes. A batch is chosen
  greedily with fantasised outcomes so it spreads out. Scored molecules, the reference and duplicates are never
  re-proposed.
- Deterministic given `--seed` and the history. The seed is recorded as `proposer_seeds = {"uncertainty": seed}`, and
  the acquisition, kappa, xi and featurizer in the proposer identity's `params`.
- From Python: `UncertaintyProposer(pool, seed=0, acquisition="ei", generator=fn)` also takes a
  `generator(rng, k) -> SMILES` that adds fresh candidates each iteration. `--proposer module:Class` instantiates a
  class; `smeltery.al_proposers:UncertaintyProposer` with no pool proposes nothing, so use `--acquisition` or your own
  module.

Measured on a synthetic landscape (`tests/test_al_proposers.py`: 312 molecules, additive substituent effects, a
fake scorer, 20 seeds, batch 4), the mean best score so far after iterations 1 to 6 was:

| proposer | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| `PoolProposer` (random) | -0.847 | -1.321 | -1.486 | -1.639 | -1.639 | -1.749 |
| `lcb` | -0.847 | -1.522 | -1.894 | -2.153 | -2.291 | -2.299 |
| `ei` | -0.847 | -1.526 | -1.994 | -2.249 | -2.276 | -2.295 |

The optimum is -2.299. This landscape is learnable by construction; it shows the machinery works, not how much it
helps on the funnel's real surface, which has not been measured.

## Cost and the budget

Each screen is priced by the same cost model the MCP tools use ([mcp.md](mcp.md)); `--budget CPU_H` is a ceiling on
the loop's **total predicted CPU-hours**. Before submitting an iteration the loop prices it, and if spending it would
cross the budget it stops (`over_budget`) rather than part-running. A screen that cannot be priced counts as over the
budget, never under it. The per-screen `SMELTERY_MAX_CPU_HOURS` / `SMELTERY_MAX_CANDIDATES` ceilings still apply;
`--confirm` overrides them, as in `screen_candidates`.

## What it records, and why `attest runs compare` works

The loop is **one attestation family, `al:<loop-name>`, with one arm per iteration, `iter-NNN`**. That is the choice
that makes `attest runs compare al:<loop-name>` meaningful: it ranks the arms of a family by a shared metric, so with
each iteration an arm the comparison becomes the proposer's learning curve — did the best score fall across
iterations? One family per iteration would give each iteration its own single-arm family with nothing to compare
across them.

**The record is correct the first time it is written, and is never rewritten afterwards.** A finished core record is
the artifact of record: its bytes are what other readers hash (the job's own `settle`, a dataset's
`source_runs[].record_sha256`, a ledger scan). So the loop hands everything over at **submit** instead — the campaign
job carries the arm and the loop's `ext` namespace (`JobSpec.arm` / `JobSpec.ext`, validated by `jobs.check_spec`,
reaching the run as `smeltery run --arm --ext-json`; see [jobs.md](jobs.md#arm-and-ext)) — and the run states them
when it writes its record. What the record then carries:

- `family = al:<loop>`, `arm = iter-NNN`;
- `ext.smeltery.al`, beside the run's own `ext.smeltery`: the loop name, the iteration index, the proposer's recorded
  identity and its exposed seeds, the batch it proposed, the budget, what was spent before, and this screen's
  *predicted* cost. All of it is known before the run starts — nothing post-hoc;
- `best_paired_ddE` and `mean_paired_ddE`: label-free, direction `min`, the quantity's unit, **derived from the run's
  own results** (the lowest / mean paired ΔΔE among its analogues). These are what `compare` ranks the arms by: the
  per-analogue `paired_ddE.*` metrics cannot serve, because each iteration screens different molecules and so carries
  different `labels.analogue`. They are emitted only for a run whose submitter named an arm — an arm exists to be
  compared — so every other smeltery record is unchanged.

The loop's own cross-iteration summary (every iteration's batch, scores, uncertainties and batch best) lives in
`al-manifest.json`, which is the loop's artifact; the records carry the per-arm scalar, and the manifest carries the
curve and the chain of record digests.

Grouping needs the `attestation` extra, as campaign jobs do. Both backends carry the arm and ext: the local one hands
them to the run (`smeltery run --arm --ext-json`), and `SMELTERY_BACKEND=temporal` carries them in the workflow's
input to its record stage, so on either one each iteration's core record states its family and arm at its first
write.

## The loop manifest and resume

`<out>/al-manifest.json` (canonical JSON) is the audit trail: the loop, the reference, the proposer's identity, the
budget, and one entry per iteration — its run directory, its `core_record_sha256` (the sha256 of the record **as the
job wrote it**; the loop reads it and never writes the file), its family/arm, its scored candidates and the batch
best. It is rewritten after each iteration. A loop
stopped by `Ctrl-C`, a crash or `smeltery cancel` **resumes** from it: re-running the same command into the same
`--out` keeps the iterations already recorded complete, re-seeds the proposer's history from their scores, and
continues.

## Exit and stop reasons

`smeltery al run` exits 0 when it ran (2 on a bad argument or a refusal). The manifest's `stopped.reason` is one of
`iterations` (ran them all), `exhausted` (fewer than two new candidates), `over_budget`, or `failed` (an iteration's
screen did not complete — its reason is in `stopped.detail`).

## Library

`smeltery.al.run_loop(...)` takes an injected `settings` and an optional `backend`, and returns the `LoopManifest`;
`smeltery.al.{Proposer, ScoredCandidate, PoolProposer, load_proposer}` are the rest of the surface. `tests/
test_active_learning.py` drives a 2–3 iteration loop on the fake engine against a `LocalJobBackend`, as the MCP and
job-backend tests do.
