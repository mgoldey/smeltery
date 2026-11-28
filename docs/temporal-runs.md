# Durable runs: `smeltery run --temporal`

A long pipeline (hours of SCFs) can run as a Temporal workflow instead of one process. The science is the
ordinary run's: the same plan, tier settings, pairing, cut and record. What Temporal adds is durability around each
SCF: a log written as each SCF finishes, a timeout per SCF, and resume after a crash or a stop. It needs the
`temporal` extra (`temporalio`) and applies to the RHF field-interaction tier only (any other tier is refused:
run it without `--temporal`).

## `smeltery serve` and `smeltery worker`

Two subcommands run a deployment; both read `SMELTERY_*` environment variables, and a flag overrides its variable
(flag, then variable, then default).

- **`smeltery serve`** is Bench: the repo's one server (the FastAPI app and the built UI), the same app as
  `smeltery bench`, and it accepts every `smeltery bench` flag (`ROOT`, `--token`, `--demo`, `--allow-host`...).
  What it adds is the backend: with `SMELTERY_BACKEND=local` (the default) Bench queues tasks in SQLite and runs each
  under a detached subprocess supervisor, and needs no server; with `SMELTERY_BACKEND=temporal` it submits them to
  the Temporal cluster instead (`TemporalTaskQueue`, which does not enforce `timeout_s` or a memory limit).
- **`smeltery worker`** starts the worker for the same backend. `local`: a supervisor loop over the workspace's
  SQLite queue that fails tasks whose supervisor died and starts queued ones, so queued work starts even when no
  server is up (tasks run under their own supervisors, so stopping the loop never kills one); it also settles
  campaign jobs (`smeltery submit`, [jobs.md](jobs.md)) whose process ended, writing their terminal record. `temporal`: ONE process
  polling both queues, `smeltery-tasks` (Bench's shell tasks) and `smeltery-pipeline` (the SCF legs of
  `run --temporal`), over one connection to the cluster. One process because both are the same deployment's work
  and one container is simpler to run; the two kinds of work do not share slots (`SMELTERY_TASK_CONCURRENCY` bounds
  shell tasks, `SMELTERY_WORKERS` bounds SCFs). To scale them apart, run `task-worker` and `temporal-worker`.

```sh
SMELTERY_BACKEND=temporal SMELTERY_WORKERS=6 smeltery worker
SMELTERY_BACKEND=temporal smeltery serve --demo
```

`smeltery submit CONFIG --out RUN_DIR` submits a whole run as a campaign job on the same backend and returns at once;
`smeltery status / cancel / result RUN_DIR` follow it. See [jobs.md](jobs.md).

### Environment variables

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `SMELTERY_BACKEND` | `local` | `serve`, `worker`, `submit` | `local` (SQLite + subprocess supervisors, no server) or `temporal`. Anything else is refused. |
| `SMELTERY_TEMPORAL_ADDRESS` | `localhost:7233` | all Temporal commands | The Temporal frontend, `host:port`. Flag: `--host` (`task-worker`, `temporal-worker`, `run`), `--temporal-address` (`serve`, `worker`). |
| `SMELTERY_TEMPORAL_NAMESPACE` | `default` | all Temporal commands | The Temporal namespace. Flag: `--namespace`. |
| `SMELTERY_TASK_QUEUE` | `smeltery-tasks` | `serve`, `worker`, `task-worker` | Queue for Bench's shell tasks. Flag: `--task-queue`. |
| `SMELTERY_PIPELINE_QUEUE` | `smeltery-pipeline` | `worker`, `temporal-worker`, `run --temporal` | Queue for the SCF legs of `run --temporal`. Flag: `--pipeline-queue` (`worker`), `--task-queue` (`temporal-worker`, `run`). |
| `SMELTERY_HOST` | Bench's: `127.0.0.1` | `serve` | Address to bind. Unset, `SMELTERY_BENCH_HOST` applies. Bench still refuses a non-loopback bind without a token. |
| `SMELTERY_PORT` | Bench's: `8765` | `serve` | Port, 1-65535. Unset, `SMELTERY_BENCH_PORT` applies. |
| `SMELTERY_ROOT` | Bench's per-user data directory | `serve`, `worker` (local) | Workspace directory; a `ROOT` argument wins, and unset, `SMELTERY_BENCH_ROOT` applies. |
| `SMELTERY_WORKERS` | `2` | `worker`, `temporal-worker`, `run --temporal` | SCFs run at once (memory-bound). Flag: `--workers`. |
| `SMELTERY_THREADS_PER_WORKER` | `1` | `worker`, `temporal-worker` | `RAYON_NUM_THREADS` per SCF child. Flag: `--threads-per-worker`. |
| `SMELTERY_TASK_CONCURRENCY` | `2` | `worker`, `task-worker` | Concurrent shell tasks per task worker. Flag: `--concurrency`. |
| `SMELTERY_MAX_RUNS` | `1` | `serve`, `worker` (local) | Launched runs at once on the local backend. Flag: `--max-runs`. |
| `SMELTERY_MAX_CPU_HOURS` | unset (no ceiling) | `mcp` | Ceiling on a screen's predicted CPU-hours; above it, submission needs `confirm=True`. A screen whose CPU time cannot be estimated counts as over it. See [mcp.md](mcp.md). Flag: `--max-cpu-hours`. |
| `SMELTERY_MAX_CANDIDATES` | unset (no ceiling) | `mcp` | Ceiling on a screen's candidate count; above it, submission needs `confirm=True`. See [mcp.md](mcp.md). Flag: `--max-candidates`. |

