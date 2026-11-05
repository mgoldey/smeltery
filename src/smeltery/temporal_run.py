"""`smeltery run CONFIG --out OUT --temporal`: one pipeline run as a durable Temporal workflow.

The pattern of `scripts/temporal_ddE.py`, applied to a whole `smeltery run`. Temporal orchestrates; it does not
compute, and the science is the non-Temporal run's: the same plan (`cli.build_plan`), the same tier settings, the
same pairing, cut and record (`cli.run_plan`, `cli.write_outputs`). What it adds is around each SCF:

* **The unit of work is one score**: (candidate, pose, field model), both legs (vacuum, field) of one pose under one
  field. The field models are the plan's own field (named by `cli.primary_model`: its pdb2pqr force field, else
  "primary") plus every field an active analysis asks for through `extra_fields(plan, ctx) -> {model: [PointCharge]}`.
* **Timeouts and heartbeats**: a score ends after `[run] scf_timeout_s` (default 4 h); it heartbeats while the SCF
  runs, so a dead worker is noticed within the heartbeat timeout, not at the start-to-close timeout.
* **Retries for transient faults only**: `RetryPolicy(maximum_attempts=3)` with `ScfNotConverged` and
  `ClashRefused` non-retryable: chemistry is not retried, it is recorded.
* **The remedy ladder** (`[field_interaction] remedies`): a score whose SCF did not converge (`result.converged`) is
  scheduled again on the next rung. Each leg climbs on its own: a leg that converged is read back from the log, not
  recomputed; each SCF line carries the rung that produced it.
* **Every SCF outcome is appended to OUT/calcs.jsonl as it completes** (fsync'd), with its field model, rung and the
  run's input digest; a score that fails every rung, or fails for another reason, gets an `outcome` line too.
* **Resume**: `prepare` reads the log back; a (candidate, pose, field model) with both legs converged, or with a
  chemistry outcome (every rung failed), is not rescheduled. An infrastructure failure (`outcome = "error"`) IS
  rescheduled by a new workflow run: it is not a measurement.
* **Pairwise drop**: a pose whose primary-field score failed for the parent is dropped for every analogue (they all
  pair to it); one that failed for an analogue is dropped for that analogue and the parent's value at that pose is
  not used in its ddE. Every drop is in `results.dropped_poses` with its reason and the rungs tried; the kept
  original pose indices are in `results.pose_index`. An extra field model's failed score is None in what its
  analysis's `summarize` receives, and is listed in `dropped_poses` too.

The workflow holds only control flow (deterministic: it is replayed from history); every read, write and SCF is in
an activity. Workers run it with `UnsandboxedWorkflowRunner`, as `smeltery task-worker` does.

Run:
    docker run -d --name temporal-dev -p 7233:7233 -p 8233:8233 temporalio/temporal:latest server start-dev --ip 0.0.0.0
    smeltery temporal-worker --workers 6         # in one shell
    smeltery run CONFIG --out OUT --temporal     # starts, or resumes, and waits
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

try:
    from temporalio import activity, workflow
    from temporalio.common import RetryPolicy
    from temporalio.exceptions import ActivityError, ApplicationError

    TEMPORAL_AVAILABLE = True
except ImportError:  # pragma: no cover - the `temporal` extra is not installed
    TEMPORAL_AVAILABLE = False

DEFAULT_HOST = "127.0.0.1:7233"
TASK_QUEUE = "smeltery-pipeline"
DOCKER_COMMAND = (
    "docker run -d --name temporal-dev -p 7233:7233 -p 8233:8233 "
    "temporalio/temporal:latest server start-dev --ip 0.0.0.0"
)
CALCS = "calcs.jsonl"
WORK_DIR = "temporal"
WORK_FILE = "work.json"
#: Non-retryable error types: chemistry refusing, not infrastructure failing.
SCF_NOT_CONVERGED = "ScfNotConverged"
CLASH_REFUSED = "ClashRefused"
#: Outcomes that are final (a measurement result); anything else is retried by a resumed run.
FINAL_OUTCOMES = ("not_converged", "clash_refused")


def server_down_message(host: str, err: object) -> str:
    return (
        f"cannot reach the Temporal server at {host}: {err}\n"
        f"start one with:\n  {DOCKER_COMMAND}\n"
        "(--temporal never falls back to an in-process run)"
    )


# ------------------------------------------------------------------------------------------------ the data passed


@dataclass
class RunInput:
    config: str  # OUT/config.toml (staged): the run reads only from OUT
    out: str
    workers: int = 2
    #: `results.config` of the staged config, so the record equals a non-Temporal run's.
    config_info: dict | None = None
    #: A worker that stops heartbeating for this long is presumed dead and its score is retried.
    heartbeat_s: float = 180.0


@dataclass
class ScoreInput:
    candidate: str
    pose: int
    field_model: str
    rung: int
    out: str = ""
    config: str = ""


@dataclass
class ScoreResult:
    dE: float | None  # kcal/mol; None when the score failed
    rung: int  # the highest rung either leg needed (the rung that produced the value), or the last tried
    cpu_s: float  # wall seconds of the SCFs this attempt ran (legs read back from the log count 0)
    error: str | None = None


@dataclass
class PrepareResult:
    pending: list[ScoreInput]  # each at the rung it starts from
    n_total: int
    n_done: int
    n_rungs: int
    scf_timeout_s: float
    digest: str


@dataclass
class RunSummary:
    record: str
    digest: str
    n_scores: int
    n_resumed: int
    n_failed: int
    dropped_poses: list[dict] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ the SCF log


_LOG_LOCK = threading.Lock()


def calcs_path(out: str | Path) -> Path:
    return Path(out) / CALCS


def append_row(out: str | Path, row: dict) -> None:
    """Append one line to OUT/calcs.jsonl, fsync'd: the durable record the workflow history is not."""
    line = json.dumps(row) + "\n"
    with _LOG_LOCK:
        fd = os.open(calcs_path(out), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)


