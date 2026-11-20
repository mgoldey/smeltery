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
   `out_dir = <loop>/iter-NNN`, **`arm = "iter-NNN"` and `ext = {"smeltery.al": {...}}`**. This runs all the ceiling /
   reuse / staging code the MCP tool runs, and the arm and the namespace travel with the spec (below).
5. Poll `screening.status(handle)` until terminal. Anything but `complete` stops the loop (the iteration's reason is
   recorded).
6. Read each proposed candidate's `paired_ddE` (`mean`, `sem`, `n`) from the run's own record → `ScoredCandidate`s.
7. Read the core record's sha256 (never write it), append the iteration to the loop manifest, and feed the new scores
   into `history`.

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

### The record is correct at its first write; it is never re-stamped

A finished core record is the **artifact of record**. Its bytes are what other readers hash: the job's own `settle`
checks the record in the run directory, a dataset's `source_runs[].record_sha256` pins it, a ledger scan may read it
between the job ending and anything else happening. Annotating it afterwards would also make it claim the producer
observed an arm and metrics at run time that it did not. So nothing is written back. Instead:

- **smeltery's job contract carries the arm and the submitter's `ext`.** `smeltery.jobs.check_spec` is smeltery's own
  check (not the published `attestation_schema.jobs` protocol, which already has `JobSpec.arm` and `JobSpec.ext`), so
  it is the thing to change: it now accepts an `arm` matching `^[A-Za-z0-9_.:-]+$` — a label, since it is a key
  readers group by — and `ext` namespaces that map to objects and are not `smeltery` (the run's own). The local
  backend's runner passes them to the run as `smeltery run --arm NAME --ext-json JSON`, `cli.emit_core_record` hands
  them to `core_record.write_core`, and `core_fields` writes them. The rule the contract states for a backend that
  *cannot* hand them over still holds: the Temporal backend's finalize activity takes the run_id only, so it
  **refuses** a spec with an arm or an ext at `submit` rather than dropping them, and `run --temporal --arm` is
  refused too.
- **smeltery has no arm of its own**, so `arm` is `None` for every run outside a job and those records are unchanged.

### The iteration metric: the run's own, not the loop's

The scalar `compare` ranks on is **derived from the run's own results**, so the producer can state it honestly at
first write: `core_record.arm_metrics(results)` emits `best_paired_ddE` (the lowest paired ΔΔE mean among this run's
analogues) and `mean_paired_ddE`, both label-free, both direction `min` (the direction the cut itself ranks on), both
carrying the quantity's unit. `results.paired_ddE` holds analogues only (never the parent), so for an AL iteration
these are exactly the batch; a run with no finite analogue mean gets neither, and nothing is invented.

They are emitted **only when the submitter named an arm** — an arm exists to be compared, so a run that is one
declares the comparable scalar. That keeps every other smeltery record byte-for-byte what it was (the prebuilt and
conversion records are pinned), and it keeps the metric out of `al.*`: it is not an active-learning quantity, it is a
fact about any campaign run, and naming it `al.batch_best` would have been the loop's language in the producer's
record.

Chosen over the alternative (keep the curve only in `al-manifest.json`): the manifest is the loop's own artifact and
is *also* where the human-readable curve lives, but a summary file is not auditable the way a signed, hashed record
is — and without a comparable metric in the records, the family/arm grouping would buy nothing and `compare` would
stay inert. So both: the records carry the per-arm scalar `compare` ranks, the manifest carries the cross-iteration
summary and the chain of record digests.

What `ext.smeltery.al` holds, all of it known **before** the run starts (which is why it can be handed over at
submit): the loop name, the iteration index, the proposer's observed identity and its exposed seeds, the batch it
proposed (name + canonical SMILES), the budget, what was already spent, and this screen's *predicted* cost. Nothing
post-hoc is in the record; the measured outcome is the record's own metrics.

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

`run_dir` is relative to the loop dir and each iteration pins `core_record_sha256` — the sha256 of its
`attestation/run.json` **as the job wrote it**, read and never rewritten — so the chain is auditable: the manifest
names every iteration's run and the exact record bytes it recorded, and a test asserts those bytes and their mtime do
not change once the job has settled. **Resume** reads the manifest and skips iterations already recorded complete,
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
- Grouping needs the `attestation` extra, because it is the core record that carries the family and arm (campaign
  jobs need it at all). Without it there is no record to group, and `core_record_sha256` is null in the manifest.
- `SMELTERY_BACKEND=temporal` cannot run a loop iteration: it carries no arm or ext, and refuses the spec. The loop
  reports that refusal as its stop reason rather than silently recording ungrouped runs.

## Tests

`tests/test_active_learning.py`, on the fake engine against a `LocalJobBackend` (as `tests/test_mcp_server.py` and
`tests/test_job_backends.py` do): a 2–3 iteration end-to-end loop that scores and records; canonical-SMILES dedup
across iterations; an unparseable proposal dropped, not fatal; resume after an interrupted loop; budget refusal;
exhaustion; the records as `compare` needs them (family `al:<loop>`, arms `iter-000…`, label-free directional
`best_paired_ddE`, `ext.smeltery.al` beside `ext.smeltery`); that a run **without** an arm is unchanged (same ext,
same metrics, `arm: null`); that the finished record is **never rewritten** (its sha256 and mtime are identical after
later iterations run, and the manifest's pinned sha is those bytes); and `check_spec`'s arm and namespace rules. The
heavy tier is never run — the fake engine is the suite's standard stand-in.
