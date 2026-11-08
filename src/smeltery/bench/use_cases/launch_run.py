from __future__ import annotations

import sys
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
    ):  # noqa: E501
        """Stage inputs into `out_dir`, write `out_dir/config.toml` and submit `smeltery run` on it. `text` is the
        config as the user wrote it (comments and all); given, it is what the run directory holds, with only the input
        paths rewritten to their staged copies. Without it the pipeline's compiled TOML is written."""
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
        task_id = self.task_queue.submit(
            f"run {pipeline.campaign}",
            list(command),
            cwd=str(out_dir.parent),
            note=f"user={self.identity.current()} time={self.clock.now().isoformat()}",
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
