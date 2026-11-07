"""Runs: the record, its verdict and tables, the figures Python already draws, poses, and the judgement layered on
top (decisions, comments, proposals). Every number comes from `smeltery.gui.results`; nothing here recomputes it."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Literal, get_args

from fastapi import APIRouter, HTTPException, Request
from molviewspec.nodes import RepresentationTypeT, validate_state_tree
from pydantic import BaseModel
from rdkit import Chem

from smeltery.bench.verdict import verdict_of
from smeltery.decisions import NOTE_REQUIRED, REASONS, STATUSES, DecisionSet
from smeltery.gui import results as R
from smeltery.gui.compare import Comparison as Cmp
from smeltery.gui.report import MOLSTAR_CSS, MOLSTAR_JS, RDKIT_JS, RDKIT_WASM, write_report
from smeltery.gui.scenes import pose_scene
from smeltery.gui.views._style import themed
from smeltery.gui.views.compare import compare_figure, comparison_caption
from smeltery.gui.views.forest import forest_figure
from smeltery.gui.views.matrix import matrix_figure
from smeltery.gui.views.per_pose import per_pose_figure
from smeltery.gui.views.per_pose_values import per_pose_values_figure
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


FIGURES = ("forest", "matrix", "per_pose", "per_pose_values")
#: The colour scheme a figure is drawn for. Python owns the palette for both (smeltery.gui.views._style).
Theme = Literal["light", "dark"]


@router.get("/runs/{idx}/figures/{name}")
def run_figure(request: Request, idx: int, name: str, theme: Theme = "light") -> dict[str, Any]:
    """The figure `smeltery.gui.views` builds, as Plotly JSON, coloured for `theme`. The browser draws it; it never
    re-derives or recolours it."""
    return themed(_figure(request, idx, name), theme)


def _figure(request: Request, idx: int, name: str) -> dict[str, Any]:
    if name not in FIGURES:
        raise HTTPException(status_code=404, detail=f"no figure {name!r}; figures are {', '.join(FIGURES)}")
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
    if name == "per_pose_values":
        return _plain(per_pose_values_figure(pp, r, QUANTITY))
    return _plain(per_pose_figure(pp, r, quantity=QUANTITY, unit=_unit(r), rows=table))


@router.get("/runs/{idx}/scene")
def run_scene(request: Request, idx: int, candidate: str, pose: int = 0, core: bool = False) -> dict[str, Any]:
    r = run(request, idx)
    if not r.has_pose_output:
        raise HTTPException(status_code=409, detail="this run record predates pose output; it has no poses to show")
    n = int(r.record["results"]["poses"].get(candidate, {}).get("n_poses", 0))
    if candidate not in r.candidates:
        raise HTTPException(status_code=400, detail=f"no candidate {candidate!r}")
    if not 0 <= pose < n:
        raise HTTPException(status_code=400, detail=f"pose {pose} out of range: {candidate} has {n} poses")
    scene = pose_scene(r, candidate, pose)
    atoms = _core_atoms(r, candidate) if core else None
    return _core_solid(scene, atoms) if atoms else scene


def _core_atoms(r: R.Run, candidate: str) -> list[int]:
    """The candidate's atoms copied exactly from the parent's pose, from `results.pose_maps` (pairs of analogue atom,
    parent atom). Empty for the parent and for a record written before pose maps."""
    maps = r.record["results"].get("pose_maps") or {}
    return sorted({int(pair[0]) for pair in maps.get(candidate, [])})


def _core_solid(scene: dict[str, Any], core: list[int]) -> dict[str, Any]:
    """The same MolViewSpec state with the candidate drawn as two components: its core atoms in the representation
    the scene already gave it (solid), every other atom small and translucent (outlined). Nothing but the candidate's
    structure node changes; the colours, parent, receptor and field are as `pose_scene` built them."""
    out = json.loads(json.dumps(scene))

    def structure(node: dict[str, Any]) -> dict[str, Any] | None:
        if node.get("kind") == "structure" and node.get("ref") == "candidate":
            return node
        for ch in node.get("children", []):
            found = structure(ch)
            if found:
                return found
        return None

    node = structure(out["root"])
    if node is None:
        return out
    (solid,) = [c for c in node["children"] if c.get("kind") == "component"]
    solid["params"]["selector"] = [{"atom_index": i} for i in core]
    others = json.loads(json.dumps(solid))
    others["params"]["selector"] = "all"
    for rep in others["children"]:
        if rep.get("kind") == "representation":
            rep["params"] = {"type": "ball_and_stick", "size_factor": 0.35}
            rep["children"] = [*rep.get("children", []), {"kind": "opacity", "params": {"opacity": 0.4}}]
    others["children"] = [c for c in others["children"] if c.get("kind") != "focus"]
    node["children"].append(others)
    validate_state_tree(json.dumps(out))
    _check_representations(out["root"])  # an unknown type fails here, not as a blank viewer
    return out


def _check_representations(node: dict[str, Any]) -> None:
    """molviewspec's State model leaves representation params loose, so check the type against its own Literal."""
    if node.get("kind") == "representation" and node["params"]["type"] not in get_args(RepresentationTypeT):
        raise ValueError(
            f"representation type {node['params']['type']!r} is not one of {get_args(RepresentationTypeT)}"
        )
    for ch in node.get("children", []):
        _check_representations(ch)