def read_rows(out: str | Path, digest: str) -> list[dict]:
    """The Temporal SCF lines of this input digest. A line of another digest, or without one (a non-Temporal
    run's), is refused: resuming into a log of different inputs would mix two measurements."""
    p = calcs_path(out)
    if not p.is_file():
        return []
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    foreign = [r for r in rows if r.get("digest") != digest[:12]]
    if foreign:
        got = sorted({str(r.get("digest")) for r in foreign})
        raise ValueError(
            f"{p} holds {len(foreign)} SCF line(s) of another run (digest {got}, this run {digest[:12]}); "
            "use a fresh --out, or remove the log to recompute"
        )
    return rows


def _key(r: dict) -> tuple[str, int, str]:
    return (r["candidate"], int(r["pose"]), r["field_model"])


def unit_state(rows: list[dict], n_rungs: int) -> dict[tuple[str, int, str], dict]:
    """Per (candidate, pose, field model): converged legs, the next rung to try, and any outcome line."""
    out: dict[tuple[str, int, str], dict] = {}
    for r in rows:
        st = out.setdefault(_key(r), {"legs": {}, "failed": {"vacuum": set(), "field": set()}, "outcome": None})
        if "outcome" in r:
            st["outcome"] = r
        elif r.get("converged"):
            st["legs"][r["leg"]] = r
        else:
            st["failed"][r["leg"]].add(int(r["rung"]))
    for st in out.values():
        open_legs = [leg for leg in ("vacuum", "field") if leg not in st["legs"]]
        nxt = [1 + max(st["failed"][leg]) if st["failed"][leg] else 0 for leg in open_legs]
        st["next_rung"] = min(nxt) if nxt else None
        st["done"] = not nxt
    return out


# ------------------------------------------------------------------------------------------------ IO (activities)


def field_models(plan, out: Path | None) -> tuple[dict[str, list], dict[str, str]]:
    """The plan's own field, plus each extra field an active analysis asks for (`extra_fields`); and which
    analysis asked for each extra model."""
    from .cli import primary_model

    models, owner = {primary_model(plan.field): list(plan.field)}, {}
    for spec in remeasuring_analyses(plan.cfg):
        for name, charges in spec.hook.extra_fields(plan, {"log": plan.stage_log, "out_dir": out}).items():
            if name in models:
                raise ValueError(f"[{spec.section}] asks for field model {name!r}, which is already scored")
            models[name], owner[name] = list(charges), spec.key
    return models, owner


