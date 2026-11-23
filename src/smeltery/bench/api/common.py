"""Shared lookups for the API routers: the app's ports, a run by its stable id, and the HTTP translation every
pipeline-editing router needs (reading a pipeline or answering 400, mapping an edit's refusal, the workspace defaults,
a capped upload body, a submitted task read back), so no router imports another router's helpers.

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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from smeltery.bench.use_cases import bundle_import
from smeltery.bench.use_cases import edit_pipeline as editing
from smeltery.bench.use_cases.read_run import inside
from smeltery.bench.verdict import verdict_of
from smeltery.config_text import ConfigTextError
from smeltery.decisions import TIME_FORMAT
from smeltery.gui.pipeline import Pipeline
from smeltery.run_view import Run

from . import models as M

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
    """The loaded run, from the run store's `read_run`. An INCOMPLETE record is a 409 naming what it lacks;
    `load_run`'s checks are not relaxed."""
    r = row(request, ref)
    v = verdict_of(record(request, ref))
    if v.state == "INCOMPLETE":
        raise HTTPException(status_code=409, detail=f"run is INCOMPLETE: missing {', '.join(v.missing)}")
    try:
        return ports(request).run_store.read_run(r["path"])
    except Exception as e:  # noqa: BLE001 - surfaced as the reason, never swallowed
        raise HTTPException(status_code=409, detail=str(e)) from e


def utc_iso(when: Any) -> str:
    """`when` as `smeltery.decisions.TIME_FORMAT` (`YYYY-MM-DDTHH:MM:SSZ`); anything without `strftime` as text."""
    return when.strftime(TIME_FORMAT) if hasattr(when, "strftime") else str(when)


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
    if not inside(base, out):
        return JSONResponse(
            status_code=400, content={"reason": f"out {requested!r} must be a directory inside the workspace {base}"}
        )
    return out


# ---------------------------------------------------------------------------------------------------- pipelines


class EditedText(BaseModel):
    toml: str


def saved_pipeline(request: Request, name: str) -> Any:
    """The saved pipeline `name` (a `PipelineFile`); an unknown or unusable name is a 404."""
    try:
        return ports(request).pipeline_store.get(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"no pipeline {name}") from e


def parse_or_400(text: str) -> Pipeline | None:
    """The pipeline `text` describes, None for TOML that is not a pipeline; text that is not TOML is a 400."""
    try:
        return editing.parse(text)
    except ConfigTextError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def pipeline_or_400(text: str) -> Pipeline:
    """`parse_or_400`, and TOML that is not a pipeline is a 400 too: the editor's panes cannot act on it."""
    p = parse_or_400(text)
    if p is None:
        raise HTTPException(status_code=400, detail=editing.NOT_A_PIPELINE)
    return p


def apply_edit(edit: Callable[[], str]) -> EditedText:
    """Run one `use_cases.edit_pipeline` edit and answer with the new text. Every refusal is a 400: a
    `ConfigTextError` or an `EditRefused` says exactly what it says; any other `KeyError`/`ValueError` (from
    `Pipeline`) loses the quotes a `KeyError` puts round its message."""
    try:
        return EditedText(toml=edit())
    except (ConfigTextError, editing.EditRefused) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e).strip("'\"")) from e


def workspace_defaults(request: Request, label: str | None = None) -> dict[str, dict[str, Any]]:
    """The Methods tab's `{method_key: {param: value}}`. A methods.toml that was hand-broken is a 409 naming the file
    and line, not a 500; `label`, when given, prefixes the reason (`"<label>: <error>"`)."""
    try:
        return ports(request).method_defaults.all()
    except ConfigTextError as e:
        raise HTTPException(status_code=409, detail=f"{label}: {e}" if label else str(e)) from e


# ---------------------------------------------------------------------------------------------------- uploads

#: An uploaded input larger than this is refused before it is written (a whole-cell cryo-EM map is not an input).
#: The same 64 MB caps one file of an imported bundle, so it is defined once, there.
MAX_UPLOAD = bundle_import.MAX_FILE


def too_large(name: str, limit: int) -> HTTPException:
    return HTTPException(status_code=413, detail=f"{name}: larger than {limit // (1024 * 1024)} MB")


async def read_capped_body(request: Request, name: str, limit: int) -> bytes:
    """The request body, refused with a 413 naming `name` when it is larger than `limit` bytes.

    Content-Length first: `request.body()` buffers the whole upload in memory, so refusing after reading it would
    protect the disk and not the process. The length is still checked after reading, for a chunked body that has
    none."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise too_large(name, limit)
    data = await request.body()
    if len(data) > limit:
        raise too_large(name, limit)
    return data


# ---------------------------------------------------------------------------------------------------- tasks


def task_model(t: Any, **extra: Any) -> M.Task:
    """A task port's task (an object with `to_dict`, or already a dict) as the API's `Task`, with `extra` fields."""
    return M.Task(**{**(t.to_dict() if hasattr(t, "to_dict") else t), **extra})


def submitted_task(request: Request, task_id: str) -> M.Task:
    """The task just submitted, read back from the queue; a queue that cannot find it is a 500 saying so."""
    t = ports(request).task_queue.get(task_id)
    if t is None:
        raise HTTPException(status_code=500, detail=f"task {task_id} was submitted but cannot be read back")
    return task_model(t)