class PoseMaps(BaseModel):
    """`results.pose_maps`: per analogue, (analogue atom, parent atom) pairs copied exactly from the parent's pose."""

    maps: dict[str, list[list[int]]]


@router.get("/runs/{idx}/pose_maps", response_model=PoseMaps)
def run_pose_maps(request: Request, idx: int) -> PoseMaps:
    """Empty for a record written before pose maps were kept; the Poses view then cannot tell core from the rest."""
    return PoseMaps(maps=record(request, idx).get("results", {}).get("pose_maps") or {})


class SensitivityRow(BaseModel):
    """One analogue's paired ddE under the primary charge model and under the other one (None: no pose on both)."""

    primary: float | None
    model: float | None
    delta: float | None
    n: int


class ModelSensitivity(BaseModel):
    primary_model: str | None = None
    paired_ddE: dict[str, SensitivityRow] = {}
    #: None when fewer than two analogues could be compared (`note` says why) or the model failed (`error`).
    sign_flips: int | None = None
    spearman: float | None = None
    floor: float | None = None
    #: analogues compared
    n: int | None = None
    n_dropped: int = 0
    dropped_poses: dict[str, list[int]] = {}
    parent_dropped_poses: list[int] = []
    excluded: list[str] = []
    #: {candidate: {pose: error}}: SCFs under this model that did not give a value
    failures: dict[str, dict[str, str]] = {}
    #: provenance of this model's pocket field (pdb2pqr version, force field, file digest, cutoff)
    field: dict[str, Any] | None = None
    note: str | None = None
    #: the model could not be measured at all; `stage` is where it failed (field, measure, summary)
    error: str | None = None
    stage: str | None = None


class ChargeSensitivity(BaseModel):
    """`results.charge_sensitivity`, one entry per extra charge model; `models` None: the analysis did not run."""

    models: dict[str, ModelSensitivity] | None


@router.get("/runs/{idx}/charge_sensitivity", response_model=ChargeSensitivity)
def run_charge_sensitivity(request: Request, idx: int) -> ChargeSensitivity:
    got = (record(request, idx).get("results") or {}).get("charge_sensitivity")
    if not isinstance(got, dict):
        return ChargeSensitivity(models=None)
    return ChargeSensitivity(models={k: ModelSensitivity.model_validate(v) for k, v in got.items()})


