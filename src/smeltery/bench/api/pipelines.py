"""Workspace pipelines: folders under ROOT/pipelines/<name>/ holding `config.toml` and the inputs it reads.

The TOML text is the pipeline. A save stores the client's text exactly as sent (comments and layout survive) after
checking that it parses; `Pipeline.check()` issues come back with it, and a draft with errors is still saved, since
only Run is blocked by them.
"""

from __future__ import annotations

import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from smeltery.bench.ports.pipeline_store import PipelineFile, StaleWrite
from smeltery.bench.provisional import place_site
from smeltery.gui import steps as step_model
from smeltery.gui._toml import dumps
from smeltery.gui.catalog import method
from smeltery.gui.config_text import ConfigTextError, apply, diff_lines
from smeltery.gui.pipeline import Pipeline

from ..use_cases.impact import SourceRun, as_dict, impact
from ..use_cases.launch_run import LaunchRun, LiveRunRefused, smeltery_command
from . import models as M
from . import prebuilt as prebuilt_api
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
    names_file: bool = Field(False, description="the value names an input file (the method's file_knobs): offer a picker")


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


class EditedText(BaseModel):
    toml: str


class PipelineLaunch(BaseModel):
    out: str | None = None


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


#: An uploaded input larger than this is refused before it is written (a whole-cell cryo-EM map is not an input).
MAX_UPLOAD = 64 * 1024 * 1024


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
    data = await request.body()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail=f"{filename}: larger than {MAX_UPLOAD // (1024 * 1024)} MB")
    try:
        return AddedFile(path=_store(request).add_file(name, filename, data))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/pipelines/{name}/files", response_model=list[str])
def list_pipeline_files(request: Request, name: str) -> list[str]:
    """The input files already in the pipeline folder, for the picker to offer."""
    _get(request, name)
    return _store(request).files(name)


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


def _with_defaults(p: Pipeline, request: Request) -> Pipeline:
    """A blank pipeline ADDS its methods, so each gets the workspace defaults copied in, exactly as
    `Pipeline.add(..., defaults=)` does. (`from_run` copies an existing config and gets none.)"""
    defaults = _workspace_defaults(request)
    for st in p.stages:
        known = {x.name for x in st.info.params}
        copied = {k: v for k, v in (defaults.get(st.method) or {}).items() if k in known}
        st.params = {**copied, **st.params}
    return p


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
    notes: list[str] = []
    files: dict[str, Path] = {}
    staging: tempfile.TemporaryDirectory[str] | None = None
    if body.from_prebuilt is not None:
        if prebuilt_api.folder(body.from_prebuilt) is None:
            raise HTTPException(status_code=400, detail=f"unknown prebuilt pipeline {body.from_prebuilt!r}")
        # R15: the config is copied EXACTLY, no workspace defaults. The example run goes in as run/, so the run store
        # (which scans the Bench root) lists it and the UI can open the workspace with ?run=<its index>.
        text = prebuilt_api.config_text(body.from_prebuilt)
        staging = tempfile.TemporaryDirectory(prefix="smeltery-prebuilt-")
        files = prebuilt_api.materialise(prebuilt_api.copy_plan(body.from_prebuilt), Path(staging.name))
    elif body.blank is not None:
        b = body.blank
        try:
            text = dumps(
                _with_defaults(Pipeline.minimal(b.campaign, b.parent, dict(b.candidates)), request).to_config()
            )
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
    finally:
        if staging is not None:
            staging.cleanup()
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


def _file_knobs(method_key: str) -> tuple[str, ...]:
    from smeltery.methods import registry

    spec = registry().get(method_key)
    return tuple(spec.file_knobs) if spec is not None else ()


def _issue(x: Any) -> M.Issue:
    return M.Issue(level=x.level, stage=x.stage, message=x.message)


def _parse_or_400(text: str) -> Pipeline | None:
    try:
        return _validate(text)
    except ConfigTextError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _workspace_defaults(request: Request) -> dict[str, dict[str, Any]]:
    """The Methods tab's `{method_key: {param: value}}`; a hand-broken methods.toml is a 409 naming its line."""
    try:
        return ports(request).method_defaults.all()
    except ConfigTextError as e:
        raise HTTPException(status_code=409, detail=f"methods.toml: {e}") from e


@router.post("/pipelines/steps", response_model=StepsOut, responses={400: {"model": M.Problem}})
def pipeline_steps(request: Request, body: StepsRequest) -> StepsOut:
    """The draft drawn as its six steps, each with its knobs, its state against the kept text, and its issues."""
    p = _parse_or_400(body.toml)
    base = _parse_or_400(body.baseline_toml) if body.baseline_toml is not None else None
    diff = [DiffLine(sign=s, line=ln) for s, ln in diff_lines(body.baseline_toml or body.toml, body.toml)]
    if p is None:
        return StepsOut(steps=[], ligands=None, pipeline_issues=_issues(body.toml), diff=diff)
    views = step_model.steps(p, base, _workspace_defaults(request))
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


