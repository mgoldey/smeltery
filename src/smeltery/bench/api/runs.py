"""Runs: the record, its verdict and tables, the figures Python already draws, poses, and the judgement layered on
top (decisions, comments, proposals). Every number comes from `smeltery.gui.results`; nothing here recomputes it."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from rdkit import Chem

from smeltery.bench.verdict import verdict_of
from smeltery.decisions import NOTE_REQUIRED, REASONS, STATUSES, DecisionSet
from smeltery.gui import results as R
from smeltery.gui.compare import Comparison as Cmp
from smeltery.gui.report import MOLSTAR_CSS, MOLSTAR_JS, RDKIT_JS, RDKIT_WASM, write_report
from smeltery.gui.scenes import pose_scene
from smeltery.gui.views.compare import compare_figure, comparison_caption
from smeltery.gui.views.forest import forest_figure
from smeltery.gui.views.matrix import matrix_figure
from smeltery.gui.views.per_pose import per_pose_figure
from smeltery.project import Proposal, make_entry_id

from . import models as M
from .common import ports, record, row, rows, run, utc_iso

router = APIRouter(prefix="/api", tags=["runs"])

QUANTITY = "dE_int"
#: The frozen report's only external references, by design (docs/gui-contract.md section 4): pinned and
#: integrity-hashed copies of the 2D and 3D libraries. Its numbers, plots and tables need no network.
REPORT_EXTERNAL = (RDKIT_JS, RDKIT_WASM, MOLSTAR_JS, MOLSTAR_CSS)


def _verdict(raw: dict) -> M.Verdict:
    v = verdict_of(raw)
    return M.Verdict(state=v.state, winner=v.winner, floor=v.floor, missing=list(v.missing))


def _unit(r: R.Run) -> str:
    return r.record.get("results", {}).get("units", {}).get(QUANTITY, "kcal/mol")


def _plain(fig: Any) -> dict[str, Any]:
    """A Plotly figure as plain JSON (`to_json` handles the numpy arrays `to_plotly_json` would leave in)."""
    return json.loads(fig.to_json())


@router.get("/runs", response_model=list[M.RunSummary])
def list_runs(request: Request) -> list[M.RunSummary]:
    out = []
    for i, r in enumerate(rows(request)):
        try:
            raw = json.loads(Path(r["path"]).read_text())
            verdict = _verdict(raw) if isinstance(raw, dict) else None
            digest = raw.get("input_digest") if isinstance(raw, dict) else None
        except (OSError, ValueError):
            verdict, digest = None, None
        cands = r.get("candidates")
        out.append(
            M.RunSummary(
                idx=i,
                path=r["path"],
                campaign=r.get("campaign"),
                parent=r.get("parent"),
                digest=digest,
                n_candidates=len(cands) if isinstance(cands, list) else None,
                verdict=verdict,
                error=None if r.get("incomplete") else r.get("error"),
            )
        )
    return out


@router.get("/runs/{idx}", response_model=M.RunDetail)
def run_detail(request: Request, idx: int) -> M.RunDetail:
    raw = record(request, idx)
    r = run(request, idx)
    poses = r.record["results"].get("poses", {}) if r.has_pose_output else {}
    return M.RunDetail(
        idx=idx,
        path=row(request, idx)["path"],
        out_dir=str(r.out_dir),
        campaign=r.campaign,
        digest=r.record["input_digest"],
        parent=r.parent,
        candidates=dict(r.candidates),
        analogues=list(r.analogues),
        groups=[list(g) for g in r.groups],
        survivors=list(r.survivors) if r.survivors is not None else None,
        floor=r.floor,
        floor_source=r.floor_source,
        z=r.z,
        keep=r.keep,
        unranked=r.unranked,
        has_pose_output=r.has_pose_output,
        n_poses={k: int(v["n_poses"]) for k, v in poses.items()},
        unit=_unit(r),
        verdict=_verdict(raw),
    )


@router.get("/runs/{idx}/verdict", response_model=M.Verdict)
def run_verdict(request: Request, idx: int) -> M.Verdict:
    return _verdict(record(request, idx))


@router.get("/runs/{idx}/ddE", response_model=M.DdeTable)
def run_dde(request: Request, idx: int) -> M.DdeTable:
    raw = record(request, idx)
    r = run(request, idx)
    return M.DdeTable(
        rows=[M.DdeRow(**x) for x in R.ddE_table(r)],
        floor=r.floor,
        floor_source=r.floor_source,
        z=r.z,
        keep=r.keep,
        unit=_unit(r),
        verdict=_verdict(raw),
    )


@router.get("/runs/{idx}/resolved", response_model=M.Resolved)
def run_resolved(request: Request, idx: int) -> M.Resolved:
    names, matrix = R.resolved_matrix(run(request, idx))
    return M.Resolved(names=names, matrix=matrix)


@router.get("/runs/{idx}/per_pose", response_model=list[M.PerPoseRow])
def run_per_pose(request: Request, idx: int, quantity: str = QUANTITY) -> list[M.PerPoseRow]:
    try:
        return [M.PerPoseRow(**x) for x in R.per_pose_table(run(request, idx), quantity)]
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/runs/{idx}/figures/{name}")
def run_figure(request: Request, idx: int, name: Literal["forest", "matrix", "per_pose"]) -> dict[str, Any]:
    """The figure `smeltery.gui.views` builds, as Plotly JSON. The browser draws it; it never re-derives it."""
    r = run(request, idx)
    table = R.ddE_table(r)
    if name == "forest":
        return _plain(forest_figure(table, r, unit=_unit(r)))
    if name == "matrix":
        names, matrix = R.resolved_matrix(r)
        return _plain(matrix_figure(names, matrix, table))
    try:
        pp = R.per_pose_table(r, QUANTITY)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _plain(per_pose_figure(pp, r, quantity=QUANTITY, unit=_unit(r), rows=table))


@router.get("/runs/{idx}/scene")
def run_scene(request: Request, idx: int, candidate: str, pose: int = 0) -> dict[str, Any]:
    r = run(request, idx)
    if not r.has_pose_output:
        raise HTTPException(status_code=409, detail="this run record predates pose output; it has no poses to show")
    n = int(r.record["results"]["poses"].get(candidate, {}).get("n_poses", 0))
    if candidate not in r.candidates:
        raise HTTPException(status_code=400, detail=f"no candidate {candidate!r}")
    if not 0 <= pose < n:
        raise HTTPException(status_code=400, detail=f"pose {pose} out of range: {candidate} has {n} poses")
    return pose_scene(r, candidate, pose)


@router.get("/runs/{idx}/pose_atoms", response_model=M.PoseAtoms)
def run_pose_atoms(request: Request, idx: int, candidate: str) -> M.PoseAtoms:
    """Atom symbols in the order of the candidate's pose SDF -- the order `[[pocket.site]]` indexes. Read from the
    SDF bytes, never derived from the SMILES, so a mismatch with any depiction is detectable."""
    r = run(request, idx)
    if not r.has_pose_output:
        raise HTTPException(status_code=409, detail="this run record predates pose output")
    try:
        sdf = R.pose_sdf(r, candidate)
    except (KeyError, ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    block = sdf.split("$$$$")[0]
    mol = Chem.MolFromMolBlock(block, sanitize=False, removeHs=False)
    if mol is None:
        raise HTTPException(status_code=409, detail=f"{candidate}: pose SDF could not be parsed")
    return M.PoseAtoms(
        candidate=candidate,
        symbols=[a.GetSymbol() for a in mol.GetAtoms()],
        source=r.record["results"]["poses"][candidate]["file"],
    )


# ---------------------------------------------------------------------------------------------------- decisions


def _decisions_path(r: R.Run) -> Path:
    return Path(r.out_dir) / f"decisions-{r.record['input_digest'][:12]}.json"


@router.get("/decisions/vocabulary", response_model=M.Vocabulary)
def decisions_vocabulary() -> M.Vocabulary:
    return M.Vocabulary(statuses=list(STATUSES), reasons=list(REASONS), note_required=sorted(NOTE_REQUIRED))


@router.get("/runs/{idx}/decisions", response_model=list[M.DecisionOut])
def list_decisions(request: Request, idx: int) -> list[M.DecisionOut]:
    p = _decisions_path(run(request, idx))
    if not p.is_file():
        return []
    return [M.DecisionOut(**d.__dict__) for d in DecisionSet.load(p).decisions]


@router.post("/runs/{idx}/decisions", response_model=M.DecisionOut)
def record_decision(request: Request, idx: int, body: M.DecisionIn) -> M.DecisionOut:
    """Every refusal is `DecisionSet`'s own; this route adds no rule. The user is `Identity.current()`."""
    r = run(request, idx)
    p = _decisions_path(r)
    ds = (
        DecisionSet.load(p)
        if p.is_file()
        else DecisionSet(campaign=r.campaign, run=r.record["input_digest"], candidates=dict(r.candidates))
    )
    ds.candidates = dict(r.candidates)
    if body.candidate not in r.candidates:
        raise HTTPException(status_code=400, detail=f"no candidate {body.candidate!r} in this run")
    try:
        d = ds.set(body.candidate, body.status, body.reason, body.note, user=ports(request).identity.current())
        ds.dump(p)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return M.DecisionOut(**d.__dict__)


