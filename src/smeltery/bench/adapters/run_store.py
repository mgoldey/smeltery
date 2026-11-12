"""Filesystem run store over the existing record format, with a stat-keyed parse cache.

Two things here are load-bearing, both for correctness rather than speed:

**A run's id is derived from the run, not from its place in a listing.** `run_id` is
`<input_digest[:12]>-<blake2s(relative path)[:6]>`: the record's own digest prefix, which says what was computed, plus
a short hash of the record's path relative to ROOT, which separates two copies of one computation in different
directories. Both halves matter: the digest alone collides when a run directory is copied, and the path alone changes
meaning when a record is edited in place. A record whose digest cannot be read (unparseable JSON, no `input_digest`)
gets `unknown-<path hash>`, so even a dead record is addressable. The id depends on nothing outside the run itself,
so adding or removing other runs never moves it -- which a position in `sorted(rglob(...))` did.

**Discovery is cached, keyed on (path, st_mtime_ns, st_size).** `list_runs` stats every record it finds and reuses the
parsed row for any file whose stat triple is unchanged, so repeated requests parse each unchanged record once. The
walk skips `.smeltery`, `.venv`, `node_modules`, `.git` and `__pycache__`, so an explicit ROOT of `.` does not
descend into a virtualenv or a node_modules tree.

There is deliberately no sqlite index: the cache above is the index. A sqlite file would be a second, persistent copy
of what the records already say, invalidated by the same stat triple, and its predecessor here was dead code (written,
never read). The records win because they are the only source.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from smeltery.gui.results import Run, load_run

_log = logging.getLogger(__name__)

#: Never walked into: Bench's own store, and the directory trees that make an explicit ROOT of `.` take minutes.
SKIP_DIRS = frozenset({".smeltery", ".venv", "node_modules", ".git", "__pycache__"})

#: Hex digits of the path hash in a run id. 6 is 24 bits: plenty to separate the copies of one digest under one ROOT.
_PATH_HASH_LEN = 6
_DIGEST_LEN = 12


def path_key(root: Path, path: Path) -> str:
    """The path half of a run id: a short blake2s of the record's path relative to `root` (absolute if it is outside
    `root`), with `/` separators so the same tree gives the same id on any platform."""
    p = Path(path)
    try:
        rel = p.resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        rel = p.resolve().as_posix()
    return hashlib.blake2s(rel.encode(), digest_size=8).hexdigest()[:_PATH_HASH_LEN]


def run_id(root: Path, path: Path, digest: str | None) -> str:
    """`<digest[:12]>-<path hash>`, or `unknown-<path hash>` when the record has no readable `input_digest`."""
    head = (digest or "")[:_DIGEST_LEN] or "unknown"
    return f"{head}-{path_key(root, path)}"


class FilesystemRunStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._store_dir = self.root / ".smeltery" / "bench"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        #: {resolved path: ((path, mtime_ns, size), row)}: the parse of a file whose stat triple still matches.
        self._cache: dict[str, tuple[tuple[str, int, int], dict[str, Any]]] = {}
        #: the same, for the raw record JSON (`read_record`); `None` means the file would not parse as an object.
        self._records: dict[str, tuple[tuple[str, int, int], dict[str, Any] | None]] = {}

    # ------------------------------------------------------------------------------------------ discovery

    def _walk(self, root: Path) -> list[Path]:
        """Every `run-*.json` under `root`, sorted, without descending into `SKIP_DIRS`."""
        found: list[Path] = []
        stack = [root]
        while stack:
            d = stack.pop()
            try:
                entries = list(d.iterdir())
            except OSError:
                continue
            for e in entries:
                if e.is_dir() and not e.is_symlink():
                    if e.name not in SKIP_DIRS:
                        stack.append(e)
                elif e.is_file() and e.name.startswith("run-") and e.name.endswith(".json"):
                    found.append(e)
        return sorted(found)

    def _discover(self, root: Path | None = None) -> list[Path]:
        r = Path(root).resolve() if root else self.root
        if not r.exists():
            return []
        return self._walk(r)

    def _stat_key(self, p: Path) -> tuple[str, int, int] | None:
        try:
            st = p.stat()
        except OSError:
            return None
        return (str(p), st.st_mtime_ns, st.st_size)

    # ------------------------------------------------------------------------------------------ rows

    def _parse(self, p: Path) -> dict[str, Any]:
        """One record as a row. `id` is `run_id`; every caller addresses the run by it."""
        try:
            run = load_run(p)
        except Exception as e:  # noqa: BLE001
            _log.debug("load_run failed for %s; falling back to a raw parse", p, exc_info=True)
            # Fall back to raw parse so an incomplete/dead run still surfaces (verdict INCOMPLETE).
            raw = self.read_record(p)
            if raw is None:
                return {"id": run_id(self.root, p, None), "path": str(p), "error": str(e)}
            inputs = raw.get("inputs", {}) if isinstance(raw.get("inputs"), dict) else {}
            cut_cfg = inputs.get("cut", {}) if isinstance(inputs.get("cut"), dict) else {}
            digest = raw.get("input_digest") if isinstance(raw.get("input_digest"), str) else None
            return {
                "id": run_id(self.root, p, digest),
                "path": str(p),
                "campaign": raw.get("campaign"),
                "parent": inputs.get("parent"),
                "keep": cut_cfg.get("keep"),
                "error": str(e),
                "incomplete": True,
            }
        return {
            "id": run_id(self.root, p, run.record.get("input_digest")),
            "path": str(p),
            "out_dir": str(run.out_dir),
            "campaign": run.campaign,
            "parent": run.parent,
            "candidates": list(run.candidates),
            "analogues": run.analogues,
            "survivors": run.survivors,
            "unranked": run.unranked,
            "groups": run.groups,
            "floor": run.floor,
            "floor_source": run.floor_source,
            "z": run.z,
            "keep": run.keep,
            "digest": run.record.get("input_digest"),
        }

    def _row(self, p: Path) -> dict[str, Any]:
        """The cached parse of `p`, re-parsed only when its (path, mtime_ns, size) changed."""
        key = self._stat_key(p)
        hit = self._cache.get(str(p))
        if hit is not None and key is not None and hit[0] == key:
            return hit[1]
        row = self._parse(p)
        if key is not None:
            self._cache[str(p)] = (key, row)
        return row

    def list_runs(self, root: str | Path | None = None) -> list[dict[str, Any]]:
        found = self._discover(Path(root) if root else None)
        rows = [self._row(p) for p in found]
        if root is None:  # a full listing of ROOT says which records still exist; drop the rest from both caches
            live = {str(p) for p in found}
            for cache in (self._cache, self._records):
                for gone in [k for k in cache if k not in live]:
                    del cache[gone]
        return rows

    def find_runs(self, root: str | Path | None = None) -> list[Path]:
        return self._discover(Path(root) if root else None)

    def read_record(self, path: str | Path) -> dict[str, Any] | None:
        """The record's raw JSON object, cached on the same stat triple as the row. `None`: unreadable, or not an
        object. Every caller that wants the record goes through here, so one request never reads a file twice."""
        p = Path(path)
        key = self._stat_key(p)
        hit = self._records.get(str(p))
        if hit is not None and key is not None and hit[0] == key:
            return hit[1]
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict):
            data = None
        if key is not None:
            self._records[str(p)] = (key, data)
        return data

    def read_run(self, path: str | Path) -> Run:
        return load_run(path)

    def get_run(self, path: str | Path) -> Run:
        return self.read_run(path)
