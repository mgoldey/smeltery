"""Campaign jobs: attestation-schema's `JobBackend` contract over `smeltery run`, behind `SMELTERY_BACKEND`.

A job is one `smeltery run CONFIG --out RUN_DIR`, submitted and left to run: `submit` returns a handle at once and
`status`, `cancel` and `result` look at it later, from any process. Two backends, one contract
(tests/test_job_backends.py runs the same conformance suite against both):

local     (default) Bench's SQLite task queue and its detached per-task supervisors
          (`bench.adapters.task_queue.SubprocessTaskQueue`, under ROOT/.smeltery/bench/tasks): the job is a capped
          task running `python -m smeltery.jobs _run STORE JOB_ID`, so it shares Bench's launched-run slots
          (SMELTERY_MAX_RUNS) and shows in its task panel. No server; `smeltery worker` keeps the queue moving and
          settles jobs whose process died. Job metadata: ROOT/.smeltery/jobs/jobs.sqlite.
temporal  the existing `temporal_run.PipelineRunWorkflow` (the `temporal` extra), started without waiting; the job id
          is the workflow id. A `smeltery worker` with SMELTERY_BACKEND=temporal runs its SCF legs.

The run directory is the job's OUT, laid out per `attestation_schema.layout`:

    RUN_DIR/config.toml, inputs/ ...      the campaign config, staged (relative paths resolve against RUN_DIR)
    RUN_DIR/attestation/state.json        the job's state, owned by the backend (`attestation.state/1`, below)
    RUN_DIR/attestation/run.json          the core record, at every terminal state

**state.json** (`attestation.state/1`) is owned by attestation-schema since attestation 9fb7eed: its reader and writer,
the `flock` on `attestation/`, `transition`, `settle` (end a job exactly once) and `terminal_record` are
`attestation_schema.state`'s, and this module only calls them. `read_state` returns a frozen `StateFile`
(`st.state`, `st.history`, `st.status`, `st.started_at`, `st.to_dict()`); a malformed file raises `StateFormatError`
(a ValueError); an illegal transition raises `IllegalTransition` (`IllegalTransitionError`) and writes nothing. POSIX
only: without `flock` (Windows) every state write is refused (`StateLockUnavailableError`).

**Terminal records.** A complete run's record is the one `smeltery run` writes (`core_record`, through
`cli.emit_core_record`), given the job's `run_id` (`smeltery run --run-id`, `RunInput.run_id` on Temporal) and with the
campaign config as `config`, so `settle` finds it is this job's. A failed or cancelled job gets the schema's minimal
record (`attestation_schema.state.terminal_record`, through `settle`): run_id, family, arm, config, times, the manifest
of whatever the run left, no metrics, and in `ext["smeltery.job"]` the `backend`, the `job_id` and
`stages_completed`: the stages the run logged as completed (OUT/stages.partial.jsonl, `smeltery.reuse`) with their
digests and whether each was reused, or null when the run left no stage log. Completed stages are already in the
workspace's stage store, so a resubmission reuses them; the stage that was interrupted is not. The job's message lives
in state.json only; a manifest refused for a symlink is said in that message. When a record already exists, the job's
terminal state follows the record's status (a cancel that races completion ends `complete`), so state.json and run.json
never disagree; a record that is not the job's (another run_id, family or config) ends it `failed`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sqlite3
import sys
import tomllib
import uuid
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

try:
    import attestation_schema as _schema
    from attestation_schema.jobs import (
        TERMINAL,
        JobError,
        JobExistsError,
        JobHandle,
        JobNotFoundError,
        JobNotTerminalError,
        JobSpec,
        JobState,
        JobStatus,
    )
    from attestation_schema.record import CodeIdentity, Producer
    from attestation_schema.state import (
        IllegalTransitionError,
        StateFile,
        init_state,
        read_state,
        record_path,
        state_path,
        transition,
    )
    from attestation_schema.state import handle_in as _handle_in
    from attestation_schema.state import settle as _schema_settle
except ImportError:  # the `attestation` extra is not installed
    _schema = None

    class JobError(Exception):  # type: ignore[no-redef]
        """Stand-in so this module imports without the extra; nothing here can run without it."""


AVAILABLE = _schema is not None
NOT_INSTALLED = "campaign jobs need attestation-schema (the `attestation` extra)"
#: The `ext` namespace of a record this module writes (a failed or cancelled job's).
EXT = "smeltery.job"
#: Bench's task queue, shared: a campaign job is a launched run, and counts against the same slots.
TASKS_DIR = Path(".smeltery") / "bench" / "tasks"
JOBS_DIR = Path(".smeltery") / "jobs"
#: The runner's argv prefix; the backend appends STORE JOB_ID. Tests point it at a fake engine.
RUNNER = [sys.executable, "-m", "smeltery.jobs", "_run"]


if AVAILABLE:
    #: The schema's error, under the name this module has always raised (an illegal transition writes nothing).
    IllegalTransition = IllegalTransitionError


class BackendUnavailable(JobError):
    """The backend cannot be reached (the Temporal server is down) or is not installed."""


def _require() -> None:
    if not AVAILABLE:
        raise BackendUnavailable(NOT_INSTALLED)


def now() -> str:
    return _schema.format_timestamp(datetime.now(UTC))


# ---- state.json and settling (attestation_schema.state) --------------------------------------------------------


def _producer() -> Producer:
    from . import core_record

    return Producer("smeltery jobs", core_record.producer()["version"])


def _code(observer: bool) -> CodeIdentity:
    """`code` for a record this module writes: smeltery's identity on the dependency ladder (`core_record`'s own
    mapping). Written by a backend that only watched the run (`observer`), it is the backend's smeltery, so a
    VERIFIED identity is downgraded to inferred, and `evidence` says why."""
    from . import core_record
    from .record import smeltery_identity

    code = core_record._code(smeltery_identity(), core_record.producer()["version"])
    if observer:
        code["evidence"] = "the smeltery of the job backend that wrote this record; the run did not report its own" + (
            f" ({code['evidence']})" if code["evidence"] else ""
        )
        if code["provenance"] == "verified":
            code["provenance"] = "inferred"
    return CodeIdentity(**code)


def settle(
    run_dir: str | os.PathLike[str],
    spec: JobSpec,
    outcome: JobState,
    message: str | None,
    *,
    observer: bool,
    started_at: str | None = None,
    ended_at: str | None = None,
) -> StateFile:
    """End the job at `outcome`, exactly once (`attestation_schema.state.settle`), and return its state.

    What smeltery adds is what the schema cannot know: its producer and code identity, and `ext["smeltery.job"]` for
    a record written here (`backend`, `job_id`, and `stages_completed` from the run's stage log). A record already in
    the run directory decides the terminal state; otherwise `complete` without a record is `failed`. `started_at` /
    `ended_at`: the scheduler's times, when it has them (the Temporal backend's workflow history)."""
    from .reuse import read_progress

    st = read_state(run_dir)
    if st is None:
        raise JobNotFoundError(f"{state_path(run_dir)} does not exist")
    # the stages the run completed before it stopped, from its stage log (null: it left none, e.g. --temporal)
    ext = {EXT: {"backend": st.backend, "job_id": st.job_id, "stages_completed": read_progress(run_dir)}}
    return _schema_settle(
        run_dir,
        spec,
        outcome,
        message,
        producer=_producer(),
        code=_code(observer),
        ext=ext,
        started_at=started_at,
        ended_at=ended_at,
    )


def handle_in(run_dir: str | os.PathLike[str]) -> JobHandle:
    """The handle of the job in `run_dir`, from its state.json (the directory resolved, so a handle names one place)."""
    return _handle_in(Path(run_dir).resolve())


def _refuse_existing(run_dir: Path) -> None:
    for p in (state_path(run_dir), record_path(run_dir)):
        if p.exists():
            raise JobExistsError(f"{p} exists: {run_dir} already holds a run; submit into a new run directory")


# ---- specs ---------------------------------------------------------------------------------------------------------


def check_spec(spec: JobSpec) -> None:
    """What a smeltery campaign job can honestly be. The complete record is derived from the run by `core_record`,
    which names the family after the config's `campaign`, has no arm, and has no slot for a caller's `ext`: a spec
    saying otherwise would get one answer in a failed record and another in a complete one, so it is refused."""
    if not isinstance(spec.run_id, str) or not spec.run_id:
        raise ValueError("run_id must be a non-empty string")
    if not isinstance(spec.config, dict) or not isinstance(spec.config.get("campaign"), str):
        raise ValueError("config must be a campaign config (a table with `campaign`), as `smeltery run` reads it")
    if spec.family != spec.config["campaign"]:
        raise ValueError(f"family must be the config's campaign ({spec.config['campaign']!r}), got {spec.family!r}")
    if spec.arm is not None:
        raise ValueError("smeltery has no arm: a campaign job's arm is None")
    if spec.ext:
        raise ValueError("a campaign job takes no ext: smeltery's complete record is derived from the run")
    json.dumps(spec.config)  # stored and recorded as JSON: a TOML datetime is refused here, not at the end


def write_config(run_dir: Path, config: dict) -> Path:
    """RUN_DIR/config.toml for `config`; an existing file that already says the same is kept (comments and all)."""
    import tomlkit

    dest = run_dir / "config.toml"
    with contextlib.suppress(OSError, tomllib.TOMLDecodeError):
        if tomllib.loads(dest.read_text()) == config:
            return dest
    run_dir.mkdir(parents=True, exist_ok=True)
    dest.write_text(tomlkit.dumps(config))
    return dest


def campaign_spec(config: str | os.PathLike[str], run_dir: str | os.PathLike[str], run_id: str | None = None):
    """A JobSpec for `smeltery run CONFIG --out RUN_DIR`: the config is staged into RUN_DIR first (its input files
    copied under RUN_DIR/inputs/, as `smeltery run --out` does), so the spec's config is self-contained. `run_id`
    names the run's records whoever writes them, and defaults to a fresh 12-hex name, not the input digest: that
    digest covers the built plan (structure prep, docking, the pocket, the tiers' tool versions), which submit does
    not build (docs/jobs.md, "run_id")."""
    _require()
    from .cli import _stage_config

    run_dir = Path(run_dir).resolve()
    _refuse_existing(run_dir)  # before staging: never rewrite a finished run's config
    staged, _ = _stage_config(Path(config), run_dir)
    cfg = tomllib.loads(staged.read_text())
    return JobSpec(
        run_id=run_id or uuid.uuid4().hex[:12],
        family=cfg.get("campaign"),
        arm=None,
        config=cfg,
        run_dir=str(run_dir),
    )


def _spec_from(row_spec: str, run_dir: str) -> JobSpec:
    d = json.loads(row_spec)
    return JobSpec(run_id=d["run_id"], family=d["family"], arm=d["arm"], config=d["config"], run_dir=run_dir)


# ---- the local backend ---------------------------------------------------------------------------------------------

_STEPS = (
    "CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, task_id TEXT, run_dir TEXT NOT NULL, "
    "spec TEXT NOT NULL, created_at TEXT NOT NULL, settled INTEGER NOT NULL DEFAULT 0)",
)
_DONE = ("succeeded", "failed", "cancelled")


@dataclass(frozen=True)
class _Job:
    job_id: str
    task_id: str | None
    run_dir: Path
    spec: JobSpec


class LocalJobBackend:
    """Campaign jobs on Bench's SQLite task queue, each under its own detached supervisor (no server).

    The runner (`python -m smeltery.jobs _run`) moves the job to `running`, runs `smeltery run`, and settles it.
    What it cannot settle -- it was cancelled, killed, or its supervisor died -- this class settles when it next looks
    (`status`, `cancel`, `result`, or `reconcile`, which `smeltery worker` calls every few seconds)."""

    name = "local"

    def __init__(self, root: str | os.PathLike[str], *, max_runs: int = 1, runner: list[str] | None = None) -> None:
        _require()
        from .bench.adapters._sqlite import migrate, open_db
        from .bench.adapters.task_queue import SubprocessTaskQueue

        self.root = Path(root).expanduser().resolve()
        self.store = self.root / JOBS_DIR
        self.store.mkdir(parents=True, exist_ok=True)
        self.db = self.store / "jobs.sqlite"
        with open_db(self.db, wal=True) as con:
            migrate(con, _STEPS)
        self.queue = SubprocessTaskQueue(self.root / TASKS_DIR, max_concurrent_runs=max_runs)
        self.runner = list(runner or RUNNER)

    def _rows(self, where: str = "", args: tuple = ()) -> list[_Job]:
        from .bench.adapters._sqlite import open_db

        with open_db(self.db) as con:
            rows = con.execute(f"SELECT job_id, task_id, run_dir, spec FROM jobs {where}", args).fetchall()
        return [_Job(j, t, Path(d), _spec_from(s, d)) for j, t, d, s in rows]

    def _job(self, handle: JobHandle) -> _Job:
        if handle.backend != self.name:
            raise JobNotFoundError(f"{handle.job_id} is a {handle.backend} job, not a {self.name} one")
        found = self._rows("WHERE job_id=?", (handle.job_id,))
        if not found:
            raise JobNotFoundError(f"no local job {handle.job_id} under {self.root}")
        return found[0]

    def submit(self, spec: JobSpec) -> JobHandle:
        from .bench.adapters._sqlite import open_db

        check_spec(spec)
        run_dir = Path(spec.run_dir).resolve()
        _refuse_existing(run_dir)
        write_config(run_dir, spec.config)
        job_id = uuid.uuid4().hex[:12]
        stored = {"run_id": spec.run_id, "family": spec.family, "arm": spec.arm, "config": spec.config}
        with open_db(self.db) as con:
            con.execute(
                "INSERT INTO jobs (job_id, run_dir, spec, created_at) VALUES (?,?,?,?)",
                (job_id, str(run_dir), json.dumps(stored), now()),
            )
        init_state(run_dir, run_id=spec.run_id, backend=self.name, job_id=job_id)
        handle = JobHandle(self.name, job_id, spec.run_id, str(run_dir))
        try:
            task_id = self.queue.submit(
                f"job {spec.run_id}",
                [*self.runner, str(self.store), job_id],
                note=f"campaign job {job_id}: smeltery run into {run_dir}",
                capped=True,
            )
        except (OSError, ValueError, sqlite3.Error) as e:  # the queue refused it: submitted -> failed, with a record
            settle(run_dir, spec, JobState.FAILED, f"the task queue refused the job: {e}", observer=True)
            return handle
        with open_db(self.db) as con:
            con.execute("UPDATE jobs SET task_id=? WHERE job_id=?", (task_id, job_id))
        return handle

    def _settle(self, job: _Job) -> StateFile:
        from .bench.adapters._sqlite import open_db

        st = read_state(job.run_dir)
        if st is None:
            raise JobNotFoundError(f"{state_path(job.run_dir)} is missing: the run directory was moved or removed")
        if st.state not in TERMINAL and job.task_id is not None:
            task = self.queue.get(job.task_id)  # fails a task whose supervisor died, and starts queued ones
            if task is None:
                st = settle(
                    job.run_dir, job.spec, JobState.FAILED, "the job's task is gone from the queue", observer=True
                )
            elif task.status in _DONE:
                outcome = JobState.CANCELLED if task.status == "cancelled" else JobState.FAILED
                if task.status == "succeeded":  # the runner exited 0 but did not settle: it settles complete itself
                    outcome = JobState.COMPLETE
                why = task.note if task.status != "succeeded" else None
                if task.status == "failed" and not why:
                    why = f"the job's process exited {task.returncode}" + _last_line(task.log_path)
                st = settle(job.run_dir, job.spec, outcome, why or None, observer=True)
        if st.state in TERMINAL:
            with open_db(self.db) as con:
                con.execute("UPDATE jobs SET settled=1 WHERE job_id=?", (job.job_id,))
        return st

    def status(self, handle: JobHandle) -> JobStatus:
        return self._settle(self._job(handle)).status

    def cancel(self, handle: JobHandle) -> JobStatus:
        job = self._job(handle)
        st = self._settle(job)
        if st.state in TERMINAL:
            return st.status
        if job.task_id is not None:
            # marks the task cancelled, then ends its process group (SIGTERM, SIGKILL after a grace) if it runs
            self.queue.cancel(job.task_id)
        return self._settle(job).status

    def result(self, handle: JobHandle):
        job = self._job(handle)
        st = self._settle(job)
        if st.state not in TERMINAL:
            raise JobNotTerminalError(f"job {handle.job_id} is {st.state.value}: it has no record yet")
        return _schema.read(job.run_dir)

    def reconcile(self) -> int:
        """Settle every unsettled job whose task has ended; returns how many are still live. `smeltery worker`."""
        live = 0
        for job in self._rows("WHERE settled=0"):
            try:
                st = self._settle(job)
            except (JobError, ValueError, OSError) as e:  # a run directory removed or damaged: say so, keep going
                print(f"smeltery worker: job {job.job_id}: {e}", file=sys.stderr)
                continue
            live += st.state not in TERMINAL
        return live

    def task_of(self, handle: JobHandle) -> dict | None:
        """The live task row behind a job ({id, status, pid, supervisor_pid}); None once it has ended."""
        job = self._job(handle)
        return next((t for t in self.queue.live_tasks() if t["id"] == job.task_id), None)


def _last_line(log_path: str) -> str:
    from .bench.adapters.task_queue import read_tail

    with contextlib.suppress(OSError):
        lines = [ln for ln in read_tail(log_path, 20).splitlines() if ln.strip()]
        if lines:
            return f": {lines[-1].strip()[:300]}"
    return ""


class _Tail(io.TextIOBase):
    """A stream that passes everything through and keeps the last lines, for the failure message."""

    def __init__(self, inner, n: int = 20) -> None:
        self.inner, self.lines, self._part = inner, deque(maxlen=n), ""

    def write(self, s: str) -> int:
        self.inner.write(s)
        self._part += s
        *done, self._part = self._part.split("\n")
        self.lines.extend(ln for ln in done if ln.strip())
        return len(s)

    def flush(self) -> None:
        self.inner.flush()

    def last(self) -> str | None:
        tail = [*self.lines, self._part] if self._part.strip() else list(self.lines)
        return tail[-1].strip()[:300] if tail else None


def runner_main(argv: list[str]) -> int:
    """`python -m smeltery.jobs _run STORE JOB_ID`: one local job, in its supervisor's process group. Moves the job
    to running (a job cancelled before it started stops here), runs `smeltery run RUN_DIR/config.toml --out RUN_DIR`,
    and settles it: complete when the run exited 0 and wrote its core record, otherwise failed with the run's last
    error line. A kill skips the settle; the backend does it when it next looks."""
    store, job_id = Path(argv[0]), argv[1]
    with contextlib.closing(sqlite3.connect(store / "jobs.sqlite", timeout=30)) as con:
        row = con.execute("SELECT run_dir, spec FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        print(f"smeltery jobs: no job {job_id} in {store}", file=sys.stderr)
        return 2
    run_dir, spec = Path(row[0]), _spec_from(row[1], row[0])
    try:
        transition(run_dir, JobState.RUNNING)
    except IllegalTransition as e:  # cancelled (or settled) before it started: nothing runs
        print(f"smeltery jobs: not started: {e}", file=sys.stderr)
        return 1
    from .cli import main as smeltery_main

    tail = _Tail(sys.stderr)
    try:
        with contextlib.redirect_stderr(tail):
            # the workspace's stage store (ROOT/.smeltery/stages, beside the job store): an identical resubmission
            # reuses the stages an earlier job completed (smeltery.reuse)
            stage_cache = store.parent / "stages"
            # --run-id: the job's, so the run's complete record is this job's (settle fails one naming another)
            argv = [
                *("run", str(run_dir / "config.toml"), "--out", str(run_dir), "--stage-cache", str(stage_cache)),
                f"--run-id={spec.run_id}",  # one token: a run_id may start with "-"
            ]
            rc = smeltery_main(argv)
    except Exception as e:  # noqa: BLE001 - any crash of the run is the job's failure, recorded, then re-raised
        settle(run_dir, spec, JobState.FAILED, f"smeltery run crashed: {type(e).__name__}: {e}"[:400], observer=False)
        raise
    if rc == 0:
        settle(run_dir, spec, JobState.COMPLETE, None, observer=False)
    else:
        last = tail.last()
        settle(
            run_dir, spec, JobState.FAILED, f"smeltery run exited {rc}" + (f": {last}" if last else ""), observer=False
        )
    return rc


# ---- the Temporal backend ------------------------------------------------------------------------------------------


class TemporalJobBackend:
    """Campaign jobs as `PipelineRunWorkflow` executions (the `temporal` extra), started without waiting.

    The workflow is the one `smeltery run --temporal` runs, unchanged: it writes the run's files and, on success, the
    core record (its finalize activity calls `cli.emit_core_record`). It does not write state.json: this class mirrors
    the workflow's status into it when asked (`status`, `cancel`, `result`), reading the time a worker first took the
    workflow from its history, and writes the failed or cancelled record then. `cancel` terminates the workflow (no
    worker is needed to stop it; running SCF children are killed when their heartbeat is refused). A crashed worker
    does not fail a job here: Temporal retries its activities on the next worker."""

    name = "temporal"

    def __init__(
        self,
        address: str = "localhost:7233",
        namespace: str = "default",
        task_queue: str = "smeltery-pipeline",
        workers: int = 2,
        heartbeat_s: float = 180.0,
    ) -> None:
        _require()
        from .temporal_run import TEMPORAL_AVAILABLE

        if not TEMPORAL_AVAILABLE:
            raise BackendUnavailable("SMELTERY_BACKEND=temporal needs temporalio (the `temporal` extra)")
        self.address, self.namespace, self.task_queue = address, namespace, task_queue
        self.workers, self.heartbeat_s = workers, heartbeat_s

    def _call(self, fn):
        import asyncio

        from .temporal_run import connect

        async def go():
            try:
                client = await connect(self.address, self.namespace)
            except Exception as e:  # noqa: BLE001 - any connect failure is "the server is down"
                raise BackendUnavailable(f"the Temporal server at {self.address} cannot be reached: {e}") from e
            return await fn(client)

        return asyncio.run(go())

    def submit(self, spec: JobSpec) -> JobHandle:
        from . import temporal_run as tr
        from .cli import _stage_config

        check_spec(spec)
        run_dir = Path(spec.run_dir).resolve()
        _refuse_existing(run_dir)
        config, info = _stage_config(write_config(run_dir, spec.config), run_dir, temporal=True)
        inp = tr.RunInput(
            str(config),
            str(run_dir),
            self.workers,
            info,
            heartbeat_s=self.heartbeat_s,
            config_sha=tr.sha256_file(config),
            run_id=spec.run_id,  # the finalize activity writes the complete record under the job's run_id
        )
        wid = tr.workflow_id(run_dir, config.read_text())

        async def start(client):
            from temporalio.exceptions import WorkflowAlreadyStartedError

            try:
                await client.start_workflow(tr.PipelineRunWorkflow.run, inp, id=wid, task_queue=self.task_queue)
            except WorkflowAlreadyStartedError as e:
                raise JobExistsError(f"workflow {wid} is already running in {run_dir}") from e
            return tr._host_of(client)

        host = self._call(start)
        marker = run_dir / tr.WORK_DIR / tr.MARKER  # what `smeltery run --out` checks before re-staging (guard_out)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"workflow_id": wid, "host": host, "task_queue": self.task_queue, "config_sha": inp.config_sha})
        )
        init_state(run_dir, run_id=spec.run_id, backend=self.name, job_id=wid)
        return JobHandle(self.name, wid, spec.run_id, str(run_dir))

    def _spec(self, handle: JobHandle, st: StateFile) -> JobSpec:
        cfg = tomllib.loads((Path(handle.run_dir) / "config.toml").read_text())
        return JobSpec(st.run_id, cfg.get("campaign"), None, cfg, handle.run_dir)

    def _state(self, handle: JobHandle) -> StateFile:
        if handle.backend != self.name:
            raise JobNotFoundError(f"{handle.job_id} is a {handle.backend} job, not a {self.name} one")
        st = read_state(handle.run_dir)
        if st is None or st.job_id != handle.job_id:
            raise JobNotFoundError(f"no temporal job {handle.job_id} in {handle.run_dir}")
        return st

    async def _observe(self, client, handle: JobHandle, st: StateFile) -> StateFile:
        """Mirror the workflow into state.json: running once a worker has taken it, terminal when it closed."""
        from temporalio.api.enums.v1 import EventType
        from temporalio.client import WorkflowExecutionStatus as S
        from temporalio.client import WorkflowFailureError
        from temporalio.service import RPCError, RPCStatusCode

        wf = client.get_workflow_handle(handle.job_id)
        try:
            desc = await wf.describe()
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:
                gone = "the workflow is gone from the Temporal server (its history expired, or it never started)"
                return settle(handle.run_dir, self._spec(handle, st), JobState.FAILED, gone, observer=True)
            raise
        began = None
        async for ev in wf.fetch_history_events():
            if ev.event_type == EventType.EVENT_TYPE_WORKFLOW_TASK_STARTED:
                began = _schema.format_timestamp(ev.event_time.ToDatetime(tzinfo=UTC))
                break
        if desc.status == S.RUNNING:
            if began and st.state is JobState.SUBMITTED:
                with contextlib.suppress(IllegalTransition):  # settled by another observer meanwhile
                    st = transition(handle.run_dir, JobState.RUNNING, None, at=began)
            return st
        if desc.status == S.COMPLETED:
            outcome, why = JobState.COMPLETE, None
        elif desc.status in (S.TERMINATED, S.CANCELED):
            outcome, why = JobState.CANCELLED, f"the workflow was {desc.status.name.lower()}"
        else:  # FAILED, TIMED_OUT, CONTINUED_AS_NEW (the pipeline never continues as new)
            outcome, why = JobState.FAILED, f"the workflow {desc.status.name.lower()}"
            try:
                await wf.result()
            except WorkflowFailureError as e:
                cause = e.cause
                while getattr(cause, "cause", None) is not None:
                    cause = cause.cause
                why = f"{why}: {cause or e}"[:400]
            except Exception:  # noqa: BLE001 - the status is known; the message is a courtesy
                pass
        # the scheduler's own times: when a worker first took the workflow, and when it closed (not this observation)
        closed = _schema.format_timestamp(desc.close_time) if desc.close_time is not None else None
        st = settle(
            handle.run_dir, self._spec(handle, st), outcome, why, observer=True, started_at=began, ended_at=closed
        )
        if st.state in TERMINAL:
            from .temporal_run import clear_marker, sha256_file

            with contextlib.suppress(OSError):
                clear_marker(handle.run_dir, sha256_file(Path(handle.run_dir) / "config.toml"))
        return st

    def status(self, handle: JobHandle) -> JobStatus:
        st = self._state(handle)
        if st.state in TERMINAL:
            return st.status
        return self._call(lambda c: self._observe(c, handle, st)).status

    def cancel(self, handle: JobHandle) -> JobStatus:
        st = self._state(handle)
        if st.state in TERMINAL:
            return st.status

        async def go(client):
            from temporalio.service import RPCError

            with contextlib.suppress(RPCError):  # already closed: the observation below says how it ended
                await client.get_workflow_handle(handle.job_id).terminate(reason="cancelled through smeltery jobs")
            return await self._observe(client, handle, st)

        return self._call(go).status

    def result(self, handle: JobHandle):
        s = self.status(handle)
        if s.state not in TERMINAL:
            raise JobNotTerminalError(f"job {handle.job_id} is {s.state.value}: it has no record yet")
        return _schema.read(handle.run_dir)


# ---- selection and the CLI -----------------------------------------------------------------------------------------


def backend_for(settings, root: str | os.PathLike[str] | None = None, name: str | None = None):
    """The backend `settings` select (or `name`, the one a state.json names). `root`: the local backend's workspace."""
    name = name or settings.backend
    if name == "temporal":
        return TemporalJobBackend(
            settings.temporal_address, settings.namespace, settings.pipeline_queue, settings.workers
        )
    if name != "local":
        raise JobNotFoundError(f"no job backend {name!r}")
    if root is None:
        if settings.root:
            root = settings.root
        else:
            try:
                from .bench.app import default_root
            except ImportError as e:  # the bench extra names the default; without it, say where
                raise BackendUnavailable("give the workspace: --root ROOT or SMELTERY_ROOT") from e
            root = default_root()
    return LocalJobBackend(root, max_runs=settings.max_runs)


def _parser(command: str, description: str) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=f"smeltery {command}", description=description)
    ap.add_argument("--root", default=None, help="local: the workspace holding the job queue; env SMELTERY_ROOT")
    ap.add_argument("--max-runs", dest="max_runs", default=None, help="local: runs at once; env SMELTERY_MAX_RUNS")
    ap.add_argument("--temporal-address", dest="temporal_address", default=None, help="env SMELTERY_TEMPORAL_ADDRESS")
    ap.add_argument("--namespace", default=None, help="env SMELTERY_TEMPORAL_NAMESPACE")
    ap.add_argument("--pipeline-queue", dest="pipeline_queue", default=None, help="env SMELTERY_PIPELINE_QUEUE")
    ap.add_argument("--workers", default=None, help="temporal: scores run at once; env SMELTERY_WORKERS")
    return ap


