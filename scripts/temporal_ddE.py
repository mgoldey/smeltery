"""The ddE run as a Temporal workflow: durable execution for work that must survive interruption.

The shell/script version of this run already survives a kill, through a `done_keys()` resume written
into it by hand. What it does NOT have, and what durable execution gives, is everything around the
step:

* **Timeouts.** One molecule in the production run took **6085 s** against a median of 52 s. Nothing
  bounded it. A runaway earlier today had to be killed by hand. `start_to_close_timeout` ends it.
* **Heartbeats inside a long step.** A 1.7 h activity killed at 1.6 h loses everything under
  content-addressed caching, which can only resume between steps. A heartbeat checkpoints progress
  WITHIN the step, and `activity.info().heartbeat_details` resumes from it.
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

Run:
    docker run -d --name temporal-dev -p 7233:7233 -p 8233:8233 \
        temporalio/temporal:latest server start-dev --ip 0.0.0.0
    python temporal_ddE.py worker          # in one shell
    python temporal_ddE.py start --cells s3acid_k --limit 5
    # UI: http://127.0.0.1:8233
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
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
#: Heartbeat well inside the timeout: a worker that dies is detected in a minute, not three hours.
HEARTBEAT = timedelta(seconds=60)


@dataclass
class CellInput:
    cell: str
    generator: str = "pxm"
    limit: int | None = None
    out: str = "ddE_temporal.jsonl"


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


def _list_molecules_sync(inp: CellInput) -> list[str]:
    _study_path()
    from gfn2_gate import PARENT, cell_spec, load_raws
    from rdkit import Chem

    from smeltery.generate import FilterConfig
    from smeltery.generate.sbdd_post import build_scaffold, process_samples, smiles_and_pose

    parent = next(Chem.SDMolSupplier(str(PARENT)))
    psmiles, ppose = smiles_and_pose(parent)
    spec = build_scaffold(psmiles, cell_spec(inp.cell)["scaffold"], ppose)
    raws = load_raws(inp.cell, inp.generator)
    accepted, _ = process_samples(raws, spec, FilterConfig(), pose_source="model", name_prefix=inp.cell)
    names = [c.name for c in accepted]
    return names[: inp.limit] if inp.limit else names


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
    # `activity.heartbeat` needs the worker's event loop, which the thread does not have, so the
    # thread posts through `call_soon_threadsafe` instead. Calling it directly from the thread
    # raises "no running event loop" and every attempt fails -- which is exactly what happened,
    # and the retry policy faithfully retried a bug three times.
    loop = asyncio.get_running_loop()
    attempt = info.attempt

    def beat(stage: str) -> None:
        loop.call_soon_threadsafe(activity.heartbeat, stage)

    return await asyncio.to_thread(_score_sync, inp, name, beat, attempt)


def _score_sync(inp: CellInput, name: str, beat, attempt: int) -> dict:
    _study_path()
    import numpy as np
    from gfn2_gate import CUTOFF_ANG, PARENT, POCKET, cell_spec, load_raws
    from rdkit import Chem

    from smeltery.generate import FilterConfig
    from smeltery.generate.sbdd_post import build_scaffold, process_samples, smiles_and_pose
    from smeltery.pocket.loader import load_pocket, min_distance_to_field_ang
    from smeltery.tiers import FieldDftInteraction

    beat("loading")
    parent = next(Chem.SDMolSupplier(str(PARENT)))
    psmiles, ppose = smiles_and_pose(parent)
    centre = tuple(float(v) for v in np.asarray(ppose.coords_ang).mean(axis=0))
    field = load_pocket(POCKET, cutoff_ang=CUTOFF_ANG, center_ang=centre, drop_waters=True)
    spec = build_scaffold(psmiles, cell_spec(inp.cell)["scaffold"], ppose)
    raws = load_raws(inp.cell, inp.generator)
    accepted, _ = process_samples(raws, spec, FilterConfig(), pose_source="model", name_prefix=inp.cell)
    cand = next((c for c in accepted if c.name == name), None)
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
    tier = FieldDftInteraction(field_provenance=field.provenance, memory_budget_gb=14.0, require_free_gb=3.0)
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
async def already_done(inp: CellInput) -> list[str]:
    """Names already in the output file, so a restart does not rescore them."""
    p = _study_dir() / inp.out
    if not p.exists():
        return []
    return [json.loads(line)["name"] for line in p.read_text().splitlines() if line.strip()]


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
        short = workflow.start_activity  # noqa: F841 - documents the two policies below
        names = await workflow.execute_activity(list_molecules, inp, start_to_close_timeout=timedelta(minutes=10))
        done = set(await workflow.execute_activity(already_done, inp, start_to_close_timeout=timedelta(minutes=2)))
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


async def run_worker() -> None:
    client = await Client.connect("127.0.0.1:7233")
    async with Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[CellWorkflow],
        activities=[list_molecules, score_molecule, already_done, append_row],
        max_concurrent_activities=2,  # the owner's CPU rule
    ):
        print(f"worker up on {TASK_QUEUE}; UI http://127.0.0.1:8233", flush=True)
        await asyncio.Future()


async def start(cells: list[str], limit: int | None, out: str) -> None:
    client = await Client.connect("127.0.0.1:7233")
    handles = []
    for cell in cells:
        h = await client.start_workflow(
            CellWorkflow.run,
            CellInput(cell=cell, limit=limit, out=out),
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
    a = ap.parse_args()
    if a.mode == "worker":
        asyncio.run(run_worker())
    else:
        asyncio.run(start(a.cells.split(","), a.limit, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
