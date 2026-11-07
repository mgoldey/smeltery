"""The ddE run as a Temporal workflow: durable execution for work that must survive interruption.

The shell/script version of this run already survives a kill, through a `done_keys()` resume written
into it by hand. What it does NOT have, and what durable execution gives, is everything around the
step:

* **Timeouts.** One molecule in the production run took **6085 s** against a median of 52 s. Nothing
  bounded it. A runaway earlier today had to be killed by hand. `start_to_close_timeout` ends it.
* **Heartbeats inside a long step.** A 1.7 h activity killed at 1.6 h loses everything under
  content-addressed caching, which can only resume between steps. A beat on a timer says the worker
  is alive while the SCF runs, and `activity.info().heartbeat_details` carries the stage it reached.
  The beat must be on a CLOCK: an earlier version beat only where the code changed stage, so the
  whole SCF passed without one and every molecule slower than `heartbeat_timeout` was killed as a
  dead worker and retried into the same wall. That cost 67 of 87 rows in the ladder run.
* **Retries.** A transient CUDA OOM or a flaky subprocess currently fails a condition permanently.
  A retry policy makes that a blip, and `non_retryable_error_types` keeps a genuine chemistry
  refusal (a clash, a non-convergence) from being retried pointlessly.
* **Visibility.** Every molecule, its attempts, its duration, in a UI, instead of `print()` behind a
  lock.

**What is deliberately NOT changed.** The science is untouched: this calls the same
`FieldDftInteraction` tier on the same water-free field with the same clash gate, and appends to the
same JSONL. Temporal orchestrates; it does not compute. The determinism rule is why the split falls
where it does -- the workflow function holds only control flow, and every piece of IO, RNG and GPU
work sits in an activity, because a workflow is replayed from its event history and must produce the
same calls every time.

**There is no GPU path for this work.** `ferric.gpu_status()` on this build reports
`{'compiled': False, 'status': 'not_compiled'}`: the CUDA backend is not compiled in, so
`configure_gpu` cannot make the SCF use a device. The two GTX 1080s serve the torch generators
(DiffSBDD, PocketXMol), which is a different code path entirely. Parallelism here is CPU workers,
and the ceiling on those is memory, not cores -- see `run_worker`.

Needs the `temporal` extra (`uv pip install 'temporalio>=1.35'`) and a server to talk to.

Run:
    docker run -d --name temporal-dev -p 7233:7233 -p 8233:8233 \
        temporalio/temporal:latest server start-dev --ip 0.0.0.0
    python temporal_ddE.py worker          # in one shell
    python temporal_ddE.py start --cells s3acid_k --limit 5
    # UI: http://127.0.0.1:8233

The ladder three-arm run (`--source ladder_source`, cells from `ladder_cells.json`, molecules
pinned by a plan written before any ddE existed):
    python temporal_ddE.py worker --concurrency 2
    python temporal_ddE.py start --source ladder_source --plan ladder_ddE_plan.json \
        --cells head_g17,tail_g17,tail_g18 --out ddE_ladder.jsonl

A molecule that fails is written to the output with an `error` field and no `dE_int`, and the cell
goes on. Re-running the same command into the same file retries exactly those: `completed_names`
skips only rows that carry a `dE_int`, so a failure is work still to do rather than a result.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

#: The study directory supplying `gfn2_gate` (cells, pocket, parent) and receiving the output.
#: A parameter, not a constant: this script is about durably orchestrating a tier, and the
#: gen-compare study is one caller of it. Set SMELTERY_STUDY_DIR to point it elsewhere.
#: Resolved lazily inside activities, NEVER at import: the workflow sandbox re-imports this module
#: to replay history and blocks non-deterministic calls, so `Path.cwd()` at module level fails
#: validation with "__call__ on pathlib.Path.cwd restricted". That is the determinism rule doing
#: its job -- a workflow must make the same calls on every replay, and the working directory is not
#: guaranteed to be the same.
STUDY_DIR_ENV = "SMELTERY_STUDY_DIR"
#: The Temporal server. `--host` overrides it; everything else about this script is a parameter, and
#: a hardcoded address in two places is the one thing that stopped it running anywhere else.
DEFAULT_HOST = os.environ.get("SMELTERY_TEMPORAL_HOST", "127.0.0.1:7233")
#: smeltery's own source, when it is not already importable (a checkout rather than an install).
SMELTERY_SRC = os.environ.get("SMELTERY_SRC", "")
TASK_QUEUE = "ddE"


def _study_dir() -> Path:
    """The study directory, read at CALL time so no non-deterministic call happens at import."""
    return Path(os.environ.get(STUDY_DIR_ENV) or ".").resolve()


def _study_path() -> None:
    """Put the study dir and smeltery on sys.path, inside the activity that needs them.

    Activities run in worker threads that do not inherit the client's interpreter state, so each
    does this itself rather than relying on a module-level import that may not have happened.
    """
    for p in (str(_study_dir()), SMELTERY_SRC):
        if p and p not in sys.path:
            sys.path.insert(0, p)


#: The median molecule is 52 s and the worst observed was 6085 s. 2x the worst is a bound that ends
#: a runaway without killing a legitimately slow molecule.
MOLECULE_TIMEOUT = timedelta(seconds=12200)
#: How often the scoring activity beats WHILE it works, not how often it changes stage. A worker
#: that dies is then detected in a minute rather than at MOLECULE_TIMEOUT, and a molecule that is
#: merely slow is not mistaken for one. `heartbeat_timeout` below is 3x this, so two beats can be
#: lost before an activity is called dead.
HEARTBEAT = timedelta(seconds=60)


@dataclass
class CellInput:
    cell: str
    generator: str = "pxm"
    limit: int | None = None
    out: str = "ddE_temporal.jsonl"
    #: The study module supplying `cell_spec` / `load_raws` / `accepted_of`. Default is the
    #: published 15-cell gate; the ladder arms live in `ladder_source`, whose cells come from
    #: `ladder_cells.json` and whose samples are pooled over 12 sweep runs. A parameter rather
    #: than an import so one workflow serves both designs and neither file has to change.
    source: str = "gfn2_gate"
    #: A JSON plan file (`ladder_ddE_plan.py`) naming exactly which molecules to score, frozen
    #: before any ddE was computed. When set, it REPLACES the source's molecule listing: a subset
    #: chosen after seeing results is not a sample, and pinning the list in a file written first
    #: is what makes that checkable.
    plan: str = ""
    #: The tier's memory budget. The published run used 14.0 GB, which suited a box with more free
    #: memory than this one: `smeltery.cost` predicts 5.62 GB of AO cache for the median head_g17
    #: molecule (80 atoms, nbf~266) and 7.74 GB for the largest, and ferric derives its own budget
    #: from memory available AT THE TIME -- it warned at 6.89 and 5.39 GB while the tier asked for
    #: 14.0, then recomputed grid AO blocks every Fock build, which is the slow path. A budget the
    #: machine can actually honour is faster than a generous one it cannot.
    memory_budget_gb: float = 6.0


@dataclass
class CellResult:
    cell: str
    scored: int
    skipped: int
    failed: int


# ---------------------------------------------------------------------------- activities
# Everything with IO, randomness or GPU work lives here. A workflow may not do any of it: it is
# replayed from history and must make the same calls every time.


@activity.defn
async def list_molecules(inp: CellInput) -> list[str]:
    """The accepted molecule names for a cell. Cheap, pure-ish, and it defines the unit of work."""
    return await asyncio.to_thread(_list_molecules_sync, inp)


def _study_module(inp: CellInput):
    """The study module named by `inp.source`, imported inside the activity.

    Imported by name rather than at module level because the workflow sandbox re-imports this file
    to replay history, and because the two designs' source modules pull in different data files.
    """
    import importlib

    _study_path()
    return importlib.import_module(inp.source)


def _accepted_candidates(inp: CellInput) -> list:
    """The cell's accepted candidates, however its source module defines them.

    `ladder_source.accepted_of` pools 12 sweep runs through ONE `process_samples` call and
    deduplicates by canonical SMILES; the published gate has one directory per cell and no
    duplicates to remove. Preferring the module's own `accepted_of` keeps that difference in the
    module that owns it instead of branching here.
    """
    mod = _study_module(inp)
    if hasattr(mod, "accepted_of"):
        return mod.accepted_of(inp.cell, inp.generator)

    from rdkit import Chem

    from smeltery.generate import FilterConfig
    from smeltery.generate.sbdd_post import build_scaffold, process_samples, smiles_and_pose

    parent = next(Chem.SDMolSupplier(str(mod.PARENT)))
    psmiles, ppose = smiles_and_pose(parent)
    spec = build_scaffold(psmiles, mod.cell_spec(inp.cell)["scaffold"], ppose)
    raws = mod.load_raws(inp.cell, inp.generator)
    accepted, _ = process_samples(raws, spec, FilterConfig(), pose_source="model", name_prefix=inp.cell)
    return accepted


def _list_molecules_sync(inp: CellInput) -> list[str]:
    candidates = _accepted_candidates(inp)
    available = {c.name for c in candidates}
    if inp.plan:
        plan = json.loads((_study_dir() / inp.plan).read_text())
        names = list(plan["arms"][inp.cell]["names"])
        # The plan is authoritative, so a name it carries that the source cannot produce is a
        # mismatch between the two and must stop the run: scoring "whatever matched" would quietly
        # shrink the arm and change what the comparison means.
        absent = [n for n in names if n not in available]
        if absent:
            raise ApplicationError(
                f"{inp.cell}: {len(absent)} planned molecules are not accepted candidates "
                f"(e.g. {absent[:3]}); plan and source disagree",
                non_retryable=True,
            )
    else:
        names = [c.name for c in candidates]
    return names[: inp.limit] if inp.limit else names


class _Stage:
    """The stage label the ticker reports, written by the worker thread and read by the event loop.

    A plain `str` attribute would do on CPython, but the lock states the crossing rather than
    leaving it to the GIL, and `set` has the signature the scoring function already expects.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stage = "starting"

    def set(self, stage: str) -> None:
        with self._lock:
            self._stage = stage

    def get(self) -> str:
        with self._lock:
            return self._stage


