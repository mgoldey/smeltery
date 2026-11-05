"""Workspace pipelines on disk: ROOT/pipelines/<name>/config.toml plus the input files it reads."""

from __future__ import annotations

import builtins
import hashlib
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path

from ..ports.pipeline_store import PipelineFile, StaleWrite

NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
CONFIG = "config.toml"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class FilesystemPipelineStore:
    def __init__(self, root: str | Path):
        self.dir = Path(root) / "pipelines"
        self._lock = threading.Lock()

    def _folder(self, name: str) -> Path:
        if not isinstance(name, str) or not NAME.fullmatch(name):
            raise ValueError(f"invalid pipeline name {name!r}: use [a-z0-9][a-z0-9_-]{{0,63}}")
        return self.dir / name

    def list(self) -> builtins.list[str]:
        if not self.dir.is_dir():
            return []
        return sorted(p.name for p in self.dir.iterdir() if NAME.fullmatch(p.name) and (p / CONFIG).is_file())

    def get(self, name: str) -> PipelineFile:
        folder = self._folder(name)
        try:
            text = (folder / CONFIG).read_text()
        except FileNotFoundError as e:
            raise KeyError(name) from e
        return PipelineFile(name, text, _sha(text), folder)

    @staticmethod
    def _write(folder: Path, text: str) -> None:
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text)
            os.replace(tmp, folder / CONFIG)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def put(self, name: str, text: str, base_sha256: str | None) -> PipelineFile:
        with self._lock:
            current = self.get(name)
            if base_sha256 != current.sha256:
                raise StaleWrite(name, current.sha256)
            self._write(current.folder, text)
        return PipelineFile(name, text, _sha(text), current.folder)

    def create(self, name: str, text: str, files: dict[str, Path]) -> PipelineFile:
        folder = self._folder(name)
        dests: list[tuple[Path, Path]] = []
        for rel, src in files.items():
            r = Path(rel)
            if r.is_absolute() or ".." in r.parts or r.name == "" or str(r) == CONFIG:
                raise ValueError(f"input path {rel!r} must be relative, inside the pipeline folder")
            dests.append((Path(src), folder / r))
        with self._lock:
            if folder.exists():
                raise FileExistsError(name)
            folder.mkdir(parents=True)
            try:
                for src, dest in dests:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(src, dest)
                self._write(folder, text)
            except BaseException:
                shutil.rmtree(folder, ignore_errors=True)
                raise
        return PipelineFile(name, text, _sha(text), folder)
