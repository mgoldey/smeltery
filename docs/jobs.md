# Campaign jobs: `smeltery submit / status / cancel / result`

A campaign job is one `smeltery run CONFIG --out RUN_DIR`, submitted and left to run. `submit` returns at once; the
other three commands look at the job later, from any shell. It implements attestation-schema's job-backend contract
(`attestation_schema.jobs.JobBackend`: `submit`, `status`, `cancel`, `result`) in `smeltery.jobs`, and needs the
`attestation` extra.

```sh
smeltery submit pipeline.toml --out runs/r1      # stages the config into runs/r1, prints the job id, returns
smeltery status runs/r1                           # submitted | running | complete | failed | cancelled, when, why
smeltery cancel runs/r1                           # idempotent: a finished job's status comes back unchanged
smeltery result runs/r1                           # the record's status and path: exit 0 complete, 1 failed or
                                                  # cancelled, 3 not finished yet
```

`status`, `cancel` and `result` take the run directory: its `attestation/state.json` names the backend and the job.
`--json` prints state.json (`StateFile.to_dict()`). A refusal (a run directory that already holds a run, a bad
setting) exits 2.

**POSIX only.** `state.json` is written under `flock`, which Windows does not have: there every state write (submit,
transition, settle) is refused with `StateLockUnavailableError` and nothing is written; reading a `state.json` works.

## Backends