def _patch(target: dict, before: dict, after: dict) -> None:
    """Carry what changed from `before` to `after` (two `Pipeline.to_config()`s) into `target` (the user's config as
    parsed), so the edit touches only the keys it changed and nothing the Pipeline would normalise."""
    for k in [k for k in before if k not in after]:
        target.pop(k, None)
    for k, v in after.items():
        old = before.get(k)
        if isinstance(v, dict) and isinstance(old, dict) and isinstance(target.get(k), dict):
            _patch(target[k], old, v)
        elif k not in before or old != v or type(old) is not type(v):
            target[k] = v


def _revert(p: Pipeline, step: str, kept: Pipeline) -> None:
    """Put step `step` of `p` back as it is in `kept`: the methods it places and their knobs (or, for Ligands, the
    campaign, parent, candidates and decisions). Every other step keeps its draft edits."""
    offered = {k: methods for k, _, methods in step_model.step_table()}
    if step not in offered:
        raise ValueError(f"no step {step!r}; steps: {list(offered)}")
    methods = offered[step]
    if not methods:
        p.campaign, p.parent, p.candidates, p.decisions = (
            kept.campaign,
            kept.parent,
            dict(kept.candidates),
            kept.decisions,
        )
        return
    for m in methods:
        if m in p.keys() and m not in kept.keys():
            p.remove(m)
    for m in methods:
        if m in kept.keys():
            if m not in p.keys():
                p.add(m)
            p.stage(m).params = dict(kept.stage(m).params)


def _edit(p: Pipeline, body: StepEdit, defaults: dict[str, dict[str, Any]]) -> None:
    if body.op == "revert":
        kept = _parse_or_400(body.baseline_toml) if body.baseline_toml is not None else None
        if kept is None:
            raise ValueError("a revert needs baseline_toml, the kept pipeline")
        _revert(p, body.key, kept)
        return
    if body.op == "add":
        p.add(body.key, body.params or None, defaults=defaults)
    elif body.op == "remove":
        p.remove(body.key)
    elif body.op == "set":
        p.set(body.key, **(body.params or {}))
    elif body.op == "choose":  # the other method of a step: drop the ones it excludes, then place it
        for other in method(body.key).exclusive:
            if other in p.keys():
                p.remove(other)
        if body.key not in p.keys():
            p.add(body.key, body.params or None, defaults=defaults)
    else:
        if body.site is None:
            raise ValueError("a site edit needs the site: atom, from_atom, q, distance")
        s = body.site
        place_site(p, s.atom, s.from_atom, q=s.q, distance=s.distance)


@router.post("/pipelines/edit", response_model=EditedText, responses={400: {"model": M.Problem}})
def pipeline_edit(request: Request, body: StepEdit) -> EditedText:
    """One step edit applied to the user's text: only the lines the edit changes are written, comments and layout
    stay (`config_text.apply`). Workspace defaults are copied into a method when it is added."""
    p = _parse_or_400(body.toml)
    if p is None:
        raise HTTPException(status_code=400, detail="this TOML is not a pipeline config; fix it in the TOML pane")
    defaults = _workspace_defaults(request) if body.op in ("add", "choose") else {}
    before = p.to_config()
    try:
        _edit(p, body, defaults)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e).strip("'\"")) from e
    after = p.to_config()
    if body.op == "remove" and body.key in Pipeline.from_config(after).keys():
        others = ", ".join(repr(o) for o in method(body.key).exclusive) or "nothing"
        raise HTTPException(
            status_code=400, detail=f"every pipeline needs {body.key!r}; choose its alternative instead: {others}"
        )
    target = tomllib.loads(body.toml)
    _patch(target, before, after)
    try:
        return EditedText(toml=apply(body.toml, target))
    except ConfigTextError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


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
    p = _validate(f.text) if not errors else None
    if errors or p is None:
        n = len(errors)
        return JSONResponse(
            status_code=409,
            content={"reason": f"{n} error{'s' if n != 1 else ''} block Run: " + "; ".join(errors)},
        )
    st = ports(request)
    root = Path(st.settings.root)
    out = Path(body.out) if body.out else root / "out" / f"{p.campaign}-{st.clock.now().strftime('%Y%m%d-%H%M%S')}"
    if not out.is_absolute():
        out = root / out
    try:
        tid = LaunchRun(st.task_queue, st.run_store, st.artifact_store, st.identity, st.clock, root=root).run(
            p, out_dir=out, config_dir=f.folder, text=f.text
        )
    except LiveRunRefused as e:  # OUT holds a live Temporal run: nothing was written
        raise HTTPException(status_code=409, detail=str(e)) from None
    t = st.task_queue.get(tid)
    if t is None:
        raise HTTPException(status_code=500, detail=f"task {tid} was submitted but cannot be read back")
    return M.Task(**(t.to_dict() if hasattr(t, "to_dict") else t))


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