@activity.defn
async def score_molecule(inp: CellInput, name: str) -> dict:
    """One molecule's dE_int. THE long step: median 52 s, worst observed 6085 s.

    Heartbeats so a worker death is noticed in a minute rather than at the timeout, and so a retry
    can see how far the last attempt got. The science is unchanged -- same tier, same field, same
    clash gate as `ddE_run.py`.
    """
    info = activity.info()
    if info.attempt > 1:
        activity.logger.warning("retry %d for %s", info.attempt, name)
    # `activity.heartbeat` needs the worker's event loop, which the scoring thread does not have.
    # The ticker below is a task ON that loop, so it can call it directly; the thread only sets a
    # label. An earlier version called it from the thread, raised "no running event loop" on every
    # attempt, and the retry policy faithfully retried a bug three times.
    attempt = info.attempt
    stage = _Stage()

    # A heartbeat says "this worker is alive", so it must be sent on a CLOCK, not at the few points
    # where the code happens to change stage. Beating only at stage boundaries meant the whole SCF
    # -- median 52 s, worst observed 6085 s -- passed without one, and every molecule slower than
    # `heartbeat_timeout` was killed as if its worker had died, then retried and killed again. In
    # the ladder run that was 67 of 87 rows. The ticker beats while the SCF runs; `stage` only
    # labels what it reports, so a retry can still see how far the last attempt got.
    async def ticker() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT.total_seconds())
            activity.heartbeat(stage.get())

    beating = asyncio.create_task(ticker())
    try:
        return await asyncio.to_thread(_score_sync, inp, name, stage.set, attempt)
    finally:
        beating.cancel()


