"""`smeltery serve` and `smeltery worker`: the two long-running processes of a deployment, configured by
`SMELTERY_*` environment variables (see `smeltery.settings`; flags override them).

serve   Bench, the repo's one server (FastAPI + the built UI), the same app as `smeltery bench`. It adds the
        backend choice: `local` (SQLite + a subprocess supervisor per task, no server needed) or `temporal` (Bench
        submits its tasks to a Temporal cluster). `serve` takes every `smeltery bench` flag too (ROOT, --token,
        --demo, --allow-host, ...).
worker  the process that executes work for the chosen backend. `local`: a supervisor loop over the same SQLite queue
        (it reconciles dead supervisors and starts queued tasks, so queued work starts even when no server is up, and
        settles campaign jobs, `smeltery.jobs`, whose process ended).
        `temporal`: ONE process polling both queues, `smeltery-tasks` (Bench's shell tasks) and `smeltery-pipeline`
        (the SCF legs of `run --temporal`), over one client connection; `--workers` bounds the SCFs at once.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from .settings import Settings, SettingsError, resolve


def _common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--backend", default=None, help="local (default) or temporal; env SMELTERY_BACKEND")
    ap.add_argument("--temporal-address", dest="temporal_address", default=None, help="env SMELTERY_TEMPORAL_ADDRESS")
    ap.add_argument("--namespace", default=None, help="env SMELTERY_TEMPORAL_NAMESPACE")
    ap.add_argument("--task-queue", dest="task_queue", default=None, help="env SMELTERY_TASK_QUEUE")


def _settings(env: Mapping[str, str], **flags: object) -> Settings | None:
    try:
        return resolve(env, **flags)
    except SettingsError as e:
        print(f"smeltery: {e}", file=sys.stderr)
        return None


def serve_main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """`smeltery serve`: Bench with the configured backend. Unknown flags go to `smeltery bench` unchanged."""
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(
        prog="smeltery serve",
        description="Serve Bench. Takes every `smeltery bench` flag too; deployment settings come from SMELTERY_* "
        "variables (docs/temporal-runs.md), flags override them.",
        epilog="Anything else is passed to `smeltery bench` (ROOT, --token, --demo, --allow-host, ...).",
    )
    _common(ap)
    ap.add_argument("--host", default=None, help="address to bind; env SMELTERY_HOST")
    ap.add_argument("--port", default=None, help="port; env SMELTERY_PORT")
    ap.add_argument("--max-runs", dest="max_runs", default=None, help="launched runs at once; env SMELTERY_MAX_RUNS")
    args, rest = ap.parse_known_args(argv)
    s = _settings(env, **vars(args))
    if s is None:
        return 2
    bench_argv = list(rest)
    if s.host is not None:
        bench_argv += ["--host", s.host]
    if s.port is not None:
        bench_argv += ["--port", str(s.port)]
    bench_argv += ["--max-runs", str(s.max_runs)]
    if s.root:  # a ROOT argument still wins: Bench reads this variable only when none is given
        os.environ["SMELTERY_BENCH_ROOT"] = s.root
    from .bench.app import main as bench_main

    if s.backend == "temporal":
        from .bench.adapters.task_queue import TemporalTaskQueue
        from .tasks import TEMPORAL_AVAILABLE

        if not TEMPORAL_AVAILABLE:
            print("smeltery serve: SMELTERY_BACKEND=temporal needs temporalio (the `temporal` extra)", file=sys.stderr)
            return 2
        return bench_main(bench_argv, task_queue=TemporalTaskQueue(s.temporal_address, s.namespace, s.task_queue))
    return bench_main(bench_argv)


def local_worker(root: Path, max_runs: int, interval_s: float = 5.0, stop: Callable[[], bool] = lambda: False) -> int:
    """The local backend's worker: every `interval_s`, reconcile tasks whose supervisor died and start the queued
    ones (`SubprocessTaskQueue.list` does both), and settle campaign jobs (`smeltery submit`, `smeltery.jobs`) whose
    process has ended: a cancelled, killed or crashed job gets its terminal state and core record here even when
    nobody asks for its status. Campaign jobs are tasks on the same queue, so they share its run slots. The tasks run
    under their own detached supervisors, so stopping this loop never kills work."""
    from . import jobs
    from .bench.adapters.task_queue import SubprocessTaskQueue

    q = SubprocessTaskQueue(Path(root) / jobs.TASKS_DIR, max_concurrent_runs=max_runs)
    campaigns = jobs.LocalJobBackend(root, max_runs=max_runs) if jobs.AVAILABLE else None
    print(f"smeltery worker (local): supervising {q.db} every {interval_s:g}s (Ctrl+C stops)", flush=True)
    if campaigns is None:
        print(f"smeltery worker (local): {jobs.NOT_INSTALLED}; campaign jobs are not settled", flush=True)
    try:
        while not stop():
            q.list(limit=1)
            if campaigns is not None:
                campaigns.reconcile()
            time.sleep(interval_s)
    except KeyboardInterrupt:
        pass
    return 0


async def temporal_worker(s: Settings) -> int:
    """One process, two workers (one per queue) sharing one client. 2 when the server is unreachable."""
    from . import temporal_run
    from .tasks import run_task_worker

    try:
        client = await temporal_run.connect(s.temporal_address, s.namespace)
    except Exception as e:  # noqa: BLE001 - any connect failure is "the server is down", reported and exit 2
        print(temporal_run.server_down_message(s.temporal_address, e), file=sys.stderr)
        return 2
    await asyncio.gather(
        run_task_worker(client, s.task_queue, s.task_concurrency),
        temporal_run.run_worker(client, s.pipeline_queue, s.workers, s.threads_per_worker),
    )
    return 0


def worker_main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """`smeltery worker`: the worker for the selected backend."""
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(
        prog="smeltery worker", description="Run the worker for the selected backend (SMELTERY_BACKEND)."
    )
    _common(ap)
    ap.add_argument("--pipeline-queue", dest="pipeline_queue", default=None, help="env SMELTERY_PIPELINE_QUEUE")
    ap.add_argument("--workers", default=None, help="temporal: SCFs at once; env SMELTERY_WORKERS")
    ap.add_argument(
        "--threads-per-worker", dest="threads_per_worker", default=None, help="env SMELTERY_THREADS_PER_WORKER"
    )
    ap.add_argument("--concurrency", dest="task_concurrency", default=None, help="env SMELTERY_TASK_CONCURRENCY")
    ap.add_argument(
        "--max-runs", dest="max_runs", default=None, help="local: launched runs at once; env SMELTERY_MAX_RUNS"
    )
    ap.add_argument("root", nargs="?", default=None, help="local: workspace directory; env SMELTERY_ROOT")
    args = ap.parse_args(argv)
    s = _settings(env, **vars(args))
    if s is None:
        return 2
    if s.backend == "temporal":
        from .tasks import TEMPORAL_AVAILABLE

        if not TEMPORAL_AVAILABLE:
            print("smeltery worker: SMELTERY_BACKEND=temporal needs temporalio (the `temporal` extra)", file=sys.stderr)
            return 2
        try:
            return asyncio.run(temporal_worker(s))
        except KeyboardInterrupt:
            return 0
    from .bench.adapters.workspace_root import default_root  # not .bench.app: the worker needs no FastAPI

    root = Path(s.root).expanduser() if s.root else default_root()
    return local_worker(root, s.max_runs)
