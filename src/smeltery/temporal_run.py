"""`smeltery run CONFIG --out OUT --temporal`: one pipeline run as a durable Temporal workflow.

The pattern of `scripts/temporal_ddE.py`, applied to a whole `smeltery run`. Temporal orchestrates; it does not
compute, and the science is the non-Temporal run's: the same plan (`cli.build_plan`), the same tier settings, the
same pairing, cut and record (`cli.run_plan`, `cli.write_outputs`). What it adds is around each SCF:

* **The unit of work is one SCF leg**: (candidate, pose, leg, field model). A field leg is one per field model: the
  plan's own field (named by `cli.primary_model`: its pdb2pqr force field, else "primary") and every field an active
  analysis asks for through `extra_fields(plan, ctx) -> {model: [PointCharge]}`. The vacuum leg has no field model:
  it is computed once per (candidate, pose) and shared by every model (its log lines carry `field_model: null`).
* **Each leg runs in its own child process** (`python -m smeltery.temporal_run _score`, its own process group, with
  `RAYON_NUM_THREADS=threads_per_worker`): ferric's thread pool is process-wide, so threads in one process would
  share it. A cancel or a timeout kills the child's process group; only the activity writes the log line, after the
  child has returned, so a killed child leaves no row.
* **Timeouts and heartbeats**: a leg ends after `[run] scf_timeout_s` (default 4 h); the activity heartbeats while its
  child runs, so a dead worker is noticed within the heartbeat timeout.
* **Retries for transient faults only**: `RetryPolicy(maximum_attempts=3)` with `ScfNotConverged`, `ClashRefused`
  and `ConfigChanged` non-retryable: chemistry is not retried, it is recorded.
* **The remedy ladder** (`[field_interaction] remedies`): a leg whose SCF did not converge (`result.converged`) is
  scheduled again on the next rung; each leg climbs on its own; each SCF line carries its rung.
* **Every SCF is appended to OUT/calcs.jsonl as it completes** (fsync'd), with its field model, rung and the run's
  input digest; a leg that fails every rung (chemistry) or fails for another reason (infrastructure) gets an
  `outcome` line too.
* **Resume**: `prepare` reads the log back. A converged leg, or one that failed every rung, is not rescheduled; an
  infrastructure failure (`outcome = "error"`) is, by the next workflow run. `finalize` refuses (the run exits 3,
  naming them) while any infrastructure failure remains: an error is not a measurement.
* **Pairwise drop** (chemistry failures only): a pose whose primary score failed for the parent is dropped for every
  analogue; one that failed for an analogue is dropped for that analogue and the parent's value at that pose is not in
  its ddE. Every drop is in `results.dropped_poses`; the kept original pose indices are in `results.pose_index`. An
  extra field model's failed score is None in what its analysis's `summarize` receives.
* **The staged config is fixed while the run is live**: activities carry its sha256 and refuse a changed file, and
  `smeltery run --out OUT` refuses to re-stage over a running workflow (`guard_out`).

The workflow holds only control flow (deterministic: it is replayed from history); every read, write and SCF is in
an activity. Workers run it with `UnsandboxedWorkflowRunner`, as `smeltery task-worker` does.

Run:
    docker run -d --name temporal-dev -p 7233:7233 -p 8233:8233 temporalio/temporal:latest server start-dev --ip 0.0.0.0
    smeltery temporal-worker --workers 6 --threads-per-worker 1   # in one shell
    smeltery run CONFIG --out OUT --temporal                      # starts, or resumes, and waits
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

try:
    from temporalio import activity, workflow
    from temporalio.common import RetryPolicy
    from temporalio.exceptions import ActivityError, ApplicationError
    from temporalio.exceptions import TimeoutError as TimeoutError_

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
MARKER = "running.json"
RESULT_PREFIX = "SMELTERY_SCORE_RESULT "
#: Non-retryable error types: chemistry refusing, or the run's inputs changing under it; not infrastructure failing.
SCF_NOT_CONVERGED = "ScfNotConverged"
CLASH_REFUSED = "ClashRefused"
CONFIG_CHANGED = "ConfigChanged"
#: Outcomes that are measurements (final); anything else ("timeout", "error") is rescheduled by a resumed run, and
#: blocks finalize.
FINAL_OUTCOMES = ("not_converged", "clash_refused")
TIMEOUT = "Timeout"
#: The server's start-to-close timeout is the leg's own limit plus this margin, so the activity's local timeout (which
#: kills the child and reports "Timeout", non-retryable) always fires first; a server-side timeout would be retried.
SERVER_TIMEOUT_MARGIN_S = 120.0
LEGS = ("vacuum", "field")


def server_down_message(host: str, err: object) -> str:
    return (
        f"cannot reach the Temporal server at {host}: {err}\n"
        f"start one with:\n  {DOCKER_COMMAND}\n"
        "(--temporal never falls back to an in-process run)"
    )


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def code_identity() -> dict:
    """The build this process runs: `{"ferric": {"git_sha", "dirty"}, "smeltery": {"commit", "dirty"}}` (ferric from
    `ferric.__build__`, None where it has none; smeltery from its install, an editable one by its checkout's HEAD and
    dirty flag). Every calcs.jsonl row an activity writes carries it, so finalize can say which code measured what."""
    from .record import _ferric_build, smeltery_identity

    fb = _ferric_build()
    sm = smeltery_identity()
    ferric = {"git_sha": fb["git_sha"], "dirty": bool(fb.get("dirty"))} if fb else {"git_sha": None, "dirty": None}
    return {"ferric": ferric, "smeltery": {"commit": sm.get("commit"), "dirty": sm.get("dirty")}}


# ------------------------------------------------------------------------------------------------ the data passed


@dataclass
class RunInput:
    config: str  # OUT/config.toml (staged): the run reads only from OUT
    out: str
    workers: int = 2
    #: `results.config` of the staged config, so the record equals a non-Temporal run's.
    config_info: dict | None = None
    #: A worker that stops heartbeating for this long is presumed dead and its leg is retried.
    heartbeat_s: float = 180.0
    #: sha256 of the staged config: every activity refuses a changed file.
    config_sha: str = ""


@dataclass
class ScoreInput:
    candidate: str
    pose: int
    leg: str  # "vacuum" | "field"
    field_model: str | None  # None for the vacuum leg: shared by every field model
    rung: int
    out: str = ""
    config: str = ""
    config_sha: str = ""
    timeout_s: float = 0.0  # `[run] scf_timeout_s`: the activity ends the leg (and its child) after this long


@dataclass
class ScoreResult:
    energy_hartree: float | None
    rung: int
    cpu_s: float  # wall seconds of the child's SCF (0 when read back from the log)
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
    n_scfs: int
    n_resumed: int
    n_failed: int
    n_duplicates: int = 0
    n_torn: int = 0
    dropped_poses: list[dict] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ the SCF log


_LOG_LOCK = threading.Lock()


def calcs_path(out: str | Path) -> Path:
    return Path(out) / CALCS


def append_row(out: str | Path, row: dict) -> None:
    """Append one line to OUT/calcs.jsonl, fsync'd. A torn last line (no newline) is closed first, so the new row is
    never glued onto it."""
    line = json.dumps(row) + "\n"
    p = calcs_path(out)
    with _LOG_LOCK:
        fd = os.open(p, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                line = "\n" + line
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)


def read_rows(out: str | Path, digest: str) -> tuple[list[dict], int]:
    """(the Temporal SCF lines of this input digest, how many unreadable lines were skipped). A torn line (a write
    cut short) is skipped and counted, never a crash. A line of another digest, or without one (a non-Temporal run's),
    is refused: resuming into a log of different inputs would mix two measurements."""
    p = calcs_path(out)
    if not p.is_file():
        return [], 0
    rows, torn = [], 0
    for x in p.read_text().splitlines():
        if not x.strip():
            continue
        try:
            r = json.loads(x)
        except ValueError:
            torn += 1
            continue
        if not isinstance(r, dict):
            torn += 1
            continue
        rows.append(r)
    old = [r for r in rows if "leg" not in r]
    if old:
        raise ValueError(
            f"{p} is the log of a --temporal run from before per-leg scores (7bdaebc): {len(old)} of its outcome "
            "line(s) name no leg, so its scores cannot be read back leg by leg; use a fresh --out"
        )
    foreign = [r for r in rows if r.get("digest") != digest[:12]]
    if foreign:
        got = sorted({str(r.get("digest")) for r in foreign})
        raise ValueError(
            f"{p} holds {len(foreign)} SCF line(s) of another run (digest {got}, this run {digest[:12]}); "
            "use a fresh --out, or remove the log to recompute"
        )
    return rows, torn


def _ident(r: dict) -> tuple:
    return (r["candidate"], int(r["pose"]), r["leg"], r.get("field_model"))


def dedupe(rows: list[dict]) -> tuple[list[dict], int]:
    """SCF lines unique by (candidate, pose, leg, field model, rung), the first kept; outcome lines all kept."""
    seen, out, dup = set(), [], 0
    for r in rows:
        if "outcome" not in r:
            k = (*_ident(r), int(r["rung"]))
            if k in seen:
                dup += 1
                continue
            seen.add(k)
        out.append(r)
    return out, dup


def leg_state(rows: list[dict]) -> dict[tuple, dict]:
    """Per leg identity: the converged line (if any), the rungs that failed, the last outcome line, the next rung.
    A line whose convergence the ladder refuses (`refuse_false_convergence`, against the leg's lower rungs) counts
    as a failed rung, whenever it was written."""
    from .tiers import refuse_false_convergence

    out: dict[tuple, dict] = {}
    scfs: dict[tuple, list[dict]] = {}
    for r in rows:
        if "outcome" not in r:
            scfs.setdefault(_ident(r), []).append(r)
    judged = {
        id(r): refuse_false_convergence(r, [e for e in legs if int(e["rung"]) < int(r["rung"])])
        for legs in scfs.values()
        for r in legs
    }
    for r in (judged.get(id(r), r) for r in rows):
        st = out.setdefault(_ident(r), {"converged": None, "failed": set(), "outcome": None})
        if "outcome" in r:
            st["outcome"] = r
        elif r.get("converged"):
            st["converged"] = st["converged"] or r
        else:
            st["failed"].add(int(r["rung"]))
    for st in out.values():
        st["next_rung"] = 1 + max(st["failed"]) if st["failed"] else 0
        st["final"] = st["converged"] is not None or (st["outcome"] or {}).get("outcome") in FINAL_OUTCOMES
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


def check_config(config: str, want: str) -> None:
    """The staged config is the one this run started with, or ValueError."""
    got = sha256_file(config)
    if want and got != want:
        raise ValueError(
            f"{config} changed under a running workflow (sha256 {got[:12]}, the run's {want[:12]}); "
            "its activities refuse to read a different config"
        )


def _plan_digest(plan) -> str:
    from .record import RunRecord

    return RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest


def legs_of(plan, models) -> list[tuple[str, int, str, str | None]]:
    """Every leg identity of the plan: one vacuum leg per pose, one field leg per pose and field model."""
    out = []
    for c in plan.candidates:
        for i in range(len(c.poses)):
            out.append((c.name, i, "vacuum", None))
            out += [(c.name, i, "field", m) for m in models]
    return out


def prepare_sync(inp: RunInput) -> PrepareResult:
    """Build the plan exactly as `smeltery run` does, check the log is this run's (before writing anything), write
    the poses and fields under OUT/temporal/, and return the legs still to run (resume: the log is read back)."""
    from .cli import _net_charge, build_plan, load_config, scf_timeout_s
    from .gates import require_passing_poses

    out = Path(inp.out)
    check_config(inp.config, inp.config_sha)
    cfg = load_config(Path(inp.config))
    plan = build_plan(cfg, out_dir=out)
    check_tier(plan.tier)
    for c in plan.candidates:  # the tier's own pre-SCF gate, once, before any leg is scheduled
        require_passing_poses(c, {})
    digest = _plan_digest(plan)
    rows, _ = read_rows(out, digest)  # refuses a foreign log before anything is written
    models, owner = field_models(plan, out)
    knobs = {f.name: getattr(plan.tier, f.name) for f in dataclasses.fields(plan.tier) if f.init}
    work = {
        "digest": digest,
        "tier": {k: v for k, v in knobs.items() if k not in ("name", "field_provenance")},
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
    os.replace(tmp, out / WORK_DIR / WORK_FILE)
    state = leg_state(dedupe(rows)[0])
    pending, done = [], 0
    limit = scf_timeout_s(cfg)
    for ident in legs_of(plan, models):
        st = state.get(ident)
        if st is not None and st["final"]:
            done += 1
            continue
        if ident[2] == "field" and vacuum_failed(state, ident[0], ident[1]):
            done += 1  # its pose's vacuum failed every rung: the field legs are not run
            continue
        rung = 0 if st is None else int(st["next_rung"])
        pending.append(ScoreInput(*ident, rung, inp.out, inp.config, inp.config_sha, limit))
    return PrepareResult(pending, done + len(pending), done, plan.tier.n_rungs, limit, digest)


def vacuum_failed(state: dict, candidate: str, pose: int) -> bool:
    """The pose's vacuum leg is a chemistry failure (every rung): its field legs give no value and are not run."""
    st = state.get((candidate, pose, "vacuum", None))
    return st is not None and st["converged"] is None and st["final"]


def _work(out: str) -> dict:
    return json.loads((Path(out) / WORK_DIR / WORK_FILE).read_text())


def scf_child(inp: ScoreInput) -> dict:
    """One SCF leg, in the child process: {converged, iterations, energy_hartree, seconds, build}."""
    import ferric
    import numpy as np

    from .model import PointCharge, Pose
    from .tiers import FieldInteraction

    check_config(inp.config, inp.config_sha)
    build = code_identity()  # read now, as near the import of the code that runs as can be
    work = _work(inp.out)
    tier = FieldInteraction(**work["tier"])  # the plan's tier knobs, as prepare built them: no config parsing here
    c = work["candidates"][inp.candidate]
    p = c["poses"][inp.pose]
    pose = Pose(tuple(p["symbols"]), np.asarray(p["coords_ang"], dtype=float))
    charges = None
    if inp.leg == "field":
        charges = [PointCharge(q, (x, y, z)).as_ferric_bohr() for q, x, y, z in work["fields"][inp.field_model]]
    bs = ferric.BasisSet.bundled(tier.basis)
    mol = ferric.Molecule.from_xyz_string(pose.to_xyz(f"{inp.candidate} pose {inp.pose}"), charge=c["charge"])
    res, secs = tier.scf(ferric, mol, bs, charges, inp.rung)
    return {
        "converged": bool(res.converged),
        "iterations": int(res.iterations),
        "energy_hartree": float(res.energy),
        "seconds": float(secs),
        "build": build,  # the child's: it imported the code that ran this SCF
    }


def scf_row(inp: ScoreInput, got: dict, digest: str, started_utc: str) -> dict:
    """The calcs.jsonl line of one SCF leg the child ran (`got`, `scf_child`'s dict): the tier's row, plus its field
    model, rung, the run's input digest, when it started and the build that ran it."""
    from types import SimpleNamespace

    from .tiers import _calc_entry

    res = SimpleNamespace(converged=got["converged"], iterations=got["iterations"])
    row = _calc_entry(inp.candidate, inp.pose, inp.leg, res, got["energy_hartree"], got["seconds"])
    row.update(field_model=inp.field_model, rung=inp.rung, digest=digest[:12])
    row.update(started_utc=started_utc, build=got.get("build"))
    return row


def child_main(argv: list[str]) -> int:
    """`python -m smeltery.temporal_run _score '<ScoreInput json>'`: print the leg's result on a marked line."""
    try:
        got = scf_child(ScoreInput(**json.loads(argv[-1])))
    except Exception:  # reported to the parent on stderr (its last line names the exception); exit code 1
        import traceback

        traceback.print_exc(file=sys.stderr)
        return 1
    print(RESULT_PREFIX + json.dumps(got), flush=True)
    return 0


_PRCTL: list = []  # [libc.prctl or None], loaded once, in the parent


def _die_with_parent_hook():
    """A preexec_fn that only calls prctl(PR_SET_PDEATHSIG, SIGKILL): the child is killed when the worker dies, so no
    orphan keeps burning CPU (Linux). libc is loaded here, once, in the parent; None where there is no prctl (the
    process group kill on cancel still applies)."""
    if not _PRCTL:
        try:
            import ctypes

            _PRCTL.append(ctypes.CDLL("libc.so.6", use_errno=True).prctl)
        except Exception:
            _PRCTL.append(None)
    prctl, sig = _PRCTL[0], int(signal.SIGKILL)
    if prctl is None:
        return None

    def hook() -> None:
        prctl(1, sig)  # PR_SET_PDEATHSIG

    return hook


def child_command() -> list[str]:
    return [sys.executable, "-m", "smeltery.temporal_run", "_score"]


def _refusal(blocked: list[tuple[tuple, dict | None]]) -> str:
    """Why finalize will not write the record: timed-out legs (the remedy is a longer timeout) and other failures
    that are not chemistry (each with its last error line). Both are rescheduled by running the command again."""

    def name(k) -> str:
        c, i, leg, m = k
        return f"{c} pose {i} {leg}{'' if m is None else f' ({m})'}"

    timeouts = [(k, o) for k, o in blocked if (o or {}).get("outcome") == "timeout"]
    others = [(k, o) for k, o in blocked if (o or {}).get("outcome") != "timeout"]
    parts = []
    if timeouts:
        legs = "; ".join(
            f"{name(k)} timed out after {float(o.get('seconds') or 0):g} s at rung {o.get('rung')}"
            for k, o in timeouts[:10]
        )
        parts.append(
            f"{len(timeouts)} SCF leg(s) timed out: {legs}. Raise [run] scf_timeout_s (it does not change the input "
            "digest) and run the same command again"
        )
    if others:
        legs = "; ".join(
            f"{name(k)}: {o['error'] if o else 'no outcome (the run was interrupted)'}" for k, o in others[:10]
        )
        parts.append(
            f"{len(others)} SCF leg(s) failed for a reason that is not chemistry: {legs}. Run the same command "
            "again: they are rescheduled (chemistry failures are not)"
        )
    return "the run is not finished: " + ". ".join(parts) + "."


UNRECORDED = "unrecorded"
#: How the measurement step's seconds are counted under --temporal: every SCF's own seconds, summed over legs that ran
#: in parallel (CPU-seconds at one thread per leg). Not wall time.
SUMMED_CPU_SECONDS = "summed_cpu_seconds"


def builds_of(rows: list[dict]) -> list[dict]:
    """The distinct code identities across the log's rows, each with how many rows it wrote. Rows without one (written
    before rows were stamped, f0c3eb1 and earlier) are one `{"identity": "unrecorded"}` entry."""
    counts: dict[str, int] = {}
    ids: dict[str, dict] = {}
    for r in rows:
        b = r.get("build")
        key = json.dumps(b, sort_keys=True) if isinstance(b, dict) else UNRECORDED
        counts[key] = counts.get(key, 0) + 1
        if isinstance(b, dict):
            ids[key] = b
    out = [{"identity": "recorded", **ids[k], "n_rows": counts[k]} for k in sorted(ids)]
    if UNRECORDED in counts:
        out.append({"identity": UNRECORDED, "n_rows": counts[UNRECORDED]})
    return out


def _mixed_refusal(builds: list[dict]) -> str:
    def name(b: dict) -> str:
        f, m = b.get("ferric") or {}, b.get("smeltery") or {}

        def one(sha, dirty) -> str:
            return f"{str(sha)[:12] if sha else 'unstamped'}{' (dirty)' if dirty else ''}"

        return f"ferric {one(f.get('git_sha'), f.get('dirty'))} + smeltery {one(m.get('commit'), m.get('dirty'))}"

    known = [b for b in builds if b["identity"] != UNRECORDED]
    each = "; ".join(f"{name(b)}: {b['n_rows']} row(s)" for b in known)
    return (
        f"calcs.jsonl was written by {len(known)} different builds ({each}): its SCFs are not one code's measurement. "
        "Re-measure with one build (a fresh --out), or set [run] allow_mixed_builds = true to record the run as mixed "
        "(results.builds lists every build and its rows)"
    )


def timing_of(rows: list[dict]) -> tuple[str | None, int, float]:
    """(the earliest SCF row's start, how many SCF rows carry no start, the SCF rows' summed seconds)."""
    scf = [r for r in rows if "outcome" not in r]
    timed = sorted(r["started_utc"] for r in scf if isinstance(r.get("started_utc"), str))
    return (timed[0] if timed else None), len(scf) - len(timed), sum(float(r.get("seconds") or 0.0) for r in scf)


def _stamp_runner(rec, rows: list[dict], builds: list[dict]) -> None:
    """What only the log knows about a --temporal run: that it ran under Temporal, which builds wrote its rows, when its
    first SCF started, and its measurement's seconds (summed CPU-seconds, not wall time)."""
    started, untimed, secs = timing_of(rows)
    if started is not None:
        rec.started_utc = started
        source = "the earliest calcs.jsonl row's start"
        if untimed:
            source += f"; {untimed} earlier-format row(s) carry no start, so the run may have started earlier"
    else:
        source = "finalize's own clock: no calcs.jsonl row carries a start (all predate start stamping)"
    rec.results["runner"] = "temporal"
    rec.results["builds"] = builds
    rec.results["timing"] = {
        "started_utc_from": source,
        "rows_without_start": untimed,
        "measure_seconds": secs,
        "measure_seconds_kind": SUMMED_CPU_SECONDS,
    }
    for row in rec.results.get("funnel") or []:
        if row.get("kind") == "measure":
            row["seconds"], row["seconds_kind"] = secs, SUMMED_CPU_SECONDS


def clear_marker(out: str | Path, config_sha: str) -> None:
    """Remove OUT/temporal/running.json when it names this run (its staged config): the run is over."""
    marker = Path(out) / WORK_DIR / MARKER
    with contextlib.suppress(OSError, ValueError):
        if json.loads(marker.read_text()).get("config_sha") == config_sha:
            marker.unlink()


def finalize_sync(inp: RunInput, n_resumed: int = 0) -> RunSummary:
    """The run record, from the per-SCF log, exactly as a non-Temporal run builds it (`run_plan`, `write_outputs`).
    Refuses while any leg has an infrastructure failure (`outcome = "error"`) or no outcome at all."""
    import numpy as np

    from .cli import StageError, allow_mixed_builds, build_plan, load_config, run_plan, write_outputs
    from .model import HARTREE_TO_KCAL

    out = Path(inp.out)
    check_config(inp.config, inp.config_sha)
    cfg = load_config(Path(inp.config))
    plan = build_plan(cfg, out_dir=out)
    digest = _plan_digest(plan)
    work = _work(inp.out)
    if work["digest"] != digest:
        raise ValueError(f"the plan rebuilt for the record (digest {digest[:12]}) is not the one scored")
    for c in plan.candidates:  # the scored poses must be the poses the record states
        got = [np.asarray(p["coords_ang"]) for p in work["candidates"][c.name]["poses"]]
        if len(got) != len(c.poses) or any(not np.array_equal(g, p.coords_ang) for g, p in zip(got, c.poses)):
            raise ValueError(f"{c.name}: the rebuilt poses differ from the scored ones")
    raw, torn = read_rows(out, digest)
    rows, n_dup = dedupe(raw)
    state = leg_state(rows)
    primary = work["primary"]
    models = list(work["fields"])
    idents = legs_of(plan, models)
    blocked = [
        (k, (state.get(k) or {}).get("outcome"))
        for k in idents
        if not (state.get(k) or {}).get("final", False)
        and not (k[2] == "field" and not (state.get((k[0], k[1], "vacuum", None)) or {}).get("converged"))
    ]  # a field leg after an unconverged vacuum leg was not run: the vacuum leg is what is reported
    if blocked:
        raise ValueError(_refusal(blocked))
    builds = builds_of(rows)
    if sum(1 for b in builds if b["identity"] != UNRECORDED) > 1 and not allow_mixed_builds(cfg):
        raise ValueError(_mixed_refusal(builds))

    def drop(c: str, i: int, legname: str, model: str | None, st: dict) -> None:
        o = st.get("outcome") or {}
        dropped.append(
            {
                "candidate": c,
                "pose": i,
                "leg": legname,
                "field_model": model,
                "reason": o.get("error"),
                "outcome": o.get("outcome"),
                "rungs_tried": o.get("rungs_tried", []),
            }
        )

    values: dict[str, dict[str, list]] = {m: {} for m in models}
    rung_of: dict[str, list] = {}
    dropped: list[dict] = []
    failed: dict[str, set[int]] = {c.name: set() for c in plan.candidates}
    for c in plan.candidates:  # a vacuum leg failing every rung is one drop, whatever the field models
        for i in range(len(c.poses)):
            vst = state[(c.name, i, "vacuum", None)]
            if vst["converged"] is None:
                drop(c.name, i, "vacuum", None, vst)
                failed[c.name].add(i)
    for model in models:
        for c in plan.candidates:
            vals = []
            for i in range(len(c.poses)):
                vac = state[(c.name, i, "vacuum", None)]["converged"]
                if vac is None:
                    vals.append(None)
                    continue
                fst = state[(c.name, i, "field", model)]
                fld = fst["converged"]
                if fld is None:
                    vals.append(None)
                    drop(c.name, i, "field", model, fst)
                    if model == primary:
                        failed[c.name].add(i)
                    continue
                vals.append((fld["energy_hartree"] - vac["energy_hartree"]) * HARTREE_TO_KCAL)
                if model == primary:
                    rung_of.setdefault(c.name, [None] * len(c.poses))[i] = max(int(vac["rung"]), int(fld["rung"]))
            values[model][c.name] = vals

    parent = plan.parent
    pose_index = None
    if any(failed.values()):
        pose_index = {parent.name: [i for i in range(len(parent.poses)) if i not in failed[parent.name]]}
        for c in plan.candidates:
            if c is not parent:
                gone = failed[c.name] | failed[parent.name]
                pose_index[c.name] = [i for i in range(len(c.poses)) if i not in gone]
        for d in dropped:
            if d["field_model"] in (primary, None):
                d["dropped_with"] = (
                    [c.name for c in plan.candidates]
                    if d["candidate"] == parent.name
                    else [parent.name, d["candidate"]]
                )
        empty = [k for k, v in pose_index.items() if not v]
        if empty:
            raise StageError(f"stage 'pairing' failed: every pose of {', '.join(empty)} was dropped", stage="pairing")

    remedies = bool(plan.tier.remedies)

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
        # per ORIGINAL pose index (None where a pose was dropped); summarize maps the compacted primary values in
        # rec.results["per_pose"] back to original indices through results.pose_index before pairing
        measured = {m: v for m, v in values.items() if work["model_owner"].get(m) == spec.key}
        got = fn(plan, rec, measured)
        if not isinstance(got, dict):
            raise StageError(
                f"stage {spec.key!r} failed: summarize returns a dict, got {type(got).__name__}", stage=spec.key
            )
        rec.results[spec.key] = {**(rec.results.get(spec.key) or {}), **got}
    if dropped:
        rec.results["dropped_poses"] = dropped
    if inp.config_info is not None:
        rec.results["config"] = inp.config_info
    _stamp_runner(rec, rows, builds)
    path = write_outputs(plan, rec, out, calc_rows=rows)  # calcs.jsonl rewritten atomically, duplicates removed
    clear_marker(out, inp.config_sha)
    n_scfs = sum(1 for r in rows if "outcome" not in r)
    return RunSummary(str(path), rec.input_digest, n_scfs, n_resumed, len(dropped), n_dup, torn, dropped)


# ------------------------------------------------------------------------------------------------ Temporal

if TEMPORAL_AVAILABLE:

    async def _heartbeating(coro_fn, limit: float | None = None):
        """Await `coro_fn()` (a task), heartbeating every quarter of the heartbeat timeout, and end it after `limit`
        seconds (else the start-to-close timeout) here: the server's timeout alone would leave the work running in
        this worker. A timeout is non-retryable ("Timeout", details: the seconds reached): it is recorded, not
        retried, and the remedy is a longer `[run] scf_timeout_s`."""
        info = activity.info()
        every = max(0.5, (info.heartbeat_timeout.total_seconds() / 4) if info.heartbeat_timeout else 30.0)
        if not limit:
            limit = info.start_to_close_timeout.total_seconds() if info.start_to_close_timeout else None
        t0 = time.monotonic()
        task = asyncio.ensure_future(coro_fn())
        activity.heartbeat("start")
        try:
            while True:
                left = None if limit is None else limit - (time.monotonic() - t0)
                if left is not None and left <= 0:
                    raise ApplicationError(
                        f"timed out after {limit:g} s", {"seconds": float(limit)}, type=TIMEOUT, non_retryable=True
                    )
                done, _ = await asyncio.wait({task}, timeout=every if left is None else min(every, left))
                if done:
                    return task.result()
                activity.heartbeat("running")
        except BaseException:
            task.cancel()  # the task kills its child and waits for it before this activity ends
            with contextlib.suppress(BaseException):
                await task
            raise

    class PipelineActivities:
        """The run's activities. `threads` is RAYON_NUM_THREADS of each score child; `command` the child's argv
        prefix (tests point it at a fake engine)."""

        def __init__(self, threads: int = 1, command: list[str] | None = None) -> None:
            self.threads = max(1, int(threads))
            self.command = list(command or child_command())

        @activity.defn(name="pipeline_prepare")
        async def prepare(self, inp: RunInput) -> PrepareResult:
            try:
                return await _heartbeating(lambda: asyncio.to_thread(prepare_sync, inp))
            except Exception as e:  # a config or stage error: the same inputs fail the same way every time
                raise ApplicationError(f"{type(e).__name__}: {e}", type="PrepareFailed", non_retryable=True) from e

        @activity.defn(name="pipeline_score")
        async def score(self, inp: ScoreInput) -> ScoreResult:
            from .tiers import refuse_false_convergence

            try:
                check_config(inp.config, inp.config_sha)
            except ValueError as e:
                raise ApplicationError(str(e), type=CONFIG_CHANGED, non_retryable=True) from e
            digest = _work(inp.out)["digest"]
            ident = (inp.candidate, inp.pose, inp.leg, inp.field_model)
            rows, _ = read_rows(inp.out, digest)
            mine = [r for r in rows if "outcome" not in r and _ident(r) == ident]
            earlier = [r for r in mine if r["rung"] < inp.rung]
            row = next((r for r in mine if r["rung"] == inp.rung), None)
            cpu = 0.0
            if row is None:  # not already logged by an attempt whose completion was lost
                started = utc_now()
                got = await _heartbeating(lambda: self._child(inp), inp.timeout_s or None)
                row = scf_row(inp, got, digest, started)
                cpu = got["seconds"]
                row = refuse_false_convergence(row, earlier)  # logged as the ladder judged it
                append_row(inp.out, row)
            row = refuse_false_convergence(row, earlier)  # a line logged before this check existed, too
            if not row["converged"]:
                where = "vacuum" if inp.field_model is None else f"field {inp.field_model}"
                raise ApplicationError(
                    f"{inp.candidate} pose {inp.pose} {where}: SCF did not converge at rung {inp.rung}",
                    type=SCF_NOT_CONVERGED,
                    non_retryable=True,
                )
            return ScoreResult(float(row["energy_hartree"]), inp.rung, cpu)

        async def _child(self, inp: ScoreInput) -> dict:
            """Run the leg in a child process (own process group); kill the group on cancel or timeout."""
            env = {**os.environ, "RAYON_NUM_THREADS": str(self.threads), "OMP_NUM_THREADS": "1"}
            env.setdefault("OPENBLAS_NUM_THREADS", "1")
            proc = await asyncio.create_subprocess_exec(
                *self.command,
                json.dumps(dataclasses.asdict(inp)),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                start_new_session=True,
                preexec_fn=_die_with_parent_hook(),
            )
            try:
                out, err = await proc.communicate()
            except BaseException:
                kill_group(proc.pid)
                await proc.wait()
                raise
            if proc.returncode != 0:
                tail = err.decode(errors="replace").strip().splitlines()
                last = tail[-1] if tail else "(no stderr)"
                raise ApplicationError(f"score child exited {proc.returncode}: {last}", type="ChildFailed")
            lines = [ln for ln in out.decode(errors="replace").splitlines() if ln.startswith(RESULT_PREFIX)]
            if not lines:
                raise ApplicationError("score child printed no result", type="ChildFailed")
            return json.loads(lines[-1][len(RESULT_PREFIX) :])

        @activity.defn(name="pipeline_record_outcome")
        async def record_outcome(self, out: str, row: dict) -> None:
            await asyncio.to_thread(lambda: append_row(out, {**row, "build": code_identity()}))

        @activity.defn(name="pipeline_finalize")
        async def finalize(self, inp: RunInput, n_resumed: int) -> RunSummary:
            try:
                return await _heartbeating(lambda: asyncio.to_thread(finalize_sync, inp, n_resumed))
            except Exception as e:
                raise ApplicationError(f"{type(e).__name__}: {e}", type="FinalizeFailed", non_retryable=True) from e

    @workflow.defn(name="PipelineRunWorkflow")
    class PipelineRunWorkflow:
        """One pipeline run: prepare, every SCF leg (up the remedy ladder), finalize. Control flow only."""

        def __init__(self) -> None:
            self._progress = {"total": 0, "done": 0, "failed": 0, "running": 0, "phase": "prepare"}

        @workflow.query
        def progress(self) -> dict:
            return dict(self._progress)

        @workflow.run
        async def run(self, inp: RunInput) -> RunSummary:
            prep = await workflow.execute_activity(
                "pipeline_prepare",
                inp,
                result_type=PrepareResult,
                start_to_close_timeout=timedelta(hours=6),
                heartbeat_timeout=timedelta(seconds=inp.heartbeat_s),
                retry_policy=RetryPolicy(maximum_attempts=3, non_retryable_error_types=["PrepareFailed"]),
            )
            self._progress.update(total=prep.n_total, done=prep.n_done, phase="score")
            sem = asyncio.Semaphore(max(1, inp.workers))

            async def one(u: ScoreInput) -> bool:
                async with sem:
                    self._progress["running"] += 1
                    try:
                        return await self._climb(inp, prep, u)
                    finally:
                        self._progress["running"] -= 1
                        self._progress["done"] += 1

            poses: dict[tuple[str, int], list[ScoreInput]] = {}
            for u in prep.pending:
                poses.setdefault((u.candidate, u.pose), []).append(u)

            async def pose(legs: list[ScoreInput]) -> None:
                """The vacuum leg first: a pose whose vacuum leg failed gives no value, so its field legs do not run
                (a rerun schedules them if the vacuum leg's failure was not chemistry)."""
                vac = [u for u in legs if u.leg == "vacuum"]
                fields = [u for u in legs if u.leg != "vacuum"]
                if vac and not await one(vac[0]):
                    self._progress["done"] += len(fields)
                    return
                await asyncio.gather(*(one(u) for u in fields))

            await asyncio.gather(*(pose(legs) for legs in poses.values()))
            self._progress["phase"] = "finalize"
            summary = await workflow.execute_activity(
                "pipeline_finalize",
                args=[inp, prep.n_done],
                result_type=RunSummary,
                start_to_close_timeout=timedelta(hours=6),
                heartbeat_timeout=timedelta(seconds=inp.heartbeat_s),
                retry_policy=RetryPolicy(maximum_attempts=3, non_retryable_error_types=["FinalizeFailed"]),
            )
            self._progress["phase"] = "done"
            return summary

        async def _climb(self, inp: RunInput, prep: PrepareResult, u: ScoreInput) -> bool:
            """The leg up the ladder; True when it converged. A failure is recorded as an outcome line."""
            tried: list[int] = list(range(u.rung))
            outcome, error = "not_converged", "every rung of the remedy ladder was tried before this run resumed"
            extra: dict = {}
            for rung in range(u.rung, prep.n_rungs):
                tried.append(rung)
                try:
                    await workflow.execute_activity(
                        "pipeline_score",
                        dataclasses.replace(u, rung=rung),
                        result_type=ScoreResult,
                        start_to_close_timeout=timedelta(seconds=prep.scf_timeout_s + SERVER_TIMEOUT_MARGIN_S),
                        heartbeat_timeout=timedelta(seconds=inp.heartbeat_s),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(seconds=5),
                            maximum_attempts=3,
                            non_retryable_error_types=[SCF_NOT_CONVERGED, CLASH_REFUSED, CONFIG_CHANGED, TIMEOUT],
                        ),
                    )
                    return True
                except ActivityError as e:
                    cause = e.cause
                    kind = getattr(cause, "type", None)
                    error = f"{type(cause or e).__name__}: {cause or e}"[:400]
                    if kind == SCF_NOT_CONVERGED:
                        continue  # chemistry: the next rung, never a retry of this one
                    if kind == TIMEOUT or isinstance(cause, TimeoutError_):
                        details = list(getattr(cause, "details", None) or [])
                        seconds = (
                            (details[0] or {}).get("seconds") if details and isinstance(details[0], dict) else None
                        )
                        outcome, extra = "timeout", {"rung": rung, "seconds": seconds or prep.scf_timeout_s}
                    else:
                        outcome = "clash_refused" if kind == CLASH_REFUSED else "error"
                    break
            self._progress["failed"] += 1
            row = {
                "candidate": u.candidate,
                "pose": u.pose,
                "leg": u.leg,
                "field_model": u.field_model,
                "outcome": outcome,
                "rungs_tried": tried,
                "error": error,
                "digest": prep.digest[:12],
                **extra,
            }
            await workflow.execute_activity(
                "pipeline_record_outcome", args=[inp.out, row], start_to_close_timeout=timedelta(minutes=2)
            )
            return False


def kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def workflow_id(out: str | Path, config_text: str) -> str:
    """The same OUT and the same staged config (inputs staged by content) are the same run: start resumes it."""
    h = hashlib.sha256(f"{Path(out).resolve()}\n{hashlib.sha256(config_text.encode()).hexdigest()}".encode())
    return "smeltery-run-" + h.hexdigest()[:20]


async def connect(host: str, namespace: str = "default"):
    from temporalio.client import Client

    return await Client.connect(host, namespace=namespace)


async def run_worker(
    client,
    task_queue: str = TASK_QUEUE,
    workers: int = 2,
    threads_per_worker: int = 1,
    stop: asyncio.Event | None = None,
    command: list[str] | None = None,
):
    """A pipeline worker: up to `workers` SCF legs at once, each in its own child process with
    RAYON_NUM_THREADS=`threads_per_worker` (so workers x threads cores; memory, not cores, is usually the limit)."""
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker

    acts = PipelineActivities(threads_per_worker, command)
    async with Worker(
        client,
        task_queue=task_queue,
        workflows=[PipelineRunWorkflow],
        activities=[acts.prepare, acts.score, acts.record_outcome, acts.finalize],
        workflow_runner=UnsandboxedWorkflowRunner(),
        max_concurrent_activities=max(1, workers) + 2,  # + prepare/finalize/record beside the scores
    ):
        print(
            f"smeltery pipeline worker up on {task_queue}: {workers} worker(s) x {threads_per_worker} thread(s) "
            "(Ctrl+C stops)",
            flush=True,
        )
        await (stop.wait() if stop is not None else asyncio.Future())


async def _pollers(client, task_queue: str) -> int | None:
    try:
        from temporalio.api.enums.v1 import TaskQueueType
        from temporalio.api.taskqueue.v1 import TaskQueue
        from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest

        resp = await client.workflow_service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=client.namespace,
                task_queue=TaskQueue(name=task_queue),
                task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW,
            )
        )
        return len(resp.pollers)
    except Exception:
        return None