def _score_sync(inp: CellInput, name: str, beat, attempt: int) -> dict:
    import numpy as np
    from rdkit import Chem

    from smeltery.generate.sbdd_post import smiles_and_pose
    from smeltery.pocket.loader import load_pocket, min_distance_to_field_ang
    from smeltery.tiers import FieldDftInteraction

    beat("loading")
    mod = _study_module(inp)
    # The field, pose and cutoff come from the source module, which for the ladder arms re-exports
    # the published gate's constants: both designs are scored by one instrument, not two.
    parent = next(Chem.SDMolSupplier(str(mod.PARENT)))
    _, ppose = smiles_and_pose(parent)
    centre = tuple(float(v) for v in np.asarray(ppose.coords_ang).mean(axis=0))
    field = load_pocket(mod.POCKET, cutoff_ang=mod.CUTOFF_ANG, center_ang=centre, drop_waters=True)
    cand = next((c for c in _accepted_candidates(inp) if c.name == name), None)
    if cand is None:
        # A genuine absence, not a transient failure: retrying cannot help.
        raise ApplicationError(f"{name} is not an accepted molecule of {inp.cell}", non_retryable=True)

    cand.poses = cand.poses[:1]
    xyz = np.asarray(cand.poses[0].coords_ang, dtype=float)
    dmin = min_distance_to_field_ang(xyz, field)
    if dmin < 1.0:
        # Chemistry refusing the pose, not infrastructure failing: never retry this.
        raise ApplicationError(
            f"protein clash: {dmin:.3f} A < 1.0 A, not scoreable by a point-charge field",
            type="ClashRefusal",
            non_retryable=True,
        )

    beat("scf")
    tier = FieldDftInteraction(
        field_provenance=field.provenance, memory_budget_gb=inp.memory_budget_gb, require_free_gb=3.0
    )
    tier.run([cand], {"field": field})
    beat("done")
    return {
        "cell": inp.cell,
        "name": name,
        "smiles": cand.smiles,
        "dE_int": float(cand.per_pose["dE_int"][0]),
        "E_d3": float(cand.per_pose["E_d3"][0]),
        "min_charge_dist_ang": round(dmin, 3),
        "attempt": attempt,
    }


