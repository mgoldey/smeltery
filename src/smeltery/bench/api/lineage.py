"""Lineage: for one candidate's paired ΔΔE, the poses behind it, the steps that ran and what the run recorded about
itself. A join of the run record's own fields; nothing here computes a number."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from smeltery.gui import results as R

from . import models as M
from .common import run

router = APIRouter(prefix="/api", tags=["lineage"])

QUANTITY = "dE_int"


def stage_tier() -> dict[str, str]:
    """{stage name `smeltery run` logs: the tier name it records}, from the registry's `MethodSpec.tier`. A method
    logs under its key, its section (`poses` for paired_poses) or its gate key (`xtb` for gfn2), so each of those
    names maps to its tier. A stage not here records no tier, or is looked up under its own name."""
    from smeltery.methods import registry

    out: dict[str, str] = {}
    for s in registry().values():
        if s.tier is None:
            continue
        for name in {s.key, s.section, s.gate_key} - {None}:
            assert name is not None
            if out.setdefault(name, s.tier) != s.tier:
                raise ValueError(f"stage name {name!r} names two tiers: {out[name]!r} and {s.tier!r}")
    return out


def no_tier() -> frozenset[str]:
    """Stages that are no tier by design: their numbers have no settings to show, so an empty dict is the whole
    truth. Every wired method registered without a tier (`smeltery.methods.registry()`); each logs under its key."""
    from smeltery.methods import registry

    return frozenset(k for k, s in registry().items() if s.wired and s.tier is None)


def _steps(res: dict, tiers: list[dict]) -> tuple[list[M.LineageStep], list[str]]:
    """Steps joined to `rec.tiers` through `stage_tier()`. Settings are None when the stage should have a tier but the
    record has none; that stage is named in the returned `missing` list."""
    by_name = {t.get("name"): t for t in tiers if isinstance(t, dict)}
    tierless = no_tier()
    names = stage_tier()
    steps: list[M.LineageStep] = []
    missing: list[str] = []

    def add(stage: str, kind: str, seconds: float | None) -> None:
        tier = by_name.get(names.get(stage, stage))
        if tier is not None:
            settings: dict[str, Any] | None = dict(tier)
        elif stage in tierless:
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
    try:  # paired by original pose index: after a pairwise drop the parent has poses this analogue does not
        paired = R.paired_poses(r, candidate, QUANTITY)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    poses = [
        M.LineagePose(pose=t["pose"], value=t["value"], parent_value=t["parent_value"], difference=t["paired"])
        for t in paired
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