# ---------------------------------------------------------------------------------------------------- comments

_ANCHOR = re.compile(r"^(run|candidate:[^:]+|pose:[^:]+:\d+)$")


@router.get("/runs/{idx}/comments", response_model=list[M.CommentOut])
def list_comments(request: Request, idx: int) -> list[M.CommentOut]:
    digest = record(request, idx).get("input_digest", "")
    return [
        M.CommentOut(
            id=c.id, anchor=c.anchor_text or "run", author=c.author, body=c.body, created_at=utc_iso(c.created_at)
        )
        for c in ports(request).comment_store.for_run(digest)
    ]


@router.post("/runs/{idx}/comments", response_model=M.CommentOut, status_code=201)
def add_comment(request: Request, idx: int, body: M.CommentIn) -> M.CommentOut:
    if not _ANCHOR.match(body.anchor):
        raise HTTPException(status_code=400, detail="anchor is 'run', 'candidate:<name>' or 'pose:<name>:<i>'")
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="a comment needs some text")
    digest = record(request, idx).get("input_digest", "")
    st = ports(request)
    c = st.comment_store.add(run=digest, anchor=body.anchor, author=st.identity.current(), body=body.body)
    return M.CommentOut(
        id=c.id, anchor=c.anchor_text or body.anchor, author=c.author, body=c.body, created_at=utc_iso(c.created_at)
    )


