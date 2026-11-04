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
    ):  # noqa: E501
        if out_dir is None:
            out_dir = out if out is not None else Path("out")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # relative paths resolve against `config_dir` (the folder the config lives in, as `smeltery run` does);
        # None means the Bench root
        base = Path(config_dir) if config_dir is not None else self.root
        text, _ = stage_inputs(pipeline.to_toml(), base, out_dir)
        (out_dir / "config.toml").write_text(text)
        if command is None:
            command = [*smeltery_command(), "run", str(out_dir / "config.toml"), "--out", str(out_dir)]
        task_id = self.task_queue.submit(
            f"run {pipeline.campaign}",
            list(command),
            cwd=str(out_dir.parent),
            note=f"user={self.identity.current()} time={self.clock.now().isoformat()}",
        )
        return task_id

    def execute(self, *a, **kw):
        return self.run(*a, **kw)

    __call__ = run
