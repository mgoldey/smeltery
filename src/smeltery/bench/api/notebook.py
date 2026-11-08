"""Campaigns, their append-only notebook, and studies.

A notebook entry is never edited: there is no PUT or PATCH here, and a correction is a new entry that supersedes the
old one, which stays readable. Studies are named by Python module path in settings and imported; there is no study
file format to scan for (`Study` has no serialisation).
"""

from __future__ import annotations

import importlib
import mimetypes
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from smeltery.project import NotebookEntry
from smeltery.study.cache import Cache
from smeltery.study.step import Study

from . import models as M
from .common import ports, row, utc_iso
from .pipelines import MAX_UPLOAD

router = APIRouter(prefix="/api", tags=["notebook"])


def _campaign(request: Request, c) -> M.CampaignOut:
    es = list(ports(request).project_store.entries(c.id))
    created = c.created_at or ""
    return M.CampaignOut(
        id=c.id,
        name=c.name,
        description=getattr(c, "description", "") or "",
        created_at=created,
        n_entries=len(es),
        n_runs=len({a for e in es for a in e.artefacts}),
        updated_at=max((e.created_at for e in es), default=created),
    )


@router.get("/campaigns", response_model=list[M.CampaignOut])
def list_campaigns(request: Request) -> list[M.CampaignOut]:
    return [_campaign(request, c) for c in ports(request).project_store.list_campaigns()]


@router.post("/campaigns", response_model=M.CampaignOut, status_code=201)
def create_campaign(request: Request, body: M.CampaignIn) -> M.CampaignOut:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="a campaign needs a name")
    try:
        return _campaign(
            request, ports(request).project_store.create_campaign(body.name.strip(), description=body.description)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def _get_campaign(request: Request, campaign_id: str):
    try:
        c = ports(request).project_store.get_campaign(campaign_id)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=404, detail=f"no campaign {campaign_id}") from e
    if c is None:
        raise HTTPException(status_code=404, detail=f"no campaign {campaign_id}")
    return c


@router.get("/campaigns/{campaign_id}", response_model=M.CampaignOut)
def get_campaign(request: Request, campaign_id: str) -> M.CampaignOut:
    return _campaign(request, _get_campaign(request, campaign_id))


def _entries(request: Request, campaign_id: str) -> list[M.EntryOut]:
    es: list[NotebookEntry] = list(ports(request).project_store.entries(campaign_id))
    superseded_by = {e.supersedes: e.id for e in es if e.supersedes}
    return [
        M.EntryOut(
            id=e.id,
            campaign_id=e.campaign_id,
            title=e.title,
            body=e.body,
            author=e.author,
            created_at=e.created_at,
            artefacts=list(e.artefacts),
            files=[M.FileRef.model_validate(f) for f in e.files],
            supersedes=e.supersedes,
            superseded_by=superseded_by.get(e.id),
        )
        for e in es
    ]


@router.get("/campaigns/{campaign_id}/entries", response_model=list[M.EntryOut])
def list_entries(request: Request, campaign_id: str) -> list[M.EntryOut]:
    _get_campaign(request, campaign_id)
    return _entries(request, campaign_id)


@router.get("/campaigns/{campaign_id}/entries/{entry_id}", response_model=M.EntryOut)
def get_entry(request: Request, campaign_id: str, entry_id: str) -> M.EntryOut:
    for e in _entries(request, campaign_id):
        if e.id == entry_id:
            return e
    raise HTTPException(status_code=404, detail=f"no entry {entry_id}")


@router.post("/campaigns/{campaign_id}/entries", response_model=M.EntryOut, status_code=201)
def add_entry(request: Request, campaign_id: str, body: M.EntryIn) -> M.EntryOut:
    """Attached runs are recorded by the sha256 of their run record, through `ArtifactStore`, never by path."""
    _get_campaign(request, campaign_id)
    st = ports(request)
    if body.supersedes is not None and body.supersedes not in {e.id for e in _entries(request, campaign_id)}:
        raise HTTPException(status_code=400, detail=f"cannot supersede unknown entry {body.supersedes}")
    artefacts = [st.artifact_store.put(Path(row(request, i)["path"])) for i in body.runs]
    files = []
    for f in body.files:
        try:
            stored = st.artifact_store.exists(f.digest)
        except ValueError:
            stored = False
        if not stored:
            raise HTTPException(status_code=400, detail=f"{f.filename}: not uploaded (no stored file {f.digest})")
        name = _safe_name(f.filename)
        files.append({"digest": f.digest, "filename": name, "media_type": _media_type(name), "kind": "file"})
    try:
        e = st.project_store.add_entry(
            campaign_id,
            title=body.title,
            body=body.body,
            author=st.identity.current(),
            artefacts=artefacts,
            files=files,
            supersedes=body.supersedes,
            created_at=utc_iso(st.clock.now()),
        )
    except ValueError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    return next(x for x in _entries(request, campaign_id) if x.id == e.id)


