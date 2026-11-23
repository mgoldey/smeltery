"""Workspace pipelines on disk: ROOT/pipelines/<name>/config.toml plus the input files it reads."""

from __future__ import annotations

import builtins
import hashlib
import shutil
import threading
from pathlib import Path

from smeltery.record import file_sha256

from ..ports.pipeline_store import (
    CONFIG,
    FILENAME,
    NAME,
    InputFile,
    PipelineFile,
    StaleWrite,
    check_file_name,
    check_pipeline_name,
)
from ._files import atomic_write, tmp_pattern

__all__ = ["CONFIG", "FILENAME", "FilesystemPipelineStore"]

#: Temp-file prefixes for the two in-flight writes; `clone` ignores exactly these.
_CONFIG_TMP = ".config-"
_UPLOAD_TMP = ".upload-"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


_SHA_CACHE: dict[tuple[str, int, int], str] = {}


def _sha_file(path: Path) -> str:
    """sha256 of the file, remembered in-process by (path, size, mtime_ns) so a listing hashes a file once."""
    st = path.stat()
    key = (str(path), st.st_size, st.st_mtime_ns)
    if key not in _SHA_CACHE:
        _SHA_CACHE[key] = file_sha256(path)
    return _SHA_CACHE[key]


class FilesystemPipelineStore:
    def __init__(self, root: str | Path):
        self.dir = Path(root) / "pipelines"
        self._lock = threading.Lock()

    def _folder(self, name: str) -> Path:
        check_pipeline_name(name)
        return self.dir / name

    def list(self) -> builtins.list[str]:
        if not self.dir.is_dir():
            return []
        return sorted(p.name for p in self.dir.iterdir() if NAME.fullmatch(p.name) and (p / CONFIG).is_file())

    def get(self, name: str) -> PipelineFile:
        folder = self._folder(name)
        try:
            text = (folder / CONFIG).read_bytes().decode("utf-8")
        except FileNotFoundError as e:
            raise KeyError(name) from e
        return PipelineFile(name, text, _sha(text), folder)

    @staticmethod
    def _write(folder: Path, text: str) -> None:
        atomic_write(folder / CONFIG, text, prefix=_CONFIG_TMP)

    def put(self, name: str, text: str, base_sha256: str | None) -> PipelineFile:
        with self._lock:
            current = self.get(name)
            if base_sha256 is None:
                raise StaleWrite(
                    name,
                    current.sha256,
                    f"pipeline {name!r} already exists: a base_sha256 is required to overwrite it",
                )
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

    def add_file(self, name: str, filename: str, data: bytes) -> str:
        check_file_name(filename)
        folder = self.get(name).folder
        with self._lock:
            dest = folder / filename
            if dest.exists() and dest.read_bytes() != data:
                stem, suffix = Path(filename).stem, Path(filename).suffix
                dest = folder / f"{stem}-{hashlib.sha256(data).hexdigest()[:8]}{suffix}"
            if not dest.exists():
                atomic_write(dest, data, prefix=_UPLOAD_TMP)
        return dest.name

    def files(self, name: str) -> builtins.list[InputFile]:
        folder = self.get(name).folder
        out = []
        for p in folder.iterdir():
            if p.is_file() and p.name != CONFIG and FILENAME.fullmatch(p.name):
                st = p.stat()
                out.append(InputFile(p.name, st.st_size, st.st_mtime, _sha_file(p)))
        return sorted(out, key=lambda f: (-f.mtime, f.name))

    def rename(self, old: str, new: str) -> PipelineFile:
        src, dst = self._folder(old), self._folder(new)
        with self._lock:
            if not (src / CONFIG).is_file():
                raise KeyError(old)
            if dst.exists():
                raise FileExistsError(new)
            src.rename(dst)
        return self.get(new)

    def clone(self, src: str, new: str) -> PipelineFile:
        source, dst = self.get(src).folder, self._folder(new)
        with self._lock:
            if dst.exists():
                raise FileExistsError(new)
            try:
                # `preview/` is derived from the config (and owned by a preview task), so a copy starts without it
                shutil.copytree(
                    source,
                    dst,
                    symlinks=True,
                    ignore=shutil.ignore_patterns("preview", tmp_pattern(_CONFIG_TMP), tmp_pattern(_UPLOAD_TMP)),
                )
            except BaseException:
                shutil.rmtree(dst, ignore_errors=True)
                raise
        return self.get(new)
