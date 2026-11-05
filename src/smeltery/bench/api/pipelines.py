"""Workspace pipelines: folders under ROOT/pipelines/<name>/ holding `config.toml` and the inputs it reads.

The TOML text is the pipeline. A save stores the client's text exactly as sent (comments and layout survive) after
checking that it parses; `Pipeline.check()` issues come back with it, and a draft with errors is still saved, since
only Run is blocked by them.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from smeltery.bench.ports.pipeline_store import PipelineFile, StaleWrite
from smeltery.gui._toml import dumps
from smeltery.gui.config_text import ConfigTextError
from smeltery.gui.pipeline import Pipeline

from ..use_cases.impact import SourceRun, as_dict, impact
from ..use_cases.launch_run import smeltery_command
from . import models as M
from .common import ports, record, row
from .work import pipeline_from_run, pipeline_issues

router = APIRouter(prefix="/api", tags=["pipelines"])


class ImpactRequest(BaseModel):
    toml: str
    baseline_toml: str
    source_run: int | None = None
    name: str | None = None  # the saved pipeline: its folder resolves relative paths and holds `preview/`


class ImpactSeconds(BaseModel):
    value: float | None
    basis: str


class ImpactMemory(BaseModel):
    """`smeltery.cli.memory_estimate`'s shape, in every case; `estimate` is None when it cannot say."""

    tier: str | None
    estimate: dict[str, Any] | None
    free_gb: float | None
    require_free_gb: float | None
    check_memory: bool
    verdict: Literal["fits", "does not fit", "cannot say", "no model"]
    basis: str | None


class Impact(BaseModel):
    seconds: ImpactSeconds
    memory: ImpactMemory
    changes: list[str]
    new_digest: bool
    poses_from: Literal["computed", "source run", "none"]
    issues: list[M.Issue]
    preview_task: str | None


class PreviewStarted(BaseModel):
    task: str


def _validate(text: str) -> Pipeline | None:
    """The pipeline `text` describes. Text that is not TOML is a `ConfigTextError` carrying its line; TOML that
    `Pipeline` cannot read as a pipeline is None (reported as an issue, never a refusal)."""
    try:
        cfg = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        m = re.search(r"line (\d+)", str(e))
        raise ConfigTextError(str(e), int(m.group(1)) if m else 1) from e
    try:
        return Pipeline.from_config(cfg)
    except (KeyError, ValueError, TypeError):
        return None


def _issues(text: str) -> list[M.Issue]:
    try:
        p = _validate(text)
    except ConfigTextError as e:
        return [M.Issue(level="error", stage=None, message=str(e))]
    if p is None:
        return [M.Issue(level="error", stage=None, message="this TOML is not a pipeline config")]
    return pipeline_issues(p)


def _doc(f: PipelineFile, notes: list[str] | None = None) -> M.PipelineDoc:
    issues = _issues(f.text)
    return M.PipelineDoc(
        name=f.name,
        toml=f.text,
        sha256=f.sha256,
        folder=str(f.folder),
        ok=not any(i.level == "error" for i in issues),
        issues=issues,
        notes=notes or [],
    )


def _store(request: Request) -> Any:
    return ports(request).pipeline_store


def _get(request: Request, name: str) -> PipelineFile:
    try:
        return _store(request).get(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"no pipeline {name}") from e


@router.get("/pipelines", response_model=list[str])
def list_pipelines(request: Request) -> list[str]:
    return _store(request).list()


@router.get("/pipelines/{name}", response_model=M.PipelineDoc)
def get_pipeline(request: Request, name: str) -> M.PipelineDoc:
    return _doc(_get(request, name))


@router.put("/pipelines/{name}", response_model=M.PipelineDoc, responses={409: {"model": M.Problem}})
def put_pipeline(request: Request, name: str, body: M.PipelinePut) -> Any:
    _get(request, name)
    try:
        _validate(body.toml)
    except ConfigTextError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        saved = _store(request).put(name, body.toml, body.base_sha256)
    except StaleWrite as e:
        return JSONResponse(status_code=409, content={"reason": str(e)})
    return _doc(saved)