def _settings(args: argparse.Namespace, env: Mapping[str, str], **extra: object):
    from .settings import resolve

    flags = {
        k: getattr(args, k)
        for k in ("root", "max_runs", "temporal_address", "namespace", "pipeline_queue", "workers")
        if hasattr(args, k)
    }
    return resolve(env, **flags, **extra)


def _cli(command: str, argv: list[str] | None, env: Mapping[str, str] | None) -> int:
    from .settings import SettingsError

    env = os.environ if env is None else env
    if command == "submit":
        ap = _parser("submit", "Submit `smeltery run CONFIG --out RUN_DIR` as a job and return at once.")
        ap.add_argument("config", type=Path)
        ap.add_argument("--out", type=Path, required=True, help="the job's run directory (a new one per job)")
        ap.add_argument("--run-id", dest="run_id", default=None, help="the job's run_id (default: a fresh name)")
        ap.add_argument("--backend", default=None, help="local (default) or temporal; env SMELTERY_BACKEND")
    else:
        ap = _parser(command, f"{command.capitalize()} the job in RUN_DIR (its backend is named in state.json).")
        ap.add_argument("run_dir", type=Path)
        ap.add_argument("--json", action="store_true", help="print state.json instead of a line")
    args = ap.parse_args(argv)
    if not AVAILABLE:
        print(f"smeltery {command}: {NOT_INSTALLED}", file=sys.stderr)
        return 2
    try:
        s = _settings(args, env, **({"backend": args.backend} if command == "submit" else {}))
        if command == "submit":
            backend = backend_for(s)
            h = backend.submit(campaign_spec(args.config, args.out, args.run_id))
            print(f"submitted {h.backend} job {h.job_id} (run_id {h.run_id})\nrun dir: {h.run_dir}")
            return 0
        h = handle_in(args.run_dir)
        backend = backend_for(s, name=h.backend)
        if command == "status":
            status = backend.status(h)
        elif command == "cancel":
            status = backend.cancel(h)
        else:
            try:
                rec = backend.result(h)
            except JobNotTerminalError as e:
                print(f"smeltery result: {e}", file=sys.stderr)
                return 3
            print(f"{rec.status}\n{record_path(h.run_dir)}")
            return 0 if rec.status == JobState.COMPLETE.value else 1
        if args.json:
            print(json.dumps(read_state(h.run_dir).to_dict(), indent=2, sort_keys=True))
        else:
            print(f"{status.state.value} {status.updated_at}" + (f" {status.message}" if status.message else ""))
            from .reuse import stage_lines

            for line in stage_lines(h.run_dir):
                print(line)
        return 0
    except (SettingsError, JobError, ValueError, OSError) as e:
        print(f"smeltery {command}: {e}", file=sys.stderr)
        return 2


def submit_main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """`smeltery submit CONFIG --out RUN_DIR`: 0 submitted, 2 refused (an existing run, a bad setting)."""
    return _cli("submit", argv, env)


def status_main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """`smeltery status RUN_DIR`: the job's state, when it last changed, and why."""
    return _cli("status", argv, env)


def cancel_main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """`smeltery cancel RUN_DIR`: idempotent; a finished job's status comes back unchanged."""
    return _cli("cancel", argv, env)


def result_main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """`smeltery result RUN_DIR`: the record's status and path; 0 complete, 1 failed/cancelled, 3 not finished."""
    return _cli("result", argv, env)


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "_run":
    sys.exit(runner_main(sys.argv[2:]))
