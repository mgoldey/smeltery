"""Workspace pipelines: folders under ROOT/pipelines/<name>/ holding `config.toml` and the inputs it reads.

The TOML text is the pipeline. A save stores the client's text exactly as sent (comments and layout survive) after
checking that it parses; `Pipeline.check()` issues come back with it, and a draft with errors is still saved, since
only Run is blocked by them.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from smeltery.bench.ports.pipeline_store import NAME, PipelineFile, StaleWrite, check_pipeline_name
from smeltery.config_text import ConfigTextError, diff_lines
from smeltery.gui import steps as step_model
from smeltery.methods.catalog import method

from ..use_cases import bundle_export, bundle_import, next_round
from ..use_cases import create_pipeline as new_pipeline
from ..use_cases import edit_pipeline as editing
from ..use_cases.impact import SourceRun, as_dict, impact
from ..use_cases.launch_run import LaunchRun, LiveRunRefused, smeltery_command
from . import models as M
from .common import (
    MAX_UPLOAD,
    EditedText,
    apply_edit,
    confined_out,
    parse_or_400,
    ports,
    read_capped_body,
    record,
    row,
    run,
    saved_pipeline,
    submitted_task,
    workspace_defaults,
)

router = APIRouter(prefix="/api", tags=["pipelines"])


class ImpactRequest(BaseModel):
    toml: str
    baseline_toml: str
    source_run: str | None = None
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


class StepsRequest(BaseModel):
    toml: str
    baseline_toml: str | None = None  # the kept text: what "changed", "was" and "result will change" compare with


class StepParam(BaseModel):
    method: str
    name: str
    value: Any
    default: Any
    help: str
    kind: str
    changed_from: Any = Field(description="the baseline's value when it differs, else null")
    workspace_default: Any = Field(description="the Methods tab's default for this knob, null when none is set")
    choices: list[Any] = []  # a `choice` knob's options, from the catalog
    names_file: bool = Field(
        False, description="the value names an input file (the method's file_knobs): offer a picker"
    )


class StepLink(BaseModel):
    step: str | None  # the step it comes from (inputs) or goes to (gives); null for the pipeline's own inputs
    text: str


class StepOut(BaseModel):
    key: str
    label: str
    summary: str
    state: Literal["ready", "needs_input", "off", "changed", "stale"]
    inputs: list[StepLink]
    gives: list[StepLink]
    methods: list[str]
    placed: list[str]  # the offered methods this draft places, in the step's order
    chosen: str | None
    params: list[StepParam]
    issues: list[M.Issue]


class DiffLine(BaseModel):
    sign: str = Field(description='"+" added, "-" removed, " " unchanged context')
    line: str


class Ligands(BaseModel):
    campaign: str
    parent: str
    candidates: dict[str, str]


class StepsOut(BaseModel):
    steps: list[StepOut]
    ligands: Ligands | None = Field(description="what the Ligands step holds; null when the text is not a pipeline")
    pipeline_issues: list[M.Issue] = Field(description="issues that belong to no one step: shown above the drawing")
    diff: list[DiffLine] = Field(description="the line diff from baseline_toml to toml, context lines included")


class SiteSpec(BaseModel):
    atom: int
    from_atom: int
    q: float = 1.0
    distance: float = 3.0


class StepEdit(BaseModel):
    toml: str
    op: Literal["add", "remove", "set", "choose", "site", "revert"]
    key: str  # a catalog method key; for `revert`, a step key
    params: dict[str, Any] | None = None  # `set`: knob -> value, null unsets; `add`/`choose`: initial knobs
    site: SiteSpec | None = None  # `site`: the charge to place
    baseline_toml: str | None = None  # `revert`: the kept text the step goes back to


class PipelineLaunch(BaseModel):
    out: str | None = None
    timeout_s: float | None = Field(
        default=None, gt=0, description="fail the run after this many wall-clock seconds; omit for no limit"
    )


def _check_name(name: str) -> None:
    """A pipeline name the store would not accept (`check_pipeline_name`) is a 400 saying why."""
    try:
        check_pipeline_name(name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _unique_name(taken: Any, base: str) -> str:
    try:
        return new_pipeline.unique_name(taken, base)
    except new_pipeline.NoFreeName as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


def _issues(text: str) -> list[M.Issue]:
    try:
        p = editing.parse(text)
    except ConfigTextError as e:
        return [M.Issue(level="error", stage=None, message=str(e))]
    if p is None:
        return [M.Issue(level="error", stage=None, message="this TOML is not a pipeline config")]
    return [M.Issue.model_validate({"level": i.level, "stage": i.stage, "message": i.message}) for i in p.check()]


def _doc(f: PipelineFile, notes: list[str] | None = None) -> M.PipelineDoc:
    issues = _issues(f.text)
    return M.PipelineDoc(
        name=f.name,
        toml=f.text,
        sha256=f.sha256,
        folder=str(f.folder),
        ok=not any(i.level == "error" for i in issues),
        issues=issues,
        # A next round says in its own config what it carried, so every GET explains it, not only the POST that
        # made it. Without this the user is handed a pipeline whose rejected candidate looks like all the others.
        notes=notes if notes is not None else next_round.notes_in(f.text),
    )


def _store(request: Request) -> Any:
    return ports(request).pipeline_store


def _get(request: Request, name: str) -> PipelineFile:
    return saved_pipeline(request, name)


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
        editing.parse(body.toml)
    except ConfigTextError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        saved = _store(request).put(name, body.toml, body.base_sha256)
    except StaleWrite as e:
        return JSONResponse(status_code=409, content={"reason": str(e)})
    return _doc(saved)


class AddedFile(BaseModel):
    path: str = Field(description="the name relative to the pipeline folder; write this into the config")


@router.put(
    "/pipelines/{name}/files/{filename}",
    response_model=AddedFile,
    responses={400: {"model": M.Problem}, 404: {"model": M.Problem}, 413: {"model": M.Problem}},
)
async def add_pipeline_file(request: Request, name: str, filename: str) -> AddedFile:
    """A file picked in the browser (a structure, a pocket), stored in the pipeline folder beside config.toml, where
    `smeltery run` and the preview resolve the config's relative paths. The body is the file's bytes. A different
    file of the same name is kept, and the new one gets its own name: the response says which name to use."""
    _get(request, name)
    data = await read_capped_body(request, filename, MAX_UPLOAD)
    try:
        return AddedFile(path=_store(request).add_file(name, filename, data))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class InputFileOut(BaseModel):
    name: str
    size: int = Field(description="bytes")
    mtime: str = Field(description="last modified, ISO 8601 UTC")
    sha256: str = Field(description="full hex digest of the content")


@router.get("/pipelines/{name}/files", response_model=list[InputFileOut])
def list_pipeline_files(request: Request, name: str) -> list[InputFileOut]:
    """The input files already in the pipeline folder, newest first, for the picker to offer."""
    _get(request, name)
    return [
        InputFileOut(
            name=f.name,
            size=f.size,
            mtime=datetime.fromtimestamp(f.mtime, UTC).isoformat(),
            sha256=f.sha256,
        )
        for f in _store(request).files(name)
    ]


@router.get("/pipelines/{name}/bundle", responses={404: {"model": M.Problem}})
def pipeline_bundle(request: Request, name: str) -> FileResponse:
    """The SAVED config.toml, every input file it names (and the other files in the folder) and a manifest.json
    (sha256 and size of each) as one zip under `<name>/`. Inputs keep their path relative to the pipeline folder;
    an absolute path is stored as `inputs/<basename>` and its original path recorded in the manifest."""
    pf = _get(request, name)
    tmp = tempfile.NamedTemporaryFile(prefix="bundle-", suffix=".zip", delete=False)
    tmp.close()
    bundle_export.write_bundle(name, pf.text, pf.folder, [f.name for f in _store(request).files(name)], Path(tmp.name))
    return FileResponse(
        tmp.name,
        media_type="application/zip",
        filename=f"{name}-bundle.zip",
        background=BackgroundTask(os.unlink, tmp.name),
    )


def _from_run(request: Request, run_id: str) -> tuple[str, dict[str, Path], list[str]]:
    """(config text, {relative path: file to copy}, notes) for a new pipeline built from run `run_id`."""
    r = row(request, run_id)
    start = next_round.from_run(Path(r["path"]).parent, record(request, run_id), lambda: run(request, run_id))
    return start.toml, start.files, start.notes


def _next_round(request: Request, spec: M.NextRoundIn, staging: Path) -> tuple[str, dict[str, Path], list[str]]:
    """The next round of run `spec.run`: its recorded config with the decisions and accepted proposals carried in.

    Everything that decides the round lives in `use_cases.next_round`; this only names the run and turns a refusal
    into a 400.
    """
    text, files, notes = _from_run(request, spec.run)
    r = run(request, spec.run)
    digest = r.record["input_digest"]
    try:
        got = next_round.gather(
            out_dir=Path(r.out_dir),
            digest=digest,
            run_ref=spec.run,
            projects=ports(request).project_store,
            proposal_ids=list(spec.add_proposals),
        )
    except ValueError as e:  # an unloadable decisions file, or a proposal of another run
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        made = next_round.plan(
            text=text,
            candidates=dict(r.candidates),
            parent=r.parent,
            decisions=got.decisions,
            decisions_name=got.decisions_name,
            carry_decisions=spec.carry_decisions,
            proposals=got.proposals,
            drop=list(spec.drop),
            staging=staging,
        )
    except next_round.NextRoundRefused as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except ConfigTextError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return made.toml, {**files, **made.files}, notes + made.notes


@router.post(
    "/pipelines",
    response_model=M.PipelineDoc,
    status_code=201,
    responses={400: {"model": M.Problem}, 409: {"model": M.Problem}},
)
def create_pipeline(request: Request, body: M.PipelineCreate) -> Any:
    given = [x for x in (body.from_run, body.from_prebuilt, body.blank, body.next_round) if x is not None]
    if len(given) != 1:
        raise HTTPException(status_code=400, detail="give exactly one of from_run, from_prebuilt, blank, next_round")
    store = _store(request)
    notes: list[str] = []
    files: dict[str, Path] = {}
    staging: tempfile.TemporaryDirectory[str] | None = None
    name = body.name
    if body.next_round is not None:
        _check_name(name)
        staging = tempfile.TemporaryDirectory(prefix="smeltery-next-round-")
        try:
            text, files, notes = _next_round(request, body.next_round, Path(staging.name))
            # a second next round from the same run is the normal case, not a mistake the user can act on
            name = _unique_name(store.list(), name)
        except next_round.InputsMissing as e:
            staging.cleanup()
            return JSONResponse(status_code=409, content={"reason": str(e)})
        except BaseException:
            staging.cleanup()
            raise
    elif body.from_prebuilt is not None:
        prebuilts = ports(request).prebuilt_source
        if body.from_prebuilt not in prebuilts.names():
            raise HTTPException(status_code=400, detail=f"unknown prebuilt pipeline {body.from_prebuilt!r}")
        # R15: the config is copied EXACTLY, no workspace defaults. The example run goes in as run/, so the run store
        # (which scans the Bench root) lists it and the UI can open the workspace with ?run=<its index>.
        text = prebuilts.config_text(body.from_prebuilt)
        staging = tempfile.TemporaryDirectory(prefix="smeltery-prebuilt-")
        files = prebuilts.stage(body.from_prebuilt, Path(staging.name))
    elif body.blank is not None:
        b = body.blank
        defaults = workspace_defaults(request, label="methods.toml")
        try:
            text = new_pipeline.blank_text(b.campaign, b.parent, dict(b.candidates), defaults)
        except (KeyError, ValueError, TypeError) as e:
            raise HTTPException(
                status_code=400, detail=f"cannot build a blank pipeline: {str(e).strip(chr(39))}"
            ) from e
    else:
        assert body.from_run is not None
        try:
            text, files, notes = _from_run(request, body.from_run)
        except next_round.InputsMissing as e:
            return JSONResponse(status_code=409, content={"reason": str(e)})
    try:
        made = store.create(name, text, files)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileExistsError:
        return JSONResponse(status_code=409, content={"reason": f"pipeline {name} already exists"})
    finally:
        if staging is not None:
            staging.cleanup()
    return _doc(made, notes)


class NewName(BaseModel):
    new_name: str


def _busy_with(request: Request, folder: Path, name: str) -> list[str]:
    """Ids of the queued or running tasks that work in this pipeline: its preview, or anything whose cwd is inside it
    (a prebuilt example run lives under `run/`). Launched runs are NOT tied to the pipeline: they stage their own
    copy of the config and inputs into OUT, and a run record stores no pipeline name, so no run is orphaned by a
    rename."""
    out = []
    for t in _live_tasks(request):
        cwd = Path(t.cwd) if t.cwd else None
        if t.name == f"preview {name}" or (cwd is not None and (cwd == folder or folder in cwd.parents)):
            out.append(t.id)
    return out


def _lifecycle(request: Request, name: str, new_name: str, op: Literal["rename", "clone"]) -> Any:
    f = _get(request, name)
    _check_name(new_name)
    if new_name == name:
        raise HTTPException(status_code=400, detail="the new name is the same as the old one")
    if op == "rename":
        busy = _busy_with(request, f.folder, name)
        if busy:
            return JSONResponse(
                status_code=409,
                content={
                    "reason": f"pipeline {name} has tasks queued or running ({', '.join(busy)}): wait or cancel them"
                },
            )
    try:
        made = getattr(_store(request), op)(name, new_name)
    except FileExistsError:
        return JSONResponse(status_code=409, content={"reason": f"pipeline {new_name} already exists"})
    return _doc(made)


@router.post(
    "/pipelines/{name}/rename",
    response_model=M.PipelineDoc,
    responses={400: {"model": M.Problem}, 409: {"model": M.Problem}},
)
def rename_pipeline(request: Request, name: str, body: NewName) -> Any:
    """Move the pipeline's folder to a new name. Refused (409) while a task of this pipeline is queued or running.
    Runs are unaffected: they keep their own staged copy and record no pipeline name."""
    return _lifecycle(request, name, body.new_name, "rename")


@router.post(
    "/pipelines/{name}/clone",
    response_model=M.PipelineDoc,
    status_code=201,
    responses={400: {"model": M.Problem}, 409: {"model": M.Problem}},
)
def clone_pipeline(request: Request, name: str, body: NewName) -> Any:
    """A copy of the pipeline's folder (config, inputs, everything but `preview/`) under a new name."""
    return _lifecycle(request, name, body.new_name, "clone")