`SMELTERY_BACKEND` (or `--backend` on `submit`) picks one; both take the settings in
[temporal-runs.md](temporal-runs.md#environment-variables).

| | `local` (default) | `temporal` |
|---|---|---|
| Needs | nothing beyond the `attestation` extra: no server | the `temporal` extra and a Temporal server |
| A job is | a task on Bench's SQLite queue (`ROOT/.smeltery/bench/tasks`) running `python -m smeltery.jobs _run`, under its own detached supervisor | a `PipelineRunWorkflow` execution (the one `run --temporal` runs); the job id is the workflow id |
| Runs it | the task's supervisor, started at submit when a run slot is free | `smeltery worker` with `SMELTERY_BACKEND=temporal` (its pipeline queue) |
| Concurrency | `SMELTERY_MAX_RUNS`, shared with Bench's launched runs: a job is a capped task, and shows in Bench's task panel | `SMELTERY_WORKERS` SCFs at once in the workflow |
| Tiers | any `smeltery run` config | the RHF field-interaction tier only, as `run --temporal` |
| Job metadata | `ROOT/.smeltery/jobs/jobs.sqlite` (`ROOT`: `--root`, `SMELTERY_ROOT`, else Bench's default) | the Temporal server |
| A crashed worker | the job ends `failed` (its supervisor died, or its process was killed) | is retried on the next worker; the job does not fail |
| `cancel` | marks the task cancelled and ends its process group (SIGTERM, SIGKILL after 2 s) | terminates the workflow (no worker needed); running SCF children are killed when their heartbeat is refused |

The local job's runner moves the job to `running`, runs `smeltery run RUN_DIR/config.toml --out RUN_DIR` in its own
process, and settles it. What it cannot settle (a cancel, a kill, a supervisor that died) is settled by whoever looks
next: `status`, `cancel`, `result`, or `smeltery worker` (local), which settles every job whose process ended every
few seconds, so a crashed job gets its record even when nobody asks. A Temporal job is settled when it is looked at
(`status`, `cancel`, `result`): the workflow does not write state.json, so it may lag the job and `run.json` is
authoritative (the contract's own text since attestation `9fb7eed`). Settling passes the workflow's own times, when a
worker first took it (`started_at`) and when it closed (`ended_at`), so the terminal entry and a backend-written
record's `finished_at` are the workflow's end, not the time of the look.

## The run directory

```
RUN_DIR/config.toml, inputs/        the campaign config, staged at submit; relative paths resolve against RUN_DIR
RUN_DIR/run-<digest12>.json ...     what `smeltery run --out` leaves (docs/gui-contract.md)
RUN_DIR/attestation/state.json      the job's state (below), owned by the backend
RUN_DIR/attestation/run.json        the core record, at every terminal state
RUN_DIR/attestation/digests.json    the manifest the record binds
```

`submit` refuses (JobExistsError) a run directory holding `state.json` or `run.json`: resubmission is a new run
directory. In Python, `smeltery.jobs.campaign_spec(config, run_dir)` stages a config and returns the `JobSpec`, whose
`config` is the staged campaign config, `family` its `campaign`, `arm` None and `ext` empty (anything else is refused
by `check_spec`: `smeltery run` has no way to carry a caller's `ext`, another family or an arm into the record it
writes, and the contract says a backend that cannot hand them over refuses the spec).

### run_id

The submitter owns it (`--run-id` on `submit`, `JobSpec.run_id`; default a fresh 12-hex name): the handle,
`state.json` and the core record in every terminal state carry the same one. The runner hands it to the run
(`smeltery run --run-id=<id>`; on Temporal, `RunInput.run_id`, which the finalize activity uses), and `settle` ends a
job `failed` if the record in its run directory names another run_id, family or config. The input digest is in the
record's `ext.smeltery.input_digest`.

The default is not `input_digest[:12]`, which would make two jobs of identical inputs share a run_id (re-executions,
in the schema's terms). That digest covers the plan, not the config: the floor a tier measures, the point charges the
pocket or a field builder yields, the docked poses' settings and every tier's `settings()` (tool versions the tiers
probe, such as `xtb --version`). Computing it at submit means `build_plan` in the submitting process: structure
preparation (a network fetch), docking, the pocket, the gates and those probes, i.e. most of a run's non-SCF work,
done twice, and done on the submitting machine, whose tool versions may differ from the worker's, so the digest at
submit could disagree with the one the run records. A caller that wants re-executions grouped passes the same
`--run-id`.

### `state.json`

The spec's `attestation.state/1`, **owned by `attestation_schema.state` since attestation `9fb7eed`**: its reader
(`read_state`, returning a frozen `StateFile`), writer (`init_state`, `transition`, `settle`), lock (`state_lock`) and
the minimal terminal record (`terminal_record`) are the schema package's, and `smeltery.jobs` only calls them. The
format did not change when they moved. JCS-serialized (RFC 8785, no trailing newline), replaced atomically (temporary
file, fsync, `os.replace`) under an exclusive `flock` on the `attestation/` directory:

```json
{
  "backend": "local",
  "history": [
    {"at": "2026-11-19T15:37:19.777219Z", "state": "submitted"},
    {"at": "2026-11-19T15:37:20.124245Z", "state": "running"},
    {"at": "2026-11-19T15:37:20.619766Z", "state": "complete"}
  ],
  "job_id": "817549d48ac9",
  "message": null,
  "run_id": "07693d07e8b9",
  "schema": "attestation.state/1",
  "state": "complete",
  "updated_at": "2026-11-19T15:37:20.619766Z"
}
```

Exactly these keys. `history` starts at `submitted`, is append-only, every adjacent pair is an allowed transition
(`attestation_schema.can_transition`; an illegal one is refused and writes nothing), its times never go backwards (a
Temporal server's clock is clamped to the last entry), and its last entry is `state` at `updated_at`. `message` says
why the job ended when it did not complete (the run's last error line, "supervisor lost: ...", "the workflow was
terminated", or that the manifest was refused for a symlink); it lives here only, not in the record. The file is kept
after the run ends. `read_state` refuses a file that breaks any of this (`StateFormatError`, a `ValueError`) rather
than guessing; `transition` refuses an illegal or terminal move (`IllegalTransition`, the schema's
`IllegalTransitionError`): ending a job is `settle`'s, which also guarantees its record.

### Terminal records

Every terminal record carries the job's `run_id`, `family`, `arm` (null) and `config` (the campaign config the job
was given), whoever wrote it, and its `status` is never null.

- **complete**: the record `smeltery run` writes (`smeltery.core_record`; see [core-record.md](core-record.md)), given
  the job's run_id; smeltery's derived `inputs` and the input digest are in `ext.smeltery`, with the reuse provenance
  (`ext.smeltery.reuse`) when stages were reused.
- **failed / cancelled**: the schema's minimal record (`attestation_schema.state.terminal_record`, written by
  `settle`): the job's identity and config, `started_at` (when it began running, null if it never did), `finished_at`
  (the same instant as the terminal `state.json` entry), the manifest of whatever it left (none, if a symlink refused
  it; the message says so), no metrics, `history`, `seeds` and `dependencies` null (the writer did not observe the
  run's environment), and `ext["smeltery.job"]`: `backend`, `job_id`, `stages_completed`. Producer `smeltery jobs`;
  `code` is smeltery's identity, and when the backend wrote it rather than the run, it is at most `inferred` and
  `evidence` says so.
- A record already in the run directory decides the terminal state: a cancel that races completion ends `complete`,
  so `state.json` and `run.json` never disagree. A run that exited 0 without writing its record ends `failed`, and so
  does one whose record is not the job's (another run_id, family or config).
- `ext["smeltery.job"]["stages_completed"]`: the stages the run had completed when it stopped, in order, each with
  its digest and whether it was reused (read from `RUN_DIR/stages.partial.jsonl`, below), or null when the run left
  no stage log (a Temporal job: the workflow keeps none).

## Resumability: resubmitting reuses completed stages

A local job runs `smeltery run ... --stage-cache ROOT/.smeltery/stages`: resubmitting an identical campaign (into a
new run directory, as every submission is) reuses every stage an earlier job in the workspace completed, and
recomputes the rest. The full design is in `smeltery.reuse`'s docstring; in short:

| Stage | Reused? | Why |
|---|---|---|
| structure, docking, poses, pocket, parity, gates | no: recomputed every run | their output is geometry and files built deterministically from the inputs; it is digested (`plan_digest`) and is the measurement's upstream |
| the measurement tier (`field_interaction`, `field_dft_interaction`) | yes | the SCFs: where the time goes |
| the cut | no: recomputed | arithmetic on the measurement, cheaper than a lookup; digested |
| an analysis | yes, unless it builds fields with an outside program | `[charge_sensitivity]` runs pdb2pqr, whose build smeltery cannot verify |

**Digest.** A stage's key is the sha256 of its name, its upstream (the measurement: the digest of every candidate's
exact poses, the per-pose values recorded so far, the point charges and their provenance; an analysis: everything it
can read, the record's inputs, tiers and results so far, with timings and file paths dropped), its settings (the
tier's `settings()`, or the analysis's section), and the code identity: a sha256 of smeltery's own source files,
Python and numpy versions, and ferric's build. Changing one setting therefore invalidates exactly that stage and every
stage after it.

**What counts as identical.** Conservative: stages are reused or stored only when ferric's identity is VERIFIED
(`ferric.__build__`, a git install, or an index release) and clean. A dirty ferric build, or one only inferred from a
checkout, is never reused and never stored (the record says why: `results.reuse.why_not`). Different ferric builds
never share a stage. smeltery is identified by the bytes of its source, so an edited checkout is reusable exactly
when its source is byte-identical.

**The store** is per workspace and content-addressed, `ROOT/.smeltery/stages/<k[:2]>/<k>.json`, rather than a search
of earlier run directories: those live anywhere, can be moved, edited or deleted, and keep no per-stage output in a
form that can be put back. Each entry holds its key document, the output and the output's sha256, and where it came
from. Writes are whole-file-then-hard-link (the first complete entry of a key wins, so concurrent resubmissions never
corrupt it); every read re-derives both digests, and an entry that fails is set aside (`*.corrupt-*`), reported and
recomputed, never trusted. `smeltery run --out OUT --stage-cache DIR` (or `SMELTERY_STAGE_CACHE`) uses a store
directly; a plain `smeltery run` without either neither reads nor writes one. The Temporal backend does not use the
store (its workflow resumes SCF by SCF within one run directory instead).

**Recorded honestly.** With a store, the run record carries `results.reuse` (in the core record:
`ext.smeltery.reuse`; per stage in `steps/NN-<stage>/stage.json` under `reuse`): per stage its digest, upstream and
output digests, `reused`, and `reused_from` (the run_id, its run directory, the entry's digest and time). A reused
stage's output is the stored one, so the run record, `calcs.jsonl` and the pose files are byte-identical to the run
that computed it except for that provenance, the timestamps and the wall-clock seconds (a reused measurement is
never timed: its funnel row says `seconds: null`). Core metrics are unchanged.
`tests/test_stage_reuse.py` holds this to account.

**With the per-SCF checkpoint.** Two layers: the stage store is consulted first, for a whole completed stage across
runs; on a miss the measurement runs with `OUT/calcs.partial.jsonl` (`smeltery.checkpoint`), which resumes an
interrupted measurement of the same OUT leg by leg; once it completes it is stored. A crash mid-measurement costs only
its unfinished SCFs on a rerun into the same OUT; a resubmission after the measurement finished costs none.

## Cancellation

`smeltery cancel RUN_DIR` (or `cancel` on the backend):

- **local**: the task is marked cancelled and its process group gets SIGTERM, then SIGKILL after a grace. `smeltery
  run` runs inside the runner's own process, in that group, so the signal ends the run itself (and any SCF in it).
- **temporal**: the workflow is terminated; its SCF children are killed when their heartbeat is refused.

While it runs, `smeltery run --out` appends each completed stage to `RUN_DIR/stages.partial.jsonl` (fsync'd, one
line per stage, removed once the run record is written). A stage is stored for reuse only after it completes, so a
cancel that lands mid-stage leaves every completed stage reusable and the interrupted one not; the cancelled record
lists the completed ones (`stages_completed`). `smeltery status RUN_DIR` prints them ("stages completed: ...",
"stages reused: N of M reusable"), and MCP's `run_status` reports each as done or reused; `screen_candidates` says
how many stages of a resubmitted screen will be reused (`reused_stages` of `reusable_stages`).

## What attestation-schema owns

Since attestation `9fb7eed` (attestation-schema 0.1.0), `attestation_schema.state` owns everything every backend needs
and `smeltery.jobs` once implemented itself: the `state.json` reader and writer, the cross-process lock, `settle`
("end exactly once; an existing record decides; record before state"; it also checks the record is the job's) and the
minimal terminal record. `smeltery.jobs` keeps what is smeltery's: the two backends, `check_spec`, the runner, and a
`settle` wrapper supplying its producer, `code` and `ext["smeltery.job"]`.

## Tests

`tests/test_job_backends.py` is one conformance suite run against both backends: state-machine legality, `submit`
returning at once with `submitted` written, a complete job, a failing one, `result` before completion, idempotent
cancel of a running job, cancel of one that never started, cancel of a finished one, a crashed worker, state.json's
exact contents (read back as a `StateFile`), and a record matching every terminal state: the job's run_id and config,
`ext["smeltery.job"]` exactly `{backend, job_id, stages_completed}`, and `finished_at` equal to the terminal history
entry's `at`; a record that is not the job's ends it failed. The campaign is a real `smeltery run` on the fake engine
(`tests/fake_ferric.py`), about a second per job. The temporal leg runs against the dev server at
`SMELTERY_TEMPORAL_ADDRESS` (default `127.0.0.1:7233`), or with `SMELTERY_TEST_TEMPORAL_LOCAL=1` against
`temporalio.testing.WorkflowEnvironment.start_local()` (which downloads the dev server on first use); otherwise it
skips. Its crashed-worker case always skips: Temporal retries a crashed worker's work instead of failing the job.

`tests/test_stage_reuse.py` covers resumability and cancellation on the fake engine, counting its SCFs: an identical
resubmission runs none and matches the first record except for reuse provenance and timestamps; a changed setting
invalidates its stage and every one after it; another ferric build invalidates; a dirty build neither reuses nor
stores; a corrupted entry is recomputed; concurrent writers leave one valid entry; a job cancelled mid-analysis lists
its completed stages and its resubmission reuses the measurement only; MCP reports the reuse. The Temporal cancel leg
is skipped there (no server here; `tests/test_job_backends.py` runs it against one).
