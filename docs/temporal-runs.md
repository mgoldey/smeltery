# Durable runs: `smeltery run --temporal`

A long pipeline (hours of SCFs) can run as a Temporal workflow instead of one process. The science is the
ordinary run's: the same plan, tier settings, pairing, cut and record. What Temporal adds is durability around each
SCF: a log written as each SCF finishes, a timeout per SCF, and resume after a crash or a stop. It needs the
`temporal` extra (`temporalio`) and applies to the RHF field-interaction tier only (any other tier is refused:
run it without `--temporal`).

## Three things must be running

1. **The Temporal dev server.** `--temporal` never falls back to an in-process run.

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
   host `127.0.0.1:7233`, task queue `smeltery-pipeline`.

3. **The run itself**, which starts the workflow (or attaches to it) and waits:

   ```sh
   smeltery run pipeline.toml --out OUT --temporal --workers 6
   ```

   `--temporal` needs `--out`. `--workers` here is "scores run at once" for this workflow. `--host` and
   `--task-queue` must match the worker's.

Watch it in the Temporal UI at <http://127.0.0.1:8233>.

### Two different workers

| Worker | Command | Task queue | Runs |
|---|---|---|---|
| `task-worker` | `smeltery task-worker` | `smeltery-tasks` | Bench's shell tasks (a submitted command line) |
| `temporal-worker` | `smeltery temporal-worker` | `smeltery-pipeline` | the SCF legs of one `run --temporal` |

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
| 2 | the Temporal server is unreachable (the message includes the docker command above; re-running the same command resumes), or `temporalio` is not installed, or a bad config |
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
