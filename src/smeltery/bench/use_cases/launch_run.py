from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

from smeltery.gui.pipeline import Pipeline
from smeltery.run_inputs import stage_inputs


def smeltery_command() -> list[str]:
    """`smeltery` from this interpreter's own install: the bench server's PATH need not include its venv, and a task
    that runs a different smeltery than the one that checked the pipeline would not be the run the user asked for."""
    script = Path(sys.executable).parent / "smeltery"
    return [str(script)] if script.is_file() else [sys.executable, "-m", "smeltery.cli"]


def uses_temporal(task_queue) -> bool:
    """Bench is configured with the Temporal task queue: launch `smeltery run --temporal` on it (the subprocess
    queue keeps the plain run, the default)."""
    from smeltery.bench.adapters.task_queue import TemporalTaskQueue

    return isinstance(task_queue, TemporalTaskQueue)


#: A long run holds several times its per-calculation memory prediction: one was measured at ~11 GB resident against
#: a ~2 GB prediction after ~40 molecules. The run's cap is the prediction times this.
RUN_MEMORY_FACTOR = 6.0
#: Never cap a run below this: not all of its memory is the SCF (the interpreter, RDKit, docking).
MIN_RUN_MEMORY_GB = 4.0
#: Never cap it above this share of the host's RAM: past that the machine swaps, and a swapping run was measured
#: 29x slower than its peers, which is worse than being stopped.
HOST_RAM_SHARE = 0.8


def host_memory_gb() -> float | None:
    """`MemTotal` from /proc/meminfo in GB; None where it cannot be read."""
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 / 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def default_memory_limit_gb(per_calculation_gb: float | None, host_gb: float | None) -> float | None:
    """The memory cap a launched run gets: `per_calculation_gb` x RUN_MEMORY_FACTOR, at least MIN_RUN_MEMORY_GB,
    and never more than HOST_RAM_SHARE of the host. Without a prediction, the host cap alone; None when neither is
    known (no cap)."""
    cap = host_gb * HOST_RAM_SHARE if host_gb else None
    if per_calculation_gb is None:
        return cap
    want = max(MIN_RUN_MEMORY_GB, per_calculation_gb * RUN_MEMORY_FACTOR)
    return min(want, cap) if cap is not None else want


def predicted_memory_gb(text: str, base: Path, preview_dir: Path | None) -> float | None:
    """The per-calculation memory `smeltery run --plan` predicted for this config (`plan.json` `memory.estimate.
    total_gb` in `preview_dir`), or None: no preview, the model cannot say, or the preview was made for other poses
    (its `poses_key` differs from this config's)."""
    if preview_dir is None:
        return None
    try:
        plan = json.loads((Path(preview_dir) / "plan.json").read_text())
        from smeltery.cli import poses_key

        if plan.get("status") != "ok" or plan.get("poses_key") != poses_key(tomllib.loads(text), base):
            return None
        total = ((plan.get("memory") or {}).get("estimate") or {}).get("total_gb")
    except (OSError, ValueError, AttributeError, tomllib.TOMLDecodeError):
        return None
    return float(total) if isinstance(total, (int, float)) and total > 0 else None


class LiveRunRefused(ValueError):
    """OUT holds a Temporal run that is still running (or whose server cannot say), or (in-process launch) a finished
    run record: nothing was written into it."""


class LaunchRun:
    def __init__(self, task_queue, run_store, artifact_store, identity, clock, root: Path | str | None = None):
        self.root = Path(root) if root is not None else Path.cwd()  # the Bench root: where relative paths start
        self.task_queue = task_queue
        self.run_store = run_store
        self.artifact_store = artifact_store
        self.identity = identity
        self.clock = clock

    def run(
        self,
        pipeline: Pipeline,
        out: str | Path | None = None,
        out_dir: str | Path | None = None,
        *,
        command: list[str] | None = None,
        config_dir: Path | None = None,
        text: str | None = None,
        preview_dir: Path | None = None,
        timeout_s: float | None = None,
    ):  # noqa: E501
        """Stage inputs into `out_dir`, write `out_dir/config.toml` and submit `smeltery run` on it. `text` is the
        config as the user wrote it (comments and all); given, it is what the run directory holds, with only the input
        paths rewritten to their staged copies. Without it the pipeline's compiled TOML is written.

        The run waits in `queued` for one of the queue's run slots (`capped`), and gets a memory cap from the
        pipeline's preview (`preview_dir/plan.json`, see `default_memory_limit_gb`) and the optional wall-clock
        `timeout_s`. Under Temporal neither is passed: the workflow's workers bound their own work."""
        if out_dir is None:
            out_dir = out if out is not None else Path("out")
        out_dir = Path(out_dir)
        # relative paths resolve against `config_dir` (the folder the config lives in, as `smeltery run` does);
        # None means the Bench root
        base = Path(config_dir) if config_dir is not None else self.root
        source = text if text is not None else pipeline.to_toml()
        temporal = command is None and uses_temporal(self.task_queue)
        self._guard(out_dir, source, base, temporal=temporal)
        if command is None and not temporal and (done := sorted(out_dir.glob("run-*.json"))):
            raise LiveRunRefused(f"{out_dir} already holds the run record {done[0].name}; pick another out folder")
        out_dir.mkdir(parents=True, exist_ok=True)
        staged, _ = stage_inputs(source, base, out_dir)
        dest = out_dir / "config.toml"
        # an unchanged config is not rewritten (a live run reads it)
        if not dest.is_file() or dest.read_text() != staged:
            dest.write_text(staged)
        if command is None:
            command = [*smeltery_command(), "run", str(out_dir / "config.toml"), "--out", str(out_dir)]
            if uses_temporal(self.task_queue):  # the run itself is then a durable workflow, not one long process
                command.append("--temporal")
        limits = {}
        if not uses_temporal(self.task_queue):
            per_calc = predicted_memory_gb(source, base, preview_dir)
            limits = {"memory_limit_gb": default_memory_limit_gb(per_calc, host_memory_gb()), "timeout_s": timeout_s}
        task_id = self.task_queue.submit(
            f"run {pipeline.campaign}",
            list(command),
            cwd=str(out_dir.parent),
            note=f"user={self.identity.current()} time={self.clock.now().isoformat()}",
            capped=True,
            **limits,
        )
        return task_id

    @staticmethod
    def _guard(out_dir: Path, text: str, base: Path, temporal: bool) -> None:
        """`smeltery run`'s own guard, before anything is written into OUT: a live Temporal run there is refused,
        unless this launch is `--temporal` with the same staged config (the CLI then attaches to it)."""
        from smeltery.cli import staged_sha256
        from smeltery.temporal_run import guard_out

        why = guard_out(out_dir, lambda: staged_sha256(text, base), temporal)
        if why:
            raise LiveRunRefused(why)

    def execute(self, *a, **kw):
        return self.run(*a, **kw)

    __call__ = run
