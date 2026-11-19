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
  server is up (tasks run under their own supervisors, so stopping the loop never kills one). `temporal`: ONE process
  polling both queues, `smeltery-tasks` (Bench's shell tasks) and `smeltery-pipeline` (the SCF legs of
  `run --temporal`), over one connection to the cluster. One process because both are the same deployment's work
  and one container is simpler to run; the two kinds of work do not share slots (`SMELTERY_TASK_CONCURRENCY` bounds
  shell tasks, `SMELTERY_WORKERS` bounds SCFs). To scale them apart, run `task-worker` and `temporal-worker`.

```sh
SMELTERY_BACKEND=temporal SMELTERY_WORKERS=6 smeltery worker
SMELTERY_BACKEND=temporal smeltery serve --demo
```

### Environment variables

| Variable | Default | Used by | Meaning |
|---|---|---|---|
| `SMELTERY_BACKEND` | `local` | `serve`, `worker` | `local` (SQLite + subprocess supervisors, no server) or `temporal`. Anything else is refused. |
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
   both. The worker image has no ferric (the SCF engine), so for `run --temporal` pipelines either run
   `smeltery temporal-worker` on the host against `localhost:7233`, or extend the image. Without Compose:

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

Refused, because each changes the quantity measured, so a value from that rung would not be the same measurement
as one from rung 0: `energy_conv` (changes the precision of the reported energy), `density_conv` (same), `smearing`
(fractional occupations change the state whose energy is reported). Any other key is an unknown override and is
refused. Empty `remedies` (the default) is the tier without a ladder.

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
| 3 | the workflow failed. Notably, finalize refuses while any leg has an infrastructure error or timed out: an error is not a measurement. For timeouts the remedy is to raise `[run] scf_timeout_s` (it does not change the digest) and run the same command again |

## Flags that do not combine with it

`--temporal` runs the whole config: it refuses `--only-candidate` and `--combine`. Separately, an analysis that
re-measures every pose under other fields (`[charge_sensitivity]`) refuses `--only-candidate` and `--combine` even
without `--temporal` (a partial measures one field only, and a combine has no SCFs to re-measure with). `--plan`
works: it stops before the SCFs and reports the poses, pocket, parity, digest and the SCF count, which under
`--temporal` is one shared vacuum leg per pose plus one field leg per model.

## What the run leaves

The ordinary run record, plus `OUT/calcs.jsonl` (see [bench.md](bench.md#provenance-and-lineage)) and `OUT/temporal/`
(`work.json`, `running.json`: the scheduling state). The danuglipron-halogen prebuilt pipeline is a worked example
(`src/smeltery/prebuilt/danuglipron-halogen/pipeline.toml`).
