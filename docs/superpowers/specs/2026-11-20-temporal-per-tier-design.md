# Temporal workflows: one workflow per campaign, one activity per stage, heartbeats, retries, the record on disk

Date: 2026-11-20. Roadmap task 20. Status: built (this note is the audit, the decisions and what is left).

Task text: "Temporal workflows. One workflow per campaign, one activity per tier, heartbeats and retries. Durable
state in Temporal; the on-disk record stays the artifact of record." Plus: lift the PR #141 limitation (the Temporal
backend refused a `JobSpec` with an `arm` or an `ext`).

## Audit: what existed at 9ca16b8

The Temporal path is `smeltery.temporal_run` (`smeltery run --out OUT --temporal`) and, on top of it,
`smeltery.jobs.TemporalJobBackend` (`SMELTERY_BACKEND=temporal`, `smeltery submit`), which starts the same workflow
without waiting and mirrors it into `state.json` when someone looks.

| Question | Finding at 9ca16b8 |
|---|---|
| One workflow per campaign? | **Yes.** One `PipelineRunWorkflow` execution per run directory: its id is a hash of OUT and the staged config's bytes (`workflow_id`), so re-running the same command attaches (`USE_EXISTING`) or resumes, and a campaign job is exactly one execution (the job id is the workflow id). |
| One activity per tier? | **No.** Four activity types: `pipeline_prepare` (monolithic: `build_plan`, i.e. structure, docking, paired poses, pocket, parity and every gate tier, plus writing `work.json`), `pipeline_score` (one per SCF leg and rung of the measurement tier: finer than a tier, deliberately), `pipeline_record_outcome`, and `pipeline_finalize` (monolithic: it **rebuilt the whole plan a second time** -- structure fetch, docking and gates again -- then the cut, every analysis, both records). An analysis that runs SCFs of its own (`[charge_sensitivity]` serially re-measures every pose) shared one activity, one timeout and one retry budget with the cut and the record. Only the RHF `field_interaction` tier is accepted. |
| Heartbeats? | **Partly.** Every long activity heartbeats (`_heartbeating`, every quarter of `heartbeat_timeout`), and `heartbeat_timeout` is set (`RunInput.heartbeat_s`, default 180 s), so a dead worker is noticed. But the details were the strings `"start"` / `"running"`: nothing says which leg, rung, stage or attempt is running, or for how long. |
| Retry policies? | **Partly right.** Scores: `maximum_attempts=3`, chemistry non-retryable (`ScfNotConverged`, `ClashRefused`), a changed config and a timeout non-retryable, a crashed child or worker retried. But prepare and finalize wrapped **every** exception as non-retryable (`PrepareFailed`, `FinalizeFailed`): a transient `OSError` (a network fetch of `[structure]`, a shared filesystem hiccup) failed the whole run at once, exactly like a config error. |
| Cancellation propagating to the running tier? | **No.** `TemporalJobBackend.cancel` **terminated** the workflow: no workflow code runs, nothing is cleaned up by the workflow, and a running score child was killed only when its next heartbeat was refused. Worse, prepare and finalize run `build_plan` / `finalize_sync` in a thread (`asyncio.to_thread`) that a cancel cannot stop: a finalize already running when the job was cancelled could still write `run.json` (with `overwrite=True`) **after** the backend had settled the job `cancelled` and written the cancelled record, leaving `state.json` and `run.json` disagreeing and a "finished" record rewritten. |
| Is the on-disk record authoritative? | **For success, yes; for failure and cancel, only once someone looks.** The complete core record is written by finalize, on disk. But the workflow never wrote `state.json`: a failed or cancelled job's terminal record and its state existed only after `status`/`cancel`/`result` (or nothing) observed the workflow. Until then the outcome and its reason lived only in Temporal's history, and after the namespace's retention expired the reason was lost ("the workflow is gone"). `stages_completed` was always null for a Temporal job. |
| Per-tier reuse by digest across retries? | **Within one run directory, per SCF.** A retried score attempt reuses an SCF line already in `calcs.jsonl` for its (leg, rung), and a rerun reads the whole log back (`prepare`). But a retried finalize rebuilt the plan from scratch and recomputed every analysis; and the workspace stage store (`smeltery.reuse`) is not used at all, so a resubmission into a new run directory recomputes every SCF (the local backend reuses them). |
| arm / ext | **Refused.** `RunInput` had no `arm`/`ext`, so `TemporalJobBackend.submit` and `smeltery run --temporal --arm/--ext-json` refused them (contract 5.C.3) rather than drop them; the backend's settle rebuilt the spec with `arm=None`. |

## Decisions

### A new workflow type, `CampaignRunWorkflow`; `PipelineRunWorkflow` frozen

