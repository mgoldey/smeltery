# Active-learning hook: an external proposer drives the funnel, each loop recorded in attestation

Date: 2026-11-20. Roadmap task 37. Status: requested by the user.

## Why

An active-learning campaign is a loop: an external model proposes candidate molecules, smeltery scores them through
its tiered funnel, the scores (and their uncertainty) go back to the model, and it proposes the next batch. smeltery
already has everything an iteration needs — a validated campaign config, the paired-ΔΔE funnel, a `JobBackend` with
budget guardrails, reuse-by-digest and cancel, and a core record per run. What is missing is the thin layer that
turns "one screen" into "a loop of screens", keeps the candidate bookkeeping (canonical-SMILES dedup, a resumable
manifest), and records each iteration so the chain is auditable and `attest runs compare` is meaningful.

The hard rule is **no second execution path**: an iteration is an ordinary campaign job submitted through
`smeltery.jobs` / `smeltery.mcp.screening`, so the ceiling (`SMELTERY_MAX_*`), the stage store and `cancel` all
apply unchanged. The loop is orchestration and recording only; it computes no chemistry itself.

## The proposer boundary

```python
@runtime_checkable
class Proposer(Protocol):
    def propose(self, history: list[ScoredCandidate], n: int) -> list[str]: ...  # SMILES, at most n
    # optional:
    def fit(self, history: list[ScoredCandidate]) -> None: ...

    seeds: Mapping[str, int]  # optional: the proposer's own seeds, recorded where it exposes them
```

`ScoredCandidate` is a frozen dataclass: canonical `smiles`, `name`, `score` (the paired ΔΔE mean against the
reference, lower is better; `None` for the reference or an unscored row), `uncertainty` (its SEM), `n_poses`, and the
`iteration` it was scored in. The proposer sees the whole scored history and returns raw SMILES; the loop owns
validity and dedup, so a proposer that re-suggests a scored molecule costs nothing.

A trivial built-in `PoolProposer(pool, seed=0)` ships for tests and the demo: it draws unscored SMILES from a fixed
pool, deterministically shuffled by `Random(seed + len(history))`, and exposes `seeds = {"pool": seed}`. No ML
dependency. It is also what `smeltery al run --pool FILE` uses when no `--proposer` is given.

`--proposer module:obj` imports `module` and takes `obj`; if `obj` is a class or a zero-arg factory it is called.
Its identity is recorded from what is **observable, never guessed**: the dotted `ref`, the module name, the object's
type, `module.__version__` when present, and the sha256 of the module's source file when it is a real file.

## The loop, one iteration

1. `fit(history)` if the proposer has it; `propose(history, batch)`.
2. Canonicalize each SMILES (RDKit); drop the unparseable (a warning, not a stop); dedup against every scored
   molecule, the reference, and within the batch. Fewer than two new candidates left → stop (`exhausted`): the
   funnel ranks analogues against each other, so a scoring screen needs a reference plus at least two analogues.
3. Budget gate (`--budget`, cumulative predicted CPU-hours for the whole loop): price the screen with
   `smeltery.cost` through the same plan the submission uses; if spending it would cross the budget, or it cannot be
   priced, stop (`over_budget`) — an unpriceable screen counts as over, never under, matching the MCP ceiling.
4. Submit as a campaign job via `smeltery.mcp.screening.screen(...)` with `campaign = "al:<loop>"`,
   `out_dir = <loop>/iter-NNN`. This runs all the ceiling / reuse / staging code the MCP tool runs.
5. Poll `screening.status(handle)` until terminal. Anything but `complete` stops the loop (the iteration's reason is
   recorded).
6. Read each proposed candidate's `paired_ddE` (`mean`, `sem`, `n`) from the run's own record → `ScoredCandidate`s.
7. Re-stamp the iteration's core record (below), append the iteration to the loop manifest (fsync), and feed the new
   scores into `history`.

## Recording: family / arm choice

**Decision: one family `al:<loop-name>`, arm `iter-NNN` per iteration** — not the roadmap's literal "one family per
iteration".

`attest runs compare FAMILY` ranks the *arms within one family* by a shared directional metric. The question an
active-learning loop asks is "did the proposer drive the best score down across iterations?", which is exactly
"rank iter-000, iter-001, … by their best score". That needs every iteration to be an **arm of one family** and to
carry a **metric with the same name and no distinguishing labels** across iterations. One family per iteration would
instead give each iteration its own single-arm family, and nothing to compare across them; the per-analogue
`paired_ddE.mean` metrics never align across iterations because each batch is different molecules (different
`analogue` labels). So the literal reading makes `compare` inert, and the chosen one makes it the loop's progress
plot.

