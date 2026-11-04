"""Lineage: for one candidate's paired ΔΔE, the poses behind it, the steps that ran and what the run recorded about
itself. A join of the run record's own fields; nothing here computes a number."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import models as M
from .common import run

router = APIRouter(prefix="/api", tags=["lineage"])

QUANTITY = "dE_int"


def _steps(res: dict, tiers: list[dict]) -> list[M.LineageStep]:
    by_name = {t.get("name"): t for t in tiers if isinstance(t, dict)}
    steps = [
        M.LineageStep(
            stage=s["stage"], kind=s["kind"], seconds=s.get("seconds"), settings=dict(by_name.get(s["stage"], {}))
        )
        for s in res.get("stages") or []
    ]
    for f in res.get("funnel") or []:
        if f.get("kind") == "measure":
            steps.append(
                M.LineageStep(
                    stage=f["stage"],
                    kind="measure",
                    seconds=f.get("seconds"),
                    settings=dict(by_name.get(f["stage"], {})),
                )
            )
    return steps


@router.get("/runs/{idx}/lineage", response_model=M.Lineage)
def run_lineage(request: Request, idx: int, candidate: str) -> M.Lineage:
    r = run(request, idx)
    rec: dict[str, Any] = r.record
    res = rec["results"]
    if candidate not in r.candidates:
        raise HTTPException(status_code=404, detail=f"no candidate {candidate!r} in this run")
    if candidate == r.parent:
        raise HTTPException(status_code=409, detail=f"{candidate!r} is the parent; it has no paired ΔΔE")
    dde = (res.get("paired_ddE") or {}).get(candidate)
    pp = res.get("per_pose") or {}
    if dde is None or QUANTITY not in pp.get(candidate, {}) or QUANTITY not in pp.get(r.parent, {}):
        raise HTTPException(status_code=409, detail=f"{candidate!r}: no recorded {QUANTITY} poses or paired ΔΔE")
    vals, pvals = pp[candidate][QUANTITY], pp[r.parent][QUANTITY]
    poses = [
        M.LineagePose(pose=i, value=v, parent_value=p, difference=v - p)
        for i, (v, p) in enumerate(zip(vals, pvals, strict=True))
    ]
    tiers = rec.get("tiers") or []
    steps = _steps(res, tiers)
    measure = next((s for s in steps if s.kind == "measure"), None)
    provenance = (measure.settings.get("field") if measure else None) or None
    smeltery = rec.get("smeltery") or None
    config, calcs = res.get("config") or None, res.get("calcs") or None
    missing = [
        name
        for name, absent in (
            ("smeltery", smeltery is None),
            ("config", config is None),
            ("calcs", calcs is None),
            ("field.provenance", provenance is None),
        )
        if absent
    ]
    return M.Lineage(
        candidate=candidate,
        quantity=QUANTITY,
        parent=r.parent,
        mean=dde["mean"],
        sem=dde["sem"],
        n=dde["n"],
        poses=poses,
        steps=steps,
        field=M.LineageField(n_charges=len(res.get("field") or []), provenance=provenance),
        engine=dict(rec.get("ferric") or {}),
        smeltery=smeltery,
        digest=rec["input_digest"],
        started_utc=rec.get("started_utc"),
        config=M.LineageConfig(**config) if config else None,
        calcs=M.LineageCalcs(**calcs) if calcs else None,
        missing=missing,
    )
