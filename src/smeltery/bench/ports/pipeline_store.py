"""Port: workspace pipelines. A pipeline is a folder holding its config TOML and the input files that config reads."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


class StaleWrite(Exception):
    """`put` was given a `base_sha256` that is not the stored file's: someone saved in between."""

    def __init__(self, name: str, current_sha256: str, message: str | None = None):
        super().__init__(message or f"pipeline {name!r} changed since it was read (now {current_sha256[:12]})")
        self.name = name
        self.current_sha256 = current_sha256


@dataclass(frozen=True)
class PipelineFile:
    name: str
    text: str
    sha256: str
    folder: Path


@runtime_checkable
class PipelineStore(Protocol):
    def list(self) -> builtins.list[str]: ...

    def get(self, name: str) -> PipelineFile:
        """KeyError for an unknown pipeline, ValueError for an invalid name."""
        ...

    def put(self, name: str, text: str, base_sha256: str | None) -> PipelineFile:
        """Replace the config text. `StaleWrite` unless `base_sha256` is the stored file's hash."""
        ...

    def create(self, name: str, text: str, files: dict[str, Path]) -> PipelineFile:
        """A new pipeline: `files` maps a path relative to the folder to the file copied there.
        FileExistsError if the name is taken, ValueError for an invalid name or a path leaving the folder."""
        ...

    def add_file(self, name: str, filename: str, data: bytes) -> str:
        """Store an input file (an uploaded structure) in the pipeline folder; return its name relative to the
        folder, which is what the config should point at. A file of that name with different bytes is never
        overwritten: the new one gets a name of its own. ValueError for a name that is not a plain file name,
        KeyError for no pipeline."""
        ...

    def files(self, name: str) -> builtins.list[str]:
        """The input files in the pipeline folder (not config.toml), relative to it, sorted."""
        ...