# ---------------------------------------------------------------------------------------------------- files

#: Shown inline by a browser. SVG is deliberately absent: it can carry script, so it is only ever a download.
_INLINE_IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
#: Served under their own type, as a download. Anything else is application/octet-stream.
_DOWNLOAD_TYPES = {"application/pdf", "text/csv", "text/plain", "application/json", "image/svg+xml"}


def _media_type(filename: str) -> str:
    guessed = mimetypes.guess_type(filename)[0] or ""
    return guessed if guessed in _INLINE_IMAGES | _DOWNLOAD_TYPES else "application/octet-stream"


def _safe_name(filename: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f\"\\]", "", filename.replace("\\", "/").rsplit("/", 1)[-1]).strip()
    return name[:200] or "file"


def _disposition(kind: str, fname: str) -> str:
    """Header values are latin-1: a non-ASCII name goes in the RFC 5987 `filename*` form, with an ASCII fallback."""
    fallback = fname.encode("ascii", "replace").decode().replace("?", "_")
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(fname, safe='')}"


@router.post(
    "/notebook/artifacts",
    response_model=M.FileRef,
    status_code=201,
    responses={413: {"model": M.Problem}, 415: {"model": M.Problem}},
)
async def upload_notebook_file(request: Request, filename: str) -> M.FileRef:
    """A figure, PDF or CSV for a notebook entry. The body is the file's bytes; it is stored by sha256, and the response
    is what to put in the entry's `files`. The media type comes from the filename, never from the client's header."""
    name = _safe_name(filename)
    # text/plain and friends are CORS "simple" requests (no preflight), so any website could write here
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/octet-stream":
        raise HTTPException(status_code=415, detail="upload the file's bytes as Content-Type: application/octet-stream")
    too_big = f"{name}: larger than {MAX_UPLOAD // (1024 * 1024)} MB"
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail=too_big)
    data = await request.body()
    if len(data) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail=too_big)
    if not data:
        raise HTTPException(status_code=400, detail=f"{name}: the file is empty")
    return M.FileRef(digest=ports(request).artifact_store.put(data), filename=name, media_type=_media_type(name))


@router.get("/notebook/artifacts/{digest}", responses={404: {"model": M.Problem}, 422: {"model": M.Problem}})
def get_notebook_file(request: Request, digest: str, name: str = "") -> Response:
    """The stored bytes, re-hashed on the way out. Only png/jpeg/gif/webp are inline; everything else is an attachment
    (SVG and HTML included), with nosniff and a sandboxing CSP so a hostile upload cannot run in this origin."""
    store = ports(request).artifact_store
    try:
        data = store.get(digest)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=f"no stored file {digest}") from e
    except ValueError as e:
        # a malformed digest, or content that no longer matches its hash
        raise HTTPException(status_code=422, detail=f"stored file {digest} cannot be served: {e}") from e
    fname = _safe_name(name) if name else digest
    media = _media_type(fname)
    disposition = "inline" if media in _INLINE_IMAGES else "attachment"
    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Disposition": _disposition(disposition, fname),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


# ---------------------------------------------------------------------------------------------------- studies


def _study(request: Request, module_path: str) -> Study:
    if module_path not in ports(request).settings.studies:
        raise HTTPException(status_code=404, detail=f"{module_path} is not a configured study")
    try:
        mod = importlib.import_module(module_path)
    except Exception as e:  # noqa: BLE001 - any import failure is the user's study, reported verbatim
        raise HTTPException(status_code=404, detail=f"cannot import {module_path}: {e}") from e
    found = [v for v in vars(mod).values() if isinstance(v, Study)]
    if len(found) != 1:
        raise HTTPException(status_code=404, detail=f"{module_path} defines {len(found)} Study objects; expected one")
    return found[0]


@router.get("/studies", response_model=list[str])
def list_studies(request: Request) -> list[str]:
    return list(ports(request).settings.studies)


@router.get("/studies/{module_path}/dag")
def study_dag(request: Request, module_path: str) -> list[dict]:
    return _study(request, module_path).dag()


@router.get("/studies/{module_path}/cells", response_model=list[M.StudyCell])
def study_cells(request: Request, module_path: str) -> list[M.StudyCell]:
    """Per (condition, step), cached or to run, against the study cache in settings -- the cache a real run of the
    study writes to, so 'cached' means something."""
    study = _study(request, module_path)
    out = []
    for cell in study.plan_cells(Cache(ports(request).settings.cache_dir)):
        cond = dict(cell["condition"])
        label = ", ".join(f"{k}={v}" for k, v in cond.items())
        out.append(M.StudyCell(condition=cond, label=label, step=cell["step"], state=cell["state"], key=cell["key"]))
    return out
