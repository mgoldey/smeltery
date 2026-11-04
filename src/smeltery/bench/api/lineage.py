"""Lineage: for one candidate's paired ΔΔE, the poses behind it, the steps that ran and what the run recorded about
itself. A join of the run record's own fields; nothing here computes a number."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import models as M
from .common import run

router = APIRouter(prefix="/api", tags=["lineage"])

QUANTITY = "dE_int"


#: Stage names `smeltery run` logs (cli.py `_log`/`_stage`) whose tier is recorded under another name. A stage not here
#: that has a tier (`docking`, `forcefield`, the measurement tier) is recorded under its own name.
STAGE_TIER = {"poses": "paired_poses", "xtb": "gfn2"}
#: Stages that are no tier by design: their numbers have no settings to show, so an empty dict is the whole truth.
NO_TIER = frozenset({"pocket", "parity", "posebusters", "structure", "cut"})


def _steps(res: dict, tiers: list[dict]) -> tuple[list[M.LineageStep], list[str]]:
    """Steps joined to `rec.tiers` through STAGE_TIER. Settings are None when the stage should have a tier but the
    record has none; that stage is named in the returned `missing` list."""
    by_name = {t.get("name"): t for t in tiers if isinstance(t, dict)}
    steps: list[M.LineageStep] = []
    missing: list[str] = []

    def add(stage: str, kind: str, seconds: float | None) -> None:
        tier = by_name.get(STAGE_TIER.get(stage, stage))
        if tier is not None:
            settings: dict[str, Any] | None = dict(tier)
        elif stage in NO_TIER:
            settings = {}
        else:
            settings = None
            missing.append(f"steps.{stage}.settings")
        steps.append(M.LineageStep(stage=stage, kind=kind, seconds=seconds, settings=settings))

    for s in res.get("stages") or []:
        add(s["stage"], s["kind"], s.get("seconds"))
    for f in res.get("funnel") or []:
        if f.get("kind") == "measure":
            add(f["stage"], "measure", f.get("seconds"))
    return steps, missing


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
    steps, missing = _steps(res, tiers)
    measure = next((s for s in steps if s.kind == "measure"), None)
    provenance = ((measure.settings or {}).get("field") if measure else None) or None
    smeltery = rec.get("smeltery") or None
    config, calcs = res.get("config") or None, res.get("calcs") or None
    missing += [
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
