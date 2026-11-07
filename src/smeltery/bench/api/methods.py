"""The Methods tab: every wired method with each setting explained, and the workspace default for each setting.

A workspace default is only ever COPIED into a step when the method is added (`Pipeline.add(..., defaults=...)`);
changing it here reaches no pipeline that already has the step.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from smeltery.gui.catalog import Method, catalog, cost_model, floor_status, measured_floor, memory_model
from smeltery.gui.config_text import ConfigTextError
from smeltery.gui.pipeline import Pipeline

from . import models as M
from .common import ports

router = APIRouter(prefix="/api", tags=["methods"])


def _defaults(request: Request) -> Any:
    return ports(request).method_defaults


def workspace_defaults(request: Request) -> dict[str, dict[str, Any]]:
    """The workspace's `{method_key: {param: value}}`. A methods.toml that was hand-broken is a 409 naming the file
    and line, not a 500."""
    try:
        return _defaults(request).all()
    except ConfigTextError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


def _used_by(request: Request) -> dict[str, list[str]]:
    """{method key: pipelines placing it}. A pipeline that does not read as a pipeline is skipped, not fatal."""
    store = ports(request).pipeline_store
    used: dict[str, list[str]] = {}
    for name in store.list():
        try:
            keys = Pipeline.from_config(tomllib.loads(store.get(name).text)).keys()
        except (KeyError, ValueError, TypeError, tomllib.TOMLDecodeError):
            continue
        for k in keys:
            used.setdefault(k, []).append(name)
    return used


# src/smeltery/bench/api/methods.py -> parents[4] is the repo root (a source checkout; a wheel has no docs/)
_REPO = Path(__file__).resolve().parents[4]


def _tier_doc(m: Method) -> str | None:
    """The tier's page under docs/tiers, relative to the repo, when that file exists (a wheel has no docs)."""
    rel = f"docs/tiers/{m.tier}.md"
    return rel if m.tier and (_REPO / rel).is_file() else None


def _view(m: Method, defaults: dict[str, dict], used: dict[str, list[str]]) -> M.MethodInfo:
    mine = defaults.get(m.key, {})
    return M.MethodInfo(
        key=m.key,
        label=m.label,
        summary=m.summary,
        quantity=m.quantity,
        params=[
            M.MethodParam(
                name=p.name,
                kind=p.kind,
                default=p.default,
                required=p.required,
                optional=p.optional,
                choices=list(p.choices),
                help=p.help,
                workspace_default=mine.get(p.name),
            )
            for p in m.params
        ],
        cost_model=cost_model(m.key),
        memory_model=memory_model(m.key),
        floor=measured_floor(m),
        floor_status=floor_status(m),
        tier_doc=_tier_doc(m),
        used_by=sorted(used.get(m.key, [])),
    )


def _wired(key: str) -> Method:
    m = catalog().get(key)
    if m is None:
        raise HTTPException(status_code=404, detail=f"no method {key}")
    if not m.wired:
        raise HTTPException(status_code=400, detail=f"{m.label} cannot run in a pipeline yet: {m.why_unwired}")
    return m


@router.get("/methods", response_model=list[M.MethodInfo])
def list_methods(request: Request) -> list[M.MethodInfo]:
    defaults, used = workspace_defaults(request), _used_by(request)
    return [_view(m, defaults, used) for m in catalog().values() if m.wired]


@router.put("/methods/{key}", response_model=M.MethodInfo)
def put_method_defaults(request: Request, key: str, body: M.MethodDefaultsPut) -> M.MethodInfo:
    m = _wired(key)
    clean: dict[str, Any] = {}
    for name, value in body.defaults.items():
        try:
            p = m.param(name)
        except KeyError as e:
            raise HTTPException(status_code=400, detail=f"{m.label} has no setting {name}") from e
        if value is None:
            raise HTTPException(status_code=400, detail=f"{name}: a default needs a value; send {{}} to clear them")
        if p.kind == "float" and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)  # TOML keeps 3 and 3.0 apart; the setting is a float
        why = p.check(value)
        if why:
            raise HTTPException(status_code=400, detail=why)
        clean[name] = value
    store = _defaults(request)
    try:
        store.put(key, clean)
    except ConfigTextError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _view(m, workspace_defaults(request), _used_by(request))
