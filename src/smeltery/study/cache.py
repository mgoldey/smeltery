"""A content-addressed step cache: a step whose inputs are unchanged is not re-executed.

This is resume, provenance and incrementality in one mechanism, and it needs no daemon, no server
and no scheduler. The key is `record.digest` of the step's name, version, parameters and the digests
of its inputs -- the same function `RunRecord.input_digest` already uses, so a cached study step and
a recorded run identify their inputs the same way.

What this buys over a hand-written resume:

* **"Did I already run this?" stops being a question.** The 5.6 h ddE run resumed only because a
  `done_keys()` was written into it by hand; the 180-run sweep resumed only because of a `find`
  predicate that was wrong on the first try and silently would have re-run everything.
* **Changing a parameter invalidates exactly what depends on it.** A new `size_std` re-runs
  generation and everything downstream, and re-runs nothing else, because the digest changes only
  along that path.
* **The provenance is the cache key.** There is no second bookkeeping scheme to drift from reality:
  if the entry is there, those inputs produced that output.

Entries are JSON by default, with an optional sidecar file for bulk output (SDFs, arrays) that is
stored by content hash so identical output is stored once. Nothing is ever silently overwritten: a
re-run that produces a different result for the same key raises, because that means the step is not
a function of its declared inputs and every downstream conclusion is suspect.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..record import digest


class CacheMismatchError(RuntimeError):
    """A step produced a different result for inputs that digest the same.

    The step is not a function of its declared inputs: it depends on something undeclared -- wall
    clock, an unseeded RNG, a file outside the key, GPU nondeterminism. Every conclusion downstream
    of it rests on which run happened to land in the cache, so this is raised rather than resolved.
    """


@dataclass(frozen=True)
class Entry:
    """What the cache holds for one key: the value, how it was made, and when."""

    key: str
    step: str
    version: str
    params: dict
    inputs: dict[str, str]
    value: Any
    blob: str | None
    written_utc: str

    @property
    def blob_path(self) -> Path | None:
        return Path(self.blob) if self.blob else None


def step_key(step: str, version: str, params: dict, inputs: dict[str, str]) -> str:
    """The content address: everything that can change the output, and nothing that cannot.

    `inputs` maps an input name to ITS key or digest, so a change anywhere upstream propagates.
    `version` is the step's own declared version: bump it when the code changes meaning, because the
    cache cannot see that and would otherwise serve a stale result forever.
    """
    return digest({"step": step, "version": version, "params": params, "inputs": dict(sorted(inputs.items()))})


class Cache:
    """A directory of JSON entries, addressed by content.

    `root/entries/<key>.json` holds the record; `root/blobs/<sha>` holds bulk output, shared between
    entries whose output bytes are identical.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.entries = self.root / "entries"
        self.blobs = self.root / "blobs"

    def path(self, key: str) -> Path:
        return self.entries / f"{key}.json"

    def has(self, key: str) -> bool:
        return self.path(key).is_file()

    def get(self, key: str) -> Entry | None:
        p = self.path(key)
        if not p.is_file():
            return None
        d = json.loads(p.read_text())
        return Entry(**d)

    def put(
        self,
        key: str,
        *,
        step: str,
        version: str,
        params: dict,
        inputs: dict[str, str],
        value: Any,
        blob_src: str | Path | None = None,
    ) -> Entry:
        """Write an entry, refusing to change one that exists with a different value.

        A differing re-run means the step is not a function of its inputs, which is a defect in the
        step rather than a cache conflict to resolve, so it raises.
        """
        existing = self.get(key)
        blob = None
        if blob_src is not None:
            blob = str(self._store_blob(Path(blob_src)))
        if existing is not None:
            if existing.value != value or (existing.blob is None) != (blob is None):
                raise CacheMismatchError(
                    f"step {step!r} gave a different result for key {key[:12]}: the step is not a "
                    f"function of its declared inputs (undeclared dependency on wall clock, an "
                    f"unseeded RNG, a file outside the key, or nondeterministic hardware). "
                    f"Fix the step or declare the missing input; do not clear the cache."
                )
            return existing
        self.entries.mkdir(parents=True, exist_ok=True)
        e = Entry(
            key=key,
            step=step,
            version=version,
            params=params,
            inputs=dict(sorted(inputs.items())),
            value=value,
            blob=blob,
            written_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        tmp = self.path(key).with_suffix(".tmp")
        tmp.write_text(json.dumps(e.__dict__, indent=2, sort_keys=True, default=str))
        # Atomic: a kill mid-write leaves no half-entry that would be read back as a cache hit.
        os.replace(tmp, self.path(key))
        return e

    def _store_blob(self, src: Path) -> Path:
        if not src.is_file():
            raise FileNotFoundError(f"blob source {src} does not exist")
        sha = digest({"bytes": src.read_bytes().hex()}) if src.stat().st_size < (1 << 20) else _file_sha(src)
        self.blobs.mkdir(parents=True, exist_ok=True)
        dst = self.blobs / sha
        if not dst.exists():
            shutil.copy2(src, dst)
        return dst

    def stats(self) -> dict:
        n = len(list(self.entries.glob("*.json"))) if self.entries.is_dir() else 0
        b = len(list(self.blobs.iterdir())) if self.blobs.is_dir() else 0
        return {"root": str(self.root), "entries": n, "blobs": b}


def _file_sha(p: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