#: A bundle larger than this is refused while it streams in (before it is unpacked).
MAX_BUNDLE = bundle_import.MAX_TOTAL


class ImportedFile(BaseModel):
    path: str
    ok: bool
    message: str | None = None


class ImportResult(BaseModel):
    pipeline: M.PipelineDoc | None = Field(description="null when anything was refused: nothing is created then")
    files: list[ImportedFile]
    notes: list[str] = []


class ImportRefused(M.Problem):
    """A refused bundle: the `Problem` reason, plus the per-file verdicts the import dialog lists."""

    pipeline: None = None
    files: list[ImportedFile]
    notes: list[str] = []


@router.post(
    "/pipelines/import",
    response_model=ImportResult,
    status_code=201,
    responses={
        400: {"model": M.Problem},
        409: {"model": M.Problem},
        413: {"model": M.Problem},
        422: {"model": ImportRefused},
    },
)
async def import_pipeline(request: Request, name: str | None = None) -> Any:
    """Create a pipeline from a bundle zip (the body is the zip's bytes, as an upload's is). The name is `name`, else
    the bundle's own, made unique with a -2, -3 suffix when taken; a `name` that is taken is a 409. Any bad entry
    (path trickery, a sha256 that does not match the manifest, damage) refuses the whole bundle with a 422 listing
    each one. An input exported from an absolute path is rewritten to its imported `inputs/<basename>`; any other
    absolute or `..` input path is refused, so a hostile config cannot point a run at a file outside the pipeline."""
    store = _store(request)
    if name is not None:
        _check_name(name)
    if name is not None and name in store.list():
        return JSONResponse(status_code=409, content={"reason": f"pipeline {name} already exists"})
    with tempfile.TemporaryDirectory(prefix="smeltery-import-") as tmp:
        zpath = Path(tmp) / "bundle.zip"
        size = 0
        with zpath.open("wb") as w:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BUNDLE:
                    raise HTTPException(status_code=413, detail=f"bundle larger than {MAX_BUNDLE // (1024 * 1024)} MB")
                await run_in_threadpool(w.write, chunk)
        return await run_in_threadpool(_import_bundle, store, zpath, Path(tmp) / "staged", name)