A bad value stops the command with exit code 2 and a message naming the variable (for example
`SMELTERY_BACKEND='redis' is not a backend; use one of: local, temporal`). The other `SMELTERY_BENCH_*` variables
(token, logging, read-only) belong to Bench and are documented in [bench.md](bench.md). The old commands and flags
(`task-worker`, `temporal-worker`, `run --temporal --host/--task-queue`) still work, and now also read these variables.

## Three things must be running

1. **The Temporal dev server.** `--temporal` never falls back to an in-process run. With Docker Compose:

   ```sh
   docker compose --profile temporal up -d temporal
   ```

   The `temporal` profile in `compose.yaml` is opt-in (a plain `docker compose up` is unchanged): it starts
   `temporalio/temporal` as a dev server (frontend and UI on loopback, history in a volume) and a `worker` service
   running `smeltery worker` with `SMELTERY_BACKEND=temporal`; `docker compose --profile temporal up --build` starts
   both. ferric (the SCF engine) comes in as smeltery's core dependency, from PyPI; the image has not been
   built and run against a live cluster yet, so if a pipeline leg fails there, run `smeltery temporal-worker`
   on the host against `localhost:7233` instead. Without Compose:

   ```sh
   docker run -d --name temporal-dev -p 7233:7233 -p 8233:8233 temporalio/temporal:latest server start-dev --ip 0.0.0.0
   ```

2. **A pipeline worker**, which executes the SCFs:

   ```sh
   smeltery temporal-worker --workers 6 --threads-per-worker 1
   ```

   `--workers` is how many SCFs run at once; each SCF leg is its own child process (its own process group), so a
   cancel or a timeout kills exactly that SCF. `--threads-per-worker` sets `RAYON_NUM_THREADS` for each child
   (default 1). Workers are memory-bound: check free memory before raising `--workers`. Defaults: `--workers 2`,
   host `localhost:7233`, task queue `smeltery-pipeline`. (`smeltery worker` with `SMELTERY_BACKEND=temporal` does
   this and the tasks worker in one process.)

3. **The run itself**, which starts the workflow (or attaches to it) and waits:

   ```sh
   smeltery run pipeline.toml --out OUT --temporal --workers 6
   ```

   `--temporal` needs `--out`. `--workers` here is "scores run at once" for this workflow. `--host`, `--task-queue`
   and `--namespace` must match the worker's.

Watch it in the Temporal UI at <http://127.0.0.1:8233>.

### Two different workers

| Worker | Command | Task queue | Runs |
|---|---|---|---|
| `task-worker` | `smeltery task-worker` (or `smeltery worker`, which runs both) | `smeltery-tasks` | Bench's shell tasks (a submitted command line) |
| `temporal-worker` | `smeltery temporal-worker` (or `smeltery worker`) | `smeltery-pipeline` | the SCF legs of one `run --temporal` |

When Bench is configured with the Temporal task queue (`TemporalTaskQueue`), launching a pipeline submits
`smeltery run ... --temporal` as a task. That needs both workers: `task-worker` to run the `smeltery run` command,
and `temporal-worker` to run the SCFs that command schedules. (`smeltery bench` as shipped builds the subprocess
task queue, which needs neither.)