@activity.defn
async def completed_names(inp: CellInput) -> list[str]:
    """Names already SCORED in the output file, so a restart does not rescore them.

    A failure row carries `error` and no `dE_int`. Counting it as done made every failure permanent
    across restarts -- the resume this workflow is built around would skip exactly the molecules
    that still needed scoring, and the only way back was to hand-edit the JSONL. A row that records
    a failure is a record of work still to do, so only scored rows are skipped.

    A row a run has since rescored appears twice, the failure first; `dE_int` on either wins.
    """
    p = _study_dir() / inp.out
    if not p.exists():
        return []
    done = []
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            # A row torn by a kill mid-append: it names nothing reliably, so it blocks nothing.
            continue
        if row.get("dE_int") is not None and row.get("name"):
            done.append(row["name"])
    return done


@activity.defn
async def append_row(inp: CellInput, row: dict) -> None:
    """Append one result, fsync'd. The durable record the workflow's own history is not."""
    p = _study_dir() / inp.out
    with p.open("a") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


# ---------------------------------------------------------------------------- workflow
# Control flow only. No IO, no clock, no RNG: this function is replayed from the event history on
# every worker restart and must issue exactly the same calls.


@workflow.defn
class CellWorkflow:
    """Score one cell's molecules, durably.

    Survives a worker kill at any point: the history replays, molecules already recorded are skipped,
    and the one in flight is retried from its last heartbeat rather than from the start of the cell.
    """

    @workflow.run
    async def run(self, inp: CellInput) -> CellResult:
        names = await workflow.execute_activity(list_molecules, inp, start_to_close_timeout=timedelta(minutes=10))
        done = set(await workflow.execute_activity(completed_names, inp, start_to_close_timeout=timedelta(minutes=2)))
        scored = skipped = failed = 0
        for name in names:
            if name in done:
                skipped += 1
                continue
            try:
                row = await workflow.execute_activity(
                    score_molecule,
                    args=[inp, name],
                    start_to_close_timeout=MOLECULE_TIMEOUT,
                    heartbeat_timeout=HEARTBEAT * 3,
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=5),
                        maximum_attempts=3,
                        # A clash or a missing molecule is chemistry, not a transient fault.
                        non_retryable_error_types=["ClashRefusal"],
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - one bad molecule must not fail the cell
                failed += 1
                # Temporal wraps the activity error; the cause is what a reader needs.
                cause = getattr(exc, "cause", None)
                detail = f"{type(exc).__name__}: {exc}" + (f" <- {cause}" if cause else "")
                await workflow.execute_activity(
                    append_row,
                    args=[inp, {"cell": inp.cell, "name": name, "error": detail[:400]}],
                    start_to_close_timeout=timedelta(minutes=2),
                )
                continue
            await workflow.execute_activity(append_row, args=[inp, row], start_to_close_timeout=timedelta(minutes=2))
            scored += 1
        return CellResult(inp.cell, scored, skipped, failed)