def remeasuring_analyses(cfg: dict) -> list:
    """Active analyses whose hook re-measures under extra fields (`extra_fields`)."""
    from .methods import active_hooks

    return [s for s in active_hooks("analysis", cfg) if hasattr(s.hook, "extra_fields")]


def check_tier(tier) -> None:
    from .tiers import FieldInteraction

    if not isinstance(tier, FieldInteraction):
        raise ValueError(
            f"--temporal scores the RHF field interaction (FieldInteraction) per pose; this config measures with "
            f"{tier.name!r}. Run it without --temporal."
        )


def _plan_digest(plan) -> str:
    from .record import RunRecord

    return RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest


def prepare_sync(inp: RunInput) -> PrepareResult:
    """Build the plan exactly as `smeltery run` does, write its poses and fields under OUT/temporal/, and return
    the scores still to run (resume: the log is read back)."""
    from .cli import _net_charge, build_plan, load_config, scf_timeout_s
    from .gates import require_passing_poses

    out = Path(inp.out)
    cfg = load_config(Path(inp.config))
    plan = build_plan(cfg, out_dir=out)
    check_tier(plan.tier)
    for c in plan.candidates:  # the tier's own pre-SCF gate, once, before any score is scheduled
        require_passing_poses(c, {})
    digest = _plan_digest(plan)
    models, owner = field_models(plan, out)
    work = {
        "digest": digest,
        "primary": next(iter(models)),
        "model_owner": owner,
        "candidates": {
            c.name: {
                "smiles": c.smiles,
                "charge": _net_charge(c.smiles),
                "poses": [{"symbols": list(p.symbols), "coords_ang": p.coords_ang.tolist()} for p in c.poses],
            }
            for c in plan.candidates
        },
        "fields": {m: [[float(q.q), *map(float, q.xyz_ang)] for q in charges] for m, charges in models.items()},
    }
    (out / WORK_DIR).mkdir(parents=True, exist_ok=True)
    tmp = out / WORK_DIR / (WORK_FILE + ".tmp")
    tmp.write_text(json.dumps(work))
    tmp.replace(out / WORK_DIR / WORK_FILE)
    n_rungs = plan.tier.n_rungs
    state = unit_state(read_rows(out, digest), n_rungs)
    pending, done = [], 0
    for model in models:
        for c in plan.candidates:
            for i in range(len(c.poses)):
                st = state.get((c.name, i, model))
                if st is not None and (st["done"] or (st["outcome"] or {}).get("outcome") in FINAL_OUTCOMES):
                    done += 1
                    continue
                rung = 0 if st is None else int(st["next_rung"])
                pending.append(ScoreInput(c.name, i, model, rung, inp.out, inp.config))
    return PrepareResult(pending, done + len(pending), done, n_rungs, scf_timeout_s(cfg), digest)


_CACHE: dict = {}


def _work(out: str) -> dict:
    p = Path(out) / WORK_DIR / WORK_FILE
    key = ("work", str(p), p.stat().st_mtime_ns)
    if key not in _CACHE:
        _CACHE[key] = json.loads(p.read_text())
    return _CACHE[key]


def _tier(config: str):
    from .cli import load_config, measurement_tier

    p = Path(config)
    key = ("tier", str(p), p.stat().st_mtime_ns)
    if key not in _CACHE:
        _CACHE[key] = measurement_tier(load_config(p))
    return _CACHE[key]