smeltery's own campaign core record is `family = campaign`, `arm = null` (it has no arm, by design, and `check_spec`
refuses an arm in a `JobSpec`). The loop does not fight that: the job writes its record as usual (`family =
al:<loop>`, `arm = null`), and **afterwards** the loop re-stamps that record with the arm and the iteration-level
metrics it alone knows. This is not a second record and not a second run — it is the orchestrator annotating a
finished run with its place in the loop, written back through `attestation_schema`'s own writer (`from_dict` →
`write(overwrite=True)`). The record stays a valid `attestation.run/1`; `attestation/digests.json` is untouched
(it covers files *outside* `attestation/`), so the manifest the record binds is unchanged.

Re-stamp adds, and only adds:

- `arm = "iter-NNN"`.
- `ext["smeltery.al"] = {loop, iteration, proposer: {...identity...}, batch: [{name, smiles}], budget_spent_cpu_h}`.
  Its own namespace (`ext.smeltery.al`), beside the run's `ext.smeltery`, so nothing smeltery wrote is rewritten.
- iteration-level metrics, shared across arms so `compare` can rank them:
  `al.batch_best` (min of the batch's scores, direction `min`, unit kcal/mol),
  `al.batch_mean` (direction `min`), `al.batch_n` (count, no direction). No `labels`, so the key is the same in every
  iteration's record.
- the proposer's seeds folded into the record's `seeds` under `al:<key>` (only when it exposes integer seeds; never
  invented).

Because each iteration is a distinct arm of one family with distinct molecules (no shared `seeds`, so never pooled as
replicates), `attest runs compare al:<loop>` reads them as separate arms and ranks them by `al.batch_best` — the
proposer's learning curve, audited from the signed records, not from the loop's own summary.

## The loop manifest

`<loop-dir>/al-manifest.json`, canonical JSON (`attestation_schema.canonical.canonicalize` when available, else
`json.dumps(sort_keys=True)`), rewritten atomically after each iteration:

```json
{"schema": "smeltery.al/1", "loop": "<name>", "reference": {"name": ..., "smiles": ...},
 "proposer": {"ref": "module:obj", "module": ..., "class": ..., "version": ..., "source_sha256": ...},
 "target": "<path>", "budget_cpu_h": <float|null>,
 "iterations": [
   {"iteration": 0, "run_dir": "iter-000", "core_record_sha256": "<hex>", "family": "al:<name>", "arm": "iter-000",
    "campaign": "al:<name>", "state": "complete", "candidates": [{"name","smiles","score","uncertainty","n"}],
    "batch_best": <float|null>, "budget_spent_cpu_h": <float>}],
 "stopped": {"reason": "exhausted|over_budget|failed|iterations", "detail": "..."}}
```

`run_dir` is relative to the loop dir and each iteration pins `core_record_sha256` (the sha256 of its
`attestation/run.json` *after* re-stamping), so the chain is auditable: the manifest names every iteration's run and
the exact record bytes it recorded. **Resume** reads the manifest and skips iterations already recorded complete,
re-seeding `history` from their candidates, so a stopped loop (`Ctrl-C`, a crash, `smeltery cancel`) continues where
it left off into the same loop dir.

## CLI / library

```
smeltery al run --loop NAME --target T --reference-smiles S [--reference-name N]
                (--proposer module:obj | --pool FILE [--seed K])
                --iterations K --batch N [--budget CPU_H]
                [--out DIR] [--target-class C] [--keep M] [--confirm] [--relax-unmapped true|false]
```

Library: `smeltery.al.run_loop(...)` takes an injected `settings` and optional `backend` (the tests pass a
`LocalJobBackend` on the fake engine, as the MCP tests do), returns the `LoopManifest`. `smeltery.al.load_proposer`,
`PoolProposer`, `ScoredCandidate`, `Proposer` are the public surface.

## What this does not do

- No surrogate model, acquisition function or uncertainty model of its own: those live behind the `Proposer`
  boundary, in the caller's code. smeltery scores and records; it does not choose.
- No new tier and no change to the funnel, the cut, or the campaign config schema.
- The re-stamp needs the `attestation` extra (it edits a core record). Without it the loop still runs and scores;
  it records a plain manifest and warns that iterations are not grouped as a family (same posture as a plain
  `smeltery run` without the extra).

## Tests

`tests/test_active_learning.py`, on the fake engine against a `LocalJobBackend` (as `tests/test_mcp_server.py` and
`tests/test_job_backends.py` do): a 2–3 iteration end-to-end loop that scores and records; canonical-SMILES dedup
across iterations; resume after an interrupted loop; budget refusal; and, gated on `attestation_schema`, the
re-stamped core records (family `al:<loop>`, arms `iter-000…`, the `al.batch_best` metric and `ext.smeltery.al`) so
`attest runs compare` has what it needs. The heavy tier is never run — the fake engine is the suite's standard
stand-in.
