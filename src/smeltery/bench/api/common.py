"""Shared lookups for the API routers: the app's ports, and a run by its stable id.

A run is addressed by the id the run store derives from the run itself (`adapters/run_store.run_id`), never by its
place in a listing: adding a run that sorts earlier used to shift every index, so a bookmark, a shared link or an
in-flight decision POST silently landed on a different run.

**Old integer links still resolve.** A path segment that is a bare non-negative integer is taken as the position in
the listing, as it was before, and `install_run_ref_deprecation` adds `Deprecation: true` plus a `Link` header naming
the stable URL to the answer. This is kept rather than 410'd because the integer form is in bookmarks, in the
`--demo` banner printed by earlier builds, and in every earlier release's shared links; a silent 404 there would be a
worse failure than one more hop, and the position is still unambiguous for a reader who has just listed the runs.
Nothing writes that form any more: `/api/runs` returns `id`, and the UI routes on it.

A run id is 12 hex digits, a hyphen and 6 more, so it is never all digits: the two forms cannot be confused.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from smeltery.bench.verdict import verdict_of
from smeltery.gui.results import Run, load_run

#: A deprecated positional run reference in a path: `/api/runs/<n>/...`, `<n>` all digits.
_POSITIONAL = re.compile(r"^(/api/runs/)(\d+)(/.*)?$")


def install_run_ref_deprecation(app: FastAPI) -> None:
    """Mark every answer to a positional `/api/runs/<n>` with `Deprecation: true` and the stable URL, so a client on
    an old link is told the new one without being broken by it."""

    @app.middleware("http")
    async def _mark(request: Request, call_next):
        response = await call_next(request)
        m = _POSITIONAL.match(request.url.path)
        if m is None or response.status_code >= 400:
            return response
        rs = request.app.state.run_store.list_runs()
        i = int(m.group(2))
        if 0 <= i < len(rs):
            response.headers["Deprecation"] = "true"
            response.headers["Link"] = f'<{m.group(1)}{rs[i]["id"]}{m.group(3) or ""}>; rel="canonical"'
        return response


def ports(request: Request) -> Any:
    """`app.state`, which holds the injected ports (run_store, task_queue, identity, clock, ...)."""
    return request.app.state


def rows(request: Request) -> list[dict]:
    return ports(request).run_store.list_runs()


def row(request: Request, ref: str | int) -> dict:
    """The row for run `ref`: its stable id, or (deprecated) its position in the listing."""
    rs = rows(request)
    ref = str(ref)
    if ref.isdigit():
        i = int(ref)
        if not 0 <= i < len(rs):
            raise HTTPException(status_code=404, detail=f"no run at position {i}; {len(rs)} known")
        return rs[i]
    for r in rs:
        if r.get("id") == ref:
            return r
    raise HTTPException(status_code=404, detail=f"no run {ref!r}; {len(rs)} known")


def record(request: Request, ref: str | int) -> dict:
    """The raw run record, from the run store's cache. Readable even when the run is INCOMPLETE."""
    r = row(request, ref)
    data = ports(request).run_store.read_record(r["path"])
    if data is None:
        raise HTTPException(status_code=409, detail=f"{r['path']}: unreadable run record")
    return data


def run(request: Request, ref: str | int) -> Run:
    """The loaded run. An INCOMPLETE record is a 409 naming what it lacks; `load_run`'s checks are not relaxed."""
    r = row(request, ref)
    v = verdict_of(record(request, ref))
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
