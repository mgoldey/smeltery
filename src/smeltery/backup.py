"""`smeltery backup ROOT --out FILE.tar.gz` and `smeltery restore FILE.tar.gz --into DIR`.

An archive holds MANIFEST.json (relative path, size, sha256 per file, smeltery version, created_utc) and the workspace
files under `workspace/`. What is backed up: everything under ROOT (pipelines, run folders, `.smeltery/bench/` stores,
methods.toml, artifacts) except caches and build output (`__pycache__`, `node_modules`, `.git`, `.venv`,
`.pytest_cache`,
`.ruff_cache`, `.mypy_cache`) and SQLite `-wal`/`-shm`/`-journal` sidecars. Each SQLite database is copied with the
online backup API, so it is consistent while Bench runs (the sidecars' content is folded into the copy). Symlinks are
skipped with a warning: nothing outside ROOT is ever followed. The archive file itself is skipped if it is inside ROOT.

Restore goes into a missing or empty directory only. Nothing is trusted: member names, types and every sha256 are
checked
in a staging folder next to the target, which replaces the target only when all of it is good.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path, PurePosixPath

PREFIX = "workspace"
MANIFEST = "MANIFEST.json"
SKIP_DIRS = frozenset({"__pycache__", "node_modules", ".git", ".venv", ".pytest_cache", ".ruff_cache", ".mypy_cache"})
SIDECARS = ("-wal", "-shm", "-journal")
DB_SUFFIXES = (".sqlite", ".sqlite3", ".db")


class BackupError(Exception):
    pass


def _is_sqlite(path: Path) -> bool:
    if path.suffix not in DB_SUFFIXES:
        return False
    try:
        with path.open("rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def _snapshot_db(src: Path, dst: Path) -> None:
    with closing(sqlite3.connect(src, timeout=30)) as s, closing(sqlite3.connect(dst)) as d:
        s.backup(d)


def _is_sidecar(p: Path) -> bool:
    """X-wal, X-shm, X-journal of a SQLite database X that exists; any other file of that name is data."""
    for sfx in SIDECARS:
        if p.name.endswith(sfx):
            base = p.with_name(p.name[: -len(sfx)])
            return base.is_file() and _is_sqlite(base)
    return False


def _walk(root: Path, skip: frozenset[Path], warn):
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        here = Path(dirpath)
        keep = []
        for d in sorted(dirnames):
            if (here / d).is_symlink():
                warn(f"skipped symlink {(here / d).relative_to(root).as_posix()}")
            elif d not in SKIP_DIRS and (here / d).resolve() not in skip:
                keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            p = here / name
            if p.is_symlink():
                warn(f"skipped symlink {p.relative_to(root).as_posix()}")
            elif _is_sidecar(p) or p.resolve() in skip or not p.is_file():
                continue
            else:
                yield p


class _Hashing:
    """Reads pass through and are hashed: the manifest hash is of exactly the bytes the archive got."""

    def __init__(self, f):
        self.f, self.h = f, hashlib.sha256()

    def read(self, n=-1):
        data = self.f.read(n)
        self.h.update(data)
        return data


def _after_stat(path: Path) -> None:
    """Called between a file's fstat and its read; a seam for tests (a live log grows exactly there)."""


def _add_file(tar: tarfile.TarFile, src: Path, arcname: str) -> dict:
    """Add `src` reading it once: the size comes from one fstat, exactly that many bytes are streamed and hashed."""
    with src.open("rb") as f:
        ti = tar.gettarinfo(arcname=arcname, fileobj=f)
        ti.name = arcname
        _after_stat(src)
        reader = _Hashing(f)
        try:
            tar.addfile(ti, reader)  # type: ignore[arg-type]  # tarfile only calls .read
        except OSError as e:
            raise BackupError(f"{src} changed while it was read (shorter than its size): {e}") from e
        return {"size": ti.size, "sha256": reader.h.hexdigest()}


def backup(
    root: Path, out: Path, warn=lambda m: print(f"warning: {m}", file=sys.stderr), *, force: bool = False
) -> dict:
    """Write the archive and return its manifest. Raises BackupError."""
    from . import __version__

    root, out = Path(root).resolve(), Path(out).absolute()
    if not root.is_dir():
        raise BackupError(f"{root} is not a directory")
    if out.exists() and not force:
        raise BackupError(f"{out} exists: pass --force to overwrite it")
    out.parent.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict] = {}
    tmp_out = out.with_name(out.name + ".part")
    scratch = Path(tempfile.mkdtemp(prefix=".smeltery-backup-", dir=out.parent))
    skip = frozenset({out.resolve(), tmp_out.resolve(), scratch.resolve()})
    try:
        with tarfile.open(tmp_out, "w:gz") as tar:
            for i, p in enumerate(_walk(root, skip, warn)):
                rel = p.relative_to(root).as_posix()
                if _is_sqlite(p):
                    snap = scratch / f"{i}.db"
                    _snapshot_db(p, snap)
                    files[rel] = _add_file(tar, snap, f"{PREFIX}/{rel}")
                    snap.unlink()
                else:
                    files[rel] = _add_file(tar, p, f"{PREFIX}/{rel}")
            manifest = {
                "smeltery_version": __version__,
                "created_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "files": files,
            }
            blob = json.dumps(manifest, indent=2, sort_keys=True).encode() + b"\n"
            ti = tarfile.TarInfo(MANIFEST)
            ti.size, ti.mtime = len(blob), int(dt.datetime.now(dt.UTC).timestamp())
            ti.mode = 0o644
            tar.addfile(ti, io.BytesIO(blob))
        os.replace(tmp_out, out)
    except (OSError, sqlite3.Error) as e:
        raise BackupError(str(e)) from e
    finally:
        tmp_out.unlink(missing_ok=True)
        shutil.rmtree(scratch, ignore_errors=True)
    return manifest