class GateRow(BaseModel):
    candidate: str
    pose: int
    #: PoseBusters' verdict for the pose; None when that gate did not run for this candidate.
    posebusters: bool | None
    failed_checks: list[str]
    strain_kcal: float | None
    #: Strain above `max_strain_kcal`; None when no threshold is configured (strain is then only recorded).
    over_strain: bool | None
    #: False when neither gate judged this pose (no PoseBusters verdict, no strain limit): not a pass.
    checked: bool
    ok: bool


class Gates(BaseModel):
    """`results.gates` as one row per (candidate, pose). `raw` is the record's own dict, unchanged."""

    ran: list[str]
    max_strain_kcal: float | None
    rows: list[GateRow]
    raw: dict[str, Any]


@router.get("/runs/{idx}/gates", response_model=Gates)
def run_gates(request: Request, idx: int) -> Gates:
    raw = record(request, idx)
    results = raw.get("results", {})
    gates: dict[str, Any] = results.get("gates") or {}
    limit = (raw.get("inputs", {}).get("gates") or {}).get("max_strain_kcal")
    pb: dict[str, Any] = gates.get("posebusters") or {}
    strain: dict[str, list[float]] = gates.get("strain_kcal") or {}
    order = list(results.get("candidate_order") or [*pb, *(c for c in strain if c not in pb)])
    rows = []
    for name in order:
        verdicts = (pb.get(name) or {}).get("poses")
        n = max(len(verdicts or []), len(strain.get(name, [])))
        for i in range(n):
            v = verdicts[i] if verdicts is not None and i < len(verdicts) else None
            s = float(strain[name][i]) if name in strain and i < len(strain[name]) else None
            over = None if limit is None or s is None else s > float(limit)
            passed = None if v is None else bool(v["passed"])
            rows.append(
                GateRow(
                    candidate=name,
                    pose=i,
                    posebusters=passed,
                    failed_checks=list(v["failed_checks"]) if v else [],
                    strain_kcal=s,
                    over_strain=over,
                    checked=passed is not None or over is not None,
                    ok=passed is not False and over is not True,
                )
            )
    return Gates(ran=sorted(gates), max_strain_kcal=None if limit is None else float(limit), rows=rows, raw=gates)


class CalcLines(BaseModel):
    """The SCF log lines of one pose: every line of the run's calc log whose candidate and original pose match."""

    file: str | None
    #: the original pose index the lines were matched on (the log's own `pose`)
    pose: int
    lines: list[dict[str, Any]]


def _original_pose(raw: dict, candidate: str, pose: int) -> int:
    """The original pose index of record `pose` of `candidate`: `results.pose_index[candidate][pose]` after a pairwise
    drop, else `pose` itself. The calc log is written before any drop, so it is keyed by the original index."""
    index = ((raw.get("results") or {}).get("pose_index") or {}).get(candidate)
    if index is None:
        return pose
    if not 0 <= pose < len(index):
        raise HTTPException(status_code=400, detail=f"pose {pose} out of range: {candidate} kept {len(index)} poses")
    return int(index[pose])