## One workflow per campaign, one activity per stage

A run is one `CampaignRunWorkflow` execution (one per OUT and staged config: re-running the same command attaches to
it or resumes it, and a campaign job is exactly one). The workflow holds control flow only; every stage of the run is
its own activity, with its own timeout, heartbeat and retries:

| Activity | What it does | Retried |
|---|---|---|
| `campaign_begin` | a campaign job's `state.json` -> `running` | up to 5 attempts |
| `campaign_plan` | the tiers before the measurement (structure, docking, paired poses, pocket, parity, gates): the plan, built once | transient errors only, 3 attempts |
| `pipeline_score` (one per SCF leg and rung) | the measurement tier ([below](#what-one-unit-of-work-is)) | a crashed child or worker, 3 attempts; never chemistry or a timeout |
| `campaign_cut` | pairing and the cut, from `calcs.jsonl` | transient errors only, 3 attempts |
| `campaign_analysis` (one per analysis) | each `analysis` section, in config order | transient errors only, 3 attempts |
| `campaign_record` | the run record, pose files, `calcs.jsonl`, the core record (with `--arm` / `--ext-json`) | transient errors only, 3 attempts |
| `campaign_settle` | a campaign job's `state.json` -> complete / failed / cancelled, and its failed or cancelled record | up to 5 attempts |

**Checkpoints.** Each stage after the plan starts from the previous stage's output, a content-addressed file
`OUT/temporal/stages/<sha256>.pkl` whose digest the workflow history recorded: a retried stage starts again from
exactly that, so it never rebuilds the plan or re-runs a finished stage, and a file whose bytes changed, or that
another smeltery build wrote, is refused (exit 3; re-running the command resumes). They are removed when the record is
written.

**Retries.** A stage's `OSError` (a network fetch, a shared filesystem; not a missing or unreadable input) and
`MemoryError` are retried; anything else -- a config error, a gate refusing, a changed config, a log of another run,
a bug -- fails the run at once, because the same inputs fail the same way every time. A worker that dies is noticed
within the heartbeat timeout and its activity runs again on another worker.

**Heartbeats.** Every long activity heartbeats every quarter of `heartbeat_s` (default 180 s) with what it is running:
`{stage, attempt, phase, elapsed_s}`, plus `candidate, pose, leg, field_model, rung` for an SCF leg. `temporal workflow
describe --workflow-id ...` (or the UI's pending activities) shows them.

**Cancellation.** Cancelling the workflow (`smeltery cancel` on a campaign job, or `temporal workflow cancel`) reaches
the running activity at its next heartbeat; a score activity kills its child's process group before it reports
cancelled, and the workflow waits for that, then settles the job `cancelled` and ends `CANCELED`. A cancel is never
recorded as an SCF outcome. A stage that runs in the worker's own thread (plan, cut, an analysis) cannot be stopped
mid-way, but it can no longer write the record: the record stage writes under the job's state lock and refuses a job
already settled.

**The record on disk.** For a campaign job the workflow writes the outcome itself (`campaign_begin`,
`campaign_settle`), so `state.json` and `run.json` say how the run ended whether or not anyone runs `smeltery status`;
Temporal's history is the scheduling state. Completed stages are logged in `OUT/stages.partial.jsonl` as the run
goes, so a failed or cancelled job's record lists them.

**Versions.** `PipelineRunWorkflow`, the single-finalize workflow of earlier releases, stays registered on every
worker (its activities `pipeline_prepare` / `pipeline_finalize` too), so an execution started before the change
finishes; nothing starts one any more. The design and its audit:
[2026-11-20-temporal-per-tier-design.md](superpowers/specs/2026-11-20-temporal-per-tier-design.md).

## What one unit of work is

The unit is one SCF leg: (candidate, pose, leg, field model). A field leg exists once per field model: the plan's
own pocket field and every extra field an active analysis asks for (for example
[`[charge_sensitivity]`](methods.md)). **The vacuum leg is shared across field models**: it is computed once per
(candidate, pose), and its `calcs.jsonl` lines have `field_model: null`.

## Settings

```toml
[run]
scf_timeout_s = 14400          # default 4 h

[field_interaction]
remedies = [
  { level_shift = 0.5 },
  { max_iter = 300, soscf = true },
]
```

**`[run] scf_timeout_s`** (seconds, > 0) bounds one SCF leg (one rung). It is not an input, so changing it does not
move the input digest and a run can be resumed with a longer one.

**`[field_interaction] remedies`** is a ladder tried, in order, when an SCF does not converge. Rung 0 is the base
`[field_interaction]` settings; rung k is rung 0 with `remedies[k-1]` applied on top. Each leg (vacuum, field)
climbs on its own, and every `calcs.jsonl` line carries its rung. Each entry must be a non-empty table.

| Key | Type | Notes |
|---|---|---|
| `max_iter` | int | >= 1 |
| `diis` | string | one of `pulay`, `adiis`, `ediis` |
| `diis_size` | int | >= 1 |
| `level_shift` | float | >= 0 |
| `soscf` | bool | |
| `guess` | string | one of `minao`, `hcore` (the initial guess) |

Refused, because each changes the quantity measured, so a value from that rung would not be the same measurement
as one from rung 0: `energy_conv` (changes the precision of the reported energy), `density_conv` (same), `smearing`
(fractional occupations change the state whose energy is reported). Any other key is an unknown override and is
refused. Empty `remedies` (the default) is the tier without a ladder. At most 5 rungs. A plain run (no
`--temporal`) takes the same ladder and, with `[field_interaction] on_scf_failure = "drop"`, the same pairwise drop;
see [troubleshooting](troubleshooting.md#an-scf-does-not-converge-a-pose-is-dropped-or-the-run-stops). `[cut]
min_pairs` applies here too: after a drop an analogue with fewer pairs is not ranked (default 3).

## Resume

Re-run the same command. `OUT/calcs.jsonl` is appended (and fsync'd) as each SCF completes; on start the log is read
back. A converged leg, or a leg that failed every rung, is never recomputed. A leg that ended with an infrastructure
failure or a timeout is scheduled again. The staged `OUT/config.toml` is fixed while the run is live: activities
refuse a changed file, and `smeltery run --out OUT` refuses to re-stage over a running workflow (wait, terminate the
workflow with `temporal workflow terminate --workflow-id ...`, or use another `--out`). A log line of another input
digest is refused: resuming into inputs that differ would mix two measurements.

## Failed SCFs: pairwise drops

An SCF that fails every rung of the ladder is a chemistry outcome, not retried, and the pose is dropped pairwise:

- a pose whose score failed for the parent is dropped for every analogue;
- a pose that failed for an analogue is dropped for that analogue, and the parent's value at that pose is not in its
  ddE.

Every drop is in `results.dropped_poses` (candidate, pose, leg, field_model, reason, outcome, rungs_tried); the kept
original pose indices per candidate are in `results.pose_index`. The run prints a `dropped:` line for each. An
extra field model's failed score is `None` in what its analysis's `summarize` receives.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | done; the record path and input digest are printed |
| 2 | the Temporal server is unreachable (the message includes the docker and compose commands above; re-running the same command resumes), or `temporalio` is not installed, or a bad config |
| 3 | the workflow failed. Notably, the cut refuses while any leg has an infrastructure error or timed out: an error is not a measurement. For timeouts the remedy is to raise `[run] scf_timeout_s` (it does not change the digest) and run the same command again |

## Flags that do not combine with it

`--temporal` runs the whole config: it refuses `--only-candidate` and `--combine`. `--arm` and `--ext-json` combine
with it: they reach the core record at its first write, as in a plain run. Separately, an analysis that
re-measures every pose under other fields (`[charge_sensitivity]`) refuses `--only-candidate` and `--combine` even
without `--temporal` (a partial measures one field only, and a combine has no SCFs to re-measure with). `--plan`
works: it stops before the SCFs and reports the poses, pocket, parity, digest and the SCF count, which under
`--temporal` is one shared vacuum leg per pose plus one field leg per model.

## What the run leaves

The ordinary run record, plus `OUT/calcs.jsonl` (see [bench.md](bench.md#provenance-and-lineage)) and `OUT/temporal/`
(`work.json`, `running.json` while the workflow is live, `stages/` until the record is written, and for a campaign job
`job.json`, its arm and ext: the scheduling state). The danuglipron-halogen prebuilt pipeline is a worked example
(`src/smeltery/prebuilt/danuglipron-halogen/pipeline.toml`).
