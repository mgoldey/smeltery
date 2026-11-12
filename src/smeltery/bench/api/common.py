"""Shared lookups for the API routers: the app's ports, and a run by its index in the run store's listing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from smeltery.bench.verdict import verdict_of
from smeltery.gui.results import Run, load_run


def ports(request: Request) -> Any:
    """`app.state`, which holds the injected ports (run_store, task_queue, identity, clock, ...)."""
    return request.app.state


def rows(request: Request) -> list[dict]:
    return ports(request).run_store.list_runs()


def row(request: Request, idx: int) -> dict:
    rs = rows(request)
    if not 0 <= idx < len(rs):
        raise HTTPException(status_code=404, detail=f"no run {idx}; {len(rs)} known")
    return rs[idx]


def record(request: Request, idx: int) -> dict:
    """The raw run record. Readable even when the run is INCOMPLETE."""
    r = row(request, idx)
    try:
        data = json.loads(Path(r["path"]).read_text())
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=409, detail=f"{r['path']}: unreadable run record ({e})") from e
    if not isinstance(data, dict):
        raise HTTPException(status_code=409, detail=f"{r['path']}: not a run record")
    return data


def run(request: Request, idx: int) -> Run:
    """The loaded run. An INCOMPLETE record is a 409 naming what it lacks; `load_run`'s checks are not relaxed."""
    r = row(request, idx)
    v = verdict_of(record(request, idx))
    if v.state == "INCOMPLETE":
        raise HTTPException(status_code=409, detail=f"run is INCOMPLETE: missing {', '.join(v.missing)}")
    try:
        return load_run(r["path"])
    except Exception as e:  # noqa: BLE001 - surfaced as the reason, never swallowed
        raise HTTPException(status_code=409, detail=str(e)) from e


def utc_iso(when: Any) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ") if hasattr(when, "strftime") else str(when)


def confined_out(root: Path, requested: str | None, default: Path) -> Path | JSONResponse:
    """Where a launched run may write: strictly inside ROOT, after `~`, `..` and symlinks are resolved.

    A relative `requested` is read from ROOT. Anything that resolves outside ROOT (an absolute path elsewhere, `../x`,
    a symlink under ROOT that leaves it) is a 400 `{"reason": ...}` returned for the route to hand back, because a
    request must never stage files or write a run wherever the server user can write."""
    base = root.resolve()
    if not requested:
        return default
    p = Path(requested).expanduser()
    out = (p if p.is_absolute() else base / p).resolve()
    if out == base or not out.is_relative_to(base):
        return JSONResponse(
            status_code=400, content={"reason": f"out {requested!r} must be a directory inside the workspace {base}"}
        )
    return out