The workflow's shape changes (new activities, a different order, cancellation handling), which is a
non-deterministic change for any execution already in flight. Versioning choice: **a new workflow name**, not
`workflow.patched()`. `PipelineRunWorkflow` keeps its code and its activities (`pipeline_prepare`, `pipeline_score`,
`pipeline_record_outcome`, `pipeline_finalize`, same names, same input dataclasses) and stays registered on every
worker, so an execution started before this change replays and finishes; nothing starts it any more, and it can be
deleted once no execution of it is open (after the namespace's retention). New starts (`smeltery run --temporal`,
`smeltery submit` on temporal) use `CampaignRunWorkflow`. Patching would have kept two shapes interleaved in one
function body for as long as either could be in flight; a second name keeps each readable and deletable.

The workflow id is unchanged (OUT + staged config), so `run --temporal` re-run against an OUT whose old workflow is
still open attaches to that execution and waits for it, and `submit` refuses it (`JobExistsError`), as before.

Input dataclasses change only additively: `RunInput` gains `arm` and `ext` (default `None`), and new dataclasses
(`PlanResult`, `StageInput`, `StageRef`, `SettleInput`) carry the new activities' arguments. Temporal's dataclass
converter fills a missing field from its default, so an old history's `RunInput` payload still decodes, and an old
`pipeline_finalize` call simply carries no arm.

### One activity per stage of the funnel

`smeltery.reuse` already names the stages of a run; the workflow now runs one activity per stage:

| Activity | Stage(s) | Retries | Heartbeat details |
|---|---|---|---|
| `campaign_begin` | `state.json` submitted -> running, at the worker's clock (campaign jobs only) | 5 attempts | -- (one write) |
| `campaign_plan` | the tiers before the measurement: structure, docking, paired poses, pocket, parity, gates | 3; deterministic errors non-retryable | `{stage: "plan", attempt, elapsed_s}` |
| `pipeline_score` x legs | the measurement tier, one activity per SCF leg and rung (unchanged) | 3; chemistry, timeout, changed config non-retryable | `{stage, candidate, pose, leg, field_model, rung, attempt, elapsed_s}` |
| `campaign_cut` | pairing + the cut, from the log | 3; deterministic errors non-retryable | `{stage: "cut", ...}` |
| `campaign_analysis` x analyses | one per active `analysis` (config order; the re-measuring ones' `summarize` after) | 3; deterministic errors non-retryable | `{stage: <key>, ...}` |
| `campaign_record` | the run record, pose files, calcs.jsonl, the core record (with arm/ext) | 3; deterministic errors non-retryable | `{stage: "record", ...}` |
| `campaign_settle` | `state.json` terminal + the failed/cancelled record (campaign jobs only) | 5 attempts | -- |

The tiers before the measurement are one activity, not one per tier: they are recomputed every run by design (their
output is geometry built deterministically from the inputs, digested as `plan_digest`, `smeltery.reuse`), each one
consumes the previous one's in-memory geometry, and splitting them would mean persisting and re-reading that geometry
between tiers to save, on a retry, only the tiers before the one that failed. The measurement tier stays one activity
per SCF leg, finer than one per tier: that is where the hours are and where the remedy ladder and the per-SCF log
live. Everything after the measurement is now one activity per stage.

**Checkpoints between stages.** The plan is built **once** (by `campaign_plan`), not again by finalize. Each stage
activity hands the next one its state as a content-addressed checkpoint, `OUT/temporal/stages/<sha256>.pkl` (pickle
of the plan, the record so far, the stage log), and returns only the sha256, which Temporal records in the workflow
history. The next activity loads exactly that file, refuses it if its bytes no longer hash to the recorded digest
(non-retryable `CheckpointRefused`), and refuses one written by another smeltery build (a different
`reuse.source_digest()`: a pickle is only as stable as the code that wrote it). A retried stage starts again from
the previous stage's recorded output, never from a half-done state, and never rebuilds the plan. The checkpoints are
deleted when the record is written (before the core record's manifest is taken) and when the job is settled.
Pickle is read only from the run's own directory and only at a digest the workflow history recorded.

### Retries: what is retried and what is not

`_stage_error` classifies an exception from a stage activity:

- **retried** (type `Transient`, up to the policy's attempts): `OSError` other than the deterministic ones
  (`FileNotFoundError`, `PermissionError`, `IsADirectoryError`, `NotADirectoryError`), and `MemoryError`: a network
  fetch, a shared filesystem, a worker short of memory. A worker that dies is retried by Temporal itself (heartbeat
  timeout) on another worker.
- **not retried** (types `PrepareFailed` / `StageFailed`, `non_retryable=True`): everything else -- `ConfigError`,
  `StageError` (a gate refusing), `ValueError` (a foreign log, a changed config, mixed builds), any bug. The same
  inputs fail the same way every time; retrying would only delay the failure.
- the frozen workflow's `pipeline_prepare` and `pipeline_finalize` classify the same way (a retry decision is the
  server's, not the workflow history's, so this is replay-safe for executions in flight).
- the measurement keeps its own rules: an SCF that does not converge is chemistry (the next rung, never a retry), a
  leg timeout is recorded and non-retryable, a crashed score child is retried (a crash cannot be told from an
  infrastructure fault) and after three attempts is an `error` outcome that a rerun reschedules.

### Cancellation propagates to the running tier

`TemporalJobBackend.cancel` now **requests cancellation** when the job is running (state `running`), waits up to
`heartbeat_s + 10` s (capped at 70 s) for the workflow to close, and only then terminates it (a job that never
started -- still `submitted` -- is terminated at once: there is nothing to propagate to). Every activity of
`CampaignRunWorkflow` is started with `ActivityCancellationType.WAIT_CANCELLATION_COMPLETED`, so the workflow waits
until the running tier has actually stopped (a score activity kills its child's process group before it reports
cancelled; the Python SDK delivers the cancel at the next heartbeat). The workflow then runs `campaign_settle`
(cancelled, with the stages completed so far) and ends `CANCELED`. A cancel is never recorded as an SCF `error`
outcome.

The thread a stage activity runs in still cannot be interrupted. What changed is that it can no longer rewrite the
record: `campaign_record` (and the frozen `pipeline_finalize`) takes the job's state lock, refuses (non-retryable
`JobSettled`) when `state.json` is already terminal, and writes the core record **under that lock**, so a settle and
the record cannot interleave; it also checks before writing the legacy run record and pose files.

### The record on disk stays the artifact of record

The workflow writes the outcome to disk itself, through activities, instead of leaving it in Temporal until someone
looks: `campaign_begin` moves `state.json` to running, `campaign_settle` settles it complete / failed (with the
failing stage and its root cause) / cancelled and, for failed and cancelled, writes the schema's minimal record
(`jobs.settle`, so the same exactly-once rule and the same `ext["smeltery.job"]`). The run logs its completed stages
to `OUT/stages.partial.jsonl` as the local backend does, so a Temporal job's failed or cancelled record now lists
`stages_completed` (it was always null). Temporal's history is the durable *scheduling* state (which activity ran,
its result digest, its retries); no outcome exists only there. The observer path in the backend is unchanged and
stays the fallback (a terminated workflow, a workflow of the old type); settle is exactly-once, so the two never
disagree.