async def start_and_wait(
    client,
    inp: RunInput,
    task_queue: str = TASK_QUEUE,
    poll_s: float = 10.0,
    log=print,
    no_worker_after_s: float = 60.0,
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
    marker = Path(inp.out) / WORK_DIR / MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {"workflow_id": wid, "host": _host_of(client), "task_queue": task_queue, "config_sha": inp.config_sha}
        )
    )
    log(f"workflow {wid} on {task_queue} (UI http://127.0.0.1:8233)")
    result = asyncio.ensure_future(handle.result())
    last, t0, warned = None, time.monotonic(), False
    while True:
        done, _ = await asyncio.wait({result}, timeout=poll_s)
        if done:
            summary = result.result()
            clear_marker(inp.out, inp.config_sha)  # finalize removed it too, unless this call wrote it after
            return summary
        if not warned and time.monotonic() - t0 >= no_worker_after_s and await _pollers(client, task_queue) == 0:
            log(
                f"no worker is polling task queue {task_queue!r}: start one with "
                f"`smeltery temporal-worker --task-queue {task_queue}`"
            )
            warned = True
        try:
            p = await handle.query(PipelineRunWorkflow.progress)
        except Exception:  # no worker has picked it up yet
            p = None
        if p != last and p is not None:
            log(f"  {p['phase']}: {p['done']}/{p['total']} SCF legs, {p['running']} running, {p['failed']} failed")
            last = p


