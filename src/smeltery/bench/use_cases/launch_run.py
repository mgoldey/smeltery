from __future__ import annotations

import shutil
import sys
from pathlib import Path

from smeltery.gui.pipeline import Pipeline


def smeltery_command() -> list[str]:
    """`smeltery` from this interpreter's own install: the bench server's PATH need not include its venv, and a task
    that runs a different smeltery than the one that checked the pipeline would not be the run the user asked for."""
    script = Path(sys.executable).parent / "smeltery"
    return [str(script)] if script.is_file() else [sys.executable, "-m", "smeltery.cli"]


class LaunchRun:
    def __init__(self, task_queue, run_store, artifact_store, identity, clock):
        self.task_queue = task_queue
        self.run_store = run_store
        self.artifact_store = artifact_store
        self.identity = identity
        self.clock = clock

    def _stage(self, pipeline: Pipeline, out_dir: Path) -> None:
        for stage in pipeline.stages:
            for key, value in list(stage.params.items()):
                if isinstance(value, str) and Path(value).is_file():
                    src = Path(value)
                    digest = self.artifact_store.put(src)
                    staged = out_dir / digest
                    if not staged.exists():
                        shutil.copyfile(src, staged)
                    stage.params[key] = str(staged)

    def run(
        self,
        pipeline: Pipeline,
        out: str | Path | None = None,
        out_dir: str | Path | None = None,
        *,
        command: list[str] | None = None,
    ):  # noqa: E501
        if out_dir is None:
            out_dir = out if out is not None else Path("out")
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._stage(pipeline, out_dir)
        (out_dir / "config.toml").write_text(pipeline.to_toml())
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