def score_sync(inp: ScoreInput, beat=lambda stage: None) -> ScoreResult:
    """Both legs of one pose under one field model, at `inp.rung`. A leg already converged (any rung) is read back
    from the log; a leg already tried at this rung is not run again. Raises ScfNotConverged (non-retryable) when a
    leg is still unconverged, after logging every SCF it ran."""
    import ferric

    from .model import HARTREE_TO_KCAL, PointCharge, Pose
    from .tiers import _calc_entry

    work, tier = _work(inp.out), _tier(inp.config)
    digest = work["digest"]
    c = work["candidates"][inp.candidate]
    p = c["poses"][inp.pose]
    import numpy as np

    pose = Pose(tuple(p["symbols"]), np.asarray(p["coords_ang"], dtype=float))
    charges = [PointCharge(q, (x, y, z)).as_ferric_bohr() for q, x, y, z in work["fields"][inp.field_model]]
    st = unit_state(read_rows(inp.out, digest), tier.n_rungs).get((inp.candidate, inp.pose, inp.field_model))
    mol = bs = None
    energies: dict[str, float | None] = {}
    rungs: dict[str, int] = {}
    cpu = 0.0
    for leg, q in (("vacuum", None), ("field", charges)):
        if st is not None and leg in st["legs"]:
            energies[leg], rungs[leg] = float(st["legs"][leg]["energy_hartree"]), int(st["legs"][leg]["rung"])
            continue
        rungs[leg] = inp.rung
        if st is not None and inp.rung in st["failed"][leg]:
            energies[leg] = None
            continue
        if mol is None:
            bs = ferric.BasisSet.bundled(tier.basis)
            mol = ferric.Molecule.from_xyz_string(pose.to_xyz(f"{inp.candidate} pose {inp.pose}"), charge=c["charge"])
        beat(f"{leg} rung {inp.rung}")
        res, secs = tier.scf(ferric, mol, bs, q, inp.rung)
        cpu += secs
        row = _calc_entry(inp.candidate, inp.pose, leg, res, res.energy, secs)
        row.update(field_model=inp.field_model, rung=inp.rung, digest=digest[:12])
        append_row(inp.out, row)
        energies[leg] = float(res.energy) if res.converged else None
    if energies["vacuum"] is None or energies["field"] is None:
        bad = [leg for leg in ("vacuum", "field") if energies[leg] is None]
        raise ApplicationError(
            f"{inp.candidate} pose {inp.pose} ({inp.field_model}): SCF did not converge at rung {inp.rung} "
            f"({', '.join(bad)})",
            type=SCF_NOT_CONVERGED,
            non_retryable=True,
        )
    e_field, e_vac = energies["field"], energies["vacuum"]
    return ScoreResult((e_field - e_vac) * HARTREE_TO_KCAL, max(rungs.values()), cpu)