def _import_bundle(store: Any, zpath: Path, staging: Path, name: str | None) -> Any:
    """The blocking half of an import: unpack, verify, rewrite, create."""
    staging.mkdir()
    try:
        u = bundle_import.unpack(zpath, staging)
    except bundle_import.BundleRefused as e:
        raise HTTPException(status_code=413 if e.too_large else 400, detail=str(e)) from e
    errors = list(u.errors)
    text = u.config
    notes: list[str] = []
    if not errors:
        try:
            editing.parse(text)
        except ConfigTextError as e:
            errors.append(("config.toml", str(e)))
    if not errors:
        text, notes, errors = bundle_import.rewrite_inputs(text, u.originals)
    if not errors:
        try:
            editing.parse(text)
        except ConfigTextError as e:
            errors.append(("config.toml", f"after rewriting input paths: {e}"))
    if errors:
        bad = {p for p, _ in errors}
        files = [ImportedFile(path=p, ok=False, message=m) for p, m in errors]
        files += [ImportedFile(path=r, ok=True) for r in u.files if r not in bad]
        n = len(errors)
        refused = ImportRefused(
            reason=f"{n} problem{'s' if n != 1 else ''} in the bundle; nothing was imported", files=files
        )
        return JSONResponse(status_code=422, content=refused.model_dump(mode="json", exclude={"errors"}))
    notes += [f"not found when the bundle was exported: {m}" for m in u.missing]
    target = name
    if target is None:
        base = re.sub(r"[^a-z0-9_-]+", "-", (u.pipeline or u.top).lower()).strip("-_")[:64]
        if not NAME.fullmatch(base):
            raise HTTPException(status_code=400, detail="the bundle's name is not usable: give one with ?name=")
        target = _unique_name(store.list(), base)
    try:
        made = store.create(target, text, u.files)
    except FileExistsError:
        return JSONResponse(status_code=409, content={"reason": f"pipeline {target} already exists"})
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse(
        status_code=201,
        content=ImportResult(
            pipeline=_doc(made), files=[ImportedFile(path=r, ok=True) for r in u.files], notes=notes
        ).model_dump(mode="json"),
    )