@router.get("/runs/{idx}/calcs", response_model=CalcLines)
def run_calcs(request: Request, idx: int, candidate: str, pose: int, field_model: str | None = None) -> CalcLines:
    """Lines from the file `results.calcs` names (calcs.jsonl, or calcs-<candidate>.jsonl for a partial run), read as
    written, of one field model: `field_model`, else the run's primary one (`results.calcs.primary`). A line naming
    no model (a run from before models were recorded) is the primary's. `pose` is the record's place in the
    candidate's poses, as `/scene` takes it; it is mapped through `results.pose_index` to the log's original index.
    409 when the run kept no SCF log."""
    raw = record(request, idx)
    calcs = (raw.get("results") or {}).get("calcs")
    if not calcs:
        raise HTTPException(status_code=409, detail="this run kept no SCF log")
    pose = _original_pose(raw, candidate, pose)
    out_dir = Path(row(request, idx)["path"]).parent.resolve()
    path = (out_dir / str(calcs["file"])).resolve()
    if out_dir not in path.parents:
        raise HTTPException(status_code=409, detail=f"the SCF log {calcs['file']!r} is outside the run directory")
    try:
        text = path.read_text()
    except OSError as e:
        raise HTTPException(status_code=409, detail=f"cannot read the SCF log {path}: {e}") from e
    primary = calcs.get("primary")
    want = field_model if field_model is not None else primary
    lines = []
    for x in text.splitlines():
        if not x.strip():
            continue
        try:
            d = json.loads(x)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("candidate") == candidate and d.get("pose") == pose:
            model = d.get("field_model", primary)
            if want is None or model is None or model == want:  # None: a --temporal vacuum leg, shared by all
                lines.append(d)
    return CalcLines(file=str(calcs["file"]), pose=pose, lines=lines)


class DroppedPose(BaseModel):
    """One entry of `results.dropped_poses`: a leg that failed every rung, so its pose was dropped pairwise."""

    candidate: str
    #: the original pose index
    pose: int
    leg: str
    #: None for a vacuum leg (shared by every field model)
    field_model: str | None = None
    reason: str | None = None
    outcome: str | None = None
    rungs_tried: list[int] = []
    #: the candidates whose ddE lost this pose; absent for a drop under an extra charge model only
    dropped_with: list[str] | None = None


class PoseDrops(BaseModel):
    """What a `--temporal` run dropped. `pose_index` is `results.pose_index` (empty: no pose was dropped, record `k`
    is pose `k`)."""

    pose_index: dict[str, list[int]]
    dropped: list[DroppedPose]


@router.get("/runs/{idx}/dropped_poses", response_model=PoseDrops)
def run_dropped_poses(request: Request, idx: int) -> PoseDrops:
    res = record(request, idx).get("results") or {}
    return PoseDrops(
        pose_index={k: [int(i) for i in v] for k, v in (res.get("pose_index") or {}).items()},
        dropped=[DroppedPose.model_validate(d) for d in res.get("dropped_poses") or []],
    )


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


def _arm_labels(ra: R.Run, rb: R.Run) -> tuple[str, str]:
    """Each arm's campaign. When both arms share one (two runs of one campaign, the usual comparison), the arm letter
    the page chose it as and its digest are added, so the caption never says a run favours itself."""
    if ra.campaign != rb.campaign:
        return ra.campaign, rb.campaign
    return (
        f"A ({ra.campaign} {ra.record['input_digest'][:8]})",
        f"B ({rb.campaign} {rb.record['input_digest'][:8]})",
    )


@router.get("/compare", response_model=M.Comparison)
def compare(request: Request, a: int, b: int, theme: Theme = "light") -> M.Comparison:
    """Two runs as arms, one comparison key each. An arm compared with itself is refused, not drawn."""
    if a == b:
        raise HTTPException(
            status_code=400, detail="a run is in both arms; an arm compared with itself is not a comparison"
        )
    ra, rb = run(request, a), run(request, b)
    label_a, label_b = _arm_labels(ra, rb)
    cmp = Cmp.from_runs({"run": ra}, {"run": rb}, label_a=label_a, label_b=label_b, floor=max(ra.floor, rb.floor))
    rows = [
        M.CompareRow(
            key=k.key,
            n_a=k.n_a,
            n_b=k.n_b,
            a=k.a,
            b=k.b,
            diff=k.diff,
            sem=k.sem if math.isfinite(k.sem) else None,
            resolved=k.resolved,
            unpaired=k.unpaired,
        )
        for k in cmp.rows()
    ]
    return M.Comparison(
        figure=themed(_plain(compare_figure(cmp)), theme),
        caption=comparison_caption(cmp),
        summary=cmp.summary(),
        rows=rows,
        unit=cmp.unit,
    )