### arm and ext

`RunInput.arm` / `RunInput.ext` reach the record activity (and the frozen `pipeline_finalize`), which passes them to
`cli.emit_core_record`, so the complete core record states them at its FIRST write, exactly as the local backend's
`smeltery run --arm/--ext-json`. The backend also writes them to `RUN_DIR/temporal/job.json` at submit, so whoever
settles the job later (the workflow, or the backend observing it) rebuilds the job's spec with its arm and ext and a
failed or cancelled record carries the arm too. The refusals in `TemporalJobBackend.submit` and in
`smeltery run --temporal --arm/--ext-json` are removed; `check_spec` still validates both.

## Tests

`tests/test_temporal_campaign.py` (needs a server; see `tests/temporal_server.py`): per-stage activities in the
workflow history, heartbeat details on a running leg, a transient failure of a stage retried without rebuilding the
plan or re-running any SCF, a non-retryable stage failure settling the job failed with its terminal record and its
completed stages, cancellation killing the running leg and ending the workflow `CANCELED` with a cancelled record,
and the frozen `PipelineRunWorkflow` still completing (and carrying an arm); without a server, the checkpoint refusals,
the retry classification and the record stage's refusal of a settled job. `tests/test_job_backends.py`: the arm/ext
case now expects "carried" on both backends. `tests/test_settings_services.py`: `run --temporal --arm --ext-json`
reaches the workflow input. CI's new `temporal` job runs these against `WorkflowEnvironment.start_local()` with
`SMELTERY_TEST_TEMPORAL_REQUIRE=1`, so they cannot pass by skipping.

The time-skipping test server is not used: the tests' score children run, hang and are killed in wall-clock time in
their own processes, which time skipping cannot shorten, and it needs a second downloaded binary.

## Left open

- **Other measurement tiers.** `--temporal` still scores the RHF `field_interaction` tier only (a DFT or hook tier is
  refused). With the plan checkpoint in place a generic tier could run as one heartbeating activity with the per-SCF
  checkpoint (`smeltery.checkpoint`) for resume; not built here.
- **The workspace stage store.** The Temporal backend still does not read or write `ROOT/.smeltery/stages`: a
  resubmission into a new run directory recomputes every SCF. Reusing a stored measurement would have to reconcile the
  Temporal log's per-leg rows, pose drops and extra field models with the stored per-pose values; not built here.
- **Uninterruptible threads.** A cancelled stage activity's thread runs to completion in the worker (it can no
  longer write the record, but it spends the CPU). Running plan/cut/analysis in a child process, as scores are, would
  fix that.