def _host_of(client) -> str:
    try:
        return client.service_client.config.target_host
    except Exception:
        return DEFAULT_HOST


def guard_out(out: Path, staged_sha, temporal: bool, log=None) -> str | None:
    """A refusal message when OUT holds a Temporal run that is still running (re-staging its config would change what
    its activities read), else None. Call it before writing anything into OUT. `--temporal` with the same staged
    config attaches instead; `staged_sha` is that sha256, or a callable computing it (called only when it decides).
    A server that cannot be reached is a refusal too: whether the run is live cannot be checked. A marker whose
    workflow is not running (the server says so) is stale: it is removed, and `log` (stderr by default) says so."""
    marker = Path(out) / WORK_DIR / MARKER
    if not marker.is_file() or not TEMPORAL_AVAILABLE:
        return None
    m = json.loads(marker.read_text())

    async def status():
        from temporalio.service import RPCError, RPCStatusCode

        client = await connect(m.get("host") or DEFAULT_HOST)
        try:
            d = await client.get_workflow_handle(m["workflow_id"]).describe()
        except RPCError as e:
            if e.status == RPCStatusCode.NOT_FOUND:  # the server is up and has no such workflow
                return "NOT_FOUND"
            raise
        return d.status

    try:
        st = asyncio.run(status())
    except Exception as e:
        return (
            f"{out} holds a Temporal run (workflow {m['workflow_id']}) and its server cannot be asked whether it is "
            f"still running ({type(e).__name__}); start the server, or remove {marker} if you know the run is over"
        )
    from temporalio.client import WorkflowExecutionStatus

    if st != WorkflowExecutionStatus.RUNNING:
        with contextlib.suppress(OSError):
            marker.unlink()
        state = getattr(st, "name", str(st))
        (log or (lambda s: print(s, file=sys.stderr)))(
            f"note: removed a stale {marker} (workflow {m['workflow_id']} is {state}, not running)"
        )
        return None
    if temporal and (staged_sha() if callable(staged_sha) else staged_sha) == m.get("config_sha"):
        return None
    return (
        f"a Temporal run (workflow {m['workflow_id']}) is still running in {out}; re-staging its config would change "
        "what its activities read. Wait for it, terminate it (temporal workflow terminate --workflow-id "
        f"{m['workflow_id']}), or use another --out"
    )