def _from_run(request: Request, idx: int) -> tuple[str, dict[str, Path], list[str]]:
    """(config text, {relative path: file to copy}, notes) for a new pipeline built from run `idx`."""
    r = row(request, idx)
    out_dir = Path(r["path"]).parent
    cfg = record(request, idx).get("results", {}).get("config")
    cfg_file = out_dir / cfg["file"] if isinstance(cfg, dict) and cfg.get("file") else None
    if cfg_file is None or not cfg_file.is_file():
        state = pipeline_from_run(request, M.PipelineFromRun(run=idx))
        return state.toml, {}, state.notes
    files: dict[str, Path] = {}
    for dotted, info in (cfg.get("inputs") or {}).items():
        copy = info.get("copy") if isinstance(info, dict) else None
        if not copy:
            continue
        src = out_dir / copy
        if not src.is_file():
            raise _Conflict(f"run records input {dotted} as {copy}, but {src} is not there")
        files[copy] = src
    return cfg_file.read_text(), files, []


class _Conflict(Exception):
    pass


@router.post(
    "/pipelines",
    response_model=M.PipelineDoc,
    status_code=201,
    responses={409: {"model": M.Problem}},
)
def create_pipeline(request: Request, body: M.PipelineCreate) -> Any:
    given = [x for x in (body.from_run, body.from_prebuilt, body.blank) if x is not None]
    if len(given) != 1:
        raise HTTPException(status_code=400, detail="give exactly one of from_run, from_prebuilt, blank")
    store = _store(request)
    if body.from_prebuilt is not None:
        raise HTTPException(status_code=400, detail=f"unknown prebuilt pipeline {body.from_prebuilt!r}")
    notes: list[str] = []
    files: dict[str, Path] = {}
    if body.blank is not None:
        b = body.blank
        try:
            text = dumps(Pipeline.minimal(b.campaign, b.parent, dict(b.candidates)).to_config())
        except (KeyError, ValueError, TypeError) as e:
            raise HTTPException(
                status_code=400, detail=f"cannot build a blank pipeline: {str(e).strip(chr(39))}"
            ) from e
    else:
        assert body.from_run is not None
        try:
            text, files, notes = _from_run(request, body.from_run)
        except _Conflict as e:
            return JSONResponse(status_code=409, content={"reason": str(e)})
    try:
        made = store.create(body.name, text, files)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileExistsError:
        return JSONResponse(status_code=409, content={"reason": f"pipeline {body.name} already exists"})
    return _doc(made, notes)


def _preview_task(request: Request, name: str) -> str | None:
    """The id of this pipeline's preview that is queued or running, newest first."""
    live = [
        t for t in ports(request).task_queue.list() if t.name == f"preview {name}" and t.status in ("queued", "running")
    ]
    return live[-1].id if live else None


@router.post("/pipelines/impact", response_model=Impact)
def pipeline_impact(request: Request, body: ImpactRequest) -> Any:
    """What the edit costs, without running docking or an SCF: estimates from RDKit-placed poses, from the source
    run's poses when nothing upstream of them changed, or "cannot say"."""
    for text in (body.toml, body.baseline_toml):
        try:
            _validate(text)
        except ConfigTextError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    base = Path(ports(request).settings.root)
    preview_dir = None
    task = None
    if body.name is not None:
        folder = _get(request, body.name).folder
        base, preview_dir, task = folder, folder / "preview", _preview_task(request, body.name)
    source = None
    if body.source_run is not None:
        source = SourceRun(Path(row(request, body.source_run)["path"]).parent, record(request, body.source_run))
    return as_dict(
        impact(body.toml, body.baseline_toml, base, source=source, preview_dir=preview_dir, preview_task=task)
    )


@router.post("/pipelines/{name}/preview", response_model=PreviewStarted, status_code=201)
def pipeline_preview(request: Request, name: str) -> Any:
    """Submit `smeltery run config.toml --plan --out <folder>/preview`: poses, digest, seconds and memory, no SCF."""
    folder = _get(request, name).folder
    queue = ports(request).task_queue
    running = _preview_task(request, name)
    if running is not None:  # one preview at a time per pipeline: a second click gets the first one's id
        return PreviewStarted(task=running)
    tid = queue.submit(
        f"preview {name}",
        [*smeltery_command(), "run", str(folder / "config.toml"), "--plan", "--out", str(folder / "preview")],
        cwd=str(folder),
        note=f"user={ports(request).identity.current()}",
    )
    return PreviewStarted(task=tid)