def finalize_sync(inp: RunInput, n_resumed: int = 0) -> RunSummary:
    """The run record, from the per-SCF log, exactly as a non-Temporal run builds it (`run_plan`, `write_outputs`)."""
    import numpy as np

    from .cli import StageError, build_plan, load_config, run_plan, write_outputs
    from .model import HARTREE_TO_KCAL

    out = Path(inp.out)
    cfg = load_config(Path(inp.config))
    plan = build_plan(cfg, out_dir=out)
    digest = _plan_digest(plan)
    work = json.loads((out / WORK_DIR / WORK_FILE).read_text())
    if work["digest"] != digest:
        raise ValueError(f"the plan rebuilt for the record (digest {digest[:12]}) is not the one scored")
    for c in plan.candidates:  # the scored poses must be the poses the record states
        got = [np.asarray(p["coords_ang"]) for p in work["candidates"][c.name]["poses"]]
        if len(got) != len(c.poses) or any(not np.array_equal(g, p.coords_ang) for g, p in zip(got, c.poses)):
            raise ValueError(f"{c.name}: the rebuilt poses differ from the scored ones")
    rows = read_rows(out, digest)
    state = unit_state(rows, plan.tier.n_rungs)
    primary = work["primary"]
    remedies = bool(plan.tier.remedies)

    values: dict[str, dict[str, list]] = {m: {} for m in work["fields"]}
    rung_of: dict[str, list] = {}
    dropped: list[dict] = []
    failed: dict[str, set[int]] = {c.name: set() for c in plan.candidates}
    for model in work["fields"]:
        for c in plan.candidates:
            vals = []
            for i in range(len(c.poses)):
                st = state.get((c.name, i, model))
                if st is None or not st["done"]:
                    o = (st or {}).get("outcome") or {}
                    if not o:
                        raise ValueError(f"{c.name} pose {i} ({model}): no outcome in the log; resume the run")
                    vals.append(None)
                    dropped.append(
                        {
                            "candidate": c.name,
                            "pose": i,
                            "field_model": model,
                            "reason": o.get("error") or o.get("outcome"),
                            "outcome": o.get("outcome"),
                            "rungs_tried": o.get("rungs_tried", []),
                        }
                    )
                    if model == primary:
                        failed[c.name].add(i)
                    continue
                legs = st["legs"]
                vals.append((legs["field"]["energy_hartree"] - legs["vacuum"]["energy_hartree"]) * HARTREE_TO_KCAL)
                if model == primary:
                    rung_of.setdefault(c.name, [None] * len(c.poses))[i] = max(
                        int(legs["vacuum"]["rung"]), int(legs["field"]["rung"])
                    )
            values[model][c.name] = vals

    parent = plan.parent
    pose_index = None
    if any(failed.values()):
        pose_index = {parent.name: [i for i in range(len(parent.poses)) if i not in failed[parent.name]]}
        for c in plan.candidates:
            if c is parent:
                continue
            gone = failed[c.name] | failed[parent.name]
            pose_index[c.name] = [i for i in range(len(c.poses)) if i not in gone]
            for d in dropped:  # who the drop took with it
                if d["field_model"] == primary and d["candidate"] == c.name:
                    d["dropped_with"] = [parent.name, c.name]
        for d in dropped:
            if d["field_model"] == primary and d["candidate"] == parent.name:
                d["dropped_with"] = [c.name for c in plan.candidates]
        empty = [k for k, v in pose_index.items() if not v]
        if empty:
            raise StageError(f"stage 'pairing' failed: every pose of {', '.join(empty)} was dropped")

    def fill(p):
        for c in p.candidates:
            keep = pose_index[c.name] if pose_index else list(range(len(c.poses)))
            c.poses = [c.poses[i] for i in keep]
            c.per_pose["dE_int"] = [float(values[primary][c.name][i]) for i in keep]
            if remedies:
                c.per_pose["scf_rung"] = [float(rung_of[c.name][i]) for i in keep]

    summarized = remeasuring_analyses(cfg)
    rec, _ = run_plan(plan, measure=fill, skip_analyses=frozenset(s.key for s in summarized), pose_index=pose_index)
    for spec in summarized:
        fn = getattr(spec.hook, "summarize", None)
        if fn is None:
            raise ValueError(f"[{spec.section}] has extra_fields but no summarize: --temporal cannot finish it")
        measured = {m: v for m, v in values.items() if work["model_owner"].get(m) == spec.key}
        got = fn(plan, rec, measured)
        if not isinstance(got, dict):
            raise StageError(f"stage {spec.key!r} failed: summarize returns a dict, got {type(got).__name__}")
        rec.results[spec.key] = {**(rec.results.get(spec.key) or {}), **got}
    if dropped:
        rec.results["dropped_poses"] = dropped
    if inp.config_info is not None:
        rec.results["config"] = inp.config_info
    path = write_outputs(plan, rec, out, calc_rows=rows)
    n_scores = sum(len(c) for m in values.values() for c in m.values())
    return RunSummary(str(path), rec.input_digest, n_scores, n_resumed, len(dropped), dropped)


# ------------------------------------------------------------------------------------------------ Temporal