def run_cli(
    config: Path,
    out: Path,
    workers: int,
    host: str,
    task_queue: str,
    config_info: dict | None,
    poll_s: float = 10.0,
) -> int:
    """`smeltery run --temporal`: 0 done, 2 the server is down (never a silent fallback), 3 the run failed."""
    if not TEMPORAL_AVAILABLE:
        print("config error: --temporal needs temporalio (the `temporal` extra)", file=sys.stderr)
        return 2
    from temporalio.client import WorkflowFailureError
    from temporalio.service import RPCError

    async def main() -> int:
        try:
            client = await connect(host)
        except Exception as e:
            print(server_down_message(host, e), file=sys.stderr)
            return 2
        inp = RunInput(
            str(Path(config).resolve()),
            str(Path(out).resolve()),
            workers,
            config_info,
            config_sha=sha256_file(config),
        )
        try:
            s = await start_and_wait(client, inp, task_queue, poll_s)
        except WorkflowFailureError as e:
            cause = e.cause
            while getattr(cause, "cause", None) is not None:
                cause = cause.cause
            print(f"error: the Temporal run failed: {cause or e}", file=sys.stderr)
            return 3
        except RPCError as e:
            print(server_down_message(host, e), file=sys.stderr)
            print("re-running the same command resumes the run from OUT/calcs.jsonl", file=sys.stderr)
            return 2
        for d in s.dropped_poses:
            print(f"dropped: {d['candidate']} pose {d['pose']} ({d['field_model']}): {d['reason']}")
        if s.n_duplicates or s.n_torn:
            print(f"calcs.jsonl: {s.n_duplicates} duplicate SCF line(s) removed, {s.n_torn} torn line(s) skipped")
        print(f"\nrun record: {s.record}\ninput_digest {s.digest}")
        print(f"{s.n_scfs} SCFs ({s.n_resumed} legs read back from the log), {s.n_failed} failed score(s)")
        return 0

    return asyncio.run(main())


def worker_cli(host: str, task_queue: str, workers: int, threads_per_worker: int = 1) -> int:
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
        await run_worker(client, task_queue, workers, threads_per_worker)
        return 0

    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "_score":
    sys.exit(child_main(sys.argv[2:]))