def _bad_name(name: str) -> str | None:
    p = PurePosixPath(name)
    if "\\" in name or "\x00" in name:
        return "backslash or NUL in the member name"
    if p.is_absolute() or re.match(r"^[A-Za-z]:", name):
        return "absolute path"
    if ".." in p.parts:
        return "path leaves the archive ('..')"
    return None


def _stage(archive: Path, stage: Path) -> list[str]:
    """Extract the archive into `stage`, checking every member; returns problems (empty means all verified)."""
    problems: list[str] = []
    try:
        tar = tarfile.open(archive, "r:gz")
    except (tarfile.TarError, OSError, EOFError) as e:
        raise BackupError(f"{archive} is not a readable backup archive: {e}") from e
    with tar:
        try:
            members = tar.getmembers()
        except (tarfile.TarError, OSError, EOFError) as e:
            raise BackupError(f"{archive} is damaged: {e}") from e
        manifest = None
        wanted: dict[str, tarfile.TarInfo] = {}
        for m in members:
            why = _bad_name(m.name)
            if why is None and not (m.isfile() or m.isdir()):
                why = "not a regular file (links and special files are refused)"
            if why is not None:
                problems.append(f"{m.name}: {why}")
            elif m.name == MANIFEST and m.isfile():
                f = tar.extractfile(m)
                try:
                    manifest = json.loads(f.read().decode("utf-8")) if f else None
                    listed = manifest["files"]
                    assert isinstance(listed, dict)
                except (ValueError, KeyError, TypeError, AssertionError, UnicodeDecodeError) as e:
                    raise BackupError(f"{MANIFEST} is not a backup manifest") from e
            elif m.isfile() and m.name.startswith(PREFIX + "/"):
                if m.name in wanted:
                    problems.append(f"{m.name}: repeated member")
                wanted[m.name] = m
            elif m.isfile():
                problems.append(f"{m.name}: not under {PREFIX}/ and not {MANIFEST}")
        if problems:
            return problems
        if manifest is None:
            raise BackupError(f"{MANIFEST} is missing: not a smeltery backup")
        rels = {n[len(PREFIX) + 1 :]: m for n, m in wanted.items()}
        for rel in sorted(set(rels) - set(listed)):
            problems.append(f"{rel}: in the archive but not in {MANIFEST}")
        for rel in sorted(set(listed) - set(rels)):
            problems.append(f"{rel}: listed in {MANIFEST} but not in the archive")
        for rel, m in sorted(rels.items()):
            meta = listed.get(rel)
            if not isinstance(meta, dict):
                problems.append(f"{rel}: its {MANIFEST} entry is not an object")
                continue
            dest = stage / rel
            h, n = hashlib.sha256(), 0
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                f = tar.extractfile(m)
                with dest.open("wb") as w:
                    while f is not None and (chunk := f.read(1 << 20)):
                        n += len(chunk)
                        h.update(chunk)
                        w.write(chunk)
            except (tarfile.TarError, OSError, EOFError) as e:
                problems.append(f"{rel}: cannot be extracted: {e}")
                continue
            if h.hexdigest() != meta.get("sha256") or n != meta.get("size"):
                problems.append(f"{rel}: sha256 or size does not match {MANIFEST}")
    return problems


def restore(archive: Path, into: Path, *, dry_run: bool = False) -> list[str]:
    """Restore into a missing or empty `into` and return the relative paths written (or that would be). Raises
    BackupError, naming the files, when anything is refused or does not verify; then nothing is left behind."""
    archive, into = Path(archive), Path(into).absolute()
    if into.exists() and (not into.is_dir() or any(into.iterdir())):
        raise BackupError(f"{into} exists and is not an empty directory: restore never overwrites")
    into.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".smeltery-restore-", dir=into.parent))
    try:
        problems = _stage(archive, stage)
        if problems:
            raise BackupError("restore refused:\n  " + "\n  ".join(problems))
        written = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file())
        if dry_run:
            return written
        mask = os.umask(0)
        os.umask(mask)
        stage.chmod(0o777 & ~mask)
        if into.exists():
            into.rmdir()
        os.rename(stage, into)
        return written
    except OSError as e:
        raise BackupError(str(e)) from e
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def backup_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="smeltery backup", description="Archive a Bench workspace with a hash manifest.")
    ap.add_argument("root", help="the Bench workspace root")
    ap.add_argument("--out", required=True, help="archive to write (.tar.gz)")
    ap.add_argument("--force", action="store_true", help="overwrite --out if it exists")
    args = ap.parse_args(argv)
    try:
        m = backup(Path(args.root), Path(args.out), force=args.force)
    except BackupError as e:
        print(f"smeltery backup: {e}", file=sys.stderr)
        return 1
    print(f"smeltery backup: {len(m['files'])} files -> {args.out}")
    return 0


def restore_main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="smeltery restore", description="Restore a backup into an empty directory.")
    ap.add_argument("archive")
    ap.add_argument("--into", required=True, help="a missing or empty directory")
    ap.add_argument(
        "--dry-run", action="store_true", help="list what would be written and verify hashes; write nothing"
    )
    args = ap.parse_args(argv)
    try:
        written = restore(Path(args.archive), Path(args.into), dry_run=args.dry_run)
    except BackupError as e:
        print(f"smeltery restore: {e}", file=sys.stderr)
        return 1
    if args.dry_run:
        print("\n".join(written))
        print(f"smeltery restore: dry run, {len(written)} files verified, nothing written")
    else:
        print(f"smeltery restore: {len(written)} files verified and written to {args.into}")
    return 0