if TEMPORAL_AVAILABLE:

    @activity.defn(name="pipeline_prepare")
    async def prepare(inp: RunInput) -> PrepareResult:
        try:
            return await _with_heartbeat(lambda beat: prepare_sync(inp))
        except ApplicationError:
            raise
        except Exception as e:  # a config or stage error: the same inputs fail the same way every time
            raise ApplicationError(f"{type(e).__name__}: {e}", type="PrepareFailed", non_retryable=True) from e

    @activity.defn(name="pipeline_score")
    async def score(inp: ScoreInput) -> ScoreResult:
        return await _with_heartbeat(lambda beat: score_sync(inp, beat))

    @activity.defn(name="pipeline_record_outcome")
    async def record_outcome(out: str, row: dict) -> None:
        await asyncio.to_thread(append_row, out, row)

    @activity.defn(name="pipeline_finalize")
    async def finalize(inp: RunInput, n_resumed: int) -> RunSummary:
        try:
            return await _with_heartbeat(lambda beat: finalize_sync(inp, n_resumed))
        except Exception as e:
            raise ApplicationError(f"{type(e).__name__}: {e}", type="FinalizeFailed", non_retryable=True) from e

    async def _with_heartbeat(call):
        """Run `call(beat)` in a thread, heartbeating from the event loop until it returns: an SCF is one blocking call,
        so the heartbeat cannot come from inside it."""
        info = activity.info()
        every = max(0.5, (info.heartbeat_timeout.total_seconds() / 4) if info.heartbeat_timeout else 30.0)
        stage = {"now": "start"}

        def beat(s: str) -> None:
            stage["now"] = s

        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(None, call, beat)
        activity.heartbeat(stage["now"])
        while True:
            done, _ = await asyncio.wait({fut}, timeout=every)
            if done:
                return fut.result()
            activity.heartbeat(stage["now"])

    @workflow.defn(name="PipelineRunWorkflow")
    class PipelineRunWorkflow:
        """One pipeline run: prepare, every score (up the remedy ladder), finalize. Control flow only."""

        def __init__(self) -> None:
            self._progress = {"total": 0, "done": 0, "failed": 0, "running": 0, "phase": "prepare"}

        @workflow.query
        def progress(self) -> dict:
            return dict(self._progress)

        @workflow.run
        async def run(self, inp: RunInput) -> RunSummary:
            prep = await workflow.execute_activity(
                prepare,
                inp,
                start_to_close_timeout=timedelta(hours=6),
                heartbeat_timeout=timedelta(seconds=inp.heartbeat_s),
                retry_policy=RetryPolicy(maximum_attempts=3, non_retryable_error_types=["PrepareFailed"]),
            )
            self._progress.update(total=prep.n_total, done=prep.n_done, phase="score")
            sem = asyncio.Semaphore(max(1, inp.workers))

            async def one(u: ScoreInput) -> None:
                async with sem:
                    self._progress["running"] += 1
                    try:
                        await self._climb(inp, prep, u)
                    finally:
                        self._progress["running"] -= 1
                        self._progress["done"] += 1

            await asyncio.gather(*(one(u) for u in prep.pending))
            self._progress["phase"] = "finalize"
            summary = await workflow.execute_activity(
                finalize,
                args=[inp, prep.n_done],
                start_to_close_timeout=timedelta(hours=6),
                heartbeat_timeout=timedelta(seconds=inp.heartbeat_s),
                retry_policy=RetryPolicy(maximum_attempts=3, non_retryable_error_types=["FinalizeFailed"]),
            )
            self._progress["phase"] = "done"
            return summary

        async def _climb(self, inp: RunInput, prep: PrepareResult, u: ScoreInput) -> None:
            tried: list[int] = list(range(u.rung))
            outcome, error = "not_converged", "every rung of the remedy ladder was tried before this run resumed"
            for rung in range(u.rung, prep.n_rungs):
                tried.append(rung)
                try:
                    await workflow.execute_activity(
                        score,
                        ScoreInput(u.candidate, u.pose, u.field_model, rung, u.out, u.config),
                        start_to_close_timeout=timedelta(seconds=prep.scf_timeout_s),
                        heartbeat_timeout=timedelta(seconds=inp.heartbeat_s),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(seconds=5),
                            maximum_attempts=3,
                            non_retryable_error_types=[SCF_NOT_CONVERGED, CLASH_REFUSED],
                        ),
                    )
                    return
                except ActivityError as e:
                    cause = e.cause
                    kind = getattr(cause, "type", None)
                    error = str(cause or e)[:400]
                    if kind == SCF_NOT_CONVERGED:
                        continue  # chemistry: the next rung, never a retry of this one
                    outcome = "clash_refused" if kind == CLASH_REFUSED else "error"
                    break
            self._progress["failed"] += 1
            row = {
                "candidate": u.candidate,
                "pose": u.pose,
                "field_model": u.field_model,
                "outcome": outcome,
                "rungs_tried": tried,
                "error": error,
                "digest": prep.digest[:12],
            }
            await workflow.execute_activity(
                record_outcome, args=[inp.out, row], start_to_close_timeout=timedelta(minutes=2)
            )


def worker_workflows() -> list:
    return [PipelineRunWorkflow] if TEMPORAL_AVAILABLE else []


