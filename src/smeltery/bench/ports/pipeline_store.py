"""Port: workspace pipelines. A pipeline is a folder holding its config TOML and the input files that config reads."""

from __future__ import annotations

import builtins
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

#: A pipeline's name, which is also its folder's name under ROOT/pipelines/.
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")

#: The file in a pipeline's folder that holds its config; an upload may not take this name.
CONFIG = "config.toml"
# An uploaded input's name: one path component, no leading dot, so it can neither leave the folder nor hide in it.
# Spaces and parentheses are allowed because that is what a browser hands over ("7lcj (1).pdb"); what is excluded is
# the separators and the characters a shell or TOML would have to be escaped for. A bundle's entries are held to it
# one path component at a time.
FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ()+._-]{0,127}")


def check_pipeline_name(name: str) -> None:
    """Raise `ValueError` unless `name` is an acceptable pipeline name (`NAME`)."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError(f"invalid pipeline name {name!r}: use [a-z0-9][a-z0-9_-]{{0,63}}")


def check_file_name(name: str) -> None:
    """Raise `ValueError` unless `name` is an acceptable uploaded input's file name (`FILENAME`, not `CONFIG`)."""
    if not FILENAME.fullmatch(name or "") or name == CONFIG:
        raise ValueError(f"invalid file name {name!r}: a plain name such as receptor.pdb")


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


@dataclass(frozen=True)
class InputFile:
    name: str
    size: int
    mtime: float  # seconds since the epoch
    sha256: str


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

    def files(self, name: str) -> builtins.list[InputFile]:
        """The input files in the pipeline folder (not config.toml), newest first (ties by name)."""
        ...

    def rename(self, old: str, new: str) -> PipelineFile:
        """Move the folder to a new name. KeyError for no pipeline `old`, FileExistsError if `new` is taken,
        ValueError for an invalid name."""
        ...

    def clone(self, src: str, new: str) -> PipelineFile:
        """Copy the whole folder (config, inputs, anything else kept there; not `preview/`) to `new`. Same errors."""
        ...
