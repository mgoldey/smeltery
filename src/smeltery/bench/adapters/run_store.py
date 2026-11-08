"""Filesystem run store over the existing record format, with a SQLite index cache."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from smeltery.bench.adapters._sqlite import open_db
from smeltery.gui.results import Run, load_run


class FilesystemRunStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._store_dir = self.root / ".smeltery" / "bench"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._db = self._store_dir / ".bench_index.sqlite"
        self._init_db()

    def _init_db(self) -> None:
        with open_db(self._db, wal=True) as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS runs (path TEXT PRIMARY KEY, campaign TEXT, digest TEXT, indexed_at TEXT)"
            )
            con.commit()

    def _discover(self, root: Path | None = None) -> list[Path]:
        r = Path(root).resolve() if root else self.root
        if not r.exists():
            return []
        return sorted(r.rglob("run-*.json"))

    def list_runs(self, root: str | Path | None = None) -> list[dict[str, Any]]:
        out = []
        for p in self._discover(Path(root) if root else None):
            try:
                run = load_run(p)
            except Exception as e:  # noqa: BLE001
                # Fall back to raw parse so an incomplete/dead run still surfaces (verdict INCOMPLETE).
                try:
                    raw = json.loads(p.read_text())
                except Exception:
                    out.append({"path": str(p), "error": str(e)})
                    continue
                if not isinstance(raw, dict):
                    out.append({"path": str(p), "error": str(e)})
                    continue
                inputs = raw.get("inputs", {}) if isinstance(raw.get("inputs"), dict) else {}
                cut_cfg = inputs.get("cut", {}) if isinstance(inputs.get("cut"), dict) else {}
                out.append(
                    {
                        "path": str(p),
                        "campaign": raw.get("campaign"),
                        "parent": inputs.get("parent"),
                        "keep": cut_cfg.get("keep"),
                        "error": str(e),
                        "incomplete": True,
                    }
                )
                continue
            out.append(
                {
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
                }
            )
        return out

    def find_runs(self, root: str | Path | None = None) -> list[Path]:
        return self._discover(Path(root) if root else None)

    def read_run(self, path: str | Path) -> Run:
        return load_run(path)

    def get_run(self, path: str | Path) -> Run:
        return self.read_run(path)

    def index_run(self, path: str | Path) -> None:
        p = Path(path)
        run = load_run(p)
        digest = run.record.get("input_digest", "")
        with open_db(self._db) as con:
            con.execute(
                "INSERT OR REPLACE INTO runs (path, campaign, digest, indexed_at) VALUES (?,?,?,?)",
                (str(p), run.campaign, digest, None),
            )
            con.commit()
