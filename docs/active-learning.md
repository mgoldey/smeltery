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
iterations? One family per iteration (the roadmap's literal wording) would give each iteration its own single-arm
family with nothing to compare across them.

smeltery's campaign record is `family = campaign`, `arm = null`. The job writes its record as usual
(`family = al:<loop>`), and the loop then **annotates that finished record** with the arm and the metrics it alone
knows, written back through attestation-schema's own writer — no second record, and `attestation/digests.json` is
untouched. The annotation adds only:

- `arm = "iter-NNN"`;
- `ext.smeltery.al`: the loop name, iteration index, the proposer's recorded identity, the batch (name + SMILES), and
  the CPU-hours spent so far;
- iteration-level metrics with no labels, so their key is the same in every iteration: `al.batch_best` (min score,
  direction `min`), `al.batch_mean`, `al.batch_n`;
- the proposer's seeds, folded into the record's `seeds` under `al:<key>`, only when it exposes them.

Without the `attestation` extra the loop still runs and scores; it writes a plain manifest and does not group the
iterations.

## The loop manifest and resume

`<out>/al-manifest.json` (canonical JSON) is the audit trail: the loop, the reference, the proposer's identity, the
budget, and one entry per iteration — its run directory, its `core_record_sha256` (the exact record bytes it
recorded), its family/arm, its scored candidates and the batch best. It is rewritten after each iteration. A loop
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