# ---------------------------------------------------------------------------------------------------- proposals


def _proposal_out(p: Proposal) -> M.ProposalOut:
    return M.ProposalOut(
        id=p.id,
        run=p.run,
        smiles=p.smiles,
        reason=p.reason,
        note=p.note,
        candidate=p.candidate,
        author=p.author,
        created_at=p.created_at,
        status=p.status,
    )


@router.get("/proposals/vocabulary", response_model=M.ProposalVocabulary)
def proposals_vocabulary() -> M.ProposalVocabulary:
    return M.ProposalVocabulary(reasons=list(Proposal.REASONS))


@router.get("/proposals", response_model=list[M.ProposalOut])
def list_proposals(request: Request, run: str | None = None) -> list[M.ProposalOut]:
    """The computational chemist's review queue. Optionally one run's (by input digest)."""
    return [_proposal_out(p) for p in ports(request).project_store.list_proposals(run)]


@router.post("/runs/{idx}/proposals", response_model=M.ProposalOut, status_code=201)
def propose(request: Request, idx: int, body: M.ProposalIn) -> M.ProposalOut:
    """Store a proposal for review. This route never touches the task queue: a comment must not start compute."""
    st = ports(request)
    digest = record(request, idx).get("input_digest", "")
    now = utc_iso(st.clock.now())
    p = Proposal(
        id=make_entry_id(digest, body.smiles, now),
        run=digest,
        smiles=body.smiles,
        reason=body.reason,
        note=body.note,
        candidate=body.candidate,
        author=st.identity.current(),
        created_at=now,
    )
    bad = p.problems()
    if bad:
        raise HTTPException(status_code=400, detail="; ".join(bad))
    st.project_store.add_proposal(p)
    return _proposal_out(p)


# ---------------------------------------------------------------------------------------------------- snapshot


@router.post("/runs/{idx}/snapshot", response_model=M.Snapshot)
def snapshot(request: Request, idx: int) -> M.Snapshot:
    """Write the frozen report beside the run, unmodified. Its numbers, plots and tables are inline; its only
    external references are the pinned, integrity-hashed 2D/3D libraries, and it says so when they cannot load."""
    r = run(request, idx)
    path = Path(r.out_dir) / f"report-{r.record['input_digest'][:12]}.html"
    write_report(r, path)
    data = path.read_bytes()
    found = sorted(set(re.findall(rb"https?://[^\s\"'<>)]+", data)))
    return M.Snapshot(
        path=str(path),
        sha256=hashlib.sha256(data).hexdigest(),
        external=[u.decode() for u in found if u.decode() in REPORT_EXTERNAL],
    )


# ---------------------------------------------------------------------------------------------------- compare


@router.get("/compare", response_model=M.Comparison)
def compare(request: Request, a: int, b: int) -> M.Comparison:
    """Two runs as arms, one comparison key each. An arm compared with itself is refused, not drawn."""
    if a == b:
        raise HTTPException(
            status_code=400, detail="a run is in both arms; an arm compared with itself is not a comparison"
        )
    ra, rb = run(request, a), run(request, b)
    cmp = Cmp.from_runs(
        {"run": ra}, {"run": rb}, label_a=ra.campaign, label_b=rb.campaign, floor=max(ra.floor, rb.floor)
    )
    return M.Comparison(figure=_plain(compare_figure(cmp)), caption=comparison_caption(cmp), summary=cmp.summary())