def _live_tasks(request: Request) -> list[Any]:
    """Every queued or running task, newest first, however long the finished history is."""
    q = ports(request).task_queue
    return sorted(
        [*q.list(status="queued", limit=None), *q.list(status="running", limit=None)],
        key=lambda t: t.created_at or "",
        reverse=True,
    )


def _preview_task(request: Request, name: str) -> str | None:
    """The id of this pipeline's preview that is queued or running, newest first."""
    live = [t for t in _live_tasks(request) if t.name == f"preview {name}"]
    return live[0].id if live else None


@router.post("/pipelines/impact", response_model=Impact)
def pipeline_impact(request: Request, body: ImpactRequest) -> Any:
    """What the edit costs, without running docking or an SCF: estimates from RDKit-placed poses, from the source
    run's poses when nothing upstream of them changed, or "cannot say"."""
    for text in (body.toml, body.baseline_toml):
        try:
            editing.parse(text)
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


def _file_knobs(method_key: str) -> tuple[str, ...]:
    from smeltery.methods import registry

    spec = registry().get(method_key)
    return tuple(spec.file_knobs) if spec is not None else ()


def _issue(x: Any) -> M.Issue:
    return M.Issue(level=x.level, stage=x.stage, message=x.message)


@router.post("/pipelines/steps", response_model=StepsOut, responses={400: {"model": M.Problem}})
def pipeline_steps(request: Request, body: StepsRequest) -> StepsOut:
    """The draft drawn as its six steps, each with its knobs, its state against the kept text, and its issues."""
    p = parse_or_400(body.toml)
    base = parse_or_400(body.baseline_toml) if body.baseline_toml is not None else None
    diff = [DiffLine(sign=s, line=ln) for s, ln in diff_lines(body.baseline_toml or body.toml, body.toml)]
    if p is None:
        return StepsOut(steps=[], ligands=None, pipeline_issues=_issues(body.toml), diff=diff)
    views = step_model.steps(p, base, workspace_defaults(request, label="methods.toml"))
    out = [
        StepOut(
            key=v.key,
            label=v.label,
            summary=v.summary,
            state=v.state,
            inputs=[StepLink(step=d["from"], text=d["text"]) for d in v.inputs],
            gives=[StepLink(step=d["to"], text=d["text"]) for d in v.gives],
            methods=v.methods,
            placed=[m for m in v.methods if m in p.keys()],
            chosen=v.chosen,
            params=[
                StepParam(
                    **x,
                    choices=list(method(x["method"]).param(x["name"]).choices),
                    names_file=x["name"] in _file_knobs(x["method"]),
                )
                for x in v.params
            ],
            issues=[_issue(x) for x in v.issues],
        )
        for v in views
    ]
    ligands = Ligands(
        campaign=str(p.campaign), parent=str(p.parent), candidates={k: str(v) for k, v in p.candidates.items()}
    )
    return StepsOut(
        steps=out, ligands=ligands, pipeline_issues=[_issue(x) for x in step_model.pipeline_issues(p, views)], diff=diff
    )