def worker_activities() -> list:
    return [prepare, score, record_outcome, finalize] if TEMPORAL_AVAILABLE else []


def workflow_id(out: str | Path, config_text: str) -> str:
    """The same OUT and the same staged config (inputs staged by content) are the same run: start resumes it."""
    h = hashlib.sha256(f"{Path(out).resolve()}\n{hashlib.sha256(config_text.encode()).hexdigest()}".encode())
    return "smeltery-run-" + h.hexdigest()[:20]


async def connect(host: str, namespace: str = "default"):
    from temporalio.client import Client

    return await Client.connect(host, namespace=namespace)


async def run_worker(client, task_queue: str = TASK_QUEUE, workers: int = 2, stop: asyncio.Event | None = None):
    """A pipeline worker: `workers` scores at once (each SCF uses RAYON_NUM_THREADS cores; memory is the limit)."""
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    async with Worker(
        client,
        task_queue=task_queue,
        workflows=worker_workflows(),
        activities=worker_activities(),
        workflow_runner=UnsandboxedWorkflowRunner(),
        max_concurrent_activities=max(1, workers),
    ):
        print(f"smeltery pipeline worker up on {task_queue}, {workers} worker(s) (Ctrl+C stops)", flush=True)
        await (stop.wait() if stop is not None else asyncio.Future())


async def start_and_wait(
    client, inp: RunInput, task_queue: str = TASK_QUEUE, poll_s: float = 10.0, log=print
) -> RunSummary:
    """Start the run's workflow, or attach to it when one with its id is running, and wait for it."""
    from temporalio.common import WorkflowIDConflictPolicy

    wid = workflow_id(inp.out, Path(inp.config).read_text())
    handle = await client.start_workflow(
        PipelineRunWorkflow.run,
        inp,
        id=wid,
        task_queue=task_queue,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    log(f"workflow {wid} on {task_queue} (UI http://127.0.0.1:8233); needs `smeltery temporal-worker` running")
    result = asyncio.ensure_future(handle.result())
    last = None
    while True:
        done, _ = await asyncio.wait({result}, timeout=poll_s)
        if done:
            return result.result()
        try:
            p = await handle.query(PipelineRunWorkflow.progress)
        except Exception:  # no worker has picked it up yet
            p = None
        if p != last and p is not None:
            log(f"  {p['phase']}: {p['done']}/{p['total']} scores, {p['running']} running, {p['failed']} failed")
            last = p


def run_cli(
    config: Path, out: Path, workers: int, host: str, task_queue: str, config_info: dict | None, poll_s: float = 10.0
) -> int:
    """`smeltery run --temporal`: 0 done, 2 the server is down (never a silent fallback), 3 the run failed."""
    if not TEMPORAL_AVAILABLE:
        print("config error: --temporal needs temporalio (the `temporal` extra)", file=sys.stderr)
        return 2
    from temporalio.client import WorkflowFailureError

    async def main() -> int:
        try:
            client = await connect(host)
        except Exception as e:
            print(server_down_message(host, e), file=sys.stderr)
            return 2
        inp = RunInput(str(Path(config).resolve()), str(Path(out).resolve()), workers, config_info)
        try:
            s = await start_and_wait(client, inp, task_queue, poll_s)
        except WorkflowFailureError as e:
            print(f"error: the Temporal run failed: {e.cause or e}", file=sys.stderr)
            return 3
        for d in s.dropped_poses:
            print(f"dropped: {d['candidate']} pose {d['pose']} ({d['field_model']}): {d['reason']}")
        print(f"\nrun record: {s.record}\ninput_digest {s.digest}")
        print(f"{s.n_scores} scores ({s.n_resumed} read back from the log), {s.n_failed} failed")
        return 0

    return asyncio.run(main())


def worker_cli(host: str, task_queue: str, workers: int) -> int:
    """`smeltery temporal-worker`: 2 when the server is down."""
    if not TEMPORAL_AVAILABLE:
        print("temporalio is not installed", file=sys.stderr)
        return 2

    async def main() -> int:
        try:
            client = await connect(host)
        except Exception as e:
            print(server_down_message(host, e), file=sys.stderr)
            return 2
        await run_worker(client, task_queue, workers)
        return 0

    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        return 0