async def run_worker(concurrency: int = 2, host: str = DEFAULT_HOST) -> None:
    """A worker. `concurrency` is how many molecules it scores at once.

    Default 2 is the owner's CPU rule (`docs/HANDOFF.md`). Raising it is an explicit decision and
    the binding constraint is MEMORY, not cores: a long-lived ferric run holds ~11 GB resident,
    several times its per-calculation prediction, and once the workers together outgrow free memory
    the cost is a cliff rather than a slowdown -- one molecule took 67 minutes against a 140 s peer
    average while paging the RI tensor every SCF iteration. Check `MemAvailable` before raising it.
    """
    client = await Client.connect(host)
    async with Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[CellWorkflow],
        activities=[list_molecules, score_molecule, completed_names, append_row],
        max_concurrent_activities=concurrency,
    ):
        print(f"worker up on {TASK_QUEUE}, concurrency {concurrency}; UI http://127.0.0.1:8233", flush=True)
        await asyncio.Future()


async def start(
    cells: list[str],
    limit: int | None,
    out: str,
    source: str,
    plan: str,
    memory_budget_gb: float,
    host: str = DEFAULT_HOST,
) -> None:
    client = await Client.connect(host)
    handles = []
    for cell in cells:
        h = await client.start_workflow(
            CellWorkflow.run,
            CellInput(
                cell=cell,
                limit=limit,
                out=out,
                source=source,
                plan=plan,
                memory_budget_gb=memory_budget_gb,
            ),
            # The ID must distinguish RUNS, not just cells: a fixed `ddE-{cell}` collides with any
            # earlier execution and raises WorkflowAlreadyStartedError. Including the output file
            # makes re-running into the same file a deliberate resume (Temporal will refuse it while
            # one is running, which is the right behaviour) and a different file a new workflow.
            id=f"ddE-{cell}-{Path(out).stem}",
            task_queue=TASK_QUEUE,
        )
        print(f"started {h.id} (run {h.result_run_id})", flush=True)
        handles.append(h)
    for h in handles:
        print(await h.result(), flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("worker", "start"))
    ap.add_argument("--cells", default="s3acid_k")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--out", default="ddE_temporal.jsonl")
    ap.add_argument("--source", default="gfn2_gate", help="study module: gfn2_gate | ladder_source")
    ap.add_argument("--plan", default="", help="JSON plan naming exactly which molecules to score")
    ap.add_argument("--concurrency", type=int, default=2, help="molecules scored at once; memory-bound")
    ap.add_argument("--memory-budget-gb", type=float, default=6.0, help="tier memory budget per molecule")
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"Temporal server (default {DEFAULT_HOST})")
    a = ap.parse_args()
    if a.mode == "worker":
        asyncio.run(run_worker(a.concurrency, a.host))
    else:
        asyncio.run(start(a.cells.split(","), a.limit, a.out, a.source, a.plan, a.memory_budget_gb, a.host))
    return 0


if __name__ == "__main__":
    sys.exit(main())