@router.post("/pipelines/edit", response_model=EditedText, responses={400: {"model": M.Problem}})
def pipeline_edit(request: Request, body: StepEdit) -> EditedText:
    """One step edit applied to the user's text: only the lines the edit changes are written, comments and layout
    stay (`config_text.apply`). Workspace defaults are copied into a method when it is added."""
    site = editing.Site(**body.site.model_dump()) if body.site is not None else None
    return apply_edit(
        lambda: editing.apply_step_edit(
            body.toml,
            body.op,
            body.key,
            params=body.params,
            site=site,
            baseline_toml=body.baseline_toml,
            defaults=lambda: workspace_defaults(request, label="methods.toml"),
        )
    )


@router.post(
    "/pipelines/{name}/launch",
    response_model=M.Task,
    status_code=201,
    responses={409: {"model": M.Problem}},
)
def pipeline_launch(request: Request, name: str, body: PipelineLaunch) -> Any:
    """Run the KEPT config: inputs staged into OUT by hash, relative paths resolved against the pipeline's folder.
    Refused, with the number of errors and each one, while `Pipeline.check()` reports an error."""
    f = _get(request, name)
    issues = _issues(f.text)
    errors = [i.message for i in issues if i.level == "error"]
    p = editing.parse(f.text) if not errors else None
    if errors or p is None:
        n = len(errors)
        return JSONResponse(
            status_code=409,
            content={"reason": f"{n} error{'s' if n != 1 else ''} block Run: " + "; ".join(errors)},
        )
    st = ports(request)
    root = Path(st.settings.root)
    default = root / "out" / f"{p.campaign}-{st.clock.now().strftime('%Y%m%d-%H%M%S')}"
    out = confined_out(root, body.out, default)
    if isinstance(out, JSONResponse):
        return out
    try:
        tid = LaunchRun(st.task_queue, st.run_store, st.artifact_store, st.identity, st.clock, root=root).run(
            p, out_dir=out, config_dir=f.folder, text=f.text, preview_dir=f.folder / "preview", timeout_s=body.timeout_s
        )
    except LiveRunRefused as e:  # OUT holds a live Temporal run: nothing was written
        raise HTTPException(status_code=409, detail=str(e)) from None
    return submitted_task(request, tid)


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
